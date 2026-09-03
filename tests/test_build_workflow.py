"""The build workflow must serialise pipelines per branch.

`update-manifest` in build.yaml is a blind `sed` of whichever image digest
its own run produced, so two pipelines running in parallel deploy whichever
one *finished last*, which is not the same as whichever commit is newest.

Measured 2026-08-12 in agora-claude-bridge, whose copy of build.yaml is
identical: its #41 and #40 merged four seconds apart, #40 was the newer
commit, and #41's build pushed its digest one second later and overwrote it
-- pinning that repo's config to the commit before the endpoint #40 had just
added. Both CI runs were green, the image built, ArgoCD synced, and the
deployed pod served a 404 for a feature that was merged in main. This repo
merges just as many PRs per cycle and has the same race; it has only been
lucky with timing.

Scope, stated precisely because the first draft of this docstring overclaimed
and a reviewer caught it: build.yaml is seeded by the Crossplane Composition
(githubservice-composition.yaml) with managementPolicies [Observe, Create,
LateInitialize], so Crossplane never overwrites this repo's hand-edited copy.
These tests guard *this repo only*. They are not seeded by the template --
the template ships a Node/vitest starter and its build.yaml runs `npm test`,
so a freshly created service would never run this file. The template got the
same concurrency block committed to it separately; nothing automatically
checks the two against each other.
"""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "build.yaml"


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def test_build_workflow_serialises_pipelines_per_branch():
    concurrency = _workflow().get("concurrency")
    assert concurrency, (
        "build.yaml declares no concurrency group -- two pushes to main run "
        "update-manifest in parallel and the last to finish wins, not the "
        "newest commit"
    )
    assert "github.ref" in concurrency["group"], (
        f"concurrency group {concurrency['group']!r} must be per-branch, or "
        "every pull_request build queues behind main"
    )


def test_in_flight_builds_are_never_cancelled():
    # Killing a run partway through update-manifest is the failure the
    # concurrency group exists to prevent, not an optimisation to add later.
    assert _workflow()["concurrency"]["cancel-in-progress"] is False


def _build_push_steps():
    return _workflow()["jobs"]["build-push"]["steps"]


def _step_index(predicate, what):
    for i, step in enumerate(_build_push_steps()):
        if predicate(step):
            return i
    raise AssertionError(f"no step in build-push {what}")


def test_the_image_is_started_before_its_digest_is_deployed():
    """The ten-hour outage of 2026-09-02.

    #660 added `import yaml` to a runtime module while the Dockerfile had no
    pip install step. Every check was green, the image built, its digest was
    committed to -config, ArgoCD synced it, and every pod then crash-looped on
    ModuleNotFoundError. Nothing in this pipeline had ever run the container.

    So: some step must actually start the built image, and it must come before
    the step that writes the digest into the config repo -- after is worthless,
    because the deploy has already happened by then. This asserts the ordering
    rather than the shell inside either step; the commands are read for `docker
    run` only, not compared against a copy of themselves kept here.
    """
    steps = _build_push_steps()

    starts_container = _step_index(
        lambda s: "docker run" in s.get("run", ""),
        "starts the built image (`docker run`) -- nothing verifies the "
        "artefact before it is deployed",
    )
    writes_digest = _step_index(
        lambda s: "manifest.yaml" in s.get("run", ""),
        "writes the digest into manifest.yaml",
    )

    assert starts_container < writes_digest, (
        f"the image is started at step {starts_container} "
        f"({steps[starts_container].get('name')!r}) but its digest is already "
        f"deployed at step {writes_digest} "
        f"({steps[writes_digest].get('name')!r}) -- a smoke test that runs "
        "after the config repo has been updated cannot stop a bad image"
    )


def test_both_entrypoints_are_smoke_tested():
    """One image, two Deployments, and only one of them was visible.

    nova-site kept serving off its previous pod all through the outage, which
    is why the status page stayed green while the heartbeat was dead. Both
    commands the two Deployments run have to be exercised, or half the image
    ships unverified.
    """
    runs = "\n".join(s.get("run", "") for s in _build_push_steps())
    for entrypoint in ("run.py", "run_nova_site.py"):
        assert entrypoint in runs, (
            f"{entrypoint} is never started in build-push -- one image serves "
            "both the runner and Nova's site, and an entrypoint nobody runs "
            "is an entrypoint nobody has checked"
        )


def test_the_smoke_test_fails_the_job_rather_than_warning():
    """`docker run` in a shell that ignores failures reports nothing.

    A container that exits immediately still leaves `docker run -d` exit 0 --
    the daemon accepted it -- so the step has to inspect the result and exit
    non-zero itself. Without `set -e` a failing inspect branch would print an
    error and let the digest through, which is the same outcome as having no
    smoke test at all while looking like one.
    """
    step = _build_push_steps()[_step_index(
        lambda s: "docker run" in s.get("run", ""), "starts the built image"
    )]
    body = step["run"]
    assert "set -e" in body, (
        "the smoke-test step does not `set -e`; a failing command inside it "
        "would be ignored and the bad digest would still be committed"
    )
    assert "exit 1" in body, (
        "the smoke-test step never exits non-zero -- `docker run -d` succeeds "
        "for a container that dies a millisecond later, so the step must fail "
        "the job on its own judgement"
    )
