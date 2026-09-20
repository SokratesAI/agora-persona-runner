"""How many bytes does a local-path volume actually hold?

`backup_health` can tell you a volume is unprotected. It cannot tell you how
much is at stake, and that is the number the decision turns on: a backup job
for an empty directory is machinery guarding nothing, and `MIN_SOURCE_BYTES`
on the shared `volume-backup.py` is a floor that has to be a measured number
rather than a guessed one.

Nothing in the cluster answers this. **The kubelet looks like it does and does
not** -- `/api/v1/nodes/<node>/proxy/stats/summary` carries a `volume` entry
per PVC with `usedBytes` and `capacityBytes`, and for a local-path volume both
are the node root filesystem's numbers, because the volume is a plain
directory on that filesystem. Measured 2026-09-20: it reported
`agents/sokrates-post-data` at 45,543,677,952 bytes used of 80,307,429,376,
and the directory held 4,096 -- an empty directory. Every one of the ten PVCs
in this cluster reported one of two numbers, one per node. That is a plausible
answer for every volume whether or not the instrument can see it, which is the
failure mode worth naming: a positive result guaranteed in advance is not a
measurement.

So the only honest instrument is `du` on the node itself, which means a
read-only one-off Job -- `tools.oneoff_job`, in the `test` namespace, where
both of Nova's ServiceAccounts hold full verbs on `batch`. One Job per node,
not one per volume: the paths are grouped, so sizing every claim in the
cluster costs two Jobs.

    python3 -m tools.volume_size
    python3 -m tools.volume_size --claim agents/sokrates-post-data

A note for whoever reads a red `NOT BACKED UP` next: `kubectl auth can-i
create jobs -n agents` is **no**, and that is the correct and narrow answer to
a question about the `agents` namespace. It is not a statement that this loop
cannot run a Job. It can, in `test`, with a node selector and a read-only
hostPath -- which is what this tool does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from tools import oneoff_job

#: Every local-path volume in this cluster is a directory under here, but the
#: PV carries its own path and that is what gets mounted -- the root, so a
#: provisioner that ever puts one elsewhere is still measured.
HOST_MOUNT = "/host"

#: `kubectl wait` on the Job. A `du` of a volume holding tens of gigabytes on
#: a busy node is the slow case; 180s is the same budget `disk_health` uses
#: for its own host sweep.
WAIT_SECONDS = 180


def _kubectl(args, stdin=None, timeout=60):
    return subprocess.run(
        ["kubectl", *args],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def local_path_targets(payload, claims=None):
    """`(claim, node, path)` for every local-path PV in a `kubectl get pv` doc.

    Pure. A PV with no `spec.local.path` is some other kind of volume and is
    skipped rather than guessed at; a PV with a path and no node affinity is
    skipped too, because a Job that cannot be pinned to the right node would
    measure a directory that is not there and report a confident zero.
    """
    wanted = set(claims) if claims else None
    targets = []
    for item in (payload or {}).get("items", []):
        spec = item.get("spec") or {}
        path = ((spec.get("local") or {}).get("path") or "").strip()
        if not path:
            continue
        ref = spec.get("claimRef") or {}
        claim = "%s/%s" % (ref.get("namespace"), ref.get("name"))
        if wanted is not None and claim not in wanted:
            continue
        node = _affinity_node(spec)
        if not node:
            continue
        targets.append((claim, node, path))
    return sorted(targets)


def _affinity_node(spec):
    """The single hostname a PV is pinned to, or None."""
    terms = (
        ((spec.get("nodeAffinity") or {}).get("required") or {})
        .get("nodeSelectorTerms")
        or []
    )
    for term in terms:
        for expr in term.get("matchExpressions") or []:
            if expr.get("key") == "kubernetes.io/hostname":
                values = expr.get("values") or []
                if len(values) == 1:
                    return values[0]
    return None


def size_command(paths, mount=HOST_MOUNT):
    """The shell the read-only Job runs. Pure, so the parser has a fixed input.

    `du -xsk` for the same reason `disk_health` uses it: kibibytes are what
    busybox and GNU `du` agree on, and `-x` keeps the walk on the node's own
    filesystem so a bind mount underneath is not counted twice.

    A directory that is not on this node prints `MISSING` rather than nothing,
    because "absent" and "empty" are opposite findings and a parser that
    skipped the line would turn the first into the second.
    """
    parts = []
    for path in paths:
        parts.append(
            'printf "%s " "{p}"; if [ -d "{t}" ]; then du -xsk "{t}" 2>/dev/null '
            '| cut -f1; else echo MISSING; fi'.format(p=path, t=mount + path)
        )
    return "; ".join(parts)


def parse_sizes(logs):
    """`<path> <kibibytes>` lines into `{path: bytes}`; `MISSING` into None."""
    sizes = {}
    for line in (logs or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        path, size = fields
        if not path.startswith("/"):
            continue
        if size == "MISSING":
            sizes[path] = None
        elif size.isdigit():
            sizes[path] = int(size) * 1024
    return sizes


def by_node(targets):
    """`{node: [path, ...]}`, each node's paths sorted and deduplicated."""
    grouped = {}
    for _claim, node, path in targets:
        grouped.setdefault(node, set()).add(path)
    return {node: sorted(paths) for node, paths in sorted(grouped.items())}


def human(size):
    """Bytes as a short string. `None` is the absent case, never a zero."""
    if size is None:
        return "not on the node"
    for unit, step in (("GB", 1000 ** 3), ("MB", 1000 ** 2), ("KB", 1000)):
        if size >= step:
            return "%.1f %s" % (size / float(step), unit)
    return "%d B" % size


def report(targets, sizes, out=print):
    """One line per claim, and the status this tool should exit with."""
    if not targets:
        out("No local-path volume matched. Nothing to size.")
        return 1
    unmeasured = 0
    for claim, node, path in targets:
        if path not in sizes:
            unmeasured += 1
            out("%s — UNMEASURED on %s (%s)" % (claim, node, path))
            continue
        size = sizes[path]
        note = ""
        if size is not None and size <= 4096:
            note = " — empty; the directory exists and holds nothing"
        out("%s — %s on %s%s" % (claim, human(size), node, note))
    out(
        "Sized %d volume(s) with %d Job(s); %d unmeasured."
        % (len(targets), len(by_node(targets)), unmeasured)
    )
    return 2 if unmeasured else 0


def measure(targets, kubectl=_kubectl, wait=WAIT_SECONDS):
    """Run one Job per node and return `{path: bytes or None}`.

    Each node is a separate Job on purpose. They are pinned to different
    machines, so one failing node leaves the other node's answer intact --
    a single Job could only have run on one of them anyway.
    """
    sizes = {}
    for node, paths in by_node(targets).items():
        manifest = oneoff_job.build_manifest(
            "volsize-" + node,
            size_command(paths),
            node=node,
            hostpath="/",
            mount_path=HOST_MOUNT,
        )
        name = manifest["metadata"]["name"]
        kubectl(["delete", "job", name, "-n", oneoff_job.NAMESPACE,
                 "--ignore-not-found=true"])
        applied = kubectl(["apply", "-f", "-"], stdin=json.dumps(manifest))
        if applied.returncode != 0:
            continue
        kubectl(
            ["wait", "--for=condition=complete", "job/" + name,
             "-n", oneoff_job.NAMESPACE, "--timeout=%ds" % wait],
            timeout=wait + 30,
        )
        logs = kubectl(["logs", "job/" + name, "-n", oneoff_job.NAMESPACE,
                        "--tail=200"])
        sizes.update(parse_sizes(logs.stdout))
    return sizes


def read_volumes(kubectl=_kubectl):
    """`kubectl get pv -o json` as a dict, or `(None, error)`."""
    proc = kubectl(["get", "pv", "-o", "json"])
    if proc.returncode != 0:
        return None, (proc.stderr or "kubectl exited %d" % proc.returncode).strip()
    try:
        return json.loads(proc.stdout), None
    except ValueError as err:
        return None, "kubectl printed something that is not JSON: %s" % err


def main(argv=None, kubectl=_kubectl, out=print):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--claim",
        action="append",
        metavar="NAMESPACE/NAME",
        help="only this claim (repeatable); default is every local-path volume",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the claims and the shell each Job would run, and stop",
    )
    args = parser.parse_args(argv)

    payload, error = read_volumes(kubectl=kubectl)
    if error:
        out("UNREADABLE — I could not list this cluster's volumes: %s" % error)
        return 1
    targets = local_path_targets(payload, claims=args.claim)
    if args.dry_run:
        if not targets:
            out("No local-path volume matched. Nothing to size.")
            return 1
        for node, paths in by_node(targets).items():
            out("%s: %s" % (node, size_command(paths)))
        return 0
    return report(targets, measure(targets, kubectl=kubectl), out=out)


if __name__ == "__main__":
    sys.exit(main())
