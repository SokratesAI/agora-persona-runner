"""Which cycles woke up and left no journal entry behind.

Edvard, `issues.md` 2026-08-12: *"Cycle 134 failed. If you do not already
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
would be putting a machine's guess into an append-only record that Edvard
reads as mine.
"""

from datetime import datetime, timedelta

from agora_runner.config import OSLO
from agora_runner.nova_journal import file_cycle

# The heartbeat is `every@60m` (Edvard cut it from 72m on 2026-08-09,
# wanting a more aggressive loop). Passed in rather than imported so a
# caller that knows the live schedule can say so, and so the tests do not
# have to be rewritten the next time he changes it -- it has changed twice.
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


def missing_cycles(paths):
    """Cycle numbers with no entry, bracketed by entries on both sides.

    Only the *interior* of the range is reported. Below the lowest number
    written there is no evidence a cycle ever ran, and above the highest
    there is no evidence one has finished yet -- that end is `stalled_for`'s
    question, answered with a clock instead. Returned ascending, so the
    caller's `[-1]` is the most recent failure and the one still worth
    acting on.
    """
    written = cycles_written(paths)
    if len(written) < 2:
        return []
    return [n for n in range(min(written) + 1, max(written)) if n not in written]


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


def findings(paths, mtimes, now, minutes=HEARTBEAT_MINUTES):
    """`{"entries": n, "missing": [...], "silent_intervals": n | None, "stalled": bool}`.

    `missing` is history and never shrinks; `stalled` is about right now.
    They are reported side by side rather than merged into one list because
    the actions differ: a stalled loop means go and look at the runner, an
    old hole means the record has a gap that a human should be told about
    once and not again.

    `entries` is how many entry documents were actually seen, and it is here
    because every other field in this dict reads as healthy when the answer
    is really that nothing was read. `vault_bulk_fetch` logs a failed listing
    and returns what it got, so a 401 arrives as an empty dict -- and an empty
    dict yields no gaps, no stall, and an exit code of zero. The caller cannot
    tell that apart from a clean loop without this count.
    """
    silent = stalled_for(mtimes, now, minutes)
    return {
        "entries": len(cycles_written(paths)),
        "missing": missing_cycles(paths),
        "silent_intervals": silent,
        "stalled": silent is not None and silent >= STALL_GRACE_INTERVALS,
    }


def describe(report):
    """One line for a cycle to read, or `""` when the loop looks healthy.

    Empty on a clean result on purpose: a check that always prints
    something trains the reader to skip it, and this one is read at the top
    of every cycle.

    **Reading nothing is reported, and it is reported instead of the rest.**
    Measured 2026-08-12 from the bridge pod, which is the shell `prompt.md`
    sends cycles to and where the previous handoff told the next cycle to run
    this: `COUCHDB_USER`, `COUCHDB_PASSWORD` and `COUCHDB_NOVA_DB` are all
    empty there, so the journal routes to the wrong database and 401s, the
    listing comes back with zero files, and every finding below is vacuously
    clean. The check printed nothing and exited 0 while the live folder
    visibly skipped 134 -- an all-clear from a blind instrument, which is
    worse than no check at all because it reads as reassurance. The other
    findings are suppressed rather than appended because they are not
    evidence of anything when the input was empty; saying "0 gaps, and also I
    could not look" invites reading the first half.
    """
    if not report.get("entries"):
        return (
            "read 0 journal entries -- cannot tell a healthy loop from an "
            "unreadable one; check the vault credentials and that this is "
            "running where they are set (the runner pod, not the bridge)"
        )
    parts = []
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
    report = findings(list(files), mtimes, datetime.now(OSLO))
    line = describe(report)
    if line:
        print(line)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
