"""Argument-parsing tests for the juxt CLI."""
from __future__ import annotations

import sys

from juxt.__main__ import _parse_args


def parse(*argv) -> object:
    old = sys.argv
    sys.argv = ["juxt", *argv]
    try:
        return _parse_args()
    finally:
        sys.argv = old


class TestPanelFlags:
    def test_info_defaults_to_closed(self):
        assert parse("plots/").info is False

    def test_status_bar_defaults_to_shown(self):
        assert parse("plots/").status_bar is True

    def test_info_flag_opens_the_sidebar(self):
        assert parse("--info", "plots/").info is True

    def test_no_info_flag_keeps_it_closed(self):
        assert parse("--info", "--no-info", "plots/").info is False

    def test_no_status_bar_flag_hides_it(self):
        assert parse("--no-status-bar", "plots/").status_bar is False

    def test_status_bar_flag_shows_it(self):
        assert parse("--no-status-bar", "--status-bar", "plots/").status_bar is True


class TestKeysFlag:
    def test_defaults_to_closed(self):
        assert parse("plots/").keys is False

    def test_flag_opens_the_sidebar(self):
        assert parse("--keys", "plots/").keys is True

    def test_no_keys_keeps_it_closed(self):
        assert parse("--keys", "--no-keys", "plots/").keys is False
