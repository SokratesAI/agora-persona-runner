"""Roll old digest lines off `journal-digest.md` into `digest-archive.md`.

The digest reached 100,991 bytes -- 96,740 of it 54 accumulated cycle
lines -- and grows ~1.8KB every hour forever. `prompt.md` step 1a
forbids a cycle from delegating that read, so every cycle pays for all
of it to reach the 3.6KB of **Needs Edvard** / **Next cycle** at the
top. Same shape as `journal.md` at 291KB, which Edvard called urgent.

This is a script rather than a paragraph in `prompt.md` for the reason
`split_journal.py` is one: a rule that lives only in prose is a rule
every cycle has to remember, and this journal's own record is that they
don't. Run it every cycle after writing the digest; it is idempotent and
a no-op once the live file is already at or under `--keep`.

    python3 -m tools.roll_digest --dry-run    # verify only, write nothing
    python3 -m tools.roll_digest              # verify, then rewrite both

Vault I/O is deliberately not in here -- the two files come in as paths
and go out as paths, so this runs from either pod, with whichever vault
client that pod actually has:

    python3 /app/bridge/vault_tool.py get '<digest>'  > live.md
    python3 /app/bridge/vault_tool.py get '<archive>' > archive.md
    python3 -m tools.roll_digest
    python3 /app/bridge/vault_tool.py put '<archive>' archive.md   # archive FIRST
    python3 /app/bridge/vault_tool.py put '<digest>'  live.md

Archive first, always. The two writes are not atomic together, so one of
them can be the last thing that happens; writing the archive first makes
the worst case a duplicated line rather than a lost one. That is only
worth anything if something actually recovers from it, so `plan` drops
lines the archive already holds -- without that, the next run rolls the
same lines a second time and Edvard gets a cycle rendered twice on his
phone, permanently and with nothing complaining.

Verification runs in both modes and any failure aborts before a write.
The check that matters is not that the files look right -- it is that
`parse_digest`, the thing that actually renders the site, produces an
identical payload from the rolled pair as it does from the pair it was
given.
"""

import argparse
import re
import sys

from agora_runner.nova_journal import parse_digest

# Half a day at the current one-cycle-an-hour cadence. The number is not
# arbitrary: Edvard sleeps 22:00-07:00, so nine cycles run while he is
# away, and anything below ~10 means he wakes to a digest that has
# already dropped part of the night.
KEEP = 12

ARCHIVE_TITLE = "# Journal — Digest Archive"
_LINE_RE = re.compile(r"^\*\*Cycle ")
_PARA_SPLIT_RE = re.compile(r"\n[ \t]*\n")


def _digest_body(live):
    """Everything under the live file's `## Digest` heading."""
    marker = "\n## Digest\n"
    index = live.find(marker)
    if index < 0:
        raise SystemExit("refusing to roll: no '## Digest' section in the live file")
    return live[: index + len(marker)], live[index + len(marker) :]


def _paragraphs(text):
    return [p.strip() for p in _PARA_SPLIT_RE.split(text.strip()) if p.strip()]


def plan(live, archive, keep=KEEP):
    """(new live, new archive), or (live, archive) unchanged if nothing rolls.

    Cycle lines are recognised by the `**Cycle` opening rather than by
    `parse_digest`'s stricter line regex, because that regex does not
    match every line the file actually holds -- `**Cycle 94 (addendum)**`
    is real, is in the file, and does not parse. Rolling has to move a
    line it cannot parse rather than drop it on the floor.
    """
    head, body = _digest_body(live)
    lines = _paragraphs(body)
    stray = [p for p in lines if not _LINE_RE.match(p)]
    if stray:
        raise SystemExit(
            "refusing to roll: the Digest section holds a paragraph that is "
            f"not a cycle line, so the split point is guesswork -- {stray[0][:120]!r}"
        )
    if len(lines) <= keep:
        return live, archive

    if archive.strip() and ARCHIVE_TITLE not in archive:
        raise SystemExit(f"refusing to roll: archive has no {ARCHIVE_TITLE!r} title")
    archived = _paragraphs(archive.split(ARCHIVE_TITLE, 1)[-1]) if archive.strip() else []

    # Recover from a half-applied previous run rather than compounding it.
    # The two vault writes are not atomic, and the archive is written
    # first, so a cycle killed between them leaves lines in *both* files.
    # The next run then reads an un-rolled live file against an already
    # rolled archive and, without this, rolls them a second time -- and
    # `verify` cannot see it, because it only compares the inputs it was
    # handed to the outputs it is about to write. The result is a cycle
    # rendered twice on Edvard's phone, permanently. Dropping the lines
    # that are already filed is what actually makes "duplicate rather
    # than lose" the recoverable failure the write order assumes.
    already = set(archived)
    rolling = [line for line in lines[keep:] if line not in already]

    new_live = head + "\n" + "\n\n".join(lines[:keep]) + "\n"
    rolled = rolling + archived
    new_archive = _archive_header(archive) + "\n\n".join(rolled) + "\n"
    return new_live, new_archive


def _archive_header(archive):
    """The archive's frontmatter and title, kept verbatim, or a fresh one."""
    if ARCHIVE_TITLE in archive:
        return archive.split(ARCHIVE_TITLE, 1)[0] + ARCHIVE_TITLE + "\n\n"
    return (
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
        "---\n\n" + ARCHIVE_TITLE + "\n\n"
    )


def verify(live, archive, new_live, new_archive):
    """Raise unless the rolled pair renders identically to the one given.

    Three things can go wrong and each gets its own check: a line can be
    dropped, a line can be duplicated across the two files, and the
    archive can grow a level-two heading -- which `parse_digest` would
    read as a rival section.

    That last one is worth stating precisely, because the obvious guess
    is wrong and a reviewer had to correct it: `_sections` keys sections
    by heading text and keeps the last of each, so a `## Digest` landing
    in the archive does *not* touch **Needs Edvard** or **Next cycle** --
    those come from different keys, populated from the live file. What it
    silently discards is the live file's own digest lines, the newest
    ones, in favour of the archive's older ones. That is precisely the
    data this script exists to stop losing, which is why the guard is
    `^##` and not a search for the two section names.
    """
    if re.search(r"^##[ \t]+", new_archive, re.MULTILINE):
        raise SystemExit(
            "refusing to write: the archive has a level-two heading, which "
            "would displace the live file's own digest lines on the site"
        )
    # The invariant is *not* "in equals out" -- a run recovering from a
    # half-applied previous one legitimately drops the lines that are
    # already in the archive. It is "out equals in, with duplicates
    # collapsed to their first occurrence", which is the same statement
    # for the ordinary case and the honest one for the recovery case.
    kept = _dedup(
        _paragraphs(_digest_body(live)[1]) + _paragraphs(archive.split(ARCHIVE_TITLE, 1)[-1])
    )
    rolled = _paragraphs(_digest_body(new_live)[1]) + _paragraphs(
        new_archive.split(ARCHIVE_TITLE, 1)[-1]
    )
    if kept != rolled:
        raise SystemExit(
            f"refusing to write: {len(kept)} digest lines in, {len(rolled)} out"
        )

    # And the same comparison through the parser that actually renders the
    # site, because the raw check above and this one are blind to
    # different things: `parse_digest` drops lines it cannot match (see
    # `plan`), and the raw check cannot see a change in how a line renders.
    before = parse_digest(_rejoin(live, archive, kept))
    after = parse_digest(f"{new_live}\n\n{new_archive}")
    if before != after:
        for key in before:
            if before[key] != after[key]:
                raise SystemExit(f"refusing to write: the split changes {key!r}")
        raise SystemExit("refusing to write: the split changes the rendered digest")


def _dedup(paragraphs):
    """Ordered unique -- first occurrence wins, later copies dropped."""
    seen, out = set(), []
    for paragraph in paragraphs:
        if paragraph not in seen:
            seen.add(paragraph)
            out.append(paragraph)
    return out


def _rejoin(live, archive, deduped):
    """The input pair as the parser should have seen it: same head and
    sections, but with any line duplicated across the two files counted
    once, so the payload comparison is against what the roll is actually
    claiming to preserve rather than against a half-applied run."""
    return _digest_body(live)[0] + "\n" + "\n\n".join(deduped) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", default="live.md")
    parser.add_argument("--archive", default="archive.md")
    parser.add_argument("--keep", type=int, default=KEEP)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    live = open(args.live).read()
    try:
        archive = open(args.archive).read()
    except FileNotFoundError:
        archive = ""

    new_live, new_archive = plan(live, archive, args.keep)
    verify(live, archive, new_live, new_archive)

    if new_live == live:
        print(f"nothing to roll: {args.live} is already at or under {args.keep} lines")
        return 0
    moved = len(_paragraphs(_digest_body(live)[1])) - args.keep
    print(
        f"verified: {moved} line(s) roll off, "
        f"{len(live)} -> {len(new_live)} bytes live, "
        f"{len(archive)} -> {len(new_archive)} bytes archived"
    )
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    # Archive first: see the module docstring. The worst case of stopping
    # between these two writes is a duplicated line, never a lost one.
    open(args.archive, "w").write(new_archive)
    open(args.live, "w").write(new_live)
    print(f"wrote {args.archive}, then {args.live}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
