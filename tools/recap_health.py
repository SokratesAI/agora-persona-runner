"""Raise when the twelve-hour recap card on the Journal page has gone stale.

The card is the first thing the owner sees on the Journal page and he rated
it 🔴 Immediately. `agora_runner/nova_recap.py` explains why its bullets are
written by a cycle rather than computed -- a cycle is not a topic, and every
mechanical grouping produced defensible groups and bullets that were not
sentences. The cost of that choice is the one this check pays off: **the
card is only as fresh as the last cycle that chose to rewrite it, and
nothing asked any cycle to.** Cycle 902 built the card and said so in the
handoff; the handoff is read by one cycle and then rolls off.

So the reminder goes where every cycle already looks. `preflight` is the one
call each cycle makes before it picks anything.

**The threshold is not a second opinion.** `STALE_AFTER_HOURS` and
`parse_recap` are imported from `nova_recap`, which is the same module the
site's `/api/recap` route renders the card from, so this check raises at the
same instant the card starts telling him it is stale -- never before, never
after. A separate number here would be a second definition of "stale" that
could drift from the one on his screen, and then the check and the card
would disagree in front of him.

**What it deliberately does not do is write the recap.** Six bullets
summarising twelve hours is the judgement `nova_recap` says cannot be taken
out of a model. This prints the age, the number of cycles that have filed a
journal entry since the recap was stamped, and the two commands, and stops.

**Exit contract.** 2 when the card is stale and a cycle should rewrite it.
1 when `recap.md` could not be read at all -- a pod with no vault client
must not read as a fresh card. 0 when the card is current.
"""

import argparse
import pathlib as _pathlib
import re
import subprocess
import sys as _sys

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_recap import (  # noqa: E402
    RECAP_PATH,
    STALE_AFTER_HOURS,
    parse_recap,
)

VAULT_TOOL = "/app/bridge/vault_tool.py"
JOURNAL_DIR = "projects/sokrates/projects/agora/nova/journal/"

#: `<seq>-cycle-<n>.md`. A weekly entry is `<seq>-monday-research.md` and
#: carries no cycle number at all (its heartbeat has its own counter), so it
#: is not matched here on purpose -- counting it would inflate "cycles since"
#: with a number that never appears in a recap's `cycles` range.
_ENTRY = re.compile(r"^\d+-cycle-(\d+)\.md$")

#: `cycles 871-901`, or a bare `901`, or empty. Only the last number matters:
#: it is the newest cycle the recap claims to cover.
_RANGE_END = re.compile(r"(\d+)\s*$")


def _vault(*args):
    return subprocess.run(
        [_sys.executable, VAULT_TOOL, *args],
        capture_output=True, text=True, check=True,
    ).stdout


def read_recap():
    """`recap.md` as text, or None when the vault cannot answer.

    None and empty are the same finding and both exit 1: this check has no
    way to distinguish "nobody has ever written the card" from "I cannot
    see the vault from here", and guessing between them would let a pod
    with no vault client report a card it never read.
    """
    try:
        raw = _vault("get", RECAP_PATH)
    except (subprocess.CalledProcessError, OSError, FileNotFoundError):
        return None
    # `vault_tool.py get` prints the document plus exactly one newline.
    body = raw[:-1] if raw.endswith("\n") else raw
    if not body.strip() or body.strip().startswith("[not found]"):
        return None
    return body


def entry_cycles(listing):
    """Every cycle number with a journal entry, from a `vault_tool.py ls`."""
    found = []
    for line in (listing or "").splitlines():
        name = line.strip().rsplit("/", 1)[-1]
        match = _ENTRY.match(name)
        if match:
            found.append(int(match.group(1)))
    return sorted(found)


def cycles_since(covered, filed):
    """How many cycles have filed an entry the recap does not cover.

    `covered` is the recap's own `cycles` string. Unparseable or absent, the
    honest answer is None rather than 0 -- a recap that does not say what it
    covers cannot be shown to cover anything, and 0 would read as "nothing
    has happened since", which is the opposite claim.
    """
    match = _RANGE_END.search((covered or "").strip())
    if not match:
        return None
    newest_covered = int(match.group(1))
    return len([n for n in filed if n > newest_covered])


def report(payload, since, out=print):
    """One skimmable block, and the exit code. Never writes anything."""
    age = payload.get("ageHours")
    label = payload.get("writtenLabel") or "?"
    covered = payload.get("cycles") or "unstated"
    count = len(payload.get("bullets", []))

    if not payload.get("stale"):
        out(f"CURRENT  the recap card was written at {label} Oslo, "
            f"{age}h ago against a {STALE_AFTER_HOURS}h threshold — "
            f"{count} bullet(s), cycles {covered}.")
        if since:
            out(f"         {since} cycle(s) have filed an entry since; "
                "still inside the window, so nothing to do.")
        return 0

    if age is None:
        out("STALE    the recap card carries no readable `generated:` stamp, "
            "so the page cannot tell him how old it is and shows it as stale.")
    else:
        out(f"STALE    the recap card was written at {label} Oslo, {age}h ago, "
            f"past the {STALE_AFTER_HOURS}h threshold — the Journal page is "
            "already telling him so on the card.")
    out(f"         it covers cycles {covered} and carries {count} bullet(s).")
    if since:
        out(f"         {since} cycle(s) have filed a journal entry since then.")
    out("         rewrite it — the bullets are a judgement, not a computation:")
    out("           python3 -m tools.recap")
    out("           python3 -m tools.recap --put bullets.txt --cycles <A>-<B>")
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", metavar="PATH",
                        help="read this local file instead of the vault")
    args = parser.parse_args(argv)

    if args.file:
        body = open(args.file, encoding="utf-8").read()
    else:
        body = read_recap()
    if body is None:
        print("CANNOT SEE  `recap.md` could not be read, so the card's age is "
              "unknown. That is not the same as a fresh card and is not "
              f"reported as one. Path: {RECAP_PATH}")
        return 1

    payload = parse_recap(body)
    try:
        since = cycles_since(payload.get("cycles"), entry_cycles(_vault("ls", JOURNAL_DIR)))
    except (subprocess.CalledProcessError, OSError, FileNotFoundError):
        # The journal listing is colour on the finding, never the finding.
        # Losing it must not turn a stale card into an unreadable one.
        since = None
    return report(payload, since)


if __name__ == "__main__":
    raise SystemExit(main())
