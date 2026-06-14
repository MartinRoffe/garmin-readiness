# Garmin Readiness Dashboard

Personal training readiness dashboard powered by Garmin Connect data and Claude AI.
Fetches daily metrics (HRV, sleep, stress, recovery), scores them against a 30-day
rolling baseline, and delivers a daily email briefing and live web dashboard.

**📖 [User Guide](docs/README.md)** — full walkthrough of every tab and feature.

## Features

**Readiness & alerts**
- **Daily readiness score** — composite z-score across 10+ Garmin wellness metrics
- **Fatigue alert system** — proactive HIGH/MODERATE banners for HRV decline (4 days), deep TSB, or volume spike; prepended to the daily email
- **Weekly coach briefing** — Monday-morning Claude Haiku briefing (form summary, key session, execution cue), cached per ISO week

**AI**
- **AI Coach chat** — conversational coach with full training context, plan-change proposals, and cross-session memory
- **AI coaching advice** via Claude in the daily email (falls back to rule-based if no API key)
- **Post-workout HR zone analysis** with Claude commentary per activity
- **Session RPE logging** — emoji-based perceived effort (😴😊😤🔥💀) on each analysis card, stored in SQLite, surfaced in coach context

**Performance & load**
- **FTP trend chart** — estimated LTHR tracked over test history, auto-populated from activity analyses
- **TSB trajectory to event** — projected TSB as a dashed overlay on the TSB chart with an event vertical line
- **Zone 2 cardiac drift trend** — easy-ride-only scatter with server-side least-squares regression and bpm annotation
- **Training polarisation charts** — stacked bar (Z1–Z5 per week) + donut (block totals) on the Performance tab

**Calendar & compliance**
- **12-week cycling training plan** with structured workout uploads to Garmin Connect
- **Split compound session tiles** — KB+MaxiClimber and Ruck+KB days render as two independent clickable cards; each opens its own modal (KB exercise list or MaxiClimber interval structure)
- **Interference load flag** — amber ⚠️ badge on quality bike sessions when strength was logged within 24 h
- **Back-to-back session tracker** — consecutive cycling pairs table with a fatigue log modal (rating + note)
- **Plan compliance view** — per-week adherence tracking with discipline breakdown

**Nutrition tracking**
- **Garmin food log integration** — calories, carbs, protein, and fat pulled daily from Garmin Connect food diary
- **Readiness tab nutrition card** — logged kcal, TDEE, calorie balance (colour-coded deficit/surplus), carbs, and protein displayed alongside readiness metrics
- **Body tab macro tiles** — today's carbs/protein plus 14-day rolling averages
- **Nutrition tab summary** — carbs and protein logged today shown in the summary banner
- **Coach macro context** — AI coach receives daily and 14-day-average macro breakdown for fuelling and recovery advice

**Other**
- **Body composition tracking** — weight, fat %, muscle mass, blood pressure (Garmin + Withings)
- **Withings sync** — push Withings body measurements to Garmin Connect
- **Daily email** with readiness score, planned workout, recent activity summary, and any fatigue alerts
- **Haute Route Alpes 2027 plan** — 46-week plan with phase calendar and CTL projection
- **Tenerife cycling camp** itinerary and Ghent–Amsterdam charity ride on the calendar

## Prerequisites

- Python 3.11+
- Garmin Connect account
- Anthropic API key (optional — falls back to rule-based advice without it)
- Gmail account with an [App Password](https://support.google.com/accounts/answer/185833) (for email delivery)

## Setup

```bash
git clone <repo-url>
cd garmin-readiness
cp .env.example .env        # fill in credentials (see Configuration below)
pip install .
garmin-readiness --backfill 30   # build 30-day baseline on first run
```

## Configuration

Copy `.env.example` to `.env` and populate:

| Variable | Required | Description |
|---|---|---|
| `GARMIN_EMAIL` | ✓ | Garmin Connect login email |
| `GARMIN_PASSWORD` | ✓ | Garmin Connect password |
| `ANTHROPIC_API_KEY` | — | Claude API key for AI advice, coach chat, and workout analysis |
| `GMAIL_ADDRESS` | — | Sender address for daily email |
| `GMAIL_APP_PASSWORD` | — | Gmail App Password |
| `REPORT_TO` | — | Recipient email (defaults to `GMAIL_ADDRESS`) |
| `DASHBOARD_USER` | — | Basic auth username (dashboard is open if unset) |
| `DASHBOARD_PASSWORD` | — | Basic auth password |

On macOS with launchd, also copy `.env` to `~/.garmin_readiness/.env` (launchd runs without a shell environment).

## Usage

```bash
# Terminal readiness report
garmin-readiness

# Web dashboard at http://127.0.0.1:8743
garmin-readiness --serve

# Send daily email (add --dry-run to preview without sending)
garmin-readiness --email [--dry-run]

# Backfill historical data to build the 30-day baseline
garmin-readiness --backfill 30

# Upload structured cycling workouts to Garmin Connect
garmin-readiness --workouts [--dry-run]

# Install launchd agents (macOS): 7 am email + always-on web server
garmin-readiness --setup-schedule
```

## Architecture

```
garmin_readiness/
├── cli.py           Entry point; argument dispatch
├── client.py        Garmin Connect session/token handling
├── metrics.py       Garmin API calls → DailyMetrics dataclass
├── history.py       SQLite persistence, z-scores, composite score
│                    Tables: daily_metrics, activities, body_metrics,
│                    blood_pressure, daily_advice, text_cache,
│                    coach_conversations, plan_overrides, coach_memory,
│                    session_rpe, ftp_tests, btb_notes
├── display.py       Value formatting, activity enrichment
├── alerts.py        Fatigue alert checks (HRV trend, TSB deep, volume spike)
├── report.py        HTML email builder, Claude advice, weekly briefing, Gmail sender
├── analysis.py      Post-workout HR zone analysis via Claude; FTP test
│                    auto-population; workout descriptions, nutrition
│                    targets, fuelling plans
├── plan.py          12-week training plan + COMPOUND_SESSIONS registry
├── hr_plan.py       46-week Haute Route Alpes 2027 plan + calendar
├── mersea_routes.py Mersea Island coastal route data
├── body.py          Body composition and blood pressure helpers
├── withings.py      Withings → Garmin Connect measurement sync
├── workouts.py      Structured workout upload to Garmin Connect
├── server.py        FastAPI web dashboard (11 tabs, Jinja2 templates)
└── templates/       HTML templates for each dashboard tab
```

Data is stored in `~/.garmin_readiness/history.db` (SQLite, auto-migrating schema).

## How the readiness score works

Each metric (HRV, sleep duration, sleep score, stress, resting HR, body battery, SpO₂, respiration, ACWR, acute training load) is z-scored against a 30-day rolling window. Lower-is-better fields (stress, ACWR, acute load) are sign-flipped so **positive always means above-average readiness**. The composite score is the mean across all scored fields that have enough baseline data.

## AI Coach

The coach chat tab streams responses from Claude Sonnet with full training context: PMC (CTL/ATL/TSB), today's readiness, all remaining plan sessions, recent activities, body composition, active plan overrides, recent RPE logs, and back-to-back fatigue history. The coach can propose session changes (duration, type swap) that appear as confirmation cards before being applied. Cross-session memory is maintained in SQLite and refreshed in the background after conversations.

Post-workout analysis also uses Claude Sonnet. Recovery suggestions, workout descriptions, nutrition targets, fuelling plans, and weekly briefings use Claude Haiku.

## AI text caching

| Cache | Location | What it holds | How to clear |
|-------|----------|---------------|--------------|
| `_advice_cache` | `server.py` in-process dict | Daily readiness advice | Restart server |
| `daily_advice` | SQLite table | Per-date advice (survives restart) | `DELETE FROM daily_advice WHERE date = '...'` |
| `text_cache` | SQLite table | Workout descriptions, metric explainers, recovery suggestions, weekly briefings, fuelling plans | `DELETE FROM text_cache WHERE key = '...'` |
| `activity_analyses` | SQLite table | Per-activity coach analysis | `DELETE FROM activity_analyses WHERE activity_id IN (...)` then hit `/analysis-refresh` |
| `workout_descriptions` | SQLite table | 2-sentence coaching notes per session label | `DELETE FROM workout_descriptions WHERE label = '...'` |
| `nutrition_targets` | SQLite table | Daily macro targets per session type+duration | `DELETE FROM nutrition_targets WHERE session_key = '...'` |
| `fuelling_plans` | SQLite table | In-ride carb/fluid/sodium plans | `DELETE FROM fuelling_plans WHERE session_key = '...'` |

## macOS background service

```bash
# Install: runs daily 7 am email + persistent web server
garmin-readiness --setup-schedule

# Restart the server after code changes
launchctl kickstart -k "gui/$(id -u)/com.garmin-readiness.server"
```
