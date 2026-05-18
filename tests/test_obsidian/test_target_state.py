#!/usr/bin/env python
"""Tests for obsidian.target_state — classifying a target directory before export."""

from __future__ import annotations

import json
import pytest

from noteworthy.obsidian.target_state import TargetState, inspect


class TestInspectMissingOrEmpty:
    def test_missing_directory_is_empty(self, tmp_path):
        target = tmp_path / "does_not_exist_yet"
        assert inspect(target) == TargetState.EMPTY

    def test_empty_directory_is_empty(self, tmp_path):
        assert inspect(tmp_path) == TargetState.EMPTY


class TestInspectObsidianVault:
    def test_obsidian_dir_alone(self, tmp_path):
        (tmp_path / ".obsidian").mkdir()
        assert inspect(tmp_path) == TargetState.OBSIDIAN

    def test_obsidian_dir_with_notes_and_assets(self, tmp_path):
        (tmp_path / ".obsidian").mkdir()
        (tmp_path / ".obsidian" / "app.json").write_text("{}")
        (tmp_path / "assets").mkdir()
        (tmp_path / "Note.md").write_text("---\napple_notes_uuid: abc\n---\nbody\n")
        assert inspect(tmp_path) == TargetState.OBSIDIAN

    def test_obsidian_wins_over_noteworthy_json(self, tmp_path):
        """If both signals are present (mid-migration or odd hybrid), Obsidian wins.

        The .obsidian directory is the more recent, intentional signal.
        """
        (tmp_path / ".obsidian").mkdir()
        (tmp_path / ".noteworthy.json").write_text("{}")
        assert inspect(tmp_path) == TargetState.OBSIDIAN


class TestInspectBackupExport:
    def test_root_noteworthy_json(self, tmp_path):
        (tmp_path / ".noteworthy.json").write_text(json.dumps({"type": "root"}))
        assert inspect(tmp_path) == TargetState.BACKUP

    def test_nested_noteworthy_json(self, tmp_path):
        """A backup export has .noteworthy.json files at account/folder/note levels.

        Detection must find them even when only nested ones exist (e.g., a user
        deleted the root one but left the rest).
        """
        nested = tmp_path / "iCloud" / "Work" / "MyNote"
        nested.mkdir(parents=True)
        (nested / ".noteworthy.json").write_text(json.dumps({"type": "note"}))
        assert inspect(tmp_path) == TargetState.BACKUP

    def test_account_dir_with_metadata(self, tmp_path):
        """Typical backup layout: account dir with its own metadata file."""
        account_dir = tmp_path / "iCloud"
        account_dir.mkdir()
        (account_dir / ".noteworthy.json").write_text(json.dumps({"type": "account"}))
        assert inspect(tmp_path) == TargetState.BACKUP


class TestInspectUnrelated:
    def test_non_empty_directory_without_signals(self, tmp_path):
        (tmp_path / "random.txt").write_text("hello")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "other.md").write_text("# Just a markdown file")
        assert inspect(tmp_path) == TargetState.UNRELATED

    def test_lone_markdown_file_is_unrelated(self, tmp_path):
        """A user's notes folder full of .md files but with no .noteworthy.json
        or .obsidian/ is not something we can safely write into."""
        (tmp_path / "ideas.md").write_text("# Ideas\n")
        assert inspect(tmp_path) == TargetState.UNRELATED


class TestInspectFileTarget:
    def test_target_is_a_file_raises(self, tmp_path):
        """A target path that points to a file (not a directory) is a user error
        — surface it explicitly rather than silently treating it as EMPTY."""
        target = tmp_path / "somefile.txt"
        target.write_text("oops")
        with pytest.raises(ValueError):
            inspect(target)
