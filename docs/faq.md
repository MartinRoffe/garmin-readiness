# FAQ & Troubleshooting

Short answers to the things most likely to trip you up.

## My readiness tiles (HRV, sleep, body battery) are empty

Those metrics only exist **after your watch syncs the overnight data**. First
thing in the morning, before a sync, they'll be blank. Open Garmin Connect on
your phone to force a sync, then hit **Refresh** on the
[Readiness](tabs/readiness.md) page.

This is also why the scheduled email waits and retries rather than sending an
empty briefing — see [Email & Automation](email-and-automation.md#why-the-email-sometimes-waits).

## My readiness score looks meaningless / is missing

The score compares today to your previous 30 days. On a fresh install there's no
history to compare against. Run the one-time backfill:

```bash
garmin-readiness --backfill 30
```

See [Getting Started](getting-started.md#first-run--build-your-baseline).

## The AI features (coach, analysis, advice) do nothing

They require an **Anthropic API key** in your `.env` (`ANTHROPIC_API_KEY`).
Without it:

- The daily email falls back to rule-based advice.
- The **Coach** and **Analysis** tabs stay empty.
- The Haute Route stage plans and event-day plans don't generate.

Add the key and restart the server.

## The AI text is stale / I want to regenerate it

AI output is cached so it doesn't regenerate (and re-bill) on every page load.
The quickest fix for daily advice is the **Refresh** action on the
[Readiness](tabs/readiness.md) page, which evicts that day's cache.

For deeper regeneration, the caches live in the local database
(`~/.garmin_readiness/history.db`) — workout analyses, workout descriptions,
nutrition targets, fuelling plans, weekly briefings, and stage plans each have
their own table you can clear. The project `CLAUDE.md` lists the exact tables and
clear commands.

## Garmin sign-in fails or asks repeatedly

The app stores a Garmin session token under `~/.garmin_readiness/` and reuses it.
If sign-in starts failing (e.g. after a password change or a long gap):

1. Confirm `GARMIN_EMAIL` / `GARMIN_PASSWORD` in `.env` are current.
2. Remove the stored token files in `~/.garmin_readiness/` so the app does a
   fresh login.
3. If you use the scheduled setup, remember the background runs read from
   `~/.garmin_readiness/.env`, not your shell — update that copy too.

Garmin occasionally requires a fresh interactive login; individual failed API
calls are handled gracefully (the affected metric just shows blank rather than
crashing the app).

## How do I lock down the dashboard?

Set both `DASHBOARD_USER` and `DASHBOARD_PASSWORD` in `.env`. The dashboard then
requires that username and password (HTTP Basic auth) on every page. If you leave
them blank, **anyone who can reach the address can view your data** — only safe on
a machine that isn't exposed to a network you don't trust.

## I changed a template / some code but the dashboard didn't update

Templates are packaged with the code, so changes need a reinstall:

```bash
pip install --force-reinstall .
```

If you're running the macOS background server, also restart it:

```bash
launchctl kickstart -k "gui/$(id -u)/com.garmin-readiness.server"
```

## Withings sync asks me to sign in

The first Withings sync needs a one-time interactive OAuth sign-in to authorise
access. After that it runs unattended. See [Body](tabs/health.md#body-tab).

## Where is my data, and is any of it uploaded?

Everything lives locally in `~/.garmin_readiness/history.db`. The app only talks
to **Garmin** (to read your data), **Gmail** (if you enabled email), and
**Anthropic** (if you enabled the AI features). Nothing else leaves your machine.
