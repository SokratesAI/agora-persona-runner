"""issue #227: a project's goal is settled in a conversation of its own.

The owner, 2026-09-13 21:02, deleting the approval gate: *"Goals, milestones,
kpis and okrs should be derived based on a conversation between you and me
where we challenge each other and then agree on something."* These pin the
two halves that can be wrong silently -- the thread that gets opened, and the
link written back onto the objective.
"""

import pytest

from agora_runner import project_goal_thread as pgt


GOALS = """\
---
type: board
---

# Project goals

## Nova

```objective
statement: the loop ships work he did not have to ask for twice
status: discussing
conversation:
```

```key-result
id: nova-kr-one
name: something
measure: a number
```

## Marcus

```objective
statement: he logs a session without thinking about it
status: discussing
```

```kpi
id: marcus-kpi-one
name: health
measure: a number
low: 1
high: 2
```
"""


class FakeAgora:
    def __init__(self, existing=(), create_status=None, notify_status=201):
        self.existing = set(existing)
        self.create_status = create_status
        self.notify_status = notify_status
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/conversations":
            if self.create_status is not None:
                return self.create_status, {}
            name = payload["name"]
            status = 200 if name in self.existing else 201
            self.existing.add(name)
            return status, {"conversation": {"id": "conv-9", "name": name}}
        if path.endswith("/notify"):
            return self.notify_status, {"message": {"id": "msg-1"}}
        if method == "PATCH":
            return 200, {}
        raise AssertionError(f"unexpected call {method} {path}")

    def notified(self):
        return [c for c in self.calls if c[1].endswith("/notify")]


@pytest.fixture
def written(monkeypatch):
    """Captures the vault write instead of performing it."""
    calls = []
    monkeypatch.setattr(pgt, "vault_write_path",
                        lambda path, content, if_rev=None: calls.append((path, content, if_rev)))
    return calls


def test_name_is_the_project_so_it_dedupes():
    assert pgt.conversation_name("  Sokrates Docs ") == "Sokrates Docs — goals"


def test_replaces_an_empty_conversation_field_in_the_right_section():
    out, problem = pgt.set_objective_conversation(GOALS, "nova", "conv-9")
    assert problem is None
    nova, marcus = out.split("## Marcus")
    assert "conversation: conv-9" in nova
    # Marcus has no field of its own and must not have gained one.
    assert "conv-9" not in marcus


def test_adds_the_field_when_the_fence_has_none():
    out, problem = pgt.set_objective_conversation(GOALS, "Marcus", "conv-9")
    assert problem is None
    fence = out.split("## Marcus")[1].split("```")[1]
    assert "conversation: conv-9" in fence
    # Added inside the objective fence, not after the closing backticks.
    assert "status: discussing\nconversation: conv-9\n" in out


def test_only_the_objective_fence_is_touched():
    out, _ = pgt.set_objective_conversation(GOALS, "Nova", "conv-9")
    assert out.count("conversation: conv-9") == 1
    assert "id: nova-kr-one" in out and "id: marcus-kpi-one" in out


def test_refuses_a_project_with_no_section():
    out, problem = pgt.set_objective_conversation(GOALS, "Agora", "conv-9")
    assert out == GOALS
    assert "no `## Agora` section" in problem


def test_refuses_a_section_with_no_objective():
    goals = "# Project goals\n\n## Agora\n\n```kpi\nid: a\n```\n"
    out, problem = pgt.set_objective_conversation(goals, "Agora", "conv-9")
    assert out == goals
    assert "no ```objective fence" in problem


def test_refuses_an_objective_that_already_links_a_thread():
    first, _ = pgt.set_objective_conversation(GOALS, "Nova", "conv-1")
    out, problem = pgt.set_objective_conversation(first, "Nova", "conv-2")
    assert out == first
    assert "conv-1" in problem and "one thread, not two" in problem


def test_open_thread_posts_and_records(monkeypatch, written):
    fake = FakeAgora()
    monkeypatch.setattr(pgt, "agora_internal", fake)
    ok, info = pgt.open_thread("Nova", "what I think and what I would measure",
                               cycle=1532, markdown=GOALS, rev="7-abc")
    assert ok, info
    assert info == {"conversationId": "conv-9", "name": "Nova — goals",
                    "adopted": False}
    assert len(fake.notified()) == 1
    assert "what I would measure" in fake.notified()[0][2]["text"]
    assert fake.notified()[0][2]["sender"] == "Nova"
    path, content, if_rev = written[0]
    assert path == pgt.PROJECT_GOALS_PATH
    assert "conversation: conv-9" in content
    assert if_rev == "7-abc"


def test_an_existing_thread_is_adopted_without_a_second_opening_message(monkeypatch, written):
    fake = FakeAgora(existing={"Nova — goals"})
    monkeypatch.setattr(pgt, "agora_internal", fake)
    ok, info = pgt.open_thread("Nova", "body", markdown=GOALS, rev="7-abc")
    assert ok and info["adopted"] is True
    assert fake.notified() == []
    assert "conversation: conv-9" in written[0][1]


def test_nothing_is_written_when_the_document_refuses_the_link(monkeypatch, written):
    fake = FakeAgora()
    monkeypatch.setattr(pgt, "agora_internal", fake)
    ok, problem = pgt.open_thread("Agora", "body", markdown=GOALS, rev="7-abc")
    assert not ok
    assert "no `## Agora` section" in problem
    assert written == []
    # The refusal is read off the document BEFORE anything is created: a
    # thread opened for a project the write then rejects is an empty
    # conversation on his phone about a goal that has nowhere to live.
    assert fake.calls == []


def test_a_failed_write_says_the_thread_survives(monkeypatch):
    fake = FakeAgora()
    monkeypatch.setattr(pgt, "agora_internal", fake)

    def boom(path, content, if_rev=None):
        raise RuntimeError("409")

    monkeypatch.setattr(pgt, "vault_write_path", boom)
    ok, problem = pgt.open_thread("Nova", "body", markdown=GOALS, rev="7-abc")
    assert not ok
    assert "conv-9" in problem and "run again to record it" in problem


def test_cli_refuses_an_empty_message(capsys):
    assert pgt.main(["--project", "Nova", "--message", "   "]) == 2
    assert "opening message is required" in capsys.readouterr().out


def test_cli_dry_run_posts_nothing(monkeypatch, capsys):
    def explode(*a, **k):
        raise AssertionError("dry run must not reach Agora")

    monkeypatch.setattr(pgt, "agora_internal", explode)
    assert pgt.main(["--project", "Nova", "--message", "the body",
                     "--cycle", "1532", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "Nova — goals" in out and "the body" in out and "cycle 1532" in out


def test_cli_refuses_a_project_that_already_has_a_thread(monkeypatch, capsys):
    linked, _ = pgt.set_objective_conversation(GOALS, "Nova", "conv-1")
    monkeypatch.setattr(pgt, "vault_read_path_rev", lambda path: (linked, "7-abc"))
    monkeypatch.setattr(pgt, "agora_internal",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
    assert pgt.main(["--project", "Nova", "--message", "body"]) == 3
    assert "already links conversation conv-1" in capsys.readouterr().out
