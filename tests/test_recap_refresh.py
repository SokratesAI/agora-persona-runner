"""The recap refresher: when it fires, what it sends, and what it never does.

His `issues.md` #219 -- regenerate the twelve-hour card when the journal
changes instead of leaving each cycle to judge whether it is stale. The
tests that matter here are the negative ones: a refresher that can blank a
real summary, or spend a bridge call on an unchanged journal, is worse than
the stale card it replaces.
"""

import datetime

import pytest

from agora_runner import recap_refresh
from agora_runner.nova_recap import parse_recap, render


OSLO = recap_refresh.OSLO
NOW = datetime.datetime(2026, 9, 13, 14, 0, tzinfo=OSLO)
JD = recap_refresh.JOURNAL_DIR


def _entry(cycle, when, footer="PR: #1 | Outcome: merged"):
    return f"### Cycle {cycle} — something ({when})\n\nprose\n\n---\n{footer}\n"


def test_newest_entry_sorts_by_sequence_not_text():
    # `1000-` sorts before `999-` as text, which is what made the card read
    # the newest entries it could see and see none of them.
    names = [JD + "999-cycle-932.md", JD + "1000-cycle-933.md", JD + "100-cycle-90.md"]
    assert recap_refresh.newest_entry(names) == "1000-cycle-933.md"


def test_newest_entry_is_empty_when_the_folder_is():
    assert recap_refresh.newest_entry([]) == ""


def test_window_stops_at_the_cutoff():
    bodies = {
        JD + "3-cycle-3.md": _entry(3, "2026-09-13 13:40"),
        JD + "2-cycle-2.md": _entry(2, "2026-09-13 09:00"),
        JD + "1-cycle-1.md": _entry(1, "2026-09-12 20:00"),
    }
    rows = recap_refresh.window(list(bodies), NOW, read=bodies.get)
    assert [r["file"] for r in rows] == ["3-cycle-3.md", "2-cycle-2.md"]
    assert rows[0]["footer"] == "PR: #1 | Outcome: merged"
    assert "Cycle 3" in rows[0]["title"]


def test_window_carries_the_opening_prose():
    # Titles alone produced bullets like "Merged 18+ improvements to Marcus"
    # -- the counting he cannot act on. The first paragraph is where an entry
    # says what actually changed, so it has to reach the model.
    body = ("### Cycle 3 — a title (2026-09-13 13:40)\n\n"
            "> a quote he did not write\n\n"
            "The board file stopped syncing and is published again.\n"
            "Second line of the same paragraph.\n\n"
            "A later paragraph that is not the lead.\n\n---\nPR: none\n")
    rows = recap_refresh.window([JD + "3-cycle-3.md"], NOW, read=lambda p: body)
    assert rows[0]["lead"] == ("The board file stopped syncing and is published "
                               "again. Second line of the same paragraph.")
    assert rows[0]["lead"] in recap_refresh.prompt_for(rows)


def test_opening_prose_is_capped():
    body = "### t\n\n" + "x" * 2000
    assert len(recap_refresh.opening_prose(body)) == recap_refresh.LEAD_CHARS


def test_opening_prose_is_empty_when_there_is_none():
    assert recap_refresh.opening_prose("### t\n\n---\nPR: none\n") == ""


def test_cycle_range_spans_the_window():
    rows = [{"file": "3-cycle-1507.md"}, {"file": "2-cycle-1502.md"}]
    assert recap_refresh.cycle_range(rows) == "1502-1507"
    assert recap_refresh.cycle_range([{"file": "3-cycle-9.md"}]) == "9"
    assert recap_refresh.cycle_range([{"file": "3-monday-research.md"}]) == ""


def test_bullets_from_strips_markers_and_keeps_order():
    assert recap_refresh.bullets_from("- one\n* two\n\n- three\n") == ["one", "two", "three"]


def test_bullets_from_rejects_prose():
    # A model that answers in a paragraph has not written a card, and the
    # caller's "" means keep the old one.
    assert recap_refresh.bullets_from("Here is a summary of the last 12 hours.") == []


def test_bullets_from_refuses_more_than_he_asked_for():
    assert recap_refresh.bullets_from("\n".join(f"- {i}" for i in range(7))) == []
    assert len(recap_refresh.bullets_from("\n".join(f"- {i}" for i in range(6)))) == 6


def test_render_stamps_the_journal_entry_and_parse_reads_it_back():
    text = render(["one"], NOW, cycles="1500-1507", journal="1571-cycle-1507.md")
    assert "| cycles 1500-1507 | journal 1571-cycle-1507.md" in text
    payload = parse_recap(text, now=NOW)
    assert payload["journal"] == "1571-cycle-1507.md"
    assert payload["cycles"] == "1500-1507"


def _stamp(text):
    return text.split("<!-- generated:")[1].split("-->")[0]


def test_render_omits_a_field_it_was_not_given():
    assert "journal" not in _stamp(render(["one"], NOW, cycles="9"))
    assert "|" not in _stamp(render(["one"], NOW))


def test_an_old_stamp_with_only_cycles_still_parses():
    # Every card written before #219 carries `| cycles 871-901` and nothing
    # else. It must keep rendering; `journal` reads as unknown.
    payload = parse_recap(
        "<!-- generated: 2026-09-04T11:00+02:00 | cycles 871-901 -->\n- one\n", now=NOW)
    assert payload["cycles"] == "871-901"
    assert payload["journal"] == ""
    assert payload["bullets"]


class _Vault:
    """The three vault calls `refresh_once` makes, recorded."""

    def __init__(self, existing, names, result="written"):
        self.existing = existing
        self.names = names
        self.result = result
        self.writes = []

    def install(self, monkeypatch, bodies=None, answer=None, calls=None):
        bodies = bodies or {}
        monkeypatch.setattr(recap_refresh, "vault_list_ids", lambda prefix: self.names)
        monkeypatch.setattr(recap_refresh, "vault_read_path_rev",
                            lambda path: (self.existing, "3-abc"))
        monkeypatch.setattr(recap_refresh, "vault_read_path", bodies.get)
        monkeypatch.setattr(recap_refresh, "window",
                            lambda names, now, **kw: [{"file": "3-cycle-3.md",
                                                       "title": "t", "footer": ""}])

        def _write(path, content, if_rev=None, **kw):
            self.writes.append((path, content, if_rev))
            return self.result

        monkeypatch.setattr(recap_refresh, "vault_write_path", _write)

        def _ask(rows, **kw):
            (calls if calls is not None else []).append(rows)
            return answer if answer is not None else ["a bullet"]

        monkeypatch.setattr(recap_refresh, "ask_haiku", _ask)


def test_an_unchanged_journal_costs_nothing(monkeypatch):
    # The whole point of stamping the entry: a poll that finds the same
    # newest entry must not spend a bridge call or a write.
    card = render(["old"], NOW, journal="3-cycle-3.md")
    calls = []
    _Vault(card, [JD + "3-cycle-3.md"]).install(monkeypatch, calls=calls)
    assert recap_refresh.refresh_once(now=NOW) is False
    assert calls == []


def test_a_new_entry_rewrites_the_card(monkeypatch):
    card = render(["old"], NOW, journal="3-cycle-3.md")
    vault = _Vault(card, [JD + "3-cycle-3.md", JD + "4-cycle-4.md"])
    vault.install(monkeypatch, answer=["the new bullet"])
    assert recap_refresh.refresh_once(now=NOW) is True
    path, content, if_rev = vault.writes[0]
    assert path == recap_refresh.RECAP_PATH
    assert "- the new bullet" in content
    assert "journal 4-cycle-4.md" in content
    # Conditional on the revision it read at: a second writer wins rather
    # than being silently overwritten.
    assert if_rev == "3-abc"


def test_a_card_with_no_journal_stamp_is_regenerated(monkeypatch):
    vault = _Vault("<!-- generated: 2026-09-13T09:55+02:00 | cycles 1-3 -->\n- old\n",
                   [JD + "4-cycle-4.md"])
    vault.install(monkeypatch)
    assert recap_refresh.refresh_once(now=NOW) is True


def test_a_silent_model_leaves_the_old_card_alone(monkeypatch):
    vault = _Vault(render(["old"], NOW, journal="3-cycle-3.md"),
                   [JD + "4-cycle-4.md"])
    vault.install(monkeypatch, answer=[])
    assert recap_refresh.refresh_once(now=NOW) is False
    assert vault.writes == []


def test_a_refused_write_is_not_an_exception(monkeypatch):
    vault = _Vault(render(["old"], NOW, journal="3-cycle-3.md"),
                   [JD + "4-cycle-4.md"], result="FAILED: 409 conflict")
    vault.install(monkeypatch)
    assert recap_refresh.refresh_once(now=NOW) is False


def test_an_empty_journal_never_writes(monkeypatch):
    vault = _Vault("", [])
    vault.install(monkeypatch)
    assert recap_refresh.refresh_once(now=NOW) is False
    assert vault.writes == []


def test_a_raising_vault_is_swallowed(monkeypatch):
    # A refresher that can take the runner down is a worse bargain than a
    # stale card -- `catalog_refresh` makes the same trade.
    monkeypatch.setattr(recap_refresh, "vault_list_ids",
                        lambda prefix: (_ for _ in ()).throw(RuntimeError("couch down")))
    assert recap_refresh.refresh_once(now=NOW) is False


def test_only_one_refresh_runs_at_a_time(monkeypatch):
    vault = _Vault(render(["old"], NOW, journal="3-cycle-3.md"), [JD + "4-cycle-4.md"])
    vault.install(monkeypatch)
    recap_refresh._lock.acquire()
    try:
        assert recap_refresh.refresh_once(now=NOW) is False
        assert vault.writes == []
    finally:
        recap_refresh._lock.release()


def test_the_prompt_carries_the_footers_and_his_grouping_rule():
    prompt = recap_refresh.prompt_for(
        [{"file": "3-cycle-3.md", "title": "a title", "footer": "PR: #9 | Outcome: merged"}])
    assert "a title" in prompt and "PR: #9" in prompt
    assert "rather than by cycle" in prompt
    assert str(recap_refresh.MAX_BULLETS) in prompt


def test_the_model_is_the_subscription_one(monkeypatch):
    # Rule 9 of identity.md: production never spends the metered API. The
    # bridge lane takes a bare model id and resolves it on the subscription;
    # an `anthropic:` prefix here would be the metered twin.
    assert not recap_refresh.RECAP_MODEL.startswith("anthropic:")
    sent = {}

    def _post(method, url, body, headers, timeout=None):
        sent.update(body)
        return 200, {"text": "- one"}

    monkeypatch.setattr(recap_refresh, "CLAUDE_BRIDGE_URL", "http://bridge")
    assert recap_refresh.ask_haiku([{"file": "f", "title": "t", "footer": ""}],
                                   post=_post) == ["one"]
    assert sent["model"] == recap_refresh.RECAP_MODEL
    assert sent["stateless"] and sent["allow_concurrent"] and sent["restricted"]
    # Not the thread's own id: his Stop button cancels by conversation id.
    assert sent["conversation_id"] == "nova-recap"


def test_an_unreachable_bridge_is_not_an_exception(monkeypatch):
    def _post(*a, **kw):
        raise OSError("no route")

    monkeypatch.setattr(recap_refresh, "CLAUDE_BRIDGE_URL", "http://bridge")
    assert recap_refresh.ask_haiku([{"file": "f", "title": "t", "footer": ""}],
                                   post=_post) == []


def test_a_non_200_from_the_bridge_is_not_a_card(monkeypatch):
    monkeypatch.setattr(recap_refresh, "CLAUDE_BRIDGE_URL", "http://bridge")
    assert recap_refresh.ask_haiku([{"file": "f", "title": "t", "footer": ""}],
                                   post=lambda *a, **kw: (503, {})) == []


def test_the_runner_starts_it(monkeypatch, lifecycle_events):
    """The wire, not the callee. A test that calls `start_recap_refresh`
    directly asserts nothing about `main` calling it -- the mutation that
    catches this is deleting the call site. Same shape as the catalog
    refresher's own wire test, for the same reason."""
    import importlib

    runner_main = importlib.import_module("agora_runner.main")

    started = []
    monkeypatch.setattr(runner_main, "start_recap_refresh", lambda: started.append(1))
    monkeypatch.setattr(runner_main, "start_catalog_refresh", lambda: None)
    monkeypatch.setattr(runner_main, "start_invoke_server", lambda: None)
    monkeypatch.setattr(runner_main, "poll_once", lambda: None)
    monkeypatch.setattr(runner_main, "join_running_heartbeats", lambda *a, **k: None)
    monkeypatch.setattr(runner_main, "_shutdown_requested", True)
    monkeypatch.setattr(runner_main.signal, "signal", lambda *a: None)

    runner_main.main()

    assert started == [1]
