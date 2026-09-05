"""Guards for tools.ci_minutes.

The defect this tool exists around is the one the first hand-run made:
summing every `Minutes` row in the billing endpoint counts public
repositories, which are not billed, and reports a crisis every month.
Most of these tests are about that split rather than about arithmetic.
"""

from datetime import datetime, timezone

import pytest

from tools import ci_minutes


def _row(repo, minutes, unit="Minutes", net=0.0):
    return {
        "repositoryName": repo,
        "quantity": minutes,
        "unitType": unit,
        "netAmount": net,
        "grossAmount": minutes * 0.006,
    }


VISIBILITY = {"secret-repo": True, "open-repo": False}


def test_public_repository_minutes_are_not_billable():
    # The whole reason this tool is not a one-line sum: 1,620 of September's
    # 2,241 minutes were a public repo and drew nothing from the allowance.
    items = [_row("open-repo", 1620), _row("secret-repo", 330)]
    private, public, unknown, net = ci_minutes.split_minutes(items, VISIBILITY)
    assert sum(private.values()) == 330
    assert sum(public.values()) == 1620
    assert unknown == {}


def test_unlisted_repository_is_unknown_rather_than_public():
    # "I could not tell whether these are billed" must not read as "free".
    items = [_row("deleted-repo", 500)]
    private, public, unknown, _net = ci_minutes.split_minutes(items, VISIBILITY)
    assert dict(unknown) == {"deleted-repo": 500}
    assert private == {} and public == {}


def test_storage_rows_are_not_minutes():
    items = [_row("secret-repo", 0.07, unit="GigabyteHours"), _row("secret-repo", 12)]
    private, _public, _unknown, _net = ci_minutes.split_minutes(items, VISIBILITY)
    assert sum(private.values()) == 12


def _run(monkeypatch, items, now, argv=()):
    monkeypatch.setattr(ci_minutes, "fetch_usage", lambda org, y, m: items)
    monkeypatch.setattr(ci_minutes, "fetch_visibility", lambda org: VISIBILITY)

    class _FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(ci_minutes, "datetime", _FrozenNow)
    return ci_minutes.main(list(argv))


DAY5 = datetime(2026, 9, 5, 6, 0, tzinfo=timezone.utc)     # 4.25 days elapsed
DAY2 = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)     # 1.0 day elapsed


def test_projection_over_the_allowance_raises(monkeypatch, capsys):
    # 365 minutes in 4.25 days projects to ~2,570 against a 2,000 allowance --
    # the live September 2026 reading this tool was written on.
    assert _run(monkeypatch, [_row("secret-repo", 365)], DAY5) == 2
    assert "past the 2000-minute allowance" in capsys.readouterr().out


def test_projection_inside_the_allowance_is_clean(monkeypatch, capsys):
    assert _run(monkeypatch, [_row("secret-repo", 100)], DAY5) == 0
    assert "Nothing to act on" in capsys.readouterr().out


def test_early_month_run_rate_is_printed_but_not_judged(monkeypatch, capsys):
    # 200 minutes on day one extrapolates to 6,000, which is a forecast from
    # one day and must not raise.
    assert _run(monkeypatch, [_row("secret-repo", 200)], DAY2) == 0
    out = capsys.readouterr().out
    assert "below the 3-day floor" in out
    assert "ACT" not in out


def test_money_already_charged_raises_even_under_the_allowance(monkeypatch, capsys):
    # netAmount is the ground truth; a plan whose allowance is smaller than the
    # constant here has to be caught by the charge rather than by the estimate.
    assert _run(monkeypatch, [_row("secret-repo", 50, net=1.25)], DAY5) == 2
    assert "charged $1.25" in capsys.readouterr().out


def test_public_minutes_alone_do_not_raise(monkeypatch, capsys):
    # 5,000 free minutes is not a finding at any point in the month.
    assert _run(monkeypatch, [_row("open-repo", 5000)], DAY5) == 0
    assert "5000 minute(s) across 1 public repo(s)" in capsys.readouterr().out


def test_unlisted_repository_makes_the_run_unreadable(monkeypatch, capsys):
    # An otherwise-clean month with an unresolvable repo is exit 1, never 0.
    assert _run(monkeypatch, [_row("secret-repo", 10), _row("ghost", 4)], DAY5) == 1
    assert "in no listing" in capsys.readouterr().out


def test_unreadable_endpoint_is_exit_one(monkeypatch, capsys):
    def _boom(org, y, m):
        raise RuntimeError("gh api ...: HTTP 403")

    monkeypatch.setattr(ci_minutes, "fetch_usage", _boom)
    assert ci_minutes.main([]) == 1
    assert "not a clean result" in capsys.readouterr().out


def test_fetch_helpers_do_not_bind_gh_as_a_default(monkeypatch):
    # A default argument binds at import, so replacing `_gh` on the module would
    # be ignored and the real `gh api` would run inside the test suite.
    calls = []

    def _fake(path, org):
        calls.append(path)
        return {"usageItems": []} if "billing" in path else []

    monkeypatch.setattr(ci_minutes, "_gh", _fake)
    ci_minutes.fetch_usage("Org", 2026, 9)
    ci_minutes.fetch_visibility("Org")
    assert len(calls) == 2


def test_month_progress_counts_today_as_partial():
    elapsed, days = ci_minutes.month_progress(DAY5)
    assert days == 30
    assert elapsed == pytest.approx(4.25)


# --- the verdict cadence_control reads --------------------------------------
# Cycle 977. `projected_overrun` and `allowance_pressure` were extracted so
# `tools.cadence_control` could ask this module whether the loop may be sped
# up. They were reachable only through `main` and through a canned string in
# the cadence tests, so neither had a test of its own.

def test_projected_overrun_charged_outranks_everything():
    kind, reason = ci_minutes.projected_overrun(10, 2000, 15.0, 30.0, net=4.66)
    assert kind == "charged"
    assert "4.66" in reason


def test_projected_overrun_spent_when_used_is_past_the_allowance():
    kind, _ = ci_minutes.projected_overrun(2400, 2000, 15.0, 30.0)
    assert kind == "spent"


def test_projected_overrun_is_not_judged_below_the_day_floor():
    # 400 minutes in 1.5 days extrapolates to 8000, and is still not a finding.
    kind, reason = ci_minutes.projected_overrun(400, 2000, 1.5, 30.0)
    assert kind is None
    assert "not judged" in reason


def test_projected_overrun_raises_on_the_run_rate():
    kind, reason = ci_minutes.projected_overrun(396, 2000, 4.6, 30.0)
    assert kind == "projected"
    assert "2000-minute allowance" in reason


def test_projected_overrun_is_clean_inside_the_allowance():
    kind, reason = ci_minutes.projected_overrun(100, 2000, 10.0, 30.0)
    assert kind is None
    assert "inside" in reason


def _gh_stub(private_minutes):
    def gh(path, org):
        if "usage" in path or "billing" in path:
            return {"usageItems": [
                {"repositoryName": "platform-config", "product": "actions",
                 "quantity": private_minutes, "netAmount": 0.0, "unitType": "Minutes"},
            ]}
        return [{"name": "platform-config", "private": True}]
    return gh


def test_allowance_pressure_blocks_when_the_rate_projects_over():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    blocked, reason = ci_minutes.allowance_pressure(now=now, gh=_gh_stub(396))
    assert blocked is True, reason
    assert "2000-minute allowance" in reason


def test_allowance_pressure_is_clear_on_a_small_bill():
    now = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
    blocked, reason = ci_minutes.allowance_pressure(now=now, gh=_gh_stub(20))
    assert blocked is False, reason
