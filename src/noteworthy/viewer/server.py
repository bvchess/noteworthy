#!/usr/bin/python3
"""HTTP server for the noteworthy backup viewer.

Serves the static UI and provides JSON API endpoints for browsing notes.
Binds to 127.0.0.1 on a random available port and auto-opens the browser.
"""
from __future__ import annotations

import json
import mimetypes
import pathlib
import re
import socket
import sys
import threading
import urllib.parse
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from noteworthy.viewer.backup_reader import scan_backup, collect_tags, BackupData
    from noteworthy.viewer.markdown_to_html import convert
    from noteworthy.viewer.search import SearchIndex
except ImportError:
    from backup_reader import scan_backup, collect_tags, BackupData
    from markdown_to_html import convert
    from search import SearchIndex

# Ensure common MIME types are registered
mimetypes.init()

_STATIC_DIR = pathlib.Path(__file__).parent / "static"
_EMPTY_NOTES_RESPONSE = {"sort_order": "default", "notes": []}


def _count_all_notes(folder) -> int:
    """Recursively count notes in a folder and all its children."""
    count = len(folder.note_ids)
    for child in folder.children:
        count += _count_all_notes(child)
    return count


class ViewerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the viewer API and static files."""

    backup: BackupData
    search_index: SearchIndex
    backup_root: pathlib.Path
    tags: list[str]

    def log_message(self, format, *args):
        """Suppress default stderr logging."""
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html_content, status=200):
        body = html_content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: pathlib.Path, content_type: str | None = None):
        if not file_path.exists() or not file_path.is_file():
            self._send_not_found()
            return
        if content_type is None:
            content_type, _ = mimetypes.guess_type(str(file_path))
            content_type = content_type or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_not_found(self):
        self._send_json({"error": "Not found"}, status=404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self._serve_index()
        elif path.startswith("/static/"):
            self._serve_static(path[8:])  # strip "/static/"
        elif path == "/api/tree":
            self._api_tree()
        elif path == "/api/notes":
            folder_id = query.get("folder", [None])[0]
            self._api_notes(folder_id)
        elif path.startswith("/api/note/"):
            note_id = urllib.parse.unquote(path[10:])  # strip "/api/note/"
            self._api_note(note_id)
        elif path.startswith("/api/attachment/"):
            self._api_attachment(path[16:])  # strip "/api/attachment/"
        elif path == "/api/search":
            q = query.get("q", [None])[0]
            self._api_search(q)
        elif path == "/api/resolve-link":
            from_id = query.get("from", [None])[0]
            rel = query.get("rel", [None])[0]
            self._api_resolve_link(from_id, rel)
        else:
            self._send_not_found()

    def _serve_index(self):
        index_path = _STATIC_DIR / "index.html"
        self._send_file(index_path, "text/html; charset=utf-8")

    def _serve_static(self, filename: str):
        # Prevent directory traversal
        safe_name = pathlib.Path(filename).name
        file_path = _STATIC_DIR / safe_name
        self._send_file(file_path)

    def _api_tree(self):
        """Return the full folder tree as JSON."""
        def folder_to_dict(folder):
            total = _count_all_notes(folder)
            return {
                "id": folder.id,
                "name": folder.name,
                "is_smart_folder": folder.is_smart_folder,
                "sort_order": folder.sort_order,
                "is_expanded": folder.is_expanded,
                "note_count": len(folder.note_ids),
                "total_note_count": total,
                "children": [
                    folder_to_dict(c)
                    for c in sorted(folder.children, key=lambda f: f.display_order)
                ],
            }

        tree = []
        for account in self.backup.accounts:
            # Count all notes across non-smart folders (smart folders contain symlinks to notes
            # already in regular folders, so including them would double-count)
            total = sum(_count_all_notes(f) for f in account.folders if not f.is_smart_folder)
            all_folder = {
                "id": f"__all__:{account.id}",
                "name": f"All {account.name}",
                "is_smart_folder": False,
                "is_all_folder": True,
                "note_count": total,
                "total_note_count": total,
                "children": [],
            }
            folders = [
                folder_to_dict(f)
                for f in sorted(account.folders, key=lambda f: f.display_order)
            ]
            account_dict = {
                "id": account.id,
                "name": account.name,
                "folders": [all_folder] + folders,
            }
            tree.append(account_dict)
        tags_expanded = any(a.tags_expanded for a in self.backup.accounts) if self.backup.accounts else True
        self._send_json({"accounts": tree, "tags": self.tags, "tags_expanded": tags_expanded})

    def _api_notes(self, folder_id: str | None):
        """Return notes in a folder, sorted according to the folder's sort preference."""
        if not folder_id:
            self._send_json(_EMPTY_NOTES_RESPONSE)
            return

        # Determine sort_order from folder metadata
        sort_order = "default"
        if folder_id.startswith("__all__:"):
            account_id = folder_id[8:]  # strip "__all__:"
            note_ids = self._collect_account_note_ids(account_id)
        elif folder_id in self.backup.folders_by_id:
            folder = self.backup.folders_by_id[folder_id]
            note_ids = folder.note_ids
            sort_order = folder.sort_order
        else:
            self._send_json(_EMPTY_NOTES_RESPONSE)
            return

        note_objs = [
            note for note_id in note_ids
            if (note := self.backup.notes_by_id.get(note_id))
        ]

        # Sort according to folder preference
        if sort_order == "date_created":
            note_objs.sort(key=lambda n: n.creation_date, reverse=True)
        elif sort_order == "title":
            note_objs.sort(key=lambda n: n.name.lower())
        else:  # "default" or "date_edited"
            note_objs.sort(key=lambda n: n.modification_date, reverse=True)

        notes = []
        for n in note_objs:
            entry = {
                "id": n.id,
                "name": n.name,
                "modification_date": n.modification_date.isoformat(),
                "creation_date": n.creation_date.isoformat(),
                "preview": n.preview,
                "folder_name": self._folder_name(n.folder_id),
            }
            if n.first_image:
                encoded_id = urllib.parse.quote(n.id, safe="")
                entry["first_image"] = f"/api/attachment/{encoded_id}/{n.first_image}"
            notes.append(entry)
        self._send_json({"sort_order": sort_order, "notes": notes})

    def _folder_name(self, folder_id: str | None) -> str:
        """Look up a folder's display name by ID, returning empty string if not found."""
        if folder_id and folder_id in self.backup.folders_by_id:
            return self.backup.folders_by_id[folder_id].name
        return ""

    def _collect_account_note_ids(self, account_id: str) -> list[str]:
        """Collect all note IDs recursively from an account's folder tree."""
        account = next((a for a in self.backup.accounts if a.id == account_id), None)
        if not account:
            return []

        note_ids: list[str] = []

        def _collect(folder):
            note_ids.extend(folder.note_ids)
            for child in folder.children:
                _collect(child)

        for folder in account.folders:
            if not folder.is_smart_folder:
                _collect(folder)
        return note_ids

    def _api_note(self, note_id: str):
        """Return rendered HTML and metadata for a single note."""
        note = self.backup.notes_by_id.get(note_id)
        if not note:
            self._send_not_found()
            return

        # Read and convert markdown
        html_content = ""
        if note.md_path and note.md_path.exists():
            try:
                md_text = note.md_path.read_text(encoding="utf-8")
                html_content = convert(md_text, note_id=note_id, tags=note.tags)
            except (OSError, UnicodeDecodeError):
                html_content = "<p><em>Could not read note content.</em></p>"

        # List attachments
        attachments = []
        attachments_dir = note.dir_path / "Attachments" if note.dir_path else None
        if attachments_dir and attachments_dir.exists():
            for f in sorted(attachments_dir.iterdir()):
                if f.is_file():
                    attachments.append(f.name)

        self._send_json({
            "id": note.id,
            "name": note.name,
            "html": html_content,
            "creation_date": note.creation_date.isoformat(),
            "modification_date": note.modification_date.isoformat(),
            "folder": self._folder_name(note.folder_id),
            "attachments": attachments,
        })

    def _api_attachment(self, path_info: str):
        """Serve an attachment file from a note's Attachments directory."""
        # path_info is "<url-encoded-note-id>/<filename>"
        parts = path_info.split("/", 1)
        if len(parts) != 2:
            self._send_not_found()
            return

        note_id = urllib.parse.unquote(parts[0])
        filename = urllib.parse.unquote(parts[1])

        note = self.backup.notes_by_id.get(note_id)
        if not note or not note.dir_path:
            self._send_not_found()
            return

        # Prevent directory traversal
        safe_filename = pathlib.Path(filename).name
        file_path = note.dir_path / "Attachments" / safe_filename
        self._send_file(file_path)

    def _api_search(self, query: str | None):
        """Search notes and return results."""
        if not query:
            self._send_json([])
            return
        stripped = query.strip()
        if re.match(r"^#[a-zA-Z][\w-]*$", stripped):
            results = self._search_tag(stripped)
        else:
            results = self.search_index.search(query)
        self._send_json(results)

    def _api_resolve_link(self, from_note_id: str | None, rel_path: str | None):
        """Resolve a relative path from a note to a target note ID."""
        if not from_note_id or not rel_path:
            self._send_not_found()
            return
        note = self.backup.notes_by_id.get(from_note_id)
        if not note or not note.md_path:
            self._send_not_found()
            return
        target = (note.md_path.parent / urllib.parse.unquote(rel_path)).resolve()
        target_id = self.backup.notes_by_md_path.get(str(target))
        if not target_id:
            self._send_not_found()
            return
        self._send_json({"note_id": target_id})

    def _search_tag(self, tag: str) -> list[dict]:
        """Search for notes with a specific hashtag (from metadata)."""
        tag_name = tag.lstrip("#").lower()
        matching = sorted(
            (note for note in self.backup.notes_by_id.values() if tag_name in note.tags),
            key=lambda n: n.modification_date,
            reverse=True,
        )
        return [
            {
                "note_id": note.id,
                "title": note.name,
                "snippet": "",
                "folder_path": self._folder_name(note.folder_id),
            }
            for note in matching
        ]


def create_server(backup_root: pathlib.Path, host: str = "127.0.0.1", port: int = 0) -> HTTPServer:
    """Create and configure the viewer HTTP server.

    Args:
        backup_root: Path to the backup directory.
        host: Address to bind to.
        port: Port to bind to (0 = auto-select).

    Returns:
        Configured HTTPServer instance (not yet started).
    """
    # Scan backup and build search index
    backup = scan_backup(backup_root)
    search_index = SearchIndex()
    search_index.build(backup)
    tags = collect_tags(backup)

    # Create handler class with backup data attached
    handler = type("Handler", (ViewerHandler,), {
        "backup": backup,
        "search_index": search_index,
        "backup_root": backup_root,
        "tags": tags,
    })

    server = HTTPServer((host, port), handler)
    return server


def main():
    """Main entry point for running the viewer server."""
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <backup_directory>", file=sys.stderr)
        sys.exit(1)

    backup_root = pathlib.Path(sys.argv[1]).resolve()
    if not backup_root.is_dir():
        print(f"Error: {backup_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning backup at {backup_root}...")
    server = create_server(backup_root)
    host, port = server.server_address
    url = f"http://{host}:{port}/"

    print(f"Viewer running at {url}")
    print("Press Ctrl+C to stop")

    # Open browser in a separate thread to not block server startup
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.shutdown()


if __name__ == "__main__":
    main()
