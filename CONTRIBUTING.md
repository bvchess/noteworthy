# Contributing to Noteworthy

Thanks for your interest in contributing.

## Getting Started

1. Fork the repo and create a branch.
2. Set up the dev environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

3. Run tests:

```bash
python -m pytest tests/ -v
```

## Adding Test Fixtures

1. Create the note in Apple Notes.
2. Find the note ID in the database.
3. Extract test data:

```bash
.venv/bin/python tests/extract_test_data.py <note_id> <test_name>
```

4. Export Apple Notes reference markdown and save as:

```
tests/test_data/<test_name>.apple_generated.md
```

5. Update expected counts in:

```
tests/test_data/<test_name>.raw_data.json
```

## Code Style

- Keep changes focused and readable.
- Prefer small, well-scoped PRs.
- Add or update tests when behavior changes.

## Privacy

This project reads local Apple Notes data. Do not share test fixtures or backups that include personal or sensitive content.

## Questions

Open a GitHub issue if you have questions or a proposed change.
