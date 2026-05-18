#!/usr/bin/env python
"""Tests for obsidian.frontmatter — YAML emission for Obsidian Properties."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from noteworthy.notes_datatypes import Note
from noteworthy.obsidian.frontmatter import render


# A fixed Core Data-style UTC datetime; tests assert the local-time projection
# so they remain portable across CI/dev machines.
_CREATED_UTC = datetime(2024, 8, 21, 17, 30, 0, tzinfo=timezone.utc)
_MODIFIED_UTC = datetime(2025, 3, 14, 14, 12, 0, tzinfo=timezone.utc)


def _expected_local(dt_utc: datetime) -> str:
    """ISO 8601, seconds precision, naive local time — matching the emitter."""
    return dt_utc.astimezone().replace(tzinfo=None).isoformat(timespec="seconds")


def _make_note(
    name: str = "My Note",
    *,
    uuid: str = "5C7F1A28-DEAD-BEEF-9999-1234567890AB",
    tags: list[str] | None = None,
) -> Note:
    return Note(name, "x-coredata://X/ICNote/p1", None, _CREATED_UTC, _MODIFIED_UTC, uuid=uuid, tags=tags)


class TestRenderBasicShape:
    def test_wrapped_in_yaml_delimiters(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert out.startswith("---\n")
        assert out.endswith("---\n")

    def test_trailing_newline(self):
        """The block ends with a newline so the body starts cleanly."""
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert out.endswith("---\n")
        assert not out.endswith("---\n\n")  # no extra blank line baked in

    def test_required_keys_present(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        for key in ("created:", "modified:", "account:", "apple_notes_uuid:"):
            assert key in out


class TestKeyOrdering:
    def test_canonical_order(self):
        """aliases, tags, created, modified, account, folder, apple_notes_uuid"""
        out = render(
            _make_note(tags=["work"]),
            account_name="iCloud",
            folder_path="Projects",
            aliases=["Original Name"],
        )
        order = ["aliases:", "tags:", "created:", "modified:", "account:", "folder:", "apple_notes_uuid:"]
        positions = [out.find(k) for k in order]
        assert positions == sorted(positions), f"out of order: {positions}"
        assert -1 not in positions, "missing key"

    def test_user_extras_appear_after_canonical_keys(self):
        out = render(
            _make_note(),
            account_name="iCloud",
            folder_path="",
            aliases=[],
            extra_user_keys={"my_custom": "value", "z_other": 42},
        )
        # canonical apple_notes_uuid must precede any user extras
        assert out.find("apple_notes_uuid:") < out.find("my_custom:")
        # extras preserve their input order (dicts are ordered)
        assert out.find("my_custom:") < out.find("z_other:")


class TestDatetimeFormatting:
    def test_naive_local_iso8601(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert f"created: {_expected_local(_CREATED_UTC)}" in out
        assert f"modified: {_expected_local(_MODIFIED_UTC)}" in out

    def test_no_timezone_suffix(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        # No "Z", no "+HH:MM" offset on the date lines.
        for line in out.splitlines():
            if line.startswith("created:") or line.startswith("modified:"):
                assert "Z" not in line
                assert "+" not in line


class TestListEmission:
    def test_aliases_block_form(self):
        out = render(
            _make_note(),
            account_name="iCloud",
            folder_path="",
            aliases=["First Original", "Second Original"],
        )
        assert "aliases:\n  - First Original\n  - Second Original\n" in out

    def test_tags_block_form(self):
        out = render(
            _make_note(tags=["work", "meetings/standup"]),
            account_name="iCloud",
            folder_path="",
            aliases=[],
        )
        assert "tags:\n  - work\n  - meetings/standup\n" in out


class TestTagSanitization:
    def test_space_becomes_dash(self):
        out = render(
            _make_note(tags=["my tag"]), account_name="iCloud", folder_path="", aliases=[],
        )
        assert "  - my-tag\n" in out
        assert "my tag" not in out

    def test_illegal_punctuation_dropped(self):
        out = render(
            _make_note(tags=["foo!bar@baz"]), account_name="iCloud", folder_path="", aliases=[],
        )
        assert "  - foobarbaz\n" in out

    def test_slash_preserved_for_nested_tags(self):
        out = render(
            _make_note(tags=["parent/child"]), account_name="iCloud", folder_path="", aliases=[],
        )
        assert "  - parent/child\n" in out

    def test_empty_after_sanitization_is_skipped(self):
        out = render(
            _make_note(tags=["!!!", "keep"]), account_name="iCloud", folder_path="", aliases=[],
        )
        assert "  - keep\n" in out
        assert "  - !!!" not in out
        assert "  - \n" not in out

    def test_all_numeric_tag_is_skipped(self):
        """Obsidian requires at least one non-numeric character in a tag."""
        out = render(
            _make_note(tags=["1984", "y1984"]), account_name="iCloud", folder_path="", aliases=[],
        )
        assert "  - y1984\n" in out
        assert "  - 1984\n" not in out


class TestEmptyFieldOmission:
    def test_no_aliases_key_when_empty(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert "aliases:" not in out

    def test_no_tags_key_when_empty(self):
        out = render(_make_note(tags=[]), account_name="iCloud", folder_path="", aliases=[])
        assert "tags:" not in out

    def test_no_tags_key_when_all_filtered_out(self):
        out = render(
            _make_note(tags=["!!!", "1984"]), account_name="iCloud", folder_path="", aliases=[],
        )
        assert "tags:" not in out

    def test_no_folder_key_when_path_empty(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert "folder:" not in out

    def test_account_always_present(self):
        """Even with a single account, the account property is still written."""
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert "account: iCloud\n" in out


class TestStringQuoting:
    def test_plain_string_unquoted(self):
        out = render(_make_note(), account_name="iCloud", folder_path="", aliases=[])
        assert "account: iCloud\n" in out

    def test_string_with_colon_quoted(self):
        out = render(
            _make_note(), account_name="iCloud", folder_path="Work: Q3", aliases=[],
        )
        assert 'folder: "Work: Q3"\n' in out

    def test_string_with_double_quote_escaped(self):
        out = render(
            _make_note(), account_name="iCloud",
            folder_path='He said "hi"', aliases=[],
        )
        assert 'folder: "He said \\"hi\\""\n' in out

    def test_string_with_backslash_escaped(self):
        out = render(
            _make_note(), account_name="iCloud",
            folder_path="a\\b", aliases=[],
        )
        assert 'folder: "a\\\\b"\n' in out

    def test_alias_with_yaml_specials_quoted(self):
        out = render(
            _make_note(), account_name="iCloud", folder_path="",
            aliases=["Plan: Phase 1", "Q3 #2"],
        )
        assert '  - "Plan: Phase 1"\n' in out
        assert '  - "Q3 #2"\n' in out


class TestUserExtraValueTypes:
    def test_extra_int_emitted_as_number(self):
        out = render(
            _make_note(), account_name="iCloud", folder_path="", aliases=[],
            extra_user_keys={"priority": 3},
        )
        assert "priority: 3\n" in out

    def test_extra_bool_emitted_lowercase(self):
        out = render(
            _make_note(), account_name="iCloud", folder_path="", aliases=[],
            extra_user_keys={"pinned": True, "archived": False},
        )
        assert "pinned: true\n" in out
        assert "archived: false\n" in out

    def test_extra_list_emitted_in_block_form(self):
        out = render(
            _make_note(), account_name="iCloud", folder_path="", aliases=[],
            extra_user_keys={"refs": ["a", "b"]},
        )
        assert "refs:\n  - a\n  - b\n" in out
