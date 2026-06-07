# Garmin Readiness Dashboard

Personal training readiness dashboard powered by Garmin Connect data and Claude AI.
Fetches daily metrics (HRV, sleep, stress, recovery), scores them against a 30-day
rolling baseline, and delivers a daily email briefing and live web dashboard.

## Features

- **Daily readiness score** — composite z-score across 10+ Garmin wellness metrics
- **AI coaching advice** via Claude (falls back to rule-based if no API key)
- **AI Coach chat** — conversational coach with full training context, plan-change proposals, and cross-session memory
- **Web dashboard** with tabs: Readiness, Performance, Analysis, Calendar, Training Plan, Compliance, Nutrition, Sleep, Body, Haute Route, Tenerife
- **12-week cycling training plan** with structured workout uploads to Garmin Connect
- **Post-workout HR zone analysis** with Claude commentary per activity
- **Body composition tracking** — weight, fat %, muscle mass, blood pressure (Garmin + Withings)
- **Withings sync** — push Withings body measurements to Garmin Connect
- **Daily email** with readiness score, planned workout, and recent activity summary
- **Haute Route Alpes 2027 plan** — 46-week plan with phase calendar and CTL projection
- **Tenerife cycling camp** itinerary and Ghent–Amsterdam charity ride on the calendar
- **Plan compliance view** — per-week adherence tracking with discipline breakdown

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
├── display.py       Value formatting, activity enrichment
├── report.py        HTML email builder, Claude advice, Gmail sender
├── analysis.py      Post-workout HR zone analysis via Claude; workout
│                    descriptions, nutrition targets, fuelling plans
├── plan.py          12-week training plan data + calendar builders
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

The coach chat tab (`/coach`) streams responses from Claude Sonnet with your full training context injected: PMC (CTL/ATL/TSB), today's readiness metrics, all remaining plan sessions, recent activities, body composition, and active plan overrides. The coach can propose session changes (duration, type swap) that appear as confirmation cards before being applied. Cross-session memory is maintained in SQLite and refreshed in the background after conversations.

Post-workout analysis (Analysis tab) also uses Claude Sonnet, with structured HR zone data and plan context. Recovery suggestions, workout descriptions, and nutrition targets use Claude Haiku.

## AI text caching

There are four separate cache layers:

| Cache | Location | What it holds | How to clear |
|-------|----------|---------------|--------------|
| `_advice_cache` | `server.py` in-process dict | Daily readiness advice | Restart server |
| `daily_advice` | SQLite table | Per-date advice (survives restart) | `DELETE FROM daily_advice WHERE date = '...'` |
| `text_cache` | SQLite table | Workout descriptions, metric explainers, recovery suggestions | `DELETE FROM text_cache WHERE key = '...'` |
| `activity_analyses` | SQLite table | Per-activity coach analysis | `DELETE FROM activity_analyses WHERE activity_id IN (...)` then hit `/analysis-refresh` |

## macOS background service

```bash
# Install: runs daily 7 am email + persistent web server
garmin-readiness --setup-schedule

# Restart the server after code changes
launchctl kickstart -k "gui/$(id -u)/com.garmin-readiness.server"
```
