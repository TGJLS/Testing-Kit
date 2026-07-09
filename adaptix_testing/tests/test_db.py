import pytest
import sqlite3
from adaptix_testing import db


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    db.create_tables(c)
    yield c
    c.close()


# ── Schema ────────────────────────────────────────────────────────────────────

def test_create_tables_creates_both(conn):
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert "preamble" in tables
    assert "tasks" in tables


def test_create_tables_idempotent(conn):
    db.create_tables(conn)  # second call must not raise


# ── Preamble CRUD ─────────────────────────────────────────────────────────────

def test_get_preamble_empty(conn):
    assert db.get_preamble(conn) == []


def test_add_and_get_preamble(conn):
    id = db.add_preamble(conn, "whoami")
    rows = db.get_preamble(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == id
    assert rows[0]["command"] == "whoami"


def test_add_preamble_auto_order(conn):
    db.add_preamble(conn, "first")
    db.add_preamble(conn, "second")
    rows = db.get_preamble(conn)
    assert rows[0]["order_index"] < rows[1]["order_index"]


def test_add_preamble_explicit_order(conn):
    db.add_preamble(conn, "b", order_index=2)
    db.add_preamble(conn, "a", order_index=1)
    rows = db.get_preamble(conn)
    assert rows[0]["command"] == "a"
    assert rows[1]["command"] == "b"


def test_get_preamble_by_id(conn):
    id = db.add_preamble(conn, "whoami")
    row = db.get_preamble_by_id(conn, id)
    assert row is not None
    assert row["command"] == "whoami"


def test_get_preamble_by_id_missing(conn):
    assert db.get_preamble_by_id(conn, 999) is None


def test_update_preamble_command(conn):
    id = db.add_preamble(conn, "whoami")
    assert db.update_preamble(conn, id, {"command": "id"}) is True
    assert db.get_preamble_by_id(conn, id)["command"] == "id"


def test_update_preamble_order(conn):
    id = db.add_preamble(conn, "whoami", order_index=5)
    db.update_preamble(conn, id, {"order_index": 1})
    assert db.get_preamble_by_id(conn, id)["order_index"] == 1


def test_update_preamble_partial(conn):
    id = db.add_preamble(conn, "whoami", order_index=5)
    db.update_preamble(conn, id, {"command": "id"})  # order_index unchanged
    row = db.get_preamble_by_id(conn, id)
    assert row["command"] == "id"
    assert row["order_index"] == 5


def test_update_preamble_not_found(conn):
    assert db.update_preamble(conn, 999, {"command": "x"}) is False


def test_delete_preamble(conn):
    id = db.add_preamble(conn, "whoami")
    assert db.delete_preamble(conn, id) is True
    assert db.get_preamble(conn) == []


def test_delete_preamble_not_found(conn):
    assert db.delete_preamble(conn, 999) is False


# ── Preamble batch ────────────────────────────────────────────────────────────

def test_batch_append_preamble_adds_without_wiping(conn):
    db.add_preamble(conn, "existing")
    db.batch_append_preamble(conn, [{"command": "a"}, {"command": "b"}])
    rows = db.get_preamble(conn)
    assert len(rows) == 3
    commands = [r["command"] for r in rows]
    assert "existing" in commands
    assert "a" in commands
    assert "b" in commands


def test_batch_append_preamble_returns_ids(conn):
    ids = db.batch_append_preamble(conn, [{"command": "a"}, {"command": "b"}])
    assert len(ids) == 2
    assert all(isinstance(i, int) for i in ids)


def test_batch_replace_preamble_wipes_old_rows(conn):
    db.add_preamble(conn, "old")
    db.batch_replace_preamble(conn, [{"command": "new1"}, {"command": "new2"}])
    rows = db.get_preamble(conn)
    assert len(rows) == 2
    assert rows[0]["command"] == "new1"
    assert rows[1]["command"] == "new2"


def test_batch_delete_preamble_removes_only_specified(conn):
    id1 = db.add_preamble(conn, "a")
    id2 = db.add_preamble(conn, "b")
    db.add_preamble(conn, "c")
    count = db.batch_delete_preamble(conn, [id1, id2])
    assert count == 2
    rows = db.get_preamble(conn)
    assert len(rows) == 1
    assert rows[0]["command"] == "c"


# ── Tasks CRUD ────────────────────────────────────────────────────────────────

def test_get_tasks_empty(conn):
    assert db.get_tasks(conn) == []


def test_add_and_get_task(conn):
    id = db.add_task(conn, {"cmdline": "whoami", "expected": "root"})
    rows = db.get_tasks(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == id
    assert rows[0]["cmdline"] == "whoami"
    assert rows[0]["expected"] == "root"
    assert rows[0]["allowed_to_fail"] is False


def test_add_task_capture_roundtrip(conn):
    db.add_task(conn, {"cmdline": "ps", "capture": {"pid": r"PID (\d+)"}})
    rows = db.get_tasks(conn)
    assert rows[0]["capture"] == {"pid": r"PID (\d+)"}


def test_add_task_no_capture(conn):
    db.add_task(conn, {"cmdline": "whoami"})
    assert db.get_tasks(conn)[0]["capture"] is None


def test_add_task_allowed_to_fail(conn):
    db.add_task(conn, {"cmdline": "bad", "allowed_to_fail": True})
    assert db.get_tasks(conn)[0]["allowed_to_fail"] is True


def test_task_ordering(conn):
    db.add_task(conn, {"cmdline": "second", "order_index": 2})
    db.add_task(conn, {"cmdline": "first", "order_index": 1})
    rows = db.get_tasks(conn)
    assert rows[0]["cmdline"] == "first"
    assert rows[1]["cmdline"] == "second"


def test_get_task_by_id(conn):
    id = db.add_task(conn, {"cmdline": "whoami"})
    row = db.get_task_by_id(conn, id)
    assert row is not None
    assert row["cmdline"] == "whoami"


def test_get_task_by_id_missing(conn):
    assert db.get_task_by_id(conn, 999) is None


def test_update_task_cmdline(conn):
    id = db.add_task(conn, {"cmdline": "whoami"})
    assert db.update_task(conn, id, {"cmdline": "id"}) is True
    assert db.get_task_by_id(conn, id)["cmdline"] == "id"


def test_update_task_partial_preserves_other_fields(conn):
    id = db.add_task(conn, {"cmdline": "whoami", "expected": "root"})
    db.update_task(conn, id, {"cmdline": "id"})
    row = db.get_task_by_id(conn, id)
    assert row["cmdline"] == "id"
    assert row["expected"] == "root"


def test_update_task_not_found(conn):
    assert db.update_task(conn, 999, {"cmdline": "x"}) is False


def test_delete_task(conn):
    id = db.add_task(conn, {"cmdline": "whoami"})
    assert db.delete_task(conn, id) is True
    assert db.get_tasks(conn) == []


def test_delete_task_not_found(conn):
    assert db.delete_task(conn, 999) is False


# ── Tasks batch ───────────────────────────────────────────────────────────────

def test_batch_append_tasks_adds_without_wiping(conn):
    db.add_task(conn, {"cmdline": "existing"})
    db.batch_append_tasks(conn, [{"cmdline": "a"}, {"cmdline": "b"}])
    assert len(db.get_tasks(conn)) == 3


def test_batch_replace_tasks_wipes_old_rows(conn):
    db.add_task(conn, {"cmdline": "old"})
    db.batch_replace_tasks(conn, [{"cmdline": "new1"}, {"cmdline": "new2"}])
    rows = db.get_tasks(conn)
    assert len(rows) == 2
    assert rows[0]["cmdline"] == "new1"


def test_batch_delete_tasks_removes_only_specified(conn):
    id1 = db.add_task(conn, {"cmdline": "a"})
    id2 = db.add_task(conn, {"cmdline": "b"})
    db.add_task(conn, {"cmdline": "c"})
    count = db.batch_delete_tasks(conn, [id1, id2])
    assert count == 2
    rows = db.get_tasks(conn)
    assert len(rows) == 1
    assert rows[0]["cmdline"] == "c"
