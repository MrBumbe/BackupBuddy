#!/usr/bin/env python3
"""
Bootstrap a BackupBuddy gatekeeper node for integration testing.

Primary mode (GK1 — no --introducer-furl):
  Creates Tahoe introducer + storage node directories, temporarily starts both
  to call mkdir() and obtain root_dir.cap, initialises catalog.db, generates
  lifeboat.key, then stops all nodes.  Prints "FURL=<value>" to stdout.

Secondary mode (GK2 — --introducer-furl provided):
  Creates only a storage node directory configured to join the existing
  introducer.  Does NOT create root_dir.cap or databases.
  Prints "READY" to stdout.

The gatekeeper daemon (GK1) or bare storage node (GK2 via run_tahoe_node.py)
is started separately by smoke_test.sh.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Allow running from any cwd — project root is two levels up.
sys.path.insert(0, str(Path(__file__).parents[2]))

from gatekeeper.db.catalog import CatalogDB
from gatekeeper.fragmenter.profiles import get_profile
from gatekeeper.lifeboat.keystore import DEFAULT_KEY_PATH, generate_key
from gatekeeper.main import _derive_catalog_key
from gatekeeper.tahoe.client import TahoeClient
from gatekeeper.tahoe.introducer import IntroducerNode
from gatekeeper.tahoe.storage_node import StorageNode

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

_MKDIR_RETRIES = 12
_MKDIR_RETRY_DELAY = 2.5   # seconds between retries
_RESERVED_BYTES = 0        # no floor reservation in smoke tests


async def _mkdir_with_retry(client: TahoeClient) -> str:
    """Call mkdir() with retries to allow the storage node time to self-announce.

    After `tahoe run` reaches "client running" the node has started, but it
    may take several more seconds before it has announced itself to the
    introducer and the client sees a storage server.  mkdir() fails with
    NotEnoughSharesError until then.
    """
    last_exc: Exception = RuntimeError("no attempts made")
    for attempt in range(1, _MKDIR_RETRIES + 1):
        try:
            return await client.mkdir()
        except Exception as exc:
            last_exc = exc
            if attempt < _MKDIR_RETRIES:
                logger.info(
                    "mkdir() attempt %d/%d failed (%s) — retrying in %.1fs",
                    attempt, _MKDIR_RETRIES, type(exc).__name__, _MKDIR_RETRY_DELAY,
                )
                await asyncio.sleep(_MKDIR_RETRY_DELAY)
    raise RuntimeError(
        f"mkdir() failed after {_MKDIR_RETRIES} attempts: {last_exc}"
    ) from last_exc


async def bootstrap_primary(
    data_dir: Path,
    storage_dir: Path,
    node_name: str,
    web_port: int,
    profile: str,
    key_path: Path,
    hostname: str = "127.0.0.1",
) -> str:
    """Create GK1 data dirs, root_dir.cap, catalog.db, and lifeboat.key.

    Returns the introducer FURL (internal use only — never logged at INFO).
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)

    generate_key(key_path)
    logger.info("Lifeboat key written to %s", key_path)

    p = get_profile(profile)
    shares_k, shares_n = p.k, p.n
    shares_happy = p.happy if p.happy is not None else shares_n

    # Create introducer
    introducer_dir = data_dir / "tahoe" / "introducer"
    introducer = IntroducerNode(str(introducer_dir))
    introducer.create()

    # Start introducer; FURL is read from disk (written at create-introducer time)
    logger.info("Starting introducer node")
    furl = await introducer.start()

    # Create and start storage node
    storage_node_dir = data_dir / "tahoe" / "storage_node"
    node = StorageNode(
        basedir=str(storage_node_dir),
        storage_dir=str(storage_dir),
        reserved_space=_RESERVED_BYTES,
        nickname=node_name,
        web_port=web_port,
        shares_needed=shares_k,
        shares_happy=shares_happy,
        shares_total=shares_n,
        hostname=hostname,
    )
    node.create(furl)
    await node.start()
    logger.info("Storage node running at %s", node.node_url)

    # Create root_dir.cap — storage node needs a few seconds to self-announce
    client = TahoeClient(node.node_url)
    try:
        logger.info("Creating root directory (retrying until storage node is ready)")
        root_dir_cap = await _mkdir_with_retry(client)
    finally:
        await client.aclose()

    cap_path = data_dir / "root_dir.cap"
    cap_path.write_text(root_dir_cap, encoding="utf-8")
    logger.info("root_dir.cap written to %s", cap_path)

    # Initialise catalog.db (schema migrations run in CatalogDB.__init__)
    catalog_key = _derive_catalog_key(root_dir_cap)
    catalog_db = CatalogDB(str(data_dir / "catalog.db"), catalog_key)
    catalog_db.close()
    logger.info("catalog.db initialised at %s", data_dir / "catalog.db")

    # Stop nodes — the gatekeeper daemon restarts them on its own
    await node.stop()
    await introducer.stop()
    logger.info("Bootstrap complete — nodes stopped")

    return furl


async def bootstrap_secondary(
    data_dir: Path,
    storage_dir: Path,
    node_name: str,
    web_port: int,
    profile: str,
    key_path: Path,
    introducer_furl: str,
    hostname: str = "127.0.0.1",
) -> None:
    """Create GK2 storage node directory configured to join GK1's introducer."""
    data_dir.mkdir(parents=True, exist_ok=True)
    storage_dir.mkdir(parents=True, exist_ok=True)

    generate_key(key_path)
    logger.info("Lifeboat key written to %s", key_path)

    p = get_profile(profile)
    shares_k, shares_n = p.k, p.n
    shares_happy = p.happy if p.happy is not None else shares_n

    storage_node_dir = data_dir / "tahoe" / "storage_node"
    node = StorageNode(
        basedir=str(storage_node_dir),
        storage_dir=str(storage_dir),
        reserved_space=_RESERVED_BYTES,
        nickname=node_name,
        web_port=web_port,
        shares_needed=shares_k,
        shares_happy=shares_happy,
        shares_total=shares_n,
        hostname=hostname,
    )
    node.create(introducer_furl)
    logger.info("Storage node directory created at %s", storage_node_dir)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--data-dir",     required=True, metavar="DIR")
    p.add_argument("--storage-dir",  required=True, metavar="DIR")
    p.add_argument("--node-name",    default="bootstrap-node", metavar="NAME")
    p.add_argument("--web-port",     type=int, default=3456, metavar="PORT")
    p.add_argument("--profile",      default="test",
                   choices=["balanced", "secure", "paranoid", "test"])
    p.add_argument("--key-path",     default=str(DEFAULT_KEY_PATH), metavar="PATH",
                   help="destination path for lifeboat.key")
    p.add_argument("--introducer-furl", default="", metavar="FURL",
                   help="existing introducer FURL (secondary mode; omit for primary)")
    p.add_argument("--hostname", default="127.0.0.1", metavar="IP",
                   help="IP address this node advertises to peers (use Tailscale or LAN IP)")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    data_dir    = Path(args.data_dir).resolve()
    storage_dir = Path(args.storage_dir).resolve()
    key_path    = Path(args.key_path).resolve()

    if args.introducer_furl:
        await bootstrap_secondary(
            data_dir=data_dir,
            storage_dir=storage_dir,
            node_name=args.node_name,
            web_port=args.web_port,
            profile=args.profile,
            key_path=key_path,
            introducer_furl=args.introducer_furl,
            hostname=args.hostname,
        )
        print("READY")
    else:
        furl = await bootstrap_primary(
            data_dir=data_dir,
            storage_dir=storage_dir,
            node_name=args.node_name,
            web_port=args.web_port,
            profile=args.profile,
            key_path=key_path,
            hostname=args.hostname,
        )
        # Print FURL last so the shell can capture it without parsing log lines
        print(f"FURL={furl}")


if __name__ == "__main__":
    asyncio.run(_main())
