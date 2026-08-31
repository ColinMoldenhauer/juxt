"""Unit tests for juxt/complete.py — placeholder-aware path completion."""
from __future__ import annotations

import stat

import pytest

from juxt.complete import (
    PLACEHOLDER_COLORS,
    Completion,
    complete_path,
    complete_placeholder_name,
    completion_words,
    local_listdir,
    normalize_template,
    placeholder_html,
    placeholder_shapes,
    reset_placeholder_shapes,
    sftp_listdir,
    shorthand_shape,
)


# ---------------------------------------------------------------------------
# Anonymous {} placeholders
# ---------------------------------------------------------------------------

class TestNormalizeTemplate:
    def test_named_placeholders_untouched(self):
        t = "plots/{sensor}_{date}.png"
        assert normalize_template(t) == t

    def test_anonymous_gets_generated_name(self):
        assert normalize_template("plots/{}.png") == "plots/{axis_1}.png"

    def test_multiple_anonymous_are_numbered(self):
        assert normalize_template("plots/{}/{}.png") == "plots/{axis_1}/{axis_2}.png"

    def test_mixed_named_and_anonymous(self):
        assert normalize_template("p/{}/{date}.png") == "p/{axis_1}/{date}.png"

    def test_generated_names_skip_taken_ones(self):
        assert normalize_template("p/{axis_1}/{}.png") == "p/{axis_1}/{axis_2}.png"

    def test_no_placeholders_at_all(self):
        assert normalize_template("plots/") == "plots/"


# ---------------------------------------------------------------------------
# Path completion against a real directory tree
# ---------------------------------------------------------------------------

@pytest.fixture
def tree(nested_plot_dir):
    """plots/{A,B}/{AM,PM}/{d1,d2}.png — returns the plots/ prefix as a string."""
    return nested_plot_dir.as_posix()


def _complete(prefix: str) -> Completion:
    return complete_path(prefix, local_listdir())


class TestPlainPathCompletion:
    def test_unique_directory_appends_separator(self, tree):
        parent, _, name = tree.rpartition("/")
        comp = _complete(f"{parent}/{name[:-1]}")
        assert comp.append == name[-1] + "/"
        assert comp.matches == []

    def test_ambiguous_lists_candidates(self, tree):
        comp = _complete(f"{tree}/")
        assert comp.matches == ["A/", "B/"]
        assert comp.append == ""

    def test_unique_file_completes_extension(self, tree):
        comp = _complete(f"{tree}/A/AM/d1")
        assert comp.append == ".png"
        assert comp.matches == []

    def test_common_prefix_is_appended(self, tree):
        comp = _complete(f"{tree}/A/AM/")
        assert comp.append == "d"        # d1.png / d2.png
        assert comp.matches == ["d1.png", "d2.png"]

    def test_no_match_completes_nothing(self, tree):
        comp = _complete(f"{tree}/zzz")
        assert comp.append == ""
        assert comp.matches == []

    def test_hidden_entries_need_an_explicit_dot(self, tmp_path):
        (tmp_path / ".hidden").mkdir()
        (tmp_path / "one").mkdir()
        (tmp_path / "two").mkdir()
        assert _complete(f"{tmp_path.as_posix()}/").matches == ["one/", "two/"]
        assert _complete(f"{tmp_path.as_posix()}/.").append == "hidden/"


class TestPlaceholderPathCompletion:
    def test_placeholder_component_expands_all_subtrees(self, tree):
        comp = _complete(f"{tree}/{{sensor}}/")
        assert comp.matches == ["AM/", "PM/"]

    def test_completion_below_two_placeholders(self, tree):
        comp = _complete(f"{tree}/{{sensor}}/{{overpass}}/d1")
        assert comp.append == ".png"

    def test_typed_placeholders_are_preserved(self, tree):
        prefix = f"{tree}/{{sensor}}/AM/d"
        comp = _complete(prefix)
        assert (prefix + comp.append).startswith(f"{tree}/{{sensor}}/AM/d")
        assert comp.matches == ["d1.png", "d2.png"]

    def test_placeholder_inside_a_component(self, tmp_path):
        for name in ("A_d1.png", "B_d1.png"):
            (tmp_path / name).write_bytes(b"")
        comp = _complete(f"{tmp_path.as_posix()}/{{sensor}}_d1")
        assert comp.append == ".png"

    def test_trailing_placeholder_over_dirs_appends_separator(self, tree):
        comp = _complete(f"{tree}/{{sensor}}")
        assert comp.append == "/"
        assert comp.matches == ["A/", "B/"]

    def test_trailing_placeholder_takes_common_suffix(self, tmp_path):
        for name in ("A_d1.png", "B_d1.png"):
            (tmp_path / name).write_bytes(b"")
        comp = _complete(f"{tmp_path.as_posix()}/{{sensor}}")
        assert comp.append == "_d1.png"

    def test_trailing_placeholder_takes_the_shared_extension(self, tmp_path):
        for name in ("2024-03-15.png", "2024-03-16.png"):
            (tmp_path / name).write_bytes(b"")
        comp = _complete(f"{tmp_path.as_posix()}/{{date}}")
        assert comp.append == ".png"

    def test_suffix_never_swallows_a_whole_name(self, tmp_path):
        # "_d1.png" is itself the common suffix of the two — completing with it
        # would leave the placeholder matching nothing.
        for name in ("_d1.png", "A_d1.png"):
            (tmp_path / name).write_bytes(b"")
        comp = _complete(f"{tmp_path.as_posix()}/{{sensor}}")
        assert comp.append == ""
        assert comp.matches == ["A_d1.png", "_d1.png"]

    def test_missing_subtree_completes_nothing(self, tree):
        assert _complete(f"{tree}/{{sensor}}/ZZ/d").matches == []


class TestPlaceholderNameCompletion:
    NAMES = ["sensor", "date"]

    def test_unique_axis_name_is_closed(self):
        comp = complete_placeholder_name("plots/{se", self.NAMES)
        assert comp.append == "nsor}"

    def test_open_brace_lists_axis_names(self):
        comp = complete_placeholder_name("plots/{", self.NAMES)
        assert comp.matches == ["{sensor}", "{date}"]
        assert comp.append == ""

    def test_common_prefix_of_axis_names(self):
        comp = complete_placeholder_name("plots/{d", ["date", "daynight"])
        assert comp.append == "a"

    def test_a_hyphenated_axis_name_completes(self):
        comp = complete_placeholder_name("plots/{yyyy-", ["yyyy-mm-dd", "sensor"])
        assert comp.append == "mm-dd}"

    def test_unknown_name_falls_through(self):
        assert complete_placeholder_name("plots/{zz", self.NAMES) is None

    def test_closed_placeholder_falls_through(self):
        assert complete_placeholder_name("plots/{sensor}/2024", self.NAMES) is None

    def test_plain_path_falls_through(self):
        assert complete_placeholder_name("plots/2024", self.NAMES) is None


# ---------------------------------------------------------------------------
# Remote completion (fake SFTP session)
# ---------------------------------------------------------------------------

class _Entry:
    def __init__(self, filename, is_dir):
        self.filename = filename
        self.st_mode = (stat.S_IFDIR if is_dir else stat.S_IFREG) | 0o644


class _FakeSFTP:
    """Minimal stand-in for paramiko's SFTPClient.listdir_attr."""

    def __init__(self, tree: dict[str, list[tuple[str, bool]]]):
        self.tree = tree
        self.calls: list[str] = []

    def listdir_attr(self, path):
        self.calls.append(path)
        if path not in self.tree:
            raise IOError(f"no such directory: {path}")
        return [_Entry(name, is_dir) for name, is_dir in self.tree[path]]


@pytest.fixture
def fake_sftp():
    return _FakeSFTP({
        "/plots": [("A", True), ("B", True)],
        "/plots/A": [("d1.png", False), ("d2.png", False)],
        "/plots/B": [("d1.png", False), ("d3.png", False)],
    })


class TestRemoteCompletion:
    def test_lists_a_remote_directory(self, fake_sftp):
        comp = complete_path("/plots/", sftp_listdir(fake_sftp))
        assert comp.matches == ["A/", "B/"]

    def test_placeholder_merges_remote_subtrees(self, fake_sftp):
        comp = complete_path("/plots/{sensor}/d", sftp_listdir(fake_sftp))
        assert comp.matches == ["d1.png", "d2.png", "d3.png"]

    def test_listings_are_cached(self, fake_sftp):
        listdir = sftp_listdir(fake_sftp)
        complete_path("/plots/", listdir)
        complete_path("/plots/", listdir)
        assert fake_sftp.calls.count("/plots") == 1

    def test_unreadable_directory_is_silent(self, fake_sftp):
        assert complete_path("/nope/x", sftp_listdir(fake_sftp)) == Completion()


# ---------------------------------------------------------------------------
# Syntax highlighting
# ---------------------------------------------------------------------------

class TestPlaceholderHtml:
    def test_each_placeholder_gets_its_own_colour(self):
        html = placeholder_html("p/{sensor}_{date}.png")
        assert f'<span style="color:{PLACEHOLDER_COLORS[0]}">{{sensor}}</span>' in html
        assert f'<span style="color:{PLACEHOLDER_COLORS[1]}">{{date}}</span>' in html

    def test_colours_cycle(self):
        html = placeholder_html("{a}{b}{c}{d}{e}{f}")
        assert html.count(PLACEHOLDER_COLORS[0]) == 2

    def test_literal_text_is_escaped(self):
        assert "&lt;x&gt;" in placeholder_html("<x>/{a}.png")

    def test_spaces_survive_as_nbsp(self):
        assert "&nbsp;" in placeholder_html("my dir/{a}.png")

    def test_placeholder_being_typed_is_highlighted(self):
        assert PLACEHOLDER_COLORS[0] in placeholder_html("plots/{sen")

    def test_caret_inside_a_placeholder(self):
        html = placeholder_html("plots/{sen▌sor}.png")
        assert f'<span style="color:{PLACEHOLDER_COLORS[0]}">{{sen▌sor}}</span>' in html


# ---------------------------------------------------------------------------
# Placeholder value shapes
# ---------------------------------------------------------------------------

@pytest.fixture
def dated_dir(tmp_path):
    """2 dates x 2 levels: 2024-03-15_L2.png ... 2024-03-16_L3.png."""
    for date in ("2024-03-15", "2024-03-16"):
        for level in ("L2", "L3"):
            (tmp_path / f"{date}_{level}.png").write_bytes(b"")
    return tmp_path


class TestShorthandShape:
    def test_dashed_date(self):
        assert shorthand_shape("yyyy-mm-dd") == r"\d{4}\-\d{2}\-\d{2}"

    def test_compact_date(self):
        assert shorthand_shape("yyyymmdd") == r"\d{4}\d{2}\d{2}"

    def test_longest_token_wins(self):
        assert shorthand_shape("yy") == r"\d{2}"
        assert shorthand_shape("yyyy") == r"\d{4}"

    def test_a_plain_name_is_not_a_shorthand(self):
        assert shorthand_shape("sensor") is None

    def test_separators_alone_are_not_a_shorthand(self):
        assert shorthand_shape("--") is None

    def test_a_handwritten_regex_is_not_a_shorthand(self):
        assert shorthand_shape(r"o\d{5}") is None


@pytest.fixture
def shipped_settings(tmp_path, monkeypatch):
    """A settings file as a fresh install writes it, with the default shapes."""
    from juxt.settings import _TEMPLATE

    cfg = tmp_path / ".juxt"      # hidden, so it is never a completion candidate
    cfg.mkdir()
    path = cfg / "settings.yaml"
    path.write_text(_TEMPLATE, encoding="utf-8")
    monkeypatch.setattr("juxt.settings.SETTINGS_PATH", path)
    reset_placeholder_shapes()
    yield path
    reset_placeholder_shapes()


@pytest.mark.usefixtures("shipped_settings")
class TestShapedCompletion:
    def test_known_name_stops_at_the_token_boundary(self, dated_dir):
        # Without the shape this would complete to "_L", or even "_L2.png".
        comp = _complete(f"{dated_dir.as_posix()}/{{date}}")
        assert comp.append == "_"

    def test_shorthand_name_stops_at_the_token_boundary(self, dated_dir):
        comp = _complete(f"{dated_dir.as_posix()}/{{yyyy-mm-dd}}")
        assert comp.append == "_"

    def test_completion_continues_past_the_boundary(self, dated_dir):
        comp = _complete(f"{dated_dir.as_posix()}/{{date}}_")
        assert comp.append == "L"

    def test_an_unknown_name_keeps_the_shared_suffix(self, dated_dir):
        comp = _complete(f"{dated_dir.as_posix()}/{{sensor}}")
        assert comp.append == ".png"

    def test_a_fully_shared_remainder_is_completed(self, tmp_path):
        for date in ("2024-03-15", "2024-03-16"):
            (tmp_path / f"{date}.png").write_bytes(b"")
        assert _complete(f"{tmp_path.as_posix()}/{{date}}").append == ".png"

    def test_date_directories_open(self, tmp_path):
        for date in ("2024-03-15", "2024-03-16"):
            (tmp_path / date).mkdir()
        assert _complete(f"{tmp_path.as_posix()}/{{date}}").append == "/"

    def test_a_shape_that_does_not_fit_falls_back(self, tmp_path):
        # Dates the built-in shape knows nothing about must still complete.
        for name in ("15Mar2024_L2.png", "16Mar2024_L3.png"):
            (tmp_path / name).write_bytes(b"")
        comp = _complete(f"{tmp_path.as_posix()}/{{date}}")
        assert comp.matches == ["15Mar2024_L2.png", "16Mar2024_L3.png"]

    def test_words_swallowed_by_a_placeholder_appear_once(self, dated_dir):
        words = completion_words(f"{dated_dir.as_posix()}/{{date}}_", local_listdir())
        assert words == [f"{dated_dir.as_posix()}/{{date}}_L2.png",
                         f"{dated_dir.as_posix()}/{{date}}_L3.png"]

    def test_shapes_apply_to_shell_words_too(self, dated_dir):
        words = completion_words(f"{dated_dir.as_posix()}/{{date}}", local_listdir())
        assert words == [f"{dated_dir.as_posix()}/{{date}}_"]


class TestConfiguredShapes:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        reset_placeholder_shapes()
        yield
        reset_placeholder_shapes()

    def _settings(self, tmp_path, body: str):
        cfg = tmp_path / ".juxt"      # hidden, so it is never a candidate
        cfg.mkdir(exist_ok=True)
        path = cfg / "settings.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_user_regex_is_used(self, tmp_path, monkeypatch):
        plots = tmp_path / "plots"
        plots.mkdir()
        for orbit, version in (("o12345", "v2"), ("o12346", "v3")):
            (plots / f"{orbit}_{version}.png").write_bytes(b"")
        settings = self._settings(tmp_path, "placeholders:\n  orbit: 'o\\d{5}'\n")
        monkeypatch.setattr("juxt.settings.SETTINGS_PATH", settings)
        assert _complete(f"{plots.as_posix()}/{{orbit}}").append == "_"

    def test_an_unconfigured_name_is_still_a_plain_placeholder(self, tmp_path, monkeypatch):
        plots = tmp_path / "plots"
        plots.mkdir()
        for orbit, version in (("o12345", "v2"), ("o12346", "v3")):
            (plots / f"{orbit}_{version}.png").write_bytes(b"")
        monkeypatch.setattr("juxt.settings.SETTINGS_PATH", tmp_path / ".juxt" / "none.yaml")
        assert _complete(f"{plots.as_posix()}/{{orbit}}").append == ".png"

    def test_user_shorthand_is_used(self, tmp_path, monkeypatch):
        plots = tmp_path / "plots"
        plots.mkdir()
        for cycle, run in (("2024-03", "a"), ("2024-04", "b")):
            (plots / f"{cycle}_{run}.png").write_bytes(b"")
        settings = self._settings(tmp_path, "placeholders:\n  cycle: yyyy-mm\n")
        monkeypatch.setattr("juxt.settings.SETTINGS_PATH", settings)
        assert _complete(f"{plots.as_posix()}/{{cycle}}").append == "_"

    def test_settings_file_is_never_written_by_completion(self, tmp_path, monkeypatch):
        missing = tmp_path / ".juxt" / "settings.yaml"
        monkeypatch.setattr("juxt.settings.SETTINGS_PATH", missing)
        placeholder_shapes()
        assert not missing.exists()

    def test_a_broken_settings_file_leaves_completion_working(
        self, tmp_path, monkeypatch, dated_dir
    ):
        settings = self._settings(tmp_path, "placeholders: [not, a, mapping\n")
        monkeypatch.setattr("juxt.settings.SETTINGS_PATH", settings)
        comp = _complete(f"{dated_dir.as_posix()}/{{date}}")
        assert comp.append == ".png"       # no shapes, but still completing
        assert len(comp.matches) == 4

    def test_built_in_names_come_from_the_settings_file(self, tmp_path, monkeypatch,
                                                        dated_dir):
        """Without the settings entry, `date` is just another placeholder name."""
        monkeypatch.setattr("juxt.settings.SETTINGS_PATH", tmp_path / ".juxt" / "no.yaml")
        assert _complete(f"{dated_dir.as_posix()}/{{date}}").append == ".png"
        assert placeholder_shapes() == {}
