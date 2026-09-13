"""This week's number for each goal in `goals.md`, taken from an instrument.

Idea #38 asked for goals and a weekly review of progress against them. Both
exist. What does not is a way to recompute the numbers: the ``now:`` value
inside each goal's ```goal``` fence is typed by hand by whichever cycle runs
the Monday review, and `tools.append_goal_snapshot` files that typed number
under a date. So the chart on `/plan` is a series of a cycle's arithmetic,
not of a measurement.

The 2026-08-24 review named the cost itself, about G5:

    Last week's "75% of spend was directed" was computed by a script that no
    longer exists in any file I can find. So I have substituted a cycle
    *count* ... A number nobody can recompute is not a measure; it is a
    memory.

    python3 -m tools.goal_measures --goals goals.md

**A goal with no instrument prints as having none, and that is the point.**
Three of the five goals on the slate are computable from things this loop
already serves -- the journal API, the board API, `gh`, Agora's heartbeat
list -- and two are not. G2 counts things the owner still has to leave the app
to do, which is a judgement about his experience and cannot be derived from
anything here. Printing `no instrument` beside it is more honest than a
proxy that would drift without saying so, and it marks the number the
review still has to defend in prose.

**Every measure here is a floor or an approximation and says which.** G3 is
a phrase match over prose, so it can only undercount. G1's denominator uses
a board row's `Updated` date as its closing date, because a row carries no
other date -- that is the same approximation the 2026-08-24 review made and
wrote down. Nothing is rounded into a headline that hides it.

Exit 0 printed a report, exit 1 could not read something it needed.

By default it never writes: the review edits `goals.md` and this tells it what
to write. **`--write` is Cycle 563 and exists because that review has never
run.** The Monday heartbeat that types these numbers in has fired zero times
since it was created, so on 2026-08-28 all four instrumented goals had drifted
from the instrument -- G1 8.2 against a written 7.8, G3 4 against 5, G4 2
against 1, G5 48% against 41 -- and those four wrong numbers were the
scoreboard at the top of `/plan`, which is the page the owner reads and the
"graphs" half of issue #96. A number a human has to retype weekly is a number
that goes stale the first week nobody does. `--write` puts the measurement
into each instrumented goal's `now:` field in the `--goals` file, in place,
and leaves a goal with no instrument alone -- writing a guess there would be
exactly the drift this is fixing. It does not touch the vault: the caller does
the read-modify-write with an `if_rev` guard, so this stays runnable from
either pod and holds no credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import statistics
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.config import OSLO
from agora_runner.nova_goal_history import goal_key
from agora_runner.nova_plan import _fenced, _goal, set_field_in_goals

SITE = os.environ.get(
    "NOVA_SITE_SELF_URL", "http://nova-site.agents.svc.cluster.local:8083"
)

# Marcus's own app, which serves its whole persisted state at `/api/state` with
# no token. That endpoint is the only instrument this loop has for what the
# owner has actually done in that app, and the numbers Cycle 1529 typed into
# `project-goals.md` by hand came from a `curl` at it.
MARCUS = os.environ.get(
    "MARCUS_SELF_URL", "http://marcus.agents.svc.cluster.local:8080"
)


def today_oslo(now=None):
    """Today's date in Oslo, as `YYYY-MM-DD`.

    The window this bounds is handed to GitHub as a `merged:<since>..<until>`
    qualifier and matched against the journal's own Oslo dates, so "today"
    has to be Oslo's today and not the pod's UTC one. This was
    `datetime.now(timezone.utc) + timedelta(hours=2)`, which is Oslo only
    from late March to late October -- in winter Oslo is UTC+1, and between
    22:00 and 23:00 UTC the extra hour rolls the date forward a day, so the
    window ended tomorrow and started a day early.

    `OSLO` comes from `agora_runner.config` rather than a fifth
    `ZoneInfo("Europe/Oslo")` of our own, which is what `tools.lint_entry`
    and `tools.roll_needs_edvard` already do. That constant is guarded and
    falls back to UTC on an image with no tzdata; a bare `ZoneInfo` there
    would raise at import, before `main` can return the exit 1 this tool's
    docstring promises. Cycle 611 measured `ZoneInfo("Europe/Oslo")`
    resolving on both the bridge pod and the runner pod, so the fallback is
    not live today -- but note it would make this window silently UTC rather
    than loudly broken, and `agora_runner/catalog_build.py` still carries a
    comment saying the image has no tzdata. The two disagree; the
    measurement is this one.
    """
    return (now or datetime.now(timezone.utc)).astimezone(OSLO).date().isoformat()

# The repos a merged pull request can land in. The 2026-08-24 review counted
# these five by hand; naming them here is what makes next week's count the
# same count rather than a similar one.
REPOS = (
    "SokratesAI/agora-persona-runner",
    "SokratesAI/agora-claude-bridge",
    "SokratesAI/agora",
    "SokratesAI/platform-config",
    "SokratesAI/vault-bridge",
)

# A card the runner writes for a cycle that woke and wrote nothing, and a
# periodic report, are not a cycle's own work -- same line `work_for_whom`
# and `nova_journal.cycle_entries` draw, and for the same reason: neither
# can carry a `board` field, so counting them deflates G5 silently.
NOT_A_CYCLES_OWN_ENTRY = ("report", "silence")

# G3 counts entries that say out loud that a fact this loop published was
# wrong. It is a phrase match over prose and therefore a floor -- an entry
# that owns a mistake in words none of these cover is invisible here. The
# list is printed with the number so the floor is arguable rather than
# hidden. First person only, because `personality.md` requires that voice
# for self-correction and a third-person "that was wrong" is usually about
# somebody else's system.
CORRECTION_PHRASES = (
    "i was wrong",
    "i had it wrong",
    "i got it wrong",
    "what i had wrong",
    "i had that wrong",
    "correcting myself",
    "corrected myself",
    "my own correction",
    "that was my mistake",
    "the mistake is mine",
    "i have to correct",
    "i said it wrong",
    "wrote it up wider",
)


def _iso(value):
    """`YYYY-MM-DD` out of a board row's `Updated`, or `None`.

    Board rows carry two shapes -- the owner's boards write `08-27` and the
    Nova board writes `2026-08-26` -- so a window filter that understood
    only one of them would silently drop a whole board. A bare `MM-DD` is
    read against the year of the window it is being tested in, which is
    the only year it can mean on a board that rolls forward.
    """
    text = (value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    return None


def _iso_in_year(value, year):
    text = (value or "").strip()
    if re.fullmatch(r"\d{2}-\d{2}", text):
        return f"{year}-{text}"
    return _iso(text)


def _get_json(url, timeout=60):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read()), None
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return None, f"could not read {url}: {exc}"


def fetch_entries(limit, site=SITE):
    """Journal entries from the site's own API, newest first."""
    payload, error = _get_json(f"{site}/api/journal?limit={limit}")
    if error:
        return [], error
    entries = payload.get("entries") or []
    return [e for e in entries if e.get("kind") not in NOT_A_CYCLES_OWN_ENTRY], None


def fetch_board(name, site=SITE):
    """Every row on one of the boards, from the site's own API."""
    payload, error = _get_json(f"{site}/api/board?name={name}")
    if error:
        return [], error
    return payload.get("items") or [], None


def fetch_marcus_state(site=MARCUS):
    """Marcus's persisted `data` object, or `(None, why)`.

    Returns the inner `data` rather than the envelope: `rev` and `updatedAt`
    describe the store, and every measure here is about what is in it.
    """
    payload, error = _get_json(f"{site}/api/state")
    if error:
        return None, error
    data = (payload or {}).get("data")
    if not isinstance(data, dict):
        return None, f"{site}/api/state answered without a `data` object"
    return data, None


def fetch_merged(repo, since, until, limit=1000):
    """Pull requests on `repo` merged inside the window, as numbers.

    **The window is applied by GitHub, not here.** Asking for the newest
    `limit` merges and filtering locally cannot count a repo that merges
    more than `limit` in a window, and this loop reached that on its own
    source: measured 2026-08-29, `SokratesAI/agora-persona-runner` merged
    213 pull requests in seven days against the old `--limit 200`, so the
    page was full of in-window rows and the true count was unreachable.
    `--search merged:<since>..<until>` makes GitHub do the filtering, so
    the page holds only what is being counted and a full page means "there
    may be more", not "the window is bigger than the page".

    `title` and `body` come back alongside the number because
    `measure_pm_written_why` reads them; every other caller counts rows and
    is untouched by their presence.
    """
    try:
        done = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "merged",
             "--search", f"merged:{since}..{until}",
             "--limit", str(limit), "--json", "number,mergedAt,title,body"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh pr list could not run on {repo}: {exc}"
    if done.returncode != 0:
        return None, f"gh pr list failed on {repo}: {done.stderr.strip()[:200]}"
    try:
        rows = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        return None, f"gh pr list returned unreadable JSON for {repo}: {exc}"
    inside = [r for r in rows
              if since <= (r.get("mergedAt") or "")[:10] <= until]
    # A window that swallows the whole page is a window this cannot measure:
    # the oldest merge read is inside it, so there may be older ones unread.
    if len(inside) == len(rows) == limit:
        return None, f"{repo}: all {limit} merges read fall inside the window, so this is a floor and not a count"
    return inside, None


def collect_merges(repos, since, until):
    """Every merge across `repos` in the window, or `None` if one could not be read.

    **`None` rather than a short list, and that is the whole point of this
    function existing.** The caller used to `continue` past a repo
    `fetch_merged` refused, so a repo it could not count was counted as zero
    and G1 -- a ratio over all of them -- published a smaller number with
    nothing on it saying so. Measured 2026-08-29: the runner repo saturated
    its page, dropped out, and G1 printed 2.0 against a real 7.1 on the
    scoreboard at the top of `/plan`. A numerator missing one of its terms is
    wrong, not low, so the honest answer is no answer.

    Returns `(prs, problems)`. `problems` always names every repo that failed,
    whether or not an earlier one already did -- one unreadable repo must not
    hide the next one's reason.
    """
    prs, problems, failed = [], [], False
    for repo in repos:
        merged, error = fetch_merged(repo, since, until)
        if error:
            problems.append(error)
            failed = True
            continue
        prs.extend(merged)
    return (None if failed else prs), problems


def entry_text(entry):
    """Every word of an entry's prose, lowercased, as one string."""
    parts = []
    for field in ("title", "briefSpans", "blocks"):
        value = entry.get(field)
        if isinstance(value, str):
            parts.append(value)
        elif value is not None:
            parts.append(json.dumps(value))
    return " ".join(parts).lower()


def in_window(entries, since, until):
    return [e for e in entries if since <= (e.get("date") or "") <= until]


def measure_g1(entries_window, boards, since, until, prs):
    """Merged pull requests per board row closed, over the window."""
    del entries_window
    if prs is None:
        return None, ("a repo in the count could not be read, and a ratio "
                      "missing part of its numerator is wrong rather than low")
    year = since[:4]
    closed = [
        row for board in boards for row in board
        if (row.get("statusKey") or "") == "done"
        and (_iso_in_year(row.get("updated"), year) or "") >= since
        and (_iso_in_year(row.get("updated"), year) or "") <= until
    ]
    if not closed:
        return None, "no board row closed in the window, so a per-row rate has no denominator"
    return round(len(prs) / len(closed), 1), (
        f"{len(prs)} merged PR(s) across {len(REPOS)} repo(s) against "
        f"{len(closed)} row(s) closed; a row's closing date is its Updated date, "
        "which is the only date it carries"
    )


def measure_g3(entries_window, boards, since, until, prs):
    """Entries that own a correction, as a floor."""
    del boards, since, until, prs
    hits = [e for e in entries_window
            if any(p in entry_text(e) for p in CORRECTION_PHRASES)]
    return len(hits), (
        f"{len(hits)} of {len(entries_window)} entries match one of "
        f"{len(CORRECTION_PHRASES)} first-person correction phrases; a phrase "
        "match undercounts and can never overcount"
    )


def measure_g4(entries_window, boards, since, until, prs, heartbeats=None):
    """Distinct personas Agora runs on a schedule that has actually fired."""
    del entries_window, boards, since, until, prs
    if heartbeats is None:
        from agora_runner.heartbeat_liveness import _fetch
        heartbeats, error = _fetch()
        if error:
            return None, error
    live = {}
    for row in heartbeats:
        if not row.get("enabled"):
            continue
        if not (row.get("lastRunAt") or "").strip():
            continue
        persona = (row.get("personaId") or "").strip()
        if persona:
            live.setdefault(persona, []).append(row.get("name") or "?")
    if not live:
        return 0, "no enabled heartbeat has ever run"
    named = "; ".join(f"{p[:8]}: {', '.join(sorted(set(n)))}" for p, n in sorted(live.items()))
    return len(live), f"{named}"


def measure_g5(entries_window, boards, since, until, prs):
    """Share of a cycle's own entries that name a row off the owner's boards."""
    del boards, since, until, prs
    if not entries_window:
        return None, "no journal entry in the window"
    named = sum(1 for e in entries_window if (e.get("board") or "").strip())
    return round(100 * named / len(entries_window)), (
        f"{named} of {len(entries_window)} entries name a board row; this is "
        "the cycle-count substitution the 2026-08-24 review made, not a share "
        "of spend -- the cost ledger carries no cycle number"
    )


# Keyed on the `G<n>` prefix of a goal's `name:`, which `goal_key` extracts
# and `goal-history.json` already keys its series on. A goal this does not
# know prints as having no instrument rather than being dropped, so adding a
# goal to the slate never silently shrinks this report.
MEASURERS = {
    "G1": measure_g1,
    "G3": measure_g3,
    "G4": measure_g4,
    "G5": measure_g5,
}

NO_INSTRUMENT = {
    "G2": "counts things the owner still has to leave the Nova app to do -- a "
          "judgement about his experience, not a fact on this box",
}


def _as_number(text):
    try:
        return float(str(text).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def render(rows, since, until, problems):
    lines = [f"GOAL MEASURES — {since} to {until} (Oslo dates on the journal, UTC on merges)"]
    for row in rows:
        key, goal, value, detail = row["key"], row["goal"], row["value"], row["detail"]
        unit = (goal.get("unit") or "").strip()
        written = goal.get("now", "")
        if value is None:
            lines.append(f"  {key}  {goal['name']}")
            lines.append(f"      goals.md says now: {written or '(none)'} — {detail}")
            continue
        shown = f"{value}{unit and ' ' + unit}"
        drift = ""
        a, b = _as_number(written), _as_number(value)
        if a is None:
            drift = "  <- goals.md carries no number"
        elif a != b:
            drift = f"  <- goals.md says {written}, drifted"
        lines.append(f"  {key}  {goal['name']}")
        lines.append(f"      measured {shown}{drift}")
        lines.append(f"      {detail}")
    lines.append("")
    lines.append("WHAT THIS CANNOT SEE")
    lines.append(f"  Merges are counted on {len(REPOS)} named repo(s); a merge anywhere else is invisible.")
    lines.append("  A board row's closing date is the last date the row changed, not the date it was finished.")
    lines.append("  G3 is a phrase match over prose, so it is a floor.")
    lines.append("  G5 counts cycles, not spend: the cost ledger's rows carry no cycle number.")
    lines.append("  G4 counts distinct persona ids on a fired heartbeat, which is not the same as distinct tenants.")
    for problem in problems:
        lines.append(f"  ! {problem}")
    return "\n".join(lines)


def write_back(path, text, rows):
    """Put each measured value into its goal's `now:`, in place. Returns a report.

    Only a goal that has an instrument *and* whose written number differs is
    touched, so a run that changes nothing writes nothing -- which matters
    because the caller wraps this in a compare-and-swap against a file the
    owner edits from his phone, and a no-op write is a real chance to lose
    his edit for nothing.

    A goal whose fence `set_field_in_goals` refuses -- a name that moved, two
    blocks claiming it, an unterminated fence -- is named in the report rather
    than skipped quietly. That is the failure this whole tool exists to stop:
    a number that is silently not what it says it is.
    """
    lines, changed = [], 0
    for row in rows:
        goal, value = row["goal"], row["value"]
        if value is None:
            continue
        written = goal.get("now", "")
        if _as_number(written) == _as_number(value):
            continue
        amended = set_field_in_goals(text, goal.get("name"), "now", value)
        if amended is None:
            lines.append(f"  ! {row['key']}: could not edit that goal's fence, left at {written or '(none)'}")
            continue
        text, changed = amended, changed + 1
        lines.append(f"  {row['key']}  now: {written or '(none)'} -> {value}")
    if not changed:
        # Not "everything already agrees" when a fence refused the edit --
        # that sentence would report a clean run over the exact failure this
        # is here to surface. Caught by its own test, not by reading it.
        head = ("WROTE NOTHING — every instrumented goal already carries its measured number"
                if not lines else "WROTE NOTHING")
        return "\n".join([head] + lines)
    try:
        open(path, "w", encoding="utf-8").write(text)
    except OSError as exc:
        return f"COULD NOT WRITE {path}: {exc}"
    return "\n".join([f"WROTE {changed} value(s) into {path}"] + lines)


# A key result in `project-goals.md` whose number is the SAME measurement a
# goal in `goals.md` already has an instrument for. Keyed on the key result's
# `id`, which is the stable address -- its `name` is a sentence rewritten
# while the conversation about it is open, and `milestone-seats.md`'s `Serves`
# column points at the id.
#
# This map exists because three of `project-goals.md`'s nine `now:` values
# were copied out of `goals.md` by hand when Cycle 1529 wrote the document,
# and `goals.md`'s own numbers had themselves drifted from this instrument --
# so the key result carried a retype of a stale number, two removes from
# anything measured. A key result NOT in this map prints as having no
# instrument and is never written, the same contract `NO_INSTRUMENT` gives a
# goal: inventing a number for it is the drift this tool exists to end.
KEY_RESULT_INSTRUMENTS = {
    "nova-kr-your-rows": "G1",
    "nova-kr-true-first-time": "G3",
}


def measure_marcus_sessions_logged(state, since, until):
    """Training sessions per week, counted in Marcus's own store.

    A session record is `{id, date: "YYYY-MM-DD", kind, ...}` -- the date is
    the day the log is *for*, which is the day he picks in the form, not the
    day the row was written. That is the right day for "the training you did".

    The window is the same one the goals use, so a `--days` other than 7 still
    reports a per-week rate rather than a raw count that silently means
    something else.
    """
    sessions = state.get("sessions")
    if not isinstance(sessions, list):
        return None, "Marcus's state carries no `sessions` list"
    inside, undated = 0, 0
    for session in sessions:
        day = _iso((session or {}).get("date") if isinstance(session, dict) else None)
        if day is None:
            undated += 1
            continue
        if since <= day <= until:
            inside += 1
    days = (date.fromisoformat(until) - date.fromisoformat(since)).days + 1
    rate = round(inside * 7 / days, 1) if days else 0
    detail = (f"{inside} session(s) dated inside {since}..{until} out of "
              f"{len(sessions)} in the store, over a {days}-day window")
    if undated:
        detail += (f"; {undated} carry no YYYY-MM-DD date and are not counted, "
                   "so this is a floor")
    return rate, detail


def measure_marcus_own_plan(state, since, until):
    """1 when the active plan has any exercise in it, 0 when it is empty.

    **This is a ceiling and the reason is worth carrying.** The key result
    wants to separate his own plan from the app's seeded demo block, and
    `/api/state` cannot: the demo marker is `demoSeeded` in the *browser's*
    localStorage, so the store holds a demo plan and his own plan in exactly
    the same shape. What the store does answer exactly is the empty case --
    `blockName: "No plan yet"` with no exercises on any day -- which is what
    it reads today. So a 0 here is exact and a 1 would need his word.
    """
    plan = state.get("plan")
    if not isinstance(plan, dict):
        return None, "Marcus's state carries no `plan` object"
    days = plan.get("days") if isinstance(plan.get("days"), list) else []
    filled = [d for d in days
              if isinstance(d, dict) and (d.get("exercises") or [])]
    name = str(plan.get("blockName") or "").strip() or "(unnamed)"
    if not filled:
        return 0, f"the active plan is {name!r} with no exercises on any of its {len(days)} day(s)"
    return 1, (f"the active plan is {name!r} with exercises on {len(filled)} of "
               f"{len(days)} day(s) -- a ceiling, because /api/state carries no "
               "marker separating the seeded demo block from a plan you drafted")


# A merged pull request names a board row when it says so in words: `issue #227`
# or `idea #38`, in the title or the body. The label is required and a bare
# `#1062` is deliberately NOT a match -- that is the pull request's own number,
# which GitHub writes into every squash title, so a bare-number pattern would
# score every merge as traceable and the measure would read 100% forever.
BOARD_REFERENCE = re.compile(r"\b(?:issue|idea)s?\s*#\s*(\d+)", re.IGNORECASE)


def measure_pm_written_why(prs, since, until):
    """Share of merged pull requests that name the board row they serve.

    **A floor, and the reason is the same one G3 carries.** This can only see
    a reference written in words -- `issue #227`, `idea #38` -- so a pull
    request that closes one of his unnumbered captures, or that names its row
    only in a commit message this never reads, counts against the share while
    being perfectly traceable in fact. A phrase match undercounts and can
    never overcount, so a number here is the least this loop is doing, not
    the most.

    The denominator is every merge in the window across `REPOS`, which is the
    same set `measure_g1` divides by rows closed -- deliberately, because the
    key result is that measure read from the other end.
    """
    del since, until
    if prs is None:
        return None, ("a repo in the count could not be read, and a share "
                      "missing part of its denominator is wrong rather than low")
    if not prs:
        return None, "no pull request merged in the window, so a share has no denominator"
    named = [pr for pr in prs
             if BOARD_REFERENCE.search(
                 f"{(pr or {}).get('title') or ''}\n{(pr or {}).get('body') or ''}")]
    share = round(100 * len(named) / len(prs))
    return share, (
        f"{len(named)} of {len(prs)} merged PR(s) across {len(REPOS)} repo(s) "
        "name a board row in the title or body as `issue #N` or `idea #N`; a "
        "bare `#N` is not counted because that is the PR's own number, and a "
        "reference only in a commit message is not read, so this is a floor"
    )


# A key result whose number is measured HERE rather than borrowed from a goal
# in `goals.md`. `KEY_RESULT_INSTRUMENTS` above covers the other direction --
# a key result that is the same measurement a goal already has -- and the two
# are deliberately separate maps: borrowing a goal row guarantees the two
# documents cannot disagree, while these have no goal to borrow from because
# `goals.md` is this loop's slate and Marcus is a different project.
#
# Each measurer takes `(state, since, until)` and returns `(value, detail)`,
# or `(None, why)` when the state it needed was not in the shape it expects.
KEY_RESULT_MEASURERS = {
    "marcus-kr-sessions-logged": measure_marcus_sessions_logged,
    "marcus-kr-a-plan-of-his-own": measure_marcus_own_plan,
}

# A key result measured off the merged-pull-request list rather than off
# Marcus's state. Kept as its own map because the argument is different: these
# take `(prs, since, until)`, where `prs` is what `collect_merges` returned --
# `None` when a repo could not be read, which is not the same as no instrument.
KEY_RESULT_PR_MEASURERS = {
    "pm-kr-written-why": measure_pm_written_why,
}

KEY_RESULT_NO_INSTRUMENT = {
    "nova-kr-in-the-app": "counts things the owner still has to leave the Nova "
                          "app to do -- a judgement about his experience, not a "
                          "fact on this box; same reason as G2, which is the "
                          "same measure",
    "marcus-kr-coach-first-try": "Marcus keeps no record of coach taps or "
                                 "retries -- /api/state holds the chat but not "
                                 "whether a tap needed a second one -- so the "
                                 "only way to read this is to drive the live "
                                 "coach a number of times and count, which is a "
                                 "sampling run against a production LLM route "
                                 "rather than a fact readable off the box",
}


#: The window every KPI here is read over. A guardrail says what is happening
#: now, so it is 24 hours regardless of `--days`, which sets the goals' window.
_KPI_WINDOW_HOURS = 24.0

def measure_nova_dropped_ticks(since, until):
    """Share of scheduled heartbeat firings in the last 24h that produced no run.

    Reads `tools.heartbeat_gaps` rather than re-deriving it: that module already
    fetches every heartbeat and every conversation, works out each schedule's
    period, and decides which slots a run covered -- including the part that is
    genuinely hard, which is that a run still in flight covers the slot it
    overran into. A second implementation here would be a second answer to one
    question, and the two would drift the way `goals.md` and `project-goals.md`
    did before the key results were wired to one measurement.

    The window is 24 hours and is NOT the `--days` window the goals use. This
    KPI's own `measure:` field says "in 24h", and a guardrail read over seven
    days would average a bad night away -- which is the one thing a guardrail
    must not do.

    A row `heartbeat_gaps` could not judge contributes nothing to either side
    of the share, and the count of those is in the detail, because a share
    taken over two of ten heartbeats is not a claim about the scheduler.
    """
    del since, until
    from datetime import datetime, timezone
    from tools import heartbeat_gaps

    heartbeats, error = heartbeat_gaps._fetch()
    if error:
        return None, f"heartbeat_gaps could not read the heartbeats: {error}"
    conversations, error = heartbeat_gaps.fetch_conversations()
    if error:
        return None, f"heartbeat_gaps could not read the conversations: {error}"
    now = datetime.now(timezone.utc)
    rows = [heartbeat_gaps.judge(h, conversations, now, _KPI_WINDOW_HOURS)
            for h in heartbeats]
    judged = [r for r in rows if r.get("verdict") == "judged"]
    if not judged:
        return None, (f"none of the {len(rows)} heartbeat(s) could be judged, so "
                      "a share has no denominator")
    expected = sum(r["expected"] for r in judged)
    if not expected:
        return None, (f"the {len(judged)} judged heartbeat(s) had no scheduled "
                      f"firing in the last {_KPI_WINDOW_HOURS:g}h")
    missed = sum(len(r["missed"]) for r in judged)
    share = round(100 * missed / expected)
    unjudged = len(rows) - len(judged)
    detail = (f"{missed} of {expected} scheduled firing(s) in the last "
              f"{_KPI_WINDOW_HOURS:g}h produced no run, across {len(judged)} "
              f"judged heartbeat(s)")
    if unjudged:
        detail += (f"; {unjudged} more could not be judged and are in neither "
                   "half of the share")
    return share, detail


def fetch_cost_ledger(site=SITE):
    """The published cost ledger, as the site already shapes it.

    `publish_costs` in the bridge rebuilds this from the transcripts at the
    end of every cycle and the site serves it at `/api/costs`, so this is a
    read of the same document the cost page draws -- not a second pass over
    the transcripts. Rows come back as arrays and `cycleColumns` names the
    positions; the caller indexes by name off that list rather than by a
    number of its own, because `nova_costs` says in as many words that
    reordering either tuple silently swaps what is being read.
    """
    payload, error = _get_json(f"{site}/api/costs")
    if error:
        return None, error
    rows = (payload or {}).get("cycles")
    columns = (payload or {}).get("cycleColumns")
    if not isinstance(rows, list) or not isinstance(columns, list):
        return None, f"{site}/api/costs answered without `cycles` and `cycleColumns`"
    return {"rows": rows, "columns": columns}, None


def measure_nova_cost_per_cycle(since, until, ledger=None):
    """Median weighted tokens per cycle over the last 24 hours, in millions.

    The `now:` on this KPI was carried out of a paragraph in `prompt.md`
    describing the 08-24..08-28 window -- a number typed from prose about a
    window that closed weeks ago. The ledger it came from is republished
    after every cycle and has been readable the whole time.

    **What is counted is the `weighted` column alone, which is the KPI's own
    `measure:` field read literally, and it understates a delegating cycle.**
    `nova_costs._subagent` is explicit that a parent's `weightedTokens`
    deliberately does not absorb its children's, so a cycle that fans out to
    subagents costs more than this number says. I did not fold them in, and
    the reason is that `low`/`high` on this fence were set against the
    narrower definition: widening the measure while leaving the bounds alone
    would push the reading toward a breach for a definitional reason rather
    than a cost one, and moving the bounds to fit is the one thing rule 4 of
    issue #227 forbids. So the delegation on top is measured and printed in
    the detail instead, where it is his call rather than mine.

    The window is 24 hours and not the goals' `--days`, the same call
    `measure_nova_dropped_ticks` makes: a guardrail averaged over a week
    hides the expensive night it exists to catch.
    """
    del since, until
    if ledger is None:
        ledger, error = fetch_cost_ledger()
        if error:
            return None, f"the cost ledger could not be read: {error}"
    columns = ledger["columns"]
    try:
        at = columns.index("at")
        weighted = columns.index("weighted")
    except ValueError:
        return None, ("the cost ledger's `cycleColumns` names no `at`/`weighted` "
                      f"column: {columns}")
    sub = columns.index("subagentWeighted") if "subagentWeighted" in columns else None

    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    floor_ms = now_ms - _KPI_WINDOW_HOURS * 3600 * 1000
    values, delegated, unattributed = [], [], 0
    for row in ledger["rows"]:
        if not isinstance(row, list) or len(row) <= max(at, weighted):
            continue
        stamp, cost = row[at], row[weighted]
        if not isinstance(stamp, (int, float)) or not isinstance(cost, (int, float)):
            continue
        if stamp < floor_ms or stamp > now_ms:
            continue
        values.append(cost)
        if sub is None or len(row) <= sub or row[sub] is None:
            # Subagent attribution landed 2026-08-19 and every row older than
            # it carries no such key. A hole is not a zero, so it is counted
            # as one rather than folded into the delegation figure.
            unattributed += 1
        elif row[sub]:
            delegated.append(row[sub])
    if not values:
        return None, (f"no cycle in the ledger ran inside the last "
                      f"{_KPI_WINDOW_HOURS:g}h, so there is no median to take")
    median = statistics.median(values)
    detail = (f"median of {len(values)} cycle(s) in the last {_KPI_WINDOW_HOURS:g}h, "
              f"from the cost ledger the site publishes")
    if delegated:
        extra = statistics.median(delegated) / 1_000_000
        detail += (f"; {len(delegated)} of them delegated and their median "
                   f"subagent cost is {extra:.2f}M on top, which this number "
                   "does NOT include -- the measure is the `weighted` column")
    if unattributed:
        detail += (f"; {unattributed} row(s) carry no subagent attribution at "
                   "all, so their delegation is unknown rather than zero")
    return round(median / 1_000_000, 2), detail


#: A KPI whose number is measured here. Each measurer takes `(since, until)`
#: -- the goals' window, which a KPI is free to ignore and this one does -- and
#: returns `(value, detail)`, or `(None, why)` when it could not read what it
#: needed. Same contract as `KEY_RESULT_MEASURERS`, deliberately, because the
#: failure it protects against is the same one: a number nobody can recompute.
KPI_MEASURERS = {
    "nova-kpi-dropped-ticks": measure_nova_dropped_ticks,
    "nova-kpi-cost-per-cycle": measure_nova_cost_per_cycle,
}

#: A KPI with no instrument, and why. Written down here rather than left as a
#: silent gap, for the reason `KEY_RESULT_NO_INSTRUMENT` exists: a blank `now`
#: says nothing about whether anyone tried, and three cycles re-deriving the
#: same "there is no endpoint for this" is three cycles spent twice.
KPI_NO_INSTRUMENT = {
    "nova-kpi-silent-cycles": "counts cycles that produced no journal entry, "
                              "which needs the heartbeat's firing list joined "
                              "to the entry list -- readable, but it is a "
                              "second answer to the question tools.cycle_health "
                              "already answers and belongs there rather than "
                              "here",
    "marcus-kpi-coach-latency": "timing it means driving the live coach, which "
                                "is a sampling run against a production LLM "
                                "route rather than a fact readable off the box "
                                "-- same reason as marcus-kr-coach-first-try",
    "pm-kpi-deprecations": "counts the features taken away again, which is "
                           "read off his own judgement of what was a mistake "
                           "rather than off any record on this box -- there is "
                           "no deprecation marker anywhere in these repos to "
                           "count",
    "marcus-kpi-push-subscribers": "nothing on the Marcus pod exposes a "
                                   "subscriber count: /api/push/subscriptions, "
                                   "/api/subscriptions and /api/push/status all "
                                   "404, so the reading has to be built before "
                                   "it can be taken",
}


def kpi_rows(sections, since=None, until=None):
    """Pair every KPI in `project-goals.md` with a measurement, or with a why.

    The mirror of `key_result_rows`, and it is deliberately a separate function
    rather than a flag on that one: a KPI has `low`/`high` where a key result
    has `target`, it is read over its own 24h window, and rule 4 of issue #227
    says a KPI may never be used as a key result. One function serving both
    would be the first place that distinction quietly stops being enforced.
    """
    out = []
    for name, section in (sections or {}).items():
        for kpi in section.get("kpis") or []:
            kpi_id = (kpi.get("id") or "").strip()
            row = {"project": name, "id": kpi_id, "kpi": kpi}
            measurer = KPI_MEASURERS.get(kpi_id)
            if measurer is None:
                why = KPI_NO_INSTRUMENT.get(
                    kpi_id, "nothing here computes this measure")
                out.append({**row, "value": None, "detail": f"no instrument — {why}"})
                continue
            value, detail = measurer(since, until)
            if value is None:
                out.append({**row, "value": None,
                            "detail": f"not measured — {detail}"})
                continue
            out.append({**row, "value": value, "detail": detail})
    return out


def render_kpis(rows, path):
    lines = [f"KPIs — {path}"]
    for row in rows:
        written = str(row["kpi"].get("now", "")).strip()
        lines.append(f"  {row['project']} / {row['id']}")
        if row["value"] is None:
            lines.append(f"      {path} says now: {written or '(blank)'} — {row['detail']}")
            continue
        low = str(row["kpi"].get("low", "")).strip()
        high = str(row["kpi"].get("high", "")).strip()
        bounds = f"  [{low or '-'}..{high or '-'}]"
        drift = "" if _as_number(written) == _as_number(row["value"]) else \
            f"  <- the document says {written or '(blank)'}, drifted"
        lines.append(f"      measured {row['value']}{bounds}{drift}")
        lines.append(f"      {row['detail']}")
    return "\n".join(lines)


def write_back_kpis(path, text, rows):
    """Put each measured value into its KPI's `now:`, in place.

    Same contract as `write_back_key_results`, including the part that matters:
    only a KPI with an instrument and a different written number is touched, so
    a run that changes nothing writes nothing. `low:` and `high:` are never
    written -- see `set_field_in_kpi` for why moving a guardrail's bounds to fit
    its reading is the failure the KPI/key-result split exists to prevent.
    """
    from agora_runner.project_goals import set_field_in_kpi

    lines, changed = [], 0
    for row in rows:
        if row["value"] is None:
            continue
        written = str(row["kpi"].get("now", "")).strip()
        if _as_number(written) == _as_number(row["value"]):
            continue
        amended = set_field_in_kpi(text, row["id"], "now", row["value"])
        if amended is None:
            lines.append(f"  ! {row['id']}: could not edit that kpi fence, "
                         f"left at {written or '(blank)'}")
            continue
        text, changed = amended, changed + 1
        lines.append(f"  {row['id']}  now: {written or '(blank)'} -> {row['value']}")
    if not changed:
        head = ("WROTE NOTHING — every instrumented KPI already carries its "
                "measured number" if not lines else "WROTE NOTHING")
        return "\n".join([head] + lines)
    try:
        open(path, "w", encoding="utf-8").write(text)
    except OSError as exc:
        return f"COULD NOT WRITE {path}: {exc}"
    return "\n".join([f"WROTE {changed} value(s) into {path}"] + lines)


def _needs_marcus(sections):
    """True when any key result in the document is measured off Marcus.

    The fetch is skipped otherwise, so a document with no Marcus section costs
    no call and cannot fail on a route it does not use.
    """
    for section in (sections or {}).values():
        for kr in section.get("keyResults") or []:
            if (kr.get("id") or "").strip() in KEY_RESULT_MEASURERS:
                return True
    return False


def key_result_rows(sections, rows, marcus=None, marcus_error=None,
                    since=None, until=None, prs=None):
    """Pair every key result in `project-goals.md` with a measurement.

    Two sources, in this order. A key result in `KEY_RESULT_INSTRUMENTS` takes
    its number from the goal row `main` already built for `goals.md`, so the
    two documents cannot disagree: there is one measurement and two places that
    print it. A key result in `KEY_RESULT_MEASURERS` is measured here, from
    `marcus` -- the `data` object off Marcus's `/api/state`. A key result in
    `KEY_RESULT_PR_MEASURERS` is measured from `prs`, the merge list
    `collect_merges` built for the goals above, so it divides by exactly the
    same set G1 does.

    `marcus` being `None` is not the same as a key result having no instrument,
    and the detail says which: an unread state names why it could not be read,
    so a cycle never reads "no instrument" over a route that was simply down.
    """
    by_key = {row["key"]: row for row in rows}
    out = []
    for name, section in (sections or {}).items():
        for kr in section.get("keyResults") or []:
            kr_id = (kr.get("id") or "").strip()
            row = {"project": name, "id": kr_id, "kr": kr}
            pr_measurer = KEY_RESULT_PR_MEASURERS.get(kr_id)
            if pr_measurer is not None:
                value, detail = pr_measurer(prs, since, until)
                if value is None:
                    out.append({**row, "value": None,
                                "detail": f"not measured — {detail}"})
                    continue
                out.append({**row, "value": value, "detail": detail})
                continue
            measurer = KEY_RESULT_MEASURERS.get(kr_id)
            if measurer is not None:
                if marcus is None:
                    why = marcus_error or "Marcus's state was not read"
                    out.append({**row, "value": None,
                                "detail": f"not measured — {why}"})
                    continue
                value, detail = measurer(marcus, since, until)
                if value is None:
                    out.append({**row, "value": None,
                                "detail": f"not measured — {detail}"})
                    continue
                out.append({**row, "value": value, "detail": detail})
                continue
            goal_key_name = KEY_RESULT_INSTRUMENTS.get(kr_id)
            source = by_key.get(goal_key_name) if goal_key_name else None
            if source is None or source.get("value") is None:
                why = KEY_RESULT_NO_INSTRUMENT.get(
                    kr_id, "nothing here computes this measure")
                if goal_key_name and source is not None:
                    why = f"{goal_key_name} could not be measured: {source['detail']}"
                out.append({**row, "value": None, "detail": f"no instrument — {why}"})
                continue
            out.append({**row, "value": source["value"],
                        "detail": f"from {goal_key_name}: {source['detail']}"})
    return out


def render_key_results(kr_rows, path):
    lines = [f"KEY RESULTS — {path}"]
    for row in kr_rows:
        written = str(row["kr"].get("now", "")).strip()
        lines.append(f"  {row['project']} / {row['id']}")
        if row["value"] is None:
            lines.append(f"      {path} says now: {written or '(blank)'} — {row['detail']}")
            continue
        drift = "" if _as_number(written) == _as_number(row["value"]) else \
            f"  <- the document says {written or '(blank)'}, drifted"
        lines.append(f"      measured {row['value']}{drift}")
        lines.append(f"      {row['detail']}")
    return "\n".join(lines)


def write_back_key_results(path, text, kr_rows):
    """Put each measured value into its key result's `now:`, in place.

    Same contract as `write_back` one function up, and it is the same contract
    for the same reason: only a key result with an instrument and a different
    written number is touched, so a run that changes nothing writes nothing --
    the caller wraps this in a compare-and-swap against a document the owner
    can edit. A fence the setter refuses is named in the report rather than
    skipped, because a number silently not what it says it is is the whole
    failure here.
    """
    from agora_runner.project_goals import set_field_in_key_result

    lines, changed = [], 0
    for row in kr_rows:
        if row["value"] is None:
            continue
        written = str(row["kr"].get("now", "")).strip()
        if _as_number(written) == _as_number(row["value"]):
            continue
        amended = set_field_in_key_result(text, row["id"], "now", row["value"])
        if amended is None:
            lines.append(f"  ! {row['id']}: could not edit that key-result fence, "
                         f"left at {written or '(blank)'}")
            continue
        text, changed = amended, changed + 1
        lines.append(f"  {row['id']}  now: {written or '(blank)'} -> {row['value']}")
    if not changed:
        head = ("WROTE NOTHING — every instrumented key result already carries its "
                "measured number" if not lines else "WROTE NOTHING")
        return "\n".join([head] + lines)
    try:
        open(path, "w", encoding="utf-8").write(text)
    except OSError as exc:
        return f"COULD NOT WRITE {path}: {exc}"
    return "\n".join([f"WROTE {changed} value(s) into {path}"] + lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=" ".join(__doc__.split("\n\n")[0].split()))
    parser.add_argument("--goals", required=True, help="path to a copy of goals.md")
    parser.add_argument("--days", type=int, default=7,
                        help="length of the window, ending today (default 7)")
    parser.add_argument("--until", default=None,
                        help="last day of the window, YYYY-MM-DD (default today, Oslo)")
    parser.add_argument("--entries", type=int, default=400,
                        help="how many journal entries to read (default 400)")
    parser.add_argument("--site", default=SITE)
    parser.add_argument("--project-goals", default=None,
                        help="path to a copy of project-goals.md; its key results "
                             "are reported too, and written by --write -- the ones "
                             "that share a measure with a goal from that goal, and "
                             "Marcus's from Marcus's own /api/state")
    parser.add_argument("--write", action="store_true",
                        help="write each measured value into the --goals file's "
                             "own `now:` field, in place (default: report only)")
    args = parser.parse_args(argv)

    until = args.until or today_oslo()
    try:
        since = (date.fromisoformat(until) - timedelta(days=args.days - 1)).isoformat()
    except ValueError:
        print(f"--until must be YYYY-MM-DD, got {until!r}", file=sys.stderr)
        return 1

    try:
        text = open(args.goals, encoding="utf-8").read()
    except OSError as exc:
        print(f"could not read {args.goals}: {exc}", file=sys.stderr)
        return 1
    blocks, _ = _fenced(text, {"goal": _goal})
    goals = blocks.get("goal") or []
    if not goals:
        print(f"{args.goals} holds no ```goal fence", file=sys.stderr)
        return 1

    problems = []
    entries, error = fetch_entries(args.entries, site=args.site)
    if error:
        problems.append(error)
    window = in_window(entries, since, until)
    if entries and window and len(window) == len(entries):
        problems.append(
            f"every one of the {len(entries)} entries read falls inside the window, "
            "so the window may reach further back than the entries do")

    boards = []
    for name in ("issues", "ideas"):
        rows, error = fetch_board(name, site=args.site)
        if error:
            problems.append(error)
        boards.append(rows)

    prs, merge_problems = collect_merges(REPOS, since, until)
    problems.extend(merge_problems)

    rows = []
    for goal in goals:
        key = goal_key(goal.get("name"))
        measurer = MEASURERS.get(key)
        if measurer is None:
            why = NO_INSTRUMENT.get(key, "nothing here computes this measure")
            rows.append({"key": key, "goal": goal, "value": None,
                         "detail": f"no instrument — {why}"})
            continue
        value, detail = measurer(window, boards, since, until, prs)
        rows.append({"key": key, "goal": goal, "value": value, "detail": detail})

    # The project-goals document is read and its instruments are gathered
    # BEFORE the goals report is rendered, because a Marcus route that did not
    # answer belongs in that report's `WHAT THIS CANNOT SEE` list -- and a
    # `render` called twice would print the second one over the first, taking
    # the `--write` block with it.
    pg_text, sections, marcus, marcus_error = None, None, None, None
    if args.project_goals:
        from agora_runner.project_goals import parse_project_goals
        try:
            pg_text = open(args.project_goals, encoding="utf-8").read()
        except OSError as exc:
            print(f"could not read {args.project_goals}: {exc}", file=sys.stderr)
            return 1
        sections = parse_project_goals(pg_text)
        if _needs_marcus(sections):
            marcus, marcus_error = fetch_marcus_state()
            if marcus_error:
                problems.append(marcus_error)

    report = render(rows, since, until, problems)
    if args.write:
        report += "\n\n" + write_back(args.goals, text, rows)

    if args.project_goals:
        kr_rows = key_result_rows(sections, rows, marcus, marcus_error,
                                  since, until, prs)
        report += "\n\n" + render_key_results(kr_rows, args.project_goals)
        kpis = kpi_rows(sections, since, until)
        report += "\n\n" + render_kpis(kpis, args.project_goals)
        if args.write:
            report += "\n\n" + write_back_key_results(
                args.project_goals, pg_text, kr_rows)
            # The write above may have just rewritten the file. Re-read it
            # before the second setter runs: handing `write_back_kpis` the text
            # as it was BEFORE that write makes its own edit undo it, and the
            # report would say both wrote.
            try:
                pg_text = open(args.project_goals, encoding="utf-8").read()
            except OSError as exc:
                report += (f"\n\nDID NOT WRITE KPIs — could not re-read "
                           f"{args.project_goals} after the key-result write: {exc}")
            else:
                report += "\n\n" + write_back_kpis(
                    args.project_goals, pg_text, kpis)

    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
