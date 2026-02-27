#!/usr/bin/python3
"""Full-text search using SQLite FTS5.

Builds an in-memory FTS5 index from backup markdown files and provides
search with BM25 ranking and highlighted snippets.
"""
from __future__ import annotations

import re
import sqlite3

try:
    from noteworthy.viewer.backup_reader import BackupData
except ImportError:
    from backup_reader import BackupData


def strip_markdown(text: str) -> str:
    """Remove markdown formatting markers, keeping only text content.

    Used to prepare note content for indexing.
    """
    # Remove code blocks entirely (they're less useful for search)
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    # Remove image/link markdown syntax, keep display text
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    # Remove inline formatting markers
    for marker in ("**", "++", "~~", "==", "`"):
        text = text.replace(marker, "")
    # Remove heading markers
    text = re.sub(r"^#{1,3}\s+", "", text, flags=re.MULTILINE)
    # Remove list markers
    text = re.sub(r"^[\*\-]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^- \[[x ]\]\s+", "", text, flags=re.MULTILINE)
    # Remove blockquote markers
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)
    # Remove table formatting
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"^[\s\-:]+$", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_folder_path(note_id: str, backup: BackupData) -> str:
    """Build a human-readable folder path for a note."""
    note = backup.notes_by_id.get(note_id)
    if not note or not note.folder_id:
        return ""
    folder = backup.folders_by_id.get(note.folder_id)
    if not folder:
        return ""
    # Walk up to build path
    parts = []
    current = folder
    while current:
        parts.append(current.name)
        parent_id = current.parent_id
        if parent_id and parent_id in backup.folders_by_id:
            current = backup.folders_by_id[parent_id]
        else:
            break
    parts.reverse()
    return "/".join(parts)


class SearchIndex:
    """In-memory FTS5 search index for notes."""

    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.execute(
            "CREATE VIRTUAL TABLE notes_fts USING fts5("
            "    note_id UNINDEXED, title, content, folder_path,"
            "    tokenize='porter unicode61'"
            ")"
        )
        self._note_count = 0

    def build(self, backup: BackupData):
        """Populate the search index from backup data."""
        rows = []
        for note_id, note in backup.notes_by_id.items():
            content = ""
            if note.md_path and note.md_path.exists():
                try:
                    content = note.md_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    content = ""
            folder_path = _get_folder_path(note_id, backup)
            rows.append((note_id, note.name, strip_markdown(content), folder_path))

        self._conn.executemany(
            "INSERT INTO notes_fts (note_id, title, content, folder_path) VALUES (?, ?, ?, ?)",
            rows,
        )
        self._note_count = len(rows)

    @property
    def note_count(self) -> int:
        return self._note_count

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search notes and return ranked results with snippets.

        Args:
            query: Search query string.
            limit: Maximum number of results.

        Returns:
            List of dicts with keys: note_id, title, snippet, folder_path, rank.
        """
        if not query or not query.strip():
            return []

        # Escape special FTS5 characters to prevent query syntax errors
        safe_query = query.strip()
        # Quote individual terms to prevent FTS5 syntax issues with special chars
        terms = safe_query.split()
        quoted_terms = []
        for term in terms:
            # Remove characters that are problematic in FTS5 queries
            cleaned = re.sub(r'[^\w\s]', '', term)
            if cleaned:
                quoted_terms.append(f'"{cleaned}"')
        if not quoted_terms:
            return []
        fts_query = " ".join(quoted_terms)

        try:
            cursor = self._conn.execute(
                "SELECT note_id, title, snippet(notes_fts, 2, '<mark>', '</mark>', '...', 40), "
                "folder_path, rank "
                "FROM notes_fts WHERE notes_fts MATCH ? "
                "ORDER BY rank "
                "LIMIT ?",
                (fts_query, limit),
            )
        except sqlite3.OperationalError:
            return []

        results = []
        for row in cursor:
            results.append({
                "note_id": row[0],
                "title": row[1],
                "snippet": row[2],
                "folder_path": row[3],
                "rank": row[4],
            })
        return results

    def close(self):
        self._conn.close()
