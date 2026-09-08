"""Tests for `tools.lifecycle_health`.

**Scope, said plainly, because a test that claims more than it checks is
the failure this repo keeps writing up.** The grouping of raw rows into
process lives is `bridge.lifecycle_log.lives`, which lives in
`agora-claude-bridge` and is tested there; this module deliberately does
not fork it, so nothing here re-spells that rule. What these tests own is
everything this tool decides on top of it: which verdict raises, that the
still-running life is never judged, that an absent ledger and an empty one
are different answers, and that a death older than the window stops
raising.

The one place the real import path is exercised is
`test_missing_bridge_module_is_unreadable`, which points `BRIDGE_APP_DIR`
at an empty directory and asserts the tool says it cannot run rather than
coming back clean.
"""
import io
import json

import pytest

from tools import lifecycle_health


def _life(verdict, started="2026-09-08T21:32:50+00:00", signal=None,
          drained=None, in_flight=0):
    life = {
        "started": {"event": "started", "at": started},
        "signal": {"event": "signal", "at": signal, "in_flight": in_flight} if signal else None,
        "drained": {"event": "drained", "at": drained} if drained else None,
        "verdict": verdict,
        "turns_lost": in_flight if verdict == "killed_mid_drain" else 0,
    }
    return life


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Install a fake bridge whose `lives` returns whatever a test hands it.

    Returns a callable: `ledger(lives, rows=...)` writes a real file at the
    path the tool will stat, so the absent/empty/present distinction is
    exercised against the filesystem rather than against a flag.
    """
    path = tmp_path / "bridge-lifecycle.jsonl"

    def install(lives, rows=None, write=True):
        rows = rows if rows is not None else [{"event": "started"}]
        if write:
            path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        monkeypatch.setattr(
            lifecycle_health, "_load",
            lambda: (lambda _p=None: rows, lambda _rows: lives, str(path)))
        return path

    return install


def _run(window_hours=lifecycle_health.DEFAULT_WINDOW_HOURS, now=None):
    status, detail = lifecycle_health.check(window_hours=window_hours, now=now)
    out = io.StringIO()
    code = lifecycle_health.report(status, detail, out=out)
    return code, out.getvalue()


NOW = __import__("datetime").datetime(2026, 9, 8, 22, 0, tzinfo=__import__("datetime").timezone.utc)


def test_clean_shutdown_passes(ledger):
    ledger([_life("clean", signal="2026-09-08T20:00:00+00:00",
                  drained="2026-09-08T20:00:09+00:00"),
            _life("running", started="2026-09-08T21:32:50+00:00")])
    code, text = _run(now=NOW)
    assert code == 0
    assert "clean" in text


def test_killed_mid_drain_raises_and_counts_the_turns(ledger):
    ledger([_life("killed_mid_drain", started="2026-09-08T19:00:00+00:00",
                  signal="2026-09-08T20:00:00+00:00", in_flight=2),
            _life("running", started="2026-09-08T21:32:50+00:00")])
    code, text = _run(now=NOW)
    assert code == 2
    assert "KILLED BEFORE IT FINISHED DRAINING" in text
    # The count is the whole reason the row carries `in_flight`; a verdict
    # without it does not say what the kill cost. Assert it on the HEADLINE,
    # not anywhere in the output: the per-life line below also prints "2
    # turn(s)", so a bare substring check passed with the headline's count
    # mutated away -- a second source satisfying the assertion is the same
    # thing as no assertion.
    assert "DRAINING — 2 turn(s) in flight" in text.splitlines()[0]


def test_no_signal_raises_with_a_different_fix(ledger):
    ledger([_life("no_signal", started="2026-09-08T19:00:00+00:00"),
            _life("running", started="2026-09-08T21:32:50+00:00")])
    code, text = _run(now=NOW)
    assert code == 2
    assert "NEVER ASKED TO STOP" in text
    # The two causes must not print the same advice: fixing the drain when
    # no SIGTERM ever arrived is fixing the wrong thing.
    assert "KILLED BEFORE IT FINISHED DRAINING" not in text


def test_only_a_running_life_is_not_a_pass_in_words(ledger):
    ledger([_life("running")])
    code, text = _run(now=NOW)
    assert code == 0
    assert "no shutdown to judge yet" in text
    assert "not 'the last shutdown was clean'" in text


def test_the_running_life_is_never_judged(ledger):
    """A `running` life with a signal and no drain is this process draining
    now. Judging it would make every bridge report itself as killed."""
    ledger([_life("clean", signal="2026-09-08T18:00:00+00:00",
                  drained="2026-09-08T18:00:05+00:00"),
            _life("running", started="2026-09-08T21:32:50+00:00",
                  signal="2026-09-08T21:59:00+00:00", in_flight=1)])
    code, _ = _run(now=NOW)
    assert code == 0


def test_a_death_older_than_the_window_stops_raising(ledger):
    lives = [_life("killed_mid_drain", started="2026-09-01T10:00:00+00:00",
                   signal="2026-09-01T10:05:00+00:00", in_flight=3),
             _life("running", started="2026-09-08T21:32:50+00:00")]
    ledger(lives)
    # Precondition: the same ledger DOES raise on a wide enough window, so
    # the pass below is the window doing its job and not the verdict being
    # unreachable.
    assert _run(window_hours=24 * 30, now=NOW)[0] == 2
    assert _run(window_hours=24, now=NOW)[0] == 0


def test_absent_ledger_is_unreadable_not_clean(ledger, tmp_path):
    path = ledger([], rows=[], write=False)
    assert not path.exists()
    code, text = _run(now=NOW)
    assert code == 1
    assert "COULD NOT READ" in text
    # Name the branch. Without this the empty-ledger branch below catches an
    # absent file too -- `read` returns `[]` either way -- so deleting the
    # `os.path.exists` guard entirely would still exit 1 and this test would
    # still pass while the tool had stopped telling the two apart.
    assert "does not exist" in text


def test_present_but_empty_ledger_is_unreadable_not_clean(ledger):
    """`lifecycle_log.read` returns `[]` for a missing file and for an empty
    one, so absence has to be measured off the filesystem. Both are exit 1,
    and this asserts the empty case reaches it by the other branch."""
    path = ledger([], rows=[])
    assert path.exists()
    code, text = _run(now=NOW)
    assert code == 1
    assert "holds no readable row" in text
    assert "does not exist" not in text


def test_missing_bridge_module_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle_health, "BRIDGE_APP_DIR", str(tmp_path))
    monkeypatch.delitem(__import__("sys").modules, "bridge.lifecycle_log", raising=False)
    monkeypatch.delitem(__import__("sys").modules, "bridge", raising=False)
    status, detail = lifecycle_health.check(now=NOW)
    assert status == 1
    assert "no bridge.lifecycle_log" in detail["error"]
