"""Tests for tools.waitfor -- the blocking multi-condition wait.

The clock and the shell runner are both injected, so these drive
hundreds of simulated seconds without spending one.
"""

import pytest

from tools import waitfor


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make(specs):
    return [waitfor.parse_condition(s) for s in specs]


def test_parse_condition_splits_on_the_first_colon_only():
    cond = waitfor.parse_condition(
        "argo:kubectl get app -o jsonpath='{.status.sync.status}' | grep -qx Synced"
    )
    assert cond.name == "argo"
    assert cond.command.endswith("grep -qx Synced")
    assert "jsonpath" in cond.command


@pytest.mark.parametrize("spec", ["no-colon-here", ":command", "name:  "])
def test_parse_condition_rejects_malformed_specs(spec):
    with pytest.raises(ValueError):
        waitfor.parse_condition(spec)


def test_every_condition_runs_even_when_the_first_one_is_slow():
    """The point of the tool: N conditions, one turn, none skipped."""
    seen = []

    def runner(command):
        seen.append(command)
        return (0 if command == "fast" else 1), "out:" + command

    clock = FakeClock()
    conditions = make(["a:slow", "b:fast", "c:slow"])
    pending = waitfor.poll(
        conditions, deadline=0, interval=1, runner=runner, clock=clock,
        sleeper=lambda s: None,
    )
    assert seen == ["slow", "fast", "slow"]
    assert [c.name for c in pending] == ["a", "c"]
    assert conditions[1].resolved is True


def test_poll_returns_as_soon_as_all_resolve():
    """Resolving early must not keep blocking to the deadline."""
    calls = {"n": 0}
    clock = FakeClock()

    def runner(command):
        calls["n"] += 1
        return (0 if calls["n"] >= 3 else 1), ""

    def sleeper(seconds):
        clock.advance(seconds)

    conditions = make(["only:cmd"])
    pending = waitfor.poll(
        conditions, deadline=1000, interval=10, runner=runner, clock=clock,
        sleeper=sleeper,
    )
    assert pending == []
    assert calls["n"] == 3
    assert clock.now == 20  # two sleeps, not a hundred


def test_poll_stops_at_the_deadline_and_reports_what_is_left():
    clock = FakeClock()

    def sleeper(seconds):
        clock.advance(seconds)

    conditions = make(["never:cmd"])
    pending = waitfor.poll(
        conditions, deadline=30, interval=10, runner=lambda c: (1, "not yet"),
        clock=clock, sleeper=sleeper,
    )
    assert [c.name for c in pending] == ["never"]
    assert clock.now == 30


def test_resolved_output_is_reproduced_verbatim():
    """A saving that drops the output would be buying tokens with capability."""
    body = "line one\nline two\n"
    conditions = make(["thing:cmd"])
    waitfor.poll(
        conditions, deadline=0, interval=1, runner=lambda c: (0, body),
        clock=FakeClock(), sleeper=lambda s: None,
    )
    text = waitfor.report(conditions, [])
    assert "line one" in text
    assert "line two" in text
    assert "RESOLVED" in text


def test_report_names_the_handoff_file_when_something_is_pending():
    conditions = make(["a:cmd"])
    pending = waitfor.poll(
        conditions, deadline=0, interval=1, runner=lambda c: (1, "nope"),
        clock=FakeClock(), sleeper=lambda s: None,
    )
    text = waitfor.report(conditions, pending, "/tmp/handoff.txt")
    assert "STILL PENDING" in text
    assert "/tmp/handoff.txt" in text
    assert "nope" in text


def test_detach_builds_an_until_loop_per_pending_condition(monkeypatch):
    launched = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            launched["argv"] = argv

    monkeypatch.setattr(waitfor.subprocess, "Popen", FakePopen)
    pending = make(["argo:kubectl get app", "ping:kubectl get pods"])
    cmd = waitfor.detach(pending, "/tmp/h.txt", 10)
    assert "until kubectl get app" in cmd
    assert "until kubectl get pods" in cmd
    assert "/tmp/h.txt" in cmd
    assert launched["argv"][0] == "bash"


def test_main_exits_two_when_something_is_still_pending(monkeypatch, capsys):
    monkeypatch.setattr(waitfor, "run_shell", lambda c: (1, "still no"))
    monkeypatch.setattr(waitfor, "detach", lambda p, path, i: "")
    code = waitfor.main(["a:cmd", "--deadline", "0", "--handoff", "/tmp/h.txt"])
    assert code == 2
    assert "STILL PENDING" in capsys.readouterr().out


def test_main_exits_zero_when_everything_resolves(monkeypatch, capsys):
    monkeypatch.setattr(waitfor, "run_shell", lambda c: (0, "yes"))
    code = waitfor.main(["a:cmd", "b:cmd2", "--deadline", "0"])
    assert code == 0
    assert "All 2 conditions resolved." in capsys.readouterr().out


def test_a_condition_the_shell_cannot_run_is_broken_not_pending():
    """Exit 127 means the command word does not exist -- that is never "not yet".

    This is the bug the change was made for: `pgrep` is not installed on
    the bridge pod, so a wait on it burned the whole deadline and then
    detached an `until pgrep ...; do sleep 10; done` loop that can never
    exit, while the report told the reader to come back for its output.
    """
    clock = FakeClock()
    calls = []

    def runner(command):
        calls.append(command)
        if "missing" in command:
            return 127, "bash: line 1: pgrep: command not found\n"
        return 0, "ok\n"

    conditions = make(["gone:pgrep missing", "fine:echo ok"])
    pending = waitfor.poll(conditions, deadline=300, interval=10,
                           runner=runner, clock=clock, sleeper=clock.advance)

    assert pending == []
    broken, good = conditions
    assert broken.broken is True and broken.resolved is False
    assert broken.exit_code == 127
    assert good.resolved is True
    # Run once and never retried -- two commands, not two rounds of two.
    assert len(calls) == 2


def test_a_broken_condition_is_never_handed_to_the_detached_watcher(monkeypatch, capsys):
    launched = []
    monkeypatch.setattr(waitfor, "detach",
                        lambda pending, path, interval: launched.append(list(pending)))
    monkeypatch.setattr(waitfor, "run_shell",
                        lambda command: (126, "bash: permission denied\n"))

    code = waitfor.main(["--deadline", "0", "nope:/etc/hosts"])

    assert code == 1
    assert launched == []
    out = capsys.readouterr().out
    assert "BROKEN -- exit 126" in out
    assert "waiting longer will not help" in out
    assert "All 1 conditions resolved." not in out


def test_broken_outranks_pending_in_the_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(waitfor, "detach", lambda pending, path, interval: None)

    def runner(command):
        return (127, "not found\n") if "nosuchbinary" in command else (1, "not yet\n")

    monkeypatch.setattr(waitfor, "run_shell", runner)

    code = waitfor.main(["--deadline", "0", "gone:nosuchbinary", "slow:false"])

    assert code == 1
    out = capsys.readouterr().out
    assert "gone: BROKEN" in out
    assert "slow: STILL PENDING" in out
