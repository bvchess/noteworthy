from __future__ import annotations

import json
import pathlib
import threading
import urllib.parse
import urllib.request

import pytest

from noteworthy.viewer.server import create_server


def _write_metadata(path: pathlib.Path, data: dict):
    path.mkdir(parents=True, exist_ok=True)
    with (path / ".noteworthy.json").open("w") as f:
        json.dump(data, f)


ACCOUNT_ID = "x-coredata://ABC/ICAccount/p1"
FOLDER_ID = "x-coredata://ABC/ICFolder/p2"
NOTE_ID = "x-coredata://ABC/ICNote/p4"


@pytest.fixture()
def backup_dir(tmp_path):
    """Create a minimal backup for server testing."""
    acct_dir = tmp_path / "iCloud"
    _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

    folder_dir = acct_dir / "Notes"
    _write_metadata(folder_dir, {
        "type": "folder", "name": "Notes", "id": FOLDER_ID,
        "parent_id": ACCOUNT_ID,
    })

    note_dir = folder_dir / "Test Note"
    _write_metadata(note_dir, {
        "type": "note", "name": "Test Note", "id": NOTE_ID, "uuid": "UUID-1",
        "creation_date": "2024-01-01T00:00:00+00:00",
        "modification_date": "2024-06-15T12:30:00+00:00",
        "folders": [FOLDER_ID],
        "tags": ["greeting"],
    })
    note_dir.mkdir(parents=True, exist_ok=True)
    (note_dir / "Test Note.md").write_text(
        "# Test Note\n\nHello world #greeting\n\n![photo.jpg](Attachments/photo.jpg)  \n",
        encoding="utf-8",
    )

    # Create an attachment
    att_dir = note_dir / "Attachments"
    att_dir.mkdir(exist_ok=True)
    (att_dir / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-data")

    return tmp_path


@pytest.fixture()
def server(backup_dir):
    """Start a test server in a background thread."""
    srv = create_server(backup_dir)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()


@pytest.fixture()
def base_url(server):
    host, port = server.server_address
    return f"http://{host}:{port}"


def _get(url: str) -> tuple[int, bytes, dict]:
    """Make a GET request and return (status, body, headers)."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def _get_json(url: str) -> tuple[int, dict | list]:
    """Make a GET request and parse JSON response."""
    status, body, _ = _get(url)
    return status, json.loads(body)


class TestStaticFiles:
    def test_index_returns_html(self, base_url):
        status, body, headers = _get(base_url + "/")
        assert status == 200
        assert "text/html" in headers.get("Content-Type", "")

    def test_style_css(self, base_url):
        status, body, headers = _get(base_url + "/static/style.css")
        assert status == 200
        assert "text/css" in headers.get("Content-Type", "")

    def test_app_js(self, base_url):
        status, body, headers = _get(base_url + "/static/app.js")
        assert status == 200
        assert "javascript" in headers.get("Content-Type", "")


class TestApiTree:
    def test_returns_tree(self, base_url):
        status, data = _get_json(base_url + "/api/tree")
        assert status == 200
        assert isinstance(data, dict)
        assert "accounts" in data
        assert "tags" in data
        accounts = data["accounts"]
        assert len(accounts) == 1
        assert accounts[0]["name"] == "iCloud"

    def test_tree_has_all_folder(self, base_url):
        status, data = _get_json(base_url + "/api/tree")
        folders = data["accounts"][0]["folders"]
        assert folders[0]["name"] == "All iCloud"
        assert folders[0]["is_all_folder"] is True
        assert folders[0]["note_count"] == 1

    def test_tree_has_folders(self, base_url):
        status, data = _get_json(base_url + "/api/tree")
        folders = data["accounts"][0]["folders"]
        # First folder is "All iCloud", second is "Notes"
        assert len(folders) == 2
        assert folders[1]["name"] == "Notes"

    def test_tags_is_list(self, base_url):
        status, data = _get_json(base_url + "/api/tree")
        assert isinstance(data["tags"], list)


class TestApiNotes:
    def test_notes_in_folder(self, base_url):
        status, data = _get_json(base_url + f"/api/notes?folder={FOLDER_ID}")
        assert status == 200
        assert data["sort_order"] == "default"
        notes = data["notes"]
        assert len(notes) == 1
        assert notes[0]["name"] == "Test Note"
        assert "modification_date" in notes[0]
        assert "creation_date" in notes[0]

    def test_all_folder_returns_notes(self, base_url):
        all_folder_id = f"__all__:{ACCOUNT_ID}"
        status, data = _get_json(base_url + f"/api/notes?folder={urllib.parse.quote(all_folder_id, safe='')}")
        assert status == 200
        assert data["sort_order"] == "default"
        assert len(data["notes"]) == 1
        assert data["notes"][0]["name"] == "Test Note"

    def test_notes_unknown_folder(self, base_url):
        status, data = _get_json(base_url + "/api/notes?folder=nonexistent")
        assert status == 200
        assert data["notes"] == []

    def test_note_with_image_has_first_image(self, base_url):
        status, data = _get_json(base_url + f"/api/notes?folder={FOLDER_ID}")
        assert status == 200
        note = data["notes"][0]
        assert "first_image" in note
        assert note["first_image"].startswith("/api/attachment/")
        assert "photo.jpg" in note["first_image"]

    def test_note_first_image_url_is_fetchable(self, base_url):
        status, data = _get_json(base_url + f"/api/notes?folder={FOLDER_ID}")
        first_image_url = data["notes"][0]["first_image"]
        img_status, img_body, img_headers = _get(base_url + first_image_url)
        assert img_status == 200
        assert "image" in img_headers.get("Content-Type", "")


class TestApiNote:
    def test_get_note(self, base_url):
        encoded_id = urllib.parse.quote(NOTE_ID, safe="")
        status, data = _get_json(base_url + f"/api/note/{encoded_id}")
        assert status == 200
        assert data["name"] == "Test Note"
        assert "<h1>" in data["html"]
        assert "Hello world" in data["html"]

    def test_note_has_attachments(self, base_url):
        encoded_id = urllib.parse.quote(NOTE_ID, safe="")
        status, data = _get_json(base_url + f"/api/note/{encoded_id}")
        assert "photo.jpg" in data["attachments"]

    def test_unknown_note(self, base_url):
        status, _ = _get_json(base_url + "/api/note/nonexistent")
        assert status == 404


class TestApiAttachment:
    def test_serve_attachment(self, base_url):
        encoded_id = urllib.parse.quote(NOTE_ID, safe="")
        status, body, headers = _get(base_url + f"/api/attachment/{encoded_id}/photo.jpg")
        assert status == 200
        assert "image/jpeg" in headers.get("Content-Type", "")
        assert body.startswith(b"\xff\xd8\xff\xe0")

    def test_missing_attachment(self, base_url):
        encoded_id = urllib.parse.quote(NOTE_ID, safe="")
        status, _, _ = _get(base_url + f"/api/attachment/{encoded_id}/missing.png")
        assert status == 404


class TestApiSearch:
    def test_search_finds_note(self, base_url):
        status, data = _get_json(base_url + "/api/search?q=hello")
        assert status == 200
        assert len(data) >= 1
        assert any(r["note_id"] == NOTE_ID for r in data)

    def test_tag_search(self, base_url):
        status, data = _get_json(base_url + "/api/search?q=%23greeting")
        assert status == 200
        assert len(data) == 1
        assert data[0]["note_id"] == NOTE_ID

    def test_tag_search_no_match(self, base_url):
        status, data = _get_json(base_url + "/api/search?q=%23nonexistent")
        assert status == 200
        assert data == []

    def test_empty_search(self, base_url):
        status, data = _get_json(base_url + "/api/search?q=")
        assert status == 200
        assert data == []


class TestUnknownPath:
    def test_404(self, base_url):
        status, data = _get_json(base_url + "/nonexistent")
        assert status == 404


NOTE_ID_2 = "x-coredata://ABC/ICNote/p5"


@pytest.fixture()
def two_note_backup_dir(tmp_path):
    """Backup with two notes so we can test relative link resolution."""
    acct_dir = tmp_path / "iCloud"
    _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

    folder_dir = acct_dir / "Notes"
    _write_metadata(folder_dir, {
        "type": "folder", "name": "Notes", "id": FOLDER_ID,
        "parent_id": ACCOUNT_ID,
    })

    note_dir = folder_dir / "Note A"
    _write_metadata(note_dir, {
        "type": "note", "name": "Note A", "id": NOTE_ID, "uuid": "UUID-1",
        "creation_date": "2024-01-01T00:00:00+00:00",
        "modification_date": "2024-01-01T00:00:00+00:00",
        "folders": [FOLDER_ID],
    })
    (note_dir / "Note A.md").write_text("# Note A\n\nLinked to B\n", encoding="utf-8")

    note_dir_2 = folder_dir / "Note B"
    _write_metadata(note_dir_2, {
        "type": "note", "name": "Note B", "id": NOTE_ID_2, "uuid": "UUID-2",
        "creation_date": "2024-01-02T00:00:00+00:00",
        "modification_date": "2024-01-02T00:00:00+00:00",
        "folders": [FOLDER_ID],
    })
    (note_dir_2 / "Note B.md").write_text("# Note B\n\nTarget note\n", encoding="utf-8")

    return tmp_path


@pytest.fixture()
def two_note_server(two_note_backup_dir):
    """Start a test server with two notes."""
    srv = create_server(two_note_backup_dir)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()


@pytest.fixture()
def two_note_base_url(two_note_server):
    host, port = two_note_server.server_address
    return f"http://{host}:{port}"


class TestApiResolveLink:
    def test_valid_relative_link(self, two_note_base_url, two_note_backup_dir):
        # Note A links to ../Note B/Note B.md (relative from Note A's md file)
        rel = "../Note B/Note B.md"
        url = (
            two_note_base_url + "/api/resolve-link"
            + "?from=" + urllib.parse.quote(NOTE_ID, safe="")
            + "&rel=" + urllib.parse.quote(rel, safe="")
        )
        status, data = _get_json(url)
        assert status == 200
        assert data["note_id"] == NOTE_ID_2

    def test_unknown_from_note(self, two_note_base_url):
        rel = "../Note B/Note B.md"
        url = (
            two_note_base_url + "/api/resolve-link"
            + "?from=" + urllib.parse.quote("x-coredata://XXX/nonexistent", safe="")
            + "&rel=" + urllib.parse.quote(rel, safe="")
        )
        status, data = _get_json(url)
        assert status == 404

    def test_unresolvable_rel_path(self, two_note_base_url):
        rel = "../Nonexistent/Nonexistent.md"
        url = (
            two_note_base_url + "/api/resolve-link"
            + "?from=" + urllib.parse.quote(NOTE_ID, safe="")
            + "&rel=" + urllib.parse.quote(rel, safe="")
        )
        status, data = _get_json(url)
        assert status == 404

    def test_missing_params(self, two_note_base_url):
        status, data = _get_json(two_note_base_url + "/api/resolve-link")
        assert status == 404


# --- Sort order tests ---

FOLDER_ID_CREATED = "x-coredata://ABC/ICFolder/p10"
FOLDER_ID_TITLE = "x-coredata://ABC/ICFolder/p11"
NOTE_ID_A = "x-coredata://ABC/ICNote/p20"
NOTE_ID_B = "x-coredata://ABC/ICNote/p21"
NOTE_ID_C = "x-coredata://ABC/ICNote/p22"


@pytest.fixture()
def sort_order_backup_dir(tmp_path):
    """Backup with folders using different sort orders and multiple notes."""
    acct_dir = tmp_path / "iCloud"
    _write_metadata(acct_dir, {"type": "account", "name": "iCloud", "id": ACCOUNT_ID})

    # Folder sorted by date_created
    folder_created = acct_dir / "Journal"
    _write_metadata(folder_created, {
        "type": "folder", "name": "Journal", "id": FOLDER_ID_CREATED,
        "parent_id": ACCOUNT_ID, "sort_order": "date_created",
    })

    # Folder sorted by title
    folder_title = acct_dir / "Reference"
    _write_metadata(folder_title, {
        "type": "folder", "name": "Reference", "id": FOLDER_ID_TITLE,
        "parent_id": ACCOUNT_ID, "sort_order": "title",
    })

    # Note A: created first (Jan), modified last (June) — name "Zebra"
    note_a = folder_created / "Zebra"
    _write_metadata(note_a, {
        "type": "note", "name": "Zebra", "id": NOTE_ID_A, "uuid": "UUID-A",
        "creation_date": "2024-01-01T00:00:00+00:00",
        "modification_date": "2024-06-15T00:00:00+00:00",
        "folders": [FOLDER_ID_CREATED],
    })
    (note_a / "Zebra.md").write_text("# Zebra\n\nContent\n", encoding="utf-8")

    # Note B: created second (June), modified first (Jan) — name "Apple"
    note_b = folder_created / "Apple"
    _write_metadata(note_b, {
        "type": "note", "name": "Apple", "id": NOTE_ID_B, "uuid": "UUID-B",
        "creation_date": "2024-06-01T00:00:00+00:00",
        "modification_date": "2024-01-01T00:00:00+00:00",
        "folders": [FOLDER_ID_CREATED],
    })
    (note_b / "Apple.md").write_text("# Apple\n\nContent\n", encoding="utf-8")

    # Note C in title-sort folder — name "Banana"
    note_c = folder_title / "Banana"
    _write_metadata(note_c, {
        "type": "note", "name": "Banana", "id": NOTE_ID_C, "uuid": "UUID-C",
        "creation_date": "2024-03-01T00:00:00+00:00",
        "modification_date": "2024-03-01T00:00:00+00:00",
        "folders": [FOLDER_ID_TITLE],
    })
    (note_c / "Banana.md").write_text("# Banana\n\nContent\n", encoding="utf-8")

    return tmp_path


@pytest.fixture()
def sort_order_server(sort_order_backup_dir):
    srv = create_server(sort_order_backup_dir)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()


@pytest.fixture()
def sort_order_base_url(sort_order_server):
    host, port = sort_order_server.server_address
    return f"http://{host}:{port}"


class TestSortOrder:
    def test_date_created_sort(self, sort_order_base_url):
        status, data = _get_json(sort_order_base_url + f"/api/notes?folder={FOLDER_ID_CREATED}")
        assert status == 200
        assert data["sort_order"] == "date_created"
        names = [n["name"] for n in data["notes"]]
        # Newest creation first: Apple (June) then Zebra (January)
        assert names == ["Apple", "Zebra"]

    def test_title_sort(self, sort_order_base_url):
        status, data = _get_json(sort_order_base_url + f"/api/notes?folder={FOLDER_ID_TITLE}")
        assert status == 200
        assert data["sort_order"] == "title"
        names = [n["name"] for n in data["notes"]]
        assert names == ["Banana"]

    def test_all_folder_uses_default_sort(self, sort_order_base_url):
        all_id = f"__all__:{ACCOUNT_ID}"
        status, data = _get_json(sort_order_base_url + f"/api/notes?folder={urllib.parse.quote(all_id, safe='')}")
        assert status == 200
        assert data["sort_order"] == "default"
        names = [n["name"] for n in data["notes"]]
        # Newest modification first: Zebra (June), Banana (March), Apple (January)
        assert names == ["Zebra", "Banana", "Apple"]

    def test_creation_date_in_response(self, sort_order_base_url):
        status, data = _get_json(sort_order_base_url + f"/api/notes?folder={FOLDER_ID_CREATED}")
        for note in data["notes"]:
            assert "creation_date" in note
            assert "modification_date" in note

    def test_tree_includes_sort_order(self, sort_order_base_url):
        status, data = _get_json(sort_order_base_url + "/api/tree")
        folders = data["accounts"][0]["folders"]
        # Skip "All" folder (index 0), check real folders
        folder_sort_orders = {f["name"]: f["sort_order"] for f in folders if "sort_order" in f}
        assert folder_sort_orders["Journal"] == "date_created"
        assert folder_sort_orders["Reference"] == "title"
