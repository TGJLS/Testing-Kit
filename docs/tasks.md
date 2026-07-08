# Tasks format

Tasks live in a YAML file (default `tasks.yaml`) under a top-level `tasks` key.

```yaml
tasks:
  - cmdline: "whoami"

  - cmdline: "fs ls C:\\Windows"
    expected: "explorer.exe"

  - cmdline: "whoami"
    expected_regex: "(?i)^.*user.*$"

  - cmdline: "whoami"
    not_expected: "access denied"

  - cmdline: "process kill 9999"
    allowed_to_fail: true
```

## Fields

| Field | Description |
|---|---|
| `cmdline` | Command dispatched to the agent via the server's AxScript engine. |
| `expected` | Case-insensitive substring that must appear in output. |
| `expected_regex` | Regex that must match somewhere in the output. Case-sensitive; prefix with `(?i)` for case-insensitive. |
| `not_expected` | Case-insensitive substring that must *not* appear in output. |
| `not_expected_regex` | Regex that must *not* match anywhere in the output. |
| `allowed_to_fail` | If `true`, failure / timeout / dispatch rejection is recorded as `xfail` and does not fail the run. |
| `capture` | Dict of `{variable_name: regex}`. After the task completes, each regex is matched against the output and the first capture group is stored for later tasks. See below. |

All assertion fields are optional and combinable — every specified assertion must pass. Omit all of them to only verify the command completed without error.

> **Note:** Only commands supported by the server-side AxScript engine work here. Client-side hook commands are not available. Some commands complete successfully but return no output via the task list API — omit `expected` for those.

---

## Capture and variable substitution

Use `capture` to store output from one task and reference it in later tasks via `{{variable_name}}`.

```yaml
tasks:
  - cmdline: 'ps run --command "notepad.exe"'
    expected_regex: "Process started: PID \\d+"
    capture:
      pid: "Process started: PID (\\d+)"

  - cmdline: "ps grep {{pid}}"
    expected: "[Token]"

  - cmdline: "ps kill {{pid}}"
    not_expected: "error"
```

Variables are substituted into `cmdline` at dispatch time. If a capture regex has no capture group, or the pattern doesn't match, the variable is not set and any `{{variable_name}}` in later tasks is left as-is.

---

## Examples

**Regression suite** — run after updating an agent or BOF:

```yaml
tasks:
  - cmdline: "whoami"
    expected: "nt authority\\system"
  - cmdline: "fs ls C:\\Windows\\System32"
    expected: "ntdll.dll"
  - cmdline: "process list"
    expected: "lsass.exe"
    not_expected: "error"
```

**Isolating a new BOF** — mark surrounding cleanup as `allowed_to_fail`:

```yaml
tasks:
  - cmdline: "bof /tmp/my_new.o arg1 arg2"
    expected: "success"
  - cmdline: "bof /tmp/cleanup.o"
    allowed_to_fail: true
```

**CI pipeline** — exits `0` on all-pass, `1` on any failure. Use `-o results.txt` to write a clean artefact without interleaved progress output.
