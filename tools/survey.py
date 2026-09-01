"""Ask the owner a short survey, on a weekly cadence, and read his answers back.

His ask, `ideas.md` capture rated High: *"I remember asking you to think
like a product manager. The first thing a product manager needs to know is
how much the customers like his products. And how does he get that
information? Qualitative surveys ... You have none of this and it is
therefore hard for you to know whats wrong with your products, only my
reported issues and ideas. ... I therefore want you to run surveys in me,
maybe weekly. They can't be to long, so maybe even make one of the last
questions to be on the survey itself to improve it. This data will serve as
your benchmark on the quality of your products. Maybe i do not even know
about all your products and features."*

Everything I know about how this loop is doing today comes from him
reporting a problem. That is a complaints channel, not a measurement: it
only ever fires when something is bad enough to type about, so a feature he
quietly does not use looks exactly like a feature he is happy with.

**The surface is a vault file in his own folder, not a page in the app.**
`projects/sokrates/projects/nova/survey.md` sits beside `notes.md`,
`issues.md` and `ideas.md` -- the same folder, the same database, so it is
on his phone through Obsidian the moment it is written, with no deploy in
between. It also means the answer format is one he already uses every day:
a question line, and an indented `  - ` bullet under it that he types into.

Like `tools.board_capture`, this **takes a path on disk and knows nothing
about the vault** when `--file` is given; the caller owns the
compare-and-swap:

    C='projects/sokrates/projects/nova/survey.md'
    python3 /app/bridge/vault_tool.py get "$C" --rev-file /tmp/s.$$.rev > survey.md \\
      && python3 -m tools.survey --file survey.md --post \\
      && python3 /app/bridge/vault_tool.py put "$C" survey.md --if-rev-file /tmp/s.$$.rev

With no `--file` it fetches that path itself, which is the form `preflight`
runs every cycle. A pod with no vault client reads that as `CANNOT SEE` and
exits 1 rather than reporting a clean cadence it never measured.

**Exit contract.** 2 when there is something for a cycle to do -- a survey
is due, or he has answered one nobody has read. 1 when the file could not
be read at all. 0 when the newest survey is fresh, or is waiting on him.

**Reading his answers is not enough; the reply is the contract.** He rated
the survey itself 3 of 5 on 2026-08-30 and said why: *"Maybe, depends what
you do with it"*. Every other channel he writes to already answers him back
-- `notes.md` moves a bullet under `## Read` with one line on what was done,
`comments.md` moves one under `## Acknowledged`, a board row gets a comment
-- and this one gave him a `— read Cycle N` stamp on a heading and nothing
else. So `--mark-read` now **refuses without `--reply`**, and the reply is
written into his file directly under his own answers, where he is already
looking. A rule that lives only in a prompt is a rule a cycle forgets; this
one is the argument parser.

**An unanswered survey is never re-asked and never stacked.** Due means the
newest section is at least `INTERVAL_DAYS` old *and answered*, or that the
file holds no survey at all. Nagging him weekly for an answer he has
already declined to give is how a survey stops being read, and five
unanswered copies are worth less than one.
"""

import argparse
import datetime as _dt
import re
import subprocess
import sys

SURVEY_PATH = "projects/sokrates/projects/nova/survey.md"

VAULT_TOOL = "/app/bridge/vault_tool.py"

#: Weekly, as he asked. A survey is due this many days after the newest
#: answered one.
INTERVAL_DAYS = 7

#: Survey v1. Five questions and it has to stay short -- *"They can't be to
#: long"* is a constraint he wrote himself, so this is his number rather
#: than one I picked. Q4 is the "maybe i do not even know about all your
#: products and features" half of the ask: it asks about the gap between
#: what exists and what he knows exists, which is the only question here
#: that a bug report could never have told me. Q5 is the survey grading
#: itself, which he asked for by name.
QUESTIONS = (
    "How useful was my work to you this week? (1-5, and one line on why)",
    "How well could you tell what I was doing and why? (1-5)",
    "What is the one thing I should do differently next week?",
    "Was there anything you wanted from me this week and could not get, "
    "or did not know how to ask for?",
    "Was this survey worth the two minutes? (1-5) What should change about it?",
)

#: A section header: `## 2026-08-30`, optionally followed by the marker a
#: cycle adds once it has read the answers. The date is what everything
#: keys off, so it is the only part that has to parse.
_HEADER = re.compile(r"^## (\d{4}-\d{2}-\d{2})\s*(.*)$")
_QUESTION = re.compile(r"^\d+\. (.*)$")
_ANSWER = re.compile(r"^  - ?(.*)$")

READ_MARKER = "— read Cycle "

#: How a reply is written into his file. It deliberately matches none of
#: `_HEADER`, `_QUESTION` or `_ANSWER`: a reply that parsed as one of his
#: own answer bullets would come back out of `parse` as something he
#: typed, and the next cycle would read my words as his.
REPLY_PREFIX = "**Nova, Cycle "


class Section:
    """One survey: its date, its question/answer pairs, and who has read it."""

    def __init__(self, date, answers, read_by=None, replied=False):
        self.date = date
        self.answers = answers
        self.read_by = read_by
        self.replied = replied

    @property
    def answered(self):
        """True once he has typed into **any** answer bullet.

        Any rather than all, deliberately. He is allowed to skip a question
        he has no opinion about, and treating a four-of-five reply as
        unanswered would leave it sitting in the file forever, unread by
        me and apparently ignored by me.
        """
        return any(a.strip() for _, a in self.answers)


def render(date_str, questions=QUESTIONS):
    """The markdown for one survey, newest-first order assumed by the caller."""
    lines = ["## %s" % date_str, ""]
    for n, question in enumerate(questions, start=1):
        lines.append("%d. %s" % (n, question))
        lines.append("  - ")
    lines.append("")
    return "\n".join(lines)


def parse(text):
    """Every survey section in `text`, newest first as written."""
    sections = []
    current = None
    pending = None
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header:
            read_by = None
            tail = header.group(2)
            if READ_MARKER in tail:
                read_by = tail.split(READ_MARKER, 1)[1].strip()
            current = Section(header.group(1), [], read_by)
            sections.append(current)
            pending = None
            continue
        if current is None:
            continue
        if line.startswith(REPLY_PREFIX):
            # Checked before the answer patterns on purpose: a reply is a
            # paragraph of mine sitting inside his section, and the only
            # thing `parse` has to know about it is that it is there.
            current.replied = True
            continue
        question = _QUESTION.match(line)
        if question:
            pending = question.group(1)
            current.answers.append((pending, ""))
            continue
        answer = _ANSWER.match(line)
        if answer and pending is not None and current.answers:
            q, existing = current.answers[-1]
            joined = (existing + " " + answer.group(1)).strip()
            current.answers[-1] = (q, joined)
    return sections


def newest(sections):
    """The section with the latest date, or `None`.

    On the date rather than on file position: I write newest-first, but he
    edits this file by hand on a phone and the order is his to disturb.
    """
    if not sections:
        return None
    return max(sections, key=lambda s: s.date)


def is_due(sections, today, interval_days=INTERVAL_DAYS):
    """`(due, reason)` -- whether a cycle should post a new survey now."""
    latest = newest(sections)
    if latest is None:
        return True, "no survey has ever been posted"
    if not latest.answered:
        return False, "the %s survey is still waiting on him" % latest.date
    age = (today - _dt.date.fromisoformat(latest.date)).days
    if age < interval_days:
        return False, "the %s survey is %d day(s) old" % (latest.date, age)
    return True, "the newest survey (%s) is %d day(s) old and answered" % (
        latest.date, age)


def unread(sections):
    """Answered surveys no cycle has recorded reading."""
    return [s for s in sections if s.answered and not s.read_by]


def post(text, date_str, questions=QUESTIONS):
    """`text` with a new survey inserted directly under the frontmatter."""
    lines = text.splitlines()
    at = 0
    if lines and lines[0].strip() == "---":
        for n in range(1, len(lines)):
            if lines[n].strip() == "---":
                at = n + 1
                break
    while at < len(lines) and not lines[at].strip():
        at += 1
    block = render(date_str, questions).splitlines()
    return "\n".join(lines[:at] + [""] + block + lines[at:]).lstrip("\n") + "\n"


def mark_read(text, date_str, cycle):
    """Stamp one section's header as read, so it stops raising every cycle."""
    out = []
    for line in text.splitlines():
        header = _HEADER.match(line)
        if header and header.group(1) == date_str and READ_MARKER not in line:
            line = "## %s %s%s" % (date_str, READ_MARKER, cycle)
        out.append(line)
    return "\n".join(out) + "\n"


def add_reply(text, date_str, cycle, reply):
    """`text` with my answer written under his answers in that section.

    Placed inside his own section rather than appended to the file or
    written somewhere else, because the whole complaint is that he could
    not tell what came of what he typed. His answers and my reply read as
    one exchange when they are three lines apart, and as two documents
    when they are not.

    One line per paragraph and no hard wrapping -- `personality.md`, and
    the reason is Obsidian: a single newline renders as a real line break,
    so a wrapped paragraph lands on his phone with one word on a line of
    its own.

    Raises `ValueError` rather than writing something wrong: an unknown
    date would otherwise write a reply into a file where nobody would find
    it, a second reply would leave two answers to one survey with nothing
    saying which is current, and a reply line that parses as one of his
    answer bullets or as a section header corrupts the document for every
    later read.
    """
    for line in reply.splitlines():
        if _HEADER.match(line) or _QUESTION.match(line) or _ANSWER.match(line):
            raise ValueError(
                "a reply line would parse as part of his survey: %r" % line)
    lines = text.splitlines()
    start = None
    for n, line in enumerate(lines):
        header = _HEADER.match(line)
        if header and header.group(1) == date_str:
            start = n
            break
    if start is None:
        raise ValueError("no survey section dated %s" % date_str)
    end = len(lines)
    for n in range(start + 1, len(lines)):
        if _HEADER.match(lines[n]):
            end = n
            break
    for line in lines[start + 1:end]:
        if line.startswith(REPLY_PREFIX):
            raise ValueError("the %s survey already carries a reply" % date_str)
    at = end
    while at > start + 1 and not lines[at - 1].strip():
        at -= 1
    block = ["", "%s%s:** %s" % (REPLY_PREFIX, cycle, reply.strip())]
    return "\n".join(lines[:at] + block + lines[at:]) + "\n"


def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    Same shape and same measured reason as `backlog_brief._fetch`: `get`
    prints `[not found: <path>]` on stdout and exits **0**, so a return
    code alone reads a vanished file as an empty one -- which here would
    read as "no survey has ever been posted" and quietly post a second copy
    of one he is already looking at.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def report(sections, today, out=None):
    """Print the status and return the exit code."""
    out = sys.stdout if out is None else out
    due, reason = is_due(sections, today)
    waiting = unread(sections)
    for section in waiting:
        print("ANSWERED AND UNREAD  %s" % section.date, file=out)
        for question, answer in section.answers:
            print("    %s" % question, file=out)
            print("      %s" % (answer.strip() or "(no answer)"), file=out)
        # The command, not just the verdict. Every other check here names
        # its own fix, and this one asks for a sentence I have to write --
        # which is exactly the step a cycle skips when it has to go and
        # look up how.
        print("    Answer him where he asked, then stamp it: "
              "python3 -m tools.survey --file survey.md --mark-read %s "
              "--cycle <N> --reply '<what I did about it>'" % section.date,
              file=out)
    if due:
        print("DUE  post a new survey -- %s" % reason, file=out)
    print("Judged %d survey(s) in his own vault file; weekly cadence, "
          "interval %d day(s). %s"
          % (len(sections), INTERVAL_DAYS, reason.capitalize() + "."), file=out)
    return 2 if (due or waiting) else 0


def main(argv=None, fetch=_fetch, out=None):
    # Resolved here rather than in the signature: a default bound at
    # import time is the *original* stdout, so a test that captures
    # stdout sees nothing and passes on an empty string.
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="the survey file on disk; "
                                       "without it the vault path is fetched")
    parser.add_argument("--today", help="YYYY-MM-DD, for tests")
    parser.add_argument("--post", action="store_true",
                        help="rewrite --file with a new survey on top")
    parser.add_argument("--mark-read", metavar="DATE",
                        help="stamp that section's answers as read")
    parser.add_argument("--cycle", help="the cycle number for --mark-read")
    parser.add_argument("--reply", help="what I did about his answers; "
                                        "written into his file under them")
    args = parser.parse_args(argv)

    today = (_dt.date.fromisoformat(args.today) if args.today
             else _dt.datetime.now().date())

    if args.file:
        try:
            with open(args.file, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print("CANNOT SEE  %s: %s" % (args.file, exc), file=out)
            return 1
    else:
        text = fetch(SURVEY_PATH)
        if text is None:
            print("CANNOT SEE  no vault client on this pod, or %s is gone -- "
                  "the survey cadence is not judged this run" % SURVEY_PATH,
                  file=out)
            return 1

    if args.post or args.mark_read:
        if not args.file:
            print("--post and --mark-read need --file; the caller owns the "
                  "compare-and-swap", file=out)
            return 1
        if args.mark_read:
            if not args.cycle:
                print("--mark-read needs --cycle", file=out)
                return 1
            if not (args.reply or "").strip():
                print("--mark-read needs --reply: a stamp on the heading "
                      "tells him a cycle read this and nothing about what "
                      "came of it, which is the 3-of-5 he gave the survey "
                      "itself", file=out)
                return 1
            try:
                new = add_reply(text, args.mark_read, args.cycle, args.reply)
            except ValueError as exc:
                print("CANNOT REPLY  %s" % exc, file=out)
                return 1
            new = mark_read(new, args.mark_read, args.cycle)
        else:
            new = post(text, today.isoformat())
        with open(args.file, "w", encoding="utf-8") as handle:
            handle.write(new)
        print("wrote %s" % args.file, file=out)
        return 0

    return report(parse(text), today, out=out)


if __name__ == "__main__":
    sys.exit(main())
