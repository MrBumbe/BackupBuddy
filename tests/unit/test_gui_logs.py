"""Unit tests for the log viewer route helpers."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from gatekeeper.gui.routes.logs import _parse_log_lines


# Sample log lines in the gatekeeper log format.
_SAMPLE_LINES = textwrap.dedent("""\
    2026-06-08T10:00:01 DEBUG    gatekeeper.verify.nightly — Debug detail
    2026-06-08T10:00:02 INFO     gatekeeper.verify.nightly — Nightly verify started
    2026-06-08T10:00:03 WARNING  gatekeeper.cluster.join — Node timeout
    2026-06-08T10:00:04 ERROR    gatekeeper.watcher — File changed during fragmentation
    2026-06-08T10:00:05 INFO     gatekeeper.restore — Restore completed
    this is not a log line and should be skipped
    2026-06-08T10:00:06 INFO     gatekeeper.lifeboat.distributor — Lifeboat distributed
""")


@pytest.fixture()
def log_file(tmp_path: Path) -> Path:
    f = tmp_path / "gatekeeper.log"
    f.write_text(_SAMPLE_LINES, encoding="utf-8")
    return f


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    result = _parse_log_lines(str(tmp_path / "no-such-file.log"), 200, "INFO", None)
    assert result == []


def test_info_level_excludes_debug(log_file: Path) -> None:
    result = _parse_log_lines(str(log_file), 200, "INFO", None)
    levels = {r["level"] for r in result}
    assert "DEBUG" not in levels
    assert "INFO" in levels
    assert "WARNING" in levels
    assert "ERROR" in levels


def test_warning_level_excludes_info_and_debug(log_file: Path) -> None:
    result = _parse_log_lines(str(log_file), 200, "WARNING", None)
    levels = {r["level"] for r in result}
    assert "DEBUG" not in levels
    assert "INFO" not in levels
    assert "WARNING" in levels
    assert "ERROR" in levels


def test_component_filter_matches_verify(log_file: Path) -> None:
    result = _parse_log_lines(str(log_file), 200, "DEBUG", "verify")
    names = {r["name"] for r in result}
    assert all("verify" in n for n in names)
    assert "gatekeeper.cluster.join" not in names
    assert "gatekeeper.watcher" not in names


def test_component_filter_matches_exact_name(log_file: Path) -> None:
    # gatekeeper.watcher is an exact match for component="watcher"
    result = _parse_log_lines(str(log_file), 200, "DEBUG", "watcher")
    names = {r["name"] for r in result}
    assert names == {"gatekeeper.watcher"}


def test_n_cap_returns_last_n(log_file: Path) -> None:
    # There are 6 valid log lines (one non-matching line skipped).
    # With level=DEBUG and no component filter all 6 are returned.
    all_lines = _parse_log_lines(str(log_file), 200, "DEBUG", None)
    assert len(all_lines) == 6

    last_two = _parse_log_lines(str(log_file), 2, "DEBUG", None)
    assert len(last_two) == 2
    # Newest first: the last two lines written are restore and lifeboat.
    assert last_two[0]["name"] == "gatekeeper.lifeboat.distributor"
    assert last_two[1]["name"] == "gatekeeper.restore"


def test_results_are_newest_first(log_file: Path) -> None:
    result = _parse_log_lines(str(log_file), 200, "DEBUG", None)
    timestamps = [r["ts"] for r in result]
    assert timestamps == sorted(timestamps, reverse=True)


def test_non_matching_lines_skipped(log_file: Path) -> None:
    result = _parse_log_lines(str(log_file), 200, "DEBUG", None)
    messages = [r["msg"] for r in result]
    assert not any("not a log line" in m for m in messages)
