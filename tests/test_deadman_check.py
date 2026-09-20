"""The decision path of the off-box deadman, as a pure function.

Every branch here runs with no network, no ref and no GitHub: an outage
alarm whose own tests need the thing being watched would be untestable
during exactly the failure it exists for.
"""
from datetime import datetime, timedelta, timezone

from tools.deadman_check import (
    alarm_body,
    assess,
    assess_channel,
    assess_heartbeats,
    degraded_alarm_body,
    heartbeat_alarm_body,
    parse_heartbeat_token,
)

NOW = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc)
GRACE = timedelta(minutes=60)


def test_a_fresh_ping_is_ok():
    verdict, reason = assess(NOW - timedelta(minutes=4), NOW, GRACE)
    assert verdict == "OK"
    assert "4 minute(s)" in reason


def test_a_ping_inside_the_grace_is_still_ok():
    # 59 minutes is twelve missed pings and deliberately not an alarm: a
    # node reboot with image pulls eats that without anything being wrong.
    assert assess(NOW - timedelta(minutes=59), NOW, GRACE)[0] == "OK"


def test_a_ping_past_the_grace_is_stale():
    verdict, reason = assess(NOW - timedelta(minutes=61), NOW, GRACE)
    assert verdict == "STALE"
    assert "61 minute(s) ago" in reason
    assert "grace is 60" in reason


def test_the_boundary_belongs_to_ok():
    assert assess(NOW - GRACE, NOW, GRACE)[0] == "OK"


def test_a_missing_ref_is_never_rather_than_stale():
    # These are opposite findings — never deployed vs the box is gone —
    # and merging them sends whoever reads the alarm to the wrong half.
    verdict, reason = assess(None, NOW, GRACE)
    assert verdict == "NEVER"
    assert "never run" in reason


def test_a_ping_from_the_future_does_not_alarm():
    # Clock skew between the cluster and GitHub is small but real, and a
    # negative age must not wrap into a stale verdict.
    assert assess(NOW + timedelta(minutes=2), NOW, GRACE)[0] == "OK"


def test_the_two_verdicts_do_not_share_a_body():
    from tools.deadman_check import alarm_body

    never = alarm_body("NEVER", "x")
    stale = alarm_body("STALE", "x")
    assert "not** evidence that the cluster is down" in never
    assert "github-bot-token" in never
    assert "It has stopped" in stale
    assert "nova-alive-ping -n obsidian" in stale


# --- the alarm channel itself -------------------------------------------
#
# Added Cycle 534. Every test above this line exercises the decision about
# the ping; none of them ask whether the alarm could be delivered, and that
# is exactly the half that was broken. Issues were disabled on this repo the
# whole time slice 1 was "shipped", so `POST /issues` answers 410 -- and the
# only scheduled run so far went green because the ping was fresh and the
# alarm branch never executed.

def test_issues_disabled_is_a_broken_channel():
    ok, reason = assess_channel(False, ["EdvardGB"])
    assert ok is False
    assert "DISABLED" in reason
    assert "410" in reason


def test_unknown_issue_setting_is_broken_not_assumed_fine():
    ok, reason = assess_channel(None, ["EdvardGB"])
    assert ok is False
    assert "could not read" in reason


def test_an_unassignable_addressee_is_a_broken_channel():
    # Issues on, but the alarm would fall back to notifying whoever watches
    # the repo -- and nobody does: subscribers_count was 0 on 2026-08-27.
    ok, reason = assess_channel(True, ["sokrates-ai-user"])
    assert ok is False
    assert "EdvardGB" in reason


def test_issues_on_and_addressee_assignable_is_usable():
    ok, reason = assess_channel(True, ["EdvardGB", "sokrates-ai-user"])
    assert ok is True
    assert "EdvardGB" in reason


def test_the_alarm_body_addresses_him_by_name():
    # A bot-authored issue in an unwatched repo notifies nobody; the @mention
    # is a participating notification, which is on by default everywhere.
    for verdict in ("NEVER", "STALE"):
        assert alarm_body(verdict, "because").startswith("@EdvardGB ")


# --- the heartbeat verdict the ping carries out (idea #117) ------------------


def test_a_ping_from_before_the_change_carries_no_token():
    # None, not "unknown". "This cluster runs an older manifest" and "the
    # cluster tried and could not read /api/health" are different facts, and
    # only one of them means anything is wrong with the site.
    assert parse_heartbeat_token("nova alive 2026-08-27T15:00:00Z") is None
    verdict, reason = assess_heartbeats(None)
    assert verdict == "UNKNOWN"
    assert "older manifest" in reason


def test_the_token_is_read_off_the_subject_line():
    assert parse_heartbeat_token("nova alive 2026-08-27T15:00:00Z hb=ok") == "ok"
    # A name with spaces in it runs to the end of the line and is not clipped
    # at the first one.
    token = parse_heartbeat_token(
        "nova alive 2026-08-27T15:00:00Z hb=bad(2):Nova — ideas & research\n\nbody"
    )
    assert token == "bad(2):Nova — ideas & research"


def test_every_heartbeat_firing_is_the_clean_answer():
    verdict, reason = assess_heartbeats("ok")
    assert verdict == "OK"
    assert "firing" in reason


def test_a_stopped_heartbeat_is_the_alarm_and_names_it():
    verdict, reason = assess_heartbeats("bad(2):Nova — ideas & research")
    assert verdict == "BAD"
    assert "2 heartbeat(s)" in reason
    assert "Nova — ideas & research" in reason


def test_a_ping_that_could_not_read_the_site_is_never_an_alarm():
    # The cluster is demonstrably up -- it pinged. Alarming here would page him
    # about a NetworkPolicy, which is not what this issue says.
    verdict, _ = assess_heartbeats("unknown")
    assert verdict == "UNKNOWN"


def test_an_unknown_that_says_why_carries_the_reason_through():
    # The whole point of the change. Three of the fast rung's last four runs
    # failed printing only "unreadable", and the reason -- a refused connection
    # from `obsidian` to nova-site:8083 -- was in a CronJob pod log that
    # expires. The slug is reproduced verbatim rather than re-worded, because
    # the reader off-box has no second source for it.
    verdict, reason = assess_heartbeats(
        "unknown:URLError-urlopen_error_[Errno_111]_Connection_refused"
    )
    assert verdict == "UNKNOWN"
    assert "URLError-urlopen_error_[Errno_111]_Connection_refused" in reason
    # And it is read as a reason rather than falling through to the
    # unrecognised-token branch, which would also print the slug -- inside a
    # repr, under a line saying this watchdog does not know what it is looking
    # at. That branch passing for this token is a real answer wearing the face
    # of a parse failure.
    assert "unrecognised" not in reason
    assert "could not read /api/health" in reason


def test_the_three_silences_the_site_can_return_are_told_apart():
    # A missing block, a block reporting its own error and an empty list all
    # wore one `unknown` before. Each is a different thing to go and look at.
    for slug in ("no-heartbeats-block", "heartbeats-list-empty",
                 "heartbeats-error-agora_said_503"):
        verdict, reason = assess_heartbeats(f"unknown:{slug}")
        assert verdict == "UNKNOWN"
        assert reason.endswith(f": {slug}")
        assert "unrecognised" not in reason


def test_the_retried_refusal_token_reaches_the_reader_whole():
    # Cross-repo contract with `cronjobs/nova-alive-ping.yaml` in
    # platform-config. The ping now asks four times before giving up, because a
    # single instant refusal is what a k3s NetworkPolicy REJECT looks like when
    # a brand-new Job pod's IP has not reached the controller's ipset yet. The
    # attempt count is appended to the slug rather than kept in a pod log that
    # expires, so an off-box reader with no cluster access can tell a real
    # outage from that race -- which only works if this reproduces it verbatim
    # instead of reading a longer slug as a token it does not recognise.
    token = ("unknown:URLError-urlopen_error_[Errno_111]_Connection_refused"
             "-after-4-tries")
    verdict, reason = assess_heartbeats(token)
    # This asserted UNKNOWN until issue #259. The slug still has to reach the
    # reader whole -- that is what this test is for -- but an exhausted ladder
    # is the finding, not the absence of one.
    assert verdict == "DEGRADED"
    assert reason.endswith("-after-4-tries")
    assert "Connection_refused" in reason
    assert "unrecognised" not in reason


def test_the_outage_of_2026_09_19_is_now_an_alarm():
    # The exact token the ping wrote at 22:28 and 00:21 UTC, read off the two
    # failed `nova-deadman (fast rung)` runs. Both printed HEARTBEATS UNKNOWN
    # and exited 1, which opens no issue and notifies nobody, and the vault,
    # the journal and the backups stayed down for eight hours.
    token = "unknown:HTTPError-HTTP_Error_503:_Service_Unavailable-after-4-tries"
    verdict, reason = assess_heartbeats(token)
    assert verdict == "DEGRADED"
    assert "503" in reason
    assert "every one of 4 attempts" in reason


def test_one_failed_attempt_is_not_degraded():
    # The ladder exists for a real race: a Job pod can be REJECTed by a
    # NetworkPolicy that permits it, because its IP reaches the controller's
    # ipset on the controller's clock. A slug carrying a single attempt has not
    # cleared that race, so it stays the verdict that alarms nobody.
    verdict, reason = assess_heartbeats(
        "unknown:URLError-Connection_refused-after-1-tries")
    assert verdict == "UNKNOWN"
    assert "could not read" in reason


def test_a_failure_before_the_ladder_is_unknown_not_degraded():
    # No `-after-n-tries` tail means the ping never got as far as asking --
    # no python3 in the image, DNS gone. That is this watchdog's own problem
    # and says nothing about the site, so it must not open the site's alarm.
    verdict, _ = assess_heartbeats("unknown:NameError-python3_missing")
    assert verdict == "UNKNOWN"


def test_the_degraded_alarm_does_not_read_like_a_dead_box():
    # He checks whatever the first line points at. This alarm fires while the
    # box is demonstrably alive, so pointing him at the box wastes the call.
    body = degraded_alarm_body("because")
    assert body.startswith("@EdvardGB ")
    assert "**up**" in body
    assert "obsidian" in body
    assert body != heartbeat_alarm_body("because")


def test_a_bare_unknown_says_the_manifest_predates_the_reason():
    # `hb=unknown` with nothing after it is still legal and still never clean,
    # but it now means something specific: the cluster is running a ping older
    # than the reason slug, which is a fact about the manifest and not about
    # the site.
    verdict, reason = assess_heartbeats("unknown")
    assert verdict == "UNKNOWN"
    assert "did not say" in reason


def test_a_token_this_does_not_recognise_is_unknown_not_clean():
    # A future manifest writing something else must not read as healthy.
    verdict, reason = assess_heartbeats("hb")
    assert verdict == "UNKNOWN"
    assert "unrecognised" in reason


def test_the_heartbeat_alarm_says_the_box_is_up():
    # The whole point of the second issue: the body must not read like an
    # outage, or he checks the box first and finds nothing wrong with it.
    body = heartbeat_alarm_body("because")
    assert body.startswith("@EdvardGB ")
    assert "**up**" in body


def test_main_opens_the_degraded_alarm_and_exits_2(monkeypatch):
    # Through `main()`, not through `assess_heartbeats`. The pure verdict was
    # already right in spirit before this change and still raised nothing: what
    # was broken was the branch below it and the exit code, so a test that
    # stops at the verdict would have passed on 2026-09-19 too.
    from tools import deadman_check as dc

    calls = []
    monkeypatch.setattr(dc, "read_channel", lambda: (True, "issues enabled"))
    monkeypatch.setattr(
        dc, "read_ping",
        lambda: (datetime.now(timezone.utc),
                 "nova alive 2026-09-19T22:25:01Z "
                 "hb=unknown:HTTPError-HTTP_Error_503:_Service_Unavailable-after-4-tries"))
    monkeypatch.setattr(dc, "open_alarm_issue", lambda title=dc.TITLE: None)
    monkeypatch.setattr(dc, "clear_alarm",
                        lambda reason, *, title=dc.TITLE: calls.append(("clear", title)))
    monkeypatch.setattr(dc, "_gh", lambda *args: calls.append(("gh", args)) or "{}")

    assert dc.main() == 2
    opened = [a for kind, a in calls if kind == "gh"]
    assert opened, "main() raised no alarm at all"
    titles = [f for call in opened for f in call if f.startswith("title=")]
    assert [f"title={dc.DEGRADED_TITLE}"] == titles
    # and it must not close the alarm in the same breath
    assert ("clear", dc.DEGRADED_TITLE) not in calls
