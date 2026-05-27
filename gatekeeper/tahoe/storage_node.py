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
import shutil
import subprocess
import sys
from pathlib import Path

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
    ) -> None:
        self.basedir = Path(os.path.realpath(basedir))
        self.storage_dir = Path(os.path.realpath(storage_dir))
        self.reserved_space = reserved_space
        self.nickname = nickname
        self.web_port = web_port
        self.shares_needed = shares_needed
        self.shares_happy = shares_happy
        self.shares_total = shares_total
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
            [self._tahoe, "create-node", str(self.basedir)],
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

        logger.info("Starting storage node")
        self._process = await asyncio.create_subprocess_exec(
            self._tahoe, "run", str(self.basedir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
        Read stderr until Tahoe emits "client running", or until timeout.
        Returns True if the node became ready, False on timeout or early exit.
        """
        if self._process is None or self._process.stderr is None:
            return False

        deadline = asyncio.get_event_loop().time() + _STARTUP_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            if self._process.returncode is not None:
                return False
            try:
                line = await asyncio.wait_for(
                    self._process.stderr.readline(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if not line:
                break

            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                logger.debug("storage-node: %s", decoded)

            # Tahoe-LAFS logs "<NODETYPE> running" when fully started.
            # For client/storage nodes NODETYPE == "client" (see allmydata/client.py).
            if "client running" in decoded.lower():
                return True

        return False
