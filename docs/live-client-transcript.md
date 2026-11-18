# Live MCP client session (stdio)

Real `mcp` Python SDK client talking to `python -m ops_server` over stdio on a fresh SYNTHETIC database. 14 MCP requests, 4.7 s wall-clock including server start-up.

### 1. tools/list - 7 ms

```json
{}
```

```
search_customers (read_only=True, destructive=False)
get_customer (read_only=True, destructive=False)
list_orders (read_only=True, destructive=False)
get_order (read_only=True, destructive=False)
list_tickets (read_only=True, destructive=False)
get_ticket (read_only=True, destructive=False)
sales_summary (read_only=True, destructive=False)
update_ticket_status (read_only=False, destructive=True)
```

### 2. resources/list - 7 ms

```json
{}
```

```
ops://schema
ops://policies/support
ops://audit/recent
```

### 3. resources/read ops://policies/support - 8 ms

```json
{}
```

```
# Support policy (synthetic demo company)

- First response targets: urgent 1 hour, high 4 hours, normal 1 business day, low 3 business days.
- Status flow: open -> pending (waiting on customer) -> resolved -> closed. Resolved tickets may be
  reopened; closed is final.
- Every status change needs a short note saying why.
- Agents never change a ticket without the operator's explicit approval of the preview.
- Refunds, discounts and order edits are out of scope for this server; escalate them to a human.

```

### 4. prompts/get triage_ticket - 7 ms

```json
{"ticket_id": "1"}
```

```
Triage support ticket 1 using the ops tools. 1) get_ticket. 2) get_customer and, if the ticket references an order, get_order. 3) Read ops://policies/support. 4) Summarise the issue, the customer's value and the SLA in under 120 words. 5) Propose a status change by calling update_ticket_status WITHOUT confirm and show me the preview. Do not confirm unless I say so.
```

### 5. tools/call list_tickets - 8 ms

```json
{"status": "open", "limit": 3}
```

```
{
  "rows": [
    {
      "id": 13,
      "customer_id": 33,
      "order_id": 58,
      "subject": "Where is my order?",
      "status": "open",
      "priority": "high",
      "created_at": "2026-08-11"
    },
    {
      "id": 22,
      "customer_id": 15,
      "order_id": 116,
      "subject": "Refund not received",
      "status": "open",
      "priority": "high",
      "created_at": "2026-08-26"
    },
    {
      "id": 40,
      "customer_id": 3,
      "order_id": 122,
      "subject": "Wrong item shipped",
      "status": "open",
      "priority": "high",
      "created_at": "2026-09-09"
    }
  ],
  "returned": 3,
  "truncated": true
}
```

### 6. tools/call get_ticket - 2 ms

```json
{"ticket_id": 13}
```

```
{
  "id": 13,
  "customer_id": 33,
  "order_id": 58,
  "subject": "Where is my order?",
  "body": "Order #58 still shows pending after a week.",
  "status": "open",
  "priority": "high",
  "created_at": "2026-08-11",
  "updated_at": "2026-08-11",
  "notes": []
}
```

### 7. tools/call sales_summary - 4 ms

```json
{"start": "2026-08-01", "end": "2026-11-30", "group_by": "month"}
```

```
{
  "start": "2026-08-01",
  "end": "2026-11-30",
  "group_by": "month",
  "groups": [
    {
      "bucket": "2026-08",
      "orders": 26,
      "revenue_usd": "30190.00"
    },
    {
      "bucket": "2026-09",
      "orders": 39,
      "revenue_usd": "42926.00"
    },
    {
      "bucket": "2026-10",
      "orders": 39,
      "revenue_usd": "41014.00"
    },
    {
      "bucket": "2026-11",
      "orders": 20,
      "revenue_usd": "24964.00"
    }
  ],
  "total_revenue_usd": "139094.00"
}
```

### 8. tools/call list_orders (is_error=true) - 3 ms

```json
{"limit": 1000}
```

```
Error executing tool list_orders: 1 validation error for list_ordersArguments
limit
  Input should be less than or equal to 100 [type=less_than_equal, input_value=1000, input_type=int]
    For further information visit https://errors.pydantic.dev/2.14/v/less_than_equal
```

### 9. tools/call search_customers - 6 ms

```json
{"query": "' OR 1=1 --"}
```

```
{
  "rows": [],
  "returned": 0,
  "truncated": false
}
```

### 10. tools/call update_ticket_status (is_error=true) - 8 ms

```json
{"ticket_id": 13, "new_status": "pending", "note": "Asked the customer for photos of the damage.", "confirm": true}
```

```
Error executing tool update_ticket_status: missing or stale confirmation_token: preview the change again (confirm=false)
```

### 11. tools/call update_ticket_status - 2 ms

```json
{"ticket_id": 13, "new_status": "pending", "note": "Asked the customer for photos of the damage."}
```

```
{
  "status": "preview_only",
  "nothing_written": true,
  "plan": {
    "ticket_id": 13,
    "from_status": "open",
    "to_status": "pending",
    "note": "Asked the customer for photos of the damage.",
    "ticket_updated_at": "2026-08-11",
    "confirmation_token": "5bee5562ba38cd06"
  },
  "next_step": "Show this to the user. If they approve, call again with confirm=true and this confirmation_token."
}
```

### 12. tools/call update_ticket_status - 94 ms

```json
{"ticket_id": 13, "new_status": "pending", "note": "Asked the customer for photos of the damage.", "confirm": true, "confirmation_token": "5bee5562ba38cd06"}
```

```
{
  "status": "written",
  "ticket_id": 13,
  "from_status": "open",
  "to_status": "pending",
  "note": "Asked the customer for photos of the damage.",
  "ticket_updated_at": "2026-08-11",
  "updated_at": "2026-11-18T01:08:55+00:00"
}
```

### 13. tools/call update_ticket_status (is_error=true) - 14 ms

```json
{"ticket_id": 13, "new_status": "pending", "note": "Asked the customer for photos of the damage.", "confirm": true, "confirmation_token": "5bee5562ba38cd06"}
```

```
Error executing tool update_ticket_status: cannot move ticket 13 from pending to pending; allowed: closed, open, resolved
```

### 14. resources/read ops://audit/recent - 8 ms

```json
{}
```

```
[
 {
  "ts": "2026-11-18T01:08:55.058+00:00",
  "tool": "search_customers",
  "args": {
   "query": "' OR 1=1 --",
   "limit": 10
  },
  "outcome": "ok",
  "detail": {
   "returned": 0
  }
 },
 {
  "ts": "2026-11-18T01:08:55.061+00:00",
  "tool": "update_ticket_status",
  "args": {
   "ticket_id": 13,
   "new_status": "pending",
   "note": "Asked the customer for photos of the damage.",
   "confirm": true
  },
  "outcome": "rejected",
  "detail": {
   "error": "missing or stale confirmation_token: preview the change again (confirm=false)"
  }
 },
 {
  "ts": "2026-11-18T01:08:55.069+00:00",
  "tool": "update_ticket_status",
  "args": {
   "ticket_id": 13,
   "new_status": "pending",
   "note": "Asked the customer for photos of the damage.",
   "confirm": false
  },
  "outcome": "preview",
  "detail": {
   "returned": 1
  }
 },
 {
  "ts": "2026-11-18T01:08:55.159+00:00",
  "tool": "update_ticket_status",
  "args": {
   "ticket_id": 13,
   "new_status": "pending",
   "note": "Asked the customer for photos of the damage.",
   "confirm": true
  },
  "outcome": "written",
  "detail": {
   "from": "open",
   "to": "pending"
  }
 },
 {
  "ts": "2026-11-18T01:08:55.172+00:00",
  "tool": "update_ticket_status",
  "args": {
   "ticket_id": 13,
   "new_status": "pending",
   "note": "Asked the customer for photos of the damage.",
   "confirm": true
  },
  "outcome": "rejected",
  "detail": {
   "error": "cannot move ticket 13 from pending to pending; allowed: closed, open, resolved"
  }
 }
]
```
