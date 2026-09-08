#!/usr/bin/env python3
"""How did the bridge process I run inside last shut down?

`agora-claude-bridge#112` gave the bridge a durable record of its own
lifecycle -- `started` / `signal` / `drained`, appended to
`/data/claude-home/bridge-lifecycle.jsonl`, which is on the PVC and so
outlives the Pod that Kubernetes deletes. That file settles the one
question that decides what to fix when a cycle disappears mid-turn: did
the process get SIGTERM and fail to drain in time, or was it never asked
to stop at all. Those are different bugs and until #112 they left
identical traces, which is how cycle 1245 read a `Recreate` ReplicaSet's
`creationTimestamp` as the START of a rollout when it is the moment
termination ENDED, and published a grace period being ignored that
nothing had measured.

**Nothing read that file on a schedule, and that is the whole reason this
exists.** A ledger nobody opens is the failure this loop names in its own
prompt: the evidence is written, correctly, into a file that only gets
looked at by a cycle that already suspects something. The bridge rolls
when I merge into it, which is exactly when I am busy with the merge, so
"a cycle will notice" is the same bet that lost four replies on
2026-09-08.

It judges by importing `bridge.lifecycle_log` from `/app` -- the running
bridge's own module, on this Pod -- rather than re-deriving the grouping
here. `lives()` is one decision about what counts as a process life and a
second copy of it in this repo would drift from the writer's copy without
anything failing. If that import is not available this is the runner Pod,
where the file does not exist either, and the honest answer is that the
check could not run.

Exit contract, the same one `reply_health` and `security_alerts` use:
**2 means the newest COMPLETED life ended badly** -- `killed_mid_drain`
(SIGTERM arrived, the drain never finished, and `turns_lost` is what that
cost) or `no_signal` (the process was never asked to stop: a crash, an
OOM kill, or a delete with no grace). 1 means the file or the module could
not be read, which never reads as clean. 0 means the last completed
shutdown was clean, or there is no completed life yet.

Three things it deliberately does not do:

- **It does not judge the newest life.** That is this process, still
  serving, and `lives()` marks it `running` for exactly that reason. A
  check that ran inside the life it was judging would report every bridge
  as un-drained forever.
- **It does not treat "no completed life" as a pass.** The file is one row
  old at the time of writing -- the bridge that carries this very cycle --
  so there is genuinely nothing to judge yet, and it says that in words
  rather than printing a clean verdict it did not earn. A file that is
  *absent* is a different thing and exits 1: `lifecycle_log.read` returns
  `[]` for a missing file and for an empty one, so this stats the path
  itself rather than inferring absence from an empty list.
- **It does not look further back than `--window-hours`.** A killed cycle
  cannot be repaired -- there is no fix to ship, only a rollout already
  past -- so an unbounded window would hand every future cycle the same
  death forever. Same call, and the same reason, as `reply_health`.
"""
import argparse
import os
import sys
import zoneinfo
from datetime import datetime, timedelta, timezone

#: The bridge's own source, on the bridge Pod. Not vendored: see the module
#: docstring -- `lives()` is the judgement and it must not fork.
BRIDGE_APP_DIR = os.environ.get("BRIDGE_APP_DIR", "/app")

DEFAULT_WINDOW_HOURS = 24.0

#: A fixed offset would be wrong for half the year and wrong by a whole
#: hour across the DST change, which is the shape of mistake `cronjob_health`
#: already documents for this cluster's crons.
OSLO = zoneinfo.ZoneInfo("Europe/Oslo")


def _load():
    """`(read, lives, path)` from the running bridge, or `None` if absent."""
    if BRIDGE_APP_DIR not in sys.path:
        sys.path.insert(0, BRIDGE_APP_DIR)
    try:
        from bridge.lifecycle_log import LIFECYCLE_FILE, lives, read
    except Exception:  # noqa: BLE001 -- any import failure means "not here"
        return None
    return read, lives, LIFECYCLE_FILE


def _oslo(stamp):
    """An ISO stamp from the ledger, rendered in the zone the owner reads."""
    try:
        return datetime.fromisoformat(stamp).astimezone(OSLO).strftime("%Y-%m-%d %H:%M:%S Oslo")
    except (TypeError, ValueError):
        return f"{stamp} (unparsed)"


def _started_at(life):
    try:
        return datetime.fromisoformat(life["started"].get("at"))
    except (TypeError, ValueError, KeyError):
        return None


def check(window_hours=DEFAULT_WINDOW_HOURS, now=None):
    """`(status, detail)` — status is the exit code, detail carries the lives.

    `now` is injected so a test does not have to move the clock, and it is
    the only clock this reads: the ledger's own `monotonic` field is
    per-process and cannot be compared across a restart.
    """
    loaded = _load()
    if loaded is None:
        return 1, {"error": f"no bridge.lifecycle_log under {BRIDGE_APP_DIR} -- "
                            "this is the runner Pod, where the ledger does not "
                            "exist either"}
    read, lives, path = loaded
    if not os.path.exists(path):
        return 1, {"error": f"{path} does not exist -- the bridge has not "
                            "recorded a lifecycle row since #112 shipped, or "
                            "the volume is not mounted here"}
    rows = read(path)
    if not rows:
        return 1, {"error": f"{path} is present and holds no readable row -- "
                            "an empty ledger is no instrument, not a clean "
                            "shutdown", "path": path}
    all_lives = lives(rows)
    finished = [lf for lf in all_lives if lf.get("verdict") != "running"]
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    in_window = [lf for lf in finished
                 if (_started_at(lf) or cutoff) >= cutoff]
    detail = {"path": path, "rows": len(rows), "lives": all_lives,
              "finished": finished, "in_window": in_window,
              "window_hours": window_hours}
    if not finished:
        return 0, detail
    newest = finished[-1]
    # `is`, not `in`: a life is a dict of dicts and `in` compares by value, so
    # two identical restarts would match each other.
    if (any(lf is newest for lf in in_window)
            and newest["verdict"] in ("killed_mid_drain", "no_signal")):
        return 2, detail
    return 0, detail


def _describe(life, out):
    started = _oslo(life["started"].get("at"))
    verdict = life.get("verdict")
    line = f"    {verdict:<16} started {started}"
    if life.get("signal"):
        line += f", SIGTERM {_oslo(life['signal'].get('at'))}"
        flight = life["signal"].get("in_flight")
        if flight is not None:
            line += f" with {flight} turn(s) in flight"
    if life.get("drained"):
        line += f", drained {_oslo(life['drained'].get('at'))}"
    print(line, file=out)


def report(status, detail, out=sys.stdout):
    if status == 1:
        print(f"COULD NOT READ THE BRIDGE LIFECYCLE LEDGER: {detail['error']}", file=out)
        return 1
    lives_ = detail["lives"]
    finished = detail["finished"]
    if status == 2:
        newest = finished[-1]
        if newest["verdict"] == "killed_mid_drain":
            print("THE BRIDGE WAS KILLED BEFORE IT FINISHED DRAINING — "
                  f"{newest['turns_lost']} turn(s) in flight when SIGTERM "
                  "arrived and no `drained` row followed.", file=out)
            print("    That is the grace period expiring or a SIGKILL, and it "
                  "is a drain to fix. A cycle that stopped mid-sentence around "
                  "then never reached him; relay what it did.", file=out)
        else:
            print("THE BRIDGE WAS NEVER ASKED TO STOP — the process ended with "
                  "no SIGTERM recorded at all.", file=out)
            print("    A crash, an OOM kill, or a delete with no grace. No "
                  "drain could have saved the turn, so fixing the drain would "
                  "be fixing the wrong thing — look at the Pod's own "
                  "termination instead.", file=out)
        for life in finished[-3:]:
            _describe(life, out)
        print(f"Read {detail['rows']} row(s) from {detail['path']}; the newest "
              f"completed life of {len(finished)} ended "
              f"{newest['verdict']!r}.", file=out)
        return 2
    for life in lives_[-3:]:
        _describe(life, out)
    if not finished:
        # **Not a pass, and it says so.** The ledger is younger than the
        # process reading it, so there is exactly one life in it and it is
        # this one. Printing "clean" here would be a positive result
        # guaranteed in advance.
        running = len(lives_)
        print(f"Read {detail['rows']} row(s) from {detail['path']}: "
              f"{running} life(s), none of them finished, so there is no "
              "shutdown to judge yet. Exit 0 is 'nothing to act on', not "
              "'the last shutdown was clean'.", file=out)
        return 0
    newest = finished[-1]
    print(f"Read {detail['rows']} row(s) from {detail['path']}: the newest of "
          f"{len(finished)} completed life(s) ended {newest['verdict']!r}, and "
          f"{len(detail['in_window'])} of them started inside the last "
          f"{detail['window_hours']:g}h window this judges.", file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--window-hours", type=float, default=DEFAULT_WINDOW_HOURS,
                        help="how far back a bad shutdown still raises "
                             "(default: %(default)s)")
    args = parser.parse_args(argv)
    return report(*check(window_hours=args.window_hours))


if __name__ == "__main__":
    sys.exit(main())
