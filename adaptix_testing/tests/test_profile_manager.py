import os
import pytest
import yaml
from adaptix_testing import profile_manager as pm


@pytest.fixture
def profile_path(tmp_path):
    path = str(tmp_path / "profile.yaml")
    with open(path, "w") as f:
        yaml.dump({"Teamserver": {"extenders": [], "axscripts": []}}, f)
    return path


def test_read_profile_returns_teamserver(profile_path):
    data = pm.read_profile(profile_path)
    assert "Teamserver" in data
    assert "extenders" in data["Teamserver"]
    assert "axscripts" in data["Teamserver"]


def test_read_profile_missing_file(tmp_path):
    data = pm.read_profile(str(tmp_path / "missing.yaml"))
    assert data["Teamserver"]["extenders"] == []
    assert data["Teamserver"]["axscripts"] == []


def test_write_profile_creates_file(tmp_path):
    path = str(tmp_path / "new.yaml")
    pm.write_profile(path, {"Teamserver": {"extenders": ["/app/e/c.yaml"], "axscripts": []}})
    data = yaml.safe_load(open(path))
    assert "/app/e/c.yaml" in data["Teamserver"]["extenders"]


def test_write_profile_is_atomic(tmp_path):
    path = str(tmp_path / "profile.yaml")
    pm.write_profile(path, {"Teamserver": {"extenders": [], "axscripts": []}})
    assert not os.path.exists(path + ".tmp")


def test_add_extender_entries_adds_config(profile_path):
    pm.add_extender_entries(
        profile_path,
        "/app/extenders/kharon",
        ["listener/config.yaml"],
        [],
    )
    data = yaml.safe_load(open(profile_path))
    assert "/app/extenders/kharon/listener/config.yaml" in data["Teamserver"]["extenders"]


def test_add_extender_entries_adds_axs(profile_path):
    pm.add_extender_entries(
        profile_path,
        "/app/extenders/ext-kit",
        [],
        ["ext-kit.axs"],
    )
    data = yaml.safe_load(open(profile_path))
    assert "/app/extenders/ext-kit/ext-kit.axs" in data["Teamserver"]["axscripts"]


def test_add_extender_entries_deduplicates(profile_path):
    pm.add_extender_entries(profile_path, "/app/extenders/k", ["l/config.yaml"], [])
    pm.add_extender_entries(profile_path, "/app/extenders/k", ["l/config.yaml"], [])
    data = yaml.safe_load(open(profile_path))
    entries = data["Teamserver"]["extenders"]
    assert entries.count("/app/extenders/k/l/config.yaml") == 1


def test_remove_extender_entries_removes_by_prefix(profile_path):
    pm.add_extender_entries(profile_path, "/app/extenders/k", ["l/config.yaml"], ["k.axs"])
    pm.add_extender_entries(profile_path, "/app/extenders/other", ["o/config.yaml"], [])
    pm.remove_extender_entries(profile_path, "/app/extenders/k")
    data = yaml.safe_load(open(profile_path))
    assert not any(e.startswith("/app/extenders/k") for e in data["Teamserver"]["extenders"])
    assert not any(e.startswith("/app/extenders/k") for e in data["Teamserver"]["axscripts"])
    assert "/app/extenders/other/o/config.yaml" in data["Teamserver"]["extenders"]


def test_remove_extender_entries_noop_if_no_match(profile_path):
    pm.remove_extender_entries(profile_path, "/app/extenders/nonexistent")
    data = yaml.safe_load(open(profile_path))
    assert data["Teamserver"]["extenders"] == []
