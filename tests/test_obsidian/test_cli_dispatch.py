#!/usr/bin/env python
"""Tests for the CLI mode dispatcher in noteworthy.noteworthy.

Covers:
  - The --obsidian flag parses
  - Mode/state mismatches refuse with the expected stderr message
  - Compatible mode/state combinations dispatch to the right backend
"""

from __future__ import annotations

import json
import pathlib
import pytest

from noteworthy import noteworthy


def _make_obsidian_vault(target: pathlib.Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / ".obsidian").mkdir()
    (target / ".obsidian" / "app.json").write_text("{}")


def _make_backup_export(target: pathlib.Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    (target / ".noteworthy.json").write_text(json.dumps({"type": "root"}))


class _DispatchSpy:
    """Captures which sync function the dispatcher chose without executing it."""

    def __init__(self, monkeypatch):
        self.backup_called = False
        self.obsidian_called = False
        self.obsidian_target: pathlib.Path | None = None
        monkeypatch.setattr(noteworthy, "_make_backup_copies", self._backup)
        # Patch the obsidian sync entry point on the noteworthy module so the
        # dispatcher's reference is intercepted, not the underlying module.
        from noteworthy.obsidian import sync as obsidian_sync
        monkeypatch.setattr(obsidian_sync, "run", self._obsidian)

    def _backup(self):
        self.backup_called = True

    def _obsidian(self, target_path, db_path=None, verbose=False):
        self.obsidian_called = True
        self.obsidian_target = target_path


@pytest.fixture
def spy(monkeypatch):
    return _DispatchSpy(monkeypatch)


class TestObsidianFlagParses:
    def test_flag_recognized_by_argparse(self, tmp_path):
        # If --obsidian were unknown, _parse_args would SystemExit(2).
        parsed = noteworthy._parse_args([str(tmp_path), "--obsidian"])
        assert parsed.obsidian is True

    def test_flag_default_is_false(self, tmp_path):
        parsed = noteworthy._parse_args([str(tmp_path)])
        assert parsed.obsidian is False


class TestModeStateCompatible:
    def test_obsidian_flag_on_empty_dir_dispatches_to_obsidian(self, spy, tmp_path):
        noteworthy.make_copies([str(tmp_path), "--obsidian"])
        assert spy.obsidian_called is True
        assert spy.backup_called is False
        assert spy.obsidian_target == tmp_path

    def test_obsidian_flag_on_existing_obsidian_vault_dispatches_to_obsidian(self, spy, tmp_path):
        _make_obsidian_vault(tmp_path)
        noteworthy.make_copies([str(tmp_path), "--obsidian"])
        assert spy.obsidian_called is True
        assert spy.backup_called is False

    def test_no_flag_on_empty_dir_dispatches_to_backup(self, spy, tmp_path):
        noteworthy.make_copies([str(tmp_path)])
        assert spy.backup_called is True
        assert spy.obsidian_called is False

    def test_no_flag_on_existing_backup_dispatches_to_backup(self, spy, tmp_path):
        _make_backup_export(tmp_path)
        noteworthy.make_copies([str(tmp_path)])
        assert spy.backup_called is True
        assert spy.obsidian_called is False


class TestModeStateMismatch:
    def test_obsidian_vault_without_flag_refuses(self, spy, tmp_path, capsys):
        _make_obsidian_vault(tmp_path)
        with pytest.raises(SystemExit) as exc:
            noteworthy.make_copies([str(tmp_path)])
        assert exc.value.code != 0
        captured = capsys.readouterr()
        assert "Obsidian vault" in captured.err
        assert "--obsidian" in captured.err
        assert spy.backup_called is False
        assert spy.obsidian_called is False

    def test_backup_export_with_obsidian_flag_refuses(self, spy, tmp_path, capsys):
        _make_backup_export(tmp_path)
        with pytest.raises(SystemExit) as exc:
            noteworthy.make_copies([str(tmp_path), "--obsidian"])
        assert exc.value.code != 0
        captured = capsys.readouterr()
        assert "backup" in captured.err.lower()
        assert spy.backup_called is False
        assert spy.obsidian_called is False

    def test_unrelated_directory_refuses_in_either_mode(self, spy, tmp_path, capsys):
        (tmp_path / "random.txt").write_text("hello")

        with pytest.raises(SystemExit):
            noteworthy.make_copies([str(tmp_path), "--obsidian"])
        err_obs = capsys.readouterr().err
        assert err_obs  # some error message present

        with pytest.raises(SystemExit):
            noteworthy.make_copies([str(tmp_path)])
        err_backup = capsys.readouterr().err
        assert err_backup

        assert spy.backup_called is False
        assert spy.obsidian_called is False


class TestObsidianSyncModule:
    """The sync module exposes a `run` entry point."""

    def test_obsidian_sync_module_importable(self):
        from noteworthy.obsidian import sync  # noqa: F401

    def test_obsidian_sync_run_attribute_callable(self):
        from noteworthy.obsidian import sync
        assert callable(sync.run)
