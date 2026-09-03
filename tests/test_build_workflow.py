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
SMOKE_SCRIPT = Path(__file__).resolve().parent.parent / ".github" / "smoke-test.sh"


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


def _build_push_step_names():
    return [step.get("name") for step in _workflow()["jobs"]["build-push"]["steps"]]


def _smoke_step():
    for step in _workflow()["jobs"]["build-push"]["steps"]:
        if (step.get("name") or "").startswith("Smoke-test"):
            return step
    return None


def test_the_built_image_is_actually_started_before_it_is_deployed():
    """Nothing in this pipeline ran the image it ships until 2026-09-03.

    On 2026-09-02 an image that crashed on `import yaml` was built, pushed,
    had its digest committed to the config repo and was deployed by ArgoCD,
    with `test` green throughout -- the suite runs on a GitHub runner that has
    pyyaml installed for its own reasons, so it could not see the difference.
    The deployment is single-replica with strategy Recreate and no fallback,
    and Nova's heartbeat runs in it, so the loop was down for ten hours.
    """
    step = _smoke_step()
    assert step is not None, "build-push no longer starts the image before deploying it"
    assert "smoke-test.sh" in step["run"], "the smoke step no longer invokes the smoke test"
    assert "${{ steps.build.outputs.digest }}" in step["run"], (
        "the smoke test must run the digest that is about to be deployed, not a rebuild of it"
    )
    # The step is one line calling a script, and the script is where the work
    # is, because tools/sync_contract.py compares this job as parsed YAML
    # against agora-claude-bridge's copy and refuses a difference. A step that
    # named `agora_runner` inline would be that difference.
    assert "docker run" in SMOKE_SCRIPT.read_text(), (
        "the smoke script must actually run the image, not inspect it"
    )


def test_the_smoke_test_runs_before_the_digest_is_committed():
    """Order is the whole value. What deploys is the line committed to the
    config repo, so a smoke test after that step would find the breakage on a
    cluster that had already taken it."""
    names = _build_push_step_names()
    smoke = next(i for i, name in enumerate(names) if (name or "").startswith("Smoke-test"))
    commit = next(i for i, name in enumerate(names) if (name or "").startswith("Update image digest"))
    assert smoke < commit, (
        f"the smoke test runs at step {smoke} and the digest is committed at step {commit}; "
        "a smoke test after the commit tests an image that is already deploying"
    )


def test_the_smoke_test_refuses_to_pass_on_an_empty_sweep():
    """A negative result only counts if a positive one was possible. If the
    package ever stops being importable by name, walking it finds nothing and
    every check inside the sweep passes vacuously."""
    script = SMOKE_SCRIPT.read_text()
    assert "if not packages:" in script, (
        "the smoke script must fail when it finds no package at all in the image"
    )
    assert "found < 10" in script, (
        "the smoke test must fail when it finds implausibly few modules, or an empty "
        "sweep reads as a clean one"
    )


def test_the_test_job_installs_the_image_s_own_requirements():
    """The environment the suite runs in must be built from the same file the
    image installs, so the two cannot silently diverge again."""
    steps = _workflow()["jobs"]["test"]["steps"]
    installs = [step.get("run", "") for step in steps if "pip install" in (step.get("run") or "")]
    assert installs, "the test job no longer installs anything"
    assert any("-r requirements.txt" in run for run in installs), (
        "the test job names its packages instead of installing requirements.txt; that is how "
        "pyyaml came to be present in CI and absent from the image"
    )


def test_the_smoke_script_is_executable_and_takes_the_image_as_its_argument():
    """The workflow calls it with `bash`, so the mode bit is not load-bearing
    there -- it is here so running it by hand does the same thing CI does."""
    assert SMOKE_SCRIPT.exists(), "the smoke script the workflow calls does not exist"
    script = SMOKE_SCRIPT.read_text()
    assert "${1:?" in script, "the script must refuse to run with no image argument"
    assert "--network none" in script, (
        "the smoke test must run with no network, so it can never reach production"
    )
