"""Roll old entries off a growing vault file into an archive beside it.

`roll_digest.py` was written for one file and the shape turned out to be
general: a live file that Nova appends to every cycle, read in full by
every cycle after, growing with no limit. `journal.md` hit 291KB and had
to be split; `journal-digest.md` hit 100KB and got `roll_digest.py`;
`nova/resources/issues.md` and `ideas.md` were 158,635 and 99,617 bytes
at Cycle 112, and they are read by the opening subagent of every cycle --
which by then could no longer read either of them in full.

Three copies of the same logic is where it becomes one function instead.
What actually differs between the two callers is small -- where the
entries start, and what separates one entry from the next -- so that is
the seam: a `RollSpec` carries the differences, everything below is
shared, and a third file to roll is a spec rather than a script.

What is shared is the part that is easy to get wrong:

- **Archive first.** The two vault writes are not atomic, so one of them
  can be the last thing that happens. Writing the archive first makes the
  worst case a duplicated entry rather than a lost one.
- **Recover from that duplicate.** The write order is only worth
  something if a later run repairs it, so `plan` drops entries the
  archive already holds. Without that, the next run rolls them a second
  time and nothing complains.
- **Verify before writing, and abort rather than guess.** The invariant
  is not "in equals out" -- a run recovering from a half-applied one
  legitimately drops what is already filed. It is "out equals in, with
  duplicates collapsed to their first occurrence".

Vault I/O stays out of here, exactly as it did in `roll_digest.py`: files
come in as paths and go out as paths, so this runs from either pod with
whichever vault client that pod actually has.
"""

import argparse

from agora_runner.md_sections import split_at_heading


class RollSpec:
    """What a particular file needs that the shared engine cannot know.

    `marker` is the heading the entries live under, including its
    newlines. `split_entries` turns the text below it into a list of
    entries, newest first. `join_entries` puts them back. `archive_title`
    is the level-one heading in the archive, and `archive_frontmatter` is
    used only when the archive does not exist yet -- an existing archive
    keeps its own header verbatim.

    `archive_section` is a heading written *below* the archive title and
    above the entries, and it is what the archive's reader keys on. It
    defaults to empty because the digest archive must not have one --
    `parse_digest` reads a named section and the archive is deliberately
    concatenated in with no rival heading. The capture archive is the
    opposite: `nova_boards.parse_notes` returns notes only from a section
    titled `entries`, so an archive without that heading renders as
    nothing at all. Cycle 114 shipped exactly that and took two thirds of
    a live board page off the screen; see `roll_captures.spec_for`.

    `check_entry` may raise on an entry the split point cannot be trusted
    around; `check_archive` and `check_render` are extra guards the
    caller wants run before any write. All three default to nothing,
    because the generic checks below are the ones that matter.
    """

    def __init__(
        self,
        marker,
        archive_title,
        archive_frontmatter,
        split_entries,
        join_entries,
        keep,
        noun="entries",
        archive_section="",
        check_entry=None,
        check_entries=None,
        check_archive=None,
        check_render=None,
    ):
        self.check_entries = check_entries
        self.noun = noun
        self.marker = marker
        self.archive_title = archive_title
        self.archive_section = archive_section
        self.archive_frontmatter = archive_frontmatter
        self.split_entries = split_entries
        self.join_entries = join_entries
        self.keep = keep
        self.check_entry = check_entry
        self.check_archive = check_archive
        self.check_render = check_render


class RollError(SystemExit):
    """A reason not to write. Every one of these aborts before any write.

    A `SystemExit` rather than a plain exception, deliberately: these are
    raised from a script whose whole job is to refuse and say why, and
    `SystemExit` already prints the message and exits non-zero without a
    traceback nobody needs. `roll_digest.py` raised `SystemExit` directly
    before this module existed; keeping that is what makes the extraction
    invisible to its callers and to its tests.
    """


def join_paragraphs(entries):
    return "\n\n".join(entries)


def split_bullets(text):
    """Top-level `- ` bullets, each carrying its own continuation lines.

    The capture files are one bullet per entry, but not one *line* per
    entry: a bullet can wrap onto indented continuation lines, and both
    files also hold the odd stray `### Cycle 94` heading from a cycle
    that used a different shape. Splitting on newline would tear those
    apart and file half an entry in each file, so anything that is not
    the start of a new top-level bullet belongs to the bullet above it.

    A `#` heading starts an entry of its own rather than joining the
    bullet above it, so a stray one travels as itself; anything else is a
    continuation.

    Blank lines between entries are *not* preserved -- `join_bullets`
    writes exactly one between each, so a file with tight and loose runs
    mixed comes back uniformly loose. That is a real reformat and it is
    only acceptable because of who reads these: the capture files live in
    `nova/resources/`, which Edvard has said he does not open, and no
    parser anywhere reads them. Do not reuse this splitter on a file
    either of those is untrue of.
    """
    entries = []
    for line in text.strip("\n").split("\n"):
        if line.startswith("- ") or line.startswith("#"):
            entries.append(line)
        elif entries:
            entries[-1] += "\n" + line
        elif line.strip():
            entries.append(line)
    return [e.strip("\n") for e in entries if e.strip()]


def join_bullets(entries):
    return "\n\n".join(entries)


def _body(live, spec):
    """(everything up to and including the marker, everything after).

    `split_at_heading` rather than `live.find(spec.marker)`: the marker is
    a heading, and a heading is a whole line outside the frontmatter and
    outside any fenced block. Nova writes prose about its own machinery
    into these files every hour, so a paragraph naming `## Digest` is not
    a hypothetical -- see `md_sections.split_at_heading`.
    """
    parts = split_at_heading(live, spec.marker)
    if parts is None:
        raise RollError(
            f"refusing to roll: no {spec.marker.strip()!r} section in the live file"
        )
    return parts


def _split_title(archive, spec):
    """(everything through the archive's title line, the rest), or None.

    **`split_at_heading` rather than `archive.split(spec.archive_title)`,
    and the difference is a reproduction rather than a worry.** These
    files all carry a `maintenance:` line quoting their own structure back
    at whichever cycle opens them; the moment one of them names its own
    title -- which is `comments.md`'s exact shape -- a substring split
    cuts *inside the frontmatter*.

    **No archive names its own title today**, and the second reader was
    right to make this precise: an earlier draft called
    `digest-archive.md` "one sentence away", which overstates it. What
    that file's `maintenance:` line actually discusses is the *opposite*
    heading -- "No `##` heading anywhere in this file" -- and it never
    mentions the level-one title at all. So this is a latent bug in a file
    family whose convention invites it, not a live one, and old and new
    code produce byte-identical output on all three real archives.

    Measured on this fixture, before the fix: four archived entries where
    there were two, one of them the fragment `title is append only.\\n---`;
    the rolling cycle line inserted above the frontmatter's closing `---`
    where no reader can see it; and the frontmatter itself written back
    severed mid-sentence with that `---` gone. The site concatenates this
    archive onto the digest Edvard opens, so the fragment renders on his
    page as a cycle line.

    **`verify` passed all of that**, which is the part worth keeping in
    mind before trusting it elsewhere: it compares `_archived(archive)`
    against `_archived(new_archive)`, so a splitter that is wrong the same
    way on both sides agrees with itself.
    """
    return split_at_heading(archive, spec.archive_title)


def _archived(archive, spec):
    if not archive.strip():
        return []
    # Cut below the title, then below the section heading if that is what
    # comes next -- `split_bullets` treats a stray heading as its own
    # entry, so leaving `## Entries` in the body would carry it back out
    # as a capture and `verify` would refuse the roll it just planned.
    #
    # **The section test is anchored rather than a substring search, and
    # that is not hypothetical caution.** An entry is free to quote
    # `## Entries` in its own text -- this cycle wrote one that does,
    # about this very heading -- and an unanchored `in` would cut inside
    # that bullet on any archive not yet carrying a real heading,
    # truncating it. Only a heading in the one position that makes it a
    # heading counts.
    #
    # A titleless archive falls back to reading the whole file as body,
    # which is what the substring version did. `plan` refuses that file by
    # name before ever getting here, so this is the defensive branch and
    # not a supported input.
    split = _split_title(archive, spec)
    body = archive if split is None else split[1]
    if spec.archive_section:
        stripped = body.lstrip("\n")
        if stripped.startswith(spec.archive_section):
            body = stripped[len(spec.archive_section):]
    return spec.split_entries(body)


def _archive_header(archive, spec):
    """An existing archive's own header, verbatim, or a fresh one.

    "Verbatim" covers the frontmatter, which is the part a human may have
    edited. The section heading is re-asserted rather than preserved, so
    an archive written before `archive_section` existed is upgraded in
    place the next time it is rolled instead of staying unreadable to its
    own page forever.
    """
    split = _split_title(archive, spec)
    if split is None:
        header = spec.archive_frontmatter + spec.archive_title + "\n\n"
    else:
        # `split[0]` already ends with the title's own line and its
        # newline, so this is the previous `head + title + "\n\n"` with
        # the title no longer re-appended from the spec -- an archive
        # whose real heading differs from the spec only in case or inner
        # whitespace now keeps what it had rather than being silently
        # restyled.
        header = split[0].rstrip("\n") + "\n\n"
    if spec.archive_section:
        header += spec.archive_section + "\n\n"
    return header


def dedup(entries):
    """Ordered unique -- first occurrence wins, later copies dropped."""
    seen, out = set(), []
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            out.append(entry)
    return out


def plan(live, archive, spec, keep=None):
    """(new live, new archive), or the pair unchanged if nothing rolls."""
    keep = spec.keep if keep is None else keep
    head, body = _body(live, spec)
    entries = spec.split_entries(body)
    if spec.check_entry:
        for entry in entries:
            spec.check_entry(entry)
    # Whole-list checks come after the per-entry ones and before the
    # early return, so a file that is too short to roll is still told it
    # is mis-ordered rather than being quietly waved through until the
    # day it grows past `keep` and rolls the wrong end.
    if spec.check_entries:
        spec.check_entries(entries)
    if len(entries) <= keep:
        return live, archive

    # Heading-aware for the same reason `_split_title` is: an archive whose
    # frontmatter merely *names* the title passed this check as a substring
    # and then got cut inside that frontmatter. Refusing by name is the
    # right end of that file -- it is malformed, and guessing where its
    # entries begin is what produced the splice.
    if archive.strip() and _split_title(archive, spec) is None:
        raise RollError(
            f"refusing to roll: archive has no {spec.archive_title!r} title"
        )
    already = set(_archived(archive, spec))
    rolling = [entry for entry in entries[keep:] if entry not in already]

    new_live = head + "\n" + spec.join_entries(entries[:keep]) + "\n"
    new_archive = (
        _archive_header(archive, spec)
        + spec.join_entries(rolling + _archived(archive, spec))
        + "\n"
    )
    return new_live, new_archive


def verify(live, archive, new_live, new_archive, spec):
    """Raise unless the rolled pair holds exactly what the given pair did."""
    if spec.check_archive:
        spec.check_archive(new_archive)
    kept = dedup(
        spec.split_entries(_body(live, spec)[1]) + _archived(archive, spec)
    )
    rolled = spec.split_entries(_body(new_live, spec)[1]) + _archived(new_archive, spec)
    if kept != rolled:
        raise RollError(
            f"refusing to write: {len(kept)} {spec.noun} in, {len(rolled)} out"
        )
    if spec.check_render:
        spec.check_render(live, archive, kept, new_live, new_archive)


def run(spec, argv=None, description=None):
    """The shared CLI: read both files, plan, verify, write archive first.

    `spec` may be a callable taking the live text instead of a `RollSpec`,
    for a caller whose spec depends on the file it is given -- the capture
    files derive their archive title from the live file's own heading, so
    one script rolls both without a flag naming which is which.
    """
    keep_default = spec.keep if isinstance(spec, RollSpec) else None
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--live", default="live.md")
    parser.add_argument("--archive", default="archive.md")
    parser.add_argument("--keep", type=int, default=keep_default)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    live = open(args.live).read()
    try:
        archive = open(args.archive).read()
    except FileNotFoundError:
        archive = ""
    if not isinstance(spec, RollSpec):
        spec = spec(live)
    if args.keep is None:
        args.keep = spec.keep

    new_live, new_archive = plan(live, archive, spec, args.keep)
    verify(live, archive, new_live, new_archive, spec)

    if new_live == live:
        print(f"nothing to roll: {args.live} is already at or under {args.keep} {spec.noun}")
        return 0
    moved = len(spec.split_entries(_body(live, spec)[1])) - args.keep
    # `noun` is plural; the original printed `line(s)` and losing that to
    # the extraction would have made a single-entry roll say "1 digest
    # lines". Naive de-pluralisation is fine for the nouns that exist
    # here and there is no third one waiting.
    moved_noun = spec.noun[:-1] if moved == 1 and spec.noun.endswith("s") else spec.noun
    print(
        f"verified: {moved} {moved_noun} roll off, "
        f"{len(live)} -> {len(new_live)} bytes live, "
        f"{len(archive)} -> {len(new_archive)} bytes archived"
    )
    if args.dry_run:
        print("--dry-run: nothing written")
        return 0
    # Archive first: see the module docstring. The worst case of stopping
    # between these two writes is a duplicated entry, never a lost one.
    open(args.archive, "w").write(new_archive)
    open(args.live, "w").write(new_live)
    print(f"wrote {args.archive}, then {args.live}")
    return 0
