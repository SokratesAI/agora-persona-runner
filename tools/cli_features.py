"""Has a gated Claude Code capability this loop is waiting on become available?

Cycle 533, on the owner's idea #83 -- the dreaming pass over my own
memory. Cycle 505 left the remaining slice as one line: the CLI carries
`autoDreamEnabled`, *"background memory consolidation (auto-dream)"*, and
it wanted a cycle once the auto-memory store had something in it. It does
now -- 18 files, 20KB, written across the cycles since bridge#80 pinned
`autoMemoryDirectory`.

**Setting the key would have done nothing, and it would have looked like
it worked.** The 2.1.245 binary gates it twice:

    function iZo(){let e=Me("tengu_onyx_plover",null);
                   return e?.enabled===!0||e?.available===!0}
    function C6n(){if(!iZo())return!1;
                   let e=Jo().autoDreamEnabled;if(e!==void 0)return e;...}

The settings key is only consulted *after* the server-side gate opens. On
this account the gate reads `{"enabled": false, "minHours": 24,
"minSessions": 3, "remoteEnabled": false}`, so auto-dream is off no
matter what the bridge writes into `--settings`. A cycle that shipped the
one-line change would have seen no error and written "auto-dream enabled"
into the permanent record -- `prompt.md`'s positive-result-guaranteed-in-
advance failure, arriving on a feature flag.

So the finding is "not yet", and the thing worth building is not the
settings line but something that says *when*. Otherwise this gets
re-derived by hand every few weeks, which is the shape
`tools.security_alerts` and `tools.cli_pin` both exist to end.

    python3 -m tools.cli_features

**It reads a cache the CLI refreshes on every launch, not the network.**
`~/.claude/.claude.json` carries `cachedGrowthBookFeatures` -- 556 gates
for this account -- alongside `cachedGrowthBookFeaturesAt`. This loop
starts a CLI every cycle, so that blob is minutes old whenever a cycle
reads it. That freshness is the whole reason a local read is honest here,
and it is checked rather than assumed: a cache older than
`--max-age-hours` is reported as unreadable, because a stale gate and a
closed gate look identical and mean opposite things.

**The availability rule is the binary's, copied exactly.** `enabled` true
*or* `available` true. Anything else -- absent, false, a gate key that is
not in the blob at all -- is closed. A gate this loop has never seen is
not evidence of anything, so a missing key reports as unknown rather than
as closed.

Exit status, matching its siblings so a cycle can read it without parsing
the text: **2 means a gate has opened and there is a change to make**, 1
means the cache was unreadable or too old (which never reads as clean),
0 means every gate answered and none of them has moved.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# One row per capability this loop is actually waiting on. `gate` is the
# server-side flag the binary consults first; `setting` is what the
# bridge would write once it opens. Adding a row is the whole extent of
# extending this -- deliberately not a scan of all 556 gates, because a
# report of everything is a report nobody reads.
WATCHED = (
    {
        "name": "auto-dream (background memory consolidation)",
        "gate": "tengu_onyx_plover",
        "setting": "autoDreamEnabled",
        "why": "idea #83's remaining slice -- consolidates the auto-memory store in nova-memory/",
        "action": (
            "add \"autoDreamEnabled\": true to the settings file the bridge writes "
            "(same place as autoMemoryDirectory, bridge#80)"
        ),
    },
)

DEFAULT_MAX_AGE_HOURS = 24


class FeatureError(Exception):
    """The gate blob could not be read, which is never the same as clean."""


def config_path():
    """Where the CLI keeps its per-account config.

    `CLAUDE_CONFIG_DIR` wins when set -- that is the CLI's own override --
    then `~/.claude/.claude.json`, which is where it lands on this pod.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return os.path.join(override, ".claude.json")
    return os.path.join(os.path.expanduser("~"), ".claude", ".claude.json")


def read_gates(path):
    """Return (gates, cached_at) or raise FeatureError.

    `cached_at` is a timezone-aware datetime; the CLI stores epoch
    milliseconds.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        raise FeatureError("no CLI config at %s" % path)
    except (OSError, ValueError) as exc:
        raise FeatureError("could not parse %s: %s" % (path, exc))

    gates = config.get("cachedGrowthBookFeatures")
    if not isinstance(gates, dict):
        raise FeatureError("%s carries no cachedGrowthBookFeatures object" % path)

    stamp = config.get("cachedGrowthBookFeaturesAt")
    if not isinstance(stamp, (int, float)):
        raise FeatureError("%s carries no cachedGrowthBookFeaturesAt timestamp" % path)

    cached_at = datetime.fromtimestamp(stamp / 1000.0, tz=timezone.utc)
    return gates, cached_at


def gate_open(value):
    """The binary's own rule: `enabled === true || available === true`.

    A bare `true` counts too -- some gates in this blob are plain booleans
    rather than objects, and one of those means on.
    """
    if value is True:
        return True
    if isinstance(value, dict):
        return value.get("enabled") is True or value.get("available") is True
    return False


def judge(gates, watched=WATCHED):
    """Classify each watched capability as open / closed / unknown."""
    verdicts = []
    for row in watched:
        key = row["gate"]
        if key not in gates:
            state = "unknown"
        elif gate_open(gates[key]):
            state = "open"
        else:
            state = "closed"
        verdicts.append({"row": row, "state": state, "value": gates.get(key)})
    return verdicts


def report(verdicts, cached_at, age, out=sys.stdout):
    """Print the verdicts and return the exit status they imply."""
    opened = [v for v in verdicts if v["state"] == "open"]
    unknown = [v for v in verdicts if v["state"] == "unknown"]

    for verdict in opened:
        row = verdict["row"]
        out.write("GATE OPEN -- %s is available to this account now.\n" % row["name"])
        out.write("      %s\n" % row["why"])
        out.write("      %s\n" % row["action"])
        out.write("      gate %s = %s\n" % (row["gate"], json.dumps(verdict["value"])))

    for verdict in unknown:
        row = verdict["row"]
        out.write(
            "CANNOT SEE  %s: gate %s is not in this account's flags at all, so its "
            "state is unknown -- not closed.\n" % (row["name"], row["gate"])
        )

    for verdict in verdicts:
        if verdict["state"] == "closed":
            row = verdict["row"]
            out.write(
                "closed      %s -- gate %s = %s\n"
                % (row["name"], row["gate"], json.dumps(verdict["value"]))
            )

    hours = age.total_seconds() / 3600.0
    out.write(
        "Read %d watched capabilit(y/ies) from the CLI's own flag cache, written "
        "%s UTC (%.1fh ago).\n" % (len(verdicts), cached_at.isoformat(timespec="seconds"), hours)
    )
    out.write(
        "The cache is rewritten every time the CLI launches, which is every cycle -- "
        "a stale one is reported rather than trusted.\n"
    )
    if opened:
        return 2
    if unknown:
        return 1
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None, help="path to the CLI's .claude.json")
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help="a flag cache older than this is reported as unreadable (default %(default)s)",
    )
    args = parser.parse_args(argv)

    path = args.config or config_path()
    try:
        gates, cached_at = read_gates(path)
    except FeatureError as exc:
        sys.stdout.write("CANNOT READ  %s\n" % exc)
        sys.stdout.write("Unreadable never reads as clean.\n")
        return 1

    age = datetime.now(timezone.utc) - cached_at
    if age > timedelta(hours=args.max_age_hours):
        sys.stdout.write(
            "CANNOT READ  the flag cache in %s was written %s UTC, %.1f hours ago -- "
            "past --max-age-hours %.1f, so it is not this account's current state.\n"
            % (path, cached_at.isoformat(timespec="seconds"), age.total_seconds() / 3600.0,
               args.max_age_hours)
        )
        sys.stdout.write("A stale gate and a closed gate look identical and mean opposite things.\n")
        return 1

    return report(judge(gates), cached_at, age)


if __name__ == "__main__":
    sys.exit(main())
