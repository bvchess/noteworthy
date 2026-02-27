#!/usr/bin/env python
"""Tests for smart folder sync functionality."""

import json
import os

from noteworthy.notes_datatypes import write_metadata_file
from noteworthy import noteworthy
from sync_test_base import SyncTestCase


class TestSmartFolderSync(SyncTestCase):
    """Test smart folder sync functionality (symlink creation)."""

    def test_make_unique_symlink_name_no_collision(self):
        """Test _make_unique_symlink_name returns base name when no collision."""
        existing = {"other_note", "another_note"}
        result = noteworthy._make_unique_symlink_name("my_note", existing)
        self.assertEqual(result, "my_note")

    def test_make_unique_symlink_name_with_collision(self):
        """Test _make_unique_symlink_name adds suffix on collision."""
        existing = {"my_note", "other_note"}
        result = noteworthy._make_unique_symlink_name("my_note", existing)
        self.assertEqual(result, "my_note_2")

    def test_make_unique_symlink_name_multiple_collisions(self):
        """Test _make_unique_symlink_name handles multiple collisions."""
        existing = {"my_note", "my_note_2", "my_note_3"}
        result = noteworthy._make_unique_symlink_name("my_note", existing)
        self.assertEqual(result, "my_note_4")

    def test_sync_smart_folder_creates_directory(self):
        """Test that syncing a smart folder creates the directory."""
        account = self.make_account()
        smart_folder = self.make_folder("#bookmark", "sf-1", account)

        noteworthy._sync_smart_folder(smart_folder)

        smart_folder_path = self.target_path / "iCloud" / "#bookmark"
        self.assertTrue(smart_folder_path.exists())
        self.assertTrue(smart_folder_path.is_dir())

    def test_sync_smart_folder_creates_metadata(self):
        """Test that syncing a smart folder creates a metadata file."""
        account = self.make_account()
        smart_folder = self.make_folder("#bookmark", "sf-1", account)

        noteworthy._sync_smart_folder(smart_folder)

        metadata_path = self.target_path / "iCloud" / "#bookmark" / ".noteworthy.json"
        self.assertTrue(metadata_path.exists())
        with metadata_path.open() as f:
            data = json.load(f)
        self.assertEqual(data["type"], "folder")
        self.assertEqual(data["name"], "#bookmark")

    def test_sync_smart_folder_creates_symlinks(self):
        """Test that syncing a smart folder creates symlinks to notes."""
        account = self.make_account()
        home_folder = self.make_folder("home", "folder-1", account)
        note = self.make_note("My Note", "note-1", home_folder, uuid="UUID-1")

        smart_folder = self.make_folder("#bookmark", "sf-1", account)
        smart_folder.add_note(note)

        note_dir = self.target_path / "iCloud" / "home" / "My Note"
        note_dir.mkdir(parents=True)
        note.set_path(note_dir)

        noteworthy._sync_smart_folder(smart_folder)

        symlink_path = self.target_path / "iCloud" / "#bookmark" / "My Note"
        self.assertTrue(symlink_path.is_symlink())
        self.assertEqual(os.readlink(symlink_path), "../home/My Note")
        self.assertTrue(symlink_path.resolve().exists())
        self.assertEqual(symlink_path.resolve(), note_dir.resolve())

    def test_sync_smart_folder_handles_name_collisions(self):
        """Test that smart folder sync handles notes with duplicate names."""
        account = self.make_account()
        folder1 = self.make_folder("work", "folder-1", account)
        note1 = self.make_note("Meeting Notes", "note-1", folder1, uuid="UUID-1")
        folder2 = self.make_folder("personal", "folder-2", account)
        note2 = self.make_note("Meeting Notes", "note-2", folder2, uuid="UUID-2")

        smart_folder = self.make_folder("#meetings", "sf-1", account)
        smart_folder.add_note(note1)
        smart_folder.add_note(note2)

        note1_dir = self.target_path / "iCloud" / "work" / "Meeting Notes"
        note1_dir.mkdir(parents=True)
        note1.set_path(note1_dir)

        note2_dir = self.target_path / "iCloud" / "personal" / "Meeting Notes"
        note2_dir.mkdir(parents=True)
        note2.set_path(note2_dir)

        noteworthy._sync_smart_folder(smart_folder)

        smart_folder_path = self.target_path / "iCloud" / "#meetings"
        symlink1 = smart_folder_path / "Meeting Notes"
        symlink2 = smart_folder_path / "Meeting Notes_2"

        self.assertTrue(symlink1.is_symlink())
        self.assertTrue(symlink2.is_symlink())
        self.assertEqual(symlink1.resolve(), note1_dir.resolve())
        self.assertEqual(symlink2.resolve(), note2_dir.resolve())

    def test_sync_smart_folder_removes_stale_symlinks(self):
        """Test that smart folder sync removes stale symlinks."""
        account = self.make_account()
        smart_folder = self.make_folder("#bookmark", "sf-1", account)

        smart_folder_path = self.target_path / "iCloud" / "#bookmark"
        smart_folder_path.mkdir(parents=True)
        stale_symlink = smart_folder_path / "Old Note"
        stale_symlink.symlink_to("../home/Old Note")

        noteworthy._sync_smart_folder(smart_folder)

        self.assertFalse(stale_symlink.exists())

    def test_sync_smart_folder_preserves_valid_symlinks(self):
        """Test that smart folder sync preserves symlinks that are still valid."""
        account = self.make_account()
        home_folder = self.make_folder("home", "folder-1", account)
        note = self.make_note("My Note", "note-1", home_folder, uuid="UUID-1")

        smart_folder = self.make_folder("#bookmark", "sf-1", account)
        smart_folder.add_note(note)

        note_dir = self.target_path / "iCloud" / "home" / "My Note"
        note_dir.mkdir(parents=True)
        note.set_path(note_dir)

        smart_folder_path = self.target_path / "iCloud" / "#bookmark"
        smart_folder_path.mkdir(parents=True)
        existing_symlink = smart_folder_path / "My Note"
        existing_symlink.symlink_to("../home/My Note")
        inode_before = os.lstat(existing_symlink).st_ino

        noteworthy._sync_smart_folder(smart_folder)

        self.assertTrue(existing_symlink.is_symlink())
        inode_after = os.lstat(existing_symlink).st_ino
        self.assertEqual(inode_before, inode_after)

    def test_sync_smart_folder_updates_wrong_symlink(self):
        """Test that smart folder sync updates symlinks that point to wrong target."""
        account = self.make_account()
        home_folder = self.make_folder("home", "folder-1", account)
        note = self.make_note("My Note", "note-1", home_folder, uuid="UUID-1")

        smart_folder = self.make_folder("#bookmark", "sf-1", account)
        smart_folder.add_note(note)

        note_dir = self.target_path / "iCloud" / "home" / "My Note"
        note_dir.mkdir(parents=True)
        note.set_path(note_dir)

        smart_folder_path = self.target_path / "iCloud" / "#bookmark"
        smart_folder_path.mkdir(parents=True)
        wrong_symlink = smart_folder_path / "My Note"
        wrong_symlink.symlink_to("../other/Wrong Note")

        noteworthy._sync_smart_folder(smart_folder)

        self.assertEqual(os.readlink(wrong_symlink), "../home/My Note")

    def test_sync_smart_folder_skips_notes_without_path(self):
        """Test that smart folder sync skips notes that don't have a path set."""
        account = self.make_account()
        home_folder = self.make_folder("home", "folder-1", account)
        note = self.make_note("My Note", "note-1", home_folder, uuid="UUID-1")

        smart_folder = self.make_folder("#bookmark", "sf-1", account)
        smart_folder.add_note(note)

        noteworthy._sync_smart_folder(smart_folder)

        smart_folder_path = self.target_path / "iCloud" / "#bookmark"
        self.assertTrue(smart_folder_path.exists())
        contents = list(smart_folder_path.iterdir())
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0].name, ".noteworthy.json")


class TestSmartFolderOperations(SyncTestCase):
    """Test smart folder specific operations (delete, membership changes)."""

    def test_deleted_smart_folder_moved_to_deleted_directory(self):
        """Test that a smart folder deleted from Apple Notes is moved to Deleted directory."""
        local_account = self.make_account()
        local_smart_folder = self.make_folder("#bookmark", "sf-1", local_account, is_smart=True)

        account_dir = self.target_path / "iCloud"
        smart_folder_dir = account_dir / "#bookmark"
        smart_folder_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        local_smart_folder.set_path(smart_folder_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(local_smart_folder, smart_folder_dir)

        apple_account = self.make_account()

        noteworthy._sync_account(apple_account, local_account)

        deleted_dir = self.target_path / "Deleted"
        self.assertTrue(deleted_dir.exists())
        self.assertTrue((deleted_dir / "#bookmark").exists())
        self.assertFalse(smart_folder_dir.exists())

    def test_smart_folder_note_added(self):
        """Test that adding a note to a smart folder creates a new symlink."""
        apple_account = self.make_account()
        home_folder = self.make_folder("home", "folder-1", apple_account)
        note = self.make_note("My Note", "note-1", home_folder, uuid="UUID-1")

        smart_folder = self.make_folder("#bookmark", "sf-1", apple_account, is_smart=True)

        account_dir = self.target_path / "iCloud"
        home_dir = account_dir / "home"
        note_dir = home_dir / "My Note"
        note_dir.mkdir(parents=True)
        note.set_path(note_dir)

        smart_folder_dir = account_dir / "#bookmark"
        smart_folder_dir.mkdir(parents=True)

        noteworthy._sync_smart_folder(smart_folder)
        symlink_path = smart_folder_dir / "My Note"
        self.assertFalse(symlink_path.exists())

        smart_folder.add_note(note)
        noteworthy._sync_smart_folder(smart_folder)

        self.assertTrue(symlink_path.is_symlink())
        self.assertEqual(symlink_path.resolve(), note_dir.resolve())

    def test_smart_folder_note_removed(self):
        """Test that removing a note from a smart folder removes the symlink."""
        apple_account = self.make_account()
        home_folder = self.make_folder("home", "folder-1", apple_account)
        note = self.make_note("My Note", "note-1", home_folder, uuid="UUID-1")

        smart_folder = self.make_folder("#bookmark", "sf-1", apple_account, is_smart=True)
        smart_folder.add_note(note)

        account_dir = self.target_path / "iCloud"
        home_dir = account_dir / "home"
        note_dir = home_dir / "My Note"
        note_dir.mkdir(parents=True)
        note.set_path(note_dir)

        smart_folder_dir = account_dir / "#bookmark"
        smart_folder_dir.mkdir(parents=True)

        noteworthy._sync_smart_folder(smart_folder)
        symlink_path = smart_folder_dir / "My Note"
        self.assertTrue(symlink_path.is_symlink())

        smart_folder._notes.remove(note)
        noteworthy._sync_smart_folder(smart_folder)

        self.assertFalse(symlink_path.exists())


if __name__ == '__main__':
    import unittest
    unittest.main()
