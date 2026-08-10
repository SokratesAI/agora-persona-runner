"""Fetching one journal entry out of the folder, instead of all of them.

The reply worker answers a comment on cycle N's card and needs cycle N's
entry. It got there through `journal_markdown`, which pulls the whole
`nova/journal/` folder because the site's journal *page* renders the whole
folder -- 103 documents and 437KB assembled and parsed to answer a
question about one of them, on every comment Edvard leaves.

Measured against the live vault on 2026-08-11, which is what these tests
are protecting:

    id-range listing (ids only)      0.045s
    _vault_file_docs (all filedocs)  0.701s
    vault_read_path (one document)   0.012s
    vault_bulk_fetch (the folder)    1.496s

So the lookup goes from 1.496s to 0.057s, and stops growing by an entry
an hour. Against a reply that takes 10-15s end to end that is not the
headline -- but it is the only part of it that is pure waste.

Two properties carry the weight. **Newest wins**, because six cycles have
written a second entry under one cycle number and the later one is the
card he is looking at. And **the listing is ids only**, which is the
shortcut that buys the 0.65s -- an id range cannot see a `deleted` flag,
so this is only safe because the caller then reads that one path through
`vault_read_path`, which does check it. That is a lookup returning a miss,
not a listing serving a tombstone, and the difference is the whole reason
`vault_list_ids` carries a docstring telling you not to show anyone its
output.
"""

from unittest.mock import patch

from agora_runner import nova_sources
from agora_runner.nova_journal import JOURNAL_DIR, entry_seq, file_cycle


def _ids(*names):
    return [JOURNAL_DIR + n for n in names]


def _lookup(cycle, ids, docs):
    """`journal_entry_markdown(cycle)` over a fake folder, plus the paths it
    actually fetched."""
    read = []

    def _read(path):
        read.append(path)
        return docs.get(path)

    with patch.object(nova_sources, "vault_list_ids", return_value=ids), \
            patch.object(nova_sources, "vault_read_path", side_effect=_read):
        return nova_sources.journal_entry_markdown(cycle), read


def test_it_fetches_exactly_one_document():
    """The point of the change. If this ever fetches two the saving is gone
    and nothing else in the suite would notice."""
    ids = _ids("100-cycle-91.md", "101-cycle-92.md", "102-cycle-93.md")
    docs = {p: f"### Cycle {p[-5:-3]}" for p in ids}
    content, read = _lookup(92, ids, docs)
    assert read == _ids("101-cycle-92.md")
    assert content == docs[_ids("101-cycle-92.md")[0]]


def test_a_second_entry_for_the_same_cycle_wins_over_the_first():
    """Six cycles have written two entries under one number -- an addendum
    after the first was already filed. The later document is the one whose
    card he is commenting on, and the sequence prefix is what orders them:
    `104-` is after `103-` whatever the cycle number says."""
    ids = _ids("103-cycle-94.md", "104-cycle-94.md")
    docs = {ids[0]: "first", ids[1]: "the addendum"}
    content, read = _lookup(94, ids, docs)
    assert content == "the addendum"
    assert read == [ids[1]]


def test_sequence_numbers_are_compared_as_numbers_not_as_text():
    """Not a bug in today's folder, and the honest version of this claim is
    narrower than it first looked. `entry_filename` zero-pads to three
    digits and all 103 real files are padded, so lexical and numeric order
    agree right up to `999` -> `1000`, where they stop: `"1000-" < "999-"`.
    That is the real boundary, and it is what this uses.

    Two things reach the padding-free case sooner. `entry_seq` deliberately
    accepts an unnumbered file rather than dropping it, so a hand-added
    entry is already outside the convention; and nothing enforces the
    padding on a file written by anything other than `entry_filename`."""
    ids = _ids("999-cycle-990.md", "1000-cycle-990.md")
    docs = {ids[0]: "the old one", ids[1]: "the new one"}
    content, _ = _lookup(990, ids, docs)
    assert content == "the new one"


def test_a_cycle_with_no_document_returns_nothing_rather_than_a_neighbour():
    """The caller falls back to the full journal on None -- which is right
    for the pre-split archive. Returning the nearest entry instead would
    put another cycle's work in front of the model with no signal at all."""
    ids = _ids("100-cycle-91.md", "101-cycle-92.md")
    content, read = _lookup(93, ids, {p: "x" for p in ids})
    assert content is None
    assert read == []


def test_an_empty_folder_returns_nothing():
    """What the vault looked like before the 2026-08-09 split, and what a
    failed listing looks like today: `vault_list_ids` returns `[]` rather
    than raising when CouchDB says no."""
    content, read = _lookup(92, [], {})
    assert content is None
    assert read == []


def test_a_tombstoned_document_returns_nothing_rather_than_its_old_text():
    """The id listing cannot see `deleted` -- that flag is on the document,
    and skipping the document bodies is the entire saving. So the tombstone
    check lands on `vault_read_path`, which returns None. That has to reach
    the caller as a miss, or a deleted entry comes back to life on a card."""
    ids = _ids("101-cycle-92.md")
    content, read = _lookup(92, ids, {})  # read_path returns None, as for a tombstone
    assert content is None
    assert read == ids


def test_a_filename_without_a_cycle_number_is_ignored():
    """`_context.md` and anything hand-added sit in the same folder."""
    ids = _ids("_context.md", "101-cycle-92.md", "notes.md")
    docs = {p: p for p in ids}
    content, read = _lookup(92, ids, docs)
    assert read == _ids("101-cycle-92.md")
    assert content == read[0]


def test_the_two_filename_readers_agree_with_the_assembler():
    """`file_cycle` and `entry_seq` are what this lookup replaces a full
    parse with, and `assemble_entries` has always ordered the page by the
    same two fields. They are now one implementation rather than two."""
    assert file_cycle(JOURNAL_DIR + "104-cycle-95.md") == 95
    assert file_cycle(JOURNAL_DIR + "_context.md") is None
    assert entry_seq(JOURNAL_DIR + "104-cycle-95.md") == 104
    assert entry_seq(JOURNAL_DIR + "notes.md") == -1
