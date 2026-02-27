"""
Data loaders for Apple Notes exporter.

Provides DatabaseNoteDataLoader for loading note data from the Apple Notes
SQLite database (NoteStore.sqlite).
"""

import sqlite3
from pathlib import Path
from typing import Optional, Dict, List, Any


class DatabaseNoteDataLoader:
    """Loads note data from the real Apple Notes database."""

    def __init__(self, db_path: str):
        """
        Initialize database loader.

        Args:
            db_path: Path to NoteStore.sqlite database
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def get_note_data(self, note_id: int) -> bytes:
        """Get compressed protobuf data for a note from database."""
        cursor = self.conn.execute(
            "SELECT ZDATA FROM ZICNOTEDATA WHERE ZNOTE = ?",
            (note_id,)
        )
        row = cursor.fetchone()
        if not row:
            raise ValueError(f"Note {note_id} not found")
        return row['ZDATA']

    def get_attachment_metadata(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Get attachment metadata from database.

        Handles both regular attachments (Z_ENT=5) and link attachments (Z_ENT=9).
        For link attachments, UUIDs from the protobuf may only match the first 3
        segments with the database UUID, so we use prefix matching.
        """
        # First try exact match for regular attachments (Z_ENT=5)
        cursor = self.conn.execute("""
            SELECT
                att.ZTITLE as title,
                att.ZALTTEXT as alt_text,
                media.ZIDENTIFIER as media_uuid,
                media.ZFILENAME as filename,
                media.ZGENERATION1 as generation
            FROM ZICCLOUDSYNCINGOBJECT att
            LEFT JOIN ZICCLOUDSYNCINGOBJECT media ON att.ZMEDIA = media.Z_PK
            WHERE att.Z_ENT = 5
              AND att.ZIDENTIFIER = ?
        """, (uuid,))

        row = cursor.fetchone()
        if row:
            # Resolve file path from media directory
            # ZGENERATION1 contains the subdirectory name when present, otherwise file is directly in media_uuid folder
            file_path = None
            if row['media_uuid'] and row['filename']:
                base_path = Path(self.db_path).parent / "Media"
                media_dir = base_path / row['media_uuid']

                if row['generation']:
                    expected_path = media_dir / row['generation'] / row['filename']
                else:
                    expected_path = media_dir / row['filename']

                if expected_path.exists():
                    file_path = str(expected_path)
                else:
                    print(f"Warning: Attachment file not found at expected path: {expected_path}")

            return {
                'uuid': uuid,
                'type': None,  # Type is in the attachment object already
                'title': row['title'],
                'filename': row['filename'],
                'file_path': file_path,
                'alt_text': row['alt_text']
            }

        # Try matching link attachments (Z_ENT=9) by UUID prefix
        # Protobuf UUIDs may differ in the 4th segment from database UUIDs
        uuid_prefix = '-'.join(uuid.split('-')[:3])
        cursor = self.conn.execute("""
            SELECT
                ZIDENTIFIER as uuid,
                ZALTTEXT as alt_text,
                ZTOKENCONTENTIDENTIFIER as token_content_identifier
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE Z_ENT = 9
              AND ZIDENTIFIER LIKE ? || '%'
        """, (uuid_prefix,))

        row = cursor.fetchone()
        if row:
            # Link attachment - alt_text contains the linked note's title
            # token_content_identifier contains "applenotes:note/UUID?..." pointing to the target note
            return {
                'uuid': row['uuid'],
                'type': 'link',
                'title': None,
                'filename': None,
                'file_path': None,
                'alt_text': row['alt_text'],
                'linked_note_name': row['alt_text'],  # The name of the note this links to
                'token_content_identifier': row['token_content_identifier']
            }

        return None

    def get_table_data(self, uuid: str) -> Optional[bytes]:
        """Get table data from database."""
        cursor = self.conn.execute("""
            SELECT ZMERGEABLEDATA1
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE ZIDENTIFIER = ? AND ZTYPEUTI = 'com.apple.notes.table'
        """, (uuid,))

        row = cursor.fetchone()
        if not row or not row['ZMERGEABLEDATA1']:
            return None

        return row['ZMERGEABLEDATA1']

    def get_gallery_children(self, uuid: str) -> List[Dict[str, Any]]:
        """Get child attachments for a gallery attachment.

        Gallery attachments (com.apple.notes.gallery) contain multiple images as children.
        Children are linked via ZPARENTATTACHMENT pointing to the gallery's Z_PK.
        """
        # First get the gallery's Z_PK
        cursor = self.conn.execute("""
            SELECT Z_PK
            FROM ZICCLOUDSYNCINGOBJECT
            WHERE ZIDENTIFIER = ? AND ZTYPEUTI = 'com.apple.notes.gallery'
        """, (uuid,))

        row = cursor.fetchone()
        if not row:
            return []

        gallery_pk = row['Z_PK']

        # Get all child attachments
        cursor = self.conn.execute("""
            SELECT
                child.ZIDENTIFIER as uuid,
                child.ZTITLE as title,
                child.ZTYPEUTI as type,
                child.ZALTTEXT as alt_text,
                media.ZIDENTIFIER as media_uuid,
                media.ZFILENAME as filename,
                media.ZGENERATION1 as generation
            FROM ZICCLOUDSYNCINGOBJECT child
            LEFT JOIN ZICCLOUDSYNCINGOBJECT media ON child.ZMEDIA = media.Z_PK
            WHERE child.ZPARENTATTACHMENT = ?
            ORDER BY child.Z_PK
        """, (gallery_pk,))

        children = []
        for row in cursor.fetchall():
            file_path = None
            if row['media_uuid'] and row['filename']:
                base_path = Path(self.db_path).parent / "Media"
                media_dir = base_path / row['media_uuid']

                if row['generation']:
                    expected_path = media_dir / row['generation'] / row['filename']
                else:
                    expected_path = media_dir / row['filename']

                if expected_path.exists():
                    file_path = str(expected_path)

            children.append({
                'uuid': row['uuid'],
                'type': row['type'],
                'title': row['title'],
                'filename': row['filename'],
                'file_path': file_path,
                'alt_text': row['alt_text']
            })

        return children

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

