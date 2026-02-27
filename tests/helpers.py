#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""
Test helper functions for Apple Notes export validation.

These helper functions are used by test cases to validate:
- Protobuf parsing (ContentBlock structure)
- Markdown generation (output comparison)
"""

import base64
import json
import re
import sqlite3
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional

from noteworthy.markdown_renderer import NoteExporter
from noteworthy.note_content import ContentBlock
from noteworthy.database import DatabaseNoteDataLoader

from notestore_factory import create_notestore_schema


def _build_fixture_db(json_path: Path) -> Path:
    """Build a temporary SQLite database from a test fixture JSON file.

    Creates the NoteStore schema and inserts note_data and attachment rows
    so that DatabaseNoteDataLoader can be used for export tests.

    Args:
        json_path: Path to a *.raw_data.json fixture file

    Returns:
        Path to the temporary SQLite file
    """
    with open(json_path, "r") as f:
        data = json.load(f)

    # Create temp DB (not auto-deleted so the caller can use it)
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    db_path = Path(tmp.name)
    tmp.close()

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    create_notestore_schema(conn)

    # Insert metadata
    conn.execute("INSERT INTO Z_METADATA (Z_UUID) VALUES (?)", ("FIXTURE-UUID",))

    # Insert note data (note pk=1)
    note_data = base64.b64decode(data["note_data"])
    conn.execute("INSERT INTO ZICNOTEDATA (Z_PK, ZNOTE, ZDATA) VALUES (1, 1, ?)", (note_data,))

    # Insert a dummy note record (Z_ENT=12) so queries work
    conn.execute(
        "INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK, Z_ENT, ZTITLE1, ZFOLDER, ZIDENTIFIER) "
        "VALUES (1, 12, 'Test Note', NULL, 'test-note-uuid')"
    )

    # Insert attachments
    pk = 100
    for att in data.get("attachments", []):
        uuid = att["uuid"]
        type_uti = att.get("type")
        title = att.get("title")
        alt_text = att.get("alt_text")
        table_data = base64.b64decode(att["table_data"]) if "table_data" in att else None

        if type_uti == "com.apple.notes.table" and table_data:
            conn.execute(
                "INSERT INTO ZICCLOUDSYNCINGOBJECT "
                "(Z_PK, Z_ENT, ZIDENTIFIER, ZTYPEUTI, ZTITLE, ZALTTEXT, ZMERGEABLEDATA1) "
                "VALUES (?, 5, ?, ?, ?, ?, ?)",
                (pk, uuid, type_uti, title, alt_text, table_data)
            )
        else:
            conn.execute(
                "INSERT INTO ZICCLOUDSYNCINGOBJECT "
                "(Z_PK, Z_ENT, ZIDENTIFIER, ZTYPEUTI, ZTITLE, ZALTTEXT) "
                "VALUES (?, 5, ?, ?, ?, ?)",
                (pk, uuid, type_uti, title, alt_text)
            )
        pk += 1

    conn.commit()
    conn.close()
    return db_path


def load_test_note_from_config(config) -> tuple[DatabaseNoteDataLoader, List[ContentBlock], str, str]:
    """
    Load test note data from TestNoteConfig and parse into ContentBlocks and markdown.

    Also writes test output to test_output/<test_name>/ for inspection:
    - blocks.txt: Human-readable ContentBlock structure
    - generated.md: Our generated markdown
    - reference.md: Apple's reference markdown (for easy comparison)

    Args:
        config: TestNoteConfig object with test data paths

    Returns:
        Tuple of (data_loader, content_blocks, generated_markdown, reference_markdown)
    """
    db_path = _build_fixture_db(config.data_file)
    data_loader = DatabaseNoteDataLoader(str(db_path))
    exporter = NoteExporter(data_loader)

    # Get and decode protobuf (note pk=1 in fixture DB)
    note_data = data_loader.get_note_data(1)
    blocks = exporter.decoder.decode_note(note_data)

    # Generate markdown
    markdown = exporter.markdown_generator.generate(blocks)

    # Load reference markdown
    with open(config.reference_file, 'r') as f:
        reference_md = f.read()

    # Write test output for inspection
    output_dir = Path(__file__).parent / "test_output" / config.name
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write ContentBlock structure
    with open(output_dir / "blocks.txt", 'w') as f:
        f.write(f"ContentBlock Structure for {config.name}\n")
        f.write("=" * 70 + "\n\n")
        for i, block in enumerate(blocks):
            f.write(f"Block {i}:\n")
            f.write(str(block) + "\n")
            f.write("-" * 70 + "\n")

    # Write generated markdown
    with open(output_dir / "generated.md", 'w') as f:
        f.write(markdown)

    # Copy reference markdown for easy comparison
    with open(output_dir / "reference.md", 'w') as f:
        f.write(reference_md)

    return data_loader, blocks, markdown, reference_md


def load_test_note(test_data_file: Path, note_id: int = 0) -> tuple[DatabaseNoteDataLoader, List[ContentBlock], str]:
    """
    Load test note data and parse into ContentBlocks and markdown.

    Args:
        test_data_file: Path to JSON test data file
        note_id: Note ID (ignored — fixture DB always uses pk=1)

    Returns:
        Tuple of (data_loader, content_blocks, generated_markdown)
    """
    db_path = _build_fixture_db(test_data_file)
    data_loader = DatabaseNoteDataLoader(str(db_path))
    exporter = NoteExporter(data_loader)

    # Get and decode protobuf (note pk=1 in fixture DB)
    note_data = data_loader.get_note_data(1)
    blocks = exporter.decoder.decode_note(note_data)

    # Generate markdown
    markdown = exporter.markdown_generator.generate(blocks)

    return data_loader, blocks, markdown


def count_blocks_by_type(blocks: List[ContentBlock]) -> Dict[str, int]:
    """
    Count content blocks by type/style.

    Args:
        blocks: List of ContentBlock objects

    Returns:
        Dictionary mapping type/style to count
    """
    counts = {}
    for block in blocks:
        if block.type == 'text':
            key = block.style or 'body'
        else:
            key = block.attachment.type if block.attachment else 'unknown'
        counts[key] = counts.get(key, 0) + 1
    return counts


def count_text_blocks(blocks: List[ContentBlock]) -> int:
    """Count number of text blocks."""
    return sum(1 for b in blocks if b.type == 'text')


def count_attachment_blocks(blocks: List[ContentBlock]) -> int:
    """Count number of attachment blocks."""
    return sum(1 for b in blocks if b.type == 'attachment')


def normalize_markdown_line(line: str) -> str:
    """
    Normalize a markdown line for comparison.

    Only handles truly cosmetic differences that don't affect rendered output:
    - Table separator dash count (--- vs -----------)
    - Table cell padding (| foo | vs | foo  |)
    - Trailing whitespace variations
    - Attachment paths: Apple uses UUID-based filenames, we resolve to real filenames.
      Both are correct, so normalize to just the link text for comparison.

    Does NOT normalize (these should be fixed in the generator):
    - URL encoding (spaces should be %20 encoded)
    - List marker positions relative to formatting
    - Sequential list numbering
    """
    # Strip trailing whitespace (hard breaks are cosmetic in most renderers)
    line = line.rstrip()

    # Normalize attachment links: [text](Attachments/anything) → [text](Attachments/...)
    line = re.sub(r'\[([^\]]*)\]\(Attachments/[^)]+\)', r'[\1](Attachments/...)', line)

    # Check if this is a table line
    if line.strip().startswith('|'):
        # Normalize table separators (--- vs ----------)
        if set(line.replace('|', '').replace(' ', '').replace('-', '').replace(':', '')) == set():
            # This is a table separator line - normalize dash counts
            parts = [p.strip() for p in line.split('|')]
            normalized_parts = []
            for part in parts:
                if not part:
                    normalized_parts.append('')
                elif ':' in part:
                    normalized_parts.append(' ---:')
                else:
                    normalized_parts.append(' ---')
            line = '|'.join(normalized_parts)
        else:
            # This is a table content line - normalize cell padding
            parts = line.split('|')
            normalized_parts = []
            for part in parts:
                normalized_parts.append(part.strip())
            # Rejoin with single space padding
            line = '|'.join(f' {p} ' if p else '' for p in normalized_parts)
            # Clean up leading/trailing empty cells
            line = line.strip()
            if not line.startswith('|'):
                line = '|' + line
            if not line.endswith('|'):
                line = line + '|'

    return line


def compare_markdown(generated: str, reference: str, normalize: bool = True) -> tuple[bool, List[str]]:
    """
    Compare generated markdown with reference.

    Args:
        generated: Generated markdown string
        reference: Reference markdown string
        normalize: If True, apply normalization to handle minor formatting differences

    Returns:
        Tuple of (matches, differences)
        - matches: True if markdown matches (exactly or semantically if normalized)
        - differences: List of difference descriptions
    """
    differences = []

    # Check exact match first
    if generated == reference:
        return True, []

    # If not exact match, check with normalization
    if normalize:
        gen_lines = [normalize_markdown_line(line) for line in generated.splitlines()]
        ref_lines = [normalize_markdown_line(line) for line in reference.splitlines()]

        # Collapse consecutive blank lines — minor spacing differences between
        # our output and Apple's export shouldn't count as semantic mismatches
        def collapse_blank_lines(lines: List[str]) -> List[str]:
            result = []
            prev_blank = False
            for line in lines:
                is_blank = not line.strip()
                if is_blank and prev_blank:
                    continue
                result.append(line)
                prev_blank = is_blank
            return result

        gen_lines = collapse_blank_lines(gen_lines)
        ref_lines = collapse_blank_lines(ref_lines)

        # Compare line counts
        if len(gen_lines) != len(ref_lines):
            differences.append(
                f"Line count mismatch: generated has {len(gen_lines)} lines, "
                f"reference has {len(ref_lines)} lines"
            )

        # Compare each line
        max_len = max(len(gen_lines), len(ref_lines))
        for i in range(max_len):
            gen_line = gen_lines[i] if i < len(gen_lines) else ''
            ref_line = ref_lines[i] if i < len(ref_lines) else ''

            if gen_line != ref_line:
                differences.append(
                    f"Line {i + 1} differs:\n"
                    f"  Generated: {repr(generated.splitlines()[i] if i < len(generated.splitlines()) else '')}\n"
                    f"  Reference: {repr(reference.splitlines()[i] if i < len(reference.splitlines()) else '')}"
                )

        # If no differences found after normalization, it's a match
        if not differences:
            return True, []
    else:
        # Without normalization, split and compare
        gen_lines = generated.splitlines()
        ref_lines = reference.splitlines()

        differences.append(f"Line count: generated={len(gen_lines)}, reference={len(ref_lines)}")

        max_len = max(len(gen_lines), len(ref_lines))
        for i in range(max_len):
            gen_line = gen_lines[i] if i < len(gen_lines) else ''
            ref_line = ref_lines[i] if i < len(ref_lines) else ''

            if gen_line != ref_line:
                differences.append(
                    f"Line {i + 1} differs:\n"
                    f"  Generated: {repr(gen_line)}\n"
                    f"  Reference: {repr(ref_line)}"
                )

    return False, differences


def get_attachments_by_type(blocks: List[ContentBlock]) -> Dict[str, List[ContentBlock]]:
    """
    Group attachment blocks by type.

    Args:
        blocks: List of ContentBlock objects

    Returns:
        Dictionary mapping attachment type to list of blocks
    """
    attachments = {}
    for block in blocks:
        if block.type == 'attachment' and block.attachment:
            att_type = block.attachment.type
            if att_type not in attachments:
                attachments[att_type] = []
            attachments[att_type].append(block)
    return attachments


def find_blocks_by_style(blocks: List[ContentBlock], style: str) -> List[ContentBlock]:
    """
    Find all text blocks with a specific style.

    Args:
        blocks: List of ContentBlock objects
        style: Style name (e.g., 'title', 'heading', 'bullet')

    Returns:
        List of matching ContentBlock objects
    """
    return [b for b in blocks if b.type == 'text' and b.style == style]


def find_blocks_with_formatting(blocks: List[ContentBlock],
                               bold: Optional[bool] = None,
                               italic: Optional[bool] = None,
                               underlined: Optional[bool] = None,
                               strikethrough: Optional[bool] = None) -> List[ContentBlock]:
    """
    Find text blocks with specific formatting.

    Args:
        blocks: List of ContentBlock objects
        bold: If specified, filter by bold status
        italic: If specified, filter by italic status
        underlined: If specified, filter by underlined status
        strikethrough: If specified, filter by strikethrough status

    Returns:
        List of matching ContentBlock objects
    """
    matches = []
    for block in blocks:
        if block.type != 'text':
            continue

        if bold is not None and block.bold != bold:
            continue
        if italic is not None and block.italic != italic:
            continue
        if underlined is not None and block.underlined != underlined:
            continue
        if strikethrough is not None and block.strikethrough != strikethrough:
            continue

        matches.append(block)

    return matches


def extract_tables_from_markdown(markdown: str) -> List[Dict[str, Any]]:
    """
    Extract tables from markdown and return their cell structure and separator lines.

    Args:
        markdown: Markdown string

    Returns:
        List of table dictionaries with keys:
        - 'rows': List of rows, where each row is a list of cells
        - 'separator': The separator line (e.g., '| --- | --- |')
    """
    tables = []
    lines = markdown.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Check if this line starts a table (contains |)
        if '|' in line and line.startswith('|'):
            table_rows = []
            separator_line = None

            # Extract all consecutive table lines
            while i < len(lines) and '|' in lines[i]:
                row_line = lines[i].strip()

                # Parse cells from this row
                # Split by | and strip whitespace
                cells = [cell.strip() for cell in row_line.split('|')]

                # Remove empty first/last cells (from leading/trailing |)
                if cells and cells[0] == '':
                    cells = cells[1:]
                if cells and cells[-1] == '':
                    cells = cells[:-1]

                # Check if this is a separator row (contain only -, :, and whitespace)
                if cells and all(set(cell.replace('-', '').replace(':', '').strip()) == set() for cell in cells):
                    # This is a separator row, save it
                    separator_line = row_line
                    i += 1
                    continue

                table_rows.append(cells)
                i += 1

            if table_rows:
                tables.append({
                    'rows': table_rows,
                    'separator': separator_line
                })
        else:
            i += 1

    return tables


def validate_table_structure(table: List[List[str]]) -> tuple[bool, Optional[str]]:
    """
    Validate that a table has consistent column counts across all rows.

    Args:
        table: List of rows, where each row is a list of cells

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not table:
        return True, None

    # Get expected column count from first row
    expected_cols = len(table[0])

    # Check all rows have the same column count
    for i, row in enumerate(table):
        if len(row) != expected_cols:
            return False, f"Row {i} has {len(row)} columns, expected {expected_cols}. Row: {row}"

    return True, None
