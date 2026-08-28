"""How often does the owner have to correct me, and is that falling?

Idea #88 on his ideas board is a criticism of this loop that I agree with:
*"every number I use to justify rewriting my own constitution is generated
by the thing being measured"* -- merged PRs, reviewer findings, retro
scores, and since Cycle 557 `tools.goal_measures` too. All of it is the
loop grading its own homework.

Cycle 336 costed the instrument the row originally proposed and closed it:
one run of Terminal-Bench 2.0 is roughly 58% of a seven-day quota window,
the method around it needs 890-1,246 task runs, and neither pod has a
container runtime to run a single task in. The row stayed open needing *a
cheaper external instrument*. This is one.

    python3 -m tools.external_signal

**The corpus is the owner's own words, timestamped by the app that stored
them.** `comments.md` holds every comment he has typed on a journal card,
each under a `### Cycle N · <date>` heading his app wrote, with my replies
under `#### Nova · <date>` headings beneath. This reads his headings and
ignores mine. Nothing in the measured text was produced by this loop, which
is the whole property idea #88 asks for and the one a self-scored retro
does not have.

**Two numbers, both about him rather than about me.** How many comments he
wrote in a week, and how many of those carry one of his own correction
markers -- `wrong`, `annoy`, `do not`, `should not` -- or a repetition
marker, where he is telling me something he has told me before. A
constitution edit that works should push the correction share down. If it
does not move over months, the edits are decoration, and that is the answer
this row was built to be able to receive.

**The marker list is printed, not hidden** (`--rules`), and every matched
comment can be listed with the phrase that matched it (`--show`), because a
share of a corpus is only honest if the reader can go and disagree with the
individual calls. The phrases are his: I took them by counting the corpus
rather than by imagining how he writes.

**What this cannot see, printed on every run.** A correction he made in
chat, in the vault directly, or out loud is not here -- only the app's
comment box writes this file. `notes.md` and his board captures carry
corrections too and are deliberately excluded, because neither is
timestamped and an undated event cannot join a trend. The marker list is
mine even though the words are his, so a correction phrased in language it
does not hold is missed, and a comment that merely quotes the word "wrong"
is counted. And silence is ambiguous in both directions: a week he did not
write is not a week I did well.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from collections import Counter

VAULT_TOOL = "/app/bridge/vault_tool.py"
COMMENTS = "projects/sokrates/projects/agora/nova/resources/comments.md"

# the owner's headings, written by the app. Mine are `#### Nova · <date>` and are
# not matched here on purpose -- the point of the instrument is that no line
# of the measured text came from this loop.
OWNER_BLOCK = re.compile(
    r"^### Cycle (?P<cycle>\d+) · (?P<date>\d{4}-\d{2}-\d{2})[^\n]*\n"
    r"(?P<body>.*?)(?=^\#{3,4} |\Z)",
    re.M | re.S,
)

# Grounded in the corpus rather than invented: Cycle 558 counted candidate
# phrases across all 155 comments and kept the ones he actually uses.
CORRECTION_MARKERS = [
    r"\bwrong\b", r"\bfalse\b", r"\bincorrect\b", r"\bmistake\b",
    r"\bdo not\b", r"\bdon't\b", r"\bdont\b", r"\bshould not\b", r"\bshouldn't\b",
    r"\bannoy(?:ed|ing|s)?\b", r"\bconfusing\b", r"\bhard to read\b",
    r"\bthat is not\b", r"\bit'?s not\b", r"\bits not\b", r"\bnot what i\b",
    r"\bwhy did you\b", r"\byou missed\b",
]

# He is saying something he has already said. A bare "again" is excluded --
# "maybe turn it on again?" is not a repetition, and it is the common use.
REPEAT_MARKERS = [
    r"\bI have told you\b", r"\bI told you\b", r"\bas I (?:have )?said\b",
    r"\blike I said\b", r"\bI already (?:said|told)\b",
    r"\brepeat myself\b", r"\brepeating myself\b",
    r"\byou often\b", r"\byou keep\b", r"\byou always\b",
    r"\bhow many times\b", r"\bonce again\b", r"\byet again\b",
    r"\bagain and again\b", r"\bagain,", r"\bsaid (?:this )?before\b",
    r"\bevery time\b",
]


def parse_comments(text):
    """Every comment the owner wrote, oldest first: `(cycle, date, body)`."""
    found = [
        (int(m.group("cycle")), m.group("date"), m.group("body").strip())
        for m in OWNER_BLOCK.finditer(text or "")
    ]
    return sorted(found, key=lambda row: (row[1], row[0]))


def _matches(body, markers):
    """Every marker phrase present in `body`, as it appears there."""
    hits = []
    for pattern in markers:
        found = re.search(pattern, body, re.I | re.M)
        if found:
            hits.append(found.group(0))
    return hits


def classify(body):
    """`(correction_phrases, repeat_phrases)` for one comment."""
    return _matches(body, CORRECTION_MARKERS), _matches(body, REPEAT_MARKERS)


def week_of(date):
    """ISO week label, `2026-W35`, for a `YYYY-MM-DD` string."""
    year, week, _ = dt.date.fromisoformat(date).isocalendar()
    return f"{year}-W{week:02d}"


def _week_complete(date):
    """Has the ISO week holding `date` finished? Sunday is its last day."""
    return dt.date.fromisoformat(date).isoweekday() == 7


def measure(text):
    """`(rows, weeks)` -- every comment classified, and the weekly tally.

    `rows` are `(cycle, date, corrections, repeats)`. `weeks` maps an ISO
    week label to a `Counter` with `comments`, `corrected` and `repeated`.
    A comment counts once for the week however many markers it carries.
    """
    rows, weeks = [], {}
    for cycle, date, body in parse_comments(text):
        corrections, repeats = classify(body)
        rows.append((cycle, date, corrections, repeats))
        tally = weeks.setdefault(week_of(date), Counter())
        tally["comments"] += 1
        tally["corrected"] += 1 if corrections else 0
        tally["repeated"] += 1 if repeats else 0
    return rows, weeks


def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    Same shape and same measured reason as `doc_integrity._fetch`: the client
    prints `[not found: <path>]` and exits 0, so a return code alone reads a
    vanished corpus as an empty one -- which here would report zero
    corrections, the best possible score, from no evidence at all.
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


def report(rows, weeks, show=False, out=sys.stdout):
    """Print the trend, and return the exit code it deserves."""
    if not rows:
        print("NO CORPUS — comments.md parsed to zero comments from the owner.", file=out)
        print("    That is no instrument, not a clean week; the heading format may have changed.", file=out)
        return 1

    print("THE OWNER'S CORRECTIONS — an outside reading of this loop", file=out)
    print(f"  corpus: {len(rows)} comment(s) he wrote, {rows[0][1]} to {rows[-1][1]}", file=out)
    print(file=out)
    print("  week        comments   corrected   repeated   corrected share", file=out)
    labels = sorted(weeks)
    for label in labels:
        tally = weeks[label]
        share = 100.0 * tally["corrected"] / tally["comments"]
        # The newest week is normally still running, so its counts are a
        # part-week and only its share compares with the rows above it.
        note = "  (part week — compare the share, not the count)" \
            if label == labels[-1] and not _week_complete(rows[-1][1]) else ""
        print(f"  {label}      {tally['comments']:8d}   {tally['corrected']:9d}   "
              f"{tally['repeated']:8d}   {share:13.0f}%{note}", file=out)

    total = len(rows)
    corrected = sum(1 for row in rows if row[2])
    repeated = sum(1 for row in rows if row[3])
    print(file=out)
    print(f"  Overall {corrected}/{total} corrected ({100.0 * corrected / total:.0f}%), "
          f"{repeated} carrying a repetition marker.", file=out)

    if show:
        print(file=out)
        for cycle, date, corrections, repeats in rows:
            if corrections or repeats:
                phrases = ", ".join(sorted(set(corrections + repeats)))
                print(f"  {date}  cycle {cycle:<5} {phrases}", file=out)

    print(file=out)
    print("  Nothing measured above was written by this loop — the words and the", file=out)
    print("  timestamps are his. The rule that sorts them is mine: --rules prints it,", file=out)
    print("  --show lists every comment it matched so the calls can be argued with.", file=out)
    print("  Blind to: corrections made in chat, in the vault, or out loud; notes.md", file=out)
    print("  and board captures, which carry corrections but no timestamp to trend.", file=out)
    print("  A quiet week is not a good week — silence is ambiguous in both directions.", file=out)
    return 0


def print_rules(out=sys.stdout):
    print("Correction markers — he is telling me something is wrong:", file=out)
    for pattern in CORRECTION_MARKERS:
        print(f"    {pattern}", file=out)
    print("Repetition markers — he is telling me something he has told me before:", file=out)
    for pattern in REPEAT_MARKERS:
        print(f"    {pattern}", file=out)
    print("Counted per comment, not per phrase. A bare 'again' is deliberately not a", file=out)
    print("repetition marker: 'maybe turn it on again?' is his commonest use of it.", file=out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--show", action="store_true",
                        help="list every matched comment with the phrase that matched it")
    parser.add_argument("--rules", action="store_true",
                        help="print the marker lists and exit")
    parser.add_argument("--path", default=COMMENTS, help="vault path of the corpus")
    args = parser.parse_args(argv)

    if args.rules:
        print_rules()
        return 0

    text = _fetch(args.path)
    if text is None:
        print(f"COULD NOT READ — {args.path}", file=sys.stdout)
        print("    That is no instrument, not no corrections.", file=sys.stdout)
        return 1
    rows, weeks = measure(text)
    return report(rows, weeks, show=args.show)


if __name__ == "__main__":
    sys.exit(main())
