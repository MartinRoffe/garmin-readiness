"""Shared fixtures: every test gets a throwaway SQLite DB so nothing touches
the real ~/.ai_endurance_coach_over50/history.db."""
import pytest

import ai_endurance_coach_over50.history as history


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB_PATH", tmp_path / "history.db")
