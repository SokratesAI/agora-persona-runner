"""What `tools.claim_drift` must get right about a claim and its manifest."""

import base64
import json
import types

import yaml

from tools import claim_drift as cd


def _proc(stdout, returncode=0, stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout,
                                 stderr=stderr)


def _manifest(service, port="8080", internal="8081", metrics="9464",
              storage="1Gi", deployment_name=None, with_pvc=True):
    docs = []
    if with_pvc:
        docs.append({"kind": "PersistentVolumeClaim",
                     "metadata": {"name": "%s-data" % service},
                     "spec": {"resources": {"requests": {"storage": storage}}}})
    env = [{"name": "PORT", "value": port},
           {"name": "INTERNAL_PORT", "value": internal},
           {"name": "METRICS_PORT", "value": metrics}]
    docs.append({"kind": "Deployment",
                 "metadata": {"name": deployment_name or service},
                 "spec": {"template": {"spec": {
                     "containers": [{"name": service, "env": env}]}}}})
    return docs


def _claim(service, **overrides):
    spec = {"serviceName": service, "publicPort": 8080, "internalPort": 8081,
            "metricsPort": 9464, "persistenceSize": "1Gi"}
    spec.update(overrides)
    return {"name": service, "namespace": "platform-catalog", "spec": spec}


def _rows(claim, docs):
    rows, has_deployment, has_pvc = cd.compare(claim, docs)
    return dict((f, (o, d)) for f, o, d in rows), has_deployment, has_pvc


def test_a_matching_manifest_reports_no_disagreement():
    rows, has_deployment, has_pvc = _rows(_claim("svc"), _manifest("svc"))
    assert has_deployment and has_pvc
    assert all(o == d for o, d in rows.values())


def test_a_changed_port_is_reported_with_both_values():
    rows, _dep, _pvc = _rows(_claim("svc"), _manifest("svc", port="8090"))
    assert rows["publicPort"] == ("8080", "8090")


def test_an_integer_claim_matches_a_string_env_value():
    # The claim stores ports as integers and Kubernetes env values are
    # always strings, so a naive == would call every service drifted.
    rows, _dep, _pvc = _rows(_claim("svc", publicPort=8080),
                             _manifest("svc", port="8080"))
    assert rows["publicPort"] == ("8080", "8080")


def test_an_env_var_the_manifest_dropped_reads_as_absent_not_equal():
    docs = _manifest("svc")
    envs = docs[1]["spec"]["template"]["spec"]["containers"][0]["env"]
    docs[1]["spec"]["template"]["spec"]["containers"][0]["env"] = [
        e for e in envs if e["name"] != "METRICS_PORT"]
    rows, _dep, _pvc = _rows(_claim("svc"), docs)
    assert rows["metricsPort"] == ("9464", None)


def test_a_renamed_deployment_is_drift_rather_than_agreement():
    rows, has_deployment, _pvc = _rows(
        _claim("svc"), _manifest("svc", deployment_name="svc-web"))
    assert has_deployment is False
    # Every templated env field reads as absent, not as matching.
    assert rows["publicPort"] == ("8080", None)


def test_a_missing_pvc_is_reported_separately_from_the_ports():
    rows, has_deployment, has_pvc = _rows(
        _claim("svc"), _manifest("svc", with_pvc=False))
    assert has_deployment is True and has_pvc is False
    assert rows["persistenceSize"] == ("1Gi", None)


def test_a_field_the_claim_does_not_set_is_not_compared():
    spec_without = {"serviceName": "svc", "publicPort": 8080}
    rows, _dep, _pvc = _rows({"name": "svc", "namespace": "n",
                              "spec": spec_without}, _manifest("svc"))
    assert rows["metricsPort"] == (None, None)


def test_env_is_read_from_the_service_container_not_the_first_match():
    docs = _manifest("svc")
    containers = docs[1]["spec"]["template"]["spec"]["containers"]
    assert containers[0]["env"][0]["name"] == "PORT"
    rows, _dep, _pvc = _rows(_claim("svc"), docs)
    assert rows["publicPort"] == ("8080", "8080")


def test_read_claims_prefers_service_name_over_the_object_name():
    payload = {"items": [{"metadata": {"name": "obj", "namespace": "ns"},
                          "spec": {"serviceName": "real", "publicPort": 1}}]}
    claims, problems = cd.read_claims(
        lambda *_a, **_k: _proc(json.dumps(payload)))
    assert problems == []
    assert claims[0]["name"] == "real"


def test_a_failing_kubectl_is_a_problem_and_not_an_empty_sweep():
    claims, problems = cd.read_claims(
        lambda *_a, **_k: _proc("", returncode=1, stderr="Forbidden"))
    assert claims == []
    assert problems and "Forbidden" in problems[0]


def test_read_manifest_decodes_the_base64_github_returns():
    raw = yaml.safe_dump_all(_manifest("svc"))
    encoded = base64.b64encode(raw.encode()).decode()
    docs, why = cd.read_manifest(
        "svc", lambda *_a, **_k: _proc(encoded + "\n"))
    assert why is None
    assert any(d.get("kind") == "Deployment" for d in docs)


def test_a_missing_manifest_is_a_reason_rather_than_no_drift():
    docs, why = cd.read_manifest(
        "svc", lambda *_a, **_k: _proc("", returncode=1, stderr="Not Found"))
    assert docs is None
    assert "Not Found" in why


def test_the_report_names_both_values_for_a_drifted_field():
    results = [{"name": "svc", "namespace": "platform-catalog",
                "rows": [("publicPort", "8080", "8090")],
                "has_deployment": True, "has_pvc": True, "drift": True}]
    report = cd.format_report(results, [])
    assert "ordered 8080, deployed 8090" in report
    assert "1 of 1" in report


def test_the_report_says_an_unreadable_repo_out_loud():
    report = cd.format_report([], ["svc: gh failed: Not Found"])
    assert "PROBLEM  svc: gh failed: Not Found" in report


def test_env_values_compare_as_text_even_when_the_manifest_holds_an_int():
    # A YAML author who writes `value: 8080` unquoted gets an int back, and
    # the claim's port is an int too -- but the report prints both sides, so
    # every read has to come back as the same type.
    deployment = {"spec": {"template": {"spec": {"containers": [
        {"name": "svc", "env": [{"name": "PORT", "value": 8080}]}]}}}}
    assert cd._env_value(deployment, "PORT") == "8080"


def test_a_renamed_deployment_counts_as_drift():
    rows, has_deployment, has_pvc = cd.compare(
        _claim("svc"), _manifest("svc", deployment_name="svc-web"))
    assert cd.is_drifted(rows, has_deployment, has_pvc) is True


def test_a_missing_pvc_counts_as_drift():
    rows, has_deployment, has_pvc = cd.compare(
        _claim("svc"), _manifest("svc", with_pvc=False))
    assert cd.is_drifted(rows, has_deployment, has_pvc) is True


def test_a_field_absent_from_the_manifest_counts_as_drift():
    docs = _manifest("svc")
    envs = docs[1]["spec"]["template"]["spec"]["containers"][0]["env"]
    docs[1]["spec"]["template"]["spec"]["containers"][0]["env"] = [
        e for e in envs if e["name"] != "METRICS_PORT"]
    rows, has_deployment, has_pvc = cd.compare(_claim("svc"), docs)
    assert cd.is_drifted(rows, has_deployment, has_pvc) is True


def test_a_fully_matching_claim_is_not_drift():
    rows, has_deployment, has_pvc = cd.compare(_claim("svc"), _manifest("svc"))
    assert cd.is_drifted(rows, has_deployment, has_pvc) is False


def test_a_shape_mismatch_counts_even_when_no_field_can_be_compared():
    # `serviceName` is the XRD's only required field, so a claim can carry
    # none of the four templated values. The field rule then has nothing to
    # compare and a manifest of some other shape would read as agreement.
    bare = {"name": "svc", "namespace": "n", "spec": {"serviceName": "svc"}}
    rows, has_deployment, has_pvc = cd.compare(
        bare, _manifest("svc", deployment_name="svc-web", with_pvc=False))
    assert all(o is None for _f, o, _d in rows)
    assert cd.is_drifted(rows, has_deployment, has_pvc) is True


def test_an_env_var_sourced_from_valuefrom_reads_as_absent_not_as_none():
    deployment = {"spec": {"template": {"spec": {"containers": [
        {"name": "svc", "env": [{"name": "PORT", "valueFrom": {
            "secretKeyRef": {"name": "s", "key": "k"}}}]}]}}}}
    assert cd._env_value(deployment, "PORT") is None
