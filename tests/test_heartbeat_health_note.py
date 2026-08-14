"""The journal self-check actually reaching a cycle.

`cycle_health` was written on 2026-08-12 and nothing called it for five
cycles (issue #70) -- the loop had a working smoke alarm with no battery in
it. These tests are about the wire, not the check: that Nova's heartbeat
carries the finding, that nobody else's does, and that a broken check
cannot take a cycle down with it.
"""

from datetime import datetime, timedelta, timezone

import pytest

import agora_runner.heartbeats as heartbeats
from agora_runner.config import NOVA_PERSONA_ID, OSLO
from agora_runner.heartbeats import _parse_run_at, nova_health_note


NOVA = {"id": NOVA_PERSONA_ID, "name": "Nova"}
SOMEONE_ELSE = {"id": "a443a704-6b53-4347-87df-4b326895877b", "name": "K3s Sentinel"}


class FakeFiles(dict):
    unreadable = ()


def fake_journal(monkeypatch, mtimes, unreadable=()):
    """Stand in for the vault read `nova_health_note` does."""
    files = FakeFiles({path: "### Cycle" for path in mtimes})
    files.unreadable = unreadable

    import agora_runner.vault as vault
    monkeypatch.setattr(vault, "vault_bulk_list", lambda prefix: (files, mtimes))


def fake_heartbeats(monkeypatch, *heartbeats, status=200, raises=None):
    """Stand in for the `/heartbeats` read `nova_cadence_minutes` does."""
    def agora_internal(method, path, payload=None):
        if raises is not None:
            raise raises
        return status, {"heartbeats": list(heartbeats)}

    monkeypatch.setattr("agora_runner.http_util.agora_internal", agora_internal)


@pytest.fixture(autouse=True)
def no_live_agora(monkeypatch):
    """No test in this file may reach the network to learn the cadence.

    Without this they still passed, for the wrong reason: `agora_internal`
    raised connection-refused against a localhost Agora that is not there,
    the guard in `nova_health_note` swallowed it, and every assertion below
    was really pinning the *fallback* path. Answering with an empty list is
    the same fallback, chosen rather than inherited -- a test that would
    change its mind if the box it ran on happened to have an Agora is not
    pinning anything.
    """
    fake_heartbeats(monkeypatch)


def entry(cycle, when):
    return {f"{100 + cycle:03d}-cycle-{cycle}.md": int(when.timestamp() * 1000)}


NOW = datetime.now(OSLO)


def test_nova_is_told_about_the_cycle_that_died_last_hour(monkeypatch):
    """The whole point: 134 wrote nothing, 135 wrote the entry that made
    the hole visible, and 136 is the first run that can be told."""
    fake_journal(monkeypatch, {**entry(133, NOW - timedelta(hours=3)),
                               **entry(135, NOW - timedelta(minutes=35))})
    note = nova_health_note(NOVA, (NOW - timedelta(hours=1)).isoformat())
    assert "134" in note
    assert "wrote no journal entry" in note
    assert "/data/workspace" in note


def test_a_healthy_loop_adds_nothing_to_the_prompt(monkeypatch):
    """An empty string, not a reassuring sentence. Every cycle pays to read
    this and a line that is always there is a line nobody reads."""
    fake_journal(monkeypatch, {**entry(132, NOW - timedelta(minutes=80)),
                               **entry(133, NOW - timedelta(minutes=20))})
    assert nova_health_note(NOVA, (NOW - timedelta(hours=1)).isoformat()) == ""


def test_another_persona_never_gets_novas_journal_read_at_all(monkeypatch):
    """Gated before the fetch, not after it -- an unrelated heartbeat must
    not pay for a vault listing it can do nothing with.

    Recorded rather than raised, and that is the whole reason this test is
    worth reading. The first version raised `AssertionError` from the fake
    fetch, which `nova_health_note`'s own `except Exception` swallowed --
    so it returned `""`, the test passed, and it passed just as happily
    with the persona gate deleted. Caught by breaking the gate on purpose;
    a fake that raises can never test code that is allowed to catch."""
    calls = []
    fake_journal(monkeypatch, {**entry(133, NOW - timedelta(hours=3)),
                               **entry(135, NOW - timedelta(minutes=35))})
    import agora_runner.vault as vault
    real = vault.vault_bulk_list

    def record(*a, **kw):
        calls.append(a)
        return real(*a, **kw)

    monkeypatch.setattr(vault, "vault_bulk_list", record)
    since = (NOW - timedelta(hours=1)).isoformat()
    assert nova_health_note(SOMEONE_ELSE, since) == ""
    assert nova_health_note(None, since) == ""
    assert calls == []
    # And the same fixture does produce a finding for Nova, so the empty
    # string above is the gate and not an accidentally healthy journal.
    assert "134" in nova_health_note(NOVA, since)
    assert len(calls) == 1


def test_a_broken_check_never_costs_the_cycle_its_turn_and_never_goes_quiet(monkeypatch):
    """Two properties, and the second was a review finding against the
    first draft. Not raising is right -- a self-check is not worth an hour.
    Returning `""` was not: the freshness boundary is Agora's `lastRunAt`,
    which advances whether or not this ran, so a gap whose bracket lands in
    a failed hour is not delayed but *lost*. Silence there is an all-clear
    from an instrument that never ran, which is the one thing this module
    exists to prevent."""
    import agora_runner.vault as vault
    monkeypatch.setattr(vault, "vault_bulk_list",
                        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("couch 503")))
    note = nova_health_note(NOVA, NOW.isoformat())
    assert "couch 503" in note
    assert "may now never be reported" in note


def test_a_blind_read_is_reported_rather_than_certified_as_healthy(monkeypatch):
    """The failure that actually shipped: an unreadable journal has no gaps
    in it, so silence here reads as an all-clear from an instrument that
    saw nothing."""
    fake_journal(monkeypatch, {}, unreadable=("listing nova: HTTP 401",))
    note = nova_health_note(NOVA, NOW.isoformat())
    assert "cannot tell" in note
    assert "401" in note


@pytest.mark.parametrize("stamp,expected", [
    ("2026-08-12T20:00:00+00:00", datetime(2026, 8, 12, 20, tzinfo=timezone.utc)),
    ("2026-08-12T20:00:00Z", datetime(2026, 8, 12, 20, tzinfo=timezone.utc)),
    ("2026-08-12T20:00:00", datetime(2026, 8, 12, 20, tzinfo=timezone.utc)),
])
def test_agoras_timestamp_shapes_all_parse(stamp, expected):
    assert _parse_run_at(stamp) == expected


@pytest.mark.parametrize("stamp", [None, "", "yesterday", "not-a-date"])
def test_an_unusable_timestamp_becomes_no_boundary_not_a_wrong_one(stamp):
    """`None` makes `gaps_since` report everything once. Erring toward one
    noisy run beats adopting a wrong boundary and swallowing a real
    failure, which is the direction that loses information."""
    assert _parse_run_at(stamp) is None


def test_run_heartbeat_actually_puts_the_note_in_the_system_prompt(monkeypatch):
    """The test that would have failed for the five cycles this sat unused.
    Every pure unit above passes just as happily when nothing calls any of
    them, which is exactly the state issue #70 describes."""
    captured = {}

    class Stop(Exception):
        pass

    def capture(persona, conversation, participants, heartbeat_extra=None):
        captured["extra"] = heartbeat_extra
        raise Stop

    monkeypatch.setattr(heartbeats, "agora_internal", lambda *a, **kw: (200, {}))
    monkeypatch.setattr(heartbeats, "fetch_persona", lambda pid: NOVA)
    monkeypatch.setattr(heartbeats, "agora_get",
                        lambda path: (200, {"personas": [], "messages": []}))
    monkeypatch.setattr(heartbeats, "rotate_cycle_conversation",
                        lambda hb, personas: hb["conversationId"])
    monkeypatch.setattr(heartbeats, "build_system", capture)
    fake_journal(monkeypatch, {**entry(133, NOW - timedelta(hours=3)),
                               **entry(135, NOW - timedelta(minutes=35))})

    heartbeat = {
        "id": "hb1", "name": "Nova", "personaId": NOVA_PERSONA_ID,
        "conversationId": "c1", "schedule": "every@60m",
        "task": "Follow prompt.md", "enabled": True,
        "lastRunAt": (NOW - timedelta(hours=1)).isoformat(),
    }
    with pytest.raises(Stop):
        heartbeats.run_heartbeat(heartbeat)

    assert "134" in captured["extra"]
    # After the task, so a cycle reads its instructions before the
    # exception report that only ever changes how it carries them out.
    assert captured["extra"].index("Follow prompt.md") < captured["extra"].index("134")


def test_a_utc_boundary_is_compared_against_oslo_write_times_correctly(monkeypatch):
    """Two different zones meet here and Oslo is +02:00 in August, so a
    naive comparison would be two hours out -- which is longer than the
    heartbeat interval this whole thing is measured in. Written as an
    explicit UTC string because that is exactly what Agora stores in
    `lastRunAt`, and as Oslo mtimes because that is what `cycle_health`
    builds. A wrong sign here either announces every old gap forever or
    swallows every new one."""
    bracket = NOW - timedelta(minutes=35)
    fake_journal(monkeypatch, {**entry(133, NOW - timedelta(hours=3)),
                               **entry(135, bracket)})

    just_before = (bracket - timedelta(minutes=5)).astimezone(timezone.utc)
    just_after = (bracket + timedelta(minutes=5)).astimezone(timezone.utc)
    assert "134" in nova_health_note(NOVA, just_before.isoformat())
    assert nova_health_note(NOVA, just_after.isoformat()) == ""


def test_the_stall_is_measured_in_this_heartbeat_s_own_interval(monkeypatch):
    """The bug this closes: the check read a 60-minute constant while the
    heartbeat ran at 40, so it waited 120 minutes to report a dead cycle
    instead of 80 -- long enough for a second one to die first.

    100 minutes of silence is 2 intervals at `every@40m` and 1 at
    `every@60m`, and `STALL_GRACE_INTERVALS` is 2. Same journal, same
    clock, two schedules, two answers.
    """
    def silent_for_100_minutes():
        fake_journal(monkeypatch, {**entry(150, NOW - timedelta(minutes=160)),
                                   **entry(151, NOW - timedelta(minutes=100))})

    since = (NOW - timedelta(hours=1)).isoformat()

    silent_for_100_minutes()
    assert "no entry for 2 heartbeat intervals" in nova_health_note(
        NOVA, since, "every@40m")

    silent_for_100_minutes()
    assert nova_health_note(NOVA, since, "every@60m") == ""


def test_a_slower_cadence_does_not_cry_stall_every_single_run(monkeypatch):
    """The other direction, and the one issue #72 says is disqualifying.

    Three hours of silence is a healthy `every@6h` loop and a dead
    `every@60m` one. Against the constant every 6-hourly run would have
    reported a stall -- a false alarm every time, which trains its reader
    to skip the line.
    """
    def silent_for_three_hours():
        fake_journal(monkeypatch, {**entry(150, NOW - timedelta(hours=9)),
                                   **entry(151, NOW - timedelta(hours=3))})

    since = (NOW - timedelta(hours=7)).isoformat()

    silent_for_three_hours()
    assert nova_health_note(NOVA, since, "every@6h") == ""

    silent_for_three_hours()
    assert "heartbeat intervals" in nova_health_note(NOVA, since, "every@60m")


def test_the_unit_is_how_often_an_entry_is_written_not_how_often_this_one_runs(monkeypatch):
    """The divergence #166 left behind, and the reason the lookup is shared.

    Two enabled heartbeats point at Nova, and the slow one is the one
    firing. Entries arrive at the *faster* rate, so 100 minutes of quiet is
    five intervals and not one -- reading `schedule` off the heartbeat in
    hand answers the narrower question and waits through four dead cycles
    before saying anything.

    `every@6h` against `every@20m` on purpose: the two answers have to
    differ by more than the grace window or this pins nothing.
    """
    fake_heartbeats(
        monkeypatch,
        {"id": "a", "personaId": NOVA_PERSONA_ID, "schedule": "every@6h",
         "enabled": True},
        {"id": "b", "personaId": NOVA_PERSONA_ID, "schedule": "every@20m",
         "enabled": True},
    )
    fake_journal(monkeypatch, {**entry(150, NOW - timedelta(hours=9)),
                               **entry(151, NOW - timedelta(minutes=100))})
    note = nova_health_note(NOVA, (NOW - timedelta(hours=10)).isoformat(), "every@6h")
    assert "no entry for 5 heartbeat intervals" in note


def test_an_unreachable_agora_costs_the_unit_and_not_the_measurement(monkeypatch):
    """The regression this guard exists for.

    `http_json` catches `HTTPError`, not `URLError`, so Agora being down
    *raises* out of the cadence lookup -- and the agora pod does roll, four
    `Connection refused` lines over 20 seconds on 2026-08-14. Without the
    inner `except` that raise lands in the outer one and the whole journal
    check is replaced by a failure line, trading a real finding about a
    dead cycle for the label its interval would have carried. `schedule` is
    already in hand and cannot fail, so there is a right answer available.
    """
    fake_heartbeats(monkeypatch, raises=OSError("Connection refused"))
    fake_journal(monkeypatch, {**entry(150, NOW - timedelta(minutes=280)),
                               **entry(151, NOW - timedelta(minutes=220))})
    note = nova_health_note(NOVA, (NOW - timedelta(hours=6)).isoformat(), "every@110m")
    assert "failed to run" not in note
    # Measured in `schedule`, the fallback, not in the 60-minute constant:
    # 220 minutes is 2 intervals at 110 and would be 3 at 60.
    assert "no entry for 2 heartbeat intervals" in note


def test_a_schedule_with_no_single_interval_falls_back_to_the_constant(monkeypatch):
    """`cron@` and `daily@` have no interval to measure in, and neither
    does a hand-edited `every@abc`. The constant is the honest fallback --
    what must not happen is a crash or a zero, both of which reach a real
    cycle's prompt through the `except` below as a scary failure line.
    """
    from agora_runner.cycle_health import HEARTBEAT_MINUTES

    assert HEARTBEAT_MINUTES == 60
    since = (NOW - timedelta(hours=1)).isoformat()

    for schedule in ("cron@0 * * * *", "daily@08:00", "every@abc", "every@0m", None):
        fake_journal(monkeypatch, {**entry(150, NOW - timedelta(minutes=280)),
                                   **entry(151, NOW - timedelta(minutes=220))})
        note = nova_health_note(NOVA, since, schedule)
        # 220 minutes is 3 intervals at the 60-minute constant, so every one
        # of these reports a stall rather than failing to answer.
        assert "no entry for 3 heartbeat intervals" in note, schedule

