#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""
Markdown generation for Apple Notes.

This module handles generating markdown from structured content blocks.
"""

import gzip
import os
import re
import sys
import urllib.parse
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from .notestore_pb2 import MergableDataProto
from .note_content import TableData, Attachment, ContentBlock, ProtobufDecoder
from .obsidian.dialect import (
    ExportDialect,
    format_internote_link,
    format_attachment_ref,
    strip_title_block,
)
from .obsidian.filename import sanitize_for_obsidian


# UTIs that represent image formats (rendered inline with ![]() syntax)
IMAGE_UTIS = {
    'public.jpeg',
    'public.png',
    'public.gif',
    'public.tiff',
    'public.heic',
    'public.heif',
    'public.webp',
    'public.avif',
    'public.bmp',
    'public.svg-image',
    # Vendor-specific aliases for formats already covered by public.* UTIs
    'com.compuserve.gif',
    'org.webmproject.webp',
}

# Map UTI types to file extensions
UTI_TO_EXTENSION = {
    'public.jpeg': '.jpg',
    'public.png': '.png',
    'public.gif': '.gif',
    'public.tiff': '.tiff',
    'public.heic': '.heic',
    'public.heif': '.heif',
    'public.webp': '.webp',
    'org.webmproject.webp': '.webp',
    'public.avif': '.avif',
    'public.bmp': '.bmp',
    'public.svg-image': '.svg',
    'com.compuserve.gif': '.gif',
    'public.pdf': '.pdf',
    'com.adobe.pdf': '.pdf',
    'public.plain-text': '.txt',
    'public.rtf': '.rtf',
    'public.html': '.html',
    'public.json': '.json',
    'public.xml': '.xml',
    'com.apple.quicktime-movie': '.mov',
    'public.mpeg-4': '.mp4',
    'public.avi': '.avi',
    'public.mp3': '.mp3',
    'public.mpeg-4-audio': '.m4a',
    'com.microsoft.word.doc': '.doc',
    'org.openxmlformats.wordprocessingml.document': '.docx',
    'com.microsoft.excel.xls': '.xls',
    'org.openxmlformats.spreadsheetml.sheet': '.xlsx',
    'com.microsoft.powerpoint.ppt': '.ppt',
    'org.openxmlformats.presentationml.presentation': '.pptx',
    'public.zip-archive': '.zip',
    'com.apple.mail.email': '.eml',
    'public.vcard': '.vcf',
}


def get_extension_for_type(uti_type: str) -> Optional[str]:
    """Get file extension for a UTI type.

    Args:
        uti_type: The UTI type string (e.g., 'public.jpeg')

    Returns:
        File extension with leading dot (e.g., '.jpg'), or None if unknown
    """
    if not uti_type:
        return None
    return UTI_TO_EXTENSION.get(uti_type.lower())


class AttachmentResolver:
    """Resolves attachment UUIDs to metadata and handles table extraction."""

    def __init__(self, data_loader, note_name: str = None, note_uuid: str = None):
        """
        Initialize attachment resolver.

        Args:
            data_loader: DatabaseNoteDataLoader instance for accessing attachment data
            note_name: Name of the note being processed, used in warning messages
            note_uuid: UUID of the note being processed, used in warning messages
        """
        self.data_loader = data_loader
        self.note_name = note_name
        self.note_uuid = note_uuid

    def resolve_attachment(self, attachment: Attachment) -> None:
        """
        Resolve attachment metadata (file path and title) and populate the attachment object.

        Args:
            attachment: Attachment object to populate with resolved data
        """
        metadata = self.data_loader.get_attachment_metadata(attachment.uuid)
        if not metadata:
            return  # Nothing to populate

        # Populate attachment fields
        attachment.title = metadata.get('title')
        attachment.file_path = metadata.get('file_path')
        attachment.alt_text = metadata.get('alt_text')
        attachment.token_content_identifier = metadata.get('token_content_identifier')

        # If no title but we have a filename, use that
        if not attachment.title and metadata.get('filename'):
            attachment.title = metadata['filename']

    def resolve_gallery(self, attachment: Attachment) -> None:
        """
        Resolve gallery attachment by populating its children.

        Args:
            attachment: Gallery attachment object to populate with children
        """
        children_metadata = self.data_loader.get_gallery_children(attachment.uuid)
        if not children_metadata:
            attachment.gallery_children = []
            return

        # Convert metadata to Attachment objects
        children = []
        for meta in children_metadata:
            child = Attachment(
                uuid=meta['uuid'],
                type=meta.get('type', 'public.data'),
                file_path=meta.get('file_path'),
                title=meta.get('title'),
                alt_text=meta.get('alt_text')
            )
            # If no title but we have a filename, use that
            if not child.title and meta.get('filename'):
                child.title = meta['filename']
            children.append(child)

        attachment.gallery_children = children

    def _table_warn(self, table_uuid: str, message: str) -> None:
        """Print a table extraction warning to stderr with note context."""
        note_ctx = f"Note '{self.note_name}' ({self.note_uuid}): " if self.note_name else ""
        print(f"Warning: {note_ctx}Table {table_uuid}: {message}", file=sys.stderr)

    def extract_table(self, uuid: str) -> Optional[TableData]:
        """
        Extract table data from ZMERGEABLEDATA1.

        The CRDT table format stores display order in ordering.array.attachment entries,
        not in ordering.contents position. Each attachment has a uuid (bytes) and index
        (display position). We look up the UUID in mergeable_data_object_uuid_item to
        map entries to their display positions.

        Args:
            uuid: Table attachment identifier (ZIDENTIFIER)

        Returns:
            TableData object containing grid and alignments, or None if extraction fails
        """
        table_bytes = self.data_loader.get_table_data(uuid)
        if not table_bytes:
            self._table_warn(uuid, "no data returned from database")
            return None

        try:
            # Decompress and parse protobuf
            decompressed = gzip.decompress(table_bytes)
            proto = MergableDataProto()
            proto.ParseFromString(decompressed)
            data = proto.mergable_data_object.mergeable_data_object_data
            entries = list(data.mergeable_data_object_entry)
            uuid_items = list(data.mergeable_data_object_uuid_item)

            if not entries:
                self._table_warn(uuid, "no entries in protobuf data")
                return None

            # Build entry_idx -> UUIDIndex mapping
            # New format uses key 4, old format uses key 2
            entry_to_uuid_index = {}
            for i, entry in enumerate(entries):
                if entry.HasField('custom_map'):
                    for me in entry.custom_map.map_entry:
                        if me.key in (2, 4) and me.value.HasField('unsigned_integer_value'):
                            entry_to_uuid_index[i] = me.value.unsigned_integer_value

            # Find crRows, crColumns and cellColumns from main CustomMap
            main_entry = entries[0]
            if not main_entry.HasField('custom_map'):
                self._table_warn(uuid, "main entry has no custom_map")
                return None

            # Collect all main map entries by key (only those with object_index values)
            main_map = {}
            for map_entry in main_entry.custom_map.map_entry:
                if map_entry.value.HasField('object_index'):
                    main_map[map_entry.key] = map_entry.value.object_index

            # Map keys are indices into key_items, which assigns semantic names to slots.
            key_items = list(data.mergeable_data_object_key_item)
            key_name_to_map_key = {name: i for i, name in enumerate(key_items)}
            rows_entry_idx = main_map.get(key_name_to_map_key.get('crRows', -1))
            cols_entry_idx = main_map.get(key_name_to_map_key.get('crColumns', -1))
            cellcols_idx = main_map.get(key_name_to_map_key.get('cellColumns', -1))

            if cols_entry_idx is None or cellcols_idx is None:
                self._table_warn(uuid, "could not find crColumns or cellColumns in main map")
                return None

            # Build UUIDIndex -> display_row mapping from crRows
            uuid_index_to_display_row = {}
            num_rows = 0
            if rows_entry_idx is not None:
                rows_entry = entries[rows_entry_idx]
                if rows_entry.HasField('ordered_set') and rows_entry.ordered_set.HasField('ordering'):
                    ordering = rows_entry.ordered_set.ordering

                    # Build uuid_bytes -> display_row from array.attachment
                    uuid_bytes_to_display_row = {}
                    for att_ref in ordering.array.attachment:
                        uuid_bytes_to_display_row[att_ref.uuid] = att_ref.index

                    num_rows = len(ordering.array.attachment)

                    # Map UUIDIndex values to display positions via uuid_items lookup
                    for elem in ordering.contents.element:
                        key_idx = elem.key.object_index
                        val_idx = elem.value.object_index
                        key_uuid_index = entry_to_uuid_index.get(key_idx)
                        val_uuid_index = entry_to_uuid_index.get(val_idx)

                        if key_uuid_index is not None and key_uuid_index < len(uuid_items):
                            uuid_bytes = uuid_items[key_uuid_index]
                            display_row = uuid_bytes_to_display_row.get(uuid_bytes)
                            if display_row is not None:
                                # Map both key and value UUIDIndex to same display position
                                uuid_index_to_display_row[key_uuid_index] = display_row
                                if val_uuid_index is not None:
                                    uuid_index_to_display_row[val_uuid_index] = display_row

            # Build UUIDIndex -> display_col mapping from crColumns
            cols_entry = entries[cols_entry_idx]
            if not cols_entry.HasField('ordered_set') or not cols_entry.ordered_set.HasField('ordering'):
                self._table_warn(uuid, "crColumns entry has no ordered_set/ordering")
                return None

            col_ordering = cols_entry.ordered_set.ordering

            uuid_bytes_to_display_col = {}
            for att_ref in col_ordering.array.attachment:
                uuid_bytes_to_display_col[att_ref.uuid] = att_ref.index

            num_cols = len(col_ordering.array.attachment)

            uuid_index_to_display_col = {}
            for elem in col_ordering.contents.element:
                key_idx = elem.key.object_index
                val_idx = elem.value.object_index
                key_uuid_index = entry_to_uuid_index.get(key_idx)
                val_uuid_index = entry_to_uuid_index.get(val_idx)

                if key_uuid_index is not None and key_uuid_index < len(uuid_items):
                    uuid_bytes = uuid_items[key_uuid_index]
                    display_col = uuid_bytes_to_display_col.get(uuid_bytes)
                    if display_col is not None:
                        uuid_index_to_display_col[key_uuid_index] = display_col
                        if val_uuid_index is not None:
                            uuid_index_to_display_col[val_uuid_index] = display_col

            # Extract cells using UUIDIndex lookups
            cellcols = entries[cellcols_idx]
            if not cellcols.HasField('dictionary'):
                self._table_warn(uuid, "cellColumns entry has no dictionary")
                return None

            grid = [['' for _ in range(num_cols)] for _ in range(num_rows)]
            cell_alignments = [[None for _ in range(num_cols)] for _ in range(num_rows)]

            for col_elem in cellcols.dictionary.element:
                col_key = col_elem.key.object_index
                col_uuid_idx = entry_to_uuid_index.get(col_key)
                display_col = uuid_index_to_display_col.get(col_uuid_idx)

                if display_col is None:
                    continue

                inner_dict_idx = col_elem.value.object_index
                inner_dict = entries[inner_dict_idx]

                if not inner_dict.HasField('dictionary'):
                    continue

                for row_elem in inner_dict.dictionary.element:
                    row_key = row_elem.key.object_index
                    row_uuid_idx = entry_to_uuid_index.get(row_key)
                    display_row = uuid_index_to_display_row.get(row_uuid_idx)

                    if display_row is None:
                        continue

                    cell_idx = row_elem.value.object_index
                    cell_entry = entries[cell_idx]

                    text = ""
                    alignment = None
                    if cell_entry.HasField('note'):
                        if cell_entry.note.HasField('note_text'):
                            text = cell_entry.note.note_text.strip()
                        if cell_entry.note.attribute_run:
                            first_run = cell_entry.note.attribute_run[0]
                            if first_run.HasField('paragraph_style') and first_run.paragraph_style.HasField('alignment'):
                                alignment = first_run.paragraph_style.alignment

                    grid[display_row][display_col] = text
                    cell_alignments[display_row][display_col] = alignment

            # Extract column alignments from first row
            column_alignments = []
            for col in range(num_cols):
                alignment = cell_alignments[0][col] if cell_alignments and num_rows > 0 else None
                column_alignments.append(alignment if alignment is not None else 0)

            return TableData(grid=grid, column_alignments=column_alignments)

        except Exception as e:
            self._table_warn(uuid, f"extraction failed: {e}")
            return None


@dataclass
class InlineFormattingState:
    """Tracks active inline formatting markers during markdown generation."""
    bold: bool = False
    italic: bool = False
    strikethrough: bool = False
    underline: bool = False
    highlight: bool = False
    link: Optional[str] = None  # URL if inside a link, None otherwise

    # Marker definitions: (attribute_name, marker_string)
    # Order matters for nesting: outer formats listed first for opening, reversed for closing
    # Note: links are handled separately due to asymmetric markers [text](url)
    OPENING_ORDER: Tuple[Tuple[str, str], ...] = (
        ('bold', '**'),
        ('italic', '*'),
        ('strikethrough', '~~'),
        ('underline', '++'),
        ('highlight', '=='),
    )

    @classmethod
    def from_block(cls, block: ContentBlock,
                   dialect: ExportDialect = ExportDialect.BACKUP) -> 'InlineFormattingState':
        """Create formatting state from a content block.

        In OBSIDIAN dialect, underline has no native syntax, so we fold it into
        highlight (`==`) — the closest visual analog — and never set underline=True.
        That also means underline + highlight on the same text collapse to a single
        `==text==` instead of producing `====` artifacts. Per requirements §6.2.
        """
        underline = block.underlined
        highlight = block.emphasis_color is not None
        if dialect is ExportDialect.OBSIDIAN:
            highlight = highlight or underline
            underline = False

        return cls(
            bold=block.bold,
            italic=block.italic,
            strikethrough=block.strikethrough,
            underline=underline,
            highlight=highlight,
            link=block.link,
        )

    def emit_closing_markers(self, target: 'InlineFormattingState') -> List[str]:
        """Emit markers for formats that are closing (active in self but not in target)."""
        markers = []
        # Close inner formats first (reverse of opening order)
        for attr, marker in reversed(self.OPENING_ORDER):
            if getattr(self, attr) and not getattr(target, attr):
                markers.append(marker)
        return markers

    def emit_opening_markers(self, target: 'InlineFormattingState') -> List[str]:
        """Emit markers for formats that are opening (active in target but not in self)."""
        markers = []
        for attr, marker in self.OPENING_ORDER:
            if getattr(target, attr) and not getattr(self, attr):
                markers.append(marker)
        return markers

    def is_closing_any(self, target: 'InlineFormattingState') -> bool:
        """Check if any format is closing when transitioning to target state."""
        for attr, _ in self.OPENING_ORDER:
            if getattr(self, attr) and not getattr(target, attr):
                return True
        return False

    def is_link_closing(self, target: 'InlineFormattingState') -> bool:
        """Check if a link is closing (we have a link, target doesn't or has different URL)."""
        return self.link is not None and self.link != target.link

    def is_link_opening(self, target: 'InlineFormattingState') -> bool:
        """Check if a link is opening (target has a link we don't have or different URL)."""
        return target.link is not None and self.link != target.link


class MarkdownGenerator:
    """Generates markdown from structured content blocks."""

    # Styles where hard line breaks are structural (item separators), not inline content,
    # so we skip close/reopen of inline formatting markers at those breaks.
    _STYLES_WITHOUT_INLINE_BREAK_FORMATTING = {'monospaced', 'bullet', 'dashed', 'numbered', 'checklist'}

    def __init__(self, attachment_resolver: AttachmentResolver, note_path_by_uuid: dict = None,
                 current_note_path: Path = None,
                 *,
                 dialect: ExportDialect = ExportDialect.BACKUP,
                 note_name: str | None = None):
        self.attachment_resolver = attachment_resolver
        self.note_path_by_uuid = note_path_by_uuid or {}
        self.current_note_path = current_note_path
        # Dialect controls a handful of branch sites: inter-note link form,
        # attachment-ref form, and title-line stripping. `note_name` is required
        # for the title-strip pass and is otherwise unused.
        self.dialect = dialect
        self.note_name = note_name

    @staticmethod
    def _is_monospaced_text(block: ContentBlock) -> bool:
        """Check if a block is a monospaced text block."""
        return block.type == 'text' and block.style == 'monospaced'

    def _merge_consecutive_monospaced(self, blocks: List[ContentBlock]) -> List[ContentBlock]:
        """Merge consecutive monospaced text blocks into single blocks.

        Apple Notes' CRDT can split code blocks when URLs or other auto-detected
        formatting (links, bold, etc.) exists within the text. Since code blocks
        don't support inline formatting, we merge them back into single blocks.
        """
        if not blocks:
            return blocks

        merged: List[ContentBlock] = []
        for block in blocks:
            if self._is_monospaced_text(block) and merged and self._is_monospaced_text(merged[-1]):
                prev = merged[-1]
                merged[-1] = ContentBlock(
                    type='text',
                    text=(prev.text or '') + (block.text or ''),
                    style='monospaced',
                )
            else:
                merged.append(block)
        return merged

    def _close_reopen_formatting_at_breaks(self, text: str, fmt: InlineFormattingState) -> str:
        """Close and reopen inline formatting markers at hard line breaks.

        Markdown inline formatting (bold, italic, etc.) doesn't reliably render across
        line breaks in all renderers. This closes active markers before each internal
        hard break and reopens them after, matching Apple Notes' export behavior.

        Only processes internal breaks; the trailing break is left for the state machine.
        """
        if fmt.link:
            return text  # Links have asymmetric markers; skip to avoid complexity

        # Reuse InlineFormattingState's marker ordering to build close/open strings.
        # Closing "from fmt to no-formatting" gives all active markers in close order;
        # opening "from no-formatting to fmt" gives them in open order.
        no_fmt = InlineFormattingState()
        close_str = ''.join(fmt.emit_closing_markers(no_fmt))
        open_str = ''.join(no_fmt.emit_opening_markers(fmt))

        if not close_str:
            return text  # No active inline formatting markers

        parts = text.split('  \n')
        if len(parts) <= 1:
            return text  # No hard breaks

        # If text ends with a hard break, the trailing break is left for the state machine
        has_trailing_break = text.endswith('  \n')
        last_break_index = len(parts) - 2

        result = []
        for i, part in enumerate(parts[:-1]):
            next_part = parts[i + 1]
            is_trailing = has_trailing_break and i == last_break_index
            both_sides_have_content = part.strip() and next_part.strip()

            if not is_trailing and both_sides_have_content:
                result.append(part + close_str + '  \n' + open_str)
            else:
                result.append(part + '  \n')
        result.append(parts[-1])

        return ''.join(result)

    def _close_reopen_formatting_at_list_items(self, text: str, fmt: InlineFormattingState) -> str:
        """Close and reopen inline formatting markers at list item boundaries.

        When a single formatted block (e.g., bold) spans multiple list items separated by
        hard breaks, each item needs its own formatting markers. Closes markers before the
        break and reopens them after the list marker on the next line.
        """
        if fmt.link:
            return text

        no_fmt = InlineFormattingState()
        close_str = ''.join(fmt.emit_closing_markers(no_fmt))
        open_str = ''.join(no_fmt.emit_opening_markers(fmt))

        if not close_str:
            return text

        parts = text.split('  \n')
        if len(parts) <= 1:
            return text

        has_trailing_break = text.endswith('  \n')
        last_break_index = len(parts) - 2

        result = []
        for i in range(len(parts) - 1):
            part = parts[i]
            next_part = parts[i + 1]
            is_trailing = has_trailing_break and i == last_break_index
            both_sides_have_content = part.strip() and next_part.strip()

            if not is_trailing and both_sides_have_content:
                # Close before break, reopen after the list marker on the next line
                result.append(part + close_str + '  \n')
                # Find where the list marker ends (whitespace + marker + space pattern)
                stripped = next_part.lstrip(' ')
                leading_spaces = len(next_part) - len(stripped)
                # Check for list markers: "* ", "- ", "1. ", "- [ ] ", "- [x] "
                marker_len = 0
                if stripped.startswith('* '):
                    marker_len = 2
                elif stripped.startswith('- [ ] ') or stripped.startswith('- [x] '):
                    marker_len = 6
                elif stripped.startswith('- '):
                    marker_len = 2
                elif re.match(r'\d+\. ', stripped):
                    match = re.match(r'\d+\. ', stripped)
                    marker_len = match.end()

                if marker_len > 0:
                    insert_pos = leading_spaces + marker_len
                    parts[i + 1] = next_part[:insert_pos] + open_str + next_part[insert_pos:]
                else:
                    parts[i + 1] = open_str + next_part
            else:
                result.append(part + '  \n')
        result.append(parts[-1])

        return ''.join(result)

    def generate(self, blocks: List[ContentBlock]) -> str:
        """
        Convert content blocks to markdown with state-tracked inline formatting.

        Tracks formatting state across blocks and only emits markers at transitions,
        producing cleaner output when the protobuf splits content at formatting
        boundaries rather than word boundaries.
        """
        blocks = self._merge_consecutive_monospaced(blocks)

        # In Obsidian, the note title comes from the filename rather than the body.
        # If the first non-empty block restates the title, drop it to avoid the
        # duplicate-heading effect users would otherwise see (§6.1).
        if self.dialect is ExportDialect.OBSIDIAN and self.note_name:
            blocks = strip_title_block(blocks, self.note_name)

        output_parts: List[str] = []
        current_fmt = InlineFormattingState()
        no_formatting = InlineFormattingState()

        prev_was_attachment = False
        prev_block_style: Optional[str] = None
        prev_block_ended_with_newline = True

        # Track numbered list item counter across blocks
        numbered_list_counter = 0

        for block in blocks:
            if block.type == 'text':
                # Whitespace-only blocks don't carry meaningful formatting —
                # ignore their formatting state to avoid emitting empty markers like ****
                if block.text and not block.text.strip():
                    block_fmt = InlineFormattingState()
                else:
                    block_fmt = InlineFormattingState.from_block(block, dialect=self.dialect)

                # Update numbered list counter
                if block.style == 'numbered':
                    # Reset counter if this is the start of a new numbered list
                    if prev_block_style != 'numbered':
                        numbered_list_counter = 0
                else:
                    # Reset when leaving numbered list
                    numbered_list_counter = 0

                # Get prefix and content separately for proper formatting order
                prefix, text = self._format_block_structure_with_prefix(
                    block, prev_was_attachment, prev_block_style, prev_block_ended_with_newline,
                    numbered_list_counter
                )

                # Update counter based on content (count non-empty lines for numbered lists)
                if block.style == 'numbered' and block.text:
                    for line in block.text.split('\n'):
                        if line.strip():
                            numbered_list_counter += 1

                # Close/reopen inline formatting at hard line breaks
                if block.style in self._STYLES_WITHOUT_INLINE_BREAK_FORMATTING:
                    # For list styles, close/reopen formatting around new list items
                    if '  \n' in text:
                        text = self._close_reopen_formatting_at_list_items(text, block_fmt)
                elif '  \n' in text:
                    text = self._close_reopen_formatting_at_breaks(text, block_fmt)

                # When a new list item starts with a prefix and has active formatting
                # that would carry over unchanged, force close/reopen around the prefix
                # so each list item gets its own formatting markers.
                force_reopen = False
                if prefix and current_fmt == block_fmt and (
                    current_fmt.bold or current_fmt.italic or current_fmt.strikethrough
                    or current_fmt.underline or current_fmt.highlight
                ):
                    force_reopen = True

                # Move trailing hard-break whitespace outside closing markers.
                # Only strip trailing whitespace that contains a newline (hard break endings),
                # not plain trailing spaces which are kept inside markers to match Apple's format.
                trailing_space = ''
                is_closing = (current_fmt.is_closing_any(block_fmt) or current_fmt.is_link_closing(block_fmt)
                              or force_reopen)
                if is_closing and output_parts:
                    last_part = output_parts[-1]
                    candidate = ''
                    while last_part and last_part[-1] in ' \t\n':
                        candidate = last_part[-1] + candidate
                        last_part = last_part[:-1]
                    if candidate and '\n' in candidate:
                        trailing_space = candidate
                        output_parts[-1] = last_part

                if force_reopen:
                    # Close all active formatting, emit prefix, then reopen
                    output_parts.extend(current_fmt.emit_closing_markers(no_formatting))
                    if trailing_space:
                        output_parts.append(trailing_space)
                    output_parts.append(prefix)
                    output_parts.extend(no_formatting.emit_opening_markers(block_fmt))
                    output_parts.append(text)
                else:
                    # Close inline formatting first (inside the link)
                    output_parts.extend(current_fmt.emit_closing_markers(block_fmt))

                    # Close link after inline formatting: ](url)
                    if current_fmt.is_link_closing(block_fmt):
                        output_parts.append(f']({current_fmt.link})')

                    if trailing_space:
                        output_parts.append(trailing_space)

                    # Emit prefix BEFORE inline formatting markers
                    if prefix:
                        output_parts.append(prefix)

                    # Open link before inline formatting: [
                    if current_fmt.is_link_opening(block_fmt):
                        output_parts.append('[')

                    output_parts.extend(current_fmt.emit_opening_markers(block_fmt))
                    output_parts.append(text)

                current_fmt = block_fmt
                prev_was_attachment = False
                prev_block_style = block.style
                prev_block_ended_with_newline = bool(block.text and block.text.endswith('\n'))

            elif block.type == 'attachment':
                output_parts.extend(current_fmt.emit_closing_markers(no_formatting))
                if current_fmt.link:
                    output_parts.append(f']({current_fmt.link})')
                output_parts.append(self._format_attachment(block))
                current_fmt = no_formatting
                prev_was_attachment = True
                prev_block_style = None
                prev_block_ended_with_newline = True
                numbered_list_counter = 0

        # Close any remaining open formatting
        output_parts.extend(current_fmt.emit_closing_markers(no_formatting))
        if current_fmt.link:
            output_parts.append(f']({current_fmt.link})')

        result = ''.join(output_parts)

        # Ensure file ends with newline (with hard break spaces)
        if result and not result.endswith('\n'):
            result += '  \n'

        return result

    def _format_block_structure_with_prefix(self, block: ContentBlock, prev_was_attachment: bool = False,
                                             prev_block_style: Optional[str] = None,
                                             prev_block_ended_with_newline: bool = True,
                                             numbered_list_counter: int = 0) -> Tuple[str, str]:
        """
        Format block-level structure returning (prefix, content) separately.

        For numbered lists, the prefix (e.g., "1. ") is returned separately so that
        inline formatting markers can wrap only the content, not the list marker.

        Returns:
            Tuple of (prefix, content) where prefix may be empty string
        """
        text = block.text
        if not text:
            return ('', '')

        # Handle monospaced text - code fences are a special case
        if block.style == 'monospaced':
            text_clean = text.rstrip('\n')
            return ('', f'```\n{text_clean}\n\n```\n')

        # Preserve trailing newlines to add at the very end
        stripped = text.rstrip('\n')
        trailing_newlines = text[len(stripped):]
        text = stripped

        # Handle leading newline after attachment
        if prev_was_attachment and text.startswith('\n') and not text.startswith('\n\n'):
            text = text[1:]

        # Add two trailing spaces before every newline (markdown hard breaks)
        text = text.replace('\n', '  \n')

        # Process trailing newlines to also have hard breaks
        if trailing_newlines:
            trailing_newlines = trailing_newlines.replace('\n', '  \n')

        # Apply block quote prefix
        if block.block_quote:
            lines = text.split('\n')
            lines = ['> ' + line if line else '>' for line in lines]
            text = '\n'.join(lines)
            text = text + trailing_newlines
            return ('', text)

        # Add back trailing newlines
        text = text + trailing_newlines

        # Indentation prefix for nested list items (4 spaces per indent level)
        indent = '    ' * block.indent_level

        # Apply style-specific formatting
        if block.style == 'title':
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip():
                    lines[i] = '# ' + line
                    break
            return ('', '\n'.join(lines))

        elif block.style == 'heading':
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if i == len(lines) - 1 and not line.strip():
                    continue
                # Skip short trailing fragments (partial words split across blocks)
                if i == len(lines) - 1 and len(line.strip()) <= 2 and line.strip().isalpha():
                    continue
                lines[i] = '## ' + line
            return ('', '\n'.join(lines))

        elif block.style == 'subheading':
            lines = text.split('\n')
            for i, line in enumerate(lines):
                if i == len(lines) - 1 and not line.strip():
                    continue
                if i == len(lines) - 1 and len(line.strip()) <= 2 and line.strip().isalpha():
                    continue
                lines[i] = '### ' + line
            return ('', '\n'.join(lines))

        elif block.style == 'bullet':
            marker = f'{indent}* '
            return self._format_list_item(text, marker, prev_block_style == 'bullet', prev_block_ended_with_newline)

        elif block.style == 'dashed':
            marker = f'{indent}- '
            return self._format_list_item(text, marker, prev_block_style == 'dashed', prev_block_ended_with_newline)

        elif block.style == 'numbered':
            # Return first item's prefix separately so inline formatting wraps content only.
            # numbered_list_counter tracks position across blocks.
            lines = text.split('\n')
            first_prefix = ''
            result_lines = []
            item_num = numbered_list_counter + 1

            for line in lines:
                if not line.strip():
                    result_lines.append(line)
                    continue
                prefix = f'{indent}{item_num}. '
                item_num += 1
                if not first_prefix:
                    first_prefix = prefix
                    result_lines.append(line)
                else:
                    result_lines.append(prefix + line)

            return (first_prefix, '\n'.join(result_lines))

        elif block.style == 'checklist':
            checkbox = '[x]' if block.checklist_done else '[ ]'
            marker = f'{indent}- {checkbox} '
            return self._format_list_item(text, marker, prev_block_style == 'checklist', prev_block_ended_with_newline)

        else:  # body text
            return ('', text)

    @staticmethod
    def _format_list_item(text: str, marker: str, same_style_as_prev: bool,
                          prev_block_ended_with_newline: bool) -> Tuple[str, str]:
        """Format a list item (bullet, dashed, or checklist), returning (prefix, content).

        The marker (e.g., '    * ') is returned as prefix for the first line so that
        inline formatting markers wrap only the content, not the list marker.

        Args:
            text: The block text (already with hard breaks applied)
            marker: The full marker string including indentation (e.g., '    * ')
            same_style_as_prev: Whether the previous block had the same list style
            prev_block_ended_with_newline: Whether the previous block's raw text ended with newline
        """
        is_new_item = (not same_style_as_prev) or prev_block_ended_with_newline or text.startswith('\n')

        if is_new_item:
            # Strip leading newline if present; we'll re-add it in the return value
            has_leading_newline = text.startswith('\n')
            content = text[1:] if has_leading_newline else text

            # First content line gets marker as prefix, subsequent content lines get it embedded
            lines = content.split('\n')
            first_prefix = ''
            result_lines = []
            for i, line in enumerate(lines):
                if line.strip() and (i == 0 or not line[0].isspace()):
                    if not first_prefix:
                        first_prefix = marker
                        result_lines.append(line)
                    else:
                        result_lines.append(marker + line)
                else:
                    result_lines.append(line)

            joined = '\n'.join(result_lines)
            return (first_prefix, '\n' + joined if has_leading_newline else joined)
        else:
            # Continuation of previous item
            if '\n' not in text:
                return ('', text)
            before, after = text.split('\n', 1)
            lines = after.split('\n')
            for i, line in enumerate(lines):
                if line.strip() and (i == 0 or not line[0].isspace()):
                    lines[i] = marker + line
            return ('', before + '\n' + '\n'.join(lines))

    def _sanitize_name_for_path(self, name: str) -> str:
        """Sanitize a note name for use in file paths.

        This matches the logic in notes_datatypes._sanitize_name.
        """
        name = (name.replace('/', '_').replace(':', '-')
                .replace('"', '"').replace("\t", " "))
        result = []
        for ch in name:
            if ord(ch) < 0x20:
                result.append(urllib.parse.quote(ch, safe=''))
            else:
                result.append(ch)
        return ''.join(result)

    def _make_unique_filename(self, base_name: str, used_names: set) -> str:
        """Generate a unique filename by adding numeric suffix if needed.

        Args:
            base_name: The sanitized filename to make unique
            used_names: Set of already-used filenames

        Returns:
            A unique filename (base_name or base_name with _2, _3, etc. suffix)
        """
        if base_name not in used_names:
            return base_name

        # Split into name and extension
        if '.' in base_name:
            name_part, ext = base_name.rsplit('.', 1)
            ext = '.' + ext
        else:
            name_part = base_name
            ext = ''

        counter = 2
        while True:
            candidate = f"{name_part}_{counter}{ext}"
            if candidate not in used_names:
                return candidate
            counter += 1

    def _parse_note_uuid_from_token(self, token_content_identifier: str) -> Optional[str]:
        """Parse the target note's UUID from a token content identifier.

        Args:
            token_content_identifier: String like "applenotes:note/13aad38b-2535-4f3d-b4fe-59c335386419?..."

        Returns:
            The UUID string, or None if parsing fails
        """
        if not token_content_identifier:
            return None

        # Format: "applenotes:note/UUID" or "applenotes:note/UUID?..."
        match = re.search(r'applenotes:note/([a-fA-F0-9-]+)', token_content_identifier)
        if match:
            return match.group(1)
        return None

    def _compute_relative_note_path(self, source_path: Path, target_path: Path) -> str:
        """Compute the relative markdown link path from source note to target note.

        Args:
            source_path: Path to the source note's directory
            target_path: Path to the target note's directory

        Returns:
            URL-encoded relative path to the target note's markdown file
        """
        # Get the target directory name (which is also the markdown file name)
        target_dir_name = target_path.name
        target_md_name = target_dir_name + ".md"

        # Compute relative path from source note directory to target note directory
        # The markdown file is inside source_path, so we compute from source_path
        rel_dir = os.path.relpath(target_path, source_path)

        # Build the final relative path to the .md file
        rel_path = f"{rel_dir}/{target_md_name}"

        # URL-encode the path
        return urllib.parse.quote(rel_path)

    def _format_internote_link_backup(self, attachment, linked_note_name: str) -> str:
        """Backup-mode inter-note link: relative markdown link to the target's .md file.

        Resolves via `note_path_by_uuid` when possible (preferred — uses the actual
        on-disk path) and falls back to a guessed `../Name/Name.md` shape when the
        link can't be resolved.
        """
        if attachment.token_content_identifier and self.note_path_by_uuid and self.current_note_path:
            target_uuid = self._parse_note_uuid_from_token(attachment.token_content_identifier)
            if target_uuid:
                target_path = self.note_path_by_uuid.get(target_uuid.upper())
                if target_path:
                    relative_path = self._compute_relative_note_path(self.current_note_path, target_path)
                    return f"[{linked_note_name}]({relative_path})"

        # Fallback when the target can't be resolved — guess the path from the display name.
        sanitized_name = self._sanitize_name_for_path(linked_note_name)
        encoded_path = urllib.parse.quote(f"../{sanitized_name}/{sanitized_name}.md")
        return f"[{linked_note_name}]({encoded_path})"

    def _format_internote_link_obsidian(self, attachment, linked_note_name: str) -> str:
        """Obsidian-mode inter-note link: `[[Target]]` or `[[target|display]]`.

        The wikilink target is the resolved note's on-disk filename (without `.md`).
        When the link can't be resolved, we still emit `[[linked_note_name]]` so
        Obsidian renders it as a (distinctly-colored) unresolved link — surfacing
        the gap to the user instead of silently flattening it to plain text.
        """
        target_filename: str | None = None
        if attachment.token_content_identifier and self.note_path_by_uuid:
            target_uuid = self._parse_note_uuid_from_token(attachment.token_content_identifier)
            if target_uuid:
                target_path = self.note_path_by_uuid.get(target_uuid.upper())
                if target_path:
                    target_filename = target_path.stem  # drop the `.md`

        if target_filename:
            return format_internote_link(target_filename, display=linked_note_name)
        return format_internote_link(linked_note_name)

    def _format_attachment(self, block: ContentBlock) -> str:
        """Format an attachment as markdown."""
        if not block.attachment:
            return "\n[Missing attachment]\n"

        attachment = block.attachment

        # Check if it's a table
        if attachment.type == 'com.apple.notes.table':
            # Extract table data if not already extracted
            if not attachment.table_data:
                attachment.table_data = self.attachment_resolver.extract_table(attachment.uuid)

            if attachment.table_data:
                return '\n' + self._render_table_markdown(attachment.table_data) + '\n'
            else:
                return f"[Table: {attachment.uuid}]\n"

        # Check if it's a link to another note
        if 'inlinetextattachment.link' in attachment.type:
            # Resolve to get linked note name and token_content_identifier
            if not attachment.alt_text:
                self.attachment_resolver.resolve_attachment(attachment)

            if not attachment.alt_text:
                return f"[Link: {attachment.uuid}]"

            linked_note_name = attachment.alt_text

            # Each dialect formats inter-note links differently. Keep the two
            # branches isolated in their own helpers so the high-level flow above
            # stays readable.
            if self.dialect is ExportDialect.OBSIDIAN:
                return self._format_internote_link_obsidian(attachment, linked_note_name)
            return self._format_internote_link_backup(attachment, linked_note_name)

        # Check if it's a hashtag inline attachment
        if 'hashtag' in attachment.type:
            # Resolve to get alt_text if not already resolved
            if not attachment.alt_text:
                self.attachment_resolver.resolve_attachment(attachment)

            # Use alt_text if available (e.g., "#bookmark")
            if attachment.alt_text:
                return attachment.alt_text
            else:
                return f"[Attachment: {attachment.uuid}]\n"

        # Check if it's a gallery
        if attachment.type == 'com.apple.notes.gallery':
            # Resolve gallery children if not already resolved
            if attachment.gallery_children is None:
                self.attachment_resolver.resolve_gallery(attachment)

            if attachment.gallery_children:
                # Generate markdown list of all gallery images
                lines = []
                for child in attachment.gallery_children:
                    if child.unique_filename:
                        sanitized_filename = child.unique_filename
                    elif self.dialect is ExportDialect.OBSIDIAN:
                        # Obsidian wikilinks can't contain # | ^ [ ] — fall back to
                        # the Obsidian-aware sanitizer so forbidden chars get fullwidth
                        # look-alikes instead of leaking into the [[...]] target.
                        sanitized_filename = sanitize_for_obsidian(child.title or child.uuid[:8])
                    else:
                        sanitized_filename = self._sanitize_name_for_path(child.title or child.uuid[:8])

                    if self.dialect is ExportDialect.OBSIDIAN:
                        lines.append(f"{format_attachment_ref(sanitized_filename)}  ")
                        continue

                    encoded_filename = urllib.parse.quote(sanitized_filename)
                    apple_path = f"Attachments/{encoded_filename}"
                    if child.type and ('image' in child.type or 'jpeg' in child.type or 'png' in child.type):
                        lines.append(f"![{child.title or 'Image'}]({apple_path})  ")
                    else:
                        lines.append(f"[{child.title or 'Attachment'}]({apple_path})  ")

                return '\n' + '\n'.join(lines) + '\n'
            else:
                # Gallery with no children found
                return f"[Gallery: {attachment.title or attachment.uuid}]\n"

        # Regular attachment - resolve if not already resolved
        if not attachment.file_path and not attachment.title:
            self.attachment_resolver.resolve_attachment(attachment)

        if not attachment.title:
            return f"[Attachment: {attachment.uuid}]\n"

        # Use unique_filename if set (collision-resolved), otherwise sanitize the title
        if attachment.unique_filename:
            sanitized_filename = attachment.unique_filename
        else:
            sanitized_filename = self._sanitize_name_for_path(attachment.title)

        # Obsidian-mode attachments live in a flat top-level assets/ directory and
        # are referenced by `![[name.ext]]` (images) or `[[name.ext]]` (everything
        # else). The extension-based decision lives in obsidian.dialect.
        if self.dialect is ExportDialect.OBSIDIAN:
            return f"{format_attachment_ref(sanitized_filename)}  \n"

        # Backup-mode attachments live next to the note in an Attachments/ subdir.
        encoded_filename = urllib.parse.quote(sanitized_filename)
        apple_path = f"Attachments/{encoded_filename}"
        if attachment.type in IMAGE_UTIS:
            return f"![{attachment.title}]({apple_path})  \n"
        return f"[{attachment.title}]({apple_path})  \n"

    def _render_table_markdown(self, table_data: TableData) -> str:
        """Render a TableData object as a markdown table."""
        if not table_data.grid:
            return '[Empty Table]'

        lines = []
        for row_idx, row in enumerate(table_data.grid):
            lines.append('| ' + ' | '.join(row) + ' |')
            # Add separator after first row
            if row_idx == 0:
                separators = ['---'] * len(row)
                lines.append('| ' + ' | '.join(separators) + ' |')

        return '\n'.join(lines)


class NoteExporter:
    """Main exporter class that coordinates the export process."""

    def __init__(self, data_loader, verbose: bool = False, note_path_by_uuid: dict = None,
                 current_note_path: Path = None, note_name: str = None, note_uuid: str = None,
                 *,
                 dialect: ExportDialect = ExportDialect.BACKUP):
        """
        Initialize note exporter.

        Args:
            data_loader: DatabaseNoteDataLoader instance for accessing note data
            verbose: If True, print detailed information about the export process
            note_path_by_uuid: Mapping of note UUID (uppercase) to pre-computed path,
                used for resolving note-to-note links correctly
            current_note_path: Path to the current note's directory, used for computing
                relative paths in note-to-note links
            note_name: Name of the note, used in warning messages
            note_uuid: UUID of the note, used in warning messages
        """
        self.data_loader = data_loader
        self.verbose = verbose
        self.decoder = ProtobufDecoder()
        self.attachment_resolver = AttachmentResolver(data_loader, note_name=note_name, note_uuid=note_uuid)
        self.markdown_generator = MarkdownGenerator(
            self.attachment_resolver, note_path_by_uuid, current_note_path,
            dialect=dialect, note_name=note_name,
        )

    def export_note(self, note_id: int, output_path: str) -> Tuple[str, List[Attachment]]:
        """
        Export a note to markdown.

        Args:
            note_id: Z_PK of the note
            output_path: Path to write markdown file

        Returns:
            Tuple of (markdown content, list of attachments with file paths)
        """
        compressed_data = self._get_note_data(note_id)
        blocks = self.decoder.decode_note(compressed_data)

        # Resolve all file attachments and detect filename collisions before generating markdown
        self._resolve_attachment_filenames(blocks)

        markdown = self.markdown_generator.generate(blocks)
        output_file = Path(output_path)
        output_file.write_text(markdown, encoding='utf-8')

        # Collect attachments with file paths for copying
        file_attachments = []
        for block in blocks:
            if block.type == 'attachment' and block.attachment:
                att = block.attachment
                # Handle gallery attachments - collect children
                if att.type == 'com.apple.notes.gallery' and att.gallery_children:
                    for child in att.gallery_children:
                        if child.file_path:
                            file_attachments.append(child)
                    continue
                # Resolve attachment if not already resolved
                if not att.file_path and not att.title:
                    self.attachment_resolver.resolve_attachment(att)
                # Only include attachments that have actual files (not tables, hashtags, etc.)
                if att.file_path:
                    file_attachments.append(att)

        return markdown, file_attachments

    def _get_note_data(self, note_id: int) -> bytes:
        """Get compressed protobuf data via data loader."""
        return self.data_loader.get_note_data(note_id)

    def _resolve_attachment_filenames(self, blocks: List[ContentBlock]) -> None:
        """Resolve all file attachments and assign unique filenames to avoid collisions.

        This must be called before generating markdown so that the correct filenames
        are used in attachment references.

        Args:
            blocks: List of content blocks from the decoded protobuf
        """
        # First pass: resolve all attachments and collect file attachments
        file_attachments = []
        for block in blocks:
            if block.type == 'attachment' and block.attachment:
                att = block.attachment
                # Skip non-file attachments (tables, hashtags, note links)
                if att.type == 'com.apple.notes.table':
                    continue
                if 'hashtag' in att.type:
                    continue
                if 'inlinetextattachment.link' in att.type:
                    continue

                # Handle gallery attachments - resolve children and add them
                if att.type == 'com.apple.notes.gallery':
                    self.attachment_resolver.resolve_gallery(att)
                    if att.gallery_children:
                        for child in att.gallery_children:
                            if child.file_path and (child.title or child.alt_text):
                                file_attachments.append(child)
                    continue

                # Resolve to get title and file_path
                if not att.file_path and not att.title:
                    self.attachment_resolver.resolve_attachment(att)

                # Only process attachments with files
                if att.file_path and (att.title or att.alt_text):
                    file_attachments.append(att)

        # Second pass: detect collisions and assign unique filenames with proper extensions
        used_names: set = set()
        for att in file_attachments:
            title = att.title or att.alt_text
            sanitized = self.markdown_generator._sanitize_name_for_path(title)

            # Add extension if the sanitized name doesn't have one
            sanitized = self._ensure_file_extension(sanitized, att.type)

            unique_name = self.markdown_generator._make_unique_filename(sanitized, used_names)
            att.unique_filename = unique_name
            used_names.add(unique_name)

    def _ensure_file_extension(self, filename: str, uti_type: str) -> str:
        """Ensure filename has an appropriate extension based on UTI type.

        Args:
            filename: The filename (may or may not have extension)
            uti_type: The UTI type of the attachment (e.g., 'public.jpeg')

        Returns:
            Filename with extension added if it was missing
        """
        # Check if filename already has a recognized extension
        known_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.tiff', '.tif', '.heic', '.heif',
                           '.webp', '.avif', '.bmp', '.svg', '.pdf', '.txt', '.rtf', '.html',
                           '.json', '.xml', '.mov', '.mp4', '.avi', '.mp3', '.m4a', '.doc',
                           '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.zip', '.eml', '.vcf'}

        # Get the current extension (if any)
        _, ext = os.path.splitext(filename)
        if ext.lower() in known_extensions:
            return filename  # Already has a recognized extension

        # Try to get extension from UTI type
        type_ext = get_extension_for_type(uti_type)
        if type_ext:
            return filename + type_ext

        return filename  # No extension could be determined
