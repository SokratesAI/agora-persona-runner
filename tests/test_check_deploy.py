"""`verdict` against the incident that produced it.

The digests below are the real ones from 2026-08-15: `f5406109...` is
the image built from main's tip 09ead5b (runner#212) and `44c26996...`
is what the manifest still pinned an hour after that merge, because the
`update-manifest` job never started. Using the real numbers means the
NOT DEPLOYED case is a regression test for something that actually
happened, not a shape someone imagined.
"""

from tools.check_deploy import (
    IN_SYNC, NO_MANIFEST, NOT_BUILT, NOT_DEPLOYED, NOT_RUNNING,
    POD_BEHIND, ROLLOUT_PENDING, Target, select_deployments, select_pods,
    verdict,
)

RUNNER = Target.named("agora-persona-runner")

TIP = "09ead5b"
NEW = "sha256:f5406109f55051e52f9e2987accb80d37284d0815930d922b5b6b34f61c1945c"
OLD = "sha256:44c2699600146d49be36678fb74ddf5a5fa91efda537c3f0229472785728fc43"

BOTH = ("agora-persona-runner", "nova-site")


def _deployed(digest):
    return {name: digest for name in BOTH}


def test_the_actual_incident_reads_as_not_deployed():
    # Exactly the state at 20:02 Oslo: image built and pushed, manifest
    # untouched, cluster serving the previous cycle's code.
    state, lines = verdict(RUNNER, TIP, NEW, OLD, _deployed(OLD))
    assert state == NOT_DEPLOYED
    body = "\n".join(lines)
    assert "f5406109f550" in body and "44c269960014" in body
    # Pins that the advice names something that still exists. This asserted
    # the literal `update-manifest` until Cycle 258, and Cycle 224 had already
    # folded that job into `build-push` -- so the test was holding the tool's
    # one actionable sentence at a job name a cycle could no longer find.
    # Both of these are pinned by the advice line only. An earlier version of
    # this test also asserted `agora-persona-runner-config/manifest.yaml`,
    # which was vacuous: the NOT DEPLOYED line above the advice already names
    # that path, so the assertion passed with the advice deleted.
    assert "build-push" in body
    assert "fix it by hand" in body


def test_not_deployed_outranks_the_stale_cluster():
    """The cluster is stale in the incident too, and saying ROLLOUT
    PENDING there would be the wrong answer wearing the right shape --
    it reads as 'wait a few minutes' for a state that never resolves."""
    state, _ = verdict(RUNNER, TIP, NEW, OLD, _deployed(OLD))
    assert state != ROLLOUT_PENDING
    # And say which state it must be. Asserting only the negative left the
    # name's claim resting on the neighbouring test: NOT_BUILT would also
    # have satisfied `!= ROLLOUT_PENDING` while contradicting the name.
    assert state == NOT_DEPLOYED


def test_missing_image_is_not_built():
    state, lines = verdict(RUNNER, TIP, None, OLD, _deployed(OLD))
    assert state == NOT_BUILT
    assert "sha-09ead5b" in "\n".join(lines)


def test_state_right_after_the_manifest_was_fixed():
    # Manifest bumped, ArgoCD has not synced yet. Both deployments stale.
    state, lines = verdict(RUNNER, TIP, NEW, NEW, _deployed(OLD))
    assert state == ROLLOUT_PENDING
    assert "agora-persona-runner" in "\n".join(lines)
    assert "nova-site" in "\n".join(lines)


def test_partial_rollout_is_visible_per_deployment():
    """Both deployments share one `image:` line, so they should move
    together; naming only the one that lagged is the point of reading
    them separately."""
    state, lines = verdict(
        RUNNER, TIP, NEW, NEW,
        {"agora-persona-runner": OLD, "nova-site": NEW},
    )
    assert state == ROLLOUT_PENDING
    body = "\n".join(lines)
    assert "agora-persona-runner is on" in body
    assert "nova-site is on" not in body


def test_unreadable_deployment_is_not_agreement():
    state, lines = verdict(
        RUNNER, TIP, NEW, NEW, {"agora-persona-runner": None, "nova-site": NEW},
    )
    assert state == ROLLOUT_PENDING
    assert "could not be read" in "\n".join(lines)


def test_everything_agreeing_is_in_sync():
    state, lines = verdict(RUNNER, TIP, NEW, NEW, _deployed(NEW))
    assert state == IN_SYNC
    assert "09ead5b" in "\n".join(lines)


def test_in_sync_is_reachable_only_when_all_three_agree():
    """Guards against a verdict that collapses to IN SYNC by default --
    every fault state must be distinguishable from it."""
    faults = [
        verdict(RUNNER, TIP, None, OLD, _deployed(OLD))[0],
        verdict(RUNNER, TIP, NEW, OLD, _deployed(OLD))[0],
        verdict(RUNNER, TIP, NEW, NEW, _deployed(OLD))[0],
    ]
    assert IN_SYNC not in faults
    assert len(set(faults)) == 3


# --- The generalisation, Cycle 295 -------------------------------------
#
# Everything above pins the 2026-08-15 incident on the one repo this tool
# was born checking. What follows pins the part that made it checkable for
# the other four: a target derived from a name, deployments discovered by
# image, and the two states that only became reachable once `DEPLOYMENTS`
# stopped being a hardcoded pair.

BRIDGE = Target.named("agora-claude-bridge")

# Real, from `kubectl get deploy -n agents` at 05:30 Oslo on 2026-08-21.
# `newspaper` is the reason this is discovery and not a table.
LISTING = "\n".join([
    "agents/agora\tghcr.io/sokratesai/agora@" + NEW,
    "agents/agora-claude-bridge\tghcr.io/sokratesai/agora-claude-bridge@" + OLD,
    "agents/agora-persona-runner\tghcr.io/sokratesai/agora-persona-runner@" + NEW,
    "agents/newspaper\tghcr.io/sokratesai/vault-bridge@" + OLD,
    "agents/nova-site\tghcr.io/sokratesai/agora-persona-runner@" + NEW,
    "agents/sokrates-docs\tghcr.io/sokratesai/sokrates-docs@" + NEW,
    "obsidian/vault-bridge\tghcr.io/sokratesai/vault-bridge@" + NEW,
])


def test_a_name_is_all_four_facts():
    assert BRIDGE.repo == "SokratesAI/agora-claude-bridge"
    assert BRIDGE.config_repo == "SokratesAI/agora-claude-bridge-config"
    assert BRIDGE.package == "agora-claude-bridge"
    assert BRIDGE.image_path == "ghcr.io/sokratesai/agora-claude-bridge"


def test_discovery_finds_the_pair_the_constant_used_to_hold():
    """The old `DEPLOYMENTS = ("agora-persona-runner", "nova-site")`
    exactly, derived rather than declared."""
    assert select_deployments(RUNNER, LISTING) == {
        "agents/agora-persona-runner": NEW, "agents/nova-site": NEW,
    }


def test_discovery_finds_a_deployment_not_named_after_its_repo():
    """`newspaper` runs the `vault-bridge` image. A lookup table keyed on
    repo names would have missed it and reported NOT RUNNING -- which is
    the specific wrong answer this test exists to keep out.

    It also finds `obsidian/vault-bridge`, which is the deployment
    `platform-config` actually pins, on a different digest. Searching
    only `agents` returns `newspaper` alone and answers confidently
    about the wrong one."""
    assert select_deployments(Target.named("vault-bridge"), LISTING) == {
        "agents/newspaper": OLD, "obsidian/vault-bridge": NEW,
    }


def test_two_namespaces_on_different_digests_read_as_a_split_rollout():
    """The real 2026-08-21 state. Averaging these into one answer, or
    seeing only the `agents` one, is the failure the namespace key
    exists to prevent."""
    state, lines = verdict(
        Target.named("vault-bridge"), TIP, NEW, NEW,
        {"agents/newspaper": OLD, "obsidian/vault-bridge": NEW},
    )
    assert state == ROLLOUT_PENDING
    body = "\n".join(lines)
    assert "agents/newspaper is on" in body
    assert "obsidian/vault-bridge is on" not in body


def test_discovery_does_not_match_on_a_name_prefix():
    """`agora` is a prefix of `agora-claude-bridge` and of
    `agora-persona-runner`. Matching `ghcr.io/sokratesai/agora` without
    the `@` would sweep in all three and average three services into one
    verdict."""
    assert select_deployments(Target.named("agora"), LISTING) == {
        "agents/agora": NEW,
    }


def test_nothing_running_is_not_in_sync():
    """Reachable only since discovery: with the pair hardcoded, `deployed`
    could never be empty, so this fell through to `0 deployment(s) all
    agree` -- IN SYNC guaranteed in advance for a service nothing runs."""
    state, lines = verdict(BRIDGE, TIP, NEW, NEW, {})
    assert state == NOT_RUNNING
    assert "ghcr.io/sokratesai/agora-claude-bridge" in "\n".join(lines)


def test_missing_config_repo_is_not_read_as_a_stale_pin():
    """`vault-bridge` has no `-config` repo, so `manifest_digest` returns
    None for it. Falling through to the `!= tip_digest` branch would have
    printed NOT DEPLOYED and told a cycle to hand-commit a digest into a
    repo that does not exist."""
    state, lines = verdict(Target.named("vault-bridge"), TIP, NEW, None, {})
    assert state == NO_MANIFEST
    body = "\n".join(lines)
    assert state != NOT_DEPLOYED
    assert "vault-bridge-config/manifest.yaml" in body


def test_advice_names_the_target_repo_not_the_runner():
    """Every actionable sentence used to interpolate module constants, so
    a check of any other repo would have sent the reader to
    agora-persona-runner's build and manifest."""
    _, built = verdict(BRIDGE, TIP, None, NEW, {})
    _, pinned = verdict(BRIDGE, TIP, NEW, OLD, {"agents/agora-claude-bridge": OLD})
    for body in ("\n".join(built), "\n".join(pinned)):
        assert "agora-claude-bridge" in body
        assert "agora-persona-runner" not in body


# --- Reviewer findings on runner#271, fixed in the same PR ---------------

def test_a_tag_pinned_deployment_is_running_not_missing():
    """Reviewer finding. Matching on `path + '@'` dropped a tag-pinned
    deployment as though it were an unrelated service, so a service that
    was running could report NOT RUNNING -- and it made verdict's 'could
    not be read' branch dead from the real call path, since discovery
    could no longer produce a None."""
    listing = "agents/newspaper\tghcr.io/sokratesai/vault-bridge:v3"
    assert select_deployments(Target.named("vault-bridge"), listing) == {
        "agents/newspaper": None,
    }


def test_a_tag_pinned_deployment_reaches_the_could_not_be_read_line():
    """The end-to-end half of the finding: the None above must render as
    'could not be read' rather than as agreement or as NOT RUNNING."""
    listing = "agents/newspaper\tghcr.io/sokratesai/vault-bridge:v3"
    deployed = select_deployments(Target.named("vault-bridge"), listing)
    state, lines = verdict(Target.named("vault-bridge"), TIP, NEW, NEW, deployed)
    assert state == ROLLOUT_PENDING
    assert state != NOT_RUNNING
    assert "could not be read" in "\n".join(lines)


def test_a_longer_package_name_is_not_matched_by_a_shorter_one():
    """`agora` must not match `agora-claude-bridge`'s image now that the
    match is on the path rather than on `path + '@'`. The `@`/`:`/end
    check is what keeps that true."""
    listing = "agents/agora-claude-bridge\tghcr.io/sokratesai/agora-claude-bridge@" + NEW
    assert select_deployments(Target.named("agora"), listing) == {}


def test_the_kubectl_query_asks_every_namespace_and_returns_the_namespace():
    """`select_deployments` keys on `namespace/name`, but every test above
    feeds it a fixture string -- so deleting the namespace from the real
    query left all of them green. Caught mutating the jsonpath during the
    Cycle 295 mutation pass. This pins the one place the two have to
    agree: the query must span namespaces and must emit the namespace,
    or the keys are names again and `obsidian/vault-bridge` and
    `agents/newspaper` collapse into one."""
    import inspect

    from tools import check_deploy

    src = inspect.getsource(check_deploy.deployed_digests)
    assert '"-A"' in src
    assert "metadata.namespace" in src
    # The separator, not just the field. Dropping only the `/` leaves
    # `metadata.namespace` in the source and yields `agentsnewspaper`,
    # which the two assertions above both accept -- measured, by mutating
    # exactly that and watching 20 tests stay green.
    assert "{'/'}{.metadata.name}" in src
    assert "-n" not in src.split("jsonpath")[0].replace("--", "")


# The 2026-09-05 drain, with the real digests. `7f0babe5...` is the image
# built from main's tip 2ba0124 (runner#775, the merge_pr trigger fix) and
# pinned by both the manifest and the Deployment by 20:59 UTC;
# `f1a13c27...` is the image the runner pod was created from at 18:23 and
# went on serving until its 48-minute grace period expired at 21:50 Oslo.
# For those 48 minutes `check_deploy` printed IN SYNC while every MCP tool
# call ran the older code -- which is how `merge_pr` came to refuse a merge
# the code on main had already made legal.
PINNED = "sha256:7f0babe58ca7c02de5b99411e4763d41040a5c730733514125cf7d7f40016f60"
DRAINING = "sha256:f1a13c270198fd36d9ef30b30b7e95ff8d73272cbc73189c4cab052a851739a0"

RUNNER_POD = "agents/agora-persona-runner-6f9c96bfcd-dzf6z"
SITE_POD = "agents/nova-site-7d9f4c8b6d-abcde"


def test_a_pod_still_serving_the_previous_image_is_not_in_sync():
    state, lines = verdict(
        RUNNER, TIP, PINNED, PINNED, _deployed(PINNED),
        {RUNNER_POD: DRAINING, SITE_POD: PINNED},
    )
    assert state == POD_BEHIND
    body = "\n".join(lines)
    assert RUNNER_POD in body
    assert "f1a13c270198" in body and "7f0babe58ca7" in body
    # The pod that HAS caught up must not be reported as behind.
    assert SITE_POD not in body


def test_pods_not_read_answers_about_the_deployment_exactly_as_before():
    """`running=None` is "I did not look", and it has to keep meaning that:
    a caller that never reads pods must not be told every pod agrees."""
    state, lines = verdict(RUNNER, TIP, NEW, NEW, _deployed(NEW))
    assert state == IN_SYNC
    assert "pod(s) are running it" not in "\n".join(lines)


def test_every_pod_on_the_pinned_digest_is_in_sync_and_says_how_many():
    state, lines = verdict(
        RUNNER, TIP, NEW, NEW, _deployed(NEW), {RUNNER_POD: NEW, SITE_POD: NEW},
    )
    assert state == IN_SYNC
    assert "2 pod(s) are running it" in "\n".join(lines)


def test_a_deployment_that_has_not_rolled_outranks_a_pod_that_has_not():
    """ROLLOUT_PENDING first. A Deployment still on the old digest explains
    the pod by itself, and reporting the pod instead would send a cycle
    looking at a drain when ArgoCD has not synced."""
    state, _ = verdict(
        RUNNER, TIP, NEW, NEW, _deployed(OLD), {RUNNER_POD: OLD},
    )
    assert state == ROLLOUT_PENDING


def test_an_unreadable_pod_image_is_not_agreement():
    state, lines = verdict(
        RUNNER, TIP, NEW, NEW, _deployed(NEW), {RUNNER_POD: None},
    )
    assert state == POD_BEHIND
    assert "could not be read" in "\n".join(lines)


def test_a_pod_that_is_not_running_is_not_serving():
    """A Pending pod holds the new image and is answering nothing; a
    Succeeded one is a finished Job. Counting either would report a drain
    as over while the old pod is still taking every call."""
    listing = (
        f"{RUNNER_POD}\tRunning\t{RUNNER.image_path}@{DRAINING}\n"
        f"agents/agora-persona-runner-new\tPending\t{RUNNER.image_path}@{PINNED}\n"
        f"agents/agora-backup-29810\tSucceeded\t{RUNNER.image_path}@{PINNED}\n"
    )
    assert select_pods(RUNNER, listing) == {RUNNER_POD: DRAINING}


def test_pods_are_selected_by_image_across_every_namespace():
    listing = (
        f"obsidian/couchdb-0\tRunning\tcouchdb:3.3\n"
        f"{RUNNER_POD}\tRunning\t{RUNNER.image_path}@{PINNED}\n"
    )
    assert select_pods(RUNNER, listing) == {RUNNER_POD: PINNED}


def test_the_pod_query_reads_the_spec_image_and_spans_namespaces():
    """`status.containerStatuses[].image` is the runtime's resolved
    reference and can carry a `docker.io/` prefix the spec does not, so a
    query built on it drops pods that are plainly running this image and
    a drain reads as clean. Every test above feeds `select_pods` a fixture,
    so nothing else pins which field the real query asks for."""
    import inspect

    from tools import check_deploy

    src = inspect.getsource(check_deploy.pod_digests)
    assert ".spec.containers[0].image" in src
    assert "status.containerStatuses" not in src
    assert ".status.phase" in src
    assert '"-A"' in src
    assert "{'/'}{.metadata.name}" in src
