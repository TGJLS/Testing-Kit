# API Reference

Base URL: `http://localhost:1234` (or your configured host/port)

Interactive docs are available while the server is running:

- `/docs` — Swagger UI (try requests directly in the browser)
- `/redoc` — ReDoc (clean reference view)
- `/openapi.json` — raw OpenAPI schema

---

## Health

### GET /health

Returns `{"status": "ok"}` when the server is up. Used by the CLI and CI to gate readiness.

---

## Run Tests

### POST /v1/run-tests

Trigger a full test run. Reads configuration from the server's `config.yaml`, preamble from the DB, and tasks from the DB.

**Response 200**
```json
{
  "passed": 3,
  "failed": 0,
  "timed_out": 0,
  "dispatch_failed": 0,
  "xfail": 1,
  "results": [
    { "cmdline": "shell whoami", "status": "passed", "output": "ci_runner", "err_msg": "" }
  ]
}
```

**Response 500** if the run itself errors (e.g. cannot reach Adaptix server).

---

## Preamble

PowerShell commands that run on the Windows target before the agent is started. Executed in `order_index` order.

### GET /v1/preamble

List all preamble commands ordered by `order_index`.

**Response 200**
```json
[
  { "id": 1, "order_index": 0, "command": "New-Item -ItemType Directory -Force -Path C:\\ci | Out-Null" },
  { "id": 2, "order_index": 1, "command": "Set-MpPreference -DisableRealtimeMonitoring $true" }
]
```

---

### POST /v1/preamble

Add one preamble command.

**Request**
```json
{ "command": "Add-MpPreference -ExclusionPath C:\\ci", "order_index": 2 }
```

`order_index` is optional — omit to append after the current last entry.

**Response 201** — the created item.

---

### PUT /v1/preamble/{id}

Update a preamble command. Send only the fields to change.

**Request**
```json
{ "command": "Set-MpPreference -DisableRealtimeMonitoring $false" }
```

**Response 200** — the updated item.  
**404** if id not found.

---

### DELETE /v1/preamble/{id}

Delete a preamble command.

**Response 204** No Content.  
**404** if id not found.

---

### POST /v1/preamble/batch

Append multiple commands without affecting existing ones.

**Request** — array of command objects:
```json
[
  { "command": "New-Item -ItemType Directory -Force -Path C:\\ci | Out-Null" },
  { "command": "Set-MpPreference -DisableRealtimeMonitoring $true" }
]
```

**Response 201** — array of created items.

---

### PUT /v1/preamble/batch

Replace the entire preamble list. All existing commands are deleted, then the provided list is inserted in one transaction.

**Request** — same shape as `POST /v1/preamble/batch`.

**Response 200** — array of created items.

---

### DELETE /v1/preamble/batch

Delete multiple commands by id.

**Request**
```json
{ "ids": [1, 3, 5] }
```

**Response 200**
```json
{ "deleted": 3 }
```

---

## Tasks

Test cases dispatched to the Adaptix agent. Executed in `order_index` order.

### GET /v1/tasks

List all tasks ordered by `order_index`.

**Response 200**
```json
[
  {
    "id": 1,
    "order_index": 0,
    "cmdline": "shell whoami",
    "expected": "ci_runner",
    "expected_regex": null,
    "not_expected": null,
    "not_expected_regex": null,
    "allowed_to_fail": false,
    "capture": null
  }
]
```

---

### POST /v1/tasks

Add one task. Only `cmdline` is required; all other fields are optional and combinable.

**Request**
```json
{
  "cmdline": "shell whoami",
  "expected": "ci_runner",
  "allowed_to_fail": false
}
```

**Response 201** — the created task.

---

### PUT /v1/tasks/{id}

Update a task. Send only the fields to change; omitted fields are preserved.

**Request**
```json
{ "expected": "administrator" }
```

**Response 200** — the updated task.  
**404** if id not found.

---

### DELETE /v1/tasks/{id}

Delete a task.

**Response 204** No Content.  
**404** if id not found.

---

### POST /v1/tasks/batch

Append multiple tasks without affecting existing ones.

**Request** — array of task objects:
```json
[
  { "cmdline": "shell whoami", "expected": "ci_runner" },
  { "cmdline": "shell dir C:\\ci", "expected": "agent.exe" }
]
```

**Response 201** — array of created tasks.

---

### PUT /v1/tasks/batch

Replace the entire task list. All existing tasks are deleted, then the provided list is inserted in one transaction.

**Request** — same shape as `POST /v1/tasks/batch`.

**Response 200** — array of created tasks.

---

### DELETE /v1/tasks/batch

Delete multiple tasks by id.

**Request**
```json
{ "ids": [2, 4] }
```

**Response 200**
```json
{ "deleted": 2 }
```

---

## Task fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cmdline` | string | yes | Command dispatched to the agent via the server's AxScript engine |
| `expected` | string | no | Case-insensitive substring that must appear in output |
| `expected_regex` | string | no | Regex that must match somewhere in output |
| `not_expected` | string | no | Case-insensitive substring that must NOT appear |
| `not_expected_regex` | string | no | Regex that must NOT match |
| `allowed_to_fail` | bool | no | If true, failures are recorded as `xfail` and do not fail the run |
| `capture` | object | no | `{"varName": "regex with (capture group)"}` — stored for substitution into later tasks via `{{varName}}` |
| `order_index` | int | no | Execution order; auto-assigned if omitted |
