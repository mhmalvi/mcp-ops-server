"""Business logic behind the MCP tools: parameterized SQL only, row limits, a ticket state machine.

Every function takes an open connection plus already-typed arguments and returns plain
JSON-ready dicts. Validation that the type system cannot express raises ``OpsError``.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, date, datetime
from typing import Any

MAX_ROWS = 100
MAX_RANGE_DAYS = 366
REVENUE_STATUSES = ("paid", "shipped")

# Allowed ticket status changes (closed is terminal; resolved can be reopened).
TRANSITIONS: dict[str, set[str]] = {
    "open": {"pending", "resolved", "closed"},
    "pending": {"open", "resolved", "closed"},
    "resolved": {"open", "closed"},
    "closed": set(),
}

# group_by value -> SQL expression. Only these fixed strings ever reach the query text.
GROUP_EXPR = {
    "day": "substr(o.created_at, 1, 10)",
    "month": "substr(o.created_at, 1, 7)",
    "country": "c.country",
    "plan": "c.plan",
}


class OpsError(ValueError):
    """Bad input or a missing record; the message is safe to show to the model."""


def money(cents: int | None) -> str:
    """Format integer cents as a decimal string (no float rounding)."""
    cents = cents or 0
    return f"{cents // 100}.{cents % 100:02d}"


def _page(rows: list[sqlite3.Row], limit: int) -> dict[str, Any]:
    """Trim a ``limit + 1`` fetch to ``limit`` rows and report whether more exist."""
    return {"rows": [dict(r) for r in rows[:limit]], "returned": min(len(rows), limit),
            "truncated": len(rows) > limit}


def _like(text: str) -> str:
    """Escape LIKE wildcards in user text and wrap it for a substring match."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_customers(conn: sqlite3.Connection, query: str, limit: int) -> dict[str, Any]:
    """Find customers whose name, company or email contains ``query`` (case-insensitive)."""
    pattern = _like(query.strip())
    rows = conn.execute(
        "SELECT id, name, company, email, country, plan FROM customers "
        "WHERE name LIKE :q ESCAPE '\\' OR company LIKE :q ESCAPE '\\' OR email LIKE :q ESCAPE '\\' "
        "ORDER BY id LIMIT :limit", {"q": pattern, "limit": limit + 1}).fetchall()
    return _page(rows, limit)


def get_customer(conn: sqlite3.Connection, customer_id: int) -> dict[str, Any]:
    """Return one customer with order count, lifetime value and open ticket ids."""
    row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if row is None:
        raise OpsError(f"customer {customer_id} not found")
    stats = conn.execute(
        "SELECT count(*) AS orders, sum(CASE WHEN status IN ('paid','shipped') THEN total_cents END) AS ltv "
        "FROM orders WHERE customer_id = ?", (customer_id,)).fetchone()
    open_ids = [r[0] for r in conn.execute(
        "SELECT id FROM tickets WHERE customer_id = ? AND status IN ('open','pending') ORDER BY id", (customer_id,))]
    return {**dict(row), "order_count": stats["orders"], "lifetime_value_usd": money(stats["ltv"]),
            "open_ticket_ids": open_ids}


def list_orders(conn: sqlite3.Connection, customer_id: int | None, status: str | None,
                since: date | None, limit: int) -> dict[str, Any]:
    """List orders newest first, optionally filtered by customer, status and start date."""
    clauses, params = [], []
    for sql, value in (("customer_id = ?", customer_id), ("status = ?", status),
                       ("created_at >= ?", since.isoformat() if since else None)):
        if value is not None:
            clauses.append(sql)
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(f"SELECT id, customer_id, status, total_cents, created_at FROM orders {where} "
                        "ORDER BY created_at DESC, id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    page = _page(rows, limit)
    for row in page["rows"]:
        row["total_usd"] = money(row.pop("total_cents"))
    return page


def get_order(conn: sqlite3.Connection, order_id: int) -> dict[str, Any]:
    """Return one order with its line items."""
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        raise OpsError(f"order {order_id} not found")
    items = conn.execute(
        "SELECT p.sku, p.name, i.quantity, i.unit_price_cents FROM order_items i "
        "JOIN products p ON p.id = i.product_id WHERE i.order_id = ? ORDER BY p.sku", (order_id,)).fetchall()
    order = dict(row)
    order["total_usd"] = money(order.pop("total_cents"))
    order["items"] = [{"sku": i["sku"], "name": i["name"], "quantity": i["quantity"],
                       "unit_price_usd": money(i["unit_price_cents"])} for i in items]
    return order


def list_tickets(conn: sqlite3.Connection, status: str | None, priority: str | None,
                 customer_id: int | None, limit: int) -> dict[str, Any]:
    """List tickets, most urgent and oldest first, with optional filters."""
    clauses, params = [], []
    for sql, value in (("status = ?", status), ("priority = ?", priority), ("customer_id = ?", customer_id)):
        if value is not None:
            clauses.append(sql)
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'normal' THEN 2 ELSE 3 END, created_at"
    rows = conn.execute(f"SELECT id, customer_id, order_id, subject, status, priority, created_at FROM tickets "
                        f"{where} ORDER BY {order} LIMIT ?", (*params, limit + 1)).fetchall()
    return _page(rows, limit)


def get_ticket(conn: sqlite3.Connection, ticket_id: int) -> dict[str, Any]:
    """Return one ticket with its notes."""
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise OpsError(f"ticket {ticket_id} not found")
    notes = conn.execute("SELECT author, body, created_at FROM ticket_notes WHERE ticket_id = ? ORDER BY id",
                         (ticket_id,)).fetchall()
    return {**dict(row), "notes": [dict(n) for n in notes]}


def sales_summary(conn: sqlite3.Connection, start: date, end: date, group_by: str) -> dict[str, Any]:
    """Revenue (paid + shipped orders) between two dates, grouped by a whitelisted dimension."""
    if end < start:
        raise OpsError("end must be on or after start")
    if (end - start).days > MAX_RANGE_DAYS:
        raise OpsError(f"date range is limited to {MAX_RANGE_DAYS} days")
    expr = GROUP_EXPR[group_by]
    rows = conn.execute(
        f"SELECT {expr} AS bucket, count(*) AS orders, sum(o.total_cents) AS revenue_cents "
        "FROM orders o JOIN customers c ON c.id = o.customer_id "
        "WHERE o.status IN (?, ?) AND substr(o.created_at, 1, 10) BETWEEN ? AND ? "
        "GROUP BY bucket ORDER BY bucket", (*REVENUE_STATUSES, start.isoformat(), end.isoformat())).fetchall()
    groups = [{"bucket": r["bucket"], "orders": r["orders"], "revenue_usd": money(r["revenue_cents"])} for r in rows]
    total = sum(r["revenue_cents"] for r in rows)
    return {"start": start.isoformat(), "end": end.isoformat(), "group_by": group_by,
            "groups": groups, "total_revenue_usd": money(total)}


def plan_ticket_update(conn: sqlite3.Connection, ticket_id: int, new_status: str, note: str) -> dict[str, Any]:
    """Validate a status change and describe it, without writing anything."""
    row = conn.execute("SELECT status, updated_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        raise OpsError(f"ticket {ticket_id} not found")
    current = row["status"]
    if new_status not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "none (closed is final)"
        raise OpsError(f"cannot move ticket {ticket_id} from {current} to {new_status}; allowed: {allowed}")
    if not note.strip():
        raise OpsError("a note explaining the change is required")
    plan = {"ticket_id": ticket_id, "from_status": current, "to_status": new_status, "note": note.strip(),
            "ticket_updated_at": row["updated_at"]}
    plan["confirmation_token"] = confirmation_token(plan)
    return plan


def confirmation_token(plan: dict[str, Any]) -> str:
    """Fingerprint a planned change plus the ticket's last update, so a stale preview cannot be confirmed."""
    raw = "|".join(str(plan[k]) for k in ("ticket_id", "from_status", "to_status", "note", "ticket_updated_at"))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def apply_ticket_update(conn: sqlite3.Connection, plan: dict[str, Any], author: str) -> dict[str, Any]:
    """Write a planned status change and its note (caller owns the transaction)."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    current = plan_ticket_update(conn, plan["ticket_id"], plan["to_status"], plan["note"])  # re-check in the txn
    conn.execute("UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
                 (current["to_status"], now, current["ticket_id"]))
    conn.execute("INSERT INTO ticket_notes (ticket_id, author, body, created_at) VALUES (?, ?, ?, ?)",
                 (current["ticket_id"], author, current["note"], now))
    return {**current, "updated_at": now}
