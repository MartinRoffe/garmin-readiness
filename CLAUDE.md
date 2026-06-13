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
- `/log-fuelling` — POST: save in-ride fuelling compliance log (date, activity_id, planned/actual carbs g/h, fluid_ok, note)

**Data layer:**
- `metrics.py` — `DailyMetrics` dataclass + `fetch_metrics()`/`fetch_activities()` calling the `garminconnect` API. Nutrition fields (`calories_consumed`, `calorie_goal`, `calorie_goal_adjusted`, `carbs_consumed`, `protein_consumed`) are populated from `get_nutrition_daily_food_log(date)`. The Garmin API returns `content["carbs"]` and `content["protein"]` (not `totalCarbohydrates`/`totalProtein`). Also fetches `resting_hr` (daily summary `restingHeartRate`, falling back to `get_rhr_day`) and probes `get_training_status` for a `heatAltitudeAcclimationDTO` → `heat_acclimation_pct` / `altitude_acclimation` (field names unverified — multi-candidate defensive probing, logs the raw DTO at DEBUG).
- `history.py` — SQLite persistence at `~/.garmin_readiness/history.db`. Tables: `daily_metrics` (auto-migrating schema), `activities`, `body_metrics`, `blood_pressure`, `daily_advice`, `text_cache`, `coach_conversations`, `plan_overrides`, `coach_memory`, `session_rpe`, `ftp_tests`, `btb_notes`, `activity_durability`, `fuelling_logs`. Provides `baseline_stats()` (30-day rolling window), `composite_score()` (mean z-score across scored fields), `z_score()` (sign-flipped for lower-is-better fields), `intensity_distribution_by_week()`, `load_session_rpe()`, `save_session_rpe()`, `load_ftp_tests()`, `save_ftp_test()`, `load_btb_summary()`, `save_btb_note()`, `weekly_monotony_strain()` (Foster monotony/strain per week), `save_durability()`/`load_durability()`/`durability_exists()`, `estimated_wkg_history()`/`latest_estimated_wkg()` (ACSM estimate from VO2max + weight: `est_ftp_w = 0.80 × (vo2max − 7) × kg / 10.8`), `acclimation_latest()`, `ftp_retest_due()`, `save_fuelling_log()`/`load_fuelling_logs()`. `raw_history()` returns `carbs_consumed`, `protein_consumed`, `rest_stress`, and `resting_hr` alongside the other daily fields.
- `modulation.py` — HRV-guided traffic light. `hrv_traffic_light(m, comp_z)` classifies the day green/amber/red/unknown from last-night HRV z-score vs 30-day baseline (+ 7d/30d ratio + composite backstop). `session_modulation(target, m, comp_z, light=)` turns amber/red into a concrete session swap (amber: variant map, duration kept; red: Recovery Spin 30 min) applied via the existing `/apply-plan-change` override flow. Returns None when green/unknown, rest day, or an override already exists. Covers `session_for_date_extended` (12-week plan + Tenerife + event prep + charity ride) and falls back to `hr_session_for_date` for Haute Route plan dates. Two amber maps: `EASIER_VARIANT` (12-week vocabulary) and `HR_EASIER_VARIANT` (HR vocabulary — `vo2`/`sweetspot`/`tempo`/`ftp`→Z2 Endurance, `endurance`→Z2 Easy, `long`/`back_to_back`→Long Ride (Easy); `recovery`/`gym` absent = pill only). Kept separate so HR-plan overrides stay within the types/labels `hr_calendar.html` colours and modals key off. Red day on an HR date uses type `recovery` (not `bike`) for the same reason. `hr_session_for_date()` and `build_hr_calendar_weeks()` honour `plan_overrides` (days carry an `overridden` flag and weekly `total_hrs` reflects overridden durations); `_hr_ctl_projection` iterates `HR_TRAINING_WEEKS` directly, so projected CTL deliberately ignores overrides.
- `display.py` — `FIELD_LABELS`, `fmt_value()`, `readiness_label()`, `enrich_activity()` (duration/distance/pace formatting).
- `client.py` — wraps `garminconnect` session/token handling. All `get_api()` calls go through here.

**Nutrition surfacing** — `_build_context()` (readiness tab) packages a `nutrition_today` dict `{calories, tdee, goal, carbs, protein, balance}` from the current `DailyMetrics` and passes it to `dashboard.html`, which renders a "Today's Nutrition" card (colour-coded balance: green=deficit, amber=small surplus, red=large surplus). `_body_context()` computes 14-day rolling averages (`avg_carbs`, `avg_protein`) from `raw_history()` and exposes them as extra tiles on `body.html`. The nutrition tab (`nutrition.html`) already had conditional carbs/protein blocks — they now populate once `raw_history()` returns those columns.

**Alerts** (`alerts.py`) — `check_fatigue_alerts(today)` checks five conditions and returns a list of `{type, severity, message}` dicts: `HRV_TREND` (4 strictly descending mornings → HIGH), `TSB_DEEP` (TSB < −180 for ≥5 days → HIGH), `VOLUME_SPIKE` (actual weekly minutes > planned × 1.20 → MODERATE), `ILLNESS_RISK` (2-of-3: HRV z < −1.5, resting-HR z > +1.5 [rest_stress fallback], sleep z < −1.5 → HIGH; each signal needs ≥7 baseline samples or abstains), `MONOTONY_HIGH` (Foster monotony > 2.0 in the most recent week with ≥4 elapsed days → MODERATE). Called in `_build_context()` and `run_report()`.

**Report** (`report.py`) — builds and sends an HTML email via Gmail SMTP. Calls Claude for advice text; falls back to rule-based if no API key. Includes planned workout from `plan.py`. `generate_weekly_briefing()` produces a Monday coach briefing (form summary, key session, execution cue) via Claude Haiku, cached in `text_cache` keyed by `weekly_briefing_v2_{monday_iso}` (v2 added an FTP-retest-due note to the prompt). HIGH fatigue alerts are prepended as a callout block before the readiness section, followed by an HRV amber/red modulation callout (rule-based, from `modulation.py`).

**Training plan** (`plan.py`) — single source of truth for the 12-week charity-ride prep plan (`PLAN_START = 2026-05-18`, `TRAINING_WEEKS`). `session_for_date()` returns `(type, label, duration_min)` for any date in the plan window. Also `session_for_date_extended()` which covers the Tenerife camp and event prep block. Consumed by `report.py` (email) and `server.py` (calendar tab). Also contains `MAXI_INTERVALS` — a dict keyed by week number (1–12) with interval specs (`sets`, `work_s`, `rest_s`, `kb`, `easy`, `norwegian` flags) used to populate clickable interval modals on MaxiClimber calendar tiles. Flag semantics: `easy: True` → Z1-2 (deload weeks 4 and 8), `norwegian: True` → Z4-5 (week 9+ Norwegian 4×4 protocol), neither → Z3-4. The `_enrich_kb_spec(spec)` helper DRYs up video URL enrichment on KB exercise lists (used when building compound sub-session modal data). Note: weeks 9–10 Friday are **Z2 Endurance** (de-stacked from the old "Tempo Intervals" to avoid three consecutive intensity days); the label reuses week 11's Z2 Endurance builder, so re-running `--workouts` re-syncs them without a new template.

**Haute Route plan** (`hr_plan.py`) — separate 46-week plan for Haute Route Alpes 2027 (`HR_PLAN_START = 2026-10-05`, event Aug 23–29 2027). Five phases: Base (wks 1–13), Build (14–25), Specific Build (26–35, mountain camp wk 31), Peak (36–43, two 3-day simulation blocks), Taper (44–46). `hr_session_for_date()` and `build_hr_calendar_weeks()` mirror the API of `plan.py`. `HR_EVENT_STAGES` holds the 7 stage details (km, elevation, key climb). `HR_HEAT_PROTOCOL` is a static dict rendered as a banner on the Taper phase header (5×60 min Z2 heat sessions, final 10 days) — deliberately NOT merged into `HR_TRAINING_WEEKS` because those tuples feed `_hr_ctl_projection`. Rendered at `/haute-route`, which also shows per-stage AI pacing & fuelling plans (`generate_hr_stage_plans()` in `analysis.py`, claude-sonnet-4-6, one batched call, cached per stage in `text_cache` as `hr_stage_plan_v1_{day}`) as expandable `<details>` cards below the event grid.

**Weekly maintenance strength (weeks 14–43).** The Thursday slot in every non-deload Build/Specific/Peak week carries the label **"Strength + Core"** (one heavy/low-volume strength dose for the 50+ athlete). The session **type is deliberately kept as `recovery`, not `gym`**: `_hr_ctl_projection` rates are type-dependent (`recovery` 0.25 vs `gym` 0.55 per min in `_HR_CTL_PER_MIN`), so changing the type would have inflated projected CTL. Only the label string differs from the old "Recovery + Core". Deload weeks (incl. 32/41 which still read "Recovery + Core"), the camp week (31), and the taper (44–46) are untouched. `hr_calendar.html` carries a matching `"Strength + Core"` entry in its `SESSION_INFO` modal map.

**Post-training analysis** (`analysis.py`) — separate SQLite table `activity_analyses` in the same DB. `refresh_analyses()` fetches HR zone data + `summaryDTO` from Garmin for each unanalysed activity, calls Claude Sonnet with a discipline-specific coach prompt, saves result. After saving, if the session label is in `_FTP_SESSION_LABELS` and `detail["ftp_effort_avg_hr"]` is present, auto-populates `ftp_tests` table via `save_ftp_test()`. `load_analyses_for_activities()` enriches activity dicts for the Analysis tab. `_find_compound_companion()` detects when an activity is one half of a compound plan session and returns the paired activity so the prompt can reference both. `_build_analysis_prompt()` injects a "do not flag as short" note when actual duration meets or exceeds the plan (≥95%), preventing Claude from misreading a completed session as cut short. Also contains:
- `prefetch_workout_descriptions()` — generates 2-sentence coaching notes per session label, cached in `workout_descriptions` table
- `prefetch_nutrition_targets()` — generates daily macro targets per session type+duration, cached in `nutrition_targets` table
- `prefetch_fuelling_plans()` — generates in-ride carb/fluid/sodium plans for endurance sessions ≥75 min, cached in `fuelling_plans` table keyed by `fuelling_session_key(stype, dur_min)` (= `f"{stype}_{dur_min}"`, the shared helper — use it for any lookup against this cache)
- `generate_recovery_suggestion()` — coach advice on missed sessions, cached in `text_cache`
- `generate_hr_stage_plans()` — per-stage Haute Route pacing/fuelling plans (claude-sonnet-4-6), cached in `text_cache`
- `generate_charity_day_plans()` — per-day pacing/fuelling plans for the two `CHARITY_DAYS` (Ghent→Amsterdam), claude-sonnet-4-6, one batched call, cached in `text_cache` as `charity_day_plan_v1_{day}`; rendered as `<details>` cards in the `/calendar` event-prep section. Mirrors `generate_hr_stage_plans()` (same negative-cache-on-failure pattern); the prompt adds 2-day carb load, 80–90 g/h, sodium, and the "Day 1 exceeds the ~5 h longest training ride by 30–40%" framing.
- `_extract_durability(api, activity)` — late-ride HR drift from lap splits (duration-weighted avg HR, final vs first third of ride; needs ≥3 HR-bearing laps). Hooked into `refresh_analyses()` for cycling activities ≥90 min, saved to `activity_durability` independently of the AI analysis.
To regenerate a stale analysis: `DELETE FROM activity_analyses WHERE activity_id = <id>` then hit `/analysis-refresh`.

**Compound sessions** (`plan.py` → `COMPOUND_SESSIONS`) — dict mapping plan label → list of sub-sessions with `garmin_key`. Example: `"KB + MaxiClimber"` maps to `strength_training` + `stair_climbing`. This is the single source of truth consumed by three places: calendar completion (tracks each sub-session independently), `_merge_compound_activities()` in `server.py` (collapses paired activities into one analysis card with side-by-side HR zones), and `_find_compound_companion()` in `analysis.py` (adds companion context to the coach prompt). Add new compound session types here first.

On the calendar, compound session days render as **two independently clickable sub-tiles** instead of a single merged card. `build_calendar_weeks()` in `plan.py` attaches per-sub modal data to each sub-session dict: the MaxiClimber sub gets `maxi_intervals` (with `kb: False` so its modal shows intervals only), the KB sub gets `kb_spec` (via `_enrich_kb_spec()`), and the Ruck sub gets `ruck_spec`. The calendar template reads `data-maxi-intervals`, `data-kb-spec`, and `data-ruck-spec` attributes from each sub-tile; the existing `openModal()` JS branches on whichever attribute is present. Completion badges appear per sub-tile, not on the outer day header.

**Interference flagging** (`server.py`) — `QUALITY_BIKE_LABELS` module-level set lists sessions that warrant an interference check (tempo, sweetspot, threshold, hill repeats, FTP tests). In `calendar_view()`, for each such day, the previous 24 h is scanned for `type_key in {"strength_training", "stair_climbing"}`; if found, `day["interference"] = True` and `day["interference_note"]` is set. The calendar template renders an amber ⚠️ badge inline with the session label.

**Body composition** (`body.py`) — `fetch_body_composition()` and `fetch_blood_pressure()` pull data from Garmin Connect. `bp_classification()` returns a label and colour for blood pressure readings. Data saved to `body_metrics` and `blood_pressure` SQLite tables.

**Withings sync** (`withings.py`) — `sync_withings_to_garmin()` fetches recent Withings measurements (weight, body fat, blood pressure), pushes them to Garmin Connect via `add_body_composition()` / `set_blood_pressure()`, and also writes directly to SQLite for immediate availability. Requires `withings-sync` package and an interactive OAuth step on first run.

**Mersea routes** (`mersea_routes.py`) — coastal route data for the Mersea Island build (rucking progression in plan weeks 9–10). `MERSEA_TARGET_DATE` drives a countdown displayed on the Calendar tab.

**Garmin workouts** (`workouts.py`) — builds `garminconnect.workout.CyclingWorkout` objects for all 27 distinct session types in the plan, uploads templates once, then schedules each on its plan dates via `upload_cycling_workout` + `schedule_workout`.

## AI Coach chat

`_COACH_SYSTEM` in `server.py` defines the coach persona and context injection format. On each request `_build_coach_context()` assembles: PMC metrics, today's readiness, all remaining plan sessions (12-week + Tenerife camp + event prep), recent activities, body composition, active plan overrides, coach memory, RAG-retrieved past session analyses, recent RPE logs (last 7 days from `session_rpe` table), fuelling compliance logs (last 5 from `fuelling_logs`, under "Fuelling Compliance"), back-to-back training history (5 most recent pairs from `btb_notes`), and calorie/macro intake (14-day averages for carbs and protein plus today's full breakdown — calories logged, TDEE, calorie balance, carbs, protein — under the "Calorie & Macro Intake (Garmin food log)" section).

The coach can call the `propose_plan_change` tool to suggest a duration/type modification. The server handles the tool-use turn, enriches the proposal with current plan data, and returns it as a JSON `proposal` alongside the text reply. The frontend renders it as a confirmation card; on approval `POST /apply-plan-change` persists it as a `plan_override`.

Coach memory (`coach_memory` SQLite table) is a compact durable memo (150–250 words) refreshed in a background thread when the conversation reaches `_MEMO_MIN_MESSAGES` or after `_MEMO_STALE_HOURS`. It captures goals, athlete tendencies, and decisions made across sessions. The in-context history window is the last 20 messages; the memo carries longer-term context beyond that.

The streaming endpoint (`/coach-chat-stream`) uses `StreamingResponse` with a sync SSE generator. The non-streaming `/coach-chat` endpoint exists for fallback.

## CTL/ATL/TSB projection

`_ctl_projection()` in `server.py` projects CTL, ATL, and TSB from today to the event date using plan sessions across all blocks (12-week, Tenerife camp, event prep). CTL uses additive per-minute deltas calibrated against week-1 observed data with a soft ceiling above CTL 300. ATL uses a 7-day exponential decay: `atl = max(0, atl * exp(−1/7) + rate * dur_min)` on session days, `atl = max(0, atl * exp(−1/7))` on rest days. Each projected entry returns `{label, ctl, atl, tsb}`. It takes an optional `modifier(date, session_tuple) -> tuple|None` callable applied per day (None = rest) — used by `_taper_scenarios()`, which computes three preset what-ifs over the final 14 days (as planned / drop one quality session / halve final-week volume) rendered as a table under the TSB chart plus a blue-dashed scenario-3 overlay. The Performance tab renders projected TSB as a dashed amber overlay on the TSB chart with an event vertical line. Note: ATL/CTL are in Garmin training-load units, not standard TSS, so absolute TSB values differ from classic PMC conventions. `_hr_ctl_projection()` does the same across the 46-week Haute Route plan (CTL only).

## Dashboard prescription cards

`_build_context()` adds three prescription surfaces to `dashboard.html`, all applied through the existing `/apply-plan-change` → `plan_overrides` flow (the shared `applyModulation(btn)` JS reads `data-date/dur/type/label/reason` attributes):
- **HRV traffic light** (`traffic_light` + `modulation` context keys) — amber/red card with an Apply button; green renders a small "session as planned" pill.
- **FTP retest** (`ftp_retest`) — fires when the last `ftp_tests` row is >42 days old (or table empty 3+ weeks into the plan, via `ftp_retest_due()`); scans the next 10 days for a tempo/ftp/bike slot and schedules an "FTP Re-test" override. Because "FTP Re-test" is in `_FTP_SESSION_LABELS`, the completed ride auto-populates `ftp_tests` and the card self-dismisses.
- **Fatigue alerts** (existing list, now including ILLNESS_RISK and MONOTONY_HIGH).

The Performance tab additionally renders: durability drift chart (`activity_durability`), estimated FTP/W-kg dual-axis chart (`estimated_wkg_history()`), Foster monotony/strain chart (`weekly_monotony_strain()`), heat/altitude acclimation tile (`acclimation_latest()`), and the taper scenario table. `/body` shows an "Est. W/kg" tile.

## Configuration

Copy `.env.example` to `.env`. Env vars are also loaded from `~/.garmin_readiness/.env` (used by launchd since it runs without shell environment).

Key vars: `GARMIN_EMAIL`, `GARMIN_PASSWORD`, `ANTHROPIC_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `REPORT_TO`, `DASHBOARD_USER`, `DASHBOARD_PASSWORD`.

## Notes

- The composite readiness score is the mean z-score across all `SCORED_FIELDS` (excludes `training_load_chronic` and `vo2_max` which are context-only, and calorie/step/sleep-stage fields). Z-scores for lower-is-better fields (stress, ACWR, acute load) are sign-flipped so positive always means better.
- `available_count()` checks how many non-null numeric fields exist — used to detect empty fetches. The email gate checks specifically for `sleep_score` and `body_battery_morning` (only populated after the watch syncs overnight data); if either is missing, the CLI exits with code 2 and the launchd retry loop tries again in 30 minutes.
- All Garmin API calls are individually try/except'd; a failed endpoint logs at DEBUG and leaves the field `None` rather than crashing.
- Templates are package data — any change to a `.html` file requires `pip install --force-reinstall .` before the running server picks it up.
- Claude model usage: **Sonnet** for coach chat, post-workout activity analysis, and Haute Route stage plans; **Haiku** for email advice, recovery suggestions, workout descriptions, nutrition targets, fuelling plans, weekly briefings, and coach memory summaries.

## AI text caching

There are several cache layers; know which to clear when regenerating AI output:

| Cache | Location | What it holds | How to clear |
|-------|----------|---------------|--------------|
| `_advice_cache` | `server.py` in-process dict | Daily readiness advice | Restart server |
| `daily_advice` | SQLite table | Per-date advice (survives restart) | `DELETE FROM daily_advice WHERE date = '...'` |
| `text_cache` | SQLite table | Workout descriptions, metric explainers, recovery suggestions, fuelling plans, weekly briefings (key: `weekly_briefing_v2_{monday_iso}`), HR stage plans (key: `hr_stage_plan_v1_{day}`), charity event-day plans (key: `charity_day_plan_v1_{day}`) | `DELETE FROM text_cache WHERE key = '...'` (stage plans: `WHERE key LIKE 'hr_stage_plan_v1_%'`; charity plans: `WHERE key LIKE 'charity_day_plan_v1_%'`) |
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

## New SQLite tables (added in 10-feature coaching release)

| Table | Key columns | Purpose |
|-------|-------------|---------|
| `activity_durability` | `activity_id PK, date, first_third_hr, final_third_hr, drift_pct, n_laps` | Late-ride HR drift per ≥90-min ride; computed from lap splits in `refresh_analyses()` |
| `fuelling_logs` | `date, activity_id UNIQUE, planned_carbs_g_per_hr, actual_carbs_g_per_hr, fluid_ok, note` | User-logged in-ride fuelling compliance from the Analysis tab |

All use `_ensure_*_schema()` lazy-init (CREATE TABLE IF NOT EXISTS) called inside each read/write function — no migration needed on first access.
