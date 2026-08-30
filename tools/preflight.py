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

One thing survives the collapse besides that line: **a clean check's own
statement that it could not judge part of its scope.** A check may honestly
exit 0 over a surface it never read -- nzbget's extension list sits behind a
password neither pod holds -- and collapsing that to a summary puts the
caveat out of sight in the one report a cycle reads every morning. See
`caveat_lines`; it costs the table one line today, measured.

The uniform exit contract is what makes this possible, and it was already
there: every one of these modules documents **2 = a finding to act on,
1 = something was unreadable and never reads as clean, 0 = nothing to act
on**. This aggregates them the same way: the overall status is the worst
one, so `preflight` exiting 0 means every check exited 0.

**A check that did not run must never look like a check that came back
clean.** So the roster is verified against the `tools/` directory before
anything runs, an unknown name is a hard error rather than a skip, and the
footer names every check that ran with its elapsed time.

That guard reads `CHECKS` against the `tools/` directory, so it cannot see
the failure one level up: **a roster that is itself out of date.** Cycle 644
ran this from a checkout still sitting on `nova/doc-integrity-frontmatter`,
a branch a previous cycle had merged and left behind, nineteen commits
behind `main`. It printed `Ran 19 check(s)` and a clean table, and the four
NAS checks -- the ones the owner calls the highest priority on this estate --
plus `cadence_control`, which moves my own heartbeat, did not exist in that
tree at all. They were absent from `CHECKS`, so `unknown_checks` had nothing
to compare and nothing to refuse. A check missing from the roster is
invisible to a guard that validates the roster.

So `source_revision` runs first, always, and it is deliberately *not* in
`CHECKS`: it is computed in this process rather than as a `tools/` module,
because a module would be missing from exactly the stale tree it is meant
to catch. It names the commit these checks came from and how far behind
`origin/main` it is, and **being behind raises 2** -- the sweep is a
partial one. Where the gap is whole missing files it names them, because
"missing: nas_watch, nas_egress" beats "some unknown subset"; the commit
count stays the trigger, since a tree can also carry an older *version* of
a check that still exists and no name diff would see that. A branch that is
behind and also ahead is told to merge rather than to check main out.
Being only *ahead* (an ordinary feature branch) prints and does not raise.
It is timed and counted in the footer like any other row, and it is the one
row that runs serially, because everything after it depends on the answer. A crash inside a
check is a non-zero exit and gets the loud treatment, so the failure mode
of this tool is noisy, not silent -- which is the direction "How to work"
asks for when a negative result could otherwise be guaranteed in advance.

Checks run concurrently because they are independent and several are slow
(`pin_drift` ~30s, `security_alerts` ~14s against 21 repos); output is
printed in the declared order regardless, so two runs are comparable.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

#: The step-1a status checks, in the order `prompt.md` lists them.
#:
#: `tidy_workspace` and `top_board_rows` are deliberately NOT here. They do
#: not share the exit contract and their output is not a status line a cycle
#: can skim -- `top_board_rows` prints the pick itself and has to be read in
#: full, and `tidy_workspace` moves files. Collapsing either would hide the
#: thing the step exists to show.
#:
#: `cadence_control` is here and it is the one entry that *acts* -- it moves
#: Nova's own heartbeat interval so the seven-day window lands on zero, which
#: is the owner's 2026-08-29 capture. It is here rather than in its own step for
#: the reason that makes it dynamic at all: a controller a cycle has to
#: remember to run is not a controller, and this is the one call every cycle
#: already makes. It earns the place by satisfying the contract above rather
#: than by being a check -- one skimmable status line, and 0/1/2 meaning the
#: same three things they mean for everything else. Two cycles running it at
#: once is safe by construction and not by luck: the second sees the first's
#: change against a burn rate still earned at the old interval and holds.
CHECKS = (
    "cadence_control",
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
    "claim_drift",
    "running_images",
    "workload_health",
    "ci_health",
    "nas_health",
    "nas_watch",
    "nas_egress",
    "nas_versions",
    "nas_ports",
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


CAVEAT_MARKERS = ("NOT JUDGED", "NOT ASKED", "CANNOT JUDGE", "CANNOT SEE",
                  "CANNOT READ", "COULD NOT READ", "UNREADABLE")


#: How long a *standing* finding may go without being reproduced in full.
#: The owner, comments board 2026-08-30: *"It is a bit heavy to check this
#: system every day... So spending that many tokens is wasteful."* Measured on
#: this morning's sweep: eight of the twenty-six checks exited non-zero, every
#: one of them a finding I cannot close from this loop -- server1's memory
#: (issue #131), the two NAS apps waiting on his upgrade, a Dependabot alert
#: that is already fixed and unrescanned, goreleaser v5. Between them they are
#: ~120 lines reproduced verbatim, at a 40-minute cadence, roughly 36 times a
#: day, and then carried in context for the ~90 turns that follow.
#:
#: This does not drop any of them and does not touch a single exit code. A
#: finding that has not changed since the last sweep collapses to one line
#: naming when it was first seen and when its full text last printed; the row
#: above it still says ACT, `--verbose` still prints it whole, and after this
#: many hours it prints in full again whether or not it changed. The rule is
#: the owner's own (`personality.md`): an interface problem gets an interface,
#: never less data.
REPRINT_HOURS = 24.0

#: Where the "have I already printed this" record lives. Not in the checkout:
#: concurrent cycles each get their own `git worktree`, so a per-tree file
#: would make every cycle the first one. Not in `/data/workspace` either --
#: `tools.tidy_workspace` archives loose files at that root. `preflight` is
#: run from the bridge pod, whose `/data/claude-home` persists across cycles.
#: A run with no readable record simply prints everything, which is the safe
#: direction to fail in.
STATE_PATH = os.environ.get(
    "NOVA_PREFLIGHT_STATE",
    os.path.join(os.path.expanduser("~"), ".nova-preflight-state.json"),
)


def finding_shape(output):
    """A digit-blind fingerprint of a check's output, plus its line count.

    Exact text will not do. Most of these findings carry a number that moves
    every single run -- `1853Mi available`, `built 1334 day(s) ago`, an elapsed
    time -- so an exact comparison would say "changed" every cycle and this
    whole mechanism would never fire once.

    So digits are blinded. **Every other byte is not**, including the line
    breaks: the hash is taken over the whole joined text, so a second alert, a
    third unhealthy pod or a newly-missing module changes it and is printed in
    full. The one thing a digit-blind match forgives is the same finding
    restated with a different number in it, which is the case this exists for.

    The count is returned alongside for the message to quote and is not part
    of the comparison. It was, for one commit, and a mutation round showed
    that removing it changed nothing -- the join already carries it. A second
    guard that cannot fail is not a second guard.

    It is not a free ride even then: `REPRINT_HOURS` puts the full text back
    in front of me on a fixed clock regardless of whether anything moved.
    """
    lines = [l.rstrip() for l in output.splitlines() if l.strip()]
    blinded = "\n".join(re.sub(r"\d+", "#", l) for l in lines)
    return len(lines), hashlib.sha256(blinded.encode("utf-8")).hexdigest()[:16]


def load_state(path=None):
    """The record of what has already been printed. Unreadable is empty, never fatal."""
    try:
        with open(path or STATE_PATH) as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    """Best effort. A read-only home must not take the sweep down with it."""
    path = path or STATE_PATH
    try:
        with open(path, "w") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def _oslo(stamp):
    try:
        moment = dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "an unreadable time"
    return moment.astimezone(ZoneInfo("Europe/Oslo")).strftime("%Y-%m-%d %H:%M Oslo")


def repeat_verdict(name, code, output, state, now):
    """(collapse?, one line about it, the state entry to keep) for one check.

    `state` is read, never mutated here -- the caller decides what to persist,
    so a `--verbose` run cannot quietly reset everybody's reprint clock.
    """
    count, shape = finding_shape(output)
    entry = state.get(name) or {}
    same = entry.get("shape") == shape
    first_seen = entry.get("first_seen", now) if same else now
    printed = entry.get("printed_at", 0.0) if same else 0.0
    hours = (now - printed) / 3600.0

    if not same or hours >= REPRINT_HOURS:
        return False, "", {"shape": shape, "lines": count,
                           "first_seen": first_seen, "printed_at": now}

    due = REPRINT_HOURS - hours
    return True, (f"UNCHANGED since {_oslo(first_seen)} ({count} line(s)); full text "
                  f"last printed {hours:.1f}h ago and prints again in {due:.1f}h. "
                  f"--verbose for it now."), {"shape": shape, "lines": count,
                                              "first_seen": first_seen,
                                              "printed_at": printed}


def caveat_lines(output):
    """The lines where a check says it could not judge part of its own scope.

    A check is allowed to exit 0 over a scope it did not fully cover, and that
    is honest: `nas_watch` cannot read nzbget's extension list because nobody
    has given this pod nzbget's password, and "locked, with no credential" is
    not a judgement of that list either way. What is *not* honest is that
    saying so costs the check a line and this report collapses a clean check
    to one -- so the caveat is in the output and structurally invisible in the
    one place a cycle reads every morning.

    Cycle 646 hit exactly that and fixed it in the wrong place. `nas_watch`
    said *"Judged the notification list of 2 service(s) of 2"* about a box with
    three code-execution surfaces, and the repair was to push the missing digit
    into that check's own summary line so `summary_line` would carry it. That
    works for one check and leaves the next one to rediscover the rule, because
    the constraint it satisfies -- *get your caveat into the last line that
    carries a digit* -- lives here and is written down nowhere a check's author
    would look. Three of those and the shape is the bug.

    So the collapse stops hiding them instead: a clean check's caveats are
    printed under its row, verbatim, however many there are. There is no cap
    and there should not be one -- the owner's rule (`personality.md`) is that
    an interface problem is solved with an interface, never by throwing data
    away, and `--verbose` is already the other end of that. Measured against a
    real sweep of all 25 checks on 2026-08-30: one clean check carries one
    caveat line, so this costs the table one line today.

    The marker has to *start* the line, which is narrower than containing it
    and deliberately so: every emitter prints it at the head of its own line,
    and a footnote that merely mentions the word -- *"a check that exits 1 is
    UNREADABLE and never reads as clean"* -- is prose about the contract rather
    than a thing this sweep failed to judge. Verified on the same sweep: over
    all 25 checks, anchoring and containment select exactly the same lines.
    """
    return [line.strip() for line in output.splitlines()
            if any(line.strip().startswith(marker) for marker in CAVEAT_MARKERS)]


def missing_modules(git, directory):
    """Check modules that exist on `origin/main` and not in this tree, by name.

    The commit count is the trigger, because a tree can also carry an *older
    version* of a check that still exists and no name diff would see that. But
    where the gap is whole missing files, saying so beats saying "some unknown
    subset" -- it would have named the five Cycle 644 lost. Absent on any git
    failure, which is a smaller report and never a wrong one.
    """
    listed = git("ls-tree", "--name-only", "origin/main", "tools/")
    if listed.returncode != 0:
        return []
    try:
        here = set(os.listdir(os.path.join(directory, "tools")))
    except OSError:
        return []
    return sorted(
        os.path.basename(line)[:-3]
        for line in listed.stdout.split()
        if line.endswith(".py") and os.path.basename(line) not in here
    )


def source_revision(directory=None, fetch=True):
    """(exit code, one-line report) for the git revision these checks came from.

    The one thing a stale checkout cannot tell you is what it is missing, so
    this measures the only number that covers every absence at once: commits
    on `origin/main` that are not in `HEAD`. Zero means the roster above is
    the current one. Anything else means some unknown subset of the checks
    does not exist here.

    `fetch` is on because a local `origin/main` is itself a checkout of
    unknown age, and a guard against staleness that reads a stale reference
    is the negative-guaranteed-in-advance failure. A fetch that fails is not
    fatal -- it falls back to the local ref and says so, since a measured
    "behind by 19" off a stale ref is still a true finding.

    No git repository at all is `CANNOT SEE` and does not raise, matching
    `nas_health` on a pod with no hop: a verdict no change here could ever
    clear is one every cycle re-derives.
    """
    directory = directory or os.path.dirname(tools_dir())

    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    def git(*args):
        """Never raises. A git that hangs or is missing must not take the sweep down with it."""
        try:
            return subprocess.run(["git", "-C", directory] + list(args),
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return failed

    inside = git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        return 0, (f"CANNOT SEE -- {directory} is not a usable git checkout, so there is "
                   f"no revision to name.")

    head = git("rev-parse", "--short", "HEAD").stdout.strip() or "?"
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "?"

    fetched = ""
    if fetch:
        got = git("fetch", "--quiet", "origin", "main")
        if got.returncode != 0:
            fetched = " (fetch failed, so this is measured against a local origin/main of unknown age)"

    counts = git("rev-list", "--left-right", "--count", "origin/main...HEAD")
    if counts.returncode != 0:
        return 1, (f"UNREADABLE -- on {branch} at {head}, and origin/main could not be "
                   f"resolved, so I cannot say whether these checks are the current ones.")
    try:
        behind, ahead = (int(n) for n in counts.stdout.split())
    except ValueError:
        return 1, (f"UNREADABLE -- on {branch} at {head}, and git answered "
                   f"{counts.stdout.strip()!r} rather than two counts.")

    where = f"on {branch} at {head}"
    if behind:
        absent = missing_modules(git, directory)
        named = (f" Missing from this tree: {', '.join(absent)}." if absent else
                 " Every check module on main exists here, so what is stale is their contents.")
        if ahead:
            fix = ("this branch carries its own work, so `git merge origin/main` "
                   "rather than checking main out")
            own = f" and {ahead} ahead"
        else:
            fix = "`git checkout main && git pull`"
            own = ""
        return 2, (f"BEHIND origin/main by {behind} commit(s){own} -- {where}{fetched}."
                   f"{named} Run {fix}, then re-run before trusting this sweep.")
    if ahead:
        return 0, (f"Current: {where}, {ahead} commit(s) ahead of origin/main and 0 behind"
                   f"{fetched} -- every check on main exists here.")
    return 0, f"Current: {where}, level with origin/main{fetched}."

def render(results, stream=sys.stdout, verbose=False, state=None, now=None, keep=None):
    """Print the collapsed report. `results` is a list of (name, code, output, seconds).

    `state` is the repeat record from `load_state`; pass `None` to disable the
    repeat collapse entirely, which is what `--verbose` and `--no-state` do.
    `keep` is an optional dict this fills with the record to persist, so the
    caller owns the write and a run that printed nothing in full cannot mark
    everything as printed.
    """
    import time as _time

    now = _time.time() if now is None else now
    worst = 0
    noisy = []
    repeated = []
    print(f"{'check':20}{'verdict':12}{'s':>6}  summary", file=stream)
    caveated = 0
    for name, code, output, seconds in results:
        worst = max(worst, code)
        word = STATUS_WORD.get(code, f"EXIT {code}")
        print(f"{name:20}{word:12}{seconds:>6.1f}  {summary_line(output)}", file=stream)
        if code == 0:
            # A caveat that is *already* the summary line is not repeated: a
            # one-line report -- `source_revision` with no git checkout says
            # only `CANNOT SEE -- ...` and exits 0 -- would otherwise print
            # itself twice, once collapsed and once indented under itself.
            summary = summary_line(output)
            caveats = [line for line in caveat_lines(output) if line != summary]
            if caveats:
                caveated += 1
            for line in caveats:
                print(f"{'':32}  {line}", file=stream)
        # A check whose entire output IS its summary line has already been
        # reproduced, in the row above. `source_revision` is the live case:
        # it reports one sentence, so a "===== full output =====" block would
        # repeat it and an UNCHANGED note would cost a line to hide nothing.
        # Same reasoning as the caveat de-duplication a few lines up.
        if code != 0 and output.strip() == summary_line(output):
            continue
        if code != 0 and state is not None and not verbose:
            collapse, note, entry = repeat_verdict(name, code, output, state, now)
            if keep is not None:
                keep[name] = entry
            if collapse:
                print(f"{'':32}  {note}", file=stream)
                repeated.append(name)
                continue
        elif state is not None and keep is not None:
            keep[name] = repeat_verdict(name, code, output, state, now)[2]
        if code != 0 or verbose:
            noisy.append((name, code, output))

    for name, code, output in noisy:
        print(file=stream)
        print(f"===== {name}: exit {code} -- full output =====", file=stream)
        print(output.rstrip(), file=stream)

    print(file=stream)
    print(f"Ran {len(results)} check(s): {', '.join(n for n, _, _, _ in results)}.", file=stream)
    unclean = sum(1 for _, code, _, _ in results if code != 0)
    if worst == 0:
        print("Every check exited 0 -- nothing to act on, and each line above "
              "names what its check swept.", file=stream)
    else:
        shown = unclean - len(repeated)
        where = (f"their full output is above, unabridged"
                 if not repeated else
                 f"{shown} of them printed in full above, unabridged, and "
                 f"{len(repeated)} collapsed to a single line as unchanged")
        print(f"{unclean} check(s) did not come back clean; {where}. "
              f"Overall exit {worst}.", file=stream)
    if repeated:
        print(f"{len(repeated)} standing finding(s) were not reproduced because nothing in "
              f"them changed since the last sweep: {', '.join(repeated)}. Their rows above "
              f"still carry their real verdict, the exit code still counts them, and "
              f"`--verbose` prints them in full.", file=stream)
    if caveated:
        print(f"{caveated} check(s) exited 0 over a scope they could not fully judge; "
              f"their caveats are the indented lines above. Exit 0 is the right "
              f"status for those and it is not a claim about what they skipped.",
              file=stream)
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="CHECK",
                        help="run just these checks, by module name")
    parser.add_argument("--list", action="store_true",
                        help="print the roster and exit")
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip the git fetch in the source_revision check")
    parser.add_argument("--verbose", action="store_true",
                        help="reproduce every check in full, clean ones included")
    parser.add_argument("--no-state", action="store_true",
                        help="print every finding in full, ignoring what was printed last sweep")
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

    import time

    rev_started = time.monotonic()
    rev_code, rev_report = source_revision(fetch=not args.no_fetch)
    rev_seconds = time.monotonic() - rev_started
    results = [("source_revision", rev_code, rev_report, rev_seconds)] + [None] * len(names)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(run_check, name): i + 1 for i, name in enumerate(names)}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()

    state = None if (args.no_state or args.verbose) else load_state()
    keep = {} if state is not None else None
    worst = render(results, verbose=args.verbose, state=state, keep=keep)
    if keep:
        save_state(keep)
    return worst


if __name__ == "__main__":
    sys.exit(main())
