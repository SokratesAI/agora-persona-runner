"""Tests for `tools.open_prs`."""

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
