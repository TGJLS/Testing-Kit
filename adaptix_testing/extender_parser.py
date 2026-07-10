import json
import re
import subprocess
from pathlib import Path
from typing import Optional

import yaml
import dukpy

_MOCK_JS = """
var _fields = [];
var form = {
    create_container: function() {
        return {
            put: function(k, w, d) {
                _fields.push({key: k, widget: w && w.t ? w.t : 'string', def: d !== undefined ? d : null});
            }
        };
    },
    create_combo: function(opts) { return {t: 'combo'}; },
    create_spin: function(mn, mx) { return {t: 'spin'}; },
    create_checkbox: function(label) { return {t: 'bool'}; },
    create_textline: function(ph) { return {t: 'string'}; },
    create_textmulti: function(ph) { return {t: 'string'}; },
    create_file: function(label) { return {t: 'file'}; },
    create_dateline: function() { return {t: 'date'}; },
    create_timeline: function() { return {t: 'time'}; },
    create_groupbox: function(label, w) { return w || {t: 'bool'}; },
};
function getNetworkInterfaces() { return ['0.0.0.0']; }
var ax = {
    script_dir: function() { return ''; },
    script_import: function() {},
    script_load: function() {},
    register_commands_group: function() {},
    create_command: function() {
        var c = {
            setPreHook: function() { return c; },
            addArgString: function() { return c; },
            addArgBool: function() { return c; },
            addArgFlagString: function() { return c; },
            addArgFlagInt: function() { return c; },
            addArgInt: function() { return c; },
            addSubCommands: function() { return c; }
        };
        return c;
    },
    create_commands_group: function() { return {}; },
};
var menu = {
    create_action: function() { return {}; },
    create_menu: function() { return {addItem: function(){}}; },
    add_session_access: function() {},
    add_processbrowser: function() {},
};
var event = { on: function() {} };
"""

_NETWORK_RE = re.compile(r'address|callback|host|ip', re.I)
_KEY_RE = re.compile(r'key|secret|token|encrypt', re.I)

_SPECIAL: dict[str, dict] = {
    "host_bind":          {"source": "auto",     "value": "0.0.0.0"},
    "sleep":              {"source": "auto",     "value": "0s"},
    "callback_addresses": {"source": "network",  "value": None},
    "encrypt_key":        {"source": "generate", "value": None},
    "uploaded_file":      {"source": "required", "value": None,
                           "hint": "base64-encoded malleable profile JSON"},
}


def parse_axs_fields(axs_text: str, fn_name: str) -> list[dict]:
    """Evaluate axs_text with mock globals; call fn_name; return raw [{key,widget,def}]."""
    arg = "'create'" if fn_name == "ListenerUI" else "''"
    try:
        interp = dukpy.JSInterpreter()
        interp.evaljs(_MOCK_JS)
        interp.evaljs(axs_text)
        interp.evaljs("_fields = [];")
        interp.evaljs(f"if (typeof {fn_name} !== 'undefined') {{ {fn_name}({arg}); }}")
        raw = interp.evaljs("JSON.stringify(_fields)")
        return json.loads(raw) if raw and raw != "null" else []
    except Exception:
        return []


def classify_field(key: str, widget: str, default) -> dict:
    """Classify a field into source/value/widget/hint."""
    field: dict = {"source": "auto", "value": default, "widget": widget, "hint": None}
    if widget == "file":
        field.update(source="required", value=None)
    elif _NETWORK_RE.search(key) and (default == "" or default is None):
        field.update(source="network", value=None)
    elif default == "" or default is None:
        field.update(source="required", value=None)
    elif _KEY_RE.search(key):
        field.update(source="generate", value=None)
    return field


def apply_special_registry(key: str, field: dict) -> dict:
    """Apply hard-coded overrides for known field names. Mutates and returns field."""
    if key == "page-payload":
        val = field.get("value") or ""
        if "<<<PAYLOAD_DATA>>>" in str(val):
            field["source"] = "auto"
        else:
            field.update(source="required", value=None)
        return field
    if key in _SPECIAL:
        ov = _SPECIAL[key]
        field["source"] = ov["source"]
        field["value"] = ov.get("value")
        if "hint" in ov:
            field["hint"] = ov["hint"]
    return field


def _build_schema(raw_fields: list[dict]) -> dict:
    schema: dict = {}
    for f in raw_fields:
        key = f["key"]
        field = classify_field(key, f["widget"], f["def"])
        schema[key] = apply_special_registry(key, field)
    return schema


def find_extender_configs(repo_dir: str) -> list[dict]:
    """Return all config.yaml files that contain an 'extender_type' key."""
    configs = []
    for path in sorted(Path(repo_dir).rglob("config.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
            if isinstance(data, dict) and "extender_type" in data:
                configs.append({
                    "path": path,
                    "rel_path": str(path.relative_to(repo_dir)),
                    "data": data,
                })
        except Exception:
            pass
    return configs


def detect_extender_type(
    configs: list[dict],
) -> tuple[str, Optional[str], Optional[str], list[str]]:
    """
    Returns (extender_type, listener_name, agent_name, compatible_listeners).
    extender_type is one of: listener | agent | listener+agent | bof
    """
    listeners = [c for c in configs if c["data"].get("extender_type") == "listener"]
    agents    = [c for c in configs if c["data"].get("extender_type") == "agent"]

    listener_name        = listeners[0]["data"].get("listener_name") if listeners else None
    agent_name           = agents[0]["data"].get("agent_name")       if agents    else None
    compatible_listeners = agents[0]["data"].get("listeners", [])    if agents    else []

    if listeners and agents:
        return "listener+agent", listener_name, agent_name, compatible_listeners
    if listeners:
        return "listener", listener_name, None, []
    if agents:
        return "agent", None, agent_name, compatible_listeners
    return "bof", None, None, []


def clone_repo(git_url: str, dest: str) -> None:
    """Shallow-clone git_url into dest."""
    subprocess.run(
        ["git", "clone", "--depth=1", git_url, dest],
        check=True,
        capture_output=True,
        text=True,
    )


def parse_extender_repo(repo_dir: str, container_base: str, name: str) -> dict:
    """
    Parse a cloned extender repo. Returns a dict with schemas and path metadata
    needed for DB storage and profile.yaml management.
    """
    configs = find_extender_configs(repo_dir)
    ext_type, listener_name, agent_name, compatible_listeners = detect_extender_type(configs)

    container_path = f"{container_base}/{name}"
    axs_files      = sorted(Path(repo_dir).rglob("*.axs"))

    listener_schema: Optional[dict] = None
    agent_schema:    Optional[dict] = None

    if ext_type in ("listener", "listener+agent"):
        for axs in axs_files:
            raw = parse_axs_fields(axs.read_text(), "ListenerUI")
            if raw:
                listener_schema = _build_schema(raw)
                break

    if ext_type in ("agent", "listener+agent"):
        for axs in axs_files:
            raw = parse_axs_fields(axs.read_text(), "GenerateUI")
            if raw:
                agent_schema = _build_schema(raw)
                break

    listener_configs = [c for c in configs if c["data"].get("extender_type") == "listener"]
    agent_configs    = [c for c in configs if c["data"].get("extender_type") == "agent"]
    bof_axs_rels     = (
        [str(p.relative_to(repo_dir)) for p in axs_files]
        if ext_type == "bof" else []
    )

    return {
        "name":                    name,
        "extender_type":           ext_type,
        "listener_name":           listener_name,
        "agent_name":              agent_name,
        "compatible_listeners":    compatible_listeners,
        "listener_schema":         listener_schema,
        "agent_schema":            agent_schema,
        "container_path":          container_path,
        "listener_config_rel_paths": [c["rel_path"] for c in listener_configs],
        "agent_config_rel_paths":    [c["rel_path"] for c in agent_configs],
        "bof_axs_rel_paths":         bof_axs_rels,
    }
