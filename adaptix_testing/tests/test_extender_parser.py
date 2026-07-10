import pytest
from adaptix_testing import extender_parser as ep


# ── Fixtures ──────────────────────────────────────────────────────────────────

LISTENER_AXS = """
function ListenerUI(mode) {
    var container = form.create_container();
    container.put("host_bind", form.create_combo(getNetworkInterfaces()), "0.0.0.0");
    container.put("port_bind", form.create_spin(1, 65535), 443);
    container.put("callback_addresses", form.create_textmulti("host:port"), "");
    container.put("encrypt_key", form.create_textline("32 hex chars"), "");
    container.put("ssl", form.create_checkbox("Enable SSL"), false);
    container.put("uploaded_file", form.create_file("profile"), "");
    container.put("sleep", form.create_spin(0, 3600), 5);
    return {container: container};
}
"""

AGENT_AXS = """
function GenerateUI(listenerType) {
    var ui_container = form.create_container();
    ui_container.put("arch", form.create_combo(["x64", "x86"]), "x64");
    ui_container.put("format", form.create_combo(["Exe", "Dll"]), "Exe");
    ui_container.put("sleep", form.create_spin(0, 3600), 5);
    ui_container.put("jitter", form.create_spin(0, 100), 0);
    return {ui_container: ui_container};
}
"""

COMBO_AXS = """
function ListenerUI(mode) {
    var c = form.create_container();
    c.put("proto", form.create_combo(["http", "https"]), "http");
    return {container: c};
}
function GenerateUI(lt) {
    var c = form.create_container();
    c.put("arch", form.create_combo(["x64"]), "x64");
    return {ui_container: c};
}
"""


# ── parse_axs_fields ──────────────────────────────────────────────────────────

def test_parse_listener_fields(tmp_path):
    fields = ep.parse_axs_fields(LISTENER_AXS, "ListenerUI")
    keys = [f["key"] for f in fields]
    assert "host_bind" in keys
    assert "port_bind" in keys
    assert "callback_addresses" in keys


def test_parse_agent_fields():
    fields = ep.parse_axs_fields(AGENT_AXS, "GenerateUI")
    keys = [f["key"] for f in fields]
    assert "arch" in keys
    assert "format" in keys


def test_parse_missing_function_returns_empty():
    assert ep.parse_axs_fields(LISTENER_AXS, "GenerateUI") == []


def test_parse_spin_widget():
    fields = ep.parse_axs_fields(LISTENER_AXS, "ListenerUI")
    port = next(f for f in fields if f["key"] == "port_bind")
    assert port["widget"] == "spin"
    assert port["def"] == 443


def test_parse_file_widget():
    fields = ep.parse_axs_fields(LISTENER_AXS, "ListenerUI")
    uf = next(f for f in fields if f["key"] == "uploaded_file")
    assert uf["widget"] == "file"


def test_parse_bool_widget():
    fields = ep.parse_axs_fields(LISTENER_AXS, "ListenerUI")
    ssl = next(f for f in fields if f["key"] == "ssl")
    assert ssl["widget"] == "bool"
    assert ssl["def"] is False


def test_parse_combo_widget():
    fields = ep.parse_axs_fields(COMBO_AXS, "ListenerUI")
    proto = next(f for f in fields if f["key"] == "proto")
    assert proto["widget"] == "combo"


def test_parse_invalid_js_returns_empty():
    assert ep.parse_axs_fields("{{{{invalid javascript", "ListenerUI") == []


def test_parse_get_network_interfaces_mocked():
    fields = ep.parse_axs_fields(LISTENER_AXS, "ListenerUI")
    hb = next(f for f in fields if f["key"] == "host_bind")
    assert hb["widget"] == "combo"


# ── classify_field ────────────────────────────────────────────────────────────

def test_classify_file_is_required():
    f = ep.classify_field("uploaded_file", "file", "")
    assert f["source"] == "required"
    assert f["value"] is None


def test_classify_network_key_empty_default():
    f = ep.classify_field("callback_addresses", "string", "")
    assert f["source"] == "network"


def test_classify_host_key_empty_default():
    f = ep.classify_field("host_ip", "string", "")
    assert f["source"] == "network"


def test_classify_empty_default_is_required():
    f = ep.classify_field("custom_field", "string", "")
    assert f["source"] == "required"
    assert f["value"] is None


def test_classify_encrypt_key_non_empty():
    f = ep.classify_field("encrypt_key", "string", "abc123")
    assert f["source"] == "generate"


def test_classify_non_empty_default_is_auto():
    f = ep.classify_field("proto", "combo", "http")
    assert f["source"] == "auto"
    assert f["value"] == "http"


def test_classify_bool_false_is_auto():
    f = ep.classify_field("ssl", "bool", False)
    assert f["source"] == "auto"
    assert f["value"] is False


def test_classify_int_default_is_auto():
    f = ep.classify_field("port_bind", "spin", 443)
    assert f["source"] == "auto"
    assert f["value"] == 443


def test_classify_zero_int_is_auto():
    f = ep.classify_field("jitter", "spin", 0)
    assert f["source"] == "auto"
    assert f["value"] == 0


# ── apply_special_registry ────────────────────────────────────────────────────

def test_special_host_bind():
    f = ep.classify_field("host_bind", "combo", "127.0.0.1")
    f = ep.apply_special_registry("host_bind", f)
    assert f["source"] == "auto"
    assert f["value"] == "0.0.0.0"


def test_special_sleep():
    f = ep.classify_field("sleep", "spin", 30)
    f = ep.apply_special_registry("sleep", f)
    assert f["source"] == "auto"
    assert f["value"] == "0s"


def test_special_encrypt_key():
    f = ep.classify_field("encrypt_key", "string", "")
    f = ep.apply_special_registry("encrypt_key", f)
    assert f["source"] == "generate"


def test_special_callback_addresses():
    f = ep.classify_field("callback_addresses", "string", "")
    f = ep.apply_special_registry("callback_addresses", f)
    assert f["source"] == "network"


def test_special_uploaded_file():
    f = ep.classify_field("uploaded_file", "file", "")
    f = ep.apply_special_registry("uploaded_file", f)
    assert f["source"] == "required"
    assert f["hint"] == "base64-encoded malleable profile JSON"


def test_special_page_payload_with_marker():
    f = ep.classify_field("page-payload", "string", "data<<<PAYLOAD_DATA>>>end")
    f = ep.apply_special_registry("page-payload", f)
    assert f["source"] == "auto"


def test_special_page_payload_without_marker():
    f = ep.classify_field("page-payload", "string", "")
    f = ep.apply_special_registry("page-payload", f)
    assert f["source"] == "required"


# ── find_extender_configs ─────────────────────────────────────────────────────

def test_find_extender_configs(tmp_path):
    (tmp_path / "listener").mkdir()
    (tmp_path / "listener" / "config.yaml").write_text(
        "extender_type: listener\nlistener_name: TestHTTP\n"
    )
    (tmp_path / "other.yaml").write_text("name: not an extender\n")
    configs = ep.find_extender_configs(str(tmp_path))
    assert len(configs) == 1
    assert configs[0]["data"]["listener_name"] == "TestHTTP"
    assert configs[0]["rel_path"] == "listener/config.yaml"


def test_find_extender_configs_empty(tmp_path):
    assert ep.find_extender_configs(str(tmp_path)) == []


# ── detect_extender_type ──────────────────────────────────────────────────────

def test_detect_listener_only():
    configs = [{"rel_path": "l/config.yaml", "data": {"extender_type": "listener", "listener_name": "TestHTTP"}}]
    ext_type, ln, an, compat = ep.detect_extender_type(configs)
    assert ext_type == "listener"
    assert ln == "TestHTTP"
    assert an is None


def test_detect_agent_only():
    configs = [{"rel_path": "a/config.yaml", "data": {
        "extender_type": "agent", "agent_name": "test-agent",
        "listeners": ["TestHTTP"]
    }}]
    ext_type, ln, an, compat = ep.detect_extender_type(configs)
    assert ext_type == "agent"
    assert an == "test-agent"
    assert compat == ["TestHTTP"]


def test_detect_listener_plus_agent():
    configs = [
        {"rel_path": "l/config.yaml", "data": {"extender_type": "listener", "listener_name": "TestHTTP"}},
        {"rel_path": "a/config.yaml", "data": {"extender_type": "agent", "agent_name": "test", "listeners": ["TestHTTP"]}},
    ]
    ext_type, ln, an, compat = ep.detect_extender_type(configs)
    assert ext_type == "listener+agent"
    assert ln == "TestHTTP"
    assert an == "test"


def test_detect_bof_no_configs():
    ext_type, ln, an, compat = ep.detect_extender_type([])
    assert ext_type == "bof"
    assert ln is None


# ── parse_extender_repo ───────────────────────────────────────────────────────

def test_parse_extender_repo_listener_plus_agent(tmp_path):
    (tmp_path / "listener").mkdir()
    (tmp_path / "listener" / "config.yaml").write_text(
        "extender_type: listener\nlistener_name: TestHTTP\n"
    )
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "config.yaml").write_text(
        "extender_type: agent\nagent_name: test-agent\nlisteners: [TestHTTP]\n"
    )
    (tmp_path / "listener_ui.axs").write_text(LISTENER_AXS)
    (tmp_path / "agent_ui.axs").write_text(AGENT_AXS)

    result = ep.parse_extender_repo(str(tmp_path), "/app/extenders", "test")
    assert result["extender_type"] == "listener+agent"
    assert result["listener_name"] == "TestHTTP"
    assert result["agent_name"] == "test-agent"
    assert result["container_path"] == "/app/extenders/test"
    assert result["listener_schema"] is not None
    assert "host_bind" in result["listener_schema"]
    assert result["agent_schema"] is not None
    assert "arch" in result["agent_schema"]
    assert "listener/config.yaml" in result["listener_config_rel_paths"]
    assert "agent/config.yaml" in result["agent_config_rel_paths"]


def test_parse_extender_repo_bof(tmp_path):
    (tmp_path / "commands.axs").write_text("ax.register_commands_group({});")
    result = ep.parse_extender_repo(str(tmp_path), "/app/extenders", "extension-kit")
    assert result["extender_type"] == "bof"
    assert result["listener_schema"] is None
    assert "commands.axs" in result["bof_axs_rel_paths"]


def test_parse_extender_repo_schema_applies_special_registry(tmp_path):
    (tmp_path / "listener").mkdir()
    (tmp_path / "listener" / "config.yaml").write_text(
        "extender_type: listener\nlistener_name: TestHTTP\n"
    )
    (tmp_path / "ui.axs").write_text(LISTENER_AXS)
    result = ep.parse_extender_repo(str(tmp_path), "/app/extenders", "test")
    schema = result["listener_schema"]
    assert schema["host_bind"]["value"] == "0.0.0.0"
    assert schema["sleep"]["value"] == "0s"
    assert schema["uploaded_file"]["source"] == "required"
    assert schema["callback_addresses"]["source"] == "network"
