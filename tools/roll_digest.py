"""Roll old digest lines off `journal-digest.md` into `digest-archive.md`.

The digest reached 100,991 bytes -- 96,740 of it 54 accumulated cycle
lines -- and grows ~1.8KB every hour forever. `prompt.md` step 1a
forbids a cycle from delegating that read, so every cycle pays for all
of it to reach the 3.6KB of **Needs Edvard** / **Next cycle** at the  (not-prose: quoting a literal)
top. Same shape as `journal.md` at 291KB, which the owner called urgent.

This is a script rather than a paragraph in `prompt.md` for the reason
`split_journal.py` is one: a rule that lives only in prose is a rule
every cycle has to remember, and this journal's own record is that they
don't. Run it every cycle after writing the digest; it is idempotent and
a no-op once the live file is already at or under `--keep`.

    python3 -m tools.roll_digest --dry-run    # verify only, write nothing
    python3 -m tools.roll_digest              # verify, then rewrite both

The mechanics -- keep the newest, archive first, drop what is already
filed, verify before writing -- moved into `tools/rolling.py` when
`roll_captures.py` became the third caller. What stays here is what is
true of the digest and of nothing else: where one entry ends, and that
the pair has to render identically through `parse_digest`, which is what
the site actually reads. Both of those now come from
`nova_journal.split_digest_entries` rather than being restated here --
this file used to say "blank-line separated" and split that way, the
site had not agreed with that since Cycle 65, and the disagreement made
the roll a silent no-op on a digest whose cards were merged. See
`rolling.py` for why the write order is what it is.

Vault I/O is deliberately not in here -- the two files come in as paths
and go out as paths, so this runs from either pod, with whichever vault
client that pod actually has:

    python3 /app/bridge/vault_tool.py get '<digest>'  > live.md
    python3 /app/bridge/vault_tool.py get '<archive>' > archive.md
    python3 -m tools.roll_digest
    python3 /app/bridge/vault_tool.py put '<archive>' archive.md   # archive FIRST
    python3 /app/bridge/vault_tool.py put '<digest>'  live.md
"""

import re
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.md_sections import split_at_heading
from agora_runner.nova_journal import parse_digest, split_digest_entries
from tools import rolling
from tools.doc_integrity import duplicate_headings
from tools.rolling import RollError, RollSpec, dedup, join_paragraphs

# Half a day at the current one-cycle-an-hour cadence. The number is not
# arbitrary: The owner sleeps 22:00-07:00, so nine cycles run while he is
# away, and anything below ~10 means he wakes to a digest that has
# already dropped part of the night.
KEEP = 12

MARKER = "\n## Digest\n"
ARCHIVE_TITLE = "# Journal — Digest Archive"
# A digest line opens with a bolded name, and for everything a weekly
# heartbeat writes that name is not "Cycle N" -- `**Ideas & research**
# (2026-08-25 13:29) — ...` is a real line in his file, written by the
# Tue/Thu/Sat run, which has its own conversation and therefore no cycle
# number at all. The old matcher took only `**Cycle `, so one weekly line
# in the section refused every roll after it: the digest sat at 15 lines
# for eight handoffs, growing, while the tool that trims it reported
# itself working correctly by refusing.
#
# The guard still means what it meant -- a paragraph nothing can date
# makes the split point guesswork -- so the second alternative requires
# the stamp rather than accepting any bold opening. `**Cycle ` stays as
# its own alternative because the addendum shape `**Cycle 94
# (addendum)**` puts the number inside the bold and does not always
# carry a stamp after it.
_LINE_RE = re.compile(
    r"^\*\*Cycle |^\*\*[^*\n]+\*\*[ \t]+\(\d{4}-\d{2}-\d{2} \d{2}:\d{2}\)"
)

ARCHIVE_FRONTMATTER = (
    "---\n"
    "type: log\n"
    "tags: [agora, evolution, self-improvement, agent-context]\n"
    "status: built\n"
    "maintenance: The digest lines that have rolled off journal-digest.md, "
    "newest first. Append only. No level-two heading anywhere in this file: "
    "the site concatenates it onto the live file's digest section, and a "
    "second one would start a rival section, silently replacing the live "
    "file's newest digest lines with these older ones "
    "(agora_runner/nova_sources.py, digest_markdown).\n"
    "---\n\n"
)


def _check_live(live):
    """Refuse a live digest that is structurally wrong, before any roll.

    The failure is not one the roll itself can cause, which is exactly
    why nothing here could see it. `plan` splits on `## Digest` and every
    other check in this file reasons about the section below it, so a
    faithful roll of a spliced document is a spliced document, and this
    script has read one and said nothing.

    On 2026-08-26 the digest went live carrying
    two `## Needs input` sections and two `## Next cycle` sections -- the
    previous cycle's block and the current one's, one under the other,
    because a cycle rewriting a section it did not write added its copy
    without removing the old. `md_sections._sections` keys by heading and
    keeps the *last* of each, so the site rendered the newer block and
    looked correct; Obsidian, which the owner reads, showed both. It was
    found by `tools.doc_integrity` twenty minutes later, at the start of
    the next cycle -- which is the right instrument at the wrong moment,
    because by then the damage is a cycle old and the next write lands on
    top of it. This script is the only code that reads the live digest
    out of the vault every cycle, seconds after it is written, so the same
    invariant costs nothing here and closes that window.

    Refusing rather than warning, deliberately, and it is not a free
    choice -- `_check_entry`'s own history is that a refusal on the live
    document stopped the trim for eight handoffs while the file grew. The
    difference is what the message can ask for: that one needed the
    matcher changed and named a paragraph instead, this one names the
    duplicated heading and the fix is deleting the stale copy. Nothing is
    lost by refusing either way; the digest was written before this runs
    and the roll is idempotent.
    """
    duplicates = duplicate_headings(live)
    if duplicates:
        found = ", ".join(f"{name!r} x{n}" for name, n in sorted(duplicates.items()))
        raise RollError(
            "refusing to roll: the live digest holds a duplicated heading -- "
            f"{found}. A second copy of a section is in this file; the site "
            "renders the last of each and hides it, Obsidian shows both. "
            "Delete the stale copy before rolling."
        )


def _check_entry(entry):
    """Every paragraph under `## Digest` must be a cycle line.

    Recognised by the `**Cycle` opening rather than by `parse_digest`'s
    stricter regex, because that regex does not match every line the file
    actually holds -- `**Cycle 94 (addendum)**` is real, is in the file,
    and does not parse. Rolling has to move a line it cannot parse rather
    than drop it on the floor; a paragraph that is not a cycle line at
    all is a different thing, and it makes the split point guesswork.
    """
    if not _LINE_RE.match(entry):
        raise RollError(
            "refusing to roll: the Digest section holds a paragraph that is "
            f"not a cycle line, so the split point is guesswork -- {entry[:120]!r}"
        )


def _check_archive(new_archive):
    """No level-two heading in the archive -- `parse_digest` reads one as a
    rival section.

    Worth stating precisely, because the obvious guess is wrong and a
    reviewer had to correct it: `_sections` keys sections by heading text
    and keeps the last of each, so a `## Digest` landing in the archive
    does *not* touch **Needs Edvard** or **Next cycle** -- those come from  (not-prose: quoting a literal)
    different keys, populated from the live file. What it silently
    discards is the live file's own digest lines, the newest ones, in
    favour of the archive's older ones. That is precisely the data this
    script exists to stop losing, which is why the guard is `^##` and not
    a search for the two section names.
    """
    if re.search(r"^##[ \t]+", new_archive, re.MULTILINE):
        raise RollError(
            "refusing to write: the archive has a level-two heading, which "
            "would displace the live file's own digest lines on the site"
        )


def _check_render(live, archive, kept, new_live, new_archive):
    """The same comparison through the parser that actually renders the site.

    The generic entry-count check and this one are blind to different
    things: `parse_digest` drops lines it cannot match (see
    `_check_entry`), and the raw check cannot see a change in how a line
    renders. `kept` is the input pair as the parser should have seen it,
    with any line duplicated across the two files counted once, so the
    comparison is against what the roll claims to preserve rather than
    against a half-applied run.
    """
    rejoined = split_at_heading(live, MARKER)[0] + "\n" + join_paragraphs(kept) + "\n"
    before = parse_digest(rejoined)
    after = parse_digest(f"{new_live}\n\n{new_archive}")
    if before != after:
        for key in before:
            if before[key] != after[key]:
                raise RollError(f"refusing to write: the split changes {key!r}")
        raise RollError("refusing to write: the split changes the rendered digest")


SPEC = RollSpec(
    marker=MARKER,
    archive_title=ARCHIVE_TITLE,
    archive_frontmatter=ARCHIVE_FRONTMATTER,
    split_entries=split_digest_entries,
    join_entries=join_paragraphs,
    keep=KEEP,
    noun="digest lines",
    check_live=_check_live,
    check_entry=_check_entry,
    check_archive=_check_archive,
    check_render=_check_render,
)


def plan(live, archive, keep=KEEP):
    return rolling.plan(live, archive, SPEC, keep)


def verify(live, archive, new_live, new_archive):
    return rolling.verify(live, archive, new_live, new_archive, SPEC)


def main(argv=None):
    return rolling.run(SPEC, argv, description=__doc__)


if __name__ == "__main__":
    sys.exit(main())
