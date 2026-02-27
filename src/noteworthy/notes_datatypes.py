from __future__ import annotations

__all__ = ["Account", "Folder", "Note", "write_metadata_file", "read_distributed_metadata", "_sanitize_name"]

import json
import pathlib
import urllib.parse
from datetime import datetime
from functools import total_ordering


def _sanitize_name(name: str) -> str:
    """
    Turn a free-form folder or note name into a safe filename:
    - Replace '/' with an underbar and ':' with a dash.
    - Percent-encode control characters (ASCII < 0x20).
    """
    # 1) map problematic chars to tokens
    name = (name.replace('/', '_').replace(':', '-')
            .replace('"', '“').replace("\t", " "))

    # 2) percent-encode control characters, leave everything else unchanged
    result = []
    for ch in name:
        if ord(ch) < 0x20:
            result.append(urllib.parse.quote(ch, safe=''))
        else:
            result.append(ch)
    return ''.join(result)


@total_ordering
class _NotesObject:
    def __init__(self, name, obj_id, path):
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(obj_id, str) or not obj_id:
            raise ValueError("obj_id must be a non-empty string")
        self._name = name
        self._id = obj_id
        self._path = pathlib.Path(path) if path else None

    @property
    def name(self):
        """Return the human-readable name of the object."""
        return self._name

    @property
    def id(self):
        """Return the Core Data URI for this object."""
        return self._id

    @property
    def path(self):
        """Return a filesystem-friendly path for the object."""
        return self._path

    def set_path(self, path: pathlib.Path):
        """Set the path for the object."""
        self._path = pathlib.Path(path) if path else None

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name!r} id={self.id!r}>"

    def __eq__(self, other):
        """
        Two NotesObject instances are equal if they have the same Core Data URI.
        """
        if isinstance(other, _NotesObject):
            return self.id == other.id
        return False

    def __lt__(self, other):
        if not isinstance(other, _NotesObject):
            return NotImplemented
        # sort by name first, then by id as tiebreaker
        return (self.name, self.id) < (other.name, other.id)

    def __hash__(self):
        return hash(self.id)


class _FolderContainer(_NotesObject):
    def __init__(self, name, obj_id, path):
        super().__init__(name, obj_id, path)
        self._folders = []

    @property
    def folders(self):
        return self._folders

    def add_folder(self, folder):
        self._folders.append(folder)
        folder.set_parent(self)

    def find_folder_by_name(self, folder_path: list[str]) -> Folder | None:
        if not folder_path:
            return None
        for folder in self.folders:
            if folder.name == folder_path[0]:
                if len(folder_path) == 1:
                    return folder
                return folder.find_folder_by_name(folder_path[1:])
        return None

    def all_folders(self) -> list[Folder]:
        ret = []
        for f in self.folders:
            ret.append(f)
            ret += f.all_folders()
        return ret


class Note(_NotesObject):
    def __init__(self, name, obj_id, path, creation_date, modification_date, uuid=None, tags=None):
        super().__init__(name, obj_id, path)
        self._creation_date = creation_date
        self._modification_date = modification_date
        self._uuid = uuid
        self._folders = []
        self._tags: list[str] = list(tags) if tags else []

    @property
    def uuid(self):
        """Return the note's UUID (ZIDENTIFIER from Apple Notes database)."""
        return self._uuid

    @property
    def tags(self) -> list[str]:
        """Return the note's tags (lowercase, without #)."""
        return self._tags

    def set_tags(self, tags: list[str]) -> None:
        """Set the note's tags (lowercase, without #)."""
        self._tags = list(tags)

    @property
    def creation_date(self):
        return self._creation_date

    @property
    def modification_date(self):
        return self._modification_date

    @property
    def folders(self):
        return self._folders

    def add_folder(self, folder: Folder):
        self._folders.append(folder)

    def to_metadata_dict(self) -> dict:
        """Return metadata dict for distributed .noteworthy.json file."""
        return {
            "type": "note",
            "name": self.name,
            "id": self.id,
            "uuid": self._uuid,
            "creation_date": self.creation_date.isoformat(),
            "modification_date": self.modification_date.isoformat(),
            "folders": (
                sorted(f.id for f in self._folders if not f.is_smart_folder)
                + sorted(f.id for f in self._folders if f.is_smart_folder)
            ),
            "tags": sorted(self._tags),
        }

    @classmethod
    def from_metadata_dict(cls, data: dict) -> Note:
        """Create a Note from a distributed metadata dict (without folder linkage)."""
        return cls(
            data["name"],
            data["id"],
            None,
            datetime.fromisoformat(data["creation_date"]),
            datetime.fromisoformat(data["modification_date"]),
            uuid=data.get("uuid"),
            tags=data.get("tags", []),
        )

    @property
    def home_folder(self):
        """Returns None if the note is not in a folder, otherwise the first folder that isn't a smart folder."""
        for folder in self._folders:
            if not folder.is_smart_folder:
                return folder
        return None

    @property
    def smart_folders(self):
        home = self.home_folder
        return [f for f in self._folders if f != home]

    def choose_path(self, folder=None) -> pathlib.Path:
        """Return a filesystem path for the note directory (not the .md file).

        Each note gets its own directory containing:
        - The note's .md file (named after the directory)
        - An 'Attachments' subdirectory if there are file attachments

        Names are assigned to same-name notes in list order, skipping any names
        already occupied by sibling subfolders (which can collide after sanitization,
        e.g. a note "Evan 1:1" and folder "Evan 1:1" both sanitize to "Evan 1-1").

        All comparisons are case-insensitive to prevent collisions on macOS
        (which uses a case-insensitive filesystem by default).
        """
        folder = self.home_folder if folder is None else folder
        home_path = folder.choose_path() if folder is not None else pathlib.Path("/")

        sanitized = _sanitize_name(self.name)
        sanitized_lower = sanitized.lower()

        # Names occupied by sibling subfolders (case-insensitive for macOS)
        occupied = {_sanitize_name(f.name).lower() for f in folder.folders}

        # Group notes by case-insensitive sanitized name so "todo" and "Todo" collide.
        # Sort by id for deterministic ordering across Apple-extracted and metadata-reconstructed data.
        notes_with_this_name = sorted(
            [n for n in folder.notes if _sanitize_name(n.name).lower() == sanitized_lower],
            key=lambda n: n.id,
        )
        my_index = notes_with_this_name.index(self)
        suffix = 0
        assigned = 0
        while True:
            candidate = sanitized if suffix == 0 else f"{sanitized}_{suffix + 1}"
            if candidate.lower() not in occupied:
                if assigned == my_index:
                    return home_path / candidate
                assigned += 1
            suffix += 1

    def __eq__(self, other):
        if not isinstance(other, Note):
            return NotImplemented
        self_folders = [f.id for f in self._folders]
        other_folders = [f.id for f in other._folders]
        return (self.name == other.name and
                self.id == other.id and
                self._uuid == other._uuid and
                self.creation_date == other.creation_date and
                self.modification_date == other.modification_date and
                set(self_folders) == set(other_folders))

    def __hash__(self):
        folder_ids = [f.id for f in self._folders]
        return hash((self.name, self.id, self._uuid, self.creation_date, self.modification_date, tuple(folder_ids)))


class Folder(_FolderContainer):
    def __init__(
        self, name, obj_id, path, is_smart_folder: bool = False, sort_order: str = "default",
        display_order: int = 0, is_expanded: bool = True,
    ):
        super().__init__(name, obj_id, path)
        self._notes = []
        self._parent = None
        self._is_smart_folder = is_smart_folder
        self._sort_order = sort_order
        self._display_order = display_order
        self._is_expanded = is_expanded

    def choose_path(self) -> pathlib.Path:
        """Return a filesystem-friendly path for the folder."""
        parent_path = self.parent.choose_path() if self.parent is not None else pathlib.Path("/")
        return parent_path / _sanitize_name(self.name)

    @property
    def full_name(self):
        """Return the full name of the folder, including the parent folder's name."""
        if self.parent is None:
            return self.name
        return f"{self.parent.full_name}/{self.name}"

    @property
    def notes(self):
        """Return a list of Notes in this folder and all subfolders."""
        return self._notes

    @property
    def is_smart_folder(self) -> bool:
        """Return True if this folder is a smart folder (has a query definition in the database)."""
        return self._is_smart_folder

    @property
    def sort_order(self) -> str:
        """Return the folder's note sort order: 'default', 'date_edited', 'date_created', or 'title'."""
        return self._sort_order

    @property
    def display_order(self) -> int:
        """Return the folder's display order (lower = earlier in sidebar)."""
        return self._display_order

    @property
    def is_expanded(self) -> bool:
        """Return whether the folder is expanded in the sidebar."""
        return self._is_expanded

    def add_note(self, note):
        self._notes.append(note)
        note.add_folder(self)

    def all_notes(self):
        return self.notes + [n for folder in self.folders for n in folder.all_notes()]

    def find_note_by_name(self, name, folder_path: list[str] = None) -> Note | None:
        if folder_path:
            if folder := self.find_folder_by_name(folder_path):
                return folder.find_note_by_name(name)
        else:
            for n in self.notes:
                if n.name == name:
                    return n
            for folder in self.folders:
                if n := folder.find_note_by_name(name):
                    return n
        return None

    def set_parent(self, parent):
        self._parent = parent

    @property
    def parent(self):
        return self._parent

    def to_metadata_dict(self) -> dict:
        """Return metadata dict for distributed .noteworthy.json file."""
        return {
            "type": "folder",
            "name": self.name,
            "id": self.id,
            "parent_id": self.parent.id if self.parent else None,
            "is_smart_folder": self._is_smart_folder,
            "sort_order": self._sort_order,
            "display_order": self._display_order,
            "is_expanded": self._is_expanded,
        }

    @classmethod
    def from_metadata_dict(cls, data: dict) -> Folder:
        """Create a Folder from a distributed metadata dict (without parent linkage)."""
        return cls(
            data["name"], data["id"], None,
            is_smart_folder=data.get("is_smart_folder", False),
            sort_order=data.get("sort_order", "default"),
            display_order=data.get("display_order", 0),
            is_expanded=data.get("is_expanded", True),
        )

    def __eq__(self, other):
        if not isinstance(other, Folder):
            return NotImplemented
        return (
            self.name == other.name and
            self.id == other.id and
            self.parent.id == other.parent.id and
            self._folders == other._folders
        )

    def __hash__(self):
        # _parent may be None. _folders is a list, so we tuple-ify it
        return hash((self.name, self.id, self.parent.id, tuple(self._folders)))


class Account(_FolderContainer):
    """
    Wrapper around an Account SBObject to expose common properties.
    """
    def __init__(self, name, obj_id, path, tags_expanded: bool = True):
        super().__init__(name, obj_id, path)
        self._tags_expanded = tags_expanded

    def choose_path(self) -> pathlib.Path:
        return pathlib.Path(_sanitize_name(self.name))

    @property
    def full_name(self):
        return self.name

    @property
    def tags_expanded(self) -> bool:
        return self._tags_expanded

    def to_metadata_dict(self) -> dict:
        """Return metadata dict for distributed .noteworthy.json file."""
        return {
            "type": "account",
            "name": self.name,
            "id": self.id,
            "tags_expanded": self._tags_expanded,
        }

    @classmethod
    def from_metadata_dict(cls, data: dict) -> Account:
        """Create an Account from a distributed metadata dict."""
        return cls(data["name"], data["id"], None, tags_expanded=data.get("tags_expanded", True))

    def all_notes(self) -> list[Note]:
        """Returns all unique notes in the account, sorted by creation date (newest first), then by ID."""
        note_set = {n for f in self.folders for n in f.all_notes()}
        return list(sorted(note_set, key=lambda n: (n.creation_date, n.id), reverse=True))

    def find_note_by_name(self, name, folder_path: list[str] = None) -> Note | None:
        if folder_path:
            if folder := self.find_folder_by_name(folder_path):
                return folder.find_note_by_name(name)
        else:
            for folder in self.folders:
                if n := folder.find_note_by_name(name):
                    return n
        return None

    def display(self):
        def print_folder(f1, indent=1):
            print("  " * indent + f"{f1.name}  {len(f1.notes)}")
            for f2 in f1.folders:
                print_folder(f2, indent + 1)

        print(f"{self.name}  {len([n for folder in self.folders for n in folder.all_notes()])}")
        for folder in self.folders:
            print_folder(folder, 1)


def write_metadata_file(obj: Account | Folder | Note, directory: pathlib.Path) -> None:
    """Write a .noteworthy.json metadata file for the given object in the specified directory.

    Args:
        obj: The Account, Folder, or Note to write metadata for.
        directory: The directory where the .noteworthy.json file should be written.
    """
    metadata_path = directory / ".noteworthy.json"
    with metadata_path.open("w", encoding="utf-8") as f:
        json.dump(obj.to_metadata_dict(), f, ensure_ascii=False, indent=2)


def read_distributed_metadata(root_path: pathlib.Path) -> list[Account]:
    """Traverse directory tree and reconstruct account hierarchy from distributed .noteworthy.json files.

    Args:
        root_path: The root directory containing account subdirectories.

    Returns:
        A list of Account objects with their folder and note hierarchies reconstructed.
    """
    accounts = []
    accounts_by_id: dict[str, Account] = {}
    folders_by_id: dict[str, Folder] = {}
    notes_with_folder_ids: list[tuple[Note, list[str], pathlib.Path]] = []

    deleted_dir = root_path / "Deleted"

    # First pass: find all .noteworthy.json files and create objects
    for metadata_path in root_path.rglob(".noteworthy.json"):
        if metadata_path.is_relative_to(deleted_dir):
            continue
        with metadata_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        obj_type = data.get("type")
        obj_dir = metadata_path.parent

        if obj_type == "account":
            account = Account.from_metadata_dict(data)
            account.set_path(obj_dir)
            accounts.append(account)
            accounts_by_id[account.id] = account

        elif obj_type == "folder":
            folder = Folder.from_metadata_dict(data)
            folder.set_path(obj_dir)
            folders_by_id[folder.id] = folder
            # Store parent_id for second pass linking
            folder._pending_parent_id = data.get("parent_id")

        elif obj_type == "note":
            note = Note.from_metadata_dict(data)
            note.set_path(obj_dir)
            # Store folder IDs for second pass linking
            folder_ids = data.get("folders", [])
            notes_with_folder_ids.append((note, folder_ids, obj_dir))

    # Second pass: link folders to their parents (accounts or other folders)
    for folder in folders_by_id.values():
        parent_id = getattr(folder, '_pending_parent_id', None)
        if parent_id:
            if parent_id in accounts_by_id:
                accounts_by_id[parent_id].add_folder(folder)
            elif parent_id in folders_by_id:
                folders_by_id[parent_id].add_folder(folder)
        delattr(folder, '_pending_parent_id')

    # Third pass: link notes to their folders
    for note, folder_ids, _ in notes_with_folder_ids:
        for folder_id in folder_ids:
            if folder_id in folders_by_id:
                folders_by_id[folder_id].add_note(note)

    return accounts
