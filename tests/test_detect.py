"""Tests for juxt/detect.py."""
from __future__ import annotations

import os
from itertools import product

import pytest

from juxt.detect import (
    _axes_from_local_template,
    _axes_from_sftp_template,
    _detect_from_rel_stems,
    _is_remote_pattern,
    _iter_images,
    _parse_remote_pattern,
    _split_with_seps,
    axis_values_on_disk,
    detect_config,
    has_bare_wildcard,
    resolve_wildcard_template,
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
        resolved, axes = _axes_from_local_template(template)
        assert resolved == template  # unchanged: no '**name' placeholder involved
        assert set(axes["sensor"]) == {"A", "B"}
        assert set(axes["date"]) == {"d1", "d2"}

    def test_preserves_discovery_order(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        _, axes = _axes_from_local_template(template)
        # Sorted by glob → alphabetical: A before B, d1 before d2
        assert axes["sensor"] == ["A", "B"]
        assert axes["date"] == ["d1", "d2"]

    def test_placeholder_name_may_be_a_date_shorthand(self, tmp_path):
        """{yyyy-mm-dd} is a valid axis name, not just a completion hint."""
        for date in ("2024-03-15", "2024-03-16"):
            (tmp_path / f"A_{date}.png").write_bytes(b"")
        _, axes = _axes_from_local_template(str(tmp_path / "{sensor}_{yyyy-mm-dd}.png"))
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
        _, axes = _axes_from_local_template(template, pinned={"sensor": ["A"]})
        assert axes["sensor"] == ["A"]
        assert set(axes["date"]) == {"d1", "d2"}

    def test_pinned_axis_keeps_given_order_unsorted(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        _, axes = _axes_from_local_template(template, pinned={"sensor": ["B", "A"]})
        assert axes["sensor"] == ["B", "A"]

    def test_pinned_axis_dedupes_repeated_values(self, flat_plot_dir):
        template = str(flat_plot_dir / "{sensor}_{date}.png")
        _, axes = _axes_from_local_template(template, pinned={"sensor": ["A", "A"]})
        assert axes["sensor"] == ["A"]

    def test_pinned_axis_can_span_path_separators(self, tmp_path):
        """A pinned value may contain '/', unlike a discovered one — lets
        unrelated directory trees become one axis."""
        roots = []
        for i in range(2):
            root = tmp_path / f"run{i}" / "nested"
            root.mkdir(parents=True)
            (root / f"plot{i}.png").write_bytes(b"")
            roots.append(root.as_posix())

        template = "{root}/plot{n}.png"
        _, axes = _axes_from_local_template(template, pinned={"root": roots})
        assert axes["root"] == roots
        assert set(axes["n"]) == {"0", "1"}

    def test_pinned_axis_normalises_backslashes(self, tmp_path):
        """A pinned value given with '\\' (e.g. str(a_windows_path)) must
        still match, and come back normalised like every other path here."""
        roots_posix = []
        roots_native = []
        for i in range(2):
            root = tmp_path / f"run{i}" / "nested"
            root.mkdir(parents=True)
            (root / f"plot{i}.png").write_bytes(b"")
            roots_posix.append(root.as_posix())
            roots_native.append(root.as_posix().replace("/", "\\"))

        _, axes = _axes_from_local_template(
            "{root}/plot{n}.png", pinned={"root": roots_native}
        )
        assert axes["root"] == roots_posix
        assert set(axes["n"]) == {"0", "1"}

    def test_pinned_multiple_axes_cartesian_product(self, tmp_path):
        for a, b in product(["x", "y"], ["1", "2"]):
            (tmp_path / f"{a}_{b}.png").write_bytes(b"")
        template = str(tmp_path / "{a}_{b}.png")
        _, axes = _axes_from_local_template(template, pinned={"a": ["x", "y"], "b": ["1", "2"]})
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


class TestRecursivePlaceholder:
    """{**name} captures across '/' instead of stopping at the next one."""

    def test_prunes_common_leading_segments(self, tmp_path):
        for overpass in ("AM", "PM"):
            p = tmp_path / "dir" / overpass
            p.mkdir(parents=True)
            (p / "plot.png").write_bytes(b"")

        resolved, axes = _axes_from_local_template(str(tmp_path / "{**plot}.png"))
        assert resolved == (tmp_path / "dir" / "{plot}.png").as_posix()
        assert set(axes["plot"]) == {"AM/plot", "PM/plot"}
        assert resolved.format(plot="AM/plot") == (tmp_path / "dir" / "AM" / "plot.png").as_posix()

    def test_full_path_disables_pruning(self, tmp_path):
        for overpass in ("AM", "PM"):
            p = tmp_path / "dir" / overpass
            p.mkdir(parents=True)
            (p / "plot.png").write_bytes(b"")

        resolved, axes = _axes_from_local_template(str(tmp_path / "{**plot}.png"), full_path=True)
        assert resolved == (tmp_path / "{plot}.png").as_posix()
        assert set(axes["plot"]) == {"dir/AM/plot", "dir/PM/plot"}

    def test_no_common_prefix_keeps_raw_values(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "plot.png").write_bytes(b"")
        (tmp_path / "b" / "sub").mkdir(parents=True)
        (tmp_path / "b" / "sub" / "plot.png").write_bytes(b"")

        resolved, axes = _axes_from_local_template(str(tmp_path / "{**plot}.png"))
        assert resolved == (tmp_path / "{plot}.png").as_posix()
        assert set(axes["plot"]) == {"a/plot", "b/sub/plot"}

    def test_combined_with_pinned_root_aggregates_across_directories(self, tmp_path):
        """The motivating use case: {root}/{**plot}.png --root r1 r2, where
        each root's substructure is pooled into one 'plot' axis, exactly as
        directory auto-discovery would if 'root' were not being kept separate."""
        for root in ("r1", "r2"):
            for overpass in ("AM", "PM"):
                p = tmp_path / root / "dir" / overpass
                p.mkdir(parents=True)
                (p / "plot.png").write_bytes(b"")

        roots = [(tmp_path / "r1").as_posix(), (tmp_path / "r2").as_posix()]
        resolved, axes = _axes_from_local_template(
            "{root}/{**plot}.png", pinned={"root": roots}
        )
        assert axes["root"] == roots
        assert set(axes["plot"]) == {"AM/plot", "PM/plot"}
        assert resolved.format(root=roots[0], plot="AM/plot") == f"{roots[0]}/dir/AM/plot.png"

    def test_pinned_recursive_axis_is_not_pruned(self, tmp_path):
        """A pinned '**name' value is the caller's literal string, not ours
        to rewrite -- pruning only applies to values we discovered."""
        (tmp_path / "leaf.png").write_bytes(b"")
        resolved, axes = _axes_from_local_template(
            str(tmp_path / "{**plot}.png"), pinned={"plot": ["dir/AM/plot", "dir/PM/plot"]}
        )
        assert axes["plot"] == ["dir/AM/plot", "dir/PM/plot"]
        assert resolved == (tmp_path / "{plot}.png").as_posix()

    def test_two_recursive_placeholders_raise(self, tmp_path):
        with pytest.raises(ValueError, match="more than one"):
            _axes_from_local_template(str(tmp_path / "{**a}/{**b}.png"))

    def test_marker_mismatch_for_same_name_raises(self, tmp_path):
        with pytest.raises(ValueError, match="uses both"):
            _axes_from_local_template(str(tmp_path / "{plot}/{**plot}.png"))


class TestResolveWildcardTemplate:
    def test_flat_wildcard_discovers_axes(self, flat_plot_dir):
        template = str(flat_plot_dir / "*.png")
        resolved, axes = resolve_wildcard_template(template)
        assert set(axes["axis_0"]) == {"A", "B"}
        assert set(axes["axis_1"]) == {"d1", "d2"}
        assert resolved.format(axis_0="A", axis_1="d1") == (flat_plot_dir / "A_d1.png").as_posix()

    def test_wildcard_recurses_into_nested_subdirs(self, nested_plot_dir):
        # Files live two levels down (sensor/overpass/date.png); a bare '*'
        # must still find them, unlike a shell glob which stops at one level.
        template = str(nested_plot_dir / "*.png")
        _, axes = resolve_wildcard_template(template)
        assert set(axes["axis_0"]) == {"A", "B"}
        assert set(axes["axis_1"]) == {"AM", "PM"}
        assert set(axes["axis_2"]) == {"d1", "d2"}

    def test_wildcard_filters_by_extension(self, tmp_path):
        (tmp_path / "A.png").write_bytes(b"")
        (tmp_path / "B.png").write_bytes(b"")
        (tmp_path / "C.jpg").write_bytes(b"")
        _, axes = resolve_wildcard_template(str(tmp_path / "*.png"))
        assert set(axes["axis_0"]) == {"A", "B"}

    def test_pinned_root_aggregates_across_directories(self, tmp_path):
        """The issue's motivating example: '{root}/*.png' --root r1 r2."""
        roots = []
        for i, root_name in enumerate(("r1", "r2")):
            root = tmp_path / root_name
            root.mkdir()
            for sensor in ("A", "B"):
                (root / f"{sensor}_d{i}.png").write_bytes(b"")
            roots.append(root.as_posix())

        template = "{root}/*.png"
        resolved, axes = resolve_wildcard_template(template, pinned={"root": roots})
        assert axes["root"] == roots
        assert set(axes["axis_0"]) == {"A", "B"}
        assert set(axes["axis_1"]) == {"d0", "d1"}
        assert resolved.format(root=roots[0], axis_0="A", axis_1="d0") == f"{roots[0]}/A_d0.png"

    def test_no_placeholder_needed(self, tmp_path):
        (tmp_path / "A_d1.png").write_bytes(b"")
        (tmp_path / "A_d2.png").write_bytes(b"")
        resolved, axes = resolve_wildcard_template(str(tmp_path / "*.png"))
        assert "axis_0" not in axes  # only one distinct value in that column
        assert set(axes["axis_1"]) == {"d1", "d2"}

    def test_placeholder_after_wildcard_raises(self, tmp_path):
        with pytest.raises(ValueError, match="cannot appear after"):
            resolve_wildcard_template(str(tmp_path / "*/{sensor}.png"))

    def test_unpinned_placeholder_before_wildcard_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be pinned"):
            resolve_wildcard_template("{root}/*.png")

    def test_pinned_unknown_placeholder_raises(self, tmp_path):
        with pytest.raises(ValueError, match="does not match any"):
            resolve_wildcard_template("{root}/*.png", pinned={"bogus": ["x"]})

    def test_no_matching_files_raises(self, tmp_path):
        with pytest.raises(ValueError, match="No files match"):
            resolve_wildcard_template(str(tmp_path / "*.png"))

    def test_incongruent_roots_raise(self, tmp_path):
        root_a = tmp_path / "a"
        root_a.mkdir()
        (root_a / "X_d1.png").write_bytes(b"")

        root_b = tmp_path / "b"
        root_b.mkdir()
        (root_b / "sub" / "Y_d1.png").parent.mkdir()
        (root_b / "sub" / "Y_d1.png").write_bytes(b"")

        template = "{root}/*.png"
        with pytest.raises(ValueError, match="inconsistent number of parts"):
            resolve_wildcard_template(
                template, pinned={"root": [root_a.as_posix(), root_b.as_posix()]}
            )


class TestHasBareWildcard:
    def test_plain_wildcard_is_bare(self):
        assert has_bare_wildcard("plots/*.png") is True

    def test_pinned_root_with_wildcard_is_bare(self):
        assert has_bare_wildcard("{root}/*.png") is True

    def test_recursive_placeholder_alone_is_not_bare(self):
        """'{**plot}' contains '*' characters, but they belong to the
        placeholder marker, not a standalone glob wildcard."""
        assert has_bare_wildcard("{root}/{**plot}.png") is False

    def test_recursive_placeholder_plus_bare_wildcard_is_bare(self):
        assert has_bare_wildcard("{root}/*/{**plot}.png") is True

    def test_plain_template_is_not_bare(self):
        assert has_bare_wildcard("{sensor}_{date}.png") is False


class _OsSFTP:
    """SFTP stand-in backed by the real filesystem, with paramiko's semantics:
    `listdir_attr` reports lstat-style modes, so a symlinked directory shows up
    as a link and only `stat` looks through it."""

    class _Entry:
        def __init__(self, filename, st_mode):
            self.filename = filename
            self.st_mode = st_mode

    def __init__(self):
        self.listed: list[str] = []

    def listdir_attr(self, path):
        self.listed.append(path)
        return [
            self._Entry(name, os.lstat(os.path.join(path, name)).st_mode)
            for name in sorted(os.listdir(path))
        ]

    def stat(self, path):
        return self._Entry(os.path.basename(path), os.stat(path).st_mode)

    def normalize(self, path):
        return os.path.realpath(path)


class TestRemoteTemplateSymlinks:
    """The remote walk has to look through symlinked directories: the tree it
    walks may well be a folder of symlinks pointing at the real output dirs."""

    def test_walks_through_a_symlinked_directory(self, tmp_path):
        real = tmp_path / "real" / "AM"
        real.mkdir(parents=True)
        (real / "d1.png").write_bytes(b"")
        (real / "d2.png").write_bytes(b"")
        link_root = tmp_path / "plots"
        link_root.mkdir()
        (link_root / "A").symlink_to(real.parent / "AM")

        axes = _axes_from_sftp_template(
            f"{link_root.as_posix()}/{{sensor}}/{{date}}.png", _OsSFTP()
        )
        assert axes == {"sensor": ["A"], "date": ["d1", "d2"]}

    def test_cyclic_symlink_does_not_loop(self, tmp_path):
        root = tmp_path / "plots"
        (root / "A").mkdir(parents=True)
        (root / "A" / "d1.png").write_bytes(b"")
        (root / "A" / "up").symlink_to(root)

        axes = _axes_from_sftp_template(
            f"{root.as_posix()}/{{sensor}}/{{date}}.png", _OsSFTP()
        )
        assert axes["sensor"] == ["A"]
        assert axes["date"] == ["d1"]


class TestRemoteTemplateWalk:
    """Every remote listing is a round trip, and pinning several placeholders
    multiplies the walks, so the walk must stay close to what the template
    can actually match."""

    def test_skips_subtrees_the_template_cannot_match(self, tmp_path):
        root = tmp_path / "plots"
        (root / "A").mkdir(parents=True)
        (root / "A" / "d1.png").write_bytes(b"")
        # at the depth of the files, so it can never hold a match
        (root / "A" / "extra").mkdir()
        (root / "A" / "extra" / "d2.png").write_bytes(b"")

        sftp = _OsSFTP()
        axes = _axes_from_sftp_template(
            f"{root.as_posix()}/{{sensor}}/{{date}}.png", sftp
        )
        assert axes == {"sensor": ["A"], "date": ["d1"]}
        assert (root / "A" / "extra").as_posix() not in sftp.listed

    def test_listings_are_shared_between_pinned_combinations(self, tmp_path):
        root = tmp_path / "plots"
        for sensor in ("A", "B"):
            (root / sensor).mkdir(parents=True)
            for date in ("d1", "d2"):
                (root / sensor / f"{date}.png").write_bytes(b"")

        sftp = _OsSFTP()
        axes = _axes_from_sftp_template(
            f"{root.as_posix()}/{{sensor}}/{{date}}.png",
            sftp,
            pinned={"date": ["d1", "d2"]},
        )
        assert axes == {"sensor": ["A", "B"], "date": ["d1", "d2"]}
        assert sorted(set(sftp.listed)) == sorted(sftp.listed)


class TestAxisValuesOnDisk:
    """The value picker searches one axis, with the others pinned to the
    values the session has, locally and over SFTP alike."""

    @pytest.fixture
    def tree(self, tmp_path):
        root = tmp_path / "plots"
        root.mkdir()
        for sensor, date in [("A", "d1"), ("A", "d2"), ("B", "d1"), ("C", "d2"), ("D", "d9")]:
            (root / f"{sensor}_{date}.png").write_bytes(b"")
        return f"{root.as_posix()}/{{sensor}}_{{date}}.png"

    def test_local_lists_values_for_the_loaded_combinations(self, tree):
        axes = {"sensor": ["A"], "date": ["d1", "d2"]}
        assert axis_values_on_disk(tree, axes, "sensor") == ["A", "B", "C"]

    def test_value_only_with_unloaded_combinations_is_left_out(self, tree):
        """D only exists for d9, which the session does not show."""
        axes = {"sensor": ["A"], "date": ["d1"]}
        assert axis_values_on_disk(tree, axes, "sensor") == ["A", "B"]

    def test_remote_matches_local(self, tree):
        axes = {"sensor": ["A"], "date": ["d1", "d2"]}
        assert axis_values_on_disk(tree, axes, "sensor", _OsSFTP()) == ["A", "B", "C"]

    def test_remote_walk_stays_within_the_pinned_values(self, tmp_path):
        root = tmp_path / "plots"
        for sensor, date in [("A", "d1"), ("B", "d1"), ("B", "d2")]:
            (root / date).mkdir(parents=True, exist_ok=True)
            (root / date / f"{sensor}.png").write_bytes(b"")
        sftp = _OsSFTP()
        axes = {"date": ["d1"], "sensor": ["A"]}
        found = axis_values_on_disk(
            f"{root.as_posix()}/{{date}}/{{sensor}}.png", axes, "sensor", sftp)
        assert found == ["A", "B"]
        assert not any(p.endswith("/d2") for p in sftp.listed)


class TestPatternPins:
    """`--NAME 'A*'` / `--NAME 're:...'` keep the values on disk they match."""

    @pytest.fixture
    def tree(self, tmp_path):
        """sensor/date.png where only B2 has d2, and the sensors include 10."""
        for sensor, date in [("A1", "d1"), ("A2", "d1"), ("A10", "d1"),
                             ("B1", "d1"), ("B2", "d2")]:
            (tmp_path / sensor).mkdir(exist_ok=True)
            (tmp_path / sensor / f"{date}.png").write_bytes(b"")
        return f"{tmp_path.as_posix()}/{{sensor}}/{{date}}.png"

    def _axes(self, template, **pins):
        from juxt.detect import parse_pins
        return _axes_from_local_template(template, pinned=parse_pins(pins))[1]

    def test_parse_pin_kinds(self):
        from juxt.detect import PinPattern, parse_pin
        assert parse_pin("A1") == "A1"
        assert parse_pin("1.5") == "1.5"  # '.' is common in values, not a regex
        for typed in ("A*", "A?", "A[12]", "re:A.*"):
            assert isinstance(parse_pin(typed), PinPattern)
        with pytest.raises(ValueError, match="Invalid regex"):
            parse_pin("re:A(")

    def test_glob_keeps_the_matching_values(self, tree):
        assert self._axes(tree, sensor=["A*"])["sensor"] == ["A1", "A10", "A2"]

    def test_each_pattern_sorts_its_matches_like_a_discovered_axis(self, tree):
        assert self._axes(tree, sensor=["A??", "A?"])["sensor"] == ["A10", "A1", "A2"]
        assert self._axes(tree, date=["d*"])["date"] == ["d1", "d2"]

    def test_regex_matches_in_full(self, tree):
        assert self._axes(tree, sensor=["re:A\\d"])["sensor"] == ["A1", "A2"]
        assert self._axes(tree, sensor=["re:[AB]1"])["sensor"] == ["A1", "B1"]

    def test_match_is_case_sensitive(self, tree):
        with pytest.raises(ValueError, match="matches no files"):
            self._axes(tree, sensor=["a*"])

    def test_literals_and_patterns_mix_in_the_given_order(self, tree):
        axes = self._axes(tree, sensor=["B2", "A*", "A1"])
        assert axes["sensor"] == ["B2", "A1", "A10", "A2"]

    def test_a_regex_with_groups_and_flags(self, tree):
        """Its own groups must not shift the template's, and a leading global
        flag must survive being spliced into the template regex."""
        assert self._axes(tree, sensor=["re:(A|B)(1)"])["sensor"] == ["A1", "B1"]
        assert self._axes(tree, sensor=["re:(?i)b\\d"])["sensor"] == ["B1", "B2"]

    def test_glob_star_stays_within_a_path_segment(self, tmp_path):
        for sub in ("runs/a", "runs/b", "runs/b/deep"):
            (tmp_path / sub).mkdir(parents=True)
            (tmp_path / sub / "plot.png").write_bytes(b"")
        root = tmp_path.as_posix()
        axes = self._axes("{root}/plot.png", root=[f"{root}/runs/*"])
        assert axes["root"] == [f"{root}/runs/a", f"{root}/runs/b"]

    def test_other_axes_only_get_values_found_with_a_match(self, tree):
        """d2 exists only for B2, which 'A*' leaves out."""
        assert self._axes(tree, sensor=["A*"])["date"] == ["d1"]
        assert self._axes(tree, sensor=["B*"])["date"] == ["d1", "d2"]

    def test_patterns_on_several_axes(self, tree):
        axes = self._axes(tree, sensor=["B*"], date=["re:d[2-9]"])
        assert axes == {"sensor": ["B2"], "date": ["d2"]}

    def test_a_pattern_that_matches_nothing_raises(self, tree):
        with pytest.raises(ValueError, match="'Z\\*' matches no files"):
            self._axes(tree, sensor=["A*", "Z*"])

    def test_a_plain_str_pin_stays_literal(self, tmp_path):
        """Discovered values are pinned again internally; a '[' in one is no class."""
        (tmp_path / "run[1].png").write_bytes(b"")
        (tmp_path / "run1.png").write_bytes(b"")
        template = f"{tmp_path.as_posix()}/{{run}}.png"
        _, axes = _axes_from_local_template(template, pinned={"run": ["run[1]"]})
        assert axes["run"] == ["run[1]"]

    def test_recursive_placeholder(self, tmp_path):
        for sub in ("AM/x", "AM/y", "PM/x"):
            (tmp_path / sub).mkdir(parents=True)
            (tmp_path / sub / "plot.png").write_bytes(b"")
        template = f"{tmp_path.as_posix()}/{{**run}}/plot.png"
        assert self._axes(template, run=["AM/*"])["run"] == ["AM/x", "AM/y"]
        assert self._axes(template, run=["*/x"])["run"] == ["AM/x", "PM/x"]

    def test_wildcard_template(self, tmp_path):
        for root, sensor in [("r1", "A"), ("r2", "B"), ("other", "C")]:
            (tmp_path / root).mkdir()
            (tmp_path / root / f"{sensor}_d1.png").write_bytes(b"")
        from juxt.detect import parse_pins
        _, axes = resolve_wildcard_template(
            "{root}/*.png", pinned=parse_pins({"root": [f"{tmp_path.as_posix()}/r*"]}))
        assert axes["root"] == [f"{tmp_path.as_posix()}/r1", f"{tmp_path.as_posix()}/r2"]
        assert axes["axis_0"] == ["A", "B"]

    def test_remote_matches_local(self, tree):
        from juxt.detect import parse_pins
        for pins in ({"sensor": ["A*"]}, {"sensor": ["B2", "re:A\\d"]}):
            remote = _axes_from_sftp_template(tree, _OsSFTP(), parse_pins(pins))
            assert remote == self._axes(tree, **pins)

    def test_literal_pin_round_trips(self):
        from juxt.detect import literal_pin, order_pinned, parse_pin
        for value in ("plain", "x*y", "run[1]", "a?", "re:b", "1.5"):
            spec = parse_pin(literal_pin(value))
            assert order_pinned([spec], [value, value + "z", "b"]) == [value]
