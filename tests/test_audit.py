"""Audit log."""

from ops_server.audit import AuditLog


def test_audit_appends_and_clips(audit_path):
    # Arrange
    log = AuditLog(audit_path)
    # Act
    log.record("get_order", {"order_id": 1}, "ok", {"returned": 1})
    log.record("update_ticket_status", {"note": "x" * 500}, "rejected", {"error": "stale"})
    # Assert
    entries = log.tail(10)
    assert [e["outcome"] for e in entries] == ["ok", "rejected"]
    assert entries[1]["args"]["note"].endswith("...(truncated)") and len(entries[1]["args"]["note"]) < 250
    assert entries[0]["ts"].endswith("+00:00")


def test_tail_of_missing_log_is_empty(tmp_path):
    # Act / Assert
    assert AuditLog(tmp_path / "none.jsonl").tail(5) == []
