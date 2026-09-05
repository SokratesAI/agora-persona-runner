"""Nothing watched the node disk, and a local-path claim's size is enforced by nothing.

The failure these pin is the one the tool was built around: every claim in
this cluster reports the *node filesystem's* used/capacity, so a reader that
judged a volume against its requested 1Gi would print thousands of percent
for every volume forever, and a reader that judged it against the reported
capacity would call a 72%-full node disk a healthy 1Gi volume.
"""

import json
import subprocess

from tools import disk_health


NODE_CAPACITY = 80307429376
NODE_AVAILABLE = 19111919616


def _summary(volumes=(), nodefs=True, imagefs=True, node_available=NODE_AVAILABLE):
    node = {"nodeName": "server1"}
    if nodefs:
        node["fs"] = {
            "capacityBytes": NODE_CAPACITY,
            "availableBytes": node_available,
            "usedBytes": NODE_CAPACITY - node_available,
        }
    if imagefs:
        node["runtime"] = {
            "imageFs": {
                "capacityBytes": NODE_CAPACITY,
                "availableBytes": node_available,
                "usedBytes": NODE_CAPACITY - node_available,
            }
        }
    pods = []
    for namespace, claim, pod, capacity, available in volumes:
        pods.append(
            {
                "podRef": {"namespace": namespace, "name": pod},
                "volume": [
                    {
                        "pvcRef": {"namespace": namespace, "name": claim},
                        "capacityBytes": capacity,
                        "availableBytes": available,
                        "usedBytes": capacity - available,
                    }
                ],
            }
        )
    return {"node": node, "pods": pods}


def _runner(nodes=("server1",), summaries=None, node_list_rc=0, claims=(), pods=()):
    summaries = summaries or {}

    def run(argv, capture_output=False, text=False):
        if argv[:3] == ["kubectl", "get", "pvc"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": list(claims)}), ""
            )
        if argv[:3] == ["kubectl", "get", "pods"]:
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"items": list(pods)}), ""
            )
        if argv[:3] == ["kubectl", "get", "nodes"]:
            body = {"items": [{"metadata": {"name": name}} for name in nodes]}
            return subprocess.CompletedProcess(
                argv, node_list_rc, json.dumps(body), "no route to the API server"
            )
        assert argv[:3] == ["kubectl", "get", "--raw"], argv
        node = argv[3].split("/")[4]
        answer = summaries.get(node)
        if answer is None:
            return subprocess.CompletedProcess(argv, 1, "", "Error from server: 502")
        return subprocess.CompletedProcess(argv, 0, json.dumps(answer), "")

    return run


def _no_host_read(node):
    """The host-directory read these tests deliberately do not wire up.

    It is only asked for on a node that raised, and it costs a real Job on that
    node. Refusing here keeps that cost out of every test that is about the
    kubelet numbers, and the refusal prints its own line rather than vanishing.
    """
    raise OSError("this test did not wire up the host-directory read")


def _lines(argv=None, host_reader=_no_host_read, **kwargs):
    printed = []
    code = disk_health.main(
        argv or [],
        runner=_runner(**kwargs),
        out=printed.append,
        host_reader=host_reader,
    )
    return code, "\n".join(printed)


def test_a_local_path_claim_is_not_judged_against_its_reported_capacity():
    """The volume reports the node's disk, so it must print NOT CAPPED, not `ok`.

    A reader that took `capacityBytes` at face value would call marcus-data a
    74.8GiB volume with 17.8GiB free — which is the node, not the volume.
    """
    summary = _summary(
        volumes=[("agents", "marcus-data", "marcus-1", NODE_CAPACITY, NODE_AVAILABLE)]
    )
    code, text = _lines(summaries={"server1": summary})
    assert "NOT CAPPED agents/marcus-data" in text
    assert "enforced by nothing" in text
    assert "0 claim(s) report a size of their own; 1 report the node's disk" in text
    assert code == 0


def test_a_volume_with_a_capacity_of_its_own_is_judged_against_it():
    """The uncapped branch must not swallow a real quota-backed volume.

    Precondition this test depends on: the node itself is healthy here
    (23.8% free), so the only thing that can raise is the volume.
    """
    summary = _summary(
        volumes=[("agents", "real-data", "pod-1", 1073741824, 10737418)]  # 1GiB, 1% free
    )
    assert disk_health.available_pct(summary["node"]["fs"]) > disk_health.raises_at(
        "nodefs"
    )
    code, text = _lines(summaries={"server1": summary})
    assert "FILLING    agents/real-data" in text
    assert "NOT CAPPED" not in text
    assert code == 2


def test_a_node_inside_the_margin_raises_and_names_the_kubelets_own_point():
    """Free space between the eviction threshold and the margin is the finding.

    12% free is above the kubelet's 10% nodefs eviction point and below this
    check's 15%, so it is exactly the window the margin exists to catch.
    """
    twelve_pct = int(NODE_CAPACITY * 0.12)
    summary = _summary(node_available=twelve_pct)
    code, text = _lines(summaries={"server1": summary})
    assert "FILLING    server1 nodefs" in text
    assert "the kubelet acts at 10.0%" in text
    assert code == 2


def test_a_node_above_the_margin_does_not_raise():
    code, text = _lines(summaries={"server1": _summary()})
    assert "ok         server1 nodefs" in text
    assert "FILLING" not in text
    assert code == 0


def test_an_unreadable_kubelet_exits_one_and_is_named():
    """A node the sweep could not reach must never read as a clean node."""
    code, text = _lines(
        nodes=("server1", "server2"), summaries={"server1": _summary()}
    )
    assert "CANNOT READ server2" in text
    assert "the sweep is partial" in text
    assert code == 1


def test_an_unreadable_node_list_exits_one_and_sweeps_nothing():
    code, text = _lines(node_list_rc=1)
    assert "CANNOT READ the node list" in text
    assert "not a clean result" in text
    assert "== server1" not in text
    assert code == 1


def test_a_missing_filesystem_block_reads_as_unread_not_as_full():
    """An absent imagefs and a full one must not print the same thing.

    The *reason* is asserted, not just the NOT READ prefix: "the kubelet
    published no block for it" and "published no availableBytes" are two
    different states of the kubelet and the first mutation I ran survived on
    a test that only checked the prefix they share.
    """
    code, text = _lines(summaries={"server1": _summary(imagefs=False)})
    assert "NOT READ   server1 imagefs — the kubelet published no block for it" in text
    assert "FILLING    server1 imagefs" not in text
    assert code == 0


def test_a_filesystem_block_with_no_available_bytes_says_which_half_is_missing():
    """A published block missing availableBytes is a different state again."""
    summary = _summary()
    del summary["node"]["fs"]["availableBytes"]
    code, text = _lines(summaries={"server1": summary})
    assert "NOT READ   server1 nodefs — the kubelet published no availableBytes" in text
    assert "FILLING    server1 nodefs" not in text
    assert code == 0


def test_the_margin_sits_above_the_kubelets_own_eviction_point():
    """The threshold is the kubelet's number plus a margin, checked against
    the literal values rather than against `raises_at`'s own arithmetic —
    the first version of this test restated the implementation line and could
    not fail.
    """
    assert disk_health.EVICTION_PCT == {"nodefs": 10.0, "imagefs": 15.0}
    assert disk_health.raises_at("nodefs") == 15.0
    assert disk_health.raises_at("imagefs") == 20.0


def test_a_real_finding_outranks_a_node_the_sweep_could_not_read():
    """Exit 2, not 1, when one node is critical and another is unreachable.

    preflight collapses 1 to `UNREADABLE`, the word for "nothing could be
    judged" — printing it over a node that WAS judged and is at 2% free is the
    less urgent answer winning.
    """
    two_pct = int(NODE_CAPACITY * 0.02)
    code, text = _lines(
        nodes=("server1", "server2"),
        summaries={"server1": _summary(node_available=two_pct)},
    )
    assert "FILLING    server1 nodefs" in text
    assert "CANNOT READ server2" in text
    assert code == 2


def test_the_last_line_carrying_a_digit_varies_with_the_result():
    """preflight's roster row is the last digit-bearing line, so it must move.

    The first version ended on a fixed disclaimer sentence, which meant the
    collapsed row said the same thing whatever the sweep found.
    """
    def last_number_line(text):
        return [ln for ln in text.splitlines() if any(c.isdigit() for c in ln)][-1]

    _, one = _lines(summaries={"server1": _summary()})
    _, two = _lines(
        nodes=("server1", "server2"),
        summaries={
            "server1": _summary(
                volumes=[("agents", "m", "p", NODE_CAPACITY, NODE_AVAILABLE)]
            ),
            "server2": _summary(),
        },
    )
    assert last_number_line(one) != last_number_line(two)
    assert last_number_line(two).startswith("Swept 2 node(s)")


def test_is_capped_compares_against_every_filesystem_the_node_reports():
    """imagefs and nodefs can differ; a volume matching either one is the disk."""
    filesystems = {
        "nodefs": {"capacityBytes": NODE_CAPACITY},
        "imagefs": {"capacityBytes": 12345},
    }
    assert disk_health.is_capped({"capacityBytes": NODE_CAPACITY}, filesystems) is False
    assert disk_health.is_capped({"capacityBytes": 12345}, filesystems) is False
    assert disk_health.is_capped({"capacityBytes": 999}, filesystems) is True
    assert disk_health.is_capped({"capacityBytes": None}, filesystems) is None


def test_a_volume_with_no_pvcref_is_not_a_claim():
    """emptyDir and configMap volumes appear in the same list and are not claims."""
    summary = _summary()
    summary["pods"] = [
        {
            "podRef": {"namespace": "agents", "name": "pod-1"},
            "volume": [
                {"name": "scratch", "capacityBytes": 100, "availableBytes": 50},
                {
                    "pvcRef": {"namespace": "agents", "name": "real"},
                    "capacityBytes": 100,
                    "availableBytes": 50,
                },
            ],
        }
    ]
    volumes = disk_health.pvc_volumes(summary)
    assert [v["claim"] for v in volumes] == ["real"]


def _done(stdout, returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["kubectl"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _claim(namespace, name, tracking=None, phase="Bound", storage="10Gi"):
    metadata = {
        "namespace": namespace,
        "name": name,
        "creationTimestamp": "2026-02-23T12:54:28Z",
    }
    if tracking:
        metadata["annotations"] = {"argocd.argoproj.io/tracking-id": tracking}
    return {
        "metadata": metadata,
        "spec": {"resources": {"requests": {"storage": storage}}},
        "status": {"phase": phase},
    }


def test_read_mounted_claims_reads_pod_volumes_not_workload_templates():
    pods = {
        "items": [
            {
                "metadata": {"namespace": "agents"},
                "spec": {
                    "volumes": [
                        {"name": "tmp", "emptyDir": {}},
                        {
                            "name": "data",
                            "persistentVolumeClaim": {"claimName": "marcus-data"},
                        },
                    ]
                },
            }
        ]
    }

    def runner(argv, **kwargs):
        assert argv[:3] == ["kubectl", "get", "pods"]
        return _done(json.dumps(pods))

    assert disk_health.read_mounted_claims(runner=runner) == {("agents", "marcus-data")}


def test_unmounted_claims_separates_owned_from_orphaned():
    claims = [
        _claim("agents", "marcus-data"),
        _claim("infra", "ollama-models", tracking="sokratesai-deployments:/x"),
        _claim("infra", "nats-js-nats-0"),
    ]
    found = disk_health.unmounted_claims(claims, {("agents", "marcus-data")})
    assert [(c["metadata"]["name"], owner) for c, owner in found] == [
        ("ollama-models", "sokratesai-deployments"),
        ("nats-js-nats-0", None),
    ]


def test_only_an_untracked_unmounted_claim_raises():
    lines = []
    findings = disk_health.report_unmounted(
        [
            (_claim("infra", "ollama-models"), "sokratesai-deployments"),
            (_claim("infra", "nats-js-nats-0"), None),
        ],
        out=lines.append,
    )
    assert findings == 1
    parked = [line for line in lines if "PARKED" in line]
    orphaned = [line for line in lines if "ORPHANED" in line]
    assert len(parked) == 1 and "ollama-models" in parked[0]
    assert len(orphaned) == 1 and "nats-js-nats-0" in orphaned[0]
    # The parked line must say who owns it — that is the whole reason it does
    # not raise, and a reader has to be able to check the claim.
    assert "sokratesai-deployments" in parked[0]


def test_an_unreadable_claim_list_is_never_clean():
    def runner(argv, **kwargs):
        if argv[:3] == ["kubectl", "get", "pvc"]:
            return _done("", returncode=1, stderr="Forbidden")
        return _done(json.dumps({"items": []}))

    lines = []
    status = disk_health.main(["--node", "server1"], runner=runner, out=lines.append)
    assert status == 1
    assert any("CANNOT READ the claim list" in line for line in lines)


GIB = 1024**3


def _breakdown_summary(images=None, ephemeral=(), image_capacity=NODE_CAPACITY):
    """A node whose image store is on the root disk unless `image_capacity` differs."""
    used = NODE_CAPACITY - NODE_AVAILABLE
    node = {
        "nodeName": "server1",
        "fs": {
            "capacityBytes": NODE_CAPACITY,
            "availableBytes": NODE_AVAILABLE,
            "usedBytes": used,
        },
        "runtime": {
            "imageFs": {
                "capacityBytes": image_capacity,
                "availableBytes": NODE_AVAILABLE,
                "usedBytes": used if images is None else images,
            }
        },
    }
    pods = [{"podRef": {"namespace": "agents", "name": "p%d" % i},
             "ephemeral-storage": {"usedBytes": size}}
            for i, size in enumerate(ephemeral)]
    return {"node": node, "pods": pods}


def test_image_store_on_the_root_disk_is_named_as_the_same_disk():
    summary = _breakdown_summary(images=10 * GIB)
    assert disk_health.shares_one_filesystem(disk_health.node_filesystems(summary))


def test_a_separate_image_disk_is_not_read_as_the_same_disk():
    summary = _breakdown_summary(images=10 * GIB, image_capacity=NODE_CAPACITY // 2)
    assert not disk_health.shares_one_filesystem(disk_health.node_filesystems(summary))


def test_a_node_publishing_no_image_store_shares_nothing():
    summary = _summary(imagefs=False)
    assert not disk_health.shares_one_filesystem(disk_health.node_filesystems(summary))


def test_breakdown_splits_used_into_images_ephemeral_and_the_rest():
    summary = _breakdown_summary(images=10 * GIB, ephemeral=(GIB, GIB // 2))
    got = disk_health.usage_breakdown(summary, disk_health.node_filesystems(summary))
    assert got["images"] == 10 * GIB
    assert got["ephemeral"] == GIB + GIB // 2
    assert got["used"] == NODE_CAPACITY - NODE_AVAILABLE
    # The remainder is what the kubelet cannot attribute, and it must be the
    # arithmetic complement rather than a second measurement.
    assert got["rest"] == got["used"] - got["images"] - got["ephemeral"]


def test_breakdown_refuses_when_the_image_store_is_a_different_disk():
    """Its bytes are not part of this disk's used total, so subtracting them lies."""
    summary = _breakdown_summary(images=10 * GIB, image_capacity=NODE_CAPACITY // 2)
    assert disk_health.usage_breakdown(summary, disk_health.node_filesystems(summary)) is None


def test_breakdown_refuses_when_the_node_published_no_used_bytes():
    summary = _breakdown_summary(images=10 * GIB)
    summary["node"]["fs"].pop("usedBytes")
    assert disk_health.usage_breakdown(summary, disk_health.node_filesystems(summary)) is None


def test_report_prints_the_breakdown_and_it_does_not_raise():
    summary = _breakdown_summary(images=10 * GIB, ephemeral=(GIB,))
    filesystems = disk_health.node_filesystems(summary)
    lines = []
    findings = disk_health.report(
        "server1",
        filesystems,
        [],
        out=lines.append,
        breakdown=disk_health.usage_breakdown(summary, filesystems),
    )
    made = [line for line in lines if "MADE OF" in line]
    assert len(made) == 1
    assert "10.0GiB of container images" in made[0]
    assert "1.0GiB of Pod ephemeral storage" in made[0]
    assert findings == 0


def test_the_second_filesystem_line_says_it_is_not_a_second_disk():
    summary = _breakdown_summary(images=10 * GIB)
    lines = []
    disk_health.report("server1", disk_health.node_filesystems(summary), [], out=lines.append)
    imagefs = [line for line in lines if "imagefs" in line]
    assert len(imagefs) == 1
    assert "the same disk as nodefs" in imagefs[0]
    nodefs = [line for line in lines if "nodefs:" in line]
    assert "the same disk" not in nodefs[0]


def _made_of(summary):
    filesystems = disk_health.node_filesystems(summary)
    lines = []
    disk_health.report(
        "server1",
        filesystems,
        [],
        out=lines.append,
        breakdown=disk_health.usage_breakdown(summary, filesystems),
    )
    return [line for line in lines if "MADE OF" in line]


def test_a_node_with_no_ephemeral_usage_reads_as_zero_not_as_unknown():
    """`_gib`'s "?" means the kubelet published nothing; 0 here is an answer."""
    made = _made_of(_breakdown_summary(images=10 * GIB, ephemeral=()))
    assert len(made) == 1
    assert "0.0GiB of Pod ephemeral storage" in made[0]
    assert "?" not in made[0]


def test_an_over_attributed_disk_says_so_instead_of_printing_a_negative_size():
    """imagefs and per-Pod ephemeral are sampled seconds apart and can overlap."""
    used = NODE_CAPACITY - NODE_AVAILABLE
    summary = _breakdown_summary(images=used, ephemeral=(3 * GIB,))
    made = _made_of(summary)
    assert len(made) == 1
    assert "-" not in made[0].split("MADE OF")[1]
    assert "3.0GiB MORE than" in made[0]
    assert "no remainder to name" in made[0]


def test_a_real_run_prints_the_breakdown():
    """The wiring in main() is the only path preflight and a person actually take."""
    summary = _breakdown_summary(images=10 * GIB, ephemeral=(GIB,))
    lines = []
    code = disk_health.main(
        ["--node", "server1"],
        runner=_runner(nodes=("server1",), summaries={"server1": summary}),
        out=lines.append,
    )
    made = [line for line in lines if "MADE OF" in line]
    assert len(made) == 1, lines
    assert "10.0GiB of container images" in made[0]
    assert code == 0


# --- naming the host directories when a disk fills ------------------------
#
# The gap these pin: on 2026-09-05 server1 crossed the threshold and the check
# said 31.6GiB was "neither" — local-path volumes and whatever the host stores.
# Finding out what that actually was took five hand-run Jobs. The answer was a
# 4.1GiB systemd journal, 5.0GiB of build caches in /root and 2.5GiB of this
# loop's own restore-point archives, and none of it is visible to a kubelet.


def _filling_summary():
    """A node inside the margin above its own eviction threshold."""
    return _summary(node_available=int(NODE_CAPACITY * 0.12))


def test_the_command_measures_every_named_directory_under_the_mount():
    command = disk_health.host_breakdown_command(("/var/log/journal", "/root"), mount="/host")
    assert '"/host/var/log/journal"' in command
    assert '"/host/root"' in command
    # The label printed is the host's path, not the mount's -- a reader of the
    # output cares where it is on the node, not where this Job mounted it, and
    # /host/root is not a directory that exists on server1.
    assert 'printf "%s " "/root"' in command
    assert 'printf "%s " "/host/root"' not in command
    assert "du -xsk" in command


def test_a_directory_that_does_not_exist_is_left_out_rather_than_read_as_zero():
    sizes = disk_health.parse_host_breakdown(
        "/var/log/journal 4179968\n/home \n/root 5079040\n"
    )
    assert sizes == {"/var/log/journal": 4179968 * 1024, "/root": 5079040 * 1024}
    assert "/home" not in sizes


def test_the_parser_ignores_anything_that_is_not_a_path_and_a_number():
    sizes = disk_health.parse_host_breakdown(
        "du: /host/opt: Permission denied\nnot a path 12\n/srv 4\n"
    )
    assert sizes == {"/srv": 4 * 1024}


def test_a_node_with_room_never_pays_for_the_host_read():
    # The whole reason this is conditional. `preflight` runs every cycle and a
    # Job per node is tens of seconds; a normal morning must not buy it.
    calls = []
    findings = disk_health.report(
        "server1",
        disk_health.node_filesystems(_summary()),
        [],
        out=lambda line: None,
        host_reader=calls.append,
    )
    assert calls == []
    assert findings == 0


def test_a_filling_node_names_the_host_directories_biggest_first():
    lines = []
    findings = disk_health.report(
        "server1",
        disk_health.node_filesystems(_filling_summary()),
        [],
        out=lines.append,
        host_reader=lambda node: {
            "/root": 5 * GIB,
            "/var/log/journal": 4 * GIB,
            "/var/lib/nova-attic": 2 * GIB,
        },
    )
    assert findings > 0
    host = [line for line in lines if "HOST DIR" in line and "GiB" in line]
    assert [line.split()[3] for line in host] == [
        "/root",
        "/var/log/journal",
        "/var/lib/nova-attic",
    ]
    assert "5.0GiB" in host[0]


def test_the_host_reader_is_asked_about_the_node_that_raised():
    asked = []
    disk_health.report(
        "server2",
        disk_health.node_filesystems(_filling_summary()),
        [],
        out=lambda line: None,
        host_reader=lambda node: asked.append(node) or {"/root": GIB},
    )
    assert asked == ["server2"]


def test_directories_absent_on_this_node_are_named_rather_than_dropped():
    lines = []
    disk_health.report(
        "server1",
        disk_health.node_filesystems(_filling_summary()),
        [],
        out=lines.append,
        host_reader=lambda node: {"/root": GIB},
    )
    missing = [line for line in lines if "do not exist on this node" in line]
    assert len(missing) == 1
    assert "/var/log/journal" in missing[0]


def test_a_host_read_that_fails_says_so_and_does_not_change_the_verdict():
    lines = []
    findings = disk_health.report(
        "server1",
        disk_health.node_filesystems(_filling_summary()),
        [],
        out=lines.append,
        host_reader=_raise(OSError("the test namespace refused the Job")),
    )
    not_read = [line for line in lines if "host directories" in line]
    assert len(not_read) == 1
    assert "the test namespace refused the Job" in not_read[0]
    # A detail line that could not be read must not move the verdict either way.
    # The same node with no host_reader at all is the control.
    without = disk_health.report(
        "server1",
        disk_health.node_filesystems(_filling_summary()),
        [],
        out=lambda line: None,
    )
    assert findings == without


def _raise(exc):
    def reader(node):
        raise exc

    return reader


class _FakeKubectl:
    """Records every kubectl call and answers the log read with `logs`."""

    def __init__(self, logs="", apply_returncode=0):
        self.calls = []
        self.applied = None
        self.logs = logs
        self.apply_returncode = apply_returncode

    def __call__(self, args, **kwargs):
        self.calls.append(args)
        stdout, returncode = "", 0
        if args[:2] == ["kubectl", "apply"]:
            returncode = self.apply_returncode
            self.applied = kwargs.get("input")
        if args[:2] == ["kubectl", "logs"]:
            stdout = self.logs
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="boom")


def test_the_job_is_deleted_before_it_is_applied():
    # A Job of the same name may still be inside its TTL from the last sweep,
    # and `kubectl apply` cannot change a Job's pod template -- without the
    # delete this would read the previous run's answer as this one's.
    kubectl = _FakeKubectl(logs="/root 1024\n")
    disk_health.read_host_breakdown("server1", runner=kubectl, wait=5)
    verbs = [args[1] for args in kubectl.calls]
    assert verbs == ["delete", "apply", "wait", "logs"]
    assert "--ignore-not-found=true" in kubectl.calls[0]


def test_the_job_is_pinned_to_the_node_that_raised():
    kubectl = _FakeKubectl(logs="/root 1024\n")
    disk_health.read_host_breakdown("server2", runner=kubectl, wait=5)
    manifest = json.loads(kubectl.applied)
    spec = manifest["spec"]["template"]["spec"]
    assert spec["nodeSelector"] == {"kubernetes.io/hostname": "server2"}
    assert spec["volumes"][0]["hostPath"]["path"] == "/"
    assert spec["containers"][0]["volumeMounts"][0]["readOnly"] is True


def test_a_refused_apply_is_an_error_rather_than_an_empty_disk():
    kubectl = _FakeKubectl(logs="/root 1024\n", apply_returncode=1)
    try:
        disk_health.read_host_breakdown("server1", runner=kubectl, wait=5)
    except OSError as exc:
        assert "boom" in str(exc)
    else:
        raise AssertionError("a refused apply must not read as a node with nothing on it")


def test_a_job_that_printed_nothing_is_an_error():
    kubectl = _FakeKubectl(logs="du: cannot read\n")
    try:
        disk_health.read_host_breakdown("server1", runner=kubectl, wait=5)
    except ValueError:
        pass
    else:
        raise AssertionError("no measurable directory must not read as a clean node")


# `tools.preflight` kills a check at 240s and reports it unreadable. A per-node
# timeout multiplies, so three nodes raising at once would have turned a real
# FILLING finding into a hung check at exactly the moment the finding matters.


def test_the_host_read_budget_is_shared_across_nodes_not_per_node():
    waits = []
    ticks = iter([0.0, 0.0, 100.0, 149.5])

    def read(node, runner=None, wait=None):
        waits.append(wait)
        return {"/root": GIB}

    reader = disk_health.budgeted_host_reader(
        budget=150, clock=lambda: next(ticks), read=read
    )
    reader("server1")
    reader("server2")
    try:
        reader("server3")
    except OSError as exc:
        assert "budget for this run is spent" in str(exc)
    else:
        raise AssertionError("the third node must not push the check past preflight's limit")
    # 150s left on the first node is capped by the per-read ceiling; 50s left on
    # the second is not, and that is the whole point of taking the minimum.
    assert waits == [disk_health.HOST_READ_SECONDS, 50]


def test_the_budget_leaves_room_for_the_rest_of_the_check_inside_preflight():
    from tools import preflight

    assert disk_health.HOST_READ_BUDGET_SECONDS < preflight.TIMEOUT_SECONDS
    assert disk_health.HOST_READ_SECONDS <= disk_health.HOST_READ_BUDGET_SECONDS
