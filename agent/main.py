"""
Agent startup sequence.

  1. Read backup.cfg
  2. Create GatekeeperClient
  3. Register with gatekeeper (non-fatal on failure — agent keeps running)
  4. Start config file watcher
  5. [stub] File watcher — task 1.6.2
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from agent.config import AgentConfig, ConfigError, load_config, watch_config
from agent.gatekeeper_client import GatekeeperClient, RegistrationError

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG = Path("/etc/backup-buddy/backup.cfg")


async def _run(config_path: Path) -> None:
    logger.info("BackupBuddy agent starting")

    try:
        config = load_config(config_path)
        logger.info("Configuration loaded from %s", config_path)
    except ConfigError as exc:
        logger.critical("Configuration error: %s", exc)
        sys.exit(1)

    client = GatekeeperClient(
        url=config.gatekeeper.url,
        token=config.gatekeeper.token,
        agent_name=config.gatekeeper.name,
        lifeboat_path=config.gatekeeper.lifeboat_path,
    )

    try:
        await client.register()
        logger.info(
            "Agent '%s' registered with gatekeeper at %s",
            config.gatekeeper.name,
            config.gatekeeper.url,
        )
    except RegistrationError as exc:
        logger.error("Registration failed: %s — will retry on next startup", exc)

    def _on_reload(new_cfg: AgentConfig) -> None:
        logger.info("Configuration reloaded from %s", config_path)

    stop_watch = watch_config(config_path, _on_reload)

    logger.info("Agent running — file watcher pending (task 1.6.2)")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        stop_watch()
        await client.aclose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="backupbuddy-agent",
        description="BackupBuddy agent node",
    )
    parser.add_argument(
        "--config",
        default=str(_DEFAULT_CONFIG),
        metavar="PATH",
        help=f"path to backup.cfg (default: {_DEFAULT_CONFIG})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="log level (default: INFO)",
    )
    return parser.parse_args()


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )


def main() -> None:
    args = _parse_args()
    _configure_logging(args.log_level)
    asyncio.run(_run(Path(args.config)))


if __name__ == "__main__":
    main()
