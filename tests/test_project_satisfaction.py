"""His satisfaction with a project -- milestone M5 of idea #260, his field.

The spec (`projects/sokrates/projects/nova/task-prioritization-redesign.md`)
assigns this one to him and to nobody else: *"satisfaction 1-5: mine alone,
a score of 2 or below auto-forces a diagnosis"*. So it is the mirror of the
TRL beside it -- that one has a CLI and no button, this one has a button and
no CLI -- and the test that pins that is the first one here, because a
`tools.project_satisfaction` is exactly the plausible-looking thing a later
cycle would add.

The other invariant worth naming before the details: **unrated and 1 are
different answers.** The spec hangs an automatic diagnosis off "2 or below",
and a project he has never scored must never trip it.

What these pin, in the order the write travels: the missing CLI, the bounds,
`True` not scoring a project 1, the difference between unrated and 1, a cell
read off a table with no Satisfaction column yet, the setter refusing a
project with no row, the capture layer refusing a missing file, and what the
route accepts from a client.
"""

import os
import re

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import (
    PROJECT_SATISFACTION_MAX,
    canonical_satisfaction,
    parse_project_meta,
    parse_project_satisfaction_cell,
    set_project_satisfaction,
)

PLAIN = """---
type: board
---

# Projects

| Project | Priority | Updated |
|---|---|---|
| Marcus | 🔴 Immediately | 09-01 |
| Demos | ⚪ Low | 09-02 |
| Nova | 🟠 High | 09-01 |
"""

WIDE = """---
type: board
---

# Projects

| Project | Priority | Updated | Order | TRL | Satisfaction |
|---|---|---|---|---|---|
| Marcus | 🔴 Immediately | 09-01 | 1 | Proven | 5 |
| Demos | ⚪ Low | 09-02 | 2 |  |  |
| Nova | 🟠 High | 09-01 | 3 | Concept | 1 |
"""


def test_this_field_has_no_cli_because_it_is_his_and_not_mine():
    """The mirror of `tools.project_trl`, and the reason is the whole field.

    A command line for this would let a cycle write his opinion into his
    own file, which is the same mistake as drawing him a button for the
    TRL, from the other end. Deleting this test is how a later cycle
    should say it disagrees -- adding the tool quietly is not.
    """
    tools = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
    assert not os.path.exists(os.path.join(tools, "project_satisfaction.py"))


def test_the_scale_is_one_to_five_and_nothing_else_is_a_score():
    assert PROJECT_SATISFACTION_MAX == 5
    for good in (1, 2, 3, 4, 5, "3", " 4 "):
        assert canonical_satisfaction(good) == int(str(good).strip())
    for bad in (0, 6, -1, "", "  ", None, "three", 2.5, "2.0"):
        assert canonical_satisfaction(bad) is None, bad


def test_a_bool_is_not_a_score_even_though_python_says_true_is_one():
    """`True == 1`, so a client sending `true` would otherwise score a 1."""
    assert canonical_satisfaction(True) is None
    assert canonical_satisfaction(False) is None


def test_unrated_and_one_are_different_answers_and_nothing_collapses_them():
    """The diagnosis fires at 2 or below; silence must never trip it."""
    assert parse_project_satisfaction_cell("") == 0
    assert parse_project_satisfaction_cell("1") == 1
    assert parse_project_satisfaction_cell("nonsense") == 0


def test_a_file_with_no_satisfaction_column_reads_as_unrated():
    meta = parse_project_meta(PLAIN)
    assert [row["satisfaction"] for row in meta.values()] == [0, 0, 0]
    wide = parse_project_meta(WIDE)
    assert wide["marcus"]["satisfaction"] == 5
    assert wide["demos"]["satisfaction"] == 0
    assert wide["nova"]["satisfaction"] == 1


def test_scoring_appends_the_column_to_a_narrow_table_without_a_rewrite():
    after = set_project_satisfaction(PLAIN, "Demos", 4)
    assert parse_project_meta(after)["demos"]["satisfaction"] == 4
    assert "| Project | Priority | Updated | Order | TRL | Satisfaction |" in after
    # Every other row keeps every other cell it had.
    before = parse_project_meta(PLAIN)
    now = parse_project_meta(after)
    for key in ("marcus", "nova"):
        assert now[key] == before[key]


def test_the_name_he_typed_survives_a_lowercase_write():
    after = set_project_satisfaction(WIDE, "marcus", 3)
    assert "| Marcus |" in after
    assert parse_project_meta(after)["marcus"]["satisfaction"] == 3


def test_clearing_a_score_puts_it_back_to_unrated_not_to_one():
    after = set_project_satisfaction(WIDE, "Marcus", "")
    assert parse_project_meta(after)["marcus"]["satisfaction"] == 0
    assert parse_project_meta(after)["marcus"]["trl"] == "Proven"


def test_an_unknown_project_and_an_out_of_range_score_are_refused():
    assert set_project_satisfaction(WIDE, "Ghost", 3) is None
    assert set_project_satisfaction(WIDE, "", 3) is None
    assert set_project_satisfaction(WIDE, "Marcus", 6) is None
    assert set_project_satisfaction(WIDE, "Marcus", 0) is None
    assert set_project_satisfaction(WIDE, "Marcus", True) is None
    assert set_project_satisfaction("no table here", "Marcus", 3) is None


def test_scoring_moves_nothing_else_on_the_row_or_on_any_other_row():
    before = parse_project_meta(WIDE)
    after = parse_project_meta(set_project_satisfaction(WIDE, "Demos", 2))
    assert after["demos"]["satisfaction"] == 2
    for key in after:
        was = dict(before[key])
        now = dict(after[key])
        was.pop("satisfaction")
        now.pop("satisfaction")
        assert was == now, key


def test_the_capture_layer_writes_it_and_refuses_a_missing_file(monkeypatch):
    seen = {}

    def read(path):
        return seen.get("doc", WIDE), "rev-1"

    def write(path, body, if_rev=None):
        seen["written"] = body
        seen["rev"] = if_rev
        return "written"

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", read)
    monkeypatch.setattr(nova_capture, "vault_write_path", write)
    ok, message = nova_capture.set_project_satisfaction("Demos", 3)
    assert ok, message
    assert seen["rev"] == "rev-1"
    assert parse_project_meta(seen["written"])["demos"]["satisfaction"] == 3
    assert "3 of 5" in message

    # A document that is not there is a refusal, the same call the order
    # write makes: a score is a judgement about a project, and a project
    # that has never been rated has no row to judge.
    seen["doc"] = ""
    seen.pop("written", None)
    ok, _message = nova_capture.set_project_satisfaction("Demos", 3)
    assert not ok
    assert "written" not in seen


# --- the HTTP layer: what a client is allowed to send ---


class _Handler:
    """`_post_project_satisfaction` unbound, with the two things it touches."""

    def __init__(self):
        self.sent = []
        self.headers = {}

    def _send_json(self, status, body):
        self.sent.append((status, body))


def _call(payload, result=(True, "Nova is now 3 of 5"), monkeypatch=None):
    import agora_runner.nova_site as nova_site
    from agora_runner.nova_site import NovaSiteHandler

    handler = _Handler()
    calls = []

    def fake_set(project, score):
        calls.append((project, score))
        return result

    monkeypatch.setattr(nova_site, "set_project_satisfaction", fake_set)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    NovaSiteHandler._post_project_satisfaction(handler, payload)
    return handler.sent[-1], calls


def test_a_good_request_trims_the_name_and_passes_the_score(monkeypatch):
    (status, body), calls = _call({"project": "  Nova  ", "score": 3},
                                  monkeypatch=monkeypatch)
    assert status == 200 and body["ok"] is True
    assert calls == [("Nova", 3)]


def test_zero_clears_and_reaches_the_writer_as_the_empty_string(monkeypatch):
    """The page sends `0`; his file spells unrated `""`. They meet here."""
    (status, body), calls = _call({"project": "Nova", "score": 0},
                                  monkeypatch=monkeypatch)
    assert status == 200 and body["ok"] is True
    assert calls == [("Nova", "")]


def test_a_score_that_is_not_an_int_in_range_is_refused_before_any_write(monkeypatch):
    for bad in ("3", 6, -1, 2.5, None, True, False):
        (status, body), calls = _call({"project": "Nova", "score": bad},
                                      monkeypatch=monkeypatch)
        assert status == 400, bad
        assert "score must be" in body["error"], bad
        assert calls == []


def test_a_missing_or_blank_project_is_refused(monkeypatch):
    for bad in ({"score": 3}, {"project": "   ", "score": 3},
                {"project": 7, "score": 3}):
        (status, body), calls = _call(bad, monkeypatch=monkeypatch)
        assert status == 400 and "project must be" in body["error"]
        assert calls == []


def test_a_refused_write_answers_502_rather_than_claiming_success(monkeypatch):
    (status, body), _calls = _call(
        {"project": "Ghost", "score": 3},
        result=(False, "cannot score 'Ghost' as 3"),
        monkeypatch=monkeypatch)
    assert status == 502 and body["ok"] is False and "Ghost" in body["message"]


def test_the_route_is_in_the_post_allowlist_and_dispatched():
    source = open(
        __import__("agora_runner.nova_site", fromlist=["x"]).__file__).read()
    assert '"/api/project/satisfaction"' in source
    assert re.search(
        r'if path == "/api/project/satisfaction":\n\s+'
        r'self\._post_project_satisfaction\(payload\)',
        source)


def test_the_payload_carries_the_score_and_the_width_the_page_draws():
    source = open(
        __import__("agora_runner.nova_site", fromlist=["x"]).__file__).read()
    assert '"satisfaction": (' in source
    assert '"satisfactionMax": PROJECT_SATISFACTION_MAX,' in source


def test_the_control_exists_on_his_project_page_and_posts_the_score():
    """The button is the whole write path, so its absence is the bug."""
    app = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       "agora_runner", "nova_public", "app.js")
    source = open(app, encoding="utf-8").read()
    assert "renderProjectSatisfaction" in source
    assert 'fetch("/api/project/satisfaction"' in source
    assert "feed.appendChild(renderProjectSatisfaction(name, payload));" in source
