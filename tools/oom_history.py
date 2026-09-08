"""Which OOM kills happened on this node, and which pod did each one hit?

Cycle 814, on my own issue #27. The container running my Claude Code
session died at 2026-09-02T09:08:51Z with exit 137 and reason `Error`,
taking two cycles with it, and every instrument here came back blank:
the Deployment has no livenessProbe, so the kubelet did not do it;
containerd said `Error` and not `OOMKilled`, so it did not look like
memory; and `memory.events` on the replacement container reads zero
because that counter is born with the container. Cycle 813 wrote "the
cause is not known" and named the two records that might survive a
restart. One of them answers: the node's own `kern.log`, readable
through `nodes/proxy`, holds the kernel's account of every OOM kill
with the victim's pid, name, rss and cgroup path.

    python3 -m tools.oom_history            # every node, last 24h
    python3 -m tools.oom_history --hours 72
    python3 -m tools.oom_history --node server2

**`Error` and `OOMKilled` are not the two answers.** The 09:08 event
reads `oom_memcg=/kubepods.slice/.../kubepods-burstable-pod<uid>.slice`
and `task_memcg=<that same path>/cri-containerd-<id>.scope` — the limit
that was hit belongs to the **pod** cgroup and the process lived one
level down in the container scope. containerd watches the container
scope for OOM events, so a kill triggered by the parent never reaches
the reason field, and Kubernetes reports the SIGKILL as a plain `Error`.
That is why five checks that all read Kubernetes objects agreed there
was no memory event: they were all reading the same blind instrument.

**The raise is narrow on purpose.** A `CONSTRAINT_NONE` kill is the
whole box running out — that is issue #131 and idea #179, it is boarded, it is
waiting on a decision that is not mine, and there were sixteen of them on
this node in the last day, so raising on it means red every morning forever, which is
the same as off. A `CONSTRAINT_MEMCG` kill is one workload asking for
more than its own declared limit, which a cycle can act on today by
raising the limit or by fixing what leaked. Both are printed; only the
second one raises.

Exit status, matching `tools.helm_repo_health`, `tools.argocd_health`
and the rest of the preflight roster: **2 means a cgroup-limit OOM kill
happened inside the window**, 1 means the kernel log or the pod list was
unreadable — which never reads as clean — and 0 means the window held no
cgroup-limit kill, naming what it swept.

**It reads every node, and it did not until Cycle 857.** The node was
`--node server1` by default, which was the whole cluster until server2
joined on 2026-09-03 — after that a kill on server2 produced a report
identical to no kill at all, from a check whose summary line said it had
swept. The node list comes off the API server rather than a constant here,
because a constant is the same failure again on the third node. A node
whose `kern.log` cannot be read exits 1 and is named in the summary, so a
partial sweep can never be read as a clean one, and a cgroup-limit kill on
any node still outranks that.

**A pod deleted since its kill is named from Prometheus, not from the
API server.** Cycle 1165: the 09:11 Oslo kill on server1 that day carried
a uid no Kubernetes object held any more, and the report said so and
stopped. Two records I had assumed were the only ones are not: the
kubelet's `/var/log/pods/<ns>_<name>_<uid>` directories are garbage
collected with the pod (56 of them on a node running 54 pods, measured
before this was written, so they answer for minutes and not for the 24h
window), and kube-state-metrics is not deployed here, so `kube_pod_info`
returns nothing. What does answer is the kubelet cAdvisor scrape, which
Prometheus keeps: every `container_memory_working_set_bytes` series
carries the pod's full cgroup path in its `id` label — uid and all, with
the dashes written as underscores exactly as the kernel writes them —
beside `namespace`, `pod` and `container` labels. So the uid in
`kern.log` joins straight onto a pod name at the instant of the kill.
That named the 09:11 kill `agents/agora-persona-runner-dcc6df8c6-mqn7z`.
Two honest limits, both printed rather than assumed away: Prometheus's
data volume is an `emptyDir`, so a restart takes the history with it and
a kill older than the restart is still unnameable; and the lookup is
presentation only — it never changes the exit status, because whether a
cgroup-limit kill happened does not depend on whether I can name it.

**It reads the tail of `kern.log`, not all of it, and that is a reliability
fix rather than a saving.** Cycle 1190: the sweep read `UNREADABLE --
server2: stream error: stream ID 1; INTERNAL_ERROR; received from peer`,
so the one node that has no swap to absorb a memory spike had no OOM
instrument at all. `kubectl get --raw` pulls the whole file down one
HTTP/2 stream and server2's `kern.log` was 12.3MB, most of it AppArmor
audit lines; that stream is what broke, twice, reproducibly enough to
catch in one cycle. The kubelet's log handler is a static file server and
answers a suffix `Range` with 206, so this asks for the smallest tail that
still reaches back past the window start and grows the range only when it
does not — which also means a step that dies mid-stream is stepped past
instead of becoming the verdict. Measured after the change: both nodes
read, in 0.96s against 46s, and server1 answers a 24h window from 8MB
instead of 8.5.

**The coverage check is the part that makes a tail read honest.** A tail
that begins after the window opened has said nothing about the gap in
between, so a quiet report off it would be a negative result guaranteed by
where the read started rather than by the log. `covers` compares the
oldest parsable timestamp in the returned bytes against the window start,
and a tail with no timestamp in it at all counts as no coverage rather
than as clean. When even the whole file does not reach back, the whole
file is still the answer — the report already prints the oldest stamp it
saw, which is the honest statement of what the window actually covered.

Scope it prints for itself: the node keeps `kern.log` for as long as
logrotate keeps it and this reads the current file only, so a window
longer than that rotation silently holds fewer days than it asks for —
the report says which timestamps it actually saw.
"""

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")

PROMETHEUS = "http://prometheus.infra.svc.cluster.local:9090"
PROM_TIMEOUT = 20

STAMP = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?[+-]\d\d:\d\d)\s")
EVENT = re.compile(r"oom-kill:constraint=(?P<constraint>\w+),")
POD_UID = re.compile(r"pod([0-9a-fA-F]{8}(?:[_-][0-9a-fA-F]{4}){3}[_-][0-9a-fA-F]{12})\.slice")
VICTIM = re.compile(
    r"Killed process (?P<pid>\d+) \((?P<name>.*?)\) total-vm:(?P<vm>\d+)kB, anon-rss:(?P<rss>\d+)kB"
)
INVOKED = re.compile(r"invoked oom-killer:")


# Progressively larger tails, in bytes, stopping at the first one that spans
# the query window. The endpoint serves the whole file, and on 2026-09-08
# server2's kern.log was 12.3MB of mostly AppArmor audit lines -- pulling all
# of it over one HTTP/2 stream is what broke, and the report then read
# UNREADABLE for the one node that has no swap to absorb a spike.
#
# The sizes are measured against that log rather than picked: a 2MB tail of
# server2 reached back about 7 hours and an 8MB tail about 34, so 2MB answers
# a 24h window on a quiet node and 8MB answers it on the noisy one. The last
# step is larger than either node's whole log on purpose -- a suffix range
# bigger than the file is satisfiable and returns all of it, so there is no
# separate unranged fallback to keep in step with this tuple.
TAIL_STEPS = (2 * 1024 * 1024, 8 * 1024 * 1024, 32 * 1024 * 1024, 128 * 1024 * 1024)

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"


def kern_log_url(node, host, port):
    return "https://%s:%s/api/v1/nodes/%s/proxy/logs/kern.log" % (host, port, node)


def ranged_fetch(url, tail_bytes=None, opener=urllib.request.urlopen, timeout=90):
    """The bytes of `url`, optionally only its last `tail_bytes`.

    `kubectl get --raw` cannot set a Range header, so this talks to the API
    server directly with the pod's own service-account token. The kubelet's
    log handler is a static file server and answers 206 with a Content-Range;
    a server that ignores the header answers 200 with the whole file, which
    is still a correct answer and is handled by the coverage check below
    rather than by trusting the status code.
    """
    request = urllib.request.Request(url)
    with open(SA_DIR + "/token") as handle:
        request.add_header("Authorization", "Bearer " + handle.read().strip())
    if tail_bytes is not None:
        request.add_header("Range", "bytes=-%d" % tail_bytes)
    context = ssl.create_default_context(cafile=SA_DIR + "/ca.crt")
    with opener(request, context=context, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def covers(text, window_start):
    """Does `text` reach back to at or before `window_start`?

    A tail read is only an answer about a window it actually spans. The oldest
    timestamp in the returned bytes is the earliest moment this read could have
    seen an OOM kill at, so if it is newer than the window start the read has
    said nothing about the gap in between -- a negative result that was
    guaranteed by where the read began, not by the log being quiet.
    """
    if window_start is None:
        return True
    for line in text.splitlines():
        when = _stamp(line)
        if when is not None:
            return when <= window_start
    # Not one parsable timestamp in the tail: nothing to judge coverage on.
    return False


def read_kern_log(node, runner=subprocess.run, fetch=None, window_start=None):
    """The node's current kernel log, through the kubelet's log endpoint.

    With `fetch` given, read the smallest tail in `TAIL_STEPS` that still
    spans back to `window_start`, growing the range until one does; a step
    that fails mid-stream is stepped past rather than being the verdict.
    Without it, pull the whole file through `kubectl get --raw` -- the older
    path, and what runs where there is no service account to authenticate
    with.
    """
    if fetch is not None:
        last = None
        text = None
        previous_len = -1
        for tail_bytes in TAIL_STEPS:
            try:
                text = fetch(tail_bytes)
            except Exception as problem:  # any transport failure, incl. HTTP/2
                last = problem
                continue
            if covers(text, window_start):
                return text
            if len(text) == previous_len:
                # A suffix range strictly returns more until the file runs
                # out, so two steps of equal length means this is the whole
                # log. Asking for more is a second download of the same bytes,
                # which is what this tool is trying to stop doing.
                break
            previous_len = len(text)
            last = None
        if text is None:
            raise OSError(str(last) or "ranged read failed")
        # Every step read, none of them reached back far enough: the log does
        # not go that deep. `sweep_one` prints the oldest stamp it saw, so the
        # honest answer is the largest read rather than a refusal.
        return text
    done = runner(
        ["kubectl", "get", "--raw", "/api/v1/nodes/%s/proxy/logs/kern.log" % node],
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get --raw failed")
    return done.stdout


def read_node_names(runner=subprocess.run):
    """Every node in the cluster, in the order the API server lists them.

    The default used to be the literal string `server1`, which was correct
    while server1 was the only node and silently wrong from 2026-09-03, when
    server2 joined: a kill on the new node produced the same clean report as
    no kill at all.
    """
    done = runner(
        ["kubectl", "get", "nodes", "-o", "json"], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get nodes failed")
    names = [
        item.get("metadata", {}).get("name")
        for item in json.loads(done.stdout).get("items", [])
    ]
    names = [name for name in names if name]
    if not names:
        raise OSError("the API server listed no nodes, which is not a cluster")
    return names


def read_pod_names(runner=subprocess.run):
    """uid -> 'namespace/name' for every pod alive right now."""
    done = runner(
        ["kubectl", "get", "pods", "-A", "-o", "json"], capture_output=True, text=True
    )
    if done.returncode != 0:
        raise OSError((done.stderr or "").strip() or "kubectl get pods failed")
    names = {}
    for item in json.loads(done.stdout).get("items", []):
        meta = item.get("metadata", {})
        if meta.get("uid"):
            names[meta["uid"]] = "%s/%s" % (meta.get("namespace", "?"), meta.get("name", "?"))
    return names


def _stamp(line):
    found = STAMP.match(line)
    if not found:
        return None
    return datetime.fromisoformat(found.group(1))


def parse_events(text):
    """Every OOM kill in the log, each with the victims the kernel named under it.

    The kernel prints the `oom-kill:constraint=` summary once and then one
    `Killed process` line per victim, and it repeats some of those lines
    verbatim — the same pid twice in one event is one kill, so victims are
    deduplicated by pid rather than counted.
    """
    events = []
    current = None
    for line in text.splitlines():
        when = _stamp(line)
        found = EVENT.search(line)
        if found:
            uid = POD_UID.search(line)
            current = {
                "when": when,
                "constraint": found.group("constraint"),
                "pod_uid": uid.group(1).replace("_", "-") if uid else None,
                "victims": [],
            }
            events.append(current)
            continue
        if INVOKED.search(line):
            # A trigger with no constraint line of its own is a kill the
            # kernel decided not to make; do not attach later victims to
            # whatever event happened to come before it.
            current = None
            continue
        victim = VICTIM.search(line)
        if victim and current is not None:
            pid = int(victim.group("pid"))
            if any(seen["pid"] == pid for seen in current["victims"]):
                continue
            current["victims"].append(
                {
                    "pid": pid,
                    "name": victim.group("name"),
                    "rss_kb": int(victim.group("rss")),
                    "vm_kb": int(victim.group("vm")),
                }
            )
    return events


def within(events, hours, now=None):
    now = now or datetime.now(timezone.utc)
    floor = now - timedelta(hours=hours)
    return [e for e in events if e["when"] is not None and e["when"] >= floor]


def _oslo(when):
    return when.astimezone(OSLO).strftime("%Y-%m-%d %H:%M:%S Oslo")


def report(node, events, hours, namer, seen_from, out=print):
    """Print one node's window and return the exit status it earns."""
    limit = [e for e in events if e["constraint"] == "CONSTRAINT_MEMCG"]
    node_wide = [e for e in events if e["constraint"] != "CONSTRAINT_MEMCG"]

    if limit:
        out(
            "CGROUP LIMIT OOM on %s -- %d kill(s) in the last %dh where a workload asked "
            "for more than its own declared limit. Kubernetes reports these as exit 137 "
            "with reason `Error` when the limit that was hit is the pod cgroup's, because "
            "containerd only watches the container scope."
            % (node, len(limit), hours)
        )
        for event in limit:
            out("  %s  %s" % (_oslo(event["when"]), _pod_label(event, namer)))
            for victim in event["victims"]:
                out(
                    "      killed %s (pid %d), %dMi resident of %dMi virtual"
                    % (
                        victim["name"],
                        victim["pid"],
                        victim["rss_kb"] // 1024,
                        victim["vm_kb"] // 1024,
                    )
                )
    if node_wide:
        out(
            "%s RAN OUT -- %d global kill(s) in the last %dh. Deliberately not raised: "
            "this is the node being oversubscribed, which is issue #131 and idea #179, "
            "boarded and waiting on the owner. Raising on it would be red every morning."
            % (node, len(node_wide), hours)
        )
        for event in node_wide:
            names = ", ".join(v["name"] for v in event["victims"]) or "no victim named"
            out("  %s  %s -- %s" % (_oslo(event["when"]), _pod_label(event, namer), names))

    out(
        "%s: read that node's own kern.log through nodes/proxy, not a Kubernetes object. "
        "%s Window %dh; a pod deleted since its kill is named from Prometheus's cAdvisor scrape, which its emptyDir loses on a restart."
        % (node, seen_from, hours)
    )
    if not limit and not node_wide:
        out("  no OOM kill on %s in the window." % node)
    return 2 if limit else 0


def _prom_query(expr, when, base=PROMETHEUS, opener=urllib.request.urlopen):
    """One instant query. Raises on anything that is not a successful answer."""
    params = {"query": expr}
    if when is not None:
        params["time"] = "%.3f" % when.timestamp()
    url = base + "/api/v1/query?" + urllib.parse.urlencode(params)
    with opener(url, timeout=PROM_TIMEOUT) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise ValueError("prometheus answered status=%r" % payload.get("status"))
    return payload.get("data", {}).get("result", [])


class PodNamer:
    """uid -> 'namespace/name', asking Prometheus only for uids the API server lost.

    Caches per uid, including the misses: two kills in one pod cost one query,
    and an unreachable Prometheus is reported once rather than per event.
    """

    def __init__(self, pod_names, base=PROMETHEUS, opener=urllib.request.urlopen):
        self._live = pod_names
        self._base = base
        self._opener = opener
        self._found = {}

    def name(self, uid, when):
        live = self._live.get(uid)
        if live:
            return live
        if uid in self._found:
            return self._found[uid]
        # The kernel writes the uid with underscores inside the cgroup path.
        expr = 'container_memory_working_set_bytes{id=~".*pod%s.*"}' % uid.replace("-", "_")
        try:
            result = _prom_query(expr, when, base=self._base, opener=self._opener)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as problem:
            answer = ("pod %s (gone since; Prometheus could not be asked: %s)"
                      % (uid, problem))
            self._found[uid] = answer
            return answer
        answer = None
        for entry in result:
            labels = entry.get("metric") or {}
            if labels.get("namespace") and labels.get("pod"):
                answer = "%s/%s" % (labels["namespace"], labels["pod"])
                if labels.get("container"):
                    answer += " (container %s)" % labels["container"]
                break
        if answer is None:
            answer = ("pod %s (gone since, and Prometheus holds no cAdvisor sample "
                      "for it -- its data volume is an emptyDir, so a restart since "
                      "the kill takes the name with it)" % uid)
        self._found[uid] = answer
        return answer


def _pod_label(event, namer):
    uid = event["pod_uid"]
    if uid is None:
        return "outside every pod cgroup (a host process)"
    return namer.name(uid, event.get("when"))


def sweep_one(node, hours, namer, runner=subprocess.run, out=print, now=None,
              fetch_factory=None):
    """One node's verdict. 1 means it could not be read, which is never clean."""
    window_start = (now or datetime.now(timezone.utc)) - timedelta(hours=hours)
    fetch = fetch_factory(node) if fetch_factory is not None else None
    try:
        text = read_kern_log(node, runner=runner, fetch=fetch,
                             window_start=window_start)
    except (OSError, ValueError) as problem:
        out("UNREADABLE -- %s: %s. A node nothing could be read from is not a clean one."
            % (node, problem))
        return 1

    events = parse_events(text)
    stamps = [e["when"] for e in events if e["when"] is not None]
    if events and not stamps:
        out("UNREADABLE -- %s carried OOM events with no parsable timestamp." % node)
        return 1
    seen_from = (
        "The current kern.log holds %d OOM event(s), oldest %s."
        % (len(events), _oslo(min(stamps)))
        if stamps
        else "The current kern.log holds no OOM event at all."
    )
    return report(node, within(events, hours, now=now), hours, namer, seen_from, out=out)


def main(argv=None, runner=subprocess.run, out=print, now=None, namer_factory=PodNamer,
         fetch_factory=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--node", default=None,
                        help="one node instead of every node in the cluster")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args(argv)

    try:
        nodes = [args.node] if args.node else read_node_names(runner=runner)
        pod_names = read_pod_names(runner=runner)
        namer = namer_factory(pod_names)
    except (OSError, ValueError) as problem:
        out("UNREADABLE -- %s. A window nothing could be read from is not a clean one." % problem)
        return 1

    statuses = [
        sweep_one(node, args.hours, namer, runner=runner, out=out, now=now,
                  fetch_factory=fetch_factory)
        for node in nodes
    ]
    unread = [node for node, status in zip(nodes, statuses) if status == 1]
    out("Swept %d node(s): %s.%s"
        % (len(nodes), ", ".join(nodes),
           "" if not unread else " Could not read %s, so this sweep is partial."
                                 % ", ".join(unread)))
    if 2 in statuses:
        return 2
    return 1 if unread else 0


def api_fetch_factory():
    """The real ranged reader, or None when there is no service account here.

    Built only on the production path so a unit test can never reach the API
    server through a default argument.
    """
    host = os.environ.get("KUBERNETES_SERVICE_HOST")
    port = os.environ.get("KUBERNETES_SERVICE_PORT")
    if not host or not port or not os.path.exists(SA_DIR + "/token"):
        return None

    def factory(node):
        url = kern_log_url(node, host, port)
        return lambda tail_bytes: ranged_fetch(url, tail_bytes=tail_bytes)

    return factory


if __name__ == "__main__":
    sys.exit(main(fetch_factory=api_fetch_factory()))
