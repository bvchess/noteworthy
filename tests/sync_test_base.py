#!/usr/bin/env python
"""Base class and helpers for sync tests."""

import pathlib
import tempfile
import unittest
from datetime import datetime

from noteworthy.notes_datatypes import Account, Folder, Note
from noteworthy import noteworthy


class SyncTestCase(unittest.TestCase):
    """Base test class providing common setup and helper methods for sync tests."""

    def setUp(self):
        """Create a temporary directory and configure noteworthy."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target_path = pathlib.Path(self.temp_dir.name)
        noteworthy._target_path = self.target_path
        noteworthy._verbose = False

    def tearDown(self):
        """Clean up the temporary directory."""
        self.temp_dir.cleanup()
        noteworthy._target_path = None

    def make_account(self, name="iCloud", obj_id="account-1"):
        """Create a test Account."""
        return Account(name, obj_id, None)

    def make_folder(self, name, obj_id, parent=None, is_smart=False):
        """Create a test Folder, optionally adding it to a parent."""
        folder = Folder(name, obj_id, None)
        if is_smart:
            folder._is_smart_folder = True
        if parent:
            parent.add_folder(folder)
        return folder

    def make_note(self, name, obj_id, folder, uuid=None, modification_date=None):
        """Create a test Note and add it to a folder."""
        mod_date = modification_date or datetime(2024, 1, 20, 14, 45)
        note = Note(name, obj_id, None, datetime(2024, 1, 15, 10, 30), mod_date, uuid=uuid)
        folder.add_note(note)
        return note
