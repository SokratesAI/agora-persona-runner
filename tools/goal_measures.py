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
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import yaml
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

# Agora's own public API, unauthenticated on :8080 -- the same host
# `tools.heartbeat_gaps` already reads. It is the only place that knows which
# model every persona, heartbeat and conversation is configured to run on, and
# its `/models` catalog is where `metered` is decided rather than restated.
AGORA = os.environ.get(
    "AGORA_SELF_URL", "http://agora.agents.svc.cluster.local:8080"
)

# The Sokrates Post's own app, unauthenticated, serving every article it has
# ever printed at `/api/articles`. It is the only instrument for how much that
# paper prints; the 113-a-day figure typed into `project-goals.md` came from a
# `curl` at it.
NEWSPAPER = os.environ.get(
    "NEWSPAPER_SELF_URL", "http://newspaper.agents.svc.cluster.local"
)

#: The docs site's own repository. `docs-kpi-staleness` is a liveness
#: guardrail on that site, and the site is published from this repo's default
#: branch, so the newest commit on it is the last time the docs changed.
DOCS_REPO = "SokratesAI/sokrates-docs"


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


def fetch_marcus_subscriber_count(site=MARCUS):
    """How many devices Marcus's push list holds, or `(None, why)`.

    `GET /api/push/subscribers` answers `{"count": N}` and nothing else --
    never an endpoint, never a key. It exists because this measure asked for
    it: until SokratesAI/marcus#160 the only routes that disclosed the count
    were the two halves of `/api/push/subscribe`, so reading it meant adding
    or removing a subscription first, and a measurement that mutates what it
    measures is not one.

    `None` is returned for an unreachable or malformed answer and never
    coerced to 0. Zero subscribers is the reading this KPI most expects to
    take -- it is the state the pod has been in all along -- so "I could not
    ask" must never be written into the document as "nobody is subscribed".
    """
    payload, error = _get_json(f"{site}/api/push/subscribers")
    if error:
        return None, error
    count = (payload or {}).get("count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None, (f"{site}/api/push/subscribers answered without a "
                      "non-negative integer `count`")
    return count, None


def fetch_agora_metered_places(site=AGORA):
    """Every place in Agora configured to run on a metered provider.

    Returns `(places, None)` -- a list of `"<kind> <name> -> <model>"` strings,
    empty when nothing is -- or `(None, why)` when Agora could not be read.

    **What counts as metered is Agora's answer, not a list here.** `/models`
    carries a `metered` flag per model, so this reads that catalog and derives
    the metered *providers* from it rather than pinning model ids: a model id
    can be retired out of the catalog while a stale config still names it, and
    matching on the provider catches that where matching on the id would not.
    `reply.METERED_PROVIDERS` -- the tuple the unattended-turn refusal itself
    uses -- is unioned in, so this instrument can never end up blinder than the
    guard it is watching. If the two ever disagree, the wider set wins here,
    because a false alarm costs a cycle and a miss costs the prepaid balance.

    **Three places, because a run can pick up a model from any of them.** A
    persona carries one, a conversation carries its own and one per persona
    link (which is what actually runs), and a heartbeat carries none today but
    is read anyway so that adding the field later does not silently widen the
    blind spot. Disabled heartbeats and archived conversations are left out:
    they cannot spend.

    **What this cannot see, and it is the whole caveat on the reading.** No
    message records the model that produced it, so this is the configuration
    as it stands right now, not a record of what ran. Spend that happened on a
    config since changed back is invisible to it, and nothing in this loop
    reads the prepaid balance.
    """
    from agora_runner.reply import METERED_PROVIDERS

    catalog, error = _get_json(f"{site}/models")
    if error:
        return None, error
    models = catalog if isinstance(catalog, list) else (catalog or {}).get("models") or []
    providers = {str(m.get("provider") or "").strip()
                 for m in models if m.get("metered")}
    providers |= set(METERED_PROVIDERS)
    providers.discard("")
    if not providers:
        return None, (f"{site}/models named no metered provider at all, which "
                      "is a catalog this cannot judge rather than a clean bill")

    def _metered(model):
        return str(model or "").split(":", 1)[0].strip() in providers

    places = []
    for path, key, kind in (("personas", "personas", "persona"),
                            ("heartbeats", "heartbeats", "heartbeat"),
                            ("conversations?active=true", "conversations",
                             "conversation")):
        payload, error = _get_json(f"{site}/{path}")
        if error:
            return None, error
        rows = payload if isinstance(payload, list) else (payload or {}).get(key) or []
        for row in rows:
            if kind == "heartbeat" and row.get("enabled") is False:
                continue
            if kind == "conversation" and row.get("archived"):
                continue
            name = row.get("name") or row.get("id") or "(unnamed)"
            if _metered(row.get("model")):
                places.append(f"{kind} {name} -> {row.get('model')}")
            for link in row.get("personas") or []:
                if _metered(link.get("model")):
                    places.append(f"{kind} {name} / {link.get('name')} -> "
                                  f"{link.get('model')}")
    return sorted(set(places)), None


def fetch_marcus_coach_latency(site=MARCUS):
    """Marcus's own summary of how long its coach made him wait, or `(None, why)`.

    `GET /api/coach/latency` answers `{count, medianMs, newestAt, oldestAt}`
    and never the individual samples. Marcus records the wall clock around its
    own call to the coach on every draft that *answered*, and keeps the newest
    few hundred on the volume its state lives on.

    Why this and not a sampling run. The reason this KPI carried no instrument
    for a week was that timing the coach meant driving the coach, which is a
    synthetic request measured on an idle pod at 02:00 -- and it spends a real
    model call to learn a number Marcus already had. The server is the process
    that does the waiting, so the honest reading is the one it took on the
    owner's own taps.

    `None` for an unreachable or malformed answer, and `count == 0` is
    *also* returned as `None` by the caller rather than as a duration: no taps
    yet is a real fact about the world, but it is not a latency, and writing a
    0 into the document would say the coach answers instantly.
    """
    payload, error = _get_json(f"{site}/api/coach/latency")
    if error:
        return None, error
    payload = payload or {}
    count = payload.get("count")
    median = payload.get("medianMs")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None, (f"{site}/api/coach/latency answered without a "
                      "non-negative integer `count`")
    if count and not isinstance(median, (int, float)):
        return None, (f"{site}/api/coach/latency reported {count} sample(s) "
                      "with no numeric `medianMs`")
    return {"count": count, "median_ms": median,
            "newest_at": payload.get("newestAt")}, None


def fetch_marcus_coach_outcomes(site=MARCUS):
    """Marcus's own record of whether each coach tap came back usable.

    `GET /api/coach/outcomes` answers `{count, answered, firstTryPct,
    newestAt, oldestAt, byRoute}` and never the individual calls. Marcus
    records one row per call that actually reached the coach, on the volume
    its state lives on.

    Why this and not a sampling run, which is what this key result's written
    "no instrument" prescribed: driving the live coach six times measures the
    six taps this loop just took at 02:00, and the key result is about the
    taps the owner takes. Same argument as `fetch_marcus_coach_latency`
    above, and the same server already knew the answer.

    **A call the coach never saw is not in here at all** -- Marcus refuses an
    unconfigured or metered coach before anything leaves the pod, and counting
    those would read 0% for a deployment where the coach was asked nothing.

    `None` for an unreachable or malformed answer. `count == 0` comes back as
    a real reading here and the caller decides what to do with it, which is
    the same split the latency fetch uses.
    """
    payload, error = _get_json(f"{site}/api/coach/outcomes")
    if error:
        return None, error
    payload = payload or {}
    count = payload.get("count")
    answered = payload.get("answered")
    pct = payload.get("firstTryPct")
    for name, value in (("count", count), ("answered", answered)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return None, (f"{site}/api/coach/outcomes answered without a "
                          f"non-negative integer `{name}`")
    if answered > count:
        return None, (f"{site}/api/coach/outcomes reported {answered} "
                      f"answered of {count} call(s), which cannot be")
    if count and not isinstance(pct, (int, float)):
        return None, (f"{site}/api/coach/outcomes reported {count} call(s) "
                      "with no numeric `firstTryPct`")
    return {"count": count, "answered": answered, "pct": pct,
            "newest_at": payload.get("newestAt"),
            "by_route": payload.get("byRoute") or {}}, None


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


def has_drifted(written, value):
    """Does a measured value disagree with the number written in the document?

    One definition, used by all three renderers and by `--exit-on-drift`, so
    a report that says "drifted" and a status that says "clean" cannot
    disagree. That is the same reason `key_result_rows` reads its number out
    of the goals measurement rather than recomputing it: two derivations of
    one fact drift apart the first time one of them is edited.

    `value is None` is **not** drift. It means the measurer had nothing to
    report -- no instrument at all, or an instrument whose history is still
    empty -- and the written number is then the last honest reading rather
    than a stale one. Raising on it would make every unbuildable instrument
    permanently red, which is the trap `serves_orphans` had to be split out
    of to get into `tools.preflight`.

    A written value that is not a number at all *is* drift: a `now:` that
    says nothing while the instrument answers is exactly the blank this was
    built to fill.
    """
    if value is None:
        return False
    return _as_number(written) != _as_number(value)


def publishes_unconfirmed_number(written, value):
    """The document publishes a number and the instrument had none to offer.

    `has_drifted` calls `value is None` not-drift, and its reason is sound:
    an instrument with an empty history is a thing that fills in, not a
    defect, and raising on it makes every young measure permanently red.
    But that reason assumes the written number is *"the last honest reading"*
    -- and nothing checks that it ever was one.

    Live case this was built on, 2026-09-15: `marcus-kpi-coach-latency`
    carries `now: 14.9` while its measurer reports that Marcus has recorded
    no answered plan draft at all. There is no history for 14.9 to be the
    tail of. It is a typed number, on the `/plan` scoreboard, that no sweep
    could contradict -- the summary counted it in neither the numerator nor
    the denominator and said only that a number with no reading is "not
    counted either way", which is true of a blank `now:` and of this in
    exactly the same words.

    So this separates the two states the old sentence merged:
    nothing published and nothing measured (fine, silent), against a number
    published with nothing behind it (named). It deliberately does **not**
    raise -- see `main`.
    """
    return value is None and _as_number(written) is not None


def kpi_drift_crosses_bounds(kpi, value):
    """A KPI whose written number has drifted -- does the drift change anything?

    Only ever asked of a row `has_drifted` already said yes to. It is the
    second half of that question and it exists because the first half, on a
    KPI, is answered by the clock.

    **Every instrumented KPI here reads a rolling window**, so its number
    moves on its own: `nova-kpi-cost-per-cycle` is a median over the last 24
    hours and `nova-kpi-dropped-ticks` counts firings in the same window, and
    both had drifted again within an hour of Cycle 1576 repairing them in the
    vault -- 1.52 to 1.5, and 2 to 0, with nothing in the system having gone
    wrong. A check that is red every sweep over that is one nobody reads,
    which is the exact reasoning `split_orphans` was built on one document
    over.

    So the line is what a KPI *claims*. Issue #227 defines it as a health
    number with a range rather than a target -- the claim is "this is inside
    its guardrail", not "this digit". While the written and the measured
    number are on the same side of every bound, that claim is still true and
    the digit is a snapshot; the moment they are on different sides, the
    document is reporting a breach that has ended or missing one that has
    started, and that is a real finding.

    Key results get the same treatment against their target rather than a
    range -- see `kr_drift_crosses_target` for why.

    A written `now` that is not a number at all always crosses: a guardrail
    with no reading is not a guardrail, and that is the blank `has_drifted`
    was built to fill rather than something to quieten here.
    """
    from agora_runner.project_goals import kpi_breach

    if value is None:
        return False
    if _as_number(kpi.get("now", "")) is None:
        return True
    return bool(kpi_breach(kpi)) != bool(kpi_breach({**kpi, "now": str(value)}))


def kr_drift_crosses_target(row, value):
    """A key result or goal whose written number has drifted -- does the
    drift change which side of its target it stands on?

    Only ever asked of a row `has_drifted` already said yes to. Until
    2026-09-17 key results had no carve-out, on the reasoning that a target
    is read against the digit. But four of them read a rolling seven-day
    window (G1 and planning's pull requests per closed row, the share of
    blocked entries naming a thread, the share of PRs naming a row), so they
    drifted within an hour of every repair and were hand-repaired four times
    in two days (cycles 1710, 1715, 1718, 1720) with no instrument wrong.
    Issue #227's rule 7 is *"monthly objectives, weekly check"*: the digit is
    checked weekly, by the Monday goals run passing `goal_drift --repair`.
    What cannot wait a week is a number reaching or leaving its target, and
    that still counts here.

    A written `now` that is not a number always crosses, the blank
    `has_drifted` was built to fill. A row with a target but no direction, or
    a target that is not one number, keeps the strict comparison: it is
    malformed, and nothing can say which side it is on.

    **A row with no target at all never crosses**, because there is no target
    to reach or leave, so nothing about it cannot wait for the Monday run. It
    kept the strict comparison until 2026-09-17, and G5 -- the one goal whose
    target is left blank on purpose, a rolling share of journal entries --
    then flapped across a rounding edge: cycle 1731 repaired it 87 -> 88 and
    the sweep three hours later read 87 again (320 of 366 entries), with
    nothing wrong. The cost of this, stated rather than hidden: an instrument
    on a targetless row that collapses is printed on every sweep and raised
    only by the weekly run.
    Design: nova/resources/ideas/rolling-key-results-judged-weekly.md.
    """
    from agora_runner.project_goals import key_result_short

    if value is None:
        return False
    if _as_number(row.get("now", "")) is None:
        return True
    if not str(row.get("target", "")).strip():
        return False
    before = key_result_short(row)
    if before is None:
        return True
    return before != key_result_short({**row, "now": str(value)})


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
        if has_drifted(written, value):
            drift = ("  <- goals.md carries no number"
                     if _as_number(written) is None
                     else f"  <- goals.md says {written}, drifted"
                     if kr_drift_crosses_target(goal, value)
                     else f"  <- goals.md says {written}, moved without "
                          "crossing its target")
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


def measure_pm_calibration(expectations, boards, since, until):
    """Share of shipped items that carried a written, checked expectation.

    The record itself is `agora_runner.expectations` -- before it existed
    there was nothing on this box that stored a prediction, which is why this
    key result's `now` was blank rather than zero. `None` here means the
    document was not handed to this run, or nothing shipped in the window;
    neither is the same as "no instrument", and the detail says which.
    """
    from agora_runner.expectations import measure_calibration
    if expectations is None:
        return None, ("the expectations document was not read -- pass "
                      "--expectations with a copy of it")
    pair = list(boards or [])
    if len(pair) != 2:
        return None, ("both boards are needed for the denominator and "
                      f"{len(pair)} board(s) were read")
    boards_by_kind = {"issue": pair[0], "idea": pair[1]}
    return measure_calibration(expectations, boards_by_kind, since, until)


# A key result measured off the expectations document plus the two boards.
# Its own map for `KEY_RESULT_PR_MEASURERS`' reason: the arguments differ, and
# collapsing three shapes into one signature would mean every measurer taking
# arguments it never reads.
KEY_RESULT_EXPECTATION_MEASURERS = {
    "pm-kr-calibration": measure_pm_calibration,
}


def measure_pm_reversals(decisions, since, until):
    """Decisions reversed inside 30 days of being taken, over the last month.

    The record itself is `agora_runner.decisions` -- before it existed nothing
    on this box stored a decision, so there was nothing to count and this key
    result's `now` was blank. `None` here means the document was not handed to
    this run, which is not the same as "no instrument".

    **The window is 30 days ending at `until`, not the seven-day goals
    window**, because the measure's own unit is *per month*:
    `measure_pm_deprecations` makes exactly the same call for the same reason.
    """
    from agora_runner.decisions import measure_reversals
    del since
    if decisions is None:
        return None, ("the decisions document was not read -- pass "
                      "--decisions with a copy of it")
    until_date = date.fromisoformat(until)
    window_start = until_date - timedelta(days=_REVERSAL_WINDOW_DAYS)
    return measure_reversals(decisions, window_start.isoformat(), until)


#: How far back this measure looks. Deliberately not shared with
#: `decisions.HELD_DAYS`, which is also 30 and means something else entirely --
#: this is the month the count is *reported over*, while that one is how long a
#: decision must hold before a reversal stops counting against it. Folding them into one constant would make a
#: change to either silently change both.
_REVERSAL_WINDOW_DAYS = 30


# A key result measured off the decisions document alone. Its own map, beside
# the three above, for the same reason they are separate: what it is handed is
# the decision record, and feeding that to a measurer registered under `PR`
# would be a map whose name lies about its argument.
KEY_RESULT_DECISION_MEASURERS = {
    "pm-kr-reversals": measure_pm_reversals,
}

def measure_marcus_coach_first_try(since, until):
    """Share of coach taps that came back with a usable answer, as a percent.

    A level like the two Marcus KPIs, and it takes and drops the window for
    the same reason the key-result rows are all called the same way. The
    window it *does* have is the length of Marcus's own history, which is
    reported in the detail rather than imposed here: 100% over 1 tap and 100%
    over 80 are the same number and different readings.

    **No calls yet returns `None`, never 0.** A 0 here says every tap failed,
    which is the opposite of what an empty history means -- the same trap as
    `measure_marcus_coach_latency`, and the opposite direction from
    `measure_marcus_push_subscribers`, where 0 is the real reading.

    "Without a retry" is a fact about the owner rather than about the server,
    and the equivalence that makes this readable is written in Marcus's own
    `coach-outcome.ts`: nothing there or in the page retries on his behalf, so
    one tap is one call and a call that did not answer is a tap he had to take
    again. If anything ever adds a retry, this measure stops meaning what it
    says and the detail line below stops being true.
    """
    del since, until
    summary, error = fetch_marcus_coach_outcomes()
    if error:
        return None, error
    count = summary["count"]
    if count == 0:
        return None, ("Marcus has recorded no coach call yet, so there is no "
                      "share to report -- the route is live and the history "
                      "fills the next time he taps the coach")
    routes = summary["by_route"]
    if isinstance(routes, dict) and routes:
        spread = ", ".join(
            f"{name} {(r or {}).get('answered')}/{(r or {}).get('count')}"
            for name, r in sorted(routes.items()))
    else:
        spread = "no per-route split reported"
    newest = summary.get("newest_at") or "an unrecorded time"
    return summary["pct"], (
        f"{summary['answered']} of {count} coach call(s) Marcus recorded came "
        f"back usable ({spread}), newest at {newest}, read live from "
        "/api/coach/outcomes -- a call the coach never saw, such as an "
        "unconfigured or metered refusal, is not counted either way")



KEY_RESULT_NO_INSTRUMENT = {
    "nova-kr-in-the-app": "counts things the owner still has to leave the Nova "
                          "app to do -- a judgement about his experience, not a "
                          "fact on this box; same reason as G2, which is the "
                          "same measure",
}


#: The window every KPI here is read over. A guardrail says what is happening
#: now, so it is 24 hours regardless of `--days`, which sets the goals' window.
_KPI_WINDOW_HOURS = 24.0

#: How many entries `measure_nova_trust_data_fresh` asks the site for. More
#: than one because `fetch_entries` drops the kinds that are not a cycle's own
#: entry, so a limit of 1 can come back empty on a perfectly healthy site and
#: read as "the view has no cycle in it".
_FRESHNESS_ENTRIES = 20

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


#: A verdict on an entryless cycle that does NOT mean the record is missing.
#: `misfiled` and `unnumbered` are `lost` downgraded *after* the search found
#: the work -- the entry exists, under a number or a name this could not
#: predict -- so counting them would count a cycle that wrote. `still running`
#: is the newest few, which legitimately have no entry yet; three cycles
#: overlap, so counting those would read the cadence as a fault every hour.
SILENT_VERDICTS_NOT_COUNTED = ("misfiled", "unnumbered", "still running")


def measure_nova_silent_cycles(since, until):
    """Cycles in the last 24h that ran and produced no journal entry.

    Reads `tools.cycle_postmortem` rather than re-deriving it, the same call
    `measure_nova_dropped_ticks` makes against `heartbeat_gaps`: that module
    already lists the journal, parses Agora's conversation names into cycle
    numbers, and -- the part that is genuinely hard -- tells a cycle that
    wrote nothing from one whose entry landed under another number. A second
    implementation here would be a second answer to one question, which is
    exactly the drift the whole split exists to stop.

    The window is 24 hours and is NOT the `--days` window the goals use, for
    the reason in `measure_nova_dropped_ticks`: a guardrail averaged over a
    week hides the bad night it exists to catch.

    **A cycle with no conversation is placed by its number, not by a stamp it
    does not have.** An `absent` cycle is a number Agora handed out with no
    record of a run, so `_created` returns nothing for it -- but numbers are
    handed out in order, so any entryless number at or above the lowest
    number whose conversation started inside the window started inside the
    window too. Dropping those instead would silently exclude the one verdict
    that means no run happened at all.
    """
    del since, until
    from datetime import datetime, timedelta, timezone
    from tools import cycle_postmortem

    results, _newest, error, conversations, _paths = cycle_postmortem.collect()
    if error:
        return None, f"cycle_postmortem could not read the loop's history: {error}"
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=_KPI_WINDOW_HOURS)
    started = {number: cycle_postmortem._created(conversation)
               for number, conversation in (conversations or {}).items()}
    in_window = [number for number, opened in started.items()
                 if opened is not None and opened >= cutoff]
    if not in_window:
        return None, (f"no cycle conversation was opened in the last "
                      f"{_KPI_WINDOW_HOURS:g}h, so there is no window to count "
                      "over -- that is a loop that stopped, not a clean zero")
    first = min(in_window)
    counted = [row for row in results
               if row["number"] >= first
               and row.get("verdict") not in SILENT_VERDICTS_NOT_COUNTED]
    excused = [row for row in results
               if row["number"] >= first
               and row.get("verdict") in SILENT_VERDICTS_NOT_COUNTED]
    detail = (f"{len(counted)} of {len(in_window)} cycle(s) that ran in the last "
              f"{_KPI_WINDOW_HOURS:g}h wrote no journal entry")
    if counted:
        detail += " (" + ", ".join(
            f"{row['number']} {row.get('verdict')}"
            for row in sorted(counted, key=lambda r: r["number"])) + ")"
    if excused:
        detail += (f"; {len(excused)} more entryless number(s) are not counted "
                   "-- their record exists, or they have not finished")
    return len(counted), detail


#: The window `measure_pm_deprecations` reads over. Its `measure:` field says
#: "per month", so the window IS the unit -- 30 days, not the `--days` window
#: the goals and key results share.
_DEPRECATION_WINDOW_DAYS = 30



def measure_nova_trust_data_fresh(since, until):
    """How many cycles behind the journal view in the app is. Cycles.

    The Trust objective says he should see something current any time he
    opens the app, and until now that key result was blank with the note
    "nothing here computes this measure". Two independent statements of the
    same fact already exist, so this reads both and subtracts: the newest
    `<seq>-cycle-<n>.md` in the vault's journal folder, and the newest cycle
    number `nova-site` actually serves at `/api/journal`. The difference is
    the age of the newest data in the view, in cycles, which is the unit the
    key result asks for.

    **This is the publish path, not the writing path.** A cycle that wrote no
    entry at all is not staleness here and is deliberately somebody else's
    measure -- `nova-kpi-silent-cycles` counts those. What this catches is an
    entry that exists in the vault and is not on his phone: a cache that
    stopped revalidating, a pod that did not roll, a listing the site cannot
    read. Both readings move together on a healthy loop, so the normal answer
    is 0.

    **`None` rather than a number whenever either side is unreadable**, and
    that guard is the point of the function rather than a nicety. The target
    is 1 and the direction is down, so 0 is the *best* value this key result
    can carry -- a vault client that is missing, or a site that answers
    nothing, would otherwise publish a perfect score off an instrument that
    read nothing, and that number would outlive the outage.

    **A negative difference is also `None`.** The site serving a cycle the
    journal listing does not have yet means the listing is the stale side of
    the comparison, so there is no age to report off it; saying so is honest
    and clamping it to 0 would hide a read I cannot trust.

    What it cannot see: whether the page *renders* what the API returns. The
    plan half of "planned vs. done" is `nova-kr-trust-cycles-shown`'s, read
    off `/api/planned` by `measure_nova_trust_cycles_shown`.
    """
    del since, until
    from agora_runner.nova_journal import file_cycle
    from agora_runner.recap_refresh import newest_entry
    from tools import recap_health

    try:
        listing = recap_health._vault("ls", recap_health.JOURNAL_DIR)
    except (subprocess.CalledProcessError, OSError, FileNotFoundError) as exc:
        return None, (f"the journal folder could not be listed, so there is "
                      f"nothing to compare the app against -- {str(exc)[:160]}")
    names = [line.strip() for line in listing.splitlines() if line.strip()]
    written = file_cycle(newest_entry(names) or "")
    if written is None:
        return None, (f"{recap_health.JOURNAL_DIR} listed {len(names)} name(s) "
                      "and none of them is a <seq>-cycle-<n>.md entry, so there "
                      "is no newest cycle to compare against")
    entries, error = fetch_entries(_FRESHNESS_ENTRIES)
    if error:
        return None, (f"the app's journal view could not be read, so its age "
                      f"is unknown rather than 0 -- {error}")
    shown = max((e.get("cycle") for e in entries
                 if isinstance(e.get("cycle"), int)), default=None)
    if shown is None:
        return None, (f"{SITE}/api/journal answered with {len(entries)} "
                      "entry/entries and no cycle number among them, so there "
                      "is no newest shown cycle to date the view by")
    if written < shown:
        return None, (f"the app is serving cycle {shown} and the journal "
                      f"folder's newest entry is {written}, so the listing I "
                      "compared against is the stale side -- no age to report")
    return written - shown, (
        f"the app's journal view is dated by cycle {shown}; the newest entry "
        f"in the vault is cycle {written}, over {len(names)} file(s) listed")


def measure_nova_trust_cycles_shown(since, until):
    """`nova-kr-trust-cycles-shown` -- the share of cycles the planned vs. done
    view shows, read off the view itself (`/api/planned`, idea #312).

    The number is recomputed from `shown` and `total` rather than taken from
    the payload's `share`, and a payload whose counts cannot be a share is no
    reading: 0 is this key result's worst value and 100 its target, so a page
    that answered nothing must not publish either.

    What it cannot see: a cycle older than the window, and whether a cycle
    that is still running will write. The view starts at the newest entry, so
    an unfinished cycle is not counted as a gap here either.
    """
    del since, until
    payload, error = _get_json(f"{SITE}/api/planned")
    if error:
        return None, (f"the planned vs. done view could not be read, so the "
                      f"share is unknown rather than 0 -- {error}")
    if not isinstance(payload, dict):
        return None, f"{SITE}/api/planned answered something that is not an object"
    shown, total = payload.get("shown"), payload.get("total")
    if not (isinstance(shown, int) and isinstance(total, int)
            and 0 <= shown <= total):
        return None, (f"{SITE}/api/planned answered shown={shown!r} "
                      f"total={total!r}, which is not a share")
    if total == 0:
        return None, (f"{SITE}/api/planned holds no cycle in its window, so "
                      "there is no share to take")
    days = payload.get("windowDays")
    since_cycle = payload.get("historyFromCycle")
    return round(100.0 * shown / total, 1), (
        f"{shown} of {total} cycle(s) in the last {days} days wrote a journal "
        f"entry, read live from /api/planned; plans are kept from cycle "
        f"{since_cycle} on")


#: The three agent kinds his Control objective names, and the one Nova-app
#: route each one's stop control would have to post to. `nova-kr-control-stop-
#: coverage` is a share of these three, so the denominator is this table and
#: the numerator is how many of them the app actually carries today.
#:
#: **Marcus's route was named here before anything served it**, so that
#: shipping the control under exactly that path lifted the number by itself
#: (issue #239 did, 66.7 -> 100), and a control shipped under a different path
#: is a miss the detail line prints in full rather than swallowing.
STOP_CONTROLS = {
    "cycles": ("/api/conversations/cancel",
               "the Stop button a running turn's Send button becomes"),
    "heartbeats": ("/api/heartbeats/enabled",
                   "the per-heartbeat on/off switch"),
    "marcus": ("/api/marcus/stop",
               "the Stop Marcus card on the Beats page"),
}

#: The Nova app's browser bundle, and the module that serves it. A stop control
#: is two things -- a route the server answers and a button the browser posts
#: to it -- so both files are read and both halves are required.
_SITE_MODULE = "agora_runner/nova_site.py"
#: The browser runs several hand-written files since issue #233 started
#: splitting `app.js`. A control that lives in the dock or behind the attach
#: button is in one of the others, so reading only `app.js` would report it
#: as missing.
_APP_BUNDLE = "agora_runner/nova_public/app.js"
_APP_BUNDLE_PARTS = (_APP_BUNDLE, "agora_runner/nova_public/chat-dock.js",
                     "agora_runner/nova_public/attach.js",
                     "agora_runner/nova_public/charts.js",
                     "agora_runner/nova_public/diag.js",
                     "agora_runner/nova_public/beats.js",
                     "agora_runner/nova_public/notes.js",
                     "agora_runner/nova_public/home.js",
                     "agora_runner/nova_public/plan.js",
                     "agora_runner/nova_public/steps.js",
                     "agora_runner/nova_public/ask.js",
                     "agora_runner/nova_public/models.js",
                     "agora_runner/nova_public/richtext.js",
                     "agora_runner/nova_public/bubble.js",
                     "agora_runner/nova_public/project.js")

#: Below this many POST routes, the allowlist parse below has found something
#: that is not the allowlist. There are over thirty today; the number is a
#: floor on "this is plainly the real tuple", not a target.
_MIN_POST_ROUTES = 10


def _repo_file(relative):
    return _pathlib.Path(__file__).resolve().parents[1] / relative


def post_routes(source):
    """Every path `nova_site`'s `do_POST` will answer, from its own allowlist.

    Parsed rather than grepped. `do_POST` refuses anything outside one literal
    tuple of paths, so that tuple *is* the served set -- and a `grep` for a
    path string would also match its handler's docstring, a comment, or the
    GET table, which is the failure this whole check exists to avoid on the
    browser side as well.
    """
    import ast

    tree = ast.parse(source)
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Tuple, ast.List)):
            continue
        values = [e.value for e in node.elts
                  if isinstance(e, ast.Constant) and isinstance(e.value, str)]
        if len(values) != len(node.elts) or not values:
            continue
        if all(v.startswith("/api/") for v in values):
            found.update(values)
    return found


def _bundle_posts_to(bundle_text, route):
    """Does the browser bundle name this route as a string it calls?

    The route has to appear in quotes. A path written in a comment -- and
    `app.js` has thousands of lines of them -- is prose, and prose is exactly
    what a bare substring search would count as a shipped button.
    """
    return (f'"{route}"' in bundle_text) or (f"'{route}'" in bundle_text)


def measure_nova_control_stop_seconds(since, until, runner=subprocess.run,
                                      tool=None):
    """Median seconds his Stop button took to stop a turn, newest five. Seconds.

    Reads the ledger `nova_site` appends to on every Stop that reached a
    running turn (`agora_runner.nova_stop_timings`, issue #240). A level over
    his newest attempts rather than a window, so it drops the window.

    **No number until five stops are recorded**, and never 0: 0 is this
    measure's best value, and an unreadable or empty ledger would otherwise
    publish a perfect stop time off attempts nobody made.
    """
    del since, until
    from agora_runner.nova_stop_timings import (STOP_TIMINGS_PATH, load,
                                                median_of_newest)
    tool = tool or VAULT_TOOL             # defined further down this module
    try:
        done = runner([sys.executable, tool, "get", STOP_TIMINGS_PATH],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not read {STOP_TIMINGS_PATH}: {exc}"
    out = done.stdout or ""
    if done.returncode != 0:
        return None, (f"{tool} get {STOP_TIMINGS_PATH} exited "
                      f"{done.returncode}: {(done.stderr or '').strip()[:200]}")
    if out.startswith("[not found"):
        return None, ("no stop has been recorded yet -- the ledger is written "
                      "the first time his Stop button reaches a running turn")
    try:
        rows = load(out)
    except ValueError as exc:
        return None, f"{STOP_TIMINGS_PATH} could not be parsed: {exc}"
    return median_of_newest(rows)


def measure_nova_control_stop_coverage(since, until):
    """Share of his three agent kinds with a stop control in the Nova app.

    A level -- what the app carries right now -- so it takes the window and
    drops it, the same as every other measurer here.

    **A stop control is two halves and both are checked separately**, because
    either half alone is a plausible wrong answer. A route `do_POST` answers
    with no button anywhere is a thing only a `curl` can reach, which is not a
    control on his phone; a button posting at a path the server 404s is a
    control that cannot succeed. So the route must be in `do_POST`'s own
    allowlist *and* the browser bundle must name it in quotes.

    **The failure this refuses to report is a dead scanner reading 0.** The
    target here is 100 and up, so 0 is the worst value on the scale and would
    outlive the fix -- and both halves fail silently in the same direction: an
    allowlist this cannot parse yields no routes, a bundle it cannot read
    contains no strings, and either one prints a confident, tidy 0%. So each
    half has to be shown to match something live first: the allowlist has to
    parse to a plausible number of routes, and the bundle has to post to at
    least one route that allowlist actually serves. If either check comes back
    empty the answer is no number, with the reason.
    """
    del since, until
    try:
        source = _repo_file(_SITE_MODULE).read_text()
        bundle = "\n".join(_repo_file(p).read_text() for p in _APP_BUNDLE_PARTS)
    except OSError as e:
        return None, (f"could not read the app's own source ({e}), so nothing "
                      "here knows which controls it carries")
    try:
        served = post_routes(source)
    except SyntaxError as e:
        return None, f"{_SITE_MODULE} did not parse ({e}), so no route list"
    if len(served) < _MIN_POST_ROUTES:
        return None, (f"{_SITE_MODULE} yielded only {len(served)} POST "
                      f"route(s), under the {_MIN_POST_ROUTES} this expects -- "
                      "that is the allowlist not being found, not the app "
                      "having lost its routes")
    wired = {r for r in served if _bundle_posts_to(bundle, r)}
    if not wired:
        return None, (f"{_APP_BUNDLE} names none of the {len(served)} served "
                      "routes in quotes, so the browser half of this check "
                      "read nothing -- every kind would score 0 whatever the "
                      "app carries")

    have, missing = [], []
    for kind in sorted(STOP_CONTROLS):
        route, what = STOP_CONTROLS[kind]
        if route in served and route in wired:
            have.append(f"{kind} ({route}, {what})")
        elif route in served:
            missing.append(f"{kind}: {route} is served but no button posts to it")
        elif route in wired:
            missing.append(f"{kind}: a button posts to {route} and do_POST 404s it")
        else:
            missing.append(f"{kind}: no {route} -- {what}")

    share = round(100.0 * len(have) / len(STOP_CONTROLS), 1)
    detail = (f"{len(have)} of {len(STOP_CONTROLS)} agent kinds have a stop "
              f"control served by do_POST and posted to by the app: "
              + "; ".join(have))
    if missing:
        detail += ". Missing: " + "; ".join(missing)
    detail += (f" (read off {_SITE_MODULE}'s own POST allowlist, "
               f"{len(served)} route(s), {len(wired)} of them named in "
               f"{_APP_BUNDLE})")
    return share, detail


#: First-person phrasings a cycle uses when it says, in its own journal entry,
#: that it is blocked on the owner. Lowercased, matched against `entry_text`.
#: **This is a floor and can never be a ceiling**, the same as G3's correction
#: phrases: a cycle that was blocked and found a wording not on this list is
#: counted in neither half, so it drops out of the measure entirely rather than
#: landing in the unrecorded side. That is the safe direction -- it understates
#: the denominator, never the numerator.
BLOCK_PHRASES = (
    "waiting on you",
    "waiting on him",
    "waiting for you",
    "blocked on you",
    "blocked on him",
    "waits on you",
    "waits on his",
    "needs your",
    "needs edvard",
)

#: Deliberately **not** on the list above: `"needs input"`. It is the name of a
#: section of the digest, not something a cycle says about itself, so it
#: matched every entry that merely discussed that section -- and the section is
#: one the owner killed on 2026-09-13 (*"I still see the needs input boxes"*),
#: so
#: the entries discussing it are mostly about retiring it. Measured over
#: 2026-09-10..2026-09-16: four entries matched it and nothing else, all four
#: were talking about the section, and none of them named an ask. It added 4 to
#: the denominator and 0 to the numerator, which is the one direction the
#: comment above says cannot happen -- a floor that overstates its own
#: denominator is not a floor. Dropping it moved the live reading 15.1 -> 16.3.

#: How many journal entries `measure_nova_scale_blocks_recorded` asks for. The
#: window is applied afterwards by `in_window`, so this only has to be deep
#: enough that a `--days` window is fully inside it.
_BLOCKS_ENTRIES = 400


def _live_ask_ids():
    """(ids, problem, blind) -- every thread this loop has open a question in.

    Two kinds, because `ask_watch` learned the hard way that there are two
    (runner#1157): a thread `needs_input` opened, tagged `nova:needs-input` or
    named with its prefix, and a goal discussion `project_goal_thread` opened,
    which carries neither and is known only because `project-goals.md` names it
    against a project still `discussing`. Reading the tag alone left `0af15d7d`
    -- the Cycles/Planning/Vault store goal thread, open and waiting on him --
    out of the set, so a block naming only that thread would score as one with
    nothing he could answer. Measured Cycle 1701 over 09-10..09-16: the one
    block naming it also named `0256140f`, so no reading had moved yet.

    `blind` is why the goal half was not read, or None. It is not a `problem`:
    the tagged half still stands on its own and only reads low without the
    other, so the share stays a floor and the detail names what went unread.

    Archived threads are outside `?active=true`, which is the same blindness
    `ask_watch` has and for the same reason: an archived ask is his "I am done
    with this". It biases this measure low, never high, and the detail says so.
    """
    from agora_runner.http_util import agora_get
    from agora_runner.needs_input import NAME_PREFIX, NEEDS_INPUT_TAG

    status, body = agora_get("/conversations?active=true")
    if status != 200:
        return None, f"the conversation listing returned HTTP {status}", None
    ids, listed = set(), set()
    for row in (body or {}).get("conversations") or []:
        cid = str(row.get("id") or "")
        if not cid:
            continue
        listed.add(cid)
        tagged = NEEDS_INPUT_TAG in (row.get("tags") or [])
        named = str(row.get("name") or "").startswith(NAME_PREFIX)
        if tagged or named:
            ids.add(cid)
    goal_ids, blind = _goal_thread_ids(listed)
    return ids | goal_ids, None, blind


def _goal_thread_ids(listed):
    """(ids, blind) -- the listed threads `project-goals.md` argues a goal in.

    Resolved with `match_thread_id`, the same resolver `ask_watch` uses, since
    the document writes an id in full or as its first eight characters.
    """
    from agora_runner.project_goals import match_thread_id
    from tools import ask_watch

    markdown, problem, _here = ask_watch.read_goals()
    if markdown is None:
        return set(), problem
    ids = set()
    for _project, written, _pending in ask_watch.goal_discussions(markdown):
        resolved = match_thread_id(written, listed)
        if resolved is not None:
            ids.add(resolved)
    return ids, None


def measure_nova_scale_blocks_recorded(since, until):
    """Share of the cycles that said they were blocked on him which named an ask.

    A block is only *recorded* if there is something he can answer. The record
    is an Agora ask thread -- `agora_runner.needs_input` opens one, names it
    after the question and buzzes his phone -- and the thing that ties a block
    to its record is the thread id written into the journal entry.

    **The two halves come from two different stores on purpose.** The
    denominator is prose the site serves; the numerator is a thread id that has
    to exist in Agora. A cycle writing "waiting on you" and no id counts
    against itself, which is the whole point of the key result: a block nobody
    can answer is not on record.

    **Both halves are proved to match something live before any share is
    taken**, because each of them fails silently toward 0 and 0 is the worst
    value on a target-100 measure. An empty phrase match is no number rather
    than a denominator of zero, and a store with no ask thread in it at all is
    no number rather than a numerator of zero -- a listing that came back
    without the tag, or a token this pod does not hold, would otherwise publish
    a confident 0% that outlives the fix.
    """
    entries, problem = fetch_entries(_BLOCKS_ENTRIES)
    if problem:
        return None, f"could not read the journal ({problem}), so no block list"
    window = in_window(entries, since, until)
    blocked = [e for e in window
               if any(p in entry_text(e) for p in BLOCK_PHRASES)]
    if not blocked:
        return None, (f"no entry in {since}..{until} says it was blocked on "
                      f"you, over {len(window)} entry/entries in the window -- "
                      "there is no share to take, and calling that 0% would "
                      "report the best week as the worst")
    ids, problem, blind = _live_ask_ids()
    if problem:
        return None, (f"{problem}, so the recorded half read nothing and every "
                      "block would score unrecorded")
    if not ids:
        return None, ("the conversation listing holds no ask thread at all, "
                      "which is the tag or the name not being found rather "
                      "than every block going unrecorded")
    prefixes = {cid[:8] for cid in ids}
    recorded, unrecorded = [], []
    for entry in blocked:
        text = entry_text(entry)
        hit = next((cid for cid in sorted(ids)
                    if cid in text or cid[:8] in text), None)
        title = str(entry.get("title") or entry.get("date") or "an entry")
        # Stamped with the cycle number, because journal titles repeat: the
        # live list read "Goals still waiting on you, so nothing was built;
        # Goals still waiting on you, so nothing was built" and there was no
        # way to tell which two entries that was, or whether it was one entry
        # counted twice. The date does not separate them either -- those two
        # are cycles 1649 and 1650, both on 2026-09-15 -- and the cycle number
        # is the handle you can actually open the entry with.
        stamp = str(entry.get("cycle") or "").strip()
        (recorded if hit else unrecorded).append(
            f"{title} (cycle {stamp})" if stamp else title)
    share = round(100.0 * len(recorded) / len(blocked), 1)
    detail = (f"{len(recorded)} of {len(blocked)} entry/entries in "
              f"{since}..{until} that say they are blocked on you name a live "
              f"ask thread, out of {len(window)} in the window and "
              f"{len(ids)} open ask(s) ({', '.join(sorted(prefixes))})")
    if unrecorded:
        detail += ". Not on record: " + "; ".join(unrecorded[:5])
        if len(unrecorded) > 5:
            detail += f"; and {len(unrecorded) - 5} more"
    detail += (" -- a floor twice over: the phrases are a fixed list, and an "
               "ask he has archived is outside the listing this reads")
    if blind:
        detail += (f"; goal discussion threads were not read ({blind}), so a "
                   "block naming one of those scored unrecorded")
    return share, detail


def measure_pm_deprecations(since, until):
    """Rows closed as `outdated` on either board in the last 30 days.

    The reason this KPI carried no instrument was recorded as "there is no
    deprecation marker anywhere in these repos to count". That was wrong, and
    the marker is one the owner asked for himself: `nova_boards.OUTDATED_STATUS`
    is a first-class board status, `statusKey` is `outdated`, and the site
    serves it on every row of both boards. 93 rows carry it today.

    **`updated` is a last-touch date, not a status-change date, and that is
    the whole caveat.** Nothing on a row records when its status moved, so a
    row retired in July and edited in September reads as September. In
    practice a closed row is not edited again -- closing it is the last thing
    that happens to it -- so this is the retirement date for almost every row
    and it is the only date on the record. It is stated here rather than left
    for a later cycle to rediscover, the same way `measure_pm_written_why`
    states that its number is a floor.

    A bare `MM-DD` is read against the year of the window it is tested in,
    which is `_iso_in_year`'s existing rule and the only year it can mean on
    a board that rolls forward. A row whose `updated` will not parse is not
    counted and is not an error: an undated row is not evidence of a
    retirement inside the window.
    """
    del since
    until_date = date.fromisoformat(until)
    window_start = until_date - timedelta(days=_DEPRECATION_WINDOW_DAYS)
    counted = []
    for name in ("issues", "ideas"):
        items, error = fetch_board(name)
        if error:
            return None, error
        for row in items:
            if (row.get("statusKey") or "") != "outdated":
                continue
            stamp = _iso_in_year(row.get("updated"), until_date.year)
            if not stamp:
                continue
            try:
                when = date.fromisoformat(stamp)
            except ValueError:
                continue
            if window_start < when <= until_date:
                counted.append((name, row.get("number"), stamp))
    detail = (f"{len(counted)} row(s) closed as Outdated across both boards in "
              f"the {_DEPRECATION_WINDOW_DAYS}d window "
              f"{window_start.isoformat()}..{until}")
    if counted:
        shown = ", ".join(f"{n} #{num} {stamp}" for n, num, stamp in counted[:8])
        detail += f" ({shown}" + (", ..." if len(counted) > 8 else "") + ")"
    detail += ("; dated by the row's `updated` cell, which is the last touch "
               "rather than the status change -- there is no status-change "
               "date on a record")
    return len(counted), detail


#: A KPI whose number is measured here. Each measurer takes `(since, until)`
#: -- the goals' window, which a KPI is free to ignore and this one does -- and
#: returns `(value, detail)`, or `(None, why)` when it could not read what it
#: needed. Same contract as `KEY_RESULT_MEASURERS`, deliberately, because the
#: failure it protects against is the same one: a number nobody can recompute.
def measure_marcus_push_subscribers(since, until):
    """Devices the 20:00 reminder can actually reach, right now.

    A level and not a rate, so it has no window at all -- the subscription
    list is the state of the world at the moment it is read, and averaging it
    over 24h would answer a question nobody asked. `since` and `until` are
    taken and dropped for the same reason every other KPI measurer takes
    them: `kpi_rows` calls them all the same way.

    The reading this most expects to take is **0**, and that is the whole
    point of the guardrail: Marcus sends a reminder at 20:00 to whoever is on
    this list, and an empty list means the job runs nightly and delivers to
    nobody. A 0 here is a real measurement and gets written; an unreadable
    pod is not, and returns `None` so the document keeps saying it does not
    know.
    """
    del since, until
    count, error = fetch_marcus_subscriber_count()
    if error:
        return None, error
    if count == 0:
        return 0, ("Marcus's push list is empty, so the 20:00 reminder "
                   "delivers to nobody -- the job still runs")
    return count, (f"{count} device(s) on Marcus's push list, read live from "
                   "/api/push/subscribers")


def measure_marcus_coach_latency(since, until):
    """How long a plan draft actually made him wait, in seconds.

    A level like `measure_marcus_push_subscribers` above, and it takes and
    drops the window for the same reason: `kpi_rows` calls every measurer the
    same way. The window it *does* have is the length of Marcus's own history,
    which is reported in the detail line rather than imposed here -- a median
    over 2 taps and a median over 60 are the same number and different
    readings, exactly as `measure_pm_reversals` prints its count beside the
    size of the record.

    **No taps yet returns `None`, never 0.** A latency of zero would say the
    coach answers instantly, which is the opposite of what an empty history
    means, and this is the same trap `fetch_marcus_subscriber_count` avoids in
    the other direction -- there 0 is the real reading and `None` is the
    failure; here 0 cannot be a real reading at all.
    """
    del since, until
    summary, error = fetch_marcus_coach_latency()
    if error:
        return None, error
    count = summary["count"]
    if count == 0:
        return None, ("Marcus has recorded no answered plan draft yet, so "
                      "there is no wait to report -- the route is live and "
                      "the history fills the next time he taps Draft")
    seconds = round(summary["median_ms"] / 1000.0, 1)
    newest = summary.get("newest_at") or "an unrecorded time"
    return seconds, (f"median of {count} answered plan draft(s) Marcus timed "
                     f"itself, newest at {newest}, read live from "
                     "/api/coach/latency")


def measure_nova_unfixed_advisories(since, until):
    """Security advisories across the org whose fix is not on main yet.

    Asks `tools.security_alerts` rather than counting again, the same call
    `measure_nova_silent_cycles` makes against `cycle_postmortem`: that module
    already enumerates every non-archived repo in every org a checkout names,
    folds in the org-level view a per-repo read cannot see, and -- the part
    that is genuinely hard -- checks each open alert's patched version against
    the lockfile on the default branch, so an alert GitHub has not re-scanned
    since the fix merged is not counted as work.

    A level rather than a rate, so it takes and drops the window: an advisory
    is open now or it is not, and averaging that over 24h answers nothing.

    **This is a floor and says so, because `Dependabot disabled` is not zero
    alerts.** `SokratesAI/platform-config` and `SokratesAI/vault` both have it
    switched off today, and a repo that cannot be asked is no instrument
    rather than a clean answer. Returning `None` whenever any repo is
    unreadable would leave this guardrail permanently blank over a state
    nobody is going to change, so the count is over the repos that answered
    and the detail names how many did not. What does return `None` is a sweep
    that could not build its own repo list at all -- then the denominator is
    unknown too, and a floor over an unknown set is not a reading.
    """
    del since, until
    from tools import security_alerts

    repos, _unplaceable, _notes, incomplete = security_alerts._repos_to_sweep()
    if incomplete or not repos:
        return None, ("could not enumerate the repos to sweep, so there is no "
                      "set to count over -- `python3 -m tools.security_alerts` "
                      "prints why")
    results = {repo: security_alerts.alerts_for(repo) for repo in repos}
    org_views = {org: security_alerts.org_alerts(org)
                 for org in security_alerts._orgs_from_workspace()}
    security_alerts.fold_in_org_only(results, org_views)
    security_alerts.verify_landed(results)

    actionable, unreadable = [], []
    for repo, (state, payload) in sorted(results.items()):
        if state != security_alerts.OK:
            unreadable.append(repo)
            continue
        for alert in payload:
            if not security_alerts._still_counts(alert):
                continue
            if alert.get("landed"):
                continue
            actionable.append(f"{repo} {alert['package']} ({alert['severity']})")
    detail = (f"{len(actionable)} advisory(ies) with no fix on the default "
              f"branch, across {len(results) - len(unreadable)} of "
              f"{len(results)} repo(s)")
    if actionable:
        detail += " (" + ", ".join(actionable[:6])
        detail += ", ..." if len(actionable) > 6 else ""
        detail += ")"
    if unreadable:
        detail += (f"; a floor, not a total -- {len(unreadable)} repo(s) could "
                   f"not be asked ({', '.join(unreadable)})")
    return len(actionable), detail


def measure_nova_markdown_board_readers(since, until):
    """Modules that still parse board markdown instead of the record store.

    The guardrail under `Board store and growth`, and it is a **ratchet**: this
    number may fall and must never rise. Issue #203 put a record store beside
    his two boards and the switchover is unfinished, so for as long as both
    exist the live risk is a *new* reader being written against the markdown --
    which costs nothing today and has to be undone later, by somebody who was
    not here. `tools.board_reader_inventory` already answers this exactly, gate
    and vetoes included, so this asks it rather than re-deriving the scan, the
    same call `measure_nova_silent_cycles` makes against `cycle_postmortem`.

    A level, so it takes and drops the window.

    **Why this is a KPI and not a key result, since the number's whole story is
    that it should reach zero.** Rule 4 of issue #227 says a KPI carries a
    range and never a target, and the honest range here is `0..<today's
    count>`: finishing #203 is a milestone with its own definition of done, and
    what no milestone can catch is the twelfth module appearing while the
    eleven are still there. The ceiling is the reading taken the day this was
    written, declared once, in the document -- nothing here may move it, for
    the reason `set_field_in_kpi` only writes `now`.

    `include_tests=False` on purpose: a test that parses markdown is testing
    the parser, and counting it would make the gate unclearable while
    `parse_board` still has tests, which it must until it is deleted.
    """
    del since, until
    from tools import board_reader_inventory

    found, _refs, unreadable, untokenized, mine, vetoed = \
        board_reader_inventory.scan(include_tests=False)
    if unreadable or untokenized:
        return None, (f"{len(unreadable) + len(untokenized)} file(s) could not "
                      "be read or tokenized, so this count is over an unknown "
                      "set -- `python3 -m tools.board_reader_inventory` says "
                      "which")
    names = sorted(found)
    detail = (f"{len(names)} module(s) still parse board markdown "
              f"({', '.join(names[:8])}" + (", ..." if len(names) > 8 else "")
              + "); a ratchet, so it may fall and must never rise")
    if mine:
        detail += (f"; {len(mine)} reader(s) of Nova's own documents rather "
                   "than his boards are counted separately by the inventory "
                   "and not here")
    if vetoed:
        detail += f"; {len(vetoed)} vetoed"
    return len(names), detail
MARCUS_BROWSER_DIR = "public"
MARCUS_REPO = "SokratesAI/marcus"


def _browser_files(repo, directory):
    """List the JS files served straight to the browser, with their sizes.

    One `gh api` call against the contents endpoint, non-recursive on purpose:
    a `vendor/` full of third-party libraries would otherwise decide this
    number, and a vendored bundle is not code anybody here wrote.
    """
    try:
        done = subprocess.run(
            ["gh", "api", f"repos/{repo}/contents/{directory}", "--jq",
             '[.[] | select(.type == "file") | {name, size}]'],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh api could not list {repo}/{directory}: {exc}"
    if done.returncode != 0:
        return None, (f"gh api failed on {repo}/{directory}: "
                      f"{done.stderr.strip()[:200]}")
    try:
        entries = json.loads(done.stdout or "[]")
    except json.JSONDecodeError as exc:
        return None, f"gh api returned unreadable JSON for {repo}: {exc}"
    return [e for e in entries if str(e.get("name", "")).endswith(".js")], None


def _marcus_browser_files():
    return _browser_files(MARCUS_REPO, MARCUS_BROWSER_DIR)


def _largest_browser_file(repo, directory, files, error):
    """The biggest file of `files` in kilobytes, or `None` with the reason."""
    if error:
        return None, error
    if not files:
        return None, (f"{repo}/{directory} holds no .js file, "
                      "so there is no browser code to size -- that is a moved "
                      "directory rather than a reading")
    biggest = max(files, key=lambda e: e.get("size") or 0)
    size_kb = round((biggest.get("size") or 0) / 1000.0)
    others = ", ".join(f"{e['name']} {round((e.get('size') or 0) / 1000.0)}KB"
                       for e in sorted(files, key=lambda e: -(e.get("size") or 0))[1:4])
    detail = (f"{biggest['name']} is {size_kb}KB, the largest of "
              f"{len(files)} hand-written browser file(s) in "
              f"{directory}/; a ratchet, so it may fall and must "
              "never rise")
    if others:
        detail += f" (next: {others})"
    return size_kb, detail


def measure_marcus_browser_monolith(since, until):
    """The biggest hand-written browser file in Marcus, in kilobytes.

    The guardrail under `Codebase health`, the last lights-on milestone on
    either of his boards that named no KPI at all. It is a **ratchet**: it may
    fall and must never rise, and `high` is the reading taken the day it was
    written -- nothing here may move it, for the same reason
    `set_field_in_kpi` only ever writes `now`.

    A level, so it takes and drops the window: a file is this big now or it is
    not, and averaging that over 24h answers nothing.

    Why this number rather than a test count or a coverage percentage. Marcus
    has no bundler and no modules by choice -- idea #213 decided that at 92 KB
    on 2026-09-01, deliberately before the LLM chat landed, because *"that row
    adds a whole subsystem ... and it will land in the same file"*. What a
    milestone cannot catch is the same file quietly growing back, which costs
    nothing on the day and is paid by whoever opens it next.

    Kilobytes, decimal, because 92 KB is the unit the decision itself was
    written in. Non-`.js` files are skipped: the page's CSS and HTML are not
    where a subsystem hides.

    **No readable listing returns `None`, never 0.** Zero kilobytes of browser
    code would say Marcus has no front end, which is the opposite of what a
    failed `gh` call means.
    """
    del since, until
    files, error = _marcus_browser_files()
    return _largest_browser_file(MARCUS_REPO, MARCUS_BROWSER_DIR, files, error)


NOVA_BROWSER_DIR = "agora_runner/nova_public"
NOVA_REPO = "SokratesAI/agora-persona-runner"


def measure_nova_browser_monolith(since, until):
    """The biggest hand-written browser file in the Nova app, in kilobytes.

    The guardrail kept by `Framework rewrite`, the Nova the app milestone that
    served no key result and kept no KPI (issue #227's orphan list). Issue #233
    decided the rewrite comes before any other Nova frontend change, because
    every render function in one hand-rolled `app.js` rebuilds its container on
    each poll. What that milestone cannot catch on its own is the file growing
    while the rewrite waits -- every surface added there is one more to
    migrate. Same ratchet as `marcus-kpi-browser-monolith`: it may fall and
    must never rise, and `high` is the reading taken the day it was written.

    Read from `main` on GitHub rather than the local checkout, because a cycle's
    checkout can sit days behind what the site actually serves.
    """
    del since, until
    files, error = _browser_files(NOVA_REPO, NOVA_BROWSER_DIR)
    return _largest_browser_file(NOVA_REPO, NOVA_BROWSER_DIR, files, error)


def measure_agora_metered_spend(since, until):
    """Places in Agora that could spend the prepaid metered balance. Level, no window.

    **This KPI's bound is the one in `project-goals.md` that is not mine to
    set.** It is `identity.md` rule 9, his words: *"We must never use the
    metered api for other than testing! It is expensive and I only have 16$
    left."* A high of zero and no low at all is deliberate.

    It used to be measured in dollars per week and had no instrument, because
    nothing here reads the prepaid balance and no Agora message records which
    model produced it -- so the only honest dollar figure was a blank. The
    measure is a count now for the reason the document's own note already gave:
    this watches *whether the enforcement is still in place*, not whether
    anyone was careful. `reply.py` refuses a metered provider on an unattended
    turn and Agora's default model is a subscription one, so the number that
    tells you whether that still holds is how many places are configured to
    reach a metered provider at all. Zero of them is a reading in exactly the
    way an empty push list is (`measure_marcus_push_subscribers`) -- it is the
    state this is supposed to be in, and it goes to a real 0 rather than a
    blank.

    **A breach must surface as a number above the ceiling, never as "not
    measured".** `has_drifted` treats `None` as not-drift, so returning `None`
    on a metered config would leave the document saying 0 and the sweep saying
    nothing -- silence on the one event this exists to catch. So `None` here
    means only that Agora could not be read.
    """
    del since, until
    places, error = fetch_agora_metered_places()
    if places is None:
        return None, error
    if not places:
        return 0, ("nothing in Agora is configured to reach a metered "
                   "provider -- no persona, no enabled heartbeat, no active "
                   "conversation; read live, so it is the configuration now "
                   "and not a record of what ran")
    return len(places), (f"{len(places)} place(s) configured on a metered "
                         f"provider: {', '.join(places[:6])}"
                         + (", ..." if len(places) > 6 else ""))


def fetch_docs_last_commit(repo=DOCS_REPO):
    """The UTC timestamp of the newest commit on the docs repo's default branch.

    Returns `(iso_timestamp, None)` or `(None, why)`. `gh api` rather than a
    clone: this is one field and the checkout is not here.
    """
    try:
        done = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits?per_page=1",
             "--jq", ".[0].commit.committer.date"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh api could not run on {repo}: {exc}"
    if done.returncode != 0:
        return None, f"gh api failed on {repo}: {done.stderr.strip()[:200]}"
    stamp = done.stdout.strip()
    if not stamp:
        return None, f"{repo} returned no commit at all, which is a repo this cannot judge"
    return stamp, None


def fetch_post_daily_counts(site=NEWSPAPER):
    """Articles per Oslo day, from the Post's own API.

    Returns `(counts, undated), None` -- `counts` a `{date: n}` dict -- or
    `(None, why)`. `published_at` comes back as a UTC timestamp and is bucketed
    by its Oslo date, because every window in this module is an Oslo one and an
    article printed at 00:30 Oslo belongs to the day he would say it was.

    `undated` is counted rather than dropped: an article with no
    `published_at` cannot be placed in any day, and the caller has to decide
    whether that makes the rate a floor. Today it is zero over all 1,599.
    """
    payload, error = _get_json(f"{site}/api/articles", timeout=120)
    if error:
        return None, error
    articles = payload.get("articles") if isinstance(payload, dict) else payload
    if not isinstance(articles, list):
        return None, (f"{site}/api/articles did not return a list of articles, "
                      "so there is nothing here to count")
    counts, undated = {}, 0
    for article in articles:
        stamp = str((article or {}).get("published_at") or "").strip()
        day = _oslo_day(stamp)
        if day is None:
            undated += 1
            continue
        counts[day] = counts.get(day, 0) + 1
    return (counts, undated), None


def _oslo_day(stamp):
    """The Oslo calendar date of a UTC timestamp, or `None` if unparseable."""
    text = stamp.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(OSLO).date().isoformat()


def measure_docs_staleness(since, until):
    """Days since the docs site last changed. A level, so no window.

    `since` and `until` are taken and dropped for the reason every other level
    measurer here takes them: `kpi_rows` calls them all the same way.

    **Zero is a real reading.** A commit landing today means the docs changed
    today, which is the state this guardrail wants; `None` means only that
    GitHub could not be asked. That is the same split as
    `measure_marcus_push_subscribers`, and getting it backwards on a guardrail
    whose low bound is 0 would make an unreadable repo look freshest of all.

    It says nothing about whether the docs are *right* -- the note beside this
    KPI in `project-goals.md` says so and it is still true, since a dependency
    bump moves this exactly as far as a rewritten page does.
    """
    del since, until
    stamp, error = fetch_docs_last_commit()
    if error:
        return None, error
    day = _oslo_day(stamp)
    if day is None:
        return None, f"{DOCS_REPO}'s newest commit carried an unreadable date: {stamp!r}"
    days = (date.fromisoformat(today_oslo()) - date.fromisoformat(day)).days
    if days < 0:
        return None, (f"{DOCS_REPO}'s newest commit is dated {day}, which is in "
                      "the future in Oslo -- that is a clock to fix, not a reading")
    return days, (f"the newest commit on {DOCS_REPO} is {day} Oslo "
                  f"({stamp}), which is {days} day(s) ago")


#: The workflow `docs-kr-sync-alive` is about, as GitHub files it. gh-aw keeps
#: the human-written job at `.github/workflows/docs-sync.md` and compiles it to
#: `docs-sync.lock.yml`, and only the compiled name is a workflow GitHub will
#: list runs for -- asking for `docs-sync.md` returns nothing, which is
#: indistinguishable from a workflow that has never run.
DOCS_SYNC_WORKFLOW = "docs-sync.lock.yml"

#: How far back `measure_docs_sync_alive` looks. Its own constant rather than a
#: share of `_DEPRECATION_WINDOW_DAYS`, which is also 30 and counts something
#: else; folding them together would make a change to either silently change
#: both. Thirty days is four scheduled runs, because docs-sync is weekly -- a
#: shorter window would put the denominator at one or two and turn a single red
#: run into a 0% reading.
_DOCS_SYNC_WINDOW_DAYS = 30


def fetch_docs_sync_runs(repo=DOCS_REPO, workflow=DOCS_SYNC_WORKFLOW,
                         runner=subprocess.run):
    """Every recorded run of the docs-sync workflow, newest first.

    Returns `(runs, None)` or `(None, why)`. Each run is the dict `gh` hands
    back: `databaseId`, `event`, `status`, `conclusion`, `createdAt`.

    An empty list is an error rather than a reading, for
    `measure_docs_covers_what_runs`' reason: this workflow demonstrably runs,
    so "no runs at all" is a workflow name that resolved to nothing far more
    often than it is a true zero, and a share over nothing is not 0%.
    """
    try:
        done = runner(
            ["gh", "run", "list", "--repo", repo, "--workflow", workflow,
             "--limit", "100", "--json",
             "databaseId,event,status,conclusion,createdAt"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh run list could not run on {repo}: {exc}"
    if done.returncode != 0:
        return None, (f"gh run list failed on {repo} for {workflow}: "
                      f"{done.stderr.strip()[:200]}")
    try:
        runs = json.loads(done.stdout or "[]")
    except json.JSONDecodeError as exc:
        return None, f"gh returned unreadable JSON for {workflow}: {exc}"
    if not isinstance(runs, list):
        return None, "gh returned a JSON object where a list of runs was expected"
    if not runs:
        return None, (f"{repo} lists no run of {workflow} at all, which is a "
                      "workflow name that resolved to nothing more often than "
                      "it is a true zero")
    return runs, None


#: The line gh-aw's MCP gateway writes when it refuses the agent a read. Since
#: sokrates-docs went public, every read of platform-config, operator and
#: sokrates-cli is refused this way ("FORCED REPOS=PUBLIC ... to prevent private
#: data reads"), and the run still ends green.
DIFC_FILTERED_MARK = "[DIFC-FILTERED]"


def fetch_run_filtered_reads(run_id, repo=DOCS_REPO, runner=subprocess.run):
    """How many reads the gh-aw gateway refused inside one run.

    Returns `(count, None)` or `(None, why)`. A run with no id, or a log `gh`
    could not hand back, is an error rather than 0: 0 is the reading that
    lets a green run count as alive.
    """
    if not run_id:
        return None, "a run carried no databaseId, so its log cannot be read"
    try:
        done = runner(
            ["gh", "run", "view", str(run_id), "--repo", repo, "--log"],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh run view could not run on {repo} run {run_id}: {exc}"
    if done.returncode != 0 or not (done.stdout or "").strip():
        return None, (f"gh run view --log failed on {repo} run {run_id}: "
                      f"{(done.stderr or '').strip()[:200]}")
    return done.stdout.count(DIFC_FILTERED_MARK), None


def measure_docs_sync_alive(since, until):
    """Share of scheduled docs-sync runs in the window that finished. A share.

    **`workflow_dispatch` runs are excluded and that is the whole point of the
    measure.** A docs job that only works when somebody presses the button is
    exactly what this key result exists to catch. Counting the manual runs
    reads 40% against 25% on today's history and hides it, which is why the
    filter is `event == "schedule"` rather than "every run".

    **A run GitHub refused to start still counts as not finishing.** The
    2026-08-21 run died two seconds in over an account spending limit, with no
    step executed -- `tools.agentic_health` deliberately does not raise its
    exit status for one of those, because there is no pull request that fixes
    a billing setting. This is a different question: the key result asks
    whether the docs keep themselves up to date, and a run that never started
    did not. The target is 90 rather than 100 for exactly that reason, and it
    is written down beside the key result.

    **A green run that could not read its sources does not count either.**
    sokrates-docs is public and the three repos it documents are private, so
    gh-aw's gateway refuses the agent every read of them and the run still
    ends `success` -- the 2026-09-11 scheduled run did exactly that and opened
    no pull request. So every green run's log is read, and one carrying a
    `[DIFC-FILTERED]` line sits in the denominator only. A log that cannot be
    read is no reading, not a green run.

    **A run still in flight is in neither half.** It is dropped from the
    denominator rather than counted as a failure, because it has not failed
    yet; the detail says how many were dropped, so a window that is mostly
    in-flight cannot read as a confident share.

    `None` when the history could not be read, or when the window holds no
    scheduled run at all -- a share over nothing is not 0%, and 0% is the
    worst reading this key result has.
    """
    del since
    runs, why = fetch_docs_sync_runs()
    if why:
        return None, why
    until_date = date.fromisoformat(until)
    window_start = until_date - timedelta(days=_DOCS_SYNC_WINDOW_DAYS)
    scheduled, undated = [], 0
    for run in runs:
        if (run.get("event") or "") != "schedule":
            continue
        day = _oslo_day(str(run.get("createdAt") or ""))
        if day is None:
            undated += 1
            continue
        when = date.fromisoformat(day)
        if window_start < when <= until_date:
            scheduled.append((day, run))
    finished = [(day, run) for day, run in scheduled
                if (run.get("status") or "") == "completed"]
    in_flight = len(scheduled) - len(finished)
    if not finished:
        return None, (f"no scheduled run of {DOCS_SYNC_WORKFLOW} has completed "
                      f"in the {_DOCS_SYNC_WINDOW_DAYS}d window "
                      f"{window_start.isoformat()}..{until}"
                      + (f" ({in_flight} still in flight)" if in_flight else "")
                      + (f"; {undated} run(s) carried an unreadable date"
                         if undated else ""))
    green, blind = [], []
    for day, run in finished:
        if (run.get("conclusion") or "") != "success":
            continue
        refused, why = fetch_run_filtered_reads(run.get("databaseId"))
        if why:
            return None, why
        (blind if refused else green).append(day)
    detail = (f"{len(green)} of {len(finished)} scheduled run(s) of "
              f"{DOCS_SYNC_WORKFLOW} finished in the "
              f"{_DOCS_SYNC_WINDOW_DAYS}d window {window_start.isoformat()}.."
              f"{until} Oslo (green: "
              f"{', '.join(green) if green else 'none'}); manual "
              f"workflow_dispatch runs are excluded, because a docs job that "
              f"only works when somebody presses the button is what this "
              f"measures")
    if blind:
        detail += (f"; {len(blind)} green run(s) do not count because the "
                   f"gh-aw gateway refused the agent a read of its sources "
                   f"({', '.join(blind)})")
    if in_flight:
        detail += f"; {in_flight} run(s) still in flight are in neither half"
    if undated:
        detail += f"; {undated} run(s) carried an unreadable date"
    return round(100.0 * len(green) / len(finished), 1), detail


#: The namespaces `docs-kr-covers-what-runs` counts, and the ones it does not.
#:
#: `agents`, `infra` and `obsidian` hold what this platform is -- the personas,
#: the sites, the telemetry stack, the vault's database. Everything else in the
#: cluster is either an upstream operator nobody here should be writing a
#: reference page about (`kube-system`, `argocd`, `crossplane-system`,
#: `tailscale`, `headlamp`, `arc-systems`) or a throwaway (`test`). Counting
#: those would put the share's denominator at 54 and its target permanently out
#: of reach for a reason that has nothing to do with whether the docs are good.
#:
#: The list is written here rather than derived, because "is this ours" is a
#: judgement and there is no label on the cluster that carries it. A namespace
#: added later is invisible to this measure until somebody adds it here, which
#: is the honest cost of the judgement and is why it is a constant a reader can
#: see rather than a filter buried in the function.
DOCUMENTED_NAMESPACES = ("agents", "infra", "obsidian")

#: A word in a heading, where a word may carry internal hyphens.
#:
#: This is the whole matcher and the reason it is a *token* rather than a
#: substring search: `hub` is a Deployment in `infra` and `GitHub` is in half
#: the headings on the site, and `agora` is a Deployment whose name is a prefix
#: of `agora-persona`, `agora-heartbeat` and `agora-claude-bridge`. A
#: mention-anywhere count reads 8 of 18 and every one of the seven extra hits
#: is one of those two mistakes. Requiring the workload's name to *be* a token
#: -- maximal, so `agora-persona` is one token and not two -- gives 1 of 18,
#: which is what I counted by hand.
_HEADING_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def read_documented_workloads(runner=subprocess.run):
    """`(names, why)` -- every Deployment and StatefulSet in the namespaces we own.

    Returns a sorted list of `(namespace, name)`. One `kubectl` call per
    namespace, both kinds at once: a workload is the unit a page would be
    written about, and a Pod is not -- a ReplicaSet's Pod is its Deployment
    counted twice, and a workload parked at zero replicas still needs a page.

    A namespace that answers with no workload at all returns `None` rather
    than contributing nothing. All three of these demonstrably run something,
    so an empty list from one of them is a read that went wrong, and letting
    it through would raise the share by shrinking its denominator -- an error
    in the flattering direction on a number whose job is to be low.
    """
    names = []
    for namespace in DOCUMENTED_NAMESPACES:
        try:
            done = runner(["kubectl", "get", "deploy,statefulset",
                           "-n", namespace, "-o", "json"],
                          capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"kubectl could not read namespace {namespace}: {exc}"
        if done.returncode != 0:
            blob = (done.stderr or done.stdout or "").strip()
            return None, (f"kubectl could not read namespace {namespace}: "
                          f"{blob.splitlines()[0] if blob else 'exited %d' % done.returncode}")
        try:
            body = json.loads(done.stdout)
        except ValueError as exc:
            return None, f"kubectl returned something that is not JSON for {namespace}: {exc}"
        items = body.get("items")
        if not items:
            return None, (f"namespace {namespace} reports no Deployment or "
                          "StatefulSet at all, which is no instrument rather "
                          "than an empty namespace")
        for item in items:
            meta = item.get("metadata") or {}
            name = (meta.get("name") or "").strip()
            if name:
                names.append((namespace, name))
    return sorted(set(names)), None


def page_headings(files):
    """Every token appearing in a heading or a `title:` of a docs page.

    `files` is the `{path: text}` a repo tarball unpacks to. Only paths under
    `docs/` count, and that is load-bearing rather than tidiness: the docs-sync
    gh-aw workflow lives at `.github/workflows/docs-sync.md` and its shell
    comments all start with `#`, so a repo-wide heading scan reads a comment
    about Gemini quota as a page heading. A page is a thing the site publishes.

    A heading and a `title:` are treated the same because they are the same
    claim -- Docusaurus uses the frontmatter title when it has one and the
    first heading when it does not, and a page that names a workload in either
    is a page about that workload.
    """
    tokens = {}
    for path, text in sorted(files.items()):
        if not path.startswith("docs/"):
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                heading = stripped.lstrip("#").strip()
            elif stripped.startswith("title:"):
                heading = stripped.split(":", 1)[1].strip().strip("\"'")
            else:
                continue
            for word in _HEADING_WORD.findall(heading.lower()):
                tokens.setdefault(word, path)
    return tokens


def measure_docs_covers_what_runs(since, until):
    """Share of the workloads we run that any docs page is named after.

    Both halves are read rather than typed. The workloads come from the live
    cluster, because what is running is the question and a manifest ArgoCD has
    not synced is not an answer to it. The pages come from
    `SokratesAI/sokrates-docs` over one tarball call, because the docs site is
    published from that repo's default branch and there is no checkout of it
    here.

    **A workload counts when a page is named after it, not when a page mentions
    it.** `_HEADING_WORD` carries that argument and the numbers behind it.

    `None` when either half could not be read. A share over the workloads that
    answered would read higher or lower than the truth depending on which
    namespace failed, and this is a key result whose whole job is to be a low
    number somebody fixes.
    """
    del since, until
    from tools import running_images

    workloads, why = read_documented_workloads()
    if why:
        return None, why
    files, why = running_images.fetch_manifests(
        repo=DOCS_REPO, suffixes=(".md", ".mdx"))
    if why:
        return None, why
    pages = sorted(p for p in files if p.startswith("docs/"))
    if not pages:
        return None, (f"{DOCS_REPO} carries no markdown under docs/ at all, "
                      "which is no instrument rather than a site with no pages")
    tokens = page_headings(files)
    covered = [(ns, name) for ns, name in workloads if name in tokens]
    missing = [name for ns, name in workloads if name not in tokens]
    named = ", ".join(f"{name} ({tokens[name]})" for _, name in covered) or "none"
    detail = (f"{len(covered)} of {len(workloads)} workload(s) across "
              f"{', '.join(DOCUMENTED_NAMESPACES)} are named in a heading or a "
              f"title of one of {DOCS_REPO}'s {len(pages)} pages under docs/. "
              f"Named: {named}. Not named: {', '.join(missing) or 'none'}. "
              "Named after, not mentioned in -- a mention-anywhere count reads "
              "higher because `hub` is inside GitHub and `agora` is a prefix of "
              "every agora-* sibling")
    return round(100.0 * len(covered) / len(workloads), 1), detail


#: The org `maint-kr-self-documenting` sweeps. The key result asks about "non-
#: config repos", which is a claim about this org and nowhere else.
SELF_DOCUMENTING_ORG = "SokratesAI"

#: Repos left out of `maint-kr-self-documenting`'s denominator, and why for
#: each one. This is a judgement, in the same shape and for the same reason as
#: `DOCUMENTED_NAMESPACES`: nothing on a GitHub repo carries a label saying
#: "this is configuration", so the line has to be drawn here where it can be
#: argued with, and the detail line prints every name it dropped.
#:
#: Two classes, and both are the key result's own words rather than a wider
#: net. **Configuration** is the `-config` repos ArgoCD syncs plus
#: `platform-config` -- the key result says "non-config repos" in as many
#: words. **A synced mirror** is a repo whose commits are made by a sync job
#: rather than by a pull request: there is no merge for documentation to
#: follow, so leaving them in would put a denominator under a target of 100%
#: that can never be reached, which is a measure nobody can ever finish.
#:
#: Archived repos are dropped separately and by `repos_in_org`, because a
#: read-only repo cannot take a workflow at all.
SELF_DOCUMENTING_OUT_OF_SCOPE = {
    "SokratesAI/agora-claude-bridge-config": "configuration",
    "SokratesAI/agora-config": "configuration",
    "SokratesAI/agora-persona-runner-config": "configuration",
    "SokratesAI/marcus-config": "configuration",
    "SokratesAI/sokrates-docs-config": "configuration",
    "SokratesAI/platform-config": "configuration",
    "SokratesAI/.claude": "a synced mirror",
    "SokratesAI/capabilities": "a synced mirror",
    "SokratesAI/dropbox": "a synced mirror",
    "SokratesAI/marcus-backup": "a synced mirror",
    "SokratesAI/session-store": "a synced mirror",
    "SokratesAI/vault": "a synced mirror",
}

#: Branch names that mean "the default branch" when a `push:` trigger names
#: its branches. Read off the trigger rather than asked of the API for one
#: call per repo: every repo in this org defaults to `main`, and a workflow
#: that fires on a push to some other branch is not answering "when a pull
#: request merges" either way.
_DEFAULT_BRANCH_NAMES = frozenset({"main", "master"})


def _trigger_block(document):
    """The parsed `on:` mapping of a workflow, or `None`.

    **The key is not the string `on`.** PyYAML resolves an unquoted `on` with
    the YAML 1.1 boolean rules, so `on:` at the top of every GitHub workflow
    ever written parses as the key `True`. Asking for `document["on"]` reads
    `None` on every file and the measure would report that nothing in the org
    has any trigger at all -- a clean, confident zero off an instrument that
    never looked.
    """
    if not isinstance(document, dict):
        return None
    for key in (True, "on"):
        block = document.get(key)
        if isinstance(block, dict):
            return block
        if isinstance(block, list):
            return {name: None for name in block}
        if isinstance(block, str):
            return {block: None}
    return None


def fires_on_merge(triggers):
    """Does this `on:` block fire when a pull request merges into the default branch?

    Two shapes count and they are the two GitHub offers. A `push` to the
    default branch is what a squash merge produces, and it is how every
    `build.yaml` in this org is already wired. A `pull_request` with `closed`
    in its `types` is the other -- it fires on a close as well as a merge, so
    it is a superset, and a superset is the right side to err on for a
    trigger check whose job is to not miss a real one.

    A `push` with no `branches:` key fires on every branch, which includes the
    default one, so it counts.
    """
    if not isinstance(triggers, dict):
        return False
    if "push" in triggers:
        block = triggers.get("push")
        if not isinstance(block, dict) or "branches" not in block:
            return True
        branches = block.get("branches")
        if isinstance(branches, str):
            branches = [branches]
        if isinstance(branches, list) and any(
                str(name).strip() in _DEFAULT_BRANCH_NAMES for name in branches):
            return True
    if "pull_request" in triggers:
        block = triggers.get("pull_request")
        types = block.get("types") if isinstance(block, dict) else None
        if isinstance(types, str):
            types = [types]
        if isinstance(types, list) and any(
                str(name).strip() == "closed" for name in types):
            return True
    return False


#: The bare name of the docs repo, as a workflow in another repo would spell
#: it -- `SokratesAI/sokrates-docs` in a checkout step, `sokrates-docs` in a
#: `gh` call. The bare name matches both.
_DOCS_REPO_NAME = "sokrates-docs"

#: A path under `docs/`, at the start of a token so `my-docs/` and a URL's
#: `/docs/` page reference do not count. `\S` keeps it to one path segment's
#: worth of characters.
_DOCS_PATH = re.compile(r"(?<![\w./-])docs/\S+")


def writes_documentation(jobs):
    """The signal in a workflow's `jobs:` that it writes documentation, or `None`.

    **Only the `jobs:` half of the file is scanned, and that is the load-
    bearing part.** A `paths: [docs/**]` filter lives under `on:` and means
    the workflow *reacts* to a docs change -- the opposite of writing one --
    so a whole-file grep for `docs/` counts every workflow that ignores docs
    as one that maintains them. Splitting the file at `jobs:` removes that
    class of false positive by construction rather than by a list of
    exceptions.

    Two signals, both of them a destination rather than a name. Naming
    `sokrates-docs` is a workflow pushing its documentation to the site's
    repo. A `docs/` path is a workflow writing pages into its own tree. A
    workflow's *name* is deliberately not a signal: "docs" in a job name is
    the substring guess that read 8 of 18 for `docs-kr-covers-what-runs`
    where the honest answer was 1.

    This reads what a workflow declares, not what it did. A job that names the
    docs repo and then does nothing counts here, and that is the known ceiling
    on this measure -- it is a claim about wiring, which is what the key result
    asks about ("updates itself when a pull request merges"), and it will read
    high rather than low if one is ever wired and left broken.
    """
    if jobs is None:
        return None
    try:
        text = yaml.safe_dump(jobs, default_flow_style=False)
    except yaml.YAMLError:
        return None
    if _DOCS_REPO_NAME in text:
        return _DOCS_REPO_NAME
    match = _DOCS_PATH.search(text)
    return match.group(0) if match else None


def judge_self_documenting(workflows):
    """`(signal, why)` for one repo's workflows -- does a merge update its docs?

    `workflows` is `{path: text}` for that repo's `.github/workflows/`.
    Returns the winning `(path, signal)` pair when one qualifies and `None`
    when none does. A repo with no workflows at all is a real "no", not a
    failed read: nothing runs on a merge there, so nothing can document it.

    gh-aw source files are `.md` and are deliberately not in `workflows` --
    the caller asks for YAML only. gh-aw compiles the source to a
    `.lock.yml` beside it and only the compiled file has an `on:` GitHub
    reads, so judging the source would judge a file that never runs.
    """
    for path in sorted(workflows):
        try:
            document = yaml.safe_load(workflows[path])
        except yaml.YAMLError:
            continue
        if not fires_on_merge(_trigger_block(document)):
            continue
        signal = writes_documentation(
            document.get("jobs") if isinstance(document, dict) else None)
        if signal:
            return path, signal
    return None


def measure_maint_self_documenting(since, until, fetch=None, list_repos=None):
    """Share of non-config repos whose documentation updates itself on a merge.

    `now` on this key result was blank -- it printed as `no instrument`, and
    the document said in as many words that the honest answer was "somewhere
    between 0 and 1 of 26 repos and I have not built the counter that decides
    which". This is that counter.

    One `gh api tarball` per repo, the same read `docs-kr-covers-what-runs`
    makes of the docs repo, fanned out because twenty of them serially is
    twenty seconds of a check that already takes forty.

    **A repo that could not be read returns `None` for the whole measure
    rather than dropping out of the denominator.** Every excluded repo would
    raise the share, and this is a number whose job is to be low until
    somebody fixes it.
    """
    del since, until
    from tools import running_images, security_alerts

    fetch = fetch or running_images.fetch_manifests
    list_repos = list_repos or security_alerts.repos_in_org
    live, error, archived = list_repos(SELF_DOCUMENTING_ORG)
    if error:
        return None, f"could not list {SELF_DOCUMENTING_ORG}'s repos: {error}"
    scope = [name for name in live if name not in SELF_DOCUMENTING_OUT_OF_SCOPE]
    if not scope:
        return None, (f"{SELF_DOCUMENTING_ORG} answered with no in-scope repo at "
                      "all, which is a failed read rather than an empty org")

    def read(repo):
        files, why = fetch(repo=repo, suffixes=(".yml", ".yaml"))
        if why:
            return repo, None, why
        return repo, {path: text for path, text in files.items()
                      if path.startswith(".github/workflows/")}, None

    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for repo, workflows, why in pool.map(read, scope):
            if why:
                return None, f"could not read {repo}'s workflows: {why}"
            results[repo] = workflows

    documenting = []
    for repo in scope:
        verdict = judge_self_documenting(results[repo])
        if verdict:
            documenting.append((repo, verdict[0], verdict[1]))
    named = ", ".join(f"{repo} ({path} names {signal})"
                      for repo, path, signal in documenting) or "none"
    dropped = sorted(name for name in live if name in SELF_DOCUMENTING_OUT_OF_SCOPE)
    detail = (f"{len(documenting)} of {len(scope)} non-config repo(s) in "
              f"{SELF_DOCUMENTING_ORG} carry a workflow that both fires on a "
              f"merge to the default branch and writes documentation: {named}. "
              f"Out of scope: {len(dropped)} config or synced-mirror repo(s) "
              f"({', '.join(dropped) or 'none'}) and {len(archived)} archived. "
              "A workflow's name is not a signal -- only a destination is, "
              "either the docs repo or a docs/ path it writes")
    return round(100.0 * len(documenting) / len(scope), 1), detail


def measure_post_volume(since, until):
    """Articles the Post printed per day, over the seven complete days to yesterday.

    **The window is derived here and the tool's own window is dropped on
    purpose.** This is a rate, so a partial day drags it toward zero for most
    of every day: at 17:00 Oslo today the Post had printed 2 articles against a
    seven-day floor of 82, and a measurer that included today would have
    written `2` into a document whose bounds are 10 to 60 and called the paper
    dead. Seven complete days ending yesterday is the same window the number in
    `project-goals.md` was hand-measured over, so the two are comparable.

    A day the Post printed nothing still counts as a day -- it is a zero in the
    average, not a day that did not happen -- so the divisor is always seven.
    """
    del since, until
    result, error = fetch_post_daily_counts()
    if error:
        return None, error
    counts, undated = result
    yesterday = date.fromisoformat(today_oslo()) - timedelta(days=1)
    window = [(yesterday - timedelta(days=n)).isoformat() for n in range(7)]
    total = sum(counts.get(day, 0) for day in window)
    daily = [counts.get(day, 0) for day in reversed(window)]
    rate = round(total / 7.0)
    caveat = (f"; {undated} article(s) carry no publication date and are not "
              "counted, so this is a floor" if undated else "")
    return rate, (f"{total} article(s) over the seven complete days "
                  f"{window[-1]}..{window[0]} Oslo is {rate} a day "
                  f"(daily: {', '.join(str(n) for n in daily)}){caveat}")


#: The article fields the Post uses to record that a human reacted to a piece
#: after it was printed. Measured live Cycle 1596 against all 1,599 articles:
#: `feedback` carries `"up"` on 37 of them and `dismissed` carries `True` on 13.
#:
#: **Neither of these existed in the hand count this replaces.** The number in
#: `project-goals.md` was taken this morning off "the union of keys across the
#: sample", and both fields sit on 2.3% of the corpus, so a sample missed them
#: and wrote a 0 that said the Post records nothing at all. A field present on
#: one article in forty is exactly what a sample cannot see, which is why this
#: measurer reads every article the API returns and never a page of them.
_POST_REACTION_FIELDS = ("feedback", "dismissed")

#: Field names that would mean somebody chose to print an article *before* it
#: went out. None of them is on the Post today; the list is here so that the
#: day the Post starts recording one, this measure moves on its own instead of
#: waiting for a cycle to notice.
#:
#: **`_POST_REACTION_FIELDS` is deliberately not in here, and that is the one
#: judgement in this measure.** A thumbs-up is a reaction to something already
#: published, so counting it would let "he liked it afterwards" read as
#: "somebody chose to print it", and the whole point of idea #96 is that a
#: 600-second timer is doing the choosing. Same shape as the `#205` exclusion
#: in `measure_agora_chat_basics`: named, with its reason beside it, so it is
#: arguable rather than re-taken silently every time somebody reads the number.
_POST_EDITORIAL_FIELDS = (
    "approved_by", "approved_at", "editor", "edited_by", "editorial_decision",
    "chosen_by", "curated_by", "selected_by", "reviewed_by", "publish_decision",
)

#: Every key the Post's articles carried when this measurer was written. It
#: exists so the measure can say "the schema moved" rather than quietly keep
#: reporting a number computed from fields that may no longer be the ones that
#: matter -- the failure this measure was written on top of.
_POST_KNOWN_FIELDS = frozenset({
    "_id", "_rev", "body_en", "category", "date", "dismissed", "feedback",
    "generated_at", "has_full_text", "image_url", "published_at",
    "source_name", "source_url", "title_en", "topic", "vault_relevant",
})


def fetch_post_articles(site=NEWSPAPER):
    """Every article the Post will serve, as a list, or `(None, why)`.

    Deliberately not paged and deliberately not sampled. See
    `_POST_REACTION_FIELDS` for what a sample cost.
    """
    payload, error = _get_json(f"{site}/api/articles", timeout=120)
    if error:
        return None, error
    articles = payload.get("articles") if isinstance(payload, dict) else payload
    if not isinstance(articles, list):
        return None, (f"{site}/api/articles did not return a list of articles, "
                      "so there is nothing here to read")
    return [a for a in articles if isinstance(a, dict)], None


def fetch_post_open_stats(site=NEWSPAPER):
    """The Post's own article-open counter, or `(None, why)`.

    The newspaper server counts a fetch of `/api/full-text/<id>` as one open --
    the one event it sees when an article is actually read -- and has kept the
    count in CouchDB since 2026-08-25 (idea #96). The live route wraps the
    payload in `open_stats`; an unwrapped one is accepted too.
    """
    payload, error = _get_json(f"{site}/api/open-stats", timeout=60)
    if error:
        return None, error
    if isinstance(payload, dict) and isinstance(payload.get("open_stats"), dict):
        payload = payload["open_stats"]
    if not isinstance(payload, dict) or not isinstance(payload.get("total_opens"), int):
        return None, (f"{site}/api/open-stats carried no integer total_opens, "
                      "so the open counter could not be read")
    return payload, None


def _post_open_days(stats):
    """The Oslo days the Post recorded at least one article open on.

    `opens_by_day` exists since platform-config#763 (idea #311, 2026-09-17);
    the opens counted before it carry no date and add no day. A server that
    predates the field, or an unreadable counter, gives the empty set.
    """
    by_day = (stats or {}).get("opens_by_day")
    if not isinstance(by_day, dict):
        return set()
    return {day for day, count in by_day.items()
            if isinstance(count, int) and count > 0
            and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day))}


def _post_opens_note(stats, error):
    """One clause on the open counter, for a readership detail line.

    Only the dated opens add a day; the running total is named beside them
    because it is the only record of a read that needs no tap, and leaving it
    out made the detail read as if reactions were all the Post keeps.
    """
    if error:
        return f"; the Post's open counter could not be read ({error})"
    in_print = stats.get("opens_in_print")
    articles = sum(c.get("articles_opened", 0)
                   for c in (stats.get("categories") or {}).values()
                   if isinstance(c, dict))
    open_days = _post_open_days(stats)
    dated = (f"; {len(open_days)} Oslo day(s) carry a dated open, newest "
             f"{max(open_days)}" if open_days else
             "; no open carries a date yet, so they add no day here")
    return (f"; separately the Post has counted {stats['total_opens']} article "
            f"open(s) ever, {in_print} of them on {articles} article(s) still "
            f"in print{dated}")


def _post_field_set(articles):
    """The union of keys across every article, and the ones I have never seen."""
    seen = set()
    for article in articles:
        seen |= set(article.keys())
    return seen, sorted(seen - _POST_KNOWN_FIELDS)


def _has_value(article, field):
    """True when `field` is present on `article` carrying something real.

    `dismissed` comes back as the *string* `"True"` on some rows and the JSON
    boolean on others, so this cannot be a plain truthiness check -- and a
    field present but empty is the Post writing a placeholder, not a decision.
    """
    value = article.get(field)
    if value is None or value is False:
        return False
    text = str(value).strip()
    return bool(text) and text.lower() not in ("false", "0", "none", "null")


def measure_post_editor(since, until):
    """Share of the Post's articles carrying a record that someone chose to print them.

    Zero here means *by absence* -- no article carries any field in
    `_POST_EDITORIAL_FIELDS` -- and that is a real reading, not a gap. The
    gap answer is `None`, and it is returned for the two cases where a 0 would
    be manufactured rather than measured: the API cannot be reached, and the
    API returns no articles at all. The second matters because the target is
    100 and the direction is up, so an empty corpus would otherwise divide by
    zero or, worse, read as a spotless 0%.
    """
    del since, until
    articles, error = fetch_post_articles()
    if error:
        return None, error
    if not articles:
        return None, ("the Post's API returned no articles, so there is no "
                      "denominator here and 0% would be a number about nothing")
    chosen = [a for a in articles
              if any(_has_value(a, f) for f in _POST_EDITORIAL_FIELDS)]
    share = round(100.0 * len(chosen) / len(articles), 1)
    _, unknown = _post_field_set(articles)
    drift = (f"; the Post has grown field(s) I have never judged "
             f"({', '.join(unknown)}) -- check whether one of them records an "
             "editorial decision" if unknown else "")
    reacted = sum(1 for a in articles
                  if any(_has_value(a, f) for f in _POST_REACTION_FIELDS))
    return share, (f"{len(chosen)} of {len(articles)} article(s) carry a field "
                   f"recording a decision to print "
                   f"({', '.join(_POST_EDITORIAL_FIELDS)}); {reacted} carry a "
                   f"reaction after printing, which is deliberately not counted "
                   f"as choosing{drift}")


def measure_post_readership(since, until):
    """Distinct days the Post holds any record that a human read an article.

    A reaction carries no timestamp of its own, so a day from a reaction is the
    *publication* day of a reacted article. That is a proxy and the detail says
    so. Since idea #311 an article open is also filed under the Oslo day it
    happened, and those days count as they are -- the one real reading date.

    Zero is real -- it means no article carries a reaction. `None` is the
    unreadable case, and the split matters more here than usual: the target is
    30 and the direction is up, so a failed fetch read as 0 would look like a
    Post that lost its readership data rather than a measure that could not ask.
    """
    del since, until
    articles, error = fetch_post_articles()
    if error:
        return None, error
    stats, open_error = fetch_post_open_stats()
    open_days = set() if open_error else _post_open_days(stats)
    note = _post_opens_note(stats, open_error)
    reacted = [a for a in articles
               if any(_has_value(a, f) for f in _POST_REACTION_FIELDS)]
    reaction_days = {d for d in (_oslo_day(str(a.get("published_at") or ""))
                                 for a in reacted) if d}
    days = sorted(reaction_days | open_days)
    undated = len(reacted) - sum(
        1 for a in reacted if _oslo_day(str(a.get("published_at") or "")))
    if not reacted:
        return len(days), (f"no article of the {len(articles)} the Post serves "
                           f"carries any of {', '.join(_POST_REACTION_FIELDS)}, "
                           "so it holds no record that anyone reacted to one"
                           + note)
    caveat = (f"; {undated} reacted article(s) carry no publication date and "
              "cannot be placed on a day" if undated else "")
    newest = f", newest {days[-1]}" if days else ""
    return len(days), (f"{len(reacted)} of {len(articles)} article(s) carry a "
                       f"reaction ({', '.join(_POST_REACTION_FIELDS)}), falling "
                       f"on {len(reaction_days)} distinct Oslo day(s) by "
                       f"publication date; {len(days)} day(s) with reading "
                       f"counting dated opens too{newest}; the reaction itself "
                       f"is not timestamped{caveat}{note}")


#: The project name `nas-kr-unattended` counts rows for, exactly as both
#: boards spell it. A string rather than an id because a board row names its
#: project in prose and nothing on the row carries a stable project key.
_NAS_PROJECT = "NAS"

#: Status keys that mean a row is no longer open. `⚫ Outdated` is closed the
#: same way `✅ Done` is -- it will never move again -- and the NAS ideas board
#: holds exactly one row, in that state, which is why it has to be named here
#: rather than left to fall out of a `done` check.
#:
#: **Do NOT read the row's own `done` field.** Every row the site serves
#: carries `done: false`, including the 181 marked `✅ Done`; that field means
#: something else. `statusKey` is the one that separates them.
_CLOSED_STATUS_KEYS = frozenset({"done", "outdated"})

#: The one status that means the row cannot move until the owner does something.
#: It is the board's own key, not a phrase matched out of the label.
_BLOCKED_ON_EDVARD = "blocked-on-edvard"


def measure_nas_unattended(since, until):
    """Open NAS rows that cannot move until the owner does something by hand.

    A count, not a share, so there is no denominator to get wrong -- but the
    target is 0 and the direction is down, which makes a low reading the good
    one and therefore the dangerous one. Everything below is about making sure
    a 0 here can only come from having looked.

    It reads both boards, not just `issues`. The hand count this replaces was
    taken off the issues board alone and the ideas board holds one NAS row, so
    on today's data the two agree; a NAS idea blocked on him tomorrow would
    make them disagree, and the key result's own words are *open NAS rows*.

    **An unreadable board is no reading at all, never a smaller count.** Half a
    sweep undercounts a measure whose best value is zero, so either board
    failing returns `None` -- the same call `measure_nas_services_down` makes
    about a partial SSH sweep for the same reason.

    **A NAS project with no rows at all is also no reading.** `_NAS_PROJECT` is
    a string I typed here and the board carries no id to check it against, so a
    rename of the project reads as a perfect score rather than as a broken
    instrument. Zero rows naming it is far more likely to be that than a real
    emptiness: the project has eight issues today and the objective exists
    because they are stuck.
    """
    del since, until
    rows = []
    for name in ("issues", "ideas"):
        items, error = fetch_board(name)
        if error:
            return None, (f"the {name} board could not be read, and half a "
                          f"sweep undercounts a count whose target is 0: {error}")
        rows.extend(items)
    nas = [r for r in rows if (r.get("project") or "").strip() == _NAS_PROJECT]
    if not nas:
        return None, (f"no row on either board names the project "
                      f"{_NAS_PROJECT!r}, which is a renamed project far more "
                      "often than it is an empty one")
    open_rows = [r for r in nas
                 if (r.get("statusKey") or "").strip() not in _CLOSED_STATUS_KEYS]
    blocked = [r for r in open_rows
               if (r.get("statusKey") or "").strip() == _BLOCKED_ON_EDVARD]
    numbers = ", ".join(f"#{r.get('number')}" for r in blocked) or "none"
    return len(blocked), (
        f"{len(blocked)} of {len(open_rows)} open {_NAS_PROJECT} row(s) are "
        f"blocked on Edvard ({numbers}), read live from the issues and ideas "
        f"boards; {len(nas) - len(open_rows)} more are done or outdated and "
        "are in neither half")


#: The milestone `agora-kr-chat-basics` counts rows under, exactly as both
#: boards spell it. A string rather than an id for the same reason
#: `_NAS_PROJECT` is one: a board row names its milestone in prose and nothing
#: on the row carries a stable milestone key.
_CHAT_BASICS_MILESTONE = "Chat basics he asked for"

#: Rows under that milestone that are NOT a missing control, by number, with
#: the reason each is here. This is the one judgement in the measure and it is
#: written down rather than hidden: issue #205 is a chat that stopped
#: responding mid-conversation, which is a breakage. Folding it in would let
#: fixing a crash read as answering an ask, and the key result's own words in
#: `project-goals.md` already say it is deliberately not counted.
_CHAT_BASICS_NOT_A_CONTROL = {205: "a breakage, not a missing control"}


def measure_agora_chat_basics(since, until):
    """Basic chat controls the owner has asked for that are still missing.

    A count with a target of 0 and a downward direction, so a low reading is
    the good one and therefore the one that has to be hard to fake. The three
    ways this could print a wrong 0 are each closed below.

    **A milestone that names no row is no reading, not a zero.**
    `_CHAT_BASICS_MILESTONE` is a string I typed here and the board carries no
    id to check it against, so renaming the milestone would otherwise read as
    every control having been built. Seven rows carry it today and the
    objective exists because they are open, so an empty match is a rename far
    more often than it is success.

    **An unreadable board is no reading either.** Half a sweep undercounts a
    measure whose best value is zero -- the same call `measure_nas_unattended`
    makes, and for the same reason. It reads the ideas board as well as issues
    because the key result says *controls you have asked for* rather than
    *issues*; today every row under this milestone is an issue and the ideas
    board contributes nothing, but an idea filed under it tomorrow is one of
    these and the hand count would have missed it.

    **Do NOT read the row's own `done` field** -- every row the site serves
    carries `done: false`, including the ones marked done. `statusKey` is the
    field that separates open from closed, and `_CLOSED_STATUS_KEYS` above is
    what this shares with the NAS measure.

    A row blocked on the owner still counts. It is a control he asked for and
    does not have; who it is waiting on changes who fixes it, not whether it
    is missing.
    """
    del since, until
    rows = []
    for name in ("issues", "ideas"):
        items, error = fetch_board(name)
        if error:
            return None, (f"the {name} board could not be read, and half a "
                          f"sweep undercounts a count whose target is 0: {error}")
        rows.extend(items)
    under = [r for r in rows
             if (r.get("milestone") or "").strip() == _CHAT_BASICS_MILESTONE]
    if not under:
        return None, (f"no row on either board names the milestone "
                      f"{_CHAT_BASICS_MILESTONE!r}, which is a renamed "
                      "milestone far more often than it is an empty one")
    open_rows = [r for r in under
                 if (r.get("statusKey") or "").strip() not in _CLOSED_STATUS_KEYS]
    excluded = [r for r in open_rows
                if r.get("number") in _CHAT_BASICS_NOT_A_CONTROL]
    missing = [r for r in open_rows
               if r.get("number") not in _CHAT_BASICS_NOT_A_CONTROL]
    numbers = ", ".join(f"#{r.get('number')}" for r in missing) or "none"
    detail = (f"{len(missing)} of {len(open_rows)} open row(s) under "
              f"{_CHAT_BASICS_MILESTONE!r} are a control he asked for and does "
              f"not have ({numbers}), read live from the issues and ideas "
              f"boards; {len(under) - len(open_rows)} more are done or outdated "
              "and are in neither half")
    if excluded:
        named = ", ".join(
            f"#{r.get('number')} ({_CHAT_BASICS_NOT_A_CONTROL[r['number']]})"
            for r in excluded)
        detail += f"; {named} is under the milestone and deliberately not counted"
    return len(missing), detail


#: The milestone `agora-kr-nothing-unused` counts rows under, exactly as both
#: boards spell it. Same shape and same reason as `_CHAT_BASICS_MILESTONE`: a
#: row names its milestone in prose and carries no stable milestone key.
_UNUSED_MILESTONE = "Retire what nothing uses"

#: Rows under that milestone that do not name a subsystem whose *use* can be
#: asked about, by number, with the reason each is here. Idea #155 is the one:
#: it asks whether the Nova app's chat and Agora should be one product, and his
#: own words in it -- *"now when i use the new chat i get alerted by agora"* --
#: say he uses the thing it is about. Counting it as a subsystem nothing uses
#: was wrong in the hand count that stood here, and folding a duplication
#: complaint into a disuse count lets merging two live products read as
#: retiring a dead one.
_UNUSED_NOT_A_SUBSYSTEM = {
    155: "a duplication complaint about a chat he uses, not a disused subsystem",
}


def _probe_agora_workflows(since, until, site=AGORA):
    """Has an Agora workflow actually run inside the window?

    Returns `(used, why)` -- `used` True, False, or `None` when Agora could
    not be read at all.

    **A workflow object records nothing about running.** `/workflows` carries
    `createdAt` and `updatedAt` and no run history, and there is no
    `/workflows/<id>/runs` route to ask -- so reading the workflow list and
    finding no run is the negative that was guaranteed before it was taken. The
    record that does exist is one level up: a heartbeat carries a `workflowId`
    and a `lastRunAt`, and a workflow in this system runs because a heartbeat
    fires it. That is the invocation trace, and it is what this reads.

    **A disabled heartbeat is not a live path.** Both heartbeats that name a
    workflow today are disabled and say so in their own names -- they are the
    two trials I ran on 2026-08-25 and switched off the same minute. Their
    `lastRunAt` falls inside a 30-day window, so counting a run off a heartbeat
    that can no longer fire would report the subsystem as used on the strength
    of my own test of it. `fetch_agora_metered_places` leaves disabled
    heartbeats out for the same reason and states it the same way: they cannot
    spend, and these cannot run.
    """
    payload, error = _get_json(f"{site}/heartbeats")
    if error:
        return None, error
    beats = (payload or {}).get("heartbeats")
    if not isinstance(beats, list):
        return None, f"{site}/heartbeats answered without a heartbeat list"
    bound = [h for h in beats if str((h or {}).get("workflowId") or "").strip()]
    live = [h for h in bound if h.get("enabled")]
    ran = [h for h in live if _within(h.get("lastRunAt"), since, until)]
    if ran:
        names = ", ".join(str(h.get("name")) for h in ran[:4])
        return True, (f"{len(ran)} enabled heartbeat(s) bound to a workflow "
                      f"fired inside the window: {names}")
    disabled = len(bound) - len(live)
    return False, (f"{len(bound)} heartbeat(s) name a workflow and {disabled} "
                   "of them are disabled, so no enabled heartbeat has fired "
                   "one inside the window; a workflow object records no run of "
                   "its own, so the heartbeat is the only invocation trace")


def _probe_agora_multi_persona(since, until, site=AGORA):
    """Has a conversation with more than one persona been spoken in?

    Returns `(used, why)`, or `(None, why)` when the conversation list could
    not be read.

    Two conditions, and the second is the one that makes this a use count
    rather than a configuration count: **more than one persona linked**, and
    **a message inside the window**. A two-persona conversation nobody has
    written in since July is a thing that exists, not a thing anybody uses.
    Archived conversations are left out -- an archived thread cannot be spoken
    in, the same call `fetch_agora_metered_places` makes about archived
    conversations for the same reason.
    """
    payload, error = _get_json(f"{site}/conversations", timeout=120)
    if error:
        return None, error
    threads = (payload or {}).get("conversations")
    if not isinstance(threads, list):
        return None, f"{site}/conversations answered without a conversation list"
    if not threads:
        return None, (f"{site}/conversations returned no conversation at all, "
                      "which is an unreadable store rather than an idle one")
    multi = [c for c in threads
             if not c.get("archived") and len(c.get("personas") or []) > 1]
    spoken = [c for c in multi if _within(c.get("lastMessageAt"), since, until)]
    if spoken:
        names = ", ".join(str(c.get("name"))[:40] for c in spoken[:4])
        return True, (f"{len(spoken)} multi-persona conversation(s) carried a "
                      f"message inside the window: {names}")
    return False, (f"0 of {len(threads)} conversation(s) in the store carry "
                   f"more than one persona and a message inside the window; "
                   f"{len(multi)} carry more than one persona at all")


#: Which live probe answers "is this one used" for each row under
#: `_UNUSED_MILESTONE`, by row number. A row with no probe here is why the
#: measure refuses to report at all -- see `measure_agora_nothing_unused`.
_UNUSED_PROBES = {
    94: _probe_agora_workflows,
    95: _probe_agora_multi_persona,
}


def _within(stamp, since, until):
    """Is a UTC timestamp inside the Oslo window `since..until`, inclusive?

    `False` for anything unparseable or absent, which is the safe direction
    here: a row whose only evidence of use is a timestamp nothing can read has
    not been shown to be used.
    """
    day = _oslo_day(str(stamp or ""))
    if day is None:
        return False
    return str(since) <= day <= str(until)


def measure_agora_nothing_unused(since, until):
    """Agora subsystems with no recorded use inside the window.

    A count with a target of 0 and a downward direction, so the low reading is
    the good one and the fake to guard against is a small number.

    **The list of subsystems is his, not mine.** The reading this replaces was
    a hand count, and `project-goals.md` said so in the sentence that mattered
    most: *"The denominator is mine: I chose which three subsystems count as
    subsystems, so this moves when I change my mind about the list rather than
    when Agora changes."* The rows under the *Retire what nothing uses*
    milestone are the same three and they are on his board, so the list now
    moves when the board does. Same construction as
    `measure_agora_chat_basics`, and it inherits that measure's two rules for
    the same reasons: a milestone naming no row is a rename rather than a
    success, and half a sweep of the boards undercounts a count whose best
    value is zero.

    **A row with no probe means no reading at all, for the whole measure.**
    This is the rule the shape turns on. Each counted row is a subsystem whose
    use is read live off Agora, and a row I have not written a probe for cannot
    be shown to be used -- so counting it as unused would be the positive
    result that was guaranteed before it was taken, and dropping it from the
    denominator would shrink a count that is trying to reach 0. Both flatter.
    So a probe that is missing, or that cannot reach Agora, returns `None` and
    names the row.

    **What is deliberately excluded is one row and it is written down.**
    `_UNUSED_NOT_A_SUBSYSTEM` holds idea #155 with its reason; the same
    judgement in `measure_agora_chat_basics` is a named constant for the same
    reason, so that it is arguable rather than re-taken in silence each time.
    """
    rows = []
    for name in ("issues", "ideas"):
        items, error = fetch_board(name)
        if error:
            return None, (f"the {name} board could not be read, and half a "
                          f"sweep undercounts a count whose target is 0: {error}")
        rows.extend(items)
    under = [r for r in rows
             if (r.get("milestone") or "").strip() == _UNUSED_MILESTONE]
    if not under:
        return None, (f"no row on either board names the milestone "
                      f"{_UNUSED_MILESTONE!r}, which is a renamed milestone far "
                      "more often than it is an empty one")
    open_rows = [r for r in under
                 if (r.get("statusKey") or "").strip() not in _CLOSED_STATUS_KEYS]
    excluded = [r for r in open_rows if r.get("number") in _UNUSED_NOT_A_SUBSYSTEM]
    judged = [r for r in open_rows
              if r.get("number") not in _UNUSED_NOT_A_SUBSYSTEM]
    unused, used, notes = [], [], []
    for row in judged:
        number = row.get("number")
        probe = _UNUSED_PROBES.get(number)
        if probe is None:
            return None, (f"idea/issue #{number} is under {_UNUSED_MILESTONE!r} "
                          "and nothing here reads whether it is used, so this "
                          "count would be a guess at that row either way")
        verdict, why = probe(since, until)
        if verdict is None:
            return None, (f"#{number} could not be read off Agora, and a "
                          f"subsystem this cannot ask about is not an unused "
                          f"one: {why}")
        (used if verdict else unused).append(number)
        notes.append(f"#{number} {'used' if verdict else 'unused'} -- {why}")
    numbers = ", ".join(f"#{n}" for n in unused) or "none"
    detail = (f"{len(unused)} of {len(judged)} subsystem(s) named by an open "
              f"row under {_UNUSED_MILESTONE!r} have no recorded use in "
              f"{since}..{until} ({numbers}), read live off Agora; "
              f"{len(under) - len(open_rows)} more row(s) are done or outdated")
    if excluded:
        named = ", ".join(f"#{r.get('number')} ({_UNUSED_NOT_A_SUBSYSTEM[r['number']]})"
                          for r in excluded)
        detail += f"; {named} is under the milestone and deliberately not counted"
    if notes:
        detail += "; " + "; ".join(notes)
    return len(unused), detail


MCP_SPEC_REPO = "modelcontextprotocol/modelcontextprotocol"

#: A module attribute that would only exist if this MCP server also spoke the
#: deprecated HTTP+SSE transport -- that transport needs a long-lived GET
#: stream beside the POST endpoint, and `handle_http` is the whole surface.
_MCP_SSE_ENTRY_POINTS = ("handle_sse", "handle_stream", "sse_stream", "event_stream")

#: The same read for Dynamic Client Registration, which is an RFC 7591
#: `/register` endpoint reached through OAuth metadata. This server mints a
#: per-turn bearer token in-process (`grant`/`revoke`) and registers nobody.
_MCP_REGISTRATION_ENTRY_POINTS = ("register_client", "handle_register",
                                  "oauth_metadata", "authorization_server_metadata")

#: JSON-RPC methods a server would have to answer to be participating in each
#: deprecated feature. Probed for real against `handle`, so a branch added to
#: the dispatch flips the reading without anybody editing this map.
_MCP_PROBE_METHODS = ("roots/list", "sampling/createMessage", "logging/setLevel")


def _mcp_feature_key(cell):
    """The Feature cell of the spec's registry, flattened for matching.

    Markdown links out, backticks out, lowercased, whitespace collapsed. The
    cell is prose with links in it -- `[Roots](/specification/.../roots)` --
    and the link target moves every revision while the label does not.
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell or "")
    text = text.replace("`", "").replace("*", "")
    return re.sub(r"\s+", " ", text).strip().lower()


def read_mcp_surface(module=None):
    """`(surface, why)` -- what this loop's own MCP server advertises and answers.

    A live read of the server rather than a grep of it: it mints a real grant,
    sends a real `initialize`, and sends one real JSON-RPC request per method
    in `_MCP_PROBE_METHODS` to see which the dispatch answers. Adding a
    `logging/setLevel` branch to `agora_runner.tools_mcp.handle` changes this
    reading with no edit here, which is the property that makes it an
    instrument instead of a second copy of the truth.

    The two things it cannot ask over JSON-RPC -- the transport and the
    authorization scheme -- it reads as the presence or absence of a named
    entry point on the module, and `measure_agora_mcp_current`'s detail string
    says so rather than passing them off as protocol readings.

    The grant is revoked in a `finally`, and no tool is ever called: every
    probed method is one this server does not implement, so the dispatch
    reaches its `unknown method` fallback and stops there.
    """
    if module is None:
        try:
            from agora_runner import tools_mcp as module
        except Exception as exc:  # pragma: no cover - import guard
            return None, f"could not import the MCP server to probe it: {exc}"
    try:
        token = module.grant({"name": "goal_measures probe"}, {"vaultRead": True}, None)
    except Exception as exc:
        return None, f"the MCP server refused a probe grant: {exc}"
    if not token:
        return None, ("the MCP server issued no grant, so nothing could be "
                      "asked of it")
    try:
        status, payload = module.handle(token, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": module.DEFAULT_PROTOCOL_VERSION},
        })
        result = (payload or {}).get("result")
        if status != 200 or not isinstance(result, dict):
            return None, (f"the MCP server did not answer initialize "
                          f"(status {status}), so its capabilities are unread")
        caps = result.get("capabilities")
        if not isinstance(caps, dict):
            return None, "the MCP server's initialize carried no capabilities"
        answers = {}
        for method in _MCP_PROBE_METHODS:
            _status, reply = module.handle(token, {
                "jsonrpc": "2.0", "id": 2, "method": method, "params": {},
            })
            answers[method] = isinstance(reply, dict) and "result" in reply
    finally:
        try:
            module.revoke(token)
        except Exception:  # pragma: no cover - revoke is best effort
            pass
    return {
        "revision": getattr(module, "DEFAULT_PROTOCOL_VERSION", None),
        "capabilities": caps,
        "answers": answers,
        "sse": [name for name in _MCP_SSE_ENTRY_POINTS if hasattr(module, name)],
        "registration": [name for name in _MCP_REGISTRATION_ENTRY_POINTS
                         if hasattr(module, name)],
    }, None


def _mcp_in_use_roots(surface):
    if "roots" in surface["capabilities"]:
        return True, "initialize advertises a roots capability"
    if surface["answers"].get("roots/list"):
        return True, "the dispatch answers roots/list"
    return False, ("no roots capability is advertised and roots/list is not "
                   "answered")


def _mcp_in_use_sampling(surface):
    if "sampling" in surface["capabilities"]:
        return True, "initialize advertises a sampling capability"
    if surface["answers"].get("sampling/createMessage"):
        return True, "the dispatch answers sampling/createMessage"
    return False, ("no sampling capability is advertised and "
                   "sampling/createMessage is not answered")


def _mcp_in_use_logging(surface):
    if "logging" in surface["capabilities"]:
        return True, "initialize advertises a logging capability"
    if surface["answers"].get("logging/setLevel"):
        return True, "the dispatch answers logging/setLevel"
    return False, ("no logging capability is advertised and logging/setLevel "
                   "is not answered")


def _mcp_in_use_include_context(surface):
    """`includeContext` is a field on a sampling request, so it follows sampling.

    The spec's own Earliest removal cell for this row says *"Follows
    Sampling"*, and a server that never sends a sampling request cannot send
    one carrying this field. Deriving it rather than probing for it is the
    honest read: there is no separate capability or method to ask about.
    """
    used, why = _mcp_in_use_sampling(surface)
    if used:
        return True, f"sampling is in use, and this is a field on it: {why}"
    return False, f"a field on a sampling request this server never sends ({why})"


def _mcp_in_use_sse(surface):
    if surface["sse"]:
        return True, ("the MCP module exposes "
                      + ", ".join(surface["sse"]) + ", which is an SSE stream")
    return False, ("the MCP module exposes no SSE stream entry point, only the "
                   "single POST endpoint Streamable HTTP asks for")


def _mcp_in_use_dcr(surface):
    if surface["registration"]:
        return True, ("the MCP module exposes "
                      + ", ".join(surface["registration"]))
    return False, ("the MCP module registers no client -- authorization is a "
                   "per-turn bearer token minted in-process")


#: Ordered, and the order is load-bearing: the `includeContext` row names
#: Sampling in its own Feature cell, so a plain "which key appears in this
#: cell" match would read it as the Sampling row. First match wins and the
#: narrower keys come first.
_MCP_DEPRECATION_PROBES = (
    ("includecontext", _mcp_in_use_include_context),
    ("dynamic client registration", _mcp_in_use_dcr),
    ("http+sse", _mcp_in_use_sse),
    ("roots", _mcp_in_use_roots),
    ("sampling", _mcp_in_use_sampling),
    ("logging", _mcp_in_use_logging),
)


def parse_mcp_deprecations(text):
    """`(rows, why)` -- the `## Deprecated` table of the spec's own registry.

    `docs/specification/<revision>/deprecated.mdx` is the spec's registry of
    features in the Deprecated state, and its own preamble calls it a derived
    view of the normative per-feature notices. It carries one markdown table
    under `## Deprecated` and a second under `## Removed`, so the section
    boundary is the thing to respect: a removed feature is not a deprecated
    one and folding the two together would count a row twice over its life.

    An empty table is a failed read, not an empty registry. The page exists
    because there are rows in it; a revision with nothing deprecated would not
    publish one, and reading 0 features off a table this could not parse is
    the flattering answer on a measure whose target is 0.
    """
    lines = (text or "").splitlines()
    section, rows = None, []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].strip().lower()
            continue
        if section != "deprecated" or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if set("".join(cells).replace(" ", "")) <= set("-:"):
            continue
        if _mcp_feature_key(cells[0]) == "feature":
            continue
        rows.append({
            "feature": cells[0],
            "key": _mcp_feature_key(cells[0]),
            "deprecated_in": cells[2],
            "removal": cells[-1],
        })
    if not rows:
        return None, ("the spec's deprecation registry parsed to no row at "
                      "all, which is a failed read rather than an empty "
                      "registry")
    return rows, None


def fetch_mcp_deprecations(fetch=None):
    """`((revision, rows), why)` -- the newest released revision's registry.

    One `gh api tarball` of the spec repo, the same read
    `measure_maint_self_documenting` makes of every repo in the org, because
    the registry is one file inside a repo of 353 markdown pages and a
    `contents` walk to find it would be two calls to save nothing.

    `draft` is deliberately skipped. It is the unreleased revision, so a
    feature deprecated only there is not yet deprecated in anything anybody
    implements, and counting it would report a removal clock that has not
    started.
    """
    from tools import running_images

    fetch = fetch or running_images.fetch_manifests
    files, why = fetch(repo=MCP_SPEC_REPO, suffixes=(".mdx",))
    if why:
        return None, f"could not read the MCP specification: {why}"
    registries = {}
    for path, text in (files or {}).items():
        match = re.fullmatch(
            r"docs/specification/(\d{4}-\d{2}-\d{2})/deprecated\.mdx", path)
        if match:
            registries[match.group(1)] = text
    if not registries:
        return None, (f"no docs/specification/<revision>/deprecated.mdx in "
                      f"{MCP_SPEC_REPO}, so the spec's own deprecation "
                      "registry could not be found")
    revision = max(registries)
    rows, why = parse_mcp_deprecations(registries[revision])
    if why:
        return None, f"{revision}: {why}"
    return (revision, rows), None


def measure_agora_mcp_current(since, until):
    """Deprecated MCP features this loop's own server still uses, with a removal date.

    A count with a target of 0 and a downward direction, so the low reading is
    the good one and the fake to guard against is a small number.

    **The list of deprecated features is the spec's, not mine.** The reading
    this replaces was a 3 read off idea #238 -- Roots, Sampling and Logging --
    and `project-goals.md` said in as many words what was wrong with it:
    *"not off the MCP spec, which nothing here reads. So it ages the moment
    the spec moves and I will not notice."* The spec publishes the registry as
    a page of its own now, `deprecated.mdx`, and this reads that page.

    **A row with no probe means no reading at all, for the whole measure**,
    the same rule and the same reason as `measure_agora_nothing_unused`: a
    feature nothing here can ask about cannot be shown to be unused, so
    counting it as unused is the positive result that was guaranteed before it
    was taken, and dropping it from the count shrinks a number trying to reach
    0. Both flatter. A seventh row appearing in the spec therefore stops this
    measure rather than being silently read as fine.

    **What the reading is about is our server, not the whole estate.** Six of
    the six rows resolve against `agora_runner.tools_mcp`, which is the only
    MCP surface this loop serves; four are read out of a live handshake and
    dispatch, and two -- the transport and Dynamic Client Registration -- are
    read as the absence of a named entry point on that module, which the
    detail string says outright because it is a weaker instrument than the
    other four.
    """
    del since, until
    fetched, why = fetch_mcp_deprecations()
    if why:
        return None, why
    revision, rows = fetched
    surface, why = read_mcp_surface()
    if why:
        return None, (f"the MCP specification's {revision} registry lists "
                      f"{len(rows)} deprecated feature(s) and this loop's own "
                      f"server could not be asked about any of them: {why}")
    in_use, undated, notes = [], [], []
    for row in rows:
        probe = next((fn for key, fn in _MCP_DEPRECATION_PROBES
                      if key in row["key"]), None)
        if probe is None:
            return None, (f"the {revision} registry deprecates "
                          f"{row['feature']!r} and nothing here reads whether "
                          "we use it, so this count would be a guess at that "
                          "row either way")
        used, reason = probe(surface)
        notes.append(f"{row['key']}: {'IN USE' if used else 'clear'} -- {reason}")
        if not used:
            continue
        if not row["removal"]:
            undated.append(row["key"])
        else:
            in_use.append(row["key"])
    named = ", ".join(in_use) or "none"
    detail = (f"{len(in_use)} of the {len(rows)} feature(s) in the MCP "
              f"specification's own {revision} deprecation registry are used "
              f"by this loop's MCP server and carry an earliest removal "
              f"({named}). Read off {MCP_SPEC_REPO}'s "
              f"docs/specification/{revision}/deprecated.mdx and off a live "
              f"handshake with agora_runner.tools_mcp, which advertises "
              f"{sorted(surface['capabilities'])} and implements MCP revision "
              f"{surface['revision']}. Four rows are decided by that handshake "
              "and its dispatch; the transport and Dynamic Client "
              "Registration rows are decided by the absence of a named entry "
              "point on that module, which is a weaker read than the other "
              "four and is why they are named here")
    if undated:
        detail += (f"; {', '.join(undated)} is in use with no earliest removal "
                   "in the registry and is not counted, because the measure "
                   "asks for a removal date")
    if notes:
        detail += "; " + "; ".join(notes)
    return len(in_use), detail


def measure_nas_services_down(since, until):
    """NAS services that did not answer over the SSH hop. A level, no window.

    Asks `tools.nas_health.services_down` rather than probing again, the same
    call `measure_nova_unfixed_advisories` makes against `tools.security_alerts`:
    that module already owns which services exist, how the hop is made and what
    counts as an answer, and a second opinion here would be a second thing to
    keep in step.

    **Zero is the reading this most expects and it is a real one**, the way an
    empty push list is in `measure_marcus_push_subscribers`. The ceiling is
    zero because these four services are the reason the NAS exists, so one of
    them being down is already the finding -- which means `None` has to be
    reserved strictly for "I could not look", and a partial sweep counts as
    could-not-look rather than as a small number of failures.

    **This guardrail dies with server1.** It runs on the box it watches, so a
    total failure of the cluster silences it instead of raising it; that is
    what `nas-kr-off-box-watch` is about and it is not a fault in this reading.
    """
    del since, until
    from tools.nas_health import services_down

    down, judged, error = services_down()
    if error:
        return None, error
    if not judged:
        return None, "no NAS service was judged at all, so there is nothing to count"
    if down == 0:
        return 0, (f"all {judged} NAS service(s) answered over the SSH hop, "
                   "read live from this pod")
    return down, (f"{down} of {judged} NAS service(s) did not answer over the "
                  "SSH hop")


#: The workload kinds a long-lived service can run as, for
#: `measure_wa_reaches_you`. **A CronJob is deliberately not one of them.**
#: `whatsapp-auth-backup` is a CronJob in `agents` that mounts the bridge's own
#: `infra/whatsapp-bridge-auth` claim every two hours, and its Pods are the only
#: thing in this cluster whose name carries the word today -- so a kind list
#: that admitted CronJobs would find the *backup of* the bridge and report the
#: bridge as running. A backup of a thing is evidence the thing once existed,
#: never that it is up.
_WA_WORKLOAD_KINDS = "deploy,statefulset,daemonset"

#: The word a workload's name has to carry to be the WhatsApp bridge, and the
#: names that carry it without being it. It is matched as a substring rather
#: than as a whole name: the repository is `whatsapp-bridge` and a deployment of
#: it could reasonably be called `whatsapp`, `whatsapp-bridge` or
#: `whatsapp-bridge-web`, and none of those is a name I get to choose.
_WA_NAME_NEEDLE = "whatsapp"
_WA_NOT_THE_BRIDGE = ("whatsapp-auth-backup",)


def _ready_replicas(item):
    """How many Pods of one workload document are ready, across all three kinds.

    A Deployment and a StatefulSet report `status.readyReplicas`; a DaemonSet
    reports `status.numberReady` and has no `readyReplicas` at all. Reading only
    the first field would score a perfectly healthy DaemonSet as zero ready,
    which on this measure is the difference between "the path is down" and "the
    path is up and I cannot read the share".
    """
    status = item.get("status") or {}
    for field in ("readyReplicas", "numberReady"):
        value = status.get(field)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def measure_wa_reaches_you(since, until):
    """Share of alerts needing an answer that reach his phone over WhatsApp.

    Reads the cluster, not a document. The value this can prove is **0**, and
    the reason it is worth an instrument anyway is that 0 here is not a guess:
    if no workload in this cluster runs the bridge, there is no path for an
    alert to travel, so nothing can have been delivered over it. That was a
    hand count taken at 17:28 Oslo on 2026-09-14 off `tools.disk_health`
    noticing the bridge's auth claim was mounted by no Pod; this asks the
    cluster directly instead, cluster-wide rather than in one namespace,
    because a bridge somebody starts in `infra` next week is as real as one in
    `agents`.

    **The branch that must never report 0 is the one where the bridge is up.**
    Nothing in this loop records which alerts needed an answer, so the moment a
    ready replica exists this returns no reading and says why. That is the
    opposite of the usual guard in this module -- `measure_nas_unattended`
    refuses a 0 it could not measure because 0 is its *target* -- and it points
    the same way: this measure's target is 100 and its worst value is 0, so the
    lie available here is reading 0 forever after the bridge starts working.
    An unreadable cluster is no reading too, for the ordinary reason.
    """
    del since, until
    try:
        done = subprocess.run(["kubectl", "get", _WA_WORKLOAD_KINDS,
                               "-A", "-o", "json"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl could not list the cluster's workloads: {exc}"
    if done.returncode != 0:
        blob = (done.stderr or done.stdout or "").strip()
        first = blob.splitlines()[0] if blob else "exited %d" % done.returncode
        return None, f"kubectl could not list the cluster's workloads: {first}"
    try:
        body = json.loads(done.stdout)
    except ValueError as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"
    items = body.get("items")
    if not items:
        return None, ("kubectl reports no Deployment, StatefulSet or DaemonSet "
                      "anywhere in this cluster, which is no instrument rather "
                      "than an empty cluster")

    namespaces = set()
    bridges = []
    for item in items:
        meta = item.get("metadata") or {}
        name = (meta.get("name") or "").strip()
        namespace = (meta.get("namespace") or "").strip()
        if not name:
            continue
        namespaces.add(namespace)
        if _WA_NAME_NEEDLE not in name.lower():
            continue
        if name in _WA_NOT_THE_BRIDGE:
            continue
        bridges.append((namespace, name, _ready_replicas(item)))

    scope = (f"judged {len(items)} workload(s) across {len(namespaces)} "
             f"namespace(s), every Deployment, StatefulSet and DaemonSet in "
             f"the cluster")
    if not bridges:
        return 0.0, ("no workload in this cluster runs the WhatsApp bridge, so "
                     "there is no path an alert could travel and the share is a "
                     f"real 0 rather than unknown -- {scope}; the CronJob that "
                     "backs up the bridge's auth claim is deliberately not "
                     "counted as the bridge")

    bridges.sort()
    ready = [one for one in bridges if one[2] > 0]
    named = ", ".join(f"{ns}/{name} ({count} ready)"
                      for ns, name, count in bridges)
    if not ready:
        return 0.0, (f"the WhatsApp bridge exists but no replica of it is "
                     f"ready ({named}), so nothing can be delivered over it "
                     f"and the share is a real 0 -- {scope}")
    return None, (f"the WhatsApp bridge is up ({named}), so the path exists -- "
                  "but nothing here records which alerts needed an answer or "
                  "which of them were delivered, so the share cannot be read; "
                  "this deliberately reports no number rather than the 0 that "
                  "was true while the bridge was down")


def measure_infra_ci_minutes(since, until):
    """Billable GitHub Actions minutes this org has used this month. A level.

    Asks `tools.ci_minutes` rather than counting again, the same call
    `measure_nova_unfixed_advisories` makes against `tools.security_alerts`:
    that module already knows which repositories are private (a public repo's
    minutes are free and are not billable at all), which month is the billing
    month, and that a run with no date still carries minutes. A second opinion
    here would be a second thing to keep in step with GitHub's billing API.

    **Public minutes are deliberately not in the number.** The KPI's own
    `high` is 2,000, which is the included private allowance on this plan, so
    folding free minutes into a number bounded by a paid allowance would make
    the guardrail fire on spend that costs nothing.

    Zero is a real reading -- a month in which no private repo ran a billable
    minute is a real month, and the low bound is 0 for that reason. `None`
    means GitHub could not be asked.
    """
    del since, until
    from tools import ci_minutes

    now = datetime.now(timezone.utc)
    try:
        items = ci_minutes.fetch_usage(ci_minutes.ORG, now.year, now.month)
        visibility = ci_minutes.fetch_visibility(ci_minutes.ORG)
    except RuntimeError as exc:
        return None, (f"GitHub's billing API could not be read -- {exc}; "
                      "`python3 -m tools.ci_minutes` prints why")
    private, public, unknown, _net = ci_minutes.split_minutes(items, visibility)
    used = round(sum(private.values()))
    elapsed, days_in_month = ci_minutes.month_progress(now)
    detail = (f"{used} billable minute(s) across {len(private)} private "
              f"repo(s) in {now.year}-{now.month:02d}, {elapsed:.1f} of "
              f"{days_in_month:.0f} day(s) elapsed; "
              f"{sum(public.values()):.0f} public minute(s) are free and are "
              "not counted")
    if unknown:
        detail += (f"; a floor -- {sum(unknown.values()):.0f} minute(s) come "
                   f"from {len(unknown)} repo(s) whose visibility GitHub did "
                   "not report, so they are in neither bucket")
    return used, detail


def measure_infra_node_headroom(since, until):
    """Memory available on the node with the least of it, in MiB. A level.

    Asks `tools.node_memory` for the same reading its own check prints, and
    that module reads each node's kubelet over `nodes/proxy` rather than this
    pod's `/proc` -- so the node this pod is *not* standing on is judged the
    same way as the one it is. Reading `/proc/meminfo` here would silently
    measure one node and call it the cluster.

    **The minimum, not the sum and not the average.** The guardrail is about
    whether a pod can still be scheduled or restarted somewhere, and two nodes
    with 8GiB and 100MiB free have no more usable headroom than the 100MiB
    node has.

    `None` when any node could not be read, and that is stricter than the
    floor `measure_nova_unfixed_advisories` takes: there are two nodes, so an
    unread one is half the estate, and a minimum over the half that answered
    can only ever read higher than the truth -- an error in the flattering
    direction, on a guardrail whose whole job is to catch a low number.
    """
    del since, until
    from tools import node_memory as nm

    try:
        nodes = nm.read_node_names()
    except (OSError, ValueError) as exc:
        return None, f"the node list could not be read -- {exc}"
    if not nodes:
        return None, "the API server listed no nodes, so there is nothing to read"
    readings = {}
    for node in nodes:
        try:
            summary = nm.read_summary(node)
        except (OSError, ValueError) as exc:
            return None, (f"node {node} could not be read -- {exc}; a minimum "
                          "over the nodes that answered would read higher "
                          "than the truth")
        pair = nm.node_memory(summary)
        if pair is None:
            return None, (f"node {node}'s kubelet reported no availableBytes, "
                          "so the minimum is unknown")
        readings[node] = int(pair[0] / nm.MIB)
    tightest = min(readings, key=readings.get)
    spread = ", ".join(f"{name} {mib}Mi" for name, mib in sorted(readings.items()))
    return readings[tightest], (
        f"{readings[tightest]}Mi available on {tightest}, the tightest of "
        f"{len(readings)} node(s) ({spread}), read from each node's own "
        "kubelet over nodes/proxy")


#: One `tools.eol_watch` sweep, held for the life of the process.
#:
#: Two numbers in `project-goals.md` come out of this one sweep --
#: `maint-kr-supported` and `maint-kpi-eol-unjudged` -- and it takes tens of
#: seconds, because it lists every non-archived repo in the org, reads every
#: Dockerfile and workflow in each, and asks endoflife.date about every line it
#: finds. Running it twice in one report would double the slowest thing here to
#: answer a question that cannot have two answers in the same run.
#:
#: Deliberately not a cache with a lifetime: the process is one report, so
#: "once per run" is the whole contract and there is nothing to invalidate.
_EOL_SWEEP = {}


def _eol_sweep():
    """`(judged, not_judged, error)` from one `tools.eol_watch` sweep."""
    if not _EOL_SWEEP:
        from tools import eol_watch

        repos, unplaceable, _notes, incomplete = eol_watch._repos_to_sweep()
        if incomplete or not repos:
            _EOL_SWEEP["result"] = (None, None, (
                "could not enumerate the repos to sweep, so there is no set to "
                "judge over -- `python3 -m tools.eol_watch` prints why"))
            return _EOL_SWEEP["result"]
        products, why = eol_watch.catalogue()
        if products is None:
            _EOL_SWEEP["result"] = (None, None, (
                f"the endoflife.date catalogue was unreadable -- {why}; that is "
                "no instrument rather than no finding"))
            return _EOL_SWEEP["result"]
        today = date.today()
        judged, not_judged, _problems = eol_watch.sweep(
            repos, products, today, eol_watch.DEFAULT_WITHIN_DAYS)
        mapping, ambiguous = eol_watch.image_map(products)
        pins, _cluster_problems = eol_watch.cluster_images()
        # The cluster's own Kubernetes line is a runtime line too (idea #322).
        pins = pins + eol_watch.node_versions()[0]
        for image in pins:
            where = eol_watch.judge(image, products, mapping, today,
                                    eol_watch.DEFAULT_WITHIN_DAYS, ambiguous)
            (judged if where == "judged" else not_judged).append(image)
        _EOL_SWEEP["result"] = (judged, not_judged, None)
        _EOL_SWEEP["unplaceable"] = unplaceable
    return _EOL_SWEEP["result"]


def measure_maint_supported(since, until):
    """Runtime lines out of support, or inside the window before it. A level.

    Counted the way `tools.eol_watch`'s own report counts it: distinct
    `image:tag` lines, not occurrences. The same base image is named once per
    stage of a multi-stage Dockerfile and once per repo that uses it, and a
    key result reading 6 because one dead line appears in six places would be
    a number about our file layout rather than about our estate.

    A line whose end-of-life date is inside `eol_watch.DEFAULT_WITHIN_DAYS`
    counts the same as one already past it, because the target is 0 and a line
    that goes dead next month is already work to do.

    `None` means the sweep could not be built at all. A line endoflife.date has
    no answer for is **not** counted here in either direction -- it is
    `maint-kpi-eol-unjudged`, the guardrail on this instrument's own blind
    spot, and folding it in would make an unreadable estate look supported.
    """
    del since, until
    from tools import eol_watch

    judged, _not_judged, error = _eol_sweep()
    if error:
        return None, error
    bad = eol_watch.group([i for i in judged
                           if i["verdict"] in ("eol", "soon")])
    named = ", ".join(sorted(eol_watch._pin(m[0]) for m in bad.values())[:6])
    total = eol_watch.group(judged)
    detail = (f"{len(bad)} of {len(total)} judged line(s) are out of support "
              f"or inside {eol_watch.DEFAULT_WITHIN_DAYS} day(s) of it")
    if bad:
        detail += f" ({named}{', ...' if len(bad) > 6 else ''})"
    return len(bad), detail


def measure_maint_eol_unjudged(since, until):
    """Unjudged runtime lines a change to `tools.eol_watch` could judge. A level.

    The guardrail on `maint-kr-supported`'s own instrument rather than on the
    estate: that key result reading 2 means very little while what we run is
    partly invisible to the catalogue.

    **It counts the reachable half, not the total, and that is the whole
    point.** It read the total until 2026-09-15, and on that day 22 of the 23
    unjudged lines carried one of `eol_watch.OUT_OF_REACH_CAUSES` -- 18
    products endoflife.date publishes nothing for, which no work here ever
    converts, and 4 whose current release has no end-of-life date published
    yet, which resolve upstream on their own. A number with a floor of 22 under
    a ceiling of 10 is not a guardrail; it is a red light that can never go out,
    and a KPI nobody can move teaches everyone to stop reading it.

    The total is not hidden by this and is not meant to be: `tools.eol_watch`
    prints it with a count per cause every sweep, and the detail below names
    both halves so the KPI and the report reconcile.

    Counted as distinct lines for `measure_maint_supported`'s reason, and off
    the same single sweep, so the two numbers can never be taken over
    different estates.

    Zero is a real reading and the one this wants. `None` means the sweep
    itself could not be built.
    """
    del since, until
    from tools import eol_watch

    _judged, not_judged, error = _eol_sweep()
    if error:
        return None, error
    lines = eol_watch.group(not_judged)
    reach = eol_watch.in_reach(not_judged)
    return len(reach), (
        f"{len(reach)} of {len(lines)} distinct unjudged line(s) are this "
        f"instrument's own reach; the other {len(lines) - len(reach)} have no "
        "support window published anywhere, or no end-of-life date published "
        "yet, so no change here judges them")


def measure_maint_pins_current(since, until):
    """Pinned versions behind what upstream has published. A level.

    Asks `tools.pin_drift`, which reads the pinned value out of the file every
    time rather than from a table of what we pin -- a table would be a second
    copy of the truth that goes stale exactly the way the pin it watches does.

    **Every gap counts, not only a minor or a major.** That check's headline
    raises on a minor or a major, because that is the line at which it wants a
    cycle to go and do something; the key result's own measure is *pins behind
    upstream*, and a patch behind is behind. The detail says how many of each
    so the two numbers can be reconciled rather than looking like a
    disagreement.

    A pin *ahead* of what it is compared against is not counted here. It is a
    real defect and `pin_drift` says so, but it is the opposite defect, and
    adding it to a count whose target is 0 would let a bump in the wrong
    direction cancel a missing one.

    Counted as distinct `(what, pinned, latest)` triples, the way that module
    groups them, for `measure_maint_supported`'s reason. `None` means the repo
    list could not be built; a single unreadable pin is not fatal and shows up
    in the detail as a floor.
    """
    del since, until
    from tools import pin_drift

    repos, _unplaceable, _notes, incomplete = pin_drift._repos_to_sweep()
    if incomplete or not repos:
        return None, ("could not enumerate the repos to sweep, so there is no "
                      "set to judge over -- `python3 -m tools.pin_drift` "
                      "prints why")
    judged, _excluded, problems = pin_drift.sweep(repos)
    behind = pin_drift._group([p for p in judged
                               if p["gap"] in ("major", "minor", "patch")])
    big = pin_drift._group([p for p in judged
                            if p["gap"] in ("major", "minor")])
    detail = (f"{len(behind)} of {len(pin_drift._group(judged))} judged pin(s) "
              f"are behind upstream, {len(big)} of them by a minor or a major")
    if problems:
        detail += (f"; a floor, not a total -- {len(problems)} pin(s) could "
                   "not be compared against upstream")
    return len(behind), detail


def measure_demos_opened(since, until):
    """Share of demos handed over that a person opened at least once. A share.

    Reads the demo registry -- the same vault document `tools.demo` allocates
    ports in -- and counts the rows carrying `opened_at`, which `nova_site`
    writes the first time a real browser asks for `/demo/<slug>/`. A headless
    probe is deliberately not an open there, so this counts people rather than
    monitors.

    **Both halves of the registry count.** A demo that is still running has a
    row under `demos`; one that has been stopped has a tombstone under
    `retired`, added by `nova_demos.unregister`. Counting only the live rows
    would answer "of the demos running right now", which is a question about
    this afternoon rather than about whether the hand-over works.

    **It is a floor on the truth and the detail says so.** Two kinds of row
    can never carry the mark: one started before Cycle 608 shipped the durable
    `opened_at`, and one retired before Cycle 1585 started keeping tombstones.
    Neither is distinguishable from a demo he ignored, so this reads low by as
    many as there are of them and climbs toward the honest number as the old
    rows age out.

    `None` when the registry could not be read or holds no demo at all: a
    share over nothing is not 0%, and 0% here is the worst reading this key
    result has.
    """
    del since, until
    from agora_runner.nova_demos import OPENED_AT, RETIRED
    from tools import demo as demo_tool

    try:
        registry, _rev = demo_tool._read_registry()
    except Exception as exc:                      # DemoError, or no vault tool
        return None, (f"could not read the demo registry, so there is no set "
                      f"to judge over -- {str(exc)[:160]}")
    rows = list(registry.get("demos", [])) + list(registry.get(RETIRED, []))
    if not rows:
        return None, ("the demo registry holds no demo, running or retired, "
                      "so there is no share to take")
    opened = [r for r in rows if r.get(OPENED_AT)]
    live = len(registry.get("demos", []))
    detail = (f"{len(opened)} of {len(rows)} demo(s) carry a durable open mark "
              f"({live} running, {len(rows) - live} retired); a floor, not a "
              f"total -- a row started before the mark existed, or retired "
              f"before this loop kept tombstones, cannot carry one")
    return round(100.0 * len(opened) / len(rows), 1), detail


def measure_demos_no_litter(since, until):
    """Directory names under the demo root that no running demo claims. A count.

    A demo exists to settle one question and then be thrown away, so the thing
    worth counting is what survived the answer: files still on disk under
    `nova_demos.DURABLE_ROOT` with no live registry row pointing at them.
    `nova_demos.orphan_dirs` already decides that -- `tools.demo list` prints
    the same set at the bottom of its output -- so this reads the same answer
    rather than inventing a second one.

    **A retired demo's directory is litter, and that falls out of the data
    rather than being a rule here.** `nova_demos.retire` keeps three fields on
    a tombstone and `dir` is deliberately not one of them, so a stopped demo
    claims no name; `entries()` reads the running rows only. Both halves agree
    that only a running demo protects a directory.

    **The listing is done here rather than through `tools.demo.durable_dirs`,
    and that is the whole point of the function.** `durable_dirs` answers `[]`
    when the root cannot be listed, which is right for a printout that is
    trying not to crash and wrong for a measure whose target is 0: on any pod
    without `/data/workspace/demos` it would report a perfectly clean loop from
    an instrument that never saw a disk. A missing or unreadable root is `None`
    -- no reading -- because the reading it would otherwise give is the best
    possible one.
    """
    del since, until
    from agora_runner import nova_demos
    from tools import demo as demo_tool

    root = nova_demos.DURABLE_ROOT
    try:
        names = sorted(n for n in os.listdir(root)
                       if os.path.isdir(os.path.join(root, n)))
    except OSError as exc:
        return None, (f"could not list {root}, so nothing here has seen the "
                      f"disk this measure is about -- {exc}")
    try:
        registry, _rev = demo_tool._read_registry()
    except Exception as exc:                      # DemoError, or no vault tool
        return None, (f"could not read the demo registry, so no directory can "
                      f"be told from a running demo's -- {str(exc)[:160]}")
    left = nova_demos.orphan_dirs(registry, names)
    running = len(registry.get("demos", []))
    detail = (f"{len(left)} of {len(names)} directory/directories under {root} "
              f"belong to no running demo ({running} running)")
    if left:
        detail += ": " + ", ".join(left)
    return len(left), detail


def measure_infra_outlives_the_box(since, until):
    """Share of this loop's automated checks that would still answer if server1 died.

    `tools.preflight` already carries the judgement, one line per check, in
    `SUBJECT`: `off-box` means the subject lives somewhere else -- GitHub, the
    NAS, endoflife.date -- so the check keeps answering while the box is dark,
    and `on-box` means it dies with what it watches. That label was written
    against a criterion in prose (*would this still report if the thing it
    watches failed completely?*), so this reads the labels rather than
    re-deciding them; a second opinion here would be a second answer to one
    question.

    **The denominator is the whole `CHECKS` roster, not the checks due this
    sweep, and that is the correction this instrument exists to make.** The
    hand-typed 23 in `project-goals.md` was 8 of 35, taken off one morning's
    printout -- but `CADENCE_HOURS` means a sweep runs only the checks whose
    subject can have moved, so 35 was that hour's subset of a 69-check roster.
    A share over it swings with the time of day and with whatever failed last
    sweep, neither of which is a fact about whether monitoring survives the
    box. Over the roster the number is stable and it is higher: 20 of 69.

    A check in `CHECKS` with no `SUBJECT` entry returns no reading at all.
    Calling it on-box would quietly depress the share and calling it off-box
    would inflate it, and preflight itself refuses to run in that state
    (`NO SUBJECT LABEL ... refusing to run`), so the honest answer here is the
    same refusal rather than a number taken over a roster I cannot label.

    **This counts preflight's roster and nothing else, so it is a floor.**
    `nova-deadman` runs on GitHub Actions, reports exactly this failure, and is
    deliberately not in the sweep -- it is the one instrument that spoke on
    2026-09-01 -- so an off-box monitor built outside preflight does not move
    this number. The detail says so, because the share's scope is the roster it
    was taken over.
    """
    del since, until
    from tools import preflight

    roster = list(preflight.CHECKS)
    if not roster:
        return None, ("tools.preflight lists no check at all, so there is no "
                      "roster to take a share over")
    unlabelled = [n for n in roster if n not in preflight.SUBJECT]
    if unlabelled:
        return None, (f"{len(unlabelled)} check(s) carry no SUBJECT label "
                      f"({', '.join(sorted(unlabelled)[:8])}), so they belong "
                      "to neither half of the share -- preflight refuses to "
                      "run in this state and so does this measure")
    off = sorted(n for n in roster if preflight.SUBJECT[n][0] == "off-box")
    on = sorted(n for n in roster if preflight.SUBJECT[n][0] == "on-box")
    odd = [n for n in roster if preflight.SUBJECT[n][0] not in ("off-box", "on-box")]
    if odd:
        return None, (f"{len(odd)} check(s) carry a SUBJECT label that is "
                      f"neither on-box nor off-box ({', '.join(sorted(odd)[:8])})")
    detail = (f"{len(off)} of {len(roster)} check(s) in tools.preflight watch "
              f"something off this box and {len(on)} run on the box they "
              f"watch; taken over the whole roster, not the subset due this "
              f"sweep. Off-box: {', '.join(off)}. A floor -- nova-deadman "
              f"reports this same failure from GitHub Actions and is not in "
              f"the roster, so it is counted on neither side")
    return round(100.0 * len(off) / len(roster), 1), detail


#: The vault folder every research write-up lands in. `identity.md` calls it
#: "durable write-ups, so no cycle pays for the same investigation twice",
#: which is the claim `pm-kpi-research-reused` exists to check.
RESEARCH_PREFIX = "projects/sokrates/projects/agora/nova/resources/research/"

VAULT_TOOL = "/app/bridge/vault_tool.py"


def research_write_ups(runner=subprocess.run, tool=VAULT_TOOL):
    """Every research write-up's slug -- its filename without `.md`.

    The slug rather than the path because that is what an entry writes: a
    cycle citing one types ``resources/research/idp-2026-08.md`` or just the
    filename, never the whole vault path.
    """
    try:
        done = runner([sys.executable, tool, "ls", RESEARCH_PREFIX],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"could not list {RESEARCH_PREFIX}: {exc}"
    if done.returncode != 0:
        return [], (f"{tool} ls {RESEARCH_PREFIX} exited {done.returncode}: "
                    f"{(done.stderr or '').strip()[:200]}")
    slugs = []
    for line in (done.stdout or "").splitlines():
        name = line.strip().rsplit("/", 1)[-1]
        if name.endswith(".md") and not name.startswith("_"):
            slugs.append(name[:-3])
    return slugs, None


def _every_journal_entry(site=SITE, limit=5000):
    """Every journal entry there is, in whatever order the site answers.

    Deliberately not `fetch_entries`: that drops `report` and `silence` cards
    because neither can carry a `board` field, and a weekly research run's
    card is exactly where a write-up is most often born. Dropping the entry
    that *wrote* a document would promote its first real citation into the
    author slot and hide it.

    The order genuinely does not matter to the caller and this does not sort:
    "an entry other than the earliest one naming it" and "at least two entries
    name it" are the same set, so deciding which one is the author would be
    work whose answer is never read.
    """
    payload, error = _get_json(f"{site}/api/journal?limit={limit}")
    if error:
        return [], error
    return list(payload.get("entries") or []), None


def measure_nova_push_delivered(since, until, runner=subprocess.run, tool=VAULT_TOOL):
    """Share of asks in the last seven days that reached his phone. A share.

    Read off `ask_push_log`, which `needs_input.ask` and `nudge_ask.nudge`
    write one line to per post, from Agora's own answer. An ask counts when it
    or a later post into the same thread went out, or when Agora held the push
    because he had the thread on screen. The log starts on the day it was
    built, so asks before it are not in the denominator at all. The window is
    the seven days before now, not `since`..`until`, which are Oslo dates.
    """
    del since, until
    from agora_runner import ask_push_log
    try:
        done = runner([sys.executable, tool, "get", ask_push_log.PATH],
                      capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"could not read {ask_push_log.PATH}: {exc}"
    if done.returncode != 0 or (done.stdout or "").startswith("[not found"):
        return None, (f"could not read {ask_push_log.PATH} -- "
                      f"{((done.stderr or '') + (done.stdout or '')).strip()[:200]}")
    return ask_push_log.delivery_share(ask_push_log.parse(done.stdout))


def false_heartbeat_statuses(fetch=None, judge=None, now=None):
    """`(names, error)` -- heartbeats the app shows as on that are not firing.

    The Heartbeats page draws a row's switch from `enabled`. A heartbeat that
    is enabled and overdue on its own schedule is showing "on" for a job that
    is not running, which is a false status on screen. A heartbeat that is off
    shows off and is true, whatever its name says, so it never counts here.
    """
    from agora_runner import heartbeat_liveness
    fetch = fetch or heartbeat_liveness._fetch
    judge = judge or heartbeat_liveness.judge
    rows, error = fetch()
    if error:
        return None, error
    now = now or datetime.now(timezone.utc)
    return [r["name"] for r in (judge(row, now) for row in rows)
            if r.get("verdict") == "overdue"], None


def false_board_statuses(check=None):
    """`(labels, error)` -- board rows showing open that this loop recorded as done.

    `board_done_drift`'s raising bucket, unchanged: a `done` claim against a
    cell that is neither closed nor blocked. It only sees rows a cycle claimed,
    and the detail line says so.
    """
    if check is None:
        from tools import board_done_drift
        check = board_done_drift.check
    findings, _blocked, _mirrors, unreadable, _swept = check()
    if unreadable:
        return None, "; ".join(unreadable)
    return [f"{board} #{number} ({status})" for board, number, status, _ in findings], None


def false_kpi_statuses(rows):
    """`(ids, error)` -- KPIs `/plan` shows on the wrong side of their own range.

    `/plan` bolds *"Out of bounds"* off the written `now` alone. When the
    instrument's reading sits on the other side of a bound, the page is
    showing a breach that has ended or hiding one that has started -- which
    is `kpi_drift_crosses_bounds`, the same test `goal_drift` raises on, so
    the sweep and this count cannot disagree. A blank `now` shows "Not
    measured yet" rather than a status, so it is not counted as a false one.

    `rows` are the sweep's own KPI readings. `None` means this was asked
    outside a sweep, where nothing was measured to compare with.
    """
    if rows is None:
        return None, None
    return [row["id"] for row in rows
            if row.get("value") is not None
            and _as_number(row["kpi"].get("now", "")) is not None
            and kpi_drift_crosses_bounds(row["kpi"], row["value"])], None


#: How long a cycle has to have been awake before the app saying no cycle is
#: running counts as false. The site refreshes Agora's heartbeat record
#: behind a 300-second cache (`nova_site.CADENCE_FRESH_SECONDS`), so for the
#: first five minutes of a cycle "not running" is the cache being honest
#: about its age; twice that keeps a slow refresh from reading as a lie.
RUNNING_BADGE_GRACE_SECONDS = 600


def own_cycle_start(conversation_id=None, heartbeat_id=None, listing=None):
    """`(createdAt, error)` of the hourly cycle this process is running in.

    `(None, None)` when this is not one -- run by hand, or from a weekly
    heartbeat's own conversation -- because only a live hourly cycle is proof
    that a cycle is running, and anything else has nothing to compare.
    """
    from agora_runner.config import NOVA_CYCLE_HEARTBEAT_ID
    from agora_runner.conversation_rotation import cycle_tag
    conversation_id = conversation_id if conversation_id is not None else \
        os.environ.get("AGORA_CONVERSATION_ID", "")
    if not conversation_id:
        return None, None
    if listing is None:
        from agora_runner.http_util import agora_get
        try:
            status, body = agora_get("/conversations")
        except Exception as exc:  # noqa: BLE001 -- any failure is "could not read"
            return None, f"could not list Agora conversations: {exc}"
        if status != 200:
            return None, f"Agora /conversations answered {status}"
        listing = body.get("conversations", [])
    tag = cycle_tag(heartbeat_id or NOVA_CYCLE_HEARTBEAT_ID)
    for conversation in listing:
        if conversation.get("id") == conversation_id:
            if tag not in (conversation.get("tags") or []):
                return None, None
            return conversation.get("createdAt"), None
    return None, None


def false_running_statuses(start=own_cycle_start, site=SITE, fetch=None, now=None):
    """`(labels, error)` -- the app says no cycle is running while this one is.

    The site draws its "running" badge from Agora's heartbeat record. The
    second source is the caller itself: a measurement taken from inside a live
    hourly cycle, past the site's cache grace, is a cycle running, so a badge
    reading `running: false` then is a false status on screen.

    **Only that direction.** A badge left on after every cycle has ended needs
    a reader outside any cycle, so it stays uncompared and is named as such.
    `(None, None)` -- uncompared, not zero -- when not asked from a cycle.
    """
    started, error = start()
    if error:
        return None, error
    if not started:
        return None, None
    try:
        began = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return None, f"unreadable cycle start {started!r}"
    now = now or datetime.now(timezone.utc)
    age = (now - began).total_seconds()
    if age < RUNNING_BADGE_GRACE_SECONDS:
        return None, None
    payload, error = (fetch or _get_json)(f"{site}/api/journal?limit=1")
    if error:
        return None, error
    status = (payload or {}).get("status") or {}
    if "running" not in status:
        return None, "the site's journal status carries no running field"
    if status["running"]:
        return [], None
    return [f"shown not running {int(age // 60)} min into a live cycle"], None


#: The kinds of status the app shows that have no second source to compare
#: with yet. Printed beside every reading, because a count over some of the
#: kinds is not a count over the app.
FALSE_STATUS_NOT_COMPARED = ("a cycle shown running after it ended",)

#: Printed as uncompared too when the KPI half had no sweep readings to use.
FALSE_STATUS_KPI_KIND = "a KPI's now"

#: Printed as uncompared too when not measured from inside a live cycle.
FALSE_STATUS_RUNNING_KIND = "a live cycle shown not running"

#: How `measure_nova_false_status` opens its detail when every kind it could
#: compare came back clean. Named because `_kpi_reading` reads it: that reading
#: is not a 0, but it does end a count written earlier -- see `KPI_NONE_FOUND`.
FALSE_STATUS_NONE_FOUND = "0 in the kinds I can compare"


def measure_nova_false_status(since, until, heartbeats=false_heartbeat_statuses,
                              board=false_board_statuses, kpi_rows=None,
                              running=false_running_statuses):
    """Known false statuses showing in the app right now. A count.

    Four comparisons exist and all are summed: a heartbeat shown on that is
    not firing, a board row shown open that a cycle released as done, a KPI
    `/plan` shows on the wrong side of its range, and a live cycle the app
    shows as not running. The KPI half reads the
    readings the same sweep already took (`kpi_rows` hands them over), so no
    instrument runs twice and this KPI never measures itself.
    **A zero is not reported.** The floor is 0 and a badge left on after a
    cycle ended has no comparison at all, so "0 found" would print as in bounds while part
    of the app went unchecked -- the best reading of this KPI coming off the
    thinnest evidence. Any count above zero is a true floor and is reported.
    """
    del since, until
    found, errors = [], []
    not_compared = list(FALSE_STATUS_NOT_COMPARED)
    compared = []
    for label, measure, kind in (
            ("heartbeat", heartbeats, None), ("board row", board, None),
            ("KPI", lambda: false_kpi_statuses(kpi_rows), FALSE_STATUS_KPI_KIND),
            ("running badge", running, FALSE_STATUS_RUNNING_KIND)):
        names, error = measure()
        if error:
            errors.append(f"could not compare {label} statuses -- {error}")
        elif names is None:
            not_compared.append(kind or label)
        else:
            compared.append(label)
            found.extend(f"{label} {name}" for name in names)
    uncompared = " and ".join(not_compared)
    if found:
        extra = f"; {'; '.join(errors)}" if errors else ""
        return len(found), (f"at least {len(found)}: {', '.join(found)} "
                            f"(not compared: {uncompared}){extra}")
    if errors:
        return None, "; ".join(errors)
    if not compared:
        return None, f"nothing compared -- {uncompared} had no second source"
    return None, (f"{FALSE_STATUS_NONE_FOUND} ({', '.join(compared)}) -- "
                  f"not a reading, because {uncompared} "
                  f"{'has' if len(not_compared) == 1 else 'have'} no second source yet")


def measure_research_reused(since, until):
    """Share of research write-ups a later journal entry has cited. A share.

    **The citation test is a name, and the name has to be the document's.**
    An entry counts as citing one when its prose holds either the path
    (`research/<slug>`) or the filename (`<slug>.md`). A bare slug is not
    enough: `board-records` is a research write-up *and* a document in his own
    project folder, and matching the bare word reads 27 entries about issue
    #203 as citations of a file none of them opened.

    **The first entry that names a write-up is read as the one that wrote it,
    and is not a citation** -- so a write-up needs two naming entries to count,
    and which of them is the author never has to be decided. Nothing in the
    vault records who created a document, so this is the available answer, and
    it makes the reading a floor: a write-up whose author never named it in
    prose loses its first real citation to the rule.

    It is a floor for a second reason as well, and the bigger one -- a cycle
    that reads a write-up and does not name it in its entry is invisible here.
    There is no read log on the vault, so "cited" is what can be measured and
    "read" is not.

    `None` when the folder cannot be listed or holds nothing: a share over no
    write-ups is not 0%, and 0% is the worst reading this key result has.
    """
    del since, until
    slugs, error = research_write_ups()
    if error:
        return None, f"could not read the research folder, so there is no set to judge over -- {error}"
    if not slugs:
        return None, f"{RESEARCH_PREFIX} holds no write-up, so there is no share to take"
    entries, error = _every_journal_entry()
    if error:
        return None, f"could not read the journal, so nothing could have cited anything -- {error}"
    if not entries:
        return None, "the journal API answered with no entry at all"
    texts = [entry_text(e) for e in entries]
    cited = 0
    for slug in slugs:
        needles = (f"research/{slug.lower()}", f"{slug.lower()}.md")
        naming = sum(1 for t in texts if any(n in t for n in needles))
        if naming >= 2:
            cited += 1
    detail = (f"{cited} of {len(slugs)} research write-up(s) are named by an "
              f"entry other than the earliest one that names them, across "
              f"{len(entries)} journal entries; a floor -- a cycle that reads "
              f"one without naming it cannot be counted, and a write-up its "
              f"own author never named loses its first citation to that rule")
    return round(100.0 * cited / len(slugs), 1), detail


# A key result whose measurer goes and reads its subject itself, rather than
# being handed a document this loop already holds. Its own map for
# `KEY_RESULT_PR_MEASURERS`' reason and no other: **the argument shape is
# `(since, until)` and nothing else.** These maps are split by signature, not
# by subject -- it held only the Marcus coach reading until Cycle 1584, and the
# comment here said so in a way that read like a rule about Marcus.
#
# It sits below the measurers rather than beside its siblings because the two
# `maint-` entries are defined further down, next to the KPI they share a sweep
# with.

#: The permission a workflow needs to reach the owner from off this box, and the
#: reason it is the whole test rather than one signal among several.
#:
#: An off-box job here has exactly one channel to him: a GitHub issue, which
#: GitHub itself notifies him about. Nothing running in GitHub Actions in this
#: org holds a Telegram token, a push key or an Agora credential -- those
#: secrets are sealed into the `agents` namespace on server1, which is the box
#: this key result assumes has died. So `issues: write` is not a proxy for
#: "can alert"; it is the only spelling of it that exists here.
OFF_BOX_ALERT_PERMISSION = ("issues", "write")

#: How far back a scheduled run has to have completed for the path to count.
#:
#: 30 days matches the other rolling windows in this file. It is here because
#: **a configured alarm is not an alerting path.** `nova-deadman` declared
#: three cadences at once and GitHub started it zero times across 866 minutes
#: (Cycle 555): the workflow was active, the repo public, the file correct,
#: and nothing would have reached him. Reading the file alone would have
#: counted that as coverage, which is the failure the workflow's own header
#: comment calls "worse than no watchdog".
_OFF_BOX_WINDOW_DAYS = 30


def judge_off_box_alert(document):
    """`why` when this workflow is an off-box alerting path, else `None`.

    `document` is one parsed workflow file. Two conditions, both read off the
    file: it fires on a **schedule** -- a path that only runs when somebody
    presses a button does not survive an outage, because the person who would
    press it is the person being alerted -- and it declares
    `issues: write`, which is the only channel off-box code here has to him.

    A `permissions:` block that is the string `write-all` grants it too, and
    that spelling is accepted rather than missed.
    """
    triggers = _trigger_block(document)
    if not isinstance(triggers, dict) or "schedule" not in triggers:
        return None
    key, needed = OFF_BOX_ALERT_PERMISSION
    grants = document.get("permissions") if isinstance(document, dict) else None
    if isinstance(grants, str):
        if grants.strip() != "write-all":
            return None
        return "permissions: write-all, on a schedule"
    if not isinstance(grants, dict):
        return None
    if str(grants.get(key) or "").strip() != needed:
        return None
    return f"{key}: {needed}, on a schedule"


def alarm_group(document, path):
    """What alarm this workflow file belongs to -- its `concurrency` group.

    **Counting workflow files counts files, not alarms.** `nova-deadman` is
    one alarm written as two files on purpose: the two cadences were split so
    a 30-minute rung could not supersede a daily one in the same occurrence
    queue, and `tools.deadman_check` comments on the one open issue rather
    than opening a second, so both rungs firing on one outage is one alarm
    reaching him once. Read as files that is 2 paths, which is the same
    overcount `agora-kr-nothing-unused` is warned about in the document.

    The `concurrency:` group is the author's own statement that two files are
    one thing, and it is read off the file rather than decided here. A
    workflow with no concurrency block is its own alarm, keyed by its path.
    """
    block = document.get("concurrency") if isinstance(document, dict) else None
    if isinstance(block, str) and block.strip():
        return block.strip()
    if isinstance(block, dict):
        group = block.get("group")
        if isinstance(group, str) and group.strip():
            return group.strip()
    return path


def fetch_workflow_runs(repo, workflow, runner=subprocess.run):
    """Every recorded run of one workflow in one repo, newest first.

    `(runs, None)` or `(None, why)`. An **empty list is a reading here**, not
    an error, and that is the opposite call from `fetch_docs_sync_runs`: that
    function asks about a workflow known to run, so nothing is far more likely
    to be a name that resolved to nothing. This one asks the question "has
    GitHub ever actually started this?", and zero is the answer it exists to
    be able to give.
    """
    try:
        done = runner(
            ["gh", "run", "list", "--repo", repo, "--workflow", workflow,
             "--limit", "100", "--json", "event,status,conclusion,createdAt"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return None, f"gh run list could not run on {repo}: {exc}"
    if done.returncode != 0:
        return None, (f"gh run list failed on {repo} for {workflow}: "
                      f"{done.stderr.strip()[:200]}")
    try:
        runs = json.loads(done.stdout or "[]")
    except json.JSONDecodeError as exc:
        return None, f"gh returned unreadable JSON for {workflow}: {exc}"
    if not isinstance(runs, list):
        return None, "gh returned a JSON object where a list of runs was expected"
    return runs, None


def measure_nas_off_box_watch(since, until, fetch=None, list_repos=None,
                              runs=None):
    """Alerting paths that survive server1 going down. A count.

    `now` was a hand count of 0, taken on the true observation that Agora
    push, the WhatsApp bridge and nova-site all run in `agents` on server1.
    That is right about the cluster and it misses the one execution
    environment in this system that is not the cluster: **GitHub Actions**.

    So a path counts when three things hold, and all three are read rather
    than decided. It is a scheduled workflow in this org (`judge_off_box_alert`
    -- off-box because GitHub runs it, scheduled because nobody is there to
    press the button). It can reach him (`OFF_BOX_ALERT_PERMISSION`). And
    **GitHub has actually completed a scheduled run of it inside
    `_OFF_BOX_WINDOW_DAYS`** -- a `workflow_dispatch` run proves only that a
    button works, and this account's scheduler has a measured history of
    running nothing at all for days.

    That last condition is the one that makes this an instrument instead of a
    second hand count, and it can genuinely go either way: the same workflow
    that satisfies it today satisfied only the first two for the 866 minutes
    Cycle 555 measured.

    **A repo that could not be read returns `None` for the whole measure.**
    The direction is up from 0 toward 1, so a dropped repo hides exactly the
    path this key result is waiting for.
    """
    del since
    from tools import running_images, security_alerts

    fetch = fetch or running_images.fetch_manifests
    list_repos = list_repos or security_alerts.repos_in_org
    runs = runs or fetch_workflow_runs
    live, error, _archived = list_repos(SELF_DOCUMENTING_ORG)
    if error:
        return None, f"could not list {SELF_DOCUMENTING_ORG}'s repos: {error}"
    if not live:
        return None, (f"{SELF_DOCUMENTING_ORG} answered with no live repo at "
                      "all, which is a failed read rather than an empty org")

    def read(repo):
        files, why = fetch(repo=repo, suffixes=(".yml", ".yaml"))
        if why:
            return repo, None, why
        return repo, {path: text for path, text in files.items()
                      if path.startswith(".github/workflows/")}, None

    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for repo, workflows, why in pool.map(read, sorted(live)):
            if why:
                return None, f"could not read {repo}'s workflows: {why}"
            for path in sorted(workflows):
                try:
                    document = yaml.safe_load(workflows[path])
                except yaml.YAMLError:
                    continue
                grant = judge_off_box_alert(document)
                if grant:
                    candidates.append((repo, path.split("/")[-1], grant,
                                       alarm_group(document, path)))

    until_date = date.fromisoformat(until)
    window_start = until_date - timedelta(days=_OFF_BOX_WINDOW_DAYS)

    def history(candidate):
        repo, workflow, grant, group = candidate
        found, why = runs(repo, workflow)
        return repo, workflow, grant, group, found, why

    alive, asleep = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for repo, workflow, grant, group, found, why in pool.map(history,
                                                                 candidates):
            if why:
                return None, f"could not read runs of {workflow}: {why}"
            newest = None
            for run in found:
                if (run.get("event") or "") != "schedule":
                    continue
                if (run.get("status") or "") != "completed":
                    continue
                day = _oslo_day(str(run.get("createdAt") or ""))
                if day is None:
                    continue
                when = date.fromisoformat(day)
                if window_start < when <= until_date and (newest is None
                                                          or day > newest):
                    newest = day
            key = (repo, group)
            if newest:
                alive.setdefault(key, []).append((workflow, grant, newest))
                asleep.pop(key, None)
            elif key not in alive:
                asleep.setdefault(key, []).append(workflow)

    named = ", ".join(
        f"{repo}/{group} ({', '.join(sorted(w for w, _g, _d in rungs))}; "
        f"{rungs[0][1]}; last scheduled run "
        f"{max(day for _w, _g, day in rungs)})"
        for (repo, group), rungs in sorted(alive.items())) or "none"
    quiet = ", ".join(f"{repo}/{group}"
                      for repo, group in sorted(asleep)) or "none"
    detail = (f"{len(alive)} alerting path(s) run off this box and have "
              f"completed a scheduled run in the "
              f"{_OFF_BOX_WINDOW_DAYS}d window {window_start.isoformat()}.."
              f"{until} Oslo: {named}. Swept {len(live)} repo(s) in "
              f"{SELF_DOCUMENTING_ORG} and found {len(candidates)} workflow "
              f"file(s) in {len(alive) + len(asleep)} alarm(s) that a schedule "
              f"starts and that can open an issue -- an alarm is a concurrency "
              f"group, because nova-deadman is two files on purpose; "
              f"{len(asleep)} alarm(s) have had no scheduled run complete in "
              f"the window ({quiet}) and are configuration rather than a path. "
              "Everything in the cluster is excluded by construction -- "
              "Agora push, the WhatsApp bridge and nova-site all run in "
              "agents on server1, so the box they would report on is the box "
              "they die with")
    return len(alive), detail


#: The kinds this measure reads provenance off. A cluster change lands on one
#: of these; a Pod is deliberately absent, because a Pod is written by its own
#: controller and never by a manifest, so counting Pods would drown every real
#: change in reconciliation noise.
SELF_SERVICE_KINDS = ("deploy,statefulset,daemonset,cronjob,service,configmap,"
                      "ingress")

#: Field managers that mean a committed manifest reconciled the object. Argo CD
#: is the only GitOps engine on this cluster, and it writes under three names --
#: the controller, the server (a UI-driven sync) and the application controller.
SELF_SERVICE_GITOPS = ("argocd-controller", "argocd-server",
                       "argocd-application-controller")

#: Field managers that mean somebody drove the change by hand. Every `kubectl`
#: verb stamps its own name (`kubectl-rollout`, `kubectl-client-side-apply`,
#: `kubectl-edit`, ...), so the prefix carries them all; `k9s` is the same act
#: through a terminal UI, and a `helm` release run at a prompt is a manual step
#: even though a chart is a manifest, because nothing committed produced it.
SELF_SERVICE_MANUAL_PREFIX = "kubectl"
SELF_SERVICE_MANUAL_EXACT = ("k9s", "helm")


def classify_field_manager(manager):
    """`"gitops"`, `"manual"`, or `None` for a manager that is neither.

    The third answer is the one that makes this measure honest. Most field
    managers on this cluster are neither side of the question -- `k3s` writing
    a status, `Reloader` bouncing a Deployment after a ConfigMap changed,
    Crossplane composing a resource, `deploy@server1` laying down k3s's own
    bundled manifests. None of those is a change anybody made; folding them
    into either half would move the share without anything having happened.
    """
    name = (manager or "").strip()
    if not name:
        return None
    if name in SELF_SERVICE_GITOPS or name.startswith("argocd"):
        return "gitops"
    if name in SELF_SERVICE_MANUAL_EXACT:
        return "manual"
    if name == SELF_SERVICE_MANUAL_PREFIX or name.startswith(
            SELF_SERVICE_MANUAL_PREFIX + "-"):
        return "manual"
    return None


def measure_infra_self_service(since, until):
    """Share of in-window cluster changes that came through a committed manifest.

    This key result carried a blank `now:` for its whole life and three cycles
    wrote down the same reason -- no counter separates a change made by
    committing a manifest from one made by hand. There is one, and it is on
    every object: `metadata.managedFields`. Server-side apply records, per
    object, which field manager last wrote which fields and when, and the
    manager name says how the change arrived. Argo CD reconciling a commit
    writes as `argocd-controller`; a `kubectl rollout restart` writes as
    `kubectl-rollout`. Both halves were read live before this was written:
    inside a seven-day window ending 2026-09-14 the cluster carried 13 Argo CD
    entries and 3 `kubectl-client-side-apply` entries, the latter a `marcus-test`
    Deployment, Service and Ingress stood up by hand in the `test` namespace.

    **An entry is a manager that touched an object, not a change event.**
    Kubernetes keeps one entry per (manager, operation, subresource) and moves
    its timestamp forward, so two `kubectl edit`s on one Deployment in the same
    week read as one. That makes the manual half a floor and the share a
    ceiling, which is the safe direction for a measure whose target is 100 and
    is said in the detail rather than hidden.

    **Nothing here can tell my hand from the owner's.** `managedFields` records
    no operator identity, so a `kubectl` this loop ran and one the owner ran at
    his own keyboard are the same entry. The key result says "changes this loop
    makes"; this reads every change anybody made. That is wider than the wording
    and it is named in the detail, because the alternative -- guessing which
    manual entries were mine -- would be a number I invented.

    **No reading when the window is empty.** The target is 100, so the flattering
    answer is 100, and a week in which nothing changed would produce it out of a
    zero denominator. An unreadable cluster, a cluster reporting no objects, and
    a window with no classified entry in it all return no number and say why.

    **And no reading when nothing in the window was by hand.** A hand deletion
    takes the object and its managedFields with it, so it is never counted, and
    it wipes the manual entries of whatever it deleted. Measured 2026-09-16:
    this read 81.2 in the morning, the three by-hand entries were the
    `marcus-test` objects, a cycle deleted them with `kubectl delete` at 14:45,
    and the next sweep read 14 of 14 -- the target -- off a week in which no
    change had moved into git. So 100 is what this shows whether or not a change
    bypassed git, and a share only counts when a manual entry proves the manual
    half was visible at all.
    """
    try:
        done = subprocess.run(["kubectl", "get", SELF_SERVICE_KINDS,
                               "-A", "-o", "json", "--show-managed-fields"],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl could not read the cluster's objects: {exc}"
    if done.returncode != 0:
        blob = (done.stderr or done.stdout or "").strip()
        first = blob.splitlines()[0] if blob else "exited %d" % done.returncode
        return None, f"kubectl could not read the cluster's objects: {first}"
    try:
        body = json.loads(done.stdout)
    except ValueError as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"
    items = body.get("items")
    if not items:
        return None, (f"kubectl reports no {SELF_SERVICE_KINDS} anywhere in this "
                      "cluster, which is no instrument rather than a cluster "
                      "nobody changed")

    start = f"{since}T00:00:00+00:00"
    end = f"{until}T23:59:59+00:00"
    gitops, manual = [], []
    seen_manual_ever = 0
    for item in items:
        meta = item.get("metadata") or {}
        name = (meta.get("name") or "").strip()
        namespace = (meta.get("namespace") or "").strip()
        kind = (item.get("kind") or "").strip()
        for field in meta.get("managedFields") or []:
            side = classify_field_manager(field.get("manager"))
            if side == "manual":
                seen_manual_ever += 1
            stamp = (field.get("time") or "").strip()
            if side is None or not stamp:
                continue
            if not start <= stamp.replace("Z", "+00:00") <= end:
                continue
            where = f"{namespace}/{kind} {name} by {field.get('manager')}"
            (gitops if side == "gitops" else manual).append(where)

    total = len(gitops) + len(manual)
    if not total:
        return None, (f"no object in this cluster was written by Argo CD or by "
                      f"hand between {since} and {until}, so there is no "
                      f"denominator to take a share over -- and 100 is this "
                      f"measure's target, so reporting it off an empty window "
                      f"would be the best possible reading of nothing")
    detail = (f"{len(gitops)} of {total} recorded change(s) to {len(items)} "
              f"object(s) between {since} and {until} came from Argo CD rather "
              f"than from a hand at a keyboard. Kubernetes keeps one "
              f"managedFields entry per manager and moves its timestamp, so two "
              f"edits by the same tool in one window read as one -- the manual "
              f"half is a floor and this share a ceiling. managedFields records "
              f"no operator identity, so a manual change of yours counts the "
              f"same as one of mine ({seen_manual_ever} manual entry/entries "
              f"exist on these objects in total, in and out of window)")
    if not manual:
        return None, (detail + " -- not a reading: nothing in the window was by "
                      "hand, and a hand deletion removes the object's "
                      "managedFields with it, so 100 is what this shows whether "
                      "or not a change bypassed git")
    detail += ". By hand: " + ", ".join(sorted(manual)[:8])
    return round(100.0 * len(gitops) / total, 1), detail


#: Open board rows I have judged to propose new recurring spend, keyed by
#: `(board, number)`, each with the NOK per month it proposes and why that
#: figure is that figure. This is the one judgement in the measure and it is
#: written down rather than hidden, the same way `_CHAT_BASICS_NOT_A_CONTROL`
#: is: deciding that a row proposes new money is mine, and deciding whether
#: the row is still open is the board's. The second half is the one that goes
#: stale, and it is the half that was making this key result a typed digit --
#: the day he buys the server or closes the row, a hand-typed 600 keeps saying
#: 600 forever.
_PROPOSED_SPEND_ROWS = {
    ("ideas", 179): (600, "a second node from the Hetzner auction to relieve "
                          "server1's memory pressure, at his own stated "
                          "budget of max 600 NOK/month"),
}

#: What a currency amount looks like in a board row's title. Deliberately
#: loose -- it is a net for rows I have not judged yet, not a parser, and a
#: false positive here costs one named row in the detail while a false
#: negative costs the whole claim.
_MONEY_IN_A_TITLE = re.compile(r"\bNOK\b|\bkr\b|\bUSD\b|\bEUR\b|[$\u20ac\u00a3]",
                               re.IGNORECASE)


def measure_infra_no_new_money(since, until):
    """NOK per month of new spend currently proposed to keep the cluster up.

    `infra-kr-no-new-money`. Target 0, direction down, so **a low reading is
    the good one and therefore the dangerous one** -- and here the best value
    is also the loudest claim this loop can make about his wallet. Everything
    below is about making a 0 impossible to reach without having looked.

    The reading is a sum over rows, not over prose: a row proposes spend
    because I said it does, and it counts because the board still says it is
    open. A row he closes -- by buying the thing or by deciding against it --
    drops out of the sum on the next sweep with no cycle retyping anything.

    Four ways this could print a wrong number, each closed:

    * **Either board unreadable is no reading at all.** Half a sweep
      undercounts a measure whose target is 0, the same call
      `measure_nas_unattended` makes.
    * **A registered row the boards do not carry is a broken instrument, not
      a closed proposal.** The numbers here are typed into this file and the
      board confirms nothing about them, so a renumbered or deleted row would
      read as the proposal having gone away.
    * **A row I have not judged is named, never silently dropped.** Any open
      row whose title carries a currency amount and is not in the registry is
      printed, because the registry cannot see a proposal made after it was
      written.
    * **A 0 with unjudged candidates outstanding is refused.** Zero is this
      measure's target, so publishing it over rows I have not read would be
      the one reading nobody would question. With something registered still
      open the candidates are only a floor caveat and the detail says so.

    Titles only: a board row the site serves carries `title` and no body, so
    the net is cast over exactly the text there is.
    """
    del since, until
    rows = {}
    for name in ("issues", "ideas"):
        items, error = fetch_board(name)
        if error:
            return None, (f"the {name} board could not be read, and half a "
                          f"sweep undercounts a measure whose target is 0: "
                          f"{error}")
        for row in items:
            rows[(name, row.get("number"))] = row

    missing = [key for key in _PROPOSED_SPEND_ROWS if key not in rows]
    if missing:
        named = ", ".join(f"{board} #{number}" for board, number in
                          sorted(missing, key=lambda k: (k[0], k[1] or 0)))
        return None, (f"{named} is registered here as proposing new spend and "
                      "is on neither board, which is a renumbered or deleted "
                      "row far more often than it is a withdrawn proposal")

    open_spend, closed_spend = [], []
    for key, (nok, why) in sorted(_PROPOSED_SPEND_ROWS.items(),
                                  key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        board, number = key
        status = (rows[key].get("statusKey") or "").strip()
        (closed_spend if status in _CLOSED_STATUS_KEYS
         else open_spend).append((board, number, nok, why))

    unjudged = []
    for (board, number), row in rows.items():
        if (board, number) in _PROPOSED_SPEND_ROWS:
            continue
        if (row.get("statusKey") or "").strip() in _CLOSED_STATUS_KEYS:
            continue
        if _MONEY_IN_A_TITLE.search(row.get("title") or ""):
            unjudged.append(f"{board} #{number}")

    total = sum(nok for _board, _number, nok, _why in open_spend)
    if total == 0 and unjudged:
        return None, (
            "nothing registered here is still open, but "
            f"{len(unjudged)} open row(s) name a currency amount and I have "
            f"not judged them ({', '.join(sorted(unjudged))}) -- 0 is this "
            "measure's target and it may not be published over a row nobody "
            "has read")

    if open_spend:
        proposals = "; ".join(f"{board} #{number} {nok} NOK/month ({why})"
                              for board, number, nok, why in open_spend)
    else:
        proposals = "none"
    detail = (f"{total} NOK per month proposed across {len(open_spend)} open "
              f"row(s), read live from the issues and ideas boards: "
              f"{proposals}")
    if closed_spend:
        detail += ("; " + ", ".join(
            f"{board} #{number}" for board, number, _nok, _why in closed_spend)
            + " is registered here and is now done or outdated, so it no "
              "longer counts")
    if unjudged:
        detail += ("; a floor -- " + ", ".join(sorted(unjudged)) +
                   " also name a currency amount and are not judged here")
    else:
        detail += ("; no other open row on either board names a currency "
                   "amount")
    return total, detail


KEY_RESULT_FETCH_MEASURERS = {
    "marcus-kr-coach-first-try": measure_marcus_coach_first_try,
    "maint-kr-supported": measure_maint_supported,
    "demos-kr-opened": measure_demos_opened,
    "demos-kr-no-litter": measure_demos_no_litter,
    "nova-kr-trust-data-fresh": measure_nova_trust_data_fresh,
    "nova-kr-trust-cycles-shown": measure_nova_trust_cycles_shown,
    "nova-kr-control-stop-coverage": measure_nova_control_stop_coverage,
    "nova-kr-control-stop-seconds": measure_nova_control_stop_seconds,
    "nova-kr-scale-blocks-recorded": measure_nova_scale_blocks_recorded,
    "infra-kr-outlives-the-box": measure_infra_outlives_the_box,
    "docs-kr-covers-what-runs": measure_docs_covers_what_runs,
    "docs-kr-sync-alive": measure_docs_sync_alive,
    "nas-kr-unattended": measure_nas_unattended,
    "nas-kr-off-box-watch": measure_nas_off_box_watch,
    "maint-kr-self-documenting": measure_maint_self_documenting,
    "agora-kr-chat-basics": measure_agora_chat_basics,
    "agora-kr-nothing-unused": measure_agora_nothing_unused,
    "post-kr-editor": measure_post_editor,
    "post-kr-readership": measure_post_readership,
    "wa-kr-reaches-you": measure_wa_reaches_you,
    "infra-kr-self-service": measure_infra_self_service,
    "infra-kr-no-new-money": measure_infra_no_new_money,
}


KPI_MEASURERS = {
    "nova-kpi-dropped-ticks": measure_nova_dropped_ticks,
    "nova-kpi-cost-per-cycle": measure_nova_cost_per_cycle,
    "nova-kpi-silent-cycles": measure_nova_silent_cycles,
    "pm-kpi-deprecations": measure_pm_deprecations,
    "marcus-kpi-push-subscribers": measure_marcus_push_subscribers,
    "marcus-kpi-coach-latency": measure_marcus_coach_latency,
    "nova-kpi-unfixed-advisories": measure_nova_unfixed_advisories,
    "nova-kpi-markdown-board-readers": measure_nova_markdown_board_readers,
    "marcus-kpi-browser-monolith": measure_marcus_browser_monolith,
    "nova-kpi-browser-monolith": measure_nova_browser_monolith,
    "agora-kpi-metered-spend": measure_agora_metered_spend,
    "docs-kpi-staleness": measure_docs_staleness,
    "post-kpi-volume": measure_post_volume,
    "nas-kpi-services-down": measure_nas_services_down,
    "infra-kpi-ci-minutes": measure_infra_ci_minutes,
    "infra-kpi-node-headroom": measure_infra_node_headroom,
    "maint-kpi-eol-unjudged": measure_maint_eol_unjudged,
    "agora-kpi-mcp-deprecated": measure_agora_mcp_current,
    "pm-kpi-pins-current": measure_maint_pins_current,
    "pm-kpi-research-reused": measure_research_reused,
    "nova-kpi-push-delivered": measure_nova_push_delivered,
    "nova-kpi-false-status": measure_nova_false_status,
}

#: KPIs whose measurer judges the other KPIs' readings rather than a source of
#: its own. `kpi_rows` runs them last and hands them the rows it already took.
KPI_MEASURERS_OVER_ROWS = frozenset({"nova-kpi-false-status"})

#: A KPI whose measurer refuses to report its best value, mapped to the detail
#: it opens with when it looked and found nothing. **Such a KPI latches.** The
#: refusal is right -- 0 off a partial sweep is the best reading coming off the
#: thinnest evidence -- but `write_back_kpis` skips a `None`, so a count
#: written while a false status was showing stayed on `/plan` after the status
#: cleared: `now: 1`, out of bounds, with nothing left to find. Measured Cycle
#: 1724: the one false status was idea #193, and nothing would ever have
#: written the 1 away. A found-nothing reading does not say 0, but it does say
#: the written count is a breach that ended, so the row is marked `stale_now`,
#: the write blanks `now` and `--exit-on-drift` counts it. An unreadable sweep
#: opens with something else and leaves the written number alone.
KPI_NONE_FOUND = {"nova-kpi-false-status": FALSE_STATUS_NONE_FOUND}

#: A KPI with no instrument, and why. Written down here rather than left as a
#: silent gap, for the reason `KEY_RESULT_NO_INSTRUMENT` exists: a blank `now`
#: says nothing about whether anyone tried, and three cycles re-deriving the
#: same "there is no endpoint for this" is three cycles spent twice.
#:
#: The entries are the must-be floors the owner's goal chain found for Nova the
#: app on 2026-09-16 (Cycle 1689) that nothing here can read yet;
#: `nova-kpi-push-delivered` got its measurer in Cycle 1690 and
#: `nova-kpi-false-status` in Cycle 1691. Delete an entry in
#: the same change that adds its measurer.
KPI_NO_INSTRUMENT = {
    "nova-kpi-owner-only-controls": (
        "the app has no login of its own -- the tailnet is the only identity "
        "check and the in-cluster port answers without one -- so who counts as "
        "the owner has to be decided before a control can be counted"),
}


def kpi_rows(sections, since=None, until=None):
    """Pair every KPI in `project-goals.md` with a measurement, or with a why.

    The mirror of `key_result_rows`, and it is deliberately a separate function
    rather than a flag on that one: a KPI has `low`/`high` where a key result
    has `target`, it is read over its own 24h window, and rule 4 of issue #227
    says a KPI may never be used as a key result. One function serving both
    would be the first place that distinction quietly stops being enforced.
    """
    out, deferred = [], []
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
            if kpi_id in KPI_MEASURERS_OVER_ROWS:
                deferred.append((len(out), row, measurer))
                out.append(row)
                continue
            out.append(_kpi_reading(row, measurer(since, until)))
    measured = [r for r in out if "value" in r]
    for index, row, measurer in deferred:
        out[index] = _kpi_reading(row, measurer(since, until, kpi_rows=measured))
    return out


def _kpi_reading(row, reading):
    value, detail = reading
    if value is None:
        out = {**row, "value": None, "detail": f"not measured — {detail}"}
        prefix = KPI_NONE_FOUND.get(row["id"])
        if (prefix and str(detail).startswith(prefix)
                and str(row["kpi"].get("now", "")).strip()):
            out["stale_now"] = True
        return out
    return {**row, "value": value, "detail": detail}


def render_kpis(rows, path):
    lines = [f"KPIs — {path}"]
    for row in rows:
        written = str(row["kpi"].get("now", "")).strip()
        lines.append(f"  {row['project']} / {row['id']}")
        if row["value"] is None:
            lines.append(f"      {path} says now: {written or '(blank)'} — {row['detail']}")
            if row.get("stale_now"):
                lines.append(f"      <- the document says {written}, which this "
                             "sweep no longer finds: a breach that ended")
            continue
        low = str(row["kpi"].get("low", "")).strip()
        high = str(row["kpi"].get("high", "")).strip()
        bounds = f"  [{low or '-'}..{high or '-'}]"
        drift = ""
        if has_drifted(written, row["value"]):
            drift = (f"  <- the document says {written or '(blank)'}, drifted"
                     if kpi_drift_crosses_bounds(row["kpi"], row["value"])
                     else f"  <- the document says {written}, moved inside "
                          "its own range")
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
        if row["value"] is None and not row.get("stale_now"):
            continue
        written = str(row["kpi"].get("now", "")).strip()
        if row["value"] is not None and _as_number(written) == _as_number(row["value"]):
            continue
        amended = set_field_in_kpi(text, row["id"], "now",
                                   "" if row["value"] is None else row["value"])
        if amended is None:
            lines.append(f"  ! {row['id']}: could not edit that kpi fence, "
                         f"left at {written or '(blank)'}")
            continue
        text, changed = amended, changed + 1
        shown = "(blank)" if row["value"] is None else row["value"]
        lines.append(f"  {row['id']}  now: {written or '(blank)'} -> {shown}")
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
                    since=None, until=None, prs=None, boards=None,
                    expectations=None, decisions=None):
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
            exp_measurer = KEY_RESULT_EXPECTATION_MEASURERS.get(kr_id)
            if exp_measurer is not None:
                value, detail = exp_measurer(expectations, boards, since, until)
                if value is None:
                    out.append({**row, "value": None,
                                "detail": f"not measured — {detail}"})
                    continue
                out.append({**row, "value": value, "detail": detail})
                continue
            dec_measurer = KEY_RESULT_DECISION_MEASURERS.get(kr_id)
            if dec_measurer is not None:
                value, detail = dec_measurer(decisions, since, until)
                if value is None:
                    out.append({**row, "value": None,
                                "detail": f"not measured — {detail}"})
                    continue
                out.append({**row, "value": value, "detail": detail})
                continue
            pr_measurer = KEY_RESULT_PR_MEASURERS.get(kr_id)
            if pr_measurer is not None:
                value, detail = pr_measurer(prs, since, until)
                if value is None:
                    out.append({**row, "value": None,
                                "detail": f"not measured — {detail}"})
                    continue
                out.append({**row, "value": value, "detail": detail})
                continue
            fetch_measurer = KEY_RESULT_FETCH_MEASURERS.get(kr_id)
            if fetch_measurer is not None:
                value, detail = fetch_measurer(since, until)
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
        drift = ""
        if has_drifted(written, row["value"]):
            drift = (f"  <- the document says {written or '(blank)'}, drifted"
                     if kr_drift_crosses_target(row["kr"], row["value"])
                     else f"  <- the document says {written}, moved without "
                          "crossing its target")
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


def drift_status(rows, kr_rows, kpis, goals_name, project_goals_name=None):
    """The drift verdict as `(lines, drifted)`: what to append, and what counts.

    Lifted out of `main` so it can be driven from a test with three lists of
    rows instead of a live measurement run -- `main` reaches the network for
    every one of the 46 numbers, so the only previously available test of
    this block was to read it.

    `drifted` is the list the exit code is built from and it deliberately
    holds neither the moved KPIs nor the unconfirmed numbers; see
    `kpi_drift_crosses_bounds` and `publishes_unconfirmed_number` for why
    each of those is printed without raising.
    """
    goals_name = os.path.basename(goals_name)
    drifted = [
        f"{row['key']} in {goals_name}"
        for row in rows if has_drifted(row["goal"].get("now", ""), row["value"])
        and kr_drift_crosses_target(row["goal"], row["value"])
    ]
    # A goal or key result that moved without crossing its target is named
    # and not counted -- see `kr_drift_crosses_target`.
    moved_kr = [
        f"{row['key']} in {goals_name}"
        for row in rows if has_drifted(row["goal"].get("now", ""), row["value"])
        and not kr_drift_crosses_target(row["goal"], row["value"])
    ]
    instrumented = [row for row in rows if row["value"] is not None]
    unconfirmed = [
        f"{row['key']} in {goals_name}"
        for row in rows
        if publishes_unconfirmed_number(row["goal"].get("now", ""), row["value"])
    ]
    moved = []
    if project_goals_name:
        pg_name = os.path.basename(project_goals_name)
        drifted += [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kr_rows
            if has_drifted(row["kr"].get("now", ""), row["value"])
            and kr_drift_crosses_target(row["kr"], row["value"])
        ]
        moved_kr += [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kr_rows
            if has_drifted(row["kr"].get("now", ""), row["value"])
            and not kr_drift_crosses_target(row["kr"], row["value"])
        ]
        # A KPI that drifted without crossing a bound is reported and not
        # counted -- see `kpi_drift_crosses_bounds` for why the digit is
        # not the claim a guardrail makes. It is named here rather than
        # dropped, because the vault still carries a number an hour old
        # and a cycle repairing it should be able to see which.
        moved += [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kpis
            if has_drifted(row["kpi"].get("now", ""), row["value"])
            and not kpi_drift_crosses_bounds(row["kpi"], row["value"])
        ]
        drifted += [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kpis
            if has_drifted(row["kpi"].get("now", ""), row["value"])
            and kpi_drift_crosses_bounds(row["kpi"], row["value"])
        ] + [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kpis if row.get("stale_now")
        ]
        instrumented += [row for row in kr_rows + kpis
                         if row["value"] is not None or row.get("stale_now")]
        unconfirmed += [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kr_rows
            if publishes_unconfirmed_number(row["kr"].get("now", ""), row["value"])
        ] + [
            f"{row['project']} / {row['id']} in {pg_name}"
            for row in kpis
            if publishes_unconfirmed_number(row["kpi"].get("now", ""), row["value"])
            and not row.get("stale_now")
        ]
    lines = ""
    for line in drifted:
        lines += f"\n  ! {line} no longer matches its instrument"
    for line in moved:
        lines += (f"\n  - {line} moved inside its own range -- reported, "
                  "not counted")
    for line in moved_kr:
        lines += (f"\n  - {line} moved without crossing its target -- "
                  "reported, not counted until the weekly --repair")
    # Printed with its own marker and deliberately NOT counted as drift.
    # A number nothing could confirm is not a number that disagrees with its
    # instrument, and an instrument whose history is still empty is a thing
    # to wait for rather than a defect a pull request closes -- the same call
    # the orphan list and `project_goals_check` make. What was missing was
    # not a verdict, it was the sentence.
    for line in unconfirmed:
        lines += (f"\n  ? {line} publishes a number no instrument could "
                  "confirm this sweep")
    # The summary is deliberately the LAST line: `tools.preflight` shows
    # one line per check and takes the last one, so a count that prints
    # above `WHAT THIS CANNOT SEE` would be invisible in the sweep.
    lines += (
        f"\nDRIFT — {len(drifted)} of {len(instrumented)} instrumented "
        f"number(s) disagree with what is written down. A number with no "
        f"reading to take is not counted either way"
        + (f", and {len(moved)} KPI(s) moved without leaving their "
           "own range, which is a snapshot ageing rather than a finding"
           if moved else "")
        + (f", and {len(moved_kr)} key result(s) moved without crossing "
           "their target, which the weekly goals run writes back"
           if moved_kr else "")
        + (f", and {len(unconfirmed)} published number(s) had no reading to "
           "confirm them at all"
           if unconfirmed else "") + ".")
    return lines, drifted


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
    parser.add_argument("--expectations", default=None,
                        help="path to a copy of expectations.md; without it "
                             "pm-kr-calibration reports as not measured rather "
                             "than as having no instrument")
    parser.add_argument("--decisions", default=None,
                        help="path to a copy of decisions.md; without it "
                             "pm-kr-reversals reports as not measured rather "
                             "than as having no instrument")
    parser.add_argument("--write", action="store_true",
                        help="write each measured value into the --goals file's "
                             "own `now:` field, in place (default: report only)")
    parser.add_argument("--exit-on-drift", action="store_true",
                        help="exit 2 when any written `now:` disagrees with its "
                             "instrument, and end the report with a one-line "
                             "count (default: report only, always exit 0)")
    args = parser.parse_args(argv)

    if args.exit_on_drift and args.write:
        # A repairer and a watcher are opposite jobs on one document: --write
        # makes the drift go away, so a status taken after it would always be
        # clean and would say nothing was ever stale.
        parser.error("--exit-on-drift and --write are opposites: one repairs "
                     "the drift, the other reports it")

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
    expectations = None
    if args.expectations:
        from agora_runner.expectations import parse_expectations, problems as _exp_problems
        try:
            exp_text = open(args.expectations, encoding="utf-8").read()
        except OSError as exc:
            print(f"could not read {args.expectations}: {exc}", file=sys.stderr)
            return 1
        expectations = parse_expectations(exp_text)
        problems.extend(_exp_problems(expectations))

    decisions = None
    if args.decisions:
        from agora_runner.decisions import parse_decisions, problems as _dec_problems
        try:
            dec_text = open(args.decisions, encoding="utf-8").read()
        except OSError as exc:
            print(f"could not read {args.decisions}: {exc}", file=sys.stderr)
            return 1
        decisions = parse_decisions(dec_text)
        problems.extend(_dec_problems(decisions))

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
                                  since, until, prs, boards, expectations,
                                  decisions)
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

    drifted_rows = []
    if args.exit_on_drift:
        tail, drifted_rows = drift_status(
            rows, kr_rows if args.project_goals else [],
            kpis if args.project_goals else [],
            args.goals, args.project_goals)
        report += tail
    print(report)
    if args.exit_on_drift and drifted_rows:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
