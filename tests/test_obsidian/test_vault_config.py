#!/usr/bin/env python
"""Tests for obsidian.vault_config — writing .obsidian/app.json on first run."""

from __future__ import annotations

import json
import pytest

from noteworthy.obsidian.vault_config import ensure_app_json


class TestFreshVault:
    def test_creates_obsidian_directory(self, tmp_path):
        ensure_app_json(tmp_path)
        assert (tmp_path / ".obsidian").is_dir()

    def test_creates_app_json(self, tmp_path):
        ensure_app_json(tmp_path)
        assert (tmp_path / ".obsidian" / "app.json").is_file()

    def test_app_json_has_attachment_folder_path(self, tmp_path):
        ensure_app_json(tmp_path)
        config = json.loads((tmp_path / ".obsidian" / "app.json").read_text())
        assert config["attachmentFolderPath"] == "assets"

    def test_app_json_uses_wikilinks(self, tmp_path):
        ensure_app_json(tmp_path)
        config = json.loads((tmp_path / ".obsidian" / "app.json").read_text())
        assert config["useMarkdownLinks"] is False

    def test_app_json_new_link_format_is_shortest(self, tmp_path):
        ensure_app_json(tmp_path)
        config = json.loads((tmp_path / ".obsidian" / "app.json").read_text())
        assert config["newLinkFormat"] == "shortest"

    def test_app_json_always_update_links(self, tmp_path):
        ensure_app_json(tmp_path)
        config = json.loads((tmp_path / ".obsidian" / "app.json").read_text())
        assert config["alwaysUpdateLinks"] is True


class TestIdempotence:
    def test_existing_app_json_not_overwritten(self, tmp_path):
        """The user may have customized their .obsidian/app.json (plugins, themes,
        layout preferences). Re-running the exporter must not clobber those."""
        (tmp_path / ".obsidian").mkdir()
        custom = {"attachmentFolderPath": "my-attachments", "theme": "dark"}
        (tmp_path / ".obsidian" / "app.json").write_text(json.dumps(custom))

        ensure_app_json(tmp_path)

        config = json.loads((tmp_path / ".obsidian" / "app.json").read_text())
        assert config == custom  # untouched

    def test_other_files_in_obsidian_dir_preserved(self, tmp_path):
        """Plugin data, workspace state, etc. live alongside app.json — never touch them."""
        obs_dir = tmp_path / ".obsidian"
        obs_dir.mkdir()
        (obs_dir / "workspace.json").write_text('{"layout": "..."}')
        (obs_dir / "plugins").mkdir()
        (obs_dir / "plugins" / "some-plugin").mkdir()

        ensure_app_json(tmp_path)

        assert (obs_dir / "workspace.json").read_text() == '{"layout": "..."}'
        assert (obs_dir / "plugins" / "some-plugin").is_dir()

    def test_obsidian_dir_exists_but_no_app_json(self, tmp_path):
        """User created an empty .obsidian/ manually — we should still write app.json."""
        (tmp_path / ".obsidian").mkdir()
        ensure_app_json(tmp_path)
        assert (tmp_path / ".obsidian" / "app.json").is_file()


class TestTargetCreation:
    def test_creates_target_if_missing(self, tmp_path):
        """The target directory itself may not exist yet (EMPTY state for a path
        that doesn't exist). ensure_app_json should create whatever's needed."""
        target = tmp_path / "new_vault"
        ensure_app_json(target)
        assert (target / ".obsidian" / "app.json").is_file()
