"""Read-only staleness pass over one of Nova's own capture files.

The first slice of Edvard's idea #83 -- a dreaming pass over my own
memory, in his words *"merges duplicates, retires what is finished, and
rewrites what has gone stale"*. His own note on the row named the slice
and the safety rule, and both are load-bearing here:

> First slice: run it read-only over `resources/issues.md` alone and see
> what it says is dead.
>
> The design risk worth naming before anyone builds it: a pass that
> rewrites my own memory unsupervised can quietly delete a fact I
> needed, and I would not know, because the evidence would be gone. It
> has to write a proposed diff for a cycle to accept, not edit in place.

So this tool **never writes to the vault and has no `--apply`**. It
reads a file on disk and prints evidence. With `--proposal` it writes a
candidate rewrite to a *second* path so a cycle can `diff` the two and
decide; the original is untouched either way.

Why the target is worth a tool. `nova/resources/issues.md` was 244,329
bytes over 887 lines on 2026-08-23, 536 bullets, and it is read at the
start of every cycle -- so the cost of the mess is charged 36 times a
day. `tools.roll_done_captures` already rolls finished bullets off
*Edvard's* two board files; nothing has ever touched mine.

Three signals, and the split between them is the point:

`done`
    The bullet already begins `DONE (Cycle N):`. A previous cycle wrote
    that marker deliberately, so this is not a judgement -- it is a
    label being honoured. **This is the only class `--proposal` moves**,
    verbatim, into a `## Retired` section at the end of the file.

`dead-path`
    The bullet cites a repo-relative code path in backticks that exists
    in none of the `--repo` checkouts given. Evidence that the note
    describes code that has been renamed or deleted; not proof the note
    is worthless, because the *problem* may outlive the file. Advisory:
    reported, never moved.

`duplicate`
    Two bullets whose normalised word sets overlap above `--similarity`.
    Advisory: reported as a cluster, never moved. Merging two notes is a
    judgement about which sentence to keep, which is exactly what the
    design risk above says a tool must not make on its own.

**Accepting a proposal changes what the Nova site lists**, which is
worth knowing before you write one back to the vault. `nova_boards.
parse_notes` read 530 notes out of `resources/issues.md` before the
first accepted proposal and 516 after -- the difference is exactly the
14 `DONE` bullets, and nothing else moved. That is the intended effect
(`identity.md` rule 8: finished items move to a processed section) and
the text is still in the file, but it is a visible change rather than a
tidy-up nobody sees.

A vault path (`projects/...`, `nova/...`) is never checked for
existence: those live in CouchDB, not in a git checkout, so "not on
disk" would mean nothing about them and flagging one would be a
guaranteed-positive test of the kind `prompt.md` warns about.

    python3 -m tools.dream_pass --file /tmp/nova-issues.md \\
        --repo /data/workspace/agora-persona-runner \\
        --proposal /tmp/nova-issues.proposed.md

Exits 0 whether or not it found anything; a clean file is a result, not
a failure. Exit 1 is a bad argument or an unreadable file.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter

# A bullet is a top-level `- ` line plus any indented continuation lines
# under it. Nothing else in these files starts a list.
_BULLET = re.compile(r"^- (?!\s*$)")
# The docstring promises `DONE (Cycle N):` and the first version matched
# `^- DONE\b`, so `- DONE deal, moving on` counted as a retired bullet and
# `--proposal` would have moved it. All 14 markers in the live file carry
# the cycle number -- two of them as `DONE (Cycle 221, runner#212):` -- so
# the tighter pattern loses none of them. Reviewer, runner#296.
_DONE = re.compile(r"^- DONE \(Cycle \d+")
_CYCLE = re.compile(r"Cycle (\d+)")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")

# Only backticked spans are considered, and only ones shaped like a file:
# no spaces, at least one `/`, and a known source extension. Prose about
# "the poll loop" or "his ideas.md" therefore cannot be flagged, and a
# bare filename with no directory is not enough either -- `cli.py` alone
# is ambiguous across three repos.
_CODE_PATH = re.compile(r"`([A-Za-z0-9_.\-/]+\.(?:py|js|ts|json|ya?ml|toml|cfg|sh))`")

# Paths that are vault documents or absolute, neither of which a repo
# checkout can answer for.
_UNCHECKABLE_PREFIX = ("projects/", "nova/", "/", "resources/")

_WORD = re.compile(r"[a-z0-9_]+")
# Words carried by nearly every bullet in these files. Leaving them in
# makes two unrelated notes look 30% alike before they have said
# anything, which is how a similarity threshold ends up tuned to noise.
_STOP = frozenset("""
a an and are as at be been but by can cycle did do does for from had has have
he i if in into is it its me my no not of on one only or so than that the
their then there these they this to two up was were what when which who will
with would you your it's i've i'd don't
""".split())


class Bullet:
    def __init__(self, index, line_no, lines):
        self.index = index
        self.line_no = line_no
        self.lines = lines

    @property
    def text(self):
        return "\n".join(self.lines)

    @property
    def head(self):
        return self.lines[0]

    @property
    def done(self):
        return bool(_DONE.match(self.head))

    @property
    def cycle(self):
        """The first cycle number on the head line, which is not always the
        cycle that *wrote* the note: a retired bullet reads
        `DONE (Cycle 332): ... (Cycle 331) — ...`, so this returns the cycle
        that closed it. `report` only uses it for a min/max range, where
        either reading is fine; anything else should say which it wants.
        """
        m = _CYCLE.search(self.head)
        return int(m.group(1)) if m else None

    @property
    def date(self):
        m = _DATE.search(self.head)
        return m.group(1) if m else None

    def paths(self):
        out = []
        for p in _CODE_PATH.findall(self.text):
            if p.startswith(_UNCHECKABLE_PREFIX) or "/" not in p:
                continue
            if p not in out:
                out.append(p)
        return out

    def words(self):
        return {w for w in _WORD.findall(self.head.lower())
                if w not in _STOP and len(w) > 2}

    def excerpt(self, width=140):
        one = " ".join(self.head[2:].split())
        return one if len(one) <= width else one[:width - 1] + "…"


def parse(markdown):
    """Every top-level bullet in the document, in file order."""
    lines = markdown.split("\n")
    bullets = []
    current = None
    for n, line in enumerate(lines, start=1):
        if _BULLET.match(line):
            current = Bullet(len(bullets), n, [line])
            bullets.append(current)
        elif current is not None and line.startswith(("  ", "\t")) and line.strip():
            current.lines.append(line)
        else:
            current = None
    return bullets


def resolves(path, repos):
    """Does `path` name a file in any of `repos`?

    Half these notes write the repo name as the first segment --
    `platform-config/deployments/...`, `agora/public/app.js` -- because
    the sentence around them is about which repo the bug is in. Checking
    only `root/path` calls all of those dead, which is a false positive
    generated by my own writing habit rather than by anything being
    stale: giving the tool all four checkouts cut the flags from 16 to 6
    and every one of the six that remained was this. So a first segment
    matching a checkout's own directory name is stripped and retried.
    """
    for root in repos:
        if os.path.exists(os.path.join(root, path)):
            return True
        head, _, rest = path.partition("/")
        if rest and head == os.path.basename(os.path.normpath(root)) \
                and os.path.exists(os.path.join(root, rest)):
            return True
    return False


def dead_paths(bullets, repos):
    """(bullet, [paths existing in none of `repos`]) for each bullet that has any.

    With no repos given this returns nothing rather than flagging
    everything -- an unanswerable question is not a positive result.
    """
    if not repos:
        return []
    out = []
    for b in bullets:
        missing = [p for p in b.paths() if not resolves(p, repos)]
        if missing:
            out.append((b, missing))
    return out


def _similarity(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def duplicates(bullets, threshold):
    """Clusters of bullets whose head lines overlap above `threshold`.

    Single-link clustering: A near B and B near C puts all three in one
    group, because the thing a cycle wants to see is "these say the same
    thing", not every pair that says it.
    """
    words = [b.words() for b in bullets]
    parent = list(range(len(bullets)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(bullets)):
        for j in range(i + 1, len(bullets)):
            if _similarity(words[i], words[j]) >= threshold:
                parent[find(j)] = find(i)

    groups = {}
    for i in range(len(bullets)):
        groups.setdefault(find(i), []).append(bullets[i])
    return [g for g in groups.values() if len(g) > 1]


_RETIRED = "## Retired"


def _retired_line(markdown):
    """1-indexed line of the `## Retired` heading, or None."""
    for n, line in enumerate(markdown.split("\n"), start=1):
        if line.strip() == _RETIRED:
            return n
    return None


def propose(markdown):
    """The file with every `DONE` bullet moved, verbatim, to `## Retired`.

    Returns `(new_markdown, moved_bullets)`. Nothing is deleted, nothing
    is reworded, and a file with no `DONE` bullets comes back byte-identical
    so a second run proposes nothing.
    """
    # Only bullets still in the live part of the file are candidates. A
    # `DONE` bullet that a previous run already moved is still a `DONE`
    # bullet, so without this line the second run moves it again and the
    # file grows a duplicate section every time. Found by the
    # idempotence test, not by reading the code.
    cutoff = _retired_line(markdown)
    bullets = [b for b in parse(markdown) if cutoff is None or b.line_no < cutoff]
    moved = [b for b in bullets if b.done]
    if not moved:
        return markdown, []

    drop = set()
    for b in moved:
        for offset in range(len(b.lines)):
            drop.add(b.line_no + offset)

    lines = markdown.split("\n")
    kept = [line for n, line in enumerate(lines, start=1) if n not in drop]

    body = "\n".join(kept).rstrip("\n")
    if _RETIRED not in body:
        body += "\n\n" + _RETIRED + "\n"
    else:
        body += "\n"
    body += "\n" + "\n".join(b.text for b in moved) + "\n"
    return body, moved


def check(before, after, moved):
    """Assert the proposal moved text and lost none of it.

    The failure mode of rewriting a file this big is silent -- a bullet
    stops appearing and no cycle notices for weeks -- so the proposal is
    verified by parsing both versions and comparing every bullet.

    **As a multiset, not as a set**, and the reviewer on runner#296 is
    why. The first version compared sets and its docstring called that
    the careful choice; it is strictly weaker, and the reviewer built
    the input that breaks it: two byte-identical bullets in, one deleted
    out, `check` returns `None`. A tool whose whole job is finding
    duplicates cannot verify itself with a comparison that collapses
    them. The `moved` loop below counts occurrences under `## Retired`
    for the same reason.
    """
    b_before = Counter(b.text for b in parse(before))
    b_after = Counter(b.text for b in parse(after))
    lost = b_before - b_after
    if lost:
        return "%d bullet(s) vanished from the proposal" % sum(lost.values())
    gained = b_after - b_before
    if gained:
        return ("%d bullet(s) appeared that were not in the original"
                % sum(gained.values()))
    after_retired = after.split(_RETIRED, 1)[-1] if _RETIRED in after else ""
    retired = Counter(b.text for b in parse(after_retired))
    for text, count in Counter(b.text for b in moved).items():
        if retired[text] < count:
            return "a DONE bullet was not moved under %s" % _RETIRED
    return None


def report(bullets, repos, threshold):
    out = []
    done = [b for b in bullets if b.done]
    cycles = [b.cycle for b in bullets if b.cycle is not None]
    out.append("%d bullets, %d marked DONE" % (len(bullets), len(done)))
    if cycles:
        out.append("cycles named: %d..%d" % (min(cycles), max(cycles)))

    out.append("")
    out.append("DONE — a cycle already retired these; --proposal moves them (%d)" % len(done))
    for b in done:
        out.append("  L%-5d %s" % (b.line_no, b.excerpt()))

    dead = dead_paths(bullets, repos)
    out.append("")
    out.append("DEAD PATH — cites code that is in none of the given checkouts, "
               "advisory only (%d)" % len(dead))
    for b, missing in dead:
        out.append("  L%-5d %s" % (b.line_no, ", ".join(missing)))
        out.append("        %s" % b.excerpt(110))

    dupes = duplicates(bullets, threshold)
    out.append("")
    out.append("DUPLICATE — word overlap >= %.2f, advisory only (%d cluster(s))"
               % (threshold, len(dupes)))
    for group in dupes:
        out.append("  cluster of %d:" % len(group))
        for b in group:
            out.append("    L%-5d %s" % (b.line_no, b.excerpt(110)))
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--file", required=True,
                    help="capture file on disk; fetch it with vault_tool.py first")
    ap.add_argument("--repo", action="append", default=[],
                    help="checkout root to resolve cited paths against; repeatable")
    ap.add_argument("--proposal",
                    help="write the candidate rewrite here (never over --file)")
    ap.add_argument("--similarity", type=float, default=0.5,
                    help="word-overlap threshold for the duplicate signal")
    args = ap.parse_args(argv)

    if args.proposal and os.path.abspath(args.proposal) == os.path.abspath(args.file):
        print("--proposal must not be --file: this tool never edits in place",
              file=sys.stderr)
        return 1
    for root in args.repo:
        if not os.path.isdir(root):
            print("no such checkout: %s" % root, file=sys.stderr)
            return 1
    try:
        with open(args.file, encoding="utf-8") as fh:
            markdown = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is a ValueError, not an OSError, so the first
        # version tracebacked on exactly the "unreadable file" this line
        # claims to handle. Reviewer, runner#296.
        print("cannot read %s: %s" % (args.file, exc), file=sys.stderr)
        return 1

    # Report the live part of the file only, for the same reason `propose`
    # does: a bullet already under `## Retired` is not something a cycle
    # can act on, and counting it under a heading that says "--proposal
    # moves them" would be a lie the second time this is run.
    cutoff = _retired_line(markdown)
    live = [b for b in parse(markdown) if cutoff is None or b.line_no < cutoff]
    print(report(live, args.repo, args.similarity))

    if args.proposal:
        after, moved = propose(markdown)
        problem = check(markdown, after, moved)
        if problem:
            print("proposal refused: %s" % problem, file=sys.stderr)
            return 1
        try:
            with open(args.proposal, "w", encoding="utf-8") as fh:
                fh.write(after)
        except OSError as exc:
            print("cannot write %s: %s" % (args.proposal, exc), file=sys.stderr)
            return 1
        print("")
        print("proposal written to %s (%d bullet(s) moved) — diff it, do not trust it"
              % (args.proposal, len(moved)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
