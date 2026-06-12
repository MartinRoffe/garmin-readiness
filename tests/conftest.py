"""Shared fixtures: every test gets a throwaway SQLite DB so nothing touches
the real ~/.garmin_readiness/history.db."""
import pytest

import garmin_readiness.history as history


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "history.db")
