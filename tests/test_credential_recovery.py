"""Tests for tools.credential_recovery.

No fixture in this file carries anything shaped like a real token. The
module under test never reads `accessToken` or `refreshToken`, and the
credential it runs against in production is live, so a fixture built by
copying one would put a working secret in the repository -- which is the
failure the module's own docstring is careful about. Every blob below
omits those keys entirely, which also means a test would fail loudly if
the module ever started requiring them.
"""
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from tools import credential_recovery as cr


NOW = datetime(2026, 9, 5, 8, 0, tzinfo=timezone.utc)


def blob(expires, plan="max", extra=None):
    oauth = {"expiresAt": expires, "subscriptionType": plan, "scopes": ["user:inference"]}
    if extra:
        oauth.update(extra)
    return json.dumps({"claudeAiOauth": oauth})


def millis(when):
    return int(when.timestamp() * 1000)


def test_expired_secret_raises_and_names_the_outage():
    secret = json.loads(blob(millis(NOW - timedelta(days=35)), "pro"))["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(hours=5)), "max"))["claudeAiOauth"]
    findings, expiry, stale, _ = cr.judge(secret, live, NOW)
    assert stale is True
    states = [f["state"] for f in findings]
    assert "expired" in states
    # The plan disagreement is a second, independent finding -- not a
    # restatement of the first one.
    assert "plan-drift" in states
    out = io.StringIO()
    assert cr.report(findings, expiry, stale, live, None, NOW, out) == 2
    assert "30 hours" in out.getvalue()


def test_a_current_secret_is_clean():
    secret = json.loads(blob(millis(NOW + timedelta(days=20))))["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(hours=5))))["claudeAiOauth"]
    findings, expiry, stale, _ = cr.judge(secret, live, NOW)
    assert findings == []
    assert stale is False
    out = io.StringIO()
    assert cr.report(findings, expiry, stale, live, None, NOW, out) == 0
    assert "would still log in" in out.getvalue()


def test_live_credential_expiring_soon_is_not_a_finding():
    """The disk copy expires every few hours by design.

    This is the discriminating case for the whole tool: if it judged the
    live credential the way it judges the Secret, this would raise on a
    system that is working exactly as designed, every cycle.
    """
    secret = json.loads(blob(millis(NOW + timedelta(days=20))))["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(minutes=3))))["claudeAiOauth"]
    findings, _, stale, _ = cr.judge(secret, live, NOW)
    assert findings == []
    assert stale is False


def test_an_already_expired_live_credential_still_does_not_raise():
    secret = json.loads(blob(millis(NOW + timedelta(days=20))))["claudeAiOauth"]
    live = json.loads(blob(millis(NOW - timedelta(days=2))))["claudeAiOauth"]
    findings, _, _, _ = cr.judge(secret, live, NOW)
    assert findings == []


def test_unreadable_disk_copy_does_not_silence_the_secret_verdict():
    """The one situation this check exists for must not silence it.

    A lost volume means no live credential to read, and that is precisely
    when the Secret's staleness matters most.
    """
    secret = json.loads(blob(millis(NOW - timedelta(days=35)), "pro"))["claudeAiOauth"]
    findings, expiry, stale, _ = cr.judge(secret, None, NOW)
    assert [f["state"] for f in findings] == ["expired"]
    out = io.StringIO()
    assert cr.report(findings, expiry, stale, None, None, NOW, out) == 2
    assert "CANNOT READ the live credential" in out.getvalue()


def test_matching_plans_produce_no_plan_finding():
    secret = json.loads(blob(millis(NOW + timedelta(days=20)), "max"))["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(hours=5)), "max"))["claudeAiOauth"]
    findings, _, _, _ = cr.judge(secret, live, NOW)
    assert findings == []


def test_expiry_in_seconds_is_not_read_as_1970():
    when = NOW + timedelta(days=10)
    oauth = json.loads(blob(int(when.timestamp())))["claudeAiOauth"]
    assert cr.expires_at(oauth, "x").date() == when.date()


def test_expiry_in_milliseconds_reads_the_same_instant():
    when = NOW + timedelta(days=10)
    oauth = json.loads(blob(millis(when)))["claudeAiOauth"]
    assert cr.expires_at(oauth, "x").date() == when.date()


@pytest.mark.parametrize("value", [None, "soon", True, [], {}])
def test_unreadable_expiry_raises_rather_than_passing(value):
    with pytest.raises(cr.CredentialError):
        cr.expires_at({"expiresAt": value}, "x")


def test_missing_secret_env_is_cannot_read_not_clean(monkeypatch):
    monkeypatch.delenv(cr.SECRET_ENV, raising=False)
    with pytest.raises(cr.CredentialError):
        cr.read_secret()


def test_main_exits_1_when_the_secret_is_absent(monkeypatch, capsys):
    monkeypatch.delenv(cr.SECRET_ENV, raising=False)
    assert cr.main([]) == 1
    assert "CANNOT READ" in capsys.readouterr().out


def test_main_exits_2_on_a_stale_secret(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(NOW - timedelta(days=35)), "pro"))
    disk = tmp_path / "creds.json"
    disk.write_text(blob(millis(datetime.now(timezone.utc) + timedelta(hours=5)), "max"))
    assert cr.main(["--disk", str(disk)]) == 2
    out = capsys.readouterr().out
    assert "RAISE" in out
    assert "kubeseal" in out


def test_main_exits_0_on_a_current_secret(monkeypatch, capsys, tmp_path):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(future), "max"))
    disk = tmp_path / "creds.json"
    disk.write_text(blob(millis(datetime.now(timezone.utc) + timedelta(hours=5)), "max"))
    assert cr.main(["--disk", str(disk)]) == 0


def test_no_token_value_is_ever_printed(monkeypatch, capsys, tmp_path):
    """Every printed line is built from expiresAt and subscriptionType.

    The fixture puts a marker in the two fields the module must never
    touch; if any future edit starts echoing the blob, this fails.
    """
    marker = "MUST-NOT-APPEAR-IN-OUTPUT"
    secret = blob(
        millis(NOW - timedelta(days=35)),
        "pro",
        extra={"accessToken": marker, "refreshToken": marker},
    )
    monkeypatch.setenv(cr.SECRET_ENV, secret)
    disk = tmp_path / "creds.json"
    disk.write_text(
        blob(
            millis(datetime.now(timezone.utc) + timedelta(hours=5)),
            "max",
            extra={"accessToken": marker, "refreshToken": marker},
        )
    )
    assert cr.main(["--disk", str(disk)]) == 2
    assert marker not in capsys.readouterr().out


def test_disk_path_follows_claude_home(monkeypatch):
    monkeypatch.setenv("CLAUDE_HOME", "/data/claude-home")
    assert cr.disk_path() == "/data/claude-home/.claude/.credentials.json"


def test_non_json_secret_is_cannot_read(monkeypatch):
    monkeypatch.setenv(cr.SECRET_ENV, "not valid json{{{")
    with pytest.raises(cr.CredentialError):
        cr.read_secret()


def test_secret_without_claudeaioauth_is_cannot_read(monkeypatch):
    monkeypatch.setenv(cr.SECRET_ENV, json.dumps({"something": "else"}))
    with pytest.raises(cr.CredentialError):
        cr.read_secret()


def test_registered_in_preflight():
    from tools import preflight

    assert "credential_recovery" in preflight.CHECKS
    assert preflight.unlabelled_checks(["credential_recovery"]) == []


# --- the field the verdict is taken from -----------------------------------
#
# A snapshot resealed from a current credential carries an `expiresAt` a few
# hours out (the CLI mints a short-lived access token) and a
# `refreshTokenExpiresAt` days or weeks out. Judging the first would raise on
# a Secret resealed the same morning, which is a check that can never be
# satisfied; judging the second asks the question the module exists for --
# could a restore log in.


def test_fresh_reseal_is_clean_although_its_access_token_has_expired():
    """The case platform-config#704 creates. Fails under the old rule."""
    secret = json.loads(
        blob(
            millis(NOW - timedelta(hours=2)),
            "max",
            {"refreshTokenExpiresAt": millis(NOW + timedelta(days=11))},
        )
    )["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(hours=5)), "max"))["claudeAiOauth"]
    findings, expiry, stale, field = cr.judge(secret, live, NOW)
    assert field == "refreshTokenExpiresAt"
    assert stale is False and findings == []
    assert expiry == NOW + timedelta(days=11)


def test_a_dead_refresh_token_raises_even_with_a_live_access_token():
    """The discriminating half: the two fields disagree and the refresh wins."""
    secret = json.loads(
        blob(
            millis(NOW + timedelta(days=30)),
            "max",
            {"refreshTokenExpiresAt": millis(NOW - timedelta(days=3))},
        )
    )["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(hours=5)), "max"))["claudeAiOauth"]
    findings, _, stale, field = cr.judge(secret, live, NOW)
    assert field == "refreshTokenExpiresAt"
    assert stale is True
    assert [f["state"] for f in findings] == ["expired"]
    assert "refreshTokenExpiresAt" in findings[0]["detail"]


def test_a_snapshot_without_the_field_still_falls_back_to_expiresat():
    """The old snapshot in the Secret today has no refreshTokenExpiresAt."""
    secret = json.loads(blob(millis(NOW - timedelta(days=35)), "pro"))["claudeAiOauth"]
    live = json.loads(blob(millis(NOW + timedelta(hours=5)), "max"))["claudeAiOauth"]
    findings, _, stale, field = cr.judge(secret, live, NOW)
    assert field == "expiresAt"
    assert stale is True and any(f["state"] == "expired" for f in findings)


def test_a_non_numeric_refresh_expiry_falls_back_rather_than_crashing():
    secret = json.loads(
        blob(millis(NOW + timedelta(days=20)), "max", {"refreshTokenExpiresAt": "soon"})
    )["claudeAiOauth"]
    _, _, stale, field = cr.judge(secret, None, NOW)
    assert field == "expiresAt" and stale is False


def test_report_names_the_field_it_judged_and_drops_the_not_judged_line():
    out = io.StringIO()
    cr.report([], NOW + timedelta(days=11), False, None, None, NOW, out,
              "refreshTokenExpiresAt")
    text = out.getvalue()
    assert "its refreshTokenExpiresAt is" in text
    # the "nothing here to read it from" caveat is only true of an old snapshot
    assert "carries no refreshTokenExpiresAt" not in text


def test_report_keeps_the_caveat_when_it_fell_back():
    out = io.StringIO()
    cr.report([], NOW + timedelta(days=20), False, None, None, NOW, out, "expiresAt")
    assert "carries no refreshTokenExpiresAt" in out.getvalue()


def test_the_stale_tail_no_longer_claims_this_loop_cannot_fix_it():
    """That sentence stopped a cycle; it was measured false at cycle 958."""
    out = io.StringIO()
    cr.report(
        [{"state": "expired", "detail": "d"}],
        NOW - timedelta(days=35), True, None, None, NOW, out, "expiresAt",
    )
    text = out.getvalue()
    assert "this loop has neither" not in text
    assert "sealed-secrets-pub.pem" in text and "/v1/verify" in text
