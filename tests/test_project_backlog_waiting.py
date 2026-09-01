"""The project page's "What's next" list ranks a question of his first.

`_project_backlog` orders a project's open rows with `nova_next.rank` --
the same function `tools.top_board_rows` uses when a cycle picks its
work -- explicitly so the page does not tell him one order while I work
another. `rank`'s strongest raise is a row where his comment is the last
word in the thread, and it reads that flag off the row with `.get`, so a
payload that never set it removed the raise in silence: the page put a
question of his wherever its rating happened to fall.

So there are two halves to hold here, and they fail differently. The
payload has to *carry* the flag, computed by the one function that
already decides what "waiting" means rather than by a second rule read
off the rendered thread. And the list has to *use* it -- a Low row he is
waiting on an answer to above an Immediately row nobody has asked
anything about, which is the ordering that would otherwise look like a
bug rather than the point.

A comment Sokrates relayed on his behalf carries `waiting` and does not
raise, per his own ask on 2026-08-29. That is pinned here too, because
carrying `waiting` without `relayed` beside it would raise exactly the
comments this loop was told not to raise.
"""

from agora_runner import nova_site


BOARD = """---
type: board
---

## Board

| # | Item | Status | Updated | Priority | Project |
|---|------|--------|---------|---|---|
| [[#1 — He asked\\|1]] | He asked | 🟡 In progress | 08-28 | ⚪ Low | Nova |
| [[#2 — I answered\\|2]] | I answered | 🟡 In progress | 08-28 | 🔴 Immediately | Nova |
| [[#3 — Relayed\\|3]] | Relayed | 🟡 In progress | 08-28 | ⚪ Low | Nova |

# Details

## 1 — He asked

The write-up.

**Edvard, 08-28:** so what happened to this one?

## 2 — I answered

The write-up.

**Edvard, 08-28:** and this one?

**Nova, 08-28 (Cycle 700):** it merged this morning.

## 3 — Relayed

The write-up.

**Edvard, 08-28:** Sokrates here, posting on Edvard's behalf and not Edvard typing this himself: he would like this looked at.
"""


def _payload(monkeypatch):
    monkeypatch.setattr(nova_site, "board_markdown", lambda name: (BOARD, "", ""))
    return nova_site.board_payload("issues")


def test_board_payload_marks_the_row_he_is_waiting_on(monkeypatch):
    rows = {item["number"]: item for item in _payload(monkeypatch)["items"]}
    assert rows[1].get("waiting") is True
    assert rows[1].get("relayed") is False


def test_board_payload_leaves_an_answered_row_unmarked(monkeypatch):
    # No key at all rather than `False`: `rank` reads it with `.get`, and a
    # row nobody has ever commented on has to look the same as one whose
    # thread I have already answered.
    rows = {item["number"]: item for item in _payload(monkeypatch)["items"]}
    assert "waiting" not in rows[2]


def test_board_payload_marks_a_relayed_comment_as_relayed(monkeypatch):
    rows = {item["number"]: item for item in _payload(monkeypatch)["items"]}
    assert rows[3].get("waiting") is True
    assert rows[3].get("relayed") is True


def _backlog(monkeypatch, items):
    monkeypatch.setattr(
        nova_site, "board_payload",
        lambda name: {"items": items if name == "issues" else []},
    )
    monkeypatch.setattr(
        nova_site, "cached_payload",
        lambda name, build: (build(), b"", "etag"),
    )
    monkeypatch.setattr(nova_site, "comments_markdown", lambda: "")
    monkeypatch.setattr(nova_site, "project_priorities", dict)
    return nova_site.project_payload("Nova")["backlog"]


def _row(number, priority_key, **extra):
    row = {
        "number": number,
        "title": "Row " + str(number),
        "status": "🟡 In progress",
        "statusKey": "in-progress",
        "updated": "08-28",
        "where": "",
        "priority": priority_key,
        "priorityKey": priority_key,
        "project": "Nova",
        "done": False,
    }
    row.update(extra)
    return row


def test_a_question_of_his_outranks_a_red_row_nobody_asked_about(monkeypatch):
    backlog = _backlog(monkeypatch, [
        _row(2, "immediate"),
        _row(1, "low", waiting=True, relayed=False),
    ])
    assert [row["number"] for row in backlog] == [1, 2]


def test_a_relayed_comment_does_not_jump_the_queue(monkeypatch):
    backlog = _backlog(monkeypatch, [
        _row(2, "immediate"),
        _row(3, "low", waiting=True, relayed=True),
    ])
    assert [row["number"] for row in backlog] == [2, 3]
