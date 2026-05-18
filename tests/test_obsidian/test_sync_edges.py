#!/usr/bin/env python
"""Edge-case + bug-fix tests for the Obsidian sync orchestrator.

The main happy-path coverage lives in `test_sync.py`. This module exists for
narrowly-scoped scenarios — each test builds the smallest possible custom DB
that exercises the behavior under test, instead of leaning on the shared
`notestore_db` fixture. That keeps these tests independent of each other and
of any future evolution of the multi-account demo fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from notestore_factory import build_note_protobuf, create_test_db
from noteworthy.obsidian import sync


# ---------- shared helpers ----------


def _single_note_db(tmp_path: Path, *, title: str, note_pk: int = 100,
                    identifier: str = "uuid-100", protobuf_parts=None,
                    creation_ts: float = 700000000.0,
                    mod_ts: float = 700000000.0) -> Path:
    """Build a one-account, one-folder, one-note DB suitable for sync.run.

    Returns the path to the SQLite file. `protobuf_parts` follows the
    `build_note_protobuf` schema; defaults to an empty body.
    """
    db_path = tmp_path / "NoteStore.sqlite"
    b = create_test_db(db_path)
    b.add_account(pk=1, name="iCloud")
    b.add_folder(pk=10, title="Notes", account_pk=1,
                 identifier="folder-10", sort_order=1, folder_type=0)
    b.add_note(pk=note_pk, title=title, folder_pk=10, identifier=identifier,
               creation_ts=creation_ts, mod_ts=mod_ts)
    b.add_note_data(note_pk=note_pk, data=build_note_protobuf(protobuf_parts or []))
    b.build()
    return db_path


def _frontmatter(text: str) -> str:
    """Pull the YAML block between the leading and second `---` lines."""
    import re
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    return m.group(1) if m else ""


# ---------- spec §5.1: aliases ----------


class TestAliasesPopulated:
    """When sanitization or disambiguation changes the on-disk filename, the
    original display name must end up in the `aliases` frontmatter list so the
    user can still find the note by typing its real name in Obsidian's quick
    switcher. Per requirements §5.1: 'Always record the original ... name'."""

    def test_forbidden_char_aliased(self, tmp_path):
        # `Plan #1` -> `Plan ＃1.md`; the original must survive as an alias.
        db = _single_note_db(tmp_path, title="Plan #1")
        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db)

        md = target / "Notes" / "Plan ＃1.md"
        assert md.is_file(), f"expected sanitized filename; got {list(target.iterdir())}"
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        assert "aliases:" in fm
        # YAML quotes Plan #1 because `#` would otherwise start a YAML comment.
        # The important bit: the original (non-fullwidth) `#` survives.
        assert '"Plan #1"' in fm

    def test_unchanged_name_no_aliases_block(self, tmp_path):
        """Notes whose display name already equals the on-disk filename get no aliases block."""
        db = _single_note_db(tmp_path, title="My Plan")
        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db)

        md = target / "Notes" / "My Plan.md"
        assert md.is_file()
        fm = _frontmatter(md.read_text(encoding="utf-8"))
        assert "aliases:" not in fm


# ---------- §11.2: every written note has a usable apple_notes_uuid ----------


class TestNotesWithoutUuid:
    """A note with no UUID has nothing to round-trip on re-export; rendering
    `apple_notes_uuid: null` would defeat §11.2. Skip with a warning instead."""

    def test_note_without_uuid_skipped(self, tmp_path, capsys):
        db = _single_note_db(tmp_path, title="Anonymous", identifier=None)
        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db)

        assert not (target / "Notes" / "Anonymous.md").exists()
        err = capsys.readouterr().err
        assert "Anonymous" in err
        assert "uuid" in err.lower()


# ---------- robustness: None creation_date in the sort key ----------


class TestNoneCreationDate:
    """A single note with a NULL creation_date should not crash the entire
    export. Sort by (creation_date, id) needs to tolerate None.
    """

    def test_none_creation_date_does_not_crash(self, tmp_path):
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-10", sort_order=1, folder_type=0)
        # Two notes, one missing creation_ts (passed as None -> NULL in DB).
        b.add_note(pk=100, title="Has Date", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        b.add_note(pk=101, title="No Date", folder_pk=10, identifier="uuid-101",
                   creation_ts=None, mod_ts=None)
        b.add_note_data(note_pk=100, data=build_note_protobuf([]))
        b.add_note_data(note_pk=101, data=build_note_protobuf([]))
        b.build()

        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db_path)  # must not raise

        assert (target / "Notes" / "Has Date.md").is_file()
        assert (target / "Notes" / "No Date.md").is_file()


# ---------- §6.1: title-line stripping respects attachment-first bodies ----------


class TestStripTitleWithLeadingAttachment:
    """If the first non-empty content block is an attachment (image), the spec
    says 'leave the body alone'. The current code skips attachments looking for
    the next text block, which can silently delete legitimate body text.
    """

    def test_leading_image_then_title_text_preserved(self, tmp_path):
        # Note named "My Plan" with body: [image, "My Plan"]
        # The text "My Plan" must remain because the first non-empty block is
        # the attachment, not the title text.
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-10", sort_order=1, folder_type=0)
        b.add_note(pk=100, title="My Plan", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        # Image first, then the title text.
        b.add_note_data(note_pk=100, data=build_note_protobuf([
            ("att-uuid-img", "public.jpeg"),
            "My Plan\n",
        ]))
        b.add_media(pk=300, identifier="att-uuid-img-media", filename="photo.jpg",
                    generation=None)
        b.add_attachment(pk=400, identifier="att-uuid-img", type_uti="public.jpeg",
                         title="Photo", media_pk=300, note_pk=100)
        media_dir = tmp_path / "Media" / "att-uuid-img-media"
        media_dir.mkdir(parents=True)
        (media_dir / "photo.jpg").write_bytes(b"jpg")
        b.build()

        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db_path)

        body = (target / "Notes" / "My Plan.md").read_text(encoding="utf-8")
        # The bare 'My Plan' line must still be in the body.
        assert "My Plan" in body.split("---\n", 2)[-1]


# ---------- §10: locked-note warning includes the account name ----------


class TestSkippedNoteWarningFormat:
    """Per §10 the warning must include the account name so the user knows
    where to look in Apple Notes."""

    def test_warning_includes_account(self, tmp_path, capsys):
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-10", sort_order=1, folder_type=0)
        # Note row but NO ZICNOTEDATA -> get_note_data returns nothing, triggers warning.
        b.add_note(pk=100, title="Locked Item", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        b.build()

        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db_path)

        err = capsys.readouterr().err
        assert "Locked Item" in err
        assert "iCloud" in err  # spec-required account context


# ---------- gallery: forbidden chars in child titles get fullwidth in wikilinks ----------


class TestGalleryWikilinkSanitization:
    """A gallery child whose title contains an Obsidian-forbidden character
    (`#`, `|`, `^`, `[`, `]`) must end up in the wikilink with a fullwidth
    look-alike, not the raw forbidden character.

    The bug path: when child.unique_filename is unset (e.g. the child has no
    file_path so it's excluded from the vault-wide naming pass), the renderer
    falls back to a backup-mode sanitization that doesn't know about Obsidian's
    extra forbidden set.
    """

    def test_child_with_hash_in_title(self, tmp_path):
        from noteworthy.markdown_renderer import MarkdownGenerator, AttachmentResolver
        from noteworthy.note_content import Attachment, ContentBlock
        from noteworthy.database import DatabaseNoteDataLoader
        from noteworthy.obsidian.dialect import ExportDialect

        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.build()

        # Gallery with a child that has no file_path (so unique_filename won't
        # be set by the vault-wide pass) but does have a forbidden title.
        child = Attachment(uuid="child-uuid", type="public.jpeg",
                           file_path=None, title="My #photo")
        gallery = Attachment(uuid="gallery-uuid", type="com.apple.notes.gallery",
                             gallery_children=[child])
        block = ContentBlock(type="attachment", attachment=gallery)

        loader = DatabaseNoteDataLoader(str(db_path))
        try:
            gen = MarkdownGenerator(
                AttachmentResolver(loader),
                dialect=ExportDialect.OBSIDIAN,
            )
            out = gen.generate([block])
        finally:
            loader.close()

        # Forbidden '#' must be fullwidth in the wikilink.
        assert "[[" in out
        assert "#" not in out.split("[[", 1)[1].split("]]", 1)[0], \
            f"raw # leaked into wikilink: {out!r}"


# ---------- coverage: missing attachment source file ----------


class TestMissingAttachmentFile:
    """When an attachment row exists but its backing media file doesn't, the
    note itself must still export cleanly. The data-loader layer drops the
    file_path; the Obsidian sync code must tolerate that without crashing and
    must not leave a half-written vault.
    """

    def test_missing_file_does_not_crash(self, tmp_path, capsys):
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-10", sort_order=1, folder_type=0)
        b.add_note(pk=100, title="Has Attachment", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        b.add_note_data(note_pk=100, data=build_note_protobuf([
            ("att-uuid-missing", "public.jpeg"),
        ]))
        # Register the attachment + media row but DON'T create the file on disk.
        b.add_media(pk=300, identifier="missing-media", filename="ghost.jpg",
                    generation=None)
        b.add_attachment(pk=400, identifier="att-uuid-missing", type_uti="public.jpeg",
                         title="Ghost", media_pk=300, note_pk=100)
        b.build()

        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db_path)  # must not raise

        # The note itself is still written.
        assert (target / "Notes" / "Has Attachment.md").is_file()
        # No ghost file copied.
        assert not (target / "assets" / "ghost.jpg").exists()


# ---------- coverage: unicode in note name end-to-end ----------


class TestUnicodeNames:
    """Filenames with non-ASCII characters must round-trip cleanly: the file
    appears on disk under its sanitized name and an inter-note wikilink to it
    resolves to the right target.
    """

    def test_resume_filename_and_link(self, tmp_path):
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-10", sort_order=1, folder_type=0)
        b.add_note(pk=100, title="café — résumé", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        b.add_note(pk=101, title="Linker", folder_pk=10, identifier="uuid-101",
                   creation_ts=700000001.0, mod_ts=700000001.0)
        b.add_note_data(note_pk=100, data=build_note_protobuf([]))
        b.add_note_data(note_pk=101, data=build_note_protobuf([
            "see ",
            ("link-uuid-100", "com.apple.notes.inlinetextattachment.link"),
        ]))
        b.add_link_attachment(pk=450, identifier="link-uuid-100",
                              alt_text="café — résumé",
                              token_content_identifier="applenotes:note/uuid-100",
                              note_pk=101)
        b.build()

        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db_path)

        assert (target / "Notes" / "café — résumé.md").is_file()
        linker_body = (target / "Notes" / "Linker.md").read_text(encoding="utf-8")
        assert "[[café — résumé]]" in linker_body


# ---------- coverage: single-account vault whose only note is locked ----------


class TestAllNotesSkipped:
    """A vault whose only renderable note is locked-and-skipped should still
    succeed: writes app.json, no .md files, no account dirs."""

    def test_all_locked_produces_empty_vault(self, tmp_path, capsys):
        db_path = tmp_path / "NoteStore.sqlite"
        b = create_test_db(db_path)
        b.add_account(pk=1, name="iCloud")
        b.add_folder(pk=10, title="Notes", account_pk=1,
                     identifier="folder-10", sort_order=1, folder_type=0)
        b.add_note(pk=100, title="Locked", folder_pk=10, identifier="uuid-100",
                   creation_ts=700000000.0, mod_ts=700000000.0)
        # No add_note_data -> get_note_data returns nothing -> warn & skip.
        b.build()

        target = tmp_path / "vault"
        target.mkdir()
        sync.run(target, db_path=db_path)

        assert (target / ".obsidian" / "app.json").is_file()
        assert list(target.rglob("*.md")) == []
