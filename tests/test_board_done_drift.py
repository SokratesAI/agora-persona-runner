"""`tools.board_done_drift` -- the ledger and the board disagreeing about "finished".

Every board here is hand-written rather than derived from a live file, on
purpose: a fixture built by asking `parse_board` which rows are open would
select its inputs with the same rule the tool judges them by, and would
pass on a tool that judged nothing at all.

Two failures point opposite ways. Raising too little leaves a finished row
at the top of `top_board_rows` for every cycle to re-derive, which is what
happened to idea #152 for five days. Raising too much puts a cycle's
deliberate state -- a row parked on the owner, a row a later cycle closed
without claiming -- into a bucket that stops the sweep, and a check that is
red every morning is one nobody reads.
"""

import json

from tools.board_done_drift import check, newest_board_claims, report


def board(*rows):
    """A board file with `rows` on `## Board`, each `(number, status)`."""
    lines = ["---", "type: board", "---", "", "# Ideas", "", "## Board", "",
             "| # | Item | Status | Updated | Priority |",
             "| --- | --- | --- | --- | --- |"]
    for number, status in rows:
        lines.append(f"| [[#{number} — a thing\\|{number}]] | a thing "
                     f"| {status} | 09-06 | 🟠 High |")
    return "\n".join(lines) + "\n"


def ledger(*claims):
    return json.dumps({"claims": [
        {"item": item, "cycle": cycle, "state": state, "at": at,
         "outcome": "what happened"}
        for item, cycle, state, at in claims]})


def fetcher(claims_text, ideas_text, issues_text):
    def fetch(path):
        if path.endswith("claims.json"):
            return claims_text
        if path.endswith("ideas.md"):
            return ideas_text
        if path.endswith("issues.md"):
            return issues_text
        raise AssertionError(f"unexpected fetch: {path}")
    return fetch


EMPTY = board()


def test_done_claim_against_an_open_cell_is_the_finding():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10")),
                      board((152, "🟡 In progress")), EMPTY))
    assert [(f[0], f[1]) for f in findings] == [("ideas", 152)]
    assert (blocked, mirrors, unreadable, swept) == ([], [], [], 1)
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=open("/dev/null", "w")) == 2


def test_done_claim_against_a_closed_cell_is_not_a_finding():
    # The precondition matters: the claim really is `done` and the row
    # really did carry one, so this cannot pass by finding no claim at all.
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10")),
                      board((152, "✅ Done")), EMPTY))
    assert swept == 1
    assert (findings, blocked, mirrors) == ([], [], [])


def test_a_done_claim_on_a_blocked_row_prints_and_does_not_raise():
    # Same claim, same tool, one cell different from the finding case above:
    # `top_board_rows` ranks a blocked row out of the ranking, so nothing is
    # being re-offered to another cycle.
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("issue-131", 989, "done", "2026-09-05T19:13")),
                      EMPTY, board((131, "⏸ Blocked on Edvard"))))
    assert findings == []
    assert [(b[0], b[1]) for b in blocked] == [("issues", 131)]
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=open("/dev/null", "w")) == 0


def test_a_closed_cell_with_an_open_claim_prints_and_does_not_raise():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("issue-30", 1059, "progressed", "2026-09-06T14:25")),
                      EMPTY, board((30, "✅ Done"))))
    assert (findings, blocked) == ([], [])
    assert [(m[0], m[1]) for m in mirrors] == [("issues", 30)]
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=open("/dev/null", "w")) == 0


def test_the_newest_claim_for_a_row_is_the_one_that_counts():
    # A row finished, then re-taken and handed on by a later cycle. Reading
    # the `done` would report drift on work that is genuinely open again.
    findings, _, _, _, swept = check(
        fetch=fetcher(ledger(("idea-121", 1050, "done", "2026-09-06T09:00"),
                             ("idea-121", 1073, "progressed", "2026-09-06T18:07")),
                      board((121, "🟡 In progress")), EMPTY))
    assert swept == 1
    assert findings == []


def test_the_newest_claim_wins_regardless_of_position_in_the_file():
    # Overlapping cycles append out of order, so the last row in the file is
    # not the newest claim. Same two claims as above, written the other way
    # round -- the answer may not change.
    findings, _, _, _, _ = check(
        fetch=fetcher(ledger(("idea-121", 1073, "progressed", "2026-09-06T18:07"),
                             ("idea-121", 1050, "done", "2026-09-06T09:00")),
                      board((121, "🟡 In progress")), EMPTY))
    assert findings == []


def test_a_slug_that_names_no_board_row_is_skipped():
    assert newest_board_claims({"claims": [
        {"item": "journal-seq-1136", "state": "done"},
        {"item": "buried-capture-pile", "state": "done"},
        {"item": "idea-152", "state": "done"},
    ]}) == {("ideas", 152): {"item": "idea-152", "state": "done"}}


def test_an_unreadable_ledger_is_not_a_clean_sweep():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(None, board((152, "🟡 In progress")), EMPTY))
    assert findings == []
    assert unreadable and unreadable[0].endswith("claims.json")
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=open("/dev/null", "w")) == 1


def test_a_ledger_that_is_not_json_is_unreadable_rather_than_empty():
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher("[not found: claims.json]{", EMPTY, EMPTY))
    assert unreadable and report(findings, blocked, mirrors, unreadable, swept,
                                 out=open("/dev/null", "w")) == 1


def test_an_unreadable_board_reports_and_does_not_hide_the_other_one():
    # The ideas board is gone; the issues board still carries real drift.
    # Unreadable wins the exit code, and the finding still prints.
    findings, blocked, mirrors, unreadable, swept = check(
        fetch=fetcher(ledger(("issue-168", 1070, "done", "2026-09-06T17:39")),
                      None, board((168, "🟡 In progress"))))
    assert [(f[0], f[1]) for f in findings] == [("issues", 168)]
    assert unreadable and unreadable[0].endswith("ideas.md")
    assert report(findings, blocked, mirrors, unreadable, swept,
                  out=open("/dev/null", "w")) == 1


def test_a_board_row_with_no_claim_is_not_swept():
    # The ledger's window is about a day, so most rows carry no claim at
    # all. Counting them would make `swept` a board size and the "no drift"
    # line meaningless.
    _, _, _, _, swept = check(
        fetch=fetcher(ledger(("idea-152", 759, "done", "2026-09-01T12:10")),
                      board((152, "✅ Done"), (999, "🟡 In progress")), EMPTY))
    assert swept == 1


class _Ran:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.returncode = stdout, returncode


def test_fetch_reads_a_not_found_body_as_a_missing_document(monkeypatch):
    # `vault_tool.py get` prints `[not found: <path>]` on stdout and exits 0.
    # Returned as text, a missing board parses to no rows and reads exactly
    # like a board with no drift on it.
    import tools.board_done_drift as tool
    monkeypatch.setattr(tool.subprocess, "run",
                        lambda *a, **k: _Ran("[not found: ideas.md]\n"))
    assert tool._fetch("ideas.md") is None


def test_fetch_returns_a_real_document():
    # The precondition for the test above: a body that is not a not-found
    # marker does come back, so that test cannot pass by returning None
    # for everything.
    import tools.board_done_drift as tool
    real = board((152, "✅ Done"))
    tool.subprocess.run, saved = (lambda *a, **k: _Ran(real)), tool.subprocess.run
    try:
        assert tool._fetch("ideas.md") == real
    finally:
        tool.subprocess.run = saved
