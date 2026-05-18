#!/usr/bin/env python
"""Tests for sync operations (create, delete, move, rename)."""

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from noteworthy.notes_datatypes import write_metadata_file, read_distributed_metadata, _sanitize_name
from noteworthy import noteworthy
from sync_test_base import SyncTestCase


class TestSyncWithStalePaths(SyncTestCase):
    """Test that sync handles stale paths (paths in metadata that don't exist on disk)."""

    def test_sync_folder_with_stale_parent_path(self):
        """Test that syncing a folder succeeds even when parent has a stale path."""
        apple_account = self.make_account()
        parent_folder = self.make_folder("home", "folder-1", apple_account)
        self.make_folder("journal", "folder-2", parent_folder)

        local_account = self.make_account()
        local_account.set_path(self.target_path / "iCloud")
        local_parent = self.make_folder("home", "folder-1", local_account)
        local_parent.set_path(self.target_path / "iCloud" / "home")

        noteworthy._sync_account(apple_account, local_account)

        self.assertTrue((self.target_path / "iCloud").exists())
        self.assertTrue((self.target_path / "iCloud" / "home").exists())
        self.assertTrue((self.target_path / "iCloud" / "home" / "journal").exists())

    def test_sync_folder_fresh_start(self):
        """Test that syncing works correctly on a fresh start with no local data."""
        apple_account = self.make_account()
        parent_folder = self.make_folder("home", "folder-1", apple_account)
        self.make_folder("journal", "folder-2", parent_folder)

        noteworthy._sync_account(apple_account, None)

        self.assertTrue((self.target_path / "iCloud").exists())
        self.assertTrue((self.target_path / "iCloud" / "home").exists())
        self.assertTrue((self.target_path / "iCloud" / "home" / "journal").exists())

    def test_sync_account_with_stale_path(self):
        """Test that syncing an account creates directory even if local has stale path."""
        apple_account = self.make_account()

        local_account = self.make_account()
        local_account.set_path(self.target_path / "iCloud")

        account_dir = self.target_path / "iCloud"
        self.assertFalse(account_dir.exists())

        noteworthy._sync_account(apple_account, local_account)

        self.assertTrue(account_dir.exists())


class TestDeletionHandling(SyncTestCase):
    """Test deletion handling - items deleted from Apple Notes are moved to Deleted directory."""

    def test_deleted_note_moved_to_deleted_directory(self):
        """Test that a note deleted from Apple Notes is moved to Deleted directory."""
        local_account = self.make_account()
        local_folder = self.make_folder("home", "folder-1", local_account)
        local_note = self.make_note("My Note", "note-1", local_folder, uuid="UUID-1")

        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)
        (note_dir / "My Note.md").write_text("# My Note\n\nContent here")

        local_account.set_path(account_dir)
        local_folder.set_path(folder_dir)
        local_note.set_path(note_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(local_folder, folder_dir)
        write_metadata_file(local_note, note_dir)

        apple_account = self.make_account()
        self.make_folder("home", "folder-1", apple_account)

        noteworthy._sync_account(apple_account, local_account)

        deleted_dir = self.target_path / "Deleted"
        self.assertTrue(deleted_dir.exists())
        self.assertTrue((deleted_dir / "My Note").exists())
        self.assertTrue((deleted_dir / "My Note" / "My Note.md").exists())
        self.assertFalse(note_dir.exists())

    def test_deleted_folder_moved_to_deleted_directory(self):
        """Test that a folder deleted from Apple Notes is moved to Deleted directory."""
        local_account = self.make_account()
        local_folder = self.make_folder("home", "folder-1", local_account)

        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        folder_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        local_folder.set_path(folder_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(local_folder, folder_dir)

        apple_account = self.make_account()

        noteworthy._sync_account(apple_account, local_account)

        deleted_dir = self.target_path / "Deleted"
        self.assertTrue(deleted_dir.exists())
        self.assertTrue((deleted_dir / "home").exists())
        self.assertFalse(folder_dir.exists())

    def test_deleted_account_moved_to_deleted_directory(self):
        """Test that an account deleted from Apple Notes is moved to Deleted directory."""
        local_account = self.make_account("OldAccount", "account-old")
        account_dir = self.target_path / "OldAccount"
        account_dir.mkdir(parents=True)
        local_account.set_path(account_dir)
        write_metadata_file(local_account, account_dir)

        apple_account = self.make_account("iCloud", "account-1")

        noteworthy._update_copy([local_account], [apple_account])

        deleted_dir = self.target_path / "Deleted"
        self.assertTrue(deleted_dir.exists())
        self.assertTrue((deleted_dir / "OldAccount").exists())
        self.assertFalse(account_dir.exists())

    def test_deleted_nested_folder_moves_contents(self):
        """Test that deleting a parent folder moves the entire subtree to Deleted."""
        local_account = self.make_account()
        parent_folder = self.make_folder("parent", "folder-1", local_account)
        child_folder = self.make_folder("child", "folder-2", parent_folder)

        account_dir = self.target_path / "iCloud"
        parent_dir = account_dir / "parent"
        child_dir = parent_dir / "child"
        child_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        parent_folder.set_path(parent_dir)
        child_folder.set_path(child_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(parent_folder, parent_dir)
        write_metadata_file(child_folder, child_dir)

        apple_account = self.make_account()

        noteworthy._sync_account(apple_account, local_account)

        deleted_dir = self.target_path / "Deleted"
        self.assertTrue(deleted_dir.exists())
        self.assertTrue((deleted_dir / "parent").exists())
        self.assertTrue((deleted_dir / "parent" / "child").exists())
        self.assertFalse(parent_dir.exists())

    def test_deletion_handles_name_collision(self):
        """Test that deletion handles name collisions in Deleted directory."""
        local_account = self.make_account()
        folder1 = self.make_folder("work", "folder-1", local_account)
        folder2 = self.make_folder("personal", "folder-2", local_account)
        note1 = self.make_note("My Note", "note-1", folder1)
        note2 = self.make_note("My Note", "note-2", folder2)

        account_dir = self.target_path / "iCloud"
        folder1_dir = account_dir / "work"
        folder2_dir = account_dir / "personal"
        note1_dir = folder1_dir / "My Note"
        note2_dir = folder2_dir / "My Note"
        note1_dir.mkdir(parents=True)
        note2_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        folder1.set_path(folder1_dir)
        folder2.set_path(folder2_dir)
        note1.set_path(note1_dir)
        note2.set_path(note2_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(folder1, folder1_dir)
        write_metadata_file(folder2, folder2_dir)
        write_metadata_file(note1, note1_dir)
        write_metadata_file(note2, note2_dir)

        apple_account = self.make_account()
        self.make_folder("work", "folder-1", apple_account)
        self.make_folder("personal", "folder-2", apple_account)

        noteworthy._sync_account(apple_account, local_account)

        deleted_dir = self.target_path / "Deleted"
        self.assertTrue(deleted_dir.exists())
        self.assertTrue((deleted_dir / "My Note").exists())
        self.assertTrue((deleted_dir / "My Note_2").exists())

    def test_deleted_directory_created_on_first_deletion(self):
        """Test that Deleted directory is created automatically on first deletion."""
        deleted_dir = self.target_path / "Deleted"
        self.assertFalse(deleted_dir.exists())

        local_account = self.make_account()
        local_folder = self.make_folder("home", "folder-1", local_account)
        local_note = self.make_note("My Note", "note-1", local_folder)

        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        local_folder.set_path(folder_dir)
        local_note.set_path(note_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(local_folder, folder_dir)
        write_metadata_file(local_note, note_dir)

        apple_account = self.make_account()
        self.make_folder("home", "folder-1", apple_account)

        noteworthy._sync_account(apple_account, local_account)

        self.assertTrue(deleted_dir.exists())


class TestFolderRelocation(SyncTestCase):
    """Test folder relocation when parent changes."""

    def test_folder_moved_to_new_parent(self):
        """Test that a folder moved to a new parent is relocated correctly."""
        local_account = self.make_account()
        parent1 = self.make_folder("parent1", "folder-p1", local_account)
        moving_folder = self.make_folder("moving", "folder-moving", parent1)

        account_dir = self.target_path / "iCloud"
        parent1_dir = account_dir / "parent1"
        moving_dir = parent1_dir / "moving"
        moving_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        parent1.set_path(parent1_dir)
        moving_folder.set_path(moving_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(parent1, parent1_dir)
        write_metadata_file(moving_folder, moving_dir)

        apple_account = self.make_account()
        self.make_folder("parent1", "folder-p1", apple_account)
        apple_parent2 = self.make_folder("parent2", "folder-p2", apple_account)
        apple_moving = self.make_folder("moving", "folder-moving", apple_parent2)

        parent2_dir = account_dir / "parent2"
        parent2_dir.mkdir(parents=True)

        local_folders_by_id = {f.id: f for f in local_account.all_folders()}
        noteworthy._sync_folder(apple_moving, local_folders_by_id.get(apple_moving.id))

        new_location = account_dir / "parent2" / "moving"
        self.assertTrue(new_location.exists())
        self.assertFalse(moving_dir.exists())

    def test_folder_moved_preserves_contents(self):
        """Test that folder move preserves all contents."""
        local_account = self.make_account()
        parent1 = self.make_folder("parent1", "folder-p1", local_account)
        moving_folder = self.make_folder("moving", "folder-moving", parent1)

        account_dir = self.target_path / "iCloud"
        parent1_dir = account_dir / "parent1"
        moving_dir = parent1_dir / "moving"
        moving_dir.mkdir(parents=True)
        (moving_dir / "test_file.txt").write_text("test content")

        local_account.set_path(account_dir)
        parent1.set_path(parent1_dir)
        moving_folder.set_path(moving_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(parent1, parent1_dir)
        write_metadata_file(moving_folder, moving_dir)

        apple_account = self.make_account()
        self.make_folder("parent1", "folder-p1", apple_account)
        apple_parent2 = self.make_folder("parent2", "folder-p2", apple_account)
        apple_moving = self.make_folder("moving", "folder-moving", apple_parent2)

        parent2_dir = account_dir / "parent2"
        parent2_dir.mkdir(parents=True)

        local_folders_by_id = {f.id: f for f in local_account.all_folders()}
        noteworthy._sync_folder(apple_moving, local_folders_by_id.get(apple_moving.id))

        new_location = account_dir / "parent2" / "moving"
        self.assertTrue((new_location / "test_file.txt").exists())
        self.assertEqual((new_location / "test_file.txt").read_text(), "test content")

    def test_folder_moved_to_account_root(self):
        """Test that a nested folder can be moved to account root."""
        local_account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", local_account)
        nested_folder = self.make_folder("nested", "folder-nested", parent)

        account_dir = self.target_path / "iCloud"
        parent_dir = account_dir / "parent"
        nested_dir = parent_dir / "nested"
        nested_dir.mkdir(parents=True)

        local_account.set_path(account_dir)
        parent.set_path(parent_dir)
        nested_folder.set_path(nested_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(parent, parent_dir)
        write_metadata_file(nested_folder, nested_dir)

        apple_account = self.make_account()
        self.make_folder("parent", "folder-parent", apple_account)
        apple_nested = self.make_folder("nested", "folder-nested", apple_account)

        local_folders_by_id = {f.id: f for f in local_account.all_folders()}
        noteworthy._sync_folder(apple_nested, local_folders_by_id.get(apple_nested.id))

        new_location = account_dir / "nested"
        self.assertTrue(new_location.exists())
        self.assertFalse(nested_dir.exists())


class TestNoteRelocation(SyncTestCase):
    """Test note relocation when home folder changes."""

    def test_note_moved_to_different_folder(self):
        """Test that a note moved to a different folder is relocated."""
        mod_date = datetime(2024, 1, 20, 14, 45)

        local_account = self.make_account()
        folder1 = self.make_folder("folder1", "folder-1", local_account)
        folder2 = self.make_folder("folder2", "folder-2", local_account)
        note = self.make_note("My Note", "note-1", folder1, modification_date=mod_date)

        account_dir = self.target_path / "iCloud"
        folder1_dir = account_dir / "folder1"
        folder2_dir = account_dir / "folder2"
        note_dir = folder1_dir / "My Note"
        note_dir.mkdir(parents=True)
        folder2_dir.mkdir(parents=True)
        (note_dir / "My Note.md").write_text("# My Note")

        local_account.set_path(account_dir)
        folder1.set_path(folder1_dir)
        folder2.set_path(folder2_dir)
        note.set_path(note_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(folder1, folder1_dir)
        write_metadata_file(folder2, folder2_dir)
        write_metadata_file(note, note_dir)

        apple_account = self.make_account()
        self.make_folder("folder1", "folder-1", apple_account)
        apple_folder2 = self.make_folder("folder2", "folder-2", apple_account)
        apple_note = self.make_note("My Note", "note-1", apple_folder2, modification_date=mod_date)

        apple_folder2.set_path(folder2_dir)
        new_note_path = self.target_path / apple_note.choose_path()
        apple_note.set_path(new_note_path)

        local_notes_by_id = {n.id: n for n in local_account.all_notes()}
        local_note = local_notes_by_id.get(apple_note.id)

        noteworthy._copy_note(apple_note, local_note, {})

        new_location = folder2_dir / "My Note"
        self.assertTrue(new_location.exists())
        self.assertTrue((new_location / "My Note.md").exists())
        self.assertFalse(note_dir.exists())


class TestNoteCreateAndModify(SyncTestCase):
    """Test note creation and content modification."""

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_new_note_calls_make_markdown_copy(self, mock_make_copy):
        """Test that a new note (not in local) triggers make_markdown_copy."""
        apple_account = self.make_account()
        apple_folder = self.make_folder("home", "folder-1", apple_account)
        apple_note = self.make_note("New Note", "note-1", apple_folder, uuid="UUID-1")

        folder_dir = self.target_path / "iCloud" / "home"
        folder_dir.mkdir(parents=True)
        apple_folder.set_path(folder_dir)

        note_path = folder_dir / "New Note"
        note_path.mkdir(parents=True)
        apple_note.set_path(note_path)

        noteworthy._copy_note(apple_note, None, {})

        mock_make_copy.assert_called_once()
        call_args = mock_make_copy.call_args
        self.assertEqual(call_args[0][0], apple_note)
        self.assertEqual(call_args[0][1], note_path)

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_modified_note_calls_make_markdown_copy(self, mock_make_copy):
        """Test that a modified note (different modification_date) triggers make_markdown_copy."""
        old_mod_date = datetime(2024, 1, 20, 14, 45)
        new_mod_date = datetime(2024, 1, 25, 10, 00)

        local_account = self.make_account()
        local_folder = self.make_folder("home", "folder-1", local_account)
        local_note = self.make_note("My Note", "note-1", local_folder, modification_date=old_mod_date)

        folder_dir = self.target_path / "iCloud" / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)
        local_folder.set_path(folder_dir)
        local_note.set_path(note_dir)

        apple_account = self.make_account()
        apple_folder = self.make_folder("home", "folder-1", apple_account)
        apple_note = self.make_note("My Note", "note-1", apple_folder, modification_date=new_mod_date)
        apple_folder.set_path(folder_dir)
        apple_note.set_path(note_dir)

        noteworthy._copy_note(apple_note, local_note, {})

        mock_make_copy.assert_called_once()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_unchanged_note_skips_make_markdown_copy(self, mock_make_copy):
        """Test that an unchanged note does not trigger make_markdown_copy."""
        mod_date = datetime(2024, 1, 20, 14, 45)

        local_account = self.make_account()
        local_folder = self.make_folder("home", "folder-1", local_account)
        local_note = self.make_note("My Note", "note-1", local_folder, modification_date=mod_date)

        folder_dir = self.target_path / "iCloud" / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)
        local_folder.set_path(folder_dir)
        local_note.set_path(note_dir)

        apple_account = self.make_account()
        apple_folder = self.make_folder("home", "folder-1", apple_account)
        apple_note = self.make_note("My Note", "note-1", apple_folder, modification_date=mod_date)
        apple_folder.set_path(folder_dir)
        apple_note.set_path(note_dir)

        noteworthy._copy_note(apple_note, local_note, {})

        mock_make_copy.assert_not_called()


class TestRenameOperations(SyncTestCase):
    """Test rename operations for accounts, folders, and notes."""

    def test_folder_renamed_relocates_directory(self):
        """Test that a renamed folder moves the directory to the new name."""
        local_account = self.make_account()
        local_folder = self.make_folder("OldName", "folder-1", local_account)

        account_dir = self.target_path / "iCloud"
        old_folder_dir = account_dir / "OldName"
        old_folder_dir.mkdir(parents=True)
        (old_folder_dir / "test_file.txt").write_text("test content")

        local_account.set_path(account_dir)
        local_folder.set_path(old_folder_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(local_folder, old_folder_dir)

        apple_account = self.make_account()
        apple_folder = self.make_folder("NewName", "folder-1", apple_account)

        noteworthy._sync_folder(apple_folder, local_folder)

        new_folder_dir = account_dir / "NewName"
        self.assertTrue(new_folder_dir.exists())
        self.assertTrue((new_folder_dir / "test_file.txt").exists())
        self.assertFalse(old_folder_dir.exists())

    def test_account_renamed_relocates_directory(self):
        """Test that a renamed account moves the directory to the new name."""
        local_account = self.make_account("OldAccount", "account-1")
        old_account_dir = self.target_path / "OldAccount"
        old_account_dir.mkdir(parents=True)
        (old_account_dir / "test_file.txt").write_text("test content")
        local_account.set_path(old_account_dir)

        write_metadata_file(local_account, old_account_dir)

        apple_account = self.make_account("NewAccount", "account-1")

        noteworthy._sync_account(apple_account, local_account)

        new_account_dir = self.target_path / "NewAccount"
        self.assertTrue(new_account_dir.exists())
        self.assertTrue((new_account_dir / "test_file.txt").exists())
        self.assertFalse(old_account_dir.exists())

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_note_renamed_relocates_directory(self, mock_make_copy):
        """Test that a renamed note moves the directory to the new name."""
        mod_date = datetime(2024, 1, 20, 14, 45)

        local_account = self.make_account()
        local_folder = self.make_folder("home", "folder-1", local_account)
        local_note = self.make_note("OldNoteName", "note-1", local_folder, modification_date=mod_date)

        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        old_note_dir = folder_dir / "OldNoteName"
        old_note_dir.mkdir(parents=True)
        (old_note_dir / "OldNoteName.md").write_text("# Old Note")

        local_account.set_path(account_dir)
        local_folder.set_path(folder_dir)
        local_note.set_path(old_note_dir)

        write_metadata_file(local_account, account_dir)
        write_metadata_file(local_folder, folder_dir)
        write_metadata_file(local_note, old_note_dir)

        apple_account = self.make_account()
        apple_folder = self.make_folder("home", "folder-1", apple_account)
        apple_note = self.make_note("NewNoteName", "note-1", apple_folder, modification_date=mod_date)

        apple_folder.set_path(folder_dir)
        new_note_dir = folder_dir / "NewNoteName"
        apple_note.set_path(new_note_dir)

        noteworthy._copy_note(apple_note, local_note, {})

        self.assertTrue(new_note_dir.exists())
        self.assertFalse(old_note_dir.exists())


class TestMetadataDatetimeRoundtrip(SyncTestCase):
    """Test that datetime values survive the write_metadata_file → read_distributed_metadata cycle."""

    def test_utc_aware_datetime_roundtrips(self):
        """Test that UTC-aware datetimes roundtrip correctly through metadata serialization."""
        mod_date = datetime(2024, 1, 20, 14, 45, 30, tzinfo=timezone.utc)
        creation_date = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)

        account = self.make_account()
        folder = self.make_folder("home", "folder-1", account)
        note = self.make_note("My Note", "note-1", folder, modification_date=mod_date)
        # Override the creation_date set by make_note
        note._creation_date = creation_date

        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)

        account.set_path(account_dir)
        folder.set_path(folder_dir)
        note.set_path(note_dir)

        write_metadata_file(account, account_dir)
        write_metadata_file(folder, folder_dir)
        write_metadata_file(note, note_dir)

        reconstructed = read_distributed_metadata(self.target_path)
        self.assertEqual(len(reconstructed), 1)

        reconstructed_notes = reconstructed[0].all_notes()
        self.assertEqual(len(reconstructed_notes), 1)

        reconstructed_note = reconstructed_notes[0]
        self.assertEqual(reconstructed_note.modification_date, mod_date)
        self.assertEqual(reconstructed_note.creation_date, creation_date)

    def test_naive_datetime_roundtrips(self):
        """Test that naive datetimes roundtrip correctly through metadata serialization."""
        mod_date = datetime(2024, 1, 20, 14, 45, 30)

        account = self.make_account()
        folder = self.make_folder("home", "folder-1", account)
        note = self.make_note("My Note", "note-1", folder, modification_date=mod_date)

        account_dir = self.target_path / "iCloud"
        folder_dir = account_dir / "home"
        note_dir = folder_dir / "My Note"
        note_dir.mkdir(parents=True)

        account.set_path(account_dir)
        folder.set_path(folder_dir)
        note.set_path(note_dir)

        write_metadata_file(account, account_dir)
        write_metadata_file(folder, folder_dir)
        write_metadata_file(note, note_dir)

        reconstructed = read_distributed_metadata(self.target_path)
        reconstructed_note = reconstructed[0].all_notes()[0]
        self.assertEqual(reconstructed_note.modification_date, mod_date)


class TestIncrementalSyncRoundtrip(SyncTestCase):
    """Test that running sync twice without changes doesn't re-extract notes.

    These tests exercise the full metadata write → read_distributed_metadata → sync roundtrip,
    unlike the shallow test_unchanged_note_skips_make_markdown_copy which bypasses serialization.
    """

    @staticmethod
    def _mkdir_side_effect(note, path, **kwargs):
        """Side effect for mocked make_markdown_copy that creates the note directory."""
        path.mkdir(parents=True, exist_ok=True)

    def _make_apple_account_with_note(self, mod_date, note_name="My Note", note_id="note-1", uuid="UUID-1"):
        """Helper to create a fresh apple account/folder/note structure (no paths set)."""
        account = self.make_account()
        folder = self.make_folder("home", "folder-1", account)
        note = self.make_note(note_name, note_id, folder, modification_date=mod_date, uuid=uuid)
        return account, folder, note

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_single_note_not_reextracted_on_second_sync(self, mock_make_copy):
        """A single unchanged note should not be re-extracted on a second sync."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, 30, tzinfo=timezone.utc)

        # First sync: fresh, no local data
        apple_account, _, _ = self._make_apple_account_with_note(mod_date)
        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        # Read back local metadata (simulates what _make_backup_copies does on next run)
        local_accounts = read_distributed_metadata(self.target_path)
        self.assertEqual(len(local_accounts), 1)
        local_account = local_accounts[0]
        local_notes = local_account.all_notes()
        self.assertEqual(len(local_notes), 1)

        # Second sync: same apple data, should skip re-extraction
        mock_make_copy.reset_mock()
        apple_account2, _, _ = self._make_apple_account_with_note(mod_date)
        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_multiple_notes_not_reextracted_on_second_sync(self, mock_make_copy):
        """Multiple unchanged notes should not be re-extracted on a second sync."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date1 = datetime(2024, 1, 20, 14, 45, tzinfo=timezone.utc)
        mod_date2 = datetime(2024, 2, 10, 9, 15, tzinfo=timezone.utc)

        apple_account = self.make_account()
        folder = self.make_folder("home", "folder-1", apple_account)
        self.make_note("Note One", "note-1", folder, modification_date=mod_date1, uuid="UUID-1")
        self.make_note("Note Two", "note-2", folder, modification_date=mod_date2, uuid="UUID-2")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 2)

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("home", "folder-1", apple_account2)
        self.make_note("Note One", "note-1", folder2, modification_date=mod_date1, uuid="UUID-1")
        self.make_note("Note Two", "note-2", folder2, modification_date=mod_date2, uuid="UUID-2")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_nested_folder_notes_not_reextracted(self, mock_make_copy):
        """Notes in nested folders should not be re-extracted on second sync."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 3, 5, 12, 0, tzinfo=timezone.utc)

        apple_account = self.make_account()
        parent = self.make_folder("parent", "folder-1", apple_account)
        child = self.make_folder("child", "folder-2", parent)
        self.make_note("Nested Note", "note-1", child, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        parent2 = self.make_folder("parent", "folder-1", apple_account2)
        child2 = self.make_folder("child", "folder-2", parent2)
        self.make_note("Nested Note", "note-1", child2, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_deleted_note_metadata_does_not_cause_reextraction(self, mock_make_copy):
        """Metadata left in Deleted/ directory should not interfere with reconstruction."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, tzinfo=timezone.utc)

        # First sync: two notes
        apple_account = self.make_account()
        folder = self.make_folder("home", "folder-1", apple_account)
        self.make_note("Note A", "note-a", folder, modification_date=mod_date, uuid="UUID-A")
        self.make_note("Note B", "note-b", folder, modification_date=mod_date, uuid="UUID-B")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 2)

        # Second sync: Note B deleted from Apple Notes
        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("home", "folder-1", apple_account2)
        self.make_note("Note A", "note-a", folder2, modification_date=mod_date, uuid="UUID-A")

        noteworthy._sync_account(apple_account2, local_account)
        # Note A should NOT be re-extracted (it's unchanged)
        mock_make_copy.assert_not_called()

        # Third sync: after deletion, Deleted/ contains Note B's metadata
        # Verify Note A is still not re-extracted
        local_accounts2 = read_distributed_metadata(self.target_path)
        self.assertEqual(len(local_accounts2), 1)
        local_account2 = local_accounts2[0]
        local_notes = local_account2.all_notes()
        # Should only find Note A, not the deleted Note B
        self.assertEqual(len(local_notes), 1)
        self.assertEqual(local_notes[0].name, "Note A")

        mock_make_copy.reset_mock()
        apple_account3 = self.make_account()
        folder3 = self.make_folder("home", "folder-1", apple_account3)
        self.make_note("Note A", "note-a", folder3, modification_date=mod_date, uuid="UUID-A")

        noteworthy._sync_account(apple_account3, local_account2)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_smart_folder_note_not_reextracted(self, mock_make_copy):
        """Notes that belong to both a regular and smart folder should not be re-extracted."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, tzinfo=timezone.utc)

        apple_account = self.make_account()
        folder = self.make_folder("home", "folder-1", apple_account)
        smart = self.make_folder("Pinned", "folder-smart", apple_account, is_smart=True)
        note = self.make_note("My Note", "note-1", folder, modification_date=mod_date, uuid="UUID-1")
        smart.add_note(note)

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("home", "folder-1", apple_account2)
        smart2 = self.make_folder("Pinned", "folder-smart", apple_account2, is_smart=True)
        note2 = self.make_note("My Note", "note-1", folder2, modification_date=mod_date, uuid="UUID-1")
        smart2.add_note(note2)

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()


    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_folder_with_colon_not_reextracted_on_second_sync(self, mock_make_copy):
        """Folder 'Evan 1:1' (sanitized to 'Evan 1-1') should not cause re-extraction."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, 30, tzinfo=timezone.utc)

        # First sync
        apple_account = self.make_account()
        folder = self.make_folder("Evan 1:1", "folder-1", apple_account)
        self.make_note("Standup Notes", "note-1", folder, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        # Verify directory uses sanitized name
        self.assertTrue((self.target_path / "iCloud" / "Evan 1-1").exists())

        # Read back local metadata
        local_accounts = read_distributed_metadata(self.target_path)
        self.assertEqual(len(local_accounts), 1)
        local_account = local_accounts[0]
        local_notes = local_account.all_notes()
        self.assertEqual(len(local_notes), 1)

        # Second sync: same data, should not re-extract
        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("Evan 1:1", "folder-1", apple_account2)
        self.make_note("Standup Notes", "note-1", folder2, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_note_with_colon_not_reextracted_on_second_sync(self, mock_make_copy):
        """Note 'Meeting 1:1 with Bob' (sanitized to 'Meeting 1-1 with Bob') should not cause re-extraction."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 2, 10, 9, 15, tzinfo=timezone.utc)

        apple_account = self.make_account()
        folder = self.make_folder("work", "folder-1", apple_account)
        self.make_note("Meeting 1:1 with Bob", "note-1", folder, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        # Verify sanitized directory name
        self.assertTrue((self.target_path / "iCloud" / "work" / "Meeting 1-1 with Bob").exists())

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("work", "folder-1", apple_account2)
        self.make_note("Meeting 1:1 with Bob", "note-1", folder2, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_folder_with_slash_not_reextracted_on_second_sync(self, mock_make_copy):
        """Folder 'Q1/2024 Goals' (sanitized to 'Q1_2024 Goals') should not cause re-extraction."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 3, 1, 8, 0, tzinfo=timezone.utc)

        apple_account = self.make_account()
        folder = self.make_folder("Q1/2024 Goals", "folder-1", apple_account)
        self.make_note("OKRs", "note-1", folder, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        # Verify sanitized directory name
        self.assertTrue((self.target_path / "iCloud" / "Q1_2024 Goals").exists())

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("Q1/2024 Goals", "folder-1", apple_account2)
        self.make_note("OKRs", "note-1", folder2, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_nested_sanitized_names_not_reextracted(self, mock_make_copy):
        """Nested folders with mixed sanitized chars should not cause re-extraction."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 4, 15, 12, 0, tzinfo=timezone.utc)

        apple_account = self.make_account()
        parent = self.make_folder("Q1/2024", "folder-1", apple_account)
        child = self.make_folder("Evan 1:1", "folder-2", parent)
        self.make_note("Action Items: Week 3", "note-1", child, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 1)

        # Verify nested sanitized directory names
        self.assertTrue((self.target_path / "iCloud" / "Q1_2024" / "Evan 1-1").exists())
        self.assertTrue(
            (self.target_path / "iCloud" / "Q1_2024" / "Evan 1-1" / "Action Items- Week 3").exists()
        )

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        parent2 = self.make_folder("Q1/2024", "folder-1", apple_account2)
        child2 = self.make_folder("Evan 1:1", "folder-2", parent2)
        self.make_note("Action Items: Week 3", "note-1", child2, modification_date=mod_date, uuid="UUID-1")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()


class TestNoteFolderNameCollision(SyncTestCase):
    """Test that a note and folder with the same sanitized name don't collide."""

    @staticmethod
    def _mkdir_side_effect(note, path, **kwargs):
        path.mkdir(parents=True, exist_ok=True)

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_note_and_folder_same_name_no_metadata_overwrite(self, mock_make_copy):
        """A note 'Evan 1:1' and folder 'Evan 1:1' under the same parent must get different directories."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, tzinfo=timezone.utc)
        inner_mod_date = datetime(2020, 1, 6, 17, 50, 35, tzinfo=timezone.utc)

        apple_account = self.make_account()
        old_folder = self.make_folder("old", "folder-old", apple_account)
        # Folder named "Evan 1:1" (a subfolder of "old")
        evan_folder = self.make_folder("Evan 1:1", "folder-evan", old_folder)
        # Note named "Evan 1:1" (a direct note in "old") -- same sanitized name as the folder
        self.make_note("Evan 1:1", "note-evan", old_folder, modification_date=mod_date, uuid="UUID-EVAN")
        # Note inside the folder
        self.make_note("Evan January 6, 2020", "note-jan6", evan_folder,
                       modification_date=inner_mod_date, uuid="UUID-JAN6")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 2)

        # The folder must own "Evan 1-1" and the note must get a suffixed name
        folder_dir = self.target_path / "iCloud" / "old" / "Evan 1-1"
        note_dir = self.target_path / "iCloud" / "old" / "Evan 1-1_2"
        self.assertTrue(folder_dir.exists())
        self.assertTrue(note_dir.exists())

        # Verify folder metadata is preserved (not overwritten by note metadata)
        import json
        with (folder_dir / ".noteworthy.json").open() as f:
            folder_meta = json.load(f)
        self.assertEqual(folder_meta["type"], "folder")
        self.assertEqual(folder_meta["name"], "Evan 1:1")

        with (note_dir / ".noteworthy.json").open() as f:
            note_meta = json.load(f)
        self.assertEqual(note_meta["type"], "note")
        self.assertEqual(note_meta["name"], "Evan 1:1")

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_note_folder_collision_roundtrip_no_reextraction(self, mock_make_copy):
        """After sync with note/folder name collision, second sync should not re-extract anything."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, tzinfo=timezone.utc)
        inner_mod_date = datetime(2020, 1, 6, 17, 50, 35, tzinfo=timezone.utc)

        # First sync
        apple_account = self.make_account()
        old_folder = self.make_folder("old", "folder-old", apple_account)
        evan_folder = self.make_folder("Evan 1:1", "folder-evan", old_folder)
        self.make_note("Evan 1:1", "note-evan", old_folder, modification_date=mod_date, uuid="UUID-EVAN")
        self.make_note("Evan January 6, 2020", "note-jan6", evan_folder,
                       modification_date=inner_mod_date, uuid="UUID-JAN6")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 2)

        # Read back and second sync
        local_accounts = read_distributed_metadata(self.target_path)
        self.assertEqual(len(local_accounts), 1)
        local_account = local_accounts[0]

        # Verify both notes are in the reconstructed hierarchy
        local_notes = local_account.all_notes()
        self.assertEqual(len(local_notes), 2)

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        old_folder2 = self.make_folder("old", "folder-old", apple_account2)
        evan_folder2 = self.make_folder("Evan 1:1", "folder-evan", old_folder2)
        self.make_note("Evan 1:1", "note-evan", old_folder2, modification_date=mod_date, uuid="UUID-EVAN")
        self.make_note("Evan January 6, 2020", "note-jan6", evan_folder2,
                       modification_date=inner_mod_date, uuid="UUID-JAN6")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_note_folder_collision_migration(self, mock_make_copy):
        """First run after fix: note occupying folder's path is re-extracted at new location."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date = datetime(2024, 1, 20, 14, 45, tzinfo=timezone.utc)
        inner_mod_date = datetime(2020, 1, 6, 17, 50, 35, tzinfo=timezone.utc)

        # Simulate the pre-fix state: note metadata at folder's path
        account_dir = self.target_path / "iCloud"
        old_dir = account_dir / "old"
        evan_dir = old_dir / "Evan 1-1"  # folder and note share this path
        inner_note_dir = evan_dir / "Evan January 6, 2020"

        # Create directories as they would exist before the fix
        inner_note_dir.mkdir(parents=True)

        # Write account metadata
        from noteworthy.notes_datatypes import Account, Folder, Note
        local_account = Account("iCloud", "account-1", None)
        local_account.set_path(account_dir)
        write_metadata_file(local_account, account_dir)

        # Write "old" folder metadata
        local_old = Folder("old", "folder-old", None)
        local_account.add_folder(local_old)
        local_old.set_path(old_dir)
        write_metadata_file(local_old, old_dir)

        # Write NOTE metadata at the folder's path (the bug: overwrites folder metadata)
        local_evan_note = Note("Evan 1:1", "note-evan", None,
                               datetime(2024, 1, 15, 10, 30), mod_date, uuid="UUID-EVAN")
        local_old.add_note(local_evan_note)
        local_evan_note.set_path(evan_dir)
        write_metadata_file(local_evan_note, evan_dir)

        # Write inner note metadata (this note references the FOLDER's id, which is missing)
        local_inner_note = Note("Evan January 6, 2020", "note-jan6", None,
                                datetime(2020, 1, 6, 15, 39, 24), inner_mod_date, uuid="UUID-JAN6")
        # Note: we intentionally add this to old_folder (not evan_folder, which doesn't exist in local)
        # because the folder metadata was overwritten. This simulates the orphan scenario.
        local_inner_note.set_path(inner_note_dir)
        write_metadata_file(local_inner_note, inner_note_dir)

        # Read back local metadata (simulates pre-fix state)
        local_accounts = read_distributed_metadata(self.target_path)
        self.assertEqual(len(local_accounts), 1)
        local_account = local_accounts[0]

        # The inner note should be orphaned (its folder metadata was overwritten)
        local_notes = local_account.all_notes()
        local_note_names = {n.name for n in local_notes}
        self.assertIn("Evan 1:1", local_note_names)
        self.assertNotIn("Evan January 6, 2020", local_note_names)

        # Now run sync with the fix - apple data has both note and folder
        apple_account = self.make_account()
        old_folder = self.make_folder("old", "folder-old", apple_account)
        evan_folder = self.make_folder("Evan 1:1", "folder-evan", old_folder)
        self.make_note("Evan 1:1", "note-evan", old_folder, modification_date=mod_date, uuid="UUID-EVAN")
        self.make_note("Evan January 6, 2020", "note-jan6", evan_folder,
                       modification_date=inner_mod_date, uuid="UUID-JAN6")

        noteworthy._sync_account(apple_account, local_account)

        # The note should NOT have moved the folder directory
        folder_dir = self.target_path / "iCloud" / "old" / "Evan 1-1"
        self.assertTrue(folder_dir.exists())

        # The note should be at the new suffixed path
        note_dir = self.target_path / "iCloud" / "old" / "Evan 1-1_2"
        self.assertTrue(note_dir.exists())

        # Folder metadata should now be correct
        import json
        with (folder_dir / ".noteworthy.json").open() as f:
            folder_meta = json.load(f)
        self.assertEqual(folder_meta["type"], "folder")


class TestSanitizeName(unittest.TestCase):
    """Unit tests for the _sanitize_name function."""

    def test_colon_replaced_with_dash(self):
        self.assertEqual(_sanitize_name("Evan 1:1"), "Evan 1-1")

    def test_slash_replaced_with_underscore(self):
        self.assertEqual(_sanitize_name("Q1/2024"), "Q1_2024")

    def test_normal_name_unchanged(self):
        self.assertEqual(_sanitize_name("Normal Name"), "Normal Name")

    def test_multiple_colons(self):
        self.assertEqual(_sanitize_name("Meeting: 1:1: Notes"), "Meeting- 1-1- Notes")

    def test_mixed_special_chars(self):
        self.assertEqual(_sanitize_name("Q1/2024: Goals"), "Q1_2024- Goals")

    def test_smart_quotes_replaced(self):
        self.assertEqual(_sanitize_name('He said \u201chi\u201d'), 'He said \u201chi\u201d')

    def test_tab_replaced_with_space(self):
        self.assertEqual(_sanitize_name("hello\tworld"), "hello world")


class TestChoosePathCollisions(SyncTestCase):
    """Test that Note.choose_path() avoids collisions with sibling folder names."""

    def test_note_avoids_sibling_folder_name(self):
        """A note whose sanitized name matches a sibling folder gets a suffix."""
        import pathlib
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        self.make_folder("Evan 1:1", "folder-evan", parent)  # sibling folder
        note = self.make_note("Evan 1:1", "note-evan", parent)  # note with same sanitized name

        # Folder gets "Evan 1-1", note must avoid it
        path = note.choose_path()
        self.assertEqual(path.name, "Evan 1-1_2")

    def test_note_no_collision_without_sibling_folder(self):
        """A note with no sibling folder collision gets the base name."""
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        note = self.make_note("My Note", "note-1", parent)

        path = note.choose_path()
        self.assertEqual(path.name, "My Note")

    def test_multiple_same_name_notes_with_folder_collision(self):
        """Multiple notes with same name, plus a sibling folder collision."""
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        self.make_folder("X", "folder-x", parent)  # folder takes "X"
        note1 = self.make_note("X", "note-x1", parent)
        note2 = self.make_note("X", "note-x2", parent)

        # "X" is taken by folder, so notes get _2 and _3
        self.assertEqual(note1.choose_path().name, "X_2")
        self.assertEqual(note2.choose_path().name, "X_3")

    def test_case_insensitive_notes_get_unique_paths(self):
        """Notes 'todo' and 'Todo' in the same folder must get different paths (macOS is case-insensitive)."""
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        # IDs determine order: note-a < note-b, so "todo" (note-a) gets base name
        note_lower = self.make_note("todo", "note-a", parent)
        note_upper = self.make_note("Todo", "note-b", parent)

        self.assertEqual(note_lower.choose_path().name, "todo")
        self.assertEqual(note_upper.choose_path().name, "Todo_2")

    def test_case_insensitive_notes_order_by_id(self):
        """The note with the smaller id gets the base name, regardless of insertion order."""
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        # Insert "Todo" first but with a larger id — it should still get the suffix
        note_upper = self.make_note("Todo", "note-z", parent)
        note_lower = self.make_note("todo", "note-a", parent)

        self.assertEqual(note_lower.choose_path().name, "todo")
        self.assertEqual(note_upper.choose_path().name, "Todo_2")

    def test_case_insensitive_folder_collision(self):
        """A note whose name matches a sibling folder case-insensitively gets a suffix."""
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        self.make_folder("Projects", "folder-proj", parent)
        note = self.make_note("projects", "note-proj", parent)

        # "projects" collides with folder "Projects" case-insensitively
        self.assertEqual(note.choose_path().name, "projects_2")

    def test_three_case_variants_get_unique_paths(self):
        """Three notes differing only in case all get unique paths."""
        account = self.make_account()
        parent = self.make_folder("parent", "folder-parent", account)
        note1 = self.make_note("ABC", "note-1", parent)
        note2 = self.make_note("abc", "note-2", parent)
        note3 = self.make_note("Abc", "note-3", parent)

        paths = {note1.choose_path().name, note2.choose_path().name, note3.choose_path().name}
        self.assertEqual(len(paths), 3)


class TestCaseInsensitiveRoundtrip(SyncTestCase):
    """Test that case-insensitive note name collisions survive the sync roundtrip."""

    @staticmethod
    def _mkdir_side_effect(note, path, **kwargs):
        path.mkdir(parents=True, exist_ok=True)

    @patch('noteworthy.noteworthy.make_markdown_copy')
    def test_case_variant_notes_not_reextracted(self, mock_make_copy):
        """Notes 'todo' and 'Todo' should not be re-extracted on second sync."""
        mock_make_copy.side_effect = self._mkdir_side_effect
        mod_date1 = datetime(2024, 1, 20, 14, 45)
        mod_date2 = datetime(2024, 2, 10, 9, 15)

        apple_account = self.make_account()
        folder = self.make_folder("home", "folder-1", apple_account)
        self.make_note("todo", "note-a", folder, modification_date=mod_date1, uuid="UUID-A")
        self.make_note("Todo", "note-b", folder, modification_date=mod_date2, uuid="UUID-B")

        noteworthy._sync_account(apple_account, None)
        self.assertEqual(mock_make_copy.call_count, 2)

        # Verify both note directories were created with distinct names
        home_dir = self.target_path / "iCloud" / "home"
        self.assertTrue((home_dir / "todo").exists())
        self.assertTrue((home_dir / "Todo_2").exists())

        local_accounts = read_distributed_metadata(self.target_path)
        local_account = local_accounts[0]
        self.assertEqual(len(local_account.all_notes()), 2)

        mock_make_copy.reset_mock()
        apple_account2 = self.make_account()
        folder2 = self.make_folder("home", "folder-1", apple_account2)
        self.make_note("todo", "note-a", folder2, modification_date=mod_date1, uuid="UUID-A")
        self.make_note("Todo", "note-b", folder2, modification_date=mod_date2, uuid="UUID-B")

        noteworthy._sync_account(apple_account2, local_account)
        mock_make_copy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
