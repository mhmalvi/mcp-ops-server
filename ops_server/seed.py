"""Deterministic SYNTHETIC seed data. Every name, company and email is invented (.example TLD).

Run ``python -m ops_server.seed demo.db`` to build the demo database.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

from ops_server import db

FIRST = ["Ava", "Liam", "Noor", "Mateo", "Yuki", "Zara", "Omar", "Ines", "Kofi", "Lena", "Ravi", "Mia"]
LAST = ["Hart", "Okafor", "Silva", "Novak", "Reyes", "Lund", "Haddad", "Moreau", "Tanaka", "Byrne"]
COMPANIES = ["Northwind Bakery", "Bluefin Logistics", "Juniper Dental", "Halcyon Studio", "Copperleaf Legal",
             "Maple & Moss", "Orbit Analytics", "Quill Books", "Fernway Travel", "Saltmarsh Foods"]
COUNTRIES = ["GB", "DE", "US", "CA", "IE", "AU", "NZ", "FR", "NL", "SE"]
PLANS = ["starter", "team", "business", "enterprise"]
PRODUCTS = [("SKU-DESK", "Standing desk", 42900), ("SKU-CHAIR", "Ergonomic chair", 31900),
            ("SKU-LAMP", "Desk lamp", 4900), ("SKU-MON27", "27in monitor", 27900),
            ("SKU-DOCK", "USB-C dock", 18900), ("SKU-KEYB", "Mechanical keyboard", 12900),
            ("SKU-HEAD", "Noise-cancelling headset", 21900), ("SKU-CAM", "4K webcam", 9900)]
SUBJECTS = [("Order arrived damaged", "The box was crushed and the {p} has a cracked panel."),
            ("Where is my order?", "Order #{o} still shows pending after a week."),
            ("Wrong item shipped", "We ordered a {p} but received something else."),
            ("Invoice needs VAT number", "Please re-issue the invoice for order #{o} with our VAT id."),
            ("Refund not received", "We returned order #{o} two weeks ago and have no refund yet."),
            ("Bulk discount question", "Do you offer volume pricing for 20+ units of the {p}?")]
START = date(2026, 8, 1)


def _customers(rng: random.Random, count: int) -> list[tuple]:
    """Generate customer rows."""
    rows = []
    for cid in range(1, count + 1):
        first, last, company = rng.choice(FIRST), rng.choice(LAST), COMPANIES[(cid - 1) % len(COMPANIES)]
        slug = company.lower().replace(" & ", "-").replace(" ", "-")
        email = f"{first.lower()}.{last.lower()}{cid}@{slug}.example"
        created = START - timedelta(days=rng.randint(30, 700))
        rows.append((cid, f"{first} {last}", company, email, rng.choice(COUNTRIES), rng.choice(PLANS), created.isoformat()))
    return rows


def _orders(rng: random.Random, customers: int, count: int) -> tuple[list[tuple], list[tuple]]:
    """Generate order rows and their line items."""
    orders, items = [], []
    for oid in range(1, count + 1):
        lines = rng.sample(range(1, len(PRODUCTS) + 1), rng.randint(1, 3))
        total = 0
        for pid in lines:
            qty, price = rng.randint(1, 4), PRODUCTS[pid - 1][2]
            items.append((oid, pid, qty, price))
            total += qty * price
        status = rng.choices(["paid", "shipped", "pending", "refunded", "cancelled"], [30, 45, 12, 8, 5])[0]
        created = START + timedelta(days=rng.randint(0, 108))
        orders.append((oid, rng.randint(1, customers), status, total, created.isoformat()))
    return orders, items


def _tickets(rng: random.Random, orders: list[tuple], count: int) -> list[tuple]:
    """Generate ticket rows linked to real orders."""
    rows = []
    for tid in range(1, count + 1):
        order = rng.choice(orders)
        subject, body = rng.choice(SUBJECTS)
        product = rng.choice(PRODUCTS)[1]
        created = date.fromisoformat(order[4]) + timedelta(days=rng.randint(1, 10))
        status = rng.choices(["open", "pending", "resolved", "closed"], [40, 20, 25, 15])[0]
        priority = rng.choices(["low", "normal", "high", "urgent"], [20, 50, 22, 8])[0]
        rows.append((tid, order[1], order[0], subject, body.format(p=product, o=order[0]), status, priority,
                     created.isoformat(), created.isoformat()))
    return rows


def build(path: Path, seed: int = 42) -> dict[str, int]:
    """Create and fill a demo database.

    Args:
        path: Target SQLite file (must not exist).
        seed: RNG seed; the same seed always yields the same data.

    Returns:
        Row counts per table.
    """
    rng = random.Random(seed)
    customers = _customers(rng, 40)
    orders, items = _orders(rng, len(customers), 160)
    tickets = _tickets(rng, orders, 60)
    conn = db.create(path)
    with conn:
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", customers)
        conn.executemany("INSERT INTO products VALUES (?,?,?,?)", [(i + 1, *p) for i, p in enumerate(PRODUCTS)])
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
        conn.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)
        conn.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?)", tickets)
    conn.close()
    return {"customers": len(customers), "orders": len(orders), "order_items": len(items), "tickets": len(tickets)}


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo.db")
    print(build(target))
