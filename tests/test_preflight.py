"""`tools.preflight` collapses clean checks and reproduces dirty ones in full."""
import io
import os
import re
import time

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


def test_a_git_that_hangs_does_not_take_the_whole_sweep_down(monkeypatch, tmp_path):
    # Only the first git call used to be guarded, so a `git fetch` that hit its
    # timeout would raise out of source_revision, out of main(), and lose every
    # other check's result along with it.
    import subprocess

    def boom(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="git", timeout=60)

    monkeypatch.setattr(subprocess, "run", boom)
    code, report = preflight.source_revision(directory=str(tmp_path))
    assert code == 0
    assert "CANNOT SEE" in report


def test_a_fetch_that_fails_still_measures_against_the_local_ref(tmp_path):
    upstream, up_run = _repo(tmp_path, "upstream")
    import subprocess
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(upstream), str(work)], check=True)
    (upstream / "f").write_text("two")
    up_run("commit", "-qam", "two")
    subprocess.run(["git", "-C", str(work), "fetch", "-q", "origin", "main"], check=True)
    # Break the remote so the fetch inside source_revision cannot succeed.
    subprocess.run(["git", "-C", str(work), "remote", "set-url", "origin",
                    str(tmp_path / "gone")], check=True)

    code, report = preflight.source_revision(directory=str(work))
    assert code == 2, report
    assert "fetch failed" in report
    assert "BEHIND origin/main by 1 commit(s)" in report


def test_an_unresolvable_origin_main_is_unreadable_and_never_reads_as_clean(tmp_path):
    # Reachable, not theoretical: a checkout whose remote is gone, or a repo
    # whose default branch is not `main`. My reviewer mutated this branch from
    # 1 to 0 and the suite stayed green -- the one contract the whole module
    # rests on, untested.
    import subprocess

    path, run = _repo(tmp_path, "solo")
    code, report = preflight.source_revision(directory=str(path))
    assert code == 1, report
    assert "UNREADABLE" in report
    assert "origin/main could not be resolved" in report


def test_git_answering_something_that_is_not_two_counts_is_unreadable(tmp_path, monkeypatch):
    import subprocess

    path, _ = _repo(tmp_path, "solo")
    real = subprocess.run

    def fake(args, **kwargs):
        if "--left-right" in args:
            return subprocess.CompletedProcess(args, 0, stdout="not two numbers\n", stderr="")
        return real(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake)
    code, report = preflight.source_revision(directory=str(path), fetch=False)
    assert code == 1, report
    assert "UNREADABLE" in report


def test_a_behind_branch_with_its_own_work_is_told_to_merge_not_to_check_main_out(tmp_path):
    import subprocess

    upstream, up_run = _repo(tmp_path, "upstream")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(upstream), str(work)], check=True)
    run = lambda *a: subprocess.run(["git", "-C", str(work)] + list(a),
                                    capture_output=True, text=True, check=True)
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    run("checkout", "-qb", "feature")
    (work / "mine").write_text("mine")
    run("add", "-A")
    run("commit", "-qm", "mine")
    (upstream / "f").write_text("two")
    up_run("commit", "-qam", "two")

    code, report = preflight.source_revision(directory=str(work))
    assert code == 2, report
    assert "1 ahead" in report
    assert "git merge origin/main" in report
    assert "git checkout main" not in report


def test_a_stale_tree_names_the_check_modules_it_is_missing(tmp_path):
    import subprocess

    upstream, up_run = _repo(tmp_path, "upstream")
    (upstream / "tools").mkdir()
    (upstream / "tools" / "old_check.py").write_text("")
    up_run("add", "-A")
    up_run("commit", "-qm", "tools")
    work = tmp_path / "work"
    subprocess.run(["git", "clone", "-q", str(upstream), str(work)], check=True)
    (upstream / "tools" / "nas_watch.py").write_text("")
    up_run("add", "-A")
    up_run("commit", "-qm", "add nas_watch")

    code, report = preflight.source_revision(directory=str(work))
    assert code == 2, report
    assert "Missing from this tree: nas_watch" in report
    assert "old_check" not in report


# --- a clean check that could not judge part of its own scope -----------------
# Cycle 646 found `nas_watch` exiting 0 with "2 service(s) of 2" over a box with
# three code-execution surfaces, and repaired it by pushing the missing digit
# into that check's summary line so the collapse would carry it. The constraint
# it was satisfying lives in `summary_line` and nowhere a check's author looks,
# so the collapse itself is what stops hiding the caveat now.

NZBGET = ("NOT JUDGED  nzbget's extension list -- reading it needs NZBGET_USER "
          "and NZBGET_PASS in this pod's environment and neither is set.")


def test_a_clean_check_still_says_what_it_could_not_judge():
    code, text = render([(
        "nas_watch", 0,
        "locked: nzbget's control port answered and refused without a credential\n"
        + NZBGET + "\n"
        "Judged the code-execution surface of 2 service(s) of 3, read over the SSH hop.\n",
        2.3,
    )])
    assert code == 0
    assert NZBGET in text
    # The rest of the body is still collapsed away -- this is not --verbose.
    assert "locked: nzbget's control port" not in text


def test_the_caveat_marker_has_to_start_the_line():
    # A footnote *about* the exit contract is prose, not a thing the sweep
    # failed to judge. Measured over a real 25-check sweep: anchoring and
    # containment select exactly the same lines, so this only ever narrows.
    _, text = render([(
        "preflight_like", 0,
        "An unreadable check is UNREADABLE and never reads as clean.\n"
        "Swept 3 repo(s).\n",
        0.2,
    )])
    assert "never reads as clean" not in text


def test_every_caveat_prints_and_there_is_no_cap_on_them():
    body = "\n".join(f"CANNOT SEE  ActionsSecret/secret-{n}: this account cannot list that kind"
                     for n in range(6)) + "\nRead 37 managed resource(s).\n"
    _, text = render([("crossplane_health", 0, body, 11.9)])
    for n in range(6):
        assert f"ActionsSecret/secret-{n}" in text


def test_a_caveat_on_a_dirty_check_is_not_printed_twice():
    # exit 2 reproduces the whole body already; repeating the caveat under the
    # row would say the same thing in two places and mean nothing extra.
    body = "PIN DRIFT -- 1 pinned version(s) are behind.\n" + NZBGET + "\nJudged 112 pin(s).\n"
    _, text = render([("pin_drift", 2, body, 62.7)])
    assert text.count(NZBGET) == 1


def test_the_footer_counts_the_clean_checks_that_could_not_judge_everything():
    _, text = render([
        ("nas_watch", 0, NZBGET + "\nJudged 2 service(s) of 3.\n", 2.3),
        ("doc_integrity", 0, "Whole. Swept 11 document(s)\n", 10.2),
    ])
    assert "1 check(s) exited 0 over a scope they could not fully judge" in text


def test_a_sweep_with_nothing_unjudged_says_nothing_about_caveats():
    _, text = render([("doc_integrity", 0, "Whole. Swept 11 document(s)\n", 10.2)])
    assert "could not fully judge" not in text


def test_verbose_does_not_report_every_clean_check_as_unclean():
    # `--verbose` puts every check in the reproduce list, and the footer used to
    # count that list: a sweep of 25 checks with 8 findings printed "25 check(s)
    # did not come back clean", which is false in the report's own summary line.
    results = [
        ("doc_integrity", 0, "Whole. Swept 11 document(s)\n", 10.2),
        ("pin_drift", 2, "PIN DRIFT -- 1 pinned version(s) are behind.\n", 62.7),
    ]
    out = io.StringIO()
    preflight.render(results, stream=out, verbose=True)
    assert "1 check(s) did not come back clean" in out.getvalue()


def test_a_one_line_caveat_is_not_printed_under_itself():
    # `source_revision` with no git checkout exits 0 and its whole report is
    # one `CANNOT SEE` line, so the collapsed summary and the caveat are the
    # same sentence. Printing it indented under itself says nothing new.
    only = "CANNOT SEE -- /x is not a usable git checkout, so there is 1 unknown."
    _, text = render([("source_revision", 0, only + "\n", 0.1)])
    assert text.count(only) == 1
    assert "could not fully judge" not in text


# --- A standing finding stops being reproduced every sweep --------------------
#
# The owner, comments board 2026-08-30: "It is a bit heavy to check this system
# every day... So spending that many tokens is wasteful." Eight of the twenty-six
# checks exit non-zero on a normal morning and every one is a finding this loop
# cannot close, reproduced in full at a 40-minute cadence.

HOUR = 3600.0

# server1's memory finding, with the number that moves on every single run.
MEM = ("NODE OUT OF MEMORY — server1 has {}Mi available of 7746Mi.\n"
       "Read 51 Pod(s) from the live cluster.\n")


def render_with(results, state, now, keep=None, verbose=False):
    out = io.StringIO()
    code = preflight.render(results, stream=out, verbose=verbose,
                            state=state, now=now, keep=keep)
    return code, out.getvalue()


def test_a_finding_seen_for_the_first_time_prints_in_full():
    keep = {}
    code, text = render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                             state={}, now=1000.0, keep=keep)
    assert code == 2
    assert "===== workload_health" in text and "NODE OUT OF MEMORY" in text
    assert "UNCHANGED" not in text
    assert keep["workload_health"]["printed_at"] == 1000.0


def test_the_same_finding_next_sweep_collapses_to_one_line():
    first = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state={}, now=1000.0, keep=first)
    code, text = render_with([("workload_health", 2, MEM.format(1853), 4.1)],
                             state=first, now=1000.0 + HOUR, keep={})
    # The verdict is untouched -- this collapses the text, never the finding.
    assert code == 2
    assert "workload_health" in text and "ACT" in text
    assert "UNCHANGED since" in text
    assert "===== workload_health" not in text
    assert "1 standing finding(s) were not reproduced" in text
    # And the footer stops claiming the full output is above, because it is not.
    assert "0 of them printed in full above" in text


def test_only_the_number_moving_still_counts_as_unchanged():
    # This is the case the whole mechanism exists for: an exact comparison
    # would say "changed" every run and never collapse anything at all.
    first = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state={}, now=1000.0, keep=first)
    _, text = render_with([("workload_health", 2, MEM.format(1402), 4.3)],
                          state=first, now=1000.0 + HOUR, keep={})
    assert "UNCHANGED since" in text and "===== workload_health" not in text


def test_an_extra_line_is_never_collapsed():
    # A second alert, a third unhealthy pod, a newly missing module: they all
    # add a line, and the line count is compared exactly for that reason.
    first = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state={}, now=1000.0, keep=first)
    _, text = render_with(
        [("workload_health", 2, MEM.format(1853) + "NODE OUT OF MEMORY — server2.\n", 4.3)],
        state=first, now=1000.0 + HOUR, keep={})
    assert "UNCHANGED" not in text
    assert "===== workload_health" in text and "server2" in text


def test_the_full_text_comes_back_after_the_reprint_window():
    first = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state={}, now=1000.0, keep=first)
    keep = {}
    _, text = render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                          state=first, now=1000.0 + preflight.REPRINT_HOURS * HOUR + 1,
                          keep=keep)
    assert "UNCHANGED" not in text
    assert "===== workload_health" in text
    assert keep["workload_health"]["printed_at"] == 1000.0 + preflight.REPRINT_HOURS * HOUR + 1
    # First seen is preserved across the reprint: it is when the finding
    # started, not when it was last shouted about.
    assert keep["workload_health"]["first_seen"] == 1000.0


def test_a_collapsed_sweep_does_not_reset_the_reprint_clock():
    # Otherwise a finding collapses once, that collapse marks it "printed now",
    # and it never prints in full again -- a guard that reports itself working.
    first = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state={}, now=1000.0, keep=first)
    keep = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state=first, now=1000.0 + HOUR, keep=keep)
    assert keep["workload_health"]["printed_at"] == 1000.0


def test_verbose_prints_a_collapsed_finding_in_full():
    first = {}
    render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                state={}, now=1000.0, keep=first)
    _, text = render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                          state=first, now=1000.0 + HOUR, keep={}, verbose=True)
    assert "===== workload_health" in text and "UNCHANGED" not in text


# A message from the owner waiting in the Telegram bridge, in the shape
# `tools.telegram_inbox` prints it: the summary line, then the message itself.
WAITING = ("1 message(s) waiting for an answer.\n"
           "  2026-09-04 14:25 Oslo  #699740089  keep the telegram messages short\n")


def test_a_waiting_telegram_message_is_never_collapsed():
    # The whole point of the exemption. An unanswered message is unchanged
    # BECAUSE nobody has dealt with it, so "unchanged" is the one reason that
    # must not stop it printing -- the owner waited two and three quarter hours on
    # 2026-09-04 for exactly this.
    first = {}
    render_with([("telegram_inbox", 2, WAITING, 0.2)],
                state={}, now=1000.0, keep=first)
    code, text = render_with([("telegram_inbox", 2, WAITING, 0.2)],
                             state=first, now=1000.0 + HOUR, keep={})
    assert code == 2
    assert "===== telegram_inbox" in text
    assert "keep the telegram messages short" in text
    assert "UNCHANGED" not in text
    assert "standing finding" not in text


def test_the_exemption_is_by_name_and_nothing_else():
    # The guard has to be able to fail: the same output under a check that is
    # not exempt collapses on the second sweep, so the test above is measuring
    # NEVER_COLLAPSE rather than something incidental about this text.
    first = {}
    render_with([("workload_health", 2, WAITING, 0.2)],
                state={}, now=1000.0, keep=first)
    _, text = render_with([("workload_health", 2, WAITING, 0.2)],
                          state=first, now=1000.0 + HOUR, keep={})
    assert "UNCHANGED since" in text and "===== workload_health" not in text


def test_an_exempt_check_records_that_it_printed_now():
    # It prints in full every sweep, so its record has to say so. A frozen
    # printed_at would collapse it on the first sweep after anyone took it
    # back out of NEVER_COLLAPSE.
    first = {}
    render_with([("telegram_inbox", 2, WAITING, 0.2)],
                state={}, now=1000.0, keep=first)
    keep = {}
    render_with([("telegram_inbox", 2, WAITING, 0.2)],
                state=first, now=1000.0 + HOUR, keep=keep)
    assert keep["telegram_inbox"]["printed_at"] == 1000.0 + HOUR


def test_the_exempt_checks_are_named_here():
    # Named here rather than left implicit: the set holds checks whose finding
    # is someone waiting on me AND whose wait is the number the fingerprint
    # blinds. Today that is two.
    assert preflight.NEVER_COLLAPSE == frozenset({"telegram_inbox", "recap_health"})


# `tools.recap_health` on a stale card, in the shape it prints: the verdict
# line carrying how long it has been stale, then what the card still covers.
def _stale_recap(hours, cycles_since):
    return (f"STALE    the recap card was written at 01:43 Oslo, {hours}h ago, "
            f"past the 3.0h threshold — the Journal page is already telling him so.\n"
            f"         it covers cycles 981-1011 and carries 6 bullet(s).\n"
            f"         {cycles_since} cycle(s) have filed a journal entry since then.\n")


def test_a_recap_going_staler_is_one_fingerprint():
    # Why the exemption is needed at all, and it is not obvious from the text:
    # `finding_shape` blinds digits, so the hour count that says how bad this
    # has got is the one thing the collapse cannot see. Three hours stale and
    # fifty-three hours stale are the same finding to it.
    assert (preflight.finding_shape(_stale_recap("3.1", 2))
            == preflight.finding_shape(_stale_recap("52.9", 175)))


def test_a_stale_recap_card_is_never_collapsed():
    # 2026-09-08: the card had been stale 52.9h, the check raised on every
    # sweep through it, and every sweep printed one UNCHANGED line instead --
    # so the owner found it before I did and filed it 🔴 Immediately.
    first = {}
    render_with([("recap_health", 2, _stale_recap("3.1", 2), 0.2)],
                state={}, now=1000.0, keep=first)
    code, text = render_with([("recap_health", 2, _stale_recap("52.9", 175), 0.2)],
                             state=first, now=1000.0 + HOUR, keep={})
    assert code == 2
    assert "===== recap_health" in text
    assert "52.9h ago" in text
    assert "UNCHANGED" not in text
    assert "standing finding" not in text


def test_a_clean_check_is_never_touched_by_any_of_this():
    _, text = render_with([("doc_integrity", 0, "Whole. Swept 11 document(s)\n", 0.4)],
                          state={}, now=1000.0, keep={})
    assert "UNCHANGED" not in text and "standing finding" not in text


def test_no_state_means_every_finding_prints():
    _, text = render_with([("workload_health", 2, MEM.format(1853), 4.3)],
                          state=None, now=1000.0)
    assert "===== workload_health" in text


def test_an_unreadable_state_file_prints_everything_rather_than_failing(tmp_path):
    bad = tmp_path / "state.json"
    bad.write_text("{not json")
    assert preflight.load_state(str(bad)) == {}
    assert preflight.load_state(str(tmp_path / "nothing-here.json")) == {}


def test_state_survives_a_round_trip(tmp_path):
    path = str(tmp_path / "state.json")
    preflight.save_state({"a": {"shape": "x", "lines": 2}}, path)
    assert preflight.load_state(path) == {"a": {"shape": "x", "lines": 2}}


def test_saving_into_an_unwritable_place_is_not_fatal(tmp_path):
    preflight.save_state({"a": 1}, str(tmp_path / "no" / "such" / "dir" / "s.json"))


def test_the_line_breaks_are_part_of_the_fingerprint():
    # Without a separator in the join, "AB / C" and "A / BC" hash the same --
    # two different findings reading as one unchanged one.
    assert preflight.finding_shape("AB\nC\n") != preflight.finding_shape("A\nBC\n")


def test_a_one_line_finding_is_not_repeated_underneath_itself():
    # source_variant of source_revision: the whole report is the summary row,
    # so there is nothing left to reproduce and nothing to collapse either.
    _, text = render_with([("source_revision", 2, "BEHIND origin/main by 1 commit(s).", 0.6)],
                          state={}, now=1000.0, keep={})
    assert text.count("BEHIND origin/main by 1 commit(s).") == 1
    assert "UNCHANGED" not in text and "standing finding" not in text
    assert "===== source_revision" not in text


def test_no_check_prints_a_blind_line_this_report_would_drop():
    """Every shouted "I could not read this" in every check has to be a caveat.

    Cycle 699's reviewer found `host_memory_trend` printing `CANNOT COUNT`,
    which the old whole-phrase prefix list did not match, so that caveat was
    dropped from the collapsed report on an otherwise-clean run. The repair was
    a test inside that one module. This is the same test asked of all 31 checks
    at once, and asking it that way is what turned one line into ten across
    nine modules -- including `CANNOT ATTRIBUTE MEMORY`, the issue #131 line,
    in two separate checks.

    The detector here is deliberately *not* `is_caveat` re-spelled, or it could
    not fail: it anchors on three capitals rather than on the shout, and it
    searches the whole line rather than its opening. So a header that carries
    the marker in its second word -- `SERVICES UNREADABLE`, in four NAS checks
    -- is found here and was not found by the rule under test until today.
    """
    import ast

    blind_word = re.compile(
        r"\b(CANNOT|COULD NOT|UNREADABLE|UNJUDGED|NOT JUDGED|NOT ASKED|NOT READ)\b")
    dropped = []
    for name in preflight.CHECKS:
        path = os.path.join(preflight.tools_dir(), name + ".py")
        with io.open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            for raw in node.value.splitlines():
                line = raw.strip()
                if not re.match(r"^[A-Z]{3,}", line):
                    continue
                if not blind_word.search(line):
                    continue
                if not preflight.is_caveat(line):
                    dropped.append("%s: %s" % (name, line[:80]))
    assert not dropped, (
        "these lines say a check could not judge something and preflight would "
        "not print them:\n" + "\n".join(sorted(set(dropped))))


def test_prose_about_the_exit_contract_is_still_not_a_caveat():
    # The one thing the old prefix rule got right, kept: a sentence that
    # merely mentions the word opens in prose, so its shouted head is one
    # character long and carries no stem.
    assert not preflight.is_caveat(
        "An unreadable check is UNREADABLE and never reads as clean.")
    assert not preflight.is_caveat("Swept 3 repo(s).")


def test_a_marker_in_the_second_word_of_a_header_is_a_caveat():
    # `SERVICES UNREADABLE` is what four NAS checks print, and the prefix list
    # this replaces matched none of them.
    assert preflight.is_caveat(
        "SERVICES UNREADABLE -- the SSH hop exists but no service answered.")
    assert preflight.is_caveat("CANNOT ATTRIBUTE MEMORY — /proc/meminfo says")


# --- on-box vs off-box: which checks survive the failure they are for ------


def test_every_check_that_runs_carries_a_subject_label():
    # source_revision is deliberately out of CHECKS but is a row in the report,
    # so it needs a label too or the two counts do not add up to the sweep.
    assert preflight.unlabelled_checks(preflight.CHECKS) == []
    assert preflight.unlabelled_checks(["source_revision"]) == []


def test_a_check_with_no_subject_label_is_a_hard_error_not_a_skip(capsys, monkeypatch):
    # Precondition: the name resolves to a real module, so the existing
    # NO SUCH CHECK guard cannot be what refuses it. Without this the test
    # passes on a build where SUBJECT is never consulted at all.
    assert preflight.unknown_checks(["doc_integrity"]) == []
    monkeypatch.delitem(preflight.SUBJECT, "doc_integrity")
    assert preflight.main(["--only", "doc_integrity"]) == 1
    err = capsys.readouterr().err
    assert "NO SUBJECT LABEL: doc_integrity" in err
    assert "NO SUCH CHECK" not in err


def test_the_two_labels_are_counted_separately_in_the_footer():
    _, text = render([
        ("host_memory_trend", 0, "server1 has headroom\n", 0.1),
        ("memory_headroom", 0, "pods fit\n", 0.1),
        ("nas_health", 0, "the NAS answers\n", 0.1),
    ])
    assert "1 watch(es) something off this box" in text
    assert "The other 2 run on the box they watch" in text
    assert "nas_health" in text.split("would still answer if server1 died:")[1]


def test_a_row_says_where_its_subject_lives():
    _, text = render([("nas_ports", 0, "8 port(s) open\n", 0.2)])
    line = [ln for ln in text.splitlines() if ln.startswith("nas_ports")][0]
    assert "off-box" in line
    _, text = render([("argocd_health", 0, "13 Application(s)\n", 0.2)])
    line = [ln for ln in text.splitlines() if ln.startswith("argocd_health")][0]
    assert "on-box" in line


def test_the_checks_that_watch_this_box_are_labelled_on_box():
    # The point of the split: everything that watches server1, this cluster or
    # my own record is silenced by the outage it would need to report.
    for name in ("host_memory_trend", "memory_headroom", "heartbeat_health",
                 "cycle_postmortem", "reply_health", "workload_health"):
        assert preflight.SUBJECT[name][0] == "on-box", name
    for name in ("nas_health", "security_alerts", "ci_health", "eol_watch"):
        assert preflight.SUBJECT[name][0] == "off-box", name


def test_alerts_is_the_check_that_runs_with_notify():
    # The pager only exists if something invokes it. This is the one place
    # that does, and a check that quietly lost its flag would page nobody
    # while every other instrument still said the cluster was fine.
    from tools import preflight as pf

    assert pf.CHECK_ARGS["alerts"] == ["--notify"]
    assert "alerts" in pf.CHECKS


def test_credential_recovery_runs_with_notify_too():
    # Cycle 964, on his ask for a five-day Telegram reminder. The login
    # deadline is the one finding in preflight that no cycle can fix, so it
    # is the second thing here allowed to reach outside the cluster. Losing
    # the flag leaves the alarm firing only into output he never reads.
    from tools import preflight as pf

    # Only the CHECK_ARGS entry is asserted. `credential_recovery in CHECKS`
    # was already true before this change, so asserting it here would pass
    # with the change reverted and cover nothing.
    assert pf.CHECK_ARGS["credential_recovery"] == ["--notify"]


def test_a_check_with_no_extra_arguments_is_run_bare(monkeypatch):
    from tools import preflight as pf

    seen = {}

    class Done:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(pf.subprocess, "run", fake_run)
    pf.run_check("alerts")
    assert seen["cmd"][-1] == "--notify"
    pf.run_check("disk_health")
    assert seen["cmd"][-1] == "tools.disk_health"


def test_sweep_stamp_names_the_clock_and_the_checkout():
    # 2026-09-06 02:16 Oslo, the minute cycle 1013 measured the collision.
    line = preflight.sweep_stamp(now=1788653760.0, checkout="/w/agora-persona-runner")
    assert line == "swept 2026-09-06 02:16 Oslo from /w/agora-persona-runner"


def test_sweep_stamp_survives_an_unreadable_checkout():
    # A report with no stamp reads exactly like the leftover this exists to
    # expose, so the line is never dropped -- it says what it could not read.
    line = preflight.sweep_stamp(now=1788653760.0, checkout="")
    assert line.startswith("swept 2026-09-06 02:16 Oslo from ")
    assert "unreadable checkout" in line


def test_report_opens_on_its_own_stamp():
    out = io.StringIO()
    preflight.render([("cli_features", 0, "nothing has moved\n", 0.1)],
                     stream=out, now=1788653760.0)
    first = out.getvalue().splitlines()[0]
    assert first.startswith("swept 2026-09-06 02:16 Oslo from ")
    # Above the table, not inside it: a stamp under the header is a row.
    assert out.getvalue().splitlines()[1].startswith("check")


def test_two_sweeps_a_minute_apart_are_told_apart_by_their_stamp():
    # The failure: two cycles redirect to one path and the reader cannot tell
    # whose bytes they are. Same clock minute, different worktree, and the
    # stamps must still differ.
    mine = preflight.sweep_stamp(now=1788653760.0, checkout="/w/7-111/agora-persona-runner")
    theirs = preflight.sweep_stamp(now=1788653760.0, checkout="/w/7-222/agora-persona-runner")
    assert mine != theirs
    # And the same worktree an hour apart differs too.
    later = preflight.sweep_stamp(now=1788657360.0, checkout="/w/7-111/agora-persona-runner")
    assert later != mine
    assert "03:16 Oslo" in later


NOW = 1788653760.0  # 2026-09-06 02:16 Oslo


def _clean(name, hours_ago, code=0):
    return {name: {"code": code, "ran_at": NOW - hours_ago * 3600.0}}


def test_a_clean_check_inside_its_cadence_is_not_run():
    state = _clean("nas_ports", 2.0)   # weekly
    due, held = preflight.due_and_held(["nas_ports"], state, NOW)
    assert due == []
    assert [n for n, _e, _c in held] == ["nas_ports"]


def test_a_clean_check_past_its_cadence_is_run_again():
    state = _clean("cli_pin", 25.0)    # daily
    due, held = preflight.due_and_held(["cli_pin"], state, NOW)
    assert due == ["cli_pin"]
    assert held == []


def test_a_check_that_did_not_come_back_clean_is_run_every_sweep():
    # The one safety property: a cadence may only ever delay re-confirming good
    # news. An alarm that is ringing is re-measured until it stops, which is
    # also the only way it can ever be seen to clear.
    state = _clean("nas_ports", 0.1, code=2)
    due, held = preflight.due_and_held(["nas_ports"], state, NOW)
    assert due == ["nas_ports"]
    assert held == []
    unreadable = _clean("nas_ports", 0.1, code=1)
    assert preflight.due_and_held(["nas_ports"], unreadable, NOW)[0] == ["nas_ports"]


def test_a_check_with_no_record_at_all_is_run():
    # A fresh state file runs the whole roster. That is the safe direction: an
    # unrun check must never read as one that came back clean.
    due, held = preflight.due_and_held(["nas_ports"], {}, NOW)
    assert due == ["nas_ports"]
    # And an entry that predates this mechanism carries no `ran_at`.
    legacy = {"nas_ports": {"shape": "abc", "printed_at": NOW - 60}}
    assert preflight.due_and_held(["nas_ports"], legacy, NOW)[0] == ["nas_ports"]


def test_a_zero_cadence_check_is_run_however_recently_it_passed():
    state = _clean("workload_health", 0.0)
    assert preflight.CADENCE_HOURS["workload_health"] == 0.0
    due, held = preflight.due_and_held(["workload_health"], state, NOW)
    assert due == ["workload_health"]
    assert held == []


def test_no_state_holds_nothing_back():
    # --verbose and --no-state pass state=None, so there is always one flag
    # that runs the whole roster on demand.
    due, held = preflight.due_and_held(["nas_ports", "cli_pin"], None, NOW)
    assert due == ["nas_ports", "cli_pin"]
    assert held == []


def test_every_check_on_the_roster_has_a_cadence():
    assert preflight.uncadenced_checks(preflight.CHECKS) == []


def test_a_check_with_no_cadence_is_refused_rather_than_defaulted():
    assert preflight.uncadenced_checks(["nas_ports", "invented"]) == ["invented"]


def test_the_never_collapse_checks_run_every_sweep():
    # `telegram_inbox` exits 0 when nobody is waiting, so a cadence would hold
    # it back on exactly the sweeps where the owner has just written.
    for name in preflight.NEVER_COLLAPSE:
        assert preflight.CADENCE_HOURS[name] == 0.0


def test_the_held_block_says_it_is_carrying_nothing_red():
    lines = preflight.held_lines([("nas_ports", {"code": 0, "ran_at": NOW - 7200.0}, 168.0)],
                                 NOW)
    assert "1 check(s) were not run" in lines[0]
    assert "run every sweep regardless" in lines[0]
    assert "nas_ports" in lines[1]
    assert "2.0h ago" in lines[1]
    assert "due again in 166.0h" in lines[1]


def test_nothing_held_prints_nothing():
    assert preflight.held_lines([], NOW) == []


def test_the_report_names_every_held_check():
    out = io.StringIO()
    preflight.render([("cli_features", 0, "nothing has moved\n", 0.1)],
                     stream=out, now=NOW,
                     held=[("nas_ports", {"code": 0, "ran_at": NOW - 3600.0}, 168.0)])
    text = out.getvalue()
    assert "Ran 1 check(s)" in text
    assert "1 check(s) were not run" in text
    assert "nas_ports" in text


def test_every_result_lands_in_the_record_it_is_read_from_next_sweep():
    # `due_and_held` reads `code` and `ran_at`. A verdict that never reached
    # the record would make the next sweep treat that check as never-run.
    keep = {}
    preflight.render([("cli_features", 0, "nothing has moved\n", 0.1),
                      ("cli_pin", 2, "BEHIND -- 3 release(s)\nand a second line\n", 0.2)],
                     stream=io.StringIO(), state={}, keep=keep, now=NOW)
    assert keep["cli_features"]["code"] == 0
    assert keep["cli_features"]["ran_at"] == NOW
    assert keep["cli_pin"]["code"] == 2
    assert keep["cli_pin"]["ran_at"] == NOW
    # and the collapse record it already kept is still there beside them
    assert "shape" in keep["cli_pin"]


def test_a_collapsed_finding_keeps_its_verdict_in_the_record():
    # The repeat collapse writes its own entry over the top; the verdict has to
    # survive that, or a standing ACT would look clean to the next sweep and
    # get held back.
    output = "BEHIND -- 3 release(s)\nand a second line\n"
    first = {}
    preflight.render([("cli_pin", 2, output, 0.2)], stream=io.StringIO(),
                     state={}, keep=first, now=NOW)
    second = {}
    text = io.StringIO()
    preflight.render([("cli_pin", 2, output, 0.2)], stream=text,
                     state=first, keep=second, now=NOW + 600.0)
    assert "UNCHANGED since" in text.getvalue()
    assert second["cli_pin"]["code"] == 2
    assert second["cli_pin"]["ran_at"] == NOW + 600.0


def test_a_record_stamped_in_the_future_is_due_rather_than_held():
    # Concurrent cycles share one record and not one clock. A sweep whose
    # `ran_at` is ahead of now must not hold a check back for the skew.
    state = _clean("nas_ports", -3.0)
    due, held = preflight.due_and_held(["nas_ports"], state, NOW)
    assert due == ["nas_ports"]
    assert held == []


def test_a_second_sweep_holds_back_what_the_first_one_cleared(tmp_path, monkeypatch, capfd):
    # capfd, not capsys: `render` binds `stream=sys.stdout` as a default argument
    # at import time, so it writes to the real descriptor and capsys sees nothing.
    # The whole mechanism, end to end and across two runs. The first sweep has
    # no record and runs everything; the second must run only what is due.
    # `main` is the only place the held records are carried into the file that
    # the next sweep reads, so a unit test of `due_and_held` cannot see this.
    monkeypatch.setattr(preflight, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(preflight, "source_revision", lambda **kw: (0, "up to date\n"))
    monkeypatch.setattr(preflight, "run_check",
                        lambda name: (name, 0, f"{name} swept 1 thing\n", 0.1))

    # Not `--only`: naming a check explicitly is a request to run that check,
    # so it deliberately ignores the cadence. This is the ordinary sweep.
    monkeypatch.setattr(preflight, "CHECKS", ("nas_ports", "workload_health"))
    assert preflight.main([]) == 0
    first = capfd.readouterr().out
    assert "Ran 3 check(s)" in first          # both, plus source_revision
    assert "were not run" not in first

    assert preflight.main([]) == 0
    second = capfd.readouterr().out
    # nas_ports is weekly and came back clean a moment ago; workload_health is
    # every-sweep and runs again.
    assert "Ran 2 check(s)" in second
    assert "workload_health" in second
    assert "1 check(s) were not run" in second

    # And the third sweep must still hold it: the record the second one wrote
    # has to carry the held check forward, not drop it.
    assert preflight.main([]) == 0
    assert "1 check(s) were not run" in capfd.readouterr().out


def test_all_runs_the_whole_roster_even_when_nothing_is_due(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(preflight, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(preflight, "source_revision", lambda **kw: (0, "up to date\n"))
    monkeypatch.setattr(preflight, "run_check",
                        lambda name: (name, 0, f"{name} swept 1 thing\n", 0.1))
    monkeypatch.setattr(preflight, "CHECKS", ("nas_ports",))
    preflight.main([])
    capfd.readouterr()
    preflight.main(["--all"])
    out = capfd.readouterr().out
    assert "Ran 2 check(s)" in out
    assert "were not run" not in out


def test_naming_a_check_runs_it_whatever_its_cadence(tmp_path, monkeypatch, capfd):
    # `--only nas_ports` is a request to run nas_ports. Answering it with "not
    # due" would make the flag useless on exactly the check you asked about.
    monkeypatch.setattr(preflight, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(preflight, "source_revision", lambda **kw: (0, "up to date\n"))
    monkeypatch.setattr(preflight, "run_check",
                        lambda name: (name, 0, f"{name} swept 1 thing\n", 0.1))
    monkeypatch.setattr(preflight, "CHECKS", ("nas_ports",))
    preflight.main([])
    capfd.readouterr()
    preflight.main(["--only", "nas_ports"])
    out = capfd.readouterr().out
    assert "Ran 2 check(s)" in out
    assert "were not run" not in out


def test_only_does_not_wipe_the_rest_of_the_record(tmp_path, monkeypatch, capfd):
    # `save_state` replaces the whole file. A sweep of one check must not
    # delete every other check's record on the way past -- that would reset
    # both the cadence and the repeat collapse for the whole roster.
    monkeypatch.setattr(preflight, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(preflight, "source_revision", lambda **kw: (0, "up to date\n"))
    monkeypatch.setattr(preflight, "run_check",
                        lambda name: (name, 0, f"{name} swept 1 thing\n", 0.1))
    monkeypatch.setattr(preflight, "CHECKS", ("nas_ports", "cli_pin"))
    preflight.main([])
    capfd.readouterr()
    preflight.main(["--only", "cli_pin"])
    capfd.readouterr()
    kept = preflight.load_state(str(tmp_path / "state.json"))
    # source_revision runs outside the roster on every sweep, so it is there too.
    assert set(kept) == {"nas_ports", "cli_pin", "source_revision"}
    assert kept["nas_ports"]["code"] == 0


def test_a_solo_check_never_runs_while_another_check_is_running(tmp_path, monkeypatch, capfd):
    # The defect this exists for: `cpu_throttle` samples a 20s window of the
    # cluster's CPU throttling, and the sweep around it is load on the two
    # containers it then names -- nova-site, which `reply_health` asks for 30
    # cycle threads, and the bridge pod every check runs in. Measured Cycle
    # 1061 two minutes apart: 89.4%/53.0% inside the pool, NOT JUDGED/0.0%
    # alone. A unit test of the pool cannot see that, so this asserts the one
    # thing that makes the number honest -- nothing else was in flight.
    import threading
    monkeypatch.setattr(preflight, "STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setattr(preflight, "source_revision", lambda **kw: (0, "up to date\n"))
    monkeypatch.setattr(preflight, "CHECKS", ("nas_ports", "cpu_throttle", "workload_health"))
    monkeypatch.setattr(preflight, "SOLO", ("cpu_throttle",))

    lock = threading.Lock()
    in_flight = set()
    # Every call, not the last one per name. Holding one entry per name lets a
    # check that ran twice -- once inside the pool and once after it -- report
    # the quiet second run and hide the overlapping first, which is exactly
    # what the mutation that drops the `not in SOLO` filter does.
    calls = []

    # The two pooled checks meet here instead of sleeping past each other.
    # A `time.sleep(0.05)` used to stand in for the overlap, and on 2026-09-08
    # it lost the race on a GitHub runner: the first thread entered, slept and
    # left before the second was scheduled, so the precondition below read
    # "this sweep was serial" and main went red on a green change. The pool is
    # six workers wide and both of these are submitted to it, so a barrier of
    # two is always met -- and if it ever is not, this raises rather than
    # passing. Named explicitly rather than via `SOLO`, so the mutation that
    # drops the `not in SOLO` filter changes what the assertions see and not
    # what the barrier does.
    pooled = ("nas_ports", "workload_health")
    overlap = threading.Barrier(len(pooled), timeout=10)

    def fake(name):
        with lock:
            calls.append((name, set(in_flight)))
            in_flight.add(name)
        if name in pooled:
            overlap.wait()
        with lock:
            in_flight.discard(name)
        return (name, 0, f"{name} swept 1 thing\n", 0.1)

    monkeypatch.setattr(preflight, "run_check", fake)
    assert preflight.main(["--all"]) == 0

    solo_calls = [alongside for name, alongside in calls if name == "cpu_throttle"]
    assert len(solo_calls) == 1
    assert solo_calls[0] == set()
    # The precondition: the pool really did overlap the other two, so "nothing
    # was alongside cpu_throttle" is a fact about SOLO and not about a sweep
    # that happened to be serial. The barrier above is what makes this a fact
    # rather than a coin toss -- the second thread cannot record an empty set,
    # because the first is still standing at the barrier when it does.
    assert any(alongside for name, alongside in calls if name != "cpu_throttle")

    out = capfd.readouterr().out
    assert "Ran 4 check(s)" in out
    assert "cpu_throttle" in out


def test_every_solo_check_is_on_the_roster():
    # A name in SOLO that is not in CHECKS is a check held out of the pool and
    # then never run at all -- `main` only ever iterates `names`.
    assert set(preflight.SOLO) <= set(preflight.CHECKS)
