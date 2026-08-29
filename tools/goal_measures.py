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
    """
    try:
        done = subprocess.run(
            ["gh", "pr", "list", "--repo", repo, "--state", "merged",
             "--search", f"merged:{since}..{until}",
             "--limit", str(limit), "--json", "number,mergedAt"],
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

    report = render(rows, since, until, problems)
    if args.write:
        report += "\n\n" + write_back(args.goals, text, rows)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
