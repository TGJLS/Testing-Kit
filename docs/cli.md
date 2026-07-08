# CLI & config reference

## Flags

```
adaptix-testing [flags]

  -c, --config FILE   Config YAML (default: config.yaml)
  -t, --tasks  FILE   Tasks YAML (default: tasks.yaml)
  -o, --output FILE   Write results to FILE; suppress all stdout during the run
```

When `-o` is given, nothing prints during the run. On completion the file receives the summary table and, on failure, the failure detail panels. Useful for CI artefacts.

Exit code is `0` on all-pass, `1` on any failure.

---

## config.yaml

### Server + operator

```yaml
server:
  url: https://127.0.0.1:4321
  endpoint: /endpoint       # from profile.yaml → server.endpoint

operator:
  name: operator
  password: changeme
```

### Pin a specific agent (optional)

```yaml
agent:
  id: 12abcdef
```

Omit to use the first available agent on the server.

---

### Setup — automatic listener + agent (optional)

Creates a listener and generates an agent binary before running tasks. Two modes: **profile-based** (reads saved profiles from `~/.adaptix/storage-v1.db`) or **inline** (config embedded directly in the YAML). You can mix the two — e.g. listener from the DB and agent inline.

#### Profile-based

```yaml
setup:
  project: myproject
  listener_profile: my_listener   # omit → first profile in project
  agent_profile: my_agent         # omit → auto-matches listener, falls back to first
  agent_output: ./agent.exe       # where to save the payload (default: ./generated_agent)
```

If the listener already exists on the server it is skipped — safe to re-run.

#### Inline

```yaml
setup:
  listener:
    name: my_listener
    type: KharonHTTP
    config:
      host_bind: "0.0.0.0"
      port_bind: 443
      callback_addresses:
        - "10.0.0.1:443"
  agent:
    agent: kharon
    listener: my_listener
    listener_type: KharonHTTP
    config:
      arch: x64
      format: Bin
      sleep: 5s
      jitter: 10
  agent_output: ./agent.bin
```

`config` can be a YAML mapping or a raw JSON string — both are accepted.

---

### SSH delivery (optional)

Uploads and starts the agent on a Windows target before running tasks. Requires SSH `authorized_keys` configured on the target.

```yaml
ssh:
  host: 192.168.1.100
  port: 22                               # optional (default: 22)
  username: administrator
  key_path: ~/.ssh/id_rsa                # optional — uses key file; omit for ssh-agent
  source_path: ./agent.exe              # optional — upload via SCP before starting
  agent_path: C:\Users\administrator\agent.exe
  terminate: true                        # kill agent + remove from server when done
  connect_retries: 60                    # attempts before giving up (default: 30)
  connect_retry_interval: 20            # seconds between attempts (default: 20)
```

Combine `setup.agent_output` and `ssh.source_path` pointing to the same path to generate and deliver an agent in one run.

When `terminate: true`, the agent process is killed via `taskkill` and its record removed from the server at the end of the run.

#### PowerShell preamble (optional)

Commands run on the target after connecting but before uploading the agent. Use this to prepare the environment — create directories, disable Defender, etc.

```yaml
ssh:
  host: 192.168.1.100
  username: administrator
  preamble:
    - "New-Item -ItemType Directory -Force -Path C:\\ci"
    - "Set-MpPreference -DisableRealtimeMonitoring $true"
    - "Add-MpPreference -ExclusionPath C:\\ci"
```

Each command runs via PowerShell `-EncodedCommand` — quoting and special characters are handled safely. A non-zero exit code aborts the run immediately. A single string is also accepted instead of a list.
