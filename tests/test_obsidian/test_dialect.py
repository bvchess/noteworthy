#!/usr/bin/env python
"""Tests for obsidian.dialect — pure helpers used by the dialect-aware renderer.

Each helper formats one kind of Obsidian-mode output (wikilink, attachment ref,
title-strip predicate). Keeping these functions small and pure lets the renderer's
branch sites stay trivial: one `if dialect is OBSIDIAN` then a helper call.
"""

from __future__ import annotations

import pytest

from noteworthy.note_content import ContentBlock
from noteworthy.obsidian.dialect import (
    ExportDialect,
    IMAGE_EXTENSIONS,
    format_internote_link,
    format_attachment_ref,
    strip_title_block,
)


class TestExportDialectEnum:
    def test_has_backup_and_obsidian(self):
        assert ExportDialect.BACKUP
        assert ExportDialect.OBSIDIAN
        assert ExportDialect.BACKUP is not ExportDialect.OBSIDIAN


class TestImageExtensions:
    def test_lowercased(self):
        # Every member is lowercase so the renderer can compare with a lowered ext.
        assert all(ext == ext.lower() for ext in IMAGE_EXTENSIONS)

    def test_includes_common_formats(self):
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".avif"):
            assert ext in IMAGE_EXTENSIONS

    def test_excludes_non_images(self):
        for ext in (".pdf", ".mp4", ".m4a", ".txt", ".heic"):
            assert ext not in IMAGE_EXTENSIONS


class TestFormatInternoteLink:
    def test_simple_target(self):
        assert format_internote_link("My Note") == "[[My Note]]"

    def test_display_same_as_target_no_pipe(self):
        assert format_internote_link("My Note", display="My Note") == "[[My Note]]"

    def test_display_differs_uses_pipe(self):
        assert format_internote_link("my-note", display="My Note") == "[[my-note|My Note]]"

    def test_display_none_no_pipe(self):
        assert format_internote_link("Target", display=None) == "[[Target]]"

    def test_display_empty_string_treated_as_none(self):
        assert format_internote_link("Target", display="") == "[[Target]]"


class TestFormatAttachmentRef:
    def test_jpg_is_embedded(self):
        assert format_attachment_ref("photo.jpg") == "![[photo.jpg]]"

    def test_png_is_embedded(self):
        assert format_attachment_ref("diagram.png") == "![[diagram.png]]"

    def test_gif_is_embedded(self):
        assert format_attachment_ref("clip.gif") == "![[clip.gif]]"

    def test_uppercase_extension_still_embedded(self):
        assert format_attachment_ref("Photo.JPG") == "![[Photo.JPG]]"

    def test_pdf_is_linked_not_embedded(self):
        assert format_attachment_ref("receipt.pdf") == "[[receipt.pdf]]"

    def test_audio_is_linked(self):
        assert format_attachment_ref("voice.m4a") == "[[voice.m4a]]"

    def test_video_is_linked(self):
        assert format_attachment_ref("clip.mp4") == "[[clip.mp4]]"

    def test_no_extension_is_linked(self):
        assert format_attachment_ref("README") == "[[README]]"

    def test_heic_is_not_in_obsidians_native_image_set(self):
        # Obsidian doesn't natively render HEIC; treat as link.
        assert format_attachment_ref("photo.heic") == "[[photo.heic]]"


def _text_block(text: str, **kwargs) -> ContentBlock:
    """Build a ContentBlock representing a plain text run; kwargs override defaults."""
    return ContentBlock(type="text", text=text, **kwargs)


class TestStripTitleBlock:
    def test_first_block_matches_plain(self):
        blocks = [_text_block("My Plan"), _text_block("rest of note")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 1
        assert result[0].text == "rest of note"

    def test_first_block_matches_heading_style(self):
        # Heading prefix `# ` is added at render time, not stored in .text.
        blocks = [_text_block("My Plan", style="heading"), _text_block("body")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 1
        assert result[0].text == "body"

    def test_first_block_matches_bold(self):
        blocks = [_text_block("My Plan", bold=True), _text_block("body")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 1
        assert result[0].text == "body"

    def test_case_insensitive_match(self):
        blocks = [_text_block("my plan"), _text_block("body")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 1
        assert result[0].text == "body"

    def test_first_block_mismatch_leaves_body_alone(self):
        blocks = [_text_block("Different opening"), _text_block("more")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 2
        assert result[0].text == "Different opening"

    def test_empty_leading_blocks_skipped(self):
        # Whitespace-only blocks before the title don't anchor the comparison;
        # they're skipped and the next non-empty block is tested.
        blocks = [_text_block(""), _text_block("   "), _text_block("My Plan"), _text_block("body")]
        result = strip_title_block(blocks, "My Plan")
        # The empty leading blocks are preserved; only the title block is dropped.
        assert [b.text for b in result] == ["", "   ", "body"]

    def test_attachment_as_first_block_leaves_alone(self):
        """If the body starts with an attachment, there's no title line to strip."""
        att_block = ContentBlock(type="attachment", text=None)
        blocks = [att_block, _text_block("body")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 2
        assert result[0] is att_block

    def test_empty_blocks_list(self):
        assert strip_title_block([], "Anything") == []

    def test_empty_note_name_is_noop(self):
        blocks = [_text_block("Some content")]
        assert strip_title_block(blocks, "") == blocks

    def test_none_note_name_is_noop(self):
        blocks = [_text_block("Some content")]
        assert strip_title_block(blocks, None) == blocks

    def test_trailing_whitespace_ignored(self):
        blocks = [_text_block("  My Plan  \n"), _text_block("body")]
        result = strip_title_block(blocks, "My Plan")
        assert len(result) == 1
        assert result[0].text == "body"
