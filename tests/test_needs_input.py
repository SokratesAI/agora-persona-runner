"""issues.md #209: a cycle asks the owner in a conversation, not in the digest."""

import pytest

from agora_runner import http_util, needs_input


class FakeAgora:
    """Records every internal call and answers the way Agora does.

    `existing` is the set of conversation names already in the store, which is
    what makes `POST /conversations` answer 200 instead of 201 -- the whole
    de-duplication mechanism runs through that one status code.
    """

    def __init__(self, existing=(), create_status=None, notify_status=201,
                 notify_body=None, tag_status=200):
        self.existing = set(existing)
        self.create_status = create_status
        self.notify_status = notify_status
        self.notify_body = notify_body
        self.tag_status = tag_status
        self.calls = []

    def __call__(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if path == "/conversations":
            if self.create_status is not None:
                return self.create_status, {}
            name = payload["name"]
            status = 200 if name in self.existing else 201
            self.existing.add(name)
            return status, {"conversation": {"id": "conv-1", "name": name}}
        if path.endswith("/notify"):
            body = self.notify_body
            if body is None:
                # What Agora answers when the phone actually buzzed. The
                # `status` field is what `push_held` reads, so a fake that
                # omits it makes every ask look silently withheld.
                body = {"status": "sent", "message": {"id": "msg-1"}}
            return self.notify_status, body
        if method == "PATCH":
            return self.tag_status, {}
        raise AssertionError(f"unexpected call {method} {path}")

    def notified(self):
        return [c for c in self.calls if c[1].endswith("/notify")]


@pytest.fixture
def agora(monkeypatch):
    fake = FakeAgora()
    monkeypatch.setattr(needs_input, "agora_internal", fake)
    return fake


# -- the ask has to open with the question ---------------------------------

@pytest.mark.parametrize("question", [
    "I have been looking at the board and I think we should drop priorities.",
    "",
    "   ",
    None,
])
def test_a_question_that_is_not_a_question_is_refused(question):
    assert needs_input.validate(question, "some context") is not None


def test_a_real_question_passes():
    assert needs_input.validate("Yes or no, keep the symbols?", "why") is None


def test_a_question_mark_after_a_trailing_newline_still_counts():
    assert needs_input.validate("Which of these three?\n", "why") is None


def test_context_is_required():
    assert needs_input.validate("Yes or no?", "  ") is not None


def test_validation_failure_posts_nothing(agora):
    ok, problem = needs_input.ask("not a question", "context", cycle=1)
    assert ok is False
    assert isinstance(problem, str)
    assert agora.calls == []


# -- the name is the de-duplication key ------------------------------------

def test_the_name_is_derived_from_the_question():
    name = needs_input.conversation_name("Yes or no, keep the symbols?")
    assert name == needs_input.NAME_PREFIX + "Yes or no, keep the symbols?"


def test_the_same_question_asked_twice_gives_the_same_name():
    a = needs_input.conversation_name("Yes or no,  keep  the symbols?")
    b = needs_input.conversation_name("Yes or no, keep the symbols?\n")
    assert a == b


def test_a_long_question_is_cut_in_the_name_only(agora):
    question = "Which of these " + ("very long option " * 20) + "should I take?"
    name = needs_input.conversation_name(question)
    assert len(name) <= len(needs_input.NAME_PREFIX) + needs_input.MAX_NAME_QUESTION_CHARS
    ok, info = needs_input.ask(question, "context", cycle=7)
    assert ok
    # The cut is cosmetic: the question itself reaches him whole.
    assert " ".join(question.split()) in agora.notified()[0][2]["text"]


# -- a second cycle asking the same thing ----------------------------------

def test_a_new_question_opens_a_thread_and_tags_it(agora):
    ok, info = needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    assert ok
    assert info["repeat"] is False
    tagged = [c for c in agora.calls if c[0] == "PATCH"]
    assert tagged and tagged[0][2] == {"tags": [needs_input.NEEDS_INPUT_TAG]}


def test_the_same_question_from_a_second_cycle_lands_in_the_same_thread(agora):
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    ok, info = needs_input.ask("Yes or no, ship it?", "because X again", cycle=1444)
    assert ok
    assert info["repeat"] is True
    assert info["conversationId"] == "conv-1"
    created = [c for c in agora.calls if c[1] == "/conversations"]
    assert len(created) == 2
    assert len(agora.notified()) == 2


def test_a_repeat_does_not_retag_the_thread(agora):
    """He can mute a thread from the Settings drawer, and mute is a tag."""
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    before = len([c for c in agora.calls if c[0] == "PATCH"])
    needs_input.ask("Yes or no, ship it?", "still stuck", cycle=1444)
    assert len([c for c in agora.calls if c[0] == "PATCH"]) == before


def test_a_repeat_says_it_is_a_repeat(agora):
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1444)
    first, second = [c[2]["text"] for c in agora.notified()]
    assert "still waiting" not in first
    assert "still waiting" in second
    assert "Cycle 1444" in second


# -- the message itself ----------------------------------------------------

def test_the_question_is_the_first_line_of_the_message():
    body = needs_input.opening_message("Yes or no, ship it?", "a long reason", cycle=9)
    assert body.splitlines()[0] == "**Yes or no, ship it?**"
    assert "a long reason" in body
    assert "cycle 9" in body


def test_the_message_is_posted_as_nova_not_as_the_owner(agora):
    """Posting under his own name would have the curator answer my own question,
    and would put words in his mouth in his own transcript."""
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    assert agora.notified()[0][2]["sender"] == "Nova"


def test_the_push_is_not_suppressed(agora):
    """The buzz on his phone is the entire reason this exists."""
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    assert agora.notified()[0][2].get("push") is not False


def test_the_thread_is_curated_by_the_subscription_persona(agora):
    """`claude-cli:`, not `anthropic:` -- identity.md rule 9."""
    needs_input.ask("Yes or no, ship it?", "because X", cycle=1443)
    created = [c for c in agora.calls if c[1] == "/conversations"][0]
    assert created[2]["personaId"] == needs_input.ANSWER_PERSONA_ID


# -- failures a cycle has to be able to report -----------------------------

def test_a_failed_create_is_reported_not_swallowed(monkeypatch):
    fake = FakeAgora(create_status=503)
    monkeypatch.setattr(needs_input, "agora_internal", fake)
    ok, problem = needs_input.ask("Yes or no?", "context", cycle=1)
    assert ok is False
    assert "503" in problem
    assert fake.notified() == []


def test_a_notify_that_records_no_message_is_a_failure(monkeypatch):
    """`agora_internal` answers (status, body); reading the envelope as an id
    made an earlier module treat every answer as a success."""
    fake = FakeAgora(notify_status=200, notify_body={"status": "recorded"})
    monkeypatch.setattr(needs_input, "agora_internal", fake)
    ok, problem = needs_input.ask("Yes or no?", "context", cycle=1)
    assert ok is False
    assert "could not post" in problem


def test_a_create_with_no_id_is_a_failure(monkeypatch):
    def no_id(method, path, payload=None):
        return 201, {"conversation": {}}
    monkeypatch.setattr(needs_input, "agora_internal", no_id)
    ok, problem = needs_input.ask("Yes or no?", "context", cycle=1)
    assert ok is False


# -- the CLI ---------------------------------------------------------------

def test_dry_run_posts_nothing(agora, capsys):
    code = needs_input.main(["--question", "Yes or no, ship it?",
                             "--context", "because X", "--cycle", "1443",
                             "--dry-run"])
    assert code == 0
    assert agora.calls == []
    out = capsys.readouterr().out
    assert needs_input.NAME_PREFIX in out
    assert "because X" in out


def test_cli_refuses_a_statement(agora, capsys):
    code = needs_input.main(["--question", "We should drop priorities.",
                             "--context", "because X"])
    assert code == 2
    assert agora.calls == []


def test_cli_reports_a_transport_failure(monkeypatch, capsys):
    monkeypatch.setattr(needs_input, "agora_internal", FakeAgora(create_status=500))
    code = needs_input.main(["--question", "Yes or no?", "--context", "X"])
    assert code == 1


def test_cli_posts_and_names_the_thread(agora, capsys):
    code = needs_input.main(["--question", "Yes or no, ship it?",
                             "--context", "because X", "--cycle", "1443"])
    assert code == 0
    assert len(agora.notified()) == 1
    assert "conv-1" in capsys.readouterr().out


# --- did his phone actually buzz? ---------------------------------------
# His capture, 2026-09-15: "Never got a notification for the ask thread from
# cycle 1617 ... my silence is a symptom of them not reaching me, not me
# ignoring them." Agora appends the message and withholds the push during
# quiet hours; nothing here read that back, so an ask nobody was told about
# reported exactly like one that rang.

def test_a_sent_push_is_not_reported_as_held():
    assert needs_input.push_held({"status": "sent", "message": {"id": "m"}}) is None


def test_quiet_hours_is_reported_as_held():
    held = needs_input.push_held({"status": "recorded", "quietHours": True})
    assert held is not None and "quiet hours" in held


def test_a_muted_thread_is_reported_as_held():
    assert "nova:mute" in needs_input.push_held(
        {"status": "recorded", "muted": True})


def test_a_watched_thread_is_reported_as_held():
    assert "on screen" in needs_input.push_held(
        {"status": "recorded", "watching": True})


def test_a_flag_that_is_not_true_does_not_count_as_held():
    """`recorded` with every flag false is a shape Agora does not send today;
    it must not read as a delivered push either, because only `sent` says so."""
    assert needs_input.push_held(
        {"status": "sent", "quietHours": False}) is None


def test_an_unrecognised_response_is_not_taken_as_delivered():
    assert needs_input.push_held({"status": "recorded"}) is not None
    assert needs_input.push_held({}) is not None
    assert needs_input.push_held(None) is not None


def test_ask_reports_the_held_push(monkeypatch):
    def fake(method, path, payload=None):
        if path == "/conversations":
            return 201, {"conversation": {"id": "c1"}}
        if path.endswith("/notify"):
            return 200, {"status": "recorded", "quietHours": True,
                         "message": {"id": "m1"}}
        return 200, {}

    monkeypatch.setattr(needs_input, "agora_internal", fake)
    ok, info = needs_input.ask("Yes or no, is this a thing?", "because")
    assert ok is True
    assert info["pushed"] is False
    assert "quiet hours" in info["pushHeld"]


def test_ask_reports_a_delivered_push(monkeypatch):
    def fake(method, path, payload=None):
        if path == "/conversations":
            return 201, {"conversation": {"id": "c1"}}
        if path.endswith("/notify"):
            return 200, {"status": "sent", "message": {"id": "m1"}}
        return 200, {}

    monkeypatch.setattr(needs_input, "agora_internal", fake)
    ok, info = needs_input.ask("Yes or no, is this a thing?", "because")
    assert info["pushed"] is True and info["pushHeld"] is None


def test_main_exits_nonzero_when_his_phone_did_not_buzz(monkeypatch, capsys):
    monkeypatch.setattr(needs_input, "ask", lambda *a, **k: (True, {
        "conversationId": "c1", "name": "Nova needs you — x?", "repeat": False,
        "messageId": "m1", "pushed": False, "pushHeld": "quiet hours"}))
    code = needs_input.main(["--question", "Yes or no?", "--context", "why"])
    assert code == 3
    assert "HIS PHONE DID NOT BUZZ" in capsys.readouterr().out


def test_main_exits_zero_when_it_did(monkeypatch, capsys):
    monkeypatch.setattr(needs_input, "ask", lambda *a, **k: (True, {
        "conversationId": "c1", "name": "Nova needs you — x?", "repeat": False,
        "messageId": "m1", "pushed": True, "pushHeld": None}))
    code = needs_input.main(["--question", "Yes or no?", "--context", "why"])
    assert code == 0
    assert "HIS PHONE DID NOT BUZZ" not in capsys.readouterr().out


def test_a_401_opening_the_thread_names_the_pod(monkeypatch):
    """An ask that cannot be opened is the one failure a cycle must not
    misread: `prompt.md` sends it to the runner pod, and a bare HTTP 401 from
    the bridge pod reads as Agora refusing rather than as the wrong shell."""
    monkeypatch.setattr(needs_input, "agora_internal", lambda *a, **k: (401, {}))
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "")
    ok, detail = needs_input.ask(
        "Should the goal threads stay batched?",
        "Long enough context for the validator to accept this as a real ask, "
        "written the way a cycle would write it when it genuinely cannot proceed.")
    assert ok is False
    assert "HTTP 401" in detail
    assert "no AGORA_TOKEN" in detail
