"""Rating a project, which is the last line of his 2026-09-01 capture.

*"Each project should also be able to be assigned a priority, making one
project and its tasks more important than others."*

The set of projects is derived from the `Project` cells on his two boards
and deliberately has no second list -- so a project-level rating, which
belongs to no single row, is the first thing about a project that needs a
document of its own. These tests cover the read side: parsing the
ratings and the ordering they buy. The write routes came out with issue
#229 -- projects stop carrying a priority set from the app.
"""

import agora_runner.nova_capture as nova_capture
from agora_runner.nova_boards import (
    parse_project_meta,
    rank_projects,
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
