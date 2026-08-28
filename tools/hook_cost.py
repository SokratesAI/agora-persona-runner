"""What do my own hooks cost me in context?

The owner's idea #145, and it is a measurement first and a decision second:

    "a clock line and a quota line arrive after every single tool call I
    make, and a cycle makes a lot of tool calls. I have never measured the
    total, which means I have been carrying an unknown, monotonically
    growing cost in the one resource a cycle actually runs out of, and
    treating it as free because each individual line is short."

The reason a short line is not obviously cheap is that context is re-sent
on every turn. A hook line injected at turn 10 of a 200-turn cycle is not
paid for once; it is paid for 190 times. So the number that matters is not
the size of the injections, it is the **carried** cost: for every assistant
turn, how much hook text was already in the conversation when that turn was
sent. That is what this measures, and it is what makes a 60-character line
worth checking at all.

Two failure modes are separated on purpose, because they have different
fixes and a single "how much" number merges them:

  * **carried share** -- steady per-turn drag. Fixed by making the line
    shorter or firing the hook less often.
  * **largest single injection** -- the wedge that 2.1.247's changelog
    describes, a hook printing megabytes of error output and overflowing
    the conversation in one go. Fixed by capping what a hook may print.
    A cycle can have a healthy share and still be one bad `stderr` away
    from dying, so a mean would hide exactly the case worth catching.

The transcripts record hook output as a first-class row rather than as
text inside a message: `{"type": "attachment", "attachment": {"type":
"hook_additional_context", "hookEvent": ..., "content": [...]}}`. That is
what makes this measurable at all, and it is why this reads transcripts
rather than trying to grep rendered prompts.

Same exit contract as the other checks in `prompt.md` step 1a:
  0  nothing to act on -- hook output is a rounding error on every cycle judged
  1  could not read (which never reads as clean -- the runner pod has no
     transcripts at all, so it returns 1 there rather than 0)
  2  hook output is a material share of a cycle's input, or a single
     injection is large enough to be a wedge risk
"""

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

# Same location cache_health reads. Only the bridge pod has these.
DEFAULT_TRANSCRIPT_ROOT = "/data/claude-home/.claude/projects"

# The CLI renders each injection with a wrapper naming the event and the
# tool -- "PostToolUse:Bash hook additional context: " -- which is itself
# context. Measured off this session's own rendered prompt; it is counted
# so the answer is not quietly smaller than what the model is actually
# sent. Held as one constant rather than reconstructed per row because the
# exact wrapper is the CLI's business and changes with it.
WRAPPER_CHARS = 45

# Roughly four characters to a token for English prose. This is an
# estimate and the report says so: an exact tokenizer would change the
# third significant figure of a number whose finding is an order of
# magnitude.
CHARS_PER_TOKEN = 4.0

# Thresholds, both derived from the first measurement rather than chosen
# for comfort (Cycle 602, eight cycles of 50+ turns): the worst carried
# share was 0.62% and the largest single injection was 106 characters. A
# hook problem worth a cycle's attention looks like an order of magnitude
# more than that, and the wedge case in the 2.1.247 changelog is
# megabytes. Both sit clear of measured-normal and clear of broken.
MAX_CARRIED_SHARE = 5.0
MAX_SINGLE_CHARS = 10000

# Below this a session is a probe, a health check or a killed cycle, not a
# cycle whose per-turn drag means anything.
MIN_TURNS = 50
DEFAULT_SESSIONS = 8


def transcript_root():
    return Path(os.environ.get("NOVA_TRANSCRIPT_ROOT", DEFAULT_TRANSCRIPT_ROOT))


def injection_text(attachment):
    """The text a hook_additional_context row actually contributes."""
    content = attachment.get("content")
    if isinstance(content, list):
        return "".join(str(part) for part in content)
    if isinstance(content, str):
        return content
    return ""


def measure_session(path):
    """Walk one transcript once, in order, accumulating the carried cost.

    Returns None when the file holds fewer than MIN_TURNS assistant turns
    -- see MIN_TURNS. Order matters: `running` is the hook text already in
    the conversation, so it must only ever grow as rows are read.

    **One API response is written as several assistant rows** -- one per
    content block, thinking and text and each tool_use -- and every one of
    them carries the *same* `usage` object. Measured on a real cycle: 154
    assistant rows against 86 distinct `message.id`, and summing the rows
    inflated that cycle's input from 11.5M tokens to 21.0M. So both the
    turn count and the token total are taken per distinct message id. The
    first draft of this tool did not, which mattered less than it looks
    like it should -- the same 1.8x lands on the numerator and the
    denominator -- but "turns" would have been a number about the
    transcript format rather than about the cycle.
    """
    turns = 0
    running_chars = 0
    carried_chars = 0
    total_chars = 0
    injections = 0
    largest = 0
    by_event = Counter()
    input_tokens = 0
    seen_messages = set()

    try:
        handle = path.open(errors="replace")
    except OSError:
        return None

    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            kind = row.get("type")
            if kind == "attachment":
                attachment = row.get("attachment") or {}
                if attachment.get("type") != "hook_additional_context":
                    continue
                size = len(injection_text(attachment)) + WRAPPER_CHARS
                injections += 1
                total_chars += size
                running_chars += size
                largest = max(largest, size)
                by_event[attachment.get("hookEvent") or "unknown"] += 1
            elif kind == "assistant":
                message = row.get("message") or {}
                message_id = message.get("id")
                # See the docstring: several rows share one response and
                # one usage object. Count the response, not the row.
                if message_id is not None and message_id in seen_messages:
                    continue
                if message_id is not None:
                    seen_messages.add(message_id)
                turns += 1
                # Every turn re-sends everything injected before it. This
                # sum, not total_chars, is what hooks actually cost.
                carried_chars += running_chars
                usage = message.get("usage") or {}
                input_tokens += (
                    usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0)
                )

    if turns < MIN_TURNS:
        return None
    return {
        "name": path.stem[:8],
        "turns": turns,
        "injections": injections,
        "total_chars": total_chars,
        "largest": largest,
        "by_event": dict(by_event),
        "carried_tokens": int(carried_chars / CHARS_PER_TOKEN),
        "input_tokens": input_tokens,
    }


def carried_share(row):
    """Carried hook tokens as a percentage of the input actually billed.

    None when the session recorded no usage at all -- that is unknown, not
    zero, and a caller must not read it as clean.
    """
    if not row["input_tokens"]:
        return None
    return 100.0 * row["carried_tokens"] / row["input_tokens"]


def is_subagent(path):
    """A subagent transcript, which runs no hooks at all.

    The CLI writes these under `<session-id>/subagents/agent-<id>.jsonl`.
    They must not be judged here: a session that structurally cannot carry
    a hook injection would report 0.00% every time, which is a clean
    result guaranteed in advance and would quietly drag the reported worst
    case down by padding the sample with rows that can only pass.
    """
    return path.parent.name == "subagents"


def newest_sessions(root, limit):
    paths = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows = []
    skipped_subagents = 0
    for path in paths:
        if is_subagent(path):
            skipped_subagents += 1
            continue
        row = measure_session(path)
        if row is None:
            continue
        rows.append(row)
        if len(rows) >= limit:
            break
    return rows, skipped_subagents


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None,
                        help="transcript root (default: the bridge pod's)")
    parser.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS,
                        help=f"how many recent cycles to judge (default {DEFAULT_SESSIONS})")
    parser.add_argument("--max-carried-share", type=float, default=MAX_CARRIED_SHARE)
    parser.add_argument("--max-single-chars", type=int, default=MAX_SINGLE_CHARS)
    args = parser.parse_args(argv)

    root = Path(args.root) if args.root else transcript_root()
    print("HOOK CONTEXT COST")
    if not root.is_dir():
        print(f"COULD NOT READ: no transcript directory at {root} -- only the "
              f"bridge pod has these, the runner pod has no CLI.")
        return 1

    rows, skipped_subagents = newest_sessions(root, args.sessions)
    if not rows:
        print(f"COULD NOT READ: no session under {root} had {MIN_TURNS} or more "
              f"assistant turns, so there is no cycle here to judge.")
        return 1

    print(f"  {'session':10} {'turns':>6} {'injected':>9} {'chars':>8} {'largest':>8} "
          f"{'carried tok':>12} {'input tok':>12}  share")
    hot = []
    unknown = []
    for row in rows:
        share = carried_share(row)
        note = ""
        if share is None:
            unknown.append(row)
            shown = "n/a"
            note = "  (no usage recorded)"
        else:
            shown = f"{share:5.2f}%"
            if share > args.max_carried_share:
                hot.append((row, share))
                note = "  <-- ABOVE THRESHOLD"
        print(f"  {row['name']:10} {row['turns']:6d} {row['injections']:9d} "
              f"{row['total_chars']:8d} {row['largest']:8d} {row['carried_tokens']:12d} "
              f"{row['input_tokens']:12d}  {shown}{note}")

    wedges = [r for r in rows if r["largest"] > args.max_single_chars]
    events = Counter()
    for row in rows:
        events.update(row["by_event"])

    print()
    print("  by hook event: " + ", ".join(f"{k} {v}" for k, v in sorted(events.items())))
    print(f"  carried = for each assistant turn, the hook text already in the "
          f"conversation when that turn was sent.")
    print(f"  chars include a {WRAPPER_CHARS}-char wrapper per injection and are "
          f"converted at {CHARS_PER_TOKEN:.0f} chars/token -- an estimate, not a tokenizer.")
    print(f"  a turn is one API response, not one transcript row -- several rows "
          f"share one response and one usage object.")
    if skipped_subagents:
        print(f"  {skipped_subagents} subagent transcript(s) passed over: they run no "
              f"hooks, so judging them would be a clean result guaranteed in advance.")

    if hot or wedges:
        print()
        if hot:
            print(f"HOOK OUTPUT IS MATERIAL on {len(hot)} of {len(rows)} cycle(s), "
                  f"threshold {args.max_carried_share:.0f}% of input:")
            for row, share in hot:
                print(f"  {row['name']}  {share:.2f}%  "
                      f"({row['injections']} injections over {row['turns']} turns)")
        if wedges:
            print(f"WEDGE RISK: {len(wedges)} cycle(s) had a single injection over "
                  f"{args.max_single_chars} characters:")
            for row in wedges:
                print(f"  {row['name']}  largest injection {row['largest']} chars")
            print("  A hook that can print unbounded output can overflow the "
                  "conversation in one turn; cap what it prints.")
        return 2

    if unknown:
        print()
        print(f"COULD NOT READ: {len(unknown)} of {len(rows)} cycle(s) recorded no "
              f"token usage, so their share is unknown rather than clean.")
        return 1

    worst = max((carried_share(r) for r in rows), default=0.0)
    print()
    print(f"Nothing to act on. Judged {len(rows)} cycle(s) of {MIN_TURNS}+ turns; "
          f"the worst carried {worst:.2f}% of its input tokens, against a "
          f"{args.max_carried_share:.0f}% threshold, and the largest single "
          f"injection was {max(r['largest'] for r in rows)} characters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
