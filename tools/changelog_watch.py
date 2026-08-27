"""Did a Claude Code release change something this loop actually depends on?

Cycle 547, on the owner's idea #126: *"The single most useful thing this
research cycle found was a version number in a Dockerfile compared
against a public changelog. That is a diff, not a judgement, and it does
not need a model to spot it. What needs judgement is which entries land
on us."*

    python3 -m tools.changelog_watch

`tools.cli_pin` already answers *whether* the pin is behind. It says
nothing about *what* is in the gap, and the gap is where the cost lives:
2.1.245 removed `messaging_socket_path`, which the bridge's own Dockerfile
comment records as the thing a hand-run contract check caught. That check
ran because a cycle happened to open the per-version changelog. Nothing
schedules that, and the ideas run that would is three days a week.

**This is a filter, not a judgement.** It fetches the changelog, takes
every section strictly newer than the version this loop is running, and
marks the entries that name something the bridge actually passes to the
CLI or reads off its stream. The reader still decides what to do; the
tool only refuses to make them read eighteen days of release notes to
find out there was nothing.

**The watch list is derived, not invented**, which is the only thing
keeping it from being a wall of keywords that matches everything. Every
term below is a literal string in `agora-claude-bridge` or in the
settings file the bridge writes, and each carries where it comes from.
Add a term when this loop starts depending on something new; a term
nothing here uses is noise with a justification attached.

**Two versions, and the older one is the subject** -- the same call
`cli_pin.older_of` already makes. The pin is what the next image build
installs; the running binary is what this session is executing inside. A
changelog entry only matters here once we are *running* past it, so the
gap is measured from whichever is older.

Exit status, matching `tools.security_alerts`, `tools.cli_pin` and the
rest so a cycle can read it without parsing the text: **2 when a release
newer than what we run touches something on the watch list**, 0 when
there is nothing newer or nothing newer touches us, 1 when something was
unreadable. "I could not check" never reads as "nothing here".

The one negative result this has to defend against is the cheap one:
if the changelog carries no section for the version we run, then "no
newer entries" and "I could not find where we are" are the same output.
That exits 1 and says so, rather than reporting a clean sweep from a
position it could not locate.
"""

import argparse
import re
import sys
import urllib.request

from tools.cli_pin import older_of, read_pin, running_version, version_key

CHANGELOG_URL = (
    "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
)

SECTION_RE = re.compile(r"^##+\s+v?(\d+\.\d+\.\d+)\s*$", re.MULTILINE)

# Each term is a literal this loop depends on, with where it comes from.
# A term that no longer appears in the bridge belongs out of this list,
# not commented out -- an unused watch term matches release notes about
# something nobody here runs, which is the noise this tool exists to cut.
WATCH_TERMS = (
    # bridge/cli.py builds the CLI argv from these, verbatim.
    "stream-json",
    "--output-format",
    "--input-format",
    "--forward-subagent-text",
    "--dangerously-skip-permissions",
    "--verbose",
    # bridge/cli.py reads these fields off the CLI's own stream.
    "parent_tool_use_id",
    # bridge/quota.py watches for this event to report remaining quota.
    "rate_limit_event",
    # The output caps the bridge sets in the CLI's environment.
    "BASH_MAX_OUTPUT_LENGTH",
    "TASK_MAX_OUTPUT_LENGTH",
    # Settings keys the bridge writes, and the flag gate tools.cli_features
    # watches for the second of them.
    "autoMemoryDirectory",
    "autoDreamEnabled",
)


def fetch_changelog(opener=urllib.request.urlopen):
    """Return (text, None) or (None, why-not)."""
    request = urllib.request.Request(CHANGELOG_URL)
    try:
        with opener(request, timeout=60) as response:
            return response.read().decode("utf-8"), None
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, f"could not reach the changelog: {exc}"


def parse_sections(text):
    """[(version, [entry, ...]), ...] in the order the changelog lists them.

    Entries are the `- ` bullets under a version heading, stripped of the
    marker and joined onto one line each, because a wrapped bullet that
    matched a watch term on its second line would otherwise print as a
    fragment.
    """
    matches = list(SECTION_RE.finditer(text))
    sections = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end]
        entries, current = [], None
        for line in body.splitlines():
            if line.startswith("- "):
                if current is not None:
                    entries.append(current)
                current = line[2:].strip()
            elif current is not None and line.strip():
                current += " " + line.strip()
            elif current is not None:
                entries.append(current)
                current = None
        if current is not None:
            entries.append(current)
        sections.append((match.group(1), entries))
    return sections


def newer_than(sections, subject):
    """The sections strictly newer than `subject`, or None if it is absent.

    None is the honest answer for a version the changelog does not carry
    -- a prerelease, a version yanked, or a document whose shape moved --
    and the caller reports that rather than the empty list it looks like.
    """
    low = version_key(subject)
    if low is None or not any(version == subject for version, _ in sections):
        return None
    out = []
    for version, entries in sections:
        key = version_key(version)
        if key is not None and key > low:
            out.append((version, entries))
    return out


def matching(entries, terms):
    """(hits, misses) -- entries naming a watch term, and the rest.

    Case-sensitive on purpose: the terms are flags, event names, env
    vars and settings keys, all of which are written one way, and a
    case-insensitive match on `--verbose` would catch prose about being
    verbose.
    """
    hits, misses = [], []
    for entry in entries:
        found = [term for term in terms if term in entry]
        (hits if found else misses).append((entry, found))
    return hits, misses


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--since",
        help="version to measure the gap from (default: whichever of the "
             "bridge pin and the running binary is older)",
    )
    parser.add_argument(
        "--watch", action="append", metavar="TERM",
        help="replace the derived watch list with these terms (repeatable)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="print every entry in the gap, not only the ones that match",
    )
    args = parser.parse_args(argv)
    terms = tuple(args.watch) if args.watch else WATCH_TERMS

    subject = args.since
    if subject is None:
        pinned, where = read_pin()
        if pinned is None:
            print(f"COULD NOT READ THE PIN — {where}")
            return 1
        subject, label = older_of(pinned, running_version())
        print(f"Reading forward from {subject} ({label}, from {where}).")
    else:
        print(f"Reading forward from {subject} (given on the command line).")

    text, error = fetch_changelog()
    if error:
        print(f"COULD NOT READ THE CHANGELOG — {error}")
        return 1

    sections = parse_sections(text)
    if not sections:
        print("COULD NOT READ THE CHANGELOG — no `## <version>` sections in it; "
              "the document's shape has moved and this parser is now blind.")
        return 1

    gap = newer_than(sections, subject)
    if gap is None:
        print(f"COULD NOT PLACE {subject} — the changelog has no section for "
              "it, so 'nothing newer' and 'I could not find where we are' "
              f"would print the same. {len(sections)} section(s) read, newest "
              f"{sections[0][0]}.")
        return 1

    if not gap:
        print(f"Nothing newer. {subject} is the newest version the changelog "
              f"carries ({len(sections)} section(s) read).")
        return 0

    total = sum(len(entries) for _, entries in gap)
    print(f"{len(gap)} release(s) newer than {subject}, {total} entr(y/ies) "
          f"in all: {', '.join(version for version, _ in gap)}")

    hit_count = 0
    for version, entries in gap:
        hits, misses = matching(entries, terms)
        hit_count += len(hits)
        if not hits and not args.all:
            print(f"\n## {version} — {len(misses)} entr(y/ies), none on the "
                  "watch list")
            continue
        print(f"\n## {version}")
        for entry, found in hits:
            print(f"  ⚠ [{', '.join(found)}] {entry}")
        if args.all:
            for entry, _ in misses:
                print(f"    {entry}")
        elif misses:
            print(f"  ({len(misses)} further entr(y/ies), none on the watch "
                  "list — `--all` prints them)")

    print(f"\nWatch list ({len(terms)} term(s)): {', '.join(terms)}")
    if hit_count:
        print(f"TOUCHES US — {hit_count} entr(y/ies) newer than {subject} name "
              "something this loop passes to the CLI or reads off its stream. "
              "Read them before the next pin bump; that is the check the "
              "bridge Dockerfile's comment asks for.")
        return 2
    print("Nothing on the watch list. The gap is real and none of it names "
          "anything this loop depends on — `--all` prints it if you want to "
          "judge that yourself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
