"""End-to-end: every tool, resource and prompt exercised through a real MCP client over stdio."""

import json
import sqlite3

from tests.mcp_session import payload, with_client

READ_TOOLS = {"search_customers", "get_customer", "list_orders", "get_order", "list_tickets", "get_ticket",
              "sales_summary"}


def _status(db_path, ticket_id):
    return sqlite3.connect(db_path).execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()[0]


def _open_ticket(db_path):
    return sqlite3.connect(db_path).execute("SELECT id FROM tickets WHERE status='open' ORDER BY id").fetchone()[0]


def test_lists_tools_with_safety_annotations(db_path, audit_path):
    # Act
    tools = with_client(db_path, audit_path, lambda c: c.list_tools())
    # Assert
    by_name = {t.name: t for t in tools.tools}
    assert set(by_name) == READ_TOOLS | {"update_ticket_status"}
    assert all(by_name[n].annotations.read_only_hint for n in READ_TOOLS)
    write = by_name["update_ticket_status"]
    assert write.annotations.destructive_hint and "GUARDED WRITE" in write.description
    assert write.input_schema["properties"]["note"]["maxLength"] == 500


def test_read_tools_return_typed_json(db_path, audit_path):
    # Arrange
    async def body(c):
        return [payload(await c.call_tool("search_customers", {"query": "juniper", "limit": 3})),
                payload(await c.call_tool("get_order", {"order_id": 7})),
                payload(await c.call_tool("sales_summary", {"start": "2026-08-01", "end": "2026-11-30",
                                                            "group_by": "country"}))]
    # Act
    customers, order, sales = with_client(db_path, audit_path, body)
    # Assert
    assert customers["returned"] == 3 and all("Juniper" in r["company"] for r in customers["rows"])
    assert order["id"] == 7 and order["items"]
    assert sales["group_by"] == "country" and sales["groups"]


def test_input_validation_rejects_bad_arguments(db_path, audit_path):
    # Arrange
    async def body(c):
        return [await c.call_tool("list_orders", {"limit": 500}),
                await c.call_tool("list_tickets", {"status": "deleted"}),
                await c.call_tool("get_customer", {"customer_id": -1}),
                await c.call_tool("sales_summary", {"start": "2024-01-01", "end": "2026-01-01"})]
    # Act
    results = with_client(db_path, audit_path, body)
    # Assert
    assert all(r.is_error for r in results)
    assert "366 days" in results[3].content[0].text


def test_write_preview_changes_nothing(db_path, audit_path):
    # Arrange
    ticket = _open_ticket(db_path)
    args = {"ticket_id": ticket, "new_status": "resolved", "note": "replacement shipped"}
    # Act
    preview = payload(with_client(db_path, audit_path, lambda c: c.call_tool("update_ticket_status", args)))
    # Assert
    assert preview["status"] == "preview_only" and preview["nothing_written"] is True
    assert _status(db_path, ticket) == "open"


def test_confirm_requires_fresh_token(db_path, audit_path):
    # Arrange
    ticket = _open_ticket(db_path)
    args = {"ticket_id": ticket, "new_status": "pending", "note": "asked customer for photos"}

    async def body(c):
        no_token = await c.call_tool("update_ticket_status", {**args, "confirm": True})
        token = payload(await c.call_tool("update_ticket_status", args))["plan"]["confirmation_token"]
        written = await c.call_tool("update_ticket_status", {**args, "confirm": True, "confirmation_token": token})
        replay = await c.call_tool("update_ticket_status", {**args, "confirm": True, "confirmation_token": token})
        return no_token, written, replay
    # Act
    no_token, written, replay = with_client(db_path, audit_path, body)
    # Assert
    assert no_token.is_error and "confirmation_token" in no_token.content[0].text
    assert payload(written)["status"] == "written" and _status(db_path, ticket) == "pending"
    assert replay.is_error  # same token cannot be used twice: the ticket changed
    notes = sqlite3.connect(db_path).execute("SELECT author, body FROM ticket_notes WHERE ticket_id=?", (ticket,))
    assert notes.fetchall() == [("pytest", "asked customer for photos")]


def test_closed_tickets_cannot_be_reopened(db_path, audit_path):
    # Arrange
    closed = sqlite3.connect(db_path).execute("SELECT id FROM tickets WHERE status='closed'").fetchone()[0]
    args = {"ticket_id": closed, "new_status": "open", "note": "customer wrote back"}
    # Act
    result = with_client(db_path, audit_path, lambda c: c.call_tool("update_ticket_status", args))
    # Assert
    assert result.is_error and "closed is final" in result.content[0].text


def test_resources_and_prompt(db_path, audit_path):
    # Arrange
    async def body(c):
        listed = await c.list_resources()
        templates = await c.list_resource_templates()
        schema = await c.read_resource("ops://schema")
        customer = await c.read_resource("ops://customers/2")
        prompt = await c.get_prompt("triage_ticket", {"ticket_id": "4"})
        return listed, templates, schema, customer, prompt
    # Act
    listed, templates, schema, customer, prompt = with_client(db_path, audit_path, body)
    # Assert
    assert {str(r.uri) for r in listed.resources} == {"ops://schema", "ops://policies/support", "ops://audit/recent"}
    assert [t.uri_template for t in templates.resource_templates] == ["ops://customers/{customer_id}"]
    assert "CREATE TABLE tickets" in schema.contents[0].text
    assert json.loads(customer.contents[0].text)["id"] == 2
    assert "Do not confirm unless I say so" in prompt.messages[0].content.text


def test_every_call_is_audited(db_path, audit_path):
    # Arrange
    async def body(c):
        await c.call_tool("get_ticket", {"ticket_id": 1})
        await c.call_tool("get_customer", {"customer_id": 9999})
        await c.call_tool("update_ticket_status", {"ticket_id": _open_ticket(db_path), "new_status": "pending",
                                                   "note": "checking"})
    # Act
    with_client(db_path, audit_path, body)
    # Assert
    entries = [json.loads(line) for line in audit_path.read_text().splitlines()]
    assert [(e["tool"], e["outcome"]) for e in entries] == [
        ("get_ticket", "ok"), ("get_customer", "rejected"), ("update_ticket_status", "preview")]
