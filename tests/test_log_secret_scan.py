"""`tools.log_secret_scan` — a credential-shaped string in a live pod log.

Every token literal here is built by concatenation. A test file carrying a
whole token-shaped string is what put a 22-day secret-scanning alert on
`tools/sync_contract.py`, and the fixture is no less honest for being glued
together at import time.
"""

import json

import pytest

from tools import log_secret_scan as mod


# Shaped like the real thing, made of nothing. Concatenated so no complete
# token-shaped literal ever appears in this file.
FAKE_GH = "gh" + "p_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
FAKE_PAT = "github" + "_pat_" + "11ABCDEFG0" + "abcdefghij" + "klmnopqrst" + "uvwx"
FAKE_ANT = "sk-" + "ant-" + "api03-" + "AAAABBBBCCCCDDDDEEEE"
FAKE_AWS = "AKIA" + "IOSFODNN7EXAMPLE"


class FakeRunner:
    """Stands in for `subprocess.run`, answering by argv shape."""

    def __init__(self, pods, logs, log_error=None):
        self.pods = pods
        self.logs = logs
        self.log_error = log_error or {}
        self.calls = []

    def __call__(self, args, capture_output=False, text=False, timeout=None):
        self.calls.append(list(args))

        class Proc:
            pass

        proc = Proc()
        if args[1] == "get":
            proc.returncode = 0
            proc.stdout = json.dumps(self.pods)
            proc.stderr = ""
            return proc
        key = (args[4], args[6])  # pod, container
        if key in self.log_error:
            proc.returncode = 1
            proc.stdout = ""
            proc.stderr = self.log_error[key]
            return proc
        proc.returncode = 0
        proc.stdout = self.logs.get(key, "")
        proc.stderr = ""
        return proc


def pod(namespace, name, container, phase="Running", started=True):
    state = {"running": {}} if started else {"waiting": {"reason": "PodInitializing"}}
    return {
        "metadata": {"namespace": namespace, "name": name},
        "spec": {"containers": [{"name": container}]},
        "status": {
            "phase": phase,
            "containerStatuses": [
                {"name": container, "state": state, "restartCount": 0}
            ],
        },
    }


def pods(*items):
    return {"items": list(items)}


# --- the patterns ---------------------------------------------------------

@pytest.mark.parametrize("blob, expected", [
    (f"cloning https://{FAKE_GH}@github.com/SokratesAI/vault", "github token"),
    (f"Authorization: Bearer {FAKE_PAT}", "github fine-grained token"),
    (f"ANTHROPIC_API_KEY={FAKE_ANT}", "anthropic key"),
    (f"aws_access_key_id = {FAKE_AWS}", "aws access key id"),
    ("-----BEGIN OPENSSH PRIVATE KEY-----", "private key block"),
    ("psql postgres://nova:hunter2@db.internal:5432/agora", "credential in a url"),
])
def test_a_credential_shaped_line_is_matched(blob, expected):
    hits = mod.scan_text(f"starting up\n{blob}\ndone\n")
    assert (expected, 2) in hits, hits


@pytest.mark.parametrize("blob", [
    "GET http://nova-site.agents.svc.cluster.local:8083/api/board",
    "connecting to redis://redis.agents.svc.cluster.local:6379/0",
    "image docker.io/library/python@sha256:3f9c1b2a4d5e6f708192a3b4c5d6e7f8",
    "level=info msg=\"backup finished\" bytes=418223",
    "ghost_writer_started for user 42",
])
def test_an_ordinary_log_line_is_not_matched(blob):
    assert mod.scan_text(blob) == []


def test_a_short_prefixed_word_is_not_a_token():
    # The length bound is what keeps these rules off ordinary prose. Without
    # it every `ghp_` or `AKIA` in a log line is a finding, and a check that
    # cries wolf every cycle is one nobody reads.
    assert mod.scan_text("gh" + "p_x") == []
    assert mod.scan_text("gh" + "s_" + "A" * 12) == []
    assert mod.scan_text("AKIA" + "SHORT") == []
    assert mod.scan_text("sk-" + "ant-" + "abc") == []


def test_a_password_only_url_is_matched():
    # `redis://:pw@host` has no username at all, which is the ordinary shape
    # for a service that authenticates on a password alone.
    assert mod.scan_text("redis://:s3cr3t@redis.agents.svc.cluster.local:6379/0") == [
        ("credential in a url", 1)]


def test_a_port_after_a_host_is_not_read_as_a_password():
    # The URL rule is the one most likely to cry wolf: every log here is full
    # of `host:port`. What separates a credential is the `@` after the colon.
    assert mod.scan_text("dialing 100.84.38.77:10250 for logs") == []
    assert mod.scan_text("ssh nova@100.89.37.25") == []


def test_the_line_number_is_where_the_match_is():
    text = "\n".join(["a", "b", f"key={FAKE_ANT}", "d"])
    assert mod.scan_text(text) == [("anthropic key", 3)]


# --- reading the cluster --------------------------------------------------

def test_every_container_of_every_pod_is_listed():
    body = pods(pod("obsidian", "vault-backup-1", "backup"),
                pod("agents", "nova-site-9", "site"))
    rows, why = mod.read_containers(FakeRunner(body, {}))
    assert why is None
    assert {(r["namespace"], r["pod"], r["container"]) for r in rows} == {
        ("obsidian", "vault-backup-1", "backup"),
        ("agents", "nova-site-9", "site"),
    }


def test_an_init_container_is_read_too():
    item = pod("agents", "marcus-1", "marcus")
    item["spec"]["initContainers"] = [{"name": "restore"}]
    item["status"]["initContainerStatuses"] = [
        {"name": "restore", "state": {"terminated": {"exitCode": 0}}, "restartCount": 0}
    ]
    rows, why = mod.read_containers(FakeRunner(pods(item), {}))
    assert why is None
    assert ("marcus-1", "restore") in {(r["pod"], r["container"]) for r in rows}
    assert all(r["started"] for r in rows)


def test_a_container_that_exited_still_counts_as_started():
    item = pod("obsidian", "vault-backup-1", "backup", phase="Failed")
    item["status"]["containerStatuses"][0]["state"] = {"terminated": {"exitCode": 1}}
    rows, _ = mod.read_containers(FakeRunner(pods(item), {}))
    assert rows[0]["started"] is True


def test_a_container_that_has_never_run_is_not_started():
    item = pod("agents", "pending-1", "app", phase="Pending", started=False)
    rows, _ = mod.read_containers(FakeRunner(pods(item), {}))
    assert rows[0]["started"] is False


def test_a_refused_pod_list_is_a_reason_not_an_empty_list():
    class Refused:
        def __call__(self, args, **kwargs):
            class Proc:
                returncode = 1
                stdout = ""
                stderr = "Error from server (Forbidden): pods is forbidden"
            return Proc()

    rows, why = mod.read_containers(Refused())
    assert rows is None
    assert "Forbidden" in why


# --- the verdict ----------------------------------------------------------

def test_a_token_in_a_log_exits_2_and_names_the_pod():
    body = pods(pod("obsidian", "vault-backup-1", "backup"))
    logs = {("vault-backup-1", "backup"): f"--- database: obsidian\nhttps://{FAKE_GH}@github.com/x\n"}
    runner = FakeRunner(body, logs)
    out = []
    status = mod.main(["--workers", "1"], runner=runner)
    assert status == 2


def test_the_matched_secret_is_never_printed(capsys):
    body = pods(pod("obsidian", "vault-backup-1", "backup"))
    logs = {("vault-backup-1", "backup"): f"https://{FAKE_GH}@github.com/x\n"}
    mod.main(["--workers", "1"], runner=FakeRunner(body, logs))
    printed = capsys.readouterr().out
    assert FAKE_GH not in printed
    # It still has to say enough to go and look.
    assert "obsidian/vault-backup-1" in printed
    assert "backup" in printed
    assert "github token" in printed


def test_a_clean_cluster_exits_0_and_says_how_far_back_it_looked(capsys):
    body = pods(pod("agents", "nova-site-9", "site"))
    logs = {("nova-site-9", "site"): "GET /api/board 200\n"}
    status = mod.main(["--workers", "1", "--tail", "200"], runner=FakeRunner(body, logs))
    printed = capsys.readouterr().out
    assert status == 0
    assert "200 line(s)" in printed
    assert "outside this" in printed


def test_a_pod_whose_log_is_refused_never_reads_as_clean(capsys):
    body = pods(pod("agents", "nova-site-9", "site"),
                pod("agents", "shy-1", "app"))
    logs = {("nova-site-9", "site"): "GET /api/board 200\n"}
    runner = FakeRunner(body, logs,
                        log_error={("shy-1", "app"): "Error from server: 502 Bad Gateway"})
    status = mod.main(["--workers", "1"], runner=runner)
    printed = capsys.readouterr().out
    assert status == 1
    assert "COULD NOT READ" in printed
    assert "agents/shy-1" in printed


def test_a_refused_pod_does_not_hide_a_credential_found_elsewhere():
    # Exit 2 outranks exit 1: a partial sweep that found something still
    # found something.
    body = pods(pod("obsidian", "vault-backup-1", "backup"), pod("agents", "shy-1", "app"))
    logs = {("vault-backup-1", "backup"): f"key={FAKE_ANT}\n"}
    runner = FakeRunner(body, logs,
                        log_error={("shy-1", "app"): "Error from server: 502"})
    assert mod.main(["--workers", "1"], runner=runner) == 2


def test_a_pod_that_never_started_is_named_and_not_counted_unreadable(capsys):
    body = pods(pod("agents", "nova-site-9", "site"),
                pod("agents", "pending-1", "app", phase="Pending", started=False))
    logs = {("nova-site-9", "site"): "ok\n"}
    runner = FakeRunner(body, logs)
    status = mod.main(["--workers", "1"], runner=runner)
    printed = capsys.readouterr().out
    assert status == 0
    assert "not judged" in printed
    assert "agents/pending-1" in printed
    # And its log was never asked for.
    assert not any(c[1] == "logs" and c[4] == "pending-1" for c in runner.calls)


def test_no_pods_at_all_is_unreadable_not_clean(capsys):
    status = mod.main([], runner=FakeRunner({"items": []}, {}))
    assert status == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_the_tail_bound_is_passed_to_kubectl():
    body = pods(pod("agents", "nova-site-9", "site"))
    runner = FakeRunner(body, {("nova-site-9", "site"): "ok\n"})
    mod.main(["--workers", "1", "--tail", "37"], runner=runner)
    log_calls = [c for c in runner.calls if c[1] == "logs"]
    assert log_calls and "--tail=37" in log_calls[0]
