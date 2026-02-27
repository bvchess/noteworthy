#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""Extract Apple Notes structure using direct SQLite database access."""
from __future__ import annotations

import gzip
import json
import plistlib
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from noteworthy.notes_datatypes import Account, Folder, Note

__all__ = ["extract_folders_and_notes"]

# Apple's Core Data epoch: 2001-01-01 00:00:00 UTC
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)

# Map ZCUSTOMNOTESORTTYPEVALUE to sort_order keywords
_SORT_ORDER_MAP: dict[int, str] = {
    0: "default",
    10: "date_edited",
    20: "date_created",
    30: "title",
}

# Default database path
DB_PATH = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"


def _evaluate_smart_folder_query(query: dict[str, Any], note_tags: set[str]) -> bool:
    """Evaluate a smart folder query against a note's hashtags.

    Args:
        query: Parsed JSON query from ZSMARTFOLDERQUERYJSON
        note_tags: Set of uppercase hashtag names (without #) for the note

    Returns:
        True if the note matches the query
    """
    if "and" in query:
        return all(_evaluate_smart_folder_query(sub, note_tags) for sub in query["and"])
    elif "or" in query:
        return any(_evaluate_smart_folder_query(sub, note_tags) for sub in query["or"])
    elif "tag" in query:
        return query["tag"].upper() in note_tags
    elif "deleted" in query:
        # We only query non-deleted notes, so deleted:false always matches
        return not query["deleted"]
    elif "type" in query:
        # Nested type - evaluate recursively
        return _evaluate_smart_folder_query(query["type"], note_tags)
    elif "entity" in query:
        # Top-level entity check - we only process notes
        return query["entity"] == "note"
    else:
        print(f"Error: Unknown query type: {query}", file=sys.stderr)
        # Unknown query type - default to not matching
        return False


def _coredata_to_datetime(timestamp: float | None) -> datetime | None:
    """Convert Core Data timestamp to Python datetime.

    Core Data timestamps are seconds since 2001-01-01 00:00:00 UTC.
    Truncates to whole seconds for consistency.
    """
    if timestamp is None:
        return None
    whole_seconds = int(timestamp)
    return APPLE_EPOCH + timedelta(seconds=whole_seconds)


def _build_coredata_uri(db_uuid: str, entity_name: str, pk: int) -> str:
    """Build x-coredata URI from components.

    Format: x-coredata://{DB_UUID}/{EntityName}/p{Z_PK}
    """
    return f"x-coredata://{db_uuid}/{entity_name}/p{pk}"


_UUID_RE = re.compile(r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}", re.IGNORECASE)

# ZSORTORDER values for folder grouping
_SORT_GROUP_DEFAULT = 1    # Default "Notes" folder — always first
_SORT_GROUP_USER = 2       # User-created folders — CRDT ordered

# ZFOLDERTYPE values
_FOLDER_TYPE_RECENTLY_DELETED = 1
_FOLDER_TYPE_SMART = 2

# Expansion state plist path (sandboxed container location)
_NOTES_PLIST_PATH = Path.home() / "Library/Containers/com.apple.Notes/Data/Library/Preferences/com.apple.Notes.plist"


def _decode_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Decode a protobuf varint at the given position. Returns (value, new_pos)."""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7F) << shift
        pos += 1
        if not (b & 0x80):
            return result, pos
        shift += 7
    return result, pos


def _iter_protobuf_fields(data: bytes) -> list[tuple[int, int, bytes | int]]:
    """Iterate protobuf fields, returning (field_number, wire_type, value) tuples.

    For varint fields (wire_type=0), value is an int.
    For length-delimited fields (wire_type=2), value is bytes.
    Fixed32/fixed64 fields are skipped.
    """
    fields: list[tuple[int, int, bytes | int]] = []
    pos = 0
    while pos < len(data):
        tag, pos = _decode_varint(data, pos)
        field_num = tag >> 3
        wire_type = tag & 7
        if wire_type == 0:  # varint
            val, pos = _decode_varint(data, pos)
            fields.append((field_num, 0, val))
        elif wire_type == 2:  # length-delimited
            length, pos = _decode_varint(data, pos)
            fields.append((field_num, 2, data[pos:pos + length]))
            pos += length
        elif wire_type == 1:  # fixed64
            pos += 8
        elif wire_type == 5:  # fixed32
            pos += 4
        else:
            break
    return fields


def _extract_uuid_order(data: bytes) -> list[str]:
    """Extract folder UUIDs in display order from gzip-compressed CRDT data.

    The CRDT blob contains a CRTree structure where each tree node has an ordering
    counter and a reference to a UUID record. Display order is determined by sorting
    nodes by their ordering counter (ascending), not by byte-stream position.

    Falls back to regex UUID extraction (byte-stream order) if protobuf parsing fails.

    Args:
        data: Gzipped protobuf CRDT blob from ZMERGEABLEDATA or ZMERGEABLEDATA2.

    Returns:
        List of UUID strings in display order.
    """
    try:
        decompressed = gzip.decompress(data)
    except (gzip.BadGzipFile, OSError):
        return []

    try:
        return _parse_crdt_folder_order(decompressed)
    except Exception:
        return _extract_uuids_by_regex(decompressed)


def _parse_crdt_folder_order(data: bytes) -> list[str]:
    """Parse the CRTree protobuf to extract UUIDs in display order.

    The protobuf structure (nested): outer → field 2 → field 3 → repeated field 3 entries.
    The first field 3 entry contains the CRTree (in its field 5 sub-field).
    Subsequent field 3 entries with field 13 contain UUID strings.

    Each CRTree node has:
      - field 2 → field 6: index referencing which UUID record (odd numbers: 1,3,5,...)
      - field 3 → field 1 → field 2: ordering counter (lower = earlier in display)
    """
    # Navigate: outer → field 2 → field 3 (the CRDT payload)
    outer_fields = _iter_protobuf_fields(data)
    f2_data = None
    for fn, wt, val in outer_fields:
        if fn == 2 and wt == 2:
            f2_data = val
            break
    if f2_data is None:
        return []

    f2_fields = _iter_protobuf_fields(f2_data)
    crdt_data = None
    for fn, wt, val in f2_fields:
        if fn == 3 and wt == 2:
            crdt_data = val
            break
    if crdt_data is None:
        return []

    # Collect all field 3 entries from the CRDT payload
    crdt_fields = _iter_protobuf_fields(crdt_data)
    field3_entries = [val for fn, wt, val in crdt_fields if fn == 3 and wt == 2]
    if not field3_entries:
        return []

    # First field 3 entry contains the CRTree (in field 5)
    tree_container = field3_entries[0]
    tree_container_fields = _iter_protobuf_fields(tree_container)
    tree_data = None
    for fn, wt, val in tree_container_fields:
        if fn == 5 and wt == 2:
            tree_data = val
            break

    # Extract UUIDs from remaining field 3 entries (those containing field 13 with a UUID)
    # The UUID is nested several levels deep in the protobuf; search the field 13 bytes directly.
    uuids_by_index: dict[int, str] = {}
    uuid_record_idx = 0
    for entry in field3_entries[1:]:
        entry_fields = _iter_protobuf_fields(entry)
        for fn, wt, val in entry_fields:
            if fn == 13 and wt == 2:
                m = _UUID_RE.search(val.decode("latin-1"))
                if m:
                    uuids_by_index[uuid_record_idx] = m.group(0).upper()
                uuid_record_idx += 1
                break
        else:
            uuid_record_idx += 1

    if tree_data is None:
        # No CRTree found — return UUIDs in field-3 order
        return list(uuids_by_index.values())

    # Parse CRTree nodes to get (uuid_record_index, ordering_counter) pairs
    tree_nodes = _iter_protobuf_fields(tree_data)
    node_ordering: list[tuple[int, int, str]] = []  # (counter, replica_id, uuid)
    for fn, wt, val in tree_nodes:
        if fn != 1 or wt != 2:
            continue
        node_fields = _iter_protobuf_fields(val)
        uuid_idx = None
        counter = 0
        replica_id = 0
        for nfn, nwt, nval in node_fields:
            if nfn == 2 and nwt == 2:
                # field 2 → field 6 = uuid record index
                for sfn, swt, sval in _iter_protobuf_fields(nval):
                    if sfn == 6 and swt == 0:
                        uuid_idx = sval
            elif nfn == 3 and nwt == 2:
                # field 3 → field 1 (message) → {field 1: replica_id, field 2: counter}
                for sfn, swt, sval in _iter_protobuf_fields(nval):
                    if sfn == 1 and swt == 2:
                        for ssfn, sswt, ssval in _iter_protobuf_fields(sval):
                            if ssfn == 1 and sswt == 0:
                                replica_id = ssval
                            elif ssfn == 2 and sswt == 0:
                                counter = ssval
        if uuid_idx is not None:
            # uuid_idx is 1-based into field3 entries (CRTree is at 0); convert to 0-based parser index
            uuid_pos = uuid_idx - 1
            uuid_str = uuids_by_index.get(uuid_pos)
            if uuid_str:
                node_ordering.append((counter, replica_id, uuid_str))

    # Sort by ordering counter ascending (lower counter = earlier in display)
    node_ordering.sort()
    seen: set[str] = set()
    result: list[str] = []
    for _, _, uuid in node_ordering:
        if uuid not in seen:
            seen.add(uuid)
            result.append(uuid)
    return result


def _extract_uuids_by_regex(data: bytes) -> list[str]:
    """Fallback: extract unique UUIDs in byte-stream order from decompressed data."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _UUID_RE.finditer(data.decode("latin-1")):
        uuid_str = m.group(0).upper()
        if uuid_str not in seen:
            seen.add(uuid_str)
            result.append(uuid_str)
    return result


def _read_expansion_state(plist_path: Path | None = None) -> dict[str, bool]:
    """Read folder expansion state from Apple Notes preferences.

    Returns:
        Dict mapping folder UUID (uppercase) to True for expanded folders.
        Returns empty dict on failure (file not found, wrong OS, etc.).
    """
    if plist_path is None:
        plist_path = _NOTES_PLIST_PATH
    try:
        with open(plist_path, "rb") as f:
            plist = plistlib.load(f)
        archive = plist.get("windowStateArchive", {})
        expansion = archive.get("kICWindowStateExpansionState", {})
        global_ctx = expansion.get("ICExpansionStateGlobalContext", {})
        identifiers = global_ctx.get("ICExpansionStateItemTypePersistentIdentifier", {})
        return {uuid.upper(): True for uuid, val in identifiers.items() if val}
    except Exception:
        return {}


def _compute_sibling_display_order(
    sibling_pks: list[int],
    crdt_uuids: list[str],
    folder_uuids: dict[int, str | None],
    folder_sort_groups: dict[int, int | None],
    folders_by_pk: dict[int, Folder],
) -> dict[int, int]:
    """Compute display_order for a group of sibling folders.

    Apple Notes sidebar order: smart folders first (in CRDT order), then default "Notes"
    folder (ZSORTORDER=1), then regular user folders (ZSORTORDER=2) in CRDT order.
    Folders not found in CRDT data are appended at the end of their group.
    """
    uuid_position: dict[str, int] = {u: i for i, u in enumerate(crdt_uuids)}
    fallback_pos = len(crdt_uuids)

    default_pks: list[int] = []
    user_pks: list[tuple[int, int]] = []
    smart_pks: list[tuple[int, int]] = []
    other_pks: list[int] = []

    for pk in sibling_pks:
        group = folder_sort_groups.get(pk)
        folder = folders_by_pk[pk]
        uuid = (folder_uuids.get(pk) or "").upper()
        pos = uuid_position.get(uuid, fallback_pos)

        if group == _SORT_GROUP_DEFAULT:
            default_pks.append(pk)
        elif folder.is_smart_folder:
            smart_pks.append((pos, pk))
        elif group == _SORT_GROUP_USER or group is None:
            user_pks.append((pos, pk))
        else:
            other_pks.append(pk)

    user_pks.sort()
    smart_pks.sort()

    result: dict[int, int] = {}
    order = 0
    # Default "Notes" folder first
    for pk in default_pks:
        result[pk] = order
        order += 1
    # Smart folders next (Apple Notes shows these after default but before user folders)
    for _, pk in smart_pks:
        result[pk] = order
        order += 1
    # Regular user folders in CRDT order
    for _, pk in user_pks:
        result[pk] = order
        order += 1
    # Any unclassified folders at the end
    for pk in other_pks:
        result[pk] = order
        order += 1
    return result


def extract_folders_and_notes(db_path: Path | None = None) -> list[Account]:
    """Extract Apple Notes structure using direct database access.

    Args:
        db_path: Path to NoteStore.sqlite. Defaults to standard location.

    Returns:
        List of Account objects containing the complete folder/note hierarchy.
    """
    if db_path is None:
        db_path = DB_PATH

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        # Get database UUID for building x-coredata URIs
        db_uuid = conn.execute("SELECT Z_UUID FROM Z_METADATA").fetchone()[0]

        # Query accounts (Z_ENT = 14)
        accounts_by_pk: dict[int, Account] = {}
        account_data_pks: dict[int, int | None] = {}  # account_pk -> account_data_pk
        for row in conn.execute("""
            SELECT Z_PK, ZNAME, ZACCOUNTDATA
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT = 14
        """):
            account_id = _build_coredata_uri(db_uuid, "ICAccount", row["Z_PK"])
            account = Account(row["ZNAME"], account_id, None)
            accounts_by_pk[row["Z_PK"]] = account
            account_data_pks[row["Z_PK"]] = row["ZACCOUNTDATA"]

        # Query ICAccountData (Z_ENT = 4) for top-level folder CRDT ordering
        account_data_crdt: dict[int, bytes] = {}  # account_data_pk -> ZMERGEABLEDATA
        for row in conn.execute("""
            SELECT Z_PK, ZMERGEABLEDATA
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT = 4
              AND ZMERGEABLEDATA IS NOT NULL
        """):
            account_data_crdt[row["Z_PK"]] = row["ZMERGEABLEDATA"]

        # Read expansion state from Apple Notes preferences
        expansion_state = _read_expansion_state()
        tags_expanded = expansion_state.pop("TAGSHEADER", True)
        for account in accounts_by_pk.values():
            account._tags_expanded = tags_expanded

        # Query folders (Z_ENT = 15)
        folders_by_pk: dict[int, Folder] = {}
        folder_parent_pks: dict[int, int | None] = {}  # folder_pk -> parent_folder_pk
        folder_account_pks: dict[int, int] = {}  # folder_pk -> account_pk
        folder_uuids: dict[int, str | None] = {}  # folder_pk -> ZIDENTIFIER
        folder_sort_groups: dict[int, int | None] = {}  # folder_pk -> ZSORTORDER
        folder_crdt_children: dict[int, bytes] = {}  # folder_pk -> ZMERGEABLEDATA2

        smart_folder_queries: dict[int, dict[str, Any]] = {}  # folder_pk -> parsed query JSON
        for row in conn.execute("""
            SELECT Z_PK, ZTITLE2, ZPARENT, ZACCOUNT8, ZSMARTFOLDERQUERYJSON,
                   ZCUSTOMNOTESORTTYPEVALUE, ZIDENTIFIER, ZSORTORDER, ZFOLDERTYPE, ZMERGEABLEDATA2,
                   ZNEEDSINITIALFETCHFROMCLOUD
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT = 15
        """):
            # Skip "Recently Deleted" folder
            if row["ZFOLDERTYPE"] == _FOLDER_TYPE_RECENTLY_DELETED:
                continue

            # Skip orphaned cloud-sync ghosts — folder references that were never fully fetched
            if row["ZNEEDSINITIALFETCHFROMCLOUD"]:
                continue

            folder_id = _build_coredata_uri(db_uuid, "ICFolder", row["Z_PK"])
            query_json = row["ZSMARTFOLDERQUERYJSON"]
            is_smart = query_json is not None or row["ZFOLDERTYPE"] == _FOLDER_TYPE_SMART
            sort_order = _SORT_ORDER_MAP.get(row["ZCUSTOMNOTESORTTYPEVALUE"], "default")
            identifier = row["ZIDENTIFIER"]
            is_expanded = expansion_state.get(identifier.upper(), False) if identifier else True
            folder = Folder(
                row["ZTITLE2"], folder_id, None,
                is_smart_folder=is_smart, sort_order=sort_order, is_expanded=is_expanded,
            )
            folders_by_pk[row["Z_PK"]] = folder
            folder_parent_pks[row["Z_PK"]] = row["ZPARENT"]
            folder_account_pks[row["Z_PK"]] = row["ZACCOUNT8"]
            folder_uuids[row["Z_PK"]] = identifier
            folder_sort_groups[row["Z_PK"]] = row["ZSORTORDER"]
            if row["ZMERGEABLEDATA2"]:
                folder_crdt_children[row["Z_PK"]] = row["ZMERGEABLEDATA2"]
            if is_smart and query_json:
                smart_folder_queries[row["Z_PK"]] = json.loads(query_json)

        # Build folder hierarchy
        for folder_pk, folder in folders_by_pk.items():
            parent_pk = folder_parent_pks[folder_pk]
            account_pk = folder_account_pks[folder_pk]

            if parent_pk is not None and parent_pk in folders_by_pk:
                # Nested folder - add to parent folder
                folders_by_pk[parent_pk].add_folder(folder)
            else:
                # Top-level folder - add to account
                account = accounts_by_pk.get(account_pk)
                if account:
                    account.add_folder(folder)

        # Assign display_order to top-level folders per account using CRDT data
        for account_pk, account in accounts_by_pk.items():
            ad_pk = account_data_pks.get(account_pk)
            crdt_data = account_data_crdt.get(ad_pk) if ad_pk else None
            crdt_uuids = _extract_uuid_order(crdt_data) if crdt_data else []
            top_pks = [pk for pk, parent in folder_parent_pks.items()
                       if (parent is None or parent not in folders_by_pk)
                       and folder_account_pks.get(pk) == account_pk]
            pk_order = _compute_sibling_display_order(
                top_pks, crdt_uuids, folder_uuids, folder_sort_groups, folders_by_pk
            )
            for pk, order in pk_order.items():
                folders_by_pk[pk]._display_order = order

        # Assign display_order to child folders using parent's ZMERGEABLEDATA2
        for parent_pk, crdt_data in folder_crdt_children.items():
            if parent_pk not in folders_by_pk:
                continue
            crdt_uuids = _extract_uuid_order(crdt_data)
            child_pks = [pk for pk, parent in folder_parent_pks.items() if parent == parent_pk and pk in folders_by_pk]
            pk_order = _compute_sibling_display_order(
                child_pks, crdt_uuids, folder_uuids, folder_sort_groups, folders_by_pk
            )
            for pk, order in pk_order.items():
                folders_by_pk[pk]._display_order = order

        # Sort all folder lists by display_order
        def sort_folders(container):
            container._folders.sort(key=lambda f: f.display_order)
            for folder in container._folders:
                sort_folders(folder)

        for account in accounts_by_pk.values():
            sort_folders(account)

        # Query notes (Z_ENT = 12)
        # Skip notes with NULL or empty titles
        notes_by_pk: dict[int, Note] = {}
        for row in conn.execute("""
            SELECT Z_PK, ZTITLE1, ZFOLDER, ZCREATIONDATE3, ZMODIFICATIONDATE1, ZIDENTIFIER
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT = 12
              AND ZTITLE1 IS NOT NULL
              AND ZTITLE1 <> ''
        """):
            note_id = _build_coredata_uri(db_uuid, "ICNote", row["Z_PK"])
            creation_date = _coredata_to_datetime(row["ZCREATIONDATE3"])
            modification_date = _coredata_to_datetime(row["ZMODIFICATIONDATE1"])

            # Skip notes whose folder was filtered out (orphaned/deleted folders)
            folder_pk = row["ZFOLDER"]
            folder = folders_by_pk.get(folder_pk)
            if folder_pk is not None and folder is None:
                continue

            note = Note(row["ZTITLE1"], note_id, None, creation_date, modification_date, uuid=row["ZIDENTIFIER"])
            notes_by_pk[row["Z_PK"]] = note

            if folder is not None:
                folder.add_note(note)

        # Smart folder membership: evaluate queries against note hashtags
        # 1. Query hashtags per note
        note_tags: dict[int, set[str]] = {}           # note_pk -> uppercase tag names (for smart folder queries)
        note_tags_display: dict[int, set[str]] = {}   # note_pk -> lowercase tag names (for metadata)
        for row in conn.execute("""
            SELECT ZNOTE1, ZALTTEXT
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT = 9
              AND ZTYPEUTI1 = 'com.apple.notes.inlinetextattachment.hashtag'
              AND ZNOTE1 IS NOT NULL
              AND ZALTTEXT IS NOT NULL
        """):
            tag_original = row["ZALTTEXT"].lstrip("#")
            note_tags.setdefault(row["ZNOTE1"], set()).add(tag_original.upper())
            note_tags_display.setdefault(row["ZNOTE1"], set()).add(tag_original.lower())

        # 2. Match notes to smart folders
        for note_pk, note in notes_by_pk.items():
            tags = note_tags.get(note_pk, set())
            for folder_pk, query in smart_folder_queries.items():
                if _evaluate_smart_folder_query(query, tags):
                    folders_by_pk[folder_pk].add_note(note)

        # 3. Attach display tags to Note objects
        for note_pk, note in notes_by_pk.items():
            tags = note_tags_display.get(note_pk, set())
            note.set_tags(sorted(tags))

        return list(accounts_by_pk.values())

    finally:
        conn.close()
