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
* `silent` --- a conversation exists and holds no message at all, so
  the heartbeat created it and nothing ever spoke.
* `absent` --- no conversation for that number. Agora's own counter
  handed the number out and there is no record of a run.

**Only `lost` raises the exit status**, and only inside the window. The
other three describe a run that is over and cannot be recovered; a check
that goes red on history is red forever, which is the call
`security_alerts` makes on an already-fixed advisory and
`argocd_health` makes on a stale Job failure. A `lost` cycle is the one
with something to go and find.

Same exit contract as its siblings: **2** means an entryless cycle in
the window did the work and left no record, **1** means something was
unreadable --- which includes an Agora that answers with no
conversations at all, since this loop demonstrably runs on it, and never
reads as clean --- and **0** means every gap in the window is explained.

**The window is the newest 48 cycle numbers**, about a day at the
20-minute cadence, and it is a reporting scope rather than a judgement:
everything outside it is still counted and still printed, it just does
not raise. Pass `--window` to widen it, `--all` to raise on every gap
ever.
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.cycle_health import missing_cycles  # noqa: E402

VAULT_TOOL = "/app/bridge/vault_tool.py"
JOURNAL_PREFIX = "projects/sokrates/projects/agora/nova/journal/"

# The public app, same address `heartbeat_health` reads. The internal API
# on :8081 needs a token this pod does not hold; :8080 answers both of
# this loop's shells (measured Cycle 607 from the bridge pod).
AGORA_PUBLIC = "http://agora.agents.svc.cluster.local:8080"

# Nova's hourly heartbeat. Its conversations are the run counter that
# `cycle_number` calls the honest one -- it counts runs, and a run that
# writes nothing still advances it, which is what makes a gap findable.
NOVA_HEARTBEAT = "ca31c116-6125-49f6-9eff-5af181fec485"

# `Nova — Cycle 607`. Anchored at the end for the same reason
# `cycle_number._NAME_RE` is: the separator is an em dash today and the
# heartbeat name is free text.
_NAME_RE = re.compile(r"Cycle\s+(\d+)\s*$")

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

DEFAULT_WINDOW = 48


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


def conversations_by_cycle(payload, heartbeat=NOVA_HEARTBEAT):
    """`{cycle number: conversation}` for one heartbeat's conversations.

    Parsed off the name rather than counted, for `cycle_number`'s reason:
    a count silently goes backwards the day a conversation is deleted.
    """
    tag = f"evolve-cycle:{heartbeat}"
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


def judge(number, conversation, messages):
    """One entryless cycle -> `{number, verdict, detail, messages}`."""
    if conversation is None:
        return {"number": number, "verdict": "absent", "messages": 0,
                "detail": "Agora holds no conversation for this number"}
    if not messages:
        return {"number": number, "verdict": "silent", "messages": 0,
                "detail": "the conversation was created and nothing ever spoke in it"}
    outcome = read_outcome(messages[-1].get("text") or "")
    if outcome is None:
        last = " ".join((messages[-1].get("text") or "").split())[:120]
        return {"number": number, "verdict": "cut off", "messages": len(messages),
                "detail": f"no closing line; the last thing said was: {last}"}
    verdict, detail = outcome
    return {"number": number, "verdict": verdict, "detail": detail,
            "messages": len(messages)}


def _fetch_messages(conversation_id, timeout=30):
    payload = _get(f"{AGORA_PUBLIC}/conversations/{conversation_id}/messages?limit=1000",
                   timeout=timeout)
    if isinstance(payload, dict):
        return payload.get("messages") or []
    return payload or []


def collect(window=DEFAULT_WINDOW):
    """`(results, newest, error)` -- one row per entryless cycle number."""
    paths = journal_paths()
    if paths is None:
        return [], None, f"could not list {JOURNAL_PREFIX} through {VAULT_TOOL}"
    gaps = sorted(missing_cycles(paths))
    try:
        payload = _get(f"{AGORA_PUBLIC}/conversations?limit=1000")
    except (urllib.error.URLError, OSError, ValueError) as error:
        return [], None, f"could not read {AGORA_PUBLIC}/conversations: {error}"
    conversations = conversations_by_cycle(payload)
    if not conversations:
        return [], None, (f"{AGORA_PUBLIC} answered with no conversations for "
                          "Nova's heartbeat -- this loop runs on them, so that is "
                          "no instrument, not an empty history")
    newest = max(conversations)
    results = []
    for number in gaps:
        conversation = conversations.get(number)
        messages = []
        if conversation is not None:
            try:
                messages = _fetch_messages(conversation["id"])
            except (urllib.error.URLError, OSError, ValueError) as error:
                results.append({"number": number, "verdict": "unreadable",
                                "messages": 0,
                                "detail": f"could not read its messages: {error}"})
                continue
        row = judge(number, conversation, messages)
        row["recent"] = number > newest - window
        results.append(row)
    return results, newest, None


_HEADINGS = (
    ("lost", "RAN AND LEFT NO RECORD — the work happened and the journal does not have it"),
    ("failed", "ENDED ON A RECORDED FAILURE — nothing to recover, the reason is Agora's own"),
    ("cut off", "STOPPED WITH NO CLOSING LINE — Agora never wrote an outcome for these"),
    ("silent", "NEVER SPOKE — a conversation with no message in it at all"),
    ("absent", "NO CONVERSATION — the number was handed out and no run is recorded"),
    ("unjudged", "NOT JUDGED — a closing line in a shape this does not read"),
    ("unreadable", "UNREADABLE — the conversation exists and its messages did not answer"),
)


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
    for verdict, heading in _HEADINGS:
        rows = [row for row in results if row["verdict"] == verdict]
        if not rows:
            continue
        lines.append(f"{heading} — {len(rows)}")
        for row in rows:
            mark = "" if row.get("recent") else "  (outside the window)"
            lines.append(f"  Cycle {row['number']}: {row['detail']}"
                         f" [{row['messages']} message(s)]{mark}")

    lines.append("")
    lines.append(f"{len(results)} cycle number(s) in the journal's range have no entry: "
                 + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())) + ".")
    lines.append(f"Newest cycle Agora has run: {newest}; the window is the newest "
                 f"{window} number(s).")
    lines.append("Only a 'lost' cycle raises the status. The others describe a run that "
                 "is over and cannot be recovered, and a check that goes red on history "
                 "is red forever.")

    unreadable = [r for r in results if r["verdict"] == "unreadable"]
    raising = [r for r in results
               if r["verdict"] == "lost" and (raise_all or r.get("recent"))]
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
    args = parser.parse_args(argv)
    results, newest, error = collect(window=args.window)
    report, status = format_report(results, newest, error,
                                   window=args.window, raise_all=args.raise_all)
    print(report)
    return status


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
