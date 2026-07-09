#!/usr/bin/env python3
import base64
import json
import os
import re
import sqlite3
import time
import urllib3
import yaml
import requests
import paramiko

from fastapi import FastAPI, HTTPException
from rich.console import Console
from rich.markup import escape

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

CONFIG_PATH = "/app/config/config.yaml"
TASKS_PATH  = "/app/config/tasks.yaml"
POLL_INTERVAL = 2
POLL_TIMEOUT  = 60

app = FastAPI()
console = Console(highlight=False)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/run-tests")
def run_tests():
    try:
        return _run()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _run() -> dict:
    cfg   = load_yaml(CONFIG_PATH)
    tasks = load_yaml(TASKS_PATH)["tasks"]

    base_url = build_base_url(cfg)
    operator = cfg["operator"]

    console.print(f"[dim]Logging in to[/dim] [cyan]{escape(base_url)}[/cyan] [dim]...[/dim]")
    try:
        token = login(base_url, operator)
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"Connection refused — is the server running at {base_url}?")
    except requests.exceptions.HTTPError as e:
        raise RuntimeError(f"Login failed: {e}")
    headers = {"Authorization": f"Bearer {token}"}
    console.print("[green]✓[/green]  Logged in")

    setup_cfg = cfg.get("setup")
    if setup_cfg:
        project = setup_cfg.get("project", "")
        listener_profile = _resolve_listener_profile(setup_cfg, project)
        _create_listener_from_profile(base_url, headers, listener_profile)
        output_path_agent = setup_cfg.get("agent_output", "/tmp/ci_agent.exe")
        agent_profile = _resolve_agent_profile(setup_cfg, project, listener_profile["name"])
        _generate_agent_from_profile(base_url, headers, agent_profile, output_path_agent)

    ssh_client = None
    ssh_cfg    = cfg.get("ssh")
    if ssh_cfg:
        ssh_client, agent_id = ssh_deliver(base_url, headers, ssh_cfg)
    else:
        cfg_agent_id = cfg.get("agent", {}).get("id") or None
        agent_id = resolve_agent(base_url, headers, cfg_agent_id)

    console.clear()
    results    = []
    n          = len(tasks)
    variables  = {}

    try:
        for i, task in enumerate(tasks, 1):
            cmdline = task["cmdline"]
            for key, val in variables.items():
                cmdline = cmdline.replace("{{" + key + "}}", val)
            allowed_fail = task.get("allowed_to_fail", False)

            console.print(f"[bold]\\[{i}/{n}][/bold] [white]{escape(cmdline)}[/white]")

            try:
                known_ids = {t.get("a_task_id") for t in get_task_list(base_url, headers, agent_id)}
                ok, err_msg = dispatch(base_url, headers, agent_id, cmdline)
            except requests.exceptions.RequestException as e:
                results.append({"task": task, "status": "dispatch-failed", "result": None, "err_msg": str(e)})
                continue

            if not ok:
                status = "xfail" if allowed_fail else "dispatch-failed"
                results.append({"task": task, "status": status, "result": None, "err_msg": err_msg})
                continue

            try:
                result = poll_for_result(base_url, headers, agent_id, cmdline, known_ids)
            except requests.exceptions.RequestException as e:
                results.append({"task": task, "status": "timed-out", "result": None, "err_msg": str(e)})
                continue

            if result is None:
                status = "xfail" if allowed_fail else "timed-out"
                results.append({"task": task, "status": status, "result": None})
                continue

            capture_spec = task.get("capture")
            if capture_spec:
                actual = result.get("a_text", "") + result.get("a_message", "")
                for var_name, pattern in capture_spec.items():
                    m = re.search(pattern, actual)
                    if m:
                        try:
                            variables[var_name] = m.group(1)
                        except IndexError:
                            pass

            if result.get("a_msg_type") == 6:
                passed = False
            else:
                _assertion_keys = ("expected", "expected_regex", "not_expected", "not_expected_regex")
                has_assertion = any(task.get(k) for k in _assertion_keys)
                passed = check_output(result, task) if has_assertion else True

            if passed:
                status = "passed"
            elif allowed_fail:
                status = "xfail"
            else:
                status = "failed"

            label = {
                "passed": "[green]✓ PASS[/green]",
                "xfail":  "[yellow]⚠ XFAIL[/yellow]",
                "failed": "[red]✗ FAIL[/red]",
            }[status]
            console.print(f"  {label}\n")
            results.append({"task": task, "status": status, "result": result})

    finally:
        if ssh_client:
            ssh_cfg_local = cfg.get("ssh", {})
            if ssh_cfg_local.get("terminate", False):
                ssh_terminate_agent(ssh_client, ssh_cfg_local["agent_path"])
                remove_agents_by_name(base_url, headers, _exe_name(ssh_cfg_local["agent_path"]))
            ssh_client.close()

    return _build_result(results)


def _build_result(results: list) -> dict:
    return {
        "passed":          sum(1 for r in results if r["status"] == "passed"),
        "failed":          sum(1 for r in results if r["status"] == "failed"),
        "timed_out":       sum(1 for r in results if r["status"] == "timed-out"),
        "dispatch_failed": sum(1 for r in results if r["status"] == "dispatch-failed"),
        "xfail":           sum(1 for r in results if r["status"] == "xfail"),
        "results": [
            {
                "cmdline": r["task"]["cmdline"],
                "status":  r["status"],
                "output":  (r.get("result") or {}).get("a_text", "")
                         + (r.get("result") or {}).get("a_message", ""),
                "err_msg": r.get("err_msg", ""),
            }
            for r in results
        ],
    }


# ── Adaptix profile DB ────────────────────────────────────────────────────────

_ADAPTIX_DB = os.path.expanduser("~/.adaptix/storage-v1.db")


def _load_profile(table, project, name=None):
    if not os.path.exists(_ADAPTIX_DB):
        raise RuntimeError(f"Adaptix database not found: {_ADAPTIX_DB}")
    con = sqlite3.connect(_ADAPTIX_DB)
    try:
        if name:
            row = con.execute(
                f"SELECT data FROM {table} WHERE project=? AND name=?", (project, name)
            ).fetchone()
        else:
            row = con.execute(
                f"SELECT data FROM {table} WHERE project=? LIMIT 1", (project,)
            ).fetchone()
    finally:
        con.close()
    if row is None:
        target = f"'{name}'" if name else "any profile"
        raise RuntimeError(f"{table}: {target} not found in project '{project}'")
    return json.loads(row[0])


def _auto_agent_profile(project, listener_name):
    if not os.path.exists(_ADAPTIX_DB):
        raise RuntimeError(f"Adaptix database not found: {_ADAPTIX_DB}")
    con = sqlite3.connect(_ADAPTIX_DB)
    try:
        rows = con.execute(
            "SELECT data FROM AgentProfiles WHERE project=?", (project,)
        ).fetchall()
    finally:
        con.close()
    if not rows:
        raise RuntimeError(f"AgentProfiles: no profiles found in project '{project}'")
    profiles = [json.loads(r[0]) for r in rows]
    match = next((p for p in profiles if p.get("listener") == listener_name), None)
    return match if match else profiles[0]


def _inline_config(val):
    if isinstance(val, dict):
        return json.dumps(val)
    return val or "{}"


def _resolve_listener_profile(setup_cfg, project):
    inline = setup_cfg.get("listener")
    if inline:
        return {
            "name":   inline["name"],
            "type":   inline["type"],
            "config": _inline_config(inline.get("config")),
        }
    return _load_profile("ListenerProfiles", project, setup_cfg.get("listener_profile"))


def _resolve_agent_profile(setup_cfg, project, listener_name):
    inline = setup_cfg.get("agent")
    if inline:
        return {
            "agent":         inline["agent"],
            "listener":      inline["listener"],
            "listener_type": inline.get("listener_type", ""),
            "config":        _inline_config(inline.get("config")),
        }
    profile_name = setup_cfg.get("agent_profile")
    if profile_name:
        return _load_profile("AgentProfiles", project, profile_name)
    return _auto_agent_profile(project, listener_name)


def _create_listener_from_profile(base_url, headers, profile):
    name = profile["name"]
    resp = requests.post(
        f"{base_url}/listener/create",
        json={"name": name, "type": profile["type"], "config": profile["config"]},
        headers=headers, verify=False, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        msg = data.get("message", "")
        if "already exists" not in msg.lower():
            raise RuntimeError(f"Failed to create listener: {msg}")


def _generate_agent_from_profile(base_url, headers, profile, output_path):
    output = os.path.expanduser(output_path)
    resp = requests.post(
        f"{base_url}/agent/generate",
        json={
            "agent":         profile["agent"],
            "listener_name": [profile["listener"]],
            "config":        profile["config"],
        },
        headers=headers, verify=False, timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Failed to generate agent: {data.get('message', '')}")
    msg = data.get("message", "")
    if not msg:
        raise RuntimeError("Agent generation succeeded but response contained no payload")
    name_b64, content_b64 = msg.split(":", 1)
    payload = base64.b64decode(content_b64)
    with open(output, "wb") as f:
        f.write(payload)


# ── Core helpers ──────────────────────────────────────────────────────────────

def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def build_base_url(cfg):
    url      = cfg["server"]["url"].rstrip("/")
    endpoint = cfg["server"].get("endpoint", "").strip("/")
    return f"{url}/{endpoint}" if endpoint else url


def login(base_url, operator):
    resp = requests.post(
        f"{base_url}/login",
        json={"username": operator["name"], "password": operator["password"], "version": "1.0"},
        verify=False, timeout=15,
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError("Login failed: no access_token in response")
    return token


def dispatch(base_url, headers, agent_id, cmdline):
    resp = requests.post(
        f"{base_url}/agent/command/raw",
        json={"id": agent_id, "cmdline": cmdline},
        headers=headers, verify=False, timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("ok", False), data.get("message", "")


def get_agent_list(base_url, headers):
    resp = requests.get(
        f"{base_url}/agent/list", headers=headers, verify=False, timeout=15,
    )
    resp.raise_for_status()
    return resp.json() or []


def resolve_agent(base_url, headers, cfg_agent_id):
    agents = get_agent_list(base_url, headers)
    if not agents:
        raise RuntimeError("No agents available on the server.")
    if cfg_agent_id:
        agent = next((a for a in agents if a.get("a_id") == cfg_agent_id), None)
        if agent is None:
            raise RuntimeError(f"Agent '{cfg_agent_id}' not found.")
    else:
        agent = agents[0]
    return agent.get("a_id")


def get_task_list(base_url, headers, agent_id):
    resp = requests.get(
        f"{base_url}/agent/task/list",
        params={"agent_id": agent_id, "limit": 1000},
        headers=headers, verify=False, timeout=15,
    )
    resp.raise_for_status()
    return resp.json() or []


def poll_for_result(base_url, headers, agent_id, cmdline, known_ids):
    deadline  = time.time() + POLL_TIMEOUT
    seen_ids  = set(known_ids)
    chunks    = []
    while time.time() < deadline:
        new_this_poll = False
        for task in get_task_list(base_url, headers, agent_id) or []:
            tid = task.get("a_task_id")
            if tid not in seen_ids and task.get("a_cmdline") == cmdline and task.get("a_completed"):
                chunks.append(task)
                seen_ids.add(tid)
                new_this_poll = True
        if chunks and not new_this_poll:
            break
        time.sleep(POLL_INTERVAL)
    if not chunks:
        return None
    if len(chunks) == 1:
        return chunks[0]
    merged = dict(chunks[0])
    merged["a_text"]    = "".join(c.get("a_text", "")    for c in chunks)
    merged["a_message"] = "".join(c.get("a_message", "") for c in chunks)
    return merged


def check_output(task_result, task):
    actual = task_result.get("a_text", "") + task_result.get("a_message", "")
    if (v := task.get("expected"))          and v.lower() not in actual.lower(): return False
    if (v := task.get("expected_regex"))    and not re.search(v, actual):        return False
    if (v := task.get("not_expected"))      and v.lower() in actual.lower():     return False
    if (v := task.get("not_expected_regex")) and re.search(v, actual):           return False
    return True


# ── SSH delivery ──────────────────────────────────────────────────────────────

def ssh_connect(ssh_cfg):
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {
        "hostname":      ssh_cfg["host"],
        "username":      ssh_cfg["username"],
        "look_for_keys": False,
        "allow_agent":   False,
        "timeout":       30,
    }
    if "port"     in ssh_cfg: kwargs["port"]         = ssh_cfg["port"]
    if "key_path" in ssh_cfg: kwargs["key_filename"] = os.path.expanduser(ssh_cfg["key_path"])
    client.connect(**kwargs)
    return client


def _exe_name(agent_path):
    return agent_path.replace("\\", "/").split("/")[-1]


def _ps_run(client, cmd):
    encoded = base64.b64encode(cmd.encode("utf-16-le")).decode()
    _, out_ch, err_ch = client.exec_command(
        f"powershell -NonInteractive -EncodedCommand {encoded}"
    )
    exit_code = out_ch.channel.recv_exit_status()
    return (
        exit_code,
        out_ch.read().decode(errors="replace").strip(),
        err_ch.read().decode(errors="replace").strip(),
    )


def ssh_start_agent(client, agent_path):
    safe_path = agent_path.replace("'", "''")
    cmd = (
        f"powershell -Command \""
        f"Start-Process -FilePath '{safe_path}' -NoNewWindow; "
        f"while($true) {{ Start-Sleep -Seconds 60 }}"
        f"\""
    )
    client.exec_command(cmd)


def ssh_terminate_agent(client, agent_path):
    client.exec_command(f"taskkill /F /IM {_exe_name(agent_path)}")


def remove_agents_by_name(base_url, headers, exe_name):
    ids = [
        a["a_id"]
        for a in get_agent_list(base_url, headers)
        if a.get("a_process", "").lower() == exe_name.lower()
    ]
    if ids:
        requests.post(
            f"{base_url}/agent/remove",
            json={"agent_id_array": ids},
            headers=headers, verify=False, timeout=15,
        )


def wait_for_active_agent(base_url, headers, known_ticks, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for agent in get_agent_list(base_url, headers):
            aid  = agent.get("a_id")
            tick = agent.get("a_last_tick", 0)
            if aid not in known_ticks or tick > known_ticks[aid]:
                return agent
        time.sleep(POLL_INTERVAL)
    return None


def ssh_deliver(base_url, headers, ssh_cfg):
    agent_path = ssh_cfg["agent_path"]
    retries    = ssh_cfg.get("connect_retries", 30)
    interval   = ssh_cfg.get("connect_retry_interval", 20)

    for attempt in range(1, retries + 1):
        try:
            client = ssh_connect(ssh_cfg)
            break
        except (OSError, paramiko.SSHException):
            if attempt == retries:
                raise RuntimeError(f"Windows target not reachable after {retries} attempts")
            time.sleep(interval)

    preamble = ssh_cfg.get("preamble", [])
    if isinstance(preamble, str):
        preamble = [preamble]
    for cmd in preamble:
        exit_code, _, err = _ps_run(client, cmd)
        if exit_code != 0:
            client.close()
            raise RuntimeError(f"Preamble command failed (exit {exit_code}): {err}")

    ssh_terminate_agent(client, agent_path)
    time.sleep(1)
    remove_agents_by_name(base_url, headers, _exe_name(agent_path))

    if "source_path" in ssh_cfg:
        source = os.path.expanduser(ssh_cfg["source_path"])
        sftp   = client.open_sftp()
        sftp.put(source, agent_path)
        sftp.close()

    known_ticks = {a["a_id"]: a.get("a_last_tick", 0) for a in get_agent_list(base_url, headers)}
    ssh_start_agent(client, agent_path)
    time.sleep(2)

    agent = wait_for_active_agent(base_url, headers, known_ticks)
    if agent is None:
        client.close()
        raise RuntimeError("Agent did not check in within 60s.")
    return client, agent.get("a_id")
