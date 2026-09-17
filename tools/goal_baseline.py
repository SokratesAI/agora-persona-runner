"""Record a baseline for every key result in `project-goals.md` that has none.

A key result carries `now:` and `target:` and, until today, nothing saying
where the number started. `project_goals_check` has been printing NO BASELINE
(30 of 30) since Cycle 1657 wrote the check -- flaw 3 of the goals-model
review in the owner's key-results thread on 2026-09-15 -- and 30 of 30 is the
shape of a check nothing can drain, because no cycle is going to hand-edit
thirty fences in a document the owner also edits from his phone.

**A baseline taken today is a late baseline, not a false one, and the date is
what says so.** `key_results_without_baseline` already accepts `0 (09-15)` --
a value with a date beside it -- so the dated form is the shape the model was
built for. Nothing here reconstructs history: `project-goals.md` has no
revision ledger this loop can read, so the earliest reading anybody can
honestly take for these thirty key results is the one standing now. Writing
`baseline: 3 (2026-09-16)` says exactly that and no more; inventing an origin
number for a key result set last week would say more than I can defend.

What it refuses, and the refusal is the point: a key result whose `now:` is
not a number gets no baseline and stays on the list with the reason printed.
`nova-kr-trust-cycles-shown` was one of those until the planned vs. done
view gave it an instrument -- `now` was blank, and a baseline of "" would
drain the list while recording nothing. A drained list that recorded nothing is worse than a red one.

It only ever ADDS `baseline:` lines. Before writing it strips every line it
inserted back out and compares the result to the input byte for byte, so a
run that changed anything else -- a re-indent, a lost fence, a rewritten
`now` -- refuses whole rather than writing a document nobody diffed. That is
`goal_drift.repair`'s line-count guard pointed the other way: that tool may
never change the line count, this one may only grow it, by exactly the
number of baselines it says it wrote.

Vault I/O is deliberately not in here, the same as `append_goal_snapshot`
and `roll_digest`: the file comes in as a path and goes out as the same
path, so it runs from either pod with whichever vault client that pod has.

    P='projects/sokrates/projects/nova/project-goals.md'
    python3 /app/bridge/vault_tool.py get "$P" --rev-file /tmp/pg.rev > pg.md \
      && python3 -m tools.goal_baseline --goals pg.md \
      && python3 /app/bridge/vault_tool.py put "$P" pg.md --if-rev-file /tmp/pg.rev

Exit 0 wrote the baselines it printed, or found nothing to write. Exit 2
refused and said why, having written nothing. `--print` writes the new
document to stdout and leaves the file alone.
"""

import argparse
import re
import sys
from difflib import SequenceMatcher

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.project_goals import (
    _number,
    parse_project_goals,
    set_field_in_key_result,
)

#: A line this tool inserted. Anchored on the field name and the dated shape
#: it writes, so stripping is the exact inverse of writing -- a `baseline:`
#: the document already carried does not match, and is therefore never
#: stripped by the verification below.
_INSERTED_RE = re.compile(r"^[ \t]*baseline:[ \t]*\S.*\(\d{4}-\d{2}-\d{2}\)[ \t]*$")


def pending(sections):
    """`(writes, skips)` -- the key results to baseline and the ones to leave.

    A `struck` key result is skipped without a line, the same call
    `key_results_without_baseline` makes: the owner killed it, so asking for
    its baseline invents work. A live one with a baseline already is simply
    not pending and says nothing either; the report is about what changed.
    """
    writes, skips = [], []
    for section in sections.values():
        project = section.get("project", "")
        for row in section.get("keyResults", ()):
            if row.get("status", "").strip().lower() == "struck":
                continue
            if row.get("baseline", "").strip():
                continue
            label = row.get("id", "").strip() or row.get("name", "").strip()
            now = row.get("now", "").strip()
            if not label:
                skips.append((project, "(unnamed)", "the fence carries no id"))
                continue
            if _number(now) is None:
                skips.append((project, label,
                              f"`now` is {now!r}, which is not a number to "
                              f"take a baseline from"))
                continue
            writes.append((project, label, now))
    return writes, skips


def apply(markdown, writes, date):
    """The document with one `baseline:` line added per entry in `writes`.

    Returns `(text, error)`. `error` is a sentence and `text` is `None` when
    the setter could not address a key result -- `set_field_in_key_result`
    returns `None` for a missing id, a duplicated one, or a fence that never
    closed, and every one of those means the address moved under us.
    """
    for _, label, now in writes:
        updated = set_field_in_key_result(markdown, label, "baseline",
                                          f"{now} ({date})")
        if updated is None:
            return None, (f"could not address key result {label!r} in the "
                          f"document -- a missing id, a duplicate one, or an "
                          f"unclosed fence")
        markdown = updated
    return markdown, None


def verify(before, after, expected):
    """`None` when `after` is `before` plus exactly `expected` new baselines.

    The whole safety of this tool: the only difference between the two
    documents may be inserted lines, every one of them a dated `baseline:`,
    and there must be exactly as many as the run said it wrote. That catches
    a rewritten `now`, a lost fence and a re-indent alike, none of which a
    line count would notice.

    Diffed rather than stripped, and the difference matters the second time
    this runs: a strip would take out the baselines written on an earlier day
    too, so a document that already carries 26 of them would refuse a run
    that correctly wrote none.
    """
    old, new = before.split("\n"), after.split("\n")
    added = 0
    for tag, i1, i2, j1, j2 in SequenceMatcher(
            a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag != "insert":
            return (f"the repaired copy {tag}s line(s) {i1 + 1}-{i2} of the "
                    f"document that was read -- something other than a "
                    f"baseline changed")
        for line in new[j1:j2]:
            if not _INSERTED_RE.match(line):
                return (f"the repaired copy inserts {line.strip()!r}, which "
                        f"is not a dated baseline line")
        added += j2 - j1
    if added != expected:
        return (f"the repaired copy carries {added} inserted baseline "
                f"line(s) against the {expected} measured")
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--goals", required=True,
                        help="local copy of project-goals.md, edited in place")
    parser.add_argument("--date", required=True,
                        help="the date the baseline is taken, YYYY-MM-DD")
    parser.add_argument("--print", dest="to_stdout", action="store_true",
                        help="write the new document to stdout instead")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", args.date or ""):
        print(f"refused: {args.date!r} is not a YYYY-MM-DD date",
              file=sys.stderr)
        return 2
    try:
        with open(args.goals, encoding="utf-8") as handle:
            before = handle.read()
    except OSError as exc:
        print(f"refused: could not read {args.goals}: {exc}", file=sys.stderr)
        return 2

    writes, skips = pending(parse_project_goals(before))
    for project, label, reason in skips:
        print(f"  - {project} / {label}: no baseline, {reason}")
    after, error = apply(before, writes, args.date)
    if error is not None:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    problem = verify(before, after, len(writes))
    if problem is not None:
        print(f"refused: {problem}", file=sys.stderr)
        return 2

    for project, label, now in writes:
        print(f"  + {project} / {label}: baseline {now} ({args.date})")
    if args.to_stdout:
        sys.stdout.write(after)
    else:
        with open(args.goals, "w", encoding="utf-8") as handle:
            handle.write(after)
    print(f"BASELINED {len(writes)} key result(s), {len(skips)} left without "
          f"one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
