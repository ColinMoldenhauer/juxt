"""Scripted juxt CLI session meant to be recorded with asciinema.

Spawns a real interactive bash (juxt's shell completion loaded) in a
pseudo-terminal, "types" a scene into it with human-like pacing and forwards
everything bash prints to stdout. So what asciinema records is genuine Tab
completion and the genuine axis-naming prompt, not a mock-up. Run it directly
to preview a scene in your own terminal.

Easiest way to record it: `scripts/record_demo.sh` (see `--help`).

Scenes
------
    completion  build a {placeholder} template with Tab, then pin values with --NAME
    detect      `juxt plots/ --save demo.yaml`: name the detected axes, show the YAML
    all         both, one after the other

Each scene runs in a throwaway directory holding copies of `examples/sample_plots`
(as `plots/`) and `examples/sample_plots_nested` (as `results/`), with `HOME`
pointing there too, so personal settings and history never leak into a recording.
The viewer window that `detect` launches runs on Qt's offscreen platform and is
closed with Ctrl+C once the config is saved.

Setup (Linux/macOS; on Windows use WSL)
---------------------------------------
    sudo apt install -y asciinema            # or: brew install asciinema
    cargo install --git https://github.com/asciinema/agg
    #   ...or download a binary from https://github.com/asciinema/agg/releases
    pip install -e .                         # juxt itself, from the repo root

Manual recording (what record_demo.sh does)
-------------------------------------------
    asciinema rec demo.cast --overwrite -c "python scripts/demo_cli.py --scene detect"
    # asciinema 2.x stores the outer terminal size; set it to --cols/--rows
    # in the first line of demo.cast (record_demo.sh does that for you)
    agg --font-size 16 demo.cast docs/assets/demo-cli-detect.gif
"""
import argparse
import fcntl
import os
import pty
import random
import re
import select
import shutil
import signal
import struct
import sys
import tempfile
import termios
import time
import tty
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[()][0-9A-B]|\x1b[=>]|\r")

TAB, ENTER, CTRL_C, CTRL_L, BACKSPACE = "\t", "\r", "\x03", "\x0c", "\x7f"
HOME, END, RIGHT = "\x01", "\x05", "\x1b[C"  # Ctrl+A / Ctrl+E: readline's start / end of line

RCFILE = r"""
PS1='\[\e[1;32m\]$\[\e[0m\] '
unset PROMPT_COMMAND
HISTFILE=/dev/null
bind 'set enable-bracketed-paste off'
bind 'set bell-style none'
bind 'set show-all-if-ambiguous on'
bind 'set completion-query-items 500'
bind 'set page-completions off'
stty -echoctl
export QT_QPA_PLATFORM=offscreen JUXT_LOG_LEVEL=ERROR
eval "$(juxt --bash-completion)"
"""


class Session:
    """An interactive bash in a pty whose output is mirrored to our stdout."""

    def __init__(self, stage: Path, cols: int, rows: int, speed: float, seed: int = 7):
        self.speed = speed
        self.rng = random.Random(seed)
        self.buf = ""  # ANSI-stripped output not yet consumed by expect()
        rc = stage / ".demo_bashrc"
        rc.write_text(RCFILE)
        (stage / ".sudo_as_admin_successful").touch()  # silence Ubuntu's sudo hint
        bindir = str(Path(sys.executable).parent)  # the juxt next to this interpreter
        env = {
            "HOME": str(stage),
            "PATH": bindir + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
        }
        if "JUXT_COMPLETE" in os.environ:
            env["JUXT_COMPLETE"] = os.environ["JUXT_COMPLETE"]
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(stage)
            os.execvpe("bash", ["bash", "--noprofile", "--rcfile", str(rc), "-i"], env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.expect_prompt(timeout=20)
        self.clear()  # wipe whatever the system bashrc printed

    # -- output ------------------------------------------------------------
    def pump(self, timeout: float) -> None:
        """Forward whatever bash prints within *timeout* seconds."""
        end = time.monotonic() + timeout
        while (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select([self.fd], [], [], left)
            if not ready:
                return
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
            self.buf += ANSI_RE.sub("", data.decode("utf-8", "replace"))

    def expect(self, pattern: str, timeout: float = 30) -> None:
        """Forward output until *pattern* (regex) shows up, then consume it."""
        end = time.monotonic() + timeout
        rx = re.compile(pattern)
        while not (m := rx.search(self.buf)):
            if time.monotonic() > end:
                raise SystemExit(f"timed out waiting for {pattern!r}; last output:\n{self.buf[-400:]}")
            self.pump(0.05)
        self.buf = self.buf[m.end():]

    def expect_prompt(self, timeout: float = 30) -> None:
        self.expect(r"\$ $", timeout)

    def pause(self, seconds: float) -> None:
        self.pump(seconds / self.speed)

    # -- input -------------------------------------------------------------
    def send(self, keys: str) -> None:
        os.write(self.fd, keys.encode())

    def type(self, text: str, wpm_delay: float = 0.07) -> None:
        for ch in text:
            self.send(ch)
            self.pause(wpm_delay * self.rng.uniform(0.6, 1.4))

    def tab(self, settle: float = 0.9) -> None:
        """Press Tab and let the candidate list (if any) sit on screen."""
        self.pause(0.35)
        self.send(TAB)
        self.pause(settle)

    def backspace(self, n: int) -> None:
        for _ in range(n):
            self.send(BACKSPACE)
            self.pause(0.035)

    def run(self, command: str, settle: float = 0.4) -> None:
        self.type(command)
        self.pause(settle)
        self.send(ENTER)

    def clear(self) -> None:
        self.send(CTRL_L)
        self.expect_prompt()

    def close(self) -> None:
        try:
            os.kill(self.pid, signal.SIGHUP)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(self.fd)


# --------------------------------------------------------------------------
# Scenes
# --------------------------------------------------------------------------
def scene_completion(s: Session) -> None:
    """Build a template top-down with Tab, then pin axis values."""
    s.pause(0.8)
    s.type("juxt results/")
    s.tab()                                  # ASCAT/ SMAP/ SMOS/
    s.type("{sensor}/")
    s.tab()                                  # what every sensor dir holds: AM/ PM/
    s.type("{overpass}/")
    s.tab(settle=1.6)                        # the four files, common prefix inserted
    s.pause(0.4)
    s.backspace(len("2024-03-1"))
    s.type("{date}_{source}.png")
    s.pause(0.6)
    # Quote the finished template. Two workarounds in one: bash 5.1 skips
    # programmable completion for every word after an unquoted word holding
    # two {...} groups, and juxt does not yet complete paths inside an open
    # quote (else the scene would simply start with `juxt "results/`).
    s.send(HOME)
    s.pause(0.3)
    for _ in range(len("juxt ")):
        s.send(RIGHT)
        s.pause(0.05)
    s.type('"')
    s.send(END)
    s.pause(0.3)
    s.type('"')
    s.pause(0.4)
    s.type(" --sen")
    s.tab(settle=0.5)                        # --sensor
    s.tab(settle=1.2)                        # ASCAT SMAP SMOS
    s.type("SMA")
    s.tab(settle=0.6)                        # SMAP
    s.type("SMO")
    s.tab(settle=0.6)                        # SMOS
    s.type("--o")
    s.tab(settle=0.5)                        # --overpass
    s.tab(settle=1.2)                        # AM PM
    s.type("P")
    s.tab(settle=2.5)                        # PM


def scene_detect(s: Session) -> None:
    """Point juxt at a directory, name the axes it found, look at the saved config."""
    s.pause(0.8)
    s.run("juxt plots/ --save demo.yaml")
    s.expect(r"Enter to keep\): ")
    s.pause(1.8)                             # let the colored pattern sink in
    s.send(ENTER)
    for name in ("sensor", "date", "overpass", "source"):
        s.expect(r"Enter to ignore\): ")
        s.pause(0.7)
        s.type(name, wpm_delay=0.09)
        s.pause(0.3)
        s.send(ENTER)
    s.expect(r"Config saved to demo\.yaml")
    s.pause(2.0)                             # the viewer is up now (offscreen)
    s.send(CTRL_C)
    s.expect_prompt()
    s.pause(0.6)
    s.clear()
    s.run("cat demo.yaml")
    s.expect_prompt()
    s.pause(1.5)
    s.run("juxt demo.yaml  # same hypercube, no questions asked", settle=2.5)
    s.expect(r"\n")
    s.pause(1.5)
    s.send(CTRL_C)
    s.expect_prompt()


SCENES = {"completion": scene_completion, "detect": scene_detect}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scene", choices=[*SCENES, "all"], default="all")
    ap.add_argument("--cols", type=int, default=92, help="terminal width of the recording")
    ap.add_argument("--rows", type=int, default=26, help="terminal height of the recording")
    ap.add_argument("--speed", type=float, default=1.0, help="playback speed factor (2 = twice as fast)")
    a = ap.parse_args()

    if shutil.which("juxt", path=str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")) is None:
        raise SystemExit("juxt not found; pip install -e . first")

    scenes = list(SCENES) if a.scene == "all" else [a.scene]
    raw = sys.stdin.isatty()
    old = termios.tcgetattr(sys.stdin) if raw else None
    with tempfile.TemporaryDirectory(prefix="juxt-demo-") as tmp:
        stage = Path(tmp)
        shutil.copytree(REPO / "examples/sample_plots", stage / "plots")
        shutil.copytree(REPO / "examples/sample_plots_nested", stage / "results")
        try:
            if raw:
                tty.setraw(sys.stdin)  # pass bash's bytes through untouched
            s = Session(stage, a.cols, a.rows, a.speed)
            for i, name in enumerate(scenes):
                if i:
                    s.pause(1.0)
                    s.send(CTRL_C)   # drop the half-typed line of the previous scene
                    s.expect_prompt()
                    s.clear()
                SCENES[name](s)
            s.close()
        finally:
            if raw:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)


if __name__ == "__main__":
    main()
