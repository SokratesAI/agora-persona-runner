"""`tools.recap`'s advisory: which bullets give him something to tap."""

from tools.recap import link_report


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
