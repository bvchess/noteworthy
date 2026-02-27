#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""
Protobuf decoding and data structures for Apple Notes.

This module handles decoding Apple Notes protobuf format into structured data.
"""

import gzip
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from .notestore_pb2 import NoteStoreProto


def utf16_slice(text: str, start: int, length: int) -> str:
    """
    Slice a string using UTF-16 code unit positions.

    Apple Notes stores AttributeRun lengths in UTF-16 code units,
    not Unicode codepoints. This function converts by encoding to
    UTF-16-LE (2 bytes per code unit), slicing, then decoding.
    """
    utf16_bytes = text.encode('utf-16-le')
    byte_start = start * 2
    byte_end = (start + length) * 2
    return utf16_bytes[byte_start:byte_end].decode('utf-16-le')


@dataclass
class TableData:
    """Represents extracted table data with alignment information."""
    grid: List[List[str]]  # 2D grid of cell contents
    column_alignments: List[int]  # 0=left, 1=center, 2=right

    ALIGNMENT_NAMES = {0: 'left', 1: 'center', 2: 'right'}

    def __str__(self) -> str:
        """Display table structure with dimensions and alignments."""
        num_rows = len(self.grid)
        num_cols = len(self.grid[0]) if self.grid else 0

        lines = [f"TableData({num_rows}x{num_cols})"]

        # Show column alignments
        if self.column_alignments:
            alignment_str = ', '.join([
                f"col{i}={self.ALIGNMENT_NAMES.get(align, 'unknown')}"
                for i, align in enumerate(self.column_alignments)
            ])
            lines.append(f"  Alignments: {alignment_str}")

        # Show grid preview (first 3 rows)
        if self.grid:
            lines.append("  Grid preview:")
            for i, row in enumerate(self.grid[:3]):
                row_preview = ' | '.join([cell[:15] + '...' if len(cell) > 15 else cell for cell in row])
                lines.append(f"    Row {i}: [{row_preview}]")
            if len(self.grid) > 3:
                lines.append(f"    ... and {len(self.grid) - 3} more rows")

        return '\n'.join(lines)


@dataclass
class Attachment:
    """Represents an attachment with optional resolved metadata."""
    uuid: str
    type: str
    file_path: Optional[str] = None
    title: Optional[str] = None
    table_data: Optional[TableData] = None  # Set if this is a table attachment
    alt_text: Optional[str] = None  # Alternative text (e.g., for hashtags)
    unique_filename: Optional[str] = None  # Collision-resolved filename for export
    token_content_identifier: Optional[str] = None  # For note links: "applenotes:note/UUID?..." pointing to target
    gallery_children: Optional[List['Attachment']] = None  # For galleries: list of child attachments

    def __str__(self) -> str:
        """Display attachment information."""
        lines = [f"Attachment(uuid={self.uuid[:8]}...)"]
        lines.append(f"  Type: {self.type}")

        if self.title:
            lines.append(f"  Title: {self.title}")

        if self.file_path:
            lines.append(f"  Path: {self.file_path}")

        if self.table_data:
            # Indent the table data display
            table_str = str(self.table_data)
            indented = '\n'.join(['  ' + line for line in table_str.split('\n')])
            lines.append(indented)

        return '\n'.join(lines)


@dataclass
class ContentBlock:
    """Represents a piece of content in a note."""
    type: str  # 'text', 'attachment'
    text: Optional[str] = None
    style: Optional[str] = None  # 'title', 'heading', 'subheading', 'monospaced', 'bullet', 'dashed', 'numbered', 'checklist', 'body'
    attachment: Optional[Attachment] = None  # Set if type='attachment'
    # Block-level formatting
    block_quote: bool = False
    checklist_done: bool = False
    indent_level: int = 0  # Nesting depth for list items (0=top-level, 1=first indent, etc.)
    alignment: Optional[int] = None  # 0/None=left, 1=center, 2=right, 3=justify
    # Inline formatting
    bold: bool = False
    italic: bool = False
    underlined: bool = False
    strikethrough: bool = False
    emphasis_color: Optional[int] = None  # Apple Notes predefined highlight colors (1-5)
    link: Optional[str] = None  # URL for hyperlinks
    # Metadata
    timestamp: Optional[int] = None  # Unix timestamp (seconds) of when this text run was created/modified

    ALIGNMENT_NAMES = {0: 'left', 1: 'center', 2: 'right', 3: 'justify'}

    def __str__(self) -> str:
        """Display content block with all formatting details."""
        lines = [f"ContentBlock(type={self.type})"]

        # Text content
        if self.text is not None:
            # Show text preview (truncate if long)
            text_preview = self.text[:60].replace('\n', '\\n')
            if len(self.text) > 60:
                text_preview += '...'
            lines.append(f"  Text: {repr(text_preview)}")

        # Style
        if self.style:
            lines.append(f"  Style: {self.style}")

        # Inline formatting (only show if True)
        formatting_flags = []
        if self.bold:
            formatting_flags.append('bold')
        if self.italic:
            formatting_flags.append('italic')
        if self.underlined:
            formatting_flags.append('underlined')
        if self.strikethrough:
            formatting_flags.append('strikethrough')
        if formatting_flags:
            lines.append(f"  Formatting: {', '.join(formatting_flags)}")

        # Block-level formatting
        if self.block_quote:
            lines.append("  Block quote: Yes")
        if self.checklist_done:
            lines.append("  Checklist: Done")
        if self.indent_level:
            lines.append(f"  Indent level: {self.indent_level}")

        # Alignment
        if self.alignment is not None and self.alignment != 0:
            alignment_name = self.ALIGNMENT_NAMES.get(self.alignment, f'unknown({self.alignment})')
            lines.append(f"  Alignment: {alignment_name}")

        # Color
        if self.emphasis_color is not None:
            lines.append(f"  Highlighted (emphasis_color={self.emphasis_color})")

        # Timestamp
        if self.timestamp is not None:
            try:
                dt = datetime.fromtimestamp(self.timestamp)
                lines.append(f"  Timestamp: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
            except:
                lines.append(f"  Timestamp: {self.timestamp} (invalid)")

        # Attachment
        if self.attachment:
            # Indent the attachment display
            attachment_str = str(self.attachment)
            indented = '\n'.join(['  ' + line for line in attachment_str.split('\n')])
            lines.append(indented)

        return '\n'.join(lines)


@dataclass
class FormattingState:
    """Tracks current formatting attributes while parsing attribute runs."""
    style: Optional[str] = None
    block_quote: bool = False
    checklist_done: bool = False
    indent_level: int = 0
    alignment: Optional[int] = None
    bold: bool = False
    italic: bool = False
    underlined: bool = False
    strikethrough: bool = False
    emphasis_color: Optional[int] = None
    timestamp: Optional[int] = None
    link: Optional[str] = None

    def to_content_block(self, text: str) -> ContentBlock:
        """Create a ContentBlock from current formatting state."""
        return ContentBlock(
            type='text',
            text=text,
            style=self.style,
            block_quote=self.block_quote,
            checklist_done=self.checklist_done,
            indent_level=self.indent_level,
            alignment=self.alignment,
            bold=self.bold,
            italic=self.italic,
            underlined=self.underlined,
            strikethrough=self.strikethrough,
            emphasis_color=self.emphasis_color,
            timestamp=self.timestamp,
            link=self.link,
        )

    def visual_formatting_differs_from(self, other: 'FormattingState') -> bool:
        """Check if visual formatting differs (excludes timestamp which doesn't affect display)."""
        return (
            self.style != other.style or
            self.block_quote != other.block_quote or
            self.checklist_done != other.checklist_done or
            self.indent_level != other.indent_level or
            self.alignment != other.alignment or
            self.bold != other.bold or
            self.italic != other.italic or
            self.underlined != other.underlined or
            self.strikethrough != other.strikethrough or
            self.emphasis_color != other.emphasis_color or
            self.link != other.link
        )


class ProtobufDecoder:
    """Decodes Apple Notes protobuf format into structured data."""

    STYLE_TYPES = {
        0: 'title',
        1: 'heading',
        2: 'subheading',
        4: 'monospaced',
        100: 'bullet',
        101: 'dashed',
        102: 'numbered',
        103: 'checklist',
    }

    def decode_note(self, compressed_data: bytes) -> List[ContentBlock]:
        """
        Decode protobuf data into a list of content blocks.

        Args:
            compressed_data: Gzip-compressed protobuf bytes from ZDATA

        Returns:
            List of ContentBlock objects representing the note structure
        """
        decompressed = gzip.decompress(compressed_data)

        note_store = NoteStoreProto()
        note_store.ParseFromString(decompressed)
        note = note_store.document.note

        blocks: List[ContentBlock] = []
        position = 0
        text_parts: List[str] = []
        current = FormattingState()

        for i, run in enumerate(note.attribute_run):
            text_chunk = utf16_slice(note.note_text, position, run.length)

            if run.HasField('attachment_info'):
                # Flush accumulated text before attachment
                if text_parts:
                    blocks.append(current.to_content_block(''.join(text_parts)))
                    text_parts = []
                    current = FormattingState()

                blocks.append(ContentBlock(
                    type='attachment',
                    attachment=Attachment(
                        uuid=run.attachment_info.attachment_identifier,
                        type=run.attachment_info.type_uti
                    )
                ))
            else:
                run_formatting = self._extract_formatting(run)
                self._warn_unhandled_fields(run, i, text_chunk)

                if run_formatting.visual_formatting_differs_from(current) and text_parts:
                    blocks.append(current.to_content_block(''.join(text_parts)))
                    text_parts = []

                current = run_formatting
                text_parts.append(text_chunk)

            position += run.length

        # Flush remaining text
        if text_parts:
            blocks.append(current.to_content_block(''.join(text_parts)))

        return blocks

    def _extract_formatting(self, run) -> FormattingState:
        """Extract all formatting attributes from an AttributeRun."""
        has_para = run.HasField('paragraph_style')
        para = run.paragraph_style if has_para else None

        return FormattingState(
            style=self._get_style(run),
            block_quote=has_para and para.HasField('block_quote') and para.block_quote == 1,
            checklist_done=has_para and para.HasField('checklist') and para.checklist.done != 0,
            indent_level=para.indent_amount if (has_para and para.HasField('indent_amount')) else 0,
            alignment=para.alignment if (has_para and para.HasField('alignment')) else None,
            bold=run.HasField('font_weight') and run.font_weight == 1,
            italic=run.HasField('font_weight') and run.font_weight == 2,
            underlined=run.HasField('underlined') and run.underlined == 1,
            strikethrough=run.HasField('strikethrough') and run.strikethrough == 1,
            emphasis_color=run.emphasis_style if run.HasField('emphasis_style') else None,
            timestamp=run.unknown_identifier if run.HasField('unknown_identifier') else None,
            link=run.link if run.HasField('link') else None,
        )

    def _get_style(self, run) -> Optional[str]:
        """Extract style from an AttributeRun."""
        if run.HasField('paragraph_style'):
            style_type = run.paragraph_style.style_type
            return self.STYLE_TYPES.get(style_type, 'body')
        return 'body'

    def _warn_unhandled_fields(self, run, run_index: int, text_preview: str):
        """Warn about protobuf fields we're not explicitly handling."""
        # List of fields we explicitly handle
        handled_fields = {
            'length',
            'paragraph_style',  # We extract: style_type, block_quote, checklist, alignment
            'font_weight',      # We extract: bold (1), italic (2)
            'underlined',
            'strikethrough',
            'emphasis_style',   # We extract: color emphasis
            'attachment_info',
            'unknown_identifier',  # We extract: timestamps (Unix seconds) for CRDT edit history
            'link',             # We extract: hyperlink URL
        }

        # Fields we know exist but haven't implemented yet
        known_unimplemented = {
        }

        # Fields we know exist but intentionally ignore (not useful for markdown)
        intentionally_ignored = {'font', 'color', 'superscript'}

        # Check for unhandled fields
        for field_name in known_unimplemented:
            if run.HasField(field_name):
                value = getattr(run, field_name)
                text_snippet = text_preview[:20].replace('\n', '\\n')
                print(f"⚠️  WARNING [Run {run_index}]: Unhandled field '{field_name}' ({known_unimplemented[field_name]})")
                print(f"   Text: {repr(text_snippet)}...")
                print(f"   Value: {value}")

        # Check for completely unknown fields (fields present but not in our lists)
        all_fields = set(f[0].name for f in run.ListFields())
        known_fields = handled_fields | set(known_unimplemented.keys()) | intentionally_ignored
        unknown_fields = all_fields - known_fields

        if unknown_fields:
            text_snippet = text_preview[:20].replace('\n', '\\n')
            print(f"⚠️  WARNING [Run {run_index}]: Completely unknown fields detected: {unknown_fields}")
            print(f"   Text: {repr(text_snippet)}...")
            print(f"   This might indicate a new protobuf field that should be documented!")
