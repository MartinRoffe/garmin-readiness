# Readiness (Home)

**Nav:** the **Readiness** item — also the page you land on at `/`.

## What it's for

Your one-screen morning check-in. It answers "how recovered am I, and should I
train as planned today?" and surfaces anything that needs your attention before
you do.

> 📸 *Screenshot: the Readiness page with the score header, metric tiles, and the nutrition card.*

## What you'll see

**The readiness score.** A single composite number summarising how today compares
to your own 30-day normal, with a plain-language label (e.g. "Ready", "Caution").
Positive is good, negative means ease off — see
[how it's built](../concepts.md#the-readiness-score).

**Metric tiles.** A grid of the individual signals behind the score — HRV, sleep,
stress, resting heart rate, body battery, SpO₂, respiration, and training-load
ratios. Each shows today's value and how it compares to baseline. Hover the **ⓘ**
on any tile for a short explanation.

**Today's Nutrition card.** Pulled from your Garmin food log: calories logged,
your estimated daily burn (TDEE), and the **balance** between them, plus carbs
and protein. The balance is colour-coded — 🟢 green for a deficit, 🟠 amber for a
small surplus, 🔴 red for a large surplus — so a weight-loss block is easy to keep
honest at a glance.

**The HRV traffic light.** A green / amber / red card rating today's planned
session against last night's HRV. See [the concept](../concepts.md#the-hrv-traffic-light).

**Fatigue alerts.** When one of the five [fatigue patterns](../concepts.md#fatigue-alerts)
trips, a banner appears here (HIGH alerts in red, moderate in amber) explaining
what was detected.

**FTP re-test card.** If it's been more than six weeks since your last fitness
test, a card suggests scheduling an "FTP Re-test" into an upcoming suitable
session. Apply it and, once you complete that ride, your test history updates
automatically and the card disappears.

## How to use it

- **Apply a session change.** On an amber or red traffic-light day (or when the
  FTP-retest card appears), click **Apply**. This records a one-day override on
  your plan — the swap then shows up on the [Calendar](plan.md) and in the daily
  email. Green days just show a "session as planned" pill, nothing to do.
- **Refresh the data.** The **Refresh** action force-fetches fresh data straight
  from Garmin and clears the cached AI advice for the day — use it if you synced
  your watch after the dashboard already loaded.
- **Send the email now.** A manual **Send Email** trigger fires the same daily
  readiness email you'd otherwise get on a schedule (handy for testing).
- **Look back at any day.** Add `?date=YYYY-MM-DD` to the home URL
  (e.g. `http://127.0.0.1:8743/?date=2026-06-01`) to see that day's readiness
  exactly as it stood.

## Good to know

- **Tiles can be blank first thing.** HRV, sleep score, and body-battery figures
  only exist *after* your watch syncs the overnight data. If they're empty, sync
  the watch and hit **Refresh**. (The scheduled email deliberately waits for this
  data before sending — see [Email & Automation](../email-and-automation.md).)
- **The score is personal.** There's no universal "good" value; it's always
  relative to your last 30 days. A new account needs the
  [backfill step](../getting-started.md#first-run--build-your-baseline) before
  the score means much.
- **AI advice is cached** per day, so it won't change every time you reload —
  use **Refresh** to regenerate it.
