# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install (always non-editable so changes take effect)
pip install .
# After any code change:
pip install --force-reinstall .

# CLI — fetch today's data and display in terminal
garmin-readiness

# Web dashboard at http://127.0.0.1:8080
garmin-readiness --serve

# Send daily readiness email (or --dry-run to preview)
garmin-readiness --email [--dry-run]

# Backfill historical data to build a 30-day baseline
garmin-readiness --backfill 30

# Upload/schedule structured Garmin workouts from the training plan
garmin-readiness --workouts [--dry-run]

# Install launchd agents (macOS): daily 7am email + always-on server
garmin-readiness --setup-schedule

# Restart the launchd server after code changes
launchctl kickstart -k "gui/$(id -u)/com.garmin-readiness.server"
```

## Architecture

The app has two interfaces sharing the same data layer:

**CLI** (`cli.py`) — terminal dashboard using `rich`, with flags for fetching, backfilling, emailing, and workout upload.

**Web dashboard** (`server.py`) — FastAPI app with Jinja2 templates. Five tabs: Readiness, Analysis, Calendar, Training Plan, Nutrition. Auth via HTTP Basic (`DASHBOARD_USER`/`DASHBOARD_PASSWORD` env vars; open access if unset).

**Data layer:**
- `metrics.py` — `DailyMetrics` dataclass + `fetch_metrics()`/`fetch_activities()` calling the `garminconnect` API.
- `history.py` — SQLite persistence at `~/.garmin_readiness/history.db`. Two tables: `daily_metrics` (auto-migrating schema) and `activities`. Provides `baseline_stats()` (30-day rolling window), `composite_score()` (mean z-score across scored fields), and `z_score()` (sign-flipped for lower-is-better fields).
- `display.py` — `FIELD_LABELS`, `fmt_value()`, `readiness_label()`, `enrich_activity()` (duration/distance/pace formatting).

**Report** (`report.py`) — builds and sends an HTML email via Gmail SMTP. Calls Claude Haiku for advice text; falls back to rule-based if no API key. Includes planned workout from `plan.py`.

**Training plan** (`plan.py`) — single source of truth for the 12-week plan (`PLAN_START = 2026-05-18`, `TRAINING_WEEKS`). `session_for_date()` returns `(type, label, duration_min)` for any date in the plan window. Consumed by both `report.py` (email) and `server.py` (calendar tab).

**Post-training analysis** (`analysis.py`) — separate SQLite table `activity_analyses` in the same DB. `refresh_analyses()` fetches HR zone data + `summaryDTO` from Garmin for each unanalysed activity, calls Claude Haiku with a cycling-coach prompt, saves result. `load_analyses_for_activities()` enriches activity dicts for the Analysis tab.

**Garmin workouts** (`workouts.py`) — builds `garminconnect.workout.CyclingWorkout` objects for all 27 distinct session types in the plan, uploads templates once, then schedules each on its plan dates via `upload_cycling_workout` + `schedule_workout`.

## Configuration

Copy `.env.example` to `.env`. Env vars are also loaded from `~/.garmin_readiness/.env` (used by launchd since it runs without shell environment).

Key vars: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_TO`, `DASHBOARD_USER`, `DASHBOARD_PASSWORD`.

## Notes

- The composite readiness score is the mean z-score across all `SCORED_FIELDS` (excludes `training_load_chronic` and `vo2_max` which are context-only). Z-scores for lower-is-better fields (stress, ACWR, acute load) are sign-flipped so positive always means better.
- `available_count()` checks how many non-null numeric fields exist — used to detect empty fetches.
- All Garmin API calls are individually try/except'd; a failed endpoint logs at DEBUG and leaves the field `None` rather than crashing.
- The `_advice_cache` dict in `server.py` is in-process only; restarts clear it.
