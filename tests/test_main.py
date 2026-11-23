"""Tests for juxt/__main__.py's argv parsing, in particular the --NAME VALUE
placeholder-pinning flags (see juxt.detect._axes_from_local_template)."""
from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import Qt

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

    def test_pattern_pins_are_parsed(self, monkeypatch):
        from juxt.detect import PinPattern
        monkeypatch.setattr(
            "sys.argv", ["juxt", "plots/{sensor}.png", "--sensor", "A", "B*", "re:C.+"])
        _args, pins = _parse_args()
        literal, glob, regex = pins["sensor"]
        assert literal == "A"
        assert isinstance(glob, PinPattern) and isinstance(regex, PinPattern)

    def test_invalid_regex_pin_exits(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["juxt", "plots/{sensor}.png", "--sensor", "re:("])
        with pytest.raises(SystemExit, match="Invalid regex"):
            _parse_args()

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

    def test_info_flag_unset_defers_to_settings(self, monkeypatch):
        assert self._args(monkeypatch, "plots/").info is None

    def test_status_bar_defaults_to_shown(self, monkeypatch):
        assert self._args(monkeypatch, "plots/").status_bar is True

    def test_keys_defaults_to_closed(self, monkeypatch):
        assert self._args(monkeypatch, "plots/").keys is False

    def test_info_flag_opens_the_sidebar(self, monkeypatch):
        assert self._args(monkeypatch, "plots/", "--info").info is True

    def test_no_info_flag_closes_it(self, monkeypatch):
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


class TestSubsetInteractive:
    """--subset-interactive: tick the values to load before any image is read."""

    def test_flag_is_parsed_and_defaults_off(self, monkeypatch):
        from juxt.__main__ import _parse_args
        monkeypatch.setattr("sys.argv", ["juxt", "plots/"])
        assert _parse_args()[0].subset_interactive is False
        monkeypatch.setattr("sys.argv", ["juxt", "plots/{sensor}.png", "--subset-interactive", "--sensor", "A"])
        args, pins = _parse_args()
        assert args.subset_interactive is True
        assert pins == {"sensor": ["A"]}

    def test_flag_is_in_help_and_completion(self, capsys):
        from juxt.__main__ import _print_help
        from juxt.complete import CLI_OPTIONS
        _print_help()
        assert "--subset-interactive" in capsys.readouterr().out
        assert "--subset-interactive" in CLI_OPTIONS

    def _pick(self, qtbot, monkeypatch, ticked):
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QDialog
        from juxt.__main__ import _pick_startup_values
        from juxt.config import Config
        from juxt.dialogs import ValuesDialog
        seen = {}

        def fake_exec(self):
            seen["title"] = self.windowTitle()
            seen["loading"] = self.loading
            seen["ticked_at_open"] = self.values()
            if ticked is None:
                return QDialog.DialogCode.Rejected
            for p in self.panels:
                for i in range(p.value_list.count()):
                    it = p.value_list.item(i)
                    on = it.data(Qt.ItemDataRole.UserRole) in ticked
                    it.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(ValuesDialog, "exec", fake_exec)
        cfg = Config(template="p/{s}_{d}.png", axes={"s": ["A", "B"], "d": ["1", "2"]},
                     keys={"s": "s", "d": "d"})
        out = _pick_startup_values(cfg, QIcon(), QApplication.primaryScreen())
        return out, seen

    def test_picker_starts_all_ticked_and_narrows_the_config(self, qtbot, monkeypatch):
        out, seen = self._pick(qtbot, monkeypatch, ticked={"B", "1", "2"})
        assert seen["ticked_at_open"] == {"s": ["A", "B"], "d": ["1", "2"]}
        assert seen["loading"] is False          # discovery already listed everything
        assert out.axes == {"s": ["B"], "d": ["1", "2"]}
        assert out.keys == {"s": "s", "d": "d"}
        assert out.template == "p/{s}_{d}.png"

    def test_cancel_quits(self, qtbot, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._pick(qtbot, monkeypatch, ticked=None)
        assert exc.value.code == 0


class TestPinsPreselectInThePicker:
    """With --subset-interactive, --NAME VALUE ticks values instead of hiding the rest."""

    @pytest.fixture
    def tree(self, tmp_path):
        d = tmp_path / "plots"
        d.mkdir()
        for s in ("A", "B", "C"):
            for t in ("d1", "d2"):
                (d / f"{s}_{t}.png").write_bytes(b"")
        return d

    def _args(self, path):
        import argparse
        return argparse.Namespace(path=path, auto=True, separator=None,
                                  max_depth=None, full_path=False)

    def test_pins_become_the_preselection(self, tree):
        from juxt.__main__ import _resolve_for_picker
        cfg, pre = _resolve_for_picker(
            self._args(f"{tree.as_posix()}/{{sensor}}_{{date}}.png"),
            {"sensor": ["A", "C"]}, None)
        assert cfg.axes == {"sensor": ["A", "B", "C"], "date": ["d1", "d2"]}
        assert pre == {"sensor": ["A", "C"]}

    def test_pattern_pins_preselect_their_matches(self, tree):
        from juxt.__main__ import _resolve_for_picker
        from juxt.detect import parse_pins
        cfg, pre = _resolve_for_picker(
            self._args(f"{tree.as_posix()}/{{sensor}}_{{date}}.png"),
            parse_pins({"sensor": ["C", "[AB]"]}), None)
        assert cfg.axes["sensor"] == ["A", "B", "C"]
        assert pre == {"sensor": ["C", "A", "B"]}

    def test_no_pins_no_preselection(self, tree):
        from juxt.__main__ import _resolve_for_picker
        cfg, pre = _resolve_for_picker(
            self._args(f"{tree.as_posix()}/{{sensor}}_{{date}}.png"), {}, None)
        assert cfg.axes["sensor"] == ["A", "B", "C"]
        assert pre == {}

    def test_pins_with_a_slash_keep_narrowing(self, tree, tmp_path):
        from juxt.__main__ import _resolve_for_picker
        root = tree.as_posix()
        cfg, pre = _resolve_for_picker(
            self._args("{root}/{sensor}_{date}.png"),
            {"root": [root], "sensor": ["B"]}, None)
        assert cfg.axes["root"] == [root]
        assert cfg.axes["sensor"] == ["A", "B", "C"]
        assert pre == {"sensor": ["B"]}

    def test_falls_back_to_pinned_discovery(self, tree):
        """A placeholder before a '*' cannot be discovered without its pin."""
        from juxt.__main__ import _resolve_for_picker
        cfg, pre = _resolve_for_picker(
            self._args("{root}/*.png"), {"root": [tree.as_posix()]}, None)
        assert cfg.axes["root"] == [tree.as_posix()]
        assert pre == {}

    def test_picker_ticks_the_pins_and_offers_the_rest(self, qtbot, monkeypatch):
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication, QDialog
        from juxt.__main__ import _pick_startup_values
        from juxt.config import Config
        from juxt.dialogs import ValuesDialog
        seen = {}

        def fake_exec(self):
            seen["ticked"] = self.values()
            p = self.panel("s")
            seen["rows"] = [p.value_list.item(i).data(Qt.ItemDataRole.UserRole)
                            for i in range(p.value_list.count())]
            return QDialog.DialogCode.Accepted
        monkeypatch.setattr(ValuesDialog, "exec", fake_exec)
        cfg = Config(template="p/{s}_{d}.png",
                     axes={"s": ["A", "B", "C"], "d": ["1", "2"]}, keys={})
        out = _pick_startup_values(cfg, QIcon(), QApplication.primaryScreen(),
                                   {"s": ["A", "C"]})
        assert seen["rows"] == ["A", "B", "C"]
        assert seen["ticked"] == {"s": ["A", "C"], "d": ["1", "2"]}
        assert out.axes == {"s": ["A", "C"], "d": ["1", "2"]}


@pytest.mark.skipif(sys.platform == "win32", reason="SIGINT delivery is POSIX-only")
class TestCtrlC:
    """Ctrl+C quits juxt from a dialogue too, not only from the viewer: the
    hook is in before the first dialogue opens, and it closes modal ones."""

    def test_quits_from_the_startup_value_picker(self, tmp_path):
        import os
        import signal as _signal
        import subprocess
        import time
        from conftest import make_png

        for s in ("A", "B"):
            (tmp_path / f"{s}.png").write_bytes(make_png())
        log = tmp_path / "juxt.log"
        proc = subprocess.Popen(
            [sys.executable, "-m", "juxt", f"{tmp_path.as_posix()}/{{s}}.png",
             "--subset-interactive"],
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen",
                 "JUXT_LOG_LEVEL": "DEBUG", "JUXT_LOG_FILE": str(log)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if log.exists() and "startup value picker open" in log.read_text():
                    break
                assert proc.poll() is None, "juxt exited before the picker opened"
                time.sleep(0.05)
            else:
                pytest.fail("the value picker never opened")
            time.sleep(0.2)   # let exec() enter its event loop
            proc.send_signal(_signal.SIGINT)
            assert proc.wait(timeout=10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()


class TestDiscoveryBusyBox:
    """Startup discovery runs in a worker, with a busy box once it takes a while."""

    class _Screen:
        @staticmethod
        def geometry():
            from PySide6.QtCore import QRect
            return QRect(0, 0, 800, 600)

    def _run(self, fn, ask=lambda label: None, delay_ms=300, interrupted=None):
        from PySide6.QtGui import QIcon
        from juxt.__main__ import _discover_with_busy
        return _discover_with_busy(
            fn, ask, app_icon=QIcon(), screen=self._Screen(),
            interrupted=interrupted or [False], delay_ms=delay_ms)

    @staticmethod
    def _busy_boxes():
        from PySide6.QtWidgets import QApplication, QProgressDialog
        return [w for w in QApplication.topLevelWidgets()
                if isinstance(w, QProgressDialog) and w.isVisible()]

    def test_returns_the_result_without_a_box_when_quick(self, qtbot):
        import threading
        seen = {}

        def fn(_ask):
            seen["thread"] = threading.current_thread()
            return "config"
        assert self._run(fn) == "config"
        assert seen["thread"] is not threading.main_thread()
        assert self._busy_boxes() == []

    def test_box_shows_while_slow_and_closes_after(self, qtbot):
        import time
        shown = []

        def fn(_ask):
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline and not shown:
                time.sleep(0.02)
            return 42
        from PySide6.QtCore import QTimer
        poll = QTimer()
        poll.timeout.connect(lambda: shown.extend(
            b.labelText() for b in self._busy_boxes()))
        poll.start(20)
        assert self._run(fn, delay_ms=50) == 42
        poll.stop()
        assert shown and shown[0] == "Collecting values…"
        qtbot.waitUntil(lambda: self._busy_boxes() == [])

    def test_errors_are_raised_on_the_main_thread(self, qtbot):
        def fn(_ask):
            raise ValueError("no files")
        with pytest.raises(ValueError, match="no files"):
            self._run(fn)

    def test_password_is_asked_on_the_main_thread(self, qtbot):
        import threading
        asked = []

        def ask(label):
            asked.append((label, threading.current_thread() is threading.main_thread()))
            return "secret"
        assert self._run(lambda a: a("me@host"), ask) == "secret"
        assert asked == [("me@host", True)]

    @pytest.mark.parametrize("how", ["button", "escape"])
    def test_cancel_quits(self, qtbot, how):
        import threading
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QPushButton
        release = threading.Event()

        def cancel():
            boxes = self._busy_boxes()
            if not boxes:
                QTimer.singleShot(20, cancel)
            elif how == "button":
                boxes[0].findChild(QPushButton).click()
            else:
                qtbot.keyClick(boxes[0], Qt.Key.Key_Escape)
        QTimer.singleShot(20, cancel)
        try:
            with pytest.raises(SystemExit) as exc:
                self._run(lambda _a: release.wait(5), delay_ms=0)
            assert exc.value.code == 0
        finally:
            release.set()

    def test_ctrl_c_flag_quits(self, qtbot):
        import threading
        from PySide6.QtCore import QTimer
        release = threading.Event()
        interrupted = [False]
        QTimer.singleShot(50, lambda: interrupted.__setitem__(0, True))
        try:
            with pytest.raises(SystemExit):
                self._run(lambda _a: release.wait(5), interrupted=interrupted)
        finally:
            release.set()


class TestMayPrompt:
    def _args(self, path, auto=False):
        import argparse
        return argparse.Namespace(path=path, auto=auto)

    def test_plain_directory_may_prompt(self, tmp_path):
        from juxt.__main__ import _may_prompt
        assert _may_prompt(self._args(str(tmp_path)))
        assert not _may_prompt(self._args(str(tmp_path), auto=True))

    def test_templates_and_remote_templates_do_not(self, tmp_path):
        from juxt.__main__ import _may_prompt
        assert not _may_prompt(self._args(f"{tmp_path}/{{s}}.png"))
        assert not _may_prompt(self._args("host:/data/{s}.png"))
        assert _may_prompt(self._args("host:/data"))
