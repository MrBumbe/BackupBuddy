"""
GatekeeperClient — agent-side interface for agent→gatekeeper communication.

HTTP methods (register, send_fragment) POST to the gatekeeper's LAN agent
API at the URL configured in backup.cfg [gatekeeper].

Lifeboat methods (store_lifeboat, get_lifeboat) are local file operations.
The gatekeeper pushes encrypted bundles to the agent via the agent's own
HTTP endpoint, which calls store_lifeboat() as a handler.  The transport
for that direction is implemented in task 1.8.3 — see the note there.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_LIFEBOAT_PATH = Path("/etc/backup-buddy/lifeboat.enc")

_HTTP_DESCRIPTIONS: dict[int, str] = {
    401: "authentication failed",
    403: "access denied",
    404: "endpoint not found",
    409: "conflict",
    507: "insufficient disk space",
}


class RegistrationError(Exception):
    """Raised when the agent fails to register with the gatekeeper."""


class GatekeeperClient:
    """Async HTTP client and local lifeboat store for one agent."""

    def __init__(
        self,
        url: str,
        token: str,
        agent_name: str,
        lifeboat_path: Path | str = _DEFAULT_LIFEBOAT_PATH,
    ) -> None:
        self._url = url.rstrip("/")
        self._agent_name = agent_name
        self._lifeboat_path = Path(lifeboat_path)
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=30.0),
        )

    # ── HTTP operations (agent → gatekeeper) ──────────────────────────────────

    async def register(self, lifeboat_port: int | None = None) -> None:
        """Announce this agent to the gatekeeper.

        Args:
            lifeboat_port: Port the agent's lifeboat HTTP server is listening on.
                           If provided, the gatekeeper records the lifeboat push URL.

        Raises RegistrationError if the gatekeeper rejects the request or is
        unreachable.  Callers should treat this as non-fatal and retry later.
        """
        payload: dict = {"agent_name": self._agent_name}
        if lifeboat_port is not None:
            payload["lifeboat_port"] = lifeboat_port
        try:
            resp = await self._client.post(
                f"{self._url}/api/agents/register",
                json=payload,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RegistrationError(
                f"Gatekeeper rejected registration (HTTP {exc.response.status_code})"
            ) from exc
        except httpx.RequestError as exc:
            raise RegistrationError(
                f"Cannot reach gatekeeper at {self._url}: {type(exc).__name__}"
            ) from exc

    async def send_fragment(self, file_path: Path, metadata: dict) -> None:
        """Stream a file to the gatekeeper for upload without loading it into memory.

        Metadata is passed as a JSON-encoded header so the body can be a raw
        byte stream.  Raises IOError on transport or HTTP failure.
        """
        file_size = file_path.stat().st_size
        try:
            resp = await self._client.post(
                f"{self._url}/api/agents/fragments",
                content=self._iter_file(file_path),
                headers={
                    "X-Fragment-Metadata": json.dumps(metadata),
                    "Content-Length": str(file_size),
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            desc = _HTTP_DESCRIPTIONS.get(code, "server error")
            raise IOError(f"HTTP {code} — {desc}") from exc
        except httpx.HTTPError as exc:
            raise IOError(f"connection error: {type(exc).__name__}") from exc

    @staticmethod
    async def _iter_file(path: Path, chunk_size: int = 65536):
        """Yield file content in chunks without loading the whole file into memory."""
        loop = asyncio.get_running_loop()
        with open(path, "rb") as f:
            while True:
                chunk = await loop.run_in_executor(None, f.read, chunk_size)
                if not chunk:
                    break
                yield chunk

    # ── Local file operations (gatekeeper → agent lifeboat store) ─────────────

    def store_lifeboat(self, encrypted_bundle: bytes) -> None:
        """Write the encrypted lifeboat bundle to disk with 0600 permissions.

        Called by the agent's lifeboat HTTP endpoint (task 1.8.3) when the
        gatekeeper distributes a new bundle.
        """
        self._lifeboat_path.parent.mkdir(parents=True, exist_ok=True)
        self._lifeboat_path.write_bytes(encrypted_bundle)
        try:
            os.chmod(self._lifeboat_path, 0o600)
        except (OSError, NotImplementedError):
            pass
        logger.info("Lifeboat bundle stored (%d bytes)", len(encrypted_bundle))

    def get_lifeboat(self) -> bytes:
        """Read and return the stored lifeboat bundle from disk.

        Raises FileNotFoundError if no bundle has been stored yet.
        """
        if not self._lifeboat_path.exists():
            raise FileNotFoundError(
                f"Lifeboat bundle not found at {self._lifeboat_path}"
            )
        return self._lifeboat_path.read_bytes()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def aclose(self) -> None:
        await self._client.aclose()
