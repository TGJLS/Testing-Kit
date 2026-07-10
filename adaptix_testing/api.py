import json
import logging
import os
import sqlite3
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Generator, Optional
from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel
from adaptix_testing import db as _db
from adaptix_testing import extender_parser as _ep
from adaptix_testing import profile_manager as _pm
from adaptix_testing import runner as _runner

ADAPTIX_PROFILE_PATH     = _pm.PROFILE_PATH
EXTENDERS_HOST_PATH      = _pm.EXTENDERS_HOST_PATH
EXTENDERS_CONTAINER_PATH = _pm.EXTENDERS_CONTAINER_PATH

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


# ── Extender models ───────────────────────────────────────────────────────────

class ExtenderCreate(BaseModel):
    git_url: str
    name: Optional[str] = None
    overrides: Optional[dict] = None


class ExtenderPatch(BaseModel):
    overrides: dict


# ── Extender helpers ──────────────────────────────────────────────────────────

def _collect_required(parsed: dict) -> dict:
    result: dict = {"listener": [], "agent": []}
    for role, key in [("listener", "listener_schema"), ("agent", "agent_schema")]:
        schema = parsed.get(key)
        if not schema:
            continue
        for field_key, field in schema.items():
            if field["source"] == "required" and field.get("value") is None:
                result[role].append({
                    "key": field_key,
                    "widget": field.get("widget", "string"),
                    "hint": field.get("hint"),
                })
    return result


def _apply_overrides(schema: Optional[dict], overrides: dict) -> Optional[dict]:
    if not schema or not overrides:
        return schema
    schema = {k: dict(v) for k, v in schema.items()}
    for key, val in overrides.items():
        if key in schema:
            schema[key]["value"] = val
            if schema[key]["source"] == "required" and val is not None:
                schema[key]["source"] = "auto"
    return schema


def _extender_name_from_url(git_url: str) -> str:
    return git_url.rstrip("/").removesuffix(".git").split("/")[-1].lower()


# ── Extender routes ───────────────────────────────────────────────────────────

@app.post("/v1/extenders")
def create_extender(body: ExtenderCreate, conn: sqlite3.Connection = Depends(get_conn)):
    existing = _db.get_extender_by_git_url(conn, body.git_url)
    if existing:
        return existing

    name = body.name or _extender_name_from_url(body.git_url)
    dest = os.path.join(EXTENDERS_HOST_PATH, name)

    if not os.path.exists(dest):
        try:
            _ep.clone_repo(body.git_url, dest)
        except subprocess.CalledProcessError as e:
            raise HTTPException(400, f"Git clone failed: {getattr(e, 'stderr', str(e))}")

    try:
        parsed = _ep.parse_extender_repo(dest, EXTENDERS_CONTAINER_PATH, name)
    except Exception as e:
        raise HTTPException(500, f"Parse failed: {e}")

    overrides = body.overrides or {}
    ls = _apply_overrides(parsed.get("listener_schema"), overrides.get("listener", {}))
    as_ = _apply_overrides(parsed.get("agent_schema"),  overrides.get("agent", {}))

    required = _collect_required({"listener_schema": ls, "agent_schema": as_})
    status = "ready" if not required["listener"] and not required["agent"] else "needs_input"

    ext_id = uuid.uuid4().hex[:8]
    _db.add_extender(conn, {
        "id": ext_id,
        "name": parsed["name"],
        "git_url": body.git_url,
        "extender_type": parsed["extender_type"],
        "status": status,
        "listener_name": parsed.get("listener_name"),
        "agent_name": parsed.get("agent_name"),
        "compatible_listeners": json.dumps(parsed.get("compatible_listeners", [])),
        "listener_schema": json.dumps(ls) if ls else None,
        "agent_schema": json.dumps(as_) if as_ else None,
        "container_path": parsed["container_path"],
        "listener_config_rel_paths": json.dumps(parsed.get("listener_config_rel_paths", [])),
        "agent_config_rel_paths": json.dumps(parsed.get("agent_config_rel_paths", [])),
        "bof_axs_rel_paths": json.dumps(parsed.get("bof_axs_rel_paths", [])),
        "created_at": datetime.utcnow().isoformat(),
    })

    response = {"id": ext_id, "name": parsed["name"],
                "type": parsed["extender_type"], "status": status}
    if status == "needs_input":
        response["required_fields"] = required
    return response


@app.patch("/v1/extenders/{id}")
def patch_extender(id: str, body: ExtenderPatch, conn: sqlite3.Connection = Depends(get_conn)):
    ext = _db.get_extender(conn, id)
    if not ext:
        raise HTTPException(404)

    ls  = json.loads(ext["listener_schema"]) if ext.get("listener_schema") else None
    as_ = json.loads(ext["agent_schema"])    if ext.get("agent_schema")    else None

    ls  = _apply_overrides(ls,  body.overrides.get("listener", {}))
    as_ = _apply_overrides(as_, body.overrides.get("agent", {}))

    required = _collect_required({"listener_schema": ls, "agent_schema": as_})
    status = "ready" if not required["listener"] and not required["agent"] else "needs_input"

    updates: dict = {"status": status}
    if ls is not None:
        updates["listener_schema"] = json.dumps(ls)
    if as_ is not None:
        updates["agent_schema"] = json.dumps(as_)
    _db.update_extender(conn, id, updates)

    resp = {"id": id, "status": status}
    if status == "needs_input":
        resp["required_fields"] = required
    return resp


@app.get("/v1/extenders")
def list_extenders(conn: sqlite3.Connection = Depends(get_conn)):
    rows = _db.get_extenders(conn)
    for r in rows:
        r["is_active_listener"] = bool(r["is_active_listener"])
        r["is_active_agent"]    = bool(r["is_active_agent"])
        r["is_active_bof"]      = bool(r["is_active_bof"])
    return rows


@app.get("/v1/extenders/{id}")
def get_extender(id: str, conn: sqlite3.Connection = Depends(get_conn)):
    ext = _db.get_extender(conn, id)
    if not ext:
        raise HTTPException(404)
    ext["is_active_listener"] = bool(ext["is_active_listener"])
    ext["is_active_agent"]    = bool(ext["is_active_agent"])
    ext["is_active_bof"]      = bool(ext["is_active_bof"])
    if ext.get("listener_schema"):
        ext["listener_schema"] = json.loads(ext["listener_schema"])
    if ext.get("agent_schema"):
        ext["agent_schema"] = json.loads(ext["agent_schema"])
    if ext.get("compatible_listeners"):
        ext["compatible_listeners"] = json.loads(ext["compatible_listeners"])
    return ext


@app.post("/v1/extenders/{id}/activate")
def activate_extender(id: str, conn: sqlite3.Connection = Depends(get_conn)):
    ext = _db.get_extender(conn, id)
    if not ext:
        raise HTTPException(404)
    if ext["status"] == "needs_input":
        raise HTTPException(400, "Extender has unfilled required fields")

    ext_type       = ext["extender_type"]
    container_path = ext["container_path"]

    if ext_type == "listener":
        active_agent = _db.get_active_agent_extender(conn)
        if active_agent:
            compat = json.loads(active_agent.get("compatible_listeners") or "[]")
            if ext["listener_name"] not in compat:
                raise HTTPException(409, detail=(
                    f"Active agent '{active_agent['agent_name']}' is not compatible with "
                    f"listener '{ext['listener_name']}'. Compatible listeners: {compat}"
                ))
        config_rels = json.loads(ext.get("listener_config_rel_paths") or "[]")
        _pm.add_extender_entries(ADAPTIX_PROFILE_PATH, container_path, config_rels, [])
        _db.set_active_listener(conn, id)

    elif ext_type == "agent":
        active_listener = _db.get_active_listener_extender(conn)
        if active_listener:
            my_compat = json.loads(ext.get("compatible_listeners") or "[]")
            if active_listener["listener_name"] not in my_compat:
                raise HTTPException(409, detail=(
                    f"Agent '{ext['agent_name']}' is not compatible with "
                    f"listener '{active_listener['listener_name']}'. "
                    f"Compatible: {my_compat}"
                ))
        config_rels = json.loads(ext.get("agent_config_rel_paths") or "[]")
        _pm.add_extender_entries(ADAPTIX_PROFILE_PATH, container_path, config_rels, [])
        _db.set_active_agent(conn, id)

    elif ext_type == "listener+agent":
        l_rels = json.loads(ext.get("listener_config_rel_paths") or "[]")
        a_rels = json.loads(ext.get("agent_config_rel_paths") or "[]")
        _pm.add_extender_entries(ADAPTIX_PROFILE_PATH, container_path, l_rels + a_rels, [])
        _db.set_active_listener(conn, id)
        _db.set_active_agent(conn, id)

    elif ext_type == "bof":
        axs_rels = json.loads(ext.get("bof_axs_rel_paths") or "[]")
        _pm.add_extender_entries(ADAPTIX_PROFILE_PATH, container_path, [], axs_rels)
        _db.set_active_bof(conn, id, True)

    return {"ok": True}


@app.post("/v1/extenders/{id}/deactivate")
def deactivate_extender(id: str, conn: sqlite3.Connection = Depends(get_conn)):
    ext = _db.get_extender(conn, id)
    if not ext:
        raise HTTPException(404)

    _pm.remove_extender_entries(ADAPTIX_PROFILE_PATH, ext["container_path"])

    if ext["extender_type"] in ("listener", "listener+agent"):
        _db.deactivate_all_listeners(conn)
    if ext["extender_type"] in ("agent", "listener+agent"):
        _db.deactivate_all_agents(conn)
    if ext["extender_type"] == "bof":
        _db.set_active_bof(conn, id, False)

    return {"ok": True}


@app.delete("/v1/extenders/{id}", status_code=204)
def delete_extender(id: str, conn: sqlite3.Connection = Depends(get_conn)):
    ext = _db.get_extender(conn, id)
    if not ext:
        raise HTTPException(404)
    if ext["is_active_listener"] or ext["is_active_agent"] or ext["is_active_bof"]:
        raise HTTPException(409, "Cannot delete an active extender; deactivate first")
    _db.delete_extender(conn, id)
    return Response(status_code=204)
