"""Do the numbers on the goal documents still match their instruments?

Issue #227's seventh rule is *"monthly objectives, weekly check -- target
versus current number says everything"*, and nothing ran the check. Every
``now:`` in `goals.md` and `project-goals.md` is a number some cycle typed
after running `tools.goal_measures` by hand, and `goal_measures` is imported
by nothing in production -- only by its own tests. That is precisely how
`goals.md` went stale before: its own docstring records four goals drifting
from their instruments by 2026-08-28 because "the Monday heartbeat that types
these numbers in has fired zero times since it was created". The scoreboard on
`/plan` was then a series of a cycle's arithmetic rather than of a
measurement, and `project-goals.md` inherited the same shape on 2026-09-13.

So this fetches the four documents the measurement needs out of the vault and
runs `tools.goal_measures --exit-on-drift` over them. It takes no measurement
of its own and holds no opinion of its own: the drift verdict comes from the
one `has_drifted` every renderer in that module already uses, so the report
and the status cannot disagree.

One carve-out sits beside it, in the same module and used by the same
renderer: `kpi_drift_crosses_bounds`. A KPI is a range rather than a target,
every instrumented one here reads a rolling window, and two of them drifted
again within an hour of Cycle 1576 repairing them -- so a KPI that moved
without leaving its own range is printed as having moved and is not counted.
Key results keep the strict comparison, because a target is read against the
digit. The report still names both, so the two can never disagree about what
was seen, only about what is worth acting on.

    python3 -m tools.goal_drift            # the watcher: report, never write
    python3 -m tools.goal_drift --repair   # the repairer: write the vault back

**The default never writes**, and that is still the whole point of the
watcher: it says the numbers are stale, it does not quietly restate them
while the owner is looking at them.

`--repair` is the other half, and it exists because the hand-repair never
happens either. The docstring above used to end by saying repairing was
"`goal_measures --write` plus a vault read-modify-write with an `if_rev`
guard, which is a cycle's decision" -- true, and in the twenty-five cycles
after it was written the sweep reported drift every time and three cycles
assembled that read-modify-write by hand. The rest left it, because it is
six commands and the standing instruction was something else. That is the
exact shape of the failure this module was built to end: a number a human
has to retype is a number that goes stale. So the six commands are one
flag now, and what stays a cycle's decision is *typing the flag*: nothing
in `tools.preflight` passes it, and it is not wired to a heartbeat.

Two guards sit on the write, because it touches documents the owner edits
from his phone:

* Each document is re-read with `get --rev-file` and written with
  `put --if-rev-file`, so an edit he made while the measurement ran is a
  409 rather than a silent clobber.
* **A repair may not change the document's line count.** `set_field_in_goals`
  and its KPI twin swap a digit inside one existing line and can never add
  or remove one, so a line-count move means something other than a
  measurement edited the file, and the write is refused with the counts
  named. That is the cheap tripwire against every failure mode where the
  local copy is not the document plus new numbers.

A document that already carries every measured number is not written at
all -- a no-op write is a real chance to lose his edit for nothing.

Exit codes are `tools.preflight`'s contract: `2` a written number disagrees
with its instrument in a way that changes what the document claims, `1` a
document it needed could not be read, `0` every instrumented number matches
or has only aged inside its own guardrail. A key result or KPI whose instrument has no
reading to take -- `pm-kr-calibration` before anything records a prediction,
Marcus's coach numbers before he taps the coach -- is not drift in either
direction and is counted as neither.

`vault_tool.py` lives only on the bridge pod, which is where `preflight`
runs; from the runner pod this exits 1 rather than pretending the documents
were clean.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.decisions import DECISIONS_PATH
from agora_runner.expectations import EXPECTATIONS_PATH
from agora_runner.nova_plan import GOALS_PATH
from agora_runner.project_goals import PROJECT_GOALS_PATH

#: `--goals` and `--project-goals` are what is being judged, so neither can be
#: missing. `expectations.md` and `decisions.md` are read only so that two
#: Product management key results report a reading instead of "not measured";
#: without them those two are simply not judged, which is a smaller loss than
#: refusing to judge the other fifteen.
REQUIRED = (("--goals", GOALS_PATH), ("--project-goals", PROJECT_GOALS_PATH))
OPTIONAL = (("--expectations", EXPECTATIONS_PATH),
            ("--decisions", DECISIONS_PATH))

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: Opt-in, and taken here rather than passed through to `goal_measures`,
#: because the write this turns on is the vault write and not that module's
#: `--write` into a local copy.
REPAIR_FLAG = "--repair"


def fetch(path, rev_file=None):
    """`(text, ok)` for one vault document, `ok` False when it could not be read.

    A missing document reads as unreadable here, unlike in
    `tools.project_goals_check` where an absent `project-goals.md` is the
    legitimate state before step 2 has written any content. There is no
    legitimate state in which the document this measures does not exist: if
    it is gone, the right answer is "I could not check", not "clean".
    """
    command = [sys.executable, VAULT_TOOL, "get", path]
    if rev_file is not None:
        # Recorded on the read so `--repair` can send it back as `if_rev`.
        # Asked for on the same call rather than a second `get`, because two
        # reads are two revisions and the guard would then be against the
        # wrong one.
        command += ["--rev-file", str(rev_file)]
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if done.returncode != 0 or "[not found]" in done.stdout[:200]:
        return "", False
    return done.stdout, True


def put(path, local, rev_file):
    """Write one repaired document back. `None` on success, else why not.

    `--if-rev-file` makes this a compare-and-swap against the revision the
    read was served at, so an edit the owner made from his phone while the
    measurement was running comes back as a conflict rather than being
    overwritten. `--allow-shrink` is deliberately NOT passed: a repair swaps
    digits and cannot shrink a document, so the client's own collapse guard
    is free protection here and there is no case where waiving it is right.
    """
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, "put", path, str(local),
             "--if-rev-file", str(rev_file)],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"could not run the vault tool: {exc}"
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        return detail[-1] if detail else f"the vault tool exited {done.returncode}"
    return None


def repair(documents, writer=None):
    """Put back every document a measurement changed. Exit code for `main`.

    `documents` is `(path, local, rev_file, before)` per required document.
    Three outcomes per document and all three are printed, because a repairer
    that says nothing is indistinguishable from one that did nothing:
    unchanged, written, or refused with the reason.
    """
    # Resolved here and not as a default argument: a default binds `put` at
    # definition time, so a test that replaces the module's `put` would still
    # have driven the real vault client.
    writer = writer or put
    written, refused = [], []
    for path, local, rev_file, before in documents:
        after = Path(local).read_text(encoding="utf-8")
        if after == before:
            print(f"  = {path} already carried every measured number")
            continue
        # `set_field_in_goals` and `set_field_in_kpi` swap a value inside one
        # existing line and can never add or remove one. A line-count move
        # therefore means the local copy is not this document plus new
        # numbers, and no repair is worth finding out what else it is.
        if after.count("\n") != before.count("\n"):
            refused.append(path)
            print(f"  ! {path} NOT written: the repaired copy has "
                  f"{after.count(chr(10))} line(s) against the "
                  f"{before.count(chr(10))} read out of the vault, and a "
                  f"measurement only ever rewrites a value inside an "
                  f"existing line", file=sys.stderr)
            continue
        error = writer(path, local, rev_file)
        if error:
            refused.append(path)
            print(f"  ! {path} NOT written: {error}", file=sys.stderr)
            continue
        written.append(path)
        print(f"  + {path} written back")
    # Last line, so `tools.preflight`'s one-line summary would read it -- even
    # though nothing in the sweep passes `--repair`.
    print(f"REPAIRED {len(written)} document(s) in the vault, "
          f"{len(refused)} refused")
    return 1 if refused else 0


def main(argv=None):
    from tools.goal_measures import main as measure

    argv = list(argv or [])
    repairing = REPAIR_FLAG in argv
    argv = [arg for arg in argv if arg != REPAIR_FLAG]
    with tempfile.TemporaryDirectory(prefix="goal-drift-") as workdir:
        args, documents = [], []
        for flag, path in REQUIRED:
            rev_file = None
            if repairing:
                rev_file = Path(workdir) / (Path(path).name + ".rev")
            text, ok = fetch(path, rev_file)
            if not ok:
                print(f"could not read {path} out of the vault -- "
                      f"nothing was judged", file=sys.stderr)
                return 1
            local = Path(workdir) / Path(path).name
            local.write_text(text, encoding="utf-8")
            args += [flag, str(local)]
            documents.append((path, local, rev_file, text))
        for flag, path in OPTIONAL:
            text, ok = fetch(path)
            if not ok:
                print(f"  ! {path} could not be read, so the key result it "
                      f"feeds is not judged this sweep")
                continue
            local = Path(workdir) / Path(path).name
            local.write_text(text, encoding="utf-8")
            args += [flag, str(local)]
        if not repairing:
            return measure(args + ["--exit-on-drift"] + argv)
        # `--write` and `--exit-on-drift` are refused together one layer down:
        # repairing makes the drift go away, so a status taken afterwards is
        # always clean. The repair's own exit code is about the writes.
        code = measure(args + ["--write"] + argv)
        if code != 0:
            print("the measurement did not finish, so nothing was written "
                  "back to the vault", file=sys.stderr)
            return code
        return repair(documents)


if __name__ == "__main__":
    raise SystemExit(main())
