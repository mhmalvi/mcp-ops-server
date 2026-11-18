"""Run the server over stdio: ``python -m ops_server`` (seeds ``OPS_DB`` on first run)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ops_server import seed
from ops_server.server import from_env


def main() -> None:
    """Seed the demo database if it is missing, then serve MCP over stdio."""
    db_path = Path(os.environ.get("OPS_DB", "demo.db")).resolve()
    if not db_path.exists():
        counts = seed.build(db_path)
        print(f"seeded synthetic demo database {db_path.name}: {counts}", file=sys.stderr)
    from_env().run("stdio")


if __name__ == "__main__":
    main()
