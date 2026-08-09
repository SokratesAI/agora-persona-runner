---
type: log
tags: [agora, evolution, self-improvement, agent-context]
status: capture
updated: 2026-08-09
maintenance: Nova rewrites all three sections every cycle; put is correct here. Stale = not written. No explanatory text — this file is Edvard's. journal.md holds the prose.
---

# Journal — Digest

## Needs Edvard

Nothing.

## Next cycle

**Verify and build in the same session — don't split them again.** The boundary check
is one command, and if it comes back clean there is nothing left in this layer to
find; Cycle 49 went through its threading, dedup, fixtures and tests. Do the check,
then start the PWA in the same cycle.

- `tail -5 /data/claude-home/quota-history.jsonl` — Cycle 49's session should show one
  `"boundary": "start"` at ~04:00 and one `"end"` at ~04:3x, and **nothing else**
  carrying a boundary. Extra `end` rows in bursts milliseconds apart mean the leak is
  back. The file was repaired this cycle: 42 ticks, one `start`, zero strays.
- `kubectl get deploy agora-claude-bridge -n agents -o jsonpath='{.spec.template.spec.containers[0].image}'`
  should have moved off `sha256:133d6713`. bridge#22 merged this cycle.

**Then, in order:**

1. **Build the Nova PWA's first slice** (`ideas.md` #34, items 1–4): journal timeline,
   status header, digest strip, per-cycle deep links. This has been top of this list
   for three cycles and keeps being displaced by the measurement layer built to serve
   it. Items 11 and 12 — quota chart and cycle ledger — are unblocked: ledger at
   `nova/resources/research/cycle-ledger.json`, regenerates with
   `python3 -m bridge.analytics --json`, and cycle edges now come from `boundary`.
2. **Sweep for tests that never let the real thing run.** Cycle 49's bug lived here and
   it is the third variant: no mock involved, just eight `QuotaWatcher` tests all
   calling `_run()` directly, so `start()`/`close()` were executed by zero tests. For
   any class with a `start()`/`close()` pair, check a test calls the real entry point.
   `ActivityReporter` has the identical shape — check it first.
3. **Guard the test suite at the transport layer.** bridge#22 fixed the one known leak,
   but any daemon thread outliving its test can still reach the live endpoint on real
   credentials. Patch `urllib.request.urlopen` for the whole session in conftest.
4. **Finish the fixture sweep.** `analytics.py` and the vault client both parse live
   payloads against invented fixtures; real payloads are on the PVC.
5. **Cut what every turn re-reads.** 79% of a cycle's cost is carrying context.
6. **Give `journal.md` a second layer** — it grows five times faster at 20 cycles/day.

**Don't re-derive the per-cycle quota cost from the seven-day percentage yet** — it
needs elapsed time. Full write-up in `nova/resources/research/cycle-economics.md`.
Re-derive around 2026-08-15.

## Digest

**Cycle 49** (2026-08-09 04:25) — My own test suite was quietly making live calls on your
Anthropic subscription and writing junk into the file that tracks what cycles cost.
Running the tests once added three fake rows carrying real numbers; nine such rows were
already in there. Nothing you touch is affected and no quota was meaningfully burned —
but it's the data the cost charts you asked for were going to be built on, so it
mattered that it was fiction. Fixed, and I cleaned the existing junk out. Worth saying
that I got the diagnosis wrong first and wrote it down as "harmless one-off" before a
second measurement caught me; the correction is in the journal.

**Cycle 48** (2026-08-09 03:02) — The thing that measures what a cycle costs couldn't
see where a cycle ended, and now it can. I added an end-of-cycle reading two cycles
ago; it has been running every cycle since and saving nothing, because a filter I
shipped in the same change was throwing it away for looking too much like the reading
a minute earlier. Nothing you touch changes — this only matters because it's the data
the cost charts you asked for will be built on, and it was quietly wrong. Also checked
last cycle's two deploys: both live and correct.

**Cycle 47** (2026-08-09 02:05) — Your #37 is done. A heartbeat can now run on
weekdays only, or twice a day, or every two hours through the daytime and never at
night — none of which the old settings could say. In the heartbeat form there's a
new "On days, at" option with day chips and a list of times, and a "Cron expression"
box next to it if you'd rather type one; they're the same schedule seen two ways, so
you can switch between them freely. The line under it always tells you the real
firing times before you save, which matters more than it sounds: cron multiplies
minutes by hours, so asking for 08:00 and 20:30 genuinely means four runs a day, and
it says so instead of quietly saving something else. Nothing existing changed. Also
the first time I've written a test for the app's own frontend, and the first time
I've actually operated the UI I built rather than assuming it worked.

**Cycle 46** (2026-08-09 00:50) — The cost tracker I built last cycle was quietly
broken in three ways, and I fixed all three. Confirmed this cycle: two of the three
are provably fixed now, the third needs one more cycle to see.

**Cycle 44** (2026-08-08 22:18) — Your phone goes quiet between 23:00 and 07:00,
starting tonight. The cycle still runs and still replies — the message is waiting in
the conversation in the morning, only the buzz is held back. It covers every persona,
not just me, because it's your phone that's asleep and not any one of us. Cycle 43
thought this would land too late for tonight; the new 72-minute rate woke me at 22:00
with 45 minutes to spare, which is the first time the faster cadence has clearly paid
for itself.

**Cycle 43** (2026-08-08 21:55) — You're on the max plan, so cycles now run every
72 minutes instead of every 6 hours: 20 a day, exactly 5x, hitting the old
22:00/04:00/10:00/16:00 times plus sixteen more. Measuring first turned up a
problem the faster rate would have caused: a cycle ran on Agora's main loop, so
nothing else in Agora could reply for the ~15 minutes I was working — 4% of the day
before, 21% after. runner#54 moves every heartbeat onto its own thread and makes
the shutdown drain wait for it, so a cycle still can't be killed mid-reply.

**Cycle 42** (2026-08-08 16:44) — Heartbeats no longer drift: an interval can be
pinned to a clock time (`every@6h@12:00` → 12:00, 18:00, 00:00, 06:00), with a
"starting at" box in the heartbeat form. Your #37, first half. The advanced timing
half is still open.

**Cycle 41** (2026-08-08 09:46) — Wrote down how I should run a research cycle and
a build cycle, from what you liked about Cycle 40, then used the build one the same
session to fix how my own instructions reach me.

**Cycle 40** (2026-08-08 03:30) — Read yoyo-evolve properly and wrote up what's
worth stealing; boarded it as `ideas.md` #34, a PWA of my own.

**Cycle 39** (2026-08-07 21:30) — Rewrote my own prompt around how this model
actually wants to be prompted; found my constitution was arriving as a user message.

**Cycle 38** (2026-08-07 15:40) — A third of the vault was deleted notes being
served as live. Fixed in the tools (runner#52).

