from __future__ import annotations

import logging
from pathlib import Path

import garminconnect

TOKENSTORE = Path.home() / ".ai_endurance_coach_over50" / "session"
logger = logging.getLogger(__name__)


def get_api(email: str, password: str) -> garminconnect.Garmin:
    TOKENSTORE.parent.mkdir(parents=True, exist_ok=True)
    api = garminconnect.Garmin(
        email,
        password,
        prompt_mfa=lambda: input("Garmin MFA code: "),
    )
    # 0.3.x: login(tokenstore=path) loads cached tokens if present,
    # falls back to credentials, and saves new tokens automatically.
    api.login(tokenstore=str(TOKENSTORE))
    return api
