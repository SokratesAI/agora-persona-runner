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


POD_START = datetime(2026, 9, 4, 20, 50, 25, tzinfo=timezone.utc)

#: Captured before the autouse fixture below can replace them, so the two
#: tests that exercise the readers themselves get the real ones.
REAL_SEALED_WRITTEN_AT = cr.sealed_written_at
REAL_POD_STARTED_AT = cr.pod_started_at


@pytest.fixture(autouse=True)
def no_cluster(monkeypatch):
    """Every test states its own freshness inputs, or has none.

    Without this the whole file's verdicts depend on whether the machine
    running pytest happens to have a `kubectl` that can reach this
    cluster -- green in CI, red on the bridge pod, for reasons that have
    nothing to do with the code under test.
    """
    monkeypatch.setattr(cr, "sealed_written_at", lambda: None)
    monkeypatch.setattr(cr, "pod_started_at", lambda: None)


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
    # The live copy needs a refresh expiry as well as an access expiry: main
    # now judges when this loop's own login dies, and a file that cannot
    # answer that is unknown rather than clean.
    disk.write_text(
        blob(
            millis(datetime.now(timezone.utc) + timedelta(hours=5)),
            "max",
            extra={"refreshTokenExpiresAt": millis(future)},
        )
    )
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


def test_env_is_stale_when_the_controller_wrote_after_this_pod_started():
    assert cr.env_is_stale(POD_START, POD_START + timedelta(hours=12)) is True


def test_env_is_fresh_when_the_pod_started_after_the_controller_wrote():
    assert cr.env_is_stale(POD_START, POD_START - timedelta(hours=1)) is False


@pytest.mark.parametrize(
    "pod_start,sealed", [(None, POD_START), (POD_START, None), (None, None)]
)
def test_an_unreadable_side_is_unknown_rather_than_fresh(pod_start, sealed):
    # None is not False. Reading it as "fresh" would let a check that
    # could not measure anything hand down the verdict of one that had.
    assert cr.env_is_stale(pod_start, sealed) is None


def test_main_cannot_judge_a_snapshot_older_than_the_secret(monkeypatch, capsys, tmp_path):
    """The exact 2026-09-05 shape: a reseal landed while this pod ran."""
    monkeypatch.setattr(cr, "pod_started_at", lambda: POD_START)
    monkeypatch.setattr(cr, "sealed_written_at", lambda: POD_START + timedelta(hours=12))
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(NOW - timedelta(days=35)), "pro"))
    disk = tmp_path / "creds.json"
    disk.write_text(blob(millis(datetime.now(timezone.utc) + timedelta(hours=5)), "max"))

    assert cr.main(["--disk", str(disk)]) == 1
    out = capsys.readouterr().out
    assert "CANNOT JUDGE" in out
    # The precondition: this same input is a RAISE once the freshness
    # gate says the environment is current. Without this line the test
    # would still pass if the gate swallowed every verdict.
    assert "RAISE       " not in out
    assert "kubeseal" not in out


def test_a_fresh_environment_still_raises_on_a_stale_secret(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cr, "pod_started_at", lambda: POD_START)
    monkeypatch.setattr(cr, "sealed_written_at", lambda: POD_START - timedelta(hours=1))
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(NOW - timedelta(days=35)), "pro"))
    disk = tmp_path / "creds.json"
    disk.write_text(blob(millis(datetime.now(timezone.utc) + timedelta(hours=5)), "max"))

    assert cr.main(["--disk", str(disk)]) == 2
    assert "RAISE" in capsys.readouterr().out


def test_unknown_freshness_says_so_and_does_not_change_the_verdict(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(NOW - timedelta(days=35)), "pro"))
    disk = tmp_path / "creds.json"
    disk.write_text(blob(millis(datetime.now(timezone.utc) + timedelta(hours=5)), "max"))

    assert cr.main(["--disk", str(disk)]) == 2
    out = capsys.readouterr().out
    assert "NOT JUDGED  whether the snapshot in this pod's environment" in out
    assert "RAISE" in out


def test_a_kubectl_that_cannot_answer_is_not_a_timestamp():
    assert REAL_SEALED_WRITTEN_AT(read=lambda args: None) is None
    assert REAL_SEALED_WRITTEN_AT(read=lambda args: "") is None
    assert REAL_SEALED_WRITTEN_AT(read=lambda args: "not a stamp") is None


def test_pod_started_at_needs_a_hostname():
    assert REAL_POD_STARTED_AT(read=lambda args: "2026-09-04T20:50:25Z", env={}) is None
    assert REAL_POD_STARTED_AT(
        read=lambda args: "2026-09-04T20:50:25Z", env={"HOSTNAME": "p"}
    ) == POD_START


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-09-04T20:50:25Z", POD_START),
        ("2026-09-04T20:50:25.123456Z", POD_START.replace(microsecond=123456)),
        ("2026-09-04T20:50:25+00:00", POD_START),
        ("2026-09-04T22:50:25+02:00", POD_START),
    ],
)
def test_every_stamp_shape_the_api_server_can_emit_parses(raw, expected):
    # A shape that does not parse returns None, which is the *unknown*
    # branch -- a caveat line and no change of verdict. So a parser that
    # only spells one shape is a gate that silently stops gating. The
    # +02:00 row is the one that would pass while being wrong if the
    # offset were dropped rather than applied.
    assert cr._stamp(raw) == expected


def test_a_kubectl_that_is_absent_or_fails_is_not_a_timestamp(monkeypatch):
    import subprocess

    def missing(*a, **k):
        raise FileNotFoundError("kubectl")

    monkeypatch.setattr(subprocess, "run", missing)
    assert cr._kubectl(["get", "pod"]) is None

    def timed_out(*a, **k):
        raise subprocess.TimeoutExpired(cmd="kubectl", timeout=20)

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert cr._kubectl(["get", "pod"]) is None

    class Done:
        def __init__(self, code, out):
            self.returncode = code
            self.stdout = out

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done(1, "denied"))
    assert cr._kubectl(["get", "pod"]) is None
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done(0, ""))
    assert cr._kubectl(["get", "pod"]) is None
    # The precondition: the same wrapper does return a value when kubectl
    # answers, so the four Nones above are the handling and not a wrapper
    # that can only ever return None.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: Done(0, " x \n"))
    assert cr._kubectl(["get", "pod"]) == "x"


# --- when this loop's own login dies -----------------------------------------
#
# A separate question from every test above it. Those all ask "would a
# restore from the Secret work"; these ask "when does the credential this
# loop is running on stop working", which nothing in this loop can move.


def live_blob(access_hours, refresh=None):
    extra = {} if refresh is None else {"refreshTokenExpiresAt": millis(refresh)}
    return json.loads(
        blob(millis(NOW + timedelta(hours=access_hours)), "max", extra)
    )["claudeAiOauth"]


def test_a_healthy_refresh_token_is_not_a_finding():
    findings, expiry = cr.judge_live_refresh(
        live_blob(8, NOW + timedelta(days=11.5)), NOW
    )
    assert findings == []
    assert expiry == NOW + timedelta(days=11.5)


def test_a_refresh_token_inside_the_recovery_window_raises():
    """One hour inside the 30 hours the 2026-08-17 recovery actually took."""
    findings, expiry = cr.judge_live_refresh(
        live_blob(8, NOW + timedelta(hours=cr.OUTAGE_HOURS - 1)), NOW
    )
    assert [f["state"] for f in findings] == ["login-expiring"]
    assert "interactive login" in findings[0]["detail"]
    assert "no margin left" in findings[0]["detail"]
    # The precondition: the same credential one hour the other side of the
    # window is clean, so this is the threshold discriminating rather than
    # the function raising on everything.
    clean, _ = cr.judge_live_refresh(
        live_blob(8, NOW + timedelta(hours=cr.LOGIN_LEAD_HOURS + 1)), NOW
    )
    assert clean == []


def test_the_alarm_fires_before_his_longest_silence_not_after():
    """The defect this threshold exists for.

    `OUTAGE_HOURS` is how long the recovery took once it started. It says
    nothing about getting the ask in front of the one person who can do
    it, and he has gone 56.7 hours without reading a journal card inside
    the last month. An alarm with 30 hours of lead can therefore be raised
    entirely inside a stretch he is not reading -- true, and unheard.
    """
    inside_his_silence = cr.OUTAGE_HOURS + cr.OWNER_SILENCE_HOURS / 2.0
    findings, _ = cr.judge_live_refresh(
        live_blob(8, NOW + timedelta(hours=inside_his_silence)), NOW
    )
    assert [f["state"] for f in findings] == ["login-expiring"]
    # It says which of the two thresholds it tripped, because "he has not
    # seen this yet" and "there is no time left to recover" are different
    # situations and only the second one is an emergency.
    assert "no margin left" not in findings[0]["detail"]
    assert "reach him" in findings[0]["detail"]
    # The precondition: the same credential just outside the lead is clean,
    # so the window has an edge rather than swallowing everything.
    clean, _ = cr.judge_live_refresh(
        live_blob(8, NOW + timedelta(hours=cr.LOGIN_LEAD_HOURS + 1)), NOW
    )
    assert clean == []


def test_an_already_expired_refresh_token_raises_and_says_so():
    findings, _ = cr.judge_live_refresh(
        live_blob(8, NOW - timedelta(hours=2)), NOW
    )
    assert [f["state"] for f in findings] == ["login-expiring"]
    assert "expired" in findings[0]["detail"]


def test_a_short_access_token_alone_never_raises():
    """The trap this whole function exists to avoid.

    `expiresAt` on the live copy is hours wide by design -- the CLI
    refreshes it. A fallback to it here would raise every afternoon on a
    system that is working perfectly.
    """
    findings, expiry = cr.judge_live_refresh(live_blob(-1, None), NOW)
    assert findings == []
    assert expiry is None


def test_login_deadline_survives_the_stale_environment_return(
    monkeypatch, capsys, tmp_path
):
    monkeypatch.setattr(cr, "pod_started_at", lambda: POD_START)
    monkeypatch.setattr(cr, "sealed_written_at", lambda: POD_START + timedelta(hours=12))
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(NOW - timedelta(days=35)), "pro"))
    disk = tmp_path / "creds.json"
    soon = datetime.now(timezone.utc) + timedelta(hours=cr.OUTAGE_HOURS - 1)
    disk.write_text(
        blob(
            millis(datetime.now(timezone.utc) + timedelta(hours=5)),
            "max",
            {"refreshTokenExpiresAt": millis(soon)},
        )
    )

    assert cr.main(["--disk", str(disk)]) == 2
    out = capsys.readouterr().out
    assert "RAISE the live credential's refresh token expires" in out
    # The precondition: the snapshot half is still unjudgeable here, so
    # this exit code came from the login half rather than from the gate
    # having been removed.
    assert "CANNOT JUDGE" in out


def test_an_unreadable_live_copy_is_not_a_clean_login(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv(cr.SECRET_ENV, blob(millis(NOW + timedelta(days=20))))
    assert cr.main(["--disk", str(tmp_path / "absent.json")]) >= 1
    out = capsys.readouterr().out
    assert "when this loop's login expires is unknown" in out
