"""Roll old captures off Nova's `issues.md` / `ideas.md` into archives.

These are Nova's own crude-capture files -- one bullet per thing noticed
and not fixed, appended by `prompt.md` step 6 at a floor of two per
cycle, never pruned. At Cycle 112 they were **158,635 and 99,617 bytes**,
320 and 205 entries as `split_bullets` counts them -- one more each than
a bare `grep -c '^- '`, which misses the stray `### Cycle 94` heading
that is its own entry -- and they are read by the opening subagent of
every cycle. That read now fails: Cycle 112's own opening report said, under
"What I could not read", that both files "exceeded what I could read in
full" and that everything older than roughly Cycle 60 was "sampled, not
individually reviewed". A backlog nobody can read is not a backlog.

Same fix as `roll_digest.py`, same engine (`tools/rolling.py`): keep the
newest, move the rest to an archive beside it, archive written first so
the worst case is a duplicate rather than a loss.

    python3 -m tools.roll_captures --live issues.md --archive issues-archive.md --dry-run
    python3 -m tools.roll_captures --live issues.md --archive issues-archive.md

`KEEP` is 60 rather than the digest's 12 because an archived capture is
only findable deliberately, through `vault_search`, so the live file has
to stay wide enough to actually pick from. What it does not have to be is
complete.

The archive title is derived from the live file's own `# ` heading
rather than passed in, so the same command rolls either file and cannot
be pointed at the wrong archive by a mistyped flag.

**Neither live file can be rolled yet, and this tool refuses both.** Two
blockers, found by reading the code that consumes these files rather
than by trusting that nothing did:

1. **They are not newest-first.** `check_newest_first` below has the
   measurement. `plan` keeps the top `keep` entries, so rolling
   `issues.md` today would archive Cycles 104-111 and keep Cycle 27. The
   guard turns that from a silent data move into a refusal.
2. **The site renders these files.** `agora_runner/nova_boards.py`
   serves both as board pages in the Nova app -- Edvard's own ask,
   `issues.md` #57 -- reading only the live path. The digest archive is
   safe to roll because `nova_journal.digest_markdown` concatenates it
   back on; there is no equivalent here, so archiving would delete two
   thirds of a page he opens. That is a site change, not a tool change,
   and it has to land first.

So this ships as the engine plus a tool that says no. Both blockers are
filed in `nova/resources/issues.md`.
"""

import re
import sys

from tools import rolling
from tools.rolling import RollError, RollSpec, join_bullets, split_bullets

# Sized against the limit that actually bites, which is the harness
# swapping a ~2KB preview in for any tool result past ~65KB -- the thing
# that made these two files unreadable in the first place. At the
# measured ~490 bytes per capture, 60 entries is ~30KB: comfortably one
# whole `get`, with room to grow for a day before the next roll. It is
# roughly a day of captures at the current 2.9-per-cycle floor, not the
# half-day the digest keeps, because a capture file is a menu to choose
# from rather than a record to read top to bottom.
KEEP = 60

MARKER = "\n## Entries\n"

ARCHIVE_FRONTMATTER = (
    "---\n"
    "type: log\n"
    "tags: [agora, evolution, self-improvement, agent-context]\n"
    "status: built\n"
    "maintenance: Captures rolled off Nova's live capture file, newest "
    "first. Append only, written by tools/roll_captures.py. Nothing reads "
    "this on a schedule -- it is deliberately cold storage, reachable with "
    "vault_search when a cycle is looking for whether something was noticed "
    "before. Do not read it as part of the opening read; that is the cost "
    "this file exists to remove.\n"
    "---\n\n"
)


def archive_title(live):
    """`# Nova — Issues` -> `# Nova — Issues Archive`.

    Derived rather than configured so that rolling `issues.md` into
    `ideas-archive.md` is not one mistyped flag away. If the live file
    has no level-one heading there is nothing to derive from and no safe
    guess, so this refuses instead.
    """
    for line in live.split("\n"):
        if line.startswith("# "):
            return line.strip() + " Archive"
    raise RollError(
        "refusing to roll: the live file has no '# ' title, so the archive "
        "title would be a guess"
    )


def _check_entry(entry):
    """Entries are bullets; a stray heading is tolerated, prose is not.

    Both files hold one `### Cycle 94` heading from a cycle that used a
    different shape. `split_bullets` gives such a line an entry of its
    own, so it travels rather than being swallowed or dropped -- which is
    why this check tolerates a leading `#`. What must not pass is a block
    that is neither: an explanatory paragraph under `## Entries` would
    mean the split point is guesswork, exactly as a non-cycle paragraph
    does in the digest.
    """
    if not entry.startswith("- ") and not entry.startswith("#"):
        raise RollError(
            "refusing to roll: the Entries section holds a block that is "
            f"neither a bullet nor a heading -- {entry[:120]!r}"
        )


# `- 2026-08-09 (Cycle 63) — the note itself`, and the date is optional.
# Anchored to the entry's own marker rather than searched for anywhere in
# it, because a capture's *body* very often names some other cycle: 30
# entries in the live `issues.md` and 17 in `ideas.md` mention a
# `(Cycle N)` while carrying no marker of their own. Searching would read
# those as the entry's date and refuse the file for a reason that is not
# true -- and a guard that can lie about why is one a future cycle will
# learn to route around. The anchored form still sees 85 and 50 markers
# respectively, and still finds 19 and 11 real ascents, so nothing is
# lost by being strict here.
_CYCLE_RE = re.compile(r"^- (?:\d{4}-\d{2}-\d{2}[ \t]*)?\(Cycle[ \t]+(\d+)")


def check_newest_first(entries):
    """Refuse a file whose newest entries are not at the top.

    **This is why the two live files cannot be rolled yet, and it is the
    whole reason this guard exists rather than a comment.** `plan` keeps
    the first `keep` entries in file order, which is only "keep the
    newest" if the file is actually newest-first. Measured against the
    live `nova/resources/issues.md` on 2026-08-11: it is not. The first
    ~120 entries descend from Cycle 103 to Cycle 27 -- the era when
    `prompt.md` said to prepend -- and the rest *ascend* to Cycle 111,
    because step 6 now says `vault_tool.py append`. So the genuinely
    newest captures are at the bottom, and rolling that file would
    archive Cycles 104-111 and keep material from Cycle 27.
    `agora_runner/nova_boards.parse_notes` had already found and
    documented the same break; nothing enforced it.

    Only ~a third of entries carry a `(Cycle N)` at all, so this checks
    the ones that do and ignores the rest: a run that descends is fine, a
    single ascent is the two-conventions break and is refused. Sorting
    instead was the obvious alternative and is wrong for the same reason
    `parse_notes` refuses to sort -- it would rank a third of the file
    and dump the other two thirds in arbitrary order.
    """
    seen = [int(m.group(1)) for e in entries if (m := _CYCLE_RE.match(e))]
    for older, newer in zip(seen, seen[1:]):
        if newer > older:
            raise RollError(
                "refusing to roll: this file is not newest-first -- "
                f"(Cycle {older}) appears above (Cycle {newer}), so keeping "
                "the top would archive the newest captures. Normalise the "
                "order first; see check_newest_first in tools/roll_captures.py"
            )


def spec_for(live):
    return RollSpec(
        marker=MARKER,
        archive_title=archive_title(live),
        archive_frontmatter=ARCHIVE_FRONTMATTER,
        split_entries=split_bullets,
        join_entries=join_bullets,
        keep=KEEP,
        noun="captures",
        check_entry=_check_entry,
        check_entries=check_newest_first,
    )


def plan(live, archive, keep=KEEP):
    return rolling.plan(live, archive, spec_for(live), keep)


def verify(live, archive, new_live, new_archive):
    return rolling.verify(live, archive, new_live, new_archive, spec_for(live))


def main(argv=None):
    return rolling.run(spec_for, argv, description=__doc__)


if __name__ == "__main__":
    sys.exit(main())
