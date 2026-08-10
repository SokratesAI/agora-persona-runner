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

from agora_runner.nova_comments import COMMENTS_PATH
from agora_runner.nova_journal import DIGEST_PATH, JOURNAL_DIR, JOURNAL_PATH, assemble_entries
from agora_runner.vault import vault_bulk_fetch, vault_read_path


def journal_markdown():
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
    """
    entries = assemble_entries(vault_bulk_fetch(JOURNAL_DIR))
    return entries or (vault_read_path(JOURNAL_PATH) or "")


def comments_markdown():
    return vault_read_path(COMMENTS_PATH) or ""


def digest_markdown():
    return vault_read_path(DIGEST_PATH) or ""
