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

    python3 -m tools.goal_drift

**It never writes.** Repairing the drift is `goal_measures --write` plus a
vault read-modify-write with an `if_rev` guard, which is a cycle's decision
and touches a document the owner edits from his phone. This says the numbers
are stale; it does not quietly restate them while he is looking at them.

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


def fetch(path):
    """`(text, ok)` for one vault document, `ok` False when it could not be read.

    A missing document reads as unreadable here, unlike in
    `tools.project_goals_check` where an absent `project-goals.md` is the
    legitimate state before step 2 has written any content. There is no
    legitimate state in which the document this measures does not exist: if
    it is gone, the right answer is "I could not check", not "clean".
    """
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, "get", path],
            capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return "", False
    if done.returncode != 0 or "[not found]" in done.stdout[:200]:
        return "", False
    return done.stdout, True


def main(argv=None):
    from tools.goal_measures import main as measure

    argv = list(argv or [])
    with tempfile.TemporaryDirectory(prefix="goal-drift-") as workdir:
        args = []
        for flag, path in REQUIRED:
            text, ok = fetch(path)
            if not ok:
                print(f"could not read {path} out of the vault -- "
                      f"nothing was judged", file=sys.stderr)
                return 1
            local = Path(workdir) / Path(path).name
            local.write_text(text, encoding="utf-8")
            args += [flag, str(local)]
        for flag, path in OPTIONAL:
            text, ok = fetch(path)
            if not ok:
                print(f"  ! {path} could not be read, so the key result it "
                      f"feeds is not judged this sweep")
                continue
            local = Path(workdir) / Path(path).name
            local.write_text(text, encoding="utf-8")
            args += [flag, str(local)]
        return measure(args + ["--exit-on-drift"] + argv)


if __name__ == "__main__":
    raise SystemExit(main())
