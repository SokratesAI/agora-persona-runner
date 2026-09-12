"""Mark my own capture lines whose named symbol was built after I wrote them.

The rule, the three verdicts and why this is advisory rather than a refusal
all live in `agora_runner.capture_symbols`. This half does the I/O: read the
two capture files, walk every checkout beside this one, and ask `git log -S`
when a symbol is actually present.

Ordering matters for cost, not for correctness. `git log -S` over a repo's
whole history is the expensive call, so nothing runs it until a plain
`git grep` has proved the symbol is in the working tree at all -- which for
a symbol that was never built is the common case and costs one grep.

`--issues`/`--ideas` take local files instead of the vault, the same shape
`backlog_brief` uses, which is how the tests drive it and how a pod with no
vault client can run it.

**This is not a `preflight` check and exits 0 on a finding, deliberately.**
Every other check in this loop earns a 2 by naming something that can be
fixed and then stops saying it. A capture line naming a symbol built after
it is not fixable -- the line is history, the code moved, and the honest
response is a cycle reading the code, which is a thing a cycle does at pick
time and not at sweep time. Wired into `preflight` it would be red on day
one and red forever, which `prompt.md` says is the same as off. Exit 1 is
reserved for a capture file this could not read, because an empty judgement
must never read like a clean one.
"""

import argparse
import os
import pathlib
import subprocess
import sys

import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.capture_symbols import (  # noqa: E402
    line_verdict, render, symbols, verdict,
)
from agora_runner.nova_boards import parse_notes  # noqa: E402
from tools.backlog_brief import _fetch  # noqa: E402

MY_ISSUES = "projects/sokrates/projects/agora/nova/resources/issues.md"
MY_IDEAS = "projects/sokrates/projects/agora/nova/resources/ideas.md"

#: How far back to look. A line older than this is archaeology rather than
#: backlog -- `backlog_brief` makes the same call with its own `--limit` --
#: and running `git log -S` over hundreds of them costs minutes for an
#: answer no cycle was going to act on.
DEFAULT_LIMIT = 60


def checkouts(root=None):
    """Every git checkout sitting beside this one, newest name order.

    Read off the filesystem rather than from a list of repo names, because
    a list is a second copy of "which repos exist" and goes stale exactly
    the way the capture lines this tool judges do.
    """
    here = pathlib.Path(root or os.environ.get("NOVA_WORKSPACE") or "/data/workspace")
    found = []
    for child in sorted(here.iterdir()) if here.is_dir() else []:
        if child.is_dir() and (child / ".git").exists():
            found.append(child)
    return found


def _git(repo, args, timeout=60):
    try:
        done = subprocess.run(["git", "-C", str(repo)] + args,
                              capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    return done


def first_seen(repo, symbol):
    """`(iso date, repo name)` of the oldest commit adding `symbol`, or `None`.

    The grep gate is the whole cost story: `git grep -q` on the working
    tree answers in milliseconds and returns 1 for every symbol that was
    never built, which is the answer this tool exists to find.
    """
    grep = _git(repo, ["grep", "-qwF", "--", symbol])
    if grep is None or grep.returncode != 0:
        return None
    log = _git(repo, ["log", "-S", symbol, "--format=%as"], timeout=120)
    if log is None or log.returncode != 0:
        return None
    dates = [line.strip() for line in log.stdout.split("\n") if line.strip()]
    if not dates:
        return None
    return dates[-1], repo.name


def oldest_sighting(repos, symbol):
    """The earliest `(date, repo)` any checkout introduced `symbol`, or `None`.

    Every repo is asked, and the *oldest* answer wins. Stopping at the first
    checkout that carries the symbol is the obvious shortcut and it is wrong:
    the checkouts are walked in name order, so `tools.poke_page` -- which has
    been in `agora-persona-runner` for weeks and was copied into
    `agora-claude-bridge` on 09-11 -- reported the copy's date and turned a
    true note from 08-31 into a finding. Measured on the live capture files,
    that shortcut produced one false finding in three.
    """
    best = None
    for repo in repos:
        hit = first_seen(repo, symbol)
        if hit and (best is None or hit[0] < best[0]):
            best = hit
    return best


def judge(notes, repos):
    """Capture lines -> the rows `render` prints, plus the unjudgeable count."""
    rows = []
    skipped = 0
    seen_cache = {}
    for note in notes:
        names = symbols(note.get("text", ""))
        if not names:
            skipped += 1
            continue
        marks = []
        for name in names:
            if name not in seen_cache:
                seen_cache[name] = oldest_sighting(repos, name)
            hit = seen_cache[name]
            date, where = hit if hit else ("", "")
            marks.append({
                "name": name,
                "verdict": verdict(note.get("date"), date),
                "first_seen": date,
                "where": where,
            })
        rows.append({
            "date": note.get("date"),
            "cycle": note.get("cycle"),
            "text": note.get("text", ""),
            "symbols": marks,
            "verdict": line_verdict([m["verdict"] for m in marks]),
        })
    return rows, skipped


def _notes(markdown, limit):
    notes = parse_notes(markdown or "")
    notes = sorted(notes, key=lambda n: (n.get("date") or "", n.get("cycle") or -1),
                   reverse=True)
    return notes[:limit] if limit > 0 else notes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help=f"newest N lines per file (default {DEFAULT_LIMIT}); 0 for all")
    parser.add_argument("--issues", help="read a local file instead of the vault")
    parser.add_argument("--ideas", help="read a local file instead of the vault")
    parser.add_argument("--workspace", help="where the checkouts live")
    args = parser.parse_args(argv)

    repos = checkouts(args.workspace)
    if not repos:
        print("cannot see a single checkout — nothing to compare a symbol against.",
              file=sys.stderr)
        return 1

    notes = []
    unreadable = []
    for label, local, path in (("issues", args.issues, MY_ISSUES),
                               ("ideas", args.ideas, MY_IDEAS)):
        if local:
            # A local path that is not there must report as unreadable
            # rather than raise: the caller asked for a judgement and
            # a traceback is not one.
            try:
                text = pathlib.Path(local).read_text()
            except OSError:
                text = None
        else:
            text = _fetch(path)
        if text is None:
            unreadable.append(label)
            continue
        notes.extend(_notes(text, args.limit))

    if unreadable:
        print(f"could not read: {', '.join(unreadable)}", file=sys.stderr)

    rows, skipped = judge(notes, repos)
    print(render(rows, len(repos), skipped))
    return 1 if unreadable else 0


if __name__ == "__main__":
    raise SystemExit(main())
