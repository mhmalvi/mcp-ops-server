"""Append-only JSONL audit log of every tool call (reads, previews, writes, rejections)."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_VALUE_CHARS = 200


def _clip(value: Any) -> Any:
    """Shorten long string arguments so the log cannot balloon."""
    if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
        return value[:MAX_VALUE_CHARS] + "...(truncated)"
    return value


class AuditLog:
    """Thread-safe JSONL writer; one line per tool call."""

    def __init__(self, path: Path) -> None:
        """Args: path: log file (created with its parent directory if missing)."""
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, tool: str, args: dict[str, Any], outcome: str, detail: dict[str, Any] | None = None) -> None:
        """Append one entry.

        Args:
            tool: Tool name.
            args: Arguments as received (long strings clipped).
            outcome: ``ok``, ``preview``, ``written`` or ``rejected``.
            detail: Small result summary (row counts, ids, error message).
        """
        entry = {"ts": datetime.now(UTC).isoformat(timespec="milliseconds"), "tool": tool,
                 "args": {k: _clip(v) for k, v in args.items()}, "outcome": outcome, "detail": detail or {}}
        line = json.dumps(entry, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def tail(self, count: int) -> list[dict[str, Any]]:
        """Return the newest ``count`` entries (oldest first)."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-count:]
        return [json.loads(line) for line in lines]
