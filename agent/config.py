"""
Agent configuration: INI parsing and Pydantic validation for backup.cfg.
"""

from __future__ import annotations

import configparser
import fnmatch
import os
import re
import threading
import time as _time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel


# ── Exceptions ────────────────────────────────────────────────────────────────

class ConfigError(ValueError):
    """Raised for invalid or malformed agent configuration."""


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_duration(value: str) -> int:
    """Parse '1h', '30m', '24h' to seconds."""
    value = value.strip().lower()
    match = re.fullmatch(r"(\d+)(h|m|s)", value)
    if not match:
        raise ConfigError(f"Invalid duration {value!r} — expected e.g. '1h', '30m'")
    amount, unit = int(match.group(1)), match.group(2)
    return amount * {"h": 3600, "m": 60, "s": 1}[unit]


def _bool(value: str) -> bool:
    return value.strip().lower() in ("true", "1", "yes")


# ── Sub-models ────────────────────────────────────────────────────────────────

class ScheduleConfig(BaseModel):
    full_scan_seconds: int = 86400  # default 24h
    stability_seconds: int = 1800   # default 30 min — file must be idle before queuing


class NodeConfig(BaseModel):
    share_log: bool = False


class GatekeeperConnectionConfig(BaseModel):
    url: str                                                    # e.g. http://192.168.1.50:8081
    token: str                                                  # pre-shared token (plaintext, Phase 1 — ADR-017)
    name: str                                                   # agent display name shown in gatekeeper GUI
    lifeboat_path: str = "/etc/backup-buddy/lifeboat.enc"


class LifeboatServerConfig(BaseModel):
    enabled: bool = True
    port: int = 8082


# ── Top-level config ──────────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    schedule: ScheduleConfig = ScheduleConfig()
    backup_paths: list[str]
    excludes: list[str] = []
    node: NodeConfig = NodeConfig()
    gatekeeper: GatekeeperConnectionConfig
    lifeboat_server: LifeboatServerConfig = LifeboatServerConfig()


# ── Path validation ───────────────────────────────────────────────────────────

# Resolved at import time so tests can monkeypatch if needed.
_CRITICAL_PATH_PREFIXES: tuple[str, ...] = (
    "/etc",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
)


def _validate_backup_paths(paths: list[str]) -> None:
    """Validate backup paths: absolute, exist, are directories, not critical."""
    for path_str in paths:
        if not os.path.isabs(path_str):
            raise ConfigError(f"Backup path must be absolute: {path_str!r}")
        real = os.path.realpath(path_str)
        if not os.path.exists(real):
            raise ConfigError(f"Backup path does not exist: {path_str!r}")
        if not os.path.isdir(real):
            raise ConfigError(f"Backup path is not a directory: {path_str!r}")
        for prefix in _CRITICAL_PATH_PREFIXES:
            if real == prefix or real.startswith(prefix + os.sep):
                raise ConfigError(
                    f"Backup path {path_str!r} resolves to a system-critical "
                    f"location ({prefix!r}) and cannot be backed up"
                )


def _validate_exclude_patterns(patterns: list[str]) -> None:
    """Validate exclude patterns are non-empty, null-byte free, and valid globs."""
    for pat in patterns:
        if not pat:
            raise ConfigError("Exclude pattern must not be empty")
        if "\0" in pat:
            raise ConfigError(f"Exclude pattern contains null byte: {pat!r}")
        try:
            re.compile(fnmatch.translate(pat))
        except re.error as exc:
            raise ConfigError(
                f"Exclude pattern {pat!r} is not valid glob syntax: {exc}"
            ) from exc


# ── INI parsing ───────────────────────────────────────────────────────────────

def _parse_ini(parser: configparser.ConfigParser) -> AgentConfig:
    """Build AgentConfig from a loaded ConfigParser instance."""

    def sec(name: str) -> dict[str, str | None]:
        return dict(parser[name]) if parser.has_section(name) else {}

    # [schedule]
    sched_raw = sec("schedule")
    stability_raw = sched_raw.get("stability_minutes")
    if stability_raw is not None:
        try:
            stability_seconds = int(stability_raw.strip()) * 60
        except ValueError:
            raise ConfigError(
                f"[schedule] stability_minutes must be a plain integer, got {stability_raw!r}"
            )
    else:
        stability_seconds = 1800
    schedule = ScheduleConfig(
        full_scan_seconds=_parse_duration(sched_raw.get("full_scan", "24h")),
        stability_seconds=stability_seconds,
    )

    # [backup] — bare keys (paths), required
    if not parser.has_section("backup"):
        raise ConfigError("[backup] section is required")
    backup_paths = list(sec("backup").keys())
    if not backup_paths:
        raise ConfigError("[backup] must contain at least one path")

    # [exclude] — bare keys (glob patterns)
    excludes = list(sec("exclude").keys()) if parser.has_section("exclude") else []

    # [node]
    node_raw = sec("node")
    node = NodeConfig(
        share_log=_bool(node_raw.get("share_log", "false")),
    )

    # [gatekeeper] — required
    gk_raw = sec("gatekeeper")
    for required_key in ("url", "token", "name"):
        if not gk_raw.get(required_key):
            raise ConfigError(f"[gatekeeper] {required_key!r} is required")
    gatekeeper = GatekeeperConnectionConfig(
        url=gk_raw["url"],
        token=gk_raw["token"],
        name=gk_raw["name"],
        lifeboat_path=gk_raw.get("lifeboat_path", "/etc/backup-buddy/lifeboat.enc"),
    )

    # [lifeboat_server]
    lb_raw = sec("lifeboat_server")
    lifeboat_server = LifeboatServerConfig(
        enabled=_bool(lb_raw.get("enabled", "true")),
        port=int(lb_raw.get("port", 8082)),
    )

    return AgentConfig(
        schedule=schedule,
        backup_paths=backup_paths,
        excludes=excludes,
        node=node,
        gatekeeper=gatekeeper,
        lifeboat_server=lifeboat_server,
    )


# ── Public API ────────────────────────────────────────────────────────────────

def load_config(path: str | Path) -> AgentConfig:
    """Load and validate backup.cfg from *path*.

    Raises ConfigError on any validation failure.
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    # delimiters=('=',) — colon must not be a delimiter because Windows paths
    # (e.g. C:\\path) contain colons that would otherwise be misinterpreted.
    parser = configparser.ConfigParser(allow_no_value=True, delimiters=("=",))
    parser.optionxform = str  # preserve case: paths and patterns are case-sensitive

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

    _validate_backup_paths(cfg.backup_paths)
    _validate_exclude_patterns(cfg.excludes)

    return cfg


def watch_config(
    path: str | Path,
    on_reload: Callable[[AgentConfig], None],
    *,
    poll_interval: float = 5.0,
) -> Callable[[], None]:
    """Poll *path* for mtime changes and call *on_reload* on each valid change.

    Runs in a daemon thread. Returns a stop callable — call it to stop polling.
    Reload errors (malformed config) are silently ignored so the agent keeps
    running with the last valid configuration.
    """
    path = Path(path)
    stop_event = threading.Event()

    try:
        last_mtime: float = path.stat().st_mtime
    except FileNotFoundError:
        last_mtime = 0.0

    def _poll() -> None:
        nonlocal last_mtime
        while not stop_event.is_set():
            stop_event.wait(poll_interval)
            if stop_event.is_set():
                break
            try:
                current_mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if current_mtime != last_mtime:
                last_mtime = current_mtime
                try:
                    new_cfg = load_config(path)
                    on_reload(new_cfg)
                except ConfigError:
                    pass  # keep running with last valid config

    thread = threading.Thread(target=_poll, daemon=True)
    thread.start()

    return stop_event.set
