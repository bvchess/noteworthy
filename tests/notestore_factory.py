#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""
Factory for creating test NoteStore SQLite databases.

Provides schema creation and a fluent builder API for populating test databases
with accounts, folders, notes, attachments, and related data.
"""

import base64
import gzip
import json
import sqlite3
from pathlib import Path


TEST_DATA_DIR = Path(__file__).parent / "test_data"


def create_notestore_schema(conn: sqlite3.Connection) -> None:
    """Create the NoteStore database schema used by the codebase."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS Z_METADATA (
            Z_UUID TEXT
        );

        CREATE TABLE IF NOT EXISTS ZICCLOUDSYNCINGOBJECT (
            Z_PK INTEGER PRIMARY KEY,
            Z_ENT INTEGER,
            ZNAME TEXT,
            ZTITLE1 TEXT,
            ZTITLE2 TEXT,
            ZIDENTIFIER TEXT,
            ZTYPEUTI TEXT,
            ZTYPEUTI1 TEXT,
            ZTITLE TEXT,
            ZALTTEXT TEXT,
            ZFILENAME TEXT,
            ZGENERATION1 TEXT,
            ZFOLDER INTEGER,
            ZPARENT INTEGER,
            ZACCOUNT8 INTEGER,
            ZACCOUNTDATA INTEGER,
            ZMEDIA INTEGER,
            ZPARENTATTACHMENT INTEGER,
            ZNOTE INTEGER,
            ZNOTE1 INTEGER,
            ZCREATIONDATE3 REAL,
            ZMODIFICATIONDATE1 REAL,
            ZSMARTFOLDERQUERYJSON TEXT,
            ZCUSTOMNOTESORTTYPEVALUE INTEGER,
            ZSORTORDER INTEGER,
            ZFOLDERTYPE INTEGER,
            ZMERGEABLEDATA BLOB,
            ZMERGEABLEDATA1 BLOB,
            ZMERGEABLEDATA2 BLOB,
            ZTOKENCONTENTIDENTIFIER TEXT,
            ZNEEDSINITIALFETCHFROMCLOUD INTEGER DEFAULT 0,
            ZSERVERRECORDDATA BLOB
        );

        CREATE TABLE IF NOT EXISTS ZICNOTEDATA (
            Z_PK INTEGER PRIMARY KEY,
            ZNOTE INTEGER,
            ZDATA BLOB
        );
    """)


class NoteStoreBuilder:
    """Fluent API for inserting test data into a NoteStore database."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._next_notedata_pk = 1

    def set_db_uuid(self, uuid: str) -> "NoteStoreBuilder":
        """Set the database UUID in Z_METADATA."""
        self.conn.execute("INSERT INTO Z_METADATA (Z_UUID) VALUES (?)", (uuid,))
        return self

    def add_account(self, pk: int, name: str, account_data_pk: int = None) -> "NoteStoreBuilder":
        """Add an account (Z_ENT=14)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT (Z_PK, Z_ENT, ZNAME, ZACCOUNTDATA) VALUES (?, 14, ?, ?)",
            (pk, name, account_data_pk)
        )
        return self

    def add_folder(
        self, pk: int, title: str, account_pk: int, parent_pk: int = None,
        smart_query_json: str = None, sort_type_value: int = None,
        identifier: str = None, sort_order: int = None, folder_type: int = None,
        mergeable_data2: bytes = None, needs_initial_fetch: int | None = 0,
    ) -> "NoteStoreBuilder":
        """Add a folder (Z_ENT=15)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZTITLE2, ZACCOUNT8, ZPARENT, ZSMARTFOLDERQUERYJSON, "
            "ZCUSTOMNOTESORTTYPEVALUE, ZIDENTIFIER, ZSORTORDER, ZFOLDERTYPE, ZMERGEABLEDATA2, "
            "ZNEEDSINITIALFETCHFROMCLOUD) "
            "VALUES (?, 15, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pk, title, account_pk, parent_pk, smart_query_json,
             sort_type_value, identifier, sort_order, folder_type, mergeable_data2,
             needs_initial_fetch)
        )
        return self

    def add_note(self, pk: int, title: str, folder_pk: int, identifier: str = None,
                 creation_ts: float = None, mod_ts: float = None) -> "NoteStoreBuilder":
        """Add a note (Z_ENT=12).

        Args:
            pk: Primary key (Z_PK)
            title: Note title (ZTITLE1). Use None to test NULL-title exclusion.
            folder_pk: Folder primary key (ZFOLDER)
            identifier: UUID identifier (ZIDENTIFIER)
            creation_ts: Core Data timestamp (seconds since 2001-01-01)
            mod_ts: Modification Core Data timestamp
        """
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZTITLE1, ZFOLDER, ZIDENTIFIER, ZCREATIONDATE3, ZMODIFICATIONDATE1) "
            "VALUES (?, 12, ?, ?, ?, ?, ?)",
            (pk, title, folder_pk, identifier, creation_ts, mod_ts)
        )
        return self

    def add_note_data(self, note_pk: int, data: bytes) -> "NoteStoreBuilder":
        """Insert compressed protobuf data into ZICNOTEDATA."""
        self.conn.execute(
            "INSERT INTO ZICNOTEDATA (Z_PK, ZNOTE, ZDATA) VALUES (?, ?, ?)",
            (self._next_notedata_pk, note_pk, data)
        )
        self._next_notedata_pk += 1
        return self

    def add_attachment(self, pk: int, identifier: str, type_uti: str, title: str = None,
                       media_pk: int = None, alt_text: str = None,
                       note_pk: int = None, parent_attachment_pk: int = None) -> "NoteStoreBuilder":
        """Add a regular attachment (Z_ENT=5)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZIDENTIFIER, ZTYPEUTI, ZTITLE, ZMEDIA, ZALTTEXT, ZNOTE, ZPARENTATTACHMENT) "
            "VALUES (?, 5, ?, ?, ?, ?, ?, ?, ?)",
            (pk, identifier, type_uti, title, media_pk, alt_text, note_pk, parent_attachment_pk)
        )
        return self

    def add_link_attachment(self, pk: int, identifier: str, alt_text: str,
                            token_content_identifier: str,
                            note_pk: int = None) -> "NoteStoreBuilder":
        """Add a link attachment (Z_ENT=9)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZIDENTIFIER, ZALTTEXT, ZTOKENCONTENTIDENTIFIER, ZNOTE1, "
            "ZTYPEUTI1) "
            "VALUES (?, 9, ?, ?, ?, ?, 'com.apple.notes.inlinetextattachment.link')",
            (pk, identifier, alt_text, token_content_identifier, note_pk)
        )
        return self

    def add_hashtag(self, pk: int, note_pk: int, alt_text: str) -> "NoteStoreBuilder":
        """Add a hashtag inline attachment (Z_ENT=9, ZTYPEUTI1=hashtag)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZALTTEXT, ZNOTE1, ZTYPEUTI1) "
            "VALUES (?, 9, ?, ?, 'com.apple.notes.inlinetextattachment.hashtag')",
            (pk, alt_text, note_pk)
        )
        return self

    def add_media(self, pk: int, identifier: str, filename: str,
                  generation: str = None) -> "NoteStoreBuilder":
        """Add a media record (Z_ENT=6, used for attachment file resolution)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZIDENTIFIER, ZFILENAME, ZGENERATION1) "
            "VALUES (?, 6, ?, ?, ?)",
            (pk, identifier, filename, generation)
        )
        return self

    def add_table(self, pk: int, identifier: str,
                  mergeable_data: bytes) -> "NoteStoreBuilder":
        """Add a table attachment with ZMERGEABLEDATA1."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZIDENTIFIER, ZTYPEUTI, ZMERGEABLEDATA1) "
            "VALUES (?, 5, ?, 'com.apple.notes.table', ?)",
            (pk, identifier, mergeable_data)
        )
        return self

    def add_gallery(self, pk: int, identifier: str) -> "NoteStoreBuilder":
        """Add a gallery attachment (Z_ENT=5, ZTYPEUTI=com.apple.notes.gallery)."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZIDENTIFIER, ZTYPEUTI) "
            "VALUES (?, 5, ?, 'com.apple.notes.gallery')",
            (pk, identifier)
        )
        return self

    def add_account_data(self, pk: int, account_pk: int, mergeable_data: bytes = None) -> "NoteStoreBuilder":
        """Add an ICAccountData entity (Z_ENT=4) linked to an account."""
        self.conn.execute(
            "INSERT INTO ZICCLOUDSYNCINGOBJECT "
            "(Z_PK, Z_ENT, ZMERGEABLEDATA) "
            "VALUES (?, 4, ?)",
            (pk, mergeable_data)
        )
        # Link the account to this account data row
        self.conn.execute(
            "UPDATE ZICCLOUDSYNCINGOBJECT SET ZACCOUNTDATA = ? WHERE Z_PK = ? AND Z_ENT = 14",
            (pk, account_pk)
        )
        return self

    def build(self) -> None:
        """Commit all changes."""
        self.conn.commit()


def load_fixture_bytes(fixture_name: str, key: str) -> bytes:
    """Load base64-encoded bytes from a test fixture JSON file.

    Args:
        fixture_name: Name without extension (e.g., "TEST_NOTE")
        key: JSON key to read (e.g., "note_data")

    Returns:
        Decoded bytes
    """
    fixture_path = TEST_DATA_DIR / f"{fixture_name}.raw_data.json"
    with open(fixture_path, "r") as f:
        data = json.load(f)
    return base64.b64decode(data[key])


def load_fixture_attachment_table_data(fixture_name: str, uuid: str) -> bytes | None:
    """Load table_data bytes for a specific attachment UUID from a fixture.

    Args:
        fixture_name: Name without extension (e.g., "TEST_NOTE")
        uuid: Attachment UUID to find

    Returns:
        Decoded table_data bytes, or None if not found
    """
    fixture_path = TEST_DATA_DIR / f"{fixture_name}.raw_data.json"
    with open(fixture_path, "r") as f:
        data = json.load(f)
    for att in data.get("attachments", []):
        if att["uuid"] == uuid and "table_data" in att:
            return base64.b64decode(att["table_data"])
    return None


def _encode_varint(value: int) -> bytes:
    """Encode an integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def _encode_field(field_num: int, wire_type: int, value: bytes | int) -> bytes:
    """Encode a single protobuf field."""
    tag = _encode_varint((field_num << 3) | wire_type)
    if wire_type == 0:  # varint
        return tag + _encode_varint(value)
    elif wire_type == 2:  # length-delimited
        return tag + _encode_varint(len(value)) + value


def build_crdt_with_uuids(uuids: list[str]) -> bytes:
    """Build minimal gzipped CRDT protobuf containing UUIDs in display order.

    Produces a protobuf structure matching Apple Notes' CRTree format so the
    parser can extract UUIDs with correct ordering counters.

    The structure is: outer{field 1: 0, field 2: {field 3: crdt_payload}}
    where crdt_payload has: field 3 entries (first = CRTree, rest = UUID records + timestamps).
    """
    # Build CRTree nodes — each node references a UUID record by index and has an ordering counter.
    # UUIDs are provided in display order, so counter values increase sequentially.
    tree_nodes = b""
    for i, _ in enumerate(uuids):
        uuid_record_index = i * 2 + 1  # odd indices: 1, 3, 5, ...
        counter = i + 1

        # field 2 = {field 6: uuid_record_index}
        f2_inner = _encode_field(6, 0, uuid_record_index)
        f2 = _encode_field(2, 2, f2_inner)

        # field 3 = {field 1: {field 1: 0 (replica_id), field 2: counter}}
        f3_inner_inner = _encode_field(1, 0, 0) + _encode_field(2, 0, counter)
        f3_inner = _encode_field(1, 2, f3_inner_inner)
        f3 = _encode_field(3, 2, f3_inner)

        # field 4 = {field 2: node_id} (not used for ordering, but included for structure)
        f4_inner = _encode_field(2, 0, i * 2 + 2)
        f4 = _encode_field(4, 2, f4_inner)

        node = _encode_field(1, 2, f2 + f3 + f4)
        tree_nodes += node

    # CRTree container: field 5 = tree_nodes
    tree_container = _encode_field(5, 2, tree_nodes)
    first_field3 = _encode_field(3, 2, tree_container)

    # UUID records: each is a field 3 entry containing field 13 = {field 4: uuid_string}
    # Followed by a timestamp field 3 entry containing field 9 (placeholder)
    uuid_records = b""
    for u in uuids:
        uuid_bytes = u.encode("ascii")
        # field 13 inner: {field 1: 1, field 4: "UUID-STRING"}
        f13_inner = _encode_field(1, 0, 1) + _encode_field(4, 2, uuid_bytes)
        f13 = _encode_field(13, 2, f13_inner)
        uuid_entry = _encode_field(3, 2, f13)

        # Timestamp placeholder: field 9 = {field 1: 0}
        f9_inner = _encode_field(1, 0, 0)
        f9 = _encode_field(9, 2, f9_inner)
        ts_entry = _encode_field(3, 2, f9)

        uuid_records += uuid_entry + ts_entry

    # Assemble: field 1 (types placeholder) + field 2 (empty) + field 3 entries
    types_placeholder = _encode_field(1, 2, b"")
    field2_empty = _encode_field(2, 2, b"")
    crdt_payload = types_placeholder + field2_empty + first_field3 + uuid_records

    # Wrap in outer structure: field 2 = {field 1: 0, field 2: 0, field 3: crdt_payload}
    inner = _encode_field(1, 0, 0) + _encode_field(2, 0, 0) + _encode_field(3, 2, crdt_payload)
    outer = _encode_field(1, 0, 0) + _encode_field(2, 2, inner)

    return gzip.compress(outer)


def build_note_protobuf(parts: list) -> bytes:
    """Build a gzipped NoteStoreProto containing a Note with the given run sequence.

    Each part is either:
      - a `str`, contributing that text as a single AttributeRun (length = UTF-16 code units)
      - a `(str, str)` tuple `(attachment_uuid, type_uti)`, contributing one OBJECT
        REPLACEMENT CHARACTER (U+FFFC) with an AttributeRun whose `attachment_info`
        carries those fields

    The returned bytes can be fed directly to `NoteStoreBuilder.add_note_data`.
    Useful for tests that need a note body referencing specific attachment UUIDs
    instead of relying on the captured-from-Apple-Notes fixtures.
    """
    # Lazy import: notestore_pb2 is only needed here and pulls in protobuf.
    from noteworthy.notestore_pb2 import NoteStoreProto

    note_store = NoteStoreProto()
    note_store.document.version = 2
    note = note_store.document.note

    text_chunks: list[str] = []
    for part in parts:
        if isinstance(part, str):
            text_chunks.append(part)
            run = note.attribute_run.add()
            # AttributeRun.length counts UTF-16 code units (matches Apple Notes encoding).
            run.length = len(part.encode("utf-16-le")) // 2
        else:
            uuid, type_uti = part
            text_chunks.append("￼")
            run = note.attribute_run.add()
            run.length = 1
            run.attachment_info.attachment_identifier = uuid
            run.attachment_info.type_uti = type_uti

    note.note_text = "".join(text_chunks)
    return gzip.compress(note_store.SerializeToString())


def create_test_db(db_path: Path, db_uuid: str = "TEST-UUID-0000-0000-000000000000") -> NoteStoreBuilder:
    """Create a test database with schema and return a builder.

    Args:
        db_path: Path for the SQLite file
        db_uuid: Database UUID for x-coredata URIs

    Returns:
        NoteStoreBuilder ready for data insertion
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    create_notestore_schema(conn)
    builder = NoteStoreBuilder(conn)
    builder.set_db_uuid(db_uuid)
    return builder
