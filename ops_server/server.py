"""The MCP server: typed read tools, one guarded write tool, resources and a prompt.

Safety model:
* read tools open the database read-only (``mode=ro`` + ``query_only``) and only run
  parameterized, fixed SQL; there is no free-form SQL tool;
* every list is capped (``limit`` <= 100) and reports ``truncated`` when more rows exist;
* the only write, ``update_ticket_status``, is two-step: without ``confirm=true`` it returns a
  preview and a ``confirmation_token``; the write needs both, and the token goes stale if the
  ticket changed in between;
* every call (read, preview, write, rejection) is appended to a JSONL audit log.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from ops_server import db, queries
from ops_server.audit import AuditLog
from ops_server.queries import OpsError

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
GUARDED_WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=False, open_world_hint=False)

Id = Annotated[int, Field(ge=1, le=10_000_000, description="Numeric record id")]
Limit = Annotated[int, Field(ge=1, le=queries.MAX_ROWS, description=f"Max rows to return (1-{queries.MAX_ROWS})")]
OrderStatus = Literal["pending", "paid", "shipped", "refunded", "cancelled"]
TicketStatus = Literal["open", "pending", "resolved", "closed"]
Priority = Literal["low", "normal", "high", "urgent"]

POLICY = """# Support policy (synthetic demo company)

- First response targets: urgent 1 hour, high 4 hours, normal 1 business day, low 3 business days.
- Status flow: open -> pending (waiting on customer) -> resolved -> closed. Resolved tickets may be
  reopened; closed is final.
- Every status change needs a short note saying why.
- Agents never change a ticket without the operator's explicit approval of the preview.
- Refunds, discounts and order edits are out of scope for this server; escalate them to a human.
"""

INSTRUCTIONS = ("Read-mostly access to a SYNTHETIC orders/customers/tickets demo database. Use the read tools "
                "freely. update_ticket_status is a guarded write: call it first without confirm, show the preview "
                "to the user, and only call it with confirm=true and the confirmation_token after the user approves.")


class OpsServer:
    """Holds configuration and implements every tool; ``build`` registers them on an MCPServer."""

    def __init__(self, db_path: Path, audit_path: Path, actor: str = "claude-via-mcp") -> None:
        """Args: db_path: SQLite file; audit_path: JSONL audit log; actor: author name on ticket notes."""
        self.db_path, self.actor = db_path, actor
        self.audit = AuditLog(audit_path)

    def read(self, tool: str, args: dict[str, Any], fn: Callable[..., dict[str, Any]],
             outcome: str = "ok") -> dict[str, Any]:
        """Run one read query on a read-only connection, auditing ``outcome`` (or ``rejected``)."""
        conn = db.connect_readonly(self.db_path)
        try:
            result = fn(conn, **args)
        except OpsError as exc:
            self.audit.record(tool, args, "rejected", {"error": str(exc)})
            raise ToolError(str(exc)) from exc
        finally:
            conn.close()
        self.audit.record(tool, args, outcome, {"returned": result.get("returned", 1)})
        return result

    def update_ticket_status(self, ticket_id: int, new_status: str, note: str, confirm: bool,
                             confirmation_token: str) -> dict[str, Any]:
        """Preview (default) or apply a ticket status change; see the module docstring."""
        args = {"ticket_id": ticket_id, "new_status": new_status, "note": note, "confirm": confirm}
        if not confirm:
            plan = self.read("update_ticket_status", args,
                             lambda c, **a: queries.plan_ticket_update(c, ticket_id, new_status, note), "preview")
            return {"status": "preview_only", "nothing_written": True, "plan": plan,
                    "next_step": "Show this to the user. If they approve, call again with confirm=true "
                                 "and this confirmation_token."}
        return self._apply(args, confirmation_token)

    def _apply(self, args: dict[str, Any], token: str) -> dict[str, Any]:
        """Re-validate inside a write transaction, check the token, then write."""
        try:
            with db.write_transaction(self.db_path) as conn:
                plan = queries.plan_ticket_update(conn, args["ticket_id"], args["new_status"], args["note"])
                if not token or token != plan["confirmation_token"]:
                    raise OpsError("missing or stale confirmation_token: preview the change again (confirm=false)")
                done = queries.apply_ticket_update(conn, plan, self.actor)
        except OpsError as exc:
            self.audit.record("update_ticket_status", args, "rejected", {"error": str(exc)})
            raise ToolError(str(exc)) from exc
        self.audit.record("update_ticket_status", args, "written",
                          {"from": done["from_status"], "to": done["to_status"]})
        return {"status": "written", **{k: v for k, v in done.items() if k != "confirmation_token"}}


def _register_reads(server: MCPServer, ops: OpsServer) -> None:
    """Register the read-only tools."""

    @server.tool(annotations=READ_ONLY)
    def search_customers(query: Annotated[str, Field(min_length=2, max_length=64)], limit: Limit = 10) -> dict:
        """Find customers whose name, company or email contains the text (case-insensitive)."""
        return ops.read("search_customers", {"query": query, "limit": limit}, queries.search_customers)

    @server.tool(annotations=READ_ONLY)
    def get_customer(customer_id: Id) -> dict:
        """One customer with order count, lifetime value and open ticket ids."""
        return ops.read("get_customer", {"customer_id": customer_id}, queries.get_customer)

    @server.tool(annotations=READ_ONLY)
    def list_orders(customer_id: Id | None = None, status: OrderStatus | None = None,
                    since: date | None = None, limit: Limit = 20) -> dict:
        """Orders newest first, optionally filtered by customer, status and start date (YYYY-MM-DD)."""
        args = {"customer_id": customer_id, "status": status, "since": since, "limit": limit}
        return ops.read("list_orders", args, queries.list_orders)

    @server.tool(annotations=READ_ONLY)
    def get_order(order_id: Id) -> dict:
        """One order with its line items."""
        return ops.read("get_order", {"order_id": order_id}, queries.get_order)


def _register_tickets(server: MCPServer, ops: OpsServer) -> None:
    """Register ticket, reporting and guarded-write tools."""

    @server.tool(annotations=READ_ONLY)
    def list_tickets(status: TicketStatus | None = None, priority: Priority | None = None,
                     customer_id: Id | None = None, limit: Limit = 20) -> dict:
        """Tickets, most urgent and oldest first, with optional filters."""
        args = {"status": status, "priority": priority, "customer_id": customer_id, "limit": limit}
        return ops.read("list_tickets", args, queries.list_tickets)

    @server.tool(annotations=READ_ONLY)
    def get_ticket(ticket_id: Id) -> dict:
        """One ticket with its full body and notes."""
        return ops.read("get_ticket", {"ticket_id": ticket_id}, queries.get_ticket)

    @server.tool(annotations=READ_ONLY)
    def sales_summary(start: date, end: date, group_by: Literal["day", "month", "country", "plan"] = "month") -> dict:
        """Revenue from paid and shipped orders between two dates (max 366 days), grouped."""
        return ops.read("sales_summary", {"start": start, "end": end, "group_by": group_by}, queries.sales_summary)

    @server.tool(annotations=GUARDED_WRITE)
    def update_ticket_status(ticket_id: Id, new_status: TicketStatus,
                             note: Annotated[str, Field(min_length=3, max_length=500)],
                             confirm: bool = False, confirmation_token: str = "") -> dict:
        """GUARDED WRITE. Change a ticket's status and add a note. Step 1: call without confirm to get a
        preview and confirmation_token (nothing is written). Step 2: only after the user explicitly approves
        the preview, call again with the same arguments, confirm=true and the confirmation_token.
        """
        return ops.update_ticket_status(ticket_id, new_status, note, confirm, confirmation_token)


def _register_context(server: MCPServer, ops: OpsServer) -> None:
    """Register resources and the prompt."""

    @server.resource("ops://schema", mime_type="text/plain", description="SQLite schema of the demo database")
    def schema() -> str:
        """Return the CREATE TABLE statements."""
        return db.SCHEMA.strip()

    @server.resource("ops://policies/support", mime_type="text/markdown", description="Support policy and SLAs")
    def policy() -> str:
        """Return the support policy markdown."""
        return POLICY

    @server.resource("ops://customers/{customer_id}", mime_type="application/json",
                     description="One customer summary as JSON")
    def customer(customer_id: str) -> str:
        """Return one customer summary as JSON (template parameter must be digits)."""
        if not customer_id.isdigit():
            raise ToolError("customer_id must be a positive integer")
        return json.dumps(ops.read("resource:customer", {"customer_id": int(customer_id)}, queries.get_customer))

    @server.resource("ops://audit/recent", mime_type="application/json", description="Newest 20 audit log entries")
    def audit_recent() -> str:
        """Return the newest audit entries as JSON."""
        return json.dumps(ops.audit.tail(20))

    @server.prompt(title="Triage a support ticket")
    def triage_ticket(ticket_id: str) -> str:
        """Walk through one ticket: read it, check the customer and order, then propose a status change."""
        return (f"Triage support ticket {ticket_id} using the ops tools. 1) get_ticket. 2) get_customer and, if the "
                "ticket references an order, get_order. 3) Read ops://policies/support. 4) Summarise the issue, the "
                "customer's value and the SLA in under 120 words. 5) Propose a status change by calling "
                "update_ticket_status WITHOUT confirm and show me the preview. Do not confirm unless I say so.")


def build(db_path: Path, audit_path: Path, actor: str = "claude-via-mcp") -> MCPServer:
    """Create the MCP server with every tool, resource and prompt registered."""
    server = MCPServer("ops", instructions=INSTRUCTIONS, version="0.1.0")
    ops = OpsServer(db_path, audit_path, actor)
    _register_reads(server, ops)
    _register_tickets(server, ops)
    _register_context(server, ops)
    return server


def from_env() -> MCPServer:
    """Build the server from ``OPS_DB`` / ``OPS_AUDIT_LOG`` / ``OPS_ACTOR`` environment variables."""
    db_path = Path(os.environ.get("OPS_DB", "demo.db")).resolve()
    audit_path = Path(os.environ.get("OPS_AUDIT_LOG", "audit/audit.jsonl")).resolve()
    return build(db_path, audit_path, os.environ.get("OPS_ACTOR", "claude-via-mcp"))
