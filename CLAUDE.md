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

# Web dashboard at http://127.0.0.1:8743
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

**Web dashboard** (`server.py`) — FastAPI app with Jinja2 templates. Tabs: Readiness, Performance, Analysis, Calendar, Training Plan, Compliance, Nutrition, Sleep, Body, Haute Route, Tenerife, Coach Chat. Auth via HTTP Basic (`DASHBOARD_USER`/`DASHBOARD_PASSWORD` env vars; open access if unset). Key endpoints:
- `/` — readiness dashboard; `?date=YYYY-MM-DD` for historical view
- `/refresh` — force-fetches fresh Garmin data and evicts advice caches
- `/send-email` — manual email trigger (same logic as CLI `--email`)
- `/sync-workouts` — re-uploads and schedules all plan cycling workouts to Garmin
- `/analysis`, `/analysis-refresh` — post-workout analysis tab and refresh trigger
- `/performance` — PMC (CTL/ATL/TSB), Z2 HR drift, CTL/TSB projection to event, zone polarisation charts, FTP trend, Z2 cardiac drift trend
- `/calendar` — unified plan/camp/event-prep calendar with completion tracking, interference flags, BTB log
- `/training`, `/compliance` — plan completion stats and per-discipline adherence
- `/nutrition` — 4-week meal plan cycle
- `/sleep` — 30-day sleep quality history with stage breakdown
- `/body`, `/body-refresh` — body composition and blood pressure tracking
- `/withings-sync` — push Withings measurements to Garmin, then refresh body data
- `/haute-route` — 46-week Haute Route Alpes 2027 plan with CTL projection
- `/tenerife` — Tenerife cycling camp itinerary
- `/coach-chat-stream` — SSE streaming coach chat endpoint
- `/apply-plan-change` — persist a coach-proposed plan override
- `/log-rpe` — POST: save session RPE (date, activity_id, rpe 1–5, optional note)
- `/api/ftp-tests` — GET: return all FTP test records (date, ftp_hr, ftp_hr_max)
- `/log-btb` — POST: save back-to-back fatigue rating (date, day_number, fatigue_rating, note)
- `/btb-summary` — GET: return consecutive cycling pairs with fatigue notes

**Data layer:**
- `metrics.py` — `DailyMetrics` dataclass + `fetch_metrics()`/`fetch_activities()` calling the `garminconnect` API.
- `history.py` — SQLite persistence at `~/.garmin_readiness/history.db`. Tables: `daily_metrics` (auto-migrating schema), `activities`, `body_metrics`, `blood_pressure`, `daily_advice`, `text_cache`, `coach_conversations`, `plan_overrides`, `coach_memory`, `session_rpe`, `ftp_tests`, `btb_notes`. Provides `baseline_stats()` (30-day rolling window), `composite_score()` (mean z-score across scored fields), `z_score()` (sign-flipped for lower-is-better fields), `intensity_distribution_by_week()`, `load_session_rpe()`, `save_session_rpe()`, `load_ftp_tests()`, `save_ftp_test()`, `load_btb_summary()`, `save_btb_note()`.
- `display.py` — `FIELD_LABELS`, `fmt_value()`, `readiness_label()`, `enrich_activity()` (duration/distance/pace formatting).
- `client.py` — wraps `garminconnect` session/token handling. All `get_api()` calls go through here.

**Alerts** (`alerts.py`) — `check_fatigue_alerts(today)` checks three conditions and returns a list of `{type, severity, message}` dicts: `HRV_TREND` (4 strictly descending mornings → HIGH), `TSB_DEEP` (TSB < −180 for ≥5 days → HIGH), `VOLUME_SPIKE` (actual weekly minutes > planned × 1.20 → MODERATE). Called in `_build_context()` and `run_report()`.

**Report** (`report.py`) — builds and sends an HTML email via Gmail SMTP. Calls Claude for advice text; falls back to rule-based if no API key. Includes planned workout from `plan.py`. `generate_weekly_briefing()` produces a Monday coach briefing (form summary, key session, execution cue) via Claude Haiku, cached in `text_cache` keyed by `weekly_briefing_v1_{monday_iso}`. HIGH fatigue alerts are prepended as a callout block before the readiness section.

**Training plan** (`plan.py`) — single source of truth for the 12-week charity-ride prep plan (`PLAN_START = 2026-05-18`, `TRAINING_WEEKS`). `session_for_date()` returns `(type, label, duration_min)` for any date in the plan window. Also `session_for_date_extended()` which covers the Tenerife camp and event prep block. Consumed by `report.py` (email) and `server.py` (calendar tab). Also contains `MAXI_INTERVALS` — a dict keyed by week number (1–12) with interval specs (`sets`, `work_s`, `rest_s`, `kb`, `easy`, `norwegian` flags) used to populate clickable interval modals on MaxiClimber calendar tiles. Week 9 introduces the Norwegian 4×4 protocol.

**Haute Route plan** (`hr_plan.py`) — separate 46-week plan for Haute Route Alpes 2027 (`HR_PLAN_START = 2026-10-05`, event Aug 23–29 2027). Five phases: Base (wks 1–13), Build (14–25), Specific Build (26–35, mountain camp wk 31), Peak (36–43, two 3-day simulation blocks), Taper (44–46). `hr_session_for_date()` and `build_hr_calendar_weeks()` mirror the API of `plan.py`. `HR_EVENT_STAGES` holds the 7 stage details (km, elevation, key climb). Rendered at `/haute-route`.

**Post-training analysis** (`analysis.py`) — separate SQLite table `activity_analyses` in the same DB. `refresh_analyses()` fetches HR zone data + `summaryDTO` from Garmin for each unanalysed activity, calls Claude Sonnet with a discipline-specific coach prompt, saves result. After saving, if the session label is in `_FTP_SESSION_LABELS` and `detail["ftp_effort_avg_hr"]` is present, auto-populates `ftp_tests` table via `save_ftp_test()`. `load_analyses_for_activities()` enriches activity dicts for the Analysis tab. `_find_compound_companion()` detects when an activity is one half of a compound plan session and returns the paired activity so the prompt can reference both. `_build_analysis_prompt()` injects a "do not flag as short" note when actual duration meets or exceeds the plan (≥95%), preventing Claude from misreading a completed session as cut short. Also contains:
- `prefetch_workout_descriptions()` — generates 2-sentence coaching notes per session label, cached in `workout_descriptions` table
- `prefetch_nutrition_targets()` — generates daily macro targets per session type+duration, cached in `nutrition_targets` table
- `prefetch_fuelling_plans()` — generates in-ride carb/fluid/sodium plans for endurance sessions ≥75 min, cached in `fuelling_plans` table
- `generate_recovery_suggestion()` — coach advice on missed sessions, cached in `text_cache`
To regenerate a stale analysis: `DELETE FROM activity_analyses WHERE activity_id = <id>` then hit `/analysis-refresh`.

**Compound sessions** (`plan.py` → `COMPOUND_SESSIONS`) — dict mapping plan label → list of sub-sessions with `garmin_key`. Example: `"KB + MaxiClimber"` maps to `strength_training` + `stair_climbing`. This is the single source of truth consumed by three places: calendar completion (tracks each sub-session independently), `_merge_compound_activities()` in `server.py` (collapses paired activities into one analysis card with side-by-side HR zones), and `_find_compound_companion()` in `analysis.py` (adds companion context to the coach prompt). Add new compound session types here first.

**Interference flagging** (`server.py`) — `QUALITY_BIKE_LABELS` module-level set lists sessions that warrant an interference check (tempo, sweetspot, threshold, hill repeats, FTP tests). In `calendar_view()`, for each such day, the previous 24 h is scanned for `type_key in {"strength_training", "stair_climbing"}`; if found, `day["interference"] = True` and `day["interference_note"]` is set. The calendar template renders an amber ⚠️ badge inline with the session label.

**Body composition** (`body.py`) — `fetch_body_composition()` and `fetch_blood_pressure()` pull data from Garmin Connect. `bp_classification()` returns a label and colour for blood pressure readings. Data saved to `body_metrics` and `blood_pressure` SQLite tables.

**Withings sync** (`withings.py`) — `sync_withings_to_garmin()` fetches recent Withings measurements (weight, body fat, blood pressure), pushes them to Garmin Connect via `add_body_composition()` / `set_blood_pressure()`, and also writes directly to SQLite for immediate availability. Requires `withings-sync` package and an interactive OAuth step on first run.

**Mersea routes** (`mersea_routes.py`) — coastal route data for the Mersea Island build (rucking progression in plan weeks 9–10). `MERSEA_TARGET_DATE` drives a countdown displayed on the Calendar tab.

**Garmin workouts** (`workouts.py`) — builds `garminconnect.workout.CyclingWorkout` objects for all 27 distinct session types in the plan, uploads templates once, then schedules each on its plan dates via `upload_cycling_workout` + `schedule_workout`.

## AI Coach chat

`_COACH_SYSTEM` in `server.py` defines the coach persona and context injection format. On each request `_build_coach_context()` assembles: PMC metrics, today's readiness, all remaining plan sessions (12-week + Tenerife camp + event prep), recent activities, body composition, active plan overrides, coach memory, RAG-retrieved past session analyses, recent RPE logs (last 7 days from `session_rpe` table), and back-to-back training history (5 most recent pairs from `btb_notes`).

The coach can call the `propose_plan_change` tool to suggest a duration/type modification. The server handles the tool-use turn, enriches the proposal with current plan data, and returns it as a JSON `proposal` alongside the text reply. The frontend renders it as a confirmation card; on approval `POST /apply-plan-change` persists it as a `plan_override`.

Coach memory (`coach_memory` SQLite table) is a compact durable memo (150–250 words) refreshed in a background thread when the conversation reaches `_MEMO_MIN_MESSAGES` or after `_MEMO_STALE_HOURS`. It captures goals, athlete tendencies, and decisions made across sessions. The in-context history window is the last 20 messages; the memo carries longer-term context beyond that.

The streaming endpoint (`/coach-chat-stream`) uses `StreamingResponse` with a sync SSE generator. The non-streaming `/coach-chat` endpoint exists for fallback.

## CTL/ATL/TSB projection

`_ctl_projection()` in `server.py` projects CTL, ATL, and TSB from today to the event date using plan sessions across all blocks (12-week, Tenerife camp, event prep). CTL uses additive per-minute deltas calibrated against week-1 observed data with a soft ceiling above CTL 300. ATL uses a 7-day exponential decay: `atl = max(0, atl * exp(−1/7) + rate * dur_min)` on session days, `atl = max(0, atl * exp(−1/7))` on rest days. Each projected entry returns `{label, ctl, atl, tsb}`. The Performance tab renders projected TSB as a dashed amber overlay on the TSB chart with an event vertical line. Note: ATL/CTL are in Garmin training-load units, not standard TSS, so absolute TSB values differ from classic PMC conventions. `_hr_ctl_projection()` does the same across the 46-week Haute Route plan (CTL only).

## Configuration

Copy `.env.example` to `.env`. Env vars are also loaded from `~/.garmin_readiness/.env` (used by launchd since it runs without shell environment).

Key vars: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_TO`, `DASHBOARD_USER`, `DASHBOARD_PASSWORD`.

## Notes

- The composite readiness score is the mean z-score across all `SCORED_FIELDS` (excludes `training_load_chronic` and `vo2_max` which are context-only, and calorie/step/sleep-stage fields). Z-scores for lower-is-better fields (stress, ACWR, acute load) are sign-flipped so positive always means better.
- `available_count()` checks how many non-null numeric fields exist — used to detect empty fetches. The email gate checks specifically for `sleep_score` and `body_battery_morning` (only populated after the watch syncs overnight data); if either is missing, the CLI exits with code 2 and the launchd retry loop tries again in 30 minutes.
- All Garmin API calls are individually try/except'd; a failed endpoint logs at DEBUG and leaves the field `None` rather than crashing.
- Templates are package data — any change to a `.html` file requires `pip install --force-reinstall .` before the running server picks it up.
- Claude model usage: **Sonnet** for coach chat and post-workout activity analysis; **Haiku** for email advice, recovery suggestions, workout descriptions, nutrition targets, fuelling plans, weekly briefings, and coach memory summaries.

## AI text caching

There are several cache layers; know which to clear when regenerating AI output:

| Cache | Location | What it holds | How to clear |
|-------|----------|---------------|--------------|
| `_advice_cache` | `server.py` in-process dict | Daily readiness advice | Restart server |
| `daily_advice` | SQLite table | Per-date advice (survives restart) | `DELETE FROM daily_advice WHERE date = '...'` |
| `text_cache` | SQLite table | Workout descriptions, metric explainers, recovery suggestions, fuelling plans, weekly briefings (key: `weekly_briefing_v1_{monday_iso}`) | `DELETE FROM text_cache WHERE key = '...'` |
| `activity_analyses` | SQLite table | Per-activity coach analysis | `DELETE FROM activity_analyses WHERE activity_id IN (...)` then hit `/analysis-refresh` |
| `workout_descriptions` | SQLite table | 2-sentence coaching notes per session label | `DELETE FROM workout_descriptions WHERE label = '...'` |
| `nutrition_targets` | SQLite table | Daily macro targets per session type+duration | `DELETE FROM nutrition_targets WHERE session_key = '...'` |
| `fuelling_plans` | SQLite table | In-ride carb/fluid/sodium plans | `DELETE FROM fuelling_plans WHERE session_key = '...'` |

## New SQLite tables (added in 9-feature release)

| Table | Key columns | Purpose |
|-------|-------------|---------|
| `session_rpe` | `date, activity_id, rpe (1–5), note` | User-logged perceived effort per activity |
| `ftp_tests` | `date UNIQUE, activity_id, ftp_hr, ftp_hr_max` | FTP test LTHR history; auto-populated by `refresh_analyses()` |
| `btb_notes` | `date, day_number, fatigue_rating, note` | Back-to-back fatigue logs from calendar modal |

All three use `_ensure_*_schema()` lazy-init (CREATE TABLE IF NOT EXISTS) called inside each read/write function — no migration needed on first access.
