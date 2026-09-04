"""Run a one-off Kubernetes Job in the `test` namespace, without a pull request.

A one-off Job is how this loop answers a question the API server cannot: the
per-volume size of a local-path claim, whether a node's kernel log holds an OOM
kill, whether an image starts at all. Until now the only way to run one was to
merge a manifest into `test-jobs/` in `platform-config`, let ArgoCD apply it,
read the logs, and then **merge a second time to delete the spent Job**.

That mechanism was written when I had read-only `kubectl` and nothing else, and
it is expensive in the one unit that is actually scarce here. GitHub bills a
private-repo Actions job in whole minutes, and `platform-config` runs one job
per pull request plus one per resulting `main` commit, so **a single one-off Job
costs four billed CI minutes** -- plus two ArgoCD sync waits, which is minutes
of wall clock inside a 45-minute turn. Cycle 926 counted at least 7 cleanup
merges in one day against a 67-minute daily allowance that `tools.preflight`
already reports as oversubscribed.

None of it was necessary. The `test` namespace grants full verbs on `batch`
Jobs to both ServiceAccounts Nova runs as (`namespaces/test.yaml` in
`platform-config`), so a Job can be applied straight from a shell. Measured
Cycle 927 from the runner pod, before this file existed: a Job with a
`nodeSelector` and a read-only `hostPath` mount ran to completion in `test` in
under 90 seconds, with no git anywhere in it.

    python3 -m tools.oneoff_job --name disk --command 'df -h /host' \
        --node server1 --hostpath /var/lib/rancher

What it does not do is guess. It prints the manifest it is about to apply with
`--dry-run`, it waits for the Job's own completion condition rather than
sleeping, and it prints the pod's logs whether the Job passed or failed --
because a failed one-off Job's logs are the entire reason it was run.

Two limits are deliberate and are the reason `test-jobs/` still exists. This
namespace has a `ResourceQuota` of 1 CPU / 1Gi and 10 pods, so a Job that needs
more than that belongs elsewhere. And the grant is scoped to `test`: a Job that
must run *in* `agents` or `obsidian` -- a PVC move, anything touching a real
workload's volume -- still goes through `platform-config`. Reaching for this
tool for one of those and finding it refused is the correct outcome, not a gap.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

NAMESPACE = "test"
PREFIX = "nova-oneoff-"
DEFAULT_IMAGE = "busybox:1.36"

# The Job cleans itself up. `test-jobs/` needed a second merge to delete a spent
# Job because ArgoCD owned it; nothing owns this one, so the TTL controller can
# have it. Ten minutes is long enough to go back and re-read the logs after the
# tool has already printed them, and short enough not to sit against the
# namespace's 10-pod quota.
TTL_SECONDS = 600


def build_manifest(
    name: str,
    command: str,
    *,
    image: str = DEFAULT_IMAGE,
    node: str | None = None,
    hostpath: str | None = None,
    mount_path: str = "/host",
) -> dict:
    """The Job this tool applies. Pure -- no cluster, no subprocess, testable.

    `backoffLimit: 0` on purpose: a one-off Job asks a question once. Retrying
    it turns a clean failure into three identical failures and a longer wait,
    and the second answer is never new information.
    """
    if not name:
        raise ValueError("a one-off Job needs a name")
    container: dict = {
        "name": "run",
        "image": image,
        "command": ["sh", "-c", command],
    }
    spec: dict = {
        "restartPolicy": "Never",
        "containers": [container],
    }
    if node:
        spec["nodeSelector"] = {"kubernetes.io/hostname": node}
    if hostpath:
        container["volumeMounts"] = [
            {"name": "host", "mountPath": mount_path, "readOnly": True}
        ]
        spec["volumes"] = [
            {"name": "host", "hostPath": {"path": hostpath, "type": "Directory"}}
        ]
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": PREFIX + name, "namespace": NAMESPACE},
        "spec": {
            "ttlSecondsAfterFinished": TTL_SECONDS,
            "backoffLimit": 0,
            "template": {"spec": spec},
        },
    }


def _kubectl(args: list[str], stdin: str | None = None, timeout: int = 120):
    return subprocess.run(
        ["kubectl", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run(manifest: dict, *, wait: int = 120, kubectl=_kubectl) -> int:
    """Apply, wait, print logs. Returns the exit code this tool should use.

    The logs are printed on both paths. A Job that fails is the interesting
    case, and `kubectl wait` tells you only that the condition never came --
    which is never the answer you ran the Job to get.
    """
    full = manifest["metadata"]["name"]
    applied = kubectl(["apply", "-f", "-"], stdin=json.dumps(manifest))
    sys.stdout.write(applied.stdout)
    if applied.returncode != 0:
        sys.stderr.write(applied.stderr)
        return 1

    waited = kubectl(
        [
            "wait",
            "--for=condition=complete",
            f"job/{full}",
            "-n",
            NAMESPACE,
            f"--timeout={wait}s",
        ],
        timeout=wait + 30,
    )
    logs = kubectl(["logs", f"job/{full}", "-n", NAMESPACE, "--tail=200"])
    sys.stdout.write(f"--- logs from {full}\n")
    sys.stdout.write(logs.stdout)
    if logs.returncode != 0:
        sys.stderr.write(logs.stderr)
    if waited.returncode != 0:
        sys.stderr.write(
            f"{full} did not complete within {wait}s -- the logs above are "
            f"whatever it managed to print.\n"
        )
        sys.stderr.write(waited.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--name", required=True, help="short slug; nova-oneoff- is prefixed")
    ap.add_argument("--command", required=True, help="passed to sh -c in the container")
    ap.add_argument("--image", default=DEFAULT_IMAGE)
    ap.add_argument("--node", help="pin to one node by kubernetes.io/hostname")
    ap.add_argument("--hostpath", help="read-only hostPath to mount")
    ap.add_argument("--mount-path", default="/host")
    ap.add_argument("--wait", type=int, default=120, help="seconds to wait for completion")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the manifest and apply nothing",
    )
    args = ap.parse_args(argv)

    manifest = build_manifest(
        args.name,
        args.command,
        image=args.image,
        node=args.node,
        hostpath=args.hostpath,
        mount_path=args.mount_path,
    )
    if args.dry_run:
        print(json.dumps(manifest, indent=2))
        return 0
    return run(manifest, wait=args.wait)


if __name__ == "__main__":
    raise SystemExit(main())
