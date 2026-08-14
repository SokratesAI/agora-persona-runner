"""The display half of Edvard's #72 -- a dead cycle you can see on screen.

> Mismatch between Nova and agora cycles. Nova is 1 behind agora. Agora
> failed a cycle Journal and you did not catch it. You do not have good
> enough system set up to catch if the previous cycle failed or if any
> cycle in the past failed or is missing.

`cycle_health` answers this for Nova (it now runs at heartbeat dispatch,
Cycle 148). Nothing answered it for Edvard, who found cycles 127 and 128
himself by noticing the feed jump from 126 to 129.

The two halves are tested apart because they fail apart: the holes are
history and pure, the stall is a judgement about right now and is the one
that a cache can freeze.
"""

from datetime import datetime, timedelta

from agora_runner.config import NOVA_PERSONA_ID, OSLO
from agora_runner.cycle_health import (
    STALL_GRACE_INTERVALS,
    gaps_between,
    missing_cycles,
)
from agora_runner.nova_journal import JOURNAL_DIR, build_status, parse_journal
from agora_runner.nova_site import (
    _fetch_cadence_minutes,
    _refresh_cadence,
    _with_silence,
    cadence_minutes,
    journal_page,
    reset_cadence,
)

NOW = datetime(2026, 8, 12, 23, 0, tzinfo=OSLO)


def _journal(*cycles):
    """A journal document holding one entry per cycle number given.

    Every entry carries the same stamp as `NOW`, so the silence under test
    is entirely the offset each test passes and not an artefact of how far
    apart the fixture's own entries are.
    """
    return "\n".join(
        f"### 2026-08-12 23:00 (Oslo) — Cycle {n}\n\nBody {n}.\n\n"
        f"---\nPR: none | Outcome: merged\n"
        for n in cycles
    )


def _entries(*cycles):
    return parse_journal(_journal(*cycles))


# --- the holes, which are history -----------------------------------------


def test_the_status_names_the_cycles_that_wrote_no_entry():
    status = build_status(_entries(129, 126, 125))
    assert status["missingCycles"] == [127, 128]


def test_a_journal_with_no_holes_reports_none():
    assert build_status(_entries(129, 128, 127))["missingCycles"] == []


def test_an_entry_with_no_cycle_number_is_not_a_hole():
    """Edvard's own notes carry no `Cycle N`, so they cannot be missing.

    They are real entries and they sit in the feed between numbered ones.
    Counting them into the range would invent a gap out of a note.
    """
    markdown = (
        "### 2026-08-12 09:00 (Oslo) — Cycle 129\n\nBody.\n\n"
        "---\nPR: none | Outcome: merged\n"
        "### 2026-08-12 08:00 (Oslo) — a note from Edvard\n\nBody.\n\n"
        "### 2026-08-12 07:00 (Oslo) — Cycle 128\n\nBody.\n\n"
        "---\nPR: none | Outcome: merged\n"
    )
    assert build_status(parse_journal(markdown))["missingCycles"] == []


def test_a_report_does_not_become_the_header():
    """The header describes the last cycle, not the last document.

    A report carries no cycle number and lands after the entry of the
    cycle that wrote it, so the naive read puts `Outcome: report` and
    `PR: none` on the front page at 06:00, 14:00 and 22:00 -- Nova's own
    summary of eight cycles rendered as a cycle that shipped nothing.
    """
    markdown = (
        "### 2026-08-13 14:00 (Oslo) — Report · Cycles 122–129\n\nEight cycles.\n\n"
        "---\nPR: none | Outcome: report\n"
        "### 2026-08-13 13:40 (Oslo) — Cycle 129\n\nBody.\n\n"
        "---\nPR: #99 | Outcome: merged\n"
    )
    status = build_status(parse_journal(markdown))
    assert (status["lastOutcome"], status["lastPr"]) == ("merged", "#99")
    assert status["lastWokeTime"] == "13:40"


def test_a_late_addendum_does_not_walk_the_cycle_number_backwards():
    """`Cycle N` in the header is the highest written, not the newest filed.

    An addendum to an earlier cycle is a normal document and it carries
    that earlier number. Reading the first number in document order made
    the front page announce a cycle the loop had already passed.
    """
    markdown = (
        "### 2026-08-13 08:10 (Oslo) — Cycle 128, addendum\n\nBody.\n\n"
        "---\nPR: none | Outcome: shipped\n"
        "### 2026-08-13 07:50 (Oslo) — Cycle 129\n\nBody.\n\n"
        "---\nPR: #98 | Outcome: merged\n"
    )
    status = build_status(parse_journal(markdown))
    assert status["cycle"] == 129
    # And the other three fields have to name the same cycle the number
    # does. Skipping reports alone left these describing 128 under a
    # heading that said 129 -- one header, two cycles.
    assert (status["lastOutcome"], status["lastPr"]) == ("merged", "#98")
    assert status["lastWokeTime"] == "07:50"


def test_the_page_and_the_self_check_cannot_disagree_about_a_hole():
    """One definition of "missing", read by both callers.

    The point of `gaps_between` existing: `cycle_health` answers this for
    Nova and `build_status` answers it for Edvard, and a second
    implementation is the hand-synced pair this repo keeps finding
    drifted. If these two ever disagree, one of the two readers is lying
    to somebody.

    Sharing the function is only half of it -- they also have to be fed
    the same set, which is why the live path passes the filename-derived
    numbers rather than the parsed ones. See
    `test_an_entry_whose_heading_cannot_be_parsed_is_not_called_a_gap`.
    """
    paths = [f"{n:03d}-cycle-{n}.md" for n in (125, 126, 129)]
    assert missing_cycles(paths) == build_status(
        _entries(129, 126, 125))["missingCycles"] == [127, 128]


def test_an_entry_whose_heading_cannot_be_parsed_is_not_called_a_gap():
    """The false alarm this feature would otherwise have shipped.

    Measured against the live journal on 2026-08-12: 140 cycle numbers
    appear in the `NNN-cycle-M.md` filenames and only 137 survive parsing
    the `### ` heading inside, because cycle 131 opens with frontmatter
    and cycles 146 and 147 wrote `## Cycle N` with two hashes. The entry
    regex needs three, so those headings are not headings and their text
    is absorbed into a neighbour.

    Those cycles wrote entries. Inferring the written set from parsed
    headings reports them missing, and the feed then prints "Cycle 146 ran
    and wrote no entry" directly above Cycle 146's own words -- a false
    accusation in the one feature built to stop false accusations.
    """
    markdown = (
        "### 2026-08-12 23:00 (Oslo) — Cycle 148\n\nBody.\n\n"
        "---\nPR: none | Outcome: merged\n"
        # Two hashes: not a heading, so cycle 147 is invisible to the parser.
        "## Cycle 147 — 2026-08-12 21:01\n\nBody.\n\n"
        "### 2026-08-12 20:00 (Oslo) — Cycle 146\n\nBody.\n\n"
        "---\nPR: none | Outcome: merged\n"
    )
    entries = parse_journal(markdown)
    assert {e["cycle"] for e in entries if e["cycle"] is not None} == {146, 148}

    # What the filenames know, which is the truth: 147 wrote an entry.
    status = build_status(entries, known_cycles=[146, 147, 148])
    assert status["missingCycles"] == []

    # And without that input it would have accused it.
    assert build_status(entries)["missingCycles"] == [147]


def test_the_payload_takes_the_written_set_from_the_filenames():
    """The plumbing, end to end -- and it is the half a unit test misses.

    `build_status` can be handed the right answer all day; what matters is
    whether `journal_payload` hands it one. Removing that argument leaves
    every other test in this file green, because they all call
    `build_status` directly. So this one goes through the real payload,
    over a corpus shaped like the live one: three entry documents, one of
    which (147) wrote `## Cycle N` and is therefore invisible to the
    heading parser while its filename says plainly that it exists.
    """
    from unittest.mock import patch

    from agora_runner import nova_sources, nova_site
    from agora_runner.vault import VaultFiles

    docs = {
        JOURNAL_DIR + "164-cycle-148.md":
            "### 2026-08-12 23:00 (Oslo) — Cycle 148\n\nBody.\n",
        JOURNAL_DIR + "163-cycle-147.md":
            "## Cycle 147 — 2026-08-12 21:01\n\nBody.\n",
        JOURNAL_DIR + "162-cycle-146.md":
            "### 2026-08-12 20:35 (Oslo) — Cycle 146\n\nBody.\n",
    }
    mtimes = {path: 1786000000000 for path in docs}

    with patch.object(nova_sources, "vault_bulk_fetch",
                      return_value=(VaultFiles(docs), mtimes)):
        status = nova_site.journal_payload()["status"]

    assert status["missingCycles"] == [], (
        "a cycle whose heading the parser cannot read was reported as a "
        "cycle that never wrote one")


def test_gaps_between_needs_two_entries_to_bracket_anything():
    # Below the lowest and above the highest there is no evidence a cycle
    # ever ran -- that end is the stall's question, answered with a clock.
    assert gaps_between([]) == []
    assert gaps_between([7]) == []


# --- the stall, which is a judgement about right now -----------------------


def test_a_cycle_still_running_is_not_reported_as_dead():
    """The ambiguity #72 is actually about.

    An entry is written at the *end* of a cycle, so for the 20-30 minutes
    one is running, agora has started cycle N and this page can only see
    N-1. That is indistinguishable from cycle N having died, and calling
    it dead would raise a false alarm every single hour.

    Ninety minutes rather than forty-five on purpose: the heartbeat is
    hourly and a cycle writes at the end of its hour, so the healthy gap
    between two entries routinely exceeds one interval. Under an hour the
    silence is zero intervals and *any* threshold passes this -- the test
    would be pinning nothing.
    """
    status = _with_silence(
        build_status(_entries(129, 128)), now=NOW + timedelta(minutes=90),
        minutes=60)
    assert status["stalled"] is False
    assert status["silentIntervals"] == 1


def test_a_loop_that_has_gone_quiet_says_so():
    status = _with_silence(
        build_status(_entries(129, 128)), now=NOW + timedelta(hours=3),
        minutes=60)
    assert status["stalled"] is True
    assert status["silentIntervals"] == 3


def test_the_grace_boundary_is_where_the_constant_says_it_is():
    entries = build_status(_entries(129, 128))
    hours = STALL_GRACE_INTERVALS
    assert _with_silence(
        entries, now=NOW + timedelta(hours=hours, minutes=-1),
        minutes=60)["stalled"] is False
    assert _with_silence(
        entries, now=NOW + timedelta(hours=hours), minutes=60)["stalled"] is True


def test_no_usable_stamp_is_not_reported_as_a_healthy_loop():
    """`None` and `0` are different answers and only one is reassurance."""
    status = _with_silence(build_status([]), minutes=60)
    assert status["silentIntervals"] is None
    assert status["stalled"] is False


def test_an_entry_stamped_in_the_future_is_not_a_negative_silence():
    status = _with_silence(
        build_status(_entries(129, 128)), now=NOW - timedelta(hours=5),
        minutes=60)
    assert status["silentIntervals"] == 0
    assert status["stalled"] is False


# --- the two halves meeting: the cache -------------------------------------


def test_the_stall_is_judged_per_request_and_not_frozen_into_the_cache():
    """The bug this design exists to avoid, pinned.

    `journal_payload` is cached and warmed at startup. Judge the stall in
    `build_status` and every request for the life of that process answers
    with the clock reading from the moment the payload was built -- so a
    process that warmed while the loop was healthy would keep saying
    "healthy" for exactly the hours it needed to say otherwise, and the
    feature would look like it worked in every test that built its own
    payload.

    So: one payload, built once, asked twice.
    """
    payload = {"entries": [dict(e) for e in _entries(129, 128)]}
    payload["status"] = build_status(parse_journal(_journal(129, 128)))

    fresh = journal_page(payload, now=NOW + timedelta(minutes=30))
    later = journal_page(payload, now=NOW + timedelta(hours=4))

    assert fresh["status"]["stalled"] is False
    assert later["status"]["stalled"] is True


def test_a_stall_changes_the_etag_so_an_open_tab_can_be_told():
    """Raised by the reviewer, and it would have shipped the feature dead.

    `stalled` is judged per request -- but the journal content it is judged
    from does not change while the loop is silent, because that silence is
    the failure. So the base etag is identical across a stall, and a page
    polling with `If-None-Match` is answered 304 for as long as it lasts.
    The warning would appear only in a tab opened after the loop died, and
    never in the one already open on Edvard's phone, which is the whole
    case it exists for.

    Same payload, same window, only the clock moved across the threshold.
    """
    from agora_runner.nova_site import journal_descriptor, page_etag

    payload = {"entries": [dict(e) for e in _entries(129, 128)]}
    payload["status"] = build_status(parse_journal(_journal(129, 128)))

    healthy = journal_page(payload, limit=20, now=NOW + timedelta(minutes=30))
    stalled = journal_page(payload, limit=20, now=NOW + timedelta(hours=4))
    assert healthy["status"]["stalled"] is False
    assert stalled["status"]["stalled"] is True

    base = "W/base"
    assert page_etag(base, journal_descriptor(healthy, 20, 0, None)) \
        != page_etag(base, journal_descriptor(stalled, 20, 0, None))


def test_the_etag_still_holds_still_when_nothing_has_changed():
    """The other half: it must not turn over on every request.

    An etag that varied with the raw clock would 200 on every poll and
    undo the whole point of `If-None-Match` -- so this pins that two
    requests in the same hour agree.
    """
    from agora_runner.nova_site import journal_descriptor, page_etag

    payload = {"entries": [dict(e) for e in _entries(129, 128)]}
    payload["status"] = build_status(parse_journal(_journal(129, 128)))

    first = journal_page(payload, limit=20, now=NOW + timedelta(minutes=5))
    second = journal_page(payload, limit=20, now=NOW + timedelta(minutes=50))

    base = "W/base"
    assert page_etag(base, journal_descriptor(first, 20, 0, None)) \
        == page_etag(base, journal_descriptor(second, 20, 0, None))


def test_build_status_never_reads_the_clock():
    """The same guard from the other side, and the one that survives a rewrite.

    The test above pins the behaviour; this pins the *reason*, so a future
    cycle tidying the stall back into `build_status` (the obvious place for
    it) fails here rather than shipping a header frozen at whatever was
    true when the pod started.
    """
    status = build_status(_entries(129, 128))
    assert "stalled" not in status
    assert "silentIntervals" not in status


# --- the cadence the silence is measured in --------------------------------
#
# Cycle 180 fixed this for the copy that talks to Nova (#166) by handing
# `cycle_health` the schedule off the heartbeat being dispatched, and left
# the copy that talks to Edvard reading `HEARTBEAT_MINUTES`. This process
# has no heartbeat in hand -- it is a different pod from the poll loop --
# so it has to ask Agora, and these pin what it does with the answer.


class _FakeAgora:
    """Stands in for `agora_internal`, recording what it was asked."""

    def __init__(self, status=200, heartbeats=(), raises=None):
        self.status = status
        self.heartbeats = list(heartbeats)
        self.raises = raises
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path))
        if self.raises is not None:
            raise self.raises
        return self.status, {"heartbeats": self.heartbeats}


def _hb(schedule, persona=NOVA_PERSONA_ID, enabled=True):
    return {"id": "hb", "personaId": persona, "schedule": schedule,
            "enabled": enabled}


def _with_agora(monkeypatch, fake):
    monkeypatch.setattr("agora_runner.http_util.agora_internal", fake)


def test_the_silence_is_measured_in_the_live_cadence_not_the_constant():
    """The bug. Ninety minutes of quiet is one interval at the hourly
    cadence and two at the forty-minute one Edvard ran on 2026-08-13, and
    the badge Edvard reads was answering with the first regardless.

    Ninety and forty are chosen so the two answers differ: at any offset
    under one interval both cadences say zero and the test pins nothing.
    """
    entries = build_status(_entries(129, 128))
    assert _with_silence(
        entries, now=NOW + timedelta(minutes=90), minutes=60
    )["silentIntervals"] == 1
    assert _with_silence(
        entries, now=NOW + timedelta(minutes=90), minutes=40
    )["silentIntervals"] == 2


def test_a_faster_cadence_calls_a_dead_cycle_sooner():
    """Not the same assertion as the one above: `silentIntervals` moving
    is only interesting if it drags the badge across the threshold, which
    is the thing on Edvard's screen. Two hours of quiet is healthy at the
    hourly cadence and a stall at forty minutes.

    Ninety minutes is the only window where the two disagree given a grace
    of two intervals: under eighty neither has stalled, at a hundred and
    twenty both have.
    """
    entries = build_status(_entries(129, 128))
    assert STALL_GRACE_INTERVALS == 2, "the window below was picked around this"
    assert _with_silence(
        entries, now=NOW + timedelta(minutes=90), minutes=60)["stalled"] is False
    assert _with_silence(
        entries, now=NOW + timedelta(minutes=90), minutes=40)["stalled"] is True


def test_a_slower_cadence_does_not_cry_stall_every_run():
    """The other direction, and the one #72 says makes the check worth
    less than nothing. At the six-hourly cadence this loop ran until
    2026-08-08, three hours of quiet is half an interval, not three.
    """
    entries = build_status(_entries(129, 128))
    assert _with_silence(
        entries, now=NOW + timedelta(hours=3), minutes=360
    )["silentIntervals"] == 0


def test_the_cadence_comes_from_novas_own_enabled_heartbeat(monkeypatch):
    fake = _FakeAgora(heartbeats=[
        _hb("every@5m", persona="someone-else"),
        _hb("every@10m", enabled=False),
        _hb("every@40m@19:00"),
    ])
    _with_agora(monkeypatch, fake)
    assert _fetch_cadence_minutes() == 40
    assert fake.calls == [("GET", "/heartbeats")]


def test_two_live_heartbeats_are_measured_at_the_faster_one(monkeypatch):
    """Any of them dispatching writes an entry, so the rate entries should
    appear at is the fastest -- picking the first match would wait through
    a dead cycle whenever the list happened to be ordered the other way."""
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[
        _hb("every@6h"), _hb("every@40m"),
    ]))
    assert _fetch_cadence_minutes() == 40
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[
        _hb("every@40m"), _hb("every@6h"),
    ]))
    assert _fetch_cadence_minutes() == 40


def test_a_schedule_with_no_single_interval_has_no_honest_answer(monkeypatch):
    """`cron@`/`daily@` and anything unparseable. `None` rather than a
    guess -- the caller falls back to the constant and says so."""
    for schedule in ("cron@0 * * * *", "daily@07:00", "every@abc", "every@0m", ""):
        _with_agora(monkeypatch, _FakeAgora(heartbeats=[_hb(schedule)]))
        assert _fetch_cadence_minutes() is None, schedule


def test_no_heartbeat_for_nova_at_all_is_not_a_cadence(monkeypatch):
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[]))
    assert _fetch_cadence_minutes() is None


def test_an_agora_that_answers_with_an_error_is_not_a_cadence(monkeypatch):
    _with_agora(monkeypatch, _FakeAgora(status=503, heartbeats=[_hb("every@40m")]))
    assert _fetch_cadence_minutes() is None


def test_the_first_caller_gets_the_fallback_without_waiting(monkeypatch):
    """The request path never blocks on Agora. A reader must not wait on a
    network call to find out whether a badge is red -- serving the
    constant cold is exactly what this did unconditionally before now."""
    reset_cadence()
    started = []
    monkeypatch.setattr(
        "agora_runner.nova_site.threading.Thread",
        lambda **kw: started.append(kw) or _NoopThread())
    assert cadence_minutes() == 60
    assert len(started) == 1
    assert started[0]["target"].__name__ == "_refresh_cadence"


class _NoopThread:
    def start(self):
        pass


def test_the_fetched_cadence_is_what_later_callers_measure_in(monkeypatch):
    reset_cadence()
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[_hb("every@40m")]))
    _refresh_cadence()
    assert cadence_minutes() == 40


def test_a_fetch_that_raises_does_not_reach_the_reader(monkeypatch):
    """A page that will not render because one badge's freshness could not
    be established is a worse answer than the badge judged against the
    fallback."""
    reset_cadence()
    _with_agora(monkeypatch, _FakeAgora(raises=OSError("connection refused")))
    _refresh_cadence()
    assert cadence_minutes() == 60


def test_a_failed_fetch_is_not_retried_on_every_single_request(monkeypatch):
    """The failure is cached the same as a success. Otherwise an Agora
    outage turns every page load into a fresh connection attempt, which is
    the poll loop this cache exists to not be."""
    reset_cadence()
    fake = _FakeAgora(raises=OSError("connection refused"))
    _with_agora(monkeypatch, fake)
    _refresh_cadence()
    for _ in range(5):
        assert cadence_minutes() == 60
    assert len(fake.calls) == 1
    ok = _FakeAgora(heartbeats=[_hb("every@40m")])
    _with_agora(monkeypatch, ok)
    for _ in range(5):
        cadence_minutes()
    assert ok.calls == []  # still inside CADENCE_FRESH_SECONDS


def test_the_badge_uses_the_live_cadence_through_the_whole_page(monkeypatch):
    """End to end rather than at `_with_silence`, because the parameter
    defaulting to `None` is the wire: pass the constant in by accident and
    every unit test above still passes while the page is unchanged."""
    reset_cadence()
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[_hb("every@40m")]))
    _refresh_cadence()
    payload = {"entries": [], "status": build_status(_entries(129, 128))}
    page = journal_page(payload, now=NOW + timedelta(hours=2))
    assert page["status"]["silentIntervals"] == 3
    assert page["status"]["stalled"] is True


def test_a_refresh_that_cannot_start_does_not_wedge_every_later_one(monkeypatch):
    """The in-flight flag is set before the thread exists, so if the thread
    never runs nothing clears it -- and the wedge is permanent and silent.
    `Thread.start` raises when the OS refuses a thread, which is memory
    pressure, and this platform has been OOM-killed twice.
    """
    reset_cadence()

    class _RefusesToStart:
        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(
        "agora_runner.nova_site.threading.Thread", lambda **kw: _RefusesToStart())
    assert cadence_minutes() == 60

    # The next caller must still try, rather than sitting on a flag that
    # says a refresh is already running when none is.
    started = []
    monkeypatch.setattr(
        "agora_runner.nova_site.threading.Thread",
        lambda **kw: started.append(kw) or _NoopThread())
    assert cadence_minutes() == 60
    assert len(started) == 1


def test_a_workflow_heartbeat_pointed_at_nova_is_not_the_cycle_cadence(monkeypatch):
    """A workflow-bound heartbeat dispatches `run_workflow_heartbeat`, a
    conversation round that writes no journal entry, and it carries a
    `personaId` like any other. Counting it would measure silence in
    intervals nothing writes in -- the false stall #72 exists to prevent.

    Five minutes against forty on purpose: the faster one wins the `min`,
    so ignoring `workflowId` is the difference between 40 and 5 rather
    than something the other heartbeat covers up.
    """
    workflow = dict(_hb("every@5m"), workflowId="wf-1")
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[workflow, _hb("every@40m")]))
    assert _fetch_cadence_minutes() == 40

    # And on its own it is not a cadence at all, rather than the shortest
    # of a list of one.
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[workflow]))
    assert _fetch_cadence_minutes() is None


def test_the_badge_the_page_draws_moves_with_the_live_cadence(monkeypatch):
    """The second end-to-end assertion, and the reason it exists is a
    finding against this very diff: nine of the tests above hand
    `_with_silence` a `minutes` literal, so they pin the arithmetic and
    would all still pass with the default wired back to the constant. The
    reviewer reverted exactly that and got 31 of 32 green. Coverage that
    only one test carries is coverage in appearance.

    So this drives `journal_page` -- the default path, no `minutes`
    argument anywhere -- across the stall threshold in both directions,
    off the cadence alone with the elapsed time held fixed.
    """
    entries = {"entries": [], "status": build_status(_entries(129, 128))}
    ninety = NOW + timedelta(minutes=90)

    reset_cadence()
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[_hb("every@60m")]))
    _refresh_cadence()
    healthy = journal_page(entries, now=ninety)["status"]
    assert (healthy["silentIntervals"], healthy["stalled"]) == (1, False)

    reset_cadence()
    _with_agora(monkeypatch, _FakeAgora(heartbeats=[_hb("every@40m")]))
    _refresh_cadence()
    stalled = journal_page(entries, now=ninety)["status"]
    assert (stalled["silentIntervals"], stalled["stalled"]) == (2, True)
