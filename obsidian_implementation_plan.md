# Obsidian Export — Implementation Plan

## Context

The companion document `obsidian_requirements.md` specifies a second export mode for Noteworthy that produces an Obsidian-vault-compatible directory: notes as flat `.md` files (no per-note dirs), a single top-level `assets/` folder, wikilinks instead of relative-path markdown links, frontmatter Properties instead of `.noteworthy.json`, smart and locked notes skipped, and a `.obsidian/app.json` written so the vault opens correctly.

The backup mode is untouched. Both modes must coexist; the CLI dispatches to one or the other based on a `--obsidian` flag and a target-directory inspection that refuses to corrupt an existing export of the wrong type.

This plan covers how to fit the new mode into the existing code without forcing a large refactor of the backup path.

## Overall approach

Three kinds of code change:

1. **CLI dispatch.** Add `--obsidian` to `noteworthy.py`'s argparse. Before doing any work, inspect the target directory and either dispatch to the existing backup flow or to a new Obsidian flow, erroring on mode mismatch.
2. **Renderer made dialect-aware.** `MarkdownGenerator` / `NoteExporter` in `markdown_renderer.py` accept a `dialect` parameter (a small enum or class). The handful of branch points — inter-note link emission, attachment emission, title-line stripping, HTML-emission sites — switch on the dialect. No abstract base classes, no plugin system; just a parameter checked at the existing emission sites.
3. **New `obsidian/` subpackage.** All Obsidian-specific layout and metadata logic lives here, parallel to the existing top-level orchestration in `noteworthy.py`. This keeps backup mode's code path visibly unchanged.

This deliberately avoids a deeper refactor (e.g., a `LayoutWriter` ABC with two implementations). The two modes' file layouts are different enough that sharing a single writer would be more confusing than helpful.

## New module layout

```
src/noteworthy/
├── noteworthy.py                # CLI entry; dispatch added
├── note_copy.py                 # unchanged (backup mode only)
├── markdown_renderer.py         # extended with dialect parameter
├── notes_datatypes.py           # unchanged in shape, possibly new helpers
├── extract_notes_db.py          # unchanged
└── obsidian/                    # NEW subpackage
    ├── __init__.py
    ├── dialect.py               # constants/helpers for wikilink + embed emission
    ├── filename.py              # vault-wide sanitization + collision pass
    ├── frontmatter.py           # YAML emitter
    ├── sync.py                  # top-level orchestrator (parallel to noteworthy._make_copies)
    ├── target_state.py          # target-directory inspection / classification
    └── vault_config.py          # writes .obsidian/app.json
```

The subpackage is the only structural addition. Each file is small and single-purpose.

## File-by-file change summary

### `noteworthy.py` (modify)

- `_parse_args`: add `--obsidian` flag (default `False`).
- New top-level function `_inspect_target(target_path) -> TargetState` calling into `obsidian/target_state.py`.
- Refactor `make_copies` (line 325) into a thin dispatcher:
  - Call `_inspect_target`.
  - Validate mode against state (the §11.1 table). On mismatch, print error to stderr and `sys.exit(1)` with the message in the requirements doc.
  - If `--obsidian`: call `obsidian.sync.run(target_path, db_path, verbose=...)`.
  - Else: call the existing `_make_copies()` body (extracted into `_make_backup_copies()` so the obsidian path doesn't trigger viewer install, deleted-folder creation, etc.).
- The module-level globals (`_target_path`, `_db_path`, `_verbose`, `_counts`) stay for the backup path; the Obsidian path keeps its own state inside `obsidian/sync.py`.

### `markdown_renderer.py` (modify — minimal-surface changes)

- Add an `ExportDialect` enum (or import from `obsidian/dialect.py`): `BACKUP` and `OBSIDIAN`.
- Thread `dialect: ExportDialect = ExportDialect.BACKUP` through `NoteExporter.__init__` (line 1082) and `MarkdownGenerator.__init__` (line 420). Default is `BACKUP` so existing callers don't break.
- Branch sites (existing line numbers; expect minor drift after edits):
  - **Inter-note link** at ~`:990`. Backup: existing `[name](relative_path)`. Obsidian: `[[Target]]` (or `[[Target|Display]]` if alias requested). Target name is the *final* on-disk filename (without `.md`), so this requires the filename-uniqueness pass to have already run — the dialect helper receives a `note_path_by_uuid` whose values are filenames, not full paths.
  - **Image / attachment refs** at ~`:1025–1061`. Backup: existing `![title](Attachments/file)` / `[title](Attachments/file)`. Obsidian: `![[file.ext]]` if the lowercased extension is in `obsidian.dialect.IMAGE_EXTENSIONS`, else `[[file.ext]]`. Filename is the vault-wide unique attachment name.
  - **Inline link to missing target** — already falls back today (lines ~994); in Obsidian mode the fallback becomes `[[Original Name]]` (unresolved wikilink) instead of `[name](../..)`.
- **Title-line stripping (Obsidian-only)**: implemented in `generate(blocks)` (line 1120) or in `NoteExporter.export_note` after generation. Cleanest place is just before serializing the first block: if `dialect is OBSIDIAN` and the first non-empty block's plain text matches the note name (case-insensitive, ignoring heading/bold/italic wrapping), drop it. The text-extraction helper is a small new method on `MarkdownGenerator`.
- **HTML elimination**: locate every site that emits inline HTML today. The Explore agent's scan found no `<span` literals in source — the user reports HTML emission happens "under some circumstances," so this needs a focused investigation as the first task in Stage 3 (probably underline or color handling in inline run rendering). Once found, each site gets a `if dialect is OBSIDIAN` branch that translates (underline → `==…==`, strikethrough → `~~…~~`) or drops the styling while keeping the text.

### New: `obsidian/target_state.py`

```
class TargetState(Enum):
    EMPTY            # path doesn't exist OR exists and is empty
    OBSIDIAN         # contains .obsidian/
    BACKUP           # contains .noteworthy.json (anywhere) and no .obsidian/
    UNRELATED        # non-empty, neither signal

def inspect(target_path: Path) -> TargetState: ...
```

Implementation: `os.scandir` shallow on `target_path`; `.obsidian` check is direct; backup check uses a single recursive `find` (or `rglob('.noteworthy.json')`) — fine even on large trees since it short-circuits on first hit.

### New: `obsidian/filename.py`

Pure functions; no I/O.

```
def sanitize_for_obsidian(name: str) -> str
    # Apply existing _sanitize_name rules, then fullwidth-replace # | ^ [ ]
    # Return "Untitled" if result is empty/whitespace

def assign_unique_names(items: list[Item]) -> dict[Item, str]
    # Vault-wide collision resolution
    # items have .name and .sort_key (creation_date, id)
    # Returns mapping with " (2)", " (3)" suffixes appended where needed
    # For attachments, suffix goes before the extension
```

The note pass and the attachment pass both call `assign_unique_names` with their respective `Item` shapes. Output is the final on-disk basename (no path, no extension for notes; with extension for attachments).

### New: `obsidian/frontmatter.py`

```
def render(
    note: Note,
    *,
    final_filename: str,                  # without .md
    account_name: str,
    folder_path: str,                     # slash-joined, may be ""
    aliases: list[str],                   # original name(s), only when != final_filename
    extra_user_keys: dict | None = None,  # preserved across re-export
) -> str
```

Returns the full `---\n…\n---\n` block. Stable key ordering: `aliases, tags, created, modified, account, folder, apple_notes_uuid`, then any preserved user keys in the order we read them.

Implementation details:
- Use `yaml.safe_dump` with `default_flow_style=False, sort_keys=False, allow_unicode=True` for the body; emit the `---` delimiters manually so we control the surrounding whitespace.
- Datetime values: convert to local tz with `dt.astimezone().replace(tzinfo=None)`, then `.isoformat(timespec='seconds')`.
- Tag values: pre-sanitize per §8 (whitespace → `-`, drop chars outside `[a-z0-9_/\-]`, skip empty / all-numeric).
- Quote wikilink values in `aliases` only if they actually contain wikilink syntax (none of ours will — they're plain display names).

### New: `obsidian/vault_config.py`

One function: `ensure_app_json(target_path: Path)`. If `.obsidian/app.json` doesn't exist, create the directory and write the four-key JSON from requirements §9. If it exists, do nothing (preserve user's plugin/theme installs).

### New: `obsidian/sync.py`

The top-level orchestrator. Sketch:

```
def run(target_path: Path, db_path: Path, *, verbose: bool) -> None:
    # 1. Extract from Apple Notes (shared with backup mode)
    accounts = extract_folders_and_notes(db_path=db_path)

    # 2. Inventory existing vault (if any) for re-export
    existing = _scan_vault(target_path)   # uuid -> (path, parsed_frontmatter)

    # 3. Build the export plan in memory
    plan = _build_plan(accounts, existing, target_path)
    # plan has: notes_to_write, notes_to_move, notes_to_rename,
    #           attachments_to_copy, warnings (locked notes)

    # 4. Emit warnings (locked notes, etc.) before any disk writes

    # 5. Execute the plan
    _write_assets(plan, target_path)
    _write_notes(plan, target_path, dialect=OBSIDIAN)
    _remove_orphans(plan, target_path)    # only if --prune; deferred

    # 6. Ensure .obsidian/app.json
    vault_config.ensure_app_json(target_path)
```

Subfunctions:

- `_scan_vault`: walks `*.md` files, parses frontmatter (re-using the same `yaml` lib used in `frontmatter.render`), pulls `apple_notes_uuid`, builds the map. Also captures any user-added frontmatter keys so they can be preserved when we rewrite the file.
- `_build_plan`: this is the brains. Drops locked / deleted notes (collecting warnings). Decides single-account vs multi-account layout. Computes folder paths. Runs `filename.assign_unique_names` over the surviving notes vault-wide and over all attachments vault-wide. Detects renames/moves by comparing to `existing`. Produces a deterministic, do-or-don't plan.
- `_write_notes`: for each note, render markdown body via `NoteExporter` with `dialect=OBSIDIAN`, build frontmatter via `frontmatter.render`, concatenate, write. For renames/moves, do an `os.replace` from the old path. Path-less wikilinks survive moves automatically.
- `_write_assets`: copy each attachment from its source path to `target/assets/<unique_name>`. Reuse `shutil.copy2` like backup mode.

Locked-note warning: emitted to `stderr` via `print(..., file=sys.stderr)` during `_build_plan`. Format: `warning: skipping locked note "<name>" in account "<account>"`.

### `extract_notes_db.py`, `note_content.py`, `notes_datatypes.py` (largely unchanged)

- The data model (`Account` / `Folder` / `Note`) carries everything we need. No new fields.
- `_sanitize_name` is left alone; the Obsidian-extended version lives in `obsidian/filename.py`.
- If the DB extractor doesn't currently surface "is this note locked?", that needs to be added — probably one new column in the SQL query and a `locked: bool` attribute on `Note`. Verify during Stage 1.

## Development methodology — test-driven

Every stage follows TDD:

1. **Red** — write the test(s) for the next slice of behavior. Run them; they should fail (or, for new modules, fail at import). Confirming the red state catches tests that accidentally pass against nothing.
2. **Green** — write the minimum production code to make the new test pass. Don't add code the tests don't drive.
3. **Refactor** — clean up only what the just-written code makes obviously rough. Tests stay green throughout.

The full existing test suite (~384 tests) must remain green at every step — never disable, skip, or `xfail` an existing test to make new work move faster. If a refactor needs an existing test updated, do it as its own commit.

Each stage below lists its tests first, deliberately.

## Implementation stages

Six small, mergeable stages. Each ends with passing tests for what it added.

### Stage 1 — CLI + dispatch + target-state detection

- **Tests first:** `tests/test_obsidian/test_target_state.py` — one test per row of the §11.1 table (empty dir, Obsidian vault, backup-mode export, unrelated non-empty dir). `tests/test_obsidian/test_cli_dispatch.py` — `--obsidian` flag parses; mode/state mismatch produces the exact stderr message and a nonzero exit; backup-mode invocation path still produces the expected greeting/log lines (lock down behavior before we move it).
- Code: add `obsidian/target_state.py` to make the classification tests pass.
- Code: add `--obsidian` to argparse; refactor `make_copies` into the dispatcher; extract existing body into `_make_backup_copies()` (mechanical move, no behavior change).
- Code: dispatch in `--obsidian` mode to a stub `obsidian.sync.run` that just prints `obsidian export not yet implemented` and exits 0.

This is shippable on its own — backup mode keeps working, and `--obsidian` says "not yet."

### Stage 2 — Filename uniqueness + frontmatter (pure functions)

- **Tests first:** `tests/test_obsidian/test_filename.py` — sanitize-with-fullwidth replacements (one test per forbidden char), `Untitled` fallback for empty/all-control names, multi-note collision ordering, attachment collision with extension preservation. `tests/test_obsidian/test_frontmatter.py` — exact YAML output for a representative note, key ordering, datetime formatting in local time, tag sanitization, empty-aliases omission, user-extra-keys preserved.
- Code: implement `obsidian/filename.py` and `obsidian/frontmatter.py` to satisfy the tests.
- Investigation (during this stage, not a blocker): verify whether `Note.creation_date` / `modification_date` are timezone-aware; adjust the local-time conversion if needed.

### Stage 3 — Dialect-aware renderer

- **Tests first:** extend `tests/test_export.py` (or a sibling) with Obsidian-dialect parametrization for every existing fixture, with new expected outputs in `tests/test_data/*.obsidian.md`. Add focused unit tests for each branch: inter-note wikilink, image embed by extension, non-image link by extension, missing-target unresolved wikilink, title-line stripping (plain / heading / bold variants), no-HTML output (find a fixture that exercises HTML emission in backup mode, assert the Obsidian-mode output contains zero angle brackets except in code blocks).
- Investigation: locate the HTML-emission sites in `markdown_renderer.py` (Stage 3's first task).
- Code: add `obsidian/dialect.py` exporting `ExportDialect` and `IMAGE_EXTENSIONS`; thread the `dialect` param through `NoteExporter` and `MarkdownGenerator`; implement each branch to satisfy the tests.

### Stage 4 — Obsidian sync orchestration

- **Tests first:** `tests/test_obsidian/test_sync.py` (shape mirrors `tests/test_sync.py`) — single-account flat layout, multi-account nested layout, attachments centralized in `assets/`, no `.noteworthy.json` written, no `Deleted/` folder, smart folders absent, `.obsidian/app.json` written with the four expected keys, locked-note warning emitted to stderr.
- Code: implement `obsidian/vault_config.py` and full `obsidian/sync.py`; replace the Stage-1 stub.

### Stage 5 — Re-export semantics

- **Tests first:** rename a note in the fixture between two runs → expect `.md` renamed and old name added to `aliases`; move a note between folders → expect file relocated, wikilinks still resolve (still `[[Name]]`); user adds an extra frontmatter key → expect it preserved on re-export; running the exporter twice in a row over the same target produces zero file modifications (byte-for-byte stable).
- Code: implement `_scan_vault` and the rename/move logic in `_build_plan`; preserve user-added frontmatter keys when rewriting.

### Stage 6 — Polish

- **Tests first:** dedicated test for the locked-note warning format (message text, stderr destination, no abort).
- Code: surface the locked-note warning if not already in Stage 4; finalize wording.
- Manual: real-vault smoke test — developer runs against own Apple Notes, opens in Obsidian, eyeballs.
- Docs: README section documenting `--obsidian`.

## Critical files referenced (line numbers from current main)

- `src/noteworthy/noteworthy.py:27` — argparse → add flag.
- `src/noteworthy/noteworthy.py:293–323` — `_make_copies` → split into backup-only path.
- `src/noteworthy/markdown_renderer.py:420` — `MarkdownGenerator.__init__` → accept `dialect`.
- `src/noteworthy/markdown_renderer.py:990` — inter-note link emission → dialect branch.
- `src/noteworthy/markdown_renderer.py:1025–1061` — attachment emission → dialect branch.
- `src/noteworthy/markdown_renderer.py:1082` — `NoteExporter.__init__` → accept `dialect`.
- `src/noteworthy/markdown_renderer.py:1120` — `generate(blocks)` → title-line stripping for Obsidian.
- `src/noteworthy/markdown_renderer.py:1148` — `_resolve_attachment_filenames` → reuse pattern for vault-wide attachment collisions.
- `src/noteworthy/markdown_renderer.py:1197` — `attachment.unique_filename` → set from vault-wide pass.
- `src/noteworthy/notes_datatypes.py:12` — `_sanitize_name` → reused by `obsidian/filename.py`.
- `src/noteworthy/notes_datatypes.py:152` — `Note.to_metadata_dict` → fields that map to frontmatter (reference only; no change here).
- `src/noteworthy/note_copy.py` — backup-only, unchanged.
- `tests/test_export.py` — extended with Obsidian-dialect parametrization.
- `tests/test_sync.py` — referenced as a shape for new `tests/test_obsidian_sync.py`.

## Verification

End-to-end checks to run as the work progresses:

1. **Each stage's unit tests pass.** Existing suite (~384 tests) must stay green throughout.
2. **`pytest tests/` after Stage 4** — Obsidian export tests + existing tests both pass.
3. **Manual: open the developer's exported vault in Obsidian.** Confirm: notes in correct folders, internal wikilinks resolve (no unresolved-link badges except for the ones we intentionally created — deleted-target case), images embed, PDFs link, properties panel populated, tag pane lists tags, no `<span>` visible in source mode.
4. **Re-run idempotence.** Run twice against the same target dir; `git diff` (if vault is git-tracked) shows zero changes on the second run.
5. **Mode-mismatch refusal.** Manual: point `--obsidian` at a backup-mode export, expect the specific error message and no writes. Inverse: omit `--obsidian` against an Obsidian vault, same.

## Open implementation questions

These can be answered during the work, not before:

1. **Where exactly does the renderer emit HTML today?** Stage 3 first task. Probably underline/color handling; possibly something else.
2. **Is `Note.locked` already extracted from the DB?** If not, add it in Stage 1 alongside the dispatch work (one column to the SQL, one bool field on `Note`).
3. **Datetime timezone awareness from the DB.** Cheap to verify with a `python -c` against the real store; tweak `frontmatter.render` accordingly.
4. **`--prune` flag.** Out of scope for this plan; mentioned only to flag the orphan question.
