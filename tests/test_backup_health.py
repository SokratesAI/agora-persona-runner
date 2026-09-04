"""Tests for tools/backup_health.py.

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

from tools import backup_health as bh
from tools.backup_health import (
    ACKNOWLEDGED,
    BACKUPS,
    format_coverage,
    judge_coverage,
    read_claims,
)


UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 9, 3, 12, 0, tzinfo=UTC)

#: The commit the job's own `git commit -q -m` produces, reproduced as input.
SUBJECT = "[backup] marcus state rev 2 at 2026-09-03 11:12Z [skip ci]"

#: The vault snapshot the `vault-backup` job commits, reproduced as input.
VAULT_SUBJECT = "[backup] vault snapshot 2026-09-03 21:52Z (14 change(s)) [skip ci]"

MARCUS = next(b for b in bh.BACKUPS if b.name == "marcus")
VAULT = next(b for b in bh.BACKUPS if b.name == "vault")


class _FrozenNow(datetime.datetime):
    """`datetime.datetime` with `now()` pinned to NOW, so `main` is testable."""

    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def _commit(date, subject=SUBJECT, sha="b" * 40):
    return {"sha": sha, "subject": subject, "date": date}


def _api(payload, code=0, err="", seen=None):
    def run(args):
        if seen is not None:
            seen.append(args)
        return code, json.dumps(payload) if payload is not None else "", err
    return run


def test_a_backup_written_an_hour_ago_is_fresh():
    verdict, age = bh.judge(MARCUS, _commit("2026-09-03T11:00:00Z"), NOW)
    assert verdict == "fresh"
    assert round(age, 2) == 1.0


def test_a_backup_just_inside_the_threshold_is_still_fresh():
    # 25h59m -- the worst legal gap is just under 24h plus the deliberate slack.
    verdict, _ = bh.judge(MARCUS, _commit("2026-09-02T10:01:00Z"), NOW)
    assert verdict == "fresh"


def test_a_backup_past_the_threshold_is_stale():
    verdict, age = bh.judge(MARCUS, _commit("2026-09-02T09:00:00Z"), NOW)
    assert verdict == "stale"
    assert age > MARCUS.stale_after_hours


def test_the_threshold_is_at_least_a_full_day():
    # The stamp carries a date, so two successful runs can be a shade under 24
    # hours apart legitimately. A threshold below that reports a working job.
    assert MARCUS.stale_after_hours >= 24


def test_no_stamp_commit_at_all_is_never_rather_than_stale():
    verdict, age = bh.judge(MARCUS, None, NOW)
    assert verdict == "never"
    assert age is None


def test_an_unparseable_date_is_not_reported_as_fresh():
    verdict, _ = bh.judge(MARCUS, _commit("not a date"), NOW)
    assert verdict == "unreadable"


def test_the_commit_read_is_filtered_to_the_stamp_file():
    seen = []
    commit, error = bh.read_stamp_commit(MARCUS, run=_api([
        {"sha": "c" * 40, "commit": {"message": SUBJECT,
                                     "committer": {"date": "2026-09-03T11:12:36Z"}}}
    ], seen=seen))
    assert error is None
    assert commit["date"] == "2026-09-03T11:12:36Z"
    assert commit["subject"] == SUBJECT
    request = seen[0][-1]
    assert f"path={MARCUS.stamp_path}" in request
    assert MARCUS.repo in request


def test_an_empty_commit_list_is_never_backed_up_and_not_an_error():
    commit, error = bh.read_stamp_commit(MARCUS, run=_api([]))
    assert commit is None
    assert error is None


def test_a_gh_failure_is_an_error_and_not_an_empty_history():
    commit, error = bh.read_stamp_commit(MARCUS, run=_api(None, code=1, err="gh: Not Found (HTTP 404)"))
    assert commit is None
    assert error == "gh: Not Found (HTTP 404)"


def test_unparseable_json_is_an_error():
    def run(args):
        return 0, "<html>not json</html>", ""
    commit, error = bh.read_stamp_commit(MARCUS, run=run)
    assert commit is None
    assert "could not parse" in error


def test_the_stale_report_names_the_age_and_the_commit():
    commit = _commit("2026-09-01T09:00:00Z")
    verdict, age = bh.judge(MARCUS, commit, NOW)
    report = bh.format_report(MARCUS, commit, verdict, age, None)
    assert "BACKUP STALE" in report
    assert commit["sha"][:12] in report
    assert SUBJECT in report


def test_the_never_report_does_not_claim_an_age():
    report = bh.format_report(MARCUS, None, "never", None, None)
    assert "NEVER BACKED UP" in report
    assert "STALE" not in report


def test_an_unreadable_repo_is_never_reported_as_healthy():
    report = bh.format_report(MARCUS, None, None, None, "gh: Not Found (HTTP 404)")
    assert "CANNOT SEE" in report
    assert "not the same as a healthy backup" in report


def test_the_fresh_report_prints_the_age_in_oslo_time():
    commit = _commit("2026-09-03T11:00:00Z")
    verdict, age = bh.judge(MARCUS, commit, NOW)
    report = bh.format_report(MARCUS, commit, verdict, age, None)
    assert "Oslo" in report
    assert "13:00" in report  # 11:00Z is 13:00 in Oslo in September


def test_main_exits_2_on_a_stale_backup(monkeypatch, capsys):
    monkeypatch.setattr(bh, "_gh", _api([
        {"sha": "d" * 40, "commit": {"message": SUBJECT,
                                     "committer": {"date": "2026-08-01T00:00:00Z"}}}
    ]))
    assert bh.main([]) == 2
    assert "BACKUP STALE" in capsys.readouterr().out


def test_main_exits_1_when_the_repo_cannot_be_read(monkeypatch, capsys):
    monkeypatch.setattr(bh, "read_claims", lambda: (["agents/marcus-data"], None))
    monkeypatch.setattr(bh, "_gh", _api(None, code=1, err="boom"))
    assert bh.main([]) == 1
    assert "CANNOT SEE" in capsys.readouterr().out


def test_main_exits_2_when_the_job_has_never_written(monkeypatch, capsys):
    monkeypatch.setattr(bh, "_gh", _api([]))
    assert bh.main([]) == 2
    assert "NEVER BACKED UP" in capsys.readouterr().out


# --- the vault, which has no stamp file -------------------------------------
#
# The vault-backup job writes no dated liveness file, so its measurement is the
# newest commit on the default branch. That difference is the whole reason this
# module carries a table instead of two constants, and the tests below drive it
# from the table rather than restating either number.


def test_the_vault_is_watched_at_all():
    # It was not, until Cycle 864: `cronjob_health` reads the CronJob object,
    # which is on the box the backup exists to survive.
    assert "SokratesAI/vault" in {b.repo for b in bh.BACKUPS}


def test_the_vault_commit_read_is_not_filtered_to_a_path():
    # There is no stamp file to filter on. A `path=` here would name a file the
    # job never writes, come back empty, and report NEVER BACKED UP forever.
    seen = []
    commit, error = bh.read_stamp_commit(VAULT, run=_api([
        {"sha": "e" * 40, "commit": {"message": VAULT_SUBJECT,
                                     "committer": {"date": "2026-09-03T21:52:42Z"}}}
    ], seen=seen))
    assert error is None
    assert commit["date"] == "2026-09-03T21:52:42Z"
    request = seen[0][-1]
    assert "path=" not in request
    assert VAULT.repo in request


def test_the_two_backups_are_judged_on_their_own_thresholds():
    # 4 hours old: stale for the vault's hourly commits, fresh for Marcus's
    # once-a-day stamp. One shared constant would have to be wrong for one of
    # them, and the wrong direction is silence.
    four_hours_old = _commit("2026-09-03T08:00:00Z", subject=VAULT_SUBJECT)
    assert bh.judge(VAULT, four_hours_old, NOW)[0] == "stale"
    assert bh.judge(MARCUS, four_hours_old, NOW)[0] == "fresh"


def test_the_vault_threshold_allows_two_missed_hourly_runs():
    # `50 * * * *`, so a single miss is a ~2h gap and must not raise.
    assert VAULT.stale_after_hours > 2
    assert VAULT.stale_after_hours < 24


def test_a_stale_vault_report_names_the_vault_and_not_marcus():
    commit = _commit("2026-09-01T09:00:00Z", subject=VAULT_SUBJECT)
    verdict, age = bh.judge(VAULT, commit, NOW)
    report = bh.format_report(VAULT, commit, verdict, age, None)
    assert "BACKUP STALE" in report
    assert VAULT.repo in report
    assert "Marcus" not in report


def test_main_raises_when_either_backup_is_stale(monkeypatch, capsys):
    # Marcus fresh, vault three days old: a sweep that judged only the first
    # backup, or that stopped at the first clean one, would exit 0 here.
    def run(args):
        request = args[-1]
        date = ("2026-09-03T11:00:00Z" if MARCUS.repo in request
                else "2026-08-31T00:00:00Z")
        return 0, json.dumps([
            {"sha": "f" * 40, "commit": {"message": VAULT_SUBJECT,
                                         "committer": {"date": date}}}
        ]), ""
    monkeypatch.setattr(bh, "_gh", run)
    monkeypatch.setattr(bh.datetime, "datetime", _FrozenNow)
    assert bh.main([]) == 2
    out = capsys.readouterr().out
    assert "BACKUP STALE" in out
    assert VAULT.repo in out
    assert "inside the 26-hour threshold" in out  # Marcus still reported, not skipped


def test_main_reports_an_unreadable_backup_beside_a_clean_one(monkeypatch, capsys):
    monkeypatch.setattr(bh, "read_claims", lambda: (["agents/marcus-data"], None))
    # A partial sweep must never read as a clean one.
    def run(args):
        if VAULT.repo in args[-1]:
            return 1, "", "gh: Not Found (HTTP 404)"
        return 0, json.dumps([
            {"sha": "a" * 40, "commit": {"message": SUBJECT,
                                         "committer": {"date": "2026-09-03T11:00:00Z"}}}
        ]), ""
    monkeypatch.setattr(bh, "_gh", run)
    monkeypatch.setattr(bh.datetime, "datetime", _FrozenNow)
    assert bh.main([]) == 1
    out = capsys.readouterr().out
    assert "CANNOT SEE" in out
    assert "1 off-box backup(s) of 2" in out


# --- coverage sweep (Cycle 883) -------------------------------------------


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _pvc_json(*names):
    return json.dumps(
        {
            "items": [
                {"metadata": {"namespace": ns, "name": name}}
                for ns, name in (n.split("/") for n in names)
            ]
        }
    )


def test_read_claims_returns_namespaced_names_sorted():
    claims, error = read_claims(
        run=lambda args: _Proc(stdout=_pvc_json("obsidian/couchdb-data", "agents/agora-data"))
    )
    assert error is None
    assert claims == ["agents/agora-data", "obsidian/couchdb-data"]


def test_read_claims_treats_an_empty_list_as_unreadable():
    """"no volumes" and "I could not look" are opposite findings."""
    claims, error = read_claims(run=lambda args: _Proc(stdout=_pvc_json()))
    assert claims == []
    assert error and "no claims at all" in error


def test_read_claims_reports_a_failed_kubectl_rather_than_returning_nothing():
    claims, error = read_claims(run=lambda args: _Proc(returncode=1, stderr="Forbidden"))
    assert claims == []
    assert error == "Forbidden"


def test_judge_coverage_splits_declared_acknowledged_and_unprotected():
    covered, acknowledged, uncovered = judge_coverage(
        ["agents/agora-data", "agents/marcus-data", "infra/ollama-models"]
    )
    assert covered == [("agents/marcus-data", "marcus")]
    assert [claim for claim, _ in acknowledged] == ["infra/ollama-models"]
    assert uncovered == ["agents/agora-data"]


def test_every_declared_backup_names_a_claim_and_every_acknowledgement_gives_a_reason():
    assert [b.name for b in BACKUPS if not b.covers] == []
    assert all(reason.strip() for reason in ACKNOWLEDGED.values())


def test_format_coverage_raises_on_an_unprotected_volume_and_names_it():
    report, status = format_coverage(["agents/agora-data", "agents/marcus-data"], None)
    assert status == 2
    assert "NOT BACKED UP — agents/agora-data" in report
    assert "1 unprotected" in report


def test_format_coverage_is_clean_when_every_volume_is_declared_or_acknowledged():
    report, status = format_coverage(["agents/marcus-data", "infra/ollama-models"], None)
    assert status == 0
    assert "NOT BACKED UP" not in report
    assert "0 unprotected" in report


def test_format_coverage_never_reads_as_clean_when_the_cluster_was_unreadable():
    report, status = format_coverage([], "Forbidden")
    assert status == 1
    assert "COVERAGE UNREADABLE" in report
    assert "NOT BACKED UP" not in report
