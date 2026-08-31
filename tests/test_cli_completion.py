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

    PATH starts out empty so each test states exactly which helper it offers.
    """
    quoted = " ".join(f'"{w}"' for w in words)
    script = f'''
        set -u
        export PATH=/nonexistent
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
