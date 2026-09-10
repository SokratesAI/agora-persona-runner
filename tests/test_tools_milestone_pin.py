"""`tools.milestone_pin` reads his boards out of the record store.

Issue #203's seam: a board reader converts by being handed what
`board_records.contents` returns instead of a file. `known_milestones` was
the reader here, it opened two markdown paths, and *nothing tested it* --
the safety check that stops a pin naming nothing had no test at all, which
is part of why it could sit opt-in for as long as it did.

The behaviour change these pin: the check is on by default now, because the
store means the caller no longer has to say where the boards live; and a
store that cannot be read refuses the pin rather than quietly skipping the
check.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from agora_runner import board_records, nova_boards, nova_next
from tools import milestone_pin

_HEAD = ("## Board\n\n"
         "| # | Item | Status | Updated | Priority | Project | Size "
         "| Milestone | Order |\n"
         "|---|---|---|---|---|---|---|---|---|\n")


def contents(*rows):
    """The four keys `board_records.contents` returns, built from real rows.

    Hand-writing the dicts is what I tried first and it is wrong: the row
    shape carries derived keys (`done`, `statusKey`) that `parse_board`
    computes, so a hand-built row makes the reader raise `KeyError` on a
    board the real store would have answered for. Going through
    `nova_boards.parse_board` -- deliberately *not* `nova_next`'s reference
    to it, which the landmine below replaces -- means these fixtures cannot
    drift from what `board_records.contents` really returns.
    """
    return nova_boards.parse_board(_HEAD + "".join(rows))


def row(number, project, milestone, status="⚪ Backlog"):
    return (f"| [[#{number}]] | row {number} | {status} | 2026-09-10 "
            f"| 🔵 Medium | {project} | M | {milestone} | |\n")


def test_known_milestones_reads_records_and_never_markdown(monkeypatch):
    """The conversion itself: no `parse_board` call is reachable from here.

    An equality assertion alone would pass on a reader that quietly went
    back to parsing markdown, because both answers are the same set. The
    landmine is what makes this a test of the seam rather than of the
    answer.
    """
    monkeypatch.setattr(nova_next, "parse_board", _explode)
    found = milestone_pin.known_milestones([
        contents(row(1, "Nova", "Picking and planning")),
        contents(row(2, "Marcus", "Notifications and nudges")),
    ])
    assert found == {("nova", "picking and planning"),
                     ("marcus", "notifications and nudges")}


def test_the_landmine_is_armed(monkeypatch):
    """`parse_board` patched in `nova_next`'s namespace is the one that fires.

    Guards the test above: if the reader resolved `parse_board` somewhere
    else, patching it here would be patching nothing and the landmine would
    be decorative.
    """
    monkeypatch.setattr(nova_next, "parse_board", _explode)
    with pytest.raises(AssertionError):
        nova_next.open_rows("| # | Item |\n", "idea")


def _explode(*_args, **_kwargs):
    raise AssertionError("a converted reader must not parse markdown")


def test_a_milestone_only_on_closed_rows_is_not_known():
    """Pinning a finished milestone would head his drawer with an empty list."""
    board = contents(row(1, "Nova", "Shipped and gone", status="✅ Done"),
                     row(2, "Nova", "Picking and planning"))
    # Precondition: the closed row really is in the input, so the assertion
    # below is about the filter and not about a row I forgot to add.
    assert any(item["milestone"] == "Shipped and gone"
               for item in board["items"])
    assert milestone_pin.known_milestones([board]) == {
        ("nova", "picking and planning")}


def test_store_milestones_asks_for_every_named_board():
    """Both boards, because a project spans them and a milestone is a pair."""
    asked = []

    def fake_contents(board, store=None):
        asked.append(board)
        return contents(row(1, "Nova", f"from {board}"))

    original = board_records.contents
    board_records.contents = fake_contents
    try:
        found = milestone_pin.store_milestones(milestone_pin.CHECKED_BOARDS)
    finally:
        board_records.contents = original
    assert asked == ["issue", "idea"]
    assert found == {("nova", "from issue"), ("nova", "from idea")}


def _pin(tmp_path, monkeypatch, argv, milestones=None, boom=None):
    """Run the CLI against a temp pins file; returns `(exit code, text)`."""
    target = tmp_path / "milestones.md"

    def fake_store_milestones(names, store=None):
        if boom is not None:
            raise boom
        return milestones or set()

    monkeypatch.setattr(milestone_pin, "store_milestones",
                        fake_store_milestones)
    code = milestone_pin.main(["--file", str(target)] + argv)
    return code, (target.read_text() if target.exists() else "")


def test_an_unreadable_store_refuses_the_pin(tmp_path, monkeypatch, capsys):
    """"I could not look" must not spell itself the same way as "it is fine".

    This is the behaviour the opt-in `--boards` could not express: with no
    boards passed, a missing check and a passing check printed the same
    nothing and both wrote the pin.
    """
    code, written = _pin(
        tmp_path, monkeypatch,
        ["--project", "Nova", "--milestone", "Picking", "--position", "1"],
        boom=board_records.UnmigratedStore("the registry has no revision"))
    assert code == 2
    assert written == ""
    err = capsys.readouterr().err
    assert "the registry has no revision" in err
    assert "--no-check" in err


def test_no_check_pins_through_an_unreadable_store(tmp_path, monkeypatch):
    """The escape hatch the refusal advertises actually works.

    Without this the test above passes on a tool that refuses everything.
    """
    code, written = _pin(
        tmp_path, monkeypatch,
        ["--project", "Nova", "--milestone", "Picking", "--position", "1",
         "--no-check"],
        boom=board_records.UnmigratedStore("the registry has no revision"))
    assert code == 0
    assert "Picking" in written


def test_the_check_is_on_without_being_asked_for(tmp_path, monkeypatch):
    """No `--boards` on the command line and the typo is still caught."""
    argv = ["--project", "Nova", "--milestone", "Pickng", "--position", "1"]
    known = {("nova", "picking and planning")}
    code, written = _pin(tmp_path, monkeypatch, argv, milestones=known)
    assert code == 2
    assert written == ""
    # Precondition: the same call with the check off does write, so the
    # refusal above is the check and not a broken argument list.
    code, written = _pin(tmp_path, monkeypatch, argv + ["--no-check"],
                         milestones=known)
    assert code == 0
    assert "Pickng" in written


def test_unpinning_never_consults_the_store(tmp_path, monkeypatch):
    """`--position 0` is how a milestone renamed away gets its pin removed.

    So it has to work when the name is no longer on any row -- and, for the
    same reason, when the store cannot answer at all. The store here raises
    on every call, which is what makes this a test of the skip rather than
    of a lookup that happened to succeed.
    """
    boom = board_records.UnmigratedStore("would have refused")
    argv = ["--project", "Nova", "--milestone", "Gone"]
    code, written = _pin(tmp_path, monkeypatch,
                         argv + ["--position", "1", "--no-check"], boom=boom)
    # Precondition: there is a pin to remove, so the assertion below is
    # about the unpin and not about a file that was always empty.
    assert code == 0 and "Gone" in written
    code, written = _pin(tmp_path, monkeypatch, argv + ["--position", "0"],
                         boom=boom)
    assert code == 0
    assert "Gone" not in written
