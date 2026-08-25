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

**An alert stays open after its fix merges, and that is not something to
act on.** GitHub closes a Dependabot alert on its own re-scan schedule,
not on the push that fixes it, so between the merge and the re-scan the
API reports an open high-severity alert over a default branch that is
already patched. Three cycles in a row hit that window on the same
`brace-expansion` alert: Cycle 397 merged the bump and filed the lag as a
one-line note, Cycle 398 re-confirmed the fix by hand, and Cycle 399 --
which is writing this -- read exit 2, ranked it above the board, and
spent four tool calls proving the same thing a third time. Each of us was
right and each of us paid full price, because the knowledge lived in a
backlog bullet rather than in the instrument.

So every open alert is now checked against the manifest on the repo's
default branch, and one whose fix has demonstrably landed is printed
under its own heading and does **not** raise the exit status. The check
only ever downgrades an alert on a positive measurement -- a manifest it
cannot parse, an ecosystem it does not know, a version it cannot compare,
or an API call that fails all leave the alert exactly as actionable as it
was. Suppressing a real vulnerability is the one failure here that costs
more than the false alarm, so every uncertain path fails towards noise.

Exit status: 0 when every repo answered and nothing needs acting on, 1
when anything was unreadable or alerts are disabled somewhere, 2 when
there is an open alert whose fix is not already on the default branch. So
a cycle can read the status without parsing the text, and "I could not
check" never reads as "nothing here".
"""

import argparse
import json
import re
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


# The only manifests this can read a resolved version out of. A lockfile
# is the right thing to check rather than `package.json`, because the
# alert is raised against what is actually resolved, not against the range
# that was asked for. Anything not in here is left actionable.
_LOCKFILES = {"package-lock.json"}

_PLAIN_VERSION = re.compile(r"\d+(?:\.\d+)*")


def _version_tuple(text):
    """A comparable tuple, or `None` when comparison would be a guess.

    Deliberately refuses anything that is not plain dotted digits --
    `1.2.3-rc1` sorts *below* `1.2.3` under semver and *above* it under a
    naive string compare, and getting that backwards would mark a
    vulnerable branch as patched. There is no partial credit here: an
    unparseable version means the alert stays actionable.
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip().lstrip("v")
    if not _PLAIN_VERSION.fullmatch(stripped):
        return None
    return tuple(int(part) for part in stripped.split("."))


def lockfile_versions(blob, package):
    """Every version of `package` recorded in an npm lockfile, or `None`.

    `None` means "could not establish", which is a different answer from
    `[]` ("the lockfile does not contain this package") and both leave the
    alert actionable. Only lockfile v2/v3 `packages` maps are read; a v1
    file has no `packages` key and returns `None` rather than a wrong
    answer from the older `dependencies` tree.

    Every path is collected, not the first one -- npm records a package
    once per place it is resolved, and a nested copy can sit at an older
    version than the top-level one.
    """
    try:
        parsed = json.loads(blob)
    except (ValueError, TypeError):
        return None
    packages = parsed.get("packages") if isinstance(parsed, dict) else None
    if not isinstance(packages, dict):
        return None
    found = []
    for path, entry in packages.items():
        if not isinstance(path, str) or "node_modules/" not in path:
            continue
        if path.split("node_modules/")[-1] != package:
            continue
        found.append(entry.get("version") if isinstance(entry, dict) else None)
    return found


def fix_landed(repo, alert, run=None):
    """`(landed, note)` -- whether the default branch is already patched.

    `landed` is True only on a positive measurement. Every other path --
    an ecosystem with no reader here, no published patch, a failed API
    call, an unparseable version -- returns False with a note saying which
    one, so the alert keeps its full weight and the report can say why it
    was not verified rather than staying silent about it.
    """
    manifest = alert.get("manifest") or ""
    if manifest.rsplit("/", 1)[-1] not in _LOCKFILES:
        return False, f"no reader for {manifest or 'a missing manifest'}"
    patched_text = alert.get("patched")
    patched = _version_tuple(patched_text)
    if patched is None:
        return False, "no comparable patched version published"

    # No `?ref=`: the contents API serves the default branch by default,
    # which is the branch the alert is raised against.
    code, out, err = (run or _gh)(
        [
            "api",
            f"repos/{repo}/contents/{manifest}",
            "-H",
            "Accept: application/vnd.github.raw",
        ]
    )
    if code != 0:
        return False, "could not read the manifest on the default branch"

    versions = lockfile_versions(out, alert.get("package") or "")
    if versions is None:
        return False, "could not parse the manifest"
    if not versions:
        return False, "the manifest no longer records this package"
    comparable = [_version_tuple(v) for v in versions]
    if any(v is None for v in comparable):
        return False, "the manifest records a version I cannot compare"
    if not all(v >= patched for v in comparable):
        return False, "the default branch still resolves a vulnerable version"
    resolved = ", ".join(sorted(set(versions)))
    return True, f"default branch resolves {resolved}, patched at {patched_text}"


def verify_landed(results, run=None):
    """Annotate every open alert in `results` in place with the check above."""
    for repo in results:
        state, payload = results[repo]
        if state != OK:
            continue
        for alert in payload:
            landed, note = fix_landed(repo, alert, run=run)
            alert["landed"] = landed
            alert["landed_note"] = note


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

    open_alerts.sort(key=lambda pair: _rank(pair[1]))
    actionable = [pair for pair in open_alerts if not pair[1].get("landed")]
    landed = [pair for pair in open_alerts if pair[1].get("landed")]

    def _describe(repo, alert, detail):
        lines.append(
            f"  {alert['severity'].upper():9} {repo}  "
            f"{alert['ecosystem']}/{alert['package']} "
            f"-> {alert['patched']}  ({alert['manifest']})"
        )
        lines.append(f"      {detail}")
        if alert["url"]:
            lines.append(f"      {alert['url']}")

    if actionable:
        lines.append(
            f"OPEN SECURITY ALERTS — {len(actionable)} across "
            f"{len({repo for repo, _ in actionable})} repo(s):"
        )
        for repo, alert in actionable:
            _describe(repo, alert, alert["summary"])
            # Only worth saying when something was tried and did not
            # settle it. "no reader for X" here is the honest reason this
            # alert is still in the actionable list rather than an
            # unexplained one.
            note = alert.get("landed_note")
            if note:
                lines.append(f"      not verified as fixed: {note}")

    if landed:
        lines.append(
            f"ALREADY FIXED ON THE DEFAULT BRANCH — {len(landed)} alert(s) "
            "GitHub has not re-scanned yet. Nothing to do; the record "
            "clears itself."
        )
        for repo, alert in landed:
            _describe(repo, alert, alert.get("landed_note", ""))
    # Printed whether or not anything was found. Without it, a report with
    # one alert on one repo says nothing about whether the other four were
    # swept at all, and "checked and clean" would be indistinguishable from
    # "never looked" -- the same equation the DISABLED line exists to refuse.
    checked = [r for r in sorted(results) if results[r][0] == OK]
    if checked:
        if actionable:
            prefix = ""
        elif landed:
            prefix = "Nothing to act on. "
        else:
            prefix = "No open security alerts. "
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

    if actionable:
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
    verify_landed(results)
    lines, code = format_report(results)
    for line in lines:
        print(line)
    for clone in unplaceable:
        print(f"⚠ {clone}: could not place this checkout on GitHub, not swept")
    return code


if __name__ == "__main__":
    sys.exit(main())
