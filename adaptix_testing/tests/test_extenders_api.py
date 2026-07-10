import json
import sqlite3
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from adaptix_testing.api import app, get_conn
from adaptix_testing import db


PARSED_EXT = {
    "name": "test-ext",
    "extender_type": "listener+agent",
    "listener_name": "TestHTTP",
    "agent_name": "test-agent",
    "compatible_listeners": ["TestHTTP"],
    "listener_schema": {
        "port_bind": {"source": "auto", "value": 443, "widget": "spin", "hint": None},
    },
    "agent_schema": {
        "arch": {"source": "auto", "value": "x64", "widget": "combo", "hint": None},
    },
    "container_path": "/app/extenders/test-ext",
    "listener_config_rel_paths": ["listener/config.yaml"],
    "agent_config_rel_paths": ["agent/config.yaml"],
    "bof_axs_rel_paths": [],
}

PARSED_EXT_NEEDS_INPUT = {
    **PARSED_EXT,
    "listener_schema": {
        "uploaded_file": {"source": "required", "value": None, "widget": "file",
                          "hint": "base64-encoded malleable profile JSON"},
    },
}

PARSED_BOF = {
    "name": "ext-kit",
    "extender_type": "bof",
    "listener_name": None, "agent_name": None,
    "compatible_listeners": [],
    "listener_schema": None, "agent_schema": None,
    "container_path": "/app/extenders/ext-kit",
    "listener_config_rel_paths": [],
    "agent_config_rel_paths": [],
    "bof_axs_rel_paths": ["ext-kit.axs"],
}


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


def _post_extender(client, parsed=None, git_url="https://github.com/test/ext", overrides=None):
    parsed = parsed or PARSED_EXT
    with patch("adaptix_testing.api._ep.clone_repo"), \
         patch("adaptix_testing.api._ep.parse_extender_repo", return_value=parsed):
        body = {"git_url": git_url}
        if overrides:
            body["overrides"] = overrides
        return client.post("/v1/extenders", json=body)


# ── POST /v1/extenders ────────────────────────────────────────────────────────

def test_post_extenders_ready(client):
    resp = _post_extender(client)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ready"
    assert data["name"] == "test-ext"
    assert "id" in data


def test_post_extenders_needs_input(client):
    resp = _post_extender(client, parsed=PARSED_EXT_NEEDS_INPUT)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "needs_input"
    assert "required_fields" in data
    assert data["required_fields"]["listener"][0]["key"] == "uploaded_file"


def test_post_extenders_returns_existing_if_already_registered(client):
    resp1 = _post_extender(client)
    resp2 = _post_extender(client)
    assert resp1.json()["id"] == resp2.json()["id"]


def test_post_extenders_with_overrides_fills_required(client):
    resp = _post_extender(
        client,
        parsed=PARSED_EXT_NEEDS_INPUT,
        overrides={"listener": {"uploaded_file": "base64content"}},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_post_extenders_bof(client):
    resp = _post_extender(client, parsed=PARSED_BOF, git_url="https://github.com/test/bof")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


# ── GET /v1/extenders ─────────────────────────────────────────────────────────

def test_get_extenders_empty(client):
    resp = client.get("/v1/extenders")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_extenders_returns_registered(client):
    _post_extender(client)
    rows = client.get("/v1/extenders").json()
    assert len(rows) == 1
    assert rows[0]["name"] == "test-ext"


# ── GET /v1/extenders/{id} ────────────────────────────────────────────────────

def test_get_extender_by_id(client):
    id_ = _post_extender(client).json()["id"]
    resp = client.get(f"/v1/extenders/{id_}")
    assert resp.status_code == 200
    assert resp.json()["listener_name"] == "TestHTTP"


def test_get_extender_not_found(client):
    assert client.get("/v1/extenders/nope").status_code == 404


# ── PATCH /v1/extenders/{id} ─────────────────────────────────────────────────

def test_patch_extender_fills_required(client):
    id_ = _post_extender(client, parsed=PARSED_EXT_NEEDS_INPUT).json()["id"]
    resp = client.patch(f"/v1/extenders/{id_}", json={
        "overrides": {"listener": {"uploaded_file": "base64data"}}
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_patch_extender_not_found(client):
    resp = client.patch("/v1/extenders/nope", json={"overrides": {}})
    assert resp.status_code == 404


# ── POST /v1/extenders/{id}/activate ─────────────────────────────────────────

def test_activate_extender(client):
    id_ = _post_extender(client).json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"):
        resp = client.post(f"/v1/extenders/{id_}/activate")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_activate_sets_active_flags(client):
    id_ = _post_extender(client).json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"):
        client.post(f"/v1/extenders/{id_}/activate")
    row = client.get(f"/v1/extenders/{id_}").json()
    assert row["is_active_listener"] is True
    assert row["is_active_agent"] is True


def test_activate_needs_input_returns_400(client):
    id_ = _post_extender(client, parsed=PARSED_EXT_NEEDS_INPUT).json()["id"]
    resp = client.post(f"/v1/extenders/{id_}/activate")
    assert resp.status_code == 400


def test_activate_agent_incompatible_listener_returns_409(client):
    listener_ext = {**PARSED_EXT, "name": "l", "extender_type": "listener",
                    "agent_name": None, "agent_schema": None,
                    "agent_config_rel_paths": [], "bof_axs_rel_paths": []}
    lid = _post_extender(client, parsed=listener_ext, git_url="https://g.com/l").json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"):
        client.post(f"/v1/extenders/{lid}/activate")

    agent_ext = {**PARSED_EXT, "name": "a", "extender_type": "agent",
                 "listener_name": None, "listener_schema": None,
                 "compatible_listeners": ["OtherListener"],
                 "listener_config_rel_paths": [], "bof_axs_rel_paths": []}
    aid = _post_extender(client, parsed=agent_ext, git_url="https://g.com/a").json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"):
        resp = client.post(f"/v1/extenders/{aid}/activate")
    assert resp.status_code == 409


def test_activate_bof(client):
    id_ = _post_extender(client, parsed=PARSED_BOF, git_url="https://g.com/bof").json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"):
        resp = client.post(f"/v1/extenders/{id_}/activate")
    assert resp.status_code == 200
    row = client.get(f"/v1/extenders/{id_}").json()
    assert row["is_active_bof"] is True


# ── POST /v1/extenders/{id}/deactivate ───────────────────────────────────────

def test_deactivate_extender(client):
    id_ = _post_extender(client).json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"), \
         patch("adaptix_testing.api._pm.remove_extender_entries"):
        client.post(f"/v1/extenders/{id_}/activate")
        resp = client.post(f"/v1/extenders/{id_}/deactivate")
    assert resp.status_code == 200
    row = client.get(f"/v1/extenders/{id_}").json()
    assert row["is_active_listener"] is False


# ── DELETE /v1/extenders/{id} ────────────────────────────────────────────────

def test_delete_extender(client):
    id_ = _post_extender(client).json()["id"]
    resp = client.delete(f"/v1/extenders/{id_}")
    assert resp.status_code == 204
    assert client.get(f"/v1/extenders/{id_}").status_code == 404


def test_delete_active_extender_returns_409(client):
    id_ = _post_extender(client).json()["id"]
    with patch("adaptix_testing.api._pm.add_extender_entries"):
        client.post(f"/v1/extenders/{id_}/activate")
    resp = client.delete(f"/v1/extenders/{id_}")
    assert resp.status_code == 409


def test_delete_extender_not_found(client):
    assert client.delete("/v1/extenders/nope").status_code == 404
