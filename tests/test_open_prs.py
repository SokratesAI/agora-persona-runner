"""Tests for `tools.open_prs`."""

import json
from datetime import datetime, timezone

import pytest

from tools import open_prs

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def pr(number=1, days_old=0.1, checks=(), draft=False, title="a change"):
    opened = NOW.timestamp() - days_old * 86400
    return {
        "number": number,
        "title": title,
        "isDraft": draft,
        "createdAt": datetime.fromtimestamp(opened, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "url": f"https://example.invalid/{number}",
        "statusCheckRollup": [
            {"name": name, "conclusion": conclusion} for name, conclusion in checks
        ],
    }


class TestJudge:
    def test_no_run_on_a_repo_that_has_workflows(self):
        verdict, detail = open_prs.judge(pr(checks=()), NOW, workflow_count=6)
        assert verdict == "no_run"
        assert "6 workflow(s)" in detail

    def test_no_run_is_not_a_finding_when_the_repo_has_no_workflows(self):
        # The four `*-config` repos return total_count 0, so a check-run was
        # never coming. Raising there is the negative result that was
        # guaranteed in advance.
        verdict, _ = open_prs.judge(pr(checks=()), NOW, workflow_count=0)
        assert verdict == "ok"

    def test_an_unreadable_workflow_count_never_becomes_a_finding(self):
        verdict, _ = open_prs.judge(pr(checks=()), NOW, workflow_count=None)
        assert verdict == "unreadable"

    def test_failing_check(self):
        verdict, detail = open_prs.judge(
            pr(checks=(("test", "SUCCESS"), ("vault-drift", "FAILURE"))),
            NOW,
            workflow_count=6,
        )
        assert verdict == "failing"
        assert "vault-drift" in detail

    def test_skipped_is_not_a_failure(self):
        # `build-push` is SKIPPED on every pull request in the bridge and
        # runner repos by design. Reading that as failing would raise on
        # every green pull request this loop opens.
        verdict, _ = open_prs.judge(
            pr(checks=(("test", "SUCCESS"), ("build-push", "SKIPPED"))),
            NOW,
            workflow_count=6,
        )
        assert verdict == "ok"

    def test_pending_never_raises(self):
        verdict, detail = open_prs.judge(
            pr(days_old=9, checks=(("test", None),)), NOW, workflow_count=6
        )
        assert verdict == "pending"
        assert "test" in detail
        assert verdict not in open_prs.RAISING

    def test_green_inside_the_window_is_not_a_finding(self):
        verdict, _ = open_prs.judge(
            pr(days_old=0.5, checks=(("test", "SUCCESS"),)), NOW, workflow_count=6
        )
        assert verdict == "ok"

    def test_green_past_the_window_is_ready_unmerged(self):
        verdict, detail = open_prs.judge(
            pr(days_old=2.2, checks=(("test", "SUCCESS"),)), NOW, workflow_count=6
        )
        assert verdict == "ready"
        assert "2.2 day(s)" in detail

    def test_the_window_is_the_flag_not_a_constant(self):
        aged = pr(days_old=2.2, checks=(("test", "SUCCESS"),))
        assert open_prs.judge(aged, NOW, 6, max_age_days=7)[0] == "ok"
        assert open_prs.judge(aged, NOW, 6, max_age_days=1)[0] == "ready"

    def test_a_draft_is_held_however_old_and_however_broken(self):
        verdict, detail = open_prs.judge(
            pr(days_old=90, draft=True, checks=(("test", "FAILURE"),)),
            NOW,
            workflow_count=6,
        )
        assert verdict == "held"
        assert detail == "draft"

    def test_a_title_saying_HELD_is_held(self):
        verdict, detail = open_prs.judge(
            pr(days_old=90, title="Give nova-site its own tag — HELD on the policy"),
            NOW,
            workflow_count=6,
        )
        assert verdict == "held"
        assert "HELD" in detail

    def test_held_is_case_sensitive_so_an_ordinary_word_does_not_park_a_pr(self):
        verdict, _ = open_prs.judge(
            pr(days_old=90, title="Stop the reaper being held by its own lock",
               checks=(("test", "SUCCESS"),)),
            NOW,
            workflow_count=6,
        )
        assert verdict == "ready"

    def test_an_unparseable_created_at_does_not_crash_the_sweep(self):
        broken = pr(checks=(("test", "SUCCESS"),))
        broken["createdAt"] = "not a timestamp"
        verdict, _ = open_prs.judge(broken, NOW, workflow_count=6)
        assert verdict == "ok"


class TestExitCode:
    """`main` through an injected `gh`, so nothing here reaches GitHub."""

    @staticmethod
    def _run_for(prs, workflows=6, repo="SokratesAI/only"):
        import json

        def run(args):
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps([{"nameWithOwner": repo, "isArchived": False}]), ""
            if args[0] == "pr":
                return 0, json.dumps(prs), ""
            if args[0] == "api":
                return 0, f"{workflows}\n", ""
            raise AssertionError(f"unexpected gh call: {args}")

        return run

    def test_clean_sweep_exits_zero(self, capsys):
        code = open_prs.main(
            [], now=NOW, run=self._run_for([pr(checks=(("test", "SUCCESS"),))])
        )
        assert code == 0
        assert "Nothing to act on." in capsys.readouterr().out

    def test_a_stalled_pr_exits_two(self, capsys):
        code = open_prs.main(
            [],
            now=NOW,
            run=self._run_for([pr(days_old=36, checks=(("test", "SUCCESS"),))]),
        )
        assert code == 2
        out = capsys.readouterr().out
        assert "READY, UNMERGED" in out
        assert "Nothing to act on." not in out

    def test_an_unreadable_repo_listing_exits_one(self, capsys):
        def run(args):
            return 1, "", "gh: could not reach github.com"

        assert open_prs.main([], now=NOW, run=run) == 1
        assert "COULD NOT LIST THE ORG" in capsys.readouterr().out

    def test_a_repo_whose_prs_cannot_be_listed_exits_one(self, capsys):
        import json

        def run(args):
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps([{"nameWithOwner": "a/b", "isArchived": False}]), ""
            return 1, "", "gh: 403"

        assert open_prs.main([], now=NOW, run=run) == 1
        assert "could not list open pull requests" in capsys.readouterr().out

    def test_a_stalled_pr_outranks_an_unreadable_workflow_count(self, capsys):
        import json

        def run(args):
            if args[:2] == ["repo", "list"]:
                return 0, json.dumps([{"nameWithOwner": "a/b", "isArchived": False}]), ""
            if args[0] == "pr":
                return 0, json.dumps(
                    [pr(1, days_old=36, checks=(("test", "SUCCESS"),)),
                     pr(2, days_old=36, checks=())]
                ), ""
            return 1, "", "gh: 500"

        assert open_prs.main([], now=NOW, run=run) == 2
        out = capsys.readouterr().out
        assert "READY, UNMERGED" in out
        assert "COULD NOT JUDGE" in out


def test_the_check_is_in_preflight():
    from tools import preflight

    assert "open_prs" in preflight.CHECKS
    assert preflight.SUBJECT["open_prs"][0] == "off-box"


# ---------------------------------------------------------------- superseded


def _pr(**kw):
    """An open pull request that would otherwise judge as `ready`."""
    pr = {
        "number": 7,
        "title": "Node 20 -> 24",
        "createdAt": "2026-08-31T03:26:59Z",
        "isDraft": False,
        "url": "https://example/7",
        "statusCheckRollup": [{"name": "test", "conclusion": "SUCCESS"}],
        "files": [{"path": "Dockerfile"}],
        "headRefOid": "headsha",
        "baseRefName": "main",
    }
    pr.update(kw)
    return pr


def _blobs(mapping, missing=()):
    """A `run` that answers `gh api .../contents/<path>?ref=<ref>`."""
    calls = []

    def run(args):
        calls.append(args)
        target = args[1]
        path, ref = target.split("/contents/")[1].split("?ref=")
        if (path, ref) in missing:
            return 1, "", "gh: Not Found (HTTP 404)"
        return 0, mapping[(path, ref)] + "\n", ""

    run.calls = calls
    return run


def test_superseded_when_every_changed_file_matches_the_base():
    run = _blobs({("Dockerfile", "headsha"): "aaa", ("Dockerfile", "main"): "aaa"})
    already, why = open_prs.is_superseded("o/r", _pr(), run=run)
    assert already is True
    assert "already identical on main" in why


def test_not_superseded_when_a_changed_file_differs():
    run = _blobs({("Dockerfile", "headsha"): "aaa", ("Dockerfile", "main"): "bbb"})
    already, _ = open_prs.is_superseded("o/r", _pr(), run=run)
    assert already is False


def test_a_file_the_pr_adds_is_not_superseded():
    # Absent on the base, present at the head: the real "this adds
    # something" case, and the one a naive 404-tolerant compare would
    # call identical.
    run = _blobs(
        {("Dockerfile", "headsha"): "aaa"}, missing=(("Dockerfile", "main"),)
    )
    already, _ = open_prs.is_superseded("o/r", _pr(), run=run)
    assert already is False


def test_a_deletion_already_on_the_base_is_superseded():
    run = _blobs({}, missing=(("Dockerfile", "headsha"), ("Dockerfile", "main")))
    already, _ = open_prs.is_superseded("o/r", _pr(), run=run)
    assert already is True


def test_an_unreadable_blob_is_none_rather_than_superseded():
    def run(args):
        return 1, "", "gh: 500 Internal Server Error"

    already, why = open_prs.is_superseded("o/r", _pr(), run=run)
    assert already is None
    assert "500" in why


def test_too_many_files_is_not_judged():
    pr = _pr(files=[{"path": f"f{n}"} for n in range(open_prs.SUPERSEDED_MAX_FILES + 1)])

    def run(args):  # pragma: no cover - must never be called
        raise AssertionError("asked the API about an over-large pull request")

    already, why = open_prs.is_superseded("o/r", pr, run=run)
    assert already is None
    assert str(open_prs.SUPERSEDED_MAX_FILES) in why


def test_a_directory_reply_is_unreadable_not_a_match():
    # `-q .sha` on a directory prints nothing. Two empty strings compare
    # equal, which would report a superseded pull request from no data.
    def run(args):
        return 0, "\n", ""

    already, _ = open_prs.is_superseded("o/r", _pr(), run=run)
    assert already is None


def test_superseded_does_not_raise_and_ready_does():
    matching = {("Dockerfile", "headsha"): "aaa", ("Dockerfile", "main"): "aaa"}
    differing = {("Dockerfile", "headsha"): "aaa", ("Dockerfile", "main"): "bbb"}

    def make(blobs):
        def run(args):
            if args[0] == "pr":
                return 0, json.dumps([_pr()]), ""
            if args[0] == "repo":
                return 0, json.dumps([{"nameWithOwner": "o/r", "isArchived": False}]), ""
            path, ref = args[1].split("/contents/")[1].split("?ref=")
            return 0, blobs[(path, ref)] + "\n", ""

        return run

    now = datetime(2026, 9, 2, tzinfo=timezone.utc)
    assert open_prs.main([], now=now, run=make(matching)) == 0
    assert open_prs.main([], now=now, run=make(differing)) == 2


def _live(number, minutes, verdict="pending", title="a change"):
    return {"repo": "SokratesAI/agora-persona-runner", "number": number,
            "title": title, "verdict": verdict, "detail": "d",
            "url": f"https://github.com/SokratesAI/agora-persona-runner/pull/{number}",
            "age": minutes / (24.0 * 60.0)}


def test_a_pull_request_opened_inside_the_claim_window_is_printed_above_the_findings():
    # The live failure: #700 was in Cycle 866's own preflight run, under
    # `still running, inside the window`, and that cycle spent its hour
    # re-fixing what #700 had fixed five minutes earlier.
    report = open_prs.format_report(
        [_live(700, 5, title="Group the swap sweep's Pods by the Job label")],
        swept=1, errors=[], caveat_repos=[], max_age_days=1.0)
    # Sliced to the section itself: the title also appears further down under
    # `still running`, so asserting it against the whole report would pass on a
    # section that printed no title at all -- which is the one thing it is for.
    section = report.split("\n")[:3]
    assert section[0].startswith("ANOTHER CYCLE MAY BE ON THESE — 1 pull request(s)")
    assert section[1] == ("  SokratesAI/agora-persona-runner#700  Group the "
                          "swap sweep's Pods by the Job label")
    assert "opened 5 minute(s) ago" in section[2]


def test_an_older_pull_request_is_not_called_a_live_cycle():
    report = open_prs.format_report(
        [_live(699, 90)], swept=1, errors=[], caveat_repos=[], max_age_days=1.0)
    assert "ANOTHER CYCLE MAY BE ON THESE" not in report


def test_the_window_is_the_claim_ledgers_own_45_minutes():
    assert open_prs.LIVE_CYCLE_DAYS == pytest.approx(45.0 / (24.0 * 60.0))
    assert [row["number"] for row in open_prs.live_cycle_rows(
        [_live(1, 44), _live(2, 46)])] == [1]


def test_a_healthy_pull_request_is_listed_too_because_that_is_where_it_hides():
    # `ok` and `pending` are the two headings that read as "nothing to act
    # on", which is exactly where a live cycle's pull request lands.
    report = open_prs.format_report(
        [_live(1, 3, verdict="ok"), _live(2, 3, verdict="held")],
        swept=1, errors=[], caveat_repos=[], max_age_days=1.0)
    assert report.startswith("ANOTHER CYCLE MAY BE ON THESE — 2 pull request(s)")


def test_a_live_pull_request_does_not_by_itself_raise():
    # Every 18 minutes there is one. A section that turns the check red on an
    # ordinary morning is a section that stops being read.
    report = open_prs.format_report(
        [_live(1, 3)], swept=1, errors=[], caveat_repos=[], max_age_days=1.0)
    assert "Nothing to act on." in report


def test_a_pull_request_with_no_age_is_not_guessed_into_the_window():
    rows = [dict(_live(1, 3), age=None)]
    assert open_prs.live_cycle_rows(rows) == []
