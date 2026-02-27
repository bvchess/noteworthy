"""Viewer installer -- copies viewer files into a backup directory."""
from __future__ import annotations

import pathlib
import shutil
import stat

_VIEWER_DIR = pathlib.Path(__file__).parent
_LAUNCHER_FILENAME = "View Notes.command"
_HIDDEN_DIR_NAME = ".noteworthy-viewer"

_VIEWER_FILES = [
    "server.py",
    "backup_reader.py",
    "markdown_to_html.py",
    "search.py",
    "static/index.html",
    "static/style.css",
    "static/app.js",
]

_LAUNCHER_SCRIPT = """\
#!/bin/bash
# Launch the Notes Viewer
export SHELL_SESSION_DID_INIT=1
cd "$(dirname "$0")"
/usr/bin/python3 .noteworthy-viewer/server.py .
"""


def install_viewer(backup_path: pathlib.Path) -> None:
    """Install the viewer into a backup directory.

    Creates:
        <backup_path>/.noteworthy-viewer/  -- viewer Python + static files
        <backup_path>/View Notes.command   -- double-clickable macOS launcher

    Idempotent: safe to call repeatedly; overwrites existing files.

    Args:
        backup_path: Root directory of the backup.
    """
    backup_path = pathlib.Path(backup_path)
    viewer_dest = backup_path / _HIDDEN_DIR_NAME
    static_dest = viewer_dest / "static"
    static_dest.mkdir(parents=True, exist_ok=True)

    for filename in _VIEWER_FILES:
        shutil.copy2(_VIEWER_DIR / filename, viewer_dest / filename)

    launcher_path = backup_path / _LAUNCHER_FILENAME
    launcher_path.write_text(_LAUNCHER_SCRIPT, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
