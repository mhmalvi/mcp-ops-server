"""Turn ``claude -p --output-format stream-json`` output into a readable markdown transcript.

    python scripts/format_session.py "Session title" session.jsonl >> docs/claude-code-session.md

Only message content, tool calls/results and the final usage line are kept; the init event
(working directory, session id, local tool list) is dropped so no machine details leak.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MAX_RESULT = 1400


def _result_text(block: dict[str, Any]) -> str:
    """Flatten a tool_result block's content to text, truncated for the transcript."""
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    text = str(content)
    return text if len(text) <= MAX_RESULT else text[:MAX_RESULT] + "\n... (truncated in transcript)"


def _assistant(event: dict[str, Any]) -> list[str]:
    """Markdown lines for one assistant event (text and tool calls; thinking is omitted)."""
    lines = []
    for block in event["message"]["content"]:
        if block.get("type") == "text" and block["text"].strip():
            lines += [f"**Claude:** {block['text'].strip()}", ""]
        elif block.get("type") == "tool_use":
            lines += [f"**Tool call** `{block['name']}`", "", "```json", json.dumps(block["input"], indent=1), "```", ""]
    return lines


def _user(event: dict[str, Any]) -> list[str]:
    """Markdown lines for tool results returned to Claude."""
    lines = []
    for block in event["message"].get("content", []):
        if isinstance(block, dict) and block.get("type") == "tool_result":
            flag = " (error)" if block.get("is_error") else ""
            lines += [f"**Tool result{flag}**", "", "```", _result_text(block), "```", ""]
    return lines


def _result(event: dict[str, Any]) -> list[str]:
    """Footer line with turns, duration and Claude Code's reported cost."""
    return [f"_Session finished: {event.get('subtype')}, {event.get('num_turns')} turns, "
            f"{event.get('duration_ms', 0) / 1000:.1f} s, Claude Code reported cost ${event.get('total_cost_usd', 0):.4f}._", ""]


def render(title: str, prompt: str, path: Path) -> str:
    """Render one stream-json session file as a markdown section."""
    lines = [f"## {title}", "", f"**Operator prompt:** {prompt}", ""]
    for raw in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(raw)
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            servers = ", ".join(f"{s['name']}={s['status']}" for s in event.get("mcp_servers", []))
            lines += [f"_Model `{event.get('model')}`; MCP servers: {servers}; tools offered: "
                      f"{', '.join(t for t in event.get('tools', []))}_", ""]
        elif kind == "assistant":
            lines += _assistant(event)
        elif kind == "user":
            lines += _user(event)
        elif kind == "result":
            lines += _result(event)
    return "\n".join(lines)


def compact(path: Path) -> str:
    """One line per tool call / result / reply, for a terminal-style capture."""
    out = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(raw)
        for block in (event.get("message") or {}).get("content", []) if event.get("type") in ("assistant", "user") else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                out.append(f"-> {block['name']} {json.dumps(block['input'])[:110]}")
            elif block.get("type") == "tool_result":
                out.append(f"   <- {' '.join(_result_text(block).split())[:110]}")
            elif block.get("type") == "text" and block["text"].strip():
                out.append(f"claude: {' '.join(block['text'].split())[:150]}")
        if event.get("type") == "result":
            out.append(f"[{event.get('num_turns')} turns, {event.get('duration_ms', 0) / 1000:.1f} s]")
    return "\n".join(out)


if __name__ == "__main__":
    if sys.argv[1] == "--compact":
        print(compact(Path(sys.argv[2])))
    else:
        print(render(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
