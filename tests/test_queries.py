"""Business logic: parameterized queries, limits, money, reporting and the ticket state machine."""

import sqlite3
from datetime import date

import pytest

from ops_server import db, queries, seed
from ops_server.queries import OpsError


@pytest.fixture
def conn(db_path):
    connection = db.connect_readonly(db_path)
    yield connection
    connection.close()


def test_seed_is_deterministic(tmp_path):
    # Arrange
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    # Act
    seed.build(a)
    seed.build(b)
    # Assert
    rows = [sqlite3.connect(p).execute("SELECT * FROM orders ORDER BY id").fetchall() for p in (a, b)]
    assert rows[0] == rows[1] and len(rows[0]) == 160


def test_readonly_connection_rejects_writes(conn):
    # Act / Assert
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM tickets")


def test_search_customers_limits_and_flags_truncation(conn):
    # Act
    page = queries.search_customers(conn, "example", limit=5)
    # Assert
    assert page["returned"] == 5 and page["truncated"] is True


def test_search_treats_wildcards_and_quotes_as_text(conn):
    # Act
    percent = queries.search_customers(conn, "%", limit=10)
    injection = queries.search_customers(conn, "' OR 1=1 --", limit=10)
    # Assert
    assert percent["returned"] == 0
    assert injection["returned"] == 0


def test_get_customer_includes_value_and_open_tickets(conn):
    # Act
    customer = queries.get_customer(conn, 1)
    # Assert
    assert customer["email"].endswith(".example")
    assert set(customer) >= {"order_count", "lifetime_value_usd", "open_ticket_ids"}


def test_missing_records_raise_ops_error(conn):
    # Act / Assert
    for fn, arg in ((queries.get_customer, 999), (queries.get_order, 999), (queries.get_ticket, 999)):
        with pytest.raises(OpsError, match="not found"):
            fn(conn, arg)


def test_list_orders_filters_compose(conn):
    # Act
    page = queries.list_orders(conn, customer_id=None, status="refunded", since=date(2026, 9, 1), limit=100)
    # Assert
    assert page["rows"] and all(r["status"] == "refunded" and r["created_at"] >= "2026-09-01" for r in page["rows"])
    assert all("." in r["total_usd"] for r in page["rows"])


def test_get_order_totals_match_line_items(conn):
    # Act
    order = queries.get_order(conn, 1)
    # Assert
    cents = sum(i["quantity"] * round(float(i["unit_price_usd"]) * 100) for i in order["items"])
    assert queries.money(cents) == order["total_usd"]


def test_list_tickets_orders_by_urgency(conn):
    # Act
    rows = queries.list_tickets(conn, status=None, priority=None, customer_id=None, limit=100)["rows"]
    # Assert
    rank = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    assert [rank[r["priority"]] for r in rows] == sorted(rank[r["priority"]] for r in rows)


def test_sales_summary_totals_and_validation(conn):
    # Act
    report = queries.sales_summary(conn, date(2026, 8, 1), date(2026, 11, 30), "month")
    # Assert
    total = sum(round(float(g["revenue_usd"]) * 100) for g in report["groups"])
    assert queries.money(total) == report["total_revenue_usd"]
    with pytest.raises(OpsError, match="on or after"):
        queries.sales_summary(conn, date(2026, 9, 1), date(2026, 8, 1), "day")
    with pytest.raises(OpsError, match="366 days"):
        queries.sales_summary(conn, date(2024, 1, 1), date(2026, 1, 1), "day")


@pytest.mark.parametrize(("cents", "text"), [(0, "0.00"), (5, "0.05"), (123456, "1234.56"), (None, "0.00")])
def test_money_formatting(cents, text):
    # Act / Assert
    assert queries.money(cents) == text


def _ticket_with_status(conn, status):
    return conn.execute("SELECT id FROM tickets WHERE status = ? ORDER BY id LIMIT 1", (status,)).fetchone()["id"]


def test_state_machine_rejects_illegal_moves(conn):
    # Arrange
    closed = _ticket_with_status(conn, "closed")
    # Act / Assert
    with pytest.raises(OpsError, match="closed is final"):
        queries.plan_ticket_update(conn, closed, "open", "reopen please")


def test_plan_requires_note_and_returns_token(conn):
    # Arrange
    ticket = _ticket_with_status(conn, "open")
    # Act
    plan = queries.plan_ticket_update(conn, ticket, "pending", "waiting on customer photos")
    # Assert
    assert plan["from_status"] == "open" and len(plan["confirmation_token"]) == 16
    with pytest.raises(OpsError, match="note"):
        queries.plan_ticket_update(conn, ticket, "pending", "   ")


def test_apply_writes_status_and_note_and_invalidates_token(db_path, conn):
    # Arrange
    ticket = _ticket_with_status(conn, "open")
    plan = queries.plan_ticket_update(conn, ticket, "resolved", "replacement shipped")
    # Act
    with db.write_transaction(db_path) as wconn:
        done = queries.apply_ticket_update(wconn, plan, "tester")
    # Assert
    after = queries.get_ticket(conn, ticket)
    assert done["to_status"] == after["status"] == "resolved"
    assert after["notes"][-1] == {"author": "tester", "body": "replacement shipped", "created_at": done["updated_at"]}
    replan = queries.plan_ticket_update(conn, ticket, "closed", "replacement shipped")
    assert replan["confirmation_token"] != plan["confirmation_token"]
