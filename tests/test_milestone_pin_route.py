"""His milestone pin, over HTTP -- the write end of milestone M4 of idea #260.

The store, the reader and the CLI shipped in #848 and nothing he can press
reached any of them: a pin could only be set by a cycle running
`tools.milestone_pin` from a terminal he does not have. This is the route,
the vault write under it, and the wiring that makes the site's own ranking
read the pins the picker was already reading.

Four things these pin: zero is legal on this route and is not legal on the
project-order route beside it, a missing pins file is created rather than
refused, `next_payload` applies the pins, and a refused write answers 502
rather than reporting a pin that never landed.
"""

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import parse_milestone_pins


PINS = """---
type: board
---

# Milestones

| Project | Milestone | Position | Updated |
|---|---|---|---|
| Nova | Picking and planning | 1 | 09-06 |
"""


# --- the vault write: read-modify-write, 409 retry, first-pin creation ---


#: What `projects.md` holds, so a read of the wrong document is not the
#: same bytes as a read of the right one. Without this the fake answers
#: every path identically and a mutation that reads his project ratings
#: and writes them back over his pins passes every test here.
PROJECTS = """---
type: board
---

# Projects

| Project | Priority | Updated | Order |
|---|---|---|---|
| Nova | \U0001f7e0 High | 09-06 | 1 |
"""


def _fake_vault(monkeypatch, doc, results):
    """Stand in for the vault. Returns the log of what was written."""
    seen = {"doc": doc, "written": []}

    def read(path):
        if path == "projects/sokrates/projects/nova/milestones.md":
            return seen["doc"], "rev-1"
        return PROJECTS, "rev-projects"

    def write(path, text, if_rev=None):
        seen["written"].append((path, text, if_rev))
        return results.pop(0)

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", read)
    monkeypatch.setattr(nova_capture, "vault_write_path", write)
    monkeypatch.setattr(nova_capture, "log", lambda *a, **k: None)
    return seen


def test_a_pin_lands_in_the_pins_file_and_not_in_the_projects_file(monkeypatch):
    seen = _fake_vault(monkeypatch, PINS, ["written"])
    ok, message = nova_capture.pin_milestone("Nova", "Second opinion", 2)
    assert ok and "#2" in message
    path, text, if_rev = seen["written"][0]
    # A pin is a decision about a milestone, not a column on a project.
    assert path == "projects/sokrates/projects/nova/milestones.md"
    assert if_rev == "rev-1"
    assert parse_milestone_pins(text)[("nova", "second opinion")] == 2
    # The pin already there is untouched -- this write is one override,
    # not a renumbering of his whole list the way a project order is.
    assert parse_milestone_pins(text)[("nova", "picking and planning")] == 1


def test_the_first_pin_creates_the_file_rather_than_being_refused(monkeypatch):
    """Unlike a project position, and the difference is the point.

    `projects.md` is a list, and a list nobody has written has no
    positions in it. `milestones.md` is a set of overrides, so the first
    override has to be able to land somewhere.
    """
    seen = _fake_vault(monkeypatch, "", ["written"])
    ok, _message = nova_capture.pin_milestone("Nova", "Cost and quota", 1)
    assert ok
    _path, text, _rev = seen["written"][0]
    assert parse_milestone_pins(text) == {("nova", "cost and quota"): 1}


def test_position_zero_removes_the_row_and_says_unpinned(monkeypatch):
    seen = _fake_vault(monkeypatch, PINS, ["written"])
    ok, message = nova_capture.pin_milestone("Nova", "Picking and planning", 0)
    assert ok
    # Not "#0" -- zero is not a place in a list, it is the absence of one.
    assert "unpinned" in message and "#" not in message
    _path, text, _rev = seen["written"][0]
    assert parse_milestone_pins(text) == {}


def test_a_409_is_retried_against_a_freshly_read_document(monkeypatch):
    seen = _fake_vault(monkeypatch, PINS, ["409 conflict", "written"])
    ok, _message = nova_capture.pin_milestone("Nova", "Second opinion", 2)
    assert ok
    assert len(seen["written"]) == 2


def test_a_non_conflict_failure_is_not_retried(monkeypatch):
    seen = _fake_vault(monkeypatch, PINS, ["503 upstream"])
    ok, message = nova_capture.pin_milestone("Nova", "Second opinion", 2)
    assert not ok and "503" in message
    assert len(seen["written"]) == 1


def test_a_name_the_markdown_layer_refuses_never_reaches_the_vault(monkeypatch):
    seen = _fake_vault(monkeypatch, PINS, [])
    ok, message = nova_capture.pin_milestone("No | pipes", "Second opinion", 1)
    assert not ok and "cannot pin" in message
    assert seen["written"] == []


# --- the HTTP layer: what a client is allowed to send ---


class _Handler:
    """`_post_milestone_pin` unbound, with the two things it touches."""

    def __init__(self):
        self.sent = []
        self.headers = {}

    def _send_json(self, status, body):
        self.sent.append((status, body))


def _call(payload, result=(True, "Second opinion is now #2 in Nova"),
          monkeypatch=None):
    import agora_runner.nova_site as nova_site
    from agora_runner.nova_site import NovaSiteHandler

    handler = _Handler()
    calls = []

    def fake_pin(project, milestone, position):
        calls.append((project, milestone, position))
        return result

    monkeypatch.setattr(nova_site, "pin_milestone", fake_pin)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    NovaSiteHandler._post_milestone_pin(handler, payload)
    return handler.sent[-1], calls


def test_a_good_request_trims_both_names_and_passes_the_position(monkeypatch):
    (status, body), calls = _call(
        {"project": "  Nova  ", "milestone": " Second opinion ", "position": 2},
        monkeypatch=monkeypatch)
    assert status == 200 and body["ok"] is True
    assert calls == [("Nova", "Second opinion", 2)]


def test_zero_is_accepted_here_and_reaches_the_write(monkeypatch):
    """The one place this route differs from `/api/project/order`.

    A project always sits somewhere in his list, so there is no
    "unplaced" position; a milestone is pinned or it is not, and 0 is how
    he takes a pin back off. A route that refused 0 would leave the UI
    able to set a pin and unable to clear one.
    """
    (status, _body), calls = _call(
        {"project": "Nova", "milestone": "Second opinion", "position": 0},
        result=(True, "Second opinion is unpinned"), monkeypatch=monkeypatch)
    assert status == 200
    assert calls == [("Nova", "Second opinion", 0)]


def test_a_position_that_is_not_a_whole_number_is_refused_before_any_write(monkeypatch):
    for bad in ("2", -1, 1.5, None, True):
        (status, body), calls = _call(
            {"project": "Nova", "milestone": "Second opinion", "position": bad},
            monkeypatch=monkeypatch)
        assert status == 400, bad
        assert "position must be" in body["error"]
        assert calls == []


def test_a_missing_or_blank_name_on_either_side_is_refused(monkeypatch):
    bad_payloads = (
        {"milestone": "Second opinion", "position": 1},
        {"project": "  ", "milestone": "Second opinion", "position": 1},
        {"project": 7, "milestone": "Second opinion", "position": 1},
        {"project": "Nova", "position": 1},
        {"project": "Nova", "milestone": "   ", "position": 1},
        {"project": "Nova", "milestone": 7, "position": 1},
    )
    for bad in bad_payloads:
        (status, body), calls = _call(bad, monkeypatch=monkeypatch)
        assert status == 400, bad
        assert "must be a non-empty string" in body["error"]
        assert calls == []


def test_a_refused_write_answers_502_rather_than_claiming_success(monkeypatch):
    (status, body), _calls = _call(
        {"project": "Ghost", "milestone": "Nothing", "position": 1},
        result=(False, "cannot pin 'Nothing' at 1"), monkeypatch=monkeypatch)
    assert status == 502 and body["ok"] is False and "Nothing" in body["message"]


def test_the_route_is_in_the_post_allowlist():
    import re

    from agora_runner import nova_site

    source = open(nova_site.__file__).read()
    # Both halves: a handler with no route is dead code, and a route the
    # allowlist does not carry is a 404.
    assert '"/api/milestone/pin"' in source
    assert re.search(
        r'if path == "/api/milestone/pin":\n\s+self\._post_milestone_pin\(payload\)',
        source)


# --- the wiring: the page has to rank by the pins the picker ranks by ---


def _board(*rows):
    """An ideas board with the size and milestone cells the ranking reads."""
    head = ["# Nova — Ideas", "", "## Board", "",
            "| # | Idea | Status | Updated | Priority | Project | Size | Milestone |",
            "|---|---|---|---|---|---|---|---|"]
    for number, title, priority, milestone in rows:
        head.append(f"| [[#{number} — {title}\\|{number}]] | {title} "
                    f"| ⚪ Backlog | 09-07 | {priority} | Nova | S | {milestone} |")
    return "\n".join(head) + "\n"


BOARD = _board(
    (1, "the formula's first pick", "\U0001f7e0 High", "Alpha"),
    (2, "the one he pinned", "\U0001f535 Medium", "Beta"),
)

EMPTY_BOARD = "# Nova — Issues\n\n## Board\n\n| # | Item |\n|---|\n"


def _titles(milestones_markdown):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from tests.test_nova_next import _payload

    payload = _payload(
        EMPTY_BOARD, BOARD, '{"claims": []}',
        datetime(2026, 9, 7, 3, 0, tzinfo=ZoneInfo("Europe/Oslo")),
        milestones_markdown=milestones_markdown)
    return [row["title"] for row in payload["next"]]


def test_the_page_ranks_by_the_formula_when_he_has_pinned_nothing():
    """The control. Without it the test below cannot tell a pin being
    applied from the two rows having been in that order all along."""
    assert _titles("") == ["the formula's first pick", "the one he pinned"]


def test_a_pin_moves_the_page_the_way_it_moves_the_picker():
    """The defect this slice was built on.

    `tools.top_board_rows` read `milestones.md` from the day the store
    shipped; `next_payload` called `milestone_ranks(rows)` with no pins,
    so a pin he set moved the terminal ranking and left the page he set
    it on showing the old order.
    """
    pinned = """| Project | Milestone | Position | Updated |
|---|---|---|---|
| Nova | Beta | 1 | 09-07 |
"""
    assert _titles(pinned) == ["the one he pinned", "the formula's first pick"]


def test_the_site_hands_the_pins_to_the_payload():
    """The route above and the ranker below are both fine on their own;
    what makes the pin visible to him is this argument being passed."""
    from agora_runner import nova_site

    source = open(nova_site.__file__).read()
    assert "milestones_markdown=milestone_pins_markdown()," in source
