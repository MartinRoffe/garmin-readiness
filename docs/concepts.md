# Key Concepts

A handful of ideas underpin the whole dashboard. Read this once and the rest of
the guide — and the app itself — will make a lot more sense. Each tab page links
back here rather than re-explaining the jargon.

## The readiness score

The number at the top of the home page is your **composite readiness score** for
the day. It answers one question: *relative to your own recent normal, how
recovered and ready to train are you today?*

How it's built:

1. The app collects a set of wellness metrics from Garmin — HRV, sleep duration,
   sleep score, stress, resting heart rate, body battery, blood-oxygen (SpO₂),
   respiration, and two training-load ratios (ACWR and acute load).
2. Each metric is turned into a **z-score** — how many standard deviations
   above or below your personal 30-day average it sits. A z-score of 0 is
   exactly average for you; +1 is notably better than usual; −1 is notably worse.
3. For metrics where **lower is better** (stress, training-load ratios), the sign
   is flipped. This means the rule is always the same: **positive is good,
   negative is bad.**
4. The composite score is the **average of all these z-scores** that have enough
   baseline data behind them.

Because it's relative to *you*, there's no universal "good" number — a +0.5 day
is a green light, a −1.0 day says ease off. A couple of context-only metrics
(chronic training load, VO₂max) and the calorie/step/sleep-stage figures are
shown but deliberately **excluded** from the score so they don't distort it.

> **Why z-scores?** They put very different metrics (milliseconds of HRV, hours
> of sleep, a stress index) on one comparable scale, so a single honest average
> can summarise them.

## Training load: CTL, ATL, and TSB (the PMC)

The **Performance** tab tracks your training load over time using the classic
Performance Management Chart (PMC) trio:

- **CTL — Chronic Training Load (“fitness”).** A ~6-week rolling average of how
  much you've been training. It rises slowly with consistent work. Higher = fitter.
- **ATL — Acute Training Load (“fatigue”).** A ~1-week average. It spikes fast
  after hard days and decays quickly with rest.
- **TSB — Training Stress Balance (“form”).** Simply CTL − ATL. Positive means
  you're fresher than your baseline (tapered, rested); negative means you're
  carrying fatigue (in a hard training block).

The app also **projects** these forward to your target event using your planned
sessions, so you can see whether you'll arrive fit *and* fresh.

> **Important caveat:** this app measures load in **Garmin's training-load
> units**, not the TSS (Training Stress Score) units of classic PMC software. The
> shape of the curves and the direction of change are what matter — the absolute
> TSB numbers will look different from tools like TrainingPeaks, so don't compare
> them directly.

## The HRV traffic light

Each morning the app gives your planned session a **green / amber / red** rating
based mainly on last night's heart-rate variability (HRV) versus your 30-day
baseline (with a couple of backstops). It's a quick "should I train as planned?"
signal:

- 🟢 **Green** — HRV is normal or better. Train as planned.
- 🟠 **Amber** — HRV is moderately suppressed. The app proposes an **easier
  variant of the same session, keeping the duration** (e.g. swap intervals for
  steady Zone 2).
- 🔴 **Red** — HRV is well below baseline. The app proposes replacing the session
  with a short **Recovery Spin (30 min)**.
- ⚪ **Unknown** — not enough data yet (e.g. the watch hasn't synced).

On amber/red days the home page shows an **Apply** button. Clicking it records
the swap as a one-day override on your plan, which then flows through to the
calendar and the daily email. Green days just show a small "session as planned"
pill. This is covered hands-on in the [Readiness](tabs/readiness.md) page.

## Fatigue alerts

Separately from the daily score, the app watches for five longer-running fatigue
patterns and raises a banner when one trips:

| Alert | Fires when | Severity |
|---|---|---|
| **HRV trend** | HRV falls for 4 mornings in a row | HIGH |
| **Deep TSB** | Training stress balance stays very negative for ≥5 days | HIGH |
| **Volume spike** | Your actual weekly minutes exceed the plan by >20% | Moderate |
| **Illness risk** | Two of three (low HRV, high resting HR, poor sleep) line up | HIGH |
| **High monotony** | Training is too samey week-on-week (Foster monotony >2.0) | Moderate |

HIGH alerts are also pushed to the top of the daily email. See
[Readiness](tabs/readiness.md) and [Email & Automation](email-and-automation.md).

## Why heart rate, not power

This is a deliberate, important design choice that affects how you read several
charts. **The athlete this app is built for trains by heart rate, not with a
power meter.** That has consequences:

- **Heart-rate zones drift.** The same effort produces a different heart rate
  depending on heat, fatigue, sleep, altitude, and cardiac drift over a long
  ride. A "Zone 2" ride isn't as clean a measurement as power would be.
- **The W/kg figure is an estimate, not a measurement.** Where the app shows an
  estimated FTP or watts-per-kilo (on Performance and Body), it's derived from
  your VO₂max and weight as a **coarse proxy** — useful for spotting a trend, not
  for precise training targets.
- **Durability and cardiac-drift charts** exist precisely because HR training
  needs you to watch how your heart rate behaves late in long rides.

Keep this in mind anywhere you see watts, W/kg, or FTP in the app: treat them as
**trend indicators**, not gospel numbers.

---

With these in hand, head to the tab you're looking at:
**[Readiness](tabs/readiness.md)** ·
**[Performance](tabs/performance.md)** ·
**[Plan](tabs/plan.md)** ·
**[Health](tabs/health.md)** ·
**[Events](tabs/events.md)** ·
**[Coach](tabs/coach.md)**
