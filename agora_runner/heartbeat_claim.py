"""Durably claiming a heartbeat run before executing it (2026-08-02).

Shared by both dispatch paths (heartbeats.run_heartbeat and
workflows.run_workflow_heartbeat) so their duplicate-protection can't
drift apart -- it already did once: #25 gave the workflow path this
claim, the regular path never got it, and the regular path is what the
Evolve loop actually runs on now (v2, one plain heartbeat, no workflow).

The window this closes: a run takes minutes (an Evolve cycle is ~11),
and until the end-of-run PATCH lands the heartbeat's PERSISTED state
still says "never ran, still forced". Anything that re-reads that state
in the meantime -- a pod restart, another replica -- sees a due
heartbeat and starts the same cycle a second time.

Confirmed live on the regular path 2026-08-02, twice in one day, both
times self-inflicted: Evolve merged a PR into its own repo, the deploy
rolled the pod hosting its own in-flight cycle, and the replacement pod
read `forceRun: true` (never cleared, because the run it belonged to
died before its final PATCH) and immediately started a duplicate cycle.
Measured on the workflow path before #25: 7 of 19 PRs were same-work
duplicates.

Note this anchors the next scheduled run to run START rather than run
END (schedule_due reads lastRunAt). For a run that can outlive its own
interval, that's the point. The caller's end-of-run PATCH still
overwrites both fields with the real outcome.
"""
from datetime import datetime, timezone

from agora_runner.http_util import agora_internal
from agora_runner.log import log


def claim_heartbeat_run(heartbeat):
    """Mark `heartbeat` as claimed (forceRun cleared, lastRunAt set,
    lastResult "running") before its run starts. Best-effort: a
    transient Agora blip must not block a real cycle, so a failed claim
    is logged loudly and execution continues."""
    status, _ = agora_internal("PATCH", f"/heartbeats/{heartbeat['id']}",
                               {"forceRun": False,
                                "lastRunAt": datetime.now(timezone.utc).isoformat(),
                                "lastResult": "running"})
    if status not in (200, 201):
        # Deliberately not fatal -- but an unlogged failure here silently
        # reopens the exact duplicate window this claim exists to close,
        # so it must not pass quietly. If duplicate runs ever show up
        # again, this line is the evidence.
        log(f"heartbeat {heartbeat.get('name')}: claim PATCH failed (HTTP {status}), "
            "run is unclaimed and may be duplicated by a restart or another replica")
    return status
