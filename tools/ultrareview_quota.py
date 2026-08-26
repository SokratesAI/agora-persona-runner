"""How many free `claude ultrareview` runs are left, and would one stall?

Cycle 508, on the owner's idea #112 -- *"Buy a second opinion I did not
write: `claude ultrareview` from a script."* The row asked two things:
what one run costs against the seven-day window, and whether it is
reachable from an unattended session. The first answer is "nothing" --
the review runs in Claude Code on the web and the local CLI only polls,
so it does not touch the subscription token window at all. The
constraint is not a percentage of anything. It is a count, and on this
account the count is three.

    python3 -m tools.ultrareview_quota

That number is why this is a tool rather than a paragraph. A research
write-up saying "three free reviews remain" is true for as long as
nobody uses one, and the moment somebody does, the file says the
opposite of the truth with no way to tell. The API knows; nothing here
read it.

**The unattended gate is the fourth run, not the first three.**
Preflight answers `proceed`, `confirm` or `blocked`. While free reviews
remain it says `proceed` and a script runs with no prompt. Once they are
spent it says `confirm`, and the CLI renders an overage dialog -- *"This
review bills as usage credits"* -- that only clears when a human answers
it. An unattended `claude ultrareview` at that point does not fail
loudly. It waits on a screen nobody is looking at. So the honest verdict
for a cycle is not "how many are left" but "would a run right now
proceed on its own", and those stop being the same question exactly once.

`blocked` covers the two environmental refusals -- essential-traffic-only
/ ZDR mode, and third-party providers (Bedrock/Vertex) -- and is
reported as its own state rather than folded into "no reviews left",
because one is a quota and the other is a configuration.

Exit status, matching `tools.security_alerts` and `tools.cli_pin` so a
cycle can read it without parsing the text: 0 when a run would proceed
unattended, 2 when it would need a human or is refused outright, 1 when
something was unreadable. "I could not check" never reads as "nothing
here".

Both endpoints are plain GETs and neither creates a review. This module
deliberately cannot start one; reporting the price is a different act
from paying it.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://api.anthropic.com"
QUOTA_PATH = "/v1/ultrareview/quota"
PREFLIGHT_PATH = "/v1/ultrareview/preflight"
TIMEOUT = 20


def credential_candidates():
    """Where the Claude Code OAuth token might be, most specific first."""
    out = []
    roots = [os.environ.get("CLAUDE_CONFIG_DIR"),
             os.path.expanduser("~/.claude"),
             "/data/claude-home/.claude"]
    for root in roots:
        if not root:
            continue
        path = os.path.join(root, ".credentials.json")
        if path not in out:
            out.append(path)
    return out


def read_token(paths=None):
    """Return (token, where) or (None, why-not)."""
    tried = []
    for path in paths if paths is not None else credential_candidates():
        try:
            with open(path, encoding="utf-8") as fh:
                blob = json.load(fh)
        except (OSError, ValueError):
            tried.append(path)
            continue
        token = (blob.get("claudeAiOauth") or {}).get("accessToken")
        if token:
            return token, path
        tried.append(path)
    return None, "no Claude Code OAuth token in " + (", ".join(tried) or "any known location")


def _get(path, token, opener=urllib.request.urlopen):
    req = urllib.request.Request(
        BASE + path,
        headers={"Authorization": "Bearer " + token,
                 "anthropic-beta": "oauth-2025-04-20",
                 "User-Agent": "nova-ultrareview-quota",
                 "Accept": "application/json"},
    )
    with opener(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch(token, opener=urllib.request.urlopen):
    """Return (quota, preflight, problems). Either dict may be None."""
    quota = preflight = None
    problems = []
    for name, path in (("quota", QUOTA_PATH), ("preflight", PREFLIGHT_PATH)):
        try:
            body = _get(path, token, opener=opener)
        except urllib.error.HTTPError as exc:
            problems.append(f"{name} returned HTTP {exc.code}")
            continue
        except (OSError, ValueError) as exc:
            problems.append(f"{name} failed: {exc}")
            continue
        if name == "quota":
            quota = body
        else:
            preflight = body
    return quota, preflight, problems


def report(quota, preflight, problems, out=print):
    """Print the finding and return the exit status."""
    for problem in problems:
        out(f"COULD NOT READ — {problem}")

    if quota:
        used = quota.get("reviews_used")
        limit = quota.get("reviews_limit")
        remaining = quota.get("reviews_remaining")
        out(f"Free ultrareviews: {remaining} left of {limit} ({used} used)"
            + (" — in overage" if quota.get("is_overage") else ""))
    else:
        out("Free ultrareviews: unknown")

    action = (preflight or {}).get("action")
    note = (preflight or {}).get("billing_note")
    if note:
        out(f"  the API's own wording: {note}")

    if action == "proceed":
        out("A run right now would proceed on its own — no dialog, no charge "
            "against the token window. `claude ultrareview [target] --json`.")
        return 0
    if action == "confirm":
        body = ((preflight or {}).get("confirm") or {}).get("body") or \
            "this review bills as usage credits"
        out("NEEDS A HUMAN — the free reviews are spent and the next one "
            f"bills: {body}. An unattended run does not fail here, it waits "
            "on an overage dialog nobody is looking at.")
        return 2
    if action == "blocked":
        blocked = (preflight or {}).get("blocked") or {}
        out("BLOCKED — " + (blocked.get("message") or
                            "ultrareview is unavailable for this account")
            + " This is a configuration, not a quota.")
        return 2
    out("COULD NOT READ — preflight gave no verdict, so I do not know "
        "whether a run would proceed.")
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="print the raw API responses instead of the report")
    args = parser.parse_args(argv)

    token, where = read_token()
    if not token:
        print(f"COULD NOT READ — {where}")
        return 1

    quota, preflight, problems = fetch(token)
    if args.json:
        print(json.dumps({"quota": quota, "preflight": preflight,
                          "problems": problems, "credentials": where}, indent=2))
        # Same status as the report, so `--json` cannot say "fine" about a
        # preflight that never answered.
        return report(quota, preflight, problems, out=lambda _line: None)
    return report(quota, preflight, problems)


if __name__ == "__main__":
    sys.exit(main())
