"""Block on several conditions at once instead of polling each one for a turn.

Cost review, 2026-08-30. Across the 201 cycles I ran between 08-26 and
08-30, 6.2% of my Bash turns (1046 of 16851) were a turn whose entire
command was `cat`/`tail` of a scratch file in /tmp -- me poking at the
output of a `nohup ... &` watcher I had started earlier. The median cycle
spent 4.8% of its Bash turns that way. A turn is not free: it re-sends
the whole conversation, so at the ~19k weighted tokens my median turn
costs, that run of pokes is roughly 100k weighted tokens a cycle spent
learning nothing except "not yet".

The pattern exists for a good reason and the reason turned out to be
false. I had it written down that this harness backgrounds anything
slower than about a second, so a blocking wait would hand me a task id
rather than an answer and the only safe shape was detach-then-poll. I
measured it this morning: a 25-second foreground command returns its
output in the same turn, and the Bash timeout goes to 600 seconds. So a
wait can simply block.

What this does NOT do is drop a check. Every condition is still run,
every one still reports, and the command's own stdout is reproduced
verbatim -- the same contract `tools.preflight` has. It removes
round-trips, not measurements. And it keeps the old shape as the
fallback rather than the default: if the deadline passes with something
still unresolved, the remaining conditions are handed to a detached
process writing to a file, and the path is printed, so a later turn
picks up exactly where polling would have left it. Nothing is lost by
guessing the deadline too low.

    python3 -m tools.waitfor \
        --deadline 240 \
        'argo:kubectl get application sokratesai-infra -n argocd -o jsonpath="{.status.sync.status}" | grep -qx Synced' \
        'ping:kubectl get pods -n obsidian | grep -q nova-alive-ping-2980022'

Each argument is `name:shell command`. A condition is resolved when its
command exits 0. `poll` is pure and takes an injected clock and runner so
the arithmetic is testable without waiting on a real minute.
"""

import argparse
import os
import shlex
import subprocess
import sys
import time


class Condition:
    """One thing being waited on, and what it printed when it resolved."""

    def __init__(self, name, command):
        self.name = name
        self.command = command
        self.resolved = False
        self.elapsed = None
        self.output = ""

    def __repr__(self):  # pragma: no cover - debugging aid
        return "Condition(%r, resolved=%r)" % (self.name, self.resolved)


def parse_condition(spec):
    """Split `name:command` into a Condition.

    The split is on the first colon only, because a command is full of
    them -- a jsonpath, a URL, a `sed -n '1,5p'` all carry colons and
    splitting on every one silently truncates the command being run.
    """
    if ":" not in spec:
        raise ValueError("condition %r has no name: expected 'name:command'" % spec)
    name, command = spec.split(":", 1)
    name = name.strip()
    command = command.strip()
    if not name:
        raise ValueError("condition %r has an empty name" % spec)
    if not command:
        raise ValueError("condition %r has an empty command" % spec)
    return Condition(name, command)


def run_shell(command):
    """Run a condition once. Returns (exit_code, combined output)."""
    proc = subprocess.run(
        ["bash", "-lc", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return proc.returncode, proc.stdout


def poll(conditions, deadline, interval, runner=None, clock=time.monotonic,
         sleeper=time.sleep):
    """Block until every condition resolves or the deadline passes.

    Returns the list of conditions still unresolved. Pure with respect to
    the clock and the runner so a test can drive a hundred simulated
    seconds without spending them.
    """
    # Resolved here rather than as a default argument: a default binds at
    # import time, so monkeypatching `run_shell` on the module would not
    # reach it and a test would silently exercise the real shell.
    runner = runner or run_shell
    start = clock()
    pending = list(conditions)
    while pending:
        still = []
        for cond in pending:
            code, out = runner(cond.command)
            if code == 0:
                cond.resolved = True
                cond.elapsed = round(clock() - start, 1)
                cond.output = out
            else:
                cond.output = out
                still.append(cond)
        pending = still
        if not pending:
            break
        if clock() - start >= deadline:
            break
        sleeper(interval)
    return pending


def detach(pending, path, interval):
    """Hand the unresolved conditions to a background process.

    This is the old detach-then-poll shape, kept as the fallback so that
    a deadline guessed too short costs a later turn rather than the
    answer. Returns the command that was launched, so a test can assert
    the handoff without spawning anything.
    """
    parts = []
    for cond in pending:
        parts.append(
            "until %s; do sleep %d; done; echo 'RESOLVED %s'"
            % (cond.command, interval, cond.name)
        )
    script = "; ".join(parts) if parts else "true"
    launched = "nohup bash -lc %s > %s 2>&1 &" % (shlex.quote(script), shlex.quote(path))
    subprocess.Popen(
        ["bash", "-lc", launched],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return launched


def report(conditions, pending, path=None):
    """Render every condition, resolved or not, with its own output."""
    lines = []
    for cond in conditions:
        if cond.resolved:
            lines.append("=== %s: RESOLVED after %ss" % (cond.name, cond.elapsed))
        else:
            lines.append("=== %s: STILL PENDING" % cond.name)
        body = cond.output.rstrip("\n")
        if body:
            lines.extend(body.split("\n"))
    if pending:
        lines.append("")
        lines.append(
            "%d still pending; a detached watcher is writing to %s -- read it in a later turn."
            % (len(pending), path)
        )
    else:
        lines.append("")
        lines.append("All %d conditions resolved." % len(conditions))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Block on several conditions at once instead of polling each for a turn."
    )
    parser.add_argument("conditions", nargs="+", help="'name:shell command', exit 0 means resolved")
    parser.add_argument(
        "--deadline",
        type=int,
        default=240,
        help="seconds to block in the foreground before detaching (default 240; Bash allows 600)",
    )
    parser.add_argument("--interval", type=int, default=10, help="seconds between rounds (default 10)")
    parser.add_argument(
        "--handoff",
        default=os.environ.get("NOVA_WAITFOR_HANDOFF", "/tmp/waitfor-pending.txt"),
        help="file the detached watcher writes to when the deadline passes",
    )
    args = parser.parse_args(argv)

    conditions = [parse_condition(spec) for spec in args.conditions]
    pending = poll(conditions, args.deadline, args.interval)
    if pending:
        detach(pending, args.handoff, args.interval)
    print(report(conditions, pending, args.handoff))
    return 0 if not pending else 2


if __name__ == "__main__":
    sys.exit(main())
