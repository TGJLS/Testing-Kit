import json
import sqlite3
import pytest
from datetime import datetime
from adaptix_testing import db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.create_tables(c)
    yield c
    c.close()


def _ext(id="e1", name="Kharon", git_url="https://github.com/x/y",
         ext_type="listener+agent", status="ready",
         listener_name="KharonHTTP", agent_name="kharon",
         compatible_listeners='["KharonHTTP"]',
         listener_schema=None, agent_schema=None,
         container_path="/app/extenders/kharon",
         listener_config_rel_paths='[]', agent_config_rel_paths='[]',
         bof_axs_rel_paths='[]', created_at=None):
    return {
        "id": id, "name": name, "git_url": git_url,
        "extender_type": ext_type, "status": status,
        "listener_name": listener_name, "agent_name": agent_name,
        "compatible_listeners": compatible_listeners,
        "listener_schema": listener_schema, "agent_schema": agent_schema,
        "container_path": container_path,
        "listener_config_rel_paths": listener_config_rel_paths,
        "agent_config_rel_paths": agent_config_rel_paths,
        "bof_axs_rel_paths": bof_axs_rel_paths,
        "created_at": created_at if created_at is not None else datetime.utcnow().isoformat(),
    }


def test_create_tables_creates_extenders(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "extenders" in tables


def test_add_and_get_extender(conn):
    db.add_extender(conn, _ext())
    row = db.get_extender(conn, "e1")
    assert row is not None
    assert row["name"] == "Kharon"
    assert row["listener_name"] == "KharonHTTP"


def test_get_extender_missing(conn):
    assert db.get_extender(conn, "nope") is None


def test_get_extender_by_git_url(conn):
    db.add_extender(conn, _ext())
    row = db.get_extender_by_git_url(conn, "https://github.com/x/y")
    assert row is not None
    assert row["id"] == "e1"


def test_get_extenders_empty(conn):
    assert db.get_extenders(conn) == []


def test_get_extenders_ordered_by_created_at(conn):
    db.add_extender(conn, _ext("e1", created_at="2026-01-01"))
    db.add_extender(conn, _ext("e2", git_url="https://other", created_at="2026-01-02"))
    rows = db.get_extenders(conn)
    assert [r["id"] for r in rows] == ["e1", "e2"]


def test_update_extender(conn):
    db.add_extender(conn, _ext())
    assert db.update_extender(conn, "e1", {"status": "needs_input"}) is True
    assert db.get_extender(conn, "e1")["status"] == "needs_input"


def test_update_extender_partial_preserves_other_fields(conn):
    db.add_extender(conn, _ext())
    db.update_extender(conn, "e1", {"status": "needs_input"})
    row = db.get_extender(conn, "e1")
    assert row["name"] == "Kharon"
    assert row["listener_name"] == "KharonHTTP"


def test_update_extender_missing(conn):
    assert db.update_extender(conn, "nope", {"status": "ready"}) is False


def test_delete_extender(conn):
    db.add_extender(conn, _ext())
    assert db.delete_extender(conn, "e1") is True
    assert db.get_extender(conn, "e1") is None


def test_delete_extender_missing(conn):
    assert db.delete_extender(conn, "nope") is False


def test_get_active_listener_none(conn):
    db.add_extender(conn, _ext())
    assert db.get_active_listener_extender(conn) is None


def test_set_and_get_active_listener(conn):
    db.add_extender(conn, _ext())
    db.set_active_listener(conn, "e1")
    row = db.get_active_listener_extender(conn)
    assert row is not None
    assert row["id"] == "e1"


def test_set_active_listener_deactivates_previous(conn):
    db.add_extender(conn, _ext("e1", git_url="u1"))
    db.add_extender(conn, _ext("e2", git_url="u2"))
    db.set_active_listener(conn, "e1")
    db.set_active_listener(conn, "e2")
    assert db.get_active_listener_extender(conn)["id"] == "e2"
    assert db.get_extender(conn, "e1")["is_active_listener"] == 0


def test_set_and_get_active_agent(conn):
    db.add_extender(conn, _ext())
    db.set_active_agent(conn, "e1")
    assert db.get_active_agent_extender(conn)["id"] == "e1"


def test_get_active_bof_extenders_empty(conn):
    assert db.get_active_bof_extenders(conn) == []


def test_set_active_bof(conn):
    db.add_extender(conn, _ext("e1", git_url="u1", ext_type="bof"))
    db.add_extender(conn, _ext("e2", git_url="u2", ext_type="bof"))
    db.set_active_bof(conn, "e1", True)
    db.set_active_bof(conn, "e2", True)
    active = db.get_active_bof_extenders(conn)
    assert {r["id"] for r in active} == {"e1", "e2"}


def test_set_active_bof_false(conn):
    db.add_extender(conn, _ext("e1", git_url="u1", ext_type="bof"))
    db.set_active_bof(conn, "e1", True)
    db.set_active_bof(conn, "e1", False)
    assert db.get_active_bof_extenders(conn) == []


def test_deactivate_all_listeners(conn):
    db.add_extender(conn, _ext())
    db.set_active_listener(conn, "e1")
    db.deactivate_all_listeners(conn)
    assert db.get_active_listener_extender(conn) is None


def test_deactivate_all_agents(conn):
    db.add_extender(conn, _ext())
    db.set_active_agent(conn, "e1")
    db.deactivate_all_agents(conn)
    assert db.get_active_agent_extender(conn) is None
