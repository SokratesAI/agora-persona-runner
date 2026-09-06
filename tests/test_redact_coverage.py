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


def _svc(name, ports):
    return {"metadata": {"name": name},
            "spec": {"ports": [{"name": n, "port": p, "protocol": "TCP"}
                               for n, p in ports]}}


def test_service_link_names_are_generated_from_the_live_service_list():
    """Not a word list: a Service added tomorrow is covered without an edit."""
    names = rc.service_link_names({"items": [_svc("nova-site", [("http", 8083)])]})
    assert {"NOVA_SITE_SERVICE_HOST", "NOVA_SITE_SERVICE_PORT",
            "NOVA_SITE_SERVICE_PORT_HTTP", "NOVA_SITE_PORT",
            "NOVA_SITE_PORT_8083_TCP", "NOVA_SITE_PORT_8083_TCP_ADDR",
            "NOVA_SITE_PORT_8083_TCP_PORT",
            "NOVA_SITE_PORT_8083_TCP_PROTO"} <= names
    # The default namespace's own Service is injected too and is never listed
    # in `agents`, so it is added rather than read.
    assert "KUBERNETES_SERVICE_HOST" in names
    assert "KUBERNETES_PORT_443_TCP_ADDR" in names


def test_unaccounted_subtracts_the_declaration_and_the_links():
    rest = rc.unaccounted({"GH_TOKEN": "x", "AGORA_SERVICE_HOST": "1.2.3.4",
                           "GPG_KEY": "abc"},
                          declared={"GH_TOKEN"}, links={"AGORA_SERVICE_HOST"})
    assert rest == ["GPG_KEY"]


def test_declared_env_names_takes_the_plain_ones_too():
    pod = _pod("agora-claude-bridge-1", "agora-claude-bridge", env=["CDB_PASS"])
    pod["spec"]["containers"][0]["env"].append({"name": "PORT", "value": "8090"})
    assert rc.declared_env_names(_pods(pod), "agora-claude-bridge") == {"CDB_PASS", "PORT"}


def test_the_sweep_names_what_no_declaration_explains():
    """The clean summary must not imply it read the whole environment."""
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-claude-bridge-1", "agora-claude-bridge", env=["CDB_PASS"])),
        environ={"CDB_PASS": "q" * 16, "AGORA_SERVICE_HOST": "10.0.0.1",
                 "GPG_KEY": "a-base-image-thing"},
        here="agora-claude-bridge", out=out,
        services={"items": [_svc("agora", [("http", 8080)])]})
    text = out.getvalue()
    assert code == 0, text
    assert "NOT SWEPT" in text
    assert "1 variable(s)" in text and "GPG_KEY" in text
    assert "AGORA_SERVICE_HOST" not in text


def test_an_undeclared_variable_redact_masks_is_called_out():
    """redact() masking something nothing declares means a credential arrived
    by a route the pod spec does not describe -- the one case in the
    complement that is worth a sentence rather than a name."""
    out = io.StringIO()
    rc.report(
        _pods(_pod("agora-claude-bridge-1", "agora-claude-bridge", env=["CDB_PASS"])),
        environ={"CDB_PASS": "q" * 16, "SOME_TOKEN": "z" * 40},
        here="agora-claude-bridge", out=out, services={"items": []})
    text = out.getvalue()
    assert "redact() masks 1 of them (SOME_TOKEN)" in text
    assert "a route nothing here declares" in text


def test_an_unreadable_service_list_is_a_caveat_not_a_silent_pass():
    out = io.StringIO()
    code = rc.report(
        _pods(_pod("agora-claude-bridge-1", "agora-claude-bridge", env=["CDB_PASS"])),
        environ={"CDB_PASS": "q" * 16}, here="agora-claude-bridge", out=out,
        services=None)
    text = out.getvalue()
    assert code == 0, text
    assert "CANNOT JUDGE" in text and "Service list is unreadable" in text
    assert "NOT SWEPT" not in text


def test_read_services_returns_none_rather_than_raising():
    class Done:
        returncode = 1
        stdout = ""
    assert rc.read_services(run=lambda *a, **k: Done()) is None
    assert rc.read_services(run=lambda *a, **k: (_ for _ in ()).throw(OSError())) is None


# ---------------------------------------------------------------------------
# Secrets mounted as files.
#
# The live shape these pin, read off both pods on 2026-09-06 before the code
# was written: each of `agora-claude-bridge` and `agora-persona-runner` mounts
# exactly one Secret as files -- `nas-ssh-key` at `/etc/nas-ssh`, holding
# `id_ed25519` (masked by redact()'s PEM pattern) and `id_ed25519.pub` (not
# masked, and correctly so). Nothing judged that mount before this, because
# every pass in the module reads `os.environ` and a file is in no environment.


def _pod_with_mount(name, workload, secret="nas-ssh-key", path="/etc/nas-ssh",
                    extra_volumes=(), extra_mounts=()):
    pod = _pod(name, workload)
    pod["spec"]["volumes"] = [{"name": "k", "secret": {"secretName": secret}}]
    pod["spec"]["volumes"].extend(extra_volumes)
    pod["spec"]["containers"][0]["volumeMounts"] = [{"name": "k", "mountPath": path}]
    pod["spec"]["containers"][0]["volumeMounts"].extend(extra_mounts)
    return pod


def test_a_secret_volume_is_declared_with_the_path_it_mounts_at():
    pod = _pod_with_mount("agora-claude-bridge-abc", "agora-claude-bridge")
    assert rc.declared_secret_mounts(_pods(pod)) == {
        "agora-claude-bridge": [("nas-ssh-key", "/etc/nas-ssh")]}


def test_a_projected_or_configmap_volume_is_not_a_declared_secret_mount():
    """The ServiceAccount token is projected, and no manifest here asked for it."""
    pod = _pod_with_mount(
        "agora-claude-bridge-abc", "agora-claude-bridge",
        extra_volumes=[{"name": "sa", "projected": {"sources": []}},
                       {"name": "cm", "configMap": {"name": "c"}}],
        extra_mounts=[{"name": "sa", "mountPath": "/var/run/secrets/kubernetes.io/x"},
                      {"name": "cm", "mountPath": "/etc/conf"}])
    assert rc.declared_secret_mounts(_pods(pod)) == {
        "agora-claude-bridge": [("nas-ssh-key", "/etc/nas-ssh")]}


def test_a_secret_volume_that_no_container_mounts_is_not_declared():
    """A volume in the spec that nothing mounts puts no file on any filesystem."""
    pod = _pod("agora-claude-bridge-abc", "agora-claude-bridge")
    pod["spec"]["volumes"] = [{"name": "k", "secret": {"secretName": "nas-ssh-key"}}]
    pod["spec"]["containers"][0]["volumeMounts"] = []
    assert rc.declared_secret_mounts(_pods(pod)) == {"agora-claude-bridge": []}


def test_a_mount_entry_with_no_path_is_dropped_rather_than_crashing_the_sweep():
    """`judge_mounts` would hand `None` to `os.path.isdir` and raise."""
    pod = _pod("agora-claude-bridge-abc", "agora-claude-bridge")
    pod["spec"]["volumes"] = [{"name": "k", "secret": {"secretName": "nas-ssh-key"}}]
    pod["spec"]["containers"][0]["volumeMounts"] = [{"name": "k"}]
    assert rc.declared_secret_mounts(_pods(pod)) == {"agora-claude-bridge": []}
    rc.judge_mounts(rc.declared_secret_mounts(_pods(pod))["agora-claude-bridge"])


_PEM = ("-----BEGIN OPENSSH PRIVATE KEY-----\n"  # gitleaks:allow — fabricated, not a key
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAAB\n"
        "-----END OPENSSH PRIVATE KEY-----\n")
_PUB = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIHqQvJnovaMeasuredNotGuessed nova@bridge\n"
_OPAQUE = "9f3c1a77b2e40d58cc91ab6207e4f3d1"


def _mount(files):
    return lambda path: dict(files)


def test_a_mounted_file_is_judged_against_the_same_environment_as_the_env_half():
    """A Secret can be mounted as a file *and* set as a variable.

    `redact()`'s value pass looks its literals up in an environment, so a
    mount judged against a different one from `judge()` makes the two halves
    of the same report contradict each other.
    """
    literal = "9f3c1a77b2e40d58cc91ab6207e4f3d1"
    _, _, unmasked, _, _ = rc.judge_mounts(
        [("s", "/etc/s")], read=_mount({"k": literal}), environ={})
    assert unmasked == [("s", "k", len(literal))]
    masked, _, unmasked, _, _ = rc.judge_mounts(
        [("s", "/etc/s")], read=_mount({"k": literal}),
        environ={"NAS_API_KEY": literal})
    assert masked == [("s", "k")]
    assert unmasked == []


def test_a_private_key_in_a_mounted_secret_is_masked():
    masked, short, unmasked, public, unreadable = rc.judge_mounts(
        [("nas-ssh-key", "/etc/nas-ssh")], read=_mount({"id_ed25519": _PEM}))
    assert masked == [("nas-ssh-key", "id_ed25519")]
    assert (short, unmasked, public, unreadable) == ([], [], [], [])


def test_an_opaque_file_in_a_mounted_secret_is_a_finding():
    """No pattern has a shape for it and no environment holds it."""
    masked, short, unmasked, public, unreadable = rc.judge_mounts(
        [("groq", "/etc/groq")], read=_mount({"api-key": _OPAQUE}))
    assert unmasked == [("groq", "api-key", len(_OPAQUE))]
    assert (masked, short, public, unreadable) == ([], [], [], [])


def test_a_short_file_in_a_mounted_secret_is_below_the_floor_not_a_finding():
    masked, short, unmasked, public, unreadable = rc.judge_mounts(
        [("nas-ssh-key", "/etc/nas-ssh")], read=_mount({"user": "nova\n"}))
    assert short == [("nas-ssh-key", "user", 4)]
    assert unmasked == []


def test_the_floor_is_exclusive_at_exactly_the_minimum_length():
    """A value *at* the floor is long enough to mask, so it is a finding.

    The first version of the test above used a 4-character fixture, where
    `< _MIN_SECRET_LEN` and `<= _MIN_SECRET_LEN` give the same answer, so the
    mutation that loosens the comparison passed against a green test. These
    two assertions sit either side of the boundary.
    """
    at_floor = "a" * rc._MIN_SECRET_LEN
    _, short, unmasked, _, _ = rc.judge_mounts(
        [("s", "/etc/s")], read=_mount({"k": at_floor}))
    assert short == []
    assert unmasked == [("s", "k", rc._MIN_SECRET_LEN)]

    under = "a" * (rc._MIN_SECRET_LEN - 1)
    _, short, unmasked, _, _ = rc.judge_mounts(
        [("s", "/etc/s")], read=_mount({"k": under}))
    assert short == [("s", "k", rc._MIN_SECRET_LEN - 1)]
    assert unmasked == []


def test_an_openssh_public_key_is_named_rather_than_raised_on():
    masked, short, unmasked, public, unreadable = rc.judge_mounts(
        [("nas-ssh-key", "/etc/nas-ssh")], read=_mount({"id_ed25519.pub": _PUB}))
    assert public == [("nas-ssh-key", "id_ed25519.pub")]  # gitleaks:allow — a secret name, no value
    assert unmasked == [], "a public key must not raise; the row would go green-blind"


def test_the_public_key_rule_reads_the_content_not_the_filename():
    """`.pub` is a convention; the first token of the line is a declaration."""
    _, _, unmasked, public, _ = rc.judge_mounts(
        [("groq", "/etc/groq")], read=_mount({"id_ed25519.pub": _OPAQUE}))
    assert public == []
    assert unmasked == [("groq", "id_ed25519.pub", len(_OPAQUE))]
    _, _, _, public_by_content, _ = rc.judge_mounts(
        [("nas", "/etc/nas")], read=_mount({"no-suffix": _PUB}))
    assert public_by_content == [("nas", "no-suffix")]


def test_a_mount_that_is_not_on_this_filesystem_is_unreadable_not_clean():
    masked, short, unmasked, public, unreadable = rc.judge_mounts(
        [("nas-ssh-key", "/etc/nas-ssh")], read=lambda path: None)
    assert unreadable == [("nas-ssh-key", "/etc/nas-ssh", "not mounted here")]
    assert (masked, short, unmasked, public) == ([], [], [], [])


def test_a_file_whose_bytes_are_not_text_is_named_rather_than_skipped():
    masked, short, unmasked, public, unreadable = rc.judge_mounts(
        [("nas-ssh-key", "/etc/nas-ssh")], read=_mount({"blob": None}))
    assert unreadable == [("nas-ssh-key", "/etc/nas-ssh/blob", "unreadable")]
    assert (masked, short, unmasked, public) == ([], [], [], [])


def test_report_raises_on_an_unmasked_file_in_a_mounted_secret():
    pod = _pod_with_mount("agora-claude-bridge-abc", "agora-claude-bridge",
                          secret="groq", path="/etc/groq")
    out = io.StringIO()
    code = rc.report(_pods(pod), environ={}, here="agora-claude-bridge", out=out,
                     services={"items": []}, read_mount=_mount({"api-key": _OPAQUE}))
    body = out.getvalue()
    assert code == 2, body
    assert "NOT MASKED — agora-claude-bridge: secret/groq mounts api-key" in body


def test_report_is_clean_on_the_live_nas_key_shape():
    pod = _pod_with_mount("agora-claude-bridge-abc", "agora-claude-bridge")
    out = io.StringIO()
    code = rc.report(_pods(pod), environ={}, here="agora-claude-bridge", out=out,
                     services={"items": []},
                     read_mount=_mount({"id_ed25519": _PEM, "id_ed25519.pub": _PUB}))
    body = out.getvalue()
    assert code == 0, body
    assert "1 of 1 file(s) in 1 mounted Secret(s) masked" in body
    assert "NOT A SECRET" in body


def test_the_other_workloads_mounts_are_counted_in_its_cannot_judge_line():
    here = _pod_with_mount("agora-claude-bridge-abc", "agora-claude-bridge")
    there = _pod_with_mount("agora-persona-runner-abc", "agora-persona-runner")
    out = io.StringIO()
    rc.report(_pods(here, there), environ={}, here="agora-claude-bridge", out=out,
              services={"items": []}, read_mount=_mount({"id_ed25519": _PEM}))
    assert ("CANNOT JUDGE — agora-persona-runner declares 0 secret-sourced "
            "variable(s) and mounts 1 Secret(s) as files") in out.getvalue()
