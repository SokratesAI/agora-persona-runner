"""`redact_coverage` judges the credentials Kubernetes says this pod holds.

The measurement these tests pin, taken on the live pods on 2026-08-31
before any of this was written: the bridge pod declares 4 secret-sourced
variables and `redact()` masks 3 of them (`CDB_PASS`, `GH_TOKEN`,
`CLAUDE_CREDENTIALS_JSON`); the fourth, `CDB_USER`, is five alphabetic
characters and is below the masking floor on purpose. The runner declares
8 and the same split holds -- 7 masked, `COUCHDB_USER` too short. So the
check's exit-0 path is the true one today, and every failing path here is
a fixture rather than a live reading.
"""

import io

from agora_runner import redact_coverage as rc


def _pod(name, workload, env=(), env_from=()):
    return {
        "metadata": {"name": name, "labels": {"app": workload}},
        "spec": {"containers": [{
            "name": workload,
            "env": [{"name": n, "valueFrom": {"secretKeyRef": {"name": "s", "key": n}}}
                    for n in env],
            "envFrom": [{"secretRef": {"name": r}} for r in env_from],
        }]},
    }


def _pods(*items):
    return {"items": list(items)}


def test_only_secret_sourced_env_is_declared():
    pod = _pod("agora-persona-runner-abc", "agora-persona-runner", env=["AGORA_TOKEN"])
    pod["spec"]["containers"][0]["env"].append({"name": "AGORA_URL", "value": "http://x"})
    by_workload, unenumerable = rc.declared_secrets(_pods(pod))
    assert by_workload == {"agora-persona-runner": ["AGORA_TOKEN"]}
    assert unenumerable == []


def test_a_pod_that_does_not_redact_is_not_judged():
    """The newspaper generator holds the Gemini key and publishes through no
    `redact()`, so counting it would report a gap that does not exist."""
    by_workload, _ = rc.declared_secrets(_pods(
        _pod("newspaper-generator-1", "newspaper", env=["GEMINI_API_KEY"])))
    assert by_workload == {}


def test_env_from_secret_ref_is_a_caveat_not_a_sweep():
    _, unenumerable = rc.declared_secrets(_pods(
        _pod("agora-claude-bridge-1", "agora-claude-bridge",
             env=["GH_TOKEN"], env_from=["agora-claude-bridge-secrets"])))
    assert unenumerable == [("agora-claude-bridge", "agora-claude-bridge-secrets")]


def test_an_unmasked_declared_secret_is_a_finding():
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-persona-runner-1", "agora-persona-runner", env=["GROQ_KEY"])),
        environ={"GROQ_KEY": "gsk-not-a-shape-anything-here-knows"},
        here="agora-persona-runner", out=out)
    text = out.getvalue()
    assert code == 2, text
    assert "NOT MASKED" in text and "GROQ_KEY" in text
    # The value itself is never printed -- only its length.
    assert "gsk-not-a-shape" not in text


def test_a_masked_declared_secret_passes():
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-persona-runner-1", "agora-persona-runner", env=["AGORA_TOKEN"])),
        environ={"AGORA_TOKEN": "z" * 64}, here="agora-persona-runner", out=out)
    assert code == 0, out.getvalue()
    assert "1 of 1 declared secret(s) masked" in out.getvalue()


def test_a_secret_below_the_masking_floor_is_named_and_does_not_raise():
    """`COUCHDB_USER` is five characters on the live runner pod. Masking a
    five-letter word would blank it out of every ordinary sentence."""
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-persona-runner-1", "agora-persona-runner", env=["COUCHDB_USER"])),
        environ={"COUCHDB_USER": "admin"}, here="agora-persona-runner", out=out)
    text = out.getvalue()
    assert code == 0, text
    assert "NOT JUDGED" in text and "COUCHDB_USER" in text
    assert "NOT MASKED" not in text


def test_the_other_workload_is_named_rather_than_passed():
    """A run reads one pod's environment. The other half must never be
    silently absent from the sweep -- exit 0 has to say what it did not read."""
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-claude-bridge-1", "agora-claude-bridge", env=["CDB_PASS"]),
              _pod("agora-persona-runner-1", "agora-persona-runner",
                   env=["AGORA_TOKEN", "TINYFISH_API_KEY"])),
        environ={"CDB_PASS": "q" * 16}, here="agora-claude-bridge", out=out)
    text = out.getvalue()
    assert code == 0, text
    assert "CANNOT JUDGE" in text
    assert "AGORA_TOKEN, TINYFISH_API_KEY" in text
    assert "2 more are" in text


def test_a_declared_name_missing_from_the_environment_is_never_a_pass():
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-persona-runner-1", "agora-persona-runner", env=["AGORA_TOKEN"])),
        environ={}, here="agora-persona-runner", out=out)
    text = out.getvalue()
    assert code == 0, text
    assert "CANNOT JUDGE" in text and "AGORA_TOKEN" in text
    assert "0 of 1 declared secret(s) masked" in text


def test_an_unreadable_pod_list_is_not_clean():
    out = io.StringIO()
    assert rc.report(None, environ={}, here="agora-persona-runner", out=out) == 1
    assert "CANNOT READ" in out.getvalue()


def test_no_matching_pod_is_not_clean():
    out = io.StringIO()
    assert rc.report(_pods(), environ={}, out=out) == 1
    assert "CANNOT READ" in out.getvalue()


def test_a_third_pod_reports_that_it_read_nothing():
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-persona-runner-1", "agora-persona-runner", env=["AGORA_TOKEN"])),
        environ={"AGORA_TOKEN": "z" * 64}, here=None, out=out)
    assert code == 1, out.getvalue()
    assert "neither redacting workload" in out.getvalue()


def test_running_workload_reads_the_pod_name_off_hostname():
    assert rc.running_workload({}, "agora-claude-bridge-78d5c87dd8-5kps5") == "agora-claude-bridge"
    assert rc.running_workload({}, "newspaper-57c6b4f98c-89mk8") is None
    assert rc.running_workload({"NOVA_WORKLOAD": "agora-persona-runner"}, "x") == "agora-persona-runner"
    assert rc.running_workload({"NOVA_WORKLOAD": "newspaper"}, "agora-claude-bridge-1") is None


def test_read_pods_returns_none_rather_than_raising_on_a_failed_kubectl():
    class Done:
        returncode = 1
        stdout = ""
    assert rc.read_pods(run=lambda *a, **k: Done()) is None
    assert rc.read_pods(run=lambda *a, **k: (_ for _ in ()).throw(OSError())) is None


def test_the_preflight_wrapper_exposes_the_same_main():
    import tools.redact_coverage as wrapper
    assert wrapper.main is rc.main
