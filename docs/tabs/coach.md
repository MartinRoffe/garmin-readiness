# Coach

**Nav:** the **Coach** item.

## What it's for

A conversational AI coach that already knows your training context — your form,
your plan, your recent sessions, how you've been fuelling and recovering — so you
can ask real questions and get answers grounded in your actual data, and even
adjust your plan from the chat.

> 📸 *Screenshot: the Coach chat with a reply streaming in and a plan-change proposal card.*

## What you'll see

A chat window. You type a question; the coach streams back a reply. Before each
answer it quietly assembles a rich picture of where you are:

- Your current training load (CTL / ATL / TSB) and today's readiness.
- Every remaining session across all your plans (12-week build, Tenerife camp,
  event prep).
- Recent activities and their AI analyses, body composition, and any active plan
  overrides.
- Your recent RPE logs, in-ride fuelling compliance, back-to-back fatigue notes,
  and your calorie/macro intake (today's full breakdown plus 14-day averages).

So you can ask things like "I felt flat on yesterday's intervals — should I
change Thursday?" and it answers with your numbers in mind.

## How to use it

- **Just talk to it.** Ask about pacing, fuelling, whether to push or back off,
  how your week is shaping up — anything a coach with your data could answer.
- **Accept or decline plan changes.** When the coach suggests a concrete change
  (a different duration or a session-type swap), it appears as a **confirmation
  card**, not an automatic edit. Approve it and the change is saved as a one-day
  override that flows through to your [Calendar](plan.md) and the daily email;
  ignore it and nothing changes.

## Good to know

- **It remembers across sessions.** A compact memory of your goals, tendencies,
  and past decisions is kept and refreshed in the background, so the coach has
  continuity beyond the last few messages on screen.
- **It knows it's working from heart rate, not power.** The coach is told the
  same [HR-not-power caveat](../concepts.md#why-heart-rate-not-power) you should
  keep in mind, so it treats W/kg and zone numbers as estimates.
- **The coach needs your Anthropic API key.** Without it, this tab is inert.
- **It advises; it doesn't act on its own.** Every plan change passes through your
  explicit approval first.
