"""The kernel log is the one record of an OOM kill that survives the restart."""
import io
import json
import subprocess
import urllib.error
import urllib.parse
from datetime import datetime, timezone

from tools import oom_history

# Copied verbatim off server1's kern.log for 2026-09-02, the event that took
# two cycles down. The duplicate `Killed process 268654` line is the kernel's
# own, not a copy/paste slip -- deduplicating it is one of the things under test.
BRIDGE_KILL = """\
2026-09-02T09:08:50.880492+00:00 Server1 kernel: MainThread invoked oom-killer: gfp_mask=0x100cca(GFP_HIGHUSER_MOVABLE), order=0, oom_score_adj=934
2026-09-02T09:08:50.903135+00:00 Server1 kernel: oom-kill:constraint=CONSTRAINT_MEMCG,nodemask=(null),cpuset=cri-containerd-cabbede.scope,mems_allowed=0,oom_memcg=/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod2c7713fd_2aed_4fa2_a60f_f006ab94b3df.slice,task_memcg=/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod2c7713fd_2aed_4fa2_a60f_f006ab94b3df.slice/cri-containerd-cabbede.scope,task=MainThread,pid=268654,uid=10001
2026-09-02T09:08:50.903136+00:00 Server1 kernel: Memory cgroup out of memory: Killed process 268654 (MainThread) total-vm:2011236kB, anon-rss:567484kB, file-rss:20608kB, shmem-rss:0kB, UID:10001 pgtables:16572kB oom_score_adj:934
2026-09-02T09:08:50.903138+00:00 Server1 kernel: Memory cgroup out of memory: Killed process 235173 (tini) total-vm:2568kB, anon-rss:0kB, file-rss:1024kB, shmem-rss:0kB, UID:10001 pgtables:48kB oom_score_adj:934
2026-09-02T09:08:50.903139+00:00 Server1 kernel: Memory cgroup out of memory: Killed process 268654 (MainThread) total-vm:2011236kB, anon-rss:567484kB, file-rss:20608kB, shmem-rss:0kB, UID:10001 pgtables:16572kB oom_score_adj:934
"""

GLOBAL_KILL = """\
2026-09-02T00:54:21.607510+00:00 Server1 kernel: apport invoked oom-killer: gfp_mask=0x140cca(GFP_HIGHUSER_MOVABLE|__GFP_COMP), order=0, oom_score_adj=0
2026-09-02T00:54:21.619844+00:00 Server1 kernel: oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,global_oom,task_memcg=/kubepods.slice/kubepods-besteffort.slice/kubepods-besteffort-pod7145627f_44a7_433c_a896_a8c81a4df533.slice/cri-containerd-914914.scope,task=argocd-dex,pid=2630150,uid=1001
2026-09-02T00:54:21.619844+00:00 Server1 kernel: Out of memory: Killed process 2630150 (argocd-dex) total-vm:1383472kB, anon-rss:18148kB, file-rss:128kB, shmem-rss:0kB, UID:1001 pgtables:408kB oom_score_adj:1000
"""

NOW = datetime(2026, 9, 2, 16, 30, tzinfo=timezone.utc)


class FakeProm:
    """Stands in for urllib.request.urlopen against Prometheus.

    `series` is the list of label dicts one query answers with; passing an
    exception instance instead makes the call fail the way an unreachable
    Prometheus does. Records every URL so a test can assert what was asked.
    """

    def __init__(self, series=(), status="success"):
        self.series = series
        self.status = status
        self.urls = []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if isinstance(self.series, Exception):
            raise self.series
        payload = {
            "status": self.status,
            "data": {"result": [{"metric": m, "value": [0, "1"]} for m in self.series]},
        }
        return io.StringIO(json.dumps(payload))


def namer(pod_names=None, opener=None):
    return oom_history.PodNamer(pod_names or {}, opener=opener or FakeProm())


def lines(events, hours=24, pod_names=None, now=NOW, node="server1", opener=None):
    got = []
    status = oom_history.report(
        node, oom_history.within(events, hours, now=now), hours,
        namer(pod_names, opener), "swept.", out=got.append)
    return status, "\n".join(got)


NODES = '{"items": [{"metadata": {"name": "server1"}}, {"metadata": {"name": "server2"}}]}'


def cluster(kern, pods='{"items": []}', nodes=NODES):
    """A fake kubectl. `kern` maps a node name to its kern.log, or to an OSError."""
    def runner(cmd, **kwargs):
        if "nodes" in cmd:
            if isinstance(nodes, int):
                # A body that parses, so the guard under test is the exit
                # status and not json.loads choking on an empty string --
                # kubectl really does print a list on some refusals.
                return subprocess.CompletedProcess(cmd, nodes, NODES, "Forbidden")
            return subprocess.CompletedProcess(cmd, 0, nodes, "")
        if "pods" in cmd:
            return subprocess.CompletedProcess(cmd, 0, pods, "")
        raw = [part for part in cmd if part.startswith("/api/v1/nodes/")][0]
        node = raw.split("/")[4]
        body = kern[node]
        if body is None:
            return subprocess.CompletedProcess(cmd, 1, "", "Error from server (Forbidden)")
        return subprocess.CompletedProcess(cmd, 0, body, "")
    return runner


def test_victims_group_under_their_own_event_and_a_repeated_pid_counts_once():
    events = oom_history.parse_events(BRIDGE_KILL)
    assert len(events) == 1
    assert [v["pid"] for v in events[0]["victims"]] == [268654, 235173]


def test_the_pod_cgroup_uid_is_read_with_kubernetes_dashes_not_slice_underscores():
    events = oom_history.parse_events(BRIDGE_KILL)
    assert events[0]["pod_uid"] == "2c7713fd-2aed-4fa2-a60f-f006ab94b3df"


def test_a_trigger_with_no_kill_does_not_adopt_a_later_victim_line():
    # The kernel logs `invoked oom-killer` for an allocation it then satisfies
    # without killing anything, and it logs kills the tail of a rotated file
    # cut the header off. Neither may be attached to the event above it -- that
    # would report a process as killed by a kill it had nothing to do with.
    text = (
        BRIDGE_KILL
        + "2026-09-02T09:10:00.000000+00:00 Server1 kernel: node invoked oom-killer: order=0\n"
        + "2026-09-02T09:10:01.000000+00:00 Server1 kernel: Out of memory: Killed process 999"
        " (stranger) total-vm:10kB, anon-rss:5kB, file-rss:0kB, shmem-rss:0kB\n"
    )
    events = oom_history.parse_events(text)
    assert len(events) == 1
    assert [v["name"] for v in events[0]["victims"]] == ["MainThread", "tini"]


def test_a_cgroup_limit_kill_raises():
    status, text = lines(oom_history.parse_events(BRIDGE_KILL))
    assert status == 2
    assert "CGROUP LIMIT OOM" in text


def test_a_global_kill_prints_and_does_not_raise():
    status, text = lines(oom_history.parse_events(GLOBAL_KILL))
    assert status == 0
    assert "server1 RAN OUT" in text
    assert "CGROUP LIMIT OOM" not in text


def test_a_global_kill_beside_a_limit_kill_does_not_hide_it():
    status, text = lines(oom_history.parse_events(GLOBAL_KILL + BRIDGE_KILL))
    assert status == 2
    assert "CGROUP LIMIT OOM on server1" in text and "server1 RAN OUT" in text


def test_a_kill_older_than_the_window_is_not_reported():
    status, text = lines(oom_history.parse_events(BRIDGE_KILL), hours=1)
    assert status == 0
    assert "no OOM kill on server1 in the window." in text


def test_a_live_pod_is_named_and_a_dead_one_nothing_remembers_is_left_as_its_uid():
    events = oom_history.parse_events(BRIDGE_KILL)
    uid = "2c7713fd-2aed-4fa2-a60f-f006ab94b3df"
    _, named = lines(events, pod_names={uid: "agents/agora-claude-bridge-67459f6f88-w5x2b"})
    assert "agents/agora-claude-bridge-67459f6f88-w5x2b" in named
    _, unnamed = lines(events, pod_names={})
    assert "Prometheus holds no cAdvisor sample" in unnamed
    assert uid in unnamed


# --- naming a pod the API server has already lost -------------------------
#
# Cycle 1165. The 09:11 Oslo kill on server1 that day was a uid and nothing
# else, because the pod was gone. cAdvisor writes the whole cgroup path into
# its `id` label, so the uid joins onto a pod name inside Prometheus.

CADVISOR = {
    "id": ("/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-"
           "pod2c7713fd_2aed_4fa2_a60f_f006ab94b3df.slice/cri-containerd-cab.scope"),
    "namespace": "agents",
    "pod": "agora-persona-runner-dcc6df8c6-mqn7z",
    "container": "persona-runner",
    "node": "server1",
}


def test_a_pod_the_api_server_lost_is_named_from_prometheus():
    prom = FakeProm([CADVISOR])
    _, text = lines(oom_history.parse_events(BRIDGE_KILL), opener=prom)
    assert "agents/agora-persona-runner-dcc6df8c6-mqn7z" in text
    assert "container persona-runner" in text
    assert "cannot" not in text


def test_the_query_asks_for_the_uid_the_kernel_spells_with_underscores():
    # The kernel writes pod2c7713fd_2aed_..., Kubernetes writes 2c7713fd-2aed-...
    # A query built from the Kubernetes spelling matches nothing at all.
    prom = FakeProm([CADVISOR])
    lines(oom_history.parse_events(BRIDGE_KILL), opener=prom)
    assert len(prom.urls) == 1
    assert "pod2c7713fd_2aed_4fa2_a60f_f006ab94b3df" in urllib.parse.unquote(prom.urls[0])
    assert "2c7713fd-2aed" not in urllib.parse.unquote(prom.urls[0])


def test_the_query_asks_at_the_instant_of_the_kill_not_now():
    # A pod that died yesterday has no sample at the current instant; the
    # series only exists around the kill.
    prom = FakeProm([CADVISOR])
    lines(oom_history.parse_events(BRIDGE_KILL), opener=prom)
    asked = urllib.parse.parse_qs(urllib.parse.urlparse(prom.urls[0]).query)
    when = oom_history.parse_events(BRIDGE_KILL)[0]["when"]
    assert when.date() == datetime(2026, 9, 2).date()
    assert float(asked["time"][0]) == round(when.timestamp(), 3)
    assert float(asked["time"][0]) != NOW.timestamp()


def test_a_live_pod_is_never_asked_about():
    prom = FakeProm([CADVISOR])
    uid = "2c7713fd-2aed-4fa2-a60f-f006ab94b3df"
    lines(oom_history.parse_events(BRIDGE_KILL), pod_names={uid: "agents/live"}, opener=prom)
    assert prom.urls == []


def test_an_unreachable_prometheus_says_so_rather_than_reading_as_no_such_pod():
    prom = FakeProm(urllib.error.URLError("connection refused"))
    status, text = lines(oom_history.parse_events(BRIDGE_KILL), opener=prom)
    assert "Prometheus could not be asked" in text
    assert "connection refused" in text
    # Naming is presentation. Whether a cgroup-limit kill happened does not
    # depend on whether Prometheus answered.
    assert status == 2


def test_a_series_with_no_pod_label_is_not_read_as_a_name():
    prom = FakeProm([{"id": "/kubepods.slice/whatever.slice"}])
    _, text = lines(oom_history.parse_events(BRIDGE_KILL), opener=prom)
    assert "holds no cAdvisor sample" in text


def test_prometheus_is_asked_once_per_uid_however_many_kills_it_had():
    prom = FakeProm([CADVISOR])
    twice = oom_history.parse_events(BRIDGE_KILL + BRIDGE_KILL.replace("09:08:5", "09:09:5"))
    _, text = lines(twice, opener=prom)
    assert text.count("agora-persona-runner-dcc6df8c6-mqn7z") == 2
    assert len(prom.urls) == 1


def test_a_status_that_is_not_success_is_reported_not_read_as_no_such_pod():
    prom = FakeProm([CADVISOR], status="error")
    _, text = lines(oom_history.parse_events(BRIDGE_KILL), opener=prom)
    assert "Prometheus could not be asked" in text


def test_the_victims_rss_is_printed_so_a_kill_says_what_asked_for_the_memory():
    _, text = lines(oom_history.parse_events(BRIDGE_KILL))
    assert "554Mi resident" in text


def test_an_unreadable_kernel_log_is_not_a_clean_window():
    # The pod list answers, so the only thing that can produce a 1 here is the
    # kernel log's own failure -- an empty log read as "no kills" would exit 0.
    half = cluster({"server1": None, "server2": None})

    got = []
    assert oom_history.main([], runner=half, out=got.append) == 1
    assert "UNREADABLE" in got[0]
    assert "Forbidden" in got[0]


def test_an_unreadable_pod_list_is_not_a_clean_window():
    # kubectl prints an empty list on some refusals, so the refused call has to
    # be caught by its exit status. A body that parses is what makes this test
    # about the guard rather than about json.loads failing on an empty string.
    def half(cmd, **kwargs):
        if "nodes" in cmd:
            return subprocess.CompletedProcess(cmd, 0, NODES, "")
        if "pods" in cmd:
            return subprocess.CompletedProcess(cmd, 1, '{"items": []}', "Forbidden")
        return subprocess.CompletedProcess(cmd, 0, GLOBAL_KILL, "")

    got = []
    assert oom_history.main([], runner=half, out=got.append) == 1
    assert "Forbidden" in got[0]


def test_a_kernel_log_with_no_oom_event_is_clean_and_says_so():
    quiet = cluster({
        "server1": "2026-09-02T09:00:00.000000+00:00 x\n",
        "server2": "2026-09-02T09:00:00.000000+00:00 x\n",
    })

    got = []
    assert oom_history.main([], runner=quiet, out=got.append) == 0
    assert "no OOM event at all" in "\n".join(got)


def test_a_kill_on_the_second_node_is_found_and_raises():
    # The bug this replaced: `--node server1` was the default, so a kill on
    # server2 produced byte-identical output to no kill anywhere.
    runner = cluster({"server1": "", "server2": BRIDGE_KILL})
    got = []
    assert oom_history.main([], runner=runner, out=got.append, now=NOW,
                            namer_factory=namer) == 2
    text = "\n".join(got)
    assert "CGROUP LIMIT OOM on server2" in text
    assert "MainThread" in text


def test_every_node_the_api_server_lists_is_swept_and_named():
    runner = cluster({"server1": "", "server2": ""})
    got = []
    assert oom_history.main([], runner=runner, out=got.append, now=NOW) == 0
    text = "\n".join(got)
    assert "Swept 2 node(s): server1, server2." in text


def test_a_node_that_cannot_be_read_makes_the_sweep_partial_not_clean():
    # server1 is clean and server2 is refused. The old shape would have exited
    # 0 on server1 alone; the summary has to say which half is missing.
    runner = cluster({"server1": "", "server2": None})
    got = []
    assert oom_history.main([], runner=runner, out=got.append, now=NOW) == 1
    text = "\n".join(got)
    assert "Could not read server2" in text
    assert "partial" in text


def test_a_real_kill_outranks_an_unreadable_node():
    runner = cluster({"server1": BRIDGE_KILL, "server2": None})
    got = []
    assert oom_history.main([], runner=runner, out=got.append, now=NOW,
                            namer_factory=namer) == 2
    assert "Could not read server2" in "\n".join(got)


def test_an_unreadable_node_list_is_not_a_clean_sweep():
    runner = cluster({"server1": "", "server2": ""}, nodes=1)
    got = []
    assert oom_history.main([], runner=runner, out=got.append, now=NOW) == 1
    assert "UNREADABLE" in got[0]
    assert "Forbidden" in got[0]


def test_an_empty_node_list_is_not_a_cluster():
    runner = cluster({"server1": ""}, nodes='{"items": []}')
    got = []
    assert oom_history.main([], runner=runner, out=got.append, now=NOW) == 1
    assert "listed no nodes" in got[0]


def test_node_restricts_the_sweep_to_one_node_without_listing_them():
    asked = []

    def runner(cmd, **kwargs):
        asked.append(cmd)
        if "pods" in cmd:
            return subprocess.CompletedProcess(cmd, 0, '{"items": []}', "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    got = []
    assert oom_history.main(["--node", "server2"], runner=runner, out=got.append, now=NOW) == 0
    assert not any("nodes" in cmd and "get" in cmd and "-o" in cmd for cmd in asked)
    assert "Swept 1 node(s): server2." in "\n".join(got)


# --- the ranged kern.log read ------------------------------------------------
#
# Cycle 1190: `oom_history` pulled the whole kern.log through one `kubectl get
# --raw` stream. On 2026-09-08 that was 12.3MB on server2 and the stream died
# with `stream error: stream ID 1; INTERNAL_ERROR`, so the sweep reported
# UNREADABLE for the one node that has no swap. The kubelet's log handler
# answers HTTP Range (measured: 206 with a Content-Range), so the fix is to
# read only the tail that spans the query window.

WINDOW_START = datetime(2026, 9, 2, 0, 0, tzinfo=timezone.utc)


class FakeFetch:
    """Stands in for one node's ranged reader. Records every tail size asked for.

    `answers` maps a tail size (or None for the whole file) to the text it
    returns, or to an exception instance to make that read fail.
    """

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, tail_bytes):
        self.asked.append(tail_bytes)
        answer = self.answers[tail_bytes]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_covers_is_true_when_the_tail_reaches_back_past_the_window():
    assert oom_history.covers(GLOBAL_KILL, WINDOW_START) is False
    assert oom_history.covers(GLOBAL_KILL, datetime(2026, 9, 3, tzinfo=timezone.utc)) is True


def test_covers_is_false_when_the_tail_has_no_timestamp_to_judge_by():
    # A tail that begins mid-line carries no parsable stamp; that is not
    # coverage, it is nothing to judge coverage on.
    assert oom_history.covers("kernel: some line with no stamp\n", WINDOW_START) is False


def test_covers_needs_no_window_when_the_caller_asked_for_none():
    assert oom_history.covers("", None) is True


def test_ranged_read_stops_at_the_smallest_tail_that_spans_the_window():
    small = oom_history.TAIL_STEPS[0]
    fetch = FakeFetch(dict.fromkeys(oom_history.TAIL_STEPS, GLOBAL_KILL))
    text = oom_history.read_kern_log(
        "server2", fetch=fetch,
        window_start=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert text == GLOBAL_KILL
    assert fetch.asked == [small], "a covered tail must not be re-read larger"


def test_ranged_read_grows_the_range_until_it_spans_the_window():
    small, big = oom_history.TAIL_STEPS[:2]
    fetch = FakeFetch(dict.fromkeys(oom_history.TAIL_STEPS, GLOBAL_KILL + BRIDGE_KILL))
    fetch.answers[small] = BRIDGE_KILL
    text = oom_history.read_kern_log(
        "server2", fetch=fetch,
        window_start=datetime(2026, 9, 2, 6, 0, tzinfo=timezone.utc))
    # The smallest tail begins at 09:08, after the window opened at 06:00, so
    # it says nothing about the gap; the next one begins at 00:54 and stops it.
    assert fetch.asked == [small, big]
    assert text == GLOBAL_KILL + BRIDGE_KILL


def test_ranged_read_keeps_going_past_a_broken_stream():
    # The exact failure that fired: the big read dies mid-stream. A smaller
    # tail that does not span the window must not be handed back as the answer.
    small, big, third = oom_history.TAIL_STEPS[:3]
    broke = OSError("stream error: stream ID 1; INTERNAL_ERROR; received from peer")
    fetch = FakeFetch(dict.fromkeys(oom_history.TAIL_STEPS, BRIDGE_KILL))
    fetch.answers[small] = "kernel: no stamp here\n"
    fetch.answers[big] = broke
    text = oom_history.read_kern_log(
        "server2", fetch=fetch,
        window_start=datetime(2026, 9, 3, tzinfo=timezone.utc))
    assert fetch.asked == [small, big, third]
    assert text == BRIDGE_KILL


def test_ranged_read_raises_when_every_attempt_fails():
    broke = OSError("stream error: stream ID 1; INTERNAL_ERROR; received from peer")
    fetch = FakeFetch(dict.fromkeys(oom_history.TAIL_STEPS, broke))
    try:
        oom_history.read_kern_log("server2", fetch=fetch, window_start=WINDOW_START)
    except OSError as problem:
        assert "INTERNAL_ERROR" in str(problem)
    else:
        raise AssertionError("a read that never succeeded must not read as clean")


def test_largest_read_is_the_answer_even_when_it_does_not_span_the_window():
    # The log is rotated: nothing reaches back that far and nothing ever will.
    # `sweep_one` already prints the oldest stamp it saw, so the honest move is
    # to return the whole file rather than to raise.
    fetch = FakeFetch(dict.fromkeys(oom_history.TAIL_STEPS, GLOBAL_KILL))
    assert oom_history.read_kern_log(
        "server2", fetch=fetch,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc)) == GLOBAL_KILL
    assert fetch.asked == list(oom_history.TAIL_STEPS[:2])


def test_kubectl_path_is_untouched_when_no_fetch_is_given():
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=GLOBAL_KILL, stderr="")

    assert oom_history.read_kern_log("server1", runner=runner) == GLOBAL_KILL
    assert calls[0][:3] == ["kubectl", "get", "--raw"]


def test_no_service_account_means_no_ranged_reader(monkeypatch):
    # Guards the one thing that would put a real API call inside a unit test.
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    assert oom_history.api_fetch_factory() is None


def test_escalation_stops_once_the_whole_log_is_in_hand():
    # Two steps that return the same bytes means the file ran out; a third
    # request is the same download again, which is the cost this fix exists
    # to remove. Measured on server2: steps three and four both returned the
    # same 12.3MB.
    fetch = FakeFetch(dict.fromkeys(oom_history.TAIL_STEPS, GLOBAL_KILL))
    assert oom_history.read_kern_log(
        "server2", fetch=fetch,
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc)) == GLOBAL_KILL
    assert fetch.asked == list(oom_history.TAIL_STEPS[:2])
