#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""Tests for hierarchy extraction queries against a test SQLite database."""

from noteworthy.extract_notes_db import extract_folders_and_notes, _extract_uuid_order
from notestore_factory import build_crdt_with_uuids, create_test_db


def _get_account(accounts, name):
    """Find an account by name in the list of extracted accounts."""
    return next(a for a in accounts if a.name == name)


class TestExtractAccounts:
    def test_account_count(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        assert len(accounts) == 2

    def test_account_names(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        names = sorted(a.name for a in accounts)
        assert names == ["On My Mac", "iCloud"]


class TestExtractFolders:
    def test_icloud_folder_count(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        # Top-level: Notes, Work, Bookmarks (smart). Personal is child of Work.
        top_level = icloud.folders
        assert len(top_level) == 3

    def test_nested_folder(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        work = next(f for f in icloud.folders if f.name == "Work")
        assert len(work.folders) == 1
        assert work.folders[0].name == "Personal"

    def test_display_order_sorting(self, notestore_db):
        """Folders are sorted by display_order: default first, then smart, then user folders in CRDT order."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        folder_names = [f.name for f in icloud.folders]
        # Notes (default) first, then Bookmarks (smart), then Work (CRDT-ordered user folder)
        assert folder_names == ["Notes", "Bookmarks", "Work"]

    def test_local_account_folders(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        local = _get_account(accounts, "On My Mac")
        assert len(local.folders) == 1
        assert local.folders[0].name == "Local Notes"


class TestExtractNotes:
    def test_notes_in_correct_folders(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes_folder = next(f for f in icloud.folders if f.name == "Notes")
        note_names = sorted(n.name for n in notes_folder.notes)
        assert note_names == ["First Note", "Second Note"]

    def test_null_title_excluded(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        all_notes = [n for a in accounts for n in a.all_notes()]
        # Note with pk=199 has NULL title and should be excluded
        assert all(n.name is not None for n in all_notes)
        assert len(all_notes) == 5

    def test_note_metadata(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes_folder = next(f for f in icloud.folders if f.name == "Notes")
        first_note = next(n for n in notes_folder.notes if n.name == "First Note")

        assert first_note.uuid == "aaa-bbb-ccc-100"
        assert first_note.creation_date is not None
        assert first_note.modification_date is not None
        assert "ICNote/p100" in first_note.id

    def test_coredata_uri_format(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        all_notes = [n for a in accounts for n in a.all_notes()]
        for note in all_notes:
            assert note.id.startswith("x-coredata://")
            assert "/ICNote/p" in note.id

    def test_multi_account_isolation(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        local = _get_account(accounts, "On My Mac")
        local_notes = local.all_notes()
        assert len(local_notes) == 1
        assert local_notes[0].name == "Local Note"


class TestSmartFolders:
    def test_smart_folder_detected(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        bookmarks = next(f for f in icloud.folders if f.name == "Bookmarks")
        assert bookmarks.is_smart_folder is True

    def test_smart_folder_membership(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        bookmarks = next(f for f in icloud.folders if f.name == "Bookmarks")
        # Note 102 ("Work Item") has #bookmark tag
        note_names = [n.name for n in bookmarks.notes]
        assert "Work Item" in note_names

    def test_note_tags_attached(self, notestore_db):
        """Note 102 ("Work Item") has #bookmark tag -- should appear in note.tags as lowercase."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        work_folder = next(f for f in icloud.folders if f.name == "Work")
        work_item = next(n for n in work_folder.notes if n.name == "Work Item")
        assert work_item.tags == ["bookmark"]

    def test_note_without_tags_has_empty_list(self, notestore_db):
        """Notes without hashtags have an empty tags list."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes_folder = next(f for f in icloud.folders if f.name == "Notes")
        first_note = next(n for n in notes_folder.notes if n.name == "First Note")
        assert first_note.tags == []

    def test_non_smart_folders(self, notestore_db):
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes_folder = next(f for f in icloud.folders if f.name == "Notes")
        assert notes_folder.is_smart_folder is False


class TestFolderSortOrder:
    def test_default_sort_order(self, notestore_db):
        """Folder with NULL ZCUSTOMNOTESORTTYPEVALUE gets sort_order='default'."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes_folder = next(f for f in icloud.folders if f.name == "Notes")
        assert notes_folder.sort_order == "default"

    def test_date_created_sort_order(self, notestore_db):
        """Folder with ZCUSTOMNOTESORTTYPEVALUE=20 gets sort_order='date_created'."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        work = next(f for f in icloud.folders if f.name == "Work")
        assert work.sort_order == "date_created"

    def test_title_sort_order(self, notestore_db):
        """Folder with ZCUSTOMNOTESORTTYPEVALUE=30 gets sort_order='title'."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        work = next(f for f in icloud.folders if f.name == "Work")
        personal = next(f for f in work.folders if f.name == "Personal")
        assert personal.sort_order == "title"


class TestFolderDisplayOrder:
    def test_default_folder_first(self, notestore_db):
        """Default 'Notes' folder comes first in display order."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes = next(f for f in icloud.folders if f.name == "Notes")
        assert notes.display_order == 0

    def test_smart_folder_after_default(self, notestore_db):
        """Smart folders come after default 'Notes' folder but before user folders."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes = next(f for f in icloud.folders if f.name == "Notes")
        bookmarks = next(f for f in icloud.folders if f.name == "Bookmarks")
        assert bookmarks.display_order > notes.display_order

    def test_user_folder_after_default(self, notestore_db):
        """User folder (ZSORTORDER=2) comes after default folder."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        notes = next(f for f in icloud.folders if f.name == "Notes")
        work = next(f for f in icloud.folders if f.name == "Work")
        assert work.display_order > notes.display_order

    def test_crdt_ordering_respected(self, tmp_path):
        """Folders are ordered by CRDT data, not alphabetically."""

        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        zebra_uuid = "AA000000-0000-0000-0000-000000000001"
        alpha_uuid = "AA000000-0000-0000-0000-000000000002"

        # CRDT says: Zebra first, then Alpha
        crdt = build_crdt_with_uuids([zebra_uuid, alpha_uuid])
        builder.add_account(pk=1, name="Test", account_data_pk=50)
        builder.add_account_data(pk=50, account_pk=1, mergeable_data=crdt)
        builder.add_folder(pk=10, title="Zebra", account_pk=1, identifier=zebra_uuid,
                           sort_order=2, folder_type=0)
        builder.add_folder(pk=11, title="Alpha", account_pk=1, identifier=alpha_uuid,
                           sort_order=2, folder_type=0)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        folder_names = [f.name for f in accounts[0].folders]
        assert folder_names == ["Zebra", "Alpha"]

    def test_folder_not_in_crdt_still_included(self, tmp_path):
        """Folders not in account CRDT are included if ZNEEDSINITIALFETCHFROMCLOUD=0."""

        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        alpha_uuid = "AA000000-0000-0000-0000-000000000001"
        new_uuid = "AA000000-0000-0000-0000-000000000002"

        # CRDT only mentions Alpha — but NewFolder is still included (not a cloud ghost)
        crdt = build_crdt_with_uuids([alpha_uuid])
        builder.add_account(pk=1, name="Test", account_data_pk=50)
        builder.add_account_data(pk=50, account_pk=1, mergeable_data=crdt)
        builder.add_folder(pk=10, title="Alpha", account_pk=1, identifier=alpha_uuid,
                           sort_order=2, folder_type=0)
        builder.add_folder(pk=11, title="NewFolder", account_pk=1, identifier=new_uuid,
                           sort_order=2, folder_type=0, needs_initial_fetch=0)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        folder_names = [f.name for f in accounts[0].folders]
        assert "Alpha" in folder_names
        assert "NewFolder" in folder_names


class TestRecentlyDeleted:
    def test_recently_deleted_skipped(self, tmp_path):
        """Recently Deleted folder (ZFOLDERTYPE=1) is excluded entirely."""

        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        notes_uuid = "BB000000-0000-0000-0000-000000000001"
        deleted_uuid = "BB000000-0000-0000-0000-000000000002"

        builder.add_account(pk=1, name="Test")
        builder.add_folder(pk=10, title="Notes", account_pk=1, identifier=notes_uuid,
                           sort_order=1, folder_type=0)
        builder.add_folder(pk=11, title="Recently Deleted", account_pk=1,
                           identifier=deleted_uuid, sort_order=3, folder_type=1)
        builder.add_note(pk=100, title="Active Note", folder_pk=10,
                         identifier="CC000000-0000-0000-0000-000000000001",
                         creation_ts=726969600.0, mod_ts=726969600.0)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        folder_names = [f.name for f in accounts[0].folders]
        assert "Recently Deleted" not in folder_names
        assert folder_names == ["Notes"]


class TestOrphanedFolderFiltering:
    def test_cloud_ghost_folder_excluded(self, tmp_path):
        """Folders with ZNEEDSINITIALFETCHFROMCLOUD=1 are filtered out as cloud-sync ghosts."""
        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        builder.add_account(pk=1, name="Test")
        builder.add_folder(pk=10, title="Notes", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000001", sort_order=1)
        builder.add_folder(pk=11, title="Real Folder", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000002", sort_order=2,
                           needs_initial_fetch=0)
        builder.add_folder(pk=12, title="Ghost Folder", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000003", sort_order=2,
                           needs_initial_fetch=1)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        folder_names = [f.name for f in accounts[0].folders]
        assert "Real Folder" in folder_names
        assert "Ghost Folder" not in folder_names

    def test_notes_in_cloud_ghost_folder_excluded(self, tmp_path):
        """Notes belonging to a cloud-ghost folder are excluded from all results."""
        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        builder.add_account(pk=1, name="Test")
        builder.add_folder(pk=10, title="Notes", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000001", sort_order=1)
        builder.add_folder(pk=11, title="Real Folder", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000002", sort_order=2,
                           needs_initial_fetch=0)
        builder.add_folder(pk=12, title="Ghost Folder", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000003", sort_order=2,
                           needs_initial_fetch=1)
        builder.add_note(pk=100, title="Good Note", folder_pk=11,
                         identifier="EE000000-0000-0000-0000-000000000001",
                         creation_ts=726969600.0, mod_ts=726969600.0)
        builder.add_note(pk=101, title="Ghost Note", folder_pk=12,
                         identifier="EE000000-0000-0000-0000-000000000002",
                         creation_ts=726969600.0, mod_ts=726969600.0)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        all_notes = [n.name for a in accounts for f in a.folders for n in f.notes]
        assert "Good Note" in all_notes
        assert "Ghost Note" not in all_notes

    def test_folder_with_zero_fetch_flag_included(self, tmp_path):
        """Folders with ZNEEDSINITIALFETCHFROMCLOUD=0 are included regardless of CRDT presence."""
        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        builder.add_account(pk=1, name="Test")
        builder.add_folder(pk=10, title="Notes", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000001", sort_order=1)
        builder.add_folder(pk=11, title="New Folder", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000002", sort_order=2,
                           needs_initial_fetch=0)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        folder_names = [f.name for f in accounts[0].folders]
        assert "New Folder" in folder_names

    def test_folder_with_null_fetch_flag_included(self, tmp_path):
        """Folders with NULL ZNEEDSINITIALFETCHFROMCLOUD are included (default/legacy rows)."""
        db_path = tmp_path / "NoteStore.sqlite"
        builder = create_test_db(db_path)

        builder.add_account(pk=1, name="Test")
        builder.add_folder(pk=10, title="Notes", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000001", sort_order=1)
        # Explicitly set needs_initial_fetch=None to simulate NULL
        builder.add_folder(pk=11, title="Legacy Folder", account_pk=1,
                           identifier="DD000000-0000-0000-0000-000000000002", sort_order=2,
                           needs_initial_fetch=None)
        builder.build()

        accounts = extract_folders_and_notes(db_path=db_path)
        folder_names = [f.name for f in accounts[0].folders]
        assert "Legacy Folder" in folder_names


class TestExpansionState:
    def test_folders_default_to_not_expanded(self, notestore_db):
        """Without expansion state plist, folders default based on expansion_state lookup (False)."""
        accounts = extract_folders_and_notes(db_path=notestore_db)
        icloud = _get_account(accounts, "iCloud")
        # No plist exists in test env, so expansion_state is empty -> folders with identifiers get False
        work = next(f for f in icloud.folders if f.name == "Work")
        assert work.is_expanded is False


class TestExtractUuidOrder:
    def test_extracts_uuids_in_order(self):
        """UUIDs are extracted in the order they appear in the CRDT data."""
        uuids = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
        data = build_crdt_with_uuids(uuids)
        result = _extract_uuid_order(data)
        assert result == [u.upper() for u in uuids]

    def test_deduplicates_uuids(self):
        """Duplicate UUIDs in CRDT data are deduplicated, keeping first occurrence."""
        uuid = "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"
        data = build_crdt_with_uuids([uuid, uuid])
        result = _extract_uuid_order(data)
        assert result == [uuid.upper()]

    def test_empty_data(self):
        """Empty gzip data returns empty list."""
        import gzip
        data = gzip.compress(b"no uuids here")
        result = _extract_uuid_order(data)
        assert result == []

    def test_invalid_gzip(self):
        """Invalid gzip data returns empty list gracefully."""
        result = _extract_uuid_order(b"not gzip at all")
        assert result == []
