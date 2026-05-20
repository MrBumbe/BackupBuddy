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


class NodeConfig(BaseModel):
    share_log: bool = False


# ── Top-level config ──────────────────────────────────────────────────────────

class AgentConfig(BaseModel):
    schedule: ScheduleConfig = ScheduleConfig()
    backup_paths: list[str]
    excludes: list[str] = []
    node: NodeConfig = NodeConfig()


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
    schedule = ScheduleConfig(
        full_scan_seconds=_parse_duration(sched_raw.get("full_scan", "24h")),
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

    return AgentConfig(
        schedule=schedule,
        backup_paths=backup_paths,
        excludes=excludes,
        node=node,
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
