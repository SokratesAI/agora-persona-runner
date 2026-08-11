"""Roll old captures off Nova's `issues.md` / `ideas.md` into archives.

These are Nova's own crude-capture files -- one bullet per thing noticed
and not fixed, appended by `prompt.md` step 6 at a floor of two per
cycle, never pruned. At Cycle 111 they were **158,635 and 99,617 bytes**,
319 and 204 entries, and they are read by the opening subagent of every
cycle. That read now fails: Cycle 112's own opening report said, under
"What I could not read", that both files "exceeded what I could read in
full" and that everything older than roughly Cycle 60 was "sampled, not
individually reviewed". A backlog nobody can read is not a backlog.

Same fix as `roll_digest.py`, same engine (`tools/rolling.py`): keep the
newest, move the rest to an archive beside it, archive written first so
the worst case is a duplicate rather than a loss.

    python3 -m tools.roll_captures --live issues.md --archive issues-archive.md --dry-run
    python3 -m tools.roll_captures --live issues.md --archive issues-archive.md

Nothing is lost by rolling and nothing *reads* the archive on a schedule
either -- unlike the digest archive, which the site concatenates back on.
That is the honest trade and it is why `KEEP` is 60 rather than the
digest's 12: an archived capture is only findable deliberately, through
`vault_search`, so the live file has to stay wide enough to actually
pick from. What it does not have to be is complete.

The archive title is derived from the live file's own `# ` heading
rather than passed in, so the same command rolls either file and cannot
be pointed at the wrong archive by a mistyped flag.
"""

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
    different shape, and `split_bullets` keeps such a line attached to
    the bullet above it rather than dropping it. What must not pass is a
    block that is neither -- an explanatory paragraph under `## Entries`
    would mean the split point is guesswork, exactly as a non-cycle
    paragraph does in the digest.
    """
    if not entry.startswith("- ") and not entry.startswith("#"):
        raise RollError(
            "refusing to roll: the Entries section holds a block that is "
            f"neither a bullet nor a heading -- {entry[:120]!r}"
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
    )


def plan(live, archive, keep=KEEP):
    return rolling.plan(live, archive, spec_for(live), keep)


def verify(live, archive, new_live, new_archive):
    return rolling.verify(live, archive, new_live, new_archive, spec_for(live))


def main(argv=None):
    return rolling.run(spec_for, argv, description=__doc__)


if __name__ == "__main__":
    sys.exit(main())
