#!/usr/bin/env python3
"""
Run a pre-created Tahoe node directory.  Blocks until interrupted.

Usage:
    python run_tahoe_node.py --basedir /path/to/tahoe/storage_node

The node must already have been created (tahoe.cfg present) by bootstrap_gk.py.
Use Ctrl-C or kill the process to stop it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _find_tahoe() -> str:
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--basedir", required=True,
                        help="Pre-created Tahoe node directory")
    args = parser.parse_args()

    basedir = Path(args.basedir).resolve()
    if not (basedir / "tahoe.cfg").exists():
        print(f"ERROR: tahoe.cfg not found in {basedir}", file=sys.stderr)
        print("Run bootstrap_gk.py first to create the node directory.", file=sys.stderr)
        sys.exit(1)

    tahoe = _find_tahoe()
    print(f"Starting Tahoe node at {basedir}", flush=True)
    try:
        subprocess.run([tahoe, "run", "--allow-stdin-close", str(basedir)], check=False)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
