#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""Tests for make_markdown_copy() using a test SQLite database."""

import pytest

from noteworthy.note_copy import make_markdown_copy, _extract_zpk


class TestExtractZpk:
    def test_valid_uri(self):
        zpk = _extract_zpk("x-coredata://UUID/ICNote/p12345")
        assert zpk == 12345

    def test_single_digit(self):
        zpk = _extract_zpk("x-coredata://UUID/ICNote/p1")
        assert zpk == 1

    def test_invalid_uri_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _extract_zpk("not-a-valid-uri")

    def test_missing_p_prefix_raises(self):
        with pytest.raises(ValueError, match="Cannot extract"):
            _extract_zpk("x-coredata://UUID/ICNote/12345")


class TestMakeMarkdownCopy:
    def test_creates_output_directory(self, notestore_db, tmp_path):
        note_dir = tmp_path / "test_note"
        note_id = "x-coredata://TEST-UUID-0000-0000-000000000000/ICNote/p100"
        make_markdown_copy(note_id, note_dir, db_path=notestore_db)
        assert note_dir.is_dir()

    def test_creates_markdown_file(self, notestore_db, tmp_path):
        note_dir = tmp_path / "test_note"
        note_id = "x-coredata://TEST-UUID-0000-0000-000000000000/ICNote/p100"
        make_markdown_copy(note_id, note_dir, db_path=notestore_db)
        md_file = note_dir / "test_note.md"
        assert md_file.is_file()

    def test_markdown_not_empty(self, notestore_db, tmp_path):
        note_dir = tmp_path / "test_note"
        note_id = "x-coredata://TEST-UUID-0000-0000-000000000000/ICNote/p100"
        make_markdown_copy(note_id, note_dir, db_path=notestore_db)
        md_file = note_dir / "test_note.md"
        content = md_file.read_text()
        assert len(content) > 0

    def test_accepts_note_object(self, notestore_db, tmp_path):
        from noteworthy.notes_datatypes import Note
        note = Note("Test", "x-coredata://TEST-UUID-0000-0000-000000000000/ICNote/p100", None, None, None)
        note_dir = tmp_path / "note_obj"
        make_markdown_copy(note, note_dir, db_path=notestore_db)
        md_file = note_dir / "note_obj.md"
        assert md_file.is_file()

    def test_stale_attachments_removed_on_update(self, notestore_db, tmp_path):
        """Test that old attachments are removed when a note is updated."""
        note_dir = tmp_path / "test_note"
        note_id = "x-coredata://TEST-UUID-0000-0000-000000000000/ICNote/p100"

        # First export
        make_markdown_copy(note_id, note_dir, db_path=notestore_db)

        # Simulate old attachment that shouldn't exist anymore
        attachments_dir = note_dir / "Attachments"
        attachments_dir.mkdir(exist_ok=True)
        stale_attachment = attachments_dir / "old_file.jpg"
        stale_attachment.write_text("stale content")
        assert stale_attachment.exists()

        # Re-export (simulating note update)
        make_markdown_copy(note_id, note_dir, db_path=notestore_db)

        # Stale attachment should be gone
        assert not stale_attachment.exists()
        # Attachments directory should also be gone (test note has no attachments)
        assert not attachments_dir.exists()
