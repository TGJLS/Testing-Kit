import pytest
import sqlite3
from fastapi.testclient import TestClient
from adaptix_testing.api import app, get_conn
from adaptix_testing import db


@pytest.fixture
def client():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    db.create_tables(conn)

    def override():
        yield conn

    app.dependency_overrides[get_conn] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    conn.close()


# ── Preamble endpoints ────────────────────────────────────────────────────────

def test_list_preamble_empty(client):
    resp = client.get("/v1/preamble")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_preamble(client):
    resp = client.post("/v1/preamble", json={"command": "whoami"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["command"] == "whoami"
    assert "id" in data
    assert "order_index" in data


def test_create_preamble_missing_command(client):
    resp = client.post("/v1/preamble", json={})
    assert resp.status_code == 422


def test_list_preamble_returns_created(client):
    client.post("/v1/preamble", json={"command": "whoami"})
    resp = client.get("/v1/preamble")
    assert len(resp.json()) == 1


def test_update_preamble(client):
    id = client.post("/v1/preamble", json={"command": "whoami"}).json()["id"]
    resp = client.put(f"/v1/preamble/{id}", json={"command": "id"})
    assert resp.status_code == 200
    assert resp.json()["command"] == "id"


def test_update_preamble_not_found(client):
    resp = client.put("/v1/preamble/999", json={"command": "x"})
    assert resp.status_code == 404


def test_delete_preamble(client):
    id = client.post("/v1/preamble", json={"command": "whoami"}).json()["id"]
    resp = client.delete(f"/v1/preamble/{id}")
    assert resp.status_code == 204
    assert client.get("/v1/preamble").json() == []


def test_delete_preamble_not_found(client):
    resp = client.delete("/v1/preamble/999")
    assert resp.status_code == 404


def test_batch_post_preamble_appends(client):
    client.post("/v1/preamble", json={"command": "existing"})
    resp = client.post("/v1/preamble/batch", json=[{"command": "a"}, {"command": "b"}])
    assert resp.status_code == 201
    assert len(client.get("/v1/preamble").json()) == 3


def test_batch_post_preamble_returns_created_items(client):
    resp = client.post("/v1/preamble/batch", json=[{"command": "a"}, {"command": "b"}])
    data = resp.json()
    assert len(data) == 2
    assert data[0]["command"] == "a"
    assert data[1]["command"] == "b"


def test_batch_put_preamble_replaces(client):
    client.post("/v1/preamble", json={"command": "old"})
    resp = client.put("/v1/preamble/batch", json=[{"command": "new1"}, {"command": "new2"}])
    assert resp.status_code == 200
    rows = client.get("/v1/preamble").json()
    assert len(rows) == 2
    assert rows[0]["command"] == "new1"


def test_batch_delete_preamble(client):
    id1 = client.post("/v1/preamble", json={"command": "a"}).json()["id"]
    id2 = client.post("/v1/preamble", json={"command": "b"}).json()["id"]
    client.post("/v1/preamble", json={"command": "c"})
    resp = client.request("DELETE", "/v1/preamble/batch", json={"ids": [id1, id2]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    rows = client.get("/v1/preamble").json()
    assert len(rows) == 1
    assert rows[0]["command"] == "c"


# ── Tasks endpoints ───────────────────────────────────────────────────────────

def test_list_tasks_empty(client):
    resp = client.get("/v1/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_create_task(client):
    resp = client.post("/v1/tasks", json={"cmdline": "whoami", "expected": "root"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["cmdline"] == "whoami"
    assert data["expected"] == "root"
    assert data["allowed_to_fail"] is False
    assert "id" in data


def test_create_task_missing_cmdline(client):
    resp = client.post("/v1/tasks", json={})
    assert resp.status_code == 422


def test_create_task_with_capture(client):
    resp = client.post("/v1/tasks", json={"cmdline": "ps", "capture": {"pid": r"PID (\d+)"}})
    assert resp.status_code == 201
    assert resp.json()["capture"] == {"pid": r"PID (\d+)"}


def test_update_task(client):
    id = client.post("/v1/tasks", json={"cmdline": "whoami"}).json()["id"]
    resp = client.put(f"/v1/tasks/{id}", json={"cmdline": "id", "expected": "root"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cmdline"] == "id"
    assert data["expected"] == "root"


def test_update_task_partial_preserves_fields(client):
    id = client.post("/v1/tasks", json={"cmdline": "whoami", "expected": "root"}).json()["id"]
    client.put(f"/v1/tasks/{id}", json={"cmdline": "id"})
    resp = client.get("/v1/tasks")
    row = resp.json()[0]
    assert row["cmdline"] == "id"
    assert row["expected"] == "root"


def test_update_task_not_found(client):
    resp = client.put("/v1/tasks/999", json={"cmdline": "x"})
    assert resp.status_code == 404


def test_delete_task(client):
    id = client.post("/v1/tasks", json={"cmdline": "whoami"}).json()["id"]
    resp = client.delete(f"/v1/tasks/{id}")
    assert resp.status_code == 204
    assert client.get("/v1/tasks").json() == []


def test_delete_task_not_found(client):
    resp = client.delete("/v1/tasks/999")
    assert resp.status_code == 404


def test_batch_post_tasks_appends(client):
    client.post("/v1/tasks", json={"cmdline": "existing"})
    resp = client.post("/v1/tasks/batch", json=[{"cmdline": "a"}, {"cmdline": "b"}])
    assert resp.status_code == 201
    assert len(client.get("/v1/tasks").json()) == 3


def test_batch_put_tasks_replaces(client):
    client.post("/v1/tasks", json={"cmdline": "old"})
    resp = client.put("/v1/tasks/batch", json=[{"cmdline": "new1"}, {"cmdline": "new2"}])
    assert resp.status_code == 200
    rows = client.get("/v1/tasks").json()
    assert len(rows) == 2
    assert rows[0]["cmdline"] == "new1"


def test_batch_delete_tasks(client):
    id1 = client.post("/v1/tasks", json={"cmdline": "a"}).json()["id"]
    id2 = client.post("/v1/tasks", json={"cmdline": "b"}).json()["id"]
    client.post("/v1/tasks", json={"cmdline": "c"})
    resp = client.request("DELETE", "/v1/tasks/batch", json={"ids": [id1, id2]})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    rows = client.get("/v1/tasks").json()
    assert len(rows) == 1
    assert rows[0]["cmdline"] == "c"
