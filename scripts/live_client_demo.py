"""Drive the server with a real MCP client over stdio and write a markdown transcript.

    python scripts/live_client_demo.py docs/live-client-transcript.md

Uses a fresh seeded SYNTHETIC database in a temp dir; nothing outside it is touched.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import Client  # noqa: E402

from ops_server import seed  # noqa: E402
from tests.mcp_session import server_params  # noqa: E402


class Transcript:
    """Collects one markdown section per MCP call, with timing."""

    def __init__(self) -> None:
        """Start an empty transcript."""
        self.lines: list[str] = []
        self.calls = 0

    def add(self, title: str, request: Any, response: str, ms: float, is_error: bool = False) -> None:
        """Append one request/response pair."""
        self.calls += 1
        flag = " (is_error=true)" if is_error else ""
        gist = " ".join(response.split())[:88]
        print(f"{'ERR' if is_error else 'ok '} {self.calls:>2}. {title:<42} {ms:>4.0f} ms  {gist}")
        body = response if len(response) < 1800 else response[:1800] + "\n... (truncated in transcript)"
        self.lines += [f"### {self.calls}. {title}{flag} - {ms:.0f} ms", "", "```json", json.dumps(request), "```",
                       "", "```", body, "```", ""]


async def timed(coro: Any) -> tuple[Any, float]:
    """Await ``coro`` and return its result with elapsed milliseconds."""
    started = time.perf_counter()
    result = await coro
    return result, (time.perf_counter() - started) * 1000


async def call(client: Client, log: Transcript, name: str, args: dict[str, Any]) -> Any:
    """Call one tool and log it."""
    result, ms = await timed(client.call_tool(name, args))
    log.add(f"tools/call {name}", args, result.content[0].text, ms, result.is_error)
    return result


async def discovery(client: Client, log: Transcript) -> None:
    """List tools, resources, templates and prompts."""
    tools, ms = await timed(client.list_tools())
    summary = [f"{t.name} (read_only={t.annotations.read_only_hint}, destructive={t.annotations.destructive_hint})"
               for t in tools.tools]
    log.add("tools/list", {}, "\n".join(summary), ms)
    resources, ms = await timed(client.list_resources())
    log.add("resources/list", {}, "\n".join(str(r.uri) for r in resources.resources), ms)
    schema, ms = await timed(client.read_resource("ops://policies/support"))
    log.add("resources/read ops://policies/support", {}, schema.contents[0].text, ms)
    prompt, ms = await timed(client.get_prompt("triage_ticket", {"ticket_id": "1"}))
    log.add("prompts/get triage_ticket", {"ticket_id": "1"}, prompt.messages[0].content.text, ms)


async def scenario(client: Client, log: Transcript) -> None:
    """Reads, validation failures, then the guarded write: preview, confirm, stale replay."""
    listed = await call(client, log, "list_tickets", {"status": "open", "limit": 3})
    ticket_id = json.loads(listed.content[0].text)["rows"][0]["id"]
    await call(client, log, "get_ticket", {"ticket_id": ticket_id})
    await call(client, log, "sales_summary", {"start": "2026-08-01", "end": "2026-11-30", "group_by": "month"})
    await call(client, log, "list_orders", {"limit": 1000})
    await call(client, log, "search_customers", {"query": "' OR 1=1 --"})
    args = {"ticket_id": ticket_id, "new_status": "pending", "note": "Asked the customer for photos of the damage."}
    await call(client, log, "update_ticket_status", {**args, "confirm": True})
    preview = await call(client, log, "update_ticket_status", args)
    token = json.loads(preview.content[0].text)["plan"]["confirmation_token"]
    await call(client, log, "update_ticket_status", {**args, "confirm": True, "confirmation_token": token})
    await call(client, log, "update_ticket_status", {**args, "confirm": True, "confirmation_token": token})
    audit, ms = await timed(client.read_resource("ops://audit/recent"))
    log.add("resources/read ops://audit/recent", {}, json.dumps(json.loads(audit.contents[0].text)[-5:], indent=1), ms)


async def main(out: Path) -> None:
    """Seed a temp database, run discovery + scenario over stdio, write the transcript."""
    tmp = Path(tempfile.mkdtemp(prefix="mcp-ops-demo-"))
    seed.build(tmp / "demo.db")
    log = Transcript()
    started = time.perf_counter()
    async with Client(server_params(tmp / "demo.db", tmp / "audit.jsonl"), read_timeout_seconds=30) as client:
        await discovery(client, log)
        await scenario(client, log)
    total = time.perf_counter() - started
    head = ["# Live MCP client session (stdio)", "",
            f"Real `mcp` Python SDK client talking to `python -m ops_server` over stdio on a fresh SYNTHETIC database. "
            f"{log.calls} MCP requests, {total:.1f} s wall-clock including server start-up.", ""]
    out.write_text("\n".join(head + log.lines), encoding="utf-8")
    print(f"wrote {out} ({log.calls} calls, {total:.1f}s)")


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1] if len(sys.argv) > 1 else "docs/live-client-transcript.md")))
