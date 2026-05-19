# Noteworthy

Export Apple Notes to Markdown with full formatting preservation.

## Features

- Export notes from Apple Notes to Markdown format
- Preserves text formatting (bold, italic, underline, strikethrough, highlights)
- Supports all list types (bullet, dashed, numbered, checklist)
- Handles headings, subheadings, and block quotes
- Exports code blocks with proper fencing
- Handles attachments (images, PDFs, documents)
- Full table support with correct row/column ordering
- Built-in web viewer for browsing backups (auto-installed, no dependencies required)
- **Obsidian export mode** (`--obsidian`): writes a ready-to-open Obsidian vault
  with wikilinks, frontmatter Properties, and a single `assets/` folder

## Requirements

- macOS (Apple Notes database access is macOS-only)
- Python 3.12+
- Full Disk Access enabled for your terminal (System Settings > Privacy & Security > Full Disk Access)
- Default Notes database location: `~/Library/Group Containers/group.com.apple.notes/NoteStore.sqlite`

## Installation

```bash
git clone https://github.com/bvchess/noteworthy.git
cd noteworthy
scripts/setup.sh
```

The setup script creates a virtual environment, checks that you have Python 3.12+, and installs the package.

If you want to run the tests or work on the code, install the dev extras as well:

```bash
source .venv/bin/activate
pip install -e ".[dev]"
```

## Usage

```bash
scripts/noteworthy <output_dir>
```

Example:

```bash
scripts/noteworthy ~/Documents/NotesBackup
```

The export creates a folder tree for each account and folder, with one directory per note:

```
NotesBackup/
├── iCloud/
│   ├── Work/
│   │   └── Project Plan/
│   │       ├── Project Plan.md
│   │       └── Attachments/
│   └── Personal/
│       └── Grocery List/
│           └── Grocery List.md
└── View Notes.command
```

### Launching the Viewer

Double-click `View Notes.command` inside the backup directory. This launches a local server using `/usr/bin/python3` and opens your browser.

To launch manually:

```bash
python3 <backup_dir>/.noteworthy-viewer/server.py <backup_dir>
```

## Obsidian Export Mode

Pass `--obsidian` (short: `-o`) to write an [Obsidian](https://obsidian.md) vault instead of a Noteworthy backup:

```bash
scripts/noteworthy ~/Documents/MyVault --obsidian
# or, equivalently:
scripts/noteworthy ~/Documents/MyVault -o
```

Add `-v` / `--verbose` to see per-note progress and a summary of attachments copied vs. skipped as already-up-to-date.

The output is shaped to be opened directly in Obsidian — folders match Apple Notes, links between notes become wikilinks, dates and tags appear in the Properties panel, and a minimal `.obsidian/app.json` is written so the vault behaves correctly on first open.

### What's different from backup mode

| | Backup mode (default) | Obsidian mode (`--obsidian`) |
| --- | --- | --- |
| Notes | one directory per note | one `.md` file per note |
| Attachments | per-note `Attachments/` subdir | single top-level `assets/` |
| Inter-note links | `[Name](relative/path.md)` | `[[Name]]` (path-less wikilinks) |
| Metadata | `.noteworthy.json` next to each note | YAML frontmatter at the top of each `.md` |
| Smart folders | reproduced as symlink trees | skipped |
| Deleted notes | preserved under `Deleted/` | skipped |
| Viewer | bundled web viewer | open the directory in Obsidian |

### Layout

```
MyVault/
├── .obsidian/
│   └── app.json
├── assets/                       # every attachment, flat, globally-unique names
│   ├── photo.jpg
│   └── receipt.pdf
├── Work/
│   └── Project Plan.md
└── Notes/
    └── Grocery List.md
```

When more than one Apple Notes account is present (e.g. iCloud + On My Mac), folders sit under `<Account>/` instead of the vault root.

### Re-exporting

You can run the exporter again over an existing vault and it'll update only what's changed:

- A note renamed in Apple Notes → the `.md` file is renamed; the old name is added to `aliases:` so old wikilinks still resolve.
- A note moved between folders → the `.md` file relocates; path-less wikilinks survive the move.
- A frontmatter key you added by hand (`priority: high`, custom tags, etc.) → preserved.
- A note no longer in Apple Notes → left in place (you may have edited it).

### Target-directory safety

Before any writes, the exporter inspects the target and refuses combinations that would corrupt an existing export:

| Target state | `--obsidian` | No flag |
| --- | --- | --- |
| Empty or doesn't exist | create Obsidian vault | create backup |
| Has `.obsidian/` | re-export Obsidian vault | **error** — re-run with `--obsidian` |
| Has `.noteworthy.json` somewhere | **error** — `--obsidian` would corrupt the backup | re-export backup |
| Non-empty, neither marker | **error** in both modes | |

See [`obsidian_requirements.md`](obsidian_requirements.md) for the full specification.

## Privacy

Noteworthy reads your local Apple Notes database and writes a local backup directory. It does not upload data anywhere. Be careful sharing backups publicly, as they may contain sensitive content.

## Project Structure

```
noteworthy/
├── src/noteworthy/          # Main package
│   ├── noteworthy.py        # CLI entry point and sync orchestration
│   ├── note_content.py      # Protobuf decoding and data structures
│   ├── markdown_renderer.py # Markdown generation and attachment handling
│   ├── database.py          # SQLite database abstraction for note data
│   ├── extract_notes_db.py  # SQLite queries for Account/Folder/Note hierarchy
│   ├── notes_datatypes.py   # Data models: Account, Folder, Note classes
│   ├── note_copy.py         # Note export coordination
│   ├── notestore_pb2.py     # Generated protobuf bindings for Apple Notes format
│   ├── notestore.proto      # Protobuf schema (for reference/regeneration)
│   └── viewer/              # Self-contained backup viewer
│       ├── server.py        # HTTP server and API endpoints
│       ├── markdown_to_html.py  # Markdown-to-HTML converter
│       ├── search.py        # FTS5 full-text search index
│       ├── backup_reader.py # Reads .noteworthy.json tree
│       └── static/          # SPA frontend (HTML, CSS, JS)
├── tests/
│   ├── test_export.py       # Parametrized test suite
│   ├── test_viewer/         # Viewer tests
│   ├── test_data/           # Test fixtures (raw protobuf + expected output)
│   ├── conftest.py          # Test discovery and parametrization
│   ├── helpers.py           # Test utilities for validation
│   └── extract_test_data.py # Utility to create new test fixtures
├── debug/                   # Debug scripts for protobuf investigation
└── pyproject.toml
```

### Key Modules

**note_content.py** - Protobuf decoding and data structures:
- `ProtobufDecoder`: Parses gzipped protobuf into `ContentBlock` objects
- `ContentBlock`, `Attachment`, `TableData`: Data structures for note content
- `FormattingState`: Tracks formatting attributes during parsing

**markdown_renderer.py** - Markdown generation:
- `MarkdownGenerator`: Converts ContentBlocks to Markdown text
- `AttachmentResolver`: Resolves attachment UUIDs and extracts table data
- `NoteExporter`: Coordinates the full export pipeline

**database.py** - Database access:
- `DatabaseNoteDataLoader`: Reads note data, attachments, and tables from Apple Notes SQLite database

## How It Works

### Export Pipeline

1. **Database Access**: Read compressed protobuf from `ZICNOTEDATA.ZDATA`
2. **Protobuf Decoding**: Decompress (gzip) and parse using generated protobuf bindings
3. **Content Blocks**: Walk `attribute_run` array to build `ContentBlock` objects with formatting
4. **Attachment Resolution**: For each attachment, resolve metadata and extract table data
5. **Markdown Generation**: Render blocks as GitHub-flavored Markdown

### Apple Notes Protobuf Format

Notes are stored as gzip-compressed protobuf in the `ZDATA` column. The structure includes:
- `note_text`: The raw text content
- `attribute_run[]`: Array of formatting runs with style, font weight, emphasis, etc.
- `attachment_info`: References to embedded attachments (images, tables, files)

### Table CRDT Format

Tables use a CRDT (Conflict-free Replicated Data Type) structure stored in `ZMERGEABLEDATA1`. The format is complex because it supports real-time collaborative editing.

**Key structures:**
```
MergableDataProto
└── mergable_data_object
    └── mergeable_data_object_data
        ├── mergeable_data_object_entry[]  # Array of all entries
        ├── mergeable_data_object_uuid_item[]  # UUID lookup table
        └── mergeable_data_object_key_item[]  # Key name lookup (crRows, crColumns, etc.)
```

**Entry types:**
- `custom_map`: Key-value pairs (main table entry has crRows, crColumns, cellColumns)
- `ordered_set`: Row/column ordering with `ordering.array.attachment[]` for display positions
- `dictionary`: Cell storage mapping (column → row → cell content)
- `note`: Individual cell content with text and formatting

**Display ordering algorithm:**

The critical insight is that display order is stored in `ordering.array.attachment[]`, not in `ordering.contents[]` position:

1. Each attachment has `uuid` (bytes) and `index` (display position)
2. Look up the UUID in `mergeable_data_object_uuid_item` to get the item index
3. Each entry has a `UUIDIndex` value that maps to this table
4. Build `UUIDIndex → display_position` mapping for rows and columns
5. When extracting cells, look up each cell's row/column key to find display position

```
crRows.ordering:
  ├── array.attachment[]: [{uuid: ..., index: 0}, {uuid: ..., index: 1}, ...]  # Display positions
  └── contents.element[]: [(key_entry, value_entry), ...]  # Entry pairs

To find display row for a cell:
  1. cell has row_key (entry index)
  2. entries[row_key].UUIDIndex → uuid_items[UUIDIndex] → uuid_bytes
  3. uuid_bytes lookup in array.attachment[] → display position
```

## Backup Viewer

Every sync installs a self-contained web viewer into the backup directory. The viewer lets you browse notes in a three-column Apple Notes-style interface (folder tree, note list, note content) -- even if noteworthy is no longer installed on the machine.

### Launching the Viewer

Double-click `View Notes.command` in the backup directory. This opens a local web server and your browser automatically. The viewer runs entirely offline using only the system Python (`/usr/bin/python3`), with no external dependencies.

To launch manually:

```bash
python3 <backup_dir>/.noteworthy-viewer/server.py <backup_dir>
```

### What's Installed

Each sync copies the viewer into the backup:

```
backup_dir/
├── View Notes.command          # Double-clickable macOS launcher
└── .noteworthy-viewer/         # Hidden directory with viewer code
    ├── server.py               # HTTP server + JSON API
    ├── markdown_to_html.py     # Markdown-to-HTML converter
    ├── search.py               # FTS5 full-text search
    ├── backup_reader.py        # Reads .noteworthy.json metadata
    └── static/
        ├── index.html
        ├── style.css
        └── app.js
```

### Features

- Three-column layout matching Apple Notes (sidebar, note list, content)
- Full-text search with ranked results and highlighted snippets
- Light/dark mode with system preference detection and manual toggle
- Image lightbox for full-size viewing
- Attachment serving with correct MIME types
- Smart folder support
- Note-to-note link navigation

## Running Tests

```bash
# Run all tests
.venv/bin/python -m pytest tests/ -v

# Run export tests only
.venv/bin/python -m pytest tests/test_export.py -v

# Run viewer tests only
.venv/bin/python -m pytest tests/test_viewer/ -v

# Run tests for a specific note
.venv/bin/python -m pytest tests/test_export.py -k "TEST_NOTE" -v
```

### Adding Test Fixtures

```bash
# Extract a note from the database as a test fixture
.venv/bin/python tests/extract_test_data.py <note_id> <test_name>

# Then export reference markdown from Apple Notes app and save as:
# tests/test_data/<test_name>.apple_generated.md
```

## Dependencies

- `protobuf` - For parsing Apple Notes protobuf format

## License

MIT
