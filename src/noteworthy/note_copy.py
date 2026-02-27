import re
import shutil
from pathlib import Path

from noteworthy.notes_datatypes import Note, _sanitize_name
from noteworthy.markdown_renderer import NoteExporter
from noteworthy.database import DatabaseNoteDataLoader

"""
Export notes from Apple Notes to Markdown using direct database access.
"""

__all__ = ["make_markdown_copy"]

_DB_PATH = Path.home() / "Library/Group Containers/group.com.apple.notes/NoteStore.sqlite"


def _extract_zpk(note_id: str) -> int:
    """Extract Z_PK (database primary key) from x-coredata URI.

    Args:
        note_id: Core Data URI like 'x-coredata://UUID/ICNote/p12345'

    Returns:
        The Z_PK integer (e.g., 12345)

    Raises:
        ValueError: If the note_id format is not recognized
    """
    match = re.search(r'/p(\d+)$', note_id)
    if not match:
        raise ValueError(f"Cannot extract Z_PK from note ID: {note_id}")
    return int(match.group(1))


def make_markdown_copy(note: Note | str, output_path: str | Path, verbose: bool = False,
                       note_path_by_uuid: dict[str, Path] = None, db_path: Path = None) -> None:
    """Export a note from Apple Notes to Markdown using database access.

    Each note gets its own directory containing:
    - The note's .md file (named after the directory)
    - An 'Attachments' subdirectory if there are file attachments

    Args:
        note: A Note object or the Core Data URI of the Apple Note.
        output_path: The destination directory path for the note.
        verbose: If True, print detailed information about the export process.
        note_path_by_uuid: Mapping of note UUID (uppercase) to pre-computed path,
            used for resolving note-to-note links correctly.
        db_path: Path to NoteStore.sqlite. Defaults to the standard Apple Notes location.

    Raises:
        FileNotFoundError: If the parent directory does not exist.
        ValueError: If the note ID format is not recognized or note not found.
    """
    note_id = note if isinstance(note, str) else note.id
    zpk = _extract_zpk(note_id)
    note_name = note.name if isinstance(note, Note) else None
    note_uuid = note.uuid if isinstance(note, Note) else None

    output_path = Path(output_path)
    if not output_path.parent.exists():
        raise FileNotFoundError(f"Directory does not exist: {output_path.parent}")

    # Create note directory
    output_path.mkdir(parents=True, exist_ok=True)

    # Derive the markdown filename from the directory name
    md_filename = output_path.name + ".md"
    md_path = output_path / md_filename

    data_loader = DatabaseNoteDataLoader(str(db_path or _DB_PATH))
    try:
        exporter = NoteExporter(data_loader, verbose=verbose, note_path_by_uuid=note_path_by_uuid,
                                current_note_path=output_path, note_name=note_name, note_uuid=note_uuid)
        markdown, attachments = exporter.export_note(zpk, str(md_path))

        # Handle attachments: remove old Attachments directory and recreate with current attachments
        attachments_dir = output_path / "Attachments"
        if attachments_dir.exists():
            shutil.rmtree(attachments_dir)

        if attachments:
            attachments_dir.mkdir(exist_ok=True)

            for att in attachments:
                if att.file_path and (att.unique_filename or att.title):
                    src_path = Path(att.file_path)
                    if src_path.exists():
                        # Use unique_filename (collision-resolved) if available, otherwise sanitize title
                        safe_filename = att.unique_filename or _sanitize_name(att.title)
                        dest_path = attachments_dir / safe_filename
                        if verbose:
                            print(f"   Copying attachment: {att.title} -> {safe_filename}")
                        if src_path.is_dir():
                            # Some attachments (folders, document packages) are directories
                            if dest_path.exists():
                                shutil.rmtree(dest_path)
                            shutil.copytree(src_path, dest_path)
                        else:
                            shutil.copy2(src_path, dest_path)
                    else:
                        print(f"Warning: Attachment file not found: {src_path}")
    finally:
        data_loader.close()
