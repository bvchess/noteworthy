# Test Suite for Apple Notes Exporter

This directory contains the test infrastructure for the Apple Notes markdown exporter.

## Structure

```
tests/
├── test_data/              # Test fixtures and reference data
│   ├── TEST_NOTE.raw_data.json          # Extracted database data with expected values
│   └── TEST_NOTE.apple_generated.md     # Apple's reference markdown output
├── test_output/            # Generated test output (gitignored)
│   └── TEST_NOTE/
│       ├── blocks.txt           # Human-readable ContentBlock structure
│       ├── generated.md         # Our generated markdown
│       └── reference.md         # Apple's reference (for easy comparison)
├── conftest.py             # pytest configuration and test discovery
├── extract_test_data.py    # Script to create new test fixtures from database
├── helpers.py              # Test helper functions (validation, comparison)
└── test_export.py          # pytest test suite (parametrized)
```

## Test Files

### `test_data/`
Contains test fixtures for different notes:
- `<test_name>.raw_data.json` - Database extract with base64-encoded binary data and expected values
- `<test_name>.apple_generated.md` - Reference markdown from Apple's export

### `test_output/`
Generated test output for inspection (gitignored):
- `<test_name>/blocks.txt` - Human-readable dump of all ContentBlocks with their properties
- `<test_name>/generated.md` - The markdown we generated from the protobuf
- `<test_name>/reference.md` - Copy of Apple's reference for easy side-by-side comparison

These files are regenerated every test run. Use them to:
- Debug protobuf parsing issues (check blocks.txt)
- See exactly what markdown we're generating (check generated.md)
- Compare our output with Apple's (diff generated.md reference.md)

### `conftest.py`
pytest configuration that automatically discovers test notes:
- Scans `test_data/` for all `*.raw_data.json` files
- Validates each has a corresponding `.apple_generated.md` file
- Loads expected values from JSON files
- Parametrizes tests to run against all discovered notes

### `extract_test_data.py`
Utility script to create new test fixtures from the Apple Notes database:
```bash
../.venv/bin/python3 extract_test_data.py <note_id> <test_name>
```

Example:
```bash
../.venv/bin/python3 extract_test_data.py 21082 TEST_NOTE
```

### `helpers.py`
Helper functions for tests:
- `load_test_note_from_config()` - Load test data from TestNoteConfig object
- `load_test_note()` - Load test data from file path (legacy)
- `count_blocks_by_type()` - Analyze block distribution
- `compare_markdown()` - Compare generated vs reference markdown
- `find_blocks_by_style()` - Find blocks with specific styles
- `find_blocks_with_formatting()` - Find blocks with specific formatting

### `test_export.py`
Main test suite with 10 parameterized tests organized into 3 classes.
All tests run once per discovered test note.

**TestProtobufParsing** (5 tests)
- `test_total_block_count` - Verifies total ContentBlock count
- `test_text_block_count` - Verifies text block count
- `test_attachment_block_count` - Verifies attachment block count
- `test_block_type_distribution` - Verifies distribution matches expected
- `test_has_expected_content` - Verifies expected content types present (titles, headings, formatting, etc.)

**TestMarkdownGeneration** (3 tests)
- `test_markdown_not_empty` - Verifies markdown was generated
- `test_has_expected_markdown_elements` - Verifies expected markdown syntax present
- `test_attachment_paths_use_apple_format` - Verifies attachments use Attachments/ paths

**TestMarkdownComparison** (2 tests)
- `test_semantic_match_with_normalization` - Validates against Apple's reference with normalization
- `test_no_unexpected_content` - Validates no errors/debug output in markdown

## Running Tests

Run all tests:
```bash
../.venv/bin/python3 -m pytest test_export.py -v
```

Run tests for a specific note:
```bash
../.venv/bin/python3 -m pytest test_export.py -k "TEST_NOTE" -v
```

Run specific test class:
```bash
../.venv/bin/python3 -m pytest test_export.py::TestProtobufParsing -v
```

Run specific test:
```bash
../.venv/bin/python3 -m pytest test_export.py::TestProtobufParsing::test_total_block_count -v
```

## Architecture

The test infrastructure is designed around the **data loader pattern**:

1. **Test data extraction** - `extract_test_data.py` extracts raw database data
2. **Test data loading** - `TestNoteDataLoader` loads from JSON fixtures
3. **Export pipeline** - `NoteExporter` processes data using the data loader
4. **Validation** - Helper functions validate structure and output

This allows testing the full export pipeline (protobuf → ContentBlocks → markdown) without accessing the real Apple Notes database.

## Adding New Tests

To add a new test case:

1. Create the note in Apple Notes
2. Find the note ID in the database
3. Extract test data:
   ```bash
   ../.venv/bin/python3 extract_test_data.py <note_id> <test_name>
   ```
4. Export reference markdown from Apple Notes to `test_data/<test_name>.apple_generated.md`
5. Add expected values to the generated `<test_name>.raw_data.json`:
   ```json
   {
     "note_data": "...",
     "attachments": [...],
     "expected": {
       "total_blocks": 45,
       "text_blocks": 40,
       "attachment_blocks": 5,
       "blocks_by_type": {
         "title": 2,
         "heading": 1,
         "body": 22,
         "com.adobe.pdf": 1,
         ...
       }
     }
   }
   ```
   Note: `blocks_by_type` counts determine what tests expect to find (e.g., if title: 2, tests verify title blocks exist)
6. Tests will automatically discover and run against the new data!

## Test Parametrization

Tests are automatically parametrized to run against all discovered test notes in `test_data/`:
- `conftest.py` discovers all `*.raw_data.json` files
- Each test note must have:
  - `<name>.raw_data.json` with note data and expected values
  - `<name>.apple_generated.md` with Apple's reference output
- All tests run once per discovered note
- Add new test notes by simply adding the files - no code changes needed!

## Current Test Coverage

- ✅ Protobuf parsing (5 tests per note)
- ✅ Markdown generation (3 tests per note)
- ✅ Output comparison (2 tests per note)
- **Total: 10 tests × number of test notes**

### TEST_NOTE Coverage
The TEST_NOTE fixture includes:
- Multiple heading levels (title, heading, subheading)
- Text formatting (bold, italic, underline, strikethrough, highlight)
- Lists (bullet, dashed, numbered, checklist)
- Block quotes
- Monospaced/code blocks
- Attachments (PDF, Word doc, image)
- Tables (2 tables with different alignments)

This provides comprehensive coverage of Apple Notes formatting features.
