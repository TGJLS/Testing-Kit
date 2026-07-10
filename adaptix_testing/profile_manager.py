import os
import yaml

PROFILE_PATH             = os.environ.get("ADAPTIX_PROFILE_PATH",      "/app/adaptixc2/profile.yaml")
EXTENDERS_CONTAINER_PATH = os.environ.get("EXTENDERS_CONTAINER_PATH",  "/app/extenders")
EXTENDERS_HOST_PATH      = os.environ.get("EXTENDERS_HOST_PATH",       "/app/adaptixc2/extenders")


def read_profile(path: str) -> dict:
    """Read YAML profile. Returns minimal structure if file absent or empty."""
    try:
        data = yaml.safe_load(open(path)) or {}
    except FileNotFoundError:
        data = {}
    ts = data.setdefault("Teamserver", {})
    ts.setdefault("extenders", [])
    ts.setdefault("axscripts", [])
    return data


def write_profile(path: str, data: dict) -> None:
    """Atomic write: write to path+'.tmp' then os.replace."""
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(tmp, "w") as fh:
        fh.write("# Managed by Testing-Kit — do not edit manually\n")
        yaml.dump(data, fh, default_flow_style=False)
    os.replace(tmp, path)


def add_extender_entries(
    profile_path: str,
    container_extender_path: str,
    config_rel_paths: list[str],
    axs_rel_paths: list[str],
) -> None:
    """Add entries to Teamserver.extenders and Teamserver.axscripts, deduplicating."""
    data = read_profile(profile_path)
    ts = data["Teamserver"]
    for rel in config_rel_paths:
        entry = f"{container_extender_path}/{rel}"
        if entry not in ts["extenders"]:
            ts["extenders"].append(entry)
    for rel in axs_rel_paths:
        entry = f"{container_extender_path}/{rel}"
        if entry not in ts["axscripts"]:
            ts["axscripts"].append(entry)
    write_profile(profile_path, data)


def remove_extender_entries(profile_path: str, container_extender_path: str) -> None:
    """Remove all entries whose paths start with container_extender_path."""
    data = read_profile(profile_path)
    ts = data["Teamserver"]
    ts["extenders"] = [e for e in ts["extenders"] if not e.startswith(container_extender_path)]
    ts["axscripts"] = [e for e in ts["axscripts"] if not e.startswith(container_extender_path)]
    write_profile(profile_path, data)
