"""The preflight check that reads the Marcus reminder's own verdict.

The exit code is the whole product of this module, so every test asserts on
it. The verdict phrases are not re-spelled as literals in the assertions where
that can be avoided -- they are the reminder script's own output and this check
matches them as substrings, so a test that invented its own wording would be
testing a second copy of the contract rather than the one in the cluster.
"""

import json

import pytest

from tools import marcus_reminder


def _pods(*names_and_starts):
    items = [
        {
            "metadata": {"name": name},
            "status": {"startTime": started, "phase": "Succeeded"},
        }
        for name, started in names_and_starts
    ]
    return json.dumps({"items": items})


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(pods_json, logs, pods_rc=0):
    """A fake `subprocess.run` answering `get pods` and `logs` separately."""

    def run(cmd, **_kwargs):
        if "logs" in cmd:
            pod = cmd[cmd.index("logs") + 1] if cmd.index("logs") + 1 < len(cmd) else ""
            # The Pod name is the last positional before the flags.
            pod = [c for c in cmd if c.startswith("marcus-reminder")][0]
            if pod not in logs:
                return _Done(returncode=1, stderr="not found")
            return _Done(stdout=logs[pod])
        return _Done(returncode=pods_rc, stdout=pods_json)

    return run


DELIVERED = "'Tomorrow: Pull': delivered to 1 device(s): {\"sent\": 1}"
NOBODY = ("'Tomorrow: Pull': NO DEVICE IS SUBSCRIBED -- nothing was delivered. "
          "The reminder is wired end to end and waiting on Edvard tapping "
          "Enable reminders in Marcus.")
REST = "nothing planned for Wednesday; sending nothing."
REFUSED = "'Tomorrow: Pull': every subscribed device refused it: {\"sent\": 0, \"failed\": 1}"
MISCONFIGURED = "send refused with HTTP 401: {\"error\": \"no\"}"


def test_delivered_is_clean(capsys):
    run = _runner(_pods(("marcus-reminder-1-a", "2026-09-08T18:00:00Z")),
                  {"marcus-reminder-1-a": DELIVERED})
    assert marcus_reminder.main([], runner=run) == 0
    assert "DELIVERED" in capsys.readouterr().out


def test_a_rest_day_is_clean_not_a_silent_failure(capsys):
    """Two nights in seven are `Rest` in the current block, so silence is the
    correct behaviour and must not read the same as a broken send."""
    run = _runner(_pods(("marcus-reminder-1-a", "2026-09-08T18:00:00Z")),
                  {"marcus-reminder-1-a": REST})
    assert marcus_reminder.main([], runner=run) == 0


def test_no_subscribed_device_raises(capsys):
    """The state the whole check exists for: the job exits 0, `cronjob_health`
    reads it as healthy, and nothing was delivered."""
    run = _runner(_pods(("marcus-reminder-1-a", "2026-09-08T18:00:00Z")),
                  {"marcus-reminder-1-a": NOBODY})
    assert marcus_reminder.main([], runner=run) == 2
    out = capsys.readouterr().out
    assert "Enable reminders" in out


@pytest.mark.parametrize("text", [REFUSED, MISCONFIGURED])
def test_a_refused_send_raises(text):
    run = _runner(_pods(("marcus-reminder-1-a", "2026-09-08T18:00:00Z")),
                  {"marcus-reminder-1-a": text})
    assert marcus_reminder.main([], runner=run) == 2


def test_an_unrecognised_log_raises_rather_than_reading_as_healthy():
    run = _runner(_pods(("marcus-reminder-1-a", "2026-09-08T18:00:00Z")),
                  {"marcus-reminder-1-a": "Traceback (most recent call last):"})
    assert marcus_reminder.main([], runner=run) == 2


def test_the_newest_run_decides_and_an_older_one_does_not():
    """An older `nobody` under a newer `delivered` is history, not a finding --
    the check would otherwise stay red forever after the first quiet night."""
    run = _runner(
        _pods(("marcus-reminder-2-b", "2026-09-08T18:00:00Z"),
              ("marcus-reminder-1-a", "2026-09-07T18:00:00Z")),
        {"marcus-reminder-2-b": DELIVERED, "marcus-reminder-1-a": NOBODY},
    )
    assert marcus_reminder.main([], runner=run) == 0


def test_pods_are_sorted_by_start_not_by_the_order_kubectl_returned_them():
    """kubectl returns Pods in name order, and the reminder's Job names carry a
    scheduling counter that is not a date -- so the newest is found by
    `startTime` or it is not found at all."""
    run = _runner(
        _pods(("marcus-reminder-1-a", "2026-09-07T18:00:00Z"),
              ("marcus-reminder-2-b", "2026-09-08T18:00:00Z")),
        {"marcus-reminder-2-b": NOBODY, "marcus-reminder-1-a": DELIVERED},
    )
    assert marcus_reminder.main([], runner=run) == 2


def test_unreadable_cluster_is_one_not_zero(capsys):
    run = _runner("", {}, pods_rc=1)
    assert marcus_reminder.main([], runner=run) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_no_pod_at_all_is_one_and_hands_the_question_to_cronjob_health(capsys):
    assert marcus_reminder.main([], runner=_runner(_pods(), {})) == 1
    assert "cronjob_health" in capsys.readouterr().out


def test_every_kept_log_unreadable_is_one_not_a_verdict(capsys):
    """A reaped history must not read as a healthy one, and it must not read as
    a broken reminder either -- there is nothing to judge."""
    run = _runner(_pods(("marcus-reminder-1-a", "2026-09-08T18:00:00Z")), {})
    assert marcus_reminder.main([], runner=run) == 1
    assert "unreadable history" in capsys.readouterr().out


def test_classify_reads_the_scripts_own_wording():
    """Every needle here is a literal in `agents/marcus-reminder.py`'s
    `outcome()` and its `main()`. If the script's wording moves, this is the
    test that says so rather than the check silently reading `unrecognised`."""
    assert marcus_reminder.classify(NOBODY)[0] == "nobody"
    assert marcus_reminder.classify(DELIVERED)[0] == "delivered"
    assert marcus_reminder.classify(REST)[0] == "rest day"
    assert marcus_reminder.classify(REFUSED)[0] == "refused"
    assert marcus_reminder.classify(MISCONFIGURED)[0] == "misconfigured"
    assert marcus_reminder.classify(None)[0] == "unreadable"


def test_a_delivered_log_that_also_mentions_nobody_reads_as_the_specific_one():
    """`delivered to ` is a general phrase and the refusal wording is specific;
    a log carrying both must not be read as healthy."""
    assert marcus_reminder.classify(DELIVERED + "\n" + NOBODY)[0] == "nobody"


def test_one_reaped_log_among_readable_ones_is_not_a_reaped_history():
    """`kubectl logs` on a Pod whose node has rotated the file fails for that
    Pod alone. Only a history where *nothing* is readable is a `1` -- one hole
    beside a readable newest run is still a verdict."""
    run = _runner(
        _pods(("marcus-reminder-2-b", "2026-09-08T18:00:00Z"),
              ("marcus-reminder-1-a", "2026-09-07T18:00:00Z")),
        {"marcus-reminder-2-b": DELIVERED},
    )
    assert marcus_reminder.main([], runner=run) == 0
