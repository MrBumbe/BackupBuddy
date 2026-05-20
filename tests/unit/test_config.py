"""
Unit tests for gatekeeper/config.py.
"""

import signal
import textwrap
from datetime import time
from pathlib import Path

import pytest

from gatekeeper.config import (
    ConfigError,
    GatekeeperConfig,
    NotifySmtpConfig,
    NotifyWebhookConfig,
    _parse_duration,
    _parse_quota_bytes,
    _parse_time_str,
    install_sighup_handler,
    load_config,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_cfg(path: Path, content: str) -> Path:
    cfg_file = path / "gatekeeper.cfg"
    cfg_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return cfg_file


def _make_pool(tmp_path: Path) -> Path:
    pool = tmp_path / "buddy-storage"
    pool.mkdir()
    return pool


def _minimal_cfg(pool_path: str) -> str:
    return f"""\
        [node]
        name         = test-node
        display_name = Test Node

        [tahoe]
        introducer   = pb://nodeid@host:3456/secret

        [storage-pool]
        {pool_path} = 100 GB
    """


# ── Parsing helpers ───────────────────────────────────────────────────────────

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


class TestParseTimeStr:
    def test_zero_zero(self):
        assert _parse_time_str("00:00") == time(0, 0)

    def test_four_zero(self):
        assert _parse_time_str("04:00") == time(4, 0)

    def test_three_thirty(self):
        assert _parse_time_str("03:30") == time(3, 30)

    def test_invalid_raises(self):
        with pytest.raises(ConfigError):
            _parse_time_str("4am")


class TestParseQuotaBytes:
    def test_gb(self):
        assert _parse_quota_bytes("2000 GB") == 2000 * 1024**3

    def test_tb(self):
        assert _parse_quota_bytes("1 TB") == 1024**4

    def test_mb(self):
        assert _parse_quota_bytes("500 MB") == 500 * 1024**2

    def test_no_space(self):
        assert _parse_quota_bytes("400GB") == 400 * 1024**3

    def test_invalid_raises(self):
        with pytest.raises(ConfigError):
            _parse_quota_bytes("lots")


# ── load_config: valid config ─────────────────────────────────────────────────

class TestValidConfig:
    def test_minimal_config_parses(self, tmp_path):
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))
        cfg = load_config(cfg_file)
        assert isinstance(cfg, GatekeeperConfig)
        assert cfg.node.name == "test-node"
        assert cfg.node.display_name == "Test Node"
        assert "pb://" in cfg.tahoe.introducer
        assert len(cfg.storage_pool) == 1
        assert cfg.storage_pool[0].path == str(pool)
        assert cfg.storage_pool[0].quota_bytes == 100 * 1024**3

    def test_full_config_parses(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name         = anders-gatekeeper
            display_name = Anders home cluster

            [tahoe]
            introducer   = pb://nodeid@host:3456/secret

            [fragmentation]
            profile             = adaptive
            adaptive.ratio      = 0.33
            adaptive.min_k      = 2
            adaptive.max_n      = 20
            adaptive.rebalance_time = 03:00

            [storage-pool]
            {pool} = 2000 GB

            [lifeboat]
            enabled       = true
            interval      = 1h
            distribute_to = all_agents

            [watcher]
            stability_minutes  = 30
            check_open_handles = true
            cpu_priority       = lowest
            io_priority        = idle
            upload_concurrent  = 2

            [exclude]
            *.tmp
            *.part
            Thumbs.db
            .DS_Store

            [quota]
            min_ratio  = 1.0
            warn_ratio = 1.2

            [rebalance]
            stability_days      = 7
            hysteresis_nodes    = 2
            daily_rebalance_pct = 3
            rebalance_time      = 03:30
            min_fragments_before_delete = true
            notify_on_start     = true
            notify_on_complete  = true

            [maintenance]
            orphan_check_interval = 24h
            orphan_grace_days     = 30
            auto_clean            = true
            notify_on_clean       = true

            [verify]
            daily_check_time     = 04:00
            test_restore_enabled = true
            test_restore_files   = 3
            test_restore_path    = /tmp/buddy-verify/
            lifeboat_max_age_hours = 6
            notify_on_success    = false
            notify_on_warning    = true
            notify_on_failure    = true
            notify_on_corrupt    = true

            [alerts]
            min_connected_nodes  = 3
            storage_warning_pct  = 85
            storage_critical_pct = 95
            node_offline_after   = 15m

            [notify]
            on_backup_success  = false
            on_backup_failure  = true
            on_storage_warning = true
            on_node_offline    = true
            on_rebalance       = true

            [notify.smtp]
            enabled  = true
            host     = smtp.gmail.com
            port     = 587
            user     = anders@gmail.com
            to       = anders@gmail.com

            [notify.webhook]
            enabled  = true
            url      = https://discord.com/api/webhooks/ignored

            [web]
            enabled = true
            port    = 8080
            bind    = tailscale
        """
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)

        assert cfg.node.name == "anders-gatekeeper"
        assert cfg.fragmentation.profile == "adaptive"
        assert cfg.fragmentation.adaptive.ratio == 0.33
        assert cfg.fragmentation.adaptive.min_k == 2
        assert cfg.fragmentation.adaptive.rebalance_time == time(3, 0)
        assert cfg.lifeboat.interval_seconds == 3600
        assert cfg.watcher.stability_minutes == 30
        assert cfg.excludes == ["*.tmp", "*.part", "Thumbs.db", ".DS_Store"]
        assert cfg.rebalance.rebalance_time == time(3, 30)
        assert cfg.maintenance.orphan_check_interval_seconds == 86400
        assert cfg.verify.daily_check_time == time(4, 0)
        assert cfg.alerts.node_offline_after_seconds == 900
        assert cfg.notify.smtp.enabled is True
        assert cfg.notify.smtp.host == "smtp.gmail.com"
        assert cfg.notify.webhook.enabled is True
        assert cfg.web.port == 8080

    def test_exclude_patterns_preserve_case(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = _minimal_cfg(str(pool)) + "\n[exclude]\nThumbs.db\n.DS_Store\n"
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert "Thumbs.db" in cfg.excludes
        assert ".DS_Store" in cfg.excludes

    def test_defaults_applied_when_sections_absent(self, tmp_path):
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))
        cfg = load_config(cfg_file)
        assert cfg.fragmentation.profile == "adaptive"
        assert cfg.lifeboat.enabled is True
        assert cfg.lifeboat.interval_seconds == 3600
        assert cfg.watcher.stability_minutes == 30
        assert cfg.quota.min_ratio == 1.0
        assert cfg.rebalance.hysteresis_nodes == 2
        assert cfg.verify.test_restore_files == 3
        assert cfg.alerts.storage_warning_pct == 85
        assert cfg.web.port == 8080

    def test_tailscale_ip_set_when_provided(self, tmp_path):
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))
        cfg = load_config(cfg_file, tailscale_ip="100.64.0.1")
        assert cfg.tailscale_ip == "100.64.0.1"

    def test_tailscale_ip_none_by_default(self, tmp_path):
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))
        cfg = load_config(cfg_file)
        assert cfg.tailscale_ip is None


# ── load_config: missing required fields ──────────────────────────────────────

class TestMissingRequiredFields:
    def test_missing_config_file_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nonexistent.cfg")

    def test_missing_node_name_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            display_name = Test

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="name"):
            load_config(cfg_file)

    def test_missing_node_display_name_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name = test-node

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="display_name"):
            load_config(cfg_file)

    def test_missing_tahoe_introducer_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="introducer"):
            load_config(cfg_file)

    def test_missing_storage_pool_section_raises(self, tmp_path):
        content = """\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="storage-pool"):
            load_config(cfg_file)

    def test_empty_storage_pool_section_raises(self, tmp_path):
        content = """\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="storage-pool"):
            load_config(cfg_file)


# ── load_config: invalid values ───────────────────────────────────────────────

class TestInvalidValues:
    def test_invalid_fragmentation_profile_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = _minimal_cfg(str(pool)) + "\n[fragmentation]\nprofile = ultra\n"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError):
            load_config(cfg_file)

    def test_invalid_lifeboat_interval_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = _minimal_cfg(str(pool)) + "\n[lifeboat]\ninterval = forever\n"
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError):
            load_config(cfg_file)

    def test_invalid_quota_format_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool} = lots
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError):
            load_config(cfg_file)


# ── load_config: storage pool path validation ─────────────────────────────────

class TestStoragePoolPathValidation:
    def test_relative_path_raises(self, tmp_path):
        content = """\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            relative/path = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="absolute"):
            load_config(cfg_file)

    def test_nonexistent_path_raises(self, tmp_path):
        nonexistent = tmp_path / "does" / "not" / "exist"
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {nonexistent} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="does not exist"):
            load_config(cfg_file)

    def test_path_that_is_file_not_dir_raises(self, tmp_path):
        pool = tmp_path / "not-a-dir"
        pool.write_text("file")
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="not a directory"):
            load_config(cfg_file)

    def test_multiple_pool_paths(self, tmp_path):
        pool1 = tmp_path / "pool1"
        pool2 = tmp_path / "pool2"
        pool1.mkdir()
        pool2.mkdir()
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool1} = 500 GB
            {pool2} = 200 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert len(cfg.storage_pool) == 2
        assert cfg.storage_pool[0].quota_bytes == 500 * 1024**3
        assert cfg.storage_pool[1].quota_bytes == 200 * 1024**3


# ── TahoeConfig: run_introducer ───────────────────────────────────────────────

class TestTahoeRunIntroducer:
    def test_run_introducer_defaults_to_false(self, tmp_path):
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))
        cfg = load_config(cfg_file)
        assert cfg.tahoe.run_introducer is False

    def test_run_introducer_true_without_furl_is_valid(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            run_introducer = true

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert cfg.tahoe.run_introducer is True
        assert cfg.tahoe.introducer == ""

    def test_run_introducer_false_without_furl_raises(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            run_introducer = false

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        with pytest.raises(ConfigError, match="introducer"):
            load_config(cfg_file)

    def test_run_introducer_true_with_furl_is_valid(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            run_introducer = true
            introducer     = pb://nodeid@host:3456/secret

            [storage-pool]
            {pool} = 100 GB
        """
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert cfg.tahoe.run_introducer is True
        assert "pb://" in cfg.tahoe.introducer


# ── Secrets not stored ────────────────────────────────────────────────────────

class TestSecretsNotStored:
    def test_smtp_password_not_in_model(self):
        """NotifySmtpConfig must not have a password field."""
        assert not hasattr(NotifySmtpConfig(), "password")

    def test_webhook_url_not_in_model(self):
        """NotifyWebhookConfig must not have a url field."""
        assert not hasattr(NotifyWebhookConfig(), "url")

    def test_smtp_password_in_cfg_file_ignored(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = textwrap.dedent(f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool} = 100 GB

            [notify.smtp]
            enabled  = true
            host     = smtp.example.com
            port     = 587
            user     = user@example.com
            to       = user@example.com
            password = super-secret-ignored
        """)
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert not hasattr(cfg.notify.smtp, "password")
        assert cfg.notify.smtp.host == "smtp.example.com"

    def test_webhook_url_in_cfg_file_ignored(self, tmp_path):
        pool = _make_pool(tmp_path)
        content = textwrap.dedent(f"""\
            [node]
            name         = n
            display_name = N

            [tahoe]
            introducer = pb://x@host:1234/s

            [storage-pool]
            {pool} = 100 GB

            [notify.webhook]
            enabled = true
            url     = https://discord.com/api/webhooks/should-be-ignored
        """)
        cfg_file = _write_cfg(tmp_path, content)
        cfg = load_config(cfg_file)
        assert not hasattr(cfg.notify.webhook, "url")
        assert cfg.notify.webhook.enabled is True


# ── SIGHUP handler ────────────────────────────────────────────────────────────

class TestSighupHandler:
    @pytest.mark.skipif(
        not hasattr(signal, "SIGHUP"),
        reason="SIGHUP not available on this platform",
    )
    def test_sighup_calls_on_reload(self, tmp_path):
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))

        reloaded: list[GatekeeperConfig] = []
        install_sighup_handler(cfg_file, reloaded.append)

        import os
        os.kill(os.getpid(), signal.SIGHUP)

        assert len(reloaded) == 1
        assert isinstance(reloaded[0], GatekeeperConfig)

    def test_install_sighup_handler_noop_on_windows(self, tmp_path, monkeypatch):
        """install_sighup_handler must not raise on platforms without SIGHUP."""
        monkeypatch.delattr(signal, "SIGHUP", raising=False)
        pool = _make_pool(tmp_path)
        cfg_file = _write_cfg(tmp_path, _minimal_cfg(str(pool)))
        install_sighup_handler(cfg_file, lambda _: None)  # must not raise
