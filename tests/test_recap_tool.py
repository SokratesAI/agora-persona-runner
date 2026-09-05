"""`tools.recap`'s advisory: which bullets give him something to tap."""

from tools.recap import entry_names, link_report


def test_it_names_the_bullets_with_nothing_to_open():
    lines = link_report([
        "**Hub.** Your start page is at [hub](https://hub.tailc83eb3.ts.net/).",
        "Moved three workloads to server2.",
    ])
    assert lines[0] == "recap: 1 of 2 bullet(s) carry a link."
    assert any("no link: Moved three workloads" in line for line in lines)
    assert any("service discovery" in line for line in lines)


def test_a_fully_linked_recap_gets_the_count_and_no_nagging():
    """The negative half. Without it the test above would pass against a
    report that printed the plea unconditionally."""
    lines = link_report(["See https://hub.tailc83eb3.ts.net/ for the hub."])
    assert lines == ["recap: 1 of 1 bullet(s) carry a link."]


def test_it_is_advisory_and_never_refuses():
    """A cycle with nothing openable to report must still be able to write
    the card -- see the module docstring for why this does not raise."""
    lines = link_report(["Nothing shipped worth opening."])
    assert lines[0] == "recap: 0 of 1 bullet(s) carry a link."


def test_the_window_sorts_by_sequence_number_not_as_text():
    """The folder passed 999 entries on 2026-09-04 and the card went stale.

    A text sort files `1000-cycle-933.md` between `100-` and `101-`, so the
    last name in the list was `999-cycle-932.md` while twenty newer entries
    sat in the middle. The window reads the *tail* of this list, so it was
    summarising a day-and-a-half-old cycle and reporting it as the newest.
    """
    listing = "\n".join(
        "projects/sokrates/projects/agora/nova/journal/" + name
        for name in [
            "100-cycle-83.md",
            "1000-cycle-933.md",
            "101-cycle-84.md",
            "1019-cycle-951.md",
            "999-cycle-932.md",
        ]
    )
    names = entry_names(listing)
    assert names[-1] == "1019-cycle-951.md"
    assert names == [
        "100-cycle-83.md",
        "101-cycle-84.md",
        "999-cycle-932.md",
        "1000-cycle-933.md",
        "1019-cycle-951.md",
    ]


def test_it_ignores_lines_that_are_not_entries():
    """The negative half: without it the test above would pass against a
    reader that returned every line the listing carried."""
    listing = (
        "projects/sokrates/projects/agora/nova/journal/1019-cycle-951.md\n"
        "projects/sokrates/projects/agora/nova/journal/\n"
        "\n"
        "projects/sokrates/projects/agora/nova/journal/notes.txt\n"
    )
    assert entry_names(listing) == ["1019-cycle-951.md"]
