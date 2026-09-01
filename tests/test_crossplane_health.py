"""The three judgements `tools.crossplane_health` makes that YAML cannot.

Every test here drives `main` through a fake `subprocess.run`, so what is
under test is the classification, not kubectl.
"""

import datetime
import json
import subprocess

from tools import crossplane_health

NOW = datetime.datetime(2026, 8, 27, 9, 0, tzinfo=datetime.timezone.utc)


def _resource(kind, name, *, synced="True", synced_reason="ReconcileSuccess",
              ready="True", message=None, paused=False, owner=None,
              composes=()):
    conditions = [
        {"type": "Synced", "status": synced, "reason": synced_reason,
         "lastTransitionTime": "2026-08-26T09:00:00Z"},
        {"type": "Ready", "status": ready, "reason": "Available",
         "lastTransitionTime": "2026-08-26T09:00:00Z"},
    ]
    if message:
        conditions[0]["message"] = message
    item = {
        "kind": kind,
        "metadata": {"name": name, "namespace": "platform-catalog"},
        "status": {"conditions": conditions},
    }
    if paused:
        item["metadata"]["annotations"] = {"crossplane.io/paused": "true"}
    if owner:
        item["metadata"]["ownerReferences"] = [
            {"kind": owner[0], "name": owner[1], "controller": True}]
    if composes:
        item["spec"] = {"crossplane": {"resourceRefs": [
            {"kind": k, "name": n, "apiVersion": f"{g}/v1alpha1"}
            for k, n, g in composes]}}
    return item


def fake_kubectl(managed, composites, *, managed_stderr="", returncode=0):
    """A `subprocess.run` that answers the two queries the tool makes."""
    def run(args, **_):
        category = args[args.index("get") + 1]
        items = managed if category == "managed" else composites
        return subprocess.CompletedProcess(
            args, returncode if category == "managed" else 0,
            stdout=json.dumps({"items": items}),
            stderr=managed_stderr if category == "managed" else "")
    return run


def test_a_paused_resource_is_not_a_finding():
    """28 of 37 resources here are paused on purpose; raising would be off."""
    runner = fake_kubectl(
        [_resource("RepositoryFile", "seeded", synced="False",
                   synced_reason="ReconcilePaused", paused=True)],
        [_resource("GitHubService", "svc")])
    assert crossplane_health.main([], runner=runner, now=NOW) == 0


def test_a_pause_nobody_annotated_is_a_finding(capsys):
    """`ReconcilePaused` without the annotation came from somewhere unwritten."""
    runner = fake_kubectl(
        [_resource("RepositoryFile", "seeded", synced="False",
                   synced_reason="ReconcilePaused", paused=False)],
        [_resource("GitHubService", "svc")])
    assert crossplane_health.main([], runner=runner, now=NOW) == 2
    assert "PAUSED WITH NO ANNOTATION" in capsys.readouterr().out


def test_the_422_is_reported_with_the_claim_that_hides_it(capsys):
    """Issue #109 exactly: the leaf carries the error, the claim reads True."""
    runner = fake_kubectl(
        [_resource("Repository", "sokrates-docs", synced="False",
                   synced_reason="ReconcileError", owner=("GitHubService", "svc"),
                   message="422 Secret scanning is not available for this repository")],
        [_resource("GitHubService", "svc")])
    assert crossplane_health.main([], runner=runner, now=NOW) == 2
    out = capsys.readouterr().out
    assert "NOT SYNCED  Repository/platform-catalog/sokrates-docs" in out
    assert "422 Secret scanning is not available" in out
    # The whole point: say that the claim above it is not a second opinion.
    assert "owned by GitHubService/svc, which reads Synced/Ready = True/True" in out


def test_a_composed_resource_this_account_cannot_list_is_not_clean(capsys):
    """A refused kind a live composite names is a real object in unknown state."""
    runner = fake_kubectl(
        [_resource("Repository", "svc", owner=("GitHubService", "svc"))],
        [_resource("GitHubService", "svc", composes=(
            ("Repository", "svc", "repo.github.m.upbound.io"),
            ("ActionsSecret", "svc-app-id", "actions.github.m.upbound.io")))],
        managed_stderr=(
            'Error from server (Forbidden): actionssecrets.actions.github.m.upbound.io '
            'is forbidden: User "u" cannot list resource "actionssecrets" in API '
            'group "actions.github.m.upbound.io" at the cluster scope\n'),
        returncode=1)
    assert crossplane_health.main([], runner=runner, now=NOW) == 1
    out = capsys.readouterr().out
    assert "CANNOT SEE  ActionsSecret/svc-app-id" in out
    assert "1 kind(s) were refused" in out


def test_a_refused_kind_nothing_composes_does_not_raise():
    """Red on day one and forever is the same as off."""
    runner = fake_kubectl(
        [_resource("Repository", "svc", owner=("GitHubService", "svc"))],
        [_resource("GitHubService", "svc", composes=(
            ("Repository", "svc", "repo.github.m.upbound.io"),))],
        managed_stderr=(
            'Error from server (Forbidden): teams.team.github.upbound.io is '
            'forbidden: User "u" cannot list resource "teams" in API group '
            '"team.github.upbound.io" at the cluster scope\n'),
        returncode=1)
    assert crossplane_health.main([], runner=runner, now=NOW) == 0


def test_an_empty_sweep_is_no_instrument_rather_than_no_problem(capsys):
    """This cluster runs Crossplane; zero managed resources is a wrong query."""
    runner = fake_kubectl([], [])
    assert crossplane_health.main([], runner=runner, now=NOW) == 1
    assert "COULD NOT READ" in capsys.readouterr().out


def test_a_refusal_that_returned_no_json_at_all_is_a_read_failure(capsys):
    """Partial output is an answer; no output is not."""
    def run(args, **_):
        return subprocess.CompletedProcess(args, 1, stdout="", stderr="Forbidden")
    assert crossplane_health.main([], runner=run, now=NOW) == 1
    assert "COULD NOT READ" in capsys.readouterr().out
