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

import pathlib
import shutil
import sys
from dataclasses import dataclass, field
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


# ---------- planning data structures ----------


@dataclass
class _NoteLayout:
    """Where a single note will live in the vault.

    `folder_dirs` is the tuple of directory names from the vault root down to the
    note's immediate parent folder (account dir first when multi-account, then the
    Apple Notes folder hierarchy). `filename` is the on-disk basename without the
    `.md` extension — used both for the file path and as the wikilink target.
    """
    folder_dirs: tuple[str, ...]
    filename: str

    @property
    def relative_path(self) -> Path:
        return Path(*self.folder_dirs) / f"{self.filename}.md"


@dataclass
class _DecodedNote:
    """A note after protobuf decode, ready to render."""
    note: Note
    blocks: list[ContentBlock]
    file_attachments: list[Attachment] = field(default_factory=list)


# ---------- public entry point ----------


def run(target_path: pathlib.Path, db_path: pathlib.Path | None = None, *, verbose: bool = False) -> None:
    """Export Apple Notes into the Obsidian vault at `target_path`."""
    target_path = pathlib.Path(target_path)
    target_path.mkdir(parents=True, exist_ok=True)

    accounts = extract_folders_and_notes(db_path=db_path)
    accounts_with_content = [a for a in accounts if any(_iter_real_notes(a))]
    flatten_account = len(accounts_with_content) <= 1

    layout = _build_note_layout(accounts_with_content, flatten_account)

    data_loader = DatabaseNoteDataLoader(str(db_path or _DEFAULT_DB))
    try:
        decoded = _decode_all_notes(layout, data_loader, verbose=verbose)
        _assign_attachment_filenames(decoded)

        note_path_by_uuid = {
            d.note.uuid.upper(): target_path / layout[d.note].relative_path
            for d in decoded if d.note.uuid
        }

        _write_notes(decoded, layout, target_path, note_path_by_uuid, data_loader,
                     accounts_with_content, flatten_account)
        _copy_attachments(decoded, target_path)
    finally:
        data_loader.close()

    vault_config.ensure_app_json(target_path)


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


def _build_note_layout(accounts: list[Account], flatten_account: bool) -> dict[Note, _NoteLayout]:
    """Compute the on-disk path for every note in `accounts`.

    Filenames are resolved vault-wide for uniqueness, sorted by (creation_date, id)
    so the order is deterministic and a re-run won't shuffle which note keeps the
    bare name vs gets a " (2)" suffix.
    """
    # Step 1: collect (note, candidate_basename, folder_chain, account) tuples.
    candidates: list[tuple[Note, str, tuple[Folder, ...], Account]] = []
    for account in accounts:
        for note, chain in _iter_real_notes(account):
            candidates.append((note, sanitize_for_obsidian(note.name), chain, account))

    # Step 2: sort by (creation_date, id) so the earliest note keeps its bare name.
    candidates.sort(key=lambda t: (t[0].creation_date, t[0].id))

    # Step 3: vault-wide uniqueness pass over the candidate filenames.
    # Use positional indices as keys — Note isn't a dataclass so identity-hashing
    # would work, but indices keep this symmetric with the Attachment pass.
    final_names = assign_unique_names(
        [(i, name) for i, (_note, name, _chain, _acc) in enumerate(candidates)],
        has_extensions=False,
    )

    # Step 4: assemble the per-note layout.
    layout: dict[Note, _NoteLayout] = {}
    for i, (note, _candidate, chain, account) in enumerate(candidates):
        layout[note] = _NoteLayout(
            folder_dirs=_folder_dirs_for(account, chain, flatten_account=flatten_account),
            filename=final_names[i],
        )
    return layout


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

    for note in layout:
        zpk = _zpk_from_core_data_id(note.id)
        if zpk is None:
            _warn_skipped_note(note, "could not parse note id")
            continue
        try:
            compressed = data_loader.get_note_data(zpk)
        except Exception as exc:
            _warn_skipped_note(note, f"could not read note data ({exc})")
            continue
        try:
            blocks = decoder.decode_note(compressed)
        except Exception as exc:
            _warn_skipped_note(note, f"could not decode note body ({exc})")
            continue

        resolver = AttachmentResolver(data_loader, note_name=note.name, note_uuid=note.uuid)
        file_atts = _resolve_attachment_metadata(blocks, resolver)
        decoded.append(_DecodedNote(note=note, blocks=blocks, file_attachments=file_atts))

    return decoded


def _zpk_from_core_data_id(note_id: str) -> int | None:
    """Extract the trailing `p<digits>` integer from a Core Data URI."""
    import re
    m = re.search(r"/p(\d+)$", note_id)
    return int(m.group(1)) if m else None


def _warn_skipped_note(note: Note, reason: str) -> None:
    """Emit a single-line stderr warning explaining why a note was skipped.

    Matches the §10 "warn then skip" rule for locked notes; we use the same path
    for any other unreadable note so the user always learns what was lost.
    """
    print(f"warning: skipping note {note.name!r} ({reason})", file=sys.stderr)


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


_KNOWN_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".tiff", ".tif", ".heic", ".heif",
    ".webp", ".avif", ".bmp", ".svg", ".pdf", ".txt", ".rtf", ".html",
    ".json", ".xml", ".mov", ".mp4", ".avi", ".mp3", ".m4a", ".doc",
    ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".eml", ".vcf",
})


def _candidate_attachment_filename(att: Attachment) -> str:
    """Pick a vault filename for an attachment, preferring the underlying file's own name.

    Requirements §4 and §5.2 show flat-namespaced filenames like `photo.jpg` and
    `receipt.pdf`. The on-disk media filename (basename of `att.file_path`) is the
    most faithful match — it's what the user originally dragged in. We fall back to
    the title only if no file path is available (e.g. unresolved metadata), and to
    the UUID prefix as a last resort. Extension is inferred from the UTI when the
    chosen base has none.
    """
    import os
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
    accounts: list[Account],
    flatten_account: bool,
) -> None:
    """Render each note's markdown and write `frontmatter + body` to its planned path."""
    account_by_id = {acc.id: acc for acc in accounts}

    for d in decoded_notes:
        plan = layout[d.note]
        md_path = target_path / plan.relative_path
        md_path.parent.mkdir(parents=True, exist_ok=True)

        body = _render_body(d, md_path, note_path_by_uuid, data_loader)
        fm = _render_frontmatter(d.note, account_by_id, flatten_account)

        md_path.write_text(fm + body, encoding="utf-8")


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


def _render_frontmatter(note: Note, account_by_id: dict[str, Account], flatten_account: bool) -> str:
    """Build the YAML frontmatter block for a note."""
    home = note.home_folder
    account = account_by_id.get(home.parent.id) if home and home.parent else None
    # Fall back: a note's home folder may itself be a top-level folder whose parent
    # is the Account (not in account_by_id keyed by folder ids). Walk up the chain.
    if account is None:
        account = _find_owning_account(note, account_by_id)

    folder_path = _folder_full_name(note)
    aliases: list[str] = []  # Stage 5 populates from disambiguation/sanitization
    return frontmatter.render(
        note,
        account_name=account.name if account else "",
        folder_path=folder_path,
        aliases=aliases,
    )


def _find_owning_account(note: Note, account_by_id: dict[str, Account]) -> Account | None:
    """Return the Account that ultimately contains `note`'s home folder."""
    home = note.home_folder
    if not home:
        return None
    cur = home
    while cur.parent is not None and not isinstance(cur.parent, Account):
        cur = cur.parent
    return cur.parent if isinstance(cur.parent, Account) else None


def _folder_full_name(note: Note) -> str:
    """Slash-joined folder path for the note within its account, e.g. `Work/Personal`.

    `Folder.full_name` includes the account name as its outermost segment; the
    spec wants the account-relative path, so we walk up from `home_folder` to
    just below the Account and join those folder names.

    Returns an empty string for notes that sit at the account root.
    """
    home = note.home_folder
    if not home:
        return ""
    chain: list[str] = []
    cur = home
    while cur is not None and not isinstance(cur, Account):
        chain.append(cur.name)
        cur = cur.parent
    chain.reverse()
    return "/".join(chain)


# ---------- step 7: copy attachments ----------


def _copy_attachments(decoded_notes: list[_DecodedNote], target_path: Path) -> None:
    """Copy every attachment's source file into `<vault>/assets/<unique_filename>`."""
    assets_dir = target_path / "assets"
    assets_dir.mkdir(exist_ok=True)
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
            else:
                shutil.copy2(src, dest)
