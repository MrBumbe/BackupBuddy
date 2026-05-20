"""
Gatekeeper configuration: INI parsing and Pydantic validation.
"""

from __future__ import annotations

import configparser
import os
import re
import signal
from datetime import time
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, field_validator


# ── Exceptions ────────────────────────────────────────────────────────────────

class ConfigError(ValueError):
    """Raised for invalid or malformed gatekeeper configuration."""


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_duration(value: str) -> int:
    """Parse '1h', '30m', '24h' to seconds."""
    value = value.strip().lower()
    match = re.fullmatch(r"(\d+)(h|m|s)", value)
    if not match:
        raise ConfigError(f"Invalid duration {value!r} — expected e.g. '1h', '30m'")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"h": 3600, "m": 60, "s": 1}[unit]


def _parse_time_str(value: str) -> time:
    """Parse 'HH:MM' to datetime.time."""
    try:
        h, m = value.strip().split(":")
        return time(int(h), int(m))
    except Exception:
        raise ConfigError(f"Invalid time {value!r} — expected 'HH:MM'")


def _parse_quota_bytes(value: str) -> int:
    """Parse '2000 GB', '1 TB' to bytes."""
    v = value.strip().upper()
    match = re.fullmatch(r"(\d+)\s*(MB|GB|TB)", v)
    if not match:
        raise ConfigError(f"Invalid quota {value!r} — expected e.g. '2000 GB'")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"MB": 1024**2, "GB": 1024**3, "TB": 1024**4}[unit]


def _bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


# ── Sub-models ────────────────────────────────────────────────────────────────

class NodeConfig(BaseModel):
    name: str
    display_name: str


class TahoeConfig(BaseModel):
    # FURL — internal only, never logged or shown to users
    introducer: str


class AdaptiveConfig(BaseModel):
    ratio: float = 0.33
    min_k: int = 2
    max_n: int = 20
    rebalance_time: time = time(3, 0)


class FragmentationConfig(BaseModel):
    profile: str = "adaptive"
    adaptive: AdaptiveConfig = AdaptiveConfig()

    @field_validator("profile")
    @classmethod
    def _validate_profile(cls, v: str) -> str:
        valid = {"balanced", "secure", "paranoid", "adaptive"}
        if v.lower() not in valid:
            raise ValueError(f"profile must be one of {sorted(valid)}, got {v!r}")
        return v.lower()


class StoragePoolEntry(BaseModel):
    path: str
    quota_bytes: int


class LifeboatConfig(BaseModel):
    enabled: bool = True
    interval_seconds: int = 3600  # default 1h
    distribute_to: str = "all_agents"


class WatcherConfig(BaseModel):
    stability_minutes: int = 30
    check_open_handles: bool = True
    cpu_priority: str = "lowest"
    io_priority: str = "idle"
    upload_concurrent: int = 2


class QuotaConfig(BaseModel):
    min_ratio: float = 1.0
    warn_ratio: float = 1.2


class RebalanceConfig(BaseModel):
    stability_days: int = 7
    hysteresis_nodes: int = 2
    daily_rebalance_pct: int = 3
    rebalance_time: time = time(3, 30)
    min_fragments_before_delete: bool = True
    notify_on_start: bool = True
    notify_on_complete: bool = True


class MaintenanceConfig(BaseModel):
    orphan_check_interval_seconds: int = 86400  # 24h
    orphan_grace_days: int = 30
    auto_clean: bool = True
    notify_on_clean: bool = True


class VerifyConfig(BaseModel):
    daily_check_time: time = time(4, 0)
    test_restore_enabled: bool = True
    test_restore_files: int = 3
    test_restore_path: str = "/tmp/buddy-verify/"
    lifeboat_max_age_hours: int = 6
    notify_on_success: bool = False
    notify_on_warning: bool = True
    notify_on_failure: bool = True
    notify_on_corrupt: bool = True


class AlertsConfig(BaseModel):
    min_connected_nodes: int = 3
    storage_warning_pct: int = 85
    storage_critical_pct: int = 95
    node_offline_after_seconds: int = 900  # default 15m


class NotifySmtpConfig(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = 587
    user: str = ""
    to: str = ""
    # password is NOT stored here — fetched from encrypted secrets store (task 1.5.3)


class NotifyWebhookConfig(BaseModel):
    enabled: bool = False
    # url is NOT stored here — fetched from encrypted secrets store (task 1.5.3)


class NotifyConfig(BaseModel):
    on_backup_success: bool = False
    on_backup_failure: bool = True
    on_storage_warning: bool = True
    on_node_offline: bool = True
    on_rebalance: bool = True
    smtp: NotifySmtpConfig = NotifySmtpConfig()
    webhook: NotifyWebhookConfig = NotifyWebhookConfig()


class WebConfig(BaseModel):
    enabled: bool = True
    port: int = 8080
    bind: str = "tailscale"  # literal selector, not an IP address


# ── Top-level config ──────────────────────────────────────────────────────────

class GatekeeperConfig(BaseModel):
    node: NodeConfig
    tahoe: TahoeConfig
    fragmentation: FragmentationConfig = FragmentationConfig()
    storage_pool: list[StoragePoolEntry]
    lifeboat: LifeboatConfig = LifeboatConfig()
    watcher: WatcherConfig = WatcherConfig()
    excludes: list[str] = []
    quota: QuotaConfig = QuotaConfig()
    rebalance: RebalanceConfig = RebalanceConfig()
    maintenance: MaintenanceConfig = MaintenanceConfig()
    verify: VerifyConfig = VerifyConfig()
    alerts: AlertsConfig = AlertsConfig()
    notify: NotifyConfig = NotifyConfig()
    web: WebConfig = WebConfig()
    # Populated at startup by gatekeeper/tailscale.py — None until resolved
    tailscale_ip: Optional[str] = None


# ── INI parsing ───────────────────────────────────────────────────────────────

def _parse_ini(parser: configparser.ConfigParser) -> GatekeeperConfig:
    """Build GatekeeperConfig from a loaded ConfigParser instance."""

    def sec(name: str) -> dict[str, str | None]:
        return dict(parser[name]) if parser.has_section(name) else {}

    # [node] — required
    node_raw = sec("node")
    try:
        node = NodeConfig(name=node_raw["name"], display_name=node_raw["display_name"])
    except KeyError as e:
        raise ConfigError(f"[node] missing required key: {e}") from e

    # [tahoe] — required
    tahoe_raw = sec("tahoe")
    if not tahoe_raw.get("introducer"):
        raise ConfigError("[tahoe] introducer is required")
    tahoe = TahoeConfig(introducer=tahoe_raw["introducer"])

    # [fragmentation]
    frag_raw = sec("fragmentation")
    adaptive_raw = {
        k[len("adaptive."):]: v
        for k, v in frag_raw.items()
        if k.startswith("adaptive.") and v is not None
    }
    adaptive = AdaptiveConfig(
        ratio=float(adaptive_raw.get("ratio", 0.33)),
        min_k=int(adaptive_raw.get("min_k", 2)),
        max_n=int(adaptive_raw.get("max_n", 20)),
        rebalance_time=(
            _parse_time_str(adaptive_raw["rebalance_time"])
            if "rebalance_time" in adaptive_raw
            else time(3, 0)
        ),
    )
    fragmentation = FragmentationConfig(
        profile=frag_raw.get("profile") or "adaptive",
        adaptive=adaptive,
    )

    # [storage-pool] — required, at least one entry
    pool_raw = sec("storage-pool")
    if not pool_raw:
        raise ConfigError("[storage-pool] must have at least one path entry")
    storage_pool: list[StoragePoolEntry] = []
    for path_str, quota_str in pool_raw.items():
        if quota_str is None:
            raise ConfigError(
                f"[storage-pool] entry {path_str!r} is missing a quota value (e.g. '2000 GB')"
            )
        storage_pool.append(
            StoragePoolEntry(path=path_str, quota_bytes=_parse_quota_bytes(quota_str))
        )

    # [lifeboat]
    lb_raw = sec("lifeboat")
    lifeboat = LifeboatConfig(
        enabled=_bool(lb_raw.get("enabled", "true")),
        interval_seconds=_parse_duration(lb_raw.get("interval", "1h")),
        distribute_to=lb_raw.get("distribute_to", "all_agents"),
    )

    # [watcher]
    w_raw = sec("watcher")
    watcher = WatcherConfig(
        stability_minutes=int(w_raw.get("stability_minutes", 30)),
        check_open_handles=_bool(w_raw.get("check_open_handles", "true")),
        cpu_priority=w_raw.get("cpu_priority", "lowest"),
        io_priority=w_raw.get("io_priority", "idle"),
        upload_concurrent=int(w_raw.get("upload_concurrent", 2)),
    )

    # [exclude] — bare keys, values are None
    excludes = list(sec("exclude").keys()) if parser.has_section("exclude") else []

    # [quota]
    q_raw = sec("quota")
    quota = QuotaConfig(
        min_ratio=float(q_raw.get("min_ratio", 1.0)),
        warn_ratio=float(q_raw.get("warn_ratio", 1.2)),
    )

    # [rebalance]
    reb_raw = sec("rebalance")
    rebalance = RebalanceConfig(
        stability_days=int(reb_raw.get("stability_days", 7)),
        hysteresis_nodes=int(reb_raw.get("hysteresis_nodes", 2)),
        daily_rebalance_pct=int(reb_raw.get("daily_rebalance_pct", 3)),
        rebalance_time=_parse_time_str(reb_raw.get("rebalance_time", "03:30")),
        min_fragments_before_delete=_bool(
            reb_raw.get("min_fragments_before_delete", "true")
        ),
        notify_on_start=_bool(reb_raw.get("notify_on_start", "true")),
        notify_on_complete=_bool(reb_raw.get("notify_on_complete", "true")),
    )

    # [maintenance]
    maint_raw = sec("maintenance")
    maintenance = MaintenanceConfig(
        orphan_check_interval_seconds=_parse_duration(
            maint_raw.get("orphan_check_interval", "24h")
        ),
        orphan_grace_days=int(maint_raw.get("orphan_grace_days", 30)),
        auto_clean=_bool(maint_raw.get("auto_clean", "true")),
        notify_on_clean=_bool(maint_raw.get("notify_on_clean", "true")),
    )

    # [verify]
    ver_raw = sec("verify")
    verify = VerifyConfig(
        daily_check_time=_parse_time_str(ver_raw.get("daily_check_time", "04:00")),
        test_restore_enabled=_bool(ver_raw.get("test_restore_enabled", "true")),
        test_restore_files=int(ver_raw.get("test_restore_files", 3)),
        test_restore_path=ver_raw.get("test_restore_path", "/tmp/buddy-verify/"),
        lifeboat_max_age_hours=int(ver_raw.get("lifeboat_max_age_hours", 6)),
        notify_on_success=_bool(ver_raw.get("notify_on_success", "false")),
        notify_on_warning=_bool(ver_raw.get("notify_on_warning", "true")),
        notify_on_failure=_bool(ver_raw.get("notify_on_failure", "true")),
        notify_on_corrupt=_bool(ver_raw.get("notify_on_corrupt", "true")),
    )

    # [alerts]
    al_raw = sec("alerts")
    alerts = AlertsConfig(
        min_connected_nodes=int(al_raw.get("min_connected_nodes", 3)),
        storage_warning_pct=int(al_raw.get("storage_warning_pct", 85)),
        storage_critical_pct=int(al_raw.get("storage_critical_pct", 95)),
        node_offline_after_seconds=_parse_duration(
            al_raw.get("node_offline_after", "15m")
        ),
    )

    # [notify], [notify.smtp], [notify.webhook]
    n_raw = sec("notify")
    smtp_raw = sec("notify.smtp")
    wh_raw = sec("notify.webhook")
    notify_smtp = NotifySmtpConfig(
        enabled=_bool(smtp_raw.get("enabled", "false")),
        host=smtp_raw.get("host", ""),
        port=int(smtp_raw.get("port", 587)),
        user=smtp_raw.get("user", ""),
        to=smtp_raw.get("to", ""),
        # password silently ignored if present — stored in encrypted secrets store
    )
    notify_webhook = NotifyWebhookConfig(
        enabled=_bool(wh_raw.get("enabled", "false")),
        # url silently ignored if present — stored in encrypted secrets store
    )
    notify = NotifyConfig(
        on_backup_success=_bool(n_raw.get("on_backup_success", "false")),
        on_backup_failure=_bool(n_raw.get("on_backup_failure", "true")),
        on_storage_warning=_bool(n_raw.get("on_storage_warning", "true")),
        on_node_offline=_bool(n_raw.get("on_node_offline", "true")),
        on_rebalance=_bool(n_raw.get("on_rebalance", "true")),
        smtp=notify_smtp,
        webhook=notify_webhook,
    )

    # [web]
    web_raw = sec("web")
    web = WebConfig(
        enabled=_bool(web_raw.get("enabled", "true")),
        port=int(web_raw.get("port", 8080)),
        bind=web_raw.get("bind", "tailscale"),
    )

    return GatekeeperConfig(
        node=node,
        tahoe=tahoe,
        fragmentation=fragmentation,
        storage_pool=storage_pool,
        lifeboat=lifeboat,
        watcher=watcher,
        excludes=excludes,
        quota=quota,
        rebalance=rebalance,
        maintenance=maintenance,
        verify=verify,
        alerts=alerts,
        notify=notify,
        web=web,
    )


def _validate_storage_pool_paths(entries: list[StoragePoolEntry]) -> None:
    """Verify storage pool paths are absolute, exist, and are directories."""
    for entry in entries:
        if not os.path.isabs(entry.path):
            raise ConfigError(
                f"Storage pool path must be absolute: {entry.path!r}"
            )
        real = os.path.realpath(entry.path)
        if not os.path.exists(real):
            raise ConfigError(
                f"Storage pool path does not exist: {entry.path!r}"
            )
        if not os.path.isdir(real):
            raise ConfigError(
                f"Storage pool path is not a directory: {entry.path!r}"
            )


# ── Public API ────────────────────────────────────────────────────────────────

def load_config(
    path: str | Path,
    *,
    tailscale_ip: str | None = None,
) -> GatekeeperConfig:
    """Load and validate gatekeeper.cfg from *path*.

    Raises ConfigError on any validation failure.
    *tailscale_ip* is optionally set now; the startup sequence populates it
    after calling gatekeeper/tailscale.py (task 1.5.1).
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    # delimiters=('=',) — colon must not be a delimiter because Windows paths
    # (e.g. C:\path) contain colons that would otherwise be misinterpreted.
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=("=",))
    parser.optionxform = str  # preserve case: paths and exclude patterns are case-sensitive

    try:
        parser.read(path, encoding="utf-8")
    except configparser.Error as exc:
        raise ConfigError(f"Failed to parse config file {path}: {exc}") from exc

    try:
        cfg = _parse_ini(parser)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"Invalid configuration in {path}: {exc}") from exc

    _validate_storage_pool_paths(cfg.storage_pool)

    if tailscale_ip is not None:
        cfg = cfg.model_copy(update={"tailscale_ip": tailscale_ip})

    return cfg


def install_sighup_handler(
    config_path: str | Path,
    on_reload: Callable[[GatekeeperConfig], None],
) -> None:
    """Register a SIGHUP handler that reloads config and passes result to *on_reload*.

    No-op on Windows (SIGHUP not available). Tests can call load_config() directly
    to exercise the reload path without sending a signal.
    """
    if not hasattr(signal, "SIGHUP"):
        return

    import logging
    _log = logging.getLogger(__name__)

    def _handler(signum: int, frame: object) -> None:
        try:
            new_cfg = load_config(config_path)
            on_reload(new_cfg)
            _log.info("Configuration reloaded from %s", config_path)
        except ConfigError as exc:
            _log.error("Configuration reload failed: %s", exc)

    signal.signal(signal.SIGHUP, _handler)  # type: ignore[attr-defined]
