# Getting Started

This page takes you from a fresh clone to a running dashboard with your own data.

## What it does

The app reads your daily wellness and training data from **Garmin Connect**,
scores how recovered you are against your own 30-day baseline, and presents it as
a live web dashboard plus an optional daily email. If you add an Anthropic
(Claude) API key, it also writes AI coaching advice, analyses each workout, and
powers a conversational coach.

## Prerequisites

- **Python 3.11 or newer.**
- A **Garmin Connect account** with a watch that syncs overnight HRV, sleep, and
  body-battery data (the readiness score depends on these).
- *(Optional)* An **Anthropic API key** for the AI features. Without it, the app
  falls back to simple rule-based advice and the coach/analysis tabs are inert.
- *(Optional)* A **Gmail account with an [App Password](https://support.google.com/accounts/answer/185833)**
  if you want the daily email.

## Install

```bash
git clone <repo-url>
cd garmin-readiness
cp .env.example .env          # then edit .env — see Configuration below
pip install .
```

> **Note:** install non-editable (`pip install .`, not `-e`). Templates are
> packaged with the code, so after any change you re-run
> `pip install --force-reinstall .` to pick it up.

## Configuration

Open `.env` and fill in what you need. Only the first two are required:

| Variable | Required | What it's for |
|---|:---:|---|
| `GARMIN_EMAIL` | ✓ | Your Garmin Connect login email |
| `GARMIN_PASSWORD` | ✓ | Your Garmin Connect password |
| `ANTHROPIC_API_KEY` | — | Enables AI advice, the coach chat, and workout analysis |
| `GMAIL_ADDRESS` | — | The address the daily email is sent **from** |
| `GMAIL_APP_PASSWORD` | — | A Gmail App Password (not your normal password) |
| `REPORT_TO` | — | Who the email goes **to** (defaults to `GMAIL_ADDRESS`) |
| `DASHBOARD_USER` | — | Username to lock the dashboard behind (see below) |
| `DASHBOARD_PASSWORD` | — | Password for the same |

**Protecting the dashboard.** If you leave `DASHBOARD_USER` /
`DASHBOARD_PASSWORD` blank, the web dashboard is **open to anyone who can reach
it**. Set both before exposing it beyond your own machine — the app then requires
that username and password (HTTP Basic auth) to view any page.

## First run — build your baseline

The readiness score compares today against the previous 30 days, so on day one
there's nothing to compare against. Backfill history first:

```bash
garmin-readiness --backfill 30      # pulls the last 30 days from Garmin
```

This populates the local database so your very first readiness score is
meaningful. You only need to do it once.

> 📸 *Screenshot: the terminal output of `--backfill 30` completing.*

## Run it

```bash
# One-off readiness report in the terminal
garmin-readiness

# Live web dashboard
garmin-readiness --serve         # then open http://127.0.0.1:8743
```

Visit **http://127.0.0.1:8743** and you'll land on the [Readiness](tabs/readiness.md)
page. From there everything is point-and-click.

## Where your data lives

Everything is stored locally in a single SQLite database and a few support files
under your home directory:

```
~/.garmin_readiness/
├── history.db        all your metrics, activities, plans, logs, and AI caches
└── .env              (optional) a copy used by scheduled background runs
```

Nothing is sent anywhere except Garmin (to read your data), Gmail (if you enable
email), and Anthropic (if you enable the AI features). The database
auto-migrates, so upgrades won't lose your history.

## Next steps

- Understand what the numbers mean → **[Key Concepts](concepts.md)**
- Tour the home page → **[Readiness](tabs/readiness.md)**
- Set up the morning email and background server → **[Email & Automation](email-and-automation.md)**
