"""The milestone tier: `milestone_ranks`, and where it sits in `rank`.

Milestone M4 of idea #260's picking redesign. The spec hands this tier to
me rather than to the owner and says how to order it: *"Rank by whatever
importance signal the milestone carries, divided by its rolled-up size ...
size alone would let a trivial milestone nobody needs jump ahead of an
important large one."*

So the three things that have to hold are the divide itself (a big
important milestone can lose to a small one, and a trivial small one still
loses to an important big one), the rollup rule (importance is the best row
in the group, size is the sum), and the placement -- below the project
order, above the row's own rating. Each test names which of the three it
is, because a ranking test that only asserts a final order does not say
which rule produced it.
"""

from agora_runner.nova_boards import PRIORITY_LABELS, STATUS_LABELS, parse_board
from agora_runner.nova_next import milestone_ranks, rank

from tests.test_nova_next import NOW, _payload, ledger

IMMEDIATE = PRIORITY_LABELS["immediate"]
HIGH = PRIORITY_LABELS["high"]
MEDIUM = PRIORITY_LABELS["medium"]
LOW = PRIORITY_LABELS["low"]
BACKLOG = STATUS_LABELS["backlog"]

PROJECTS = """| Project | Priority | Order |
|---|---|---|
| Marcus | 🔴 Immediately | |
| Demos | ⚪ Low | |
"""


def row(number, priority, project, size, milestone, updated="08-01",
        board_name="idea"):
    """One parsed-shaped row. Built by hand rather than through a board
    fixture on purpose: these tests are about the ordering function, and a
    markdown fixture would make a parser change look like a ranking bug."""
    from agora_runner.nova_boards import priority_key, size_key, status_key
    return {
        "number": number,
        "title": f"row {number}",
        "board": board_name,
        "status": BACKLOG,
        "statusKey": status_key(BACKLOG),
        "updated": updated,
        "priority": priority,
        "priorityKey": priority_key(priority),
        "project": project,
        "size": size,
        "sizeKey": size_key(size),
        "milestone": milestone,
    }


def order(rows, projects=None):
    ranked = rank(rows, projects, milestone_ranks(rows))
    return [r["number"] for r in ranked]


def test_a_small_milestone_beats_a_large_one_of_the_same_importance():
    """The divide. Both groups are High; one is 1 point of work, one is 5."""
    rows = [
        row(1, HIGH, "Marcus", "XL", "big"),
        row(2, HIGH, "Marcus", "S", "small"),
    ]
    assert order(rows) == [2, 1]


def test_size_alone_does_not_decide_it():
    """The half the spec calls out by name: importance is the numerator.

    `small` is one S of Low work; `big` is four S's of High work. Under
    smallest-first the trivial one wins, and that is exactly the failure
    the divide exists to avoid.

    It also pins the *scale* of the numerator, which is the one number in
    this module I chose rather than measured. On a linear reading of the
    ratings -- High 3, Low 1, which is what `len(_RANK) - rank` gives --
    `big` scores 3/4 and `small` scores 1/1, so the trivial row wins and
    the divide has bought nothing. On `_IMPORTANCE` it is 5/4 against 1/1.

    #2 is High rather than Immediately on purpose: an Immediately row skips
    to the top a tier above this one, so it would pass whatever the divide
    did.
    """
    rows = [
        row(1, LOW, "Marcus", "S", "small"),
        row(2, HIGH, "Marcus", "S", "big"),
        row(3, LOW, "Marcus", "S", "big"),
        row(4, LOW, "Marcus", "S", "big"),
        row(5, LOW, "Marcus", "S", "big"),
    ]
    assert order(rows)[-1] == 1


def test_importance_is_the_best_row_and_size_is_the_sum():
    """The rollup rule, asserted on the numbers rather than on an order.

    Max on importance and sum on size is the one design call in this
    function: importance does not accumulate, work does. A milestone
    holding the one Immediately row is an Immediately milestone and does
    not become less urgent by also holding three Low rows.
    """
    rows = [
        row(1, IMMEDIATE, "Marcus", "S", "mixed"),
        row(2, LOW, "Marcus", "S", "mixed"),
        row(3, HIGH, "Marcus", "M", "solo"),
    ]
    ranks = milestone_ranks(rows)
    # mixed: importance 13 / size 2. solo: importance 5 / size 2.
    assert ranks[("marcus", "mixed")] < ranks[("marcus", "solo")]
    # If importance summed instead of maxing, `mixed` would win by even
    # more, so pin the other direction too: drop the Immediately row and
    # the two Low rows must not beat `solo`.
    quiet = milestone_ranks([row(1, LOW, "Marcus", "S", "mixed"),
                             row(2, LOW, "Marcus", "S", "mixed"),
                             row(3, HIGH, "Marcus", "M", "solo")])
    assert quiet[("marcus", "solo")] < quiet[("marcus", "mixed")]


def test_the_same_name_in_two_projects_is_two_milestones():
    """The key is the pair. Two projects may each have a `Backup`."""
    rows = [row(1, HIGH, "Marcus", "S", "Backup"),
            row(2, LOW, "Demos", "S", "Backup")]
    ranks = milestone_ranks(rows)
    assert set(ranks) == {("marcus", "backup"), ("demos", "backup")}


def test_a_milestone_with_no_sized_rows_sorts_behind_every_sized_one():
    """Unestimated sinks -- the same rule an unrated project and an unrated
    row already follow, and the reason is the same: a default here would be
    inventing the number the divide is most sensitive to."""
    rows = [
        row(1, HIGH, "Marcus", "", "unsized"),
        row(2, LOW, "Marcus", "XL", "sized"),
    ]
    assert order(rows) == [2, 1]


def test_a_half_sized_milestone_counts_only_what_is_known():
    """Counting an unsized row as free would reward not sizing things."""
    rows = [row(1, HIGH, "Marcus", "M", "half"), row(2, HIGH, "Marcus", "", "half")]
    ranks = milestone_ranks(rows)
    assert set(ranks) == {("marcus", "half")}
    # M alone, not M plus a phantom: the same group with only the sized row
    # in it scores identically.
    assert milestone_ranks([row(1, HIGH, "Marcus", "M", "half")]) == ranks


def test_an_ungrouped_row_sinks_below_the_grouped_rows_of_its_project():
    rows = [row(1, IMMEDIATE, "Marcus", "S", ""),
            row(2, LOW, "Marcus", "S", "grouped")]
    # #1 is Immediately, so only the milestone tier can put it second --
    # and skip-to-top sits ABOVE the milestone tier, so it does not.
    assert order(rows) == [1, 2]
    quiet = [row(1, HIGH, "Marcus", "S", ""), row(2, LOW, "Marcus", "S", "grouped")]
    assert order(quiet) == [2, 1]


def test_the_project_order_still_outranks_the_milestone_order():
    """Placement, upward: tier 3 above tier 4.

    The Demos row is in a perfectly ranked milestone and the Marcus row is
    ungrouped; his project order has to win anyway, or M3 has been undone.
    """
    from agora_runner.nova_next import project_ranks
    rows = [row(1, HIGH, "Demos", "S", "tight"), row(2, LOW, "Marcus", "", "")]
    assert order(rows, project_ranks(PROJECTS)) == [2, 1]


def test_the_milestone_order_outranks_the_rows_own_rating():
    """Placement, downward: tier 4 above tier 5.

    Both rows are in Marcus, so the project tier ties and the only thing
    that can reorder them is the milestone. Under the old ranking the High
    row wins on its rating alone -- that is the assertion that would have
    passed before this change.
    """
    rows = [row(1, HIGH, "Marcus", "XL", "big"),
            row(2, MEDIUM, "Marcus", "S", "small")]
    assert [r["number"] for r in rank(rows)] == [1, 2]
    assert order(rows) == [2, 1]


def test_a_board_with_no_milestones_ranks_exactly_as_it_did():
    """The call M1 and M3 both made: nothing reshuffles on the day it ships.

    Every row on both live boards is ungrouped today, so `milestone_ranks`
    returns `{}` and the tier is a constant for every row.
    """
    rows = [row(1, HIGH, "Marcus", "XL", ""), row(2, MEDIUM, "Marcus", "S", "")]
    assert milestone_ranks(rows) == {}
    assert order(rows) == [r["number"] for r in rank(rows)]


def test_next_payload_reads_the_milestone_cell_off_a_real_board():
    """End to end, through the parser, because the cell has to survive it."""
    ideas = (
        "## Board\n\n"
        "| # | Idea | Status | Updated | Priority | Project | Size | Milestone |\n"
        "|---|---|---|---|---|---|---|---|\n"
        f"| [[#1 — big\\|1]] | big | {BACKLOG} | 08-01 | {HIGH} | Marcus | XL | big |\n"
        f"| [[#2 — small\\|2]] | small | {BACKLOG} | 08-01 | {MEDIUM} | Marcus | S | small |\n"
        "\n## Done\n\n| # | Item | Updated | Where |\n|---|---|---|---|\n"
    )
    issues = "## Board\n\n| # | Item | Status | Updated |\n|---|---|---|---|\n"
    assert parse_board(ideas)["items"][1]["milestone"] == "small"
    payload = _payload(issues, ideas, ledger(), NOW, projects_markdown=PROJECTS)
    assert [item["number"] for item in payload["next"]] == [2, 1]


def test_three_small_rows_are_more_work_than_one_medium_one():
    """Size *sums* across the group -- it does not take the largest row.

    Taking the largest would make a milestone of three S rows (cost 1) look
    cheaper than one M row (cost 2), so splitting work into more rows would
    make a milestone rank higher. This is the discriminator: under a max
    rollup the answer flips, and every other test here passes either way.
    """
    rows = [
        row(1, HIGH, "Marcus", "S", "three-small"),
        row(2, HIGH, "Marcus", "S", "three-small"),
        row(3, HIGH, "Marcus", "S", "three-small"),
        row(4, HIGH, "Marcus", "M", "one-medium"),
    ]
    ranks = milestone_ranks(rows)
    assert ranks[("marcus", "one-medium")] < ranks[("marcus", "three-small")]
    assert order(rows)[0] == 4
