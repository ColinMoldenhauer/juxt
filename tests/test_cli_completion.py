"""Tests for the `juxt-complete` helper and the bash completion function."""
from __future__ import annotations

import ast
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from juxt.complete import CLI_OPTIONS, bash_script, cli_complete, main

REPO_ROOT = Path(__file__).resolve().parent.parent
COMPLETION_BASH = REPO_ROOT / "juxt" / "completion.bash"


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

@pytest.fixture
def in_plot_dir(nested_plot_dir, monkeypatch):
    """Run with the parent of plots/ as the working directory."""
    monkeypatch.chdir(nested_plot_dir.parent)
    return nested_plot_dir.name


class TestCliComplete:
    def test_completes_a_directory(self, in_plot_dir):
        assert cli_complete("plot") == ["plots/"]

    def test_words_keep_the_typed_placeholders(self, in_plot_dir):
        words = cli_complete("plots/{sensor}/")
        assert words == ["plots/{sensor}/AM/", "plots/{sensor}/PM/"]

    def test_placeholders_merge_the_subtrees(self, in_plot_dir):
        words = cli_complete("plots/{sensor}/{overpass}/d")
        assert words == ["plots/{sensor}/{overpass}/d1.png",
                         "plots/{sensor}/{overpass}/d2.png"]

    def test_trailing_placeholder_offers_one_shared_completion(self, in_plot_dir):
        assert cli_complete("plots/{sensor}") == ["plots/{sensor}/"]

    def test_option_names_are_completed(self):
        assert cli_complete("--gr") == ["--grid", "--grid-layout", "--grid-values"]

    def test_unknown_option_completes_to_nothing(self):
        assert cli_complete("--zz") == []

    def test_opaque_option_values_are_left_alone(self, in_plot_dir):
        assert cli_complete("", "--grid") == []
        assert cli_complete("plot", "--axis-h") == []

    def test_save_takes_a_path(self, in_plot_dir):
        assert cli_complete("plot", "--save") == ["plots/"]

    def test_word_split_on_a_colon_is_not_completed(self, in_plot_dir):
        assert cli_complete("/plots", ":") == []

    def test_remote_paths_are_left_to_the_app(self, in_plot_dir):
        assert cli_complete("myhost:/data/") == []

    def test_option_list_matches_the_argparse_parser(self):
        """CLI_OPTIONS is a copy of the parser's flags — catch any drift."""
        source = (REPO_ROOT / "juxt" / "__main__.py").read_text(encoding="utf-8")
        declared = {
            arg.value
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
            for arg in node.args
            if isinstance(arg, ast.Constant) and str(arg.value).startswith("-")
        }
        assert declared == set(CLI_OPTIONS)


class TestHelperCommand:
    def test_prints_candidates_one_per_line(self, in_plot_dir, capsys):
        assert main(["--", "", "plots/{sensor}/"]) == 0
        assert capsys.readouterr().out.split() == [
            "plots/{sensor}/AM/", "plots/{sensor}/PM/",
        ]

    def test_single_argument_is_the_current_word(self, in_plot_dir, capsys):
        assert main(["plot"]) == 0
        assert capsys.readouterr().out.strip() == "plots/"

    def test_bash_prints_the_completion_script(self, capsys):
        assert main(["--bash"]) == 0
        assert "complete -o nospace -F _juxt_completion juxt" in capsys.readouterr().out

    def test_complete_words_flag(self, in_plot_dir, capsys):
        assert main(["--complete-words", "", "plot"]) == 0
        assert capsys.readouterr().out.strip() == "plots/"

    def test_bash_completion_flag_is_an_alias(self, capsys):
        assert main(["--bash-completion"]) == 0
        assert capsys.readouterr().out == bash_script()

    def test_juxt_itself_answers_without_importing_qt(self, nested_plot_dir):
        """The `juxt --complete-words` fast path must stay ahead of the Qt import."""
        import juxt

        env = {**os.environ,
               "PYTHONPATH": str(Path(juxt.__file__).resolve().parent.parent)}
        out = subprocess.run(
            [sys.executable, "-X", "importtime", "-m", "juxt",
             "--complete-words", "", "plots/{sensor}/"],
            cwd=nested_plot_dir.parent, text=True, capture_output=True, env=env,
        )
        assert out.returncode == 0
        assert out.stdout.split() == ["plots/{sensor}/AM/", "plots/{sensor}/PM/"]
        assert "PySide6" not in out.stderr   # -X importtime lists every import

    def test_output_is_lf_only(self, nested_plot_dir):
        """A text-mode stdout on Windows would end every candidate with a CR.

        Captured as bytes on purpose: `text=True` applies universal newlines,
        which rewrites the CRLF away before an assertion can see it — which is
        exactly why this went unnoticed until a candidate carrying a literal
        ^M turned up on the command line.
        """
        import juxt

        env = {**os.environ,
               "PYTHONPATH": str(Path(juxt.__file__).resolve().parent.parent)}
        out = subprocess.run(
            [sys.executable, "-m", "juxt.complete", "--", "", "plots/{sensor}/"],
            cwd=nested_plot_dir.parent, capture_output=True, env=env, check=True,
        )
        assert out.stdout == b"plots/{sensor}/AM/\nplots/{sensor}/PM/\n"

    def test_comments_carry_no_stray_quotes(self):
        """The script is eval'd, so an odd quote anywhere breaks the parse."""
        for n, line in enumerate(bash_script().splitlines(), 1):
            if not line.lstrip().startswith("#"):
                continue
            assert "`" not in line, f"backtick in comment on line {n}"
            assert line.count("'") % 2 == 0, f"unbalanced ' in comment on line {n}"
            assert line.count('"') % 2 == 0, f'unbalanced " in comment on line {n}'

    def test_bash_script_is_the_shipped_file(self):
        assert bash_script() == COMPLETION_BASH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The bash function itself
# ---------------------------------------------------------------------------

def _git_bash_candidate() -> str | None:
    """The bash.exe shipped next to git.exe on Windows.

    Windows Server images also ship a `bash.exe` stub in System32 that only
    launches WSL, and fails outright when no distribution is installed. If it
    sits earlier on PATH than Git's own bash, `shutil.which("bash")` picks it
    up instead, so it has to be ruled out explicitly.
    """
    git = shutil.which("git")
    if git is None:
        return None
    candidate = Path(git).resolve().parent.parent / "bin" / "bash.exe"
    return str(candidate) if candidate.is_file() else None


def _find_bash() -> str | None:
    """A bash that actually runs, preferring Git's over whatever is on PATH."""
    candidates = [shutil.which("bash")]
    if sys.platform == "win32":
        candidates.insert(0, _git_bash_candidate())
    for candidate in dict.fromkeys(c for c in candidates if c):
        try:
            subprocess.run([candidate, "-c", "true"], check=True,
                           capture_output=True, timeout=10)
        except (subprocess.CalledProcessError, OSError):
            continue
        return candidate
    return None


BASH = _find_bash()
bash_required = pytest.mark.skipif(BASH is None,
                                   reason="no working POSIX bash found")


def _run_completion(cwd: Path, words: list[str], setup: str = "") -> list[str]:
    """Source completion.bash, complete *words*, and return COMPREPLY.

    PATH starts out empty and JUXT_COMPLETE is cleared, so each test states
    exactly which helper it offers.
    """
    quoted = " ".join(f'"{w}"' for w in words)
    script = f'''
        set -u
        export PATH=/nonexistent
        unset JUXT_COMPLETE
        {setup}
        source "{COMPLETION_BASH}"
        COMP_WORDS=({quoted})
        COMP_CWORD={len(words) - 1}
        _juxt_completion
        printf "%s\\n" "${{COMPREPLY[@]}}"
    '''
    out = subprocess.run([BASH, "-c", script], cwd=cwd, text=True,
                         capture_output=True, check=True)
    return [line for line in out.stdout.split("\n") if line != ""]


def _stub(path: Path, body: str) -> str:
    """Write an executable shell stub and return its path."""
    path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def _python_stub(path: Path, args: str) -> str:
    """A stub that runs juxt's completion out of the source tree."""
    import juxt

    pkg_parent = Path(juxt.__file__).resolve().parent.parent
    return _stub(path, f'PYTHONPATH="{pkg_parent}" exec "{sys.executable}" '
                       f'-m juxt.complete {args}"$@"')


@pytest.fixture
def helper(tmp_path):
    """A stand-in for the installed `juxt-complete` console script."""
    return _python_stub(tmp_path / "juxt-complete", "")


def _run_bash(cwd: Path, script: str, check: bool = True) -> str:
    """Run *script* under bash and return its stdout."""
    out = subprocess.run([BASH, "-c", script], cwd=cwd, text=True,
                         capture_output=True, check=check)
    return out.stdout


# Stubs standing in for the parts of the ble.sh API the hook touches.
BLE_STUBS = r"""
function ble/complete/cand/yield { printf '%s|%s\n' "$1" "$2"; }
function ble/complete/action/complete.addtail { printf 'tail=[%s]\n' "$1"; }
"""


@bash_required
class TestBleShHook:
    """ble.sh bypasses the compspec, so juxt ships a hook for it as well.

    What matters is that the hook completes the word as typed (COMPS) rather
    than the brace-mangled value ble.sh derives from it.
    """

    def _hook(self, cwd: Path, comps: str, helper: str,
              check: bool = True) -> list[str]:
        script = f'''
            set -u
            export PATH=/nonexistent
            export JUXT_COMPLETE="{helper}"
            source "{COMPLETION_BASH}"
            {BLE_STUBS}
            COMPS={shlex.quote(comps)}
            COMPV="brace-mangled-value-the-hook-must-ignore"
            comp_words=(juxt {shlex.quote(comps)})
            comp_cword=1
            ble/cmdinfo/complete:juxt
        '''
        return [line for line in _run_bash(cwd, script, check).split("\n") if line]

    def test_completes_the_word_as_typed(self, nested_plot_dir, helper):
        yielded = self._hook(nested_plot_dir.parent, "plots/{sensor}/", helper)
        assert yielded == ["juxt|plots/{sensor}/AM/", "juxt|plots/{sensor}/PM/"]

    def test_yields_nothing_when_nothing_matches(self, nested_plot_dir, helper):
        # The hook returns non-zero so ble.sh falls back to its own sources.
        assert self._hook(nested_plot_dir.parent, "plots/{sensor}/zz", helper,
                          check=False) == []

    def test_only_a_finished_word_is_closed_with_a_space(self, nested_plot_dir):
        script = f'''
            source "{COMPLETION_BASH}"
            {BLE_STUBS}
            CAND="plots/{{sensor}}/"      ble/complete/action:juxt/complete
            CAND="plots/{{date}}_"        ble/complete/action:juxt/complete
            CAND="plots/{{sensor}}/x.png" ble/complete/action:juxt/complete
        '''
        assert _run_bash(nested_plot_dir.parent, script).splitlines() == ["tail=[ ]"]


@bash_required
class TestBashFunction:
    @staticmethod
    def _with_helper(helper: str) -> str:
        return f'export JUXT_COMPLETE="{helper}"'

    def test_completes_below_a_placeholder(self, nested_plot_dir, helper):
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "plots/{sensor}/"],
                                self._with_helper(helper))
        assert reply == ["plots/{sensor}/AM/", "plots/{sensor}/PM/"]

    def test_directories_do_not_get_a_trailing_space(self, nested_plot_dir, helper):
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "plot"],
                                self._with_helper(helper))
        assert reply == ["plots/"]

    def test_a_finished_word_gets_a_trailing_space(self, nested_plot_dir, helper):
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "plots/A/AM/d1"],
                                self._with_helper(helper))
        assert reply == ["plots/A/AM/d1.png "]

    def test_a_word_stopping_on_a_boundary_stays_open(self, tmp_path, helper):
        """Completing to `{date}_` must not close the word with a space."""
        plots = tmp_path / "plots"
        plots.mkdir()
        for date, level in (("2024-03-15", "L2"), ("2024-03-16", "L3")):
            (plots / f"{date}_{level}.png").write_bytes(b"")
        reply = _run_completion(tmp_path, ["juxt", "plots/{yyyy-mm-dd}"],
                                self._with_helper(helper))
        assert reply == ["plots/{yyyy-mm-dd}_"]

    def test_a_crlf_helper_yields_clean_candidates(self, nested_plot_dir, tmp_path):
        """An older or foreign helper answering with CRLF must not leak the CR.

        Only the last line loses its CR to command substitution, so the bug
        shows up on every candidate but the final one.
        """
        crlf = _stub(tmp_path / "juxt-complete",
                     r"printf 'plots/{sensor}/AM/\r\nplots/{sensor}/PM/\r\n'")
        # Bracketed and captured as bytes: _run_completion decodes with
        # universal newlines, which would swallow the very CR under test.
        script = f'''
            set -u
            export PATH=/nonexistent
            export JUXT_COMPLETE="{crlf}"
            source "{COMPLETION_BASH}"
            COMP_WORDS=(juxt "plots/{{sensor}}/")
            COMP_CWORD=1
            _juxt_completion
            printf "[%s]" "${{COMPREPLY[@]}}"
        '''
        out = subprocess.run([BASH, "-c", script], cwd=nested_plot_dir.parent,
                             capture_output=True, check=True).stdout
        assert out == b"[plots/{sensor}/AM/][plots/{sensor}/PM/]"

    def test_options_are_completed(self, nested_plot_dir, helper):
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "--squ"],
                                self._with_helper(helper))
        assert reply == ["--squeeze "]

    def test_juxt_itself_backs_the_completion(self, nested_plot_dir, tmp_path):
        """Without juxt-complete on PATH, `juxt --complete-words` is used."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _python_stub(bin_dir / "juxt", "--complete-words ")
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "plots/{sensor}/"],
                                f'export PATH="{bin_dir}"')
        assert reply == ["plots/{sensor}/AM/", "plots/{sensor}/PM/"]

    def test_an_older_juxt_falls_back_to_filenames(self, nested_plot_dir, tmp_path):
        """A juxt that rejects --complete-words must not swallow Tab."""
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        _stub(bin_dir / "juxt", 'echo "unrecognized arguments" >&2; exit 2')
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "plot"],
                                f'export PATH="{bin_dir}"')
        assert reply == ["plots"]

    def test_falls_back_to_filenames_without_any_helper(self, nested_plot_dir):
        reply = _run_completion(nested_plot_dir.parent, ["juxt", "plot"])
        assert reply == ["plots"]


# ---------------------------------------------------------------------------
# Option values: --NAME pins, axis names, --grid-values
# ---------------------------------------------------------------------------

@pytest.fixture
def uneven_dir(tmp_path):
    """sensor/model/date.png where sensor B has no model m2 and A no m3."""
    layout = {"A": ["m1", "m2"], "B": ["m1", "m3"]}
    for sensor, models in layout.items():
        for model in models:
            for date in ("d1", "d2"):
                path = tmp_path / "plots" / sensor / model / f"{date}.png"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"")
    return tmp_path


UNEVEN = "plots/{sensor}/{model}/{date}.png"


class TestOptionValueCompletion:
    def test_template_placeholders_are_offered_as_pin_flags(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("--m", words=[UNEVEN]) == ["--model", "--max-depth"]
        assert cli_complete("--s", words=[UNEVEN])[0] == "--sensor"

    def test_a_flag_already_used_is_not_offered_again(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert "--sensor" not in cli_complete("--", words=[UNEVEN, "--sensor", "A"])
        assert "--model" in cli_complete("--", words=[UNEVEN, "--sensor", "A"])

    def test_a_placeholder_named_like_an_option_stays_that_option(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert cli_complete("--na", words=["plots/{name}.png"]) == ["--name"]

    def test_pin_values_are_discovered(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("", "--sensor", [UNEVEN, "--sensor"]) == ["A", "B"]

    def test_pins_narrow_the_other_placeholders(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--sensor", "A", "--model"]
        assert cli_complete("", "--model", words) == ["m1", "m2"]
        words = [UNEVEN, "--sensor", "B", "--model"]
        assert cli_complete("", "--model", words) == ["m1", "m3"]

    def test_unpinned_placeholders_are_not_narrowed(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("", "--model", [UNEVEN, "--model"]) == ["m1", "m2", "m3"]

    def test_a_flags_own_pins_do_not_narrow_itself(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--sensor", "A", "--sensor"]
        assert cli_complete("", "--sensor", words) == ["A", "B"]

    def test_values_continue_across_a_list_and_skip_the_ones_typed(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--model", "m1", "m3"]
        assert cli_complete("", "m3", words) == ["m2"]

    def test_the_typed_prefix_filters(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("m", "--model", [UNEVEN, "--model"]) == ["m1", "m2", "m3"]
        assert cli_complete("m2", "--model", [UNEVEN, "--model"]) == ["m2"]

    def test_nothing_left_to_offer_is_not_a_cue_for_filenames(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--sensor", "A", "B"]
        assert cli_complete("", "B", words) == []

    def test_a_quoted_template_is_understood(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("", "--sensor", [f'"{UNEVEN}"', "--sensor"]) == ["A", "B"]
        assert cli_complete("", "--sensor", [f"'{UNEVEN}'", "--sensor"]) == ["A", "B"]

    def test_options_before_the_template_are_skipped(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = ["-a", "--max-depth", "3", "--name", "run", UNEVEN, "--sensor"]
        assert cli_complete("", "--sensor", words) == ["A", "B"]

    def test_values_needing_quotes_are_quoted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "plots" / "run 1").mkdir(parents=True)
        (tmp_path / "plots" / "run 1" / "x.png").write_bytes(b"")
        (tmp_path / "plots" / "run2").mkdir()
        (tmp_path / "plots" / "run2" / "x.png").write_bytes(b"")
        got = cli_complete("", "--run", ["plots/{run}/x.png", "--run"])
        assert got == ["'run 1'", "run2"]

    def test_a_pinned_value_may_be_a_path_the_scan_cannot_find(self, tmp_path, monkeypatch):
        """`--root` can name unrelated trees, so an empty scan falls back to paths."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "run1").mkdir()
        (tmp_path / "run2").mkdir()
        got = cli_complete("ru", "--root", ["{root}/*.png", "--root"])
        assert got == ["run1/", "run2/"]

    def test_remote_and_directory_templates_offer_no_values(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        from juxt.complete import discover_values
        assert discover_values("host:/data/{sensor}.png", "sensor", {}) == []
        assert discover_values("plots", "sensor", {}) == []
        assert discover_values("plots/{sensor}/none/{x}.png", "sensor", {}) == []

    def test_recursive_placeholders_are_discovered(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        got = cli_complete("", "--plot", ["{root}/{**plot}.png", "--root", "plots/A", "--plot"])
        assert got == ["m1/d1", "m1/d2", "m2/d1", "m2/d2"]


class TestAxisNameCompletion:
    @pytest.mark.parametrize("option", ["--axis-h", "--axis-v", "--grid"])
    def test_names_come_from_the_template(self, uneven_dir, monkeypatch, option):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("", option, [UNEVEN, option]) == ["sensor", "model", "date"]
        assert cli_complete("d", option, [UNEVEN, option]) == ["date"]

    def test_names_need_a_template(self):
        assert cli_complete("", "--axis-h", ["--axis-h"]) == []

    def test_only_one_name_is_taken(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--axis-h", "sensor"]
        assert not {"sensor", "model", "date"} & set(cli_complete("", "sensor", words))

    def test_a_dash_after_an_option_with_a_value_offers_nothing(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("-", "--axis-h", [UNEVEN, "--axis-h"]) == []

    def test_grid_values_follow_the_grid_axis(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--grid", "model", "--grid-values"]
        assert cli_complete("", "--grid-values", words) == ["m1", "m2", "m3"]
        words = [UNEVEN, "--grid", "model", "--grid-values", "m2"]
        assert cli_complete("", "m2", words) == ["m1", "m3"]

    def test_grid_values_respect_pins(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        words = [UNEVEN, "--sensor", "A", "--grid", "model", "--grid-values"]
        assert cli_complete("", "--grid-values", words) == ["m1", "m2"]

    def test_grid_values_without_a_grid_axis_offer_nothing(self, uneven_dir, monkeypatch):
        monkeypatch.chdir(uneven_dir)
        assert cli_complete("", "--grid-values", [UNEVEN, "--grid-values"]) == []


@pytest.mark.skipif(not hasattr(__import__("signal"), "setitimer"),
                    reason="the time budget needs POSIX interval timers")
class TestDiscoveryBudget:
    def test_a_slow_scan_is_abandoned(self, uneven_dir, monkeypatch):
        import time

        import juxt.detect
        from juxt.complete import discover_values

        monkeypatch.chdir(uneven_dir)
        monkeypatch.setattr(juxt.detect, "_axes_from_local_template",
                            lambda *a, **k: time.sleep(5))
        start = time.perf_counter()
        assert discover_values(UNEVEN, "sensor", {}, timeout=0.05) == []
        assert time.perf_counter() - start < 2

    def test_the_budget_can_be_set_from_the_environment(self, uneven_dir, monkeypatch):
        import time

        import juxt.detect
        from juxt.complete import discover_values

        monkeypatch.chdir(uneven_dir)
        monkeypatch.setenv("JUXT_COMPLETE_TIMEOUT", "0.05")
        monkeypatch.setattr(juxt.detect, "_axes_from_local_template",
                            lambda *a, **k: time.sleep(5))
        start = time.perf_counter()
        assert discover_values(UNEVEN, "sensor", {}) == []
        assert time.perf_counter() - start < 2

    def test_the_timer_is_cleared_afterwards(self, uneven_dir, monkeypatch):
        import signal

        from juxt.complete import discover_values

        monkeypatch.chdir(uneven_dir)
        assert discover_values(UNEVEN, "sensor", {}, timeout=5) == ["A", "B"]
        assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


class TestHelperCommandWithWords:
    def test_the_whole_line_reaches_cli_complete(self, uneven_dir, monkeypatch, capsys):
        monkeypatch.chdir(uneven_dir)
        assert main(["--", UNEVEN, "--sensor", "A", "--model", ""]) == 0
        assert capsys.readouterr().out.split() == ["m1", "m2"]


@bash_required
class TestBashPassesTheWholeLine:
    def test_bash_function(self, uneven_dir, helper):
        reply = _run_completion(
            uneven_dir, ["juxt", f"'{UNEVEN}'", "--sensor", "A", "--model", ""],
            f'export JUXT_COMPLETE="{helper}"')
        assert reply == ["m1", "m2"]

    def test_bash_function_with_options_before_the_template(self, uneven_dir, helper):
        reply = _run_completion(
            uneven_dir, ["juxt", "-a", UNEVEN, "--sensor", ""],
            f'export JUXT_COMPLETE="{helper}"')
        assert reply == ["A", "B"]

    def test_a_single_value_is_closed_with_a_space(self, uneven_dir, helper):
        reply = _run_completion(
            uneven_dir, ["juxt", UNEVEN, "--sensor", "B", "--model", "m3"],
            f'export JUXT_COMPLETE="{helper}"')
        assert reply == ["m3 "]

    def test_ble_hook(self, uneven_dir, helper):
        script = f'''
            set -u
            export PATH=/nonexistent
            export JUXT_COMPLETE="{helper}"
            source "{COMPLETION_BASH}"
            {BLE_STUBS}
            COMPS=""
            comp_words=(juxt {shlex.quote(UNEVEN)} --sensor B --model "")
            comp_cword=5
            ble/cmdinfo/complete:juxt
        '''
        yielded = [l for l in _run_bash(uneven_dir, script).split("\n") if l]
        assert yielded == ["juxt|m1", "juxt|m3"]
