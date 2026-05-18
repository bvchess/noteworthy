"""Classify a target directory before running an export.

The exporter needs to know what is currently in the target directory so it can
refuse mode/state mismatches that would corrupt an existing export. See
obsidian_requirements.md §11.1 for the full behavior table.
"""

from __future__ import annotations

import pathlib
from enum import Enum


__all__ = ["TargetState", "inspect"]


class TargetState(Enum):
    EMPTY = "empty"
    OBSIDIAN = "obsidian"
    BACKUP = "backup"
    UNRELATED = "unrelated"


def inspect(target_path: pathlib.Path) -> TargetState:
    """Classify the target directory by looking for mode-specific markers.

    Detection signals, in order:
      1. .obsidian/ directory at the root  -> OBSIDIAN  (wins over backup signals)
      2. .noteworthy.json anywhere in tree -> BACKUP
      3. directory exists and is non-empty -> UNRELATED
      4. otherwise (missing or empty)      -> EMPTY

    Raises ValueError if `target_path` exists and is not a directory.
    """
    target_path = pathlib.Path(target_path)

    if not target_path.exists():
        return TargetState.EMPTY

    if not target_path.is_dir():
        raise ValueError(f"target {target_path} is not a directory")

    if (target_path / ".obsidian").is_dir():
        return TargetState.OBSIDIAN

    # Search for .noteworthy.json anywhere in the tree. rglob short-circuits
    # via the generator, so we stop on the first hit.
    for _ in target_path.rglob(".noteworthy.json"):
        return TargetState.BACKUP

    # No signals — empty if nothing's there, otherwise unrelated.
    try:
        next(iter(target_path.iterdir()))
    except StopIteration:
        return TargetState.EMPTY
    return TargetState.UNRELATED
