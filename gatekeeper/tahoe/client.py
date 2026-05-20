"""
Async HTTP client for the Tahoe-LAFS gateway node.

Wraps the Tahoe HTTP API for the four operations the gatekeeper needs:
upload, download, ls (directory listing), and mkdir.

Internal Tahoe terminology (caps, FURLs, shares, storage indices) does not
appear in any name or docstring visible outside this module.
Callers work with opaque reference strings returned by upload/mkdir.
"""

import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import quote as urlquote

import httpx

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 65536
_DEFAULT_TIMEOUT = 300  # seconds — large files may take a while


class TahoeClient:
    """
    Async client for the Tahoe-LAFS HTTP gateway.

    node_url is the base URL where the Tahoe node listens,
    e.g. "http://127.0.0.1:3456".

    All methods are coroutines and must be awaited.
    The caller is responsible for managing the httpx.AsyncClient lifetime;
    call aclose() when done, or use TahoeClient as an async context manager.

    Raises TahoeError on any Tahoe-side failure.
    """

    def __init__(self, node_url: str, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._node_url = node_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "TahoeClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def upload(self, file_path: str, metadata: dict | None = None) -> str:
        """
        Upload a file to the Tahoe grid as an unlinked immutable object.

        Returns an opaque reference string that can be passed to download().
        The metadata parameter is accepted for API compatibility but is not
        stored at this layer — directory linking with metadata is handled by
        the fragmenter (task 1.7.1).

        Raises TahoeError on upload failure.
        """
        path = Path(file_path)
        logger.debug("Uploading %s (%d bytes)", path.name, path.stat().st_size)

        with open(path, "rb") as fh:
            response = await self._http.put(
                f"{self._node_url}/uri",
                content=fh,
                headers={"Content-Type": "application/octet-stream"},
            )

        _raise_for_tahoe_error(response, "upload")
        ref = response.text.strip()
        logger.debug("Upload complete, ref length=%d", len(ref))
        return ref

    async def download(self, file_ref: str, dest_path: str) -> str:
        """
        Download a file from the Tahoe grid to dest_path.

        Returns the SHA-256 hex digest of the downloaded content so the
        caller can verify integrity.

        Raises TahoeError if the download fails or the reference is not found.
        """
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        logger.debug("Downloading to %s", dest.name)

        encoded_ref = urlquote(file_ref, safe="")
        hasher = hashlib.sha256()

        async with self._http.stream("GET", f"{self._node_url}/uri/{encoded_ref}") as response:
            _raise_for_tahoe_error(response, "download")
            with open(dest, "wb") as fh:
                async for chunk in response.aiter_bytes(_CHUNK_SIZE):
                    fh.write(chunk)
                    hasher.update(chunk)

        digest = hasher.hexdigest()
        logger.debug("Download complete, sha256=%s…", digest[:16])
        return digest

    async def ls(self, dir_ref: str) -> list[tuple[str, str]]:
        """
        List the immediate children of a Tahoe directory.

        Returns a list of (name, child_ref) tuples.
        Subdirectories and files are both included; the caller can distinguish
        them by checking whether child_ref starts with a directory cap prefix —
        but at this layer they are just opaque strings.

        Raises TahoeError if dir_ref does not refer to a directory.
        """
        encoded_ref = urlquote(dir_ref, safe="")
        response = await self._http.get(
            f"{self._node_url}/uri/{encoded_ref}",
            params={"t": "json"},
        )
        _raise_for_tahoe_error(response, "ls")

        data = response.json()
        # Tahoe returns ["dirnode", {"children": {"name": [type, info], ...}}]
        if not isinstance(data, list) or data[0] != "dirnode":
            raise TahoeError(f"ls: expected dirnode, got {data[0]!r}")

        children = data[1].get("children", {})
        result: list[tuple[str, str]] = []
        for name, (node_type, node_info) in children.items():
            if node_type == "filenode":
                child_ref = node_info.get("ro_uri") or node_info.get("rw_uri", "")
            elif node_type == "dirnode":
                child_ref = node_info.get("rw_uri") or node_info.get("ro_uri", "")
            else:
                continue
            result.append((name, child_ref))

        return result

    async def mkdir(self) -> str:
        """
        Create a new mutable directory in the Tahoe grid.

        Returns an opaque reference string for the new directory.
        This reference can be passed to ls() and used as a parent
        when linking files.

        Raises TahoeError on failure.
        """
        response = await self._http.post(
            f"{self._node_url}/uri",
            params={"t": "mkdir"},
        )
        _raise_for_tahoe_error(response, "mkdir")
        ref = response.text.strip()
        logger.debug("mkdir complete, ref length=%d", len(ref))
        return ref

    async def link_file(
        self,
        dir_ref: str,
        name: str,
        file_ref: str,
        metadata: dict,
    ) -> None:
        """
        Link a file cap into a Tahoe directory with associated metadata.

        Uses POST /uri/<dir_ref>?t=set_children (verified in fork source:
        src/allmydata/web/directory.py).  The file cap is stored as a
        read-only URI in the directory entry.  metadata is arbitrary JSON
        stored alongside the entry — used by the fragmenter for encrypted
        call-home reconstruction tags (ADR-008).

        Raises TahoeError on failure.
        """
        encoded_dir = urlquote(dir_ref, safe="")
        body = {name: ["filenode", {"ro_uri": file_ref, "metadata": metadata}]}
        response = await self._http.post(
            f"{self._node_url}/uri/{encoded_dir}",
            params={"t": "set_children"},
            json=body,
        )
        _raise_for_tahoe_error(response, "link_file")
        logger.debug("File linked as %r in directory", name)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

class TahoeError(Exception):
    """Raised when a Tahoe gateway operation fails."""


def _raise_for_tahoe_error(response: httpx.Response, operation: str) -> None:
    """Raise TahoeError with a plain message if the HTTP response indicates failure."""
    if response.status_code >= 400:
        raise TahoeError(
            f"{operation} failed (HTTP {response.status_code}): {response.text[:200]}"
        )
