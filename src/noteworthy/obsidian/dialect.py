"""Dialect helpers for the Obsidian export mode.

This module centralizes the Obsidian-specific output decisions so the main
markdown renderer stays readable: each branch site in `markdown_renderer.py`
checks the dialect once and calls into one of the helpers below. Behavior
that's best expressed as a data transform on the block stream (title-line
stripping) also lives here as a pure function.

See obsidian_requirements.md §6 for the dialect rules these helpers implement.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import List

from noteworthy.note_content import ContentBlock


__all__ = [
    "ExportDialect",
    "IMAGE_EXTENSIONS",
    "format_internote_link",
    "format_attachment_ref",
    "strip_title_block",
]


class ExportDialect(Enum):
    """Which output format the markdown renderer should produce."""
    BACKUP = "backup"      # current behavior: relative markdown links, Attachments/ subdirs, etc.
    OBSIDIAN = "obsidian"  # wikilinks, single assets/ folder, frontmatter Properties


# Obsidian's native image-rendering extensions (lowercased for case-insensitive lookup).
# See https://obsidian.md/help/file-formats. Anything outside this set is emitted as
# a non-embedding `[[link]]` rather than `![[embed]]` so users aren't surprised by
# inline PDF/audio/video players in their notes.
IMAGE_EXTENSIONS = frozenset({
    ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp",
})


def format_internote_link(target: str, display: str | None = None) -> str:
    """Format an inter-note wikilink.

    `target` is the destination note's on-disk filename (without `.md`).
    `display` is the visible link text; if it differs from `target`, an alias-pipe
    is added (`[[target|display]]`). When display equals target or is empty/None,
    a bare `[[target]]` is emitted.
    """
    if display and display != target:
        return f"[[{target}|{display}]]"
    return f"[[{target}]]"


def format_attachment_ref(filename: str) -> str:
    """Format an attachment reference, embedding only for image extensions.

    `filename` includes the extension (e.g., `photo.jpg`). The decision between
    `![[…]]` (embed) and `[[…]]` (link) is made purely by extension — Obsidian
    only natively renders images inline, so non-image embeds would surprise users.
    Extension comparison is case-insensitive.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return f"![[{filename}]]"
    return f"[[{filename}]]"


def strip_title_block(blocks: List[ContentBlock], note_name: str | None) -> List[ContentBlock]:
    """Drop the first content block whose plain text matches `note_name`.

    Apple Notes treats the first line of a note as its title; in Obsidian the title
    comes from the filename instead, so emitting the line again duplicates it
    visually. This function finds the first *non-empty* block, compares its plain
    text to the note's name (case-insensitive, both stripped), and drops the block
    if they match. If they don't match, the body is left alone — we never silently
    delete unrelated content.

    `block.text` already holds the plain text without markdown markers (the `# `
    heading prefix and `**bold**` markers are added at render time), so the
    comparison handles plain, heading-styled, and bold-styled title lines uniformly.

    Empty/whitespace-only leading TEXT blocks are skipped over (Apple Notes can
    serialize a leading blank line). An attachment is *not* skipped — if it is
    the first content block, the body's "first content" is the attachment, not
    the title, so we leave the body alone per §6.1's "doesn't match → preserve."
    """
    if not blocks or not note_name:
        return blocks

    target = note_name.strip().casefold()
    for i, block in enumerate(blocks):
        if block.type == "text" and not (block.text and block.text.strip()):
            # Empty leading text block — skip over it.
            continue
        if block.type == "text" and block.text.strip().casefold() == target:
            return blocks[:i] + blocks[i + 1:]
        # First non-empty block is either non-matching text or an attachment —
        # leave the body untouched.
        return blocks

    return blocks
