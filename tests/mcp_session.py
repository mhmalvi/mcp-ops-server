"""Helper: run a coroutine against the server spawned as a real subprocess over stdio."""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters

ROOT = Path(__file__).resolve().parent.parent


def server_params(db_path: Path, audit_path: Path) -> StdioServerParameters:
    """Launch command for ``python -m ops_server`` pointed at the given database."""
    return StdioServerParameters(command=sys.executable, args=["-m", "ops_server"], cwd=str(ROOT),
                                 env={"OPS_DB": str(db_path), "OPS_AUDIT_LOG": str(audit_path), "OPS_ACTOR": "pytest"})


def with_client(db_path: Path, audit_path: Path, body: Callable[[Client], Awaitable[Any]]) -> Any:
    """Open a stdio MCP session, run ``body(client)``, close the session, return the result."""

    async def main() -> Any:
        async with Client(server_params(db_path, audit_path), read_timeout_seconds=30) as client:
            return await body(client)

    return asyncio.run(main())


def payload(result: Any) -> dict[str, Any]:
    """Decode the JSON text content of a successful tool result."""
    assert not result.is_error, result.content[0].text
    return json.loads(result.content[0].text)
