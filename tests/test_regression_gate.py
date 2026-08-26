"""Every check in the corpus is re-run against the failure it claims to catch.

A gate whose checks have only ever been seen passing is not a gate, and
`prompt.md` is explicit about why: *"a test whose negative result was
guaranteed in advance is not evidence"*. So each test below puts the code
back into the state it was in when the incident happened -- the guard
removed, the author defaulted, the sweep narrowed -- and asserts the gate
turns red. A check that stays green through its own incident is deleted
from the corpus, not kept for the coverage.

The second half of most tests is the mirror: a check must not pass by
refusing everything, so where the gate asserts a good input is accepted,
there is a test that breaks *that* half and expects red too.
"""

import pytest

from tools import regression_gate as gate


def _verdict(slug, corpus=None):
    entry = next(r for r in (corpus or gate.CORPUS) if r.slug == slug)
    rows, status = gate.run([entry])
    return rows[0][1], rows[0][2], status


# --- the metered-provider guard (cycle 78) ---------------------------------

def test_removing_the_metered_guard_turns_it_red(monkeypatch):
    from agora_runner import reply as reply_mod
    monkeypatch.setattr(reply_mod, "METERED_PROVIDERS", ())
    monkeypatch.setattr(reply_mod, "anthropic_generate", lambda *a, **k: None, raising=False)
    verdict, detail, status = _verdict("unattended-metered-provider")
    assert verdict == "REGRESSED", detail
    assert status == 2


def test_a_guard_that_blocks_every_provider_is_not_a_pass(monkeypatch):
    from agora_runner import reply as reply_mod
    monkeypatch.setattr(reply_mod, "METERED_PROVIDERS", ("anthropic", "claude-cli"))
    verdict, detail, _ = _verdict("unattended-metered-provider")
    assert verdict == "REGRESSED"
    assert "subscription provider" in detail


def test_the_metered_guard_holds_today():
    assert _verdict("unattended-metered-provider")[0] == "held"


# --- the board comment's author (cycle 479) --------------------------------

def test_defaulting_the_author_to_the_owner_turns_it_red(monkeypatch):
    from agora_runner import nova_site
    original = nova_site.NovaSiteHandler._post_board_comment

    def defaults_the_author(self, payload):
        payload.setdefault("author", "Edvard")
        return original(self, payload)

    monkeypatch.setattr(nova_site.NovaSiteHandler, "_post_board_comment", defaults_the_author)
    verdict, detail, status = _verdict("board-comment-with-no-author")
    assert verdict == "REGRESSED", detail
    assert status == 2


def test_a_route_that_refuses_every_author_is_not_a_pass(monkeypatch):
    from agora_runner import nova_site
    original = nova_site.NovaSiteHandler._post_board_comment

    def refuses_everything(self, payload):
        return original(self, dict(payload, author="nobody"))

    monkeypatch.setattr(nova_site.NovaSiteHandler, "_post_board_comment", refuses_everything)
    verdict, detail, _ = _verdict("board-comment-with-no-author")
    assert verdict == "REGRESSED"
    assert "never reached the writer" in detail


def test_the_author_check_holds_today():
    assert _verdict("board-comment-with-no-author")[0] == "held"


# --- the advisory sweep's reach (cycle 432) --------------------------------

def test_narrowing_the_sweep_back_to_the_checkouts_turns_it_red(monkeypatch):
    from tools import security_alerts

    def checkouts_only(run=None):
        repos, unplaceable = security_alerts._repos_from_workspace()
        return sorted(repos), unplaceable, [], False

    monkeypatch.setattr(security_alerts, "_repos_to_sweep", checkouts_only)
    verdict, detail, status = _verdict("security-sweep-covered-only-the-checkouts")
    assert verdict == "REGRESSED", detail
    assert "Cycle 432" in detail
    assert status == 2


def test_a_sweep_that_calls_an_unlistable_org_complete_turns_it_red(monkeypatch):
    from tools import security_alerts
    real = security_alerts._repos_to_sweep

    def never_incomplete(run=None):
        repos, unplaceable, notes, _ = real(run=run)
        return repos, unplaceable, notes, False

    monkeypatch.setattr(security_alerts, "_repos_to_sweep", never_incomplete)
    verdict, detail, _ = _verdict("security-sweep-covered-only-the-checkouts")
    assert verdict == "REGRESSED"
    assert "reported as a complete sweep" in detail


def test_the_org_sweep_holds_today():
    assert _verdict("security-sweep-covered-only-the-checkouts")[0] == "held"


# --- an ask that opens with a statement (cycle 273) ------------------------

def test_accepting_an_ask_with_no_question_turns_it_red(monkeypatch):
    from tools import lint_entry
    monkeypatch.setattr(lint_entry, "_ask_question_finding", lambda body: None)
    verdict, detail, status = _verdict("an-ask-that-opens-with-a-statement")
    assert verdict == "REGRESSED", detail
    assert status == 2


def test_a_lint_that_refuses_every_ask_is_not_a_pass(monkeypatch):
    from tools import lint_entry
    monkeypatch.setattr(lint_entry, "_ask_question_finding", lambda body: "no")
    verdict, detail, _ = _verdict("an-ask-that-opens-with-a-statement")
    assert verdict == "REGRESSED"
    assert "refuses everything" in detail


def test_the_ask_lint_holds_today():
    assert _verdict("an-ask-that-opens-with-a-statement")[0] == "held"


# --- the two textual checks, and the vault they read -----------------------

def test_narrowing_the_pod_sweep_to_one_namespace_turns_it_red(monkeypatch):
    monkeypatch.setattr(gate, "read_vault", lambda path: "run `kubectl get pods -n agents`\n")
    verdict, detail, status = _verdict("pod-sweep-narrowed-to-one-namespace")
    assert verdict == "REGRESSED", detail
    assert "37 hours" in detail
    assert status == 2


def test_hardcoding_the_shared_checkout_turns_it_red(monkeypatch):
    monkeypatch.setattr(
        gate, "read_vault",
        lambda path: "```bash\ncd /data/workspace/agora-persona-runner\n```\n",
    )
    verdict, detail, status = _verdict("hardcoded-shared-checkout")
    assert verdict == "REGRESSED", detail
    assert status == 2


def test_the_workspace_variable_is_accepted(monkeypatch):
    monkeypatch.setattr(
        gate, "read_vault",
        lambda path: "```bash\ncd ${NOVA_WORKSPACE:-/data/workspace}/agora-persona-runner\n```\n",
    )
    assert _verdict("hardcoded-shared-checkout")[0] == "held"


def test_a_vault_that_cannot_be_read_is_never_clean(monkeypatch):
    def refuses(path):
        raise gate.Unreadable("no vault client here")

    monkeypatch.setattr(gate, "read_vault", refuses)
    verdict, detail, status = _verdict("pod-sweep-narrowed-to-one-namespace")
    assert verdict == "UNREADABLE"
    assert status == 1


def test_a_check_that_crashes_is_never_clean():
    entry = gate.Regression(
        slug="crashes", cycle="0", date="x", surface="drove the code",
        failure="f", check=lambda: 1 / 0,
    )
    rows, status = gate.run([entry])
    assert rows[0][1] == "UNREADABLE"
    assert status == 1


# --- the corpus itself -----------------------------------------------------

def test_every_entry_cites_a_cycle_and_says_what_went_wrong():
    for item in gate.CORPUS:
        assert item.cycle and item.date, item.slug
        assert len(item.failure) > 40, item.slug
        assert item.surface in ("drove the code", "read the text"), item.slug


def test_slugs_are_unique():
    slugs = [r.slug for r in gate.CORPUS]
    assert len(slugs) == len(set(slugs))


def test_a_red_row_prints_both_the_incident_and_what_it_sees_now():
    entry = gate.CORPUS[0]
    rows = [(entry, "REGRESSED", "the guard is gone")]
    report = gate.format_report(rows, 2)
    assert entry.failure in report
    assert "the guard is gone" in report
    assert "Do not merge over this." in report
