"""`tools.preflight` collapses clean checks and reproduces dirty ones in full."""
import io
import os

from tools import preflight


def render(results):
    out = io.StringIO()
    code = preflight.render(results, stream=out)
    return code, out.getvalue()


def test_clean_check_collapses_to_the_line_that_carries_its_count():
    code, text = render([
        ("doc_integrity", 0, "line one\nWhole. Swept 11 document(s): a, b, c\n", 0.4),
    ])
    assert code == 0
    assert "Whole. Swept 11 document(s): a, b, c" in text
    # The body above the verdict is not carried.
    assert "line one" not in text
    assert "full output" not in text


def test_a_trailing_footnote_is_skipped_for_the_line_that_measured_something():
    # Five of the fourteen real checks close on a fixed explanatory sentence.
    # Collapsing to it would print a summary that cannot vary with the result.
    _, text = render([(
        "heartbeat_health", 0,
        "ok  Nova -- every 30m\n"
        "Read 8 heartbeat(s) from http://agora.agents.svc.cluster.local:8080.\n"
        "A heartbeat that is off on purpose carries '(disabled' in its own name.\n",
        1.0,
    )])
    assert "Read 8 heartbeat(s)" in text
    assert "off on purpose" not in text


def test_a_check_with_no_digit_anywhere_falls_back_to_its_last_line():
    _, text = render([("cli_features", 0, "first\nnothing has moved\n", 0.1)])
    assert "nothing has moved" in text


def test_verbose_reproduces_a_clean_check_in_full():
    results = [("doc_integrity", 0, "line one\nWhole. Swept 11 document(s)\n", 0.4)]
    out = io.StringIO()
    preflight.render(results, stream=out, verbose=True)
    text = out.getvalue()
    assert "line one" in text
    assert "exit 0 -- full output" in text
    # ...and without it, the body stays collapsed.
    assert "line one" not in render(results)[1]


def test_a_finding_is_reproduced_verbatim_and_unabridged():
    body = "ALERT high brace-expansion in repo x\nline two\nline three\nACT ON THIS"
    code, text = render([("security_alerts", 2, body, 1.0)])
    assert code == 2
    for line in body.splitlines():
        assert line in text
    assert "exit 2 -- full output" in text


def test_unreadable_is_reproduced_too_and_never_reads_as_clean():
    code, text = render([("crossplane_health", 1, "CANNOT SEE: 6 ActionsSecret\n", 1.0)])
    assert code == 1
    assert "CANNOT SEE: 6 ActionsSecret" in text
    assert "UNREADABLE" in text


def test_overall_status_is_the_worst_not_the_last():
    code, _ = render([
        ("a", 2, "found something\n", 0.1),
        ("b", 1, "could not read\n", 0.1),
        ("c", 0, "clean\n", 0.1),
    ])
    assert code == 2
    code, _ = render([("a", 1, "could not read\n", 0.1), ("b", 0, "clean\n", 0.1)])
    assert code == 1


def test_footer_names_every_check_that_ran():
    _, text = render([("cli_pin", 0, "current\n", 0.2), ("ci_health", 0, "green\n", 0.3)])
    assert "Ran 2 check(s): cli_pin, ci_health." in text


def test_a_check_with_no_output_is_not_summarised_as_blank():
    _, text = render([("cli_features", 0, "   \n\n", 0.1)])
    assert "(no output)" in text


def test_every_declared_check_resolves_to_a_module():
    assert preflight.unknown_checks(preflight.CHECKS) == []


def test_a_typo_in_the_roster_is_a_hard_error_not_a_skip(capsys):
    assert preflight.main(["--only", "doc_integrity", "no_such_check"]) == 1
    err = capsys.readouterr().err
    assert "NO SUCH CHECK: no_such_check" in err


def test_the_two_non_status_tools_are_deliberately_out_of_the_roster():
    # top_board_rows prints the pick and tidy_workspace moves files; collapsing
    # either would hide the thing step 1a exists to show.
    assert "top_board_rows" not in preflight.CHECKS
    assert "tidy_workspace" not in preflight.CHECKS


def test_a_hung_check_is_reported_as_unreadable(monkeypatch):
    import subprocess

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="x", timeout=preflight.TIMEOUT_SECONDS)

    monkeypatch.setattr(subprocess, "run", boom)
    name, code, output, _ = preflight.run_check("doc_integrity")
    assert code == 1
    assert "TIMED OUT" in output


def test_a_check_that_crashes_carries_its_stderr_into_the_report():
    name, code, output, _ = preflight.run_check("preflight_no_such_module_xyz")
    assert code != 0
    assert output.strip()


# --- source_revision: the roster itself can be out of date ------------------
#
# Cycle 644 ran preflight from a checkout left on a merged branch, nineteen
# commits behind main. It printed a clean table and the four NAS checks plus
# cadence_control were absent from that tree entirely -- so they were absent
# from CHECKS too, and unknown_checks had nothing to refuse. These tests sit
# on that boundary: behind raises, ahead does not, no repo does not.


def _repo(tmp_path, name):
    import subprocess

    path = tmp_path / name
    path.mkdir()
    run = lambda *a: subprocess.run(["git", "-C", str(path)] + list(a),
                                    capture_output=True, text=True, check=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (path / "f").write_text("one")
    run("add", "-A")
    run("commit", "-qm", "one")
    return path, run


def test_a_checkout_behind_main_raises_and_says_it_cannot_name_what_is_missing(tmp_path):
    upstream, up_run = _repo(tmp_path, "upstream")
    import subprocess
    subprocess.run(["git", "clone", "-q", str(upstream), str(tmp_path / "work")], check=True)
    (upstream / "f").write_text("two")
    up_run("commit", "-qam", "two")

    code, report = preflight.source_revision(directory=str(tmp_path / "work"))
    assert code == 2, report
    assert "BEHIND origin/main by 1 commit(s)" in report


def test_a_feature_branch_ahead_of_main_is_not_a_finding(tmp_path):
    upstream, _ = _repo(tmp_path, "upstream")
    import subprocess
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(upstream), str(work)], check=True)
    run = lambda *a: subprocess.run(["git", "-C", str(work)] + list(a),
                                    capture_output=True, text=True, check=True)
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    run("checkout", "-qb", "feature")
    (work / "f").write_text("mine")
    run("commit", "-qam", "mine")

    code, report = preflight.source_revision(directory=str(work))
    assert code == 0, report
    assert "1 commit(s) ahead" in report


def test_no_git_checkout_cannot_see_rather_than_clean_or_broken(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    code, report = preflight.source_revision(directory=str(plain))
    assert code == 0
    assert "CANNOT SEE" in report


def test_source_revision_is_not_in_the_roster_because_a_stale_tree_would_not_have_it():
    # It is computed in-process on purpose: a tools/ module would be missing
    # from exactly the out-of-date checkout it exists to catch.
    assert "source_revision" not in preflight.CHECKS


def test_source_revision_runs_even_when_only_names_one_check(monkeypatch):
    # --only is how a cycle re-runs one check, and that is exactly when it is
    # most likely to be sitting on the wrong branch. The row is not optional.
    seen = {}
    monkeypatch.setattr(preflight, "run_check",
                        lambda name: (name, 0, "swept 1 thing", 0.1))
    monkeypatch.setattr(preflight, "source_revision",
                        lambda **kw: (2, "BEHIND origin/main by 3 commit(s)"))
    monkeypatch.setattr(preflight, "render",
                        lambda results, **kw: seen.setdefault("r", results) and 0)

    preflight.main(["--only", "doc_integrity", "--no-fetch"])
    names = [r[0] for r in seen["r"]]
    assert names == ["source_revision", "doc_integrity"]
    assert seen["r"][0][1] == 2
