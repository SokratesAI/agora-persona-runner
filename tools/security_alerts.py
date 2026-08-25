"""Open Dependabot alerts across every repo this loop has a checkout of.

Cycle 397. GitHub had been reporting one high-severity vulnerability on
`SokratesAI/agora`'s default branch, and the only place that fact ever
appeared was a `remote:` line printed by `git push` -- so it was visible
only to a cycle that happened to push to that repo, and only in the
scrollback of a command run for another reason. Cycle 396 saw one that
way and wrote it into the handoff. No step in `prompt.md` reads an alert,
so there was no path by which a cycle that did not push would find one.

The thing worth naming is not the single alert -- that is one `npm audit
fix`. It is that a security advisory has no owner here at all, the same
shape as `roadmap.md` sitting nine days stale because no job refreshed
it. A finding that arrives only as a side effect of an unrelated command
is not reported; it is merely occasionally noticed.

    python3 -m tools.security_alerts

**Three outcomes per repo, and keeping them apart is the whole design.**
`ok` means the API answered and the count is real. `disabled` means the
repo has Dependabot alerts switched off, which is not zero alerts -- it
is no instrument, and `SokratesAI/platform-config` and `SokratesAI/vault`
are both in that state today. `error` means the call failed. A tool that
collapsed the last two into "no alerts found" would print exactly what a
clean sweep prints, which is the one equation this repo keeps having to
refuse to write (`tidy_workspace.origin_repos` refuses it too, and for
the same reason).

Exit status: 0 when every repo answered and none had an alert, 1 when
anything was unreadable or alerts are disabled somewhere, 2 when there is
a real open alert to act on. So a cycle can read the status without
parsing the text, and "I could not check" never reads as "nothing here".
"""

import argparse
import json
import subprocess
import sys

# Worst first. `gh` reports whatever GitHub's advisory database says, and
# an unrecognised value sorts last rather than crashing -- a severity name
# this list has not heard of is still an alert worth printing.
SEVERITY_ORDER = ["critical", "high", "moderate", "low"]

OK = "ok"
DISABLED = "disabled"
ERROR = "error"

# GitHub answers 403 with this message when the feature is off, which is a
# different thing from 403-because-the-token-cannot-see-the-repo. Matching
# the message is how the two stay apart; if GitHub reworded it, the repo
# would be reported as an error, which is the safe direction to be wrong in.
_DISABLED_MARKER = "dependabot alerts are disabled"


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(
        ["gh"] + args, capture_output=True, text=True, timeout=60
    )
    return proc.returncode, proc.stdout, proc.stderr


def alerts_for(repo, run=None):
    """`(state, alerts_or_message)` for one `owner/name`.

    `run` is injected so the tests never reach GitHub, and it resolves to
    `_gh` at call time rather than as a default argument -- a default binds
    the function object once at import, so monkeypatching `_gh` would leave
    this calling the real `gh` while the test believed otherwise. That is a
    test that passes for the wrong reason, which is the failure this repo
    keeps writing down.
    """
    code, out, err = (run or _gh)(
        [
            "api",
            f"repos/{repo}/dependabot/alerts?state=open&per_page=100",
        ]
    )
    if code != 0:
        blob = (err or out or "").strip()
        if _DISABLED_MARKER in blob.lower():
            return DISABLED, "Dependabot alerts are disabled for this repository"
        return ERROR, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return ERROR, "gh returned something that is not JSON"
    if not isinstance(payload, list):
        return ERROR, "gh returned a JSON object where a list of alerts was expected"
    return OK, [_summarise(a) for a in payload]


def _summarise(alert):
    """The five fields worth printing, each defaulted rather than assumed.

    Every one of these has been absent from a real GitHub payload at some
    point -- a withdrawn advisory carries no patched version, and a
    manifest can be null on an alert raised against the repo itself -- so
    a missing key prints `?` instead of raising and losing the whole repo.
    """
    advisory = alert.get("security_advisory") or {}
    vuln = alert.get("security_vulnerability") or {}
    package = (vuln.get("package") or (alert.get("dependency") or {}).get("package")) or {}
    patched = vuln.get("first_patched_version") or {}
    return {
        "severity": (advisory.get("severity") or "unknown").lower(),
        "package": package.get("name") or "?",
        "ecosystem": package.get("ecosystem") or "?",
        "summary": advisory.get("summary") or "(no summary)",
        "manifest": (alert.get("dependency") or {}).get("manifest_path") or "?",
        "patched": patched.get("identifier") or "none published",
        "url": alert.get("html_url") or "",
    }


def _rank(alert):
    severity = alert["severity"]
    index = (
        SEVERITY_ORDER.index(severity)
        if severity in SEVERITY_ORDER
        else len(SEVERITY_ORDER)
    )
    return (index, alert["package"])


def format_report(results):
    """`(lines, exit_code)` from `{repo: (state, payload)}`."""
    lines = []
    open_alerts = []
    unreadable = []

    for repo in sorted(results):
        state, payload = results[repo]
        if state == OK:
            for alert in payload:
                open_alerts.append((repo, alert))
        else:
            unreadable.append((repo, state, payload))

    if open_alerts:
        open_alerts.sort(key=lambda pair: _rank(pair[1]))
        lines.append(
            f"OPEN SECURITY ALERTS — {len(open_alerts)} across "
            f"{len({repo for repo, _ in open_alerts})} repo(s):"
        )
        for repo, alert in open_alerts:
            lines.append(
                f"  {alert['severity'].upper():9} {repo}  "
                f"{alert['ecosystem']}/{alert['package']} "
                f"-> {alert['patched']}  ({alert['manifest']})"
            )
            lines.append(f"      {alert['summary']}")
            if alert["url"]:
                lines.append(f"      {alert['url']}")
    # Printed whether or not anything was found. Without it, a report with
    # one alert on one repo says nothing about whether the other four were
    # swept at all, and "checked and clean" would be indistinguishable from
    # "never looked" -- the same equation the DISABLED line exists to refuse.
    checked = [r for r in sorted(results) if results[r][0] == OK]
    if checked:
        prefix = "" if open_alerts else "No open security alerts. "
        lines.append(f"{prefix}Answered: " + ", ".join(checked))

    for repo, state, payload in unreadable:
        if state == DISABLED:
            lines.append(
                f"⚠ {repo}: Dependabot alerts are DISABLED — this is not "
                "zero alerts, it is no instrument. Nobody can see a "
                "vulnerability in this repo."
            )
        else:
            lines.append(f"⚠ {repo}: COULD NOT READ — {payload}")

    if not results:
        lines.append(
            "⚠ No repos to check — nothing was measured, which is not the "
            "same as nothing being wrong."
        )
        return lines, 1

    if open_alerts:
        return lines, 2
    if unreadable:
        return lines, 1
    return lines, 0


def _repos_from_workspace():
    """The repos this loop has checked out, plus any clone it could not place.

    Derived rather than hardcoded, for `tidy_workspace.origin_repos`' own
    reason: a hardcoded list of "the repos we touch" has already gone stale
    in this repo twice. The cost is that a repo with no checkout here is
    not swept, which the report says out loud rather than quietly omitting.
    """
    from tools.tidy_workspace import origin_repos, workspace_roots

    repos, unplaceable = origin_repos(workspace_roots())
    return repos, unplaceable


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        action="append",
        default=None,
        help="owner/name; repeatable. Defaults to every repo checked out "
        "in the workspace.",
    )
    args = parser.parse_args(argv)

    unplaceable = []
    if args.repo:
        repos = args.repo
    else:
        repos, unplaceable = _repos_from_workspace()

    results = {repo: alerts_for(repo) for repo in repos}
    lines, code = format_report(results)
    for line in lines:
        print(line)
    for clone in unplaceable:
        print(f"⚠ {clone}: could not place this checkout on GitHub, not swept")
    return code


if __name__ == "__main__":
    sys.exit(main())
