"""The vault reads behind Nova's site -- the journal, and the comments on it.

These lived in `nova_site` until `nova_replies` needed the same two, and
a worker importing the HTTP server to get at them would be a cycle
(`nova_site` has to import the worker to enqueue). Copying them instead
would be the mistake this repo already documents at the top of
`nova_site`: the vault client exists twice, in this repo and in the
bridge, with nothing detecting drift, and every further copy of a read is
one more place for the two halves to disagree.

So: one definition, imported by both. Parsing stays in `nova_journal` and
`nova_comments`, which do no I/O at all; this module is only the fetch.
"""

from agora_runner.nova_boards import BOARD_PATHS
from agora_runner.nova_capture import CAPTURE_TARGETS
from agora_runner.nova_catalog import CATALOG_PATH
from agora_runner.nova_comments import COMMENTS_PATH
from agora_runner.nova_costs import COST_LEDGER_PATH
from agora_runner.nova_plan import PLAN_DOCUMENTS
from agora_runner.nova_goal_history import GOAL_HISTORY_PATH
from agora_runner.nova_retro import RETRO_LEDGER_PATH
from agora_runner.nova_journal import (
    DIGEST_ARCHIVE_PATH,
    DIGEST_PATH,
    JOURNAL_DIR,
    assemble_entries,
    entry_seq,
    entry_times,
    file_cycle,
    normalise_entry,
)
from agora_runner.vault import vault_bulk_fetch, vault_list_ids, vault_read_path


def journal_markdown(with_times=False):
    """The entries, from the per-entry documents. One source, no fallback.

    `vault_bulk_fetch` pulls a whole folder with two batched `_all_docs`
    POSTs regardless of how many files are in it, so splitting 70 entries
    into 70 documents costs the site nothing. What it buys is on the
    other side -- a Nova cycle needs the newest three entries and used to
    have to read all 291KB to get them.

    **This used to fall back to `journal.md` and that fallback is why
    this raises now.** It made the 2026-08-09 migration safe in either
    order -- until the folder existed the archive answered, and once it
    did the archive was ignored. But the archive was emptied on
    2026-08-10, so from that day the fallback could only ever return zero
    entries, and the branch that ran it was the branch where the folder
    read had *failed*. A lost database therefore rendered as a journal
    Nova had never written anything into: the one shape a reader cannot
    tell from the truth, on the page whose whole job is showing that the
    loop is running. `vault_bulk_fetch` has reported exactly this on
    `.unreadable` since Cycle 136 and this caller was throwing it away.

    So the read either succeeds or says why it did not. An empty folder
    still returns `""` -- that is a real answer and a new vault gives it
    -- while a folder that could not be read raises, which `nova_site`'s
    handler turns into a 502 carrying this message.

    `with_times` also returns `{cycle: [(date, time), ...]}` from the
    documents' own mtimes, so the card can show when an entry was
    actually written instead of the stamp the cycle typed by hand.
    """
    files, mtimes = vault_bulk_fetch(JOURNAL_DIR, with_mtimes=True)
    # `files.unreadable`, not `getattr(files, "unreadable", ())`. The
    # tolerant form would be one more silent fallback in the function
    # whose whole point is deleting one: a caller or a test handing this a
    # plain dict would skip the check and get the old behaviour back,
    # quietly. `vault_bulk_fetch` returns a `VaultFiles` and nothing else
    # does, so an AttributeError here is a wrong mock saying so out loud.
    if files.unreadable:
        raise RuntimeError(
            f"journal folder {JOURNAL_DIR} could not be fully read: "
            + "; ".join(files.unreadable)
        )
    entries = assemble_entries(files)
    times = entry_times(mtimes) if entries else {}
    return (entries, times) if with_times else entries


def journal_folder_best_effort():
    """`(entries, unreadable)` -- the folder without the refusal above.

    `journal_markdown` refuses a partial read because its caller renders
    the whole feed, and an entry list missing an unknown number of entries
    is indistinguishable from a loop that did not run. `_entry_for` is
    looking for **one identifiable entry**, and can tell whether it found
    it, so the same partial read is a perfectly good answer there whenever
    the entry it wants is in the half that arrived.

    Raised by the reviewer on #147: routing that caller through the
    refusing version turned a chunk failure on some unrelated 2026-08-09
    entry into a failed reply to a comment on today's card. Two functions
    rather than a flag, because this repo has already paid for the flag
    version -- Cycle 156 deleted one whose default was the destructive
    answer while 34 of its 35 call sites wanted the other. A name at the
    call site says which question is being asked and cannot be omitted.

    The caller must do something with `unreadable`. Handing it back rather
    than logging it here is the point: "I did not find it" and "I did not
    find it and I could not see all of it" are different answers, and only
    the caller knows which one it is about to state."""
    files, _ = vault_bulk_fetch(JOURNAL_DIR, with_mtimes=True)
    return assemble_entries(files), list(files.unreadable)


def journal_entry_markdown(cycle):
    """Just the newest entry document for one cycle, or None.

    `journal_markdown` above pulls the whole folder because the site's
    journal page renders the whole folder. The reply worker wants one
    entry and was calling the same function to get it: 437KB and 103
    documents assembled and parsed to answer "what did cycle 95 say",
    on every comment the owner leaves, growing by one entry an hour.

    Measured against the live vault 2026-08-11, which is why this exists:
    the folder fetch is **1.496s**, the id listing plus one document is
    **0.057s**. Against a reply that takes 10-15s end to end that is not
    the headline, but it is the only part of it that is pure waste and
    the only part that gets worse every cycle.

    Newest wins, same as the full-journal path it replaces: six cycles
    have written a second entry for one cycle number, and the later one
    is the one whose card he is commenting on. `NNN-cycle-M.md` sorts by
    NNN, so the highest sequence number is the later document.

    Returns None rather than guessing whenever the shape is not exactly
    what it expects -- no folder, no file for that cycle, a tombstone, or
    a document whose heading does not parse to the cycle its filename
    claims. Every one of those falls the caller back to the full journal,
    which is slow and right.
    """
    paths = [p for p in vault_list_ids(JOURNAL_DIR) if file_cycle(p) == cycle]
    if not paths:
        return None
    newest = max(paths, key=lambda p: (entry_seq(p), p))
    # Same normalisation the folder path gets, for the same reason: a
    # document that wrote its heading at the wrong depth parses to zero
    # entries here, and the caller's fallback is the full journal, where
    # that entry is absorbed into its neighbour and so is not found
    # either. Without this, the owner commenting on one of those three cards
    # gets a reply written with no memory of the entry he is replying to.
    #
    # A missing read stays missing rather than becoming `""`: None is this
    # function's documented "I have nothing, fall back", and a tombstone
    # is the case that reaches it.
    raw = vault_read_path(newest)
    return normalise_entry(newest, raw) if raw else raw


def comments_markdown():
    return vault_read_path(COMMENTS_PATH) or ""


def digest_markdown():
    """The live digest, with the rolled-off lines appended to it.

    Two files, one document, and the join is plain concatenation because
    `parse_digest` reads `## Digest` as everything from that heading to
    the next `##` one -- and the archive deliberately has no `##`
    heading, so its lines land inside the live file's digest section
    rather than starting a rival one -- which would not hide **Needs
    the owner** or **Next cycle**, as this comment used to claim, but would
    silently replace the live file's own newest digest lines with the
    archive's older ones, `_sections` keeping the last heading of each
    name. Order is preserved: both files are
    newest-first and the archive holds only lines older than the live
    file's oldest.

    Anything in the archive that is not a digest line -- its frontmatter,
    its `#` title -- fails `_DIGEST_LINE_RE` and is dropped, the same way
    the live file's own prose already is.

    A missing archive is `""`, which makes the split safe in either
    deploy order: before the vault file exists this is exactly the old
    behaviour, and after it the site shows every line it ever showed.
    """
    live = vault_read_path(DIGEST_PATH) or ""
    archive = vault_read_path(DIGEST_ARCHIVE_PATH) or ""
    if not archive:
        return live
    return f"{live}\n\n{archive}"


def cost_ledger_json():
    """The published cost ledger, raw.

    The only fetch here that is not markdown, and the only one written by
    the other repo: `publish_costs` in the bridge rebuilds it from the
    transcripts at the end of every cycle, and this side only ever reads
    it. Shaping is `nova_costs.costs_payload`, which does no I/O, the same
    split every other read on this page follows.

    `""` when the document does not exist, which the shaping turns into a
    page with nothing on it rather than a 502.
    """
    return vault_read_path(COST_LEDGER_PATH) or ""


def retro_ledger_json():
    """The Friday retrospective ledger, raw.

    The second JSON fetch on this server, and the one written by a cycle
    rather than by a publisher: `tools/append_retro.py` adds a row on
    Friday mornings and nothing else touches it. Shaping is
    `nova_retro.retros_payload`, which does no I/O.

    `""` before the first retro has ever run, which the shaping turns
    into a page with nothing on it rather than a 502 -- the same split
    `cost_ledger_json` draws, and it matters more here, because this
    ledger is empty by design until the first Friday.
    """
    return vault_read_path(RETRO_LEDGER_PATH) or ""


def board_markdown(name):
    """`(edvard, nova, nova_archive)` markdown for one board.

    Three reads rather than one because they are three files: two by two
    authors, which is why the page shows them as two tabs, plus the
    archive that `tools/roll_captures.py` moves my older captures into.
    All fetched together so the payload is built from one consistent
    moment rather than from whichever the client asked for first.

    **The archive is returned separately rather than concatenated**,
    which is where this differs from `digest_markdown` above. That one
    can join two files into one string because `parse_digest` reads a
    named section and the archive deliberately has no rival heading.
    These two both have a real `## Entries` heading of their own *and*
    frontmatter, and `parse_notes` joins any non-bullet line onto the
    note above it -- so concatenating would silently glue
    `maintenance: Captures rolled off ...` onto the end of my oldest live
    capture. Two parses, appended, cannot do that.

    A missing file comes back as `""` and parses to an empty board. That
    is deliberate and it is what makes this safe to deploy before the
    first roll ever runs: no archive exists yet, so this is exactly the
    old behaviour until one does.
    """
    paths = BOARD_PATHS[name]
    return (
        vault_read_path(paths["edvard"]) or "",
        vault_read_path(paths["nova"]) or "",
        vault_read_path(paths["nova_archive"]) or "",
    )


def notes_markdown():
    """`notes.md`, raw -- the owner's third capture file.

    The path comes out of `nova_capture.CAPTURE_TARGETS` rather than
    being written here a second time. That map is what the Note button
    writes to, and a page reading a different path from the one the
    button writes would be the two-copies-of-one-constant failure this
    loop has already filed against itself three times.

    `""` if it is missing, which parses to a page with nothing on it --
    the `cost_ledger_json` call rather than the `journal_markdown` one,
    for `plan_markdown`'s reason: an empty notes file is a state a fresh
    vault is legitimately in.
    """
    return vault_read_path(CAPTURE_TARGETS["notes"]) or ""


def goal_history_json():
    """The weekly goal snapshots, raw (`goal-history.json`).

    Written by `tools/append_goal_snapshot.py` at the weekly review and
    read only here. Shaping is `nova_goal_history.series`, which does no
    I/O -- the same split every other read on this page follows.

    `""` before the first snapshot, which the shaping turns into a
    scoreboard with no lines under it rather than a 502. That is the
    right failure: the numbers the owner actually reads are in `goals.md`
    and do not come from this file at all.
    """
    return vault_read_path(GOAL_HISTORY_PATH) or ""


def plan_markdown():
    """`{key: markdown}` for every document on the `/plan` page.

    Two reads rather than one because they are two files with two jobs --
    `roadmap.md` is the order of what to do next, `goals.md` is what any
    of it is for -- and they are fetched together so the page is built
    from one consistent moment rather than from whichever the client
    asked for first, the same reason `board_markdown` reads its three at
    once.

    A missing file comes back as `""`, which `nova_plan` renders as a
    "not written yet" card. That is the `cost_ledger_json` call rather
    than the `journal_markdown` one: the journal folder being unreadable
    means the loop looks dead, which has to be loud, while either of
    these two genuinely not existing is a state a fresh vault is in.
    """
    return {key: vault_read_path(path) or "" for key, _label, path in PLAN_DOCUMENTS}


def catalog_markdown():
    """`nova/catalog.md`, raw -- the service catalog `tools.catalog` writes.

    `""` if it is missing, for `plan_markdown`'s reason: a vault with no
    catalog in it yet is a state this system is legitimately in, and the
    page says so rather than erroring. The path comes from
    `nova_catalog` rather than being written here a second time.
    """
    return vault_read_path(CATALOG_PATH) or ""
