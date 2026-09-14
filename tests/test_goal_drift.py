"""`tools.goal_drift` — the goal numbers watched, not just measurable.

Issue #227's seventh rule asks for a weekly check of target against current
number. `tools.goal_measures` could always take the measurement; nothing in
production ever ran it, so the numbers on `goals.md` and `project-goals.md`
were whatever the last cycle to run it by hand had typed. These tests pin the
two halves that make it a watcher: one definition of drift shared with the
renderers, and a status a sweep can read.
"""

import pytest

from tools import goal_drift
from tools.goal_measures import has_drifted


def _recorder(seen):
    """Stand in for `goal_measures.main`, keeping the argv it was handed."""
    def measure(argv):
        seen["argv"] = argv
        return 0
    return measure


def test_a_measurement_matching_the_document_is_not_drift():
    assert has_drifted("3.9", 3.9) is False
    # `_as_number` strips a trailing percent, so `83` and `83 %` are one
    # number written two ways rather than a disagreement.
    assert has_drifted("83%", 83) is False


def test_a_measurement_disagreeing_with_the_document_is_drift():
    assert has_drifted("5.3", 5.5) is True


def test_a_blank_now_beside_a_real_reading_is_drift():
    """The blank is the thing this was built to fill, not a pass."""
    assert has_drifted("", 4) is True
    assert has_drifted("(blank)", 4) is True


def test_an_instrument_with_no_reading_is_never_drift():
    """`value is None` means nothing was measured, in either direction.

    Marcus's coach numbers read nothing until he taps the coach. Counting
    that as drift would make the check permanently red on an instrument that
    is working exactly as designed, which is the state that gets a check
    ignored.
    """
    assert has_drifted("14.9", None) is False
    assert has_drifted("", None) is False


def test_the_renderer_and_the_status_share_one_definition():
    """A report saying `drifted` beside a status saying clean is the bug."""
    import inspect
    from tools import goal_measures
    for name in ("render", "render_key_results", "render_kpis"):
        source = inspect.getsource(getattr(goal_measures, name))
        assert "has_drifted(" in source, f"{name} derives drift on its own"


def test_writing_and_watching_are_refused_together():
    """`--write` repairs the drift, so a status taken after it is always clean."""
    from tools.goal_measures import main
    with pytest.raises(SystemExit):
        main(["--goals", "/nonexistent", "--write", "--exit-on-drift"])


def test_an_unreadable_document_is_exit_1_and_judges_nothing(monkeypatch, capsys):
    """Not exit 0. A vault it could not reach must never read as clean."""
    monkeypatch.setattr(goal_drift, "fetch", lambda path: ("", False))
    called = []
    monkeypatch.setattr("tools.goal_measures.main",
                        lambda argv: called.append(argv) or 0)
    assert goal_drift.main([]) == 1
    assert called == [], "it measured against a document it could not read"


def test_a_missing_optional_document_still_judges_the_rest(monkeypatch):
    """`expectations.md` feeds one key result; losing it must not lose fifteen."""
    from agora_runner.expectations import EXPECTATIONS_PATH

    def fetch(path):
        return ("", False) if path == EXPECTATIONS_PATH else ("# doc\n", True)

    monkeypatch.setattr(goal_drift, "fetch", fetch)
    seen = {}
    monkeypatch.setattr("tools.goal_measures.main",
                        _recorder(seen))
    assert goal_drift.main([]) == 0
    argv = seen["argv"]
    assert "--expectations" not in argv
    assert "--decisions" in argv
    assert "--exit-on-drift" in argv


def test_it_passes_the_measurement_its_own_exit_code(monkeypatch):
    """The verdict is `goal_measures`'s, not a second opinion taken here."""
    monkeypatch.setattr(goal_drift, "fetch", lambda path: ("# doc\n", True))
    monkeypatch.setattr("tools.goal_measures.main", lambda argv: 2)
    assert goal_drift.main([]) == 2


def test_it_never_writes(monkeypatch):
    """It reports stale numbers; repairing them is a cycle's decision."""
    monkeypatch.setattr(goal_drift, "fetch", lambda path: ("# doc\n", True))
    seen = {}
    monkeypatch.setattr("tools.goal_measures.main",
                        _recorder(seen))
    goal_drift.main([])
    assert "--write" not in seen["argv"]


def test_it_is_registered_in_the_opening_sweep():
    """A check nothing runs is the gap this closes, one level up."""
    from tools import preflight
    assert "goal_drift" in preflight.CHECKS
