"""
Unit tests for agent/config.py.
"""

import textwrap
import time
from pathlib import Path

import pytest

from agent.config import (
    AgentConfig,
    ConfigError,
    NodeConfig,
    ScheduleConfig,
    _parse_duration,
    load_config,
    watch_config,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_cfg(path: Path, content: str) -> Path:
    cfg_file = path / "backup.cfg"
    cfg_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return cfg_file


def _make_backup_dir(tmp_path: Path, name: str = "documents") -> Path:
    d = tmp_path / name
    d.mkdir()
    return d


_GATEKEEPER_SECTION = """\
[gatekeeper]
url = http://192.168.1.50:8081
token = test-token
name = test-agent
"""


def _minimal_cfg(backup_path: str) -> str:
    return f"""\
        [backup]
        {backup_path}

        {_GATEKEEPER_SECTION}
    """


# ── _parse_duration ───────────────────────────────────────────────────────────

class TestParseDuration:
    def test_hours(self):
        assert _parse_duration("1h") == 3600

    def test_minutes(self):
        assert _parse_duration("30m") == 1800

    def test_24h(self):
        assert _parse_duration("24h") == 86400

    def test_seconds(self):
        assert _parse_duration("90s") == 90

    def test_invalid_raises(self):
        with pytest.raises(ConfigError):
            _parse_duration("2 hours")

    def test_invalid_unit_raises(self):
        with pytest.raises(ConfigError):
            _parse_duration("5d")


# ── Valid config ──────────────────────────────────────────────────────────────

class TestValidConfig:
    def test_minimal_config_parses(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(d)))
        cfg = load_config(cfg_file)
        assert isinstance(cfg, AgentConfig)
        assert cfg.backup_paths == [str(d)]

    def test_full_config_parses(self, tmp_path):
        docs = _make_backup_dir(tmp_path, "documents")
        pics = _make_backup_dir(tmp_path, "pictures")
        content = f"""\
            [schedule]
            full_scan = 12h

            [backup]
            {docs}
            {pics}

            [exclude]
            *.tmp
            *.part
            ~$*

            [node]
            share_log = true

            {_GATEKEEPER_SECTION}
        """
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert cfg.schedule.full_scan_seconds == 43200
        assert str(docs) in cfg.backup_paths
        assert str(pics) in cfg.backup_paths
        assert len(cfg.backup_paths) == 2
        assert cfg.excludes == ["*.tmp", "*.part", "~$*"]
        assert cfg.node.share_log is True

    def test_defaults_when_optional_sections_absent(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(d)))
        cfg = load_config(cfg_file)
        assert cfg.schedule.full_scan_seconds == 86400
        assert cfg.excludes == []
        assert cfg.node.share_log is False

    def test_share_log_false_by_default(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(d)))
        cfg = load_config(cfg_file)
        assert cfg.node.share_log is False

    def test_exclude_patterns_preserve_case(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        content = _minimal_cfg(str(d)) + "\n[exclude]\nThumbs.db\n.DS_Store\n"
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert "Thumbs.db" in cfg.excludes
        assert ".DS_Store" in cfg.excludes

    def test_common_exclude_patterns_accepted(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        content = f"""\
            [backup]
            {d}

            [exclude]
            *.tmp
            *.part
            ~$*
            *.db-journal
            Thumbs.db
            .DS_Store

            {_GATEKEEPER_SECTION}
        """
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert len(cfg.excludes) == 6


# ── Missing required fields ───────────────────────────────────────────────────

class TestMissingRequiredFields:
    def test_missing_config_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.cfg")

    def test_missing_backup_section_raises(self, tmp_path):
        cfg_file = _write_cfg(tmp_path, "[schedule]\nfull_scan = 24h\n")
        with pytest.raises(ConfigError, match=r"\[backup\]"):
            load_config(cfg_file)

    def test_empty_backup_section_raises(self, tmp_path):
        cfg_file = _write_cfg(tmp_path, "[backup]\n")
        with pytest.raises(ConfigError, match=r"\[backup\]"):
            load_config(cfg_file)


# ── Backup path validation ────────────────────────────────────────────────────

class TestBackupPathValidation:
    def test_relative_path_raises(self, tmp_path):
        content = f"[backup]\nrelative/path\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="absolute"):
            load_config(cfg_file)

    def test_nonexistent_path_raises(self, tmp_path):
        nonexistent = tmp_path / "does" / "not" / "exist"
        content = f"[backup]\n{nonexistent}\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="does not exist"):
            load_config(cfg_file)

    def test_file_not_directory_raises(self, tmp_path):
        not_a_dir = tmp_path / "file.txt"
        not_a_dir.write_text("hello")
        content = f"[backup]\n{not_a_dir}\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="not a directory"):
            load_config(cfg_file)

    @pytest.mark.skipif(
        not Path("/etc").exists(),
        reason="/etc does not exist on this platform",
    )
    def test_critical_path_etc_raises(self, tmp_path):
        content = f"[backup]\n/etc\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="system-critical"):
            load_config(cfg_file)

    @pytest.mark.skipif(
        not Path("/proc").exists(),
        reason="/proc does not exist on this platform",
    )
    def test_critical_path_proc_raises(self, tmp_path):
        content = f"[backup]\n/proc\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="system-critical"):
            load_config(cfg_file)

    def test_multiple_paths_all_validated(self, tmp_path):
        good = _make_backup_dir(tmp_path, "good")
        bad = tmp_path / "does_not_exist"
        content = f"[backup]\n{good}\n{bad}\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="does not exist"):
            load_config(cfg_file)


# ── Exclude pattern validation ────────────────────────────────────────────────

class TestExcludePatternValidation:
    def test_null_byte_in_pattern_raises(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        content = f"[backup]\n{d}\n\n[exclude]\n*.tmp\0bad\n\n{_GATEKEEPER_SECTION}"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="null byte"):
            load_config(cfg_file)

    def test_invalid_full_scan_duration_raises(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        content = f"[schedule]\nfull_scan = never\n\n[backup]\n{d}\n"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError):
            load_config(cfg_file)


# ── watch_config ──────────────────────────────────────────────────────────────

class TestWatchConfig:
    def test_reload_called_when_file_changes(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        d2 = _make_backup_dir(tmp_path, "pictures")
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(d)))

        reloaded: list[AgentConfig] = []
        stop = watch_config(cfg_file, reloaded.append, poll_interval=0.05)

        try:
            # Give the watcher thread a moment to start, then modify the file.
            time.sleep(0.1)
            new_content = f"[backup]\n{d}\n{d2}\n\n{_GATEKEEPER_SECTION}"
            cfg_file.write_text(textwrap.dedent(new_content), encoding="utf-8")
            # Wait for the watcher to pick up the change.
            deadline = time.monotonic() + 2.0
            while not reloaded and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop()

        assert len(reloaded) >= 1
        assert str(d2) in reloaded[-1].backup_paths

    def test_stop_halts_polling(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(d)))

        call_count: list[int] = [0]

        def _on_reload(_: AgentConfig) -> None:
            call_count[0] += 1

        stop = watch_config(cfg_file, _on_reload, poll_interval=0.05)
        stop()

        # Modify the file after stopping — should not trigger callback.
        time.sleep(0.2)
        d2 = _make_backup_dir(tmp_path, "after_stop")
        cfg_file.write_text(f"[backup]\n{d}\n{d2}\n", encoding="utf-8")
        time.sleep(0.2)

        assert call_count[0] == 0

    def test_invalid_reload_does_not_crash_watcher(self, tmp_path):
        d = _make_backup_dir(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(d)))

        reloaded: list[AgentConfig] = []
        stop = watch_config(cfg_file, reloaded.append, poll_interval=0.05)

        try:
            time.sleep(0.1)
            # Write a malformed config — watcher must not crash.
            cfg_file.write_text("[backup]\n", encoding="utf-8")
            time.sleep(0.2)
            # Now write a valid config — watcher should recover.
            d2 = _make_backup_dir(tmp_path, "recovered")
            cfg_file.write_text(
                f"[backup]\n{d}\n{d2}\n\n{_GATEKEEPER_SECTION}", encoding="utf-8"
            )
            deadline = time.monotonic() + 2.0
            while not reloaded and time.monotonic() < deadline:
                time.sleep(0.05)
        finally:
            stop()

        assert len(reloaded) >= 1
