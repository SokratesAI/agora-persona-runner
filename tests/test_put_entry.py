"""The journal `<seq>` race, at 18-minute cycles.

Every test here is written against the failure it exists to stop, not
against the implementation, and two of them assert the *old* behaviour is
gone rather than that the new one is present -- see
`test_two_cycles_never_share_a_seq`, which is the whole point of the tool.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import argparse

import pytest

from agora_runner.nova_claims import dumps, load, take
from agora_runner.nova_journal import file_cycle
from tools.put_entry import (
    GRANTED,
    LOST,
    REFUSED,
    entry_name,
    next_seq,
    reserve_seq,
    seq_slug,
    taken_seqs,
    weekly_slug,
)

OSLO = ZoneInfo("Europe/Oslo")
NOW = datetime(2026, 8, 23, 15, 20, tzinfo=OSLO)

FOLDER = [
    "projects/sokrates/projects/agora/nova/journal/368-cycle-340.md",
    "projects/sokrates/projects/agora/nova/journal/369-cycle-341.md",
    "projects/sokrates/projects/agora/nova/journal/370-cycle-342.md",
]


class FakeLedger:
    """One shared claims document, the way CouchDB holds it.

    `claim_once` here does what `vault_claim_once` does against the real
    vault: read, decide, write back. `bump_rev` simulates another cycle
    claiming something else in between, which is the `LOST` path.
    """

    def __init__(self):
        self.text = dumps(load(""))
        self.rev = 0
        self.steal_next_write = False

    def claim_once(self, cycle):
        def once(slug, cycle_number=None):
            ledger = load(self.text)
            granted, _ = take(ledger, slug, cycle_number or cycle, NOW)
            if not granted:
                return REFUSED
            if self.steal_next_write:
                self.steal_next_write = False
                self.rev += 1
                return LOST
            self.text = dumps(ledger)
            self.rev += 1
            return GRANTED

        return once


def test_next_seq_is_previous_highest_plus_one():
    assert next_seq(FOLDER) == 371
    assert next_seq([]) == 1


def test_a_gap_in_the_folder_is_never_filled_in():
    """369 missing means a cycle wrote nothing. `prompt.md` forbids repairing it."""
    holed = [FOLDER[0], FOLDER[2]]
    assert taken_seqs(holed) == {368, 370}
    assert next_seq(holed) == 371


def test_seq_slug_pads_so_one_number_has_one_name():
    assert seq_slug(70) == "journal-seq-070"
    assert seq_slug(370) == "journal-seq-370"
    assert seq_slug(7) != seq_slug(70)


def test_entry_name_carries_both_numbers():
    assert entry_name(371, 343) == "371-cycle-343.md"


def test_weekly_entry_drops_the_cycle_number():
    """A weekly run's entry is named by what it is, not by a colliding number.

    Its heartbeat is its own Agora conversation with its own counter, so the
    `<n>` it would stamp starts at 1 and is a number an hourly cycle already
    used. The slug replaces it; the `<seq>` prefix is untouched, because that
    is the journal's only total order and a weekly entry takes its place in it.
    """
    assert entry_name(421, 1, "monday-research") == "421-monday-research.md"
    assert file_cycle("421-monday-research.md") is None


def test_weekly_slug_refuses_a_name_the_gap_detector_would_misread():
    """`cycle-` in a weekly slug would file it as an hourly cycle.

    `nova_journal.file_cycle` matches `-cycle-(\\d+)` anywhere in the stem, so
    `monday-cycle-3` would answer 3 and `cycle_health.missing_cycles` would
    stop reporting a real hourly cycle 3 that never ran.
    """
    assert weekly_slug("friday-retrospective") == "friday-retrospective"
    for bad in ("cycle-3", "monday-cycle-3", "Monday-Research", "monday--research",
                "-monday", "monday research", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            weekly_slug(bad)


def test_two_cycles_never_share_a_seq():
    """The bug this tool exists for.

    Before it, both cycles computed 371, wrote `371-cycle-343.md` and
    `371-cycle-344.md` -- different paths, so the compare-and-swap never
    fired and both landed. Here the second one is refused 371 and bumps.
    """
    shared = FakeLedger()
    first, _ = reserve_seq(343, FOLDER, shared.claim_once(343))
    second, trail = reserve_seq(344, FOLDER, shared.claim_once(344))
    assert first == 371
    assert second == 372
    assert first != second
    assert any("held by another cycle" in line for line in trail)


def test_three_overlapping_cycles_get_three_numbers():
    shared = FakeLedger()
    got = [reserve_seq(c, FOLDER, shared.claim_once(c))[0] for c in (343, 344, 345)]
    assert got == [371, 372, 373]
    assert len(set(got)) == 3


def test_a_lost_compare_and_swap_retries_the_same_number():
    """Losing the CAS says somebody claimed something *else*, not this.

    Bumping there would burn a sequence number for no reason and leave a
    hole the folder listing can never explain.
    """
    shared = FakeLedger()
    shared.steal_next_write = True
    seq, trail = reserve_seq(343, FOLDER, shared.claim_once(343))
    assert seq == 371
    assert any("lost the ledger compare-and-swap" in line for line in trail)


def test_a_seq_already_on_disk_is_skipped_without_asking_the_ledger():
    """The folder is the long-term truth; the ledger only has a 45-minute TTL.

    A claim that has aged out must not hand back a number whose entry is
    already written -- so the file check comes first.
    """
    asked = []

    def once(slug, cycle):
        asked.append(slug)
        return GRANTED

    seq, _ = reserve_seq(343, FOLDER, once, start=369)
    assert seq == 371
    assert asked == ["journal-seq-371"]


def test_it_gives_up_rather_than_looping_forever():
    seq, trail = reserve_seq(343, FOLDER, lambda slug, cycle: REFUSED, attempts=3)
    assert seq is None
    assert len(trail) == 3


def test_scratch_paths_are_private_to_the_process():
    """Two overlapping cycles can share one bridge pod, and therefore `/tmp`.

    A fixed scratch name would have both of them writing one revision
    file, so the second read hands the first cycle somebody else's
    revision to compare-and-swap against -- the guard reports success
    while guarding the wrong document. `prompt.md`'s claim block uses
    `$$` for this; here it is the pid.
    """
    import os

    from tools.put_entry import _private

    path = _private("/tmp", "claims.rev")
    assert str(os.getpid()) in path
    assert path.startswith("/tmp/put_entry.")
    assert path.endswith(".claims.rev")
    assert _private("/tmp", "claims.rev") != _private("/tmp", "entry.rev")


class FakeVault:
    """Enough of `Vault` to drive `release_seq`, holding one ledger in memory."""

    def __init__(self, lose_first=0):
        self.text = dumps(load(""))
        self.lose_first = lose_first
        self.puts = 0

    def get(self, path, rev_file):
        return self.text

    def put(self, path, local, rev_file):
        self.puts += 1
        if self.lose_first > 0:
            self.lose_first -= 1
            return 3
        self.text = open(local, encoding="utf-8").read()
        return 0


def _claims(vault):
    return load(vault.text)["claims"]


def test_a_written_entry_releases_its_number(tmp_path):
    """A number taken and never released says a cycle died, not that it wrote.

    `prune` collects an abandoned open row after a day as of runner#314, so
    this is no longer the difference between a permanent row and none -- it
    is the difference between a row that records the entry landing and a row
    that spends a day claiming a cycle was killed mid-write. About 80 a day
    at an 18-minute heartbeat, in the one document every claim in this loop
    reads and rewrites. My first version never released, and a reviewer
    found it.
    """
    from tools.put_entry import GRANTED, release_seq, vault_claim_once

    vault = FakeVault()
    work = str(tmp_path)
    assert vault_claim_once(vault, work)(seq_slug(400), 343) == GRANTED
    assert [row["state"] for row in _claims(vault)] == ["open"]

    assert release_seq(vault, work, 400, 343) == GRANTED
    rows = _claims(vault)
    assert [row["state"] for row in rows] == ["done"]
    assert rows[0]["item"] == "journal-seq-400"


def test_a_lost_release_is_retried_rather_than_swallowed(tmp_path):
    """The entry is already written, so nothing later notices a dropped release."""
    from tools.put_entry import GRANTED, release_seq, vault_claim_once

    vault = FakeVault()
    work = str(tmp_path)
    vault_claim_once(vault, work)(seq_slug(400), 343)
    vault.lose_first = 2
    assert release_seq(vault, work, 400, 343) == GRANTED
    assert [row["state"] for row in _claims(vault)] == ["done"]


def test_the_refusal_is_not_vacuous():
    """Guard against a test that would pass with the bug still present.

    If `reserve_seq` ignored its `claim_once` answer entirely, every test
    above except this one would still be green for the single-cycle case.
    """
    with pytest.raises(ValueError):
        reserve_seq(343, FOLDER, lambda slug, cycle: "something-else")
