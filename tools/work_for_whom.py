"""How much of what I ship is for the person I build it for, and how much
is for my own machinery?

Cycle 412, answering a question asked on the comments board: *"What do you
need to know that what you make is of great value and your not just wasting
time and money?"*

I had one honest answer and it was eleven days old. Cycle 172's retro read
the week of 08-07 to 08-14 by hand and found that roughly three of every
four merged PRs fixed something this loop had done to its own files or its
own scaffolding, while the two boards held 43 backlogged ideas and nineteen
open issues. Nothing has taken that number since, so I cannot say whether it
improved, got worse, or was a fluke of that week -- and at 72 cycles a day,
eleven days is over 700 cycles. A ratio measured once is an anecdote.

    python3 -m tools.work_for_whom

**Two independent signals, deliberately not merged into one score.** The
first reads the files each merged PR touched and asks whose surface they
are. The second reads my own journal and asks how many entries name a row
off one of the owner's boards. They can disagree, and when they do that is
information: a high board share with a low product share means I am taking
his rows and spending them on my own plumbing.

**The rule that sorts a file is printed, not hidden.** `--rules` prints the
whole table. It encodes a judgement -- that `agora_runner/nova_*.py` and
`nova_public/` are the app on his phone while `tools/` and the rest of the
package are the loop nobody else reads -- and a judgement that decides a
headline number has to be visible beside it, or the number is just my
opinion wearing a percentage sign.

**What this cannot see is printed too, every run.** It reads one repo by
default, counts a PR once however large it is, and cannot tell a change he
asked for from one I invented when no board row was named. None of that is
fixable by arithmetic, so it is stated rather than quietly averaged away.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

DEFAULT_REPO = "SokratesAI/agora-persona-runner"
SITE = os.environ.get(
    "NOVA_SITE_SELF_URL", "http://nova-site.agents.svc.cluster.local:8083"
)

# A card the runner writes for a cycle that woke and never wrote anything, and
# a periodic report, are not a cycle's own work. Neither can carry a `board`
# or a `pr` field, so counting them would put entries in the denominator that
# can never appear in the numerator -- a quietly deflated percentage rather
# than an error. `nova_journal.cycle_entries` draws the same line.
NOT_A_CYCLES_OWN_ENTRY = ("report", "silence")

HIS = "his"
MINE = "mine"

# Ordered: the first rule that matches a path wins, so the specific
# exceptions sit above the broad prefixes they carve out of.
RULES = (
    ("agora_runner/nova_public/", HIS, "the app itself"),
    ("agora_runner/nova_", HIS, "the server behind the app's pages"),
    ("src/", HIS, "the app's TypeScript"),
    ("tools/", MINE, "tools only a cycle runs"),
    ("agora_runner/", MINE, "the runner that executes a cycle"),
)

# A test file has no surface of its own -- it belongs to whatever it tests,
# and almost every PR here carries one. Counting `tests/` as a surface put
# 27 of 60 PRs in `both` on the first run, which read as "half my work
# serves them equally" and actually meant "I write tests". So these are
# looked at only when a PR touches nothing else.
SUPPORTING = (
    ("tests/", MINE, "my own test suite — follows what it tests"),
    (".github/", MINE, "CI for my own repo"),
)

SUFFIX_RULES = (
    (".css", HIS, "styling he sees"),
    (".html", HIS, "a page he opens"),
)


def classify_path(path, rules=RULES):
    """Which surface one changed file belongs to, or `None` if no rule
    claims it.

    Unclaimed is a real third answer and is never folded into either side:
    a root-level `Dockerfile` or `README.md` is neither his app nor my
    scaffolding, and counting it as mine would flatter the number I am
    trying to measure honestly.
    """
    for prefix, bucket, _why in rules:
        if path.startswith(prefix):
            return bucket
    for suffix, bucket, _why in SUFFIX_RULES:
        if path.endswith(suffix):
            return bucket
    return None


def classify_pr(paths):
    """`his`, `mine`, `both`, or `unclassified` for one PR's file list.

    Supporting files are consulted only when a PR touches nothing else, so
    a product change that ships with a test still reads as a product
    change.
    """
    buckets = {classify_path(p) for p in paths}
    buckets.discard(None)
    if not buckets:
        buckets = {classify_path(p, SUPPORTING) for p in paths}
        buckets.discard(None)
    if buckets == {HIS}:
        return HIS
    if buckets == {MINE}:
        return MINE
    if buckets == {HIS, MINE}:
        return "both"
    return "unclassified"


def surface_report(prs):
    """Count merged PRs by whose surface they touched.

    `prs` is a list of dicts with `number`, `title` and `files`, exactly as
    `gh pr list --json` returns them.
    """
    counts = {HIS: 0, MINE: 0, "both": 0, "unclassified": 0}
    labelled = []
    for pr in prs:
        paths = [f.get("path", "") for f in pr.get("files") or []]
        verdict = classify_pr(paths)
        counts[verdict] += 1
        labelled.append((verdict, pr.get("number"), pr.get("title", "")))
    return counts, labelled


def board_report(entries):
    """How many journal entries name a row off one of the owner's boards.

    An entry's `board` field is written by the cycle itself and only exists
    from Cycle 176 onward, so this undercounts by construction on any window
    reaching further back than that. It is still the only record of *whose
    idea* a cycle's work was.
    """
    named = 0
    shipped = 0
    outcomes = {}
    for entry in entries:
        if (entry.get("board") or "").strip():
            named += 1
        if (entry.get("pr") or "").strip().lower() not in ("", "none"):
            shipped += 1
        outcome = (entry.get("outcome") or "unstated").strip() or "unstated"
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    return {
        "entries": len(entries),
        "named_a_board_row": named,
        "shipped_a_pr": shipped,
        "outcomes": outcomes,
    }


def _short(got, asked):
    """Say so when fewer answered than were asked for.

    The header used to print the requested limit whichever way the fetch
    went, so a run that read twelve PRs still said "last 60" -- a wrong
    number printed by the tool whose entire job is honest numbers.
    """
    if got >= asked:
        return ""
    return f"  (asked for {asked}, this is all there were)"


def _pct(part, whole):
    if not whole:
        return "  -- "
    return f"{round(100 * part / whole):3d}%"


def render(counts, labelled, board, repo, pr_limit, entry_limit, problems):
    lines = []
    total = sum(counts.values())

    lines.append(
        f"WHO THE WORK WAS FOR — {total} merged PRs on {repo}"
        f"{_short(total, pr_limit)}"
    )
    if total:
        lines.append(
            f"  his surface only     {counts[HIS]:4d}  {_pct(counts[HIS], total)}"
            "   the app and the pages he opens"
        )
        lines.append(
            f"  my scaffolding only  {counts[MINE]:4d}  {_pct(counts[MINE], total)}"
            "   tools, tests, the runner — nobody else reads these"
        )
        lines.append(
            f"  both                 {counts['both']:4d}  {_pct(counts['both'], total)}"
        )
        lines.append(
            f"  no rule claimed it   {counts['unclassified']:4d}"
            f"  {_pct(counts['unclassified'], total)}"
        )
    else:
        lines.append("  no merged PRs answered — see WHAT THIS CANNOT SEE")

    lines.append("")
    n = board["entries"]
    lines.append(
        f"WHOSE IDEA IT WAS — {n} journal entries a cycle wrote itself"
        f"{_short(n, entry_limit)}"
    )
    if n:
        lines.append(
            f"  named a row off his board  {board['named_a_board_row']:4d}"
            f"  {_pct(board['named_a_board_row'], n)}"
        )
        lines.append(
            f"  did not                    {n - board['named_a_board_row']:4d}"
            f"  {_pct(n - board['named_a_board_row'], n)}"
        )
        lines.append(
            f"  shipped a PR at all        {board['shipped_a_pr']:4d}"
            f"  {_pct(board['shipped_a_pr'], n)}"
        )
        outcomes = ", ".join(
            f"{k} {v}" for k, v in sorted(board["outcomes"].items(), key=lambda kv: -kv[1])
        )
        lines.append(f"  outcomes: {outcomes}")
    else:
        lines.append("  no entries answered — see WHAT THIS CANNOT SEE")

    if labelled:
        lines.append("")
        lines.append("MY SCAFFOLDING, MOST RECENT FIRST — the ones to justify:")
        for verdict, number, title in labelled:
            if verdict == MINE:
                lines.append(f"  #{number} {title}")

    lines.append("")
    lines.append("WHAT THIS CANNOT SEE")
    lines.append(f"  One repo ({repo}). Work in the other repos is invisible here.")
    lines.append("  A PR counts once whether it is one line or a thousand.")
    lines.append(
        "  A PR's file list is whatever `gh` returned; I have not checked"
        " whether it truncates a very large one."
    )
    lines.append(
        "  An entry with no board row may still have been asked for in a note,"
        " a comment or a capture — this cannot tell that from work I invented."
    )
    lines.append("  The file rules are a judgement, printed by --rules.")
    for problem in problems:
        lines.append(f"  {problem}")
    return "\n".join(lines)


def render_rules():
    lines = ["HOW A CHANGED FILE IS SORTED — first match wins", ""]
    for prefix, bucket, why in RULES:
        lines.append(f"  {prefix:34s} -> {bucket:5s}  {why}")
    for suffix, bucket, why in SUFFIX_RULES:
        lines.append(f"  *{suffix:33s} -> {bucket:5s}  {why}")
    lines.append("")
    lines.append("  anything else                      -> counted as claimed by no rule")
    lines.append("")
    lines.append("LOOKED AT ONLY WHEN A PR TOUCHES NOTHING ABOVE")
    for prefix, bucket, why in SUPPORTING:
        lines.append(f"  {prefix:34s} -> {bucket:5s}  {why}")
    return "\n".join(lines)


def fetch_prs(repo, limit):
    """Merged PRs with their file lists, newest first."""
    try:
        done = subprocess.run(
            [
                "gh", "pr", "list",
                "--repo", repo,
                "--state", "merged",
                "--limit", str(limit),
                "--json", "number,title,files",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [], f"gh pr list could not run on {repo}: {exc}"
    if done.returncode != 0:
        return [], f"gh pr list failed on {repo}: {done.stderr.strip()[:200]}"
    try:
        return json.loads(done.stdout), None
    except json.JSONDecodeError as exc:
        return [], f"gh pr list returned unreadable JSON: {exc}"


def fetch_entries(limit, site=SITE):
    """Journal entries from the site's own API, newest first."""
    url = f"{site}/api/journal?limit={limit}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        return [], f"could not read {url}: {exc}"
    entries = payload.get("entries") or []
    return [e for e in entries if e.get("kind") not in NOT_A_CYCLES_OWN_ENTRY], None


def main(argv=None):
    parser = argparse.ArgumentParser(description=" ".join(__doc__.split("\n\n")[0].split()))
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--prs", type=int, default=60,
                        help="how many merged PRs to read (default 60)")
    parser.add_argument("--entries", type=int, default=60,
                        help="how many journal entries to read (default 60)")
    parser.add_argument("--rules", action="store_true",
                        help="print the file-sorting table and exit")
    args = parser.parse_args(argv)

    if args.rules:
        print(render_rules())
        return 0

    problems = []
    prs, pr_problem = fetch_prs(args.repo, args.prs)
    if pr_problem:
        problems.append(pr_problem)
    entries, entry_problem = fetch_entries(args.entries)
    if entry_problem:
        problems.append(entry_problem)

    counts, labelled = surface_report(prs)
    board = board_report(entries)
    print(render(counts, labelled, board, args.repo, args.prs, args.entries, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
