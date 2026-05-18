"""Top-level orchestrator for the Obsidian export mode.

Stage 1 stub: parses the dispatch contract but does no real work. Stage 4
replaces this with the full implementation.
"""

from __future__ import annotations

import pathlib


__all__ = ["run"]


def run(target_path: pathlib.Path, db_path: pathlib.Path | None = None, *, verbose: bool = False) -> None:
    """Export Apple Notes into an Obsidian vault at `target_path`.

    Stage 1 stub — prints a placeholder message and returns.
    """
    print("obsidian export not yet implemented")
