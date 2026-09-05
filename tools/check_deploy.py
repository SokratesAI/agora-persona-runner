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

    python3 -m tools.check_deploy                       # the runner
    python3 -m tools.check_deploy agora-claude-bridge   # any sibling

**Four repos share this deploy chain and only one of them was checked.**
`agora-claude-bridge`, `agora` and `sokrates-docs` build the way this
repo does, write a digest into a paired `-config` repo, and are picked up
by the same ArgoCD -- so the 2026-08-15 failure above is available to all
four and was detectable in none of them but this one. Cycle 258 filed
that; Cycle 295 parameterised the one tool rather than copying it, which
is the whole point: a second copy is a second thing to keep true.

**`vault-bridge` is the fifth and it is deliberately not one of those
four.** It has no `-config` repo; its manifest lives in `platform-config`
and it is the last repo still on the older path. Running the tool against
it is still worth doing and answers honestly rather than pretending --
that is what `NO_MANIFEST` is for. Do not read the paragraph above as a
uniformity this org has; it does not.

The generalisation needs no lookup table, and that matters more than it
sounds. A repo name is all four facts: `SokratesAI/<name>` is the repo,
`<name>-config` is where the manifest lives, `<name>` is the GHCR
package, and **the deployments are whichever ones in the cluster run
that package's image** -- discovered, not listed, in every namespace. A
table keyed on names would have been wrong on the first repo that needed
it, twice over: the deployment running `ghcr.io/sokratesai/vault-bridge`
in `agents` is called `newspaper`, and there is a *second* one in
`obsidian` on a different digest, which is the one `platform-config`
actually pins.

`verdict` is pure and takes a `Target` plus the four facts; `main`
gathers them with `gh` and `kubectl`, which exist in the bridge pod. The
split is so the interesting half is testable without a cluster.
"""

import argparse
import json
import subprocess
from typing import NamedTuple

ORG = "SokratesAI"
REGISTRY_OWNER = "sokratesai"
DEFAULT_REPO = "agora-persona-runner"

IN_SYNC = "IN SYNC"
ROLLOUT_PENDING = "ROLLOUT PENDING"
NOT_DEPLOYED = "NOT DEPLOYED"
NOT_BUILT = "NOT BUILT"
NOT_RUNNING = "NOT RUNNING"
POD_BEHIND = "POD BEHIND"
NO_MANIFEST = "NO MANIFEST"


class Target(NamedTuple):
    """The four names a deploy check needs, all derived from one.

    Kept as a value rather than module constants so the tool can answer
    for any repo in the org without a second copy of itself.
    """

    name: str
    repo: str
    config_repo: str
    package: str

    @classmethod
    def named(cls, name):
        return cls(
            name=name,
            repo=f"{ORG}/{name}",
            config_repo=f"{ORG}/{name}-config",
            package=name,
        )

    @property
    def image_path(self):
        return f"ghcr.io/{REGISTRY_OWNER}/{self.package}"


def digest_of(image, path):
    """`(matches, digest)` for one deployment's image string.

    Matching on the path and reading the digest separately is deliberate.
    Requiring `path + "@"` to match at all looked equivalent and was not:
    a deployment pinned by *tag* (`path:v3`, or a hand-run `kubectl set
    image` during an incident) then failed the match and was dropped as
    though it were some unrelated service, so a running workload could
    report `NOT RUNNING`. Worse, it made `verdict`'s "could not be read"
    branch dead from the real call path -- the per-name lookup this
    replaced could return None for a deployment it could not resolve, and
    discovery could no longer produce one at all. So a tag-pinned match
    returns None here, which is the existing word for "running, and I
    cannot tell you on what". Reviewer finding, Cycle 295.
    """
    if not image.startswith(path):
        return False, None
    rest = image[len(path):]
    if rest.startswith("@"):
        return True, rest[1:]
    if rest == "" or rest.startswith(":"):
        return True, None
    return False, None


def verdict(target, tip_sha, tip_digest, manifest_digest, deployed, running=None):
    """Compare main's tip against the manifest and the cluster.

    `tip_digest` is the digest of the image tagged `sha-<tip_sha>`, or
    None when the registry holds no image for that commit. `deployed`
    maps deployment name to the digest it is running; a value of None
    means the deployment could not be read, which is reported rather
    than treated as agreement.

    Returns `(state, lines)`. `ROLLOUT_PENDING` is not a fault -- it is
    the expected reading for a cycle that merged minutes ago, and calling
    that a failure would make the check cry wolf on exactly the cycles
    that did the right thing. Every other state is.

    `running` maps `namespace/name` to the digest a **Pod** was created
    from, or `None` when pods were not read at all -- which is the old
    behaviour and still answers about the Deployment only.

    **A Deployment that has rolled is not a Pod that has restarted, and
    that gap is 48 minutes wide here.** `agora-persona-runner` sets
    `terminationGracePeriodSeconds: 2880` so a live cycle finishes before
    its own pod is killed. Measured 2026-09-05: the manifest, the
    Deployment and the registry all agreed on `7f0babe5` at 21:12 Oslo
    while the pod serving `/mcp` was still on `f1a13c27` and would be
    until 21:50 -- and this tool printed `IN SYNC`. Every MCP tool call a
    cycle makes runs in that pod, so `merge_pr` refused a merge the fix
    on main had already made legal, and the honest reading of that refusal
    needed a fact no command here reported.

    `POD_BEHIND` is not a fault, for the same reason `ROLLOUT_PENDING` is
    not: it is the expected reading during a drain somebody merged on
    purpose. The bug being fixed is a confident `IN SYNC`, not a missing
    alarm.

    **Two of those states exist only because the target is no longer
    fixed.** With `DEPLOYMENTS` a hardcoded pair, `deployed` could never
    be empty and the manifest could never be missing, so an empty
    `deployed` fell through to "0 deployment(s) all agree" -- IN SYNC,
    guaranteed in advance, for a service nothing runs. Discovery makes
    both reachable: `vault-bridge` has no `-config` repo at all.
    """
    lines = []
    if tip_digest is None:
        lines.append(
            f"{NOT_BUILT}: main is at {tip_sha} and the registry has no image "
            f"tagged sha-{tip_sha}. The build either failed before build-push "
            f"or is still running. Check `gh run list --repo {target.repo} "
            f"--branch main --limit 3 --json conclusion,createdAt`."
        )
        return NOT_BUILT, lines

    if manifest_digest is None:
        lines.append(
            f"{NO_MANIFEST}: could not read a single pinned digest from "
            f"{target.config_repo}/manifest.yaml. Either that repo does not "
            f"exist -- {target.name} may still deploy through platform-config, "
            "which is what vault-bridge does -- or its `image:` lines "
            "disagree with each other. Nothing below this can be checked, so "
            "this is reported rather than guessed past."
        )
        return NO_MANIFEST, lines

    if manifest_digest != tip_digest:
        lines.append(
            f"{NOT_DEPLOYED}: main is at {tip_sha}, whose image is "
            f"{_short(tip_digest)}, but {target.config_repo}/manifest.yaml "
            f"still pins {_short(manifest_digest)}. The image exists and "
            "nothing points at it, so the merged code will never reach the "
            "cluster on its own."
        )
        lines.append(
            "  This is what a manifest write that never happened looks like -- "
            "a refused job before Cycle 224, a failed last step of `build-push` "
            "after it. Either way it is not visible in `gh pr checks`, in pod "
            "status, or in the logs. Check the `build-push` job on main's "
            "newest run, then fix it by hand: commit the digest above to "
            f"{target.config_repo}/manifest.yaml, which is all the step does."
        )
        return NOT_DEPLOYED, lines

    if not deployed:
        lines.append(
            f"{NOT_RUNNING}: main {tip_sha} is built and "
            f"{target.config_repo}/manifest.yaml pins it, but no deployment in "
            f"the cluster runs {target.image_path}. The manifest may "
            "describe something nothing deploys -- check what the workload is "
            "actually called before concluding the pin is wrong."
        )
        return NOT_RUNNING, lines

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

    behind = {n: d for n, d in (running or {}).items() if d != manifest_digest}
    if behind:
        for name, dig in sorted(behind.items()):
            seen = "could not be read" if dig is None else _short(dig)
            lines.append(
                f"{POD_BEHIND}: pod {name} is still serving {seen} while the "
                f"manifest and its Deployment both pin {_short(manifest_digest)}."
            )
        lines.append(
            f"  The rollout has happened and the pod has not caught up yet. "
            f"{target.name} drains rather than restarting, so code merged to "
            "main is built, pinned and NOT yet executing. Anything running in "
            "that pod -- every MCP tool call -- is the older image until it "
            "goes. Do not judge a tool's behaviour against main during this."
        )
        return POD_BEHIND, lines

    lines.append(
        f"{IN_SYNC}: main {tip_sha} -> {_short(tip_digest)}, manifest and "
        f"{len(deployed)} deployment(s) all agree."
        + (f" {len(running)} pod(s) are running it." if running else "")
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


def tip_sha(target):
    return _run([
        "gh", "api", f"repos/{target.repo}/commits/main", "--jq", ".sha",
    ])


def digest_for_tag(target, tag):
    """Digest of the package version carrying `tag`, or None."""
    raw = _run([
        "gh", "api",
        f"/orgs/{ORG}/packages/container/{target.package}/versions",
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


def manifest_digest(target):
    """The digest pinned in the config repo, or None if the `image:`
    lines disagree -- which is itself a fault worth seeing."""
    raw = _run([
        "gh", "api", f"repos/{target.config_repo}/contents/manifest.yaml",
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


def select_deployments(target, listing):
    """Deployments in `listing` running this target's image.

    `listing` is `namespace/name\\timage` lines, and both halves of that
    are load-bearing.

    **Selecting by image rather than by name** is why this needs no
    lookup table, and it is not a tidiness argument: the deployment
    running `ghcr.io/sokratesai/vault-bridge` in `agents` is named
    `newspaper`, so a table keyed on repo names would have been wrong on
    its first new entry.

    **Reading every namespace, not just `agents`,** is the same lesson
    one step further out. `vault-bridge` has *two* deployments on two
    different images -- `agents/newspaper` on one, `obsidian/vault-bridge`
    on another -- and only the second is what
    `platform-config/deployments/vault-bridge/vault-bridge.yaml` pins.
    Looking only in `agents` finds the wrong one and reports a confident
    answer about it. Keys are `namespace/name` so the two can never be
    confused for one.

    Two deployments sharing one image both appear, which is what keeps a
    partial rollout visible instead of averaged away.
    """
    out = {}
    for line in (listing or "").splitlines():
        name, _, image = line.partition("\t")
        matches, digest = digest_of(image, target.image_path)
        if matches:
            out[name.strip()] = digest
    return out


def select_pods(target, listing):
    """Pods in `listing` running this target's image, keyed `namespace/name`.

    `listing` is `namespace/name\tphase\timage` lines. The image comes off
    the **pod spec**, not `status.containerStatuses[].image`: the status
    field is the resolved reference the runtime reports and can carry a
    `docker.io/` prefix the spec does not, so matching on it drops pods
    that are plainly running this image.

    Only `Running` pods count. A `Pending` one is not serving anything yet,
    and a `Succeeded`/`Failed` one is a finished Job pod that would report a
    digest nobody is talking to. A pod with a `deletionTimestamp` is still
    `Running` and is deliberately kept -- that is exactly the draining pod
    this check exists to name.
    """
    out = {}
    for line in (listing or "").splitlines():
        name, _, rest = line.partition("\t")
        phase, _, image = rest.partition("\t")
        if phase.strip() != "Running":
            continue
        matches, digest = digest_of(image, target.image_path)
        if matches:
            out[name.strip()] = digest
    return out


def pod_digests(target):
    listing = _run([
        "kubectl", "get", "pods", "-A", "-o",
        "jsonpath={range .items[*]}{.metadata.namespace}{'/'}{.metadata.name}"
        "{'\\t'}{.status.phase}{'\\t'}{.spec.containers[0].image}{'\\n'}{end}",
    ])
    if listing is None:
        return None
    return select_pods(target, listing)


def deployed_digests(target):
    listing = _run([
        "kubectl", "get", "deploy", "-A", "-o",
        "jsonpath={range .items[*]}{.metadata.namespace}{'/'}{.metadata.name}"
        "{'\\t'}{.spec.template.spec.containers[0].image}{'\\n'}{end}",
    ])
    if listing is None:
        return None
    return select_deployments(target, listing)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "repo", nargs="?", default=DEFAULT_REPO,
        help=f"repo name inside {ORG} (default: {DEFAULT_REPO})",
    )
    ap.add_argument("--tip", help="override main's tip sha")
    args = ap.parse_args(argv)

    target = Target.named(args.repo)
    sha = args.tip or tip_sha(target)
    if not sha:
        print(
            f"COULD NOT READ: main's tip sha for {target.repo}. Check the name "
            "and `gh auth status`."
        )
        return 1
    deployed = deployed_digests(target)
    if deployed is None:
        print("COULD NOT READ: deployments in `agents`. Check `kubectl`.")
        return 1
    running = pod_digests(target)
    if running is None:
        print("COULD NOT READ: pods. Check `kubectl`.")
        return 1
    short = sha[:7]
    state, lines = verdict(
        target, short, digest_for_tag(target, f"sha-{short}"),
        manifest_digest(target), deployed, running,
    )
    print("\n".join(lines))
    return 0 if state in (IN_SYNC, ROLLOUT_PENDING, POD_BEHIND) else 1


if __name__ == "__main__":
    raise SystemExit(main())
