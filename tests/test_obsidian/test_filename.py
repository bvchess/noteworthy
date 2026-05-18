#!/usr/bin/env python
"""Tests for obsidian.filename — Obsidian-safe sanitization and vault-wide collision resolution."""

from __future__ import annotations

import pytest

from noteworthy.obsidian.filename import sanitize_for_obsidian, assign_unique_names


class TestSanitizePassThrough:
    def test_plain_ascii_unchanged(self):
        assert sanitize_for_obsidian("My Plan") == "My Plan"

    def test_unicode_unchanged(self):
        assert sanitize_for_obsidian("café — résumé") == "café — résumé"

    def test_internal_whitespace_preserved(self):
        assert sanitize_for_obsidian("a  b   c") == "a  b   c"


class TestSanitizeFullwidthReplacements:
    """The five characters Obsidian forbids inside [[wikilinks]] get fullwidth look-alikes."""

    def test_hash_becomes_fullwidth(self):
        assert sanitize_for_obsidian("Plan #1") == "Plan ＃1"

    def test_pipe_becomes_fullwidth(self):
        assert sanitize_for_obsidian("a|b") == "a｜b"

    def test_caret_becomes_fullwidth(self):
        assert sanitize_for_obsidian("a^b") == "a＾b"

    def test_open_bracket_becomes_fullwidth(self):
        assert sanitize_for_obsidian("a[b") == "a［b"

    def test_close_bracket_becomes_fullwidth(self):
        assert sanitize_for_obsidian("a]b") == "a］b"

    def test_all_five_at_once(self):
        assert sanitize_for_obsidian("# | ^ [ ]") == "＃ ｜ ＾ ［ ］"


class TestSanitizeExistingReplacements:
    """The replacements from notes_datatypes._sanitize_name must still apply."""

    def test_slash_becomes_underscore(self):
        assert sanitize_for_obsidian("a/b") == "a_b"

    def test_colon_becomes_dash(self):
        assert sanitize_for_obsidian("Evan 1:1") == "Evan 1-1"

    def test_straight_quote_becomes_curly(self):
        # Existing _sanitize_name maps all straight double quotes to U+201C
        # (left curly quote), not directional opening/closing pairs.
        assert sanitize_for_obsidian('"quoted"') == "“quoted“"

    def test_tab_becomes_space(self):
        assert sanitize_for_obsidian("a\tb") == "a b"

    def test_control_char_percent_encoded(self):
        assert sanitize_for_obsidian("a\x01b") == "a%01b"


class TestSanitizeUntitledFallback:
    def test_empty_string_becomes_untitled(self):
        assert sanitize_for_obsidian("") == "Untitled"

    def test_whitespace_only_becomes_untitled(self):
        assert sanitize_for_obsidian("   ") == "Untitled"

    def test_tab_only_becomes_untitled(self):
        # Tab → space via existing rules; resulting whitespace-only string falls back.
        assert sanitize_for_obsidian("\t\t") == "Untitled"


class TestSanitizeCombined:
    def test_forbidden_and_existing_replacements_compose(self):
        # `/` -> `_`, `#` -> `＃`, `:` -> `-`, `]` -> `］`
        assert sanitize_for_obsidian("Project/Plan #2: [draft]") == "Project_Plan ＃2- ［draft］"


# ---- assign_unique_names ----


class TestAssignUniqueNamesNoCollisions:
    def test_distinct_names_pass_through(self):
        items = [("k1", "Alpha"), ("k2", "Beta"), ("k3", "Gamma")]
        result = assign_unique_names(items)
        assert result == {"k1": "Alpha", "k2": "Beta", "k3": "Gamma"}

    def test_empty_list(self):
        assert assign_unique_names([]) == {}


class TestAssignUniqueNamesCollisions:
    def test_two_colliding_names(self):
        items = [("k1", "Notes"), ("k2", "Notes")]
        result = assign_unique_names(items)
        assert result == {"k1": "Notes", "k2": "Notes (2)"}

    def test_three_colliding_names(self):
        items = [("k1", "Todo"), ("k2", "Todo"), ("k3", "Todo")]
        result = assign_unique_names(items)
        assert result == {"k1": "Todo", "k2": "Todo (2)", "k3": "Todo (3)"}

    def test_iteration_order_determines_winner(self):
        """The caller is responsible for sorting; we honor the input order."""
        items_a = [("first", "Same"), ("second", "Same")]
        items_b = [("second", "Same"), ("first", "Same")]
        assert assign_unique_names(items_a) == {"first": "Same", "second": "Same (2)"}
        assert assign_unique_names(items_b) == {"second": "Same", "first": "Same (2)"}

    def test_case_insensitive_collision(self):
        """macOS is case-insensitive by default; 'Todo' and 'todo' collide."""
        items = [("k1", "Todo"), ("k2", "todo")]
        result = assign_unique_names(items)
        # The second one (case-different but same lowered) is suffixed.
        assert result == {"k1": "Todo", "k2": "todo (2)"}

    def test_existing_parens_suffix_does_not_double(self):
        """Caller pre-sanitization may legitimately produce 'Note (2)' as a base.
        Two such bases still collide and only one gets suffixed."""
        items = [("k1", "Note (2)"), ("k2", "Note (2)")]
        result = assign_unique_names(items)
        assert result == {"k1": "Note (2)", "k2": "Note (2) (2)"}


class TestAssignUniqueNamesWithExtensions:
    def test_attachment_suffix_before_extension(self):
        items = [("k1", "photo.jpg"), ("k2", "photo.jpg")]
        result = assign_unique_names(items, has_extensions=True)
        assert result == {"k1": "photo.jpg", "k2": "photo (2).jpg"}

    def test_multi_extension_collisions(self):
        items = [("k1", "doc.pdf"), ("k2", "doc.pdf"), ("k3", "doc.pdf")]
        result = assign_unique_names(items, has_extensions=True)
        assert result == {"k1": "doc.pdf", "k2": "doc (2).pdf", "k3": "doc (3).pdf"}

    def test_extension_case_preserved(self):
        items = [("k1", "Photo.JPG"), ("k2", "Photo.JPG")]
        result = assign_unique_names(items, has_extensions=True)
        assert result == {"k1": "Photo.JPG", "k2": "Photo (2).JPG"}

    def test_no_extension_treated_as_whole_name(self):
        items = [("k1", "README"), ("k2", "README")]
        result = assign_unique_names(items, has_extensions=True)
        assert result == {"k1": "README", "k2": "README (2)"}

    def test_collision_only_on_basename_when_extensions_differ(self):
        """photo.jpg and photo.png have the same stem but different extensions —
        they do NOT collide as filenames."""
        items = [("k1", "photo.jpg"), ("k2", "photo.png")]
        result = assign_unique_names(items, has_extensions=True)
        assert result == {"k1": "photo.jpg", "k2": "photo.png"}

    def test_dotfile_no_extension(self):
        """Leading dot is not an extension separator."""
        items = [("k1", ".hidden"), ("k2", ".hidden")]
        result = assign_unique_names(items, has_extensions=True)
        assert result == {"k1": ".hidden", "k2": ".hidden (2)"}


class TestAssignUniqueNamesMixedCollisions:
    def test_partial_collision_in_set(self):
        items = [
            ("k1", "Alpha"),
            ("k2", "Beta"),
            ("k3", "Alpha"),
            ("k4", "Gamma"),
            ("k5", "Alpha"),
        ]
        result = assign_unique_names(items)
        assert result == {
            "k1": "Alpha",
            "k2": "Beta",
            "k3": "Alpha (2)",
            "k4": "Gamma",
            "k5": "Alpha (3)",
        }
