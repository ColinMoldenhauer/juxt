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
