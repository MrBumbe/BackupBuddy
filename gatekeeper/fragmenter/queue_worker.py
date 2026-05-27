"""
Upload queue worker for the gatekeeper fragmenter.

Consumes UploadItems from an asyncio.Queue and calls fragment_and_upload for
each item.  Retries on FragmentationError with exponential backoff.

Retry semantics (MAX_RETRIES = 3):
  - 1 initial attempt + 3 retries = 4 attempts total.
  - The 3rd retry's failure triggers the critical alert.
  - Retry loop is inline: sleep inside the worker task, not re-queue.
    Re-queuing would reorder items and complicate ordering guarantees.
  - Backoff: min(60, 2^attempt) seconds — capped at 60 s.

Security (SECURITY.md §6):
  - Failure logs include only: agent name, attempt count, error type, file size.
  - No file_path, original_path, or any filename in any log output.

Notification (task 1.13.1 not yet implemented):
  - send_alert is an injectable async callable.  When None (default), a
    critical log line is emitted instead.  Wire in
    gatekeeper.notify.dispatcher once task 1.13.1 lands.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from gatekeeper.fragmenter.fragmenter import FragmentationError, Fragmenter

logger = logging.getLogger(__name__)

MAX_RETRIES = 3          # 1 initial attempt + MAX_RETRIES retries = 4 total
_MAX_BACKOFF_SECONDS = 60


# ── Item type ─────────────────────────────────────────────────────────────────

@dataclass
class UploadItem:
    """An item in the upload queue.

    file_path and original_path are kept separate:
    - file_path: path on the gatekeeper's filesystem where the file lives.
    - original_path: path as reported by the agent; recorded in catalog.db.
    """
    file_path: str
    profile: str
    agent: str
    original_path: str
    attempt: int = field(default=0)


# ── Worker ────────────────────────────────────────────────────────────────────

class UploadQueueWorker:
    """Consumes UploadItems from an asyncio.Queue and uploads them via Fragmenter.

    Spawns upload_concurrent worker tasks.  Each task processes items serially
    (blocking on queue.get), applying retry logic inline.  Parallel uploads
    are achieved by having multiple tasks reading from the same queue.

    Args:
        queue:             asyncio.Queue[UploadItem] — shared upload queue.
        fragmenter:        Fragmenter instance for fragment_and_upload calls.
        upload_concurrent: Number of parallel worker tasks (from WatcherConfig).
        send_alert:        Optional async callable invoked with (message: str)
                           on permanent failure.  When None, logs critical instead.
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        fragmenter: Fragmenter,
        upload_concurrent: int = 2,
        send_alert: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._queue = queue
        self._fragmenter = fragmenter
        self._upload_concurrent = max(1, upload_concurrent)
        self._send_alert = send_alert
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        """Spawn worker tasks.  Must be called inside a running event loop."""
        for i in range(self._upload_concurrent):
            task = asyncio.create_task(
                self._worker(), name=f"upload-worker-{i}"
            )
            self._tasks.append(task)
        logger.info(
            "Upload queue worker started (%d concurrent)", self._upload_concurrent
        )

    async def stop(self) -> None:
        """Cancel all worker tasks and await their completion."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Upload queue worker stopped")

    # ── Internal ──────────────────────────────────────────────────────────────

    async def _worker(self) -> None:
        """Main worker loop: dequeue one item, process it, repeat."""
        logger.debug("Worker task starting — waiting for items")
        while True:
            item: UploadItem = await self._queue.get()
            logger.debug("Worker dequeued item for agent=%s", item.agent)
            try:
                await self._process_with_retries(item)
            except Exception as exc:
                logger.error(
                    "Unexpected error in upload worker — agent=%s error=%s",
                    item.agent, type(exc).__name__, exc_info=True,
                )
            finally:
                # task_done() always called after a successful get(), even on
                # unexpected exceptions, so callers awaiting queue.join() don't hang.
                self._queue.task_done()

    async def _process_with_retries(self, item: UploadItem) -> None:
        """Upload item with inline retry loop.

        Catches FragmentationError only.  Any other exception (OSError,
        programmer error, CancelledError) propagates to the caller.
        """
        for attempt in range(MAX_RETRIES + 1):
            if attempt > 0:
                backoff = min(_MAX_BACKOFF_SECONDS, 2 ** attempt)
                logger.warning(
                    "Upload retry %d/%d — agent=%s backoff=%ds",
                    attempt, MAX_RETRIES, item.agent, backoff,
                )
                await asyncio.sleep(backoff)

            try:
                await self._fragmenter.fragment_and_upload(
                    file_path=item.file_path,
                    profile=item.profile,
                    agent=item.agent,
                    original_path=item.original_path,
                )
                if attempt > 0:
                    logger.info(
                        "Upload succeeded on retry %d/%d — agent=%s",
                        attempt, MAX_RETRIES, item.agent,
                    )
                return  # success — exit retry loop

            except FragmentationError as exc:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        "Upload attempt %d/%d failed — agent=%s error=%s",
                        attempt + 1, MAX_RETRIES + 1,
                        item.agent, type(exc).__name__,
                    )
                    # Loop continues to next attempt after backoff sleep above.
                else:
                    # Final attempt failed — log and alert without leaking paths.
                    size_str = _safe_size(item.file_path)
                    logger.error(
                        "Upload permanently failed after %d attempts — "
                        "agent=%s size=%s error=%s",
                        MAX_RETRIES + 1,
                        item.agent,
                        size_str,
                        type(exc).__name__,
                    )
                    await self._dispatch_alert(item.agent)

    async def _dispatch_alert(self, agent: str) -> None:
        """Send a failure alert, or log critical if no dispatcher is configured."""
        message = (
            f"Upload failed after {MAX_RETRIES + 1} attempts "
            f"for agent '{agent}'. Check logs for details."
        )
        if self._send_alert is not None:
            try:
                await self._send_alert(message)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Failed to send upload failure alert: %s", type(exc).__name__
                )
        else:
            logger.critical("ALERT (no dispatcher configured): %s", message)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_size(file_path: str) -> str:
    """Return file size as a string, or 'unknown' if the file cannot be stat'd."""
    try:
        return str(os.path.getsize(file_path))
    except OSError:
        return "unknown"
