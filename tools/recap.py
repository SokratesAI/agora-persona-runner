"""The raw material for the twelve-hour recap card, and the writer for it.

The owner, capture 2026-09-04, 🔴 Immediately: *"I want a stick Journal card
at the top that summarizes the last 12 hours ... max 5-6 bullets as many
cycles work on the same problem/project."*

Read `agora_runner/nova_recap.py` for why the bullets are written by a
cycle instead of computed: a cycle is not a topic, and every mechanical
grouping I tried produced defensible groups and bullets that were not
sentences. What a tool *can* do without judgement is put the window's
entries in front of the cycle writing them, which is what a bare run
does, and stamp and store the result, which is `--put`.

    python3 -m tools.recap                    # the window, newest first
    python3 -m tools.recap --put bullets.txt  # write it to the vault

`bullets.txt` is one bullet per line, with or without the leading `- `.
The stamp is written here rather than typed, for the reason the card
prints it at all: a recap whose "as of" is hand-typed is a recap whose
"as of" can be wrong, and the whole job of that line is to be right.
"""

import argparse
import re
import subprocess
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pathlib as _pathlib
import sys as _sys

_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.nova_recap import RECAP_PATH  # noqa: E402

VAULT_TOOL = "/app/bridge/vault_tool.py"
JOURNAL_DIR = "projects/sokrates/projects/agora/nova/journal/"
OSLO = ZoneInfo("Europe/Oslo")

DEFAULT_HOURS = 12
#: He asked for "max 5-6". Six is the ceiling and it is his number, not
#: one I chose -- so this refuses a seventh rather than silently cutting,
#: because a summary quietly missing its last bullet is worse than a
#: refusal a cycle can see and fix.
MAX_BULLETS = 6

_STAMP_LINE = re.compile(r"^\s*(?:PR|Board|Outcome)\b", re.I)


def _vault(*args):
    out = subprocess.run(
        [sys.executable, VAULT_TOOL, *args],
        capture_output=True, text=True, check=True,
    ).stdout
    # `vault_tool.py get` prints the document plus exactly one newline.
    return out[:-1] if out.endswith("\n") else out


def _entry_names():
    listing = _vault("ls", JOURNAL_DIR)
    names = [line.strip().rsplit("/", 1)[-1]
             for line in listing.splitlines() if line.strip().endswith(".md")]
    names.sort()
    return names


#: Minutes between cycles, used only to turn "12 hours" into a count of
#: entries. An entry's heading carries no timestamp -- I checked, they are
#: `### Cycle 900 — <title>` -- and the cheapest date on one is the entry
#: text itself, which is the thing being summarised. So the window is
#: approximate by construction and this says so rather than implying a
#: precision it does not have. Reading two extra entries costs the writer
#: nothing; the card's own stamp is the exact claim.
CYCLE_MINUTES = 24


def window(hours=DEFAULT_HOURS, now=None, limit=None):
    """The newest entries, with the one line each that says what they were."""
    now = now or datetime.now(OSLO)
    cutoff = now - timedelta(hours=hours)
    if limit is None:
        limit = max(1, int(hours * 60 / CYCLE_MINUTES))
    rows = []
    for name in reversed(_entry_names()[-limit:]):
        body = _vault("get", JOURNAL_DIR + name)
        head = ""
        for line in body.splitlines():
            if line.startswith("### "):
                head = line[4:].strip()
                break
        stamp = _entry_when(head)
        if stamp is not None and stamp < cutoff:
            break
        footer = [l.strip() for l in body.splitlines() if l.strip().startswith("PR:")]
        rows.append({
            "file": name,
            "title": head,
            "footer": footer[-1] if footer else "",
            "when": stamp,
        })
    return rows


_WHEN = re.compile(r"(20\d\d-\d\d-\d\d)[ T](\d\d:\d\d)")


def _entry_when(heading):
    match = _WHEN.search(heading or "")
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1) + "T" + match.group(2)).replace(tzinfo=OSLO)
    except ValueError:
        return None


def render(bullets, now=None, cycles=""):
    now = now or datetime.now(OSLO)
    lines = [
        "---",
        "type: log",
        "tags: [agora, recap]",
        "status: capture",
        f"updated: {now:%Y-%m-%d}",
        "maintenance: Written by a cycle, read by the Journal page's top card. "
        "One line per bullet, never hard-wrapped. `python3 -m tools.recap --put` "
        "writes it and stamps it; do not hand-edit the generated comment.",
        "---",
        "",
        "# Last 12 hours",
        "",
        f"<!-- generated: {now.isoformat(timespec='minutes')} | cycles {cycles} -->",
        "",
    ]
    lines += [f"- {b}" for b in bullets]
    return "\n".join(lines) + "\n"


def read_bullets(path):
    raw = [l.strip() for l in open(path, encoding="utf-8").read().splitlines()]
    bullets = [l[2:].strip() if l.startswith("- ") else l for l in raw if l]
    if len(bullets) > MAX_BULLETS:
        raise SystemExit(
            f"recap: {len(bullets)} bullets, and he asked for at most {MAX_BULLETS}. "
            "Merge two of them rather than letting one be cut."
        )
    return bullets


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS)
    parser.add_argument("--put", metavar="FILE", help="write these bullets to the vault")
    parser.add_argument("--cycles", default="", help="the cycle range the recap covers")
    args = parser.parse_args(argv)

    if args.put:
        bullets = read_bullets(args.put)
        body = render(bullets, cycles=args.cycles)
        tmp = "/tmp/recap.md"
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(body)
        print(_vault("put", RECAP_PATH, tmp))
        return 0

    rows = window(hours=args.hours)
    print(f"{len(rows)} entr(y/ies) in the newest {args.hours}h. "
          "Group them by what the work was, not by cycle -- he asked for that explicitly.")
    for row in rows:
        print(f"  {row['file']}  {row['title']}")
        if row["footer"]:
            print(f"      {row['footer']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
