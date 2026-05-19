#!/usr/bin/env python3
import argparse
import os
import pathlib
import shutil
import sys

from noteworthy.note_copy import make_markdown_copy
from noteworthy.extract_notes_db import extract_folders_and_notes
from noteworthy.notes_datatypes import (
    Folder, Note, Account, write_metadata_file, read_distributed_metadata
)
from noteworthy.viewer import install_viewer
from noteworthy.obsidian import sync as obsidian_sync
from noteworthy.obsidian.target_state import TargetState, inspect as inspect_target

__all__ = ["make_copies"]

_target_path: pathlib.Path | None = None
_db_path: pathlib.Path | None = None
_verbose: bool = False
_counts: dict[str, int] = {"created": 0, "updated": 0, "deleted": 0, "moved": 0}


class CopyError(RuntimeError):
    pass


def _parse_args(args: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="noteworthy",
        description="Export Apple Notes to Markdown files"
    )
    parser.add_argument(
        "target_directory",
        help="Directory to export notes to"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Print export progress. Repeat (-vv) to log every note and attachment "
             "actually written or copied — useful for diagnosing spurious rewrites."
    )
    parser.add_argument(
        "-o", "--obsidian",
        action="store_true",
        help="Export as an Obsidian vault (wikilinks, frontmatter Properties, flat note files) "
             "instead of the default backup layout"
    )
    return parser.parse_args(args)


def _set_target_path(target_path: pathlib.Path) -> None:
    global _target_path
    if not target_path.exists():
        raise CopyError(f"target directory {target_path} does not exist")
    elif not os.access(target_path, os.W_OK):
        raise CopyError(f"cannot write to target directory {target_path}")
    elif not target_path.is_dir():
        raise CopyError(f"target {target_path} is not a directory")
    _target_path = target_path


def _move_to_deleted(item_path: pathlib.Path, item_name: str) -> None:
    """Move a deleted item to the Deleted directory, handling name collisions."""
    deleted_dir = _target_path / "Deleted"
    deleted_dir.mkdir(exist_ok=True)

    # Handle name collisions with counter suffix
    dest_path = deleted_dir / item_name
    counter = 2
    while dest_path.exists():
        dest_path = deleted_dir / f"{item_name}_{counter}"
        counter += 1

    shutil.move(str(item_path), str(dest_path))


def _sync_folder(apple_folder: Folder, local_folder: Folder) -> None:
    path_with_target = _target_path / apple_folder.choose_path()

    if local_folder and local_folder.path and local_folder.path != path_with_target:
        old_path = local_folder.path
        if old_path.exists():
            if local_folder.parent != apple_folder.parent:
                print(f"    relocating folder {local_folder.name}")
            else:
                print(f"    renaming folder {local_folder.name} to {apple_folder.name}")
            shutil.move(str(old_path), str(path_with_target))

    # Always ensure directory exists (handles stale paths from previous runs)
    if not path_with_target.exists():
        if _verbose:
            print(f"    creating folder {apple_folder.name} at {path_with_target}")
        path_with_target.mkdir(parents=True, exist_ok=True)
    apple_folder.set_path(path_with_target)

    # Write distributed metadata file
    write_metadata_file(apple_folder, path_with_target)


def _copy_note(
    apple_note: Note, local_note: Note, note_path_by_uuid: dict[str, pathlib.Path],
    synced_folder_paths: set[pathlib.Path] = None,
) -> None:
    # Use pre-computed path from mapping
    path_with_target = apple_note.path
    if not path_with_target:
        # Fallback if path wasn't pre-computed (shouldn't happen)
        path_with_target = _target_path / apple_note.choose_path()
        apple_note.set_path(path_with_target)

    # Handle note relocation (move or rename) if path changed
    if local_note and local_note.path and local_note.path != path_with_target:
        if local_note.path.exists():
            if synced_folder_paths and local_note.path in synced_folder_paths:
                # Note's old path is a folder directory (note/folder name collision).
                # Don't move — it would destroy the folder. Force re-extraction at the new path.
                local_note = None
            else:
                if local_note.home_folder != apple_note.home_folder:
                    print(f"    relocating note '{local_note.name}'")
                else:
                    print(f"    renaming note '{local_note.name}' to '{apple_note.name}'")
                shutil.move(str(local_note.path), str(path_with_target))
                _counts["moved"] += 1

    # Create or update note content if needed
    if not local_note:
        if _verbose:
            print(f"    creating note '{apple_note.name}' in {apple_note.home_folder.name}")
        _counts["created"] += 1
        make_markdown_copy(apple_note, path_with_target, verbose=_verbose, note_path_by_uuid=note_path_by_uuid,
                          db_path=_db_path)
    elif local_note.modification_date != apple_note.modification_date:
        if _verbose:
            print(f"    updating note '{apple_note.name}'")
        _counts["updated"] += 1
        make_markdown_copy(apple_note, path_with_target, verbose=_verbose, note_path_by_uuid=note_path_by_uuid,
                          db_path=_db_path)
    elif not local_note.path:
        if _verbose:
            print(f"    recreating note '{apple_note.name}' (missing from disk)")
        _counts["created"] += 1
        make_markdown_copy(apple_note, path_with_target, verbose=_verbose, note_path_by_uuid=note_path_by_uuid,
                          db_path=_db_path)

    # Write distributed metadata file for the note
    write_metadata_file(apple_note, path_with_target)


def _make_unique_symlink_name(base_name: str, existing_names: set) -> str:
    """Generate a unique name for a symlink, handling collisions."""
    if base_name not in existing_names:
        return base_name
    counter = 2
    while f"{base_name}_{counter}" in existing_names:
        counter += 1
    return f"{base_name}_{counter}"


def _sync_smart_folder(apple_folder: Folder) -> None:
    """Sync a smart folder by creating symlinks to notes in their home folders."""
    # Create smart folder directory
    smart_folder_path = _target_path / apple_folder.choose_path()
    if not smart_folder_path.exists():
        if _verbose:
            print(f"    creating smart folder {apple_folder.name} at {smart_folder_path}")
        smart_folder_path.mkdir(parents=True, exist_ok=True)
    apple_folder.set_path(smart_folder_path)

    # Write metadata file
    write_metadata_file(apple_folder, smart_folder_path)

    # Track existing symlinks for cleanup
    existing_symlinks = {item.name: item for item in smart_folder_path.iterdir() if item.is_symlink()}
    valid_symlink_names = set()
    used_names = set()

    # Create symlinks for each note
    for note in apple_folder.notes:
        if not note.path:
            continue

        # Handle name collisions
        base_name = note.path.name
        symlink_name = _make_unique_symlink_name(base_name, used_names)
        used_names.add(symlink_name)
        valid_symlink_names.add(symlink_name)

        symlink_path = smart_folder_path / symlink_name
        relative_target = os.path.relpath(note.path, smart_folder_path)

        # Update symlink if needed
        if symlink_path.is_symlink():
            if os.readlink(symlink_path) == relative_target:
                continue
            symlink_path.unlink()
        elif symlink_path.exists():
            if _verbose:
                print(f"    warning: {symlink_path} exists but is not a symlink")
            continue

        if _verbose:
            print(f"    creating symlink {symlink_name} -> {relative_target}")
        symlink_path.symlink_to(relative_target)

    # Clean up stale symlinks
    for symlink_name, symlink_path in existing_symlinks.items():
        if symlink_name not in valid_symlink_names:
            if _verbose:
                print(f"    removing stale symlink {symlink_name}")
            symlink_path.unlink()


def _sync_account(apple_account: Account, local_account: Account) -> None:

    recently_deleted = apple_account.find_folder_by_name(["Recently Deleted"])
    if recently_deleted:
        apple_account.folders.remove(recently_deleted)

    local_folders_by_id = {f.id: f for f in local_account.all_folders()} if local_account else {}
    apple_folders_by_id = {f.id: f for f in apple_account.all_folders()}

    # Compute the target path for the account
    path_with_target = _target_path / apple_account.choose_path()

    # Handle account rename: if local account exists with different path, move it
    if local_account and local_account.path and local_account.path != path_with_target:
        if local_account.path.exists():
            print(f"    renaming account {local_account.name} to {apple_account.name}")
            shutil.move(str(local_account.path), str(path_with_target))

    # Always ensure account directory exists (handles stale paths from previous runs)
    if not path_with_target.exists():
        if _verbose:
            print(f"    creating account {apple_account.name} at {path_with_target}")
        path_with_target.mkdir(parents=True, exist_ok=True)
    apple_account.set_path(path_with_target)

    # Write distributed metadata file
    write_metadata_file(apple_account, path_with_target)

    for folder_id, apple_folder in apple_folders_by_id.items():
        local_folder = local_folders_by_id.get(folder_id, None)
        _sync_folder(apple_folder, local_folder)

    # Collect synced folder paths so _copy_note can detect note/folder name collisions
    synced_folder_paths = {f.path for f in apple_account.all_folders() if f.path}

    for folder_id, local_folder in local_folders_by_id.items():
        if folder_id not in apple_folders_by_id:
            if local_folder.path and local_folder.path.exists():
                print(f"    folder {local_folder.name} deleted from Apple Notes")
                _move_to_deleted(local_folder.path, local_folder.name)
                _counts["deleted"] += 1

    local_notes_by_id = {n.id: n for n in local_account.all_notes()} if local_account else {}
    apple_notes_by_id = {n.id: n for n in apple_account.all_notes()}

    for note_id, local_note in local_notes_by_id.items():
        if note_id not in apple_notes_by_id:
            if local_note.path and local_note.path.exists():
                print(f"    note '{local_note.name}' deleted from Apple Notes")
                _move_to_deleted(local_note.path, local_note.name)
                _counts["deleted"] += 1

    # Pre-compute all note paths to handle collisions correctly for note-to-note links
    # We need to iterate notes in a consistent order so collision suffixes are deterministic
    note_path_by_uuid: dict[str, pathlib.Path] = {}
    for apple_note in apple_account.all_notes():
        path_with_target = _target_path / apple_note.choose_path()
        apple_note.set_path(path_with_target)
        if apple_note.uuid:
            # Key by uppercase UUID for case-insensitive matching
            note_path_by_uuid[apple_note.uuid.upper()] = path_with_target

    for note_id, apple_note in apple_notes_by_id.items():
        local_note = local_notes_by_id.get(note_id, None)
        _copy_note(apple_note, local_note, note_path_by_uuid, synced_folder_paths)

    for apple_folder in [f for f in apple_account.folders if f.is_smart_folder]:
        _sync_smart_folder(apple_folder)


def _update_copy(local_copy_accounts: list[Account], apple_notes_accounts: list[Account]) -> None:
    local_by_id = {a.id: a for a in local_copy_accounts}
    apple_by_id = {a.id: a for a in apple_notes_accounts}

    for account_id, apple_account in apple_by_id.items():
        local_account = local_by_id.get(account_id, None)
        _sync_account(apple_account, local_account)

    for account_id, local_account in local_by_id.items():
        if account_id not in apple_by_id:
            if local_account.path and local_account.path.exists():
                print(f"    account {local_account.name} deleted from Apple Notes")
                _move_to_deleted(local_account.path, local_account.name)
                _counts["deleted"] += 1


def _make_backup_copies():
    print("  loading existing copy")

    # Try distributed metadata first, fall back to legacy single file
    legacy_ctl_file = _target_path / ".noteworthy.json"
    local_copy_accounts = read_distributed_metadata(_target_path)

    note_count = len([n for a in local_copy_accounts for n in a.all_notes() if n.path])
    print(f"    found {note_count} already-copied notes")

    print("  loading Apple Notes")
    apple_notes_accounts = extract_folders_and_notes(db_path=_db_path)
    note_count = len([n for a in apple_notes_accounts for n in a.all_notes()])
    print(f"    found {note_count} notes in Apple Notes")

    print("  updating local copy")
    _counts.update(created=0, updated=0, deleted=0, moved=0)
    _update_copy(local_copy_accounts, apple_notes_accounts)

    parts = [f"{v} {k}" for k, v in _counts.items() if v > 0]
    print(f"    {', '.join(parts)}" if parts else "    no changes")

    print("  finishing up")

    # Remove legacy root-level metadata file if it exists (we now use distributed files)
    if legacy_ctl_file.exists():
        legacy_ctl_file.unlink()

    print("  installing viewer")
    install_viewer(_target_path)


_OBSIDIAN_WITHOUT_FLAG_MSG = (
    "error: target looks like an Obsidian vault but --obsidian was not specified. "
    "Re-run with --obsidian, or choose a different target."
)
_BACKUP_WITH_OBSIDIAN_FLAG_MSG = (
    "error: target contains a backup-mode export. --obsidian would corrupt it. "
    "Re-run without --obsidian, or choose a different target."
)
_UNRELATED_TARGET_MSG = (
    "error: target directory is not empty and contains no recognized export markers. "
    "Choose an empty directory or an existing Noteworthy/Obsidian export target."
)


def _enforce_mode_state_compatibility(target_path: pathlib.Path, obsidian: bool) -> None:
    """Refuse mode/state combinations that would corrupt an existing export.

    See obsidian_requirements.md §11.1 for the full behavior table.
    """
    try:
        state = inspect_target(target_path)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    if state is TargetState.OBSIDIAN and not obsidian:
        print(_OBSIDIAN_WITHOUT_FLAG_MSG, file=sys.stderr)
        sys.exit(1)
    if state is TargetState.BACKUP and obsidian:
        print(_BACKUP_WITH_OBSIDIAN_FLAG_MSG, file=sys.stderr)
        sys.exit(1)
    if state is TargetState.UNRELATED:
        print(_UNRELATED_TARGET_MSG, file=sys.stderr)
        sys.exit(1)


def make_copies(args: list[str], db_path: pathlib.Path = None) -> None:
    global _verbose, _db_path
    parsed = _parse_args(args)
    target_path = pathlib.Path(parsed.target_directory)

    _enforce_mode_state_compatibility(target_path, parsed.obsidian)

    if parsed.obsidian:
        obsidian_sync.run(target_path, db_path=db_path, verbose=parsed.verbose)
        return

    # Backup mode (existing behavior).
    try:
        _verbose = parsed.verbose
        _db_path = db_path
        _set_target_path(target_path)
        _make_backup_copies()
    except CopyError as e:
        print(f"error: {e}")


def main():
    args = sys.argv[1:]  # Skip the script name
    make_copies(args)


if __name__ == "__main__":
    main()
