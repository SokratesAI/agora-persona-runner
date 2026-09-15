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

**A command the shell cannot run is not "not yet".** Exit 127 (command
not found) and 126 (not executable) are reported as BROKEN, run once
rather than retried, never handed to the detached watcher, and they make
the process exit 1 rather than 2. Cycle 1663 hand-rolled `until ! pgrep
-f tools.preflight` on the bridge pod, where `pgrep` is not installed:
the negation made 127 read as "the process is gone", so the loop declared
the wait over after ten seconds and the cycle read a nought-byte report as
a finished one. Waiting on it through this tool had the mirror of that
bug -- 127 read as "not yet", so the whole deadline burned and then an
`until pgrep ...; do sleep 10; done` loop was detached that can never
exit, with the report telling the reader to come back for its output. The
exit codes say which of the two answers you got: **2 means the thing has
not happened yet, 1 means this wait never measured anything.**
"""

import argparse
import os
import shlex
import subprocess
import sys
import time


class Condition:
    """One thing being waited on, and what it printed when it resolved."""

    # An exit status that means bash could not run the command word at all
    # -- 127 is "command not found", 126 is "found but not executable". A
    # condition that exits either of these is not "not yet"; it is a
    # condition that can never resolve, and treating it as transient is
    # what this class of bug looks like from the inside.
    CANNOT_RUN = (126, 127)

    def __init__(self, name, command):
        self.name = name
        self.command = command
        self.resolved = False
        self.broken = False
        self.exit_code = None
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
            cond.exit_code = code
            cond.output = out
            if code == 0:
                cond.resolved = True
                cond.elapsed = round(clock() - start, 1)
            elif code in Condition.CANNOT_RUN:
                # Drop it rather than retry it. Re-running a command the
                # shell cannot find burns the whole deadline and then
                # detaches an `until` loop that never exits, so the cycle
                # is told to read a handoff file nothing will ever write.
                cond.broken = True
            else:
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
        elif cond.broken:
            lines.append(
                "=== %s: BROKEN -- exit %s, the shell could not run this command. "
                "Not 'not yet': it can never resolve, so it was not retried and "
                "not detached." % (cond.name, cond.exit_code)
            )
        else:
            lines.append("=== %s: STILL PENDING" % cond.name)
        body = cond.output.rstrip("\n")
        if body:
            lines.extend(body.split("\n"))
    broken = [c for c in conditions if c.broken]
    if pending:
        lines.append("")
        lines.append(
            "%d still pending; a detached watcher is writing to %s -- read it in a later turn."
            % (len(pending), path)
        )
    if broken:
        lines.append("")
        lines.append(
            "%d condition(s) could not be run at all: %s. Fix the command; waiting longer will not help."
            % (len(broken), ", ".join(c.name for c in broken))
        )
    if not pending and not broken:
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
    # A broken condition outranks a pending one: 2 says "the thing you are
    # waiting for has not happened yet", which is a fact about the world,
    # and 1 says "this wait never measured anything", which is a fact about
    # the instrument. They call for opposite next moves.
    if any(c.broken for c in conditions):
        return 1
    return 0 if not pending else 2


if __name__ == "__main__":
    sys.exit(main())
