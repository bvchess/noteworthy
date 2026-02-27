from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest

from noteworthy.viewer.backup_reader import scan_backup, collect_tags, BackupData, _extract_first_image


def _write_metadata(path: pathlib.Path, data: dict):
    """Helper to write a .noteworthy.json file."""
    path.mkdir(parents=True, exist_ok=True)
    with (path / ".noteworthy.json").open("w") as f:
        json.dump(data, f)


def _make_note_dir(path: pathlib.Path, name: str, content: str = "# Title\n\nbody text\n"):
    """Helper to create a note directory with a .md file."""
    path.mkdir(parents=True, exist_ok=True)
    md_file = path / f"{name}.md"
    md_file.write_text(content, encoding="utf-8")
    return md_file


ACCOUNT_ID = "x-coredata://ABC/ICAccount/p1"
FOLDER_ID = "x-coredata://ABC/ICFolder/p2"
SUBFOLDER_ID = "x-coredata://ABC/ICFolder/p3"
NOTE_ID = "x-coredata://ABC/ICNote/p4"
NOTE_ID_2 = "x-coredata://ABC/ICNote/p5"
SMART_FOLDER_ID = "x-coredata://ABC/ICFolder/p6"


class TestScanEmptyDirectory:
    def test_no_accounts(self, tmp_path):
        result = scan_backup(tmp_path)
        assert isinstance(result, BackupData)
        assert result.accounts == []
        assert result.notes_by_id == {}
        assert result.folders_by_id == {}


class TestScanSingleAccount:
    @pytest.fixture()
    def backup_dir(self, tmp_path):
        """Create a minimal backup with one account, one folder, one note."""
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID, "is_smart_folder": False,
        })

        note_dir = folder_dir / "My Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "My Note", "id": NOTE_ID, "uuid": "UUID-1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-06-15T12:30:00+00:00",
            "folders": [FOLDER_ID],
        })
        _make_note_dir(note_dir, "My Note", "# My Note\n\nThis is the body.\n")
        return tmp_path

    def test_finds_account(self, backup_dir):
        result = scan_backup(backup_dir)
        assert len(result.accounts) == 1
        assert result.accounts[0].name == "iCloud"
        assert result.accounts[0].id == ACCOUNT_ID

    def test_finds_folder(self, backup_dir):
        result = scan_backup(backup_dir)
        assert FOLDER_ID in result.folders_by_id
        folder = result.folders_by_id[FOLDER_ID]
        assert folder.name == "Notes"
        assert folder.is_smart_folder is False

    def test_folder_linked_to_account(self, backup_dir):
        result = scan_backup(backup_dir)
        account = result.accounts[0]
        assert len(account.folders) == 1
        assert account.folders[0].id == FOLDER_ID

    def test_finds_note(self, backup_dir):
        result = scan_backup(backup_dir)
        assert NOTE_ID in result.notes_by_id
        note = result.notes_by_id[NOTE_ID]
        assert note.name == "My Note"
        assert note.uuid == "UUID-1"
        assert note.folder_id == FOLDER_ID

    def test_note_dates_parsed(self, backup_dir):
        result = scan_backup(backup_dir)
        note = result.notes_by_id[NOTE_ID]
        assert note.creation_date == datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert note.modification_date == datetime(2024, 6, 15, 12, 30, tzinfo=timezone.utc)

    def test_note_md_path(self, backup_dir):
        result = scan_backup(backup_dir)
        note = result.notes_by_id[NOTE_ID]
        assert note.md_path is not None
        assert note.md_path.name == "My Note.md"
        assert note.md_path.exists()

    def test_note_in_folder(self, backup_dir):
        result = scan_backup(backup_dir)
        folder = result.folders_by_id[FOLDER_ID]
        assert NOTE_ID in folder.note_ids

    def test_note_preview(self, backup_dir):
        result = scan_backup(backup_dir)
        note = result.notes_by_id[NOTE_ID]
        assert note.preview == "This is the body."


class TestNestedFolders:
    @pytest.fixture()
    def backup_dir(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        parent_dir = acct_dir / "Work"
        _write_metadata(parent_dir, {
            "type": "folder", "name": "Work", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        child_dir = parent_dir / "Projects"
        _write_metadata(child_dir, {
            "type": "folder", "name": "Projects", "id": SUBFOLDER_ID,
            "parent_id": FOLDER_ID,
        })

        note_dir = child_dir / "Todo"
        _write_metadata(note_dir, {
            "type": "note", "name": "Todo", "id": NOTE_ID, "uuid": "UUID-2",
            "creation_date": "2024-03-01T00:00:00+00:00",
            "modification_date": "2024-03-01T00:00:00+00:00",
            "folders": [SUBFOLDER_ID],
        })
        _make_note_dir(note_dir, "Todo")
        return tmp_path

    def test_subfolder_linked_to_parent(self, backup_dir):
        result = scan_backup(backup_dir)
        parent = result.folders_by_id[FOLDER_ID]
        assert len(parent.children) == 1
        assert parent.children[0].id == SUBFOLDER_ID

    def test_note_in_subfolder(self, backup_dir):
        result = scan_backup(backup_dir)
        child = result.folders_by_id[SUBFOLDER_ID]
        assert NOTE_ID in child.note_ids


class TestSmartFolder:
    @pytest.fixture()
    def backup_dir(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        # Regular folder with a note
        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID, "is_smart_folder": False,
        })

        note_dir = folder_dir / "My Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "My Note", "id": NOTE_ID, "uuid": "UUID-1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        _make_note_dir(note_dir, "My Note")

        # Smart folder with is_smart_folder flag
        smart_dir = acct_dir / "#tagged"
        _write_metadata(smart_dir, {
            "type": "folder", "name": "#tagged", "id": SMART_FOLDER_ID,
            "parent_id": ACCOUNT_ID, "is_smart_folder": True,
        })

        # Symlink in smart folder pointing to the note
        (smart_dir / "My Note").symlink_to(note_dir)

        return tmp_path

    def test_smart_folder_detected_via_metadata(self, backup_dir):
        result = scan_backup(backup_dir)
        folder = result.folders_by_id[SMART_FOLDER_ID]
        assert folder.is_smart_folder is True

    def test_smart_folder_resolves_symlinks(self, backup_dir):
        result = scan_backup(backup_dir)
        folder = result.folders_by_id[SMART_FOLDER_ID]
        assert NOTE_ID in folder.note_ids


class TestSmartFolderDetectionBySymlinks:
    """Test that smart folders are detected even without is_smart_folder metadata field."""

    @pytest.fixture()
    def backup_dir(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        note_dir = folder_dir / "My Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "My Note", "id": NOTE_ID, "uuid": "UUID-1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        _make_note_dir(note_dir, "My Note")

        # Folder without is_smart_folder flag but with symlinks
        smart_dir = acct_dir / "#now"
        _write_metadata(smart_dir, {
            "type": "folder", "name": "#now", "id": SMART_FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })
        (smart_dir / "My Note").symlink_to(note_dir)

        return tmp_path

    def test_detected_as_smart_folder(self, backup_dir):
        result = scan_backup(backup_dir)
        folder = result.folders_by_id[SMART_FOLDER_ID]
        assert folder.is_smart_folder is True


class TestMissingMdFile:
    def test_note_without_md_file(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        # Note directory without .md file
        note_dir = folder_dir / "Empty Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Empty Note", "id": NOTE_ID, "uuid": "UUID-1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })

        result = scan_backup(tmp_path)
        note = result.notes_by_id[NOTE_ID]
        assert note.md_path is None
        assert note.preview == ""


class TestMultipleAccounts:
    @pytest.fixture()
    def backup_dir(self, tmp_path):
        account2_id = "x-coredata://DEF/ICAccount/p10"
        folder2_id = "x-coredata://DEF/ICFolder/p11"

        acct1_dir = tmp_path / "iCloud"
        _write_metadata(acct1_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})
        folder1_dir = acct1_dir / "Notes"
        _write_metadata(folder1_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID, "parent_id": ACCOUNT_ID,
        })

        acct2_dir = tmp_path / "On My Mac"
        _write_metadata(acct2_dir, {"type": "account", "name": "On My Mac", "id": account2_id})
        folder2_dir = acct2_dir / "Local"
        _write_metadata(folder2_dir, {
            "type": "folder", "name": "Local", "id": folder2_id, "parent_id": account2_id,
        })

        return tmp_path

    def test_both_accounts_found(self, backup_dir):
        result = scan_backup(backup_dir)
        assert len(result.accounts) == 2
        names = {a.name for a in result.accounts}
        assert names == {"iCloud", "On My Mac"}

    def test_folders_in_correct_accounts(self, backup_dir):
        result = scan_backup(backup_dir)
        for account in result.accounts:
            assert len(account.folders) == 1
            if account.name == "iCloud":
                assert account.folders[0].name == "Notes"
            else:
                assert account.folders[0].name == "Local"


class TestWithRealBackup:
    """Integration test with the actual test_backup directory if available."""

    @pytest.fixture()
    def test_backup_path(self):
        path = pathlib.Path(__file__).parent.parent.parent / "test_backup"
        if not path.exists():
            pytest.skip("test_backup directory not available")
        return path

    def test_scans_without_error(self, test_backup_path):
        result = scan_backup(test_backup_path)
        assert len(result.accounts) >= 1
        assert len(result.notes_by_id) > 0
        assert len(result.folders_by_id) > 0

    def test_smart_folders_detected(self, test_backup_path):
        result = scan_backup(test_backup_path)
        smart = [f for f in result.folders_by_id.values() if f.is_smart_folder]
        assert len(smart) > 0, "Expected at least one smart folder in test_backup"


class TestNoteTagsFromMetadata:
    @pytest.fixture()
    def backup_with_tags(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        note_dir = folder_dir / "Tagged Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Tagged Note", "id": NOTE_ID, "uuid": "UUID-T1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
            "tags": ["python-dev", "world"],
        })
        _make_note_dir(note_dir, "Tagged Note", "# Tagged Note\n\nHello #world and #python-dev\n")

        note_dir2 = folder_dir / "Another Note"
        _write_metadata(note_dir2, {
            "type": "note", "name": "Another Note", "id": NOTE_ID_2, "uuid": "UUID-T2",
            "creation_date": "2024-01-02T00:00:00+00:00",
            "modification_date": "2024-01-02T00:00:00+00:00",
            "folders": [FOLDER_ID],
            "tags": ["travel", "world"],
        })
        _make_note_dir(note_dir2, "Another Note", "# Another Note\n\n#world #travel\n")

        return scan_backup(tmp_path)

    def test_note_tags_populated_from_metadata(self, backup_with_tags):
        note = backup_with_tags.notes_by_id[NOTE_ID]
        assert note.tags == ["python-dev", "world"]

    def test_note_without_tags_field_has_empty_list(self, tmp_path):
        """Notes from old backups without 'tags' in metadata degrade gracefully."""
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})
        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID, "parent_id": ACCOUNT_ID,
        })
        note_dir = folder_dir / "Old Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Old Note", "id": NOTE_ID, "uuid": "UUID-O1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        _make_note_dir(note_dir, "Old Note")
        result = scan_backup(tmp_path)
        assert result.notes_by_id[NOTE_ID].tags == []


class TestCollectTags:
    @pytest.fixture()
    def backup_with_tags(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        note_dir = folder_dir / "Tagged Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Tagged Note", "id": NOTE_ID, "uuid": "UUID-T1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
            "tags": ["python-dev", "world"],
        })
        _make_note_dir(note_dir, "Tagged Note", "# Tagged Note\n\nHello #world and #python-dev\n")

        note_dir2 = folder_dir / "Another Note"
        _write_metadata(note_dir2, {
            "type": "note", "name": "Another Note", "id": NOTE_ID_2, "uuid": "UUID-T2",
            "creation_date": "2024-01-02T00:00:00+00:00",
            "modification_date": "2024-01-02T00:00:00+00:00",
            "folders": [FOLDER_ID],
            "tags": ["travel", "world"],
        })
        _make_note_dir(note_dir2, "Another Note", "# Another Note\n\n#world #travel\n")

        return scan_backup(tmp_path)

    def test_finds_tags(self, backup_with_tags):
        tags = collect_tags(backup_with_tags)
        assert "world" in tags
        assert "python-dev" in tags
        assert "travel" in tags

    def test_tags_are_sorted(self, backup_with_tags):
        tags = collect_tags(backup_with_tags)
        assert tags == sorted(tags)

    def test_tags_are_unique(self, backup_with_tags):
        tags = collect_tags(backup_with_tags)
        assert len(tags) == len(set(tags))
        # "world" appears in both notes but should only be listed once
        assert tags.count("world") == 1

    def test_no_tags_in_empty_backup(self, tmp_path):
        backup = scan_backup(tmp_path)
        tags = collect_tags(backup)
        assert tags == []

    def test_plain_hash_text_in_markdown_not_a_tag(self, tmp_path):
        """Notes with #word in markdown but no 'tags' in metadata don't produce tags."""
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})
        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })
        note_dir = folder_dir / "Plain Text"
        _write_metadata(note_dir, {
            "type": "note", "name": "Plain Text", "id": NOTE_ID, "uuid": "UUID-P1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
            # No "tags" field — old backup / no Apple Notes tags
        })
        _make_note_dir(note_dir, "Plain Text", "# Plain Text\n\nSome #notreallya #tag here.\n")

        backup = scan_backup(tmp_path)
        tags = collect_tags(backup)
        assert tags == []


class TestNotesByMdPath:
    """Test that BackupData.notes_by_md_path maps resolved md paths to note IDs."""

    def test_notes_by_md_path_populated(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        note_dir = folder_dir / "My Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "My Note", "id": NOTE_ID, "uuid": "UUID-1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        md_file = _make_note_dir(note_dir, "My Note")

        result = scan_backup(tmp_path)
        resolved = str(md_file.resolve())
        assert resolved in result.notes_by_md_path
        assert result.notes_by_md_path[resolved] == NOTE_ID

    def test_notes_by_md_path_excludes_notes_without_md(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        # Note with no .md file
        note_dir = folder_dir / "Empty Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Empty Note", "id": NOTE_ID, "uuid": "UUID-E1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })

        result = scan_backup(tmp_path)
        assert result.notes_by_md_path == {}


class TestNoteWithSmartFolderFirst:
    """Test that a note whose folders list has a smart folder first still lands in its regular folder."""

    REGULAR_FOLDER_ID = "x-coredata://ABC/ICFolder/p100"
    SMART_FOLDER_ID_2 = "x-coredata://ABC/ICFolder/p7"

    @pytest.fixture()
    def backup_dir(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        # Regular folder
        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": self.REGULAR_FOLDER_ID,
            "parent_id": ACCOUNT_ID, "is_smart_folder": False,
        })

        # Smart folder (sorts before regular folder alphabetically by ID)
        smart_dir = acct_dir / "#tagged"
        _write_metadata(smart_dir, {
            "type": "folder", "name": "#tagged", "id": self.SMART_FOLDER_ID_2,
            "parent_id": ACCOUNT_ID, "is_smart_folder": True,
        })

        # Note with smart folder FIRST in the folders list
        note_dir = folder_dir / "My Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "My Note", "id": NOTE_ID, "uuid": "UUID-SF",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-06-15T12:30:00+00:00",
            "folders": [self.SMART_FOLDER_ID_2, self.REGULAR_FOLDER_ID],
        })
        _make_note_dir(note_dir, "My Note")

        # Symlink in smart folder pointing to the note
        (smart_dir / "My Note").symlink_to(note_dir)

        return tmp_path

    def test_note_folder_id_is_regular_folder(self, backup_dir):
        result = scan_backup(backup_dir)
        note = result.notes_by_id[NOTE_ID]
        assert note.folder_id == self.REGULAR_FOLDER_ID

    def test_note_in_regular_folder_note_ids(self, backup_dir):
        result = scan_backup(backup_dir)
        folder = result.folders_by_id[self.REGULAR_FOLDER_ID]
        assert NOTE_ID in folder.note_ids

    def test_note_in_smart_folder_via_symlinks(self, backup_dir):
        result = scan_backup(backup_dir)
        smart = result.folders_by_id[self.SMART_FOLDER_ID_2]
        assert NOTE_ID in smart.note_ids


class TestExtractFirstImage:
    """Unit tests for _extract_first_image()."""

    def test_returns_none_for_no_images(self, tmp_path):
        md = tmp_path / "note.md"
        md.write_text("# Title\n\nJust text, no attachments.\n", encoding="utf-8")
        assert _extract_first_image(md) is None

    def test_finds_image_syntax(self, tmp_path):
        md = tmp_path / "note.md"
        md.write_text("# Title\n\n![photo.jpg](Attachments/photo.jpg)\n", encoding="utf-8")
        assert _extract_first_image(md) == "photo.jpg"

    def test_finds_link_syntax_old_export(self, tmp_path):
        """Old exports used []() for images; should still be detected by extension."""
        md = tmp_path / "note.md"
        md.write_text("# Title\n\n[photo.jpg](Attachments/photo.jpg)\n", encoding="utf-8")
        assert _extract_first_image(md) == "photo.jpg"

    def test_skips_non_image_attachments(self, tmp_path):
        md = tmp_path / "note.md"
        md.write_text("# Title\n\n[doc.pdf](Attachments/doc.pdf)\n", encoding="utf-8")
        assert _extract_first_image(md) is None

    def test_returns_first_image(self, tmp_path):
        md = tmp_path / "note.md"
        md.write_text(
            "# Title\n\n[doc.pdf](Attachments/doc.pdf)\n\n![first.png](Attachments/first.png)\n"
            "![second.jpg](Attachments/second.jpg)\n",
            encoding="utf-8",
        )
        assert _extract_first_image(md) == "first.png"

    def test_various_image_extensions(self, tmp_path):
        for ext in (".jpg", ".jpeg", ".png", ".gif", ".tiff", ".tif", ".heic", ".heif", ".webp", ".avif", ".bmp", ".svg"):
            md = tmp_path / f"note{ext}.md"
            md.write_text(f"# T\n\n![img{ext}](Attachments/img{ext})\n", encoding="utf-8")
            assert _extract_first_image(md) == f"img{ext}"

    def test_returns_none_for_missing_file(self, tmp_path):
        assert _extract_first_image(tmp_path / "nonexistent.md") is None

    def test_note_first_image_populated_in_scan(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {"type": "folder", "name": "Notes", "id": FOLDER_ID, "parent_id": ACCOUNT_ID})

        note_dir = folder_dir / "Photo Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Photo Note", "id": NOTE_ID, "uuid": "UUID-I1",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        _make_note_dir(note_dir, "Photo Note", "# Photo Note\n\n![img.png](Attachments/img.png)\n")

        result = scan_backup(tmp_path)
        assert result.notes_by_id[NOTE_ID].first_image == "img.png"

    def test_note_first_image_none_when_no_images(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {"type": "folder", "name": "Notes", "id": FOLDER_ID, "parent_id": ACCOUNT_ID})

        note_dir = folder_dir / "Text Note"
        _write_metadata(note_dir, {
            "type": "note", "name": "Text Note", "id": NOTE_ID, "uuid": "UUID-I2",
            "creation_date": "2024-01-01T00:00:00+00:00",
            "modification_date": "2024-01-01T00:00:00+00:00",
            "folders": [FOLDER_ID],
        })
        _make_note_dir(note_dir, "Text Note", "# Text Note\n\nJust words.\n")

        result = scan_backup(tmp_path)
        assert result.notes_by_id[NOTE_ID].first_image is None


class TestFolderSortOrder:
    def test_sort_order_read_from_metadata(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID, "sort_order": "date_created",
        })

        result = scan_backup(tmp_path)
        assert result.folders_by_id[FOLDER_ID].sort_order == "date_created"

    def test_default_sort_order_when_missing(self, tmp_path):
        """Old backups without sort_order default to 'default'."""
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID,
        })

        result = scan_backup(tmp_path)
        assert result.folders_by_id[FOLDER_ID].sort_order == "default"

    def test_title_sort_order(self, tmp_path):
        acct_dir = tmp_path / "iCloud"
        _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

        folder_dir = acct_dir / "Notes"
        _write_metadata(folder_dir, {
            "type": "folder", "name": "Notes", "id": FOLDER_ID,
            "parent_id": ACCOUNT_ID, "sort_order": "title",
        })

        result = scan_backup(tmp_path)
        assert result.folders_by_id[FOLDER_ID].sort_order == "title"
