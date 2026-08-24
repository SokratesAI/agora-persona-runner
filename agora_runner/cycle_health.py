"""Which cycles woke up and left no journal entry behind.

The owner, `issues.md` 2026-08-12: *"Cycle 134 failed. If you do not already
have a self check that your previous cycles worked correctly, you should
make yourself do this and self repair automatically."* He found that hole
by eye, reading the journal on his phone, which is the whole problem --
nothing in this loop notices its own missing hours. Cycles 127 and 128
were OOM-killed on the same morning and it took a human noticing that the
record simply skipped from 126 to 129.

**The ground truth is the cycle-number sequence, not the clock and not the
headings.** Every entry is a document named `NNN-cycle-M.md`; `M` is
assigned by the cycle that ran, and a cycle that dies mid-flight still
consumed its number, so the number never gets reused. That makes a hole in
the run of numbers a direct observation -- cycle 134 woke, did something or
nothing, and wrote no entry -- rather than an inference from timing. The
heading inside the document states the same number independently, and this
module deliberately reads the filename instead: a cycle killed while
writing can leave a document whose heading disagrees, and the filename is
the half `nova_sources` already trusts to decide which document to fetch.

**The last cycle is the one this cannot see from numbers alone, and it is
also the one that matters most.** A hole is only visible once something
*later* exists to bracket it, so the most recent failure -- the one a cycle
could still act on -- is exactly the case with no upper bracket. That case
needs the clock: the heartbeat fires on a fixed interval, so if the newest
entry is older than one interval plus a grace period, at least one cycle
has woken since and left nothing. Two different questions, two different
instruments, and neither substitutes for the other.

**Everything here is pure.** It takes the filenames and modification times
`nova_sources` already fetches for the journal page and returns findings;
it does not read the vault, does not write to it, and does not decide what
to do about a gap. That is deliberate -- the repair for a dead cycle is
picking up whatever it left in `/data/workspace` and finishing it, which
only a cycle can do, and a module that invented a replacement journal entry
would be putting a machine's guess into an append-only record that the owner
reads as mine.
"""

from datetime import datetime, timedelta

from agora_runner.config import OSLO
from agora_runner.nova_journal import file_cycle

# The last-resort fallback, not the truth. `nova_cadence_minutes` below is
# the truth, and this is what a caller measures in when that returns `None`
# -- Agora unreachable, or a schedule with no single interval. It says
# `every@60m` because that is what the owner set on 2026-08-09; he has changed
# the cadence four times since 2026-08-08, so treat any agreement between
# this number and reality as luck.
HEARTBEAT_MINUTES = 60

# One extra interval before calling the newest cycle dead. A cycle runs
# 20-30 minutes (measured 2026-08-09) and writes its entry at the end, so
# an entry can legitimately be ~50 minutes old while the next cycle is
# mid-flight. Waiting a full second interval means a cycle that is merely
# slow is never reported as missing, at the cost of noticing one interval
# later -- and a false "the last cycle died" is worse than a late true one,
# because the only thing to do about it is go looking for work that isn't
# there.
STALL_GRACE_INTERVALS = 2

# The longest a single cycle can possibly still be running. The bridge kills
# a turn at 45 minutes -- measured Cycle 82, and the turn hook prints the
# remaining minutes on every tool call -- so a run claimed longer ago than
# this is over, however it ended, whatever `lastResult` still says.
#
# It exists because `STALL_GRACE_INTERVALS` stopped covering a running cycle
# when the cadence dropped. At 60 minutes, two intervals was two hours and no
# healthy cycle came close; at the 20 minutes the owner set on 2026-08-24 it is
# 40 minutes, which is *less than one cycle's own runtime* -- so an ordinary
# slow cycle crossed the stall threshold while it was still working, and at
# 17:38 the site pushed "Nova has stopped writing" into Cycle 373's own live
# conversation while that cycle was mid-turn. The grace interval is a count
# of cadence intervals and therefore shrinks with the cadence; how long a
# cycle runs does not, so the two need separate numbers.
MAX_CYCLE_MINUTES = 45


def nova_cadence_minutes():
    """Minutes between Nova's own heartbeat runs, live from Agora, or `None`.

    `None` means "no honest answer": Agora unreachable, no enabled
    heartbeat pointed at Nova, or a `cron@`/`daily@` schedule that has no
    single interval. The caller falls back to `HEARTBEAT_MINUTES` rather
    than inventing one.

    The **shortest** interval when more than one enabled heartbeat targets
    Nova, because any of them dispatching writes a journal entry -- so the
    rate entries should appear at is the fastest of them, and measuring
    silence against a slower one would wait through a dead cycle before
    saying anything. There is one such heartbeat today; picking the first
    match would be arbitrary the day there are two.

    Lives here, beside the constant it replaces, because both callers are
    asking the same question for the same reason and #166/#167 answered it
    twice. `nova_site` asks it for the badge the owner reads and caches the
    answer off the request path; `heartbeats.nova_health_note` asks it for
    the line handed to a waking cycle. That second caller has one
    heartbeat's schedule in hand and used only that, which is a different
    question -- "how often does *this* heartbeat run" rather than "how
    often does an entry get written" -- and the two answers diverge the
    day Nova has a second enabled heartbeat.
    """
    return nova_heartbeat_snapshot()[0]


def nova_heartbeat_snapshot():
    """`(minutes, last_run_at, last_result)` for Nova's own heartbeat.

    One `/heartbeats` fetch answering both questions the site asks of it,
    because it was already making the call and throwing away every field
    but `schedule`.

    `lastRunAt` and `lastResult` are the other half of the owner's #72. The
    cadence tells the page how long silence is allowed to last; these two
    tell it whether the silence is a cycle *working*. Agora writes
    `lastResult: "running"` when it claims a run and overwrites it with
    the outcome when the run returns (`heartbeats.run_heartbeat`), so
    "running" plus a `lastRunAt` newer than the newest journal entry is a
    measured statement that cycle N is in flight -- not an inference from
    the clock. That is the fact the app never had, and why it could only
    ever show N-1 and leave him to guess whether N had died.

    A cycle that is *killed* leaves `lastResult` stuck on "running"
    forever, so this is not on its own a liveness check and must never be
    read as one -- see `nova_site._running_now`, which drops the claim
    once the loop crosses the stall grace window.

    **A running heartbeat wins over a newer finished one**, and the
    difference is not academic -- it is the same "any of them running is a
    cycle running" rule the cadence follows by taking the shortest
    interval. Taking the newest `lastRunAt` outright looks equivalent and
    is not: a slow heartbeat claimed at 15:00 and still in flight, beside
    a fast one claimed at 15:20 and already finished, would report the
    finished one's outcome and the page would say no cycle is running
    while one is. That is precisely the false negative #72 is about,
    reintroduced by the aggregation rule. Among running heartbeats, and
    among finished ones, the newest `lastRunAt` still wins.

    There is one enabled heartbeat targeting Nova today, so this is
    latent rather than live -- but `nova_cadence_minutes` beside it
    already says the two answers diverge the day there is a second one,
    and a rule that is wrong only in the case it was written for is not
    much of a rule.

    All three are `None` when Agora is unreachable or no enabled
    heartbeat targets Nova, which is the same "no honest answer" the
    cadence returns rather than a fabricated one.
    """
    from agora_runner.http_util import agora_internal
    from agora_runner.turns import schedule_minutes

    status, body = agora_internal("GET", "/heartbeats")
    if status != 200:
        return (None, None, None)
    nova = nova_cycle_heartbeats(body.get("heartbeats"))
    usable = [m for m in (schedule_minutes(hb.get("schedule", "")) for hb in nova) if m]
    minutes = min(usable) if usable else None
    ran = [hb for hb in nova if hb.get("lastRunAt")]
    if not ran:
        return (minutes, None, None)
    newest = max(ran, key=lambda hb: (hb.get("lastResult") == "running",
                                      hb["lastRunAt"]))
    return (minutes, newest.get("lastRunAt"), newest.get("lastResult"))


def nova_cycle_heartbeats(heartbeats):
    """The enabled heartbeats that actually make Nova write journal entries.

    `workflowId` excluded, not just filtered for tidiness: a workflow-bound
    heartbeat dispatches `run_workflow_heartbeat`, a multi-step conversation
    round that writes no journal entry, and `create_heartbeat` requires a
    `personaId` on those too. One pointed at Nova at a faster cadence -- a
    workflow left enabled, say -- would have `nova_cadence_minutes`
    measuring silence in intervals nothing writes in, which is the false
    stall #72 exists to prevent.

    Its own function because two callers now ask the same question of the
    same list: this module, for the interval the stall is judged in, and
    `stall_notice.nova_conversation`, for where to send the notice. The
    second one hand-copied the three conditions and its own docstring said
    so, which is how the pair the next drift check finds gets made.
    """
    from agora_runner.config import NOVA_PERSONA_ID

    return [
        hb for hb in (heartbeats or [])
        if (hb.get("enabled") and not hb.get("workflowId")
            and hb.get("personaId") == NOVA_PERSONA_ID)
    ]


def cycles_written(paths):
    """The set of cycle numbers that have an entry document, from filenames.

    A path with no `-cycle-N` in its name is not an entry -- the folder has
    only ever held entries, but `file_cycle` returning `None` is the honest
    answer for anything else that lands there and it must not be counted as
    a cycle.
    """
    found = set()
    for path in paths:
        number = file_cycle(path)
        if number is not None:
            found.add(number)
    return found


def gaps_between(numbers):
    """The interior cycle numbers absent from `numbers`, ascending.

    Split out of `missing_cycles` so the journal page can mark the same
    holes on screen without deciding for itself what a hole is. The site
    reaches this through `build_status`, which has parsed cycle numbers
    rather than paths -- and a second implementation over there is exactly
    the hand-synced pair this repo keeps finding drifted (the two vault
    clients, Cycles 136-142). One definition, two callers.
    """
    written = set(numbers)
    if len(written) < 2:
        return []
    return [n for n in range(min(written) + 1, max(written)) if n not in written]


RECENT_GAP_WINDOW = 24


def recent_gaps(numbers, window=RECENT_GAP_WINDOW):
    """`gaps_between`, cut to holes within `window` cycles of the newest.

    The owner, comments board 2026-08-14, on the header's stall badge:
    *"Should be displayed if the return fetch came in with missing
    journals."* The header's whole job is whether the loop is alive, and
    the only thing it has ever said about that is a clock -- how long
    since the newest entry. A hole is the stronger signal and a much
    harder one to cry wolf with, because it is a fact about the record
    rather than a guess about the present: cycle 128 either wrote an
    entry or it did not.

    The window is what stops that becoming a badge nobody reads.
    `gaps_between` is history and never shrinks -- the live journal holds
    six holes, the newest being 134 against a newest cycle of 205 -- so
    rendering all of it in the header would pin a permanent warning about
    a failure three days old, which is the same "badge every hour"
    failure the client comments warn about. The feed already marks every
    hole in the position it happened; this is only for the ones still
    worth interrupting him about.

    24 is that window and it is a duration in disguise: across every
    cadence the owner has actually run -- 40, 60 and 72 minutes -- it spans
    16 to 29 hours, so "a cycle failed within about the last day". It is
    also roughly the first screen of the feed (Cycle 205 measured 20
    cards), which means a hole the header names is one he can scroll to
    and see marked.
    """
    numbers = set(numbers)
    gaps = gaps_between(numbers)
    if not gaps:
        return []
    floor = max(numbers) - window
    return [n for n in gaps if n >= floor]


def missing_cycles(paths):
    """Cycle numbers with no entry, bracketed by entries on both sides.

    Only the *interior* of the range is reported. Below the lowest number
    written there is no evidence a cycle ever ran, and above the highest
    there is no evidence one has finished yet -- that end is `stalled_for`'s
    question, answered with a clock instead. Returned ascending, so the
    caller's `[-1]` is the most recent failure and the one still worth
    acting on.
    """
    return gaps_between(cycles_written(paths))


def newest_entry_at(mtimes):
    """When the highest-numbered entry was written, as an aware datetime.

    Keyed on the cycle number rather than on the timestamp, because these
    are the same file set the journal page orders by number: two cycles that
    overlap write out of chronological order, and the newest *cycle* is the
    one whose absence of a successor is being judged, not the newest write.
    """
    latest = None
    stamp = None
    for path, ms in mtimes.items():
        number = file_cycle(path)
        if number is None or not ms:
            continue
        if latest is None or number > latest:
            latest = number
            stamp = ms
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp / 1000, tz=OSLO)


def stalled_for(mtimes, now, minutes=HEARTBEAT_MINUTES):
    """Whole heartbeat intervals since the newest entry, or `None`.

    `None` means there is nothing to judge -- no entry has a usable time --
    which is a different answer from zero and must not be flattened into it.
    """
    written_at = newest_entry_at(mtimes)
    if written_at is None:
        return None
    elapsed = now - written_at
    if elapsed < timedelta(0):
        # An entry stamped in the future is a clock disagreement, not a
        # healthy loop and not a stalled one. Report no elapsed intervals
        # rather than a negative count the caller would have to guard.
        return 0
    return int(elapsed.total_seconds() // (minutes * 60))


def findings(paths, mtimes, now, minutes=HEARTBEAT_MINUTES, unreadable=()):
    """`{"entries": n, "missing": [...], "silent_intervals": n | None, "stalled": bool}`.

    `missing` is history and never shrinks; `stalled` is about right now.
    They are reported side by side rather than merged into one list because
    the actions differ: a stalled loop means go and look at the runner, an
    old hole means the record has a gap that a human should be told about
    once and not again.

    `entries` is how many distinct cycle *numbers* were parsed out of the
    filenames -- deliberately the size of the same set `missing_cycles` works
    from, and **not** a count of documents. A cycle that wrote twice leaves two
    files under one number (`nova_journal` documents 081 and its addendum), so
    the two figures genuinely differ and only this one answers the question
    being asked. It is here because every other field in this dict reads as
    healthy when the answer is really that nothing was read: `vault_bulk_fetch`
    logs a failed listing and returns what it got, so a 401 arrives as an empty
    dict -- and an empty dict yields no gaps, no stall, and an exit code of
    zero. The caller cannot tell that apart from a clean loop without this
    count. Zero here means nothing parseable came back at all, which is the
    only threshold the blindness check depends on.
    """
    silent = stalled_for(mtimes, now, minutes)
    return {
        "unreadable": list(unreadable),
        "entries": len(cycles_written(paths)),
        "missing": missing_cycles(paths),
        "silent_intervals": silent,
        "stalled": silent is not None and silent >= STALL_GRACE_INTERVALS,
    }


def first_written_at(mtimes):
    """`{cycle number: when its first document appeared}`, Oslo.

    The *first*, not the newest: a cycle that wrote twice (an addendum) is
    still a cycle whose existence became observable at the earlier write,
    and that moment is what `gaps_since` brackets against.
    """
    out = {}
    for path, ms in mtimes.items():
        number = file_cycle(path)
        if number is None or not ms:
            continue
        stamp = datetime.fromtimestamp(ms / 1000, tz=OSLO)
        if number not in out or stamp < out[number]:
            out[number] = stamp
    return out


def gaps_since(paths, mtimes, since):
    """Interior gaps that became *observable* after `since`, ascending.

    `missing_cycles` is history and never shrinks, which is right for a
    report a human reads once and wrong for a line put in front of every
    cycle: after a while it says "6 cycles wrote no entry" every hour
    forever, and a check that always prints something trains its reader to
    skip it -- the same reason `describe` returns "" on a clean loop.

    The filter is not "recent" in any arbitrary sense; there is an actual
    event to key on. **A dead cycle changes nothing at the moment it
    dies** -- it leaves no document, so there is nothing to observe. The
    hole only appears once a *later* cycle writes the entry that brackets
    it from above, and that write is a real, timed event. So a gap is new
    to this run exactly when its upper bracket was written since the
    previous one, which announces each gap to exactly one cycle: the first
    one that could possibly have seen it.

    `since` of `None` means there is no previous run to compare against,
    and everything is reported once. That is the honest answer rather than
    a convenient silence: with no boundary, no gap has been shown to
    anyone yet. Note when this actually fires, because the obvious guess
    is wrong: the boundary is Agora's `lastRunAt`, which lives in the
    heartbeat store and not in this process, so **deploying this code does
    not reset it**. Only a brand-new heartbeat, or an unparseable
    timestamp, takes this branch.
    """
    gaps = missing_cycles(paths)
    if since is None or not gaps:
        return gaps
    written_at = first_written_at(mtimes)
    fresh = []
    for gap in gaps:
        brackets = [n for n in written_at if n > gap]
        if not brackets:
            # No timed entry above it, so nothing dates this gap. Stay
            # quiet: `stalled_for` owns the top of the range, and guessing
            # here would re-report old history on every run.
            continue
        if written_at[min(brackets)] > since:
            fresh.append(gap)
    return fresh


def heartbeat_findings(paths, mtimes, now, since, minutes=HEARTBEAT_MINUTES,
                       unreadable=()):
    """`findings`, but reporting only the gaps this run is the first to see.

    Same dict, same renderer (`describe`), one substitution -- the two
    callers differ in *which gaps count*, not in how a gap reads. The
    stall and the blind-read are unchanged, because both are already
    statements about right now.
    """
    report = findings(paths, mtimes, now, minutes, unreadable)
    report["missing"] = gaps_since(paths, mtimes, since)
    return report


def describe(report):
    """One line for a cycle to read, or `""` when the loop looks healthy.

    Empty on a clean result on purpose: a check that always prints
    something trains the reader to skip it, and this one is read at the top
    of every cycle.

    **Reading nothing is reported, and it is reported instead of the rest.**
    Measured 2026-08-12 from the bridge pod, which is the shell `prompt.md`
    sends cycles to and where the previous handoff told the next cycle to run
    this. The reason it reads nothing there is worth stating exactly, because
    the obvious guess is wrong and would send the next debugger hunting for a
    missing secret: **the bridge pod's CouchDB credentials are present and
    working -- under different names.** Its own vault client reads `CDB_USER`,
    `CDB_PASS`, `CDB_NOVA_DB`; this package reads `COUCHDB_*`, of which not one
    is set in that pod. `agora_runner` is not in the bridge image at all
    (`import agora_runner` outside a checkout is a `ModuleNotFoundError`), so
    the only way to run this there is out of a git checkout in the workspace,
    where the names this package wants default to empty. `db_for` then routes
    the journal to the owner's database instead of Nova's, that request 401s, the
    listing comes back with zero files, and every finding below is vacuously
    clean. The check printed nothing and exited 0 while the live folder
    visibly skipped 134 -- an all-clear from a blind instrument, which is
    worse than no check at all because it reads as reassurance. The fix for
    running it in the wrong pod is this message, not a credential. The other
    findings are suppressed rather than appended because they are not
    evidence of anything when the input was empty; saying "0 gaps, and also I
    could not look" invites reading the first half.
    """
    if not report.get("entries"):
        line = (
            "read 0 journal entries -- cannot tell a healthy loop from an "
            "unreadable one; check the vault credentials and that this is "
            "running where they are set (the runner pod, not the bridge)"
        )
        # When the read knew why it failed, say that instead of guessing at
        # credentials. `vault_bulk_fetch` carries the reason back on the
        # mapping it returns (`VaultFiles.unreadable`); a cycle staring at
        # this line wants the 401 and the database name, not advice.
        if report.get("unreadable"):
            line += " -- the read reported: " + "; ".join(report["unreadable"])
        return line
    parts = []
    # A partial read is not only the `entries == 0` case, and the other case
    # is worse. If one entry document lost its content chunks -- which has
    # happened in production, to `ideas.md`, 6 chunks of 184 -- the rest of
    # the folder still arrives, `entries` is healthy, and that one cycle
    # number simply is not in the set. `missing_cycles` then reports it as
    # "ran and wrote no journal entry", which is a confident false claim
    # about a cycle that wrote its entry perfectly well. So the reason comes
    # first, before the findings it undermines.
    for note in report.get("unreadable") or []:
        parts.append(f"part of the journal could not be read: {note}")
    missing = report.get("missing") or []
    if missing:
        recent = ", ".join(str(n) for n in missing[-5:])
        parts.append(
            f"{len(missing)} cycle(s) ran and wrote no journal entry: {recent}"
            + (" (newest last)" if len(missing) > 1 else "")
        )
    if report.get("stalled"):
        parts.append(
            f"no entry for {report['silent_intervals']} heartbeat intervals -- "
            "the most recent cycle(s) produced nothing"
        )
    return "; ".join(parts)


def main():
    """`python -m agora_runner.cycle_health` -- the check a cycle runs itself.

    Exits 1 when something is wrong so a shell can branch on it, and prints
    nothing at all when the loop is healthy.
    """
    from agora_runner.nova_journal import JOURNAL_DIR
    from agora_runner.vault import vault_bulk_fetch

    files, mtimes = vault_bulk_fetch(JOURNAL_DIR, with_mtimes=True)
    report = findings(
        list(files), mtimes, datetime.now(OSLO),
        unreadable=getattr(files, "unreadable", ()),
    )
    line = describe(report)
    if line:
        print(line)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
