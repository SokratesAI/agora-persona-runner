"""His pin overriding a computed milestone position.

The last piece of milestone M4 of idea #260. `milestone_ranks` orders a
project's milestones by importance over rolled-up size, and the spec says
that order is mine by default and his to override one item at a time:
*"The only new piece of real state is [his] optional pin, checked
first, which overrides the computed position for that one item until
removed."*

So the things that have to hold are: a pin moves the milestone it names
and only inside its own project, no pins leaves the computed order
byte-identical to what it was before pins existed, a pin past the end of
the list clamps instead of vanishing, and a position of 0 removes the row
rather than writing a zero into it.
"""

import pytest

from agora_runner.nova_boards import (
    MILESTONE_PINS_PATH, parse_milestone_pins, set_milestone_pin,
)
from agora_runner.nova_next import milestone_ranks

from tests.test_nova_next_milestones import (
    BACKLOG, HIGH, LOW, MEDIUM, row,
)


def rows():
    """Two Nova milestones and one Marcus one, with a known computed order.

    `nova/a` is High at size S and `nova/b` is Low at size XL, so the
    formula puts `a` first; Marcus sits between them once sorted globally,
    which is what makes the cross-project assertion below mean something.
    """
    return [row(1, HIGH, "Nova", "S", "A"),
            row(2, LOW, "Nova", "XL", "B"),
            row(3, MEDIUM, "Marcus", "S", "C")]


def test_no_pins_is_the_function_it_was():
    """Placement: an empty pin map must not perturb the computed order."""
    computed = milestone_ranks(rows())
    assert milestone_ranks(rows(), None) == computed
    assert milestone_ranks(rows(), {}) == computed
    assert computed[("nova", "a")] < computed[("nova", "b")]


def test_a_pin_outranks_the_formula():
    """The override: `b` scores worse than `a` and he put it first."""
    ranked = milestone_ranks(rows(), {("nova", "b"): 1})
    assert ranked[("nova", "b")] < ranked[("nova", "a")]


def test_a_pin_moves_nothing_in_another_project():
    """Scope: pinning inside Nova leaves Marcus's slot where it was."""
    before = milestone_ranks(rows())
    after = milestone_ranks(rows(), {("nova", "b"): 1})
    assert after[("marcus", "c")] == before[("marcus", "c")]


def test_a_pin_permutes_within_the_project_and_keeps_the_slots():
    """A pin is read against the project's own list, not the global one.

    `milestone_ranks` returns one flat numbering across every project, but
    the project tier sorts above it, so what a position means is "where in
    my project's list". The invariant that says so: pinning inside Nova
    leaves the *set* of positions each project holds untouched and only
    permutes which milestone sits in which of Nova's own.
    """
    def slots(ranked):
        held = {}
        for (project, _milestone), position in ranked.items():
            held.setdefault(project, []).append(position)
        return {project: sorted(positions)
                for project, positions in held.items()}

    before = milestone_ranks(rows())
    after = milestone_ranks(rows(), {("nova", "b"): 1})
    assert slots(after) == slots(before)
    assert after[("nova", "b")] < after[("nova", "a")]
    assert sorted(after.values()) == [0, 1, 2]


def test_a_pin_past_the_end_clamps():
    """Milestones close: a 4 he set when there were four means last now."""
    ranked = milestone_ranks(rows(), {("nova", "a"): 9})
    assert ranked[("nova", "a")] > ranked[("nova", "b")]


def test_a_pin_for_a_milestone_no_row_carries_is_ignored():
    """The pin file outlives the milestone; an absent one must not raise."""
    assert milestone_ranks(rows(), {("nova", "gone"): 1}) == \
        milestone_ranks(rows())


def test_two_pins_in_one_project_apply_lowest_first():
    """Determinism: the result may not depend on dict iteration order."""
    rs = rows() + [row(4, MEDIUM, "Nova", "M", "D")]
    pins = {("nova", "b"): 1, ("nova", "d"): 2}
    ranked = milestone_ranks(rs, pins)
    nova = sorted((position, key) for key, position in ranked.items()
                  if key[0] == "nova")
    assert [key[1] for _position, key in nova] == ["b", "d", "a"]
    assert milestone_ranks(rs, dict(reversed(list(pins.items())))) == ranked
    # And with the positions against the alphabet, so sorting the pins by
    # name instead of by position is a different answer rather than the
    # same one. Without this the two sorts agree and the mutant lives.
    reversed_pins = {("nova", "d"): 1, ("nova", "b"): 2}
    flipped = milestone_ranks(rs, reversed_pins)
    nova = sorted((position, key) for key, position in flipped.items()
                  if key[0] == "nova")
    assert [key[1] for _position, key in nova] == ["d", "b", "a"]


def test_set_writes_the_document_when_there_is_none():
    """The first pin has no file to edit, so it makes one."""
    written = set_milestone_pin("", "Nova", "Picking", 1, "09-07")
    assert parse_milestone_pins(written) == {("nova", "picking"): 1}
    assert "type: board" in written


def test_zero_removes_the_row_rather_than_writing_a_zero():
    """"Until he removes it" -- and a milestone absent from the file and a
    milestone he never pinned have to read the same, because they are."""
    written = set_milestone_pin("", "Nova", "Picking", 1, "09-07")
    cleared = set_milestone_pin(written, "nova", "picking", 0)
    assert parse_milestone_pins(cleared) == {}
    # Case-insensitively: `set_milestone_pin` writes back the spelling it
    # was handed, so a `"Picking" not in cleared` passes on a row written
    # as `| nova | picking | 0 |` -- which is exactly the bug this test is
    # for. Measured: that assertion let the "never remove" mutant live.
    assert "picking" not in cleared.lower()
    assert " 0 " not in cleared


def test_setting_the_same_pair_twice_replaces_it():
    written = set_milestone_pin("", "Nova", "Picking", 1, "09-07")
    again = set_milestone_pin(written, "Nova", "Picking", 3, "09-08")
    assert parse_milestone_pins(again) == {("nova", "picking"): 3}
    assert again.count("| Nova | Picking |") == 1


@pytest.mark.parametrize("project,milestone,position", [
    ("", "Picking", 1),
    ("Nova", "", 1),
    ("No|va", "Picking", 1),
    ("Nova", "Pick|ing", 1),
    ("Nova", "Picking", -1),
    ("Nova", "Picking", "top"),
])
def test_refused(project, milestone, position):
    assert set_milestone_pin("", project, milestone, position) is None


def test_a_broken_position_cell_is_dropped_not_read_as_zero():
    """Positions are 1-based everywhere here, so a cell that is not a
    number must not become one -- reading it as 0 would pin it to the top,
    which is the one place a typo must never send something."""
    text = """| Project | Milestone | Position | Updated |
|---|---|---|---|
| Nova | Picking | top | 09-07 |
| Nova | Other | 0 | 09-07 |
| Nova | Good | 2 | 09-07 |
"""
    assert parse_milestone_pins(text) == {("nova", "good"): 2}


def test_the_path_is_in_his_folder():
    """It sits beside `projects.md`, which is the folder that routes to
    Nova's database and that he does not open in Obsidian."""
    assert MILESTONE_PINS_PATH == "projects/sokrates/projects/nova/milestones.md"


def board_markdown(milestone):
    """One open Nova row in `milestone`, in the shape `parse_board` reads."""
    return f"""# Nova — Ideas

## Board

| # | Idea | Status | Updated | Priority | Project | Size | Milestone |
|---|---|---|---|---|---|---|---|
| [[#1 — a row\\|1]] | a row | {BACKLOG} | 09-07 | {HIGH} | Nova | S | {milestone} |
"""


def records_store(milestone):
    """A record store holding one open Nova row in `milestone`.

    Not patched onto the module: a test that both patches the global and
    passes `store=` cannot tell an honoured argument from an ignored one,
    which is how a `store=` parameter becomes a decoration a real caller's
    read lands past (cycle 1345 caught exactly that in `nova_idea_pool`).
    """
    from tests.test_board_records import migrated

    _parsed, fake = migrated(board="idea", markdown=board_markdown(milestone))
    return fake


def pin_store(monkeypatch, milestone):
    """`tools.milestone_pin` reading a record store holding one such row."""
    from tools import milestone_pin

    fake = records_store(milestone)
    monkeypatch.setattr(milestone_pin, "board_store", fake)
    return fake


def test_the_cli_refuses_a_milestone_no_board_row_carries(tmp_path,
                                                          monkeypatch):
    """A typo in `--milestone` writes a pin that resolves to nothing and is
    ignored by the ranking forever, with no error anywhere. `--board` is
    what turns that into an exit code."""
    from tools.milestone_pin import main
    pin_store(monkeypatch, "Picking")
    pins = tmp_path / "milestones.md"
    assert main(["--file", str(pins), "--project", "Nova",
                 "--milestone", "Pikcing", "--position", "1",
                 "--board", "idea"]) == 2
    assert not pins.exists()
    assert main(["--file", str(pins), "--project", "Nova",
                 "--milestone", "Picking", "--position", "1",
                 "--board", "idea"]) == 0
    assert parse_milestone_pins(pins.read_text()) == {("nova", "picking"): 1}


def test_the_cli_unpins_without_needing_the_milestone_to_still_exist(
        tmp_path, monkeypatch):
    """Removing a pin whose milestone was renamed away is exactly when the
    existence check must not fire."""
    from tools.milestone_pin import main
    pin_store(monkeypatch, "Renamed")
    pins = tmp_path / "milestones.md"
    pins.write_text(set_milestone_pin("", "Nova", "Picking", 1, "09-07"),
                    encoding="utf-8")
    assert main(["--file", str(pins), "--project", "Nova",
                 "--milestone", "Picking", "--position", "0",
                 "--board", "idea"]) == 0
    assert parse_milestone_pins(pins.read_text()) == {}


def test_the_check_reads_the_records_and_never_a_board_file(monkeypatch):
    """The point of the conversion, and the only assertion that can see it.

    `known_milestones` returns the same set either way, so nothing about
    its answer can tell a records read from a `parse_board` read. What
    separates them is which function runs: make `parse_board` raise in
    both namespaces and the check must still answer. This is also what
    keeps `agora_runner/nova_next.py`'s markdown entry points free to be
    deleted -- this module was their last caller outside the tests.
    """
    from agora_runner import nova_boards, nova_next
    from tools import milestone_pin

    def refuse(_markdown):
        raise AssertionError("a board file was parsed")

    # Built first: the fixture itself parses a board to fill the store,
    # which is the migration, not the read under test. And deliberately
    # not patched onto the module -- `store=` has to be the thing that
    # answers, or the argument is decoration.
    store = records_store("Picking")
    monkeypatch.setattr(nova_boards, "parse_board", refuse)
    monkeypatch.setattr(nova_next, "parse_board", refuse)
    assert milestone_pin.known_milestones(["idea"], store=store) == {
        ("nova", "picking")}


def test_a_row_in_no_milestone_contributes_nothing_to_the_check():
    """The empty `Milestone` cell is the common case on his boards, and it
    must not become a `(project, "")` pair the check would then accept.
    `--milestone ""` reaching `known_milestones` is a typo, not a milestone,
    and the set is the only place that can tell.
    """
    from tools import milestone_pin

    assert milestone_pin.known_milestones(
        ["idea"], store=records_store("")) == set()


def test_an_unmigrated_store_refuses_rather_than_pinning_nothing(tmp_path,
                                                                 monkeypatch):
    """A board that has never been migrated answers `[]` for its rows, which
    is the same value a board whose rows were all closed answers with. Taken
    quietly that refuses every pin forever with "no open row carries it",
    which is a sentence about his board and not about the store. So the
    refusal from `board_records.contents` is printed as itself, and exit 1
    separates it from the exit 2 a genuine typo gets.
    """
    from tools import milestone_pin
    from tests.test_board_records import FakeStore

    monkeypatch.setattr(milestone_pin, "board_store", FakeStore([], {}))
    pins = tmp_path / "milestones.md"
    assert milestone_pin.main(["--file", str(pins), "--project", "Nova",
                               "--milestone", "Picking", "--position", "1",
                               "--board", "idea"]) == 1
    assert not pins.exists()


def test_top_board_rows_reads_the_pins(monkeypatch):
    """The wiring, which is the half that was silent last time.

    Cycle 1110 found `top_board_rows` passing `rank` two arguments while
    the milestone map was the third, so 57 milestones were inert in the
    one list a cycle actually reads and nothing failed. The pin has the
    same shape -- `render` would rank fine with the argument left off --
    so this asserts the pin reaches the page rather than that the ranking
    function works.
    """
    from tools import top_board_rows as tbr
    monkeypatch.setattr(tbr, "_fetch", lambda path: None)
    assert tbr.fetch_milestone_pins() == ""

    seen = {}
    real = tbr.milestone_ranks
    monkeypatch.setattr(tbr, "milestone_ranks",
                        lambda rows, pins=None: seen.setdefault("pins", pins)
                        or real(rows, pins))
    tbr.render([], milestone_pins_markdown=set_milestone_pin(
        "", "Nova", "Picking", 1, "09-07"))
    assert seen["pins"] == {("nova", "picking"): 1}
