"""Tests for juxt/settings.py, focused on the highlight format parser."""
from __future__ import annotations

import pytest

from juxt.settings import (
    DEFAULT_HIGHLIGHT,
    DEFAULT_HIGHLIGHT_CANDIDATES,
    load_settings,
    parse_highlight,
)


class TestParseHighlight:
    def test_default_spec(self):
        hl = parse_highlight(DEFAULT_HIGHLIGHT)
        assert (hl.color, hl.prefix, hl.suffix, hl.raw) == ("#6af", "", "", None)

    def test_delimiters(self):
        hl = parse_highlight(DEFAULT_HIGHLIGHT_CANDIDATES)
        assert (hl.color, hl.prefix, hl.suffix) == ("#6af", "[", "]")

    def test_style_flags(self):
        hl = parse_highlight("bold italic underline #f80:» {} «")
        assert (hl.bold, hl.italic, hl.underline) == (True, True, True)
        assert (hl.color, hl.prefix, hl.suffix) == ("#f80", "» ", " «")

    def test_colour_name(self):
        assert parse_highlight("red:{}").color == "red"

    def test_template_only(self):
        hl = parse_highlight("[{}]")
        assert hl.color is None and (hl.prefix, hl.suffix) == ("[", "]")

    def test_angle_brackets_are_delimiters_not_markup(self):
        hl = parse_highlight("#0f0:<{}>")
        assert hl.raw is None and (hl.prefix, hl.suffix) == ("<", ">")

    def test_html_marker_gives_raw_template(self):
        hl = parse_highlight('html:<b>[{}]</b>')
        assert hl.raw == "<b>[{}]</b>"

    def test_missing_placeholder_falls_back(self):
        assert parse_highlight("#6af:no-placeholder").spec == DEFAULT_HIGHLIGHT

    def test_fallback_is_configurable(self):
        hl = parse_highlight("broken", DEFAULT_HIGHLIGHT_CANDIDATES)
        assert hl.spec == DEFAULT_HIGHLIGHT_CANDIDATES

    def test_unknown_style_token_is_treated_as_template(self):
        hl = parse_highlight("12px solid:{}")   # not a colour, not a flag
        assert hl.color is None
        assert (hl.prefix, hl.suffix) == ("12px solid:", "")

    def test_bare_word_is_taken_as_a_colour_name(self):
        assert parse_highlight("darkorange:{}").color == "darkorange"


class TestLoadHighlightSettings:
    def _write(self, tmp_path, body: str):
        path = tmp_path / "settings.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def test_defaults_when_file_is_created(self, tmp_path):
        s = load_settings(tmp_path / "settings.yaml")
        assert s.highlight.spec == DEFAULT_HIGHLIGHT
        assert s.highlight_candidates.spec == DEFAULT_HIGHLIGHT_CANDIDATES

    def test_generated_file_round_trips(self, tmp_path):
        path = tmp_path / "settings.yaml"
        load_settings(path)                      # writes the default template
        s = load_settings(path)                  # reads it back
        assert s.highlight.spec == DEFAULT_HIGHLIGHT
        assert s.highlight_candidates.spec == DEFAULT_HIGHLIGHT_CANDIDATES

    def test_per_context_specs(self, tmp_path):
        path = self._write(tmp_path, 'highlight:\n'
                                     '  selected: "red:{}"\n'
                                     '  candidates: "bold:<{}>"\n')
        s = load_settings(path)
        assert s.highlight.color == "red"
        assert s.highlight_candidates.bold and s.highlight_candidates.prefix == "<"

    def test_single_string_sets_both(self, tmp_path):
        path = self._write(tmp_path, 'highlight: "#f80:«{}»"\n')
        s = load_settings(path)
        assert s.highlight.spec == s.highlight_candidates.spec == "#f80:«{}»"

    def test_missing_section_is_appended(self, tmp_path):
        path = self._write(tmp_path, "seek:\n  greedy: false\n")
        load_settings(path)
        assert "highlight:" in path.read_text(encoding="utf-8")

    def test_invalid_spec_keeps_defaults(self, tmp_path):
        path = self._write(tmp_path, 'highlight:\n  candidates: "nonsense"\n')
        s = load_settings(path)
        assert s.highlight_candidates.spec == DEFAULT_HIGHLIGHT_CANDIDATES


class TestSeekFuzzy:
    def test_defaults_to_on(self, tmp_path):
        from juxt.settings import load_settings

        p = tmp_path / "settings.yaml"
        p.write_text("seek:\n  greedy: true\n", encoding="utf-8")
        assert load_settings(p, write=False).seek_fuzzy is True

    def test_can_be_switched_off(self, tmp_path):
        from juxt.settings import load_settings

        p = tmp_path / "settings.yaml"
        p.write_text("seek:\n  fuzzy: false\n", encoding="utf-8")
        assert load_settings(p, write=False).seek_fuzzy is False

    def test_greedy_is_read_alongside_it(self, tmp_path):
        from juxt.settings import load_settings

        p = tmp_path / "settings.yaml"
        p.write_text("seek:\n  greedy: false\n  fuzzy: false\n", encoding="utf-8")
        s = load_settings(p, write=False)
        assert (s.seek_greedy, s.seek_fuzzy) == (False, False)

    def test_generated_template_documents_it(self, tmp_path):
        from juxt.settings import ensure_settings_file

        p = ensure_settings_file(tmp_path / "settings.yaml")
        assert "fuzzy: true" in p.read_text(encoding="utf-8")
