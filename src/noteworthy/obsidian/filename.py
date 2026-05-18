"""Filename sanitization and vault-wide collision resolution for the Obsidian exporter.

`sanitize_for_obsidian` is a stricter variant of `notes_datatypes._sanitize_name`
that also handles the five characters Obsidian forbids inside `[[wikilinks]]`
(`# | ^ [ ]`) by mapping them to visually-identical fullwidth Unicode codepoints.

`assign_unique_names` runs a vault-wide collision pass over already-sanitized
candidates, suffixing duplicates with " (2)", " (3)", … so Obsidian's path-less
wikilinks resolve unambiguously. See obsidian_requirements.md §5 for the spec.
"""

from __future__ import annotations

from typing import Hashable, Iterable

from noteworthy.notes_datatypes import _sanitize_name


__all__ = ["sanitize_for_obsidian", "assign_unique_names"]


_FULLWIDTH_REPLACEMENTS = {
    "#": "＃",   # U+FF03
    "|": "｜",   # U+FF5C
    "^": "＾",   # U+FF3E
    "[": "［",   # U+FF3B
    "]": "］",   # U+FF3D
}


def sanitize_for_obsidian(name: str) -> str:
    """Sanitize `name` so it's safe as both a filesystem name and an Obsidian wikilink target.

    Applies the existing backup-mode sanitization, then replaces Obsidian-forbidden
    characters with fullwidth look-alikes, then strips surrounding whitespace.
    Returns the literal string "Untitled" if the result is empty.
    """
    sanitized = _sanitize_name(name) if name else ""
    for src, dst in _FULLWIDTH_REPLACEMENTS.items():
        sanitized = sanitized.replace(src, dst)
    sanitized = sanitized.strip()
    return sanitized or "Untitled"


def _split_extension(name: str) -> tuple[str, str]:
    """Split `name` into (stem, extension) for collision-suffixing.

    A leading dot does not start an extension ('.hidden' has no extension).
    Only the last dot is treated as the separator.
    """
    dot = name.rfind(".")
    if dot <= 0:  # no dot, or only a leading dot
        return name, ""
    return name[:dot], name[dot:]


def assign_unique_names(
    candidates: Iterable[tuple[Hashable, str]],
    *,
    has_extensions: bool = False,
) -> dict[Hashable, str]:
    """Resolve name collisions across an entire vault.

    Args:
        candidates: iterable of (key, name) pairs in the order the caller wants
            collisions broken — the first occurrence keeps its bare name, later
            duplicates get " (2)", " (3)", … suffixes. Comparison is case-insensitive
            to match macOS's default case-insensitive filesystem.
        has_extensions: if True, the suffix is inserted before the last `.` so
            `photo.jpg` collides with another `photo.jpg` and becomes `photo (2).jpg`.

    Returns:
        Mapping from key to its final unique name. Names already unique are returned
        unchanged.
    """
    used_lower: set[str] = set()
    result: dict[Hashable, str] = {}

    for key, name in candidates:
        if has_extensions:
            stem, ext = _split_extension(name)
        else:
            stem, ext = name, ""

        candidate = name
        counter = 2
        while candidate.lower() in used_lower:
            candidate = f"{stem} ({counter}){ext}"
            counter += 1

        used_lower.add(candidate.lower())
        result[key] = candidate

    return result
