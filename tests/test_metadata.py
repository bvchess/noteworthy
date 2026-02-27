#!/usr/bin/env python
"""Tests for distributed metadata handling (.noteworthy.json files)."""

import json
import pathlib
import tempfile
import unittest
from datetime import datetime

from noteworthy.notes_datatypes import Account, Folder, Note, write_metadata_file, read_distributed_metadata
from noteworthy import noteworthy


class TestDistributedMetadata(unittest.TestCase):
    """Test distributed .noteworthy.json metadata files."""

    def setUp(self):
        """Create a temporary directory for each test."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_path = pathlib.Path(self.temp_dir.name)

    def tearDown(self):
        """Clean up the temporary directory."""
        self.temp_dir.cleanup()

    def _make_account(self, name="iCloud", obj_id="account-1"):
        return Account(name, obj_id, None)

    def _make_folder(self, name, obj_id, parent=None):
        folder = Folder(name, obj_id, None)
        if parent:
            parent.add_folder(folder)
        return folder

    def _make_note(self, name, obj_id, folder, uuid=None):
        note = Note(name, obj_id, None, datetime(2024, 1, 15, 10, 30), datetime(2024, 1, 20, 14, 45), uuid=uuid)
        folder.add_note(note)
        return note

    def test_account_to_metadata_dict(self):
        """Test Account.to_metadata_dict() returns correct format."""
        account = self._make_account("iCloud", "x-coredata://123/Account/abc")
        metadata = account.to_metadata_dict()

        self.assertEqual(metadata["type"], "account")
        self.assertEqual(metadata["name"], "iCloud")
        self.assertEqual(metadata["id"], "x-coredata://123/Account/abc")
        self.assertEqual(metadata["tags_expanded"], True)
        self.assertEqual(len(metadata), 4)

    def test_folder_to_metadata_dict(self):
        """Test Folder.to_metadata_dict() returns correct format."""
        account = self._make_account()
        folder = self._make_folder("home", "x-coredata://123/Folder/def", account)
        metadata = folder.to_metadata_dict()

        self.assertEqual(metadata["type"], "folder")
        self.assertEqual(metadata["name"], "home")
        self.assertEqual(metadata["id"], "x-coredata://123/Folder/def")
        self.assertEqual(metadata["parent_id"], account.id)
        self.assertEqual(metadata["is_smart_folder"], False)
        self.assertEqual(metadata["sort_order"], "default")
        self.assertEqual(metadata["display_order"], 0)
        self.assertEqual(metadata["is_expanded"], True)
        self.assertEqual(len(metadata), 8)

    def test_note_to_metadata_dict(self):
        """Test Note.to_metadata_dict() returns correct format."""
        account = self._make_account()
        folder = self._make_folder("home", "folder-1", account)
        note = self._make_note("My Note", "x-coredata://123/Note/ghi", folder, uuid="NOTE-UUID-123")
        metadata = note.to_metadata_dict()

        self.assertEqual(metadata["type"], "note")
        self.assertEqual(metadata["name"], "My Note")
        self.assertEqual(metadata["id"], "x-coredata://123/Note/ghi")
        self.assertEqual(metadata["uuid"], "NOTE-UUID-123")
        self.assertEqual(metadata["creation_date"], "2024-01-15T10:30:00")
        self.assertEqual(metadata["modification_date"], "2024-01-20T14:45:00")
        self.assertEqual(metadata["folders"], ["folder-1"])
        self.assertNotIn("path", metadata)

    def test_write_metadata_file_account(self):
        """Test write_metadata_file() creates correct JSON for account."""
        account = self._make_account("iCloud", "account-1")
        account_dir = self.target_path / "iCloud"
        account_dir.mkdir()

        write_metadata_file(account, account_dir)

        metadata_path = account_dir / ".noteworthy.json"
        self.assertTrue(metadata_path.exists())
        with metadata_path.open() as f:
            data = json.load(f)
        self.assertEqual(data["type"], "account")
        self.assertEqual(data["name"], "iCloud")

    def test_write_metadata_file_folder(self):
        """Test write_metadata_file() creates correct JSON for folder."""
        account = self._make_account()
        folder = self._make_folder("home", "folder-1", account)
        folder_dir = self.target_path / "iCloud" / "home"
        folder_dir.mkdir(parents=True)

        write_metadata_file(folder, folder_dir)

        metadata_path = folder_dir / ".noteworthy.json"
        self.assertTrue(metadata_path.exists())
        with metadata_path.open() as f:
            data = json.load(f)
        self.assertEqual(data["type"], "folder")
        self.assertEqual(data["name"], "home")
        self.assertEqual(data["parent_id"], account.id)

    def test_write_metadata_file_note(self):
        """Test write_metadata_file() creates correct JSON for note."""
        account = self._make_account()
        folder = self._make_folder("home", "folder-1", account)
        note = self._make_note("My Note", "note-1", folder, uuid="UUID-123")
        note_dir = self.target_path / "iCloud" / "home" / "My Note"
        note_dir.mkdir(parents=True)

        write_metadata_file(note, note_dir)

        metadata_path = note_dir / ".noteworthy.json"
        self.assertTrue(metadata_path.exists())
        with metadata_path.open() as f:
            data = json.load(f)
        self.assertEqual(data["type"], "note")
        self.assertEqual(data["name"], "My Note")
        self.assertEqual(data["uuid"], "UUID-123")

    def test_read_distributed_metadata_single_account(self):
        """Test read_distributed_metadata() reconstructs a single account correctly."""
        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)

        with (account_dir / ".noteworthy.json").open("w") as f:
            json.dump({"type": "account", "name": "iCloud", "id": "account-1"}, f)
        with (folder_dir / ".noteworthy.json").open("w") as f:
            json.dump({"type": "folder", "name": "home", "id": "folder-1", "parent_id": "account-1"}, f)
        with (note_dir / ".noteworthy.json").open("w") as f:
            json.dump({
                "type": "note", "name": "My Note", "id": "note-1", "uuid": "UUID-123",
                "creation_date": "2024-01-15T10:30:00", "modification_date": "2024-01-20T14:45:00",
                "folders": ["folder-1"]
            }, f)

        accounts = read_distributed_metadata(self.target_path)

        self.assertEqual(len(accounts), 1)
        account = accounts[0]
        self.assertEqual(account.name, "iCloud")
        self.assertEqual(account.id, "account-1")
        self.assertEqual(account.path, account_dir)

        self.assertEqual(len(account.folders), 1)
        folder = account.folders[0]
        self.assertEqual(folder.name, "home")
        self.assertEqual(folder.id, "folder-1")
        self.assertEqual(folder.parent, account)
        self.assertEqual(folder.path, folder_dir)

        self.assertEqual(len(folder.notes), 1)
        note = folder.notes[0]
        self.assertEqual(note.name, "My Note")
        self.assertEqual(note.uuid, "UUID-123")
        self.assertEqual(note.path, note_dir)
        self.assertIn(folder, note.folders)

    def test_read_distributed_metadata_nested_folders(self):
        """Test read_distributed_metadata() handles nested folder hierarchies."""
        account_dir = self.target_path / "iCloud"
        parent_dir = account_dir / "home"
        child_dir = parent_dir / "journal"
        child_dir.mkdir(parents=True)

        with (account_dir / ".noteworthy.json").open("w") as f:
            json.dump({"type": "account", "name": "iCloud", "id": "account-1"}, f)
        with (parent_dir / ".noteworthy.json").open("w") as f:
            json.dump({"type": "folder", "name": "home", "id": "folder-1", "parent_id": "account-1"}, f)
        with (child_dir / ".noteworthy.json").open("w") as f:
            json.dump({"type": "folder", "name": "journal", "id": "folder-2", "parent_id": "folder-1"}, f)

        accounts = read_distributed_metadata(self.target_path)

        self.assertEqual(len(accounts), 1)
        account = accounts[0]
        self.assertEqual(len(account.folders), 1)
        parent_folder = account.folders[0]
        self.assertEqual(parent_folder.name, "home")
        self.assertEqual(parent_folder.parent, account)

        self.assertEqual(len(parent_folder.folders), 1)
        child_folder = parent_folder.folders[0]
        self.assertEqual(child_folder.name, "journal")
        self.assertEqual(child_folder.parent, parent_folder)

    def test_read_distributed_metadata_empty_directory(self):
        """Test read_distributed_metadata() returns empty list for directory without metadata."""
        accounts = read_distributed_metadata(self.target_path)
        self.assertEqual(accounts, [])

    def test_sync_creates_distributed_metadata(self):
        """Test that account and folder sync creates distributed metadata files."""
        noteworthy._target_path = self.target_path
        noteworthy._verbose = False

        try:
            apple_account = self._make_account("iCloud", "account-1")
            parent_folder = self._make_folder("home", "folder-1", apple_account)

            path_with_target = self.target_path / apple_account.choose_path()
            path_with_target.mkdir(parents=True, exist_ok=True)
            apple_account.set_path(path_with_target)
            write_metadata_file(apple_account, path_with_target)

            noteworthy._sync_folder(parent_folder, None)

            account_metadata = self.target_path / "iCloud" / ".noteworthy.json"
            self.assertTrue(account_metadata.exists())
            with account_metadata.open() as f:
                data = json.load(f)
            self.assertEqual(data["type"], "account")

            folder_metadata = self.target_path / "iCloud" / "home" / ".noteworthy.json"
            self.assertTrue(folder_metadata.exists())
            with folder_metadata.open() as f:
                data = json.load(f)
            self.assertEqual(data["type"], "folder")
            self.assertEqual(data["parent_id"], "account-1")
        finally:
            noteworthy._target_path = None

    def test_write_and_read_note_metadata(self):
        """Test that note metadata can be written and read back correctly."""
        account = self._make_account("iCloud", "account-1")
        folder = self._make_folder("home", "folder-1", account)
        note = self._make_note("Test Note", "note-1", folder, uuid="UUID-TEST")

        note_dir = self.target_path / "iCloud" / "home" / "Test Note"
        note_dir.mkdir(parents=True)
        note.set_path(note_dir)

        write_metadata_file(note, note_dir)

        metadata_path = note_dir / ".noteworthy.json"
        self.assertTrue(metadata_path.exists())
        with metadata_path.open() as f:
            data = json.load(f)
        self.assertEqual(data["type"], "note")
        self.assertEqual(data["name"], "Test Note")
        self.assertEqual(data["uuid"], "UUID-TEST")
        self.assertEqual(data["folders"], ["folder-1"])

    def test_note_tags_default_empty(self):
        """Note created without tags has an empty tags list."""
        note = Note("My Note", "note-1", None, datetime(2024, 1, 15, 10, 30), datetime(2024, 1, 20, 14, 45))
        self.assertEqual(note.tags, [])

    def test_note_set_tags(self):
        """set_tags() updates the tags list."""
        note = Note("My Note", "note-1", None, datetime(2024, 1, 15, 10, 30), datetime(2024, 1, 20, 14, 45))
        note.set_tags(["todo", "bookmark"])
        self.assertEqual(note.tags, ["todo", "bookmark"])

    def test_note_to_metadata_dict_includes_tags(self):
        """to_metadata_dict() includes sorted tags."""
        account = self._make_account()
        folder = self._make_folder("home", "folder-1", account)
        note = self._make_note("My Note", "note-1", folder)
        note.set_tags(["todo", "bookmark"])
        metadata = note.to_metadata_dict()
        self.assertEqual(metadata["tags"], ["bookmark", "todo"])

    def test_note_from_metadata_dict_restores_tags(self):
        """from_metadata_dict() restores tags from the 'tags' field."""
        data = {
            "type": "note",
            "name": "My Note",
            "id": "note-1",
            "uuid": "UUID-1",
            "creation_date": "2024-01-15T10:30:00",
            "modification_date": "2024-01-20T14:45:00",
            "folders": [],
            "tags": ["bookmark", "todo"],
        }
        note = Note.from_metadata_dict(data)
        self.assertEqual(note.tags, ["bookmark", "todo"])

    def test_note_from_metadata_dict_no_tags_field(self):
        """from_metadata_dict() uses empty list when 'tags' field is absent (old backup)."""
        data = {
            "type": "note",
            "name": "Old Note",
            "id": "note-2",
            "uuid": "UUID-2",
            "creation_date": "2024-01-15T10:30:00",
            "modification_date": "2024-01-20T14:45:00",
            "folders": [],
        }
        note = Note.from_metadata_dict(data)
        self.assertEqual(note.tags, [])

    def test_note_tags_roundtrip(self):
        """Tags survive a to_metadata_dict / from_metadata_dict round-trip."""
        account = self._make_account()
        folder = self._make_folder("home", "folder-1", account)
        note = self._make_note("My Note", "note-1", folder)
        note.set_tags(["todo", "bookmark"])
        data = note.to_metadata_dict()
        restored = Note.from_metadata_dict(data)
        self.assertEqual(restored.tags, ["bookmark", "todo"])

    def test_folder_sort_order_default(self):
        """Folder with no explicit sort_order has 'default' in metadata."""
        account = self._make_account()
        folder = self._make_folder("home", "folder-1", account)
        metadata = folder.to_metadata_dict()
        self.assertEqual(metadata["sort_order"], "default")

    def test_folder_sort_order_date_created(self):
        """Folder with sort_order='date_created' includes it in metadata."""
        account = self._make_account()
        folder = Folder("journal", "folder-1", None, sort_order="date_created")
        account.add_folder(folder)
        metadata = folder.to_metadata_dict()
        self.assertEqual(metadata["sort_order"], "date_created")

    def test_folder_from_metadata_dict_with_sort_order(self):
        """from_metadata_dict() reads sort_order."""
        data = {"name": "home", "id": "folder-1", "sort_order": "title"}
        folder = Folder.from_metadata_dict(data)
        self.assertEqual(folder.sort_order, "title")

    def test_folder_from_metadata_dict_missing_sort_order(self):
        """Old metadata without sort_order defaults to 'default'."""
        data = {"name": "home", "id": "folder-1"}
        folder = Folder.from_metadata_dict(data)
        self.assertEqual(folder.sort_order, "default")

    def test_folder_sort_order_roundtrip(self):
        """sort_order survives a to_metadata_dict / from_metadata_dict round-trip."""
        account = self._make_account()
        folder = Folder("Projects", "f1", None, sort_order="title")
        account.add_folder(folder)
        data = folder.to_metadata_dict()
        restored = Folder.from_metadata_dict(data)
        self.assertEqual(restored.sort_order, "title")

    def test_folder_display_order_in_metadata(self):
        """Folder with display_order includes it in metadata."""
        account = self._make_account()
        folder = Folder("Projects", "f1", None, display_order=3)
        account.add_folder(folder)
        metadata = folder.to_metadata_dict()
        self.assertEqual(metadata["display_order"], 3)

    def test_folder_is_expanded_in_metadata(self):
        """Folder with is_expanded=False includes it in metadata."""
        account = self._make_account()
        folder = Folder("Projects", "f1", None, is_expanded=False)
        account.add_folder(folder)
        metadata = folder.to_metadata_dict()
        self.assertEqual(metadata["is_expanded"], False)

    def test_folder_from_metadata_dict_with_display_order(self):
        """from_metadata_dict() reads display_order."""
        data = {"name": "home", "id": "folder-1", "display_order": 5}
        folder = Folder.from_metadata_dict(data)
        self.assertEqual(folder.display_order, 5)

    def test_folder_from_metadata_dict_missing_display_order(self):
        """Old metadata without display_order defaults to 0."""
        data = {"name": "home", "id": "folder-1"}
        folder = Folder.from_metadata_dict(data)
        self.assertEqual(folder.display_order, 0)

    def test_folder_from_metadata_dict_with_is_expanded(self):
        """from_metadata_dict() reads is_expanded."""
        data = {"name": "home", "id": "folder-1", "is_expanded": False}
        folder = Folder.from_metadata_dict(data)
        self.assertEqual(folder.is_expanded, False)

    def test_folder_from_metadata_dict_missing_is_expanded(self):
        """Old metadata without is_expanded defaults to True."""
        data = {"name": "home", "id": "folder-1"}
        folder = Folder.from_metadata_dict(data)
        self.assertEqual(folder.is_expanded, True)

    def test_folder_display_order_roundtrip(self):
        """display_order survives a to_metadata_dict / from_metadata_dict round-trip."""
        account = self._make_account()
        folder = Folder("Projects", "f1", None, display_order=7)
        account.add_folder(folder)
        data = folder.to_metadata_dict()
        restored = Folder.from_metadata_dict(data)
        self.assertEqual(restored.display_order, 7)

    def test_folder_is_expanded_roundtrip(self):
        """is_expanded survives a to_metadata_dict / from_metadata_dict round-trip."""
        account = self._make_account()
        folder = Folder("Projects", "f1", None, is_expanded=False)
        account.add_folder(folder)
        data = folder.to_metadata_dict()
        restored = Folder.from_metadata_dict(data)
        self.assertEqual(restored.is_expanded, False)

    def test_note_metadata_regular_folders_before_smart_folders(self):
        """Test that to_metadata_dict() puts regular folders before smart folders."""
        account = self._make_account()
        regular_folder = self._make_folder("home", "folder-z", account)
        smart_folder = Folder("tagged", "folder-a", None, is_smart_folder=True)
        account.add_folder(smart_folder)

        note = Note("Test Note", "note-1", None, datetime(2024, 1, 15, 10, 30), datetime(2024, 1, 20, 14, 45))
        regular_folder.add_note(note)
        smart_folder.add_note(note)

        metadata = note.to_metadata_dict()
        folders = metadata["folders"]
        # "folder-a" (smart) sorts before "folder-z" (regular) alphabetically,
        # but regular folders should come first in the metadata
        self.assertEqual(folders, ["folder-z", "folder-a"])


if __name__ == '__main__':
    unittest.main()
