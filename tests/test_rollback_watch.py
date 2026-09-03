"""Tests for tools/rollback_watch.py.

The failure this guards against is the one the tool exists to end: a report
that says "the watchdog has not fired" because it cannot recognise a revert,
which is indistinguishable from the truth. So the detector is driven from the
*actual* message platform-config's `revert_message()` produces, not from a
string written here to match the matcher.
"""

import json

from tools import rollback_watch as rw


#: The body platform-config's `revert_message()` writes, reproduced here as the
#: input under test. A message invented to satisfy `is_automatic_revert` would
#: make every test below vacuous -- the point is that this shape, which is what
#: lands on the branch, is recognised.
REVERT_BODY = (
    'Revert "Update image to sha256:deadbeef"\n\n'
    "Automatic rollback: this digest deployed and the pod never came up.\n"
    "Reverts aaaaaaaaaaaa.\n\n"
    "Measured at 2026-09-03T12:00:00Z: 0 available replicas, "
    "agora-persona-runner-abc/runner restarts=9 CrashLoopBackOff, and -config HEAD "
    "is a 25.0m-old digest update\n\n"
    "Opened by the deploy-rollback CronJob, not by a person. At most one\n"
    "automatic revert happens per incident -- if this did not fix it, the\n"
    "next run refuses because HEAD is now a revert.\n\n"
    "Automatic-Rollback: deploy-rollback\n"
)

DIGEST_BODY = "Update image to sha256:cafebabe"


def _commit(message, sha="a" * 40, date="2026-09-03T10:00:00Z"):
    return {"sha": sha, "message": message, "date": date}


def test_a_real_revert_commit_is_recognised():
    assert rw.is_automatic_revert(REVERT_BODY) is True


def test_an_ordinary_digest_commit_is_not():
    assert rw.is_automatic_revert(DIGEST_BODY) is False


def test_a_commit_that_merely_mentions_the_trailer_is_not_a_revert():
    """The whole reason the marker is a line: a journal entry or a PR body
    quoting `Automatic-Rollback: deploy-rollback` mid-sentence must not read as
    the watchdog having fired."""
    mention = ("Report on the watchdog\n\n"
               "It stamps Automatic-Rollback: deploy-rollback on what it reverts.\n")
    assert rw.is_automatic_revert(mention) is False


def test_a_revert_from_before_the_trailer_is_still_found():
    legacy = REVERT_BODY.replace("\nAutomatic-Rollback: deploy-rollback\n", "\n")
    assert rw.TRAILER not in legacy
    assert rw.is_automatic_revert(legacy) is True


def test_the_reason_is_pulled_out_of_the_commit():
    reason = rw.reason_of(REVERT_BODY)
    assert reason.startswith("Measured at 2026-09-03T12:00:00Z:")
    assert "CrashLoopBackOff" in reason, "the crash evidence is the point of the record"


def test_a_legacy_revert_reports_no_reason_rather_than_inventing_one():
    legacy = "\n".join(l for l in REVERT_BODY.splitlines()
                       if not l.startswith("Measured at "))
    assert rw.reason_of(legacy) is None


def test_a_revert_at_head_is_pending():
    commits = [_commit(REVERT_BODY), _commit(DIGEST_BODY, sha="b" * 40)]
    reverts, pending = rw.judge(commits)
    assert pending is True
    assert len(reverts) == 1


def test_a_revert_a_later_deploy_has_superseded_is_history():
    """The raising rule is positional, and this is why: once CI has pushed a new
    digest on top, somebody shipped a fix and the incident is closed."""
    commits = [_commit(DIGEST_BODY, sha="c" * 40), _commit(REVERT_BODY)]
    reverts, pending = rw.judge(commits)
    assert pending is False
    assert len(reverts) == 1, "it is still counted -- 'how often' is the other half of the ask"


def test_the_report_names_the_pod_and_the_time_when_a_revert_stands():
    commits = [_commit(REVERT_BODY)]
    reverts, pending = rw.judge(commits)
    report = rw.format_report(commits, reverts, pending, None)
    assert "REVERT STANDING" in report
    assert "agora-persona-runner-abc/runner" in report
    assert "2026-09-03 12:00 Oslo" in report, "GitHub answers UTC and this loop writes Oslo"


def test_a_clean_window_says_how_far_back_it_looked():
    commits = [_commit(DIGEST_BODY, date="2026-09-01T08:00:00Z")]
    reverts, pending = rw.judge(commits)
    report = rw.format_report(commits, reverts, pending, None)
    assert "0 automatic revert(s)" in report
    assert "2026-09-01 10:00 Oslo" in report


def test_an_unreadable_repo_is_not_a_clean_window():
    report = rw.format_report([], [], False, "HTTP 404")
    assert "CANNOT SEE" in report
    assert "not the same as no reverts" in report


def test_main_exits_2_only_when_a_revert_stands(monkeypatch, capsys):
    page = json.dumps([
        {"sha": "a" * 40,
         "commit": {"message": REVERT_BODY, "committer": {"date": "2026-09-03T12:00:00Z"}}},
    ])

    monkeypatch.setattr(rw, "_gh", lambda args: (0, page, ""))
    assert rw.main([]) == 2

    clean = json.dumps([
        {"sha": "b" * 40,
         "commit": {"message": DIGEST_BODY, "committer": {"date": "2026-09-03T12:00:00Z"}}},
    ])
    monkeypatch.setattr(rw, "_gh", lambda args: (0, clean, ""))
    assert rw.main([]) == 0


def test_a_failing_gh_exits_1_rather_than_0(monkeypatch):
    monkeypatch.setattr(rw, "_gh", lambda args: (1, "", "gh: HTTP 403"))
    assert rw.main([]) == 1


def test_an_empty_commit_list_exits_1(monkeypatch):
    """No commits at all is no instrument, not a clean repo."""
    monkeypatch.setattr(rw, "_gh", lambda args: (0, "[]", ""))
    assert rw.main([]) == 1


def test_it_stops_paging_once_a_short_page_comes_back(monkeypatch):
    calls = []

    def fake(args):
        calls.append(args)
        return 0, json.dumps([
            {"sha": "a" * 40,
             "commit": {"message": DIGEST_BODY, "committer": {"date": "2026-09-03T12:00:00Z"}}},
        ]), ""

    monkeypatch.setattr(rw, "_gh", fake)
    commits, error = rw.read_commits()
    assert error is None
    assert len(calls) == 1, "a page shorter than per_page is the end of the history"
    assert len(commits) == 1
