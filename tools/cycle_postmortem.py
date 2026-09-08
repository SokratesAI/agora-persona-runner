"""Why did a cycle write no journal entry?

Idea #159, approved 2026-08-27: *"A cycle killed by a pod eviction, a
cycle that hit its turn limit, and a cycle that hung waiting on an API
that never answered are three different problems with three different
fixes. Recording why a cycle ended ... would turn one unexplained number
into three countable ones."*

    python3 -m tools.cycle_postmortem

`cycle_health.missing_cycles` already names the gaps. It has never been
able to say anything about *why*, so twenty-four entryless cycle numbers
have sat in the record as one undifferentiated number since 2026-08-03.

**Nothing had to be recorded to answer this. It was already recorded.**
Agora writes a closing message into every cycle's own conversation ---
`heartbeat: Nova finished in 30m 13s --- replied 2458 chars`, or
`finished in 0s --- failed: <urlopen error [Errno 111] Connection
refused>` --- and no step of this loop has ever read one. Measured
Cycle 607 across all 24 entryless cycles: every one of them had an
answer waiting, and they are not one failure. Four never reached the
bridge at all (`Connection refused`, 0s), two had their stream truncated
mid-reply (`IncompleteRead`), two lost the connection outright, one
`timed out` at 46 minutes --- the hang the idea asks about --- two
conversations hold no message whatsoever, one has no conversation at
all, and **ten ran to completion and replied to the owner**.

So the split that matters is not killed-versus-hung. It is **did the
work happen**:

* `failed` --- Agora recorded a reason the run ended. Whatever the cycle
  had done is gone, and the reason is quoted verbatim rather than
  bucketed, because bucketing is what `agentic_health` had to unlearn
  one layer down: a streak counter merges causes, and the cause is the
  thing with the fix attached.
* `lost` --- the run finished, replied to the owner, and left no entry.
  The work happened and the record does not have it. Cycle 580 is the
  worked example: it replied 2,458 characters opening *"Cycle 579
  done"*, so it wrote its entry under another cycle's number and 580
  reads as missing forever.
* `misfiled` --- `lost`, except `find_misfiled` found the entry, one
  number up. The record has the work; the number on it is wrong, and
  historical entries are never renumbered, so there is nothing to go and
  do. This is a downgrade of `lost` applied after the search rather than
  a verdict `judge` can reach on its own -- it takes reading the entry
  and the reply of two different cycles to know.
* `unnumbered` --- `lost`, except the run's record is in the journal
  folder under a name that is not a cycle number at all. Two of the seven
  `lost` cycles were this on 2026-09-09: 265 and 276 each wrote an
  eight-cycle report (`319-report-256-263.md`, `330-report-268-274.md`)
  and no entry, and `entryless` cannot see either because it reads
  `file_cycle`, which parses `-cycle-M` and nothing else. Located by
  `find_unnumbered` on the document's own Oslo stamp against the run's
  window, so the gap between 275 and 277 -- which holds two lost cycles
  and one document -- resolves to 276 rather than to a coin toss.
* `silent` --- a conversation exists and the heartbeat never spoke in
  it. Agora's own system notices about other cycles do not count as
  speaking; see `run_messages`.
* `absent` --- no conversation for that number. Agora's own counter
  handed the number out and there is no record of a run.

Three more verdicts exist and each is a state, not an outcome: `cut off`
(the run stopped and Agora never wrote a closing line at all), `unjudged`
(a closing line in a shape this does not read) and `still running` (no
outcome yet, and it spoke a moment ago -- three cycles overlap, so the
newest few legitimately have none).

**`lost`, `cut off` and `unjudged` raise the exit status; `failed`,
`misfiled`, `unnumbered`, `silent`, `absent` and `still running` do not.** The line is whether the
gap is *explained*: the three that raise each leave a real question open,
and the five that do not are Agora giving a definite answer that the run
did not complete, or has not finished yet. A check that goes red on
history is red forever, which is the call `security_alerts` makes on an
already-fixed advisory and `argocd_health` makes on a stale Job failure.

**The tail is included, and leaving it out was the serious bug.** The
first version of this reused `cycle_health.missing_cycles` alone, which
returns *interior* gaps only -- so a loop that stopped writing an hour
ago has no gap at all, and a live outage read as "nothing to act on" and
exited 0. That is a negative result guaranteed in advance, in the one
direction that costs most. `entryless` adds every number above the newest
entry up to the newest run, excluding the newest itself, which is the
cycle asking the question.

Same exit contract as its siblings otherwise: **2** means an entryless
cycle in the window is unexplained, **1** means something was unreadable
--- which includes an Agora that answers with no conversations at all,
since this loop demonstrably runs on it, and a conversation at the
message read limit, and never reads as clean --- and **0** means every
gap in the window is explained.

**The window is the newest 48 cycle numbers**, about a day at the
20-minute cadence, and it is a reporting scope rather than a judgement:
everything outside it is still counted and still printed, it just does
not raise. Pass `--window` to widen it, `--all` to raise on every gap
ever.

**`--split-at <instant>` answers "did that change fix it?"** --- idea
#170, on the Claude Code 2.1.251 pin whose changelog names a bug that
leaves a thinking-only turn stuck, which is a cycle that writes nothing:

    python3 -m tools.cycle_postmortem --split-at 2026-08-28T23:14:42Z

It prints the entryless *rate* on each side of that instant over
equal-length windows, with the verdicts kept apart. Three things it is
deliberately not: it is not a count (the cadence has changed four times,
so more silent cycles per day can just mean more cycles per day), it is
not a rate against all of history (the loop's early weeks were a
different machine), and it is not one summed number (`failed: Connection
refused` is the bridge being down, and no CLI version changes that). It
never moves the exit status --- it is a report, and a rate that has not
moved is not a fault.

**For a CLI pin, the instant is when the pod carrying it was created,
not when the commit merged.** `kubectl get rs -n agents -o
custom-columns=NAME:.metadata.name,CREATED:.metadata.creationTimestamp,IMAGE:.spec.template.spec.containers[0].image`
gives it. The 2.1.251 merge and its replica set are five minutes apart,
which is under one cycle here, but the bridge Deployment is `Recreate`
with a 2880-second grace, so a cycle that starts inside the roll can run
on either binary and this cannot tell you which.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.config import NOVA_CYCLE_HEARTBEAT_ID  # noqa: E402
from agora_runner.conversation_rotation import cycle_tag  # noqa: E402
from agora_runner.cycle_health import MAX_CYCLE_MINUTES, missing_cycles  # noqa: E402
from agora_runner.nova_journal import entry_seq, file_cycle  # noqa: E402
from agora_runner.cycle_number import _NAME_RE  # noqa: E402
from agora_runner.heartbeat_liveness import AGORA_PUBLIC  # noqa: E402

VAULT_TOOL = "/app/bridge/vault_tool.py"
JOURNAL_PREFIX = "projects/sokrates/projects/agora/nova/journal/"

# `AGORA_PUBLIC`, `NOVA_CYCLE_HEARTBEAT_ID`, `cycle_tag` and `_NAME_RE`
# are all imported above rather than re-declared. Every one of them
# already existed in `agora_runner`, and my own step-2 rule says a second
# copy is the bug -- `heartbeat_health`, this module's closest sibling,
# imports its address for the same reason. Nova's heartbeat is the run
# counter `cycle_number` calls the honest one: it counts runs, and a run
# that writes nothing still advances it, which is what makes a gap
# findable at all.

# Agora's own closing line, e.g.
#   heartbeat: Nova finished in 30m 13s — replied 2458 chars
#   heartbeat: Nova finished in 0s — failed: <urlopen error ...>
# The duration and the outcome are both free text after the em dash, so
# only the two words that decide the verdict are matched.
_FINISHED_RE = re.compile(
    r"heartbeat:.*?finished in (?P<duration>.+?)\s+[—-]\s+(?P<outcome>.*)$",
    re.S,
)
_REPLIED_RE = re.compile(r"^replied\s+(\d+)\s+chars", re.I)
_FAILED_RE = re.compile(r"^failed:\s*(.*)$", re.I | re.S)
#: The Claude Code CLI posts its own transport failures into the
#: conversation as an ordinary assistant message, so Agora's closing line
#: counts them as a reply of N chars like any other. It is read with
#: `.match`, which is where the anchoring lives: the phrase appears
#: inside real replies that discuss it, this very cycle's included, and
#: a `^` in the pattern too would be a second copy of that one decision
#: -- removing either alone then leaves the behaviour unchanged, so
#: neither is testable.
_API_ERROR_RE = re.compile(r"\s*API Error:", re.I)

DEFAULT_WINDOW = 48

#: A cycle number above the newest journal entry with no closing line yet
#: is very likely still running -- three of us overlap. Older than one
#: cycle's own ceiling and it is not running, it stopped.
STILL_RUNNING_MINUTES = MAX_CYCLE_MINUTES


def entryless(paths, newest):
    """Every cycle number with no entry, interior gaps *and* the tail.

    `cycle_health.missing_cycles` returns only the interior -- its own
    docstring says the range above the highest entry "is `stalled_for`'s
    question, answered with a clock instead". Reusing it alone made this
    check structurally blind to the freshest failure there is: a loop
    that stopped writing an hour ago has no interior gap at all, so a
    live outage read as "nothing to act on" and exited 0. That is a
    negative result guaranteed in advance, in the one direction where it
    costs the most, and my reviewer found it.

    So the tail is included, up to the newest number Agora has run. The
    newest is always excluded: that is the cycle asking the question,
    and it has not written its entry yet.
    """
    written = {n for n in (file_cycle(path) for path in paths) if n is not None}
    if not written:
        return []
    interior = sorted(missing_cycles(paths))
    tail = [n for n in range(max(written) + 1, newest) if n not in written]
    return sorted(set(interior) | set(tail))


#: The `PR:` field of an entry's fixed footer, e.g.
#:   PR: agora-persona-runner#876 | Board: idea #187 | Outcome: merged
#: Only the field before the first `|` is read, so a `Board:` number can
#: never be mistaken for the pull request the entry is about.
_FOOTER_PR_RE = re.compile(r"^PR:\s*(?P<field>[^|\n]*)", re.M)

_HASH_NUMBER_RE = re.compile(r"#(\d+)")


def entry_pr_numbers(text):
    """The pull request number(s) an entry's footer names, as a frozenset.

    Empty when the entry says `PR: none`, which is the honest answer for a
    cycle that shipped nothing, and empty is deliberately not a match: an
    entry with no pull request in it cannot be attributed to a run by this.
    """
    numbers = set()
    for match in _FOOTER_PR_RE.finditer(text or ""):
        numbers.update(int(n) for n in _HASH_NUMBER_RE.findall(match.group("field")))
    return frozenset(numbers)


def final_reply(messages):
    """The text of the run's reply to the owner, or `None`.

    That is the last message that is neither one of Agora's own notices
    about another cycle nor Agora's closing line -- measured across cycles
    1183 to 1185, it is `messages[-2]` every time, with the closing line
    last.
    """
    for message in reversed(run_messages(messages)):
        text = (message or {}).get("text") or ""
        if text.strip() and read_outcome(text) is None:
            return text
    return None


def reply_numbers(messages):
    """Every `#<digits>` in the run's reply, as a frozenset, or `None`.

    The reply and *only* the reply. The first version of this read every
    message in the conversation and it could not work: a cycle reads the
    digest, its own board and its own diff, so the transcript names
    hundreds of pull requests including the one the cycle before it
    shipped. Measured against cycle 1183, whose reply announces exactly
    one: the transcript answered with 300-odd numbers, which makes the
    first condition in `misfiled_entries` true for free and the second one
    false for free. A set that wide is a guaranteed answer, not a
    measurement.

    `None` means there was no reply to read, which `misfiled_entries`
    treats as "cannot say" rather than "named nothing".
    """
    text = final_reply(messages)
    if text is None:
        return None
    return frozenset(int(n) for n in _HASH_NUMBER_RE.findall(text))


def misfiled_entries(lost, entry_prs, reply_prs):
    """`[(wrote_it, filed_as), ...]` -- entries filed under the wrong cycle.

    A cycle that asks `cycle_number` without its conversation id is
    answered with the highest number that *exists*, and three cycles
    overlap -- so a run that lasts longer than the heartbeat interval is
    handed the number of the cycle that woke after it. Its entry then
    lands one number up, and the next cycle does the same thing, so the
    shift is not one entry: measured live 2026-09-08, cycles 1183 to 1204
    each filed under the next number -- twenty-two entries deep, and every
    one of them reads as a healthy cycle from `judge` because a file with
    that name exists. Three older runs are in the record too (367, 871-874,
    969-973, 986-992), 39 entries in all. The chain ends where a cycle
    replied nothing at all, which is why 1205 stopped it.

    `judge` cannot see any of this. It only ever looks at cycle numbers
    with *no* entry, so the cycle whose work went missing reads as `lost`
    (which is true) and the cycle holding somebody else's entry reads as
    fine (which is not).

    Two conditions, both required, because attributing one cycle's work to
    another on a guess is worse than not noticing:

    * every pull request in the entry's footer was named by the *earlier*
      run, and
    * none of them was named by the run the entry is filed under.

    The second is what keeps a coincidence out. Two cycles working the
    same pull request both name it, so the pair is ambiguous and this
    says nothing rather than picking one.
    """
    found = []
    for start in sorted(lost):
        earlier, said = start, reply_prs.get(start)
        while said:
            filed_as = earlier + 1
            footer = entry_prs.get(filed_as)
            if not footer or not footer <= said:
                break
            own = reply_prs.get(filed_as)
            if own is None or footer & own:
                break
            found.append((earlier, filed_as))
            earlier, said = filed_as, own
    return found


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def journal_paths():
    """Entry filenames from the vault, or `None` if the listing failed.

    Shelling out to the bridge's own client rather than
    `agora_runner.vault`, which holds working CouchDB credentials under
    `CDB_*` while that module reads `COUCHDB_*` -- from this pod it
    answers 401 and returns an empty listing, and an empty journal has no
    gaps in it. That is a check certifying a healthy loop from a blind
    instrument, so the listing failing has to be exit 1 and not exit 0.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "ls", JOURNAL_PREFIX],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    paths = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return paths or None


def conversations_by_cycle(payload, heartbeat=NOVA_CYCLE_HEARTBEAT_ID):
    """`{cycle number: conversation}` for one heartbeat's conversations.

    Parsed off the name rather than counted, for `cycle_number`'s reason:
    a count silently goes backwards the day a conversation is deleted.
    """
    tag = cycle_tag(heartbeat)
    found = {}
    for conversation in payload.get("conversations") or []:
        if tag not in (conversation.get("tags") or []):
            continue
        match = _NAME_RE.search(conversation.get("name") or "")
        if match:
            found[int(match.group(1))] = conversation
    return found


def read_outcome(text):
    """Agora's closing line -> `(verdict, detail)`, or `None`.

    `None` means this is not a closing line at all, which is its own
    finding: the run stopped without Agora ever writing one.
    """
    match = _FINISHED_RE.search(text or "")
    if not match:
        return None
    duration = match.group("duration").strip()
    outcome = match.group("outcome").strip()
    replied = _REPLIED_RE.match(outcome)
    if replied:
        return "lost", f"ran {duration} and replied {replied.group(1)} chars"
    failed = _FAILED_RE.match(outcome)
    if failed:
        reason = " ".join(failed.group(1).split())
        return "failed", f"after {duration}: {reason}"
    return "unjudged", f"after {duration}: {outcome}"


def run_messages(messages):
    """`messages` with Agora's own system notices dropped.

    Agora posts a `system` message into a cycle's conversation to tell
    the owner that some *earlier* cycle never replied. That notice is about
    another run, it lands after the closing line, and `judge` reads the
    last message -- so on 2026-09-06 the notifier corrupted the very
    instrument that counts silent cycles. Cycle 1035 failed in 0s with
    `Connection refused`, Agora recorded that closing line, and two
    notices landed on top of it, so it was filed as `cut off` (which
    raises) rather than `failed` (which does not). Cycle 1041 never ran
    at all and its conversation holds nothing *but* three notices, so it
    read as a run that stopped mid-way instead of one that never spoke.

    The filter is on `system` rather than on the sender name, because
    `Nova` is the sender of the heartbeat lines this check exists to
    read and `Agora` is the sender of a legitimate reply in other
    conversations -- only the flag separates a notice from the record.
    """
    return [m for m in (messages or []) if not (m or {}).get("system")]


def _api_error_detail(closing, reply):
    """`(closing line, reply text)` -> the detail for an `api error` run."""
    match = _FINISHED_RE.search(closing or "")
    duration = match.group("duration").strip() if match else "an unknown time"
    return (f"ran {duration} and its last message is the model call's own error, "
            f"which Agora counted as a reply: {' '.join(reply.split())[:120]}")


def judge(number, conversation, messages, now=None):
    """One entryless cycle -> `{number, verdict, detail, messages}`.

    `now` only matters for a run with no closing line: three cycles
    overlap, so the newest few numbers legitimately have no outcome yet,
    and calling those `cut off` would report the loop working as a
    failure on every run. Including the tail is what makes that case
    reachable at all -- interior gaps are always finished.

    `messages` is filtered through `run_messages` first: Agora's own
    notices are not this run's record and judging on them is how a
    `failed` run reads as `cut off`.
    """
    fetched = len(messages or [])
    messages = run_messages(messages)
    if conversation is None:
        return {"number": number, "verdict": "absent", "messages": 0,
                "detail": "Agora holds no conversation for this number"}
    # The truncation test is on what Agora *returned*, not on what
    # survives the filter: the read is cut off at MESSAGE_LIMIT rows
    # whatever they contain, and counting the filtered ones would let a
    # long conversation slip under the ceiling and be judged off a
    # closing line that is not there.
    if fetched >= MESSAGE_LIMIT:
        return {"number": number, "verdict": "unreadable", "messages": fetched,
                "detail": f"the conversation is at or past the {MESSAGE_LIMIT}-message "
                          "read limit, so its closing line may be off the end"}
    if not messages:
        detail = "the conversation was created and nothing ever spoke in it"
        if fetched:
            detail = (f"the heartbeat never spoke in it; the {fetched} message(s) "
                      "it holds are all Agora's own notices about other cycles")
        return {"number": number, "verdict": "silent", "messages": 0,
                "detail": detail}
    outcome = read_outcome(messages[-1].get("text") or "")
    if outcome is None:
        last = " ".join((messages[-1].get("text") or "").split())[:120]
        if now is not None and _spoke_within(conversation, now, STILL_RUNNING_MINUTES):
            return {"number": number, "verdict": "still running",
                    "messages": len(messages),
                    "detail": "no closing line yet, and it spoke within the last "
                              f"{STILL_RUNNING_MINUTES} minutes"}
        return {"number": number, "verdict": "cut off", "messages": len(messages),
                "detail": f"no closing line; the last thing said was: {last}"}
    verdict, detail = outcome
    if verdict == "lost":
        reply = final_reply(messages)
        if reply is not None and _API_ERROR_RE.match(reply):
            return {"number": number, "verdict": "api error",
                    "messages": len(messages),
                    "detail": _api_error_detail(messages[-1].get("text") or "", reply)}
        # The reply travels with the row rather than being re-fetched by
        # whoever reads the report. `collect` has already paid for these
        # messages; a reader who has to go back to Agora for them is the
        # state that had four cycles calling this bucket undiagnosable.
        return {"number": number, "verdict": verdict, "detail": detail,
                "messages": len(messages), "reply": reply}
    return {"number": number, "verdict": verdict, "detail": detail,
            "messages": len(messages)}


#: How many messages to ask Agora for. It answers with the *oldest* N, so
#: a conversation longer than this loses its closing line -- and the
#: closing line is the entire measurement. The busiest entryless cycle
#: measured 586 messages (cycle 134, 24 minutes), so this is comfortably
#: above anything seen; `judge` refuses rather than guessing if a
#: conversation ever reaches it, because a truncated read and a run that
#: stopped without a closing line are indistinguishable from the last
#: message alone.
MESSAGE_LIMIT = 1000


def _spoke_within(conversation, now, minutes):
    """Did this conversation say anything in the last `minutes`?

    `lastMessageAt` is Agora's own field, UTC with a `Z`. A stamp this
    cannot parse reads as *not* recent, which sends the row to `cut off`
    -- the louder of the two answers, which is the right direction to
    fail in for a clock it could not read.
    """
    stamp = (conversation or {}).get("lastMessageAt")
    if not stamp:
        return False
    try:
        at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False
    return (now - at) <= timedelta(minutes=minutes)


def _fetch_messages(conversation_id, timeout=30):
    payload = _get(
        f"{AGORA_PUBLIC}/conversations/{conversation_id}/messages?limit={MESSAGE_LIMIT}",
        timeout=timeout)
    if isinstance(payload, dict):
        return payload.get("messages") or []
    return payload or []


def _created(conversation):
    """A conversation's `createdAt` as a datetime, or `None`.

    This is when the heartbeat opened the conversation, which is the
    closest thing Agora records to when the cycle started.
    """
    stamp = (conversation or {}).get("createdAt")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def _spoke_last(conversation):
    """A conversation's `lastMessageAt` as a datetime, or `None`.

    The closing line Agora writes is a message, so this is the end of the
    run as Agora saw it. Paired with `_created` it is the run's window
    without parsing a duration string out of prose -- `find_unnumbered`
    joins a document's own clock against it.
    """
    stamp = (conversation or {}).get("lastMessageAt")
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


def rate_split(results, conversations, split_at, newest):
    """Entryless rate either side of an instant, over matched-length windows.

    Idea #170 asks whether a Claude Code pin fixed the cycles that run and
    write nothing: *"count silent cycles for a week against the count
    before the bump and see whether the rate moves"*. The count alone
    cannot answer it -- the cadence has changed four times, so more silent
    cycles per day can mean more cycles per day -- and neither can a rate
    against all of history, because the loop's early weeks were a
    different machine. So both sides are rates, and the window before is
    the *same duration* as the window after.

    The verdicts are kept apart rather than summed, for this module's own
    reason: `failed: Connection refused` is the bridge being down and has
    nothing to do with which binary it runs. A single before/after number
    merges that into the answer.

    `newest` is excluded from both sides -- that is the cycle asking the
    question, and it has not written its entry yet.
    """
    entryless_verdicts = {row["number"]: row["verdict"] for row in results}
    started = {number: _created(conversation)
               for number, conversation in conversations.items()
               if number != newest}
    started = {n: t for n, t in started.items() if t is not None}
    after = sorted(n for n, t in started.items() if t >= split_at)
    if not after:
        return None
    span = max(started[n] for n in after) - split_at
    before = sorted(n for n, t in started.items() if split_at - span <= t < split_at)

    def side(numbers):
        gaps = [n for n in numbers if n in entryless_verdicts]
        return {
            "cycles": len(numbers),
            "gaps": len(gaps),
            "verdicts": Counter(entryless_verdicts[n] for n in gaps),
            "numbers": gaps,
            "lo": min(numbers) if numbers else None,
            "hi": max(numbers) if numbers else None,
        }

    return {"split_at": split_at, "hours": span.total_seconds() / 3600.0,
            "after": side(after), "before": side(before),
            "undated": len(conversations) - len(started) - 1}


def format_rate_split(split):
    """The `--split-at` block, as lines. Never changes the exit status."""
    if split is None:
        return ["ENTRYLESS RATE — no cycle has run at or after that instant, "
                "so there is nothing on the after side to compare."]
    hours = split["hours"]
    lines = [f"ENTRYLESS RATE, SPLIT AT {split['split_at'].isoformat()} "
             f"— {hours:.1f}h each side"]
    for name, label in (("after", "after "), ("before", "before")):
        side = split[name]
        share = (100.0 * side["gaps"] / side["cycles"]) if side["cycles"] else 0.0
        detail = ", ".join(f"{count} {verdict}"
                           for verdict, count in sorted(side["verdicts"].items()))
        lines.append(f"  {label}: {side['gaps']} of {side['cycles']} cycle(s) "
                     f"({share:.2f}%) — cycles {side['lo']}..{side['hi']}"
                     + (f" — {detail}" if detail else ""))
        if side["numbers"]:
            lines.append(f"          {', '.join(str(n) for n in side['numbers'])}")
    lines.append("  The two sides are equal-length windows and the verdicts are kept "
                 "apart on purpose: a `failed` is Agora reporting the bridge "
                 "unreachable, which no CLI version changes. This is a report, not a "
                 "judgement — it does not move the exit status.")
    if split["undated"]:
        lines.append(f"  NOT COUNTED — {split['undated']} conversation(s) carry no "
                     "readable createdAt, so they are in neither side.")
    return lines


def collect(window=DEFAULT_WINDOW):
    """`(results, newest, error, conversations, paths)` -- a row per entryless cycle.

    `paths` comes back so `find_misfiled` can read an entry's footer
    without a second listing of the journal folder.
    """
    paths = journal_paths()
    if paths is None:
        return [], None, f"could not list {JOURNAL_PREFIX} through {VAULT_TOOL}", {}, []
    try:
        payload = _get(f"{AGORA_PUBLIC}/conversations?limit=1000")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return [], None, f"could not read {AGORA_PUBLIC}/conversations: {error}", {}, []
    try:
        conversations = conversations_by_cycle(payload)
    except (AttributeError, TypeError, ValueError) as error:
        return [], None, (f"{AGORA_PUBLIC}/conversations answered in a shape "
                          f"this cannot read: {error}"), {}, []
    if not conversations:
        return [], None, (f"{AGORA_PUBLIC} answered with no conversations for "
                          "Nova's heartbeat -- this loop runs on them, so that is "
                          "no instrument, not an empty history"), {}, []
    newest = max(conversations)
    gaps = entryless(paths, newest)
    now = datetime.now(timezone.utc)

    def one(number):
        conversation = conversations.get(number)
        if conversation is not None:
            try:
                messages = _fetch_messages(conversation["id"])
            except (urllib.error.URLError, OSError, ValueError, KeyError,
                    TypeError) as error:
                return {"number": number, "verdict": "unreadable", "messages": 0,
                        "detail": f"could not read its messages: {error}"}
        else:
            messages = []
        return judge(number, conversation, messages, now=now)

    # Concurrent because the gap list only ever grows -- it is history, so
    # every past gap is re-read on every run, forever, and one blocking
    # fetch each would eventually walk this check into `preflight`'s
    # 240-second hang ceiling for a reason unrelated to the loop's health.
    # Six, matching `preflight`'s own pool.
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, gaps))
    for row in results:
        row["recent"] = row["number"] > newest - window
    return results, newest, None, conversations, paths


class _LazyMap:
    """A `.get()`-shaped mapping that computes and caches on first ask.

    `misfiled_entries` walks a chain forward and stops as soon as one link
    does not match, so it must not be handed a prefetched neighbourhood --
    the chain is one entry long almost always, and prefetching a fixed
    window would spend a vault read and a message read per cycle for the
    case that never happens. Tests pass plain dicts; the pure function
    cannot tell the difference.
    """

    def __init__(self, compute):
        self._compute = compute
        self._cache = {}

    def get(self, key, default=None):
        if key not in self._cache:
            self._cache[key] = self._compute(key)
        return self._cache[key]


def _read_entry(path):
    """One journal entry's text, or `None` if the vault would not answer."""
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def find_misfiled(results, conversations, paths, read_entry=None, fetch=None):
    """`[(wrote_it, filed_as), ...]` for the `lost` cycles in `results`.

    Nothing is read at all unless a cycle came back `lost`, which is the
    only state that can mean "its entry is somewhere else".
    """
    lost = [row["number"] for row in results if row["verdict"] == "lost"]
    if not lost:
        return []
    read_entry = _read_entry if read_entry is None else read_entry
    fetch = _fetch_messages if fetch is None else fetch
    by_cycle = {}
    for path in paths or []:
        number = file_cycle(path)
        if number is not None:
            by_cycle[number] = path

    def entry(number):
        path = by_cycle.get(number)
        if path is None:
            return frozenset()
        text = read_entry(path)
        return entry_pr_numbers(text) if text is not None else frozenset()

    def reply(number):
        conversation = conversations.get(number)
        if conversation is None:
            return None
        try:
            return reply_numbers(fetch(conversation["id"]))
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            return None

    return misfiled_entries(lost, _LazyMap(entry), _LazyMap(reply))


#: The Oslo-stamped heading every document in `nova/journal/` opens with,
#: e.g. `### 2026-08-17 14:07 (Oslo) — Report · Cycles 256–263`. Minute
#: resolution, local time, and it is the only statement a document makes
#: about when it was written -- the vault carries no per-document mtime
#: this check can read.
_DOC_STAMP_RE = re.compile(
    r"^###\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})\s*\(Oslo\)", re.M)

#: Oslo is UTC+2 in summer and UTC+1 in winter, and every document this
#: joins against was written in the summer half. `zoneinfo` is the honest
#: converter and it is in the standard library, so there is no table here.
_OSLO = "Europe/Oslo"


def document_written_at(text):
    """When a journal document says it was written, as UTC, or `None`.

    `None` for a document with no `### <date> <time> (Oslo)` heading, and
    that is not a fallback to guess from: a document that does not stamp
    itself cannot be joined to a run, and saying so is the finding.
    """
    match = _DOC_STAMP_RE.search(text or "")
    if not match:
        return None
    date, hour, minute = match.group(1), int(match.group(2)), int(match.group(3))
    try:
        from zoneinfo import ZoneInfo
        local = datetime.fromisoformat(date).replace(
            hour=hour, minute=minute, tzinfo=ZoneInfo(_OSLO))
    except (ValueError, KeyError, ImportError):
        return None
    return local.astimezone(timezone.utc)


def unnumbered_candidates(paths, lost):
    """`{cycle number: [path, ...]}` -- documents that could be a lost cycle's.

    A candidate is a journal document whose filename does **not** parse as
    `NNN-cycle-M.md` and whose sequence number falls in the gap a lost
    cycle sits in -- between the last numbered entry below it and the
    first above it. The sequence prefix exists to make a lexical sort
    chronological (`nova_journal.entry_filename`), so the gap is a real
    time window and narrowing by it first is what keeps this to a handful
    of vault reads instead of one per document in the folder.

    Position is only the shortlist. It cannot decide *which* lost cycle
    wrote a document, because one gap can hold two lost cycles and one
    document -- 275 and 276 do, around `330-report-268-274.md` -- so the
    same document is offered to both and `find_unnumbered` settles it on
    the clock.
    """
    numbered = {}
    others = []
    for path in paths or []:
        number = file_cycle(path)
        if number is None:
            others.append(path)
        else:
            numbered.setdefault(number, entry_seq(path))
    if not numbered:
        return {}
    found = {}
    for cycle in sorted(lost or ()):
        below = [seq for n, seq in numbered.items() if n < cycle]
        above = [seq for n, seq in numbered.items() if n > cycle]
        low = max(below) if below else -1
        high = min(above) if above else None
        window = [path for path in others
                  if entry_seq(path) > low
                  and (high is None or entry_seq(path) < high)]
        if window:
            found[cycle] = sorted(window, key=entry_seq)
    return found


def find_unnumbered(results, conversations, paths, read_entry=None):
    """`[(number, path), ...]` for `lost` cycles whose document is in the folder.

    The join is the document's own Oslo stamp against the conversation's
    `createdAt`..`lastMessageAt` -- the run's real window, straight out of
    Agora, with no duration parsing in between. **A document matching more
    than one lost cycle is dropped rather than assigned**, because naming
    one of two would be a guess wearing a measurement's clothes, and the
    positional shortlist above deliberately offers the ambiguous case to
    both.

    Measured 2026-09-09 against the live folder: of the seven `lost`
    cycles, 265 wrote `319-report-256-263.md` (stamped 14:07 Oslo inside a
    run that opened at 12:00:01Z) and 276 wrote `330-report-268-274.md`
    (06:53 Oslo, inside 04:39:01Z..05:04:38Z). That second one is the case
    position alone gets wrong: the same gap holds cycle 275, whose window
    closed at 03:44:54Z, an hour before the document was written.
    """
    lost = [row["number"] for row in results if row["verdict"] == "lost"]
    if not lost:
        return []
    read_entry = _read_entry if read_entry is None else read_entry
    candidates = unnumbered_candidates(paths, lost)
    if not candidates:
        return []
    stamps = {}
    for cycle_paths in candidates.values():
        for path in cycle_paths:
            if path in stamps:
                continue
            text = read_entry(path)
            stamps[path] = document_written_at(text) if text is not None else None
    claims = {}
    for cycle, cycle_paths in candidates.items():
        conversation = conversations.get(cycle) if conversations else None
        opened = _created(conversation)
        closed = _spoke_last(conversation)
        if opened is None or closed is None:
            continue
        for path in cycle_paths:
            written = stamps.get(path)
            if written is None:
                continue
            if opened <= written <= closed:
                claims.setdefault(path, []).append(cycle)
    return sorted((cycles[0], path) for path, cycles in claims.items()
                  if len(cycles) == 1)


def format_unnumbered(pairs):
    """The unnumbered block, or `[]` when there is nothing to say."""
    if not pairs:
        return []
    lines = ["",
             "WROTE A DOCUMENT UNDER ANOTHER NAME — the record is in the journal "
             f"folder, not under a cycle number — {len(pairs)}"]
    for number, path in pairs:
        name = path.rsplit("/", 1)[-1]
        lines.append(f"  Cycle {number} wrote `{name}`: its own Oslo stamp falls "
                     f"inside {number}'s run window, and no other entryless cycle "
                     "can claim it.")
    lines.append("  Historical documents are never renamed — this says where the "
                 "record is, it does not ask for a repair.")
    return lines


def apply_unnumbered(results, pairs):
    """Downgrade every `lost` row whose document `find_unnumbered` located.

    Same call `apply_misfiled` makes one function up, for the same reason:
    `lost` raises because the gap is unexplained, and a run whose document
    this report has just named is explained. It is a different fix from
    `misfiled` and gets its own verdict -- that entry is under the wrong
    number, this one is under no number at all, and merging them would
    print a repair instruction that does not fit either.
    """
    located = dict((number, path) for number, path in pairs or ())
    for row in results:
        if row["verdict"] != "lost":
            continue
        path = located.get(row["number"])
        if path is None:
            continue
        row["verdict"] = "unnumbered"
        name = path.rsplit("/", 1)[-1]
        row["detail"] = f"{row['detail']}; it wrote `{name}` instead of an entry"
    return results


def doubled_entries(lost, entry_prs, reply_prs, paths_by_cycle):
    """`[(wrote_it, path), ...]` -- a lost cycle's entry filed under a neighbour.

    A third way a gap gets explained, and the one the two above are blind
    to by construction. `misfiled_entries` reads a chain of entries each
    filed one number *up*; `find_unnumbered` reads documents whose
    filename carries no cycle number at all. Neither can see the case
    where two cycles ran at once, both asked `cycle_number` without a
    conversation id, and both were handed the same number -- so the
    journal holds **two** `NNN-cycle-M.md` documents and the number beside
    M holds none.

    Measured live 2026-09-09, and it is why this exists: the folder holds
    `516-cycle-454.md` and `517-cycle-454.md`, cycle 455 has no entry, and
    455's own reply to Edvard announces `#396` -- which is the pull
    request in `517`'s footer, and is named nowhere in 454's reply, which
    announces `#531` and `#535`.

    The two conditions are `misfiled_entries`', for its reason: every pull
    request in the document's footer was named by the lost run's reply,
    and none of them by the run whose number the document carries. The
    second is what keeps a coincidence out, and here it does a second job
    -- one of the two documents really is the neighbour's, and this is
    what stops that one being taken away from it.

    A document more than one lost cycle can claim is dropped, and so is a
    lost cycle that can claim both of a neighbour's documents: the first
    is a guess between two answers, and the second is a contradiction,
    since the neighbour would then have written none of its own.
    """
    claims = {}
    for cycle in sorted(lost or ()):
        said = reply_prs.get(cycle)
        if not said:
            continue
        for neighbour in (cycle - 1, cycle + 1):
            paths = paths_by_cycle.get(neighbour) or []
            if len(paths) < 2:
                continue
            own = reply_prs.get(neighbour)
            if own is None:
                continue
            for path in paths:
                footer = entry_prs.get(path) or frozenset()
                if not footer or not footer <= said or footer & own:
                    continue
                claims.setdefault(path, []).append(cycle)
    taken = [(cycles[0], path) for path, cycles in claims.items()
             if len(cycles) == 1]
    greedy = {cycle for cycle, _ in taken
              if sum(1 for other, _ in taken if other == cycle) > 1}
    return sorted((cycle, path) for cycle, path in taken if cycle not in greedy)


def find_doubled(results, conversations, paths, read_entry=None, fetch=None):
    """`doubled_entries` wired to the vault and to Agora.

    Nothing is read unless a `lost` cycle actually sits beside a number
    that carries more than one document, which is three cycles in the
    whole history -- so the shortlist is built from filenames first and
    the vault reads follow it.
    """
    lost = [row["number"] for row in results if row["verdict"] == "lost"]
    if not lost:
        return []
    read_entry = _read_entry if read_entry is None else read_entry
    fetch = _fetch_messages if fetch is None else fetch
    by_cycle = {}
    for path in paths or []:
        number = file_cycle(path)
        if number is not None:
            by_cycle.setdefault(number, []).append(path)
    shortlist = {}
    for cycle in lost:
        for neighbour in (cycle - 1, cycle + 1):
            if len(by_cycle.get(neighbour) or []) > 1:
                shortlist[neighbour] = sorted(by_cycle[neighbour], key=entry_seq)
    if not shortlist:
        return []
    entry_prs = {}
    for cycle_paths in shortlist.values():
        for path in cycle_paths:
            text = read_entry(path)
            entry_prs[path] = entry_pr_numbers(text) if text is not None else frozenset()

    def reply(number):
        conversation = (conversations or {}).get(number)
        if conversation is None:
            return None
        try:
            return reply_numbers(fetch(conversation["id"]))
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            return None

    return doubled_entries(lost, entry_prs, _LazyMap(reply), shortlist)


def format_doubled(pairs):
    """The doubled block, or `[]` when there is nothing to say."""
    if not pairs:
        return []
    lines = ["",
             "FILED UNDER A NUMBER SOMEBODY ELSE ALSO USED — two entries carry one "
             f"cycle number and this one is the lost run's — {len(pairs)}"]
    for number, path in pairs:
        name = path.rsplit("/", 1)[-1]
        lines.append(f"  Cycle {number}'s work is in `{name}`: its footer names a "
                     f"pull request {number}'s own reply announced, and that the "
                     "cycle in the filename never mentioned.")
    lines.append("  Historical entries are never renamed — this says where the "
                 "record is, it does not ask for a repair. The cause is two "
                 "overlapping cycles asking `cycle_number` without a conversation id "
                 "and both being handed the same number.")
    return lines


def apply_doubled(results, pairs):
    """Downgrade every `lost` row whose entry `find_doubled` located.

    The same call `apply_misfiled` and `apply_unnumbered` make: `lost`
    raises because the gap is unexplained, and a run whose entry this
    report has just named is explained. Its own verdict rather than a
    reuse of `misfiled`, because the repair instruction differs -- that
    entry is one number up and alone, this one shares a number with a
    document that genuinely belongs to the cycle named in the filename.
    """
    located = dict((number, path) for number, path in pairs or ())
    for row in results:
        if row["verdict"] != "lost":
            continue
        path = located.get(row["number"])
        if path is None:
            continue
        row["verdict"] = "doubled"
        name = path.rsplit("/", 1)[-1]
        row["detail"] = f"{row['detail']}; its entry is `{name}`"
    return results


def format_misfiled(pairs):
    """The misfiled block, or `[]` when there is nothing to say."""
    if not pairs:
        return []
    lines = ["",
             "FILED UNDER THE WRONG NUMBER — the entry exists and names the wrong "
             f"cycle — {len(pairs)}"]
    for wrote_it, filed_as in pairs:
        lines.append(f"  Cycle {wrote_it}'s work is in the entry filed as cycle "
                     f"{filed_as}: that entry's PR was announced by {wrote_it}'s own "
                     f"reply and not by {filed_as}'s.")
    lines.append("  Historical entries are never renumbered — this says where the "
                 "record is, it does not ask for a repair. The cause is a cycle "
                 "asking `cycle_number` without its conversation id.")
    return lines


def apply_misfiled(results, pairs):
    """Downgrade every `lost` row whose entry `find_misfiled` located.

    `lost` says "the work happened and the journal does not have it", and it
    raises the exit status because that gap is unexplained. For the head of a
    misfiled chain both halves of that are false: the journal does have the
    entry, one number up, and the same report names where. Measured
    2026-09-08: five of the sixteen `lost` cycles -- 366, 870, 968, 985 and
    1183 -- were located by the block printed twelve lines below them, and
    1183 is inside the window, so the run exited 2 on a gap it had explained
    itself.

    Only the head of a chain is touched, because only the head is `lost`;
    every later link has an entry filed under its own number (somebody else's
    work) and never reaches `results` at all. Mutates and returns `results`
    so the caller's list is the one the report is built from.
    """
    located = dict(pairs or ())
    for row in results:
        if row["verdict"] != "lost":
            continue
        filed_as = located.get(row["number"])
        if filed_as is None:
            continue
        row["verdict"] = "misfiled"
        row["detail"] = f"{row['detail']}; the entry is filed as cycle {filed_as}"
    return results


#: The verdicts that mean "this gap is not explained, or the work it did
#: is not in the record". `failed`, `silent` and `absent` are all Agora
#: giving a definite answer that the run did not complete, so there is
#: nothing to go and find. `misfiled` is the one non-raising verdict where
#: the run DID complete and its work IS in the record -- under the next
#: cycle's number, which is where `find_misfiled` says it is, and which
#: historical entries are never renumbered to correct. `still running` is
#: not an outcome at all. The three below each leave a real question
#: open. The docstring's contract is that 0 means every gap in the window
#: is explained, and `unjudged` is by its own name the opposite of that --
#: my reviewer found it exiting 0, which would make the day Agora grows a
#: third outcome word a silent one.
RAISING_VERDICTS = ("lost", "api error", "cut off", "unjudged")

_HEADINGS = (
    ("lost", "RAN AND LEFT NO RECORD — the work happened and the journal does not have it",
     "Each row prints the run's own reply to Edvard, recovered from its Agora "
     "conversation -- so what the cycle did is readable here, without a CLI "
     "transcript and without a second call to Agora."),
    ("api error", "DIED ON A MODEL-CALL ERROR — the run's last message is the error itself, "
                  "not a reply",
     "Agora's closing line counts it as a reply of N chars, which is why these used "
     "to sit above under RAN AND LEFT NO RECORD. There is no reply to recover; the "
     "cause is upstream of this loop."),
    ("misfiled", "FILED ONE NUMBER UP — the work is in the record, under the next "
                 "cycle's number"),
    ("unnumbered", "WROTE SOMETHING ELSE — the run's record is in the journal folder "
                   "under a name that is not a cycle number"),
    ("doubled", "SHARES ANOTHER CYCLE'S NUMBER — two entries carry one number and "
                "one of them is this run's"),
    ("failed", "ENDED ON A RECORDED FAILURE — nothing to recover, the reason is Agora's own"),
    ("cut off", "STOPPED WITH NO CLOSING LINE — Agora never wrote an outcome for these"),
    ("silent", "NEVER SPOKE — a conversation with no message in it at all"),
    ("absent", "NO CONVERSATION — the number was handed out and no run is recorded"),
    ("unjudged", "NOT JUDGED — a closing line in a shape this does not read"),
    ("unreadable", "UNREADABLE — the conversation exists and its messages did not answer"),
    ("still running", "STILL RUNNING — no outcome yet, and it spoke a moment ago"),
)


#: Where the recovered reply starts, and where it stops. A `lost` row's
#: reply is the cycle's own account of work the journal does not have, so
#: it is printed whole -- prose that a fence makes readable rather than a
#: length that would decide for the reader which part mattered.
_REPLY_OPEN = "      --- its own account of the work, recovered from Agora ---"
_REPLY_CLOSE = "      --- end of recovered reply ---"


def _recovered_reply_lines(reply):
    """The block printed under a `lost` row, as lines.

    A `lost` row whose reply cannot be read says so. The heading above it
    promises the reply is still in the conversation, and a row that
    quietly printed nothing would make that promise false without any
    reader being able to tell -- which is the shape of failure this whole
    tool exists to remove.
    """
    if not (reply or "").strip():
        return ["      no reply in the conversation to recover — the closing line "
                "counted characters that are not in any message"]
    return ([_REPLY_OPEN]
            + [f"      {line}" for line in reply.strip().splitlines()]
            + [_REPLY_CLOSE])


def format_report(results, newest, error, window=DEFAULT_WINDOW, raise_all=False):
    """`(text, status)` --- the report and its exit code."""
    if error:
        return (f"COULD NOT READ — {error}\n"
                "This is no instrument, not a clean sweep."), 1
    lines = []
    if not results:
        lines.append("Nothing to act on. Every cycle number in the journal's range "
                     "has an entry.")
        lines.append(f"Newest cycle Agora has run: {newest}.")
        return "\n".join(lines), 0

    counts = Counter(row["verdict"] for row in results)
    for entry in _HEADINGS:
        verdict, heading = entry[0], entry[1]
        note = entry[2] if len(entry) > 2 else None
        rows = [row for row in results if row["verdict"] == verdict]
        if not rows:
            continue
        lines.append(f"{heading} — {len(rows)}")
        if note:
            lines.append(f"    {note}")
        for row in rows:
            mark = "" if row.get("recent") else "  (outside the window)"
            lines.append(f"  Cycle {row['number']}: {row['detail']}"
                         f" [{row['messages']} message(s)]{mark}")
            if verdict == "lost":
                lines.extend(_recovered_reply_lines(row.get("reply")))

    lines.append("")
    lines.append(f"{len(results)} cycle number(s) in the journal's range have no entry: "
                 + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ".")
    lines.append(f"Newest cycle Agora has run: {newest}; the window is the newest "
                 f"{window} number(s).")
    lines.append("Raising verdicts: " + ", ".join(RAISING_VERDICTS)
                 + " -- the gap is unexplained or the work is not in the record. "
                 "The rest are Agora saying definitely that the run did not complete, "
                 "or has not finished yet, and a check that goes red on history is red "
                 "forever.")

    unreadable = [r for r in results if r["verdict"] == "unreadable"]
    raising = [r for r in results
               if r["verdict"] in RAISING_VERDICTS and (raise_all or r.get("recent"))]
    if raising:
        return "\n".join(lines), 2
    if unreadable:
        return "\n".join(lines), 1
    return "\n".join(lines), 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                        help="how many of the newest cycle numbers raise the status")
    parser.add_argument("--all", action="store_true", dest="raise_all",
                        help="raise on every entryless cycle, however old")
    parser.add_argument("--split-at", metavar="ISO8601",
                        help="also report the entryless rate either side of this "
                             "instant, over equal-length windows (idea #170)")
    args = parser.parse_args(argv)
    split_at = None
    if args.split_at:
        try:
            split_at = datetime.fromisoformat(args.split_at.replace("Z", "+00:00"))
        except ValueError:
            print(f"COULD NOT READ — --split-at {args.split_at!r} is not an ISO 8601 "
                  "instant, e.g. 2026-08-28T23:14:42Z")
            return 1
        if split_at.tzinfo is None:
            split_at = split_at.replace(tzinfo=timezone.utc)
    results, newest, error, conversations, paths = collect(window=args.window)
    # Located before the report is built, not after: `format_report` decides
    # the exit status from the verdicts, so a `lost` row this can explain has
    # to stop being `lost` first. Printing the explanation under a red status
    # is what the run on 2026-09-08 did.
    pairs = [] if error else find_misfiled(results, conversations, paths)
    apply_misfiled(results, pairs)
    # After `misfiled`, because an entry filed one number up is a stronger
    # answer than a document with no number at all: both explain the same
    # gap, and only one of them names an entry with this cycle's work in it.
    unnumbered = [] if error else find_unnumbered(results, conversations, paths)
    apply_unnumbered(results, unnumbered)
    # Last of the three, because it is the only one that reads a document
    # already filed under a real cycle number -- so a gap either of the
    # two above can explain is explained by them first, and this never
    # competes with them for the same row.
    doubled = [] if error else find_doubled(results, conversations, paths)
    apply_doubled(results, doubled)
    report, status = format_report(results, newest, error,
                                   window=args.window, raise_all=args.raise_all)
    if not error:
        report = "\n".join([report] + format_misfiled(pairs)
                           + format_unnumbered(unnumbered)
                           + format_doubled(doubled))
    print(report)
    if split_at is not None and not error:
        print()
        print("\n".join(format_rate_split(
            rate_split(results, conversations, split_at, newest))))
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
