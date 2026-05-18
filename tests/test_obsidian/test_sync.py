#!/usr/bin/env python
"""End-to-end tests for obsidian.sync.run against the conftest DB fixture.

These exercise the whole pipeline: read Apple Notes -> filter/sanitize -> render ->
write vault. The conftest `notestore_db` fixture builds a representative test DB
with two accounts, nested folders, a smart folder, attachments, and an inter-note
link, so a single fixture covers the bulk of layout and content behavior.
"""

from __future__ import annotations

import json
import re
import pytest

from noteworthy.obsidian import sync


# ---------- shared fixtures ----------


@pytest.fixture
def vault(tmp_path, notestore_db):
    """Run obsidian.sync.run against the multi-account fixture DB and return the vault path."""
    target = tmp_path / "vault"
    target.mkdir()
    sync.run(target, db_path=notestore_db)
    return target


def _note_files(root):
    """Iterate every .md file under root (recursively)."""
    return [p for p in root.rglob("*.md")]


def _frontmatter(text):
    """Extract the YAML frontmatter block from a note's text. Returns None if absent."""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    return m.group(1) if m else None


def _frontmatter_value(fm_block: str, key: str) -> str | None:
    """Cheap one-line YAML value lookup. Lists return None (use the raw block for those)."""
    for line in fm_block.splitlines():
        if line.startswith(f"{key}: "):
            return line[len(key) + 2:]
    return None


# ---------- layout ----------


class TestMultiAccountLayout:
    """The fixture has two accounts (iCloud + On My Mac) so the multi-account layout
    is used. Each account's *renderable* notes get nested under their account dir.

    (On My Mac's only note has no protobuf data in the fixture so it's warned
    about and skipped — meaning its dir doesn't physically exist, which is fine:
    the layout decision is about *where notes go*, not about creating empty dirs.)
    """

    def test_icloud_dir_used_as_account_wrapper(self, vault):
        # Multi-account mode nests folders under <Account>/ — iCloud's "Notes"
        # folder ends up at iCloud/Notes/, not at the vault root.
        assert (vault / "iCloud" / "Notes").is_dir()
        assert not (vault / "Notes").is_dir()

    def test_folder_hierarchy_preserved(self, vault):
        # Personal is a subfolder of Work in the fixture.
        assert (vault / "iCloud" / "Work" / "Personal").is_dir()

    def test_notes_are_flat_md_files_not_directories(self, vault):
        """Per §4, notes are .md files, not directories holding .md files."""
        for md in _note_files(vault):
            assert md.is_file()
            assert md.suffix == ".md"

    def test_first_note_at_expected_path(self, vault):
        # iCloud/Notes/First Note.md (no per-note dir wrapper)
        assert (vault / "iCloud" / "Notes" / "First Note.md").is_file()

    def test_personal_subfolder_note(self, vault):
        assert (vault / "iCloud" / "Work" / "Personal" / "Personal Stuff.md").is_file()


class TestSingleAccountLayout:
    """When the source has a single account, no account-level wrapper is used."""

    def test_folders_at_vault_root(self, tmp_path, notestore_db):
        # Use the same DB but only sync the iCloud account by stripping On My Mac
        # at the data layer — sync.run can be parameterized later for this; here
        # we monkey the extract layer.
        from noteworthy import obsidian as obsidian_pkg
        from noteworthy.obsidian import sync as sync_mod
        from noteworthy.extract_notes_db import extract_folders_and_notes

        target = tmp_path / "vault_single"
        target.mkdir()

        orig_extract = extract_folders_and_notes

        def single_account_extract(db_path=None):
            accounts = orig_extract(db_path=db_path)
            return [a for a in accounts if a.name == "iCloud"]

        original = sync_mod.extract_folders_and_notes
        sync_mod.extract_folders_and_notes = single_account_extract
        try:
            sync_mod.run(target, db_path=notestore_db)
        finally:
            sync_mod.extract_folders_and_notes = original

        # With a single account, folders sit at the vault root, no <Account>/ dir.
        assert (target / "Notes").is_dir()
        assert (target / "Work").is_dir()
        assert not (target / "iCloud").exists()


# ---------- excluded content ----------


class TestExcludedContent:
    def test_smart_folder_directory_absent(self, vault):
        """The Bookmarks smart folder must not appear as a real directory."""
        assert not (vault / "iCloud" / "Bookmarks").exists()

    def test_no_noteworthy_json_files_anywhere(self, vault):
        """Obsidian mode replaces .noteworthy.json with frontmatter."""
        assert list(vault.rglob(".noteworthy.json")) == []

    def test_no_deleted_directory(self, vault):
        assert not (vault / "Deleted").exists()


# ---------- attachments / assets ----------


class TestAssetsDirectory:
    def test_assets_dir_created(self, vault):
        assert (vault / "assets").is_dir()

    def test_image_attachment_copied(self, vault):
        # Filenames mirror the underlying media file (photo.jpg / doc.pdf in the
        # fixture), not the user-facing title — per requirements §4 / §5.2.
        assert (vault / "assets" / "photo.jpg").is_file()

    def test_pdf_attachment_copied(self, vault):
        assert (vault / "assets" / "doc.pdf").is_file()


# ---------- frontmatter ----------


class TestFrontmatter:
    def test_every_note_has_frontmatter(self, vault):
        notes = _note_files(vault)
        assert notes, "fixture should have produced at least one note"
        for md in notes:
            fm = _frontmatter(md.read_text(encoding="utf-8"))
            assert fm is not None, f"{md} missing frontmatter"

    def test_required_keys_present(self, vault):
        for md in _note_files(vault):
            fm = _frontmatter(md.read_text(encoding="utf-8"))
            for key in ("created:", "modified:", "account:", "apple_notes_uuid:"):
                assert key in fm, f"{md} missing {key}"

    def test_account_matches_path(self, vault):
        # iCloud-rooted notes have account: iCloud; On My Mac similar.
        first = (vault / "iCloud" / "Notes" / "First Note.md").read_text()
        fm = _frontmatter(first)
        assert _frontmatter_value(fm, "account") == "iCloud"

    def test_folder_path_in_frontmatter(self, vault):
        nested = (vault / "iCloud" / "Work" / "Personal" / "Personal Stuff.md").read_text()
        fm = _frontmatter(nested)
        assert _frontmatter_value(fm, "folder") == "Work/Personal"

    def test_apple_notes_uuid_round_trips(self, vault):
        first = (vault / "iCloud" / "Notes" / "First Note.md").read_text()
        fm = _frontmatter(first)
        # Fixture sets identifier 'aaa-bbb-ccc-100' for First Note.
        assert _frontmatter_value(fm, "apple_notes_uuid") == "aaa-bbb-ccc-100"


# ---------- wikilinks + embeds in body ----------


class TestBodyContent:
    def test_inter_note_link_uses_wikilink(self, vault):
        """Note 100 has a link to note 101 ("Second Note") via token_content_identifier."""
        body = (vault / "iCloud" / "Notes" / "First Note.md").read_text()
        assert "[[Second Note]]" in body

    def test_image_uses_embed_syntax(self, vault):
        body = (vault / "iCloud" / "Notes" / "First Note.md").read_text()
        assert "![[photo.jpg]]" in body

    def test_pdf_uses_non_embedding_link(self, vault):
        body = (vault / "iCloud" / "Notes" / "First Note.md").read_text()
        assert "[[doc.pdf]]" in body
        # Must NOT be embedded.
        assert "![[doc.pdf]]" not in body

    def test_no_attachments_path_in_body(self, vault):
        """Backup-mode `Attachments/...` paths must never appear in Obsidian output."""
        for md in _note_files(vault):
            assert "Attachments/" not in md.read_text(encoding="utf-8")


# ---------- .obsidian/app.json ----------


class TestObsidianConfig:
    def test_app_json_written(self, vault):
        assert (vault / ".obsidian" / "app.json").is_file()

    def test_app_json_attachment_folder_path(self, vault):
        cfg = json.loads((vault / ".obsidian" / "app.json").read_text())
        assert cfg["attachmentFolderPath"] == "assets"


# ---------- idempotence ----------


class TestIdempotence:
    def test_second_run_produces_no_changes(self, tmp_path, notestore_db):
        """Running the exporter twice in a row should produce byte-identical output."""
        target = tmp_path / "vault"
        target.mkdir()

        sync.run(target, db_path=notestore_db)
        first_snapshot = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}

        sync.run(target, db_path=notestore_db)
        second_snapshot = {p: p.read_bytes() for p in target.rglob("*") if p.is_file()}

        assert set(first_snapshot) == set(second_snapshot)
        for path in first_snapshot:
            assert first_snapshot[path] == second_snapshot[path], f"{path} changed on second run"
