#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""
pytest configuration and test discovery for Apple Notes export tests.
"""

import json
import pytest
from pathlib import Path
from typing import List, Dict, Any
from dataclasses import dataclass

from notestore_factory import (
    build_crdt_with_uuids,
    build_note_protobuf,
    create_test_db,
    load_fixture_attachment_table_data,
    load_fixture_bytes,
)


TEST_DATA_DIR = Path(__file__).parent / "test_data"

DB_UUID = "TEST-UUID-0000-0000-000000000000"

# Folder identifiers (realistic UUID format)
UUID_FOLDER_NOTES = "10000000-0000-0000-0000-000000000001"
UUID_FOLDER_WORK = "10000000-0000-0000-0000-000000000002"
UUID_FOLDER_PERSONAL = "10000000-0000-0000-0000-000000000003"
UUID_FOLDER_LOCAL = "10000000-0000-0000-0000-000000000004"
UUID_FOLDER_BOOKMARKS = "10000000-0000-0000-0000-000000000005"

# Core Data timestamps (seconds since 2001-01-01 00:00:00 UTC)
# These correspond to known dates for test assertions
TS_2024_01_15 = 726969600.0   # 2024-01-15 00:00:00 UTC
TS_2024_06_01 = 738892800.0   # 2024-06-01 00:00:00 UTC
TS_2024_07_01 = 741484800.0   # 2024-07-01 00:00:00 UTC
TS_2024_08_01 = 744163200.0   # 2024-08-01 00:00:00 UTC


@pytest.fixture
def notestore_db(tmp_path):
    """Create a populated test NoteStore SQLite database.

    Contains:
    - 2 accounts (iCloud, On My Mac)
    - Nested folders (top-level + child)
    - A smart folder with tag query JSON
    - Multiple notes across folders
    - A note with NULL title (should be excluded)
    - Hashtags for smart folder matching
    - Regular attachment with media record
    - Table attachment with ZMERGEABLEDATA1
    - Link attachment for UUID prefix matching
    - Gallery with child attachments
    """
    db_path = tmp_path / "NoteStore.sqlite"
    media_dir = tmp_path / "Media"

    builder = create_test_db(db_path, DB_UUID)

    # Accounts (with account_data_pk linking to ICAccountData)
    builder.add_account(pk=1, name="iCloud", account_data_pk=50)
    builder.add_account(pk=2, name="On My Mac")

    # ICAccountData for iCloud — CRDT ordering: Work before Notes, then Bookmarks
    icloud_crdt = build_crdt_with_uuids([UUID_FOLDER_WORK, UUID_FOLDER_NOTES, UUID_FOLDER_BOOKMARKS])
    builder.add_account_data(pk=50, account_pk=1, mergeable_data=icloud_crdt)

    # Folders for iCloud (account pk=1)
    builder.add_folder(pk=10, title="Notes", account_pk=1, identifier=UUID_FOLDER_NOTES,
                       sort_order=1, folder_type=0)  # default "Notes" folder (sort_order=1)
    builder.add_folder(pk=11, title="Work", account_pk=1, sort_type_value=20,
                       identifier=UUID_FOLDER_WORK, sort_order=2, folder_type=0)  # date_created
    builder.add_folder(pk=12, title="Personal", account_pk=1, parent_pk=11,
                       sort_type_value=30, identifier=UUID_FOLDER_PERSONAL,
                       sort_order=2, folder_type=0)  # title

    # Folder for On My Mac (account pk=2)
    builder.add_folder(pk=20, title="Local Notes", account_pk=2, identifier=UUID_FOLDER_LOCAL,
                       sort_order=2, folder_type=0)

    # Smart folder (with hashtag query)
    smart_query = json.dumps({"type": {"and": [{"deleted": False}, {"tag": "bookmark"}]}})
    builder.add_folder(pk=30, title="Bookmarks", account_pk=1, smart_query_json=smart_query,
                       identifier=UUID_FOLDER_BOOKMARKS, sort_order=2, folder_type=0)

    # Notes
    builder.add_note(pk=100, title="First Note", folder_pk=10, identifier="aaa-bbb-ccc-100",
                     creation_ts=TS_2024_01_15, mod_ts=TS_2024_06_01)
    builder.add_note(pk=101, title="Second Note", folder_pk=10, identifier="aaa-bbb-ccc-101",
                     creation_ts=TS_2024_01_15, mod_ts=TS_2024_07_01)
    builder.add_note(pk=102, title="Work Item", folder_pk=11, identifier="aaa-bbb-ccc-102",
                     creation_ts=TS_2024_06_01, mod_ts=TS_2024_08_01)
    builder.add_note(pk=103, title="Personal Stuff", folder_pk=12, identifier="aaa-bbb-ccc-103",
                     creation_ts=TS_2024_06_01, mod_ts=TS_2024_07_01)
    builder.add_note(pk=104, title="Local Note", folder_pk=20, identifier="aaa-bbb-ccc-104",
                     creation_ts=TS_2024_01_15, mod_ts=TS_2024_06_01)

    # NULL-title note (should be excluded by queries)
    builder.add_note(pk=199, title=None, folder_pk=10, identifier="aaa-bbb-ccc-199")

    # First Note's body: synthesized so the embedded attachment UUIDs match the
    # records this conftest adds below (pk=400 photo, pk=401 pdf, pk=450 link to
    # Second Note). The captured TEST_NOTE fixture references unrelated UUIDs and
    # would render as `[Attachment: ...]` placeholders.
    first_note_data = build_note_protobuf([
        "Here is an image:\n",
        ("att-uuid-400", "public.jpeg"),
        "\nAnd a document:\n",
        ("att-uuid-401", "com.adobe.pdf"),
        "\nSee also: ",
        ("link-aaa-bbb-ccc-ddd", "com.apple.notes.inlinetextattachment.link"),
        "\n",
    ])
    builder.add_note_data(note_pk=100, data=first_note_data)

    day_planner_data = load_fixture_bytes("DAY_PLANNER", "note_data")
    builder.add_note_data(note_pk=101, data=day_planner_data)

    formatting_data = load_fixture_bytes("FORMATTING_BOUNDARY", "note_data")
    builder.add_note_data(note_pk=103, data=formatting_data)

    # Hashtag on note 102 for smart folder matching
    builder.add_hashtag(pk=500, note_pk=102, alt_text="#bookmark")

    # Regular attachment with media record
    builder.add_media(pk=300, identifier="media-uuid-300", filename="photo.jpg", generation="gen1")
    media_file_dir = media_dir / "media-uuid-300" / "gen1"
    media_file_dir.mkdir(parents=True)
    (media_file_dir / "photo.jpg").write_bytes(b"fake-jpg-data")

    builder.add_attachment(pk=400, identifier="att-uuid-400", type_uti="public.jpeg",
                           title="My Photo", media_pk=300, note_pk=100)

    # Media without generation subdirectory
    builder.add_media(pk=301, identifier="media-uuid-301", filename="doc.pdf")
    media_file_dir2 = media_dir / "media-uuid-301"
    media_file_dir2.mkdir(parents=True)
    (media_file_dir2 / "doc.pdf").write_bytes(b"fake-pdf-data")

    builder.add_attachment(pk=401, identifier="att-uuid-401", type_uti="com.adobe.pdf",
                           title="My Document", media_pk=301, note_pk=100)

    # Table attachment
    table_data = load_fixture_attachment_table_data("TEST_NOTE", "0A8E3C41-2063-49F5-9727-A16FF84B0721")
    if table_data:
        builder.add_table(pk=410, identifier="table-uuid-410", mergeable_data=table_data)

    # Link attachment (Z_ENT=9)
    builder.add_link_attachment(pk=450, identifier="link-aaa-bbb-ccc-ddd",
                                alt_text="Second Note",
                                token_content_identifier="applenotes:note/aaa-bbb-ccc-101",
                                note_pk=100)

    # Gallery with child attachments
    builder.add_gallery(pk=460, identifier="gallery-uuid-460")

    builder.add_media(pk=302, identifier="media-uuid-302", filename="img1.jpg", generation="gen1")
    gallery_media_dir = media_dir / "media-uuid-302" / "gen1"
    gallery_media_dir.mkdir(parents=True)
    (gallery_media_dir / "img1.jpg").write_bytes(b"fake-img1")

    builder.add_attachment(pk=461, identifier="gallery-child-1", type_uti="public.jpeg",
                           title="Gallery Image 1", media_pk=302, parent_attachment_pk=460)

    builder.add_media(pk=303, identifier="media-uuid-303", filename="img2.png")
    gallery_media_dir2 = media_dir / "media-uuid-303"
    gallery_media_dir2.mkdir(parents=True)
    (gallery_media_dir2 / "img2.png").write_bytes(b"fake-img2")

    builder.add_attachment(pk=462, identifier="gallery-child-2", type_uti="public.png",
                           title="Gallery Image 2", media_pk=303, parent_attachment_pk=460)

    builder.build()

    return db_path


@dataclass
class TestNoteConfig:
    """Configuration for a single test note."""
    name: str
    data_file: Path
    reference_file: Path
    expected: Dict[str, Any]

    @property
    def test_id(self) -> str:
        """Return a pytest-friendly test ID."""
        return self.name


def discover_test_notes() -> List[TestNoteConfig]:
    """
    Discover all test notes in test_data directory.

    Looks for files matching pattern: <name>.raw_data.json
    And corresponding: <name>.apple_generated.md

    Returns:
        List of TestNoteConfig objects
    """
    test_notes = []

    # Find all .raw_data.json files
    for data_file in TEST_DATA_DIR.glob("*.raw_data.json"):
        # Extract test name (e.g., "TEST_NOTE" from "TEST_NOTE.raw_data.json")
        name = data_file.stem.replace(".raw_data", "")

        # Check for corresponding reference file
        reference_file = TEST_DATA_DIR / f"{name}.apple_generated.md"
        if not reference_file.exists():
            pytest.skip(f"Missing reference file for {name}: {reference_file}")
            continue

        # Load expected values from JSON
        with open(data_file, 'r') as f:
            data = json.load(f)

        expected = data.get('expected', {})
        if not expected:
            pytest.skip(f"Missing 'expected' section in {data_file}")
            continue

        test_notes.append(TestNoteConfig(
            name=name,
            data_file=data_file,
            reference_file=reference_file,
            expected=expected
        ))

    return test_notes


def pytest_generate_tests(metafunc):
    """
    Automatically parametrize tests with test_note_config fixture.

    This allows tests to be written once and automatically run against
    all discovered test notes.
    """
    if "test_note_config" in metafunc.fixturenames:
        test_notes = discover_test_notes()
        metafunc.parametrize(
            "test_note_config",
            test_notes,
            ids=[note.test_id for note in test_notes]
        )
