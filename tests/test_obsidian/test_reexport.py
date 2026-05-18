#!/usr/bin/env python
"""Stage 5 re-export semantics — see obsidian_requirements.md §11.2.

Each test runs the exporter twice against the same target directory, mutating
the source between runs to model what happens when a user changes notes in
Apple Notes and re-exports. The expected behavior is:

  * Renames in Apple Notes → rename the .md file, add the previous name to
    `aliases` so old wikilinks still resolve.
  * Moves between folders → relocate the .md file. Wikilinks are path-less
    so they survive automatically.
  * User-added frontmatter keys (anything outside the §7 schema) → preserved
    when we rewrite the file.
  * Notes whose UUID is no longer present in Apple Notes → leave the existing
    .md file alone (no --prune in v1).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from notestore_factory import build_note_protobuf, create_test_db
from noteworthy.obsidian import sync


# ---------- helpers ----------


def _build_db(tmp_path: Path, notes: list[dict], db_name: str = "NoteStore.sqlite") -> Path:
    """Build a one-account DB containing the given notes.

    `notes` is a list of dicts with keys:
      pk, title, folder_pk, identifier, creation_ts, mod_ts (optional),
      body_parts (optional — list passed to build_note_protobuf)

    The caller is responsible for creating any extra folders beyond the
    default "Notes" (pk=10).
    """
    db_path = tmp_path / db_name
    b = create_test_db(db_path)
    b.add_account(pk=1, name="iCloud")
    b.add_folder(pk=10, title="Notes", account_pk=1,
                 identifier="folder-notes", sort_order=1, folder_type=0)

    extra_folders = set()
    for n in notes:
        if n["folder_pk"] != 10 and n["folder_pk"] not in extra_folders:
            b.add_folder(pk=n["folder_pk"], title=n.get("folder_title", f"Folder {n['folder_pk']}"),
                         account_pk=1, identifier=f"folder-{n['folder_pk']}",
                         sort_order=2, folder_type=0)
            extra_folders.add(n["folder_pk"])

    for n in notes:
        b.add_note(pk=n["pk"], title=n["title"], folder_pk=n["folder_pk"],
                   identifier=n["identifier"],
                   creation_ts=n.get("creation_ts", 700000000.0),
                   mod_ts=n.get("mod_ts", 700000000.0))
        b.add_note_data(note_pk=n["pk"],
                        data=build_note_protobuf(n.get("body_parts", [])))
    b.build()
    return db_path


def _frontmatter(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    return m.group(1) if m else ""


# ---------- rename detection ----------


class TestRename:
    """When a note's title changes in Apple Notes, the on-disk .md file is
    renamed and the previous name lands in `aliases` so wikilinks survive."""

    def test_rename_moves_file_and_adds_alias(self, tmp_path):
        target = tmp_path / "vault"
        target.mkdir()

        # First run: note titled "Original".
        db1 = _build_db(tmp_path, [{
            "pk": 100, "title": "Original", "folder_pk": 10, "identifier": "uuid-100",
        }])
        sync.run(target, db_path=db1)
        assert (target / "Notes" / "Original.md").is_file()

        # Second run: same UUID, new title "Renamed".
        db2 = _build_db(tmp_path, [{
            "pk": 100, "title": "Renamed", "folder_pk": 10, "identifier": "uuid-100",
        }], db_name="NoteStore2.sqlite")
        sync.run(target, db_path=db2)

        assert (target / "Notes" / "Renamed.md").is_file()
        assert not (target / "Notes" / "Original.md").exists()
        fm = _frontmatter((target / "Notes" / "Renamed.md").read_text(encoding="utf-8"))
        assert "aliases:" in fm
        assert "- Original" in fm


# ---------- move detection ----------


class TestMove:
    """When a note's folder changes in Apple Notes, the .md file relocates.
    Wikilinks to/from it keep working because they're path-less.
    """

    def test_move_relocates_file(self, tmp_path):
        target = tmp_path / "vault"
        target.mkdir()

        # First run: note in "Notes".
        db1 = _build_db(tmp_path, [{
            "pk": 100, "title": "Wanderer", "folder_pk": 10, "identifier": "uuid-100",
        }])
        sync.run(target, db_path=db1)
        assert (target / "Notes" / "Wanderer.md").is_file()

        # Second run: same UUID, now in "Work" (a new folder).
        db2 = _build_db(tmp_path, [{
            "pk": 100, "title": "Wanderer", "folder_pk": 20, "folder_title": "Work",
            "identifier": "uuid-100",
        }], db_name="NoteStore2.sqlite")
        sync.run(target, db_path=db2)

        assert (target / "Work" / "Wanderer.md").is_file()
        assert not (target / "Notes" / "Wanderer.md").exists()

    def test_wikilink_to_moved_note_resolves(self, tmp_path):
        target = tmp_path / "vault"
        target.mkdir()

        # Two notes, B links to A. Both start in "Notes". We build the DB
        # directly because _build_db can't add the link attachment.
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-notes", sort_order=1, folder_type=0)
        b.add_note(pk=100, title="Target", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        b.add_note(pk=101, title="Linker", folder_pk=10, identifier="uuid-101",
                   creation_ts=700000001.0, mod_ts=700000001.0)
        b.add_note_data(note_pk=100, data=build_note_protobuf([]))
        b.add_note_data(note_pk=101, data=build_note_protobuf([
            "See ",
            ("link-uuid-100", "com.apple.notes.inlinetextattachment.link"),
        ]))
        b.add_link_attachment(pk=450, identifier="link-uuid-100",
                              alt_text="Target",
                              token_content_identifier="applenotes:note/uuid-100",
                              note_pk=101)
        b.build()
        sync.run(target, db_path=db_path)

        # Now move Target to Work in a fresh DB.
        db_path2 = tmp_path / "NoteStore2.sqlite"
        b = create_test_db(db_path2)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-notes", sort_order=1, folder_type=0)
        b.add_folder(pk=20, title="Work", account_pk=1,
                     identifier="folder-work", sort_order=2, folder_type=0)
        b.add_note(pk=100, title="Target", folder_pk=20, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        b.add_note(pk=101, title="Linker", folder_pk=10, identifier="uuid-101",
                   creation_ts=700000001.0, mod_ts=700000001.0)
        b.add_note_data(note_pk=100, data=build_note_protobuf([]))
        b.add_note_data(note_pk=101, data=build_note_protobuf([
            "See ",
            ("link-uuid-100", "com.apple.notes.inlinetextattachment.link"),
        ]))
        b.add_link_attachment(pk=450, identifier="link-uuid-100",
                              alt_text="Target",
                              token_content_identifier="applenotes:note/uuid-100",
                              note_pk=101)
        b.build()
        sync.run(target, db_path=db_path2)

        # Target moved.
        assert (target / "Work" / "Target.md").is_file()
        assert not (target / "Notes" / "Target.md").exists()
        # Linker still has a path-less wikilink to Target.
        body = (target / "Notes" / "Linker.md").read_text(encoding="utf-8")
        assert "[[Target]]" in body


# ---------- user-added frontmatter preservation ----------


class TestUserExtras:
    """A user who opens the vault in Obsidian may add their own frontmatter
    keys (priority, project, custom tags from plugins, etc.). Re-export must
    leave those keys in place, only rewriting the keys defined by §7."""

    def test_user_key_preserved(self, tmp_path):
        target = tmp_path / "vault"
        target.mkdir()

        db = _build_db(tmp_path, [{
            "pk": 100, "title": "Project Note", "folder_pk": 10, "identifier": "uuid-100",
        }])
        sync.run(target, db_path=db)

        # User edits frontmatter to add a custom key.
        md = target / "Notes" / "Project Note.md"
        text = md.read_text(encoding="utf-8")
        # Inject `priority: high` just before the closing ---.
        text = text.replace("---\n\n", "priority: high\nstatus: active\n---\n\n", 1)
        # Some bodies don't have the blank line; fall back to a robust splice.
        if "priority: high" not in text:
            text = re.sub(r"\n---\n", "\npriority: high\nstatus: active\n---\n", text, count=1)
        md.write_text(text, encoding="utf-8")
        assert "priority: high" in md.read_text(encoding="utf-8")

        # Re-export; the custom keys must survive.
        sync.run(target, db_path=db)
        out = md.read_text(encoding="utf-8")
        fm = _frontmatter(out)
        assert "priority: high" in fm
        assert "status: active" in fm
        # Owned keys still present.
        assert "apple_notes_uuid: uuid-100" in fm


# ---------- orphans (UUID gone) ----------


class TestOrphanedNotePreserved:
    """If a note's UUID was previously exported but isn't in the source DB
    anymore (deleted in Apple Notes, or the user is exporting a different
    subset), leave the .md alone. The user may have edited it."""

    def test_missing_uuid_leaves_file_in_place(self, tmp_path):
        target = tmp_path / "vault"
        target.mkdir()

        # First run with two notes.
        db1 = _build_db(tmp_path, [
            {"pk": 100, "title": "Keeper", "folder_pk": 10, "identifier": "uuid-100"},
            {"pk": 101, "title": "Goner", "folder_pk": 10, "identifier": "uuid-101"},
        ])
        sync.run(target, db_path=db1)
        keeper = target / "Notes" / "Keeper.md"
        goner = target / "Notes" / "Goner.md"
        assert keeper.is_file()
        assert goner.is_file()
        goner_bytes_before = goner.read_bytes()

        # Second run: only Keeper is present in the source DB.
        db2 = _build_db(tmp_path, [
            {"pk": 100, "title": "Keeper", "folder_pk": 10, "identifier": "uuid-100"},
        ], db_name="NoteStore2.sqlite")
        sync.run(target, db_path=db2)

        # Goner is still on disk, byte-for-byte unchanged.
        assert goner.is_file()
        assert goner.read_bytes() == goner_bytes_before
