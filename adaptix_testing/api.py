import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Generator, Optional
from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel
from adaptix_testing import db as _db
from adaptix_testing import runner as _runner

DB_PATH = os.environ.get("TESTING_KIT_DB", "testing_kit.db")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.yaml")
TASKS_SEED_PATH = os.environ.get("TASKS_SEED_PATH", "")

_log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if TASKS_SEED_PATH:
        try:
            conn = _db.open_db(DB_PATH)
            n = _db.seed_tasks_from_yaml(conn, TASKS_SEED_PATH)
            conn.close()
            if n:
                _log.info("Seeded %d tasks from %s", n, TASKS_SEED_PATH)
        except Exception as exc:
            _log.warning("Task seeding failed: %s", exc)
    yield


app = FastAPI(title="Testing Kit API", version="1.0.0", lifespan=lifespan)


def get_conn() -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _db.create_tables(conn)
    try:
        yield conn
    finally:
        conn.close()


# ── Health + run-tests ────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/run-tests")
def run_tests(conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return _runner.run_tests(CONFIG_PATH, conn)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Preamble models ───────────────────────────────────────────────────────────

class PreambleCreate(BaseModel):
    command: str
    order_index: Optional[int] = None


class PreambleUpdate(BaseModel):
    command: Optional[str] = None
    order_index: Optional[int] = None


class PreambleItem(BaseModel):
    id: int
    order_index: int
    command: str


# ── Task models ───────────────────────────────────────────────────────────────

class TaskCreate(BaseModel):
    cmdline: str
    order_index: Optional[int] = None
    expected: Optional[str] = None
    expected_regex: Optional[str] = None
    not_expected: Optional[str] = None
    not_expected_regex: Optional[str] = None
    allowed_to_fail: bool = False
    capture: Optional[dict] = None


class TaskUpdate(BaseModel):
    cmdline: Optional[str] = None
    order_index: Optional[int] = None
    expected: Optional[str] = None
    expected_regex: Optional[str] = None
    not_expected: Optional[str] = None
    not_expected_regex: Optional[str] = None
    allowed_to_fail: Optional[bool] = None
    capture: Optional[dict] = None


class TaskItem(BaseModel):
    id: int
    order_index: int
    cmdline: str
    expected: Optional[str] = None
    expected_regex: Optional[str] = None
    not_expected: Optional[str] = None
    not_expected_regex: Optional[str] = None
    allowed_to_fail: bool
    capture: Optional[dict] = None


class BatchDeleteBody(BaseModel):
    ids: list[int]


# ── Preamble routes ───────────────────────────────────────────────────────────
# /batch routes defined before /{id} so FastAPI does not treat the string
# "batch" as an integer path parameter.

@app.get("/v1/preamble", response_model=list[PreambleItem])
def list_preamble(conn: sqlite3.Connection = Depends(get_conn)):
    return _db.get_preamble(conn)


@app.post("/v1/preamble", response_model=PreambleItem, status_code=201)
def create_preamble(body: PreambleCreate, conn: sqlite3.Connection = Depends(get_conn)):
    id = _db.add_preamble(conn, body.command, body.order_index)
    return _db.get_preamble_by_id(conn, id)


@app.post("/v1/preamble/batch", response_model=list[PreambleItem], status_code=201)
def batch_append_preamble(
    body: list[PreambleCreate], conn: sqlite3.Connection = Depends(get_conn)
):
    ids = _db.batch_append_preamble(conn, [b.model_dump() for b in body])
    return [_db.get_preamble_by_id(conn, i) for i in ids]


@app.put("/v1/preamble/batch", response_model=list[PreambleItem])
def batch_replace_preamble(
    body: list[PreambleCreate], conn: sqlite3.Connection = Depends(get_conn)
):
    ids = _db.batch_replace_preamble(conn, [b.model_dump() for b in body])
    return [_db.get_preamble_by_id(conn, i) for i in ids]


@app.delete("/v1/preamble/batch")
def batch_delete_preamble(
    body: BatchDeleteBody, conn: sqlite3.Connection = Depends(get_conn)
):
    count = _db.batch_delete_preamble(conn, body.ids)
    return {"deleted": count}


@app.put("/v1/preamble/{id}", response_model=PreambleItem)
def update_preamble(
    id: int, body: PreambleUpdate, conn: sqlite3.Connection = Depends(get_conn)
):
    if not _db.update_preamble(conn, id, body.model_dump(exclude_unset=True)):
        raise HTTPException(status_code=404)
    return _db.get_preamble_by_id(conn, id)


@app.delete("/v1/preamble/{id}", status_code=204)
def delete_preamble(id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if not _db.delete_preamble(conn, id):
        raise HTTPException(status_code=404)
    return Response(status_code=204)


# ── Tasks routes ──────────────────────────────────────────────────────────────
# /batch routes defined before /{id}.

@app.get("/v1/tasks", response_model=list[TaskItem])
def list_tasks(conn: sqlite3.Connection = Depends(get_conn)):
    return _db.get_tasks(conn)


@app.post("/v1/tasks", response_model=TaskItem, status_code=201)
def create_task(body: TaskCreate, conn: sqlite3.Connection = Depends(get_conn)):
    id = _db.add_task(conn, body.model_dump())
    return _db.get_task_by_id(conn, id)


@app.post("/v1/tasks/batch", response_model=list[TaskItem], status_code=201)
def batch_append_tasks(
    body: list[TaskCreate], conn: sqlite3.Connection = Depends(get_conn)
):
    ids = _db.batch_append_tasks(conn, [b.model_dump() for b in body])
    return [_db.get_task_by_id(conn, i) for i in ids]


@app.put("/v1/tasks/batch", response_model=list[TaskItem])
def batch_replace_tasks(
    body: list[TaskCreate], conn: sqlite3.Connection = Depends(get_conn)
):
    ids = _db.batch_replace_tasks(conn, [b.model_dump() for b in body])
    return [_db.get_task_by_id(conn, i) for i in ids]


@app.delete("/v1/tasks/batch")
def batch_delete_tasks(
    body: BatchDeleteBody, conn: sqlite3.Connection = Depends(get_conn)
):
    count = _db.batch_delete_tasks(conn, body.ids)
    return {"deleted": count}


@app.put("/v1/tasks/{id}", response_model=TaskItem)
def update_task(
    id: int, body: TaskUpdate, conn: sqlite3.Connection = Depends(get_conn)
):
    if not _db.update_task(conn, id, body.model_dump(exclude_unset=True)):
        raise HTTPException(status_code=404)
    return _db.get_task_by_id(conn, id)


@app.delete("/v1/tasks/{id}", status_code=204)
def delete_task(id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if not _db.delete_task(conn, id):
        raise HTTPException(status_code=404)
    return Response(status_code=204)
