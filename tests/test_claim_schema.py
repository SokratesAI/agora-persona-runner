"""`tools.claim_schema` — a claim is judged against the live XRD, not git's."""

import json

import pytest

from tools import claim_schema


GHS_SCHEMA = {
    "type": "object",
    "required": ["serviceName"],
    "properties": {
        "serviceName": {"type": "string",
                        "pattern": "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"},
        "description": {"type": "string"},
        "publicPort": {"type": "integer"},
        "visibility": {"type": "string", "enum": ["private", "public"]},
    },
}

XRD_ITEM = {
    "spec": {
        "group": "platform.sokratesai.io",
        "names": {"kind": "XGitHubService"},
        "claimNames": {"kind": "GitHubService"},
        "versions": [{
            "name": "v1alpha1",
            "served": True,
            "schema": {"openAPIV3Schema": {"properties": {"spec": GHS_SCHEMA}}},
        }],
    }
}

SCHEMAS = claim_schema.schemas_from_xrds([XRD_ITEM])


def doc(spec, kind="GitHubService"):
    return [("a.yaml", {"apiVersion": "platform.sokratesai.io/v1alpha1",
                        "kind": kind,
                        "metadata": {"name": "x"},
                        "spec": spec})]


def test_both_the_claim_kind_and_the_composite_kind_get_the_schema():
    assert ("platform.sokratesai.io", "GitHubService") in SCHEMAS
    assert ("platform.sokratesai.io", "XGitHubService") in SCHEMAS


def test_an_unserved_version_contributes_nothing():
    item = json.loads(json.dumps(XRD_ITEM))
    item["spec"]["versions"][0]["served"] = False
    assert claim_schema.schemas_from_xrds([item]) == {}


def test_a_field_the_live_schema_lacks_is_the_finding_platform_config_577_was():
    findings, judged, _skipped, _caveats = claim_schema.judge(
        doc({"serviceName": "ok", "repoVisibility": "public"}), SCHEMAS)
    assert judged == 1
    assert len(findings) == 1
    assert any("repoVisibility" in p for p in findings[0][1])


def test_a_missing_required_field_is_a_finding():
    findings, _judged, _skipped, _caveats = claim_schema.judge(
        doc({"description": "no name"}), SCHEMAS)
    assert any("serviceName is required" in p for p in findings[0][1])


@pytest.mark.parametrize("spec,needle", [
    ({"serviceName": "Not_Legal"}, "pattern"),
    ({"serviceName": "ok", "publicPort": "8080"}, "integer"),
    ({"serviceName": "ok", "visibility": "internal"}, "allows"),
])
def test_type_pattern_and_enum_each_bite(spec, needle):
    findings, _judged, _skipped, _caveats = claim_schema.judge(doc(spec), SCHEMAS)
    assert findings, f"{spec} should not have passed"
    assert any(needle in p for p in findings[0][1])


def test_a_legal_claim_is_no_finding():
    findings, judged, _skipped, _caveats = claim_schema.judge(
        doc({"serviceName": "ok", "description": "fine", "visibility": "private"}),
        SCHEMAS)
    assert judged == 1
    assert findings == []


def test_a_platform_kind_with_no_live_xrd_is_named_not_silently_passed():
    findings, judged, skipped, _caveats = claim_schema.judge(
        doc({"anything": 1}, kind="TailscaleExposure"), SCHEMAS)
    assert judged == 0
    assert findings == []
    assert skipped == {"TailscaleExposure.platform.sokratesai.io"}


def test_a_keyword_this_validator_cannot_evaluate_is_a_caveat_not_a_finding():
    schema = dict(GHS_SCHEMA, **{"x-kubernetes-validations": [{"rule": "true"}]})
    schemas = {("platform.sokratesai.io", "GitHubService"): ("v1alpha1", schema)}
    findings, _judged, _skipped, caveats = claim_schema.judge(
        doc({"serviceName": "ok"}), schemas)
    assert findings == []
    assert any("x-kubernetes-validations" in c for c in caveats)


def test_the_claim_demo_promote_renders_fits_the_schema():
    """The one-tap promotion of idea #138 opens a PR carrying this text."""
    findings, judged, _skipped, _caveats = claim_schema.judge(
        [("promote", claim_schema.promotion_document())], SCHEMAS)
    assert judged == 1, "the rendered claim was not judged against any schema"
    assert findings == []


def test_an_unreadable_cluster_is_none_and_never_an_empty_sweep():
    class Failed:
        returncode = 1
        stdout = ""
        stderr = "Forbidden"

    assert claim_schema.read_live_xrds(run=lambda argv: Failed()) is None
