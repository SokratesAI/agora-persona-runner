"""Open code-scanning and secret-scanning alerts across this loop's GitHub orgs.

Cycle 1114. `tools.security_alerts` reads `dependabot/alerts` and nothing
else, so two of GitHub's three alert classes have no reader here at all.
That is the same accidental-noticing failure that module was built to end,
one class over: cycle 1055 turned CodeQL on for `SokratesAI/marcus`, saw two
alerts because it happened to be looking at that repo's Security tab, fixed
them, and no step in `prompt.md` would have shown a third one to anybody.

The measurement that made this a cycle's work rather than a note: sweeping
all 26 non-archived repos by hand found **one open secret-scanning alert on
`SokratesAI/agora-persona-runner`, this loop's own source, opened
2026-08-16 and still open 22 days later**. A leaked credential is the
highest-severity thing this platform can produce and it was the one class
with no instrument.

    python3 -m tools.scanning_alerts

**The alert body is never printed, and a test fails by name if it is.**
`GET secret-scanning/alerts` returns the matched secret in a `secret` field.
Printing it would copy a live credential into a journal entry, a PR body and
this loop's own transcript -- the exact move `credential-read-as-evidence`
warns about. Everything here is metadata: the type's display name, the
number, the date, the URL to look at it on GitHub, and GitHub's own validity
verdict.

**Four outcomes per repo per class, and keeping them apart is the design.**
`ok` means the API answered and the count is real. `off` means the scanner
is not available on that repo -- GitHub answers *"Advanced Security must be
enabled"* or *"Secret scanning is disabled"* -- which is **no instrument**,
not zero alerts. `no-analysis` means code scanning is available and nothing
has ever run, which is also no instrument and reads differently from `off`
because the fix is different. `error` is anything else.

**`off` on a private repo does not raise, and that is a measurement rather
than a shrug.** Both scanners need paid GitHub Advanced Security on a private
repo and are free on a public one. Swept 2026-09-07: all six public repos
have secret scanning on, all twenty private ones have it off, and the two
sets are exactly the public/private split. So `off` on a private repo is
GitHub's price list, which no pull request closes -- the same call
`agentic_health` makes about a run GitHub refused to start for billing, and
the same reason: a finding that fires every sweep and can never be closed is
one nobody reads. `off` or `no-analysis` on a **public** repo is free to fix
and does raise.
"""

import argparse
import json
import subprocess
import sys

from tools.security_alerts import _repos_to_sweep

#: The two classes swept. `path` is the REST collection, `label` is what a
#: report line calls it, and `off_markers` are the substrings GitHub answers
#: with when the scanner is not available -- matched rather than the HTTP
#: status, because code scanning answers 403 for "not enabled" and 404 for
#: "enabled but nothing analysed", and those two mean different things.
CLASSES = {
    "code": {
        "path": "code-scanning/alerts?state=open&per_page=100",
        "label": "code scanning",
        "off_markers": ("advanced security must be enabled",),
        "empty_markers": ("no analysis found",),
    },
    "secret": {
        "path": "secret-scanning/alerts?state=open&per_page=100",
        "label": "secret scanning",
        "off_markers": ("secret scanning is disabled",),
        "empty_markers": (),
    },
}

OK = "ok"
OFF = "off"
NO_ANALYSIS = "no-analysis"
ERROR = "error"

#: Fields copied out of an alert. `secret` is deliberately absent from both
#: lists; see the module docstring.
_CODE_FIELDS = ("number", "created_at", "html_url")
_SECRET_FIELDS = ("number", "created_at", "html_url", "validity")


def _gh(args):
    """Run `gh` and return `(exit_code, stdout, stderr)`."""
    proc = subprocess.run(["gh"] + args, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def _summarise(kind, alert):
    """One alert, reduced to what may be printed.

    Built by naming the fields to keep rather than the fields to drop, so a
    field GitHub adds later cannot arrive in the report unread. That matters
    here more than it usually would: the field this must never print is on
    the secret-scanning payload today.
    """
    if kind == "secret":
        out = {k: alert.get(k) for k in _SECRET_FIELDS}
        out["what"] = alert.get("secret_type_display_name") or alert.get("secret_type")
        return out
    out = {k: alert.get(k) for k in _CODE_FIELDS}
    rule = alert.get("rule") or {}
    out["what"] = rule.get("description") or rule.get("id") or "?"
    out["severity"] = (rule.get("security_severity_level") or rule.get("severity") or "").lower()
    return out


def alerts_for(repo, kind, run=None):
    """`(state, alerts_or_message)` for one `owner/name` and one class.

    `run` resolves to `_gh` at call time rather than as a default argument:
    a default binds once at import, so monkeypatching `_gh` would leave this
    calling the real `gh` while the test believed otherwise.
    """
    spec = CLASSES[kind]
    code, out, err = (run or _gh)(["api", f"repos/{repo}/{spec['path']}"])
    if code != 0:
        blob = (err or out or "").strip()
        low = blob.lower()
        for marker in spec["off_markers"]:
            if marker in low:
                return OFF, f"{spec['label']} is not available on this repository"
        for marker in spec["empty_markers"]:
            if marker in low:
                return NO_ANALYSIS, f"{spec['label']} is on and nothing has ever been analysed"
        return ERROR, blob.splitlines()[0] if blob else f"gh exited {code}"
    try:
        payload = json.loads(out)
    except ValueError:
        return ERROR, "gh returned something that is not JSON"
    if not isinstance(payload, list):
        return ERROR, "gh returned a JSON object where a list of alerts was expected"
    return OK, [_summarise(kind, a) for a in payload]


def repo_visibility(repos, run=None):
    """`{repo: True if public}` for the repos swept, from GitHub.

    Read rather than inferred, because the whole `off`-does-not-raise rule
    below turns on it and "we know which of ours are public" is exactly the
    kind of hardcoded list this repo has already had go stale twice. A repo
    the call cannot place is treated as public, so an unknown repo fails
    towards raising rather than towards silence.
    """
    seen = {}
    for repo in repos:
        code, out, _err = (run or _gh)(["api", f"repos/{repo}", "--jq", ".private"])
        if code != 0:
            seen[repo] = True
            continue
        seen[repo] = out.strip() != "true"
    return seen


def sweep(repos, run=None, visibility=None):
    """`{repo: {kind: (state, payload)}}` plus the public/private map."""
    public = visibility if visibility is not None else repo_visibility(repos, run=run)
    results = {}
    for repo in repos:
        results[repo] = {k: alerts_for(repo, k, run=run) for k in CLASSES}
    return results, public


def _findings(results, public):
    """`(open_alerts, missing_free, unreadable)` -- the three raising sets.

    `missing_free` is a public repo whose scanner is off or has never run:
    free on a public repo, so it is a gap somebody can close. A private repo
    in the same state is GitHub's price list and is not in here.
    """
    open_alerts, missing_free, unreadable = [], [], []
    for repo in sorted(results):
        for kind in CLASSES:
            state, payload = results[repo][kind]
            if state == OK and payload:
                open_alerts.append((repo, kind, payload))
            elif state == ERROR:
                unreadable.append((repo, kind, payload))
            elif state in (OFF, NO_ANALYSIS) and public.get(repo, True):
                missing_free.append((repo, kind, state))
    return open_alerts, missing_free, unreadable


def format_report(results, public, notes=(), incomplete=False):
    """`(lines, exit_code)`."""
    open_alerts, missing_free, unreadable = _findings(results, public)
    lines = []
    if open_alerts:
        lines.append(
            f"OPEN ALERT — {len(open_alerts)} repo/class pair(s) carry an alert "
            "GitHub still considers open."
        )
        for repo, kind, payload in open_alerts:
            lines.append(f"  {repo}  {CLASSES[kind]['label']} — {len(payload)} open")
            for alert in payload:
                bits = [f"#{alert.get('number')}", str(alert.get("what"))]
                if alert.get("severity"):
                    bits.append(alert["severity"])
                if alert.get("validity"):
                    bits.append(f"validity {alert['validity']}")
                lines.append(f"      {' | '.join(bits)}  opened {alert.get('created_at')}")
                lines.append(f"      {alert.get('html_url')}")
        lines.append(
            "  The alert body is deliberately not printed — read it on GitHub. "
            "Copying a matched secret into a report is how a live credential "
            "ends up in a journal entry."
        )
    if missing_free:
        lines.append(
            f"NO INSTRUMENT — {len(missing_free)} scanner(s) are off on a PUBLIC "
            "repo, where they are free."
        )
        for repo, kind, state in missing_free:
            lines.append(f"  {repo}  {CLASSES[kind]['label']} — {state}")
    if unreadable:
        lines.append(f"UNREADABLE — {len(unreadable)} call(s) failed.")
        for repo, kind, message in unreadable:
            lines.append(f"  {repo}  {CLASSES[kind]['label']} — {message}")

    off_private = sorted(
        f"{repo} {CLASSES[kind]['label']}"
        for repo in results
        for kind in CLASSES
        if results[repo][kind][0] in (OFF, NO_ANALYSIS) and not public.get(repo, True)
    )
    if off_private:
        lines.append(
            f"Not raised: {len(off_private)} scanner(s) are off on a private repo, "
            "where GitHub charges for Advanced Security. No pull request closes "
            "that, so it is reported and not counted."
        )
    clean = sorted(
        repo
        for repo in results
        if all(results[repo][k][0] == OK and not results[repo][k][1] for k in CLASSES)
    )
    if clean:
        lines.append(
            f"Clean and instrumented: {len(clean)} repo(s) answered on both "
            f"classes with no open alert — {', '.join(clean)}."
        )
    lines.extend(notes)
    lines.append(
        f"Swept {len(results)} repo(s) across {len(CLASSES)} alert class(es): "
        + ", ".join(CLASSES[k]["label"] for k in CLASSES)
        + ". Dependabot is tools.security_alerts and is not repeated here."
    )
    if not (open_alerts or missing_free or unreadable):
        lines.insert(0, "Nothing to act on.")

    if open_alerts or missing_free:
        return lines, 2
    if unreadable or incomplete:
        return lines, 1
    return lines, 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo",
        action="append",
        default=None,
        help="sweep only this owner/name; repeatable. Default is every "
        "non-archived repo in every org this workspace's checkouts name.",
    )
    args = parser.parse_args(argv)

    if args.repo:
        repos, notes, incomplete = sorted(args.repo), [], False
    else:
        repos, unplaceable, notes, incomplete = _repos_to_sweep()
        notes = list(notes)
        for clone in unplaceable:
            notes.append(f"⚠ {clone}: a checkout here whose origin could not be placed.")

    results, public = sweep(repos)
    lines, code = format_report(results, public, notes=notes, incomplete=incomplete)
    print("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
