"""The kernel log is the one record of an OOM kill that survives the restart."""
import subprocess
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


def lines(events, hours=24, pod_names=None, now=NOW, node="server1"):
    got = []
    status = oom_history.report(
        node, oom_history.within(events, hours, now=now), hours,
        pod_names or {}, "swept.", out=got.append)
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


def test_a_live_pod_is_named_and_a_dead_one_is_left_as_its_uid():
    events = oom_history.parse_events(BRIDGE_KILL)
    uid = "2c7713fd-2aed-4fa2-a60f-f006ab94b3df"
    _, named = lines(events, pod_names={uid: "agents/agora-claude-bridge-67459f6f88-w5x2b"})
    assert "agents/agora-claude-bridge-67459f6f88-w5x2b" in named
    _, unnamed = lines(events, pod_names={})
    assert "gone since, cannot be named" in unnamed
    assert uid in unnamed


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
    assert oom_history.main([], runner=runner, out=got.append, now=NOW) == 2
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
    assert oom_history.main([], runner=runner, out=got.append, now=NOW) == 2
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
