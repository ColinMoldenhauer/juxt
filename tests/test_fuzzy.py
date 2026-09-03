"""Unit tests for juxt/fuzzy.py."""
from __future__ import annotations

from juxt.fuzzy import fuzzy_filter, prefix_filter, score

COMMANDS = ["fit", "fit-height", "fit-width", "fullscreen", "grid",
            "grid-sharex", "grid-sharey", "info", "settings", "zoom"]
SENSORS = ["ASCAT", "SMAP", "SMOS"]


class TestScore:
    def test_non_match_scores_none(self):
        assert score("xyz", "SMAP") is None

    def test_out_of_order_characters_do_not_match(self):
        assert score("pms", "SMAP") is None

    def test_subsequence_matches(self):
        assert score("smp", "SMAP") is not None

    def test_exact_beats_prefix(self):
        assert score("fit", "fit") < score("fit", "fit-height")

    def test_prefix_beats_subsequence(self):
        assert score("in", "info") < score("in", "settings")

    def test_empty_query_matches_anything(self):
        assert score("", "whatever") is not None


class TestFuzzyFilter:
    def test_finds_smap_from_smp(self):
        assert fuzzy_filter("smp", SENSORS) == ["SMAP"]

    def test_matching_is_case_insensitive_both_ways(self):
        assert fuzzy_filter("SMO", SENSORS) == ["SMOS"]

    def test_drops_non_matching_entries(self):
        assert "ASCAT" not in fuzzy_filter("sm", SENSORS)

    def test_empty_query_keeps_pool_order(self):
        assert fuzzy_filter("", SENSORS) == SENSORS

    def test_prefix_matches_come_first(self):
        assert fuzzy_filter("f", COMMANDS)[0] == "fit"

    def test_word_boundary_beats_a_mid_word_hit(self):
        # 'h' after the hyphen reads as an initial; inside "fit-width" it does not
        assert fuzzy_filter("fh", COMMANDS) == ["fit-height", "fit-width"]

    def test_acronyms_reach_hyphenated_commands(self):
        assert fuzzy_filter("gsx", COMMANDS) == ["grid-sharex"]

    def test_shorter_candidate_wins_an_otherwise_equal_match(self):
        assert fuzzy_filter("fit", COMMANDS)[0] == "fit"

    def test_ties_keep_pool_order(self):
        assert fuzzy_filter("s", ["beta-s", "alpha-s"]) == ["beta-s", "alpha-s"]


class TestPrefixFilter:
    def test_only_prefixes_survive(self):
        assert prefix_filter("sm", SENSORS) == ["SMAP", "SMOS"]

    def test_subsequence_is_not_enough(self):
        assert prefix_filter("smp", SENSORS) == []

    def test_empty_query_keeps_pool_order(self):
        assert prefix_filter("", SENSORS) == SENSORS
