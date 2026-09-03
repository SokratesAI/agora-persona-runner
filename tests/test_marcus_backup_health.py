"""Tests for tools/marcus_backup_health.py.

The failure this guards against is the one the tool exists to end: a green
report that means "nothing changed" when it should mean "the job is dead".
Both look like an unchanged repository from outside, so every test below drives
the judgement off a real commit *date* rather than off a message, and the
threshold is exercised from both sides of its own boundary.

The `path=` in the API request is asserted directly. The whole design rests on
reading `last-run.txt` rather than matching the job's commit subject, and a
request that silently dropped the path filter would return the seed commit and
report a healthy backup on a repo the job has never written to.
"""

import datetime
import json

from tools import marcus_backup_health as mb


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

#: The commit the job's own `git commit -q -m` produces, reproduced as input.
SUBJECT = "[backup] marcus state rev 2 at 2026-09-03 11:12Z [skip ci]"


def _commit(date, subject=SUBJECT, sha="b" * 40):
    return {"sha": sha, "subject": subject, "date": date}


def _api(payload, code=0, err="", seen=None):
    def run(args):
        if seen is not None:
            seen.append(args)
        return code, json.dumps(payload) if payload is not None else "", err
    return run


def test_a_backup_written_an_hour_ago_is_fresh():
    verdict, age = mb.judge(_commit("2026-09-03T11:00:00Z"), NOW)
    assert verdict == "fresh"
    assert round(age, 2) == 1.0


def test_a_backup_just_inside_the_threshold_is_still_fresh():
    # 25h59m -- the worst legal gap is just under 24h plus the deliberate slack.
    verdict, _ = mb.judge(_commit("2026-09-02T10:01:00Z"), NOW)
    assert verdict == "fresh"


def test_a_backup_past_the_threshold_is_stale():
    verdict, age = mb.judge(_commit("2026-09-02T09:00:00Z"), NOW)
    assert verdict == "stale"
    assert age > mb.STALE_AFTER_HOURS


def test_the_threshold_is_at_least_a_full_day():
    # The stamp carries a date, so two successful runs can be a shade under 24
    # hours apart legitimately. A threshold below that reports a working job.
    assert mb.STALE_AFTER_HOURS >= 24


def test_no_stamp_commit_at_all_is_never_rather_than_stale():
    verdict, age = mb.judge(None, NOW)
    assert verdict == "never"
    assert age is None


def test_an_unparseable_date_is_not_reported_as_fresh():
    verdict, _ = mb.judge(_commit("not a date"), NOW)
    assert verdict == "unreadable"


def test_the_commit_read_is_filtered_to_the_stamp_file():
    seen = []
    commit, error = mb.read_stamp_commit(run=_api([
        {"sha": "c" * 40, "commit": {"message": SUBJECT,
                                     "committer": {"date": "2026-09-03T11:12:36Z"}}}
    ], seen=seen))
    assert error is None
    assert commit["date"] == "2026-09-03T11:12:36Z"
    assert commit["subject"] == SUBJECT
    request = seen[0][-1]
    assert f"path={mb.RUN_FILE}" in request
    assert mb.BACKUP_REPO in request


def test_an_empty_commit_list_is_never_backed_up_and_not_an_error():
    commit, error = mb.read_stamp_commit(run=_api([]))
    assert commit is None
    assert error is None


def test_a_gh_failure_is_an_error_and_not_an_empty_history():
    commit, error = mb.read_stamp_commit(run=_api(None, code=1, err="gh: Not Found (HTTP 404)"))
    assert commit is None
    assert error == "gh: Not Found (HTTP 404)"


def test_unparseable_json_is_an_error():
    def run(args):
        return 0, "<html>not json</html>", ""
    commit, error = mb.read_stamp_commit(run=run)
    assert commit is None
    assert "could not parse" in error


def test_the_stale_report_names_the_age_and_the_commit():
    commit = _commit("2026-09-01T09:00:00Z")
    verdict, age = mb.judge(commit, NOW)
    report = mb.format_report(commit, verdict, age, None)
    assert "BACKUP STALE" in report
    assert commit["sha"][:12] in report
    assert SUBJECT in report


def test_the_never_report_does_not_claim_an_age():
    report = mb.format_report(None, "never", None, None)
    assert "NEVER BACKED UP" in report
    assert "STALE" not in report


def test_an_unreadable_repo_is_never_reported_as_healthy():
    report = mb.format_report(None, None, None, "gh: Not Found (HTTP 404)")
    assert "CANNOT SEE" in report
    assert "not the same as a healthy backup" in report


def test_the_fresh_report_prints_the_age_in_oslo_time():
    commit = _commit("2026-09-03T11:00:00Z")
    verdict, age = mb.judge(commit, NOW)
    report = mb.format_report(commit, verdict, age, None)
    assert "Oslo" in report
    assert "13:00" in report  # 11:00Z is 13:00 in Oslo in September


def test_main_exits_2_on_a_stale_backup(monkeypatch, capsys):
    monkeypatch.setattr(mb, "_gh", _api([
        {"sha": "d" * 40, "commit": {"message": SUBJECT,
                                     "committer": {"date": "2026-08-01T00:00:00Z"}}}
    ]))
    assert mb.main([]) == 2
    assert "BACKUP STALE" in capsys.readouterr().out


def test_main_exits_1_when_the_repo_cannot_be_read(monkeypatch, capsys):
    monkeypatch.setattr(mb, "_gh", _api(None, code=1, err="boom"))
    assert mb.main([]) == 1
    assert "CANNOT SEE" in capsys.readouterr().out


def test_main_exits_2_when_the_job_has_never_written(monkeypatch, capsys):
    monkeypatch.setattr(mb, "_gh", _api([]))
    assert mb.main([]) == 2
    assert "NEVER BACKED UP" in capsys.readouterr().out
