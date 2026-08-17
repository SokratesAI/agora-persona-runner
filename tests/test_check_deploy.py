"""`verdict` against the incident that produced it.

The digests below are the real ones from 2026-08-15: `f5406109...` is
the image built from main's tip 09ead5b (runner#212) and `44c26996...`
is what the manifest still pinned an hour after that merge, because the
`update-manifest` job never started. Using the real numbers means the
NOT DEPLOYED case is a regression test for something that actually
happened, not a shape someone imagined.
"""

from tools.check_deploy import (
    IN_SYNC, NOT_BUILT, NOT_DEPLOYED, ROLLOUT_PENDING, verdict,
)

TIP = "09ead5b"
NEW = "sha256:f5406109f55051e52f9e2987accb80d37284d0815930d922b5b6b34f61c1945c"
OLD = "sha256:44c2699600146d49be36678fb74ddf5a5fa91efda537c3f0229472785728fc43"

BOTH = ("agora-persona-runner", "nova-site")


def _deployed(digest):
    return {name: digest for name in BOTH}


def test_the_actual_incident_reads_as_not_deployed():
    # Exactly the state at 20:02 Oslo: image built and pushed, manifest
    # untouched, cluster serving the previous cycle's code.
    state, lines = verdict(TIP, NEW, OLD, _deployed(OLD))
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
    state, _ = verdict(TIP, NEW, OLD, _deployed(OLD))
    assert state != ROLLOUT_PENDING
    # And say which state it must be. Asserting only the negative left the
    # name's claim resting on the neighbouring test: NOT_BUILT would also
    # have satisfied `!= ROLLOUT_PENDING` while contradicting the name.
    assert state == NOT_DEPLOYED


def test_missing_image_is_not_built():
    state, lines = verdict(TIP, None, OLD, _deployed(OLD))
    assert state == NOT_BUILT
    assert "sha-09ead5b" in "\n".join(lines)


def test_state_right_after_the_manifest_was_fixed():
    # Manifest bumped, ArgoCD has not synced yet. Both deployments stale.
    state, lines = verdict(TIP, NEW, NEW, _deployed(OLD))
    assert state == ROLLOUT_PENDING
    assert "agora-persona-runner" in "\n".join(lines)
    assert "nova-site" in "\n".join(lines)


def test_partial_rollout_is_visible_per_deployment():
    """Both deployments share one `image:` line, so they should move
    together; naming only the one that lagged is the point of reading
    them separately."""
    state, lines = verdict(
        TIP, NEW, NEW,
        {"agora-persona-runner": OLD, "nova-site": NEW},
    )
    assert state == ROLLOUT_PENDING
    body = "\n".join(lines)
    assert "agora-persona-runner is on" in body
    assert "nova-site is on" not in body


def test_unreadable_deployment_is_not_agreement():
    state, lines = verdict(
        TIP, NEW, NEW, {"agora-persona-runner": None, "nova-site": NEW},
    )
    assert state == ROLLOUT_PENDING
    assert "could not be read" in "\n".join(lines)


def test_everything_agreeing_is_in_sync():
    state, lines = verdict(TIP, NEW, NEW, _deployed(NEW))
    assert state == IN_SYNC
    assert "09ead5b" in "\n".join(lines)


def test_in_sync_is_reachable_only_when_all_three_agree():
    """Guards against a verdict that collapses to IN SYNC by default --
    every fault state must be distinguishable from it."""
    faults = [
        verdict(TIP, None, OLD, _deployed(OLD))[0],
        verdict(TIP, NEW, OLD, _deployed(OLD))[0],
        verdict(TIP, NEW, NEW, _deployed(OLD))[0],
    ]
    assert IN_SYNC not in faults
    assert len(set(faults)) == 3
