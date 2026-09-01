"""One line on the feed when a cycle dies, because silence is not a record.

Sokrates' write-up on the owner's `issues.md`, 2026-08-24, after cycles 358-360
produced nothing at all during the Hetzner outage: *"three consecutive dead
cycles left zero trace anywhere until the owner asked a human to go look -- no
journal stub, no alert, nothing."* His first proposal is this module: *"When a
heartbeat trigger fails before the claude_cli session even starts (e.g.
DNS/network dead), write a minimal 'failed to start' journal stub instead of
dying silently, so a run of dead cycles is visible on the feed itself, not just
retroactively via /api/health."*

**What this can and cannot see, said up front because the proposal's own
example is outside it.** This runs inside `run_heartbeat`, so it covers the run
that started and then failed -- the CLI unreachable, DNS gone, the bridge
refusing. It cannot cover 358-360 themselves: the node was down, and a runner
that is not running writes nothing. That half is `stall_notice`'s, from the
site process, and it already exists. So this is the half that was missing, not
the whole answer, and a marker that claimed otherwise would be worse than none.

**A stub must not silence the stall notice, and that is the trap in the
proposal.** `stall_notice.due` keys on `lastWrittenAt`, the write time of the
newest journal entry, precisely so that one message goes out per stall rather
than one per check. A stub is a journal entry. Write one on every failed run
and a loop that fails every cycle keeps moving that stamp, `stalled` never
becomes true, and the owner's phone never rings -- the marker would have replaced
the alarm with a quieter version of the same silence. So the stub declares
itself in its own heading, `nova_journal.parse_heading` files it as
`kind: "silence"`, and `_newest_written_at` skips it. It shows on the feed and
it counts for nothing else.
"""

from datetime import datetime

from agora_runner.log import log
from agora_runner.nova_journal import JOURNAL_DIR, OSLO, SILENCE_TITLE, entry_seq

#: Suffixes a marker may take at one position, in order. The letters are
#: the bound on how many markers one outage can add: they all ride on the
#: same sequence number (see `stub_filename`), so the 27th failed run in a
#: row writes nothing. That is a consequence of the naming rather than a
#: number I picked -- and 26 cards is already far past the point where the
#: feed has made its point.
SUFFIXES = "abcdefghijklmnopqrstuvwxyz"


def stub_filename(seq, attempt=0):
    """`(418, 0)` -> `418a-silence.md`.

    **A marker rides on the newest entry's number instead of taking the
    next one, and that is the whole collision story.** The obvious version
    -- `max(seq) + 1` -- looks safe because `if_rev=None` 409s a second PUT
    to the same path, and it is not: a live cycle reserves its number in
    `claims.json` *before* its file exists, so a sibling run that dies
    mid-way sees no `419-*` on disk, writes `419-silence.md`, and the cycle
    then writes `419-cycle-366.md`. Different paths, no conflict, two
    documents at one position in the only total order the folder has. That
    is verbatim the bug `tools/put_entry.py` exists to prevent, and my
    first version of this file asserted it could not happen.

    Sharing the previous entry's number sidesteps the ledger entirely
    rather than reimplementing it in the failure path: a marker never
    claims a number a cycle might want. Ties sort by name, and
    `418-cycle-364.md` < `418a-silence.md`, so the marker lands just after
    the entry it follows.

    Deliberately not `nova_journal.entry_filename`, which would slug the
    declaration into `418-silence-a-heartbeat-run-failed-before-i.md`. The
    name is the one part of a marker a human greps for.
    """
    return f"{seq:03d}{SUFFIXES[attempt]}-silence.md"


def current_seq(paths):
    """The number of the newest entry in the folder -- the one to ride on."""
    return max((entry_seq(p) for p in paths), default=0)


def stub_markdown(reason, when=None):
    """The whole entry, as a pure function of what went wrong and when.

    `reason` is `run_heartbeat`'s own `result` string -- already truncated
    to 200 characters there, and already the text that goes into the
    heartbeat's `lastResult`, so the feed and Agora say the same thing
    rather than two paraphrases of it.
    """
    when = when or datetime.now(OSLO)
    stamp = when.strftime("%Y-%m-%d %H:%M")
    return "\n".join([
        f"### {stamp} (Oslo) — {SILENCE_TITLE}",
        "",
        f"A cycle woke at {when.strftime('%H:%M')} and stopped before it could write anything. "
        f"What the runner recorded: `{reason}`.",
        "",
        "Nothing was built, merged or decided. This marker is here so a run of dead cycles "
        "shows up on the feed instead of looking like a quiet hour, and it is written by the "
        "runner rather than by a cycle. It is deliberately not counted as the loop writing, so "
        "the stall notice still reaches your phone if the loop stays down.",
        "",
        "---",
        "PR: none | Outcome: stuck",
        "",
    ])


def write_stub(reason, when=None, list_paths=None, write=None, suffixes=SUFFIXES):
    """Put one marker in the journal folder. Returns its path, or `None`.

    Never raises: the caller is `run_heartbeat`'s failure path, and a
    marker that fails to land must cost a marker, not the rest of the
    failure handling.

    A conflict here means a marker is already sitting at this position --
    an earlier failed run in the same outage -- so the suffix walks on.
    `if_rev=None` is what makes that safe: CouchDB 409s a PUT with no
    revision against a live document, so two runners racing for one name
    cannot both think they wrote it. Note what that does *not* cover, and
    why `stub_filename` rides on the previous number rather than taking the
    next: two writers on two different names never conflict at all.
    """
    if list_paths is None or write is None:
        from agora_runner.vault import vault_list_ids, vault_write_path

        list_paths = list_paths or (lambda: vault_list_ids(JOURNAL_DIR))
        write = write or vault_write_path
    body = stub_markdown(reason, when)
    try:
        seq = current_seq(list_paths())
    except Exception as error:  # noqa: BLE001 -- see docstring
        log(f"silence marker: could not list {JOURNAL_DIR}: {error!r}")
        return None
    for attempt in range(len(suffixes)):
        path = JOURNAL_DIR + stub_filename(seq, attempt)
        try:
            result = write(path, body, if_rev=None)
        except Exception as error:  # noqa: BLE001 -- see docstring
            log(f"silence marker: write of {path} raised {error!r}")
            return None
        if result == "written":
            log(f"silence marker written at {path}")
            return path
        if "409" not in str(result):
            # Not a conflict, so the next suffix would be refused the same
            # way -- retrying is a spin. The refusal is logged rather than
            # swallowed because this is the failure path's failure path.
            log(f"silence marker: {path} refused with {result}")
            return None
    log(f"silence marker: {len(suffixes)} already at position {seq}, not writing another")
    return None
