from __future__ import annotations

import pathlib
import stat

import pytest

from noteworthy.viewer import install_viewer


class TestInstallViewer:
    def test_creates_hidden_directory(self, tmp_path):
        install_viewer(tmp_path)
        viewer_dir = tmp_path / ".noteworthy-viewer"
        assert viewer_dir.is_dir()

    def test_creates_static_directory(self, tmp_path):
        install_viewer(tmp_path)
        static_dir = tmp_path / ".noteworthy-viewer" / "static"
        assert static_dir.is_dir()

    def test_copies_python_modules(self, tmp_path):
        install_viewer(tmp_path)
        viewer_dir = tmp_path / ".noteworthy-viewer"
        for filename in ("server.py", "backup_reader.py", "markdown_to_html.py", "search.py"):
            assert (viewer_dir / filename).is_file(), f"Missing {filename}"

    def test_copies_static_files(self, tmp_path):
        install_viewer(tmp_path)
        static_dir = tmp_path / ".noteworthy-viewer" / "static"
        for filename in ("index.html", "style.css", "app.js"):
            assert (static_dir / filename).is_file(), f"Missing {filename}"

    def test_creates_launcher(self, tmp_path):
        install_viewer(tmp_path)
        launcher = tmp_path / "View Notes.command"
        assert launcher.is_file()

    def test_launcher_is_executable(self, tmp_path):
        install_viewer(tmp_path)
        launcher = tmp_path / "View Notes.command"
        mode = launcher.stat().st_mode
        assert mode & stat.S_IXUSR, "Launcher should be executable by owner"

    def test_launcher_content(self, tmp_path):
        install_viewer(tmp_path)
        launcher = tmp_path / "View Notes.command"
        content = launcher.read_text(encoding="utf-8")
        assert "#!/bin/bash" in content
        assert "/usr/bin/python3" in content
        assert ".noteworthy-viewer/server.py" in content

    def test_idempotent(self, tmp_path):
        """Running install_viewer twice should not break anything."""
        install_viewer(tmp_path)
        install_viewer(tmp_path)

        viewer_dir = tmp_path / ".noteworthy-viewer"
        assert viewer_dir.is_dir()
        assert (viewer_dir / "server.py").is_file()
        assert (tmp_path / "View Notes.command").is_file()

    def test_python_files_are_valid(self, tmp_path):
        """Installed Python files should be parseable (valid syntax)."""
        install_viewer(tmp_path)
        viewer_dir = tmp_path / ".noteworthy-viewer"
        for filename in ("server.py", "backup_reader.py", "markdown_to_html.py", "search.py"):
            content = (viewer_dir / filename).read_text(encoding="utf-8")
            # Should not raise SyntaxError
            compile(content, filename, "exec")
