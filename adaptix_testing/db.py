import json
import sqlite3
from typing import Optional


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS preamble (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            order_index INTEGER NOT NULL,
            command     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            order_index         INTEGER NOT NULL,
            cmdline             TEXT NOT NULL,
            expected            TEXT,
            expected_regex      TEXT,
            not_expected        TEXT,
            not_expected_regex  TEXT,
            allowed_to_fail     INTEGER NOT NULL DEFAULT 0,
            capture             TEXT
        );
    """)
    conn.commit()


def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    create_tables(conn)
    return conn


# ── Internal helpers ──────────────────────────────────────────────────────────

def _next_preamble_order(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(order_index) FROM preamble").fetchone()
    return (row[0] or 0) + 1


def _next_task_order(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(order_index) FROM tasks").fetchone()
    return (row[0] or 0) + 1


def _insert_preamble(conn: sqlite3.Connection, command: str, order_index: int) -> int:
    cur = conn.execute(
        "INSERT INTO preamble (command, order_index) VALUES (?, ?)",
        (command, order_index),
    )
    return cur.lastrowid


def _insert_task(conn: sqlite3.Connection, task: dict, order_index: int) -> int:
    capture = json.dumps(task["capture"]) if task.get("capture") else None
    cur = conn.execute(
        """INSERT INTO tasks
               (order_index, cmdline, expected, expected_regex,
                not_expected, not_expected_regex, allowed_to_fail, capture)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            order_index,
            task["cmdline"],
            task.get("expected"),
            task.get("expected_regex"),
            task.get("not_expected"),
            task.get("not_expected_regex"),
            int(task.get("allowed_to_fail", False)),
            capture,
        ),
    )
    return cur.lastrowid


def _row_to_task(row) -> dict:
    d = dict(row)
    d["capture"] = json.loads(d["capture"]) if d.get("capture") else None
    d["allowed_to_fail"] = bool(d["allowed_to_fail"])
    return d


# ── Preamble ──────────────────────────────────────────────────────────────────

def get_preamble(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM preamble ORDER BY order_index"
    )]


def get_preamble_by_id(conn: sqlite3.Connection, id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM preamble WHERE id=?", (id,)).fetchone()
    return dict(row) if row else None


def add_preamble(
    conn: sqlite3.Connection, command: str, order_index: Optional[int] = None
) -> int:
    if order_index is None:
        order_index = _next_preamble_order(conn)
    id = _insert_preamble(conn, command, order_index)
    conn.commit()
    return id


def update_preamble(conn: sqlite3.Connection, id: int, updates: dict) -> bool:
    row = conn.execute("SELECT * FROM preamble WHERE id=?", (id,)).fetchone()
    if row is None:
        return False
    existing = dict(row)
    conn.execute(
        "UPDATE preamble SET command=?, order_index=? WHERE id=?",
        (
            updates.get("command", existing["command"]),
            updates.get("order_index", existing["order_index"]),
            id,
        ),
    )
    conn.commit()
    return True


def delete_preamble(conn: sqlite3.Connection, id: int) -> bool:
    cur = conn.execute("DELETE FROM preamble WHERE id=?", (id,))
    conn.commit()
    return cur.rowcount > 0


def batch_append_preamble(conn: sqlite3.Connection, commands: list[dict]) -> list[int]:
    base = _next_preamble_order(conn)
    ids = [
        _insert_preamble(conn, item["command"], item["order_index"] if item.get("order_index") is not None else base + i)
        for i, item in enumerate(commands)
    ]
    conn.commit()
    return ids


def batch_replace_preamble(conn: sqlite3.Connection, commands: list[dict]) -> list[int]:
    conn.execute("DELETE FROM preamble")
    ids = [
        _insert_preamble(conn, item["command"], item["order_index"] if item.get("order_index") is not None else i)
        for i, item in enumerate(commands)
    ]
    conn.commit()
    return ids


def batch_delete_preamble(conn: sqlite3.Connection, ids: list[int]) -> int:
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(f"DELETE FROM preamble WHERE id IN ({placeholders})", ids)
    conn.commit()
    return cur.rowcount


# ── Tasks ─────────────────────────────────────────────────────────────────────

def get_tasks(conn: sqlite3.Connection) -> list[dict]:
    return [_row_to_task(r) for r in conn.execute(
        "SELECT * FROM tasks ORDER BY order_index"
    )]


def get_task_by_id(conn: sqlite3.Connection, id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
    return _row_to_task(row) if row else None


def add_task(conn: sqlite3.Connection, task: dict) -> int:
    order_index = task.get("order_index")
    if order_index is None:
        order_index = _next_task_order(conn)
    id = _insert_task(conn, task, order_index)
    conn.commit()
    return id


def update_task(conn: sqlite3.Connection, id: int, updates: dict) -> bool:
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (id,)).fetchone()
    if row is None:
        return False
    existing = dict(row)
    capture_in_updates = "capture" in updates
    if capture_in_updates:
        capture = json.dumps(updates["capture"]) if updates["capture"] else None
    else:
        capture = existing["capture"]
    conn.execute(
        """UPDATE tasks
           SET order_index=?, cmdline=?, expected=?, expected_regex=?,
               not_expected=?, not_expected_regex=?, allowed_to_fail=?, capture=?
           WHERE id=?""",
        (
            updates.get("order_index", existing["order_index"]),
            updates.get("cmdline", existing["cmdline"]),
            updates.get("expected", existing["expected"]),
            updates.get("expected_regex", existing["expected_regex"]),
            updates.get("not_expected", existing["not_expected"]),
            updates.get("not_expected_regex", existing["not_expected_regex"]),
            int(updates.get("allowed_to_fail", bool(existing["allowed_to_fail"]))),
            capture,
            id,
        ),
    )
    conn.commit()
    return True


def delete_task(conn: sqlite3.Connection, id: int) -> bool:
    cur = conn.execute("DELETE FROM tasks WHERE id=?", (id,))
    conn.commit()
    return cur.rowcount > 0


def batch_append_tasks(conn: sqlite3.Connection, tasks: list[dict]) -> list[int]:
    base = _next_task_order(conn)
    ids = [
        _insert_task(conn, task, task["order_index"] if task.get("order_index") is not None else base + i)
        for i, task in enumerate(tasks)
    ]
    conn.commit()
    return ids


def batch_replace_tasks(conn: sqlite3.Connection, tasks: list[dict]) -> list[int]:
    conn.execute("DELETE FROM tasks")
    ids = [
        _insert_task(conn, task, task["order_index"] if task.get("order_index") is not None else i)
        for i, task in enumerate(tasks)
    ]
    conn.commit()
    return ids


def batch_delete_tasks(conn: sqlite3.Connection, ids: list[int]) -> int:
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(f"DELETE FROM tasks WHERE id IN ({placeholders})", ids)
    conn.commit()
    return cur.rowcount


def seed_tasks_from_yaml(conn: sqlite3.Connection, path: str) -> int:
    """Insert tasks from a YAML seed file if the tasks table is currently empty."""
    if get_tasks(conn):
        return 0
    import yaml
    with open(path) as f:
        data = yaml.safe_load(f)
    tasks = data.get("tasks", [])
    if not tasks:
        return 0
    batch_append_tasks(conn, tasks)
    return len(tasks)
