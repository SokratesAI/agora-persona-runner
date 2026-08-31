"""github_read (read-only gh) and create_pr/github_comment/merge_pr (real GitHub writes via the bot account)."""

import base64
import json
import os
import subprocess
import time
import urllib.parse

from agora_runner.config import GITHUB_READONLY_TOKEN, GITHUB_BOT_TOKEN, GITHUB_ORG
from agora_runner.log import log, debug_log
from agora_runner.http_util import http_json


GITHUB_ALLOWED_SUBCOMMANDS = {
    "issue": {"list", "view"},
    "pr": {"list", "view", "diff", "checks"},
    "repo": {"view", "list"},
    "run": {"list", "view"},
    "workflow": {"list", "view"},
    "release": {"list", "view"},
    "api": {"GET"},  # subcommand here is the request method, enforced GET-only below
}
GITHUB_FORBIDDEN_FLAG_PREFIXES = ("--method", "-x", "--input")


def github_read(args):
    if not isinstance(args, dict):
        return "[gh: invalid arguments]"
    command = str(args.get("command", "")).strip().lower()
    sub = str(args.get("subcommand", "")).strip()
    extra = args.get("args") or []
    if command not in GITHUB_ALLOWED_SUBCOMMANDS:
        return f"[gh: command {command!r} not allowed -- only {sorted(GITHUB_ALLOWED_SUBCOMMANDS)}]"
    if not isinstance(extra, list):
        return "[gh: 'args' must be a list of strings]"
    for flag in extra:
        if str(flag).lower().startswith(GITHUB_FORBIDDEN_FLAG_PREFIXES):
            return "[gh: only read (GET) requests are allowed through this tool]"
    if command == "api":
        # sub is the HTTP path here, not a subcommand -- method is fixed GET.
        cmd = ["gh", "api", sub, "--method", "GET"] + [str(a) for a in extra]
    else:
        allowed_subs = GITHUB_ALLOWED_SUBCOMMANDS[command]
        if sub not in allowed_subs:
            return f"[gh: '{command} {sub}' not allowed -- only {sorted(allowed_subs)}]"
        cmd = ["gh", command, sub] + [str(a) for a in extra]
    if not GITHUB_READONLY_TOKEN:
        log("github_read: GITHUB_READONLY_TOKEN not set -- refusing (was the repo-read-token secret ever mounted here?)")
        return "[gh: no token configured (GITHUB_READONLY_TOKEN not set)]"
    env = dict(os.environ)
    env["GH_TOKEN"] = GITHUB_READONLY_TOKEN
    debug_log(f"github_read: running {cmd}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20, env=env)
    except FileNotFoundError:
        log(f"github_read: binary not installed in this image (cmd={cmd})")
        return "[gh: binary not installed in this image]"
    except Exception as e:
        log(f"github_read: {cmd} raised {e}")
        return f"[gh error: {e}]"
    output = (result.stdout or "") + (result.stderr or "")
    # Always logged, not debug-gated -- same reasoning as kubectl_read: a
    # nonzero exit here usually means the token is invalid/expired/wrong
    # scope, not that the query itself was bad.
    if result.returncode != 0:
        log(f"github_read: {cmd} exited {result.returncode}: {output[:500]!r}")
    else:
        debug_log(f"github_read: {cmd} exited 0, {len(output)} chars output")
    return output[:8000] or "[no output]"


# --------------------------------------------------------------------------
# create_pr / merge_pr (2026-07-26, githubWrite/githubMerge) -- real GitHub
# writes via the bot account. Deliberately NOT git/gh-CLI-shaped: no git
# binary, no local clone, just GitHub's REST API directly (same approach
# platform-workers/drones/pr-drone already uses, simplified to
# one-commit-per-file via the Contents API since Agora personas write
# whole files, same shape as vault_write, not diffs/patches -- far more
# reliable for a model to produce than a valid unified diff). The scoping
# here isn't a repo allowlist or a narrower token (the owner's call: the bot
# account already has broad access and repo-scoping the tool wouldn't
# actually restrict what the token itself can do) -- it's that these two
# functions are hardcoded to exactly one sequence of calls each
# (branch/contents/pulls for create_pr; pulls/check-runs/merge for
# merge_pr), never an arbitrary request shaped by model output.
# --------------------------------------------------------------------------
def _github_api(method, path, body=None):
    if not GITHUB_BOT_TOKEN:
        return None, "no token configured (GITHUB_BOT_TOKEN not set)"
    headers = {
        "Authorization": f"Bearer {GITHUB_BOT_TOKEN}",
        "Accept": "application/vnd.github+json",
    }
    status, data = http_json(method, f"https://api.github.com{path}", body, headers, timeout=30)
    if status >= 400:
        return None, f"GitHub API {method} {path} -> HTTP {status}: {json.dumps(data)[:400]}"
    return data, None


def create_pr(repo, branch, files, commit_message, title, body="", base="main"):
    """Opens a PR from `branch` -> `base` on `repo`, writing `files`
    (list of {"path", "content"}) as whole-file commits via the Contents
    API. `branch` is always caller-supplied (the owner's call: it should
    reflect what the change actually is, not an opaque autogenerated
    slug) -- if it already exists, new commits land on its current tip
    rather than resetting it, so repeated calls accumulate. Returns the
    existing open PR for this branch instead of creating a duplicate."""
    if not repo or not branch or not files:
        return "[create_pr: repo, branch, and at least one file are required]"
    if branch in ("main", "master", base):
        return f"[create_pr: branch must not be the same as base ({base!r})]"

    repo_path = f"/repos/{GITHUB_ORG}/{repo}"

    base_ref, err = _github_api("GET", f"{repo_path}/git/ref/heads/{urllib.parse.quote(base)}")
    if err:
        return f"[create_pr: could not resolve base branch {base!r}: {err}]"
    base_sha = base_ref["object"]["sha"]

    _existing_branch, branch_err = _github_api(
        "GET", f"{repo_path}/git/ref/heads/{urllib.parse.quote(branch)}"
    )
    if branch_err:
        _created, create_err = _github_api(
            "POST", f"{repo_path}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha}
        )
        if create_err:
            return f"[create_pr: could not create branch {branch!r}: {create_err}]"

    for f in files:
        file_path = str(f.get("path", "")).lstrip("/")
        content = str(f.get("content", ""))
        if not file_path:
            return "[create_pr: every file needs a non-empty path]"
        existing, _existing_err = _github_api(
            "GET",
            f"{repo_path}/contents/{urllib.parse.quote(file_path)}?ref={urllib.parse.quote(branch)}",
        )
        existing_sha = existing.get("sha") if isinstance(existing, dict) else None
        put_body = {
            "message": commit_message,
            "content": base64.b64encode(content.encode("utf-8")).decode(),
            "branch": branch,
        }
        if existing_sha:
            put_body["sha"] = existing_sha
        _committed, put_err = _github_api(
            "PUT", f"{repo_path}/contents/{urllib.parse.quote(file_path)}", put_body
        )
        if put_err:
            return f"[create_pr: failed writing {file_path}: {put_err}]"

    existing_prs, list_err = _github_api(
        "GET", f"{repo_path}/pulls?head={GITHUB_ORG}:{urllib.parse.quote(branch)}&state=open"
    )
    if not list_err and existing_prs:
        pr = existing_prs[0]
        return f"pushed {len(files)} file(s) to existing PR #{pr['number']}: {pr['html_url']}"

    pr, pr_err = _github_api(
        "POST", f"{repo_path}/pulls", {"title": title, "body": body, "head": branch, "base": base}
    )
    if pr_err:
        return f"[create_pr: {len(files)} file(s) committed to {branch!r} but PR creation failed: {pr_err}]"
    return f"created PR #{pr['number']}: {pr['html_url']}"


def github_comment(repo, issue_number, body):
    """Posts a comment on an issue or a PR. One endpoint covers both:
    GitHub models every PR as an issue, so /issues/{n}/comments is the
    right call either way -- no pr-vs-issue branch to get wrong, and no
    extra API round-trip to find out which one `issue_number` is. (This
    is a review comment on the conversation thread, not an inline
    review comment on a diff line -- that's a different endpoint and a
    much larger tool.) Reuses githubWrite: a persona already trusted to
    open a PR is trusted to comment on one."""
    if not repo or not issue_number:
        return "[github_comment: repo and issue_number are required]"
    if not str(body or "").strip():
        return "[github_comment: body must not be empty]"

    result, err = _github_api(
        "POST", f"/repos/{GITHUB_ORG}/{repo}/issues/{issue_number}/comments", {"body": str(body)}
    )
    if err:
        return f"[github_comment: could not comment on {repo}#{issue_number}: {err}]"
    return f"commented on {repo}#{issue_number}: {result.get('html_url') or '?'}"


# GitHub's own verdict on whether a pull request may merge, read off
# `mergeable_state` on GET /pulls/{n}. Replicating the required-check rules
# by hand is what this replaces: merge_pr used to refuse on *any* red
# check-run, including ones the repo itself declares advisory, which blocked
# platform-config#580 on an advisory secret-scan that GitHub was happy to
# merge past.
MERGE_STATE_REFUSALS = {
    "dirty": "the branch conflicts with its base",
    "blocked": "a check or review this repo actually requires is not satisfied",
    "behind": "this repo requires the branch to be up to date with its base",
    "draft": "the pull request is still a draft",
}
# `unstable` is the load-bearing one: it means only non-required checks are
# unhappy, which is exactly the case GitHub allows and the old code refused.
MERGE_STATE_ALLOWED = ("clean", "unstable", "has_hooks")


def _merge_state(repo_path, pr_number, attempts, delay, sleep):
    """GitHub computes `mergeable_state` asynchronously, so a PR read seconds
    after it was opened answers `unknown` and means nothing yet. Ask again
    rather than treating the placeholder as a verdict."""
    pr = err = None
    for attempt in range(attempts):
        pr, err = _github_api("GET", f"{repo_path}/pulls/{pr_number}")
        if err:
            return None, None, err
        if pr.get("state") != "open":
            # A closed PR never gets a mergeable_state, so retrying is 6s spent
            # to reach a refusal the first answer already justified.
            return pr, pr.get("mergeable_state"), None
        if pr.get("mergeable_state") not in (None, "", "unknown"):
            return pr, pr["mergeable_state"], None
        if attempt < attempts - 1:
            sleep(delay)
    return pr, pr.get("mergeable_state") if pr else None, None


def merge_pr(repo, pr_number, merge_method="squash", _attempts=4, _delay=2.0, _sleep=time.sleep):
    """Merges an open PR once GitHub itself says it may merge, plus one
    guard GitHub cannot give us.

    The verdict is `mergeable_state`, not a hand-rolled pass over every
    check-run: only the repo knows which of its checks are required, and
    reimplementing that here refused merges GitHub would have allowed. No
    'did this bot/persona open it' check either -- every agent shares the
    same GitHub account (the owner's call), so that distinction carries
    zero signal.

    The guard GitHub cannot give us is the blind merge. `mergeable_state`
    reads `clean` both when CI has passed and when CI has not started yet,
    so a PR merged seconds after a push merges untested. So: a check-run
    that has not completed still refuses, and *zero* check-runs refuses too
    -- but only when the repo has an active Actions workflow that should
    have produced one. The `*-config` repos run no workflows at all, and
    refusing there was waiting for something that was never coming.

    Known narrow false positive, written down rather than coded around: a
    repo whose only workflows are `on: push` has active workflows and
    produces no PR check-run, so it lands in that refusal. The workflows
    API does not report triggers, and parsing every workflow's YAML to find
    out is more machinery than the case is worth."""
    repo_path = f"/repos/{GITHUB_ORG}/{repo}"
    pr, state, err = _merge_state(repo_path, pr_number, _attempts, _delay, _sleep)
    if err:
        return f"[merge_pr: could not fetch PR #{pr_number}: {err}]"
    if pr.get("state") != "open":
        return f"[merge_pr: PR #{pr_number} is not open (state={pr.get('state')})]"
    if state in (None, "", "unknown"):
        return (f"[merge_pr: GitHub has not finished computing whether PR #{pr_number} "
                f"can merge (mergeable_state={state!r}) -- try again shortly]")
    if state in MERGE_STATE_REFUSALS:
        return f"[merge_pr: GitHub says {state!r} -- {MERGE_STATE_REFUSALS[state]} -- refusing to merge]"
    if state not in MERGE_STATE_ALLOWED:
        return (f"[merge_pr: GitHub says {state!r}, which this tool does not recognise "
                f"-- refusing rather than guessing]")

    head_sha = pr["head"]["sha"]
    checks, err = _github_api("GET", f"{repo_path}/commits/{head_sha}/check-runs")
    if err:
        return f"[merge_pr: could not fetch check runs: {err}]"
    runs = checks.get("check_runs", [])
    pending = [r["name"] for r in runs if r.get("status") != "completed"]
    if pending:
        return f"[merge_pr: checks still running: {', '.join(pending)} -- try again shortly]"
    if not runs:
        workflows, err = _github_api("GET", f"{repo_path}/actions/workflows")
        if err:
            return (f"[merge_pr: no CI checks found for {head_sha[:7]} and I could not read "
                    f"this repo's workflows to find out whether that is expected: {err}]")
        active = [w["name"] for w in workflows.get("workflows", []) if w.get("state") == "active"]
        if active:
            return (f"[merge_pr: no CI checks found for {head_sha[:7]} but this repo has active "
                    f"workflow(s) ({', '.join(active)}) -- refusing to merge blind]")

    result, merge_err = _github_api(
        "PUT", f"{repo_path}/pulls/{pr_number}/merge", {"merge_method": merge_method}
    )
    if merge_err:
        return f"[merge_pr: merge failed: {merge_err}]"
    red = [r["name"] for r in runs if r.get("conclusion") not in ("success", "neutral", "skipped")]
    note = f", over {len(red)} non-required red check(s): {', '.join(red)}" if red else ""
    if not runs:
        note = ", this repo runs no workflows so there was no check to wait for"
    return f"merged PR #{pr_number} ({merge_method}), sha={(result.get('sha') or '?')[:7]}{note}"
