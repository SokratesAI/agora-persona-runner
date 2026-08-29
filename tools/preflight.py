"""Run every step-1a status check in one call, and print only what is not clean.

The owner, capture 2026-08-28: *"I see that the median cycle now consumes much
more tokens now than what they used to do at the start of the Nova project,
earlier in August. Why? Make an effort to optimize your token usage."*

That is right, and Cycle 581 measured where it went. Against the vault cost
ledger (`nova/resources/cost-ledger.json`, 577 cycles back to 08-03), the
median cycle cost **1.16M weighted tokens over 08-03..08-10 and 1.74M over
08-24..08-28** -- half again as much. The decomposition is the useful part,
because it names the lever:

    median turns per cycle      64  ->  93     (+45%)
    median weighted per turn  17.5k -> 18.9k   (+8%)

So the prompt did not get much fatter per turn. **The cycle got longer.**
Nearly all of the growth is more turns, and the largest block of turns
this loop added in that window is right here: step 1a of `prompt.md` grew
from a handful of reads into fourteen separate status checks, each one its
own tool call, each one printing a full report that is then carried in
context for the remaining ~90 turns whether or not it found anything.

Thirteen of those fourteen answer "nothing to act on" on a normal morning.
That is thirteen turns and thirteen reports to say nothing happened.

**This does not drop anything, and that is deliberate.** The owner's rule
(`personality.md`, on the 400-chip cap): *"I want control. I want to know
what's going on in every corner of this system, but I also want the option
to not see it... That's an interface problem, and interface problems get
solved with an interface, never by throwing away the data."* So a check
that exits 0 collapses to its own last line -- which every one of these
tools writes as its summary, naming what it swept -- and a check that exits
1 or 2 is reproduced **in full, verbatim**, because that is the output a
cycle actually has to read.

The uniform exit contract is what makes this possible, and it was already
there: every one of these modules documents **2 = a finding to act on,
1 = something was unreadable and never reads as clean, 0 = nothing to act
on**. This aggregates them the same way: the overall status is the worst
one, so `preflight` exiting 0 means every check exited 0.

**A check that did not run must never look like a check that came back
clean.** So the roster is verified against the `tools/` directory before
anything runs, an unknown name is a hard error rather than a skip, and the
footer names every check that ran with its elapsed time. A crash inside a
check is a non-zero exit and gets the loud treatment, so the failure mode
of this tool is noisy, not silent -- which is the direction "How to work"
asks for when a negative result could otherwise be guaranteed in advance.

Checks run concurrently because they are independent and several are slow
(`pin_drift` ~30s, `security_alerts` ~14s against 21 repos); output is
printed in the declared order regardless, so two runs are comparable.
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys

#: The step-1a status checks, in the order `prompt.md` lists them.
#:
#: `tidy_workspace` and `top_board_rows` are deliberately NOT here. They do
#: not share the exit contract and their output is not a status line a cycle
#: can skim -- `top_board_rows` prints the pick itself and has to be read in
#: full, and `tidy_workspace` moves files. Collapsing either would hide the
#: thing the step exists to show.
CHECKS = (
    "security_alerts",
    "agentic_health",
    "doc_integrity",
    "cli_pin",
    "pin_drift",
    "eol_watch",
    "cli_features",
    "changelog_watch",
    "cache_health",
    "hook_cost",
    "heartbeat_health",
    "cycle_postmortem",
    "schedule_health",
    "helm_repo_health",
    "argocd_health",
    "crossplane_health",
    "ci_health",
)

#: Wall-clock ceiling per check. Above any of them by a wide margin --
#: the slowest measured is `pin_drift` at ~30s against 21 repos -- so a
#: check that hits this has hung, and a hang is reported as a failure
#: rather than waited on.
TIMEOUT_SECONDS = 240

STATUS_WORD = {0: "ok", 1: "UNREADABLE", 2: "ACT"}


def tools_dir():
    return os.path.dirname(os.path.abspath(__file__))


def unknown_checks(names, directory=None):
    """Names in `names` with no matching module in the tools directory.

    A typo here would otherwise run nothing and report nothing, which is
    the one outcome this tool must not have.
    """
    directory = directory or tools_dir()
    return [n for n in names if not os.path.isfile(os.path.join(directory, n + ".py"))]


def run_check(name):
    """Run one check as a subprocess. Returns (name, exit_code, output, seconds)."""
    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tools." + name],
            cwd=os.path.dirname(tools_dir()),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        output = (f"TIMED OUT after {TIMEOUT_SECONDS}s with no verdict. "
                  f"A hung check is not a clean check.")
        code = 1
    return name, code, output, time.monotonic() - started


def summary_line(output):
    """A pointer at what the check measured. **The verdict is the exit code.**

    The first version of this took the last non-empty line, on the belief
    that every check ends on its verdict. Measured against a real run of all
    fourteen: about half of them do not. `agentic_health`, `heartbeat_health`,
    `argocd_health`, `cli_features` and `schedule_health` each close on an
    explanatory footnote -- *"A heartbeat that is off on purpose carries
    '(disabled' in its own name"* -- which is true, useful in place, and says
    nothing about what this morning's sweep found. Collapsing a check to that
    line is a summary that cannot vary with the result, which is the
    positive-guaranteed-in-advance failure wearing a table.

    So the rule is narrower and it is a heuristic, stated rather than hidden:
    **the last line carrying a digit**, because every one of these tools
    reports its sweep as a count -- "Read 12 ArgoCD Application(s)", "Swept 11
    document(s)", "Caching healthy on all 7 day(s) judged". A count moves when
    the world moves; a footnote does not. Where no line carries a digit the
    last non-empty line is the fallback.

    It is still a guess at which line matters, and it is allowed to be one
    because nothing rests on it: the exit code is the verdict and it is exact,
    a non-clean check is reproduced in full regardless, and `--verbose` prints
    every check whole.
    """
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    if not lines:
        return "(no output)"
    for line in reversed(lines):
        if any(ch.isdigit() for ch in line):
            return line
    return lines[-1]


def render(results, stream=sys.stdout, verbose=False):
    """Print the collapsed report. `results` is a list of (name, code, output, seconds)."""
    worst = 0
    noisy = []
    print(f"{'check':20}{'verdict':12}{'s':>6}  summary", file=stream)
    for name, code, output, seconds in results:
        worst = max(worst, code)
        word = STATUS_WORD.get(code, f"EXIT {code}")
        print(f"{name:20}{word:12}{seconds:>6.1f}  {summary_line(output)}", file=stream)
        if code != 0 or verbose:
            noisy.append((name, code, output))

    for name, code, output in noisy:
        print(file=stream)
        print(f"===== {name}: exit {code} -- full output =====", file=stream)
        print(output.rstrip(), file=stream)

    print(file=stream)
    print(f"Ran {len(results)} check(s): {', '.join(n for n, _, _, _ in results)}.", file=stream)
    if worst == 0:
        print("Every check exited 0 -- nothing to act on, and each line above "
              "names what its check swept.", file=stream)
    else:
        print(f"{len(noisy)} check(s) did not come back clean; their full output is "
              f"above, unabridged. Overall exit {worst}.", file=stream)
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="CHECK",
                        help="run just these checks, by module name")
    parser.add_argument("--list", action="store_true",
                        help="print the roster and exit")
    parser.add_argument("--verbose", action="store_true",
                        help="reproduce every check in full, clean ones included")
    args = parser.parse_args(argv)

    names = list(args.only or CHECKS)
    if args.list:
        for name in names:
            print(name)
        return 0

    missing = unknown_checks(names)
    if missing:
        print(f"NO SUCH CHECK: {', '.join(missing)} -- refusing to run, because a "
              f"check that never ran must not read as a check that came back clean.",
              file=sys.stderr)
        return 1

    results = [None] * len(names)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(run_check, name): i for i, name in enumerate(names)}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()

    return render(results, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
