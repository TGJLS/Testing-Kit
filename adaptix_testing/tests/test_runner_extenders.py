import json
import pytest
from adaptix_testing import runner

CFG = {
    "server": {"url": "https://c2.example.com", "endpoint": ""},
    "operator": {"name": "ci", "password": "pass"},
}

LISTENER_EXT = {
    "listener_name": "KharonHTTP",
    "agent_name": None,
    "listener_schema": json.dumps({
        "port_bind":          {"source": "auto",     "value": 443,       "widget": "spin",   "hint": None},
        "host_bind":          {"source": "auto",     "value": "0.0.0.0", "widget": "combo",  "hint": None},
        "ssl":                {"source": "auto",     "value": False,     "widget": "bool",   "hint": None},
        "callback_addresses": {"source": "network",  "value": None,      "widget": "string", "hint": None},
        "encrypt_key":        {"source": "generate", "value": None,      "widget": "string", "hint": None},
        "sleep":              {"source": "auto",     "value": "0s",      "widget": "spin",   "hint": None},
    }),
}

AGENT_EXT = {
    "agent_name": "kharon",
    "listener_name": None,
    "agent_schema": json.dumps({
        "arch":   {"source": "auto", "value": "x64", "widget": "combo", "hint": None},
        "format": {"source": "auto", "value": "Exe", "widget": "combo", "hint": None},
        "sleep":  {"source": "auto", "value": "0s",  "widget": "spin",  "hint": None},
    }),
}


def test_resolve_listener_name_and_type():
    profile = runner._resolve_listener_from_extender(LISTENER_EXT, CFG)
    assert profile["name"] == "kharonhttp_ci"
    assert profile["type"] == "KharonHTTP"


def test_resolve_listener_config_is_json():
    profile = runner._resolve_listener_from_extender(LISTENER_EXT, CFG)
    config = json.loads(profile["config"])
    assert config["port_bind"] == 443
    assert config["host_bind"] == "0.0.0.0"
    assert config["ssl"] is False
    assert config["sleep"] == "0s"


def test_resolve_listener_network_field():
    profile = runner._resolve_listener_from_extender(LISTENER_EXT, CFG)
    config = json.loads(profile["config"])
    assert config["callback_addresses"] == "c2.example.com:443"


def test_resolve_listener_generate_field():
    profile = runner._resolve_listener_from_extender(LISTENER_EXT, CFG)
    config = json.loads(profile["config"])
    key = config["encrypt_key"]
    assert len(key) == 32
    assert all(c in "0123456789abcdef" for c in key)


def test_resolve_listener_generate_is_random():
    p1 = runner._resolve_listener_from_extender(LISTENER_EXT, CFG)
    p2 = runner._resolve_listener_from_extender(LISTENER_EXT, CFG)
    assert json.loads(p1["config"])["encrypt_key"] != json.loads(p2["config"])["encrypt_key"]


def test_resolve_listener_required_with_value():
    ext = {
        **LISTENER_EXT,
        "listener_schema": json.dumps({
            "custom": {"source": "required", "value": "myvalue", "widget": "string", "hint": None}
        }),
    }
    profile = runner._resolve_listener_from_extender(ext, CFG)
    assert json.loads(profile["config"])["custom"] == "myvalue"


def test_resolve_listener_required_without_value_raises():
    ext = {
        **LISTENER_EXT,
        "listener_schema": json.dumps({
            "custom": {"source": "required", "value": None, "widget": "string", "hint": None}
        }),
    }
    with pytest.raises(RuntimeError, match="custom"):
        runner._resolve_listener_from_extender(ext, CFG)


def test_resolve_agent_name_and_listener():
    profile = runner._resolve_agent_from_extender(AGENT_EXT, CFG, "kharonhttp_ci")
    assert profile["agent"] == "kharon"
    assert profile["listener"] == "kharonhttp_ci"


def test_resolve_agent_config_is_json():
    profile = runner._resolve_agent_from_extender(AGENT_EXT, CFG, "kharonhttp_ci")
    config = json.loads(profile["config"])
    assert config["arch"] == "x64"
    assert config["format"] == "Exe"
