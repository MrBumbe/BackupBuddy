"""
Agent startup sequence.

  1. Read backup.cfg
  2. Create GatekeeperClient
  3. Register with gatekeeper (non-fatal on failure — agent keeps running)
  4. Start lifeboat HTTP server on LAN IP (if lifeboat_server.enabled)
  5. Start config file watcher (backup.cfg reload on change)
  6. Start file watcher with stability detection
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import secrets as _secrets_mod
import socket
import sys
from pathlib import Path

import psutil
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from agent.config import AgentConfig, ConfigError, load_config, watch_config
from agent.gatekeeper_client import GatekeeperClient, RegistrationError
from agent.watcher import FileWatcher

logger = logging.getLogger("agent.main")

_DEFAULT_CONFIG = Path("/etc/backup-buddy/backup.cfg")
_TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _get_lan_ip() -> str | None:
    """Return the best LAN IPv4 address for binding the lifeboat server.

    Scans all interfaces, returns the first private, non-loopback address
    that is not in the Tailscale CGNAT block (100.64.0.0/10).
    """
    for _iface, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family != socket.AF_INET:
                continue
            try:
                ip = ipaddress.ip_address(addr.address)
            except ValueError:
                continue
            if ip.is_loopback or ip in _TAILSCALE_CGNAT or not ip.is_private:
                continue
            return addr.address
    return None


def _create_lifeboat_app(
    config: AgentConfig,
    gk_client: GatekeeperClient,
) -> FastAPI:
    """Build a minimal FastAPI app for gatekeeper → agent lifeboat push."""
    app = FastAPI(title="BackupBuddy Agent Lifeboat", docs_url=None, redoc_url=None)
    expected_token = config.gatekeeper.token

    def _check_auth(request: Request) -> bool:
        auth = request.headers.get("authorization", "")
        return (
            auth.startswith("Bearer ")
            and _secrets_mod.compare_digest(auth[7:], expected_token)
        )

    @app.post("/lifeboat")
    async def store_lifeboat(request: Request) -> JSONResponse:
        if not _check_auth(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        data = await request.body()
        if not data:
            return JSONResponse({"error": "Empty body"}, status_code=400)

        try:
            gk_client.store_lifeboat(data)
        except Exception as exc:
            logger.error("Failed to store lifeboat bundle: %s", exc)
            return JSONResponse({"error": "Storage failed"}, status_code=500)

        return JSONResponse({"status": "ok"})

    @app.get("/lifeboat")
    async def get_lifeboat(request: Request) -> Response:
        if not _check_auth(request):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            data = gk_client.get_lifeboat()
            return Response(content=data, media_type="application/octet-stream")
        except FileNotFoundError:
            return JSONResponse(
                {"error": "No lifeboat bundle stored yet"}, status_code=404
            )

    return app


async def _upload_worker(
    queue: asyncio.Queue,
    client: GatekeeperClient,
    agent_name: str,
    watcher: FileWatcher,
) -> None:
    """Read file paths from the upload queue and send each file to the gatekeeper."""
    logger.info("Upload worker started")
    while True:
        file_path: str = await queue.get()
        path = Path(file_path)
        try:
            file_size = path.stat().st_size
            metadata = {
                "original_path": file_path,
                "agent_name": agent_name,
            }
            await client.send_fragment(path, metadata)
            logger.info("SUCCESS — uploaded file (%d bytes)", file_size)
        except (OSError, IOError) as exc:
            # File name logged on upload errors only — agent-local log, owner-approved
            # exception to SECURITY.md §6 so the user can identify which file failed.
            detail = os.strerror(exc.errno) if exc.errno is not None else str(exc)
            logger.error(
                "Failed to upload file %s: %s (%s)",
                path.name,
                type(exc).__name__,
                detail,
            )
            watcher.dequeue(file_path)
        except Exception as exc:
            logger.error(
                "Failed to upload file %s: unexpected error: %s",
                path.name,
                type(exc).__name__,
            )
            watcher.dequeue(file_path)
        finally:
            queue.task_done()


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

    # Determine lifeboat server binding before registering so the port is
    # included in the registration message.
    lifeboat_host: str | None = None
    lifeboat_port: int | None = None
    if config.lifeboat_server.enabled:
        lifeboat_host = _get_lan_ip()
        if lifeboat_host:
            lifeboat_port = config.lifeboat_server.port
            logger.info(
                "Lifeboat server will listen on %s:%d",
                lifeboat_host,
                lifeboat_port,
            )
        else:
            logger.warning(
                "Lifeboat server enabled but no LAN IP found — lifeboat server disabled"
            )

    try:
        await client.register(lifeboat_port=lifeboat_port)
        logger.info(
            "Agent '%s' registered with gatekeeper at %s",
            config.gatekeeper.name,
            config.gatekeeper.url,
        )
    except RegistrationError as exc:
        logger.error("Registration failed: %s — will retry on next startup", exc)

    upload_queue: asyncio.Queue[str] = asyncio.Queue()

    watcher = FileWatcher(
        backup_paths=config.backup_paths,
        stability_seconds=config.schedule.stability_seconds,
        exclude_patterns=config.excludes,
        queue=upload_queue,
    )

    def _on_reload(new_cfg: AgentConfig) -> None:
        logger.info("Configuration reloaded from %s", config_path)

    stop_watch = watch_config(config_path, _on_reload)

    logger.info("Agent running — file watcher and upload worker active")

    upload_worker_task = asyncio.create_task(
        _upload_worker(upload_queue, client, config.gatekeeper.name, watcher),
        name="upload-worker",
    )
    try:
        if lifeboat_host and lifeboat_port:
            lifeboat_app = _create_lifeboat_app(config, client)
            lb_cfg = uvicorn.Config(
                lifeboat_app,
                host=lifeboat_host,
                port=lifeboat_port,
                log_level="warning",
            )
            await asyncio.gather(
                uvicorn.Server(lb_cfg).serve(),
                watcher.run(),
                upload_worker_task,
            )
        else:
            await asyncio.gather(watcher.run(), upload_worker_task)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        upload_worker_task.cancel()
        await asyncio.gather(upload_worker_task, return_exceptions=True)
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
