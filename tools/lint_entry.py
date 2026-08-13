"""Check a journal entry before it is written, while the author can fix it.

Four live documents cannot be rendered as written -- six breakages in
all, three of the heading rule and three of the footer rule, with two
documents failing both -- and every repair so far has been code that
reads the mistake back afterwards.
`normalise_entry` promotes a `## ` heading and synthesises one from the
filename when there is none (Cycle 150); `stray_footer` lifts a `PR: ...`
line the cycle bolded and put at the top (Cycle 151). Both work, and
both are guesses made by something that was not there when the entry was
written. The author was. An entry is written once and never edited, so
the only moment a mistake is cheap to correct is before the `put`.

    python3 -m tools.lint_entry entry.md --name 168-cycle-152.md

Exits 0 when the document renders as written, 1 when a repair would be
needed, 2 when it could not be read. `prompt.md` step 7 chains the `put`
behind it with `&&`, so a failed check does not write.

**It reports where the renderer would have to repair the document; it
does not restate the renderer's rules.** That distinction is the whole
design. A linter carrying its own copy of what a valid entry looks like
is a seventh statement of the rules, free to drift from the six already
in `nova_journal.py`, and a linter that disagrees with the parser is
worse than no linter. So every check here runs the real function and
compares: heading by calling `normalise_entry`, footer by applying
`_FOOTER_RE` and then `stray_footer` exactly as `parse_journal` does.

Measured against all 166 live entries (2026-08-13): 4 documents are
flagged and 162 pass untouched -- 3 fail the heading check (Cycle 150's
bug) and 3 the footer check (Cycle 151's), two of them failing both. The
cycle-number check fires on none of them, which is said plainly rather
than dropped: the one live heading/filename disagreement is Cycle 131's,
and the heading check already has it. It stays because the failure it
guards is distinct and silent -- a correct-looking heading carrying the
wrong number puts one cycle's words under another's name, while the gap
detector counts that cycle from the filename, so the two halves of the
site disagree and nothing anywhere raises an error. That measurement is
also why there is no rule here
requiring the `---` above the footer, which `personality.md` asks for
and 17 live entries do not have -- every one of those 17 because it
carries the `Reviewer: n findings` line the review rubric asks for, in
the place the rule would go. `_FOOTER_RE` was deliberately changed to make that rule
optional (Cycle 104's card showed no PR for a cycle that had merged
one). A check written from the prose alone would fail a sixth of the
real journal, which is how a linter becomes something cycles learn to
ignore.
"""

import argparse
import re
import sys

from agora_runner.nova_journal import (
    _ENTRY_HEADING_RE,
    _FOOTER_RE,
    JOURNAL_DIR,
    normalise_entry,
    parse_journal,
    stray_footer,
)

# `<seq>-cycle-<n>.md`, and the `-addendum` suffixes twelve live files
# carry. Entry 004 has no cycle token at all and never will -- it is
# Edvard's own first message -- so a filename that does not match is not
# a finding, it just means there is no second statement of the cycle
# number to check the heading against.
_FILENAME_CYCLE_RE = re.compile(r"\A\d+-cycle-(\d+)(?:-|\.)")


def _heading_finding(path, content):
    """The document does not start where `parse_journal` looks for a start."""
    normalised = normalise_entry(path, content)
    if normalised == (content or "").strip():
        return None
    first = ((content or "").strip().split("\n") or [""])[0]
    return (
        "heading: this document does not begin with its `### ` heading, so "
        "the site would repair it rather than read it. Its first line is "
        f"{first[:70]!r}. Write the entry starting `### ` on line 1, with no "
        "frontmatter and exactly three hashes -- two makes the whole hour "
        "render as the tail of the previous cycle's card."
    )


def _raw_body(normalised):
    """The entry body exactly as `parse_journal` slices it, before any repair.

    One document is one entry, so this is everything after the first
    `### ` line. It has to be recomputed rather than read off the parsed
    entry because `parse_journal` hands back a body with the footer
    already removed -- by the strict rule *or* by the repair, and it does
    not say which.
    """
    headings = list(_ENTRY_HEADING_RE.finditer(normalised))
    if not headings:
        return normalised.strip()
    start = headings[0].end()
    # Bounded by the next heading, exactly as `parse_journal` bounds each
    # entry. Taking everything to the end instead let a document with two
    # headings pass the footer check on the *second* entry's footer, since
    # `_FOOTER_RE` anchors to the end of what it is given -- so a first
    # entry with no footer at all reported nothing. Masked by the `split`
    # finding today, and still the parser disagreeing with the checker.
    end = headings[1].start() if len(headings) > 1 else len(normalised)
    return normalised[start:end].strip()


def _footer_finding(body):
    """The `PR: ... | Outcome: ...` line is not where the renderer reads it.

    Applies `_FOOTER_RE` directly, which is the only way to see the
    answer. Asking the parsed entry whether it has a `pr` cannot fail:
    `parse_journal` falls back to `stray_footer` when the strict rule
    misses, so by the time it returns, a repaired entry and a correct one
    are indistinguishable. The first version of this check did exactly
    that and reported nothing on all three of the live entries whose
    missing PR badge Edvard could see -- a negative result that was
    guaranteed in advance.
    """
    if _FOOTER_RE.search(body):
        return None
    _, pr, _ = stray_footer(body)
    if pr:
        return (
            "footer: the `PR: ... | Outcome: ...` line is in the document but "
            "not at the end of it, so the site would have to move it to give "
            "this cycle a badge. Put it bare on the last line, not bolded and "
            "not wrapped across two lines."
        )
    return (
        "footer: no `PR: ... | Outcome: ...` line the site can read, so this "
        "cycle's card would show no PR and no outcome -- which reads as an "
        "hour that shipped nothing. Add it as the last line, e.g. "
        "`PR: #123 | Outcome: merged`, or `PR: none | Outcome: ...`."
    )


def _cycle_finding(name, entry):
    """The heading and the filename disagree about which cycle this is.

    Read off the parsed entry's `cycle`, not its `title` -- `parse_heading`
    classifies each segment of a heading independently and lifts the cycle
    number *out* of the title, so searching the title reports every
    correctly written entry as having no cycle number. Caught by running
    this against the live folder, where the first version failed Cycle
    151's own entry.

    Safe to run on a repaired document as well as a correct one: a
    synthesised heading is built *from* the filename, so it agrees by
    construction, and a promoted one carries the author's own words and
    can genuinely disagree. See the comment at the call site for why
    there is no guard in front of this.
    """
    declared = _FILENAME_CYCLE_RE.match(name)
    if not declared:
        return None
    want = int(declared.group(1))
    found = entry.get("cycle")
    if found == want:
        return None
    return (
        f"cycle: the filename says cycle {want} and the heading says "
        f"{found if found is not None else 'no cycle number'}. The gap "
        "detector counts cycles from the filenames and the cards title "
        "themselves from the headings, so a disagreement puts one cycle's "
        "words under another cycle's name."
    )


def lint(name, content):
    """`(filename, text)` -> a list of findings, empty when it renders as written."""
    if not (content or "").strip():
        return ["empty: there is nothing in this file to write."]
    path = JOURNAL_DIR + name
    findings = []
    heading = _heading_finding(path, content)
    if heading:
        findings.append(heading)
    # Every later check reads the document the way the site does, which
    # means through the repair -- otherwise a bad heading would report
    # itself a second time as a missing footer, and the cycle would fix
    # one thing and see two.
    normalised = normalise_entry(path, content)
    # One entry document, so there is no preamble to cut off the front --
    # `parse_journal`, the entries-body parser, same as the site and the
    # reply lookup. This used to call the whole-file parser, and a
    # `## Entries` line in
    # an entry's prose therefore cut the entry's own heading off and left
    # nothing to parse. There was a whole rule here refusing such an entry
    # outright; it is gone with the hazard that justified it (runner#135),
    # because the cost of keeping it was refusing a cycle that wrote a true
    # sentence about the append command `prompt.md` step 6 mandates.
    entries = parse_journal(normalised)
    if not entries:
        findings.append(
            "unparseable: the site could not read a single entry out of this "
            "document, even after repair."
        )
        return findings
    entry = entries[0]
    if len(entries) > 1:
        findings.append(
            f"split: this document holds {len(entries)} `### ` headings, so it "
            "would render as that many separate cards. One entry per file."
        )
    footer = _footer_finding(_raw_body(normalised))
    if footer:
        findings.append(footer)
    # Runs unconditionally, and that is a measurement rather than an
    # oversight. This started as a blanket "skip when the heading is
    # broken", which hid the wrong cycle number in `## Cycle 153` inside
    # `...-152.md` -- two real, independent defects, so the author fixed
    # the hash count and only then learned the number was wrong. The
    # narrower replacement, skipping only a *synthesised* heading, then
    # survived having the guard deleted entirely: `synthetic_heading`
    # builds the heading out of the filename, so the two agree by
    # construction for every name shape in the folder, and a filename
    # with no cycle token returns above. The guard could not change an
    # answer, so it is gone rather than tested -- an unreachable branch
    # and a blind test look identical in a mutation report and need
    # opposite fixes. The invariant it leaned on is pinned instead, by
    # `test_a_synthesised_heading_cannot_disagree_with_the_filename`.
    cycle = _cycle_finding(name, entry)
    if cycle:
        findings.append(cycle)
    return findings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", help="the entry as written, on local disk")
    ap.add_argument(
        "--name",
        help="the filename it will be written under, if it differs from the "
        "local one (e.g. 168-cycle-152.md)",
    )
    args = ap.parse_args(argv)
    try:
        with open(args.file, encoding="utf-8") as fh:
            content = fh.read()
    except OSError as exc:
        print(f"lint_entry: cannot read {args.file}: {exc}", file=sys.stderr)
        return 2
    name = args.name or args.file.rsplit("/", 1)[-1]
    findings = lint(name, content)
    if not findings:
        print(f"lint_entry: {name} renders as written.")
        return 0
    print(f"lint_entry: {name} would be repaired by the site, not read:", file=sys.stderr)
    for finding in findings:
        print(f"  - {finding}", file=sys.stderr)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
