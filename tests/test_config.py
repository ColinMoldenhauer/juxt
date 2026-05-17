"""Tests for juxt/config.py."""
from __future__ import annotations

import pytest
import yaml

from juxt.config import (
    Config,
    RemoteConfig,
    _auto_discover,
    _auto_keys,
    _parse_mode,
    _parse_remote,
    dump_config,
    load_config,
)


class TestAutoKeys:
    def test_first_letter_per_axis(self):
        axes = {"sensor": [], "date": [], "overpass": []}
        assert _auto_keys(axes) == {"s": "sensor", "d": "date", "o": "overpass"}

    def test_collision_falls_back_to_next_letter(self):
        # "source" can't take "s" (taken by "sensor"), so falls back to "o"
        axes = {"sensor": [], "source": []}
        keys = _auto_keys(axes)
        assert keys["s"] == "sensor"
        assert keys["o"] == "source"

    def test_empty_axes(self):
        assert _auto_keys({}) == {}

    def test_all_letters_exhausted(self):
        # "a" takes "a"; "ab" can't take "a" (taken), takes "b"; "a" has no
        # remaining letter → gets no key
        axes = {"a": [], "ab": [], "abc": []}
        keys = _auto_keys(axes)
        assert keys.get("a") == "a"
        assert keys.get("b") == "ab"
        assert keys.get("c") == "abc"


class TestParseRemote:
    def test_host_only(self):
        r = _parse_remote("myhost")
        assert r == RemoteConfig(host="myhost", user=None, port=22)

    def test_user_at_host(self):
        r = _parse_remote("alice@myhost")
        assert r.host == "myhost"
        assert r.user == "alice"
        assert r.port == 22

    def test_host_with_port(self):
        r = _parse_remote("myhost:2222")
        assert r.host == "myhost"
        assert r.port == 2222
        assert r.user is None

    def test_user_host_port(self):
        r = _parse_remote("alice@myhost:2222")
        assert r.host == "myhost"
        assert r.user == "alice"
        assert r.port == 2222

    def test_dict_full(self):
        r = _parse_remote({"host": "srv", "user": "bob", "port": 2222, "key_path": "/id_ed"})
        assert r == RemoteConfig(host="srv", user="bob", port=2222, key_path="/id_ed")

    def test_dict_key_alias(self):
        # "key" is an accepted alias for "key_path"
        r = _parse_remote({"host": "srv", "key": "/id_rsa"})
        assert r.key_path == "/id_rsa"

    def test_dict_defaults(self):
        r = _parse_remote({"host": "srv"})
        assert r.port == 22
        assert r.user is None
        assert r.key_path is None

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid remote"):
            _parse_remote(42)


class TestParseMode:
    @pytest.mark.parametrize("value,expected", [
        (0, 0), (1, 1), (2, 2),
        ("0", 0), ("tap", 0),
        ("1", 1), ("seek", 1),
        ("2", 2), ("pin", 2),
    ])
    def test_valid(self, value, expected):
        assert _parse_mode(value) == expected

    def test_out_of_range_int(self):
        with pytest.raises(ValueError, match="0–2"):
            _parse_mode(3)

    def test_unknown_string(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            _parse_mode("bogus")

    def test_legacy_names_rejected(self):
        # The config file uses tap/seek/pin; "twin" etc. are command-mode aliases
        with pytest.raises(ValueError):
            _parse_mode("twin")


class TestAutoDiscover:
    def test_detects_two_axes(self, tmp_path):
        for name in ["A_d1.png", "A_d2.png", "B_d1.png", "B_d2.png"]:
            (tmp_path / name).write_bytes(b"")
        template, axes = _auto_discover(str(tmp_path), "_")
        assert set(axes.keys()) == {"axis_0", "axis_1"}
        assert set(axes["axis_0"]) == {"A", "B"}
        assert set(axes["axis_1"]) == {"d1", "d2"}

    def test_template_uses_placeholders(self, tmp_path):
        for name in ["A_d1.png", "B_d1.png"]:
            (tmp_path / name).write_bytes(b"")
        template, _ = _auto_discover(str(tmp_path), "_")
        assert "{axis_0}" in template

    def test_fixed_column_excluded(self, tmp_path):
        # column 1 ("d1") never changes → it's fixed, not an axis
        for name in ["A_d1.png", "B_d1.png"]:
            (tmp_path / name).write_bytes(b"")
        _, axes = _auto_discover(str(tmp_path), "_")
        assert len(axes) == 1
        assert "axis_0" in axes

    def test_empty_dir_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No files"):
            _auto_discover(str(tmp_path), "_")

    def test_inconsistent_parts_raises(self, tmp_path):
        (tmp_path / "A_d1.png").write_bytes(b"")
        (tmp_path / "B.png").write_bytes(b"")  # only one part
        with pytest.raises(ValueError, match="inconsistent"):
            _auto_discover(str(tmp_path), "_")


class TestLoadConfig:
    def test_template_mode(self, tmp_path, flat_plot_dir):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "template": str(flat_plot_dir / "{sensor}_{date}.png"),
            "axes": {"sensor": ["A", "B"], "date": ["d1", "d2"]},
        }))
        cfg = load_config(str(cfg_path))
        assert cfg.axes == {"sensor": ["A", "B"], "date": ["d1", "d2"]}
        assert cfg.remote is None

    def test_discover_mode(self, tmp_path, flat_plot_dir):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "discover": {"directory": str(flat_plot_dir), "separator": "_"},
        }))
        cfg = load_config(str(cfg_path))
        assert len(cfg.axes) == 2

    def test_remote_parsed(self, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "template": "plots/{sensor}.png",
            "axes": {"sensor": ["A", "B"]},
            "remote": "alice@server:2222",
        }))
        cfg = load_config(str(cfg_path))
        assert cfg.remote is not None
        assert cfg.remote.host == "server"
        assert cfg.remote.user == "alice"
        assert cfg.remote.port == 2222

    def test_remote_and_discover_conflict(self, tmp_path, flat_plot_dir):
        # The conflict check runs after _auto_discover succeeds, so point discover
        # at a real directory containing images.
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "discover": {"directory": str(flat_plot_dir), "separator": "_"},
            "remote": "myhost",
        }))
        with pytest.raises(ValueError, match="'remote' and 'discover'"):
            load_config(str(cfg_path))

    def test_missing_axes_key_raises(self, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({"template": "plots/{sensor}.png"}))
        with pytest.raises(ValueError):
            load_config(str(cfg_path))

    def test_explicit_mode(self, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "template": "plots/{sensor}.png",
            "axes": {"sensor": ["A", "B"]},
            "mode": "pin",  # config uses tap/seek/pin, not twin/multi-select/case-sensitive
        }))
        assert load_config(str(cfg_path)).mode == 2

    def test_auto_keys_assigned(self, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "template": "plots/{sensor}_{date}.png",
            "axes": {"sensor": ["A"], "date": ["d1"]},
        }))
        cfg = load_config(str(cfg_path))
        assert cfg.keys.get("s") == "sensor"
        assert cfg.keys.get("d") == "date"

    def test_explicit_keys_override_auto(self, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "template": "plots/{sensor}_{date}.png",
            "axes": {"sensor": ["A"], "date": ["d1"]},
            "keys": {"x": "sensor", "y": "date"},
        }))
        cfg = load_config(str(cfg_path))
        assert cfg.keys == {"x": "sensor", "y": "date"}

    def test_string_coercion_in_axes(self, tmp_path):
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(yaml.dump({
            "template": "plots/{year}.png",
            "axes": {"year": [2023, 2024]},  # YAML ints
        }))
        cfg = load_config(str(cfg_path))
        assert cfg.axes["year"] == ["2023", "2024"]


class TestDumpConfig:
    def _base_config(self, **kw) -> Config:
        axes = {"sensor": ["A", "B"], "date": ["d1", "d2"]}
        return Config(
            template="plots/{sensor}_{date}.png",
            axes=axes,
            keys=_auto_keys(axes),
            **kw,
        )

    def test_roundtrip(self, tmp_path):
        cfg = self._base_config()
        out = tmp_path / "saved.yaml"
        dump_config(cfg, str(out))
        reloaded = load_config(str(out))
        assert reloaded.template == cfg.template
        assert reloaded.axes == cfg.axes
        assert reloaded.keys == cfg.keys
        assert reloaded.mode == cfg.mode

    def test_default_mode_omitted(self, tmp_path):
        # mode 0 (tap) is the default — no need to write it
        out = tmp_path / "saved.yaml"
        dump_config(self._base_config(mode=0), str(out))
        data = yaml.safe_load(out.read_text())
        assert "mode" not in data

    def test_non_default_mode_written_as_name(self, tmp_path):
        out = tmp_path / "saved.yaml"
        dump_config(self._base_config(mode=1), str(out))
        data = yaml.safe_load(out.read_text())
        assert data["mode"] == "seek"

        dump_config(self._base_config(mode=2), str(out))
        data = yaml.safe_load(out.read_text())
        assert data["mode"] == "pin"

    def test_axes_written_as_inline_lists(self, tmp_path):
        out = tmp_path / "saved.yaml"
        dump_config(self._base_config(), str(out))
        text = out.read_text()
        # Flow-style lists look like [A, B] on a single line
        assert "[" in text and "]" in text

    def test_remote_simple_written_as_string(self, tmp_path):
        cfg = self._base_config(remote=RemoteConfig(host="myhost", user="alice", port=22))
        out = tmp_path / "saved.yaml"
        dump_config(cfg, str(out))
        data = yaml.safe_load(out.read_text())
        assert data["remote"] == "alice@myhost"

    def test_remote_with_port_written_as_string(self, tmp_path):
        cfg = self._base_config(remote=RemoteConfig(host="myhost", user="alice", port=2222))
        out = tmp_path / "saved.yaml"
        dump_config(cfg, str(out))
        data = yaml.safe_load(out.read_text())
        assert data["remote"] == "alice@myhost:2222"

    def test_remote_with_key_path_written_as_dict(self, tmp_path):
        cfg = self._base_config(
            remote=RemoteConfig(host="myhost", user="alice", port=22, key_path="/home/alice/.ssh/id_ed25519")
        )
        out = tmp_path / "saved.yaml"
        dump_config(cfg, str(out))
        data = yaml.safe_load(out.read_text())
        assert isinstance(data["remote"], dict)
        assert data["remote"]["host"] == "myhost"
        assert data["remote"]["key_path"] == "/home/alice/.ssh/id_ed25519"
        assert "port" not in data["remote"]  # default port omitted

    def test_roundtrip_with_remote(self, tmp_path):
        remote = RemoteConfig(host="srv", user="bob", port=2222)
        cfg = self._base_config(remote=remote)
        out = tmp_path / "saved.yaml"
        dump_config(cfg, str(out))
        reloaded = load_config(str(out))
        assert reloaded.remote is not None
        assert reloaded.remote.host == "srv"
        assert reloaded.remote.user == "bob"
        assert reloaded.remote.port == 2222
