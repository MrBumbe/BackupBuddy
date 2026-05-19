"""
Manages a Tahoe-LAFS introducer node as a background subprocess.

The FURL and other Tahoe internals never leave this module in user-facing form.
All Tahoe stdout/stderr is captured — nothing is forwarded to users.
"""

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_STARTUP_TIMEOUT = 30  # seconds to wait for introducer to become ready
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


class IntroducerNode:
    """
    Manages a single Tahoe-LAFS introducer node.

    Lifecycle:
        node = IntroducerNode("/var/lib/backup-buddy/introducer")
        node.create()           # one-time setup
        furl = await node.start()
        ...
        await node.stop()
    """

    def __init__(self, basedir: str) -> None:
        self.basedir = Path(os.path.realpath(basedir))
        self._process: asyncio.subprocess.Process | None = None
        self._tahoe: str = _find_tahoe()

    def create(self) -> None:
        """
        Create the introducer node directory.
        Idempotent: does nothing if the node is already created.
        Raises RuntimeError if tahoe create-introducer fails.
        """
        if (self.basedir / "tahoe.cfg").exists():
            logger.info("Introducer node already exists at %s", self.basedir)
            return

        logger.info("Creating introducer node at %s", self.basedir)
        result = subprocess.run(
            [self._tahoe, "create-introducer", str(self.basedir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create introducer node (exit {result.returncode})"
            )
        logger.info("Introducer node created")

    async def start(self) -> str:
        """
        Start the introducer node as a managed background subprocess.
        Returns the internal FURL string (used only by storage nodes and clients).
        Raises RuntimeError if the node does not become ready within the timeout.
        """
        if self.is_running():
            return self._read_furl()

        logger.info("Starting introducer node")
        self._process = await asyncio.create_subprocess_exec(
            self._tahoe, "run", str(self.basedir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Poll for the node to begin accepting connections.
        # Tahoe writes the FURL at create-introducer time; it's already on disk.
        # We detect readiness by reading the process output for the startup message.
        ready = await self._wait_for_ready()
        if not ready:
            await self.stop()
            raise RuntimeError(
                f"Introducer node did not become ready within {_STARTUP_TIMEOUT}s"
            )

        furl = self._read_furl()
        logger.info("Introducer node is running")
        return furl

    async def stop(self) -> None:
        """
        Stop the introducer node subprocess.
        Waits for clean exit; kills the process if it does not exit in time.
        """
        if self._process is None:
            return
        if self._process.returncode is not None:
            self._process = None
            return

        logger.info("Stopping introducer node")
        self._process.terminate()
        try:
            await asyncio.wait_for(self._process.wait(), timeout=_SHUTDOWN_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning("Introducer node did not exit cleanly — killing process")
            self._process.kill()
            await self._process.wait()

        logger.info("Introducer node stopped")
        self._process = None

    def is_running(self) -> bool:
        """Return True if the subprocess is currently running."""
        return self._process is not None and self._process.returncode is None

    def _read_furl(self) -> str:
        """Read the internal FURL from disk. Raises RuntimeError if not found."""
        furl_path = self.basedir / "private" / "introducer.furl"
        if not furl_path.exists():
            raise RuntimeError(
                "Introducer FURL file not found — has the node been created?"
            )
        return furl_path.read_text().strip()

    async def _wait_for_ready(self) -> bool:
        """
        Read stdout/stderr until Tahoe reports it is running,
        or until the timeout expires.
        Returns True if the node became ready, False on timeout.
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
                # Check if the FURL file exists — if so, the node is ready
                if (self.basedir / "private" / "introducer.furl").exists():
                    return True
                continue

            if not line:
                break

            decoded = line.decode("utf-8", errors="replace").strip()
            if decoded:
                logger.debug("introducer: %s", decoded)

            # Tahoe-LAFS logs this when the introducer is accepting connections
            if "introducer running" in decoded.lower() or "ready" in decoded.lower():
                return True

        # Final check: if the FURL file exists the node was at least created
        return (self.basedir / "private" / "introducer.furl").exists()
