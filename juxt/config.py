from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class RemoteConfig:
    host: str
    user: str | None = None
    port: int = 22
    key_path: str | None = None  # path to private key; None = use agent / default keys


@dataclass
class Config:
    template: str
    axes: dict[str, list[str]]  # ordered; all values are strings
    keys: dict[str, str]        # letter -> axis_name
    mode: int = 0               # NavMode value (0=tap, 1=seek, 2=pin)
    remote: RemoteConfig | None = None


def _auto_discover(directory: str, separator: str) -> tuple[str, dict[str, list[str]]]:
    files = sorted(f for f in Path(directory).iterdir() if f.is_file())
    if not files:
        raise ValueError(f"No files found in {directory!r}")

    stems = [f.stem for f in files]
    ext = files[0].suffix

    parts_list = [s.split(separator) for s in stems]
    n_cols = len(parts_list[0])
    if any(len(p) != n_cols for p in parts_list):
        raise ValueError("Filenames have inconsistent number of parts after splitting")

    axes: dict[str, list[str]] = {}
    col_axis: dict[int, str] = {}
    for i in range(n_cols):
        values = list(dict.fromkeys(p[i] for p in parts_list))
        if len(values) > 1:
            name = f"axis_{i}"
            axes[name] = values
            col_axis[i] = name

    template_parts = [
        f"{{{col_axis[i]}}}" if i in col_axis else parts_list[0][i]
        for i in range(n_cols)
    ]
    template = str(Path(directory) / (separator.join(template_parts) + ext))
    return template, axes


def _auto_keys(axes: dict[str, list[str]]) -> dict[str, str]:
    """Assign each axis the first letter of its name that isn't already taken."""
    keys: dict[str, str] = {}
    used: set[str] = set()
    for name in axes:
        for ch in name.lower():
            if ch.isalpha() and ch not in used:
                keys[ch] = name
                used.add(ch)
                break
    return keys


def load_config(path: str) -> Config:
    with open(path) as f:
        data = yaml.safe_load(f)

    if "discover" in data:
        disc = data["discover"]
        template, axes = _auto_discover(
            disc["directory"],
            disc.get("separator", "_"),
        )
    else:
        if "template" not in data or "axes" not in data:
            raise ValueError("Config must contain 'template' + 'axes', or a 'discover' block")
        template = data["template"]
        axes = {k: [str(v) for v in vs] for k, vs in data["axes"].items()}

    if not axes:
        raise ValueError("No axes found in config")

    keys_cfg = data.get("keys", {})
    keys = {str(k): str(v) for k, v in keys_cfg.items()} if keys_cfg else _auto_keys(axes)

    mode = _parse_mode(data.get("mode", 0))

    remote = None
    if "remote" in data:
        if "discover" in data:
            raise ValueError("'remote' and 'discover' cannot be used together")
        remote = _parse_remote(data["remote"])

    return Config(template=template, axes=axes, keys=keys, mode=mode, remote=remote)


def _parse_remote(value) -> RemoteConfig:
    if isinstance(value, str):
        # accepts: host  |  user@host  |  host:port  |  user@host:port
        user = None
        host = value
        port = 22
        if "@" in host:
            user, host = host.split("@", 1)
        if ":" in host:
            host, port_str = host.rsplit(":", 1)
            port = int(port_str)
        return RemoteConfig(host=host, user=user, port=port)
    if isinstance(value, dict):
        return RemoteConfig(
            host=value["host"],
            user=value.get("user"),
            port=int(value.get("port", 22)),
            key_path=value.get("key_path") or value.get("key"),
        )
    raise ValueError(f"Invalid remote config: {value!r}; expected 'user@host' or a dict")


def _parse_mode(value) -> int:
    if isinstance(value, int):
        if 0 <= value <= 2:
            return value
        raise ValueError(f"mode must be 0–2, got {value}")
    s = str(value).lower().replace("-", "_").replace(" ", "_")
    table = {"0": 0, "tap": 0, "1": 1, "seek": 1, "2": 2, "pin": 2}
    if s not in table:
        raise ValueError(f"Unknown mode {value!r}; choose tap/seek/pin or 0/1/2")
    return table[s]
