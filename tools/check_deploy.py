"""Answer whether what is on `main` is actually what is running.

Cycle 222, from a live incident. Cycle 221 merged runner#212 at 19:20
Oslo and its build reported `failure`: `test`, `vault-drift` and
`build-push` all passed, and the fourth job never started at all --

    The job was not started because recent account payments have failed
    or your spending limit needs to be increased.

That fourth job was `update-manifest`, the one that writes the new image
digest into `agora-persona-runner-config`. So the image was built and
pushed to the registry, and nothing on earth pointed at it. ArgoCD kept
serving the previous digest, the pod stayed on the previous code, and
every check a cycle actually runs was green or absent:

  - `gh pr checks` on the PR passes -- the manifest write only happens
    on the push to `main`, after the merge.
  - `kubectl get pods -n agents` shows a healthy pod. It is healthy. It
    is running last cycle's code.
  - `kubectl logs ... | grep -i traceback` finds nothing, because
    nothing crashed.

**That job no longer exists, and the check is not thereby retired.**
Cycle 224 folded `update-manifest` into `build-push` as its last step,
so a manifest write that fails now reddens a job somebody is watching
rather than one nothing was. What it did not do is make the manifest
write unskippable: the step still runs after a push, still needs an
installation token and a push to a second repo, and a cycle that merges
and then reads `gh pr checks` is still reading a check that ran before
any of that. So the three facts below still live in three systems, and
this is still the only command that puts two of them side by side. The
specific 2026-08-15 shape -- a whole job refused at startup over
billing -- is history; the gap it exposed is not.

The handoff asks each cycle to "confirm the deploy came up healthy",
and the honest answer for runner#212 was that it never would. Nothing
in this loop compares the three facts that would have said so, because
they live in three different systems and no single command shows two of
them side by side. That is the same shape as `top_board_rows`: the
information existed, and was never once put next to the decision it was
supposed to change.

    python3 -m tools.check_deploy

`verdict` is pure and takes the four facts; `main` gathers them with
`gh` and `kubectl`, which exist in the bridge pod. The split is so the
interesting half is testable without a cluster.
"""

import argparse
import json
import subprocess
import sys

REPO = "SokratesAI/agora-persona-runner"
CONFIG_REPO = REPO + "-config"
PACKAGE = "agora-persona-runner"

# Both deployments run the same image off the same `image:` line, which is
# why the manifest carries a comment saying so. Reading them separately is
# what makes a partial rollout visible instead of averaged away.
DEPLOYMENTS = ("agora-persona-runner", "nova-site")

IN_SYNC = "IN SYNC"
ROLLOUT_PENDING = "ROLLOUT PENDING"
NOT_DEPLOYED = "NOT DEPLOYED"
NOT_BUILT = "NOT BUILT"


def verdict(tip_sha, tip_digest, manifest_digest, deployed):
    """Compare main's tip against the manifest and the cluster.

    `tip_digest` is the digest of the image tagged `sha-<tip_sha>`, or
    None when the registry holds no image for that commit. `deployed`
    maps deployment name to the digest it is running; a value of None
    means the deployment could not be read, which is reported rather
    than treated as agreement.

    Returns `(state, lines)`. Only `NOT_BUILT` and `NOT_DEPLOYED` are
    faults -- `ROLLOUT_PENDING` is the expected reading for a cycle that
    merged minutes ago, and calling that a failure would make the check
    cry wolf on exactly the cycles that did the right thing.
    """
    lines = []
    if tip_digest is None:
        lines.append(
            f"{NOT_BUILT}: main is at {tip_sha} and the registry has no image "
            f"tagged sha-{tip_sha}. The build either failed before build-push "
            f"or is still running. Check `gh run list --repo {REPO} "
            f"--branch main --limit 3 --json conclusion,createdAt`."
        )
        return NOT_BUILT, lines

    if manifest_digest != tip_digest:
        lines.append(
            f"{NOT_DEPLOYED}: main is at {tip_sha}, whose image is "
            f"{_short(tip_digest)}, but {CONFIG_REPO}/manifest.yaml still pins "
            f"{_short(manifest_digest)}. The image exists and nothing points at "
            f"it, so the merged code will never reach the cluster on its own."
        )
        lines.append(
            "  This is what a manifest write that never happened looks like -- "
            "a refused job before Cycle 224, a failed last step of `build-push` "
            "after it. Either way it is not visible in `gh pr checks`, in pod "
            "status, or in the logs. Check the `build-push` job on main's "
            "newest run, then fix it by hand: commit the digest above to "
            f"{CONFIG_REPO}/manifest.yaml, which is all the step does."
        )
        return NOT_DEPLOYED, lines

    stale = {n: d for n, d in deployed.items() if d != manifest_digest}
    if stale:
        for name, dig in sorted(stale.items()):
            seen = "could not be read" if dig is None else _short(dig)
            lines.append(
                f"{ROLLOUT_PENDING}: deployment {name} is on {seen}, manifest "
                f"pins {_short(manifest_digest)}."
            )
        lines.append(
            "  Expected for a few minutes after a merge, and expected for the "
            "whole cycle if you are the one draining agora-persona-runner. If "
            "it is still pending on the NEXT cycle, ArgoCD has not synced."
        )
        return ROLLOUT_PENDING, lines

    lines.append(
        f"{IN_SYNC}: main {tip_sha} -> {_short(tip_digest)}, manifest and "
        f"{len(deployed)} deployment(s) all agree."
    )
    return IN_SYNC, lines


def _short(digest):
    if digest is None:
        return "unknown"
    body = digest.split(":", 1)[-1]
    return body[:12]


def _run(cmd):
    """Return stdout, or None if the command failed.

    A command that fails must not read as an answer -- `verdict` treats
    None as "could not read" for exactly this reason.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip()


def tip_sha():
    return _run(["gh", "api", f"repos/{REPO}/commits/main", "--jq", ".sha"])


def digest_for_tag(tag):
    """Digest of the package version carrying `tag`, or None."""
    raw = _run([
        "gh", "api", f"/orgs/SokratesAI/packages/container/{PACKAGE}/versions",
        "--paginate",
    ])
    if raw is None:
        return None
    try:
        versions = json.loads(raw)
    except ValueError:
        return None
    for v in versions:
        tags = v.get("metadata", {}).get("container", {}).get("tags", [])
        if tag in tags:
            return v.get("name")
    return None


def manifest_digest():
    """The digest pinned in the config repo, or None if the two
    `image:` lines disagree -- which is itself a fault worth seeing."""
    raw = _run([
        "gh", "api", f"repos/{CONFIG_REPO}/contents/manifest.yaml",
        "--jq", ".content",
    ])
    if raw is None:
        return None
    import base64
    try:
        text = base64.b64decode(raw).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    found = set()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("image:") and "@sha256:" in line:
            found.add(line.split("@", 1)[1])
    if len(found) != 1:
        return None
    return found.pop()


def deployed_digests():
    out = {}
    for name in DEPLOYMENTS:
        img = _run([
            "kubectl", "get", "deploy", name, "-n", "agents", "-o",
            "jsonpath={.spec.template.spec.containers[0].image}",
        ])
        out[name] = img.split("@", 1)[1] if img and "@" in img else None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tip", help="override main's tip sha")
    args = ap.parse_args(argv)

    sha = args.tip or tip_sha()
    if not sha:
        print("COULD NOT READ: main's tip sha. Check `gh auth status`.")
        return 1
    short = sha[:7]
    state, lines = verdict(
        short, digest_for_tag(f"sha-{short}"), manifest_digest(),
        deployed_digests(),
    )
    print("\n".join(lines))
    return 1 if state in (NOT_BUILT, NOT_DEPLOYED) else 0


if __name__ == "__main__":
    raise SystemExit(main())
