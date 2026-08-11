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

**Both live files have been rolled.** Cycle 114 ran it against the vault
on 2026-08-11: 265KB down to 58KB across the two, all 534 captures still
rendering, verified by polling `/api/board` until the count settled.
Re-running is a no-op until a file passes `KEEP` captures again.

This module shipped at Cycle 112 as the engine plus a tool that refused,
because two blockers stood in front of it, and both are now closed. They
are worth keeping written down, because each one left a guard behind
that is still load-bearing:

1. **They were not newest-first.** These files grew in both directions
   for a hundred cycles -- `vault_tool.py append` inserts under the
   section marker when given one and at the bottom when not, and nobody
   knew there was a choice. `plan` keeps the top `keep` entries, so
   rolling then would have archived Cycles 104-111 and kept Cycle 27.
   `normalise_captures` merged the two streams (#98, #99);
   `check_newest_first` below is what stops the split re-opening, and it
   will fire again the first time a cycle appends without the marker.
2. **The site renders these files.** `agora_runner/nova_boards.py`
   serves both as board pages in the Nova app -- Edvard's own ask,
   `issues.md` #57. It reads the archive alongside the live half now
   (#98), the way `nova_journal.digest_markdown` always has. That fix
   was necessary and not sufficient: #100 then had to add the
   `## Entries` heading the archive's own parser keys on, after a roll
   that passed every writer-side guard took two thirds of a live board
   page off the screen. `_check_render` is the guard that came out of
   it, and it is the only one here that asks the *reader* rather than
   the writer.
"""

import re
import sys

from agora_runner.nova_boards import parse_notes
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
    live `nova/resources/issues.md` on 2026-08-11: it is not. **The numbers
    this paragraph first gave were wrong and Cycle 113 re-measured them**
    -- it is 324 entries, 89 carrying a marker; the first 118 descend
    from Cycle 112 to Cycle 24 (with 85 too old to carry a marker at all)
    and the remaining 206 *ascend* from Cycle 26 to Cycle 111. Both runs
    reach today, which is the part the first reading missed: this is not
    one convention that changed on a date, it is two running side by
    side, because `vault_tool.py append` inserts under the `## Entries`
    marker when handed one and at the end of the file when not. So the
    genuinely newest captures are at *both* ends, and rolling that file
    would archive Cycles 104-111 and keep material from Cycle 27.
    `agora_runner/nova_boards.parse_notes` had already found and
    documented the same break; nothing enforced it.

    Only ~a third of entries carry a `(Cycle N)` at all, so this checks
    the ones that do and ignores the rest: a run that descends is fine, a
    single ascent is the two-conventions break and is refused. Sorting
    instead was the obvious alternative and is wrong for the same reason
    `parse_notes` refuses to sort -- it would rank a third of the file
    and dump the other two thirds in arbitrary order. The repair is
    `tools/normalise_captures.py`, which merges the two streams instead;
    it is a one-time run and this guard is what stays.
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


def _check_render(live, archive, kept, new_live, new_archive):
    """The same comparison through the parser that actually renders the board.

    `roll_digest` has had this since it was written and `roll_captures`
    did not, which is the whole reason Cycle 114's regression reached the
    site: every guard here counted captures, and the one thing that
    mattered was whether `nova_boards.parse_notes` could still find them.
    A count survives a change in how a note renders -- or in whether it
    renders at all -- and that is exactly the failure that shipped.

    `kept` is the input pair as the parser should have seen it, so the
    comparison is against what the roll claims to preserve rather than
    against a half-applied run. The archive is parsed separately and
    appended, the same way `nova_site.board_payload` consumes it.
    """
    rejoined = live[: live.find(MARKER) + len(MARKER)] + "\n" + join_bullets(kept) + "\n"
    before = [note["text"] for note in parse_notes(rejoined)]
    after = [note["text"] for note in parse_notes(new_live)] + [
        note["text"] for note in parse_notes(new_archive)
    ]
    if before != after:
        raise RollError(
            "refusing to write: the board would render "
            f"{len(after)} captures where it rendered {len(before)}"
        )


def spec_for(live):
    return RollSpec(
        marker=MARKER,
        archive_title=archive_title(live),
        # **The archive is the site's data, not just cold storage, and
        # leaving this off cost Edvard two thirds of a page he opens.**
        # `nova_boards.parse_notes` returns notes only from a section
        # titled `entries` and `[]` from anything else, so an archive of
        # frontmatter-then-bullets renders as an empty half of the board.
        # Cycle 114 rolled both live files and watched `/api/board` report
        # 60 notes where the unrolled file had reported 328. Every guard
        # in this module passed, because all of them ask whether the
        # captures survived and none asked whether the reader could still
        # find them. `board_markdown`'s own docstring already asserted
        # this heading existed; nothing wrote it.
        archive_section=MARKER.strip(),
        archive_frontmatter=ARCHIVE_FRONTMATTER,
        split_entries=split_bullets,
        join_entries=join_bullets,
        keep=KEEP,
        noun="captures",
        check_entry=_check_entry,
        check_entries=check_newest_first,
        check_render=_check_render,
    )


def plan(live, archive, keep=KEEP):
    return rolling.plan(live, archive, spec_for(live), keep)


def verify(live, archive, new_live, new_archive):
    return rolling.verify(live, archive, new_live, new_archive, spec_for(live))


def main(argv=None):
    return rolling.run(spec_for, argv, description=__doc__)


if __name__ == "__main__":
    sys.exit(main())
