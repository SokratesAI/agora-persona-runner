"""One line on the feed when a cycle dies, because silence is not a record.

Sokrates' write-up on Edvard's `issues.md`, 2026-08-24, after cycles 358-360
produced nothing at all during the Hetzner outage: *"three consecutive dead
cycles left zero trace anywhere until Edvard asked a human to go look -- no
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
becomes true, and Edvard's phone never rings -- the marker would have replaced
the alarm with a quieter version of the same silence. So the stub declares
itself in its own heading, `nova_journal.parse_heading` files it as
`kind: "silence"`, and `_newest_written_at` skips it. It shows on the feed and
it counts for nothing else.
"""

from datetime import datetime

from agora_runner.log import log
from agora_runner.nova_journal import JOURNAL_DIR, OSLO, SILENCE_TITLE, entry_seq

#: How many sequence numbers to walk before giving up. A conflict means
#: another writer took the number between the listing and the PUT, which is
#: one cycle of retry per concurrent writer -- and the concurrency limit is
#: three, so five is slack rather than a guess at a distribution.
ATTEMPTS = 5


def stub_filename(seq):
    """`372` -> `372-silence.md`.

    Deliberately not `nova_journal.entry_filename`, which would slug the
    declaration into `372-silence-a-heartbeat-run-failed-before-i.md`. The
    name is the one part of a marker a human greps for.
    """
    return f"{seq:03d}-silence.md"


def next_seq(paths):
    """The first sequence number no entry in the folder has taken."""
    return max((entry_seq(p) for p in paths), default=0) + 1


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


def write_stub(reason, when=None, list_paths=None, write=None, attempts=ATTEMPTS):
    """Put one marker in the journal folder. Returns its path, or `None`.

    Never raises: the caller is `run_heartbeat`'s failure path, and a
    marker that fails to land must cost a marker, not the rest of the
    failure handling.

    The sequence number is claimed by the write itself rather than through
    `claims.json` the way `tools/put_entry.py` does it. Two writers racing
    for the same number is exactly what `if_rev=None` already answers --
    CouchDB 409s the second PUT because a document is there -- and reaching
    for the ledger from the runner would mean a second implementation of it
    on a path that only ever runs when things are already broken.
    """
    if list_paths is None or write is None:
        from agora_runner.vault import vault_list_ids, vault_write_path

        list_paths = list_paths or (lambda: vault_list_ids(JOURNAL_DIR))
        write = write or vault_write_path
    body = stub_markdown(reason, when)
    try:
        seq = next_seq(list_paths())
    except Exception as error:  # noqa: BLE001 -- see docstring
        log(f"silence marker: could not list {JOURNAL_DIR}: {error!r}")
        return None
    for _ in range(attempts):
        path = JOURNAL_DIR + stub_filename(seq)
        try:
            result = write(path, body, if_rev=None)
        except Exception as error:  # noqa: BLE001 -- see docstring
            log(f"silence marker: write of {path} raised {error!r}")
            return None
        if result == "written":
            log(f"silence marker written at {path}")
            return path
        if "409" not in str(result):
            # Anything that is not a conflict will say the same thing at
            # the next sequence number too, so retrying is a spin.
            log(f"silence marker: {path} refused with {result}")
            return None
        seq += 1
    log(f"silence marker: gave up after {attempts} sequence conflicts")
    return None
