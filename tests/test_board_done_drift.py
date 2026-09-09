"""`tools.board_done_drift` -- the ledger and the board disagreeing about "finished".

Every board here is hand-written rather than derived from a live one, on
purpose: a fixture built by asking the tool's own reader which rows are open
would select its inputs with the same rule the tool judges them by, and would
pass on a tool that judged nothing at all.

The rows are **records, not markdown** (issue #203), minted through
`board_document.to_document`, so they outlive `parse_board` rather than
outliving it by accident. The ledger stays a text fixture, because
`claims.json` is this loop's own vault document and no part of a board.

Two failures point opposite ways. Raising too little leaves a finished row
at the top of `top_board_rows` for every cycle to re-derive, which is what
happened to idea #152 for five days. Raising too much puts a cycle's
deliberate state -- a row parked on the owner, a row a later cycle closed
without claiming -- into a bucket that stops the sweep, and a check that is
red every morning is one nobody reads.
"""

import json

from agora_runner import board_document, board_store, entity_id
from tests.test_board_records import FakeStore
from tools.board_done_drift import SLUG_RE, check, newest_board_claims, report


def _row(board, number, status):
    """One board row as its record document."""
    return board_document.to_document(
        {"number": number, "status": status, "done": False}, board)


def store(*docs):
    """A store holding exactly these rows and a *stored* empty registry.

    `_rev` is what makes it stored, and `board_records.contents` refuses a
    registry without one -- an unmigrated store answers `[]` for every board
    and would otherwise read here as a board with no drift.
    """
    return FakeStore(docs, dict(entity_id.new_registry(), _rev="1-abc"))


def ledger(*claims):
    return json.dumps({"claims": [
        {"item": item, "cycle": cycle, "state": state, "at": at,
         "outcome": "what happened"}
        for item, cycle, state, at in claims]})


def fetcher(claims_text):
    """The ledger, and nothing else -- the boards no longer come through here."""
    def fetch(path):
        if path.endswith("claims.json"):
            return claims_text
        raise AssertionError(f"unexpected fetch: {path}")
    return fetch


def devnull():
    return open("/dev/null", "w")


def test_done_claim_against_an_open_cell_is_the_finding():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10"))),
        store=store(_row("idea", 152, "🟡 In progress")))
    assert [(f[0], f[1]) for f in findings] == [("idea", 152)]
    assert (blocked, mirrors, unreadable, swept) == ([], [], [], 1)
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=devnull()) == 2


def test_done_claim_against_a_closed_cell_is_not_a_finding():
    # The precondition matters: the claim really is `done` and the row
    # really did carry one, so this cannot pass by finding no claim at all.
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10"))),
        store=store(_row("idea", 152, "✅ Done")))
    assert swept == 1
    assert (findings, blocked, mirrors) == ([], [], [])


def test_a_done_claim_on_a_blocked_row_prints_and_does_not_raise():
    # Same claim, same tool, one cell different from the finding case above:
    # `top_board_rows` ranks a blocked row out of the ranking, so nothing is
    # being re-offered to another cycle.
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("issue-131", 989, "done", "2026-09-05T19:13"))),
        store=store(_row("issue", 131, "⏸ Blocked on Edvard")))
    assert findings == []
    assert [(b[0], b[1]) for b in blocked] == [("issue", 131)]
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=devnull()) == 0


def test_a_closed_cell_with_an_open_claim_prints_and_does_not_raise():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("issue-30", 1059, "progressed", "2026-09-06T14:25"))),
        store=store(_row("issue", 30, "✅ Done")))
    assert (findings, blocked) == ([], [])
    assert [(m[0], m[1]) for m in mirrors] == [("issue", 30)]
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=devnull()) == 0


def test_both_boards_are_swept_not_one_twice():
    """A row on each board, one drifted. A `check` hardcoded to either board
    reports one finding or none; only reading both reports this pair."""
    findings, _, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10"),
                             ("issue-30", 1059, "progressed", "2026-09-06T14:25"))),
        store=store(_row("idea", 152, "🟡 In progress"),
                    _row("issue", 30, "✅ Done")))
    assert [(f[0], f[1]) for f in findings] == [("idea", 152)]
    assert [(m[0], m[1]) for m in mirrors] == [("issue", 30)]
    assert (unreadable, swept) == ([], 2)


def test_the_newest_claim_for_a_row_is_the_one_that_counts():
    # A row finished, then re-taken and handed on by a later cycle. Reading
    # the `done` would report drift on work that is genuinely open again.
    findings, _, _, _, swept = check(
        fetch=fetcher(ledger(("idea-121", 1050, "done", "2026-09-06T09:00"),
                             ("idea-121", 1073, "progressed", "2026-09-06T18:07"))),
        store=store(_row("idea", 121, "🟡 In progress")))
    assert swept == 1
    assert findings == []


def test_the_newest_claim_wins_regardless_of_position_in_the_file():
    # Overlapping cycles append out of order, so the last row in the file is
    # not the newest claim. Same two claims as above, written the other way
    # round -- the answer may not change.
    findings, _, _, _, _ = check(
        fetch=fetcher(ledger(("idea-121", 1073, "progressed", "2026-09-06T18:07"),
                             ("idea-121", 1050, "done", "2026-09-06T09:00"))),
        store=store(_row("idea", 121, "🟡 In progress")))
    assert findings == []


def test_a_slug_that_names_no_board_row_is_skipped():
    assert newest_board_claims({"claims": [
        {"item": "journal-seq-1136", "state": "done"},
        {"item": "buried-capture-pile", "state": "done"},
        {"item": "idea-152", "state": "done"},
    ]}) == {("idea", 152): {"item": "idea-152", "state": "done"}}


def test_the_claim_slug_names_the_store_s_own_boards():
    """The slug's first half is passed to the store as a board name with no
    translation, so the two sets have to be the same set. A third board added
    to one and not the other silently sweeps nothing on it."""
    words = set(SLUG_RE.pattern.split("(")[1].split(")")[0].split("|"))
    assert words == set(board_document.BOARDS)


def test_an_unreadable_ledger_is_not_a_clean_sweep():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(None), store=store(_row("idea", 152, "🟡 In progress")))
    assert findings == []
    assert unreadable and unreadable[0].endswith("claims.json")
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=devnull()) == 1


def test_a_ledger_that_is_not_json_is_unreadable_rather_than_empty():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher("[not found: claims.json]{"), store=store())
    assert unreadable and report(findings, blocked, mirrors, unreadable, swept,
                                 out=devnull()) == 1


class _HalfReadableStore:
    """The issue board reads; the idea board raises, as an outage does."""

    def __init__(self, store):
        self.store = store

    def read_rows(self, board):
        if board == "idea":
            raise board_store.StoreError("listing idea records: 503 {}")
        return self.store.read_rows(board)

    def read_registry(self):
        return self.store.read_registry()


def test_an_unreadable_board_reports_and_does_not_hide_the_other_one():
    # The ideas board will not read; the issues board still carries real
    # drift. Unreadable wins the exit code, and the finding still prints.
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("issue-168", 1070, "done", "2026-09-06T17:39"))),
        store=_HalfReadableStore(store(_row("issue", 168, "🟡 In progress"))))
    assert [(f[0], f[1]) for f in findings] == [("issue", 168)]
    assert unreadable and "the idea records" in unreadable[0]
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=devnull()) == 1


def test_a_contradictory_document_is_unread_not_swept_around():
    """A `RecordError` is a document that disagrees with its own board. The
    rows around it read fine, and sweeping those would print "no drift" over
    a board nobody looked at whole."""
    stray = dict(_row("idea", 152, "🟡 In progress"), type="something-else")
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10"))),
        store=store(stray))
    assert (findings, swept) == ([], 0)
    assert unreadable and "the idea records" in unreadable[0]
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=devnull()) == 1


def test_a_board_row_with_no_claim_is_not_swept():
    # The ledger's window is about a day, so most rows carry no claim at
    # all. Counting them would make `swept` a board size and the "no drift"
    # line meaningless.
    _, _, _, _, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10"))),
        store=store(_row("idea", 152, "✅ Done"),
                    _row("idea", 999, "🟡 In progress")))
    assert swept == 1


class _Ran:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_fetch_reads_a_not_found_body_as_a_missing_document(monkeypatch):
    # `vault_tool.py get` prints `[not found: <path>]` on stdout and exits 0.
    # Returned as text, a ledger that is gone reads as a ledger holding no
    # claims, and the sweep prints "no drift" having swept nothing.
    import tools.board_done_drift as tool
    monkeypatch.setattr(tool.subprocess, "run",
                        lambda *a, **k: _Ran("[not found: claims.json]\n"))
    assert tool._fetch("claims.json") is None


def test_fetch_returns_a_real_document():
    # The precondition for the test above: a body that is not a not-found
    # marker does come back, so that test cannot pass by returning None
    # for everything.
    import tools.board_done_drift as tool
    real = ledger(("idea-152", 759, "done", "2026-09-01T12:10"))
    tool.subprocess.run, saved = (lambda *a, **k: _Ran(real)), tool.subprocess.run
    try:
        assert tool._fetch("claims.json") == real
    finally:
        tool.subprocess.run = saved
