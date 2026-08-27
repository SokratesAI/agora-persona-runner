"""Open Dependabot alerts across every repo in this loop's GitHub orgs.

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

**The sweep covers the org, not the checkouts -- Cycle 432.** It used to
ask GitHub about the three or four repos this loop happens to have cloned,
which is a different set from "the repos this platform owns" and is much
smaller. On 2026-08-25 it printed "Nothing to act on" while
`SokratesAI/sokrates-docs` carried six open Dependabot alerts, four of them
high; the only cycle that ever saw them was one that pushed to that repo
and read a `remote:` line in passing. That is the accidental-noticing this
module was written to replace, reappearing one level up, in the module
itself. The org list is derived from the checkouts' own owners rather than
hardcoded, so the "no stale constant" property above survives, and the
checkouts are unioned back in so nothing that was swept before stops being.

**A dismissal is not permanent when the reason for it was "there is no
fix" -- Cycle 457.** `SokratesAI/sokrates-docs` carried two high-severity
`image-size` advisories with no patched version published, so this tool
exited 2 every cycle and no cycle could do anything but re-measure that
there was nothing to do. They are dismissed now as `tolerable_risk`, with
the measurement in the dismissal comment: the package is reached only by
`@docusaurus/mdx-loader` at build time, and the Docker runner stage ships
only `build/` and a `serve.mjs` that imports nothing but node builtins, so
no `node_modules` reaches production at all. That dismissal is correct and
it is also a trap, because GitHub never reopens a dismissed alert -- if a
patch is published tomorrow, nothing anywhere would ever say so again. So
the sweep now reads dismissed alerts too and brings one back the moment a
patched version exists, but only when the dismissal was a judgement about
the cost of fixing (`tolerable_risk`, `no_bandwidth`, `fix_started`)
rather than about whether the vulnerability applies at all (`not_used`,
`inaccurate`) -- see `_REVIVING_DISMISSALS`.

**Every repo is read twice, over two different routes -- Cycle 548.** The
per-repo sweep printed "No open security alerts" at 21:33 on 2026-08-27
with `SokratesAI/agora` on its answered list, while that repo's open
high-severity `brace-expansion` alert was there the whole time and came
back on eleven later calls. I never reproduced the empty answer and so
name no cause; what is certain is that `alerts_for` returned exit 0 with
an empty list, and that is byte-for-byte what a clean repo looks like. A
false clean is the worst thing this instrument can do, so `org_alerts`
now reads `/orgs/{org}/dependabot/alerts` -- the same alerts, a different
route -- and `cross_check` reports any alert one view has and the other
does not, in either direction. It never reads as clean.

Exit status: 0 when every repo answered and nothing needs acting on, 1
when anything was unreadable, alerts are disabled somewhere, or the two
views disagree, 2 when there is an open alert whose fix is not already on
the default branch. A disagreement is deliberately 1 rather than 2: it
does not say there is something to fix, it says this instrument's answer
cannot be believed right now, which is exactly what `disabled` means. So
a cycle can read the status without parsing the text, and "I could not
check" never reads as "nothing here". Failing to enumerate an org is a 1
for the same reason a `disabled` repo is: the sweep was smaller than it
claims, and a smaller sweep must never print as a clean one.
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

# `gh repo list` takes a limit and gives no "there were more" signal, so the
# only way to notice a truncated org is that the count came back exactly at
# the limit. Far above any real org here (27 today), and checked rather than
# assumed, because a silent truncation is the one failure this module is for.
_ORG_PAGE_LIMIT = 500


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
            f"repos/{repo}/dependabot/alerts?state=open,dismissed&per_page=100",
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
    return OK, [a for a in (_summarise(x) for x in payload) if _still_counts(a)]


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
        # GitHub's own per-repo alert number. Nothing prints it; it is the
        # only stable identity an alert has, so it is what the org-level
        # cross-check below matches the two views on.
        "number": alert.get("number"),
        "severity": (advisory.get("severity") or "unknown").lower(),
        "package": package.get("name") or "?",
        "ecosystem": package.get("ecosystem") or "?",
        "summary": advisory.get("summary") or "(no summary)",
        "manifest": (alert.get("dependency") or {}).get("manifest_path") or "?",
        "patched": patched.get("identifier") or "none published",
        "url": alert.get("html_url") or "",
        "state": (alert.get("state") or "open").lower(),
        "dismissed_reason": (alert.get("dismissed_reason") or "").lower(),
    }


# Dismissal reasons a published patch actually overturns. Cycle 457 wrote
# this after dismissing two `image-size` advisories on `sokrates-docs`
# that had no patch to apply -- 2.0.2 was the newest release and both
# advisories were open against it, so the tool exited 2 every cycle and
# there was nothing any cycle could do about it. Dismissing them is
# honest; dismissing them *silently and forever* is not, because the
# reason they were tolerable was partly that no fix existed, and nothing
# on GitHub reopens a dismissed alert when one is published.
#
# So the split is by what the dismissal was actually a judgement about.
# `tolerable_risk`, `no_bandwidth` and `fix_started` are judgements about
# the *cost of fixing*, and a published patch is exactly the fact that
# changes that cost -- those come back. `not_used` and `inaccurate` are
# judgements about whether the vulnerability applies here at all, and a
# patch says nothing about that, so re-raising them would be noise
# against a decision that is still correct. Anything GitHub adds later
# that this set does not name stays dismissed, which is the quiet
# direction; that is deliberate, because a reason nobody has reasoned
# about should not start raising the exit status on its own.
_REVIVING_DISMISSALS = {"tolerable_risk", "no_bandwidth", "fix_started"}


def _still_counts(alert):
    """Is this alert something a cycle should still be shown?

    Open alerts always. A dismissed one only once a patch it could apply
    exists and the dismissal was about the cost of applying it.
    """
    if alert["state"] == "open":
        return True
    if alert["state"] != "dismissed":
        return False
    if alert["patched"] == "none published":
        return False
    return alert["dismissed_reason"] in _REVIVING_DISMISSALS


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
            # A revived alert reads as a brand-new finding otherwise, and
            # the next cycle would re-derive the whole judgement that was
            # already made and written on the dismissal.
            if alert.get("state") == "dismissed":
                lines.append(
                    "      previously dismissed as "
                    f"{alert.get('dismissed_reason') or 'unknown'} — back because "
                    f"{alert['patched']} is now published; read the "
                    "dismissal comment before deciding again"
                )
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

    # One line for all of them, with every name on it. Sweeping the whole
    # org turned this from two repos into sixteen, and sixteen copies of
    # the same sentence is a wall a cycle skims -- the names are the data
    # and none of them may be dropped, so the fix is the layout, never a
    # cap or a count. (Cycle 432; `personality.md`, "I don't limit myself
    # to make things tidy".)
    disabled = [repo for repo, state, _ in unreadable if state == DISABLED]
    if disabled:
        lines.append(
            f"⚠ Dependabot alerts are DISABLED on {len(disabled)} repo(s) — "
            "this is not zero alerts, it is no instrument. Nobody can see a "
            "vulnerability in these: " + ", ".join(disabled)
        )
    for repo, state, payload in unreadable:
        if state != DISABLED:
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


def repos_in_org(org, run=None):
    """`(live, error, archived)` -- every repo in one GitHub org, split.

    Archived repos are left out on purpose and counted by the caller: they
    are read-only, so an alert on one cannot be fixed by a pull request and
    would sit in the actionable list forever. That is a judgement, not a
    measurement, so it is said out loud in the report rather than hidden.
    """
    code, out, err = (run or _gh)(
        [
            "repo",
            "list",
            org,
            "--limit",
            str(_ORG_PAGE_LIMIT),
            "--json",
            "nameWithOwner,isArchived",
        ]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return [], blob.splitlines()[0] if blob else f"gh exited {code}", []
    try:
        payload = json.loads(out)
    except ValueError:
        return [], "gh returned something that is not JSON", []
    if not isinstance(payload, list):
        return [], "gh returned a JSON object where a list of repos was expected", []
    if len(payload) >= _ORG_PAGE_LIMIT:
        return (
            [],
            f"the org listing came back at the {_ORG_PAGE_LIMIT}-repo limit, "
            "so it may be truncated and this sweep cannot claim to cover it",
            [],
        )
    live, archived = [], []
    for entry in payload:
        name = (entry or {}).get("nameWithOwner")
        if not name:
            continue
        (archived if entry.get("isArchived") else live).append(name)
    return sorted(live), None, sorted(archived)


# GitHub answers the org-wide alert list from the same store as the
# per-repo one, but it is a different request against a different route, so
# the two agreeing is real evidence and the two disagreeing is real evidence
# that one of them is lying. Same "exactly at the limit" reasoning as
# `_ORG_PAGE_LIMIT`: this route pages at 100 and gives no "there were more"
# signal, so a full page is treated as a failed read rather than a complete
# one.
_ORG_ALERT_PAGE = 100


def org_alerts(org, run=None):
    """`(state, alerts_or_message)` for a whole org in one request.

    Each alert carries the `repo` it belongs to alongside the same fields
    `_summarise` produces, so it can be compared to the per-repo sweep
    without a second shape to keep in step.
    """
    code, out, err = (run or _gh)(
        [
            "api",
            f"orgs/{org}/dependabot/alerts"
            f"?state=open,dismissed&per_page={_ORG_ALERT_PAGE}",
        ]
    )
    if code != 0:
        blob = (err or out or "").strip()
        return ERROR, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return ERROR, "gh returned something that is not JSON"
    if not isinstance(payload, list):
        return ERROR, "gh returned a JSON object where a list of alerts was expected"
    if len(payload) >= _ORG_ALERT_PAGE:
        return ERROR, (
            f"the org alert list came back at exactly {_ORG_ALERT_PAGE} alerts, "
            "so it is truncated and cannot be compared"
        )
    out_alerts = []
    for raw in payload:
        repo = ((raw.get("repository") or {}).get("full_name") or "").strip()
        if not repo:
            return ERROR, "an org alert carried no repository name"
        summary = _summarise(raw)
        if not _still_counts(summary):
            continue
        summary["repo"] = repo
        out_alerts.append(summary)
    return OK, out_alerts


def cross_check(results, org_views):
    """`(lines, disagreed)` -- do the two views of the same alerts agree?

    **This exists because the per-repo sweep was caught printing a clean
    answer for a repo that had an open high-severity alert.** At 21:33 on
    2026-08-27 `tools.security_alerts` printed "No open security alerts"
    with `SokratesAI/agora` on the answered list, while
    `SokratesAI/agora`'s `brace-expansion` alert was open and stayed open
    through eleven later calls that all reported it. I could not reproduce
    the empty answer, so I am not naming a cause -- what I can say is that
    `alerts_for` returned exit 0 and a list with nothing in it, and that
    path is indistinguishable from a genuinely clean repo. A false clean on
    the one instrument that watches this surface is the worst failure it
    has, so the fix is a second reading rather than a guess at the first.

    Only alerts on repos this sweep actually read are compared. An org-wide
    alert on an archived repo, or on one whose alerts are disabled for this
    token, is not a disagreement -- it is a repo that was never in the
    sweep, which the report already says elsewhere.
    """
    lines, disagreed = [], False
    for org in sorted(org_views):
        state, payload = org_views[org]
        if state != OK:
            lines.append(
                f"⚠ {org}: COULD NOT CROSS-CHECK THE ORG ALERT LIST — {payload}. "
                "The per-repo sweep below is unconfirmed."
            )
            disagreed = True
            continue
        readable = {
            repo for repo in results
            if results[repo][0] == OK and repo.split("/")[0] == org
        }
        seen = {
            (repo, alert.get("number"))
            for repo in readable
            for alert in results[repo][1]
        }
        from_org = {
            (alert["repo"], alert.get("number"))
            for alert in payload
            if alert["repo"] in readable
        }
        for repo, number in sorted(from_org - seen, key=lambda k: (k[0], k[1] or 0)):
            lines.append(
                f"⚠ {repo}: the org-wide alert list reports alert #{number} and "
                "the per-repo sweep did not. The per-repo answer for this repo "
                "is wrong, and a clean line for it means nothing."
            )
            disagreed = True
        for repo, number in sorted(seen - from_org, key=lambda k: (k[0], k[1] or 0)):
            lines.append(
                f"⚠ {repo}: the per-repo sweep reports alert #{number} and the "
                "org-wide list did not. The two disagree, so neither is trusted."
            )
            disagreed = True
    return lines, disagreed


def _orgs_from_workspace():
    """The org names this workspace's checkouts belong to, sorted."""
    checkouts, _unplaceable = _repos_from_workspace()
    return sorted({r.split("/")[0] for r in checkouts if "/" in r})


def _repos_to_sweep(run=None):
    """`(repos, unplaceable, notes, incomplete)` -- what this sweep covers.

    **The blind spot this replaces cost four high-severity alerts.** Until
    Cycle 432 the default was `_repos_from_workspace()` alone, so the sweep
    saw the three or four repos this loop happens to clone and reported
    "nothing to act on" for the org. `SokratesAI/sokrates-docs` had six open
    Dependabot alerts at that moment, four of them high, and the only cycle
    that ever saw them was one that pushed to the repo and read a `remote:`
    line in passing -- which is the exact accidental-noticing this whole
    module exists to replace, reappearing one level up.

    The owners are still derived from the checkouts rather than hardcoded,
    which keeps the property the docstring above argues for: nothing here
    names an org, so a new org shows up the moment a clone of one does. The
    workspace repos are then unioned back in, so a checkout of a repo
    outside any of those orgs cannot stop being swept by this change.
    """
    checkouts, unplaceable = _repos_from_workspace()
    orgs = sorted({r.split("/")[0] for r in checkouts if "/" in r})
    repos, notes, incomplete = set(checkouts), [], False
    for org in orgs:
        found, error, archived = repos_in_org(org, run=run)
        if error:
            notes.append(
                f"⚠ {org}: COULD NOT LIST THE ORG — {error}. Swept only the "
                "repos with a checkout here, which is a smaller sweep than "
                "this tool claims to do."
            )
            incomplete = True
            continue
        repos.update(found)
        note = f"Swept {org}: {len(found)} repo(s) in the org"
        if archived:
            note += (
                f", plus {len(archived)} archived one(s) left out — read-only, "
                "so an alert there cannot be closed by a pull request"
            )
        notes.append(note + ".")
    if not orgs:
        notes.append(
            "⚠ No org to enumerate — no checkout here named one, so this is "
            "the workspace sweep only."
        )
        incomplete = True
    return sorted(repos), unplaceable, notes, incomplete


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        action="append",
        default=None,
        help="owner/name; repeatable. Defaults to every non-archived repo in "
        "every org this workspace has a checkout of.",
    )
    args = parser.parse_args(argv)

    unplaceable, notes, incomplete = [], [], False
    if args.repo:
        repos = args.repo
    else:
        repos, unplaceable, notes, incomplete = _repos_to_sweep()

    results = {repo: alerts_for(repo) for repo in repos}
    verify_landed(results)
    # Same rule as the sweep itself: the orgs are whatever this workspace's
    # checkouts name, so nothing here hardcodes one and an owner that is a
    # user rather than an org never gets asked an org-only question.
    org_views = {org: org_alerts(org) for org in _orgs_from_workspace()}
    disagreement, disagreed = cross_check(results, org_views)
    lines, code = format_report(results)
    for line in lines:
        print(line)
    for line in disagreement:
        print(line)
    for note in notes:
        print(note)
    for clone in unplaceable:
        print(f"⚠ {clone}: could not place this checkout on GitHub, not swept")
    if incomplete:
        # A sweep that could not build its own repo list is not a clean
        # sweep, whatever the repos it did reach answered. Same rule as
        # `disabled` above: no instrument never reads as no alerts.
        code = max(code, 1)
    if disagreed:
        # Deliberately 1 and not 2. A disagreement does not say there is an
        # alert to fix, it says this instrument's answer cannot be believed
        # right now -- which is the same thing `disabled` and `COULD NOT
        # READ` mean, and they are already 1.
        code = max(code, 1)
    return code


if __name__ == "__main__":
    sys.exit(main())
