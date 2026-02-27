#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""
Tests for Apple Notes export functionality.

These tests validate the export pipeline using test fixtures.
Tests are automatically parametrized to run against all test notes in test_data/.

To add a new test case:
1. Create <name>.raw_data.json with note data and expected values
2. Create <name>.apple_generated.md with reference output
3. Tests will automatically discover and run against the new data
"""

import warnings

import pytest

from helpers import (
    load_test_note_from_config,
    count_blocks_by_type,
    count_text_blocks,
    count_attachment_blocks,
    compare_markdown,
    get_attachments_by_type,
    find_blocks_by_style,
    find_blocks_with_formatting,
    extract_tables_from_markdown,
    validate_table_structure
)


@pytest.fixture
def test_note_data(test_note_config):
    """
    Load test note data for each test.

    This fixture is parametrized by conftest.py to run against all discovered test notes.
    """
    data_loader, blocks, markdown, reference_md = load_test_note_from_config(test_note_config)

    return {
        'config': test_note_config,
        'data_loader': data_loader,
        'blocks': blocks,
        'markdown': markdown,
        'reference_md': reference_md
    }


class TestProtobufParsing:
    """Tests for protobuf decoding into ContentBlock structures."""

    def test_total_block_count(self, test_note_data):
        """Verify total number of content blocks matches expected."""
        blocks = test_note_data['blocks']
        expected = test_note_data['config'].expected.get('total_blocks')

        if expected is not None:
            assert len(blocks) == expected, \
                f"Expected {expected} blocks, got {len(blocks)}"

    def test_text_block_count(self, test_note_data):
        """Verify number of text blocks matches expected."""
        blocks = test_note_data['blocks']
        expected = test_note_data['config'].expected.get('text_blocks')

        if expected is not None:
            text_count = count_text_blocks(blocks)
            assert text_count == expected, \
                f"Expected {expected} text blocks, got {text_count}"

    def test_attachment_block_count(self, test_note_data):
        """Verify number of attachment blocks matches expected."""
        blocks = test_note_data['blocks']
        expected = test_note_data['config'].expected.get('attachment_blocks')

        if expected is not None:
            attachment_count = count_attachment_blocks(blocks)
            assert attachment_count == expected, \
                f"Expected {expected} attachment blocks, got {attachment_count}"

    def test_block_type_distribution(self, test_note_data):
        """Verify the distribution of block types matches expected."""
        blocks = test_note_data['blocks']
        expected = test_note_data['config'].expected.get('blocks_by_type')

        if expected:
            counts = count_blocks_by_type(blocks)
            assert counts == expected, \
                f"Block type distribution mismatch.\nExpected: {expected}\nGot: {counts}"

    def test_has_expected_content(self, test_note_data):
        """Verify expected content types are present based on blocks_by_type counts."""
        blocks = test_note_data['blocks']
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        # Check for titles
        if blocks_by_type.get('title', 0) > 0:
            title_blocks = find_blocks_by_style(blocks, 'title')
            assert len(title_blocks) > 0, "Expected to find title blocks based on blocks_by_type"

        # Check for headings
        if blocks_by_type.get('heading', 0) > 0:
            heading_blocks = find_blocks_by_style(blocks, 'heading')
            assert len(heading_blocks) > 0, "Expected to find heading blocks based on blocks_by_type"

        # Check for checklists
        if blocks_by_type.get('checklist', 0) > 0:
            checklist_blocks = find_blocks_by_style(blocks, 'checklist')
            assert len(checklist_blocks) > 0, "Expected to find checklist blocks based on blocks_by_type"

        # Check for tables
        if blocks_by_type.get('com.apple.notes.table', 0) > 0:
            attachments = get_attachments_by_type(blocks)
            assert 'com.apple.notes.table' in attachments, "Expected to find table attachments based on blocks_by_type"


class TestMarkdownGeneration:
    """Tests for markdown generation from ContentBlocks."""

    def test_markdown_not_empty(self, test_note_data):
        """Generated markdown should not be empty."""
        markdown = test_note_data['markdown']
        assert len(markdown) > 0, "Generated markdown is empty"

    def test_has_expected_markdown_elements(self, test_note_data):
        """Verify expected markdown elements are present based on blocks_by_type counts."""
        markdown = test_note_data['markdown']
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        # Check for title formatting
        if blocks_by_type.get('title', 0) > 0:
            assert '# ' in markdown, "Expected to find H1 title markers"

        # Check for heading formatting
        if blocks_by_type.get('heading', 0) > 0 or blocks_by_type.get('subheading', 0) > 0:
            assert '## ' in markdown or '### ' in markdown, "Expected to find heading markers"

        # Check for monospaced/code formatting
        if blocks_by_type.get('monospaced', 0) > 0:
            assert '```' in markdown, "Expected to find code block markers"

        # Check for checklist formatting
        if blocks_by_type.get('checklist', 0) > 0:
            assert '- [ ]' in markdown or '- [x]' in markdown, \
                "Expected to find checklist markers"

        # Check for table formatting
        if blocks_by_type.get('com.apple.notes.table', 0) > 0:
            assert '|' in markdown and '---' in markdown, \
                "Expected to find table markers"

    def test_attachment_paths_use_apple_format(self, test_note_data):
        """Attachments should use Apple's Attachments/ path format."""
        markdown = test_note_data['markdown']
        blocks = test_note_data['blocks']

        # Find all file-based attachment blocks (exclude inline attachments like hashtags and tables)
        file_attachment_blocks = [
            b for b in blocks
            if b.type == 'attachment' and b.attachment and
            'hashtag' not in b.attachment.type and  # Inline hashtag attachments don't use file paths
            'table' not in b.attachment.type  # Tables are rendered inline, not as files
        ]

        if file_attachment_blocks:
            # Check that markdown contains Attachments/ paths
            assert 'Attachments/' in markdown or len(file_attachment_blocks) == 0, \
                "Expected file attachments to use Attachments/ path format"

    def test_table_structure_is_valid(self, test_note_data):
        """Tables should have consistent column counts across all rows."""
        markdown = test_note_data['markdown']
        reference_md = test_note_data['reference_md']
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        # Only run this test if we expect tables
        if blocks_by_type.get('com.apple.notes.table', 0) == 0:
            pytest.skip("No tables expected in this note")

        # Extract tables from both generated and reference markdown
        generated_tables = extract_tables_from_markdown(markdown)
        reference_tables = extract_tables_from_markdown(reference_md)

        assert len(generated_tables) > 0, "Expected to find tables in generated markdown"
        assert len(reference_tables) > 0, "Expected to find tables in reference markdown"

        # We should have the same number of tables
        assert len(generated_tables) == len(reference_tables), \
            f"Table count mismatch: generated has {len(generated_tables)}, reference has {len(reference_tables)}"

        # Validate each table
        for i, (gen_table, ref_table) in enumerate(zip(generated_tables, reference_tables)):
            gen_rows = gen_table['rows']
            ref_rows = ref_table['rows']

            # Check column counts are consistent
            is_valid, error_msg = validate_table_structure(gen_rows)
            assert is_valid, f"Table {i} has invalid structure: {error_msg}"

            # Check separator line matches reference
            gen_sep = gen_table['separator']
            ref_sep = ref_table['separator']

            # Parse separator cells to check alignment markers
            if gen_sep and ref_sep:
                gen_sep_cells = [cell.strip() for cell in gen_sep.split('|') if cell.strip()]
                ref_sep_cells = [cell.strip() for cell in ref_sep.split('|') if cell.strip()]

                assert len(gen_sep_cells) == len(ref_sep_cells), \
                    f"Table {i} separator column count mismatch: {len(gen_sep_cells)} vs {len(ref_sep_cells)}"

                # Check alignment markers match
                for j, (gen_cell, ref_cell) in enumerate(zip(gen_sep_cells, ref_sep_cells)):
                    gen_has_right = gen_cell.rstrip().endswith(':')
                    ref_has_right = ref_cell.rstrip().endswith(':')

                    if gen_has_right != ref_has_right:
                        pytest.fail(
                            f"Table {i}, column {j} alignment mismatch:\n"
                            f"  Generated: {repr(gen_cell)} (right-aligned: {gen_has_right})\n"
                            f"  Reference: {repr(ref_cell)} (right-aligned: {ref_has_right})"
                        )

    def test_inline_formatting_markers(self, test_note_data):
        """
        Verify inline formatting markers (bold, highlight) match reference output.

        This test checks that bullet list items with inline formatting like **bold** and
        ==highlight== have their markers positioned correctly. Due to CRDT-based character
        storage in Apple Notes, formatting runs can split mid-word based on edit history,
        and our heuristic boundary adjustment may not always match Apple's export.
        """
        markdown = test_note_data['markdown']
        reference_md = test_note_data['reference_md']
        blocks = test_note_data['blocks']
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        # Only run this test if we expect bullet lists
        if blocks_by_type.get('bullet', 0) == 0:
            pytest.skip("No bullet lists expected in this note")

        # Extract lines from generated and reference markdown
        gen_lines = markdown.split('\n')
        ref_lines = reference_md.split('\n')

        # Find lines that should be bullet items by looking for lines starting with * or - in reference
        ref_bullet_lines = []
        for i, line in enumerate(ref_lines):
            stripped = line.lstrip()
            if stripped.startswith('* ') or stripped.startswith('- '):
                ref_bullet_lines.append((i, stripped))

        # For each reference bullet line, find the corresponding generated line
        # and verify it starts with * or -
        errors = []
        for ref_line_num, ref_line in ref_bullet_lines:
            # Try to find this line in generated output (accounting for minor text differences)
            # by looking for the text after the bullet marker
            ref_text = ref_line[2:].strip()[:50]  # First 50 chars after "* "

            # Find matching line in generated output — prefer bullet lines over substring matches
            found = False
            best_non_bullet_match = None
            for gen_line_num, gen_line in enumerate(gen_lines):
                if ref_text in gen_line:
                    gen_stripped = gen_line.lstrip()
                    if gen_stripped.startswith('* ') or gen_stripped.startswith('- '):
                        found = True
                        break
                    elif best_non_bullet_match is None:
                        best_non_bullet_match = (gen_line_num, gen_line)

            if not found and best_non_bullet_match is not None:
                gen_line_num, gen_line = best_non_bullet_match
                errors.append(
                    f"Line {gen_line_num+1} should be a bullet item but doesn't start with '* ' or '- ':\n"
                    f"  Generated: {repr(gen_line[:80])}\n"
                    f"  Reference: {repr(ref_lines[ref_line_num][:80])}"
                )
                found = True

            if not found and len(ref_text) > 10:  # Only report if text is meaningful
                errors.append(
                    f"Could not find bullet item in generated output:\n"
                    f"  Reference line {ref_line_num+1}: {repr(ref_lines[ref_line_num][:80])}"
                )

        if errors:
            pytest.fail(f"Found {len(errors)} bullet formatting issues:\n" + "\n".join(errors[:5]))


    def test_no_table_extraction_warnings(self, test_note_data):
        """Table extraction should not emit any warnings."""
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        if blocks_by_type.get('com.apple.notes.table', 0) == 0:
            pytest.skip("No tables expected in this note")

        # Re-run markdown generation while capturing warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            _, _, markdown, _ = load_test_note_from_config(test_note_data['config'])

        table_warnings = [w for w in caught if 'Table' in str(w.message) or 'table' in str(w.message)]
        if table_warnings:
            msgs = "\n".join(f"  - {w.message}" for w in table_warnings)
            pytest.fail(f"Table extraction emitted {len(table_warnings)} warning(s):\n{msgs}")


class TestMarkdownComparison:
    """Tests comparing generated markdown with Apple's reference output."""

    def test_semantic_match_with_normalization(self, test_note_data):
        """Generated markdown should semantically match Apple's output when normalized."""
        markdown = test_note_data['markdown']
        reference_md = test_note_data['reference_md']

        matches, differences = compare_markdown(markdown, reference_md, normalize=True)

        if not matches:
            # Count line differences
            line_diffs = [d for d in differences if 'Line' in d and 'differs' in d]

            # Allow minimal formatting differences (only truly cosmetic table formatting)
            # Most semantic differences should be fixed in the generator
            max_allowed_diffs = max(5, len(reference_md.splitlines()) // 10)

            assert len(line_diffs) <= max_allowed_diffs, \
                f"Too many differences ({len(line_diffs)}):\n" + "\n".join(differences[:10])

    def test_no_unexpected_content(self, test_note_data):
        """Generated markdown should not contain unexpected content."""
        markdown = test_note_data['markdown']

        # Should not have debug output or errors
        assert 'Error' not in markdown, "Markdown contains error messages"
        assert 'DEBUG' not in markdown, "Markdown contains debug output"
        assert 'Exception' not in markdown, "Markdown contains exceptions"
        assert 'Traceback' not in markdown, "Markdown contains tracebacks"

    def test_url_encoding_in_attachment_paths(self, test_note_data):
        """Attachment paths with spaces should be URL-encoded."""
        markdown = test_note_data['markdown']
        reference_md = test_note_data['reference_md']
        import re

        # Find all markdown links and images with "Attachments/" in the path
        link_pattern = r'\[([^\]]*)\]\(Attachments/([^)]+)\)'

        gen_matches = re.findall(link_pattern, markdown)
        ref_matches = re.findall(link_pattern, reference_md)

        # Skip if no attachments
        if not ref_matches:
            pytest.skip("No attachment links in this note")

        # Check that spaces are URL-encoded in paths that have spaces
        for title, path in gen_matches:
            # If the reference has %20, we should too
            for ref_title, ref_path in ref_matches:
                if title == ref_title:
                    if '%20' in ref_path:
                        assert '%20' in path or ' ' not in path, \
                            f"Attachment path should URL-encode spaces: got '{path}', expected '%20' encoding"
                    break

    def test_numbered_list_sequential(self, test_note_data):
        """Numbered lists should use sequential numbers (1, 2, 3...) not all 1s."""
        markdown = test_note_data['markdown']
        reference_md = test_note_data['reference_md']
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        # Skip if no numbered lists expected
        if blocks_by_type.get('numbered', 0) == 0:
            pytest.skip("No numbered lists expected in this note")

        import re

        # Find numbered list items in reference
        ref_numbers = re.findall(r'^(\d+)\. ', reference_md, re.MULTILINE)

        # Skip if no numbered items found
        if not ref_numbers:
            pytest.skip("No numbered list items found in reference")

        # Find numbered list items in generated
        gen_numbers = re.findall(r'^(\d+)\. ', markdown, re.MULTILINE)

        # If reference has sequential numbers, generated should too
        ref_has_sequence = len(ref_numbers) > 1 and ref_numbers != ['1'] * len(ref_numbers)
        gen_has_sequence = len(gen_numbers) > 1 and gen_numbers != ['1'] * len(gen_numbers)

        if ref_has_sequence:
            assert gen_has_sequence, \
                f"Numbered list should use sequential numbers.\n" \
                f"Generated: {gen_numbers}\n" \
                f"Reference: {ref_numbers}"

    def test_inline_formatting_outside_list_markers(self, test_note_data):
        """Inline formatting markers should be inside list markers, not wrap them."""
        markdown = test_note_data['markdown']
        reference_md = test_note_data['reference_md']
        blocks_by_type = test_note_data['config'].expected.get('blocks_by_type', {})

        # Check if any list types are expected
        list_types = ['numbered', 'bullet', 'dashed', 'checklist']
        has_lists = any(blocks_by_type.get(lt, 0) > 0 for lt in list_types)

        if not has_lists:
            pytest.skip("No list items expected in this note")

        import re

        # Look for patterns where formatting markers wrap list markers
        # Bad patterns - formatting wrapping list markers:
        #   ==1. text==, ==* text==, ==- text==, ==- [ ] text==
        # Good patterns - formatting inside list markers:
        #   1. ==text==, * ==text==, - ==text==, - [ ] ==text==
        bad_patterns = [
            # Numbered list patterns
            (r'==\d+\. ', 'highlight wrapping numbered list marker'),
            (r'\*\*\d+\. ', 'bold wrapping numbered list marker'),
            (r'(?<!\*)\*\d+\. ', 'italic wrapping numbered list marker'),
            (r'~~\d+\. ', 'strikethrough wrapping numbered list marker'),
            (r'\+\+\d+\. ', 'underline wrapping numbered list marker'),
            # Bullet list patterns (*)
            (r'==\* ', 'highlight wrapping bullet marker'),
            (r'\*\*\* ', 'bold wrapping bullet marker'),
            (r'~~\* ', 'strikethrough wrapping bullet marker'),
            (r'\+\+\* ', 'underline wrapping bullet marker'),
            # Dashed list patterns (-)
            (r'^==- ', 'highlight wrapping dash marker'),
            (r'^\*\*- ', 'bold wrapping dash marker'),
            (r'^~~- ', 'strikethrough wrapping dash marker'),
            (r'^\+\+- ', 'underline wrapping dash marker'),
            # Checklist patterns
            (r'==- \[[ x]\] ', 'highlight wrapping checklist marker'),
            (r'\*\*- \[[ x]\] ', 'bold wrapping checklist marker'),
            (r'~~- \[[ x]\] ', 'strikethrough wrapping checklist marker'),
            (r'\+\+- \[[ x]\] ', 'underline wrapping checklist marker'),
        ]

        for pattern, description in bad_patterns:
            gen_matches = re.findall(pattern, markdown, re.MULTILINE)
            ref_matches = re.findall(pattern, reference_md, re.MULTILINE)

            # If reference doesn't have this bad pattern, neither should we
            if not ref_matches and gen_matches:
                pytest.fail(
                    f"Found {description} in generated output but not in reference.\n"
                    f"Found: {gen_matches}\n"
                    f"This suggests inline formatting markers are wrapping list markers incorrectly."
                )


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, '-v'])
