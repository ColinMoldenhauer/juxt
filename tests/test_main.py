"""Tests for juxt/__main__.py's argv parsing, in particular the --NAME VALUE
placeholder-pinning flags (see juxt.detect._axes_from_local_template)."""
from __future__ import annotations

import pytest

from juxt.__main__ import _parse_args, _parse_axis_pins


class TestParseAxisPins:
    def test_single_flag_single_value(self):
        assert _parse_axis_pins(["--sensor", "A"]) == {"sensor": ["A"]}

    def test_single_flag_multiple_values(self):
        assert _parse_axis_pins(["--sensor", "A", "B", "C"]) == {"sensor": ["A", "B", "C"]}

    def test_multiple_flags(self):
        result = _parse_axis_pins(["--sensor", "A", "B", "--date", "X"])
        assert result == {"sensor": ["A", "B"], "date": ["X"]}

    def test_no_input_returns_empty(self):
        assert _parse_axis_pins([]) == {}

    def test_hyphenated_axis_name_preserved_verbatim(self):
        """Placeholder names may contain '-' (e.g. {yyyy-mm-dd}) so, unlike a
        generic flag parser, hyphens must not be folded to underscores."""
        assert _parse_axis_pins(["--yyyy-mm-dd", "2024-03-15"]) == {
            "yyyy-mm-dd": ["2024-03-15"]
        }

    def test_duplicate_flag_raises(self):
        with pytest.raises(SystemExit, match="Duplicate axis flag"):
            _parse_axis_pins(["--sensor", "A", "--sensor", "B"])

    def test_value_with_no_preceding_flag_raises(self):
        with pytest.raises(SystemExit, match="Unrecognized argument"):
            _parse_axis_pins(["A"])

    def test_flag_with_no_values_raises(self):
        with pytest.raises(SystemExit, match="no values"):
            _parse_axis_pins(["--sensor"])


class TestParseArgsPinIntegration:
    """_parse_args() must return pins parsed from unrecognized argv, while
    still parsing every known flag normally."""

    def test_pins_after_path(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["juxt", "plots/{sensor}_{date}.png", "--sensor", "A", "B"],
        )
        args, pins = _parse_args()
        assert args.path == "plots/{sensor}_{date}.png"
        assert pins == {"sensor": ["A", "B"]}

    def test_no_pins_gives_empty_dict(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["juxt", "/some/dir"])
        args, pins = _parse_args()
        assert args.path == "/some/dir"
        assert pins == {}

    def test_known_flags_unaffected_by_pin_parsing(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["juxt", "plots/{sensor}.png", "--squeeze", "--sensor", "A"],
        )
        args, pins = _parse_args()
        assert args.squeeze is True
        assert pins == {"sensor": ["A"]}

    def test_pins_before_path_are_misparsed_by_argparse(self, monkeypatch):
        """Documented limitation: pin flags must follow PATH. Placed first,
        argparse's own (single, optional) positional grabs the first bare
        token as PATH instead of the real path — same trade-off broadcast_to_tmux
        makes for its command positional."""
        monkeypatch.setattr(
            "sys.argv",
            ["juxt", "--sensor", "A", "B", "plots/{sensor}.png"],
        )
        args, pins = _parse_args()
        assert args.path != "plots/{sensor}.png"


class TestPanelFlags:
    """--info / --status-bar / --keys set the panels' initial visibility.

    They are declared on the parser rather than left to _parse_axis_pins, so
    a bare --info stays a panel flag instead of being read as an axis pin.
    """

    def _args(self, monkeypatch, *argv):
        monkeypatch.setattr("sys.argv", ["juxt", *argv])
        args, _pins = _parse_args()
        return args

    def test_info_defaults_to_closed(self, monkeypatch):
        assert self._args(monkeypatch, "plots/").info is False

    def test_status_bar_defaults_to_shown(self, monkeypatch):
        assert self._args(monkeypatch, "plots/").status_bar is True

    def test_keys_defaults_to_closed(self, monkeypatch):
        assert self._args(monkeypatch, "plots/").keys is False

    def test_info_flag_opens_the_sidebar(self, monkeypatch):
        assert self._args(monkeypatch, "plots/", "--info").info is True

    def test_no_info_flag_keeps_it_closed(self, monkeypatch):
        assert self._args(monkeypatch, "plots/", "--info", "--no-info").info is False

    def test_no_status_bar_flag_hides_it(self, monkeypatch):
        assert self._args(monkeypatch, "plots/", "--no-status-bar").status_bar is False

    def test_status_bar_flag_shows_it(self, monkeypatch):
        args = self._args(monkeypatch, "plots/", "--no-status-bar", "--status-bar")
        assert args.status_bar is True

    def test_keys_flag_opens_the_sidebar(self, monkeypatch):
        assert self._args(monkeypatch, "plots/", "--keys").keys is True

    def test_no_keys_keeps_it_closed(self, monkeypatch):
        assert self._args(monkeypatch, "plots/", "--keys", "--no-keys").keys is False

    def test_panel_flags_are_not_swallowed_as_axis_pins(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv",
            ["juxt", "plots/{sensor}.png", "--info", "--sensor", "A"],
        )
        args, pins = _parse_args()
        assert args.info is True
        assert pins == {"sensor": ["A"]}


class TestVersionFlag:
    @pytest.mark.parametrize("flag", ["--version", "-V"])
    def test_prints_the_version_and_exits(self, monkeypatch, capsys, flag):
        import juxt

        monkeypatch.setattr("sys.argv", ["juxt", flag])
        with pytest.raises(SystemExit) as exc:
            _parse_args()
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == f"juxt {juxt.__version__}"

    def test_wins_over_a_path_and_pins(self, monkeypatch, capsys):
        monkeypatch.setattr("sys.argv", ["juxt", "plots/{sensor}.png", "--sensor", "A", "--version"])
        with pytest.raises(SystemExit) as exc:
            _parse_args()
        assert exc.value.code == 0
        assert capsys.readouterr().out.startswith("juxt ")

    def test_runs_without_opening_a_window(self):
        import subprocess
        import sys

        import juxt

        r = subprocess.run([sys.executable, "-m", "juxt", "--version"],
                           capture_output=True, text=True, timeout=60,
                           env={**__import__("os").environ, "QT_QPA_PLATFORM": "offscreen"})
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == f"juxt {juxt.__version__}"

    def test_is_in_the_help_text(self, capsys):
        from juxt.__main__ import _print_help

        _print_help()
        assert "--version" in capsys.readouterr().out


class TestListAxes:
    """--list-axes runs discovery, prints the axes, and exits without a GUI."""

    def _run(self, *argv, stdin=""):
        import os
        import subprocess
        import sys

        return subprocess.run(
            [sys.executable, "-m", "juxt", *argv],
            capture_output=True, text=True, timeout=60, input=stdin,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        )

    def test_flag_is_parsed_and_defaults_off(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["juxt", "plots/"])
        assert _parse_args()[0].list_axes is False
        monkeypatch.setattr("sys.argv", ["juxt", "plots/", "--list-axes"])
        assert _parse_args()[0].list_axes is True

    def test_is_not_swallowed_as_an_axis_pin(self, monkeypatch):
        monkeypatch.setattr(
            "sys.argv", ["juxt", "plots/{sensor}.png", "--list-axes", "--sensor", "A"])
        _args, pins = _parse_args()
        assert pins == {"sensor": ["A"]}

    def test_is_in_the_help_text(self, capsys):
        from juxt.__main__ import _print_help

        _print_help()
        assert "--list-axes" in capsys.readouterr().out

    def test_format_lists_every_value_aligned(self):
        from juxt.__main__ import _format_axes
        from juxt.config import Config

        values = [str(i) for i in range(25)]
        cfg = Config(template="p/{a}_{longer}.png",
                     axes={"a": ["x", "y"], "longer": values}, keys={})
        lines = _format_axes(cfg).splitlines()
        assert lines[0] == "template: p/{a}_{longer}.png"
        assert lines[2] == "a (2):        x  y"
        assert lines[3] == "longer (25):  " + "  ".join(values)

    def test_format_without_axes_is_just_the_template(self):
        from juxt.__main__ import _format_axes
        from juxt.config import Config

        assert _format_axes(Config(template="p.png", axes={}, keys={})) == "template: p.png"

    def test_directory_scan_prints_axes_without_prompting(self, flat_plot_dir):
        r = self._run(str(flat_plot_dir), "--list-axes")
        assert r.returncode == 0, r.stderr
        assert r.stdout.splitlines()[0] == f"template: {flat_plot_dir.as_posix()}/{{axis_0}}_{{axis_1}}.png"
        assert "axis_0 (2):  A  B" in r.stdout
        assert "axis_1 (2):  d1  d2" in r.stdout
        assert "Change separator" not in r.stdout

    def test_template_scan_uses_placeholder_names(self, flat_plot_dir):
        r = self._run(f"{flat_plot_dir}/{{sensor}}_{{date}}.png", "--list-axes")
        assert r.returncode == 0, r.stderr
        assert "sensor (2):  A  B" in r.stdout
        assert "date (2):    d1  d2" in r.stdout

    def test_pinned_values_are_listed_as_given(self, flat_plot_dir):
        r = self._run(f"{flat_plot_dir}/{{sensor}}_{{date}}.png", "--list-axes",
                      "--sensor", "A")
        assert r.returncode == 0, r.stderr
        assert "sensor (1):  A" in r.stdout

    def test_yaml_config_is_listed(self, flat_plot_dir, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            f"template: '{flat_plot_dir.as_posix()}/{{sensor}}_{{date}}.png'\n"
            "axes:\n  sensor: [A, B]\n  date: [d1, d2]\n"
        )
        r = self._run(str(cfg), "--list-axes")
        assert r.returncode == 0, r.stderr
        assert "sensor (2):  A  B" in r.stdout

    def test_needs_a_path(self):
        r = self._run("--list-axes")
        assert r.returncode == 1
        assert "needs a PATH" in r.stderr
        assert r.stdout == ""

    def test_bad_path_errors_on_stderr(self, tmp_path):
        r = self._run(str(tmp_path / "nope"), "--list-axes")
        assert r.returncode == 1
        assert r.stderr.startswith("Error:")
        assert r.stdout == ""

    def test_empty_directory_errors_on_stderr(self, tmp_path):
        r = self._run(str(tmp_path), "--list-axes")
        assert r.returncode == 1
        assert "No image files found" in r.stderr
