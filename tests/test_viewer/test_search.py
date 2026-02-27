from __future__ import annotations

import json
import pathlib

import pytest

from noteworthy.viewer.backup_reader import scan_backup, BackupData
from noteworthy.viewer.search import SearchIndex, strip_markdown


def _write_metadata(path: pathlib.Path, data: dict):
    path.mkdir(parents=True, exist_ok=True)
    with (path / ".noteworthy.json").open("w") as f:
        json.dump(data, f)


ACCOUNT_ID = "x-coredata://ABC/ICAccount/p1"
FOLDER_ID = "x-coredata://ABC/ICFolder/p2"


@pytest.fixture()
def backup_with_notes(tmp_path):
    """Create a small backup with 3 notes for search testing."""
    acct_dir = tmp_path / "iCloud"
    _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

    folder_dir = acct_dir / "Notes"
    _write_metadata(folder_dir, {
        "type": "folder", "name": "Notes", "id": FOLDER_ID,
        "parent_id": ACCOUNT_ID,
    })

    notes = [
        ("Apple Pie Recipe", "apple-id", "UUID-1", "# Apple Pie Recipe\n\nMix apples with cinnamon and sugar.\n"),
        ("Banana Bread", "banana-id", "UUID-2", "# Banana Bread\n\nMash ripe bananas and mix with flour.\n"),
        ("Cherry Cobbler", "cherry-id", "UUID-3", "# Cherry Cobbler\n\nFresh cherries baked with a biscuit topping.\n"),
    ]

    for name, note_id, uuid, content in notes:
        note_dir = folder_dir / name
        _write_metadata(note_dir, {
            "type": "note", "name": name, "id": note_id, "uuid": uuid,
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        note_dir.mkdir(parents=True, exist_ok=True)
        (note_dir / f"{name}.md").write_text(content, encoding="utf-8")

    return scan_backup(tmp_path)


class TestBuildIndex:
    def test_empty_backup(self):
        index = SearchIndex()
        index.build(BackupData())
        assert index.note_count == 0
        index.close()

    def test_index_contains_expected_count(self, backup_with_notes):
        index = SearchIndex()
        index.build(backup_with_notes)
        assert index.note_count == 3
        index.close()


class TestSearch:
    @pytest.fixture()
    def index(self, backup_with_notes):
        idx = SearchIndex()
        idx.build(backup_with_notes)
        yield idx
        idx.close()

    def test_single_word_search(self, index):
        results = index.search("apple")
        assert len(results) >= 1
        note_ids = [r["note_id"] for r in results]
        assert "apple-id" in note_ids

    def test_multi_word_search(self, index):
        results = index.search("banana flour")
        assert len(results) >= 1
        note_ids = [r["note_id"] for r in results]
        assert "banana-id" in note_ids

    def test_no_results(self, index):
        results = index.search("xyznonexistent")
        assert results == []

    def test_results_have_snippets(self, index):
        results = index.search("cherry")
        assert len(results) >= 1
        assert results[0]["snippet"]  # non-empty snippet

    def test_results_have_title(self, index):
        results = index.search("apple")
        assert len(results) >= 1
        assert results[0]["title"] == "Apple Pie Recipe"

    def test_empty_query_returns_empty(self, index):
        assert index.search("") == []
        assert index.search("   ") == []

    def test_special_characters_dont_crash(self, index):
        # These should not raise exceptions
        index.search("hello (world)")
        index.search('test "quotes"')
        index.search("a + b = c")
        index.search("@#$%")


class TestStripMarkdown:
    def test_removes_bold(self):
        assert "bold text" in strip_markdown("**bold text**")
        assert "**" not in strip_markdown("**bold text**")

    def test_removes_heading_markers(self):
        result = strip_markdown("# Title\n## Heading")
        assert "Title" in result
        assert "#" not in result

    def test_removes_list_markers(self):
        result = strip_markdown("* item\n- item2\n1. item3")
        assert "item" in result
        assert "*" not in result

    def test_removes_link_keeps_text(self):
        result = strip_markdown("[click here](https://example.com)")
        assert "click here" in result
        assert "https" not in result

    def test_removes_image_keeps_alt(self):
        result = strip_markdown("![photo](Attachments/photo.jpg)")
        assert "photo" in result
        assert "Attachments" not in result

    def test_preserves_plain_text(self):
        text = "Just some plain text content"
        assert strip_markdown(text) == text
