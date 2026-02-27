#!/Users/brian/dev/noteworthy/.venv/bin/python3
"""Tests for DatabaseNoteDataLoader queries against a test SQLite database."""

import pytest

from noteworthy.database import DatabaseNoteDataLoader


class TestGetNoteData:
    def test_returns_bytes(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            data = loader.get_note_data(100)
            assert isinstance(data, bytes)
            assert len(data) > 0
        finally:
            loader.close()

    def test_missing_note_raises(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            with pytest.raises(ValueError, match="not found"):
                loader.get_note_data(99999)
        finally:
            loader.close()


class TestGetAttachmentMetadata:
    def test_exact_match(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            meta = loader.get_attachment_metadata("att-uuid-400")
            assert meta is not None
            assert meta["title"] == "My Photo"
            assert meta["uuid"] == "att-uuid-400"
            assert meta["file_path"] is not None
            assert "photo.jpg" in meta["file_path"]
        finally:
            loader.close()

    def test_file_path_with_generation(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            meta = loader.get_attachment_metadata("att-uuid-400")
            assert meta["file_path"] is not None
            assert "gen1" in meta["file_path"]
        finally:
            loader.close()

    def test_file_path_without_generation(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            meta = loader.get_attachment_metadata("att-uuid-401")
            assert meta["file_path"] is not None
            assert "doc.pdf" in meta["file_path"]
        finally:
            loader.close()

    def test_link_attachment_prefix_match(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            # Link attachments match by UUID prefix (first 3 segments)
            meta = loader.get_attachment_metadata("link-aaa-bbb-different-suffix")
            assert meta is not None
            assert meta["type"] == "link"
            assert meta["alt_text"] == "Second Note"
            assert meta["token_content_identifier"] == "applenotes:note/aaa-bbb-ccc-101"
        finally:
            loader.close()

    def test_unknown_uuid_returns_none(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            meta = loader.get_attachment_metadata("nonexistent-uuid")
            assert meta is None
        finally:
            loader.close()


class TestGetTableData:
    def test_returns_bytes(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            data = loader.get_table_data("table-uuid-410")
            assert data is not None
            assert isinstance(data, bytes)
            assert len(data) > 0
        finally:
            loader.close()

    def test_unknown_returns_none(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            data = loader.get_table_data("nonexistent-table")
            assert data is None
        finally:
            loader.close()


class TestGetGalleryChildren:
    def test_returns_ordered_children(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            children = loader.get_gallery_children("gallery-uuid-460")
            assert len(children) == 2
            assert children[0]["uuid"] == "gallery-child-1"
            assert children[0]["title"] == "Gallery Image 1"
            assert children[1]["uuid"] == "gallery-child-2"
            assert children[1]["title"] == "Gallery Image 2"
        finally:
            loader.close()

    def test_children_have_file_paths(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            children = loader.get_gallery_children("gallery-uuid-460")
            assert children[0]["file_path"] is not None
            assert "img1.jpg" in children[0]["file_path"]
            assert children[1]["file_path"] is not None
            assert "img2.png" in children[1]["file_path"]
        finally:
            loader.close()

    def test_unknown_gallery_returns_empty(self, notestore_db):
        loader = DatabaseNoteDataLoader(str(notestore_db))
        try:
            children = loader.get_gallery_children("nonexistent-gallery")
            assert children == []
        finally:
            loader.close()
