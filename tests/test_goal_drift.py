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
from tools.goal_measures import has_drifted, kpi_drift_crosses_bounds


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
    monkeypatch.setattr(goal_drift, "fetch", lambda path, rev=None: ("", False))
    called = []
    monkeypatch.setattr("tools.goal_measures.main",
                        lambda argv: called.append(argv) or 0)
    assert goal_drift.main([]) == 1
    assert called == [], "it measured against a document it could not read"


def test_a_missing_optional_document_still_judges_the_rest(monkeypatch):
    """`expectations.md` feeds one key result; losing it must not lose fifteen."""
    from agora_runner.expectations import EXPECTATIONS_PATH

    def fetch(path, rev=None):
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
    monkeypatch.setattr(goal_drift, "fetch", lambda path, rev=None: ("# doc\n", True))
    monkeypatch.setattr("tools.goal_measures.main", lambda argv: 2)
    assert goal_drift.main([]) == 2


def test_it_never_writes_by_default(monkeypatch):
    """It reports stale numbers; repairing them is an explicit decision."""
    monkeypatch.setattr(goal_drift, "fetch", lambda path, rev=None: ("# doc\n", True))
    seen = {}
    monkeypatch.setattr("tools.goal_measures.main",
                        _recorder(seen))
    goal_drift.main([])
    assert "--write" not in seen["argv"]


def test_the_sweep_never_passes_the_repair_flag():
    """`preflight` runs this every cycle; the write must stay a cycle typing it."""
    import inspect
    from tools import preflight
    source = inspect.getsource(preflight)
    assert goal_drift.REPAIR_FLAG not in source


def test_no_revision_is_recorded_when_it_is_only_watching(monkeypatch):
    """A `--rev-file` on a read that will never write is a call for nothing."""
    revs = []
    monkeypatch.setattr(goal_drift, "fetch",
                        lambda path, rev=None: (revs.append(rev), ("# doc\n", True))[1])
    monkeypatch.setattr("tools.goal_measures.main", _recorder({}))
    goal_drift.main([])
    assert revs and all(rev is None for rev in revs)


def test_a_read_is_the_document_not_the_newline_print_adds(monkeypatch):
    """A get-then-put round trip must be byte-identical, or every repair pads the file."""
    document = "# goals\n\nnow: 1\n"

    class Done:
        returncode = 0
        stdout = document + "\n"  # what `print(content)` in vault_tool.py emits

    monkeypatch.setattr(goal_drift.subprocess, "run", lambda *a, **k: Done())
    assert goal_drift.fetch("projects/x.md") == (document, True)


def test_a_read_from_a_get_that_no_longer_pads_keeps_the_final_newline(monkeypatch):
    """After agora-claude-bridge#118 `get` emits the document exactly.

    Stripping unconditionally would then eat the document's own final newline
    on every repair.
    """
    document = "# goals\n\nnow: 1\n"

    class Done:
        returncode = 0
        stdout = document

    monkeypatch.setattr(goal_drift.subprocess, "run", lambda *a, **k: Done())
    assert goal_drift.fetch("projects/x.md") == (document, True)


def _repairing(monkeypatch, before, after, writer):
    """Drive `--repair` over one fake required document. Returns the exit code.

    The measurement is replaced by something that rewrites the local copy the
    way `goal_measures --write` would, so what is under test is the vault
    half: the revision guard, the line-count tripwire and the no-op skip.
    """
    from agora_runner.nova_plan import GOALS_PATH

    def fetch(path, rev=None):
        return (before, True) if path == GOALS_PATH else ("# doc\n", True)

    monkeypatch.setattr(goal_drift, "fetch", fetch)

    def measure(argv):
        assert "--write" in argv
        local = argv[argv.index("--goals") + 1]
        open(local, "w", encoding="utf-8").write(after)
        return 0

    monkeypatch.setattr("tools.goal_measures.main", measure)
    monkeypatch.setattr(goal_drift, "put", writer)
    return goal_drift.main(["--repair"])


def test_a_repair_writes_the_changed_document_back_under_its_revision(monkeypatch, capsys):
    """The whole point: the measured number reaches the document he reads."""
    seen = []
    code = _repairing(monkeypatch, "now: 6.1\n", "now: 6.6\n",
                      lambda path, local, rev: seen.append((path, rev)) or None)
    assert code == 0
    assert len(seen) == 1, "it wrote a document it had not changed"
    path, rev = seen[0]
    assert rev is not None, "written with no if_rev guard -- that is a clobber"


def test_a_document_that_already_agrees_is_not_written(monkeypatch, capsys):
    """A no-op write is a real chance to lose his edit for nothing."""
    seen = []
    code = _repairing(monkeypatch, "now: 6.6\n", "now: 6.6\n",
                      lambda path, local, rev: seen.append(path) or None)
    assert code == 0
    assert seen == []
    assert "already carried every measured number" in capsys.readouterr().out


def test_a_repair_that_changed_the_line_count_is_refused(monkeypatch, capsys):
    """A measurement swaps a value inside a line; anything else is not one."""
    seen = []
    code = _repairing(monkeypatch, "now: 6.1\n", "now: 6.6\nnow: 1\n",
                      lambda path, local, rev: seen.append(path) or None)
    assert code == 1, "a refused write must not read as a clean repair"
    assert seen == [], "it wrote a copy it could not account for"
    assert "line(s) against the" in capsys.readouterr().err


def test_a_refused_write_is_exit_1_and_says_which(monkeypatch, capsys):
    """A 409 from his own edit has to be visible, not swallowed."""
    code = _repairing(monkeypatch, "now: 6.1\n", "now: 6.6\n",
                      lambda path, local, rev: "FAILED(conflict)")
    assert code == 1
    captured = capsys.readouterr()
    assert "FAILED(conflict)" in captured.err
    assert "REPAIRED 0 document(s)" in captured.out


def test_nothing_is_written_when_the_measurement_failed(monkeypatch):
    """A half-taken measurement must not become the scoreboard."""
    monkeypatch.setattr(goal_drift, "fetch", lambda path, rev=None: ("# doc\n", True))
    monkeypatch.setattr("tools.goal_measures.main", lambda argv: 1)
    seen = []
    monkeypatch.setattr(goal_drift, "put",
                        lambda path, local, rev: seen.append(path) or None)
    assert goal_drift.main(["--repair"]) == 1
    assert seen == []


def test_the_repair_never_waives_the_collapse_guard():
    """`--allow-shrink` on a digit swap could only ever hide a bad read.

    Read off the compiled constants rather than the source, because the
    source says the words in a comment explaining why they are not passed.
    """
    assert "--allow-shrink" not in goal_drift.put.__code__.co_consts


def test_it_is_registered_in_the_opening_sweep():
    """A check nothing runs is the gap this closes, one level up."""
    from tools import preflight
    assert "goal_drift" in preflight.CHECKS


def test_a_kpi_that_moved_inside_its_own_range_is_not_counted():
    """The rolling-window treadmill this carve-out exists to end.

    `nova-kpi-cost-per-cycle` is a median over the last 24 hours; it read
    1.52 when Cycle 1576 wrote it into the vault and 1.5 an hour later, with
    nothing having gone wrong. Both are inside `[0.8..2.0]`, so the guardrail's
    own claim — this is in bounds — is unchanged, and the digit is a snapshot
    ageing.
    """
    kpi = {"id": "nova-kpi-cost-per-cycle", "now": "1.52",
           "low": "0.8", "high": "2.0"}
    assert has_drifted(kpi["now"], 1.5) is True
    assert kpi_drift_crosses_bounds(kpi, 1.5) is False


def test_a_kpi_that_left_its_range_is_counted():
    """The other side of the same bound, which must still raise."""
    kpi = {"id": "nova-kpi-cost-per-cycle", "now": "1.52",
           "low": "0.8", "high": "2.0"}
    assert kpi_drift_crosses_bounds(kpi, 2.4) is True


def test_a_kpi_whose_written_breach_has_ended_is_counted():
    """Crossing inwards is a finding too: the page reports a breach that is over.

    `nova-kpi-silent-cycles` sat at 6 against a ceiling of 1 for days. A
    document still saying 6 while the instrument reads 0 is misreporting the
    system in the flattering direction, which is the one nobody checks.
    """
    kpi = {"id": "nova-kpi-silent-cycles", "now": "6", "low": "0", "high": "1"}
    assert kpi_drift_crosses_bounds(kpi, 0) is True


def test_a_kpi_with_no_readable_number_always_counts():
    """A guardrail with no reading is not a guardrail.

    This is the blank `has_drifted` was built to fill, so the carve-out must
    never be the thing that quietens it.
    """
    kpi = {"id": "nova-kpi-dropped-ticks", "now": "", "low": "0", "high": "10"}
    assert kpi_drift_crosses_bounds(kpi, 3) is True
    kpi["now"] = "not measured"
    assert kpi_drift_crosses_bounds(kpi, 3) is True


def test_no_reading_is_not_a_crossing():
    """`value is None` stays neither drift nor crossing, as in `has_drifted`."""
    kpi = {"id": "marcus-kpi-coach-latency", "now": "14.9",
           "low": "0", "high": "30"}
    assert kpi_drift_crosses_bounds(kpi, None) is False


def test_key_results_get_no_carve_out():
    """A target is read against the digit, so any drift there stays a defect.

    Pinned through the two renderers rather than by reading the status block,
    because what a reader sees and what the exit code counts have to be the
    same judgement: a key result that moved 0.1 still says `drifted`, and a
    KPI that moved the same 0.1 inside its range does not.
    """
    from tools.goal_measures import render_key_results, render_kpis

    kr = {"id": "nova-kr-closes-rows", "name": "The work closes your rows",
          "now": "3.9", "target": "2.0"}
    drifted = render_key_results(
        [{"project": "nova", "id": kr["id"], "kr": kr, "value": 3.8,
          "detail": "measured"}], "project-goals.md")
    assert "drifted" in drifted

    # The same size of move on a KPI inside its range says the other thing.
    kpi = {"id": "nova-kpi-cost-per-cycle", "now": "1.52",
           "low": "0.8", "high": "2.0"}
    moved = render_kpis(
        [{"project": "nova", "id": kpi["id"], "kpi": kpi, "value": 1.5,
          "detail": "measured"}], "project-goals.md")
    assert "moved inside its own range" in moved
    assert "drifted" not in moved

    crossed = render_kpis(
        [{"project": "nova", "id": kpi["id"], "kpi": kpi, "value": 2.4,
          "detail": "measured"}], "project-goals.md")
    assert "drifted" in crossed
    assert "moved inside its own range" not in crossed


def test_the_command_line_reaches_main():
    """`main()` with no argv drops every flag typed at the shell, in silence.

    That is what happened on the first real `--repair` run: it printed a
    complete drift report and wrote nothing, because `__main__` called
    `main()` and the module had never taken a flag before.
    """
    import inspect
    source = inspect.getsource(goal_drift)
    tail = source[source.index('if __name__ == "__main__":'):]
    assert "main(sys.argv[1:])" in tail, "the shell's arguments never reach main"
