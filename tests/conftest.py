"""Fixtures: a fresh seeded SYNTHETIC database per test."""

from __future__ import annotations

from pathlib import Path

import pytest

from ops_server import seed


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A freshly seeded demo database in a temp dir."""
    path = tmp_path / "demo.db"
    seed.build(path)
    return path


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    """Where the audit log for this test goes."""
    return tmp_path / "audit" / "audit.jsonl"
