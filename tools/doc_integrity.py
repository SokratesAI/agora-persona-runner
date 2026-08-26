"""Has any vault document this loop maintains been spliced into itself?

Run it from the runner checkout, every cycle, before you pick:

    python3 -m tools.doc_integrity

**Exit 2 means a document is damaged**, 1 means one could not be read
(which never reads as clean), 0 means every document answered whole and
the report names the ones it swept.

On 2026-08-26 at 10:07:59 Oslo something wrote `comments.md` -- the file
the owner's chat-bubble replies land in -- with a second copy of the
document's header spliced into the middle of the first copy's
frontmatter. CouchDB revision 319 is revision 318 with 1,861 characters
inserted immediately after the literal `## Acknowledged` inside the
`contract:` line, carrying a `#### Nova` reply that then had no comment
above it, so the app could not render it and the owner never saw the
answer. The same splice at the same offset in the same file happened on
2026-08-13, and `agora_runner/md_sections.py` exists because of it.

Both were found by accident, by an unrelated tool refusing for an
unrelated reason. That is the gap this closes. `nova_comments.verify_write`
now counts landmark headings and refuses a duplicating write, but it only
sits in front of the three writers inside `nova_comments`; a cycle that
hand-rolls a script and PUTs through `vault_tool.py` goes around it, and a
hand-rolled script is what did the damage both times.

**The invariant is duplicate heading lines, not a per-file landmark
table.** A table of "which headings must be unique in which file" is a
list of numbers I would have invented, and it goes stale the first time a
document grows a section. Every one of these files instead has the
property that no two of its `#` or `##` headings are the same line: the
board files number their rows, the digest and its archive stamp theirs,
and comments.md has three fixed landmarks. Measured before it was written
-- all twelve documents below, live, hold zero duplicates today, and the
damaged revision 319 holds three (`# Comments`, `## New`, `## Acknowledged`).
So a duplicate is a finding in every one of them, and nothing has to be
maintained here when a file gains a section.

Frontmatter and fenced code are excluded through `md_sections`, for the
reason that module exists: these files quote their own headings back at
the reader inside a `contract:` line, and one of them uses a YAML block
scalar that puts a bare `## New` inside the header.

`### ` and deeper are deliberately not counted. `comments.md` keys a
comment on `### Cycle <n> · <stamp>` and two comments in the same minute
on the same card are a legal document, not damage.
"""

import argparse
import collections
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.md_sections import _ANY_HEADING_RE, _normalise, _skippable

VAULT_TOOL = "/app/bridge/vault_tool.py"

# Every markdown document this loop writes. JSON ledgers are left out --
# they have no headings and a duplicated one fails to parse loudly.
PATHS = (
    "projects/sokrates/projects/agora/nova/resources/comments.md",
    "projects/sokrates/projects/agora/journal-digest.md",
    "projects/sokrates/projects/agora/nova/resources/digest-archive.md",
    "projects/sokrates/projects/agora/nova/catalog.md",
    "projects/sokrates/projects/agora/nova/resources/idea-pool.md",
    "projects/sokrates/projects/nova/issues.md",
    "projects/sokrates/projects/nova/ideas.md",
    "projects/sokrates/projects/nova/notes.md",
    "projects/sokrates/projects/agora/nova/resources/issues.md",
    "projects/sokrates/projects/agora/nova/resources/ideas.md",
    "projects/sokrates/projects/agora/nova/resources/inbox.md",
)


def duplicate_headings(text):
    """`{heading line: count}` for every `#`/`##` heading that appears twice.

    The heading is returned normalised -- lowercased, whitespace collapsed --
    because that is what `md_sections` treats as the same heading and
    therefore what a renderer and the owner see as the same heading.
    """
    lines = (text or "").split("\n")
    skip = _skippable(lines)
    counts = collections.Counter()
    for i, line in enumerate(lines):
        if i in skip:
            continue
        match = _ANY_HEADING_RE.match(line)
        if match and len(match.group("hashes")) <= 2:
            counts[_normalise(line)] += 1
    return {name: n for name, n in counts.items() if n > 1}


def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    Same shape as `backlog_brief._fetch`, and for the same measured reason:
    `vault_tool.py get` prints `[not found: <path>]` on stdout and exits 0,
    so a return code alone reads a vanished file as an empty one -- which
    here would report a missing document as an undamaged one.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if not done.stdout.strip() or done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def check(paths=PATHS, fetch=_fetch):
    """`(damaged, unreadable, clean)` -- three lists, in report order.

    `damaged` entries are `(path, {heading: count})`. Taking `fetch` as an
    argument is what lets the tests run without a vault client; nothing
    else passes it.
    """
    damaged, unreadable, clean = [], [], []
    for path in paths:
        text = fetch(path)
        if text is None:
            unreadable.append(path)
            continue
        found = duplicate_headings(text)
        (damaged.append((path, found)) if found else clean.append(path))
    return damaged, unreadable, clean


def report(damaged, unreadable, clean, out=sys.stdout):
    """Print the finding, and return the exit code it deserves."""
    for path, found in damaged:
        print(f"SPLICED — {path}", file=out)
        for name, n in sorted(found.items(), key=lambda item: -item[1]):
            print(f"    {name!r} appears {n} times outside the frontmatter", file=out)
        print("    A second copy of the document, or of one of its sections, is inside this file.", file=out)
        print("    Read it before writing to it — a write on top of this makes the damage permanent.", file=out)
    for path in unreadable:
        print(f"COULD NOT READ — {path}", file=out)
    if damaged:
        print(f"{len(damaged)} damaged document(s). Swept {len(clean) + len(damaged) + len(unreadable)}.", file=out)
        return 2
    if unreadable:
        print(f"Could not read {len(unreadable)} document(s) — that is no instrument, not no damage.", file=out)
        return 1
    print(f"Whole. Swept {len(clean)} document(s): {', '.join(clean)}", file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("paths", nargs="*", default=None,
                        help="vault paths to check; defaults to every document this loop writes")
    args = parser.parse_args(argv)
    return report(*check(tuple(args.paths) if args.paths else PATHS))


if __name__ == "__main__":
    sys.exit(main())
