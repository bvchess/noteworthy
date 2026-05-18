#!/usr/bin/env python
"""Integration tests for MarkdownGenerator in Obsidian dialect mode.

These exercise the four branch sites the dialect param controls:

  1. Inter-note links become [[Target]] / [[target|display]]
  2. Attachment refs become ![[file.jpg]] (image) or [[file.pdf]] (link)
  3. The title line is stripped from the body
  4. Inline underline (`++`) translates to highlight (`==`); never raw HTML

Tests build ContentBlock / Attachment objects directly and call
MarkdownGenerator.generate() so we don't depend on protobuf fixtures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from noteworthy.markdown_renderer import MarkdownGenerator, AttachmentResolver
from noteworthy.note_content import Attachment, ContentBlock
from noteworthy.obsidian.dialect import ExportDialect


# ---------- test helpers ----------


class _StubDataLoader:
    """Minimal data_loader stub — never called when attachments are pre-populated."""

    def get_attachment_metadata(self, uuid):  # pragma: no cover - defensive
        return None


def _make_generator(
    *,
    note_path_by_uuid: dict | None = None,
    current_note_path: Path | None = None,
    note_name: str | None = None,
) -> MarkdownGenerator:
    resolver = AttachmentResolver(_StubDataLoader())
    return MarkdownGenerator(
        resolver,
        note_path_by_uuid=note_path_by_uuid,
        current_note_path=current_note_path,
        dialect=ExportDialect.OBSIDIAN,
        note_name=note_name,
    )


def _text(text: str, **kwargs) -> ContentBlock:
    return ContentBlock(type="text", text=text, **kwargs)


def _attachment_block(att: Attachment) -> ContentBlock:
    return ContentBlock(type="attachment", text=None, attachment=att)


# ---------- 1. Inter-note wikilinks ----------


class TestInterNoteWikilinks:
    """Branch at markdown_renderer.py ~:990 (link to another note)."""

    def _link_attachment(self, target_uuid: str, display_text: str) -> Attachment:
        att = Attachment(uuid="link-1", type="com.apple.notes.inlinetextattachment.link")
        att.alt_text = display_text
        att.token_content_identifier = f"applenotes:note/{target_uuid}"
        return att

    def test_simple_wikilink_when_target_resolves(self, tmp_path):
        target_uuid = "AAAA-BBBB-CCCC-DDDD"
        target_path = tmp_path / "iCloud" / "Folder" / "Target Note"
        gen = _make_generator(
            note_path_by_uuid={target_uuid.upper(): target_path},
            current_note_path=tmp_path / "iCloud" / "Folder" / "Source Note",
        )
        att = self._link_attachment(target_uuid, "Target Note")
        out = gen.generate([_attachment_block(att)])
        assert "[[Target Note]]" in out
        # Backup-mode artifacts must NOT leak into Obsidian output.
        assert "../" not in out
        assert "](" not in out

    def test_wikilink_with_alias_when_display_differs_from_filename(self, tmp_path):
        """If the display text the user typed differs from the resolved filename,
        emit `[[filename|display]]` so the visible text matches what was typed."""
        target_uuid = "AAAA-BBBB-CCCC-DDDD"
        # path.name is the on-disk note filename (without .md). It can differ from
        # the link text if the user typed an alias or if sanitization changed things.
        target_path = tmp_path / "iCloud" / "Folder" / "target-note-renamed"
        gen = _make_generator(
            note_path_by_uuid={target_uuid.upper(): target_path},
            current_note_path=tmp_path / "src",
        )
        att = self._link_attachment(target_uuid, "Original Display")
        out = gen.generate([_attachment_block(att)])
        assert "[[target-note-renamed|Original Display]]" in out

    def test_unresolved_target_still_emits_wikilink(self, tmp_path):
        """Per requirements §6, a missing target produces an unresolved wikilink
        (Obsidian colors these distinctly) rather than degrading to plain text."""
        gen = _make_generator(
            note_path_by_uuid={},  # nothing resolves
            current_note_path=tmp_path / "src",
        )
        att = self._link_attachment("UNKNOWN-UUID", "Some Deleted Note")
        out = gen.generate([_attachment_block(att)])
        assert "[[Some Deleted Note]]" in out
        assert "](" not in out


# ---------- 2. Attachment refs (embed vs link by extension) ----------


class TestAttachmentEmbeds:
    """Branch at markdown_renderer.py ~:1058 (regular attachments)."""

    def _file_attachment(self, *, uti: str, title: str, unique_filename: str) -> Attachment:
        att = Attachment(uuid="att-1", type=uti)
        att.title = title
        att.file_path = f"/tmp/{unique_filename}"
        att.unique_filename = unique_filename
        return att

    def test_jpg_emits_image_embed(self):
        gen = _make_generator()
        att = self._file_attachment(uti="public.jpeg", title="Photo", unique_filename="photo.jpg")
        out = gen.generate([_attachment_block(att)])
        assert "![[photo.jpg]]" in out
        assert "Attachments/" not in out
        assert "](" not in out

    def test_png_emits_image_embed(self):
        gen = _make_generator()
        att = self._file_attachment(uti="public.png", title="Diagram", unique_filename="diagram.png")
        out = gen.generate([_attachment_block(att)])
        assert "![[diagram.png]]" in out

    def test_pdf_emits_link_not_embed(self):
        gen = _make_generator()
        att = self._file_attachment(uti="com.adobe.pdf", title="Receipt", unique_filename="receipt.pdf")
        out = gen.generate([_attachment_block(att)])
        assert "[[receipt.pdf]]" in out
        # Must NOT be embedded (no leading bang).
        assert "![[receipt.pdf]]" not in out

    def test_audio_emits_link(self):
        gen = _make_generator()
        att = self._file_attachment(uti="public.mpeg-4-audio", title="Voice", unique_filename="voice.m4a")
        out = gen.generate([_attachment_block(att)])
        assert "[[voice.m4a]]" in out
        assert "![[voice.m4a]]" not in out

    def test_uppercase_extension_still_embedded(self):
        gen = _make_generator()
        att = self._file_attachment(uti="public.jpeg", title="Photo", unique_filename="Photo.JPG")
        out = gen.generate([_attachment_block(att)])
        assert "![[Photo.JPG]]" in out


# ---------- 3. Title-line stripping ----------


class TestTitleStripping:
    """The strip happens in MarkdownGenerator.generate() before block emission."""

    def test_title_line_dropped_from_body(self):
        gen = _make_generator(note_name="My Plan")
        blocks = [_text("My Plan"), _text("body content")]
        out = gen.generate(blocks)
        # The title shouldn't appear as a standalone first line.
        # (It might still appear later in the body — e.g., inside other content —
        #  but the requirement is that it's not the duplicate first line.)
        first_nonblank = next((ln for ln in out.splitlines() if ln.strip()), "")
        assert "My Plan" not in first_nonblank
        assert "body content" in out

    def test_no_strip_when_first_block_differs(self):
        gen = _make_generator(note_name="My Plan")
        blocks = [_text("Different opening"), _text("more")]
        out = gen.generate(blocks)
        assert "Different opening" in out


# ---------- 4. Inline marker translation (underline -> highlight, no HTML) ----------


class TestInlineMarkerTranslation:
    """Underline has no native Obsidian syntax; translate to highlight (`==`)
    per requirements §6.2. Never emit raw HTML."""

    def test_underline_renders_as_highlight_markers(self):
        gen = _make_generator()
        out = gen.generate([_text("important", underlined=True)])
        assert "==important==" in out
        # The backup-mode `++` underline marker must not appear in Obsidian output.
        assert "++" not in out

    def test_no_html_tags_anywhere_in_output(self):
        """Defense-in-depth: regardless of formatting combo, the renderer must
        never emit HTML tag syntax in Obsidian mode."""
        gen = _make_generator()
        # Cover several combinations that could conceivably trigger HTML fallback.
        blocks = [
            _text("u", underlined=True),
            _text(" "),
            _text("b", bold=True),
            _text(" "),
            _text("i", italic=True),
            _text(" "),
            _text("s", strikethrough=True),
            _text(" "),
            _text("u+h", underlined=True, emphasis_color="0xFFFF00"),
        ]
        out = gen.generate(blocks)
        # Strip out any code-fence content where angle brackets are legitimate;
        # our blocks don't include code, so a simple scan is fine.
        assert "<" not in out, f"unexpected '<' in output: {out!r}"
        assert ">" not in out, f"unexpected '>' in output: {out!r}"

    def test_highlight_alone_still_uses_double_equals(self):
        gen = _make_generator()
        out = gen.generate([_text("warn", emphasis_color="0xFFFF00")])
        assert "==warn==" in out

    def test_underline_plus_highlight_collapse_to_single_pair(self):
        """Both attributes on the same text should not produce `====text====`
        (which Obsidian would render as empty highlight around empty highlight).
        The translation folds underline into highlight, so a single `==text==`
        is emitted."""
        gen = _make_generator()
        out = gen.generate([_text("both", underlined=True, emphasis_color="0xFFFF00")])
        assert "==both==" in out
        assert "====" not in out
