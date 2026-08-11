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
from agora_runner.nova_comments import COMMENTS_PATH
from agora_runner.nova_journal import (
    DIGEST_ARCHIVE_PATH,
    DIGEST_PATH,
    JOURNAL_DIR,
    JOURNAL_PATH,
    assemble_entries,
    entry_seq,
    entry_times,
    file_cycle,
)
from agora_runner.vault import vault_bulk_fetch, vault_list_ids, vault_read_path


def journal_markdown(with_times=False):
    """The entries, from the per-entry documents, falling back to the
    monolith.

    Both sources are read the same number of round trips:
    `vault_bulk_fetch` pulls a whole folder with two batched `_all_docs`
    POSTs regardless of how many files are in it, so splitting 70 entries
    into 70 documents costs the site nothing. What it buys is on the
    other side -- a Nova cycle needs the newest three entries and used to
    have to read all 291KB to get them.

    The fallback is what makes the migration safe in either order: until
    the folder exists this returns the archive, and once it does the
    archive is ignored.

    `with_times` also returns `{cycle: [(date, time), ...]}` from the
    documents' own mtimes, so the card can show when an entry was
    actually written instead of the stamp the cycle typed by hand. The
    archive has no per-entry files and so no times; its headings are all
    the site ever had for those.
    """
    files, mtimes = vault_bulk_fetch(JOURNAL_DIR, with_mtimes=True)
    entries = assemble_entries(files)
    times = entry_times(mtimes) if entries else {}
    markdown = entries or (vault_read_path(JOURNAL_PATH) or "")
    return (markdown, times) if with_times else markdown


def journal_entry_markdown(cycle):
    """Just the newest entry document for one cycle, or None.

    `journal_markdown` above pulls the whole folder because the site's
    journal page renders the whole folder. The reply worker wants one
    entry and was calling the same function to get it: 437KB and 103
    documents assembled and parsed to answer "what did cycle 95 say",
    on every comment Edvard leaves, growing by one entry an hour.

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
    return vault_read_path(newest)


def comments_markdown():
    return vault_read_path(COMMENTS_PATH) or ""


def digest_markdown():
    """The live digest, with the rolled-off lines appended to it.

    Two files, one document, and the join is plain concatenation because
    `parse_digest` reads `## Digest` as everything from that heading to
    the next `##` one -- and the archive deliberately has no `##`
    heading, so its lines land inside the live file's digest section
    rather than starting a rival one. Order is preserved: both files are
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


def board_markdown(name):
    """`(edvard, nova)` markdown for one board -- issues, or ideas.

    Two reads rather than one because they are two files by two authors,
    and the page shows them as two tabs for exactly that reason. Both are
    fetched together so the payload is built from one consistent moment
    rather than from whichever the client asked for first.

    A missing file comes back as `""` and parses to an empty board. That
    is deliberate: `nova/resources/ideas.md` existing is not something
    this site should require, and half a page beats a 502.
    """
    paths = BOARD_PATHS[name]
    return (
        vault_read_path(paths["edvard"]) or "",
        vault_read_path(paths["nova"]) or "",
    )
