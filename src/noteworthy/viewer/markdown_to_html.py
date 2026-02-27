#!/usr/bin/python3
"""Convert noteworthy-flavored markdown to HTML.

Custom converter for the specific markdown subset that noteworthy produces.
Not a general-purpose markdown parser. Uses only stdlib.

Key design detail: noteworthy markdown uses two trailing spaces on nearly every
line as a hard line break. Consecutive body text lines with hard breaks are
grouped into a single <p> with <br> between them, matching how Apple Notes
renders tightly-spaced text.
"""
from __future__ import annotations

import html
import re
import urllib.parse

# Inline formatting patterns -- order matters (bold before italic)
_INLINE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"`([^`]+)`"), r"<code>\1</code>"),
    (re.compile(r"\*\*(.+?)\*\*"), r"<strong>\1</strong>"),
    (re.compile(r"\*(.+?)\*"), r"<em>\1</em>"),
    (re.compile(r"~~(.+?)~~"), r"<del>\1</del>"),
    (re.compile(r"\+\+(.+?)\+\+"), r"<u>\1</u>"),
    (re.compile(r"==(.+?)=="), r"<mark>\1</mark>"),
]

# Matches potential hashtag tokens: #letter followed by word chars and hyphens.
# Used to highlight only known tags (Apple Notes inline text attachments).
_HASHTAG_RE = re.compile(r"(?<!\w)#([a-zA-Z][\w-]*)")

# Blank-line spacer emitted between block elements to preserve vertical whitespace
_BLANK_LINE_HTML = '<div class="blank-line"></div>'

# Image attachment: ![title](Attachments/file.jpg)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((Attachments/[^)]+)\)")
# Non-image attachment: [title](Attachments/file.pdf)
_ATTACHMENT_RE = re.compile(r"\[([^\]]*)\]\((Attachments/[^)]+)\)")
# Note-to-note link: [Title](../path/Note.md)
_NOTE_LINK_RE = re.compile(r"\[([^\]]*)\]\((\.\./[^)]+\.md)\)")
# External URL: [text](https://...) -- handles balanced single-level parens (e.g. Wikipedia)
_EXTERNAL_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://(?:[^()\s]+|\([^()]*\))+)\)")


def _escape(text: str) -> str:
    """Escape HTML entities in text."""
    return html.escape(text, quote=True)


def _cleanup_orphaned_markers(text: str) -> str:
    """Strip orphaned * formatting markers not consumed by inline patterns.

    The noteworthy exporter can produce cross-line italic/bold markers that
    don't form valid pairs within a single line. After inline pattern matching,
    any remaining * sequences at text boundaries are orphaned and should be removed.
    """
    # Lines that are only asterisks/whitespace → empty
    if re.match(r"^\s*\*+\s*$", text):
        return ""
    # Leading 2+ asterisks (e.g., orphaned bold/italic open)
    text = re.sub(r"^\*{2,4}\s*", "", text)
    # Trailing 2+ asterisks (e.g., orphaned bold/italic close)
    text = re.sub(r"\*{2,4}\s*$", "", text)
    # Trailing single asterisk after non-word char (orphaned italic close)
    text = re.sub(r"(?<=\W)\*\s*$", "", text)
    return text


def _process_inline(text: str, note_id: str = "", known_tags: frozenset = frozenset()) -> str:
    """Apply inline formatting to a line of text.

    Text is first HTML-escaped, then formatting patterns are applied.
    Links and attachments are processed before escaping to preserve URLs.
    """
    # Process links/attachments first (before escaping), collecting replacements
    replacements: list[tuple[int, int, str]] = []

    for m in _IMAGE_RE.finditer(text):
        title = _escape(m.group(1))
        path = m.group(2)
        if note_id:
            encoded_id = urllib.parse.quote(note_id, safe="")
            url = f"/api/attachment/{encoded_id}/{path.split('/', 1)[1]}"
        else:
            url = path
        replacement = (
            f'<a href="{_escape(url)}" class="image-link" target="_blank">'
            f'<img src="{_escape(url)}" alt="{title}" loading="lazy"></a>'
        )
        replacements.append((m.start(), m.end(), replacement))

    for m in _NOTE_LINK_RE.finditer(text):
        if any(start <= m.start() < end for start, end, _ in replacements):
            continue
        title = _escape(m.group(1))
        path = m.group(2)
        replacement = f'<a href="#" class="note-link" data-path="{_escape(path)}">{title}</a>'
        replacements.append((m.start(), m.end(), replacement))

    for m in _EXTERNAL_LINK_RE.finditer(text):
        if any(start <= m.start() < end for start, end, _ in replacements):
            continue
        link_text = _escape(m.group(1))
        url = m.group(2)
        replacement = f'<a href="{_escape(url)}" target="_blank" rel="noopener">{link_text}</a>'
        replacements.append((m.start(), m.end(), replacement))

    for m in _ATTACHMENT_RE.finditer(text):
        if any(start <= m.start() < end for start, end, _ in replacements):
            continue
        title = _escape(m.group(1))
        path = m.group(2)
        if note_id:
            encoded_id = urllib.parse.quote(note_id, safe="")
            url = f"/api/attachment/{encoded_id}/{path.split('/', 1)[1]}"
        else:
            url = path
        replacement = f'<a href="{_escape(url)}" class="attachment-link" target="_blank">{title}</a>'
        replacements.append((m.start(), m.end(), replacement))

    # Apply replacements in reverse order to preserve positions
    if replacements:
        replacements.sort(key=lambda r: r[0], reverse=True)
        parts = list(text)
        result_parts = []
        last_end = len(text)
        for start, end, repl in replacements:
            # Escape the text after this replacement
            after = "".join(parts[end:last_end])
            result_parts.insert(0, _escape(after))
            result_parts.insert(0, repl)
            last_end = start
        # Escape remaining text before first replacement
        before = "".join(parts[:last_end])
        result_parts.insert(0, _escape(before))
        escaped = "".join(result_parts)
    else:
        escaped = _escape(text)

    # Apply inline formatting patterns
    for pattern, replacement in _INLINE_PATTERNS:
        escaped = pattern.sub(replacement, escaped)

    # Apply hashtag spans only for known tags on this note
    if known_tags:
        def _tag_replace(m: re.Match) -> str:
            return (
                f'<span class="hashtag">#{m.group(1)}</span>'
                if m.group(1).lower() in known_tags
                else m.group(0)
            )
        escaped = _HASHTAG_RE.sub(_tag_replace, escaped)

    # Clean up orphaned formatting markers the exporter may produce
    # (e.g., cross-line italic/bold that can't be matched within a single line)
    escaped = _cleanup_orphaned_markers(escaped)

    return escaped


def _parse_table_alignment(separator_line: str) -> list[str]:
    """Parse table separator line to determine column alignments.

    Returns a list of 'left', 'center', or 'right' for each column.
    """
    cells = [c.strip() for c in separator_line.strip().strip("|").split("|")]
    alignments = []
    for cell in cells:
        cell = cell.strip()
        if cell.startswith(":") and cell.endswith(":"):
            alignments.append("center")
        elif cell.endswith(":"):
            alignments.append("right")
        else:
            alignments.append("left")
    return alignments


def _is_table_separator(line: str) -> bool:
    """Check if a line is a table separator row (e.g., | --- | --- |)."""
    stripped = line.strip().strip("|")
    cells = [c.strip() for c in stripped.split("|")]
    return all(re.match(r":?-{2,}:?$", c) for c in cells if c)


def _has_hard_break(raw: str) -> bool:
    """Check if a raw line ends with two or more trailing spaces (markdown hard break)."""
    return raw.rstrip("\n").endswith("  ")


def _is_block_element(stripped: str) -> bool:
    """Check if a stripped line starts a block-level element (heading, list, etc.)."""
    if stripped.startswith(("# ", "## ", "### ")) or stripped in ("#", "##", "###"):
        return True
    if stripped.startswith(("* ", "- ")):
        return True
    if re.match(r"^\*{2,}\s", stripped):  # orphaned marker + bullet: ** text
        return True
    if stripped.startswith(("- [ ] ", "- [x] ")):
        return True
    if stripped.startswith("> "):
        return True
    if stripped.startswith("```"):
        return True
    if re.match(r"^\d+\. ", stripped):
        return True
    return False


def _is_block_element_raw(line: str) -> bool:
    """Check if a raw (non-stripped) line starts a block-level element, including indented lists."""
    return _is_block_element(line.strip()) or _parse_list_item(line) is not None


def _is_continuation_line(line: str) -> bool:
    """Check if a line is plain body text that can continue a preceding list item.

    Returns True for non-empty lines that are not block elements (headings,
    lists, code fences, etc.) or table separators. The caller is responsible
    for verifying the hard-break connection to the preceding list item.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if _is_block_element_raw(line):
        return False
    if "|" in stripped and _is_table_separator(line):
        return False
    return True


def _parse_list_item(line: str) -> tuple[int, str, str] | None:
    """Parse a line as a list item, returning (indent_level, list_type, content) or None.

    Recognizes indented list items (4 spaces per indent level) for all list types:
    bullet (* ), dashed (- ), numbered (1. ), and checklist (- [ ] / - [x] ).
    """
    # Count leading spaces → indent level (4 spaces per level)
    stripped_left = line.lstrip(" ")
    leading_spaces = len(line) - len(stripped_left)
    indent = leading_spaces // 4

    # Checklist (must check before dashed since both start with "- ")
    if stripped_left.startswith("- [ ] ") or stripped_left.startswith("- [x] "):
        return (indent, "checklist", stripped_left)
    # Ordered list
    m = re.match(r"^(\d+)\. (.*)$", stripped_left)
    if m:
        return (indent, "ol", stripped_left)
    # Unordered list (bullet or dash)
    if stripped_left.startswith("* ") or stripped_left.startswith("- "):
        return (indent, "ul", stripped_left)
    # Orphaned bold/italic marker before bullet: ** text, *** text
    star_bullet = re.match(r"^\*{2,}\s+(.*)", stripped_left)
    if star_bullet:
        content = star_bullet.group(1).strip()
        if content and not re.match(r"^\*+$", content):
            return (indent, "ul", stripped_left)
    return None


def convert(markdown: str, note_id: str = "", skip_title: str = "", tags: list[str] = ()) -> str:
    """Convert noteworthy markdown to HTML.

    Args:
        markdown: The markdown content to convert.
        note_id: The note ID, used to construct attachment URLs.
        skip_title: If set, the first H1 matching this title will be omitted
            (to avoid duplicating the title shown in the note header).
        tags: Known tag names for this note (lowercase, without #). Only these
            will be wrapped in <span class="hashtag">; plain #word text is left alone.

    Returns:
        HTML string.
    """
    if not markdown:
        return ""

    _known_tags = frozenset(tags)

    def _pi(text: str) -> str:
        return _process_inline(text, note_id=note_id, known_tags=_known_tags)

    lines = markdown.split("\n")
    html_parts: list[str] = []
    i = 0
    in_code_block = False
    code_lines: list[str] = []
    code_lang_attr: str = ""
    # List nesting stack: each entry is the list type ('ul', 'ol', 'checklist') at that depth.
    # Empty stack means not in a list. Stack[0] is the outermost list.
    list_stack: list[str] = []
    in_blockquote = False
    title_skipped = False

    def _flush_code_block():
        nonlocal in_code_block
        content = "\n".join(code_lines).rstrip("\n")
        html_parts.append(f"<pre><code{code_lang_attr}>{content}</code></pre>")
        in_code_block = False

    li_open = False  # whether the last <li> is still open (waiting for potential nesting)

    def _list_close_tag(list_type: str) -> str:
        return "</ol>" if list_type == "ol" else "</ul>"

    def _list_open_tag(list_type: str) -> str:
        if list_type == "checklist":
            return '<ul class="checklist">'
        if list_type == "ol":
            return "<ol>"
        return "<ul>"

    def _close_li_if_open():
        nonlocal li_open
        if li_open:
            html_parts.append("</li>")
            li_open = False

    def _close_list():
        """Close all open list levels."""
        _close_li_if_open()
        while list_stack:
            html_parts.append(_list_close_tag(list_stack.pop()))
            # Each list container was nested inside a parent <li> (except the outermost)
            if list_stack:
                html_parts.append("</li>")

    def _adjust_list_depth(target_depth: int, list_type: str):
        """Adjust the list nesting to match the target depth and type.

        Opens or closes nested lists as needed so that the next <li> will be
        at the correct nesting level. The target_depth is 0-based
        (0 = outermost list level).
        """
        nonlocal li_open
        target_level = target_depth + 1  # desired stack length

        # Close deeper levels
        _close_li_if_open()
        while len(list_stack) > target_level:
            html_parts.append(_list_close_tag(list_stack.pop()))
            # The parent <li> that contained this nested list
            if list_stack:
                html_parts.append("</li>")

        if len(list_stack) == target_level:
            # Same depth — check if list type changed
            if list_stack[-1] != list_type:
                # Type changed: close current list, open new one of the right type
                html_parts.append(_list_close_tag(list_stack.pop()))
                if list_stack:
                    html_parts.append("</li>")
                html_parts.append(_list_open_tag(list_type))
                list_stack.append(list_type)
        else:
            # Need to go deeper — open nested lists
            while len(list_stack) < target_level:
                html_parts.append(_list_open_tag(list_type))
                list_stack.append(list_type)

    def _close_blockquote():
        nonlocal in_blockquote
        if in_blockquote:
            html_parts.append("</blockquote>")
            in_blockquote = False

    def _next_is_continuation(raw: str, idx: int) -> bool:
        """Check if the line at idx has a hard break and the following line is continuation text."""
        return _has_hard_break(raw) and idx + 1 < len(lines) and _is_continuation_line(lines[idx + 1])

    while i < len(lines):
        line = lines[i]
        raw_line = line
        stripped = line.strip()

        # Code blocks
        if stripped.startswith("```"):
            if in_code_block:
                _flush_code_block()
            else:
                _close_list()
                _close_blockquote()
                lang = stripped[3:].strip()
                code_lang_attr = f' class="language-{_escape(lang)}"' if lang else ""
                code_lines = []
                in_code_block = True
            i += 1
            continue

        if in_code_block:
            code_lines.append(_escape(line.rstrip()))
            i += 1
            continue

        # Table detection: line with pipes that is followed by a separator line
        if "|" in stripped and i + 1 < len(lines) and _is_table_separator(lines[i + 1]):
            _close_list()
            _close_blockquote()
            # Parse header
            header_cells = [c.strip() for c in stripped.strip().strip("|").split("|")]
            alignments = _parse_table_alignment(lines[i + 1])
            html_parts.append("<table>")
            html_parts.append("<thead><tr>")
            for ci, cell in enumerate(header_cells):
                align = alignments[ci] if ci < len(alignments) else "left"
                style = f' style="text-align: {align}"' if align != "left" else ""
                html_parts.append(f"<th{style}>{_pi(cell)}</th>")
            html_parts.append("</tr></thead>")
            html_parts.append("<tbody>")
            i += 2  # skip header and separator
            while i < len(lines) and "|" in lines[i].rstrip():
                row_stripped = lines[i].rstrip()
                if not row_stripped.strip():
                    break
                cells = [c.strip() for c in row_stripped.strip().strip("|").split("|")]
                html_parts.append("<tr>")
                for ci, cell in enumerate(cells):
                    align = alignments[ci] if ci < len(alignments) else "left"
                    style = f' style="text-align: {align}"' if align != "left" else ""
                    html_parts.append(f"<td{style}>{_pi(cell)}</td>")
                html_parts.append("</tr>")
                i += 1
            html_parts.append("</tbody></table>")
            continue

        # Block quotes
        if stripped.startswith("> "):
            _close_list()
            if not in_blockquote:
                html_parts.append("<blockquote>")
                in_blockquote = True
            content = stripped[2:]
            html_parts.append(f"<p>{_pi(content)}</p>")
            i += 1
            continue
        elif in_blockquote and stripped == ">":
            # Empty blockquote continuation line
            i += 1
            continue
        elif in_blockquote and not stripped.startswith(">"):
            _close_blockquote()

        # Headings
        if stripped.startswith("### ") or stripped == "###":
            _close_list()
            _close_blockquote()
            heading_text = stripped[4:] if len(stripped) > 3 else ""
            html_parts.append(f"<h3>{_pi(heading_text)}</h3>")
            i += 1
            continue
        if stripped.startswith("## ") or stripped == "##":
            _close_list()
            _close_blockquote()
            heading_text = stripped[3:] if len(stripped) > 2 else ""
            html_parts.append(f"<h2>{_pi(heading_text)}</h2>")
            i += 1
            continue
        if stripped.startswith("# ") or stripped == "#":
            _close_list()
            _close_blockquote()
            heading_text = stripped[2:] if len(stripped) > 1 else ""
            # Skip the first H1 if it matches the note title (already shown in header)
            if skip_title and not title_skipped and heading_text.strip() == skip_title.strip():
                title_skipped = True
                i += 1
                continue
            html_parts.append(f"<h1>{_pi(heading_text)}</h1>")
            i += 1
            continue

        # List items (bullet, dashed, numbered, checklist) with nesting support
        list_item = _parse_list_item(line)
        if list_item is not None:
            indent, list_type, item_text = list_item
            _close_blockquote()
            _adjust_list_depth(indent, list_type)

            # Extract content and open <li>. It will be closed by the next
            # _adjust_list_depth call, _close_list, or inline if no nesting follows.
            if list_type == "checklist":
                checked = item_text.startswith("- [x] ")
                content = item_text[6:]
                checked_attr = " checked" if checked else ""
                done_class = ' class="done"' if checked else ""
                html_parts.append(
                    f"<li{done_class}><input type=\"checkbox\" disabled{checked_attr}>"
                    f"{_pi(content)}"
                )
            elif list_type == "ol":
                m = re.match(r"^(\d+)\. (.*)$", item_text)
                html_parts.append(f"<li>{_pi(m.group(2))}")
            else:  # ul
                star_bullet = re.match(r"^\*{2,}\s+(.*)", item_text)
                if star_bullet:
                    content = star_bullet.group(1).strip()
                else:
                    content = item_text[2:]
                html_parts.append(f"<li>{_pi(content)}")

            # Close <li> inline if next line isn't a deeper nested item or continuation text
            next_item = _parse_list_item(lines[i + 1]) if i + 1 < len(lines) else None
            if next_item is not None and next_item[0] > indent:
                li_open = True  # leave open for nesting
            elif _next_is_continuation(raw_line, i):
                li_open = True  # leave open for continuation text
            else:
                html_parts[-1] += "</li>"
            i += 1
            continue

        # Continuation line inside an open list item (body text connected by hard break)
        if li_open and list_stack and _is_continuation_line(line):
            html_parts.append(f"<br>{_pi(stripped)}")
            if not _next_is_continuation(raw_line, i):
                html_parts.append("</li>")
                li_open = False
            i += 1
            continue

        # Non-list line -- close any open list
        if list_stack and stripped:
            _close_list()

        # Empty line or line that is only formatting markers (e.g., ****, ** **)
        if not stripped or re.match(r"^\*[\s*]*$", stripped):
            if list_stack:
                _close_list()
            # Preserve blank lines as vertical space (matches Apple Notes rendering).
            # Only emit when there's already content (skip leading blank lines).
            if not stripped and html_parts:
                html_parts.append(_BLANK_LINE_HTML)
            i += 1
            continue

        # Paragraph: group consecutive body-text lines with hard breaks into
        # a single <p> with <br> between them. This matches how Apple Notes
        # renders tightly-spaced text.
        para_parts = [_pi(stripped)]
        has_break = _has_hard_break(raw_line)
        while has_break and i + 1 < len(lines):
            next_line = lines[i + 1]
            next_stripped = next_line.strip()
            # Stop grouping if the next line is empty or starts a block element
            if not next_stripped or _is_block_element_raw(next_line):
                break
            # Also stop if next line is a table row
            if "|" in next_stripped and i + 2 < len(lines) and _is_table_separator(lines[i + 2]):
                break
            para_parts.append("<br>")
            para_parts.append(_pi(next_stripped))
            has_break = _has_hard_break(next_line)
            i += 1
        html_parts.append(f"<p>{''.join(para_parts)}</p>")
        i += 1

    # Close any remaining open elements
    _close_list()
    _close_blockquote()
    if in_code_block:
        _flush_code_block()

    # Strip trailing blank-line spacers
    while html_parts and html_parts[-1] == _BLANK_LINE_HTML:
        html_parts.pop()

    return "\n".join(html_parts)
