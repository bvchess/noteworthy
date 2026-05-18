"""Render a YAML frontmatter block for a note exported to an Obsidian vault.

The frontmatter shape is the constrained subset described in
obsidian_requirements.md §7: aliases (list of strings), tags (list of strings,
sanitized per §8), created and modified (naive ISO 8601 in local time),
account (string), folder (string, omitted when empty), apple_notes_uuid
(string), then any user-added keys preserved from a prior export.

We emit YAML by hand because the input domain is small and adding a runtime
dependency just for this would be excessive. Strings that could be misparsed
(YAML specials, leading/trailing whitespace, keywords, parseable numbers/dates)
are double-quoted with `\\` and `"` escaped.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from noteworthy.notes_datatypes import Note


__all__ = ["render"]


# ---------- tag sanitization (requirements §8) ----------

_TAG_WHITESPACE_RE = re.compile(r"\s+")
_TAG_ILLEGAL_RE = re.compile(r"[^a-z0-9_/\-]")


def _sanitize_tag(tag: str) -> str | None:
    """Apply Obsidian tag rules; return None for tags that should be skipped."""
    tag = _TAG_WHITESPACE_RE.sub("-", tag.lower().strip())
    tag = _TAG_ILLEGAL_RE.sub("", tag)
    if not tag or tag.isdigit():
        return None
    return tag


# ---------- scalar emission ----------

_YAML_KEYWORDS = {"true", "false", "yes", "no", "null", "on", "off", "~", ""}
_NEEDS_QUOTE_CHARS = set(':#[]{},&*!|>\'"%@`\\\n\t')
_LEADING_FORBIDDEN = set(" -?\t")
_TRAILING_FORBIDDEN = set(" :")
_NUMBER_LIKE_RE = re.compile(r"^-?\d+(\.\d+)?$")
_DATE_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _string_needs_quote(s: str) -> bool:
    if not s:
        return True
    if s.lower() in _YAML_KEYWORDS:
        return True
    if s[0] in _LEADING_FORBIDDEN or s[-1] in _TRAILING_FORBIDDEN:
        return True
    if any(c in _NEEDS_QUOTE_CHARS for c in s):
        return True
    if _NUMBER_LIKE_RE.match(s) or _DATE_LIKE_RE.match(s):
        return True
    return False


def _quote(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _format_datetime(dt: datetime) -> str:
    """Naive local-time ISO 8601, second precision. Source is expected tz-aware."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat(timespec="seconds")


def _emit_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        return _format_datetime(value)
    if value is None:
        return "null"
    s = str(value)
    return _quote(s) if _string_needs_quote(s) else s


def _emit_list_block(values: list[Any], *, indent: str = "  ") -> str:
    return "\n".join(f"{indent}- {_emit_scalar(v)}" for v in values)


# ---------- public entry point ----------

def render(
    note: Note,
    *,
    account_name: str,
    folder_path: str,
    aliases: list[str],
    extra_user_keys: dict[str, Any] | None = None,
) -> str:
    """Render the full ---\\n…\\n--- frontmatter block (with trailing newline)."""
    parts: list[str] = []

    if aliases:
        parts.append("aliases:\n" + _emit_list_block(aliases))

    tags = [t for t in (_sanitize_tag(t) for t in (note.tags or [])) if t]
    if tags:
        parts.append("tags:\n" + _emit_list_block(tags))

    parts.append(f"created: {_emit_scalar(note.creation_date)}")
    parts.append(f"modified: {_emit_scalar(note.modification_date)}")
    parts.append(f"account: {_emit_scalar(account_name)}")
    if folder_path:
        parts.append(f"folder: {_emit_scalar(folder_path)}")
    parts.append(f"apple_notes_uuid: {_emit_scalar(note.uuid)}")

    if extra_user_keys:
        for key, value in extra_user_keys.items():
            if isinstance(value, list):
                parts.append(f"{key}:\n" + _emit_list_block(value))
            else:
                parts.append(f"{key}: {_emit_scalar(value)}")

    return "---\n" + "\n".join(parts) + "\n---\n"
