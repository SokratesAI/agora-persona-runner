"""Is prompt caching actually working for this loop?

Cycle 549, on the owner's idea #130. Claude Code 2.1.237 fixed prompt
caching for sessions routed through an LLM gateway or a custom base URL,
and this loop was running 2.1.226 when that shipped. The row rated it
High for one reason, and it is the reason this file exists: *"the failure
mode is invisible: nothing breaks, we just pay more and hit limits
sooner."*

That specific worry is closed -- measured Cycle 549 across every
transcript on the bridge pod, cache reads were 96.59% to 97.20% of input
on every one of 20-26 August, including the five days on 2.1.226 before
the fix existed. We were never routed through a gateway, so the bug never
applied.

But the check that closed it was a throwaway script, and a throwaway
script is the shape of a fact that gets rediscovered rather than
reported. Caching can be lost again by one environment variable -- an
`ANTHROPIC_BASE_URL`, a proxy, a Bedrock or Vertex switch -- and the
only symptom is a larger bill. So:

    python3 -m tools.cache_health

**The measurement is a ratio, not a total.** Cache reads and fresh input
both scale with how much this loop ran that day, so an absolute number
says nothing on its own. `cache_read / (cache_read + cache_creation +
input)` is flat at ~97% across a week whose daily volume varied by a
factor of three, which is what makes a threshold on it meaningful.

**The threshold is derived from that range, not chosen for comfort.**
Caching working looks like 97%; caching broken looks like ~0%, because
every turn re-sends the whole prompt as fresh input. 50% sits an order of
magnitude below the observed floor and an order of magnitude above a
failure, so it cannot fire on ordinary variation and cannot miss the
thing it is for. `--min-cache-share` moves it if the shape of the loop
changes.

**Today is excluded and short days are skipped.** A day in progress is a
partial sample, and a day with a handful of messages is dominated by
whichever turn happened to be a cold start. `--min-messages` is that
floor.

**The environment is reported beside the ratio, because it is the
cause.** If the ratio ever drops, the next question is which variable
changed, and the answer should be in the same output rather than in a
second investigation.

Exit status, matching `tools.security_alerts` and `tools.cli_pin` so a
cycle can read it without parsing the text: 0 when caching is healthy on
the days measured, 2 when a completed day fell below the threshold, 1
when nothing readable was found. "I could not check" never reads as
"nothing here".
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Where the Claude Code CLI writes session transcripts. Only the bridge
# pod has these -- the runner pod has no CLI and therefore no transcripts,
# which is why this tool reports "could not read" rather than "clean"
# when the directory is missing.
DEFAULT_TRANSCRIPT_ROOT = "/data/claude-home/.claude/projects"

# Set by a gateway, a proxy, or a non-Anthropic backend. Any of these
# being present is what makes the 2.1.237 bug reachable at all.
ROUTING_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BEDROCK_BASE_URL",
    "ANTHROPIC_VERTEX_BASE_URL",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "HTTPS_PROXY",
    "HTTP_PROXY",
)

MIN_CACHE_SHARE = 50.0
MIN_MESSAGES = 200
DEFAULT_DAYS = 7


def transcript_root():
    return Path(os.environ.get("NOVA_TRANSCRIPT_ROOT", DEFAULT_TRANSCRIPT_ROOT))


def usage_by_day(root, since=None):
    """Sum token usage per UTC day across every session transcript.

    Subagent transcripts are included deliberately: a subagent is a real
    request against the same account, so excluding it would measure a
    cheaper loop than the one we actually run.
    """
    days = {}
    files = 0
    for path in sorted(root.rglob("*.jsonl")):
        files += 1
        try:
            handle = path.open(errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                if '"usage"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                usage = message.get("usage")
                if not isinstance(usage, dict):
                    continue
                stamp = record.get("timestamp")
                if not isinstance(stamp, str) or len(stamp) < 10:
                    continue
                day = stamp[:10]
                if since and day < since:
                    continue
                counts = days.setdefault(day, Counter())
                counts["messages"] += 1
                counts["input"] += usage.get("input_tokens") or 0
                counts["cache_create"] += usage.get("cache_creation_input_tokens") or 0
                counts["cache_read"] += usage.get("cache_read_input_tokens") or 0
                counts["output"] += usage.get("output_tokens") or 0
    return days, files


def cache_share(counts):
    """Cache reads as a percentage of everything sent as input."""
    total = counts["input"] + counts["cache_create"] + counts["cache_read"]
    if not total:
        return None
    return 100.0 * counts["cache_read"] / total


def routing_env(environ):
    """The variables that would put a gateway between us and the API."""
    return {name: environ[name] for name in ROUTING_VARS if environ.get(name)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help="how many completed days back to measure")
    parser.add_argument("--min-cache-share", type=float, default=MIN_CACHE_SHARE,
                        help="percent of input that must come from cache")
    parser.add_argument("--min-messages", type=int, default=MIN_MESSAGES,
                        help="skip a day with fewer assistant messages than this")
    parser.add_argument("--now", default=None,
                        help="UTC date to treat as today, YYYY-MM-DD (testing)")
    args = parser.parse_args(argv)

    today = args.now or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    since = (datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=timezone.utc)
             - timedelta(days=args.days)).strftime("%Y-%m-%d")

    root = transcript_root()
    if not root.is_dir():
        print(f"COULD NOT READ: no transcript directory at {root}")
        print("This tool only works from the bridge pod, which is where the CLI writes.")
        return 1

    days, files = usage_by_day(root, since=since)
    measured = {day: counts for day, counts in days.items() if day < today}

    print("PROMPT CACHE HEALTH")
    routing = routing_env(os.environ)
    if routing:
        print("  routed through:  " + ", ".join(sorted(routing)))
        print("  (a gateway or custom base URL is set -- prompt caching depends on")
        print("   the CLI being 2.1.237 or newer when this is in play)")
    else:
        print("  routed through:  nothing -- no gateway, proxy or custom base URL set")

    if not measured:
        print(f"COULD NOT READ: {files} transcript file(s) under {root}, "
              f"no completed day of usage since {since}")
        return 1

    print()
    print(f"  {'day':12} {'msgs':>7} {'fresh input':>13} {'cache write':>13} "
          f"{'cache read':>14}  cached")
    below = []
    skipped = []
    for day in sorted(measured):
        counts = measured[day]
        share = cache_share(counts)
        note = ""
        if counts["messages"] < args.min_messages:
            skipped.append(day)
            note = "  (too few messages to judge)"
        elif share is not None and share < args.min_cache_share:
            below.append((day, share))
            note = "  <-- BELOW THRESHOLD"
        shown = "n/a" if share is None else f"{share:6.2f}%"
        print(f"  {day:12} {counts['messages']:7d} {counts['input']:13d} "
              f"{counts['cache_create']:13d} {counts['cache_read']:14d}  {shown}{note}")

    judged = [d for d in sorted(measured) if d not in skipped]
    print()
    if below:
        print(f"CACHING DEGRADED on {len(below)} of {len(judged)} day(s) judged, "
              f"threshold {args.min_cache_share:.0f}%:")
        for day, share in below:
            print(f"  {day}  {share:.2f}% of input came from cache")
        print("Check the routing line above first: a gateway or custom base URL "
              "needs Claude Code 2.1.237 or newer.")
        return 2

    if not judged:
        print(f"COULD NOT READ: every day since {since} had fewer than "
              f"{args.min_messages} messages")
        return 1

    print(f"Caching healthy on all {len(judged)} day(s) judged "
          f"({judged[0]} to {judged[-1]}), threshold {args.min_cache_share:.0f}%.")
    if skipped:
        print(f"Not judged, too few messages: {', '.join(skipped)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
