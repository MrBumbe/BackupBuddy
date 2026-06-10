"""
Manages a Tahoe-LAFS storage node as a background subprocess.

Storage nodes receive and store encrypted fragments for cluster peers.
Internal Tahoe details (FURLs, share counts, storage indices) never leave
this module in user-facing form. All Tahoe stdout/stderr is captured.
"""

import asyncio
import configparser
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT = 30  # seconds to wait for the node to become ready
_SHUTDOWN_TIMEOUT = 10  # seconds to wait for clean exit before kill


def _find_tahoe() -> str:
    """Locate the tahoe binary co-located with the running Python interpreter."""
    scripts_dir = os.path.dirname(sys.executable)
    for name in ("tahoe", "tahoe.exe"):
        candidate = os.path.join(scripts_dir, name)
        if os.path.isfile(candidate):
            return candidate
    found = shutil.which("tahoe")
    if found:
        return found
    raise RuntimeError(
        "tahoe binary not found in venv scripts directory or PATH. "
        "Ensure the BackupBuddy venv is active."
    )


_DEFAULT_WEB_PORT = 3456
_INTRODUCER_CONNECT_TIMEOUT = 10.0


def _parse_furl_locations(furl: str) -> list[tuple[str, int]]:
    """Extract (host, port) pairs from a Foolscap FURL for reachability checks.

    Handles single and multi-hint FURLs in both HOST:PORT and tcp:HOST:PORT form.
    Returns an empty list if the FURL cannot be parsed.
    """
    match = re.search(r'@([^/]+)/', furl)
    if not match:
        return []
    locations = []
    for hint in match.group(1).split(","):
        hint = hint.strip()
        if hint.startswith("tcp:"):
            hint = hint[4:]
        try:
            if hint.startswith("["):
                # IPv6 address
                bracket_end = hint.index("]")
                host = hint[1:bracket_end]
                port = int(hint[bracket_end + 2:])
            else:
                last_colon = hint.rfind(":")
                if last_colon < 0:
                    continue
                host = hint[:last_colon]
                port = int(hint[last_colon + 1:])
            locations.append((host, port))
        except (ValueError, IndexError):
            continue
    return locations


def _parse_tub_port(tub_port_str: str) -> int:
    """Extract the port number from a Tahoe tub.port value (e.g. 'tcp:12345')."""
    parts = tub_port_str.split(":")
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return 0


class StorageNode:
    """
    Manages a single Tahoe-LAFS storage node for the gatekeeper.

    The node stores encrypted fragments for cluster peers and acts as the
    local Tahoe gateway for the gatekeeper's own upload/download operations.

    reserved_space tells Tahoe to refuse writes that would leave fewer than
    this many bytes free in storage_dir — it is a floor, not a cap.
    Real quota enforcement lives in gatekeeper/storage/pool.py (task 1.5.2).

    web_port is the TCP port for the Tahoe HTTP gateway on localhost.
    TahoeClient connects to http://127.0.0.1:<web_port>.

    Peer connection to the introducer is verified in the two-node smoke test
    (task 1.16.2) rather than in unit tests, because unit tests mock the
    subprocess and cannot inspect live Tahoe peer state.

    Lifecycle:
        node = StorageNode(
            basedir="/var/lib/backup-buddy/storage-node",
            storage_dir="/mnt/pool/fragments",
            reserved_space=10 * 1024 ** 3,
        )
        node.create(introducer_furl="pb://...")   # one-time setup
        await node.start()
        ...
        await node.stop()
    """

    def __init__(
        self,
        basedir: str,
        storage_dir: str,
        reserved_space: int = 0,
        nickname: str = "backupbuddy-storage",
        web_port: int = _DEFAULT_WEB_PORT,
        shares_needed: int = 3,
        shares_happy: int = 5,
        shares_total: int = 5,
        hostname: str = "127.0.0.1",
    ) -> None:
        self.basedir = Path(os.path.realpath(basedir))
        self.storage_dir = Path(os.path.realpath(storage_dir))
        self.reserved_space = reserved_space
        self.nickname = nickname
        self.web_port = web_port
        self.shares_needed = shares_needed
        self.shares_happy = shares_happy
        self.shares_total = shares_total
        self.hostname = hostname
        self._process: asyncio.subprocess.Process | None = None
        self._tahoe: str = _find_tahoe()

    @property
    def node_url(self) -> str:
        """Base URL for the Tahoe HTTP gateway on this node."""
        return f"http://127.0.0.1:{self.web_port}"

    def create(self, introducer_furl: str) -> None:
        """
        Create the storage node directory and configure it.
        Idempotent: if the node already exists, only updates configuration.
        Raises RuntimeError if tahoe create-node fails.
        """
        if (self.basedir / "tahoe.cfg").exists():
            logger.info(
                "Storage node already exists at %s — updating config", self.basedir
            )
            self._configure(introducer_furl)
            return

        # Tahoe uses os.mkdir() which does not create parent directories.
        self.basedir.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Creating storage node at %s", self.basedir)
        result = subprocess.run(
            [self._tahoe, "create-node", f"--hostname={self.hostname}", str(self.basedir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create storage node (exit {result.returncode})"
            )

        self._configure(introducer_furl)
        logger.info("Storage node created")

    def _configure(self, introducer_furl: str) -> None:
        """
        Write storage node settings into tahoe.cfg.

        Uses configparser to read and update the existing file so that any
        settings written by tahoe create-node are preserved. Comments are
        lost on rewrite — acceptable for the gatekeeper's internal node dir.
        """
        cfg_path = self.basedir / "tahoe.cfg"
        config = configparser.ConfigParser()
        config.read(str(cfg_path))

        if not config.has_section("node"):
            config.add_section("node")
        config.set("node", "nickname", self.nickname)
        config.set("node", "web.port", f"tcp:{self.web_port}:interface=127.0.0.1")
        # Set tub.location to the real hostname so remote storage peers can connect.
        # Read the existing tub.port (written by tahoe create-node) to preserve
        # the assigned port; only the hostname part is ours to override.
        existing_tub_port = config.get("node", "tub.port", fallback="tcp:0")
        listen_port = _parse_tub_port(existing_tub_port)
        config.set("node", "tub.location", f"tcp:{self.hostname}:{listen_port}")

        if not config.has_section("client"):
            config.add_section("client")
        # Tahoe reads introducer.furl from [client] to connect at startup.
        config.set("client", "introducer.furl", introducer_furl)
        # ADR-018: k/n is a node-level setting; per-upload overrides are not
        # supported by the Tahoe HTTP API.  All uploads use these values.
        config.set("client", "shares.needed", str(self.shares_needed))
        config.set("client", "shares.happy",  str(self.shares_happy))
        config.set("client", "shares.total",  str(self.shares_total))

        if not config.has_section("storage"):
            config.add_section("storage")
        config.set("storage", "enabled", "true")
        config.set("storage", "readonly", "false")
        # Absolute path — Tahoe's get_config_path() correctly uses it as-is.
        config.set("storage", "storage_dir", str(self.storage_dir))
        config.set("storage", "reserved_space", str(self.reserved_space))

        self.basedir.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w") as f:
            config.write(f)

        logger.debug("Storage node config written to %s", cfg_path)

    async def start(self) -> None:
        """
        Start the storage node as a managed background subprocess.
        Raises RuntimeError if the node has not been created or does not
        become ready within the startup timeout.
        """
        if self.is_running():
            logger.info("Storage node is already running")
            return

        if not (self.basedir / "tahoe.cfg").exists():
            raise RuntimeError(
                "Storage node has not been created — call create() first"
            )

        cfg = configparser.ConfigParser()
        cfg.read(str(self.basedir / "tahoe.cfg"))
        tub_location = cfg.get("node", "tub.location", fallback="")

        from gatekeeper.tailscale import get_tailscale_ip as _get_tailscale_ip
        ts_ip = _get_tailscale_ip()
        # Always rebuild tub.location with the current Tailscale IP.
        # Peers on other home networks can only reach this node via Tailscale
        # (ADR-002).  Using the LAN IP here would make the published FURL
        # unreachable cross-VLAN, so Foolscap Reconnectors on peer nodes would
        # never connect and restores would fail the moment the introducer dies.
        try:
            existing_port_str = tub_location.rsplit(":", 1)[1]
            int(existing_port_str)  # validate it parses as a port number
            if ts_ip is None:
                raise RuntimeError(
                    "Tailscale is not running — cannot set storage node tub.location. "
                    "Ensure Tailscale is active before starting."
                )
            patched = f"tcp:{ts_ip}:{existing_port_str}"
        except (ValueError, IndexError):
            patched = tub_location  # empty or unparseable — leave unchanged
        if patched != tub_location:
            cfg.set("node", "tub.location", patched)
            with open(str(self.basedir / "tahoe.cfg"), "w") as _f:
                cfg.write(_f)
            logger.info(
                "Storage node tub.location set to Tailscale IP %s", ts_ip
            )

        if not await self._check_introducer_reachable(cfg):
            cached_count = self._count_cached_servers()
            if cached_count > 0:
                logger.warning(
                    "Storage coordinator temporarily unreachable — using cached server list "
                    "(%d servers). Backups may be slower or incomplete.",
                    cached_count,
                )
            else:
                logger.warning(
                    "Storage coordinator unreachable and no server cache found. "
                    "Backups will fail until the coordinator recovers."
                )

        logger.info("Starting storage node")
        # Discard all Tahoe output — reading from a PIPE without draining blocks
        # the subprocess's reactor once the OS buffer fills.  Readiness is
        # detected by polling the HTTP gateway port instead.
        self._process = await asyncio.create_subprocess_exec(
            self._tahoe, "run", "--allow-stdin-close", str(self.basedir),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        ready = await self._wait_for_ready()
        if not ready:
            await self.stop()
            raise RuntimeError(
                f"Storage node did not become ready within {_STARTUP_TIMEOUT}s"
            )

        logger.info("Storage node is running")

    async def stop(self) -> None:
        """
        Stop the storage node subprocess.
        Waits for clean exit; kills the process if it does not exit in time.
        """
        if self._process is None:
            return
        if self._process.returncode is not None:
            self._process = None
            return

        logger.info("Stopping storage node")
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Storage node did not exit cleanly — killing process")
            self._process.kill()
            await self._process.wait()

        logger.info("Storage node stopped")
        self._process = None

    def is_running(self) -> bool:
        """Return True if the subprocess is currently running."""
        return self._process is not None and self._process.returncode is None

    async def _wait_for_ready(self) -> bool:
        """
        Poll the Tahoe HTTP gateway until it accepts a TCP connection or timeout.
        Returns True if the port became reachable, False on timeout or early exit.
        """
        if self._process is None:
            return False

        deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            if self._process.returncode is not None:
                return False
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", self.web_port),
                    timeout=1.0,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.5)

        return False

    async def _check_introducer_reachable(
        self, cfg: configparser.ConfigParser
    ) -> bool:
        """Return True if the introducer can be reached via TCP within the timeout.

        Returns True when no introducer FURL is configured, or when the FURL
        cannot be parsed, so that ambiguous cases never block startup.
        """
        furl = cfg.get("client", "introducer.furl", fallback="").strip()
        if not furl:
            return True
        locations = _parse_furl_locations(furl)
        if not locations:
            return True
        for host, port in locations:
            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port),
                    timeout=_INTRODUCER_CONNECT_TIMEOUT,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return True
            except (OSError, asyncio.TimeoutError):
                continue
        return False

    def _count_cached_servers(self) -> int:
        """Return the number of storage servers in private/servers.yaml, or 0."""
        servers_yaml = self.basedir / "private" / "servers.yaml"
        if not servers_yaml.exists():
            return 0
        try:
            with open(servers_yaml) as f:
                data = yaml.safe_load(f) or {}
            return len(data.get("storage", {}))
        except Exception:
            return 0
