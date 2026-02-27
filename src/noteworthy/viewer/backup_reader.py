#!/usr/bin/python3
"""Read a noteworthy backup directory and reconstruct the folder/note tree.

Standalone module -- no noteworthy imports. Uses only stdlib so the viewer
can work from a backup directory without noteworthy installed.
"""
from __future__ import annotations

import json
import os
import pathlib
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NoteInfo:
    id: str
    name: str
    uuid: str | None
    creation_date: datetime
    modification_date: datetime
    md_path: pathlib.Path | None  # path to the .md file
    dir_path: pathlib.Path  # path to the note directory
    folder_id: str | None  # primary (home) folder id
    preview: str = ""  # first non-title line of content
    tags: list[str] = field(default_factory=list)  # authoritative tags from metadata
    first_image: str | None = None  # filename of first image attachment, if any


@dataclass
class FolderInfo:
    id: str
    name: str
    parent_id: str | None
    is_smart_folder: bool = False
    sort_order: str = "default"
    display_order: int = 0
    is_expanded: bool = True
    children: list[FolderInfo] = field(default_factory=list)
    note_ids: list[str] = field(default_factory=list)
    dir_path: pathlib.Path | None = None


@dataclass
class AccountInfo:
    id: str
    name: str
    folders: list[FolderInfo] = field(default_factory=list)
    dir_path: pathlib.Path | None = None
    tags_expanded: bool = True


@dataclass
class BackupData:
    accounts: list[AccountInfo] = field(default_factory=list)
    notes_by_id: dict[str, NoteInfo] = field(default_factory=dict)
    folders_by_id: dict[str, FolderInfo] = field(default_factory=dict)
    notes_by_md_path: dict[str, str] = field(default_factory=dict)  # resolved md path → note_id


_IMAGE_EXTENSIONS = frozenset({
    '.jpg', '.jpeg', '.png', '.gif', '.tiff', '.tif',
    '.heic', '.heif', '.webp', '.avif', '.bmp', '.svg',
})
_ATTACHMENT_LINK_RE = re.compile(r'!?\[[^\]]*\]\(Attachments/([^)]+)\)')


def _find_md_file(note_dir: pathlib.Path) -> pathlib.Path | None:
    """Find the markdown file inside a note directory."""
    for f in note_dir.iterdir():
        if f.suffix == ".md" and f.is_file():
            return f
    return None


def _extract_preview(md_path: pathlib.Path) -> str:
    """Extract a preview line from a markdown file.

    Returns the first non-empty, non-title line (stripped of markdown formatting).
    """
    try:
        with md_path.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                # Skip the title (first heading)
                if stripped.startswith("# "):
                    continue
                # Strip common markdown prefixes for preview
                for prefix in ("## ", "### ", "* ", "- ", "> ", "- [ ] ", "- [x] "):
                    if stripped.startswith(prefix):
                        stripped = stripped[len(prefix):]
                        break
                # Strip inline formatting
                for marker in ("**", "*", "~~", "++", "==", "`"):
                    stripped = stripped.replace(marker, "")
                return stripped[:200]
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def _extract_first_image(md_path: pathlib.Path) -> str | None:
    """Return the filename of the first image attachment referenced in the markdown, or None.

    Matches both ![]() image syntax and []() link syntax so backups exported before
    the image-detection fix are still handled correctly.
    """
    try:
        with md_path.open("r", encoding="utf-8") as f:
            for line in f:
                for m in _ATTACHMENT_LINK_RE.finditer(line):
                    filename = m.group(1)
                    _, ext = os.path.splitext(filename)
                    if ext and ext.lower() in _IMAGE_EXTENSIONS:
                        return filename
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _detect_smart_folder(folder_dir: pathlib.Path, metadata: dict) -> bool:
    """Detect if a folder is a smart folder.

    Checks the is_smart_folder metadata field first. Falls back to checking
    whether the folder contains symlinks (smart folders use symlinks to reference
    notes that live in other folders).
    """
    if metadata.get("is_smart_folder"):
        return True
    # Fallback: if directory contains symlinks to other directories, it's a smart folder
    try:
        for entry in folder_dir.iterdir():
            if entry.is_symlink():
                return True
    except OSError:
        pass
    return False


def _resolve_smart_folder_notes(folder_dir: pathlib.Path, all_notes: dict[str, NoteInfo]) -> list[str]:
    """Resolve symlinks in a smart folder to find the target note IDs.

    Smart folders contain symlinks pointing to note directories in other folders.
    We resolve each symlink and match it to a known note by directory path.
    """
    note_ids = []
    # Build a lookup from resolved directory path to note ID
    dir_to_note_id: dict[pathlib.Path, str] = {}
    for note in all_notes.values():
        if note.dir_path:
            try:
                dir_to_note_id[note.dir_path.resolve()] = note.id
            except OSError:
                pass

    try:
        for entry in folder_dir.iterdir():
            if entry.is_symlink():
                try:
                    target = entry.resolve()
                    if target in dir_to_note_id:
                        note_ids.append(dir_to_note_id[target])
                except OSError:
                    pass
    except OSError:
        pass
    return note_ids


def scan_backup(root_path: pathlib.Path) -> BackupData:
    """Scan a backup directory and build the folder/note tree.

    Reads .noteworthy.json files throughout the directory tree, following the same
    distributed metadata pattern as noteworthy's read_distributed_metadata().

    Args:
        root_path: Root directory of the backup (contains account subdirectories).

    Returns:
        BackupData with accounts, notes_by_id, and folders_by_id populated.
    """
    backup = BackupData()
    accounts_by_id: dict[str, AccountInfo] = {}
    folders_by_id: dict[str, FolderInfo] = {}
    notes_by_id: dict[str, NoteInfo] = {}
    # Pending folder-to-parent links
    folder_parent_ids: dict[str, str | None] = {}
    # Full folder lists per note (for resolving home folder after smart folder detection)
    note_all_folder_ids: dict[str, list[str]] = {}

    # First pass: read all .noteworthy.json files
    for metadata_path in sorted(root_path.rglob(".noteworthy.json")):
        try:
            with metadata_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        obj_type = data.get("type")
        obj_dir = metadata_path.parent

        if obj_type == "account":
            account = AccountInfo(
                id=data["id"],
                name=data["name"],
                dir_path=obj_dir,
                tags_expanded=data.get("tags_expanded", True),
            )
            accounts_by_id[account.id] = account
            backup.accounts.append(account)

        elif obj_type == "folder":
            is_smart = _detect_smart_folder(obj_dir, data)
            folder = FolderInfo(
                id=data["id"],
                name=data["name"],
                parent_id=data.get("parent_id"),
                is_smart_folder=is_smart,
                sort_order=data.get("sort_order", "default"),
                display_order=data.get("display_order", 0),
                is_expanded=data.get("is_expanded", True),
                dir_path=obj_dir,
            )
            folders_by_id[folder.id] = folder
            folder_parent_ids[folder.id] = data.get("parent_id")

        elif obj_type == "note":
            md_path = _find_md_file(obj_dir)
            folder_ids = data.get("folders", [])

            note = NoteInfo(
                id=data["id"],
                name=data["name"],
                uuid=data.get("uuid"),
                creation_date=datetime.fromisoformat(data["creation_date"]),
                modification_date=datetime.fromisoformat(data["modification_date"]),
                md_path=md_path,
                dir_path=obj_dir,
                folder_id=None,  # resolved in third pass after smart folder detection
                preview=_extract_preview(md_path) if md_path else "",
                tags=data.get("tags", []),
                first_image=_extract_first_image(md_path) if md_path else None,
            )
            notes_by_id[note.id] = note
            note_all_folder_ids[note.id] = folder_ids

    # Second pass: link folders to parents
    for folder_id, parent_id in folder_parent_ids.items():
        folder = folders_by_id[folder_id]
        if parent_id in accounts_by_id:
            accounts_by_id[parent_id].folders.append(folder)
        elif parent_id in folders_by_id:
            folders_by_id[parent_id].children.append(folder)

    # Third pass: link notes to folders (pick first non-smart folder as home)
    for note in notes_by_id.values():
        folder_ids = note_all_folder_ids.get(note.id, [])
        home_id = None
        for fid in folder_ids:
            if fid in folders_by_id and not folders_by_id[fid].is_smart_folder:
                home_id = fid
                break
        if home_id is None and folder_ids:
            home_id = folder_ids[0]
        note.folder_id = home_id
        if home_id and home_id in folders_by_id:
            folders_by_id[home_id].note_ids.append(note.id)

    # Fourth pass: resolve smart folder symlinks
    for folder in folders_by_id.values():
        if folder.is_smart_folder and folder.dir_path:
            resolved_ids = _resolve_smart_folder_notes(folder.dir_path, notes_by_id)
            folder.note_ids = resolved_ids

    backup.notes_by_id = notes_by_id
    backup.folders_by_id = folders_by_id
    backup.notes_by_md_path = {
        str(note.md_path.resolve()): note.id
        for note in notes_by_id.values()
        if note.md_path
    }
    return backup


def collect_tags(backup: BackupData) -> list[str]:
    """Return sorted unique tag names (without #) from note metadata."""
    tags: set[str] = set()
    for note in backup.notes_by_id.values():
        tags.update(note.tags)
    return sorted(tags)
