"""
Async HTTP client for the Tahoe-LAFS gateway node.

Wraps the Tahoe HTTP API for the four operations the gatekeeper needs:
upload, download, ls (directory listing), and mkdir.

Internal Tahoe terminology (caps, FURLs, shares, storage indices) does not
appear in any name or docstring visible outside this module.
Callers work with opaque reference strings returned by upload/mkdir.
"""

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from urllib.parse import quote as urlquote

import httpx

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 65536
_TIMEOUT = httpx.Timeout(connect=30.0, read=3600.0, write=3600.0, pool=30.0)


async def _iter_file(path: Path) -> ...:
    """Yield file content in chunks without loading the whole file into memory."""
    loop = asyncio.get_running_loop()
    with open(path, "rb") as f:
        while True:
            chunk = await loop.run_in_executor(None, f.read, _CHUNK_SIZE)
            if not chunk:
                break
            yield chunk


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

    def __init__(self, node_url: str) -> None:
        self._node_url = node_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=_TIMEOUT)

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
        file_size = path.stat().st_size
        logger.debug("Uploading %s (%d bytes)", path.name, file_size)

        response = await self._http.put(
            f"{self._node_url}/uri",
            content=_iter_file(path),
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

    async def ls_with_metadata(self, dir_ref: str) -> list[dict]:
        """
        List the immediate file children of a Tahoe directory,
        returning the stored metadata dict alongside each entry.

        Returns a list of dicts with keys:
          name     (str)  — entry name in the directory
          file_ref (str)  — opaque reference to the file
          metadata (dict) — arbitrary dict stored with the entry (may be {})
          size     (int)  — file size in bytes (0 if unavailable)

        Only filenodes are included; subdirectories are skipped.
        Used by catalog reconstruction (ADR-008) to recover encrypted
        metadata tags written by the fragmenter.

        Raises TahoeError if dir_ref does not refer to a directory.
        """
        encoded_ref = urlquote(dir_ref, safe="")
        response = await self._http.get(
            f"{self._node_url}/uri/{encoded_ref}",
            params={"t": "json"},
        )
        _raise_for_tahoe_error(response, "ls_with_metadata")

        data = response.json()
        if not isinstance(data, list) or data[0] != "dirnode":
            raise TahoeError(f"ls_with_metadata: expected dirnode, got {data[0]!r}")

        children = data[1].get("children", {})
        result: list[dict] = []
        for name, (node_type, node_info) in children.items():
            if node_type != "filenode":
                continue
            file_ref = node_info.get("ro_uri") or node_info.get("rw_uri", "")
            metadata = node_info.get("metadata") or {}
            size = node_info.get("size", 0)
            result.append({
                "name": name,
                "file_ref": file_ref,
                "metadata": metadata,
                "size": size,
            })

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

    async def check_cap(self, file_ref: str) -> dict | None:
        """Check a file reference and return real share counts.

        Uses POST /uri/<ref>?t=check&output=json — this endpoint contacts
        storage nodes to count available shares and is supported by the
        BackupBuddy Tahoe fork (t=check exists on POST, not on GET).

        Returns a dict with keys:
          accessible    (bool) — True when the check completed successfully
          shares_good   (int)  — shares currently available in the grid
          shares_needed (int)  — minimum shares needed for reconstruction (k)

        Returns None if the check itself fails (network error, ref not found).
        Never raises — callers use the return value to decide next steps.
        """
        encoded_ref = urlquote(file_ref, safe="")
        try:
            response = await self._http.post(
                f"{self._node_url}/uri/{encoded_ref}",
                params={"t": "check", "output": "json"},
            )
            _raise_for_tahoe_error(response, "check_cap")
            data = response.json()
            results = data.get("results", {})
            # LIT files return {"healthy": True} without share counts — default to 1/1
            shares_good = results.get("count-shares-good", 1)
            shares_needed = results.get("count-shares-needed", 1)
            return {
                "accessible": True,
                "shares_good": shares_good,
                "shares_needed": shares_needed,
            }
        except Exception:
            return None

    async def delete(self, file_ref: str) -> None:
        """
        Delete a file from the Tahoe grid.

        Calls DELETE /uri/<ref> on the gateway node.  The gateway removes
        the file cap; share-level cleanup is handled by Tahoe's garbage
        collector.

        Raises TahoeError if the operation fails (HTTP >= 400).
        Does NOT silently ignore failures.
        """
        encoded_ref = urlquote(file_ref, safe="")
        response = await self._http.delete(f"{self._node_url}/uri/{encoded_ref}")
        _raise_for_tahoe_error(response, "delete")
        logger.debug("Delete complete for ref (len=%d)", len(file_ref))

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
        try:
            body = response.text[:200]
        except httpx.ResponseNotRead:
            # Inside a streaming context the body hasn't been read yet.
            body = "(streaming response — body not yet read)"
        raise TahoeError(
            f"{operation} failed (HTTP {response.status_code}): {body}"
        )
