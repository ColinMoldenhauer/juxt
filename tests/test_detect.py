"""Tests for juxt/detect.py."""
from __future__ import annotations

from itertools import product

import pytest

from juxt.detect import (
    _axes_from_local_template,
    _detect_from_rel_stems,
    _is_remote_pattern,
    _iter_images,
    _parse_remote_pattern,
    _split_with_seps,
    detect_config,
)


class TestSplitWithSeps:
    def test_single_sep(self):
        tokens, seps = _split_with_seps("A_d1", ["_"])
        assert tokens == ["A", "d1"]
        assert seps == ["_"]

    def test_single_sep_three_tokens(self):
        tokens, seps = _split_with_seps("A_AM_d1", ["_"])
        assert tokens == ["A", "AM", "d1"]
        assert seps == ["_", "_"]

    def test_multiple_seps_path(self):
        tokens, seps = _split_with_seps("ASCAT/AM/d1", ["_", "/"])
        assert tokens == ["ASCAT", "AM", "d1"]
        assert seps == ["/", "/"]

    def test_mixed_seps(self):
        tokens, seps = _split_with_seps("ASCAT_AM/d1", ["_", "/"])
        assert tokens == ["ASCAT", "AM", "d1"]
        assert seps == ["_", "/"]

    def test_preserves_sep_identity(self):
        _, seps = _split_with_seps("A_B/C", ["_", "/"])
        assert seps[0] == "_"
        assert seps[1] == "/"


class TestIterImages:
    def test_flat_returns_all_images(self, flat_plot_dir):
        files = _iter_images(flat_plot_dir)
        assert len(files) == 4

    def test_only_image_extensions_included(self, tmp_path):
        (tmp_path / "readme.txt").write_bytes(b"")
        (tmp_path / "plot.png").write_bytes(b"")
        (tmp_path / "data.csv").write_bytes(b"")
        files = _iter_images(tmp_path)
        assert len(files) == 1 and files[0].name == "plot.png"

    def test_nested_all_depths(self, nested_plot_dir):
        files = _iter_images(nested_plot_dir)
        assert len(files) == 8

    def test_max_depth_zero_excludes_all_nested(self, nested_plot_dir):
        # Files live at depth 2; depth 0 means only top-level files
        assert _iter_images(nested_plot_dir, max_depth=0) == []

    def test_max_depth_one_excludes_nested(self, nested_plot_dir):
        # sensor/ dirs are at depth 0; overpass/ dirs at depth 1; files at depth 2
        assert _iter_images(nested_plot_dir, max_depth=1) == []

    def test_max_depth_two_finds_all(self, nested_plot_dir):
        assert len(_iter_images(nested_plot_dir, max_depth=2)) == 8

    def test_results_are_sorted(self, flat_plot_dir):
        files = _iter_images(flat_plot_dir)
        assert files == sorted(files)


class TestDetectFromRelStems:
    def test_two_variable_columns(self):
        stems = ["A_d1", "A_d2", "B_d1", "B_d2"]
        cfg, _ = _detect_from_rel_stems(stems, ".png", "/data", ["_"])
        assert len(cfg.axes) == 2
        assert set(cfg.axes["axis_0"]) == {"A", "B"}
        assert set(cfg.axes["axis_1"]) == {"d1", "d2"}

    def test_template_has_placeholders(self):
        stems = ["A_d1", "B_d2"]
        cfg, _ = _detect_from_rel_stems(stems, ".png", "/data", ["_"])
        assert "{axis_0}" in cfg.template
        assert "{axis_1}" in cfg.template

    def test_fixed_column_not_in_axes(self):
        stems = ["A_fixed", "B_fixed"]  # col 1 never changes
        cfg, _ = _detect_from_rel_stems(stems, ".png", "/data", ["_"])
        assert len(cfg.axes) == 1          # only one variable column
        # "fixed" appears as a literal in the template, not as a {placeholder}
        assert "{axis_1}" not in cfg.template
        assert "fixed" in cfg.template

    def test_inconsistent_column_count_raises(self):
        stems = ["A_d1", "B"]  # different number of tokens
        with pytest.raises(ValueError, match="inconsistent"):
            _detect_from_rel_stems(stems, ".png", "/data", ["_"])

    def test_all_identical_raises(self):
        stems = ["A_d1", "A_d1"]
        with pytest.raises(ValueError, match="No variable columns"):
            _detect_from_rel_stems(stems, ".png", "/data", ["_"])


class TestIsRemotePattern:
    @pytest.mark.parametrize("arg", [
        "user@host:/path",
        "user@host.example.com:/remote/dir",
        "hostname:/path/to/file",
        "longhost:/",
    ])
    def test_remote_strings(self, arg):
        assert _is_remote_pattern(arg) is True

    @pytest.mark.parametrize("arg", [
        "C:/path/to/file",        # Windows drive letter — single uppercase letter
        "D:/plots",
        "/local/absolute/path",
        "relative/path",
        "plots/",
        "just_a_filename.png",
    ])
    def test_local_strings(self, arg):
        assert _is_remote_pattern(arg) is False


class TestParseRemotePattern:
    def test_user_at_host_path(self):
        rc, path = _parse_remote_pattern("alice@myhost:/data/plots")
        assert rc.host == "myhost"
        assert rc.user == "alice"
        assert path == "/data/plots"

    def test_host_only_path(self):
        rc, path = _parse_remote_pattern("myhost:/data/plots")
        assert rc.host == "myhost"
        assert rc.user is None
        assert path == "/data/plots"

    def test_template_path_preserved(self):
        _, path = _parse_remote_pattern("host:/data/{sensor}_{date}.png")
        assert path == "/data/{sensor}_{date}.png"


class TestDetectConfig:
    def test_flat_directory(self, flat_plot_dir):
        cfg, seps = detect_config(flat_plot_dir, separators=["_"])
        assert len(cfg.axes) == 2
        for values in cfg.axes.values():
            assert len(values) == 2

    def test_flat_template_is_correct(self, flat_plot_dir):
        cfg, _ = detect_config(flat_plot_dir, separators=["_"])
        # Template should reconstruct to one of the actual files
        first_combo = {name: vals[0] for name, vals in cfg.axes.items()}
        path = cfg.template.format(**first_combo)
        import os
        assert os.path.exists(path)

    def test_nested_directory(self, nested_plot_dir):
        cfg, _ = detect_config(nested_plot_dir, separators=["_", "/"])
        assert len(cfg.axes) == 3

    def test_empty_directory_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No image files"):
            detect_config(tmp_path)

    def test_default_separators(self, nested_plot_dir):
        # Default separators are ["_", "/"] — should handle nested structure
        cfg, _ = detect_config(nested_plot_dir)
        assert len(cfg.axes) == 3

    def test_keys_assigned(self, flat_plot_dir):
        cfg, _ = detect_config(flat_plot_dir, separators=["_"])
        assert len(cfg.keys) > 0


class TestAxesFromLocalTemplate:
    def test_extracts_two_axes(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        axes = _axes_from_local_template(template)
        assert set(axes["sensor"]) == {"A", "B"}
        assert set(axes["date"]) == {"d1", "d2"}

    def test_preserves_discovery_order(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        axes = _axes_from_local_template(template)
        # Sorted by glob → alphabetical: A before B, d1 before d2
        assert axes["sensor"] == ["A", "B"]
        assert axes["date"] == ["d1", "d2"]

    def test_placeholder_name_may_be_a_date_shorthand(self, tmp_path):
        """{yyyy-mm-dd} is a valid axis name, not just a completion hint."""
        for date in ("2024-03-15", "2024-03-16"):
            (tmp_path / f"A_{date}.png").write_bytes(b"")
        axes = _axes_from_local_template(str(tmp_path / "{sensor}_{yyyy-mm-dd}.png"))
        assert axes["sensor"] == ["A"]
        assert axes["yyyy-mm-dd"] == ["2024-03-15", "2024-03-16"]

    def test_no_placeholders_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no"):
            _axes_from_local_template(str(tmp_path / "fixed.png"))

    def test_no_matching_files_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No files match"):
            _axes_from_local_template(str(tmp_path / "{sensor}.png"))

    def test_pinned_axis_filters_to_given_values(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        axes = _axes_from_local_template(template, pinned={"sensor": ["A"]})
        assert axes["sensor"] == ["A"]
        assert set(axes["date"]) == {"d1", "d2"}

    def test_pinned_axis_keeps_given_order_unsorted(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        axes = _axes_from_local_template(template, pinned={"sensor": ["B", "A"]})
        assert axes["sensor"] == ["B", "A"]

    def test_pinned_axis_dedupes_repeated_values(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        axes = _axes_from_local_template(template, pinned={"sensor": ["A", "A"]})
        assert axes["sensor"] == ["A"]

    def test_pinned_axis_can_span_path_separators(self, tmp_path):
        """A pinned value may contain '/', unlike a discovered one — lets
        unrelated directory trees become one axis."""
        roots = []
        for i in range(2):
            root = tmp_path / f"run{i}" / "nested"
            root.mkdir(parents=True)
            (root / f"plot{i}.png").write_bytes(b"")
            roots.append(str(root))

        template = "{root}/plot{n}.png"
        axes = _axes_from_local_template(template, pinned={"root": roots})
        assert axes["root"] == roots
        assert set(axes["n"]) == {"0", "1"}

    def test_pinned_multiple_axes_cartesian_product(self, tmp_path):
        for a, b in product(["x", "y"], ["1", "2"]):
            (tmp_path / f"{a}_{b}.png").write_bytes(b"")
        template = str(tmp_path / "{a}_{b}.png")
        axes = _axes_from_local_template(template, pinned={"a": ["x", "y"], "b": ["1", "2"]})
        assert axes["a"] == ["x", "y"]
        assert axes["b"] == ["1", "2"]

    def test_pinned_unknown_placeholder_raises(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        with pytest.raises(ValueError, match="does not match any"):
            _axes_from_local_template(template, pinned={"bogus": ["x"]})

    def test_pinned_axis_no_matches_raises(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        with pytest.raises(ValueError, match="No files match"):
            _axes_from_local_template(template, pinned={"sensor": ["ZZZ"]})
