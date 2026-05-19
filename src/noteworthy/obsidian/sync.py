"""Top-level orchestrator for the Obsidian export mode.

The flow is organized as a sequence of small helpers, each doing one thing:

  1. Read Apple Notes via `extract_folders_and_notes`.
  2. Walk the account/folder tree, **skipping smart folders**, and compute a
     vault-wide unique on-disk filename for every surviving note (`_build_note_layout`).
  3. Decode each note's protobuf body and resolve attachment metadata, collecting
     the (note, blocks, attachments) tuples we'll need to render. Notes whose data
     can't be read (locked / encrypted / missing) are warned about and skipped.
  4. Assign vault-wide unique filenames to attachments (`_assign_attachment_filenames`).
  5. Build a `uuid -> final vault path` map so the renderer can resolve inter-note
     wikilinks to the right target filename.
  6. Render each note's markdown body in OBSIDIAN dialect, prepend the frontmatter
     block, and write to disk.
  7. Copy each attachment's source file into the vault's `assets/` directory.
  8. Ensure `.obsidian/app.json` exists so the vault opens correctly.

See obsidian_requirements.md for the spec; see obsidian_implementation_plan.md
for stage-by-stage rationale.
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from noteworthy.database import DatabaseNoteDataLoader
from noteworthy.extract_notes_db import extract_folders_and_notes
from noteworthy.markdown_renderer import (
    MarkdownGenerator,
    AttachmentResolver,
    UTI_TO_EXTENSION,
)
from noteworthy.note_content import Attachment, ContentBlock, ProtobufDecoder
from noteworthy.notes_datatypes import Account, Folder, Note

from . import frontmatter, vault_config
from .dialect import ExportDialect
from .filename import assign_unique_names, sanitize_for_obsidian


__all__ = ["run"]


_DEFAULT_DB = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"

# Sentinel used when a note has no creation_date so the sort key stays totally
# ordered. tz-aware to match the (tz-aware) datetimes Apple Notes produces.
_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)


# ---------- planning data structures ----------


@dataclass
class _NoteLayout:
    """Where a single note will live in the vault, plus the context the renderer needs.

    `folder_dirs` is the tuple of directory names from the vault root down to the
    note's immediate parent folder (account dir first when multi-account, then the
    Apple Notes folder hierarchy). `filename` is the on-disk basename without the
    `.md` extension — used both for the file path and as the wikilink target.
    `account` and `folder_path_in_account` are pre-computed so frontmatter
    rendering doesn't have to walk parents at write time.

    `aliases` carries any names the user might still type for this note in
    Obsidian's quick switcher — the original display name when sanitization or
    disambiguation changed the on-disk filename (requirements §5.1).
    """
    folder_dirs: tuple[str, ...]
    filename: str
    account: Account
    folder_path_in_account: str
    aliases: list[str] = field(default_factory=list)

    @property
    def relative_path(self) -> Path:
        return Path(*self.folder_dirs) / f"{self.filename}.md"


@dataclass
class _DecodedNote:
    """A note after protobuf decode, ready to render."""
    note: Note
    blocks: list[ContentBlock]
    file_attachments: list[Attachment] = field(default_factory=list)


@dataclass
class _ExistingNote:
    """A note we found already in the vault on a previous run, matched by UUID.

    Drives rename/move detection (compare `path` to the freshly-computed path)
    and frontmatter round-tripping: `aliases` is preserved so user-added or
    historical entries survive, and `extras` carries any frontmatter keys we
    don't own (per §11.2 the user can add their own and we leave them alone).
    """
    path: Path
    aliases: list[str] = field(default_factory=list)
    extras: dict = field(default_factory=dict)


# ---------- public entry point ----------


def run(target_path: pathlib.Path, db_path: pathlib.Path | None = None, *, verbose: int = 0) -> None:
    """Export Apple Notes into the Obsidian vault at `target_path`.

    `verbose` is a count, not a bool: 0 = silent, 1 = scan + done summary
    plus rename/move actions, 2+ = also log every note actually written and
    every attachment actually copied. Level 2 is for diagnosing spurious
    rewrites on re-export and is too noisy for routine use.
    """
    target_path = pathlib.Path(target_path)
    target_path.mkdir(parents=True, exist_ok=True)

    accounts = extract_folders_and_notes(db_path=db_path)
    accounts_with_content = [a for a in accounts if any(_iter_real_notes(a))]
    flatten_account = len(accounts_with_content) <= 1

    # Re-export: scan what's already in the vault so we can detect renames /
    # moves and preserve user-added frontmatter keys (requirements §11.2).
    existing = _scan_vault(target_path)

    layout = _build_note_layout(accounts_with_content, flatten_account, existing)

    if verbose:
        print(f"scanning: {len(accounts_with_content)} account(s), {len(layout)} note(s) to export")

    data_loader = DatabaseNoteDataLoader(str(db_path or _DEFAULT_DB))
    try:
        decoded = _decode_all_notes(layout, data_loader, verbose=verbose)
        _assign_attachment_filenames(decoded)

        note_path_by_uuid = {
            d.note.uuid.upper(): target_path / layout[d.note].relative_path
            for d in decoded if d.note.uuid
        }

        notes_written, notes_unchanged = _write_notes(
            decoded, layout, target_path, note_path_by_uuid, data_loader, existing,
            verbose=verbose,
        )
        attachments_copied, attachments_unchanged = _copy_attachments(
            decoded, target_path, verbose=verbose,
        )
    finally:
        data_loader.close()

    vault_config.ensure_app_json(target_path)

    if verbose:
        print(
            f"done: {notes_written} note(s) written, {notes_unchanged} unchanged; "
            f"{attachments_copied} attachment(s) copied, {attachments_unchanged} unchanged"
        )


# ---------- step 2: layout ----------


def _iter_real_notes(account: Account) -> Iterable[tuple[Note, tuple[Folder, ...]]]:
    """Yield (note, folder_chain) for every note reachable through non-smart folders.

    `folder_chain` is the sequence of folders from the account's top-level folder
    down to the folder that directly contains the note.
    """
    def walk(folder: Folder, chain: tuple[Folder, ...]):
        for note in folder.notes:
            yield note, chain
        for sub in folder.folders:
            if sub.is_smart_folder:
                continue
            yield from walk(sub, chain + (sub,))

    for top in account.folders:
        if top.is_smart_folder:
            continue
        yield from walk(top, (top,))


def _folder_dirs_for(account: Account, folder_chain: tuple[Folder, ...], *, flatten_account: bool) -> tuple[str, ...]:
    """On-disk directory chain for a note: optional account dir, then sanitized folder names."""
    parts = [] if flatten_account else [sanitize_for_obsidian(account.name)]
    parts.extend(sanitize_for_obsidian(f.name) for f in folder_chain)
    return tuple(parts)


# ---------- step 2a: scan existing vault (re-export support) ----------


def _scan_vault(target_path: Path) -> dict[str, _ExistingNote]:
    """Walk `*.md` files under `target_path`, returning uuid → _ExistingNote.

    Files without a parseable `apple_notes_uuid` frontmatter key are skipped
    silently — they may be user-authored notes the exporter never touched.
    UUIDs are normalized to uppercase for lookup symmetry with the rest of the
    pipeline (`note_path_by_uuid` uses the same convention).
    """
    found: dict[str, _ExistingNote] = {}
    for md_path in target_path.rglob("*.md"):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        meta = frontmatter.parse(text)
        uuid = meta.get("apple_notes_uuid")
        if not isinstance(uuid, str) or not uuid:
            continue
        aliases = meta.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        extras = {k: v for k, v in meta.items() if k not in frontmatter.OWNED_KEYS}
        found[uuid.upper()] = _ExistingNote(path=md_path, aliases=list(aliases), extras=extras)
    return found


def _build_note_layout(
    accounts: list[Account],
    flatten_account: bool,
    existing: dict[str, _ExistingNote] | None = None,
) -> dict[Note, _NoteLayout]:
    """Compute the on-disk path for every note in `accounts`.

    Filenames are resolved vault-wide for uniqueness, sorted by (creation_date, id)
    so the order is deterministic and a re-run won't shuffle which note keeps the
    bare name vs gets a " (2)" suffix. Notes without a UUID are dropped here
    (with a stderr warning) because the spec requires `apple_notes_uuid` for
    re-export round-tripping.
    """
    # Step 1: collect (note, candidate_basename, folder_chain, account) tuples.
    # Notes without a UUID are skipped with a warning — see §11.2: identity is
    # established by apple_notes_uuid, so an entry without one couldn't be
    # matched on re-export and would orphan with every run.
    candidates: list[tuple[Note, str, tuple[Folder, ...], Account]] = []
    for account in accounts:
        for note, chain in _iter_real_notes(account):
            if not note.uuid:
                _warn_skipped_note(note, account.name, "no apple_notes_uuid available")
                continue
            candidates.append((note, sanitize_for_obsidian(note.name), chain, account))

    # Step 2: sort by (creation_date, id) so the earliest note keeps its bare name.
    # Notes with a missing creation_date sort first (sentinel below) so the
    # comparison never hits a NoneType vs datetime TypeError on bad data. The
    # sentinel is tz-aware because the real creation_date is tz-aware.
    candidates.sort(key=lambda t: (t[0].creation_date or _MIN_DATETIME, t[0].id))

    # Step 3: vault-wide uniqueness pass over the candidate filenames. Indices
    # serve as keys so callers don't have to find a stable hash for `Note`
    # (whose hash walks `_folders`, which would be wasteful here).
    final_names = assign_unique_names(
        [(i, name) for i, (_note, name, _chain, _acc) in enumerate(candidates)],
        has_extensions=False,
    )

    # Step 4: assemble the per-note layout, populating aliases when sanitization
    # or disambiguation changed the on-disk name (requirements §5.1) and when
    # a previous export had a different on-disk filename (re-export rename, §11.2).
    layout: dict[Note, _NoteLayout] = {}
    for i, (note, _candidate, chain, account) in enumerate(candidates):
        filename = final_names[i]
        aliases = _compute_aliases(note, filename, existing)
        layout[note] = _NoteLayout(
            folder_dirs=_folder_dirs_for(account, chain, flatten_account=flatten_account),
            filename=filename,
            account=account,
            folder_path_in_account="/".join(f.name for f in chain),
            aliases=aliases,
        )
    return layout


def _compute_aliases(
    note: Note,
    filename: str,
    existing: dict[str, _ExistingNote] | None,
) -> list[str]:
    """Aliases for this note: preserve previous + add freshly-applicable.

    Sources of truth, in priority order:
      1. The existing aliases on disk (user might have added entries, prior
         runs accumulated rename history) — these come first to keep order.
      2. The previous on-disk filename, when this run is renaming the file —
         appended so old wikilinks keep resolving.
      3. The original (un-sanitized) display name, when sanitization changed
         the filename — required by §5.1.
    Deduplicated while preserving first-seen order.
    """
    aliases: list[str] = []
    seen: set[str] = set()

    def add(alias: str | None) -> None:
        if alias and alias not in seen:
            aliases.append(alias)
            seen.add(alias)

    previous = existing.get(note.uuid.upper()) if existing and note.uuid else None
    if previous:
        for a in previous.aliases:
            add(a)
        prev_stem = previous.path.stem
        if prev_stem != filename:
            add(prev_stem)

    if note.name and note.name != filename:
        add(note.name)
    return aliases


# ---------- step 3: decode ----------


def _decode_all_notes(
    layout: dict[Note, _NoteLayout],
    data_loader: DatabaseNoteDataLoader,
    *,
    verbose: bool,
) -> list[_DecodedNote]:
    """Decode each planned note's protobuf body and resolve attachment metadata.

    Notes whose data is missing or unreadable (locked / encrypted / corrupt) are
    warned about on stderr and dropped from the result list — they will not be
    written to the vault.
    """
    decoder = ProtobufDecoder()
    decoded: list[_DecodedNote] = []

    for note, plan in layout.items():
        account_name = plan.account.name
        zpk = _zpk_from_core_data_id(note.id)
        if zpk is None:
            _warn_skipped_note(note, account_name, "could not parse note id")
            continue
        try:
            compressed = data_loader.get_note_data(zpk)
        except Exception as exc:
            _warn_skipped_note(note, account_name, f"could not read note data ({exc})")
            continue
        try:
            blocks = decoder.decode_note(compressed)
        except Exception as exc:
            _warn_skipped_note(note, account_name, f"could not decode note body ({exc})")
            continue

        resolver = AttachmentResolver(data_loader, note_name=note.name, note_uuid=note.uuid)
        file_atts = _resolve_attachment_metadata(blocks, resolver)
        decoded.append(_DecodedNote(note=note, blocks=blocks, file_attachments=file_atts))

    return decoded


_CORE_DATA_PK_RE = re.compile(r"/p(\d+)$")


def _zpk_from_core_data_id(note_id: str) -> int | None:
    """Extract the trailing `p<digits>` integer from a Core Data URI."""
    m = _CORE_DATA_PK_RE.search(note_id)
    return int(m.group(1)) if m else None


def _warn_skipped_note(note: Note, account_name: str, reason: str) -> None:
    """Emit a single-line stderr warning explaining why a note was skipped.

    Per §10, the warning includes both the note name AND the account so the user
    can find the note in Apple Notes to investigate. Used for locked / encrypted
    notes (the §10 case) and for any other unreadable note so nothing is lost
    silently.
    """
    print(
        f"warning: skipping note {note.name!r} in account {account_name!r} ({reason})",
        file=sys.stderr,
    )


def _resolve_attachment_metadata(blocks: list[ContentBlock], resolver: AttachmentResolver) -> list[Attachment]:
    """Populate title/file_path/type on each attachment in `blocks`, returning the
    file-bearing ones (the others — tables, hashtags, inter-note links — don't get
    copied to assets/).
    """
    file_attachments: list[Attachment] = []
    for block in blocks:
        if block.type != "attachment" or not block.attachment:
            continue
        att = block.attachment
        if att.type == "com.apple.notes.table" or "hashtag" in att.type or "inlinetextattachment.link" in att.type:
            continue  # non-file attachments — nothing to copy

        if att.type == "com.apple.notes.gallery":
            resolver.resolve_gallery(att)
            for child in (att.gallery_children or []):
                if child.file_path and (child.title or child.alt_text):
                    file_attachments.append(child)
            continue

        if not att.file_path and not att.title:
            resolver.resolve_attachment(att)
        if att.file_path and (att.title or att.alt_text):
            file_attachments.append(att)
    return file_attachments


# ---------- step 4: vault-wide attachment filenames ----------


# The set of extensions the candidate-filename pass recognizes as "already has
# one, leave it alone." Derived from the canonical UTI map so the two sources
# can't drift; .tif/.jpeg are added as recognized spellings missing from the
# UTI table.
_KNOWN_EXTENSIONS = frozenset(UTI_TO_EXTENSION.values()) | {".tif", ".jpeg"}


def _candidate_attachment_filename(att: Attachment) -> str:
    """Pick a vault filename for an attachment, preferring the underlying file's own name.

    Requirements §4 and §5.2 show flat-namespaced filenames like `photo.jpg` and
    `receipt.pdf`. The on-disk media filename (basename of `att.file_path`) is the
    most faithful match — it's what the user originally dragged in. We fall back to
    the title only if no file path is available (e.g. unresolved metadata), and to
    the UUID prefix as a last resort. Extension is inferred from the UTI when the
    chosen base has none.
    """
    base = ""
    if att.file_path:
        base = os.path.basename(att.file_path)
    if not base:
        base = att.title or att.alt_text or att.uuid[:8]
    base = sanitize_for_obsidian(base)
    _, ext = os.path.splitext(base)
    if ext.lower() in _KNOWN_EXTENSIONS:
        return base
    inferred = UTI_TO_EXTENSION.get((att.type or "").lower())
    return base + inferred if inferred else base


def _assign_attachment_filenames(decoded_notes: list[_DecodedNote]) -> None:
    """Set `att.unique_filename` on every attachment, vault-wide uniqueness."""
    # Flatten and sort attachments so the order is deterministic across runs.
    all_atts: list[Attachment] = []
    for d in decoded_notes:
        all_atts.extend(d.file_attachments)
    all_atts.sort(key=lambda a: (a.uuid or ""))

    # Attachment is an unhashable @dataclass, so use positional indices as keys.
    assignments = assign_unique_names(
        [(i, _candidate_attachment_filename(att)) for i, att in enumerate(all_atts)],
        has_extensions=True,
    )
    for i, att in enumerate(all_atts):
        att.unique_filename = assignments[i]


# ---------- step 6: render + write notes ----------


def _write_notes(
    decoded_notes: list[_DecodedNote],
    layout: dict[Note, _NoteLayout],
    target_path: Path,
    note_path_by_uuid: dict[str, Path],
    data_loader: DatabaseNoteDataLoader,
    existing: dict[str, _ExistingNote],
    *,
    verbose: int = 0,
) -> tuple[int, int]:
    """Render each note's markdown and write `frontmatter + body` to its planned path.

    When `existing` reports the note already lives at a different path, we
    `os.replace` it into position first so the write happens at the new path
    and the old file disappears (rename/move support — §11.2). Any frontmatter
    keys we don't own are passed through to `frontmatter.render` as
    `extra_user_keys` to preserve user edits.

    Returns (written_count, unchanged_count) so the caller can summarize.
    """
    written = 0
    unchanged = 0
    for d in decoded_notes:
        plan = layout[d.note]
        md_path = target_path / plan.relative_path
        md_path.parent.mkdir(parents=True, exist_ok=True)

        previous = existing.get(d.note.uuid.upper()) if d.note.uuid else None
        if previous and previous.path != md_path and previous.path.exists():
            # Move the file into its new location before we rewrite it. Using
            # os.replace keeps the operation atomic on the same filesystem and
            # avoids a half-baked state if the rewrite below fails.
            if verbose:
                old_rel = previous.path.relative_to(target_path)
                new_rel = md_path.relative_to(target_path)
                print(f"  moving {old_rel} -> {new_rel}")
            os.replace(previous.path, md_path)

        body = _render_body(d, md_path, note_path_by_uuid, data_loader)
        fm = frontmatter.render(
            d.note,
            account_name=plan.account.name,
            folder_path=plan.folder_path_in_account,
            aliases=plan.aliases,
            extra_user_keys=previous.extras if previous else None,
        )

        # Skip the write entirely when the on-disk file already matches what
        # we'd produce. Avoids cloud-sync churn on unchanged notes and lets
        # the user run re-exports cheaply.
        new_content = fm + body
        if md_path.exists():
            try:
                if md_path.read_text(encoding="utf-8") == new_content:
                    unchanged += 1
                    continue
            except OSError:
                pass
        if verbose >= 2:
            print(f"  writing {md_path.relative_to(target_path)}")
        md_path.write_text(new_content, encoding="utf-8")
        written += 1
    return written, unchanged


def _render_body(
    decoded: _DecodedNote,
    md_path: Path,
    note_path_by_uuid: dict[str, Path],
    data_loader: DatabaseNoteDataLoader,
) -> str:
    """Render the markdown body for one note using a fresh MarkdownGenerator in OBSIDIAN dialect."""
    resolver = AttachmentResolver(data_loader, note_name=decoded.note.name, note_uuid=decoded.note.uuid)
    generator = MarkdownGenerator(
        resolver,
        note_path_by_uuid=note_path_by_uuid,
        current_note_path=md_path,
        dialect=ExportDialect.OBSIDIAN,
        note_name=decoded.note.name,
    )
    return generator.generate(decoded.blocks)


# ---------- step 7: copy attachments ----------


def _copy_attachments(decoded_notes: list[_DecodedNote], target_path: Path,
                      *, verbose: int = 0) -> tuple[int, int]:
    """Copy every attachment's source file into `<vault>/assets/<unique_filename>`.

    Skips the copy when an existing dest file already matches the source by
    size and isn't older than it. shutil.copy2 preserves source mtime, so a
    re-export over an unchanged source produces (src.size == dest.size,
    src.mtime == dest.mtime) and the rewrite is avoided — important for
    cloud-synced vaults (iCloud Drive / Dropbox / Obsidian Sync) where any
    inode touch triggers an upload.

    Returns (copied_count, skipped_count) so the caller can summarize.
    """
    assets_dir = target_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    copied = 0
    skipped = 0
    for d in decoded_notes:
        for att in d.file_attachments:
            if not (att.file_path and att.unique_filename):
                continue
            src = Path(att.file_path)
            if not src.exists():
                print(f"warning: attachment file not found: {src}", file=sys.stderr)
                continue
            dest = assets_dir / att.unique_filename
            if src.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
                if verbose >= 2:
                    print(f"  copying assets/{att.unique_filename}/ (directory)")
                copied += 1
                continue
            if _dest_already_matches(src, dest):
                skipped += 1
                continue
            if verbose >= 2:
                reason = "new" if not dest.exists() else "changed"
                print(f"  copying assets/{att.unique_filename} ({reason})")
            shutil.copy2(src, dest)
            copied += 1
    return copied, skipped


def _dest_already_matches(src: Path, dest: Path) -> bool:
    """True if `dest` already reflects `src` and a copy would be wasted work.

    Size match plus dest-mtime >= src-mtime is enough in practice: shutil.copy2
    propagates the source's mtime to dest on a successful copy, so a clean
    re-export over an unchanged source meets both conditions. If the user
    replaces the attachment in Apple Notes the source's mtime advances and we
    re-copy. (A same-size replacement that somehow keeps the original mtime is
    pathological and not worth defending against — the user would re-export
    explicitly if they noticed staleness.)
    """
    if not dest.exists() or dest.is_dir():
        return False
    try:
        src_stat = src.stat()
        dest_stat = dest.stat()
    except OSError:
        return False
    return src_stat.st_size == dest_stat.st_size and dest_stat.st_mtime >= src_stat.st_mtime
