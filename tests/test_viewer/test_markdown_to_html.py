from __future__ import annotations

import pathlib

import pytest

from noteworthy.viewer.markdown_to_html import convert


class TestHeadings:
    def test_h1(self):
        assert "<h1>Title</h1>" in convert("# Title")

    def test_h2(self):
        assert "<h2>Heading</h2>" in convert("## Heading")

    def test_h3(self):
        assert "<h3>Subheading</h3>" in convert("### Subheading")

    def test_bare_h1(self):
        result = convert("#")
        assert "<h1>" in result

    def test_bare_h2(self):
        result = convert("##")
        assert "<h2>" in result
        assert "##" not in result.replace("<h2>", "").replace("</h2>", "")

    def test_bare_h3(self):
        result = convert("###")
        assert "<h3>" in result


class TestInlineFormatting:
    def test_bold(self):
        result = convert("**bold text**")
        assert "<strong>bold text</strong>" in result

    def test_italic(self):
        result = convert("*italic text*")
        assert "<em>italic text</em>" in result

    def test_strikethrough(self):
        result = convert("~~struck~~")
        assert "<del>struck</del>" in result

    def test_underline(self):
        result = convert("++underlined++")
        assert "<u>underlined</u>" in result

    def test_highlight(self):
        result = convert("==highlighted==")
        assert "<mark>highlighted</mark>" in result

    def test_code_span(self):
        result = convert("`some code`")
        assert "<code>some code</code>" in result

    def test_nested_bold_highlight(self):
        result = convert("**==bold highlight==**")
        assert "<strong>" in result
        assert "<mark>" in result
        assert "bold highlight" in result

    def test_hashtag_known_tag(self):
        result = convert("#now", tags=["now"])
        assert 'class="hashtag"' in result
        assert "#now" in result

    def test_hashtag_in_text_known_tag(self):
        result = convert("some text #important here", tags=["important"])
        assert 'class="hashtag"' in result
        assert "#important" in result

    def test_hashtag_unknown_word_not_highlighted(self):
        """Plain #word text not in the note's tags should not get a hashtag span."""
        result = convert("some text #notreallya here")
        assert 'class="hashtag"' not in result

    def test_hashtag_only_known_tags_highlighted(self):
        """When a note has some tags, only those specific tags are highlighted."""
        result = convert("tagged: #real and also #fake", tags=["real"])
        assert '<span class="hashtag">#real</span>' in result
        assert 'class="hashtag"' not in result.replace('<span class="hashtag">#real</span>', "")


class TestLists:
    def test_bullet_list(self):
        result = convert("* item one\n* item two")
        assert "<ul>" in result
        assert "<li>item one</li>" in result
        assert "<li>item two</li>" in result
        assert "</ul>" in result

    def test_dash_list(self):
        result = convert("- item one\n- item two")
        assert "<ul>" in result
        assert "<li>item one</li>" in result

    def test_ordered_list(self):
        result = convert("1. first\n2. second\n3. third")
        assert "<ol>" in result
        assert "<li>first</li>" in result
        assert "<li>second</li>" in result
        assert "</ol>" in result

    def test_double_star_bullet(self):
        """** text is orphaned italic + bullet marker from exporter."""
        result = convert("** NLCORP gets 26.1 on March 20")
        assert "<li>" in result
        assert "NLCORP gets 26.1" in result
        assert "**" not in result

    def test_triple_star_bullet(self):
        """*** text is orphaned bold + bullet marker from exporter."""
        result = convert("*** Evan: Oracle contract")
        assert "<li>" in result
        assert "Evan: Oracle contract" in result
        assert "***" not in result

    def test_trailing_orphaned_star(self):
        """Trailing * from cross-line italic should be stripped."""
        result = convert("* are we sending people to Spain next week?*")
        assert "<li>" in result
        assert "Spain next week?" in result
        assert result.count("*") == 0 or "next week?*" not in result

    def test_indented_bullet_list(self):
        result = convert("  * item one\n  * item two")
        assert "<ul>" in result
        assert "<li>item one</li>" in result
        assert "<li>item two</li>" in result

    def test_indented_dash_list(self):
        result = convert("  - item one\n  - item two")
        assert "<ul>" in result
        assert "<li>item one</li>" in result

    def test_indented_ordered_list(self):
        result = convert("  1. first\n  2. second")
        assert "<ol>" in result
        assert "<li>first</li>" in result

    def test_checklist_unchecked(self):
        result = convert("- [ ] unchecked item")
        assert 'class="checklist"' in result
        assert '<input type="checkbox" disabled>' in result
        assert "unchecked item" in result

    def test_checklist_checked(self):
        result = convert("- [x] checked item")
        assert 'class="checklist"' in result
        assert '<input type="checkbox" disabled checked>' in result
        assert "checked item" in result

    def test_nested_bullet_list(self):
        md = "* top item\n    * nested item\n* second top"
        result = convert(md)
        # Should have nested <ul> inside first <li>
        assert result.count("<ul>") == 2
        assert result.count("</ul>") == 2
        assert "top item" in result
        assert "nested item" in result
        assert "second top" in result

    def test_nested_two_levels(self):
        md = "* level 0\n    * level 1\n        * level 2"
        result = convert(md)
        assert result.count("<ul>") == 3
        assert result.count("</ul>") == 3

    def test_nested_bullet_with_bold(self):
        """Nested indented bullet with bold content (like AI_FEST note)."""
        md = "* question text  \n    * **BOLD ANSWER**  \n* next question  \n"
        result = convert(md)
        assert "<strong>BOLD ANSWER</strong>" in result
        assert result.count("<ul>") == 2  # outer + nested
        assert "question text" in result
        assert "next question" in result

    def test_nested_ordered_list(self):
        md = "1. first\n    1. nested first\n    2. nested second\n2. second"
        result = convert(md)
        assert result.count("<ol>") == 2
        assert result.count("</ol>") == 2
        assert "nested first" in result

    def test_nested_checklist(self):
        md = "- [ ] parent\n    - [x] child done"
        result = convert(md)
        assert "parent" in result
        assert "child done" in result
        assert result.count("checklist") >= 2  # class appears in both open tags

    def test_multiple_nested_items_at_same_level(self):
        md = "* top\n    * nested one\n    * nested two\n    * nested three"
        result = convert(md)
        assert "nested one" in result
        assert "nested two" in result
        assert "nested three" in result
        assert result.count("<ul>") == 2

    def test_back_to_top_after_nesting(self):
        """After nested items, returning to top level closes the nested list properly."""
        md = "* A\n    * A1\n    * A2\n* B\n* C"
        result = convert(md)
        assert result.count("<ul>") == 2
        assert result.count("</ul>") == 2
        # All items present
        for text in ["A", "A1", "A2", "B", "C"]:
            assert text in result

    def test_blank_line_between_list_sections(self):
        """Blank lines between list sections produce vertical whitespace."""
        md = "* item one\n    * sub item\n\n* item two"
        result = convert(md)
        assert '<div class="blank-line"></div>' in result
        # Should produce two separate lists
        assert result.count("<ul>") >= 2

    def test_multiple_blank_lines_between_lists(self):
        """Multiple blank lines produce multiple spacers."""
        md = "* A\n\n\n* B"
        result = convert(md)
        assert result.count('<div class="blank-line"></div>') == 2

    def test_no_trailing_blank_line_spacers(self):
        """Trailing blank lines should not produce spacers."""
        md = "* item\n\n\n"
        result = convert(md)
        assert not result.endswith('<div class="blank-line"></div>')

    def test_no_leading_blank_line_spacers(self):
        """Leading blank lines before any content should not produce spacers."""
        md = "\n\n* item"
        result = convert(md)
        assert not result.startswith('<div class="blank-line"></div>')

    def test_continuation_line_after_bullet(self):
        """Body text connected by hard break stays inside the same <li>."""
        md = "* *Book Title*  \nDescription of the book."
        result = convert(md)
        assert result.count("<li>") == 1
        assert "<br>" in result
        assert "Description of the book." in result
        assert "</li>" in result

    def test_multiple_continuation_lines(self):
        """Multiple continuation lines all stay inside the same <li>."""
        md = "* *Book Title*  \nLine one.  \nLine two."
        result = convert(md)
        assert result.count("<li>") == 1
        assert result.count("<br>") == 2
        assert "Line one." in result
        assert "Line two." in result

    def test_next_bullet_after_continuation(self):
        """A new bullet after continuation text starts a new <li>."""
        md = "* *Book A*  \nDescription A.\n* *Book B*"
        result = convert(md)
        assert result.count("<li>") == 2
        assert "Description A." in result
        assert "<em>Book B</em>" in result

    def test_no_hard_break_closes_list(self):
        """Body text NOT connected by hard break closes the list and becomes <p>."""
        md = "* Bullet item\nParagraph text."
        result = convert(md)
        assert "<li>Bullet item</li>" in result
        assert "<p>Paragraph text.</p>" in result

    def test_nested_bullet_with_continuation(self):
        """Continuation text on a nested bullet stays in the nested <li>."""
        md = "* Parent  \n    * *Child item*  \n    Child description.\n* Next parent"
        result = convert(md)
        assert "Child description." in result
        # The continuation should be inside the nested list, not ejected
        assert result.count("<li>") == 3  # parent, child, next parent


class TestCodeBlocks:
    def test_fenced_code_block(self):
        md = "```\nprint('hello')\n```"
        result = convert(md)
        assert "<pre><code>" in result
        assert "print(&#x27;hello&#x27;)" in result
        assert "</code></pre>" in result

    def test_fenced_code_block_with_language(self):
        md = "```python\nprint('hello')\n```"
        result = convert(md)
        assert 'class="language-python"' in result

    def test_fenced_code_block_no_blank_lines_between_content_lines(self):
        md = "```\nline1\nline2\nline3\n```"
        result = convert(md)
        assert "line1\nline2\nline3" in result
        assert "line1\n\nline2" not in result

    def test_fenced_code_block_no_blank_line_at_start(self):
        md = "```\nfirst line\n```"
        result = convert(md)
        assert "<pre><code>first line" in result

    def test_fenced_code_block_trailing_blank_lines_stripped(self):
        md = "```\ncontent\n\n\n```"
        result = convert(md)
        assert "content</code></pre>" in result


class TestBlockQuotes:
    def test_block_quote(self):
        result = convert("> a block quote")
        assert "<blockquote>" in result
        assert "<p>a block quote</p>" in result
        assert "</blockquote>" in result


class TestTables:
    def test_basic_table(self):
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
        result = convert(md)
        assert "<table>" in result
        assert "<th>A</th>" in result
        assert "<td>1</td>" in result
        assert "</table>" in result

    def test_table_alignment(self):
        md = "| Left | Center | Right |\n| :--- | :---: | ---: |\n| a | b | c |"
        result = convert(md)
        assert 'text-align: center' in result
        assert 'text-align: right' in result

    def test_multi_row_table(self):
        md = "| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |"
        result = convert(md)
        assert result.count("<tr>") == 3  # header + 2 data rows


class TestAttachments:
    def test_image_attachment(self):
        md = "![photo.jpg](Attachments/photo.jpg)"
        result = convert(md, note_id="note-1")
        assert '<img src="/api/attachment/note-1/photo.jpg"' in result
        assert 'loading="lazy"' in result
        assert 'class="image-link"' in result

    def test_non_image_attachment(self):
        md = "[report.pdf](Attachments/report.pdf)"
        result = convert(md, note_id="note-1")
        assert 'class="attachment-link"' in result
        assert 'href="/api/attachment/note-1/report.pdf"' in result
        assert "report.pdf" in result
        assert 'target="_blank"' in result

    def test_image_with_webp_extension(self):
        md = "![img.png.webp](Attachments/img.png.webp)"
        result = convert(md, note_id="n1")
        assert "<img" in result

    def test_attachment_with_encoded_space(self):
        md = "[More testing.docx](Attachments/More%20testing.docx)"
        result = convert(md, note_id="n1")
        assert "More%20testing.docx" in result

    def test_image_note_id_with_slashes(self):
        md = "![photo.jpg](Attachments/photo.jpg)"
        result = convert(md, note_id="x-coredata://ABC/ICNote/p4")
        assert "/api/attachment/x-coredata%3A%2F%2FABC%2FICNote%2Fp4/photo.jpg" in result

    def test_attachment_note_id_with_slashes(self):
        md = "[report.pdf](Attachments/report.pdf)"
        result = convert(md, note_id="x-coredata://ABC/ICNote/p4")
        assert "/api/attachment/x-coredata%3A%2F%2FABC%2FICNote%2Fp4/report.pdf" in result


class TestLinks:
    def test_note_to_note_link(self):
        md = "[Other Note](../folder/Other Note.md)"
        result = convert(md)
        assert 'class="note-link"' in result
        assert 'data-path="../folder/Other Note.md"' in result
        assert ">Other Note</a>" in result

    def test_external_url(self):
        md = "[Google](https://www.google.com)"
        result = convert(md)
        assert 'href="https://www.google.com"' in result
        assert 'target="_blank"' in result
        assert 'rel="noopener"' in result

    def test_external_url_with_parens(self):
        md = "[British cup](https://en.wikipedia.org/wiki/Cup_(unit)#British_cup)"
        result = convert(md)
        assert 'href="https://en.wikipedia.org/wiki/Cup_(unit)#British_cup"' in result
        assert ">British cup</a>" in result

    def test_external_url_with_multiple_paren_segments(self):
        md = "[link](https://example.com/foo_(bar)_(baz))"
        result = convert(md)
        assert 'href="https://example.com/foo_(bar)_(baz)"' in result
        assert ">link</a>" in result


class TestLineBreaks:
    def test_hard_line_break_joins_next_line(self):
        """Trailing spaces cause the next line to be grouped with <br>, not a new <p>."""
        result = convert("line one  \nline two")
        assert "<br>" in result
        assert result.count("<p>") == 1

    def test_consecutive_hard_break_lines_grouped(self):
        """Consecutive lines with trailing spaces should be grouped into one <p> with <br>."""
        md = "line one  \nline two  \nline three  \n"
        result = convert(md)
        assert result.count("<p>") == 1
        assert result.count("<br>") >= 2
        assert "line one" in result
        assert "line two" in result
        assert "line three" in result

    def test_blank_line_separates_paragraphs(self):
        """A blank line should create separate paragraphs."""
        md = "paragraph one  \n\nparagraph two  \n"
        result = convert(md)
        assert result.count("<p>") == 2


class TestSkipTitle:
    def test_skip_matching_title(self):
        md = "# My Note\n\nbody text"
        result = convert(md, skip_title="My Note")
        assert "<h1>" not in result
        assert "body text" in result

    def test_keeps_non_matching_h1(self):
        md = "# Different Title\n\nbody text"
        result = convert(md, skip_title="My Note")
        assert "<h1>" in result

    def test_only_skips_first_h1(self):
        md = "# My Note\n\n# My Note\n\nbody"
        result = convert(md, skip_title="My Note")
        # First is skipped, second is kept
        assert result.count("<h1>") == 1


class TestEmpty:
    def test_empty_input(self):
        assert convert("") == ""

    def test_whitespace_only(self):
        # All whitespace lines should produce empty result
        assert convert("   \n   \n") == ""


class TestOrphanedMarkers:
    """Test handling of orphaned * markers from exporter cross-line formatting."""

    def test_trailing_double_star_stripped(self):
        result = convert("Tell everyone we're pausing assistant**")
        assert "assistant" in result
        assert "**" not in result

    def test_quadruple_stars_stripped(self):
        result = convert("Top of Mind****")
        assert "Top of Mind" in result
        assert "****" not in result

    def test_standalone_stars_produce_empty(self):
        assert convert("****") == ""

    def test_star_space_star_stripped(self):
        assert convert("** **") == ""

    def test_valid_bold_preserved(self):
        result = convert("**bold text**")
        assert "<strong>bold text</strong>" in result

    def test_valid_italic_preserved(self):
        result = convert("*italic text*")
        assert "<em>italic text</em>" in result


class TestHtmlEscaping:
    def test_angle_brackets_escaped(self):
        result = convert("use <div> tags")
        assert "&lt;div&gt;" in result

    def test_ampersand_escaped(self):
        result = convert("A & B")
        assert "&amp;" in result


class TestIntegrationWithRealNote:
    """Test with the actual TEST_NOTE.apple_generated.md file."""

    @pytest.fixture()
    def test_note_html(self):
        path = pathlib.Path(__file__).parent.parent / "test_data" / "TEST_NOTE.apple_generated.md"
        if not path.exists():
            pytest.skip("TEST_NOTE.apple_generated.md not available")
        md = path.read_text(encoding="utf-8")
        return convert(md, note_id="test-note")

    def test_converts_without_error(self, test_note_html):
        assert len(test_note_html) > 0

    def test_contains_title(self, test_note_html):
        assert "<h1>VLUV MVVL" in test_note_html

    def test_contains_headings(self, test_note_html):
        assert "<h1>rzrga rawr" in test_note_html
        assert "<h2>kajdzks rawr" in test_note_html
        assert "<h3>paykajdzks rawr" in test_note_html

    def test_contains_code_block(self, test_note_html):
        assert "<pre><code>" in test_note_html
        assert "coko rawr" in test_note_html

    def test_contains_lists(self, test_note_html):
        assert "<li>yaggar" in test_note_html
        assert "<li>djpk" in test_note_html

    def test_contains_blockquote(self, test_note_html):
        assert "<blockquote>" in test_note_html
        assert "j ygotx qaora" in test_note_html

    def test_contains_checklist(self, test_note_html):
        assert "aktkatxad tkatxgzpr zrac" in test_note_html
        assert "tkatxad tkatxgzpr zrac" in test_note_html

    def test_contains_inline_formatting(self, test_note_html):
        assert "<u>akdalgzka</u>" in test_note_html
        assert "<del>prlzxarkloask</del>" in test_note_html
        assert "<em>zrjgztp</em>" in test_note_html
        assert "<strong>yogd</strong>" in test_note_html

    def test_contains_attachment(self, test_note_html):
        assert "akzqaa_kjca_54321.bdf" in test_note_html
        assert "attachment-link" in test_note_html

    def test_contains_image(self, test_note_html):
        assert "<img" in test_note_html
        assert "Baggos-Ljpral-Lss" in test_note_html

    def test_contains_table(self, test_note_html):
        assert "<table>" in test_note_html
        assert "abbal gafr" in test_note_html

    def test_contains_ordered_list(self, test_note_html):
        assert "<ol>" in test_note_html
        assert "<mark>balbga</mark>" in test_note_html
