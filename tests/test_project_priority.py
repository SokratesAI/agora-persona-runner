"""Rating a project, which is the last line of his 2026-09-01 capture.

*"Each project should also be able to be assigned a priority, making one
project and its tasks more important than others."*

The set of projects is derived from the `Project` cells on his two boards
and deliberately has no second list -- so a project-level rating, which
belongs to no single row, is the first thing about a project that needs a
document of its own. These tests cover the three layers of that: the
markdown read/write in `nova_boards`, the vault write path with its 409
retry, and the ordering the rating buys, which is the half that makes it
a priority rather than a label.
"""

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import (
    PROJECT_META_PATH,
    parse_project_meta,
    rank_projects,
    set_project_priority,
)

RATED = """---
type: board
---

# Projects

| Project | Priority | Updated |
|---|---|---|
| Marcus | 🔴 Immediately | 09-01 |
| NAS | ⚪ Low | 08-30 |
"""


def test_parse_reads_the_rating_and_keys_it_case_insensitively():
    meta = parse_project_meta(RATED)
    assert meta["marcus"]["project"] == "Marcus"
    assert meta["marcus"]["priority"] == "🔴 Immediately"
    assert meta["marcus"]["priorityKey"] == "immediate"
    assert meta["nas"]["updated"] == "08-30"
    # The header and the |---| rule are table lines and neither is a project.
    assert set(meta) == {"marcus", "nas"}


def test_an_existing_project_is_rerated_in_place_not_appended():
    written = set_project_priority(RATED, "marcus", "High", dated="09-02")
    meta = parse_project_meta(written)
    assert meta["marcus"]["priority"] == "🟠 High"
    assert meta["marcus"]["updated"] == "09-02"
    # He typed `marcus`; the file keeps the spelling it already had, so his
    # page heading does not change case because he rated it from a phone.
    assert meta["marcus"]["project"] == "Marcus"
    assert written.count("| Marcus |") == 1
    assert meta["nas"]["priority"] == "⚪ Low"


def test_a_new_project_is_appended_below_the_rows_already_there():
    written = set_project_priority(RATED, "Infra", "Medium", dated="09-02")
    meta = parse_project_meta(written)
    assert meta["infra"]["priority"] == "🔵 Medium"
    assert set(meta) == {"marcus", "nas", "infra"}
    # Appended, not spliced above an existing row: his own edits stay a
    # one-line diff.
    assert written.strip().split("\n")[-1].startswith("| Infra |")


def test_an_empty_file_is_created_whole_so_the_first_rating_is_not_the_one_that_fails():
    written = set_project_priority("", "Marcus", "High", dated="09-02")
    assert written.startswith("---\n")
    assert "# Projects" in written
    assert parse_project_meta(written)["marcus"]["priority"] == "🟠 High"


def test_clearing_a_rating_keeps_the_row_and_empties_the_cell():
    written = set_project_priority(RATED, "Marcus", "", dated="09-02")
    meta = parse_project_meta(written)
    assert "marcus" in meta
    assert meta["marcus"]["priority"] == ""
    assert meta["marcus"]["priorityKey"] == ""


def test_a_name_that_would_break_out_of_the_cell_is_refused():
    # A `|` opens a fourth column, a newline ends the table, a `*` leaves
    # unbalanced emphasis in his own file -- `set_row_project`'s boundary.
    for name in ("Ma|rcus", "Mar\ncus", "*Marcus", "", "  ", "x" * 41):
        assert set_project_priority(RATED, name, "High") is None


def test_a_rating_outside_the_four_labels_is_refused():
    # `priority_key` aliases synonyms on purpose -- "Urgent" is
    # `immediate`, and accepting it is right. "Sideways" is not a rating.
    assert set_project_priority(RATED, "Marcus", "Sideways") is None
    assert set_project_priority(RATED, "Marcus", "Urgent") is not None
    # And the empty string is not one of those -- it is a real answer.
    assert set_project_priority(RATED, "Marcus", "") is not None


def test_rank_puts_the_rated_projects_first_and_keeps_board_order_under_that():
    meta = parse_project_meta(RATED)
    order = rank_projects(["Nova", "NAS", "Marcus", "Infra"], meta)
    # Immediately first, then the two unrated in the order the board gave
    # them, then Low. Unrated above Low on purpose: every project is
    # unrated today, so sorting them last would bury the whole index the
    # first time one project is rated Low.
    assert order == ["Marcus", "Nova", "Infra", "NAS"]


def test_rank_is_stable_when_nothing_is_rated():
    assert rank_projects(["Nova", "NAS", "Marcus"], {}) == ["Nova", "NAS", "Marcus"]


def _writer(monkeypatch, body=RATED, results=("written",)):
    seen = {}
    calls = []
    pending = list(results)
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (body, "7-abc"))

    def fake_write(path, text, if_rev=None):
        calls.append(path)
        seen.update(path=path, body=text, if_rev=if_rev)
        return pending.pop(0) if pending else "written"

    monkeypatch.setattr(nova_capture, "vault_write_path", fake_write)
    return seen, calls


def test_the_write_path_goes_to_his_folder_with_the_revision_it_read(monkeypatch):
    seen, calls = _writer(monkeypatch)
    ok, message = nova_capture.set_project_priority("Marcus", "🟠 High")
    assert ok and "Marcus" in message
    assert calls == [PROJECT_META_PATH]
    assert seen["if_rev"] == "7-abc"
    assert parse_project_meta(seen["body"])["marcus"]["priority"] == "🟠 High"


def test_a_409_is_retried_and_a_refusal_is_not(monkeypatch):
    _seen, calls = _writer(monkeypatch, results=("409 conflict", "written"))
    ok, _message = nova_capture.set_project_priority("Marcus", "High")
    assert ok and len(calls) == 2

    _seen, calls = _writer(monkeypatch)
    ok, message = nova_capture.set_project_priority("Ma|rcus", "High")
    # Re-reading would give the same answer, so this must not spin.
    assert not ok and calls == [] and "cannot rate" in message


def test_a_missing_file_is_written_rather_than_failing(monkeypatch):
    seen = {}
    monkeypatch.setattr(nova_capture, "vault_read_path_rev", lambda p: (None, None))
    monkeypatch.setattr(
        nova_capture, "vault_write_path",
        lambda path, text, if_rev=None: seen.update(body=text) or "written")
    ok, _message = nova_capture.set_project_priority("Marcus", "High")
    assert ok
    assert parse_project_meta(seen["body"])["marcus"]["priority"] == "🟠 High"


def test_the_stamp_is_oslo_and_written_by_the_write_path(monkeypatch):
    from datetime import datetime

    from agora_runner.config import OSLO

    seen, _calls = _writer(monkeypatch)
    nova_capture.set_project_priority("Marcus", "High")
    assert (parse_project_meta(seen["body"])["marcus"]["updated"]
            == datetime.now(OSLO).strftime("%m-%d"))


# --- the HTTP layer: what a client is allowed to send ---


class _Handler:
    """`_post_project_priority` unbound, with the two things it touches."""

    def __init__(self):
        self.sent = []
        self.headers = {}

    def _send_json(self, status, body):
        self.sent.append((status, body))


def _call(payload, result=(True, "Marcus is now 🟠 High"), monkeypatch=None):
    import agora_runner.nova_site as nova_site
    from agora_runner.nova_site import NovaSiteHandler

    handler = _Handler()
    calls = []

    def fake_set(project, priority):
        calls.append((project, priority))
        return result

    monkeypatch.setattr(nova_site, "set_project_priority", fake_set)
    monkeypatch.setattr(nova_site, "audit", lambda *a, **k: None)
    NovaSiteHandler._post_project_priority(handler, payload)
    return handler.sent[-1], calls


def test_a_good_request_stores_the_canonical_label(monkeypatch):
    (status, body), calls = _call(
        {"project": "  Marcus  ", "priority": "high"}, monkeypatch=monkeypatch)
    assert status == 200 and body["ok"] is True
    # Trimmed, and the label written is the one the boards spell, not the
    # word the phone sent -- the same normalisation `_post_priority` does.
    assert calls == [("Marcus", "🟠 High")]


def test_a_rating_outside_the_four_is_refused_before_any_write(monkeypatch):
    (status, body), calls = _call(
        {"project": "Marcus", "priority": "Sideways"}, monkeypatch=monkeypatch)
    assert status == 400 and "priority must be one of" in body["error"]
    assert calls == []


def test_an_empty_rating_is_accepted_because_unrated_is_an_answer(monkeypatch):
    (status, _body), calls = _call(
        {"project": "Marcus", "priority": ""}, monkeypatch=monkeypatch)
    assert status == 200 and calls == [("Marcus", "")]


def test_a_missing_or_blank_project_is_refused(monkeypatch):
    for bad in ({"priority": "High"}, {"project": "   ", "priority": "High"},
                {"project": 7, "priority": "High"}):
        (status, body), calls = _call(bad, monkeypatch=monkeypatch)
        assert status == 400 and "project must be" in body["error"]
        assert calls == []


def test_the_route_does_not_check_the_name_against_a_list(monkeypatch):
    """The create half, at the HTTP layer.

    `_post_project` deliberately has no allowed-projects list because the
    project set is derived from the board cells. This route inherits that
    and must not grow one: a rating on a project name no row carries yet
    is a rating waiting for its first row, which is a normal thing to do
    the day he starts a project.
    """
    (status, _body), calls = _call(
        {"project": "Something Brand New", "priority": "Immediately"},
        monkeypatch=monkeypatch)
    assert status == 200 and calls == [("Something Brand New", "🔴 Immediately")]


def test_a_failed_write_answers_502_rather_than_claiming_success(monkeypatch):
    (status, body), _calls = _call(
        {"project": "Marcus", "priority": "High"},
        result=(False, "could not write project ratings: 409"),
        monkeypatch=monkeypatch)
    assert status == 502 and body["ok"] is False and "409" in body["message"]


def test_the_route_is_in_the_post_allowlist():
    import re

    from agora_runner import nova_site

    source = open(nova_site.__file__).read()
    # Both halves: a handler with no route is dead code, and a route the
    # allowlist does not carry is a 404 -- the shape a previous cycle
    # shipped and could not see, because the unit test called the method
    # directly.
    assert '"/api/project/priority"' in source
    assert re.search(
        r'if path == "/api/project/priority":\n\s+self\._post_project_priority\(payload\)',
        source)


def test_an_unreadable_ratings_file_costs_the_order_not_the_page(monkeypatch):
    """The ranking is the least important thing on the project page.

    It is a second vault read on the critical path of a page that worked
    without one for a week, so a CouchDB blip must degrade to "nothing is
    rated" rather than 500 the rows and the conversation with it.
    """
    def blow_up(_path):
        raise OSError("couchdb is not answering")

    monkeypatch.setattr(nova_capture, "vault_read_path_rev", blow_up)
    logged = []
    monkeypatch.setattr(nova_capture, "log", lambda line: logged.append(line))
    assert nova_capture.project_priorities() == {}
    # Logged, not swallowed: a page that has quietly stopped ranking looks
    # exactly like a board nobody has rated.
    assert any("project ratings" in line for line in logged)
