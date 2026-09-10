"""Run every step-1a status check in one call, and print only what is not clean.

The owner, capture 2026-08-28: *"I see that the median cycle now consumes much
more tokens now than what they used to do at the start of the Nova project,
earlier in August. Why? Make an effort to optimize your token usage."*

That is right, and Cycle 581 measured where it went. Against the vault cost
ledger (`nova/resources/cost-ledger.json`, 577 cycles back to 08-03), the
median cycle cost **1.16M weighted tokens over 08-03..08-10 and 1.74M over
08-24..08-28** -- half again as much. The decomposition is the useful part,
because it names the lever:

    median turns per cycle      64  ->  93     (+45%)
    median weighted per turn  17.5k -> 18.9k   (+8%)

So the prompt did not get much fatter per turn. **The cycle got longer.**
Nearly all of the growth is more turns, and the largest block of turns
this loop added in that window is right here: step 1a of `prompt.md` grew
from a handful of reads into fourteen separate status checks, each one its
own tool call, each one printing a full report that is then carried in
context for the remaining ~90 turns whether or not it found anything.

Thirteen of those fourteen answer "nothing to act on" on a normal morning.
That is thirteen turns and thirteen reports to say nothing happened.

**This does not drop anything, and that is deliberate.** The owner's rule
(`personality.md`, on the 400-chip cap): *"I want control. I want to know
what's going on in every corner of this system, but I also want the option
to not see it... That's an interface problem, and interface problems get
solved with an interface, never by throwing away the data."* So a check
that exits 0 collapses to its own last line -- which every one of these
tools writes as its summary, naming what it swept -- and a check that exits
1 or 2 is reproduced **in full, verbatim**, because that is the output a
cycle actually has to read.

One thing survives the collapse besides that line: **a clean check's own
statement that it could not judge part of its scope.** A check may honestly
exit 0 over a surface it never read -- nzbget's extension list sits behind a
password neither pod holds -- and collapsing that to a summary puts the
caveat out of sight in the one report a cycle reads every morning. See
`caveat_lines`; on this morning's sweep four clean checks carried one,
measured, and ten more blind lines across nine checks were being dropped
because the matcher wanted the marker at the front of the line.

The uniform exit contract is what makes this possible, and it was already
there: every one of these modules documents **2 = a finding to act on,
1 = something was unreadable and never reads as clean, 0 = nothing to act
on**. This aggregates them the same way: the overall status is the worst
one, so `preflight` exiting 0 means every check exited 0.

**A check that did not run must never look like a check that came back
clean.** So the roster is verified against the `tools/` directory before
anything runs, an unknown name is a hard error rather than a skip, and the
footer names every check that ran with its elapsed time.

That guard reads `CHECKS` against the `tools/` directory, so it cannot see
the failure one level up: **a roster that is itself out of date.** Cycle 644
ran this from a checkout still sitting on `nova/doc-integrity-frontmatter`,
a branch a previous cycle had merged and left behind, nineteen commits
behind `main`. It printed `Ran 19 check(s)` and a clean table, and the four
NAS checks -- the ones the owner calls the highest priority on this estate --
plus `cadence_control`, which moves my own heartbeat, did not exist in that
tree at all. They were absent from `CHECKS`, so `unknown_checks` had nothing
to compare and nothing to refuse. A check missing from the roster is
invisible to a guard that validates the roster.

So `source_revision` runs first, always, and it is deliberately *not* in
`CHECKS`: it is computed in this process rather than as a `tools/` module,
because a module would be missing from exactly the stale tree it is meant
to catch. It names the commit these checks came from and how far behind
`origin/main` it is, and **being behind raises 2** -- the sweep is a
partial one. Where the gap is whole missing files it names them, because
"missing: nas_watch, nas_egress" beats "some unknown subset"; the commit
count stays the trigger, since a tree can also carry an older *version* of
a check that still exists and no name diff would see that. A branch that is
behind and also ahead is told to merge rather than to check main out.
Being only *ahead* (an ordinary feature branch) prints and does not raise.
It is timed and counted in the footer like any other row, and it is the one
row that runs serially, because everything after it depends on the answer. A crash inside a
check is a non-zero exit and gets the loud treatment, so the failure mode
of this tool is noisy, not silent -- which is the direction "How to work"
asks for when a negative result could otherwise be guaranteed in advance.

**Every row says where its subject lives, and the footer counts the two
separately.** All 34 of these run here, on server1, inside the cycle -- so
the ones whose subject is *also* here cannot report that subject failing
completely. On 2026-09-01 the box was dark for eight hours and eighteen
minutes and not one check in this sweep said so. That is structural, not a
bug in any of them, and the honest fix is to stop counting a status readout
as coverage. See `SUBJECT`.

Checks run concurrently because they are independent and several are slow
(`pin_drift` ~30s, `security_alerts` ~14s against 21 repos); output is
printed in the declared order regardless, so two runs are comparable. The
exception is `SOLO`, which is the roster of checks whose subject this sweep
is itself load on -- they run after the pool drains, alone.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

#: The step-1a status checks, in the order `prompt.md` lists them.
#:
#: `tidy_workspace` and `top_board_rows` are deliberately NOT here. They do
#: not share the exit contract and their output is not a status line a cycle
#: can skim -- `top_board_rows` prints the pick itself and has to be read in
#: full, and `tidy_workspace` moves files. Collapsing either would hide the
#: thing the step exists to show.
#:
#: `cadence_control` is here and it is the one entry that *acts* -- it moves
#: Nova's own heartbeat interval so the seven-day window lands on zero, which
#: is the owner's 2026-08-29 capture. It is here rather than in its own step for
#: the reason that makes it dynamic at all: a controller a cycle has to
#: remember to run is not a controller, and this is the one call every cycle
#: already makes. It earns the place by satisfying the contract above rather
#: than by being a check -- one skimmable status line, and 0/1/2 meaning the
#: same three things they mean for everything else. Two cycles running it at
#: once is safe by construction and not by luck: the second sees the first's
#: change against a burn rate still earned at the old interval and holds.
CHECKS = (
    "cadence_control",
    "security_alerts",
    "scanning_alerts",
    "agentic_health",
    "doc_integrity",
    "redact_coverage",
    "reloader_coverage",
    "cli_pin",
    "pin_drift",
    "ci_minutes",
    "eol_watch",
    "cli_features",
    "credential_recovery",
    "changelog_watch",
    "cache_health",
    "hook_cost",
    "heartbeat_health",
    "heartbeat_gaps",
    "cycle_postmortem",
    "reply_health",
    "lifecycle_health",
    "schedule_health",
    "helm_repo_health",
    "argocd_health",
    "cronjob_health",
    "log_secret_scan",
    "crossplane_health",
    "seal_cert_drift",
    "claim_drift",
    "claim_schema",
    "roll_health",
    "board_done_drift",
    "ticket_drift",
    "running_images",
    "workload_health",
    "alerts",
    "trace_health",
    "telegram_inbox",
    "login_handshake",
    "host_memory_trend",
    "memory_headroom",
    "node_memory",
    "limit_headroom",
    "cpu_throttle",
    "oom_rank",
    "oom_history",
    "disk_health",
    "ci_health",
    "open_prs",
    "main_build",
    "rollback_watch",
    "backup_health",
    "marcus_capacity",
    "nas_health",
    "nas_watch",
    "nas_egress",
    "nas_versions",
    "nas_ports",
    "nas_privilege",
    "survey",
    "roadmap_drift",
    "recap_health",
    "browser_env_health",
    "marcus_reminder",
)

#: Checks that must not run while the rest of the sweep is running, because
#: the sweep is load on the thing they measure.
#:
#: `cpu_throttle` reads how much of each container's runnable time the kernel
#: took away at its own CPU limit, over a 20-second window. Run inside the
#: concurrent group it samples a cluster that this sweep is hammering, and the
#: two containers it hammers hardest are the two it then reports: `nova-site`,
#: which `reply_health` asks for 30 cycle threads, and `agora-claude-bridge`,
#: which is the pod every check in the sweep executes in. Measured Cycle 1061,
#: two minutes apart on an otherwise idle cluster: inside the pool nova-site
#: read **89.4% throttled over 151 periods** and the bridge **53.0% over 302**,
#: raising exit 2; run alone, nova-site accumulated **29** periods and fell
#: under `MIN_PERIODS` entirely while the bridge read **0.0%**. So the ACT was
#: manufactured by the observer, and a cycle acting on it would have gone
#: looking for a load that only exists while it is looking.
#:
#: Running these after the pool drains costs their own wall clock -- about 21s
#: for `cpu_throttle` -- and that is the price of the number meaning anything.
#: It is not perfect isolation and does not claim to be: another Nova cycle
#: sweeping at the same moment is load this process cannot see. What it removes
#: is the load this process makes itself, which is the part that was guaranteed
#: to be there every single sweep.
SOLO = ("cpu_throttle",)

#: Where each check's *subject* lives, and it is not where the check runs --
#: every one of these runs here, inside the cycle, on server1.
#:
#: server1 went dark at 22:40 Oslo on 2026-09-01 and came back at 06:58 the
#: next morning. Eight hours and eighteen minutes, about twenty-five heartbeat
#: slots that produced nothing, and not one of the 34 checks below reported it
#: -- because when the box dies, a check that runs on the box dies with it.
#: Three retros in a row tried to fix the growth of this sweep with a count
#: ("no new instrument without deleting one"). Both counts failed. The
#: criterion that replaces them is not a number
#: (`nova/resources/research/monitoring-that-survives-2026-09-02.md`):
#:
#:     Would this still report if the thing it watches failed completely?
#:
#: `off-box` means yes -- the subject is somewhere else (GitHub, the NAS,
#: endoflife.date), so the subject can fail while the check keeps answering.
#: `on-box` means no. Those are **status readouts**, not monitors. They are
#: cheap and worth having and this does not propose deleting any of them; the
#: one thing they must not do is be counted as coverage of the failure they
#: cannot survive. The only instrument that reported last night is
#: `nova-deadman`, which is not in this sweep at all and does not run here.
#:
#: The string is the subject, so the label carries its own reason.
SUBJECT = {
    "source_revision":   ("on-box",  "this checkout"),
    "cadence_control":   ("on-box",  "my own heartbeat and burn rate"),
    "security_alerts":   ("off-box", "GitHub advisories"),
    "scanning_alerts":   ("off-box", "GitHub code- and secret-scanning alerts"),
    "agentic_health":    ("off-box", "GitHub workflow history"),
    "doc_integrity":     ("on-box",  "the vault, served by CouchDB here"),
    "redact_coverage":   ("on-box",  "workloads in this cluster"),
    "reloader_coverage": ("on-box",  "Reloader and workloads in this cluster"),
    "cli_pin":           ("off-box", "published Claude CLI releases"),
    "rollback_watch":    ("off-box", "the -config repo's history on GitHub"),
    "backup_health":     ("off-box", "the backup repos on GitHub"),
    "pin_drift":         ("off-box", "upstream action and image versions"),
    "ci_minutes":        ("off-box", "this org's GitHub Actions billing"),
    "eol_watch":         ("off-box", "endoflife.date"),
    "cli_features":      ("on-box",  "the CLI's flag cache on this pod"),
    #: On-box because the Secret it judges is only readable here, as an
    #: environment variable -- the Secret object itself is refused by RBAC.
    "credential_recovery": ("on-box", "the recovery credential in the claude-auth Secret"),
    "changelog_watch":   ("off-box", "the upstream CLI changelog"),
    "cache_health":      ("on-box",  "my own cycle records"),
    "hook_cost":         ("on-box",  "my own cycle records"),
    "heartbeat_health":  ("on-box",  "Agora, in this cluster"),
    "heartbeat_gaps":    ("on-box",  "Agora, in this cluster"),
    "cycle_postmortem":  ("on-box",  "my own journal and Agora"),
    "reply_health":      ("on-box",  "nova-site, in this cluster"),
    #: On-box in the strongest sense -- the ledger is a file on this Pod's own
    #: volume, written by the process this cycle runs inside. It survives the
    #: Pod, but nothing outside this box would answer if server1 died.
    "lifecycle_health":  ("on-box",  "the bridge's own shutdown ledger, on this Pod's volume"),
    "schedule_health":   ("off-box", "GitHub Actions scheduling"),
    "helm_repo_health":  ("on-box",  "Helm sources in this cluster"),
    "argocd_health":     ("on-box",  "ArgoCD in this cluster"),
    "cronjob_health":    ("on-box",  "CronJobs in this cluster"),
    "log_secret_scan":   ("on-box",  "the logs of every pod in this cluster"),
    "crossplane_health": ("on-box",  "Crossplane in this cluster"),
    "seal_cert_drift":   ("on-box",  "the sealing key this cluster serves, against the cert platform-config commits"),
    "claim_drift":       ("on-box",  "live claims in this cluster"),
    "claim_schema":      ("on-box",  "live claims in this cluster"),
    "roll_health":       ("on-box",  "my own digest and handoff files"),
    "board_done_drift":  ("on-box",  "the owner's boards against my own claim ledger"),
    "ticket_drift":      ("on-box",  "the ticket store, in CouchDB here"),
    "running_images":    ("on-box",  "containers running in this cluster"),
    "workload_health":   ("on-box",  "workloads in this cluster"),
    "alerts":            ("on-box",  "Prometheus in this cluster"),
    "trace_health":      ("on-box",  "the live prometheus and tempo in this cluster"),
    "telegram_inbox":    ("on-box",  "the Telegram bridge in this cluster"),
    "login_handshake":   ("on-box",  "this pod's credential and the Telegram bridge"),
    #: These two read this pod's own /proc, so their subject is whichever node
    #: the bridge pod is scheduled on -- server1 today. server2 joined on
    #: 2026-09-03 and neither of them can see it. `oom_history` below was the
    #: same shape and is fixed; these two need a different mechanism than a
    #: node argument, and that is my own issue rather than a thing to paper
    #: over by renaming the label.
    "host_memory_trend": ("on-box",  "the bridge pod's own node's memory"),
    "memory_headroom":   ("on-box",  "the bridge pod's own node's memory"),
    #: The different mechanism the comment above asks for: the kubelet's own
    #: stats, over nodes/proxy, so every node is judged and not just this one's.
    "node_memory":       ("on-box",  "every node's own free memory and swap"),
    #: node_memory reads what a node has left; this reads what each container
    #: has left of its *own* limit, which is what killed grafana on a node that
    #: had 4.8GiB free at the time.
    "limit_headroom":    ("on-box",  "each container against its own memory limit"),
    #: and this one reads each container against its own *CPU* limit, which no
    #: usage figure can express: pinned at the limit and using half of it both
    #: read as a number below a ceiling.
    "cpu_throttle":      ("on-box",  "each container against its own CPU limit"),
    "oom_rank":          ("on-box",  "every node's kernel kill order"),
    "oom_history":       ("on-box",  "every node's own kernel log"),
    "disk_health":       ("on-box",  "every node's own disk, over its kubelet"),
    "ci_health":         ("off-box", "GitHub's minute meter"),
    "open_prs":          ("off-box", "open pull requests on GitHub"),
    "main_build":        ("off-box", "the default branch build on GitHub"),
    "nas_health":        ("off-box", "the NAS"),
    "nas_watch":         ("off-box", "the NAS"),
    "nas_egress":        ("off-box", "the NAS"),
    "nas_versions":      ("off-box", "the NAS"),
    "nas_ports":         ("off-box", "the NAS"),
    "nas_privilege":     ("off-box", "the NAS"),
    "survey":            ("on-box",  "this repository's own source"),
    "roadmap_drift":     ("on-box",  "the roadmap in the vault; the boards it names, in the record store"),
    "recap_health":      ("on-box",  "the recap card's own file in the vault"),
    "browser_env_health": ("on-box",  "the browser environment on this pod's own volume"),
    "marcus_capacity":   ("on-box",  "Marcus's state document, read over the cluster network"),
    "marcus_reminder":   ("on-box",  "the Marcus reminder Job's own log, in this cluster"),
}


#: How often each check has to actually run, in hours. 0 means every sweep.
#:
#: The owner, idea #183: *"Cut preflight frequency from every cycle to weekly or
#: monthly once the NAS security work is done -- it's a two-person estate,
#: running full checks every cycle is wasteful of tokens."* Cycle 673 did the
#: half that did not wait on him -- a standing finding collapses to one line
#: instead of reprinting -- and deliberately left the frequency alone. What was
#: left is the floor that collapse cannot touch: **the summary table itself.**
#: Measured on a real sweep, 2026-09-06 05:55 Oslo, 57 checks: 19,477 bytes of
#: table and caveats against 32,728 bytes of full findings. The table is the
#: part that is the same every time and is paid ~96 times a day.
#:
#: So the saving is not running fewer checks for its own sake. It is that a
#: check which came back clean an hour ago, and whose subject cannot have moved
#: since, costs a subprocess and a table row to say so again.
#:
#: **The one safety property, and everything else follows from it: a check that
#: did not exit 0 last sweep is due every sweep, whatever its cadence says.** A
#: cadence can therefore only ever delay re-confirming *good* news. An alarm
#: that is already ringing keeps being re-measured until it stops, which is also
#: the only way it can ever be seen to clear. A check with no record at all is
#: due too, so a fresh state file runs the whole roster -- the safe direction.
#:
#: The tiers are a judgement about the subject, not a measurement, and they are
#: written as one sentence each rather than a number I cannot defend:
#:
#:   0    the subject can change between two cycles AND the change would change
#:        what I do in this cycle -- the live cluster, the loop itself, my own
#:        files, and anything I might have altered by merging something.
#:   24   the subject moves on a human or daily scale: GitHub's meters, upstream
#:        releases, my own history, disks and memory trends.
#:   168  the subject moves on a release or estate scale: base-image EOL, Helm
#:        sources, annotations on workloads, the NAS's own surface.
DEFAULT_CADENCE_HOURS = 24.0

CADENCE_HOURS = {
    # Every sweep -- live cluster state, and a failure here is this cycle's work.
    "workload_health": 0.0,
    "argocd_health": 0.0,
    "crossplane_health": 0.0,
    "alerts": 0.0,
    "limit_headroom": 0.0,
    "cpu_throttle": 0.0,
    "node_memory": 0.0,
    "oom_history": 0.0,
    # Every sweep -- the loop itself, and `cadence_control` *acts* on a burn
    # rate that is only current if it is read now.
    "cadence_control": 0.0,
    "heartbeat_health": 0.0,
    "heartbeat_gaps": 0.0,
    # Every sweep -- the owner is waiting on a reply in it. Same reason it is in
    # NEVER_COLLAPSE: "unchanged since last sweep" is the worst possible reason
    # to stop looking at an unanswered message.
    "telegram_inbox": 0.0,
    # Every sweep -- it is the second half of telegram_inbox's reason. He
    # answers the 5-day reminder once and expects a link back; a daily cadence
    # would leave that yes sitting for up to a day, which is most of the margin
    # the reminder exists to buy. Outside the window it is one file read.
    "login_handshake": 0.0,
    # Every sweep -- the card it watches goes stale after 3h, so a 24h cadence
    # let it read clean on the sweep where the staleness actually began. Same
    # reason it is in NEVER_COLLAPSE.
    "recap_health": 0.0,
    # Twelve hours -- its subject is one CronJob run a night at 20:00 Oslo, so a
    # cadence past 24h would step over a night entirely, and anything shorter
    # re-reads a log that cannot have changed. A verdict that raises is due
    # every sweep regardless of what this says.
    "marcus_reminder": 12.0,
    # Every sweep -- a directory on a volume any cycle can tidy, and the check
    # is a handful of stat calls. A 24h cadence would let the sweep that first
    # sees the loss be the one that collapses it.
    "browser_env_health": 0.0,
    # Every sweep -- three stat calls and a JSONL read of a file on this Pod.
    # The bridge rolls when I merge into it, which is exactly the cycle that is
    # busy with the merge, so a 24h cadence would let the sweep that first sees
    # a killed drain be the one that skipped it.
    "lifecycle_health": 0.0,
    # Every sweep -- files I rewrite in my own wrap-up, so the previous cycle is
    # exactly who could have broken them.
    "doc_integrity": 0.0,
    "roll_health": 0.0,
    # Every sweep -- the ledger window is about a day, so a drift this
    # collapses is one that ages out of the evidence before it is read.
    "board_done_drift": 0.0,
    # Every sweep -- a pull request I open or merge in this cycle moves both.
    "open_prs": 0.0,
    "main_build": 0.0,
    # Daily -- GitHub's meters, upstream releases, my own history.
    "security_alerts": 24.0,
    "scanning_alerts": 24.0,
    "agentic_health": 24.0,
    "cli_pin": 24.0,
    "pin_drift": 24.0,
    "ci_minutes": 24.0,
    "ci_health": 24.0,
    "cli_features": 24.0,
    "changelog_watch": 24.0,
    "credential_recovery": 24.0,
    "cache_health": 24.0,
    "hook_cost": 24.0,
    "cycle_postmortem": 24.0,
    "reply_health": 24.0,
    "seal_cert_drift": 24.0,
    "claim_drift": 24.0,
    "claim_schema": 24.0,
    "ticket_drift": 24.0,
    "running_images": 24.0,
    "cronjob_health": 24.0,
    "log_secret_scan": 24.0,
    "disk_health": 24.0,
    "host_memory_trend": 24.0,
    "memory_headroom": 24.0,
    "oom_rank": 24.0,
    "marcus_capacity": 24.0,
    "backup_health": 24.0,
    "rollback_watch": 24.0,
    "trace_health": 24.0,
    "roadmap_drift": 24.0,
    "survey": 24.0,
    "nas_health": 24.0,
    # Weekly -- a release train, a chart source, an annotation on a workload or
    # a listening port on a box nobody reinstalls between Tuesdays.
    "eol_watch": 168.0,
    "schedule_health": 168.0,
    "helm_repo_health": 168.0,
    "reloader_coverage": 168.0,
    "redact_coverage": 168.0,
    "nas_watch": 168.0,
    "nas_egress": 168.0,
    "nas_versions": 168.0,
    "nas_ports": 168.0,
    "nas_privilege": 168.0,
}


def uncadenced_checks(names):
    """Names in `names` with no entry in `CADENCE_HOURS`.

    Same contract as `unlabelled_checks`: a hard error before anything runs,
    rather than a silent fall back to the default. A check added without a
    cadence would inherit a whole day of staleness from a dict it is not in,
    and nobody would ever find out -- the row would look identical to a check
    someone had thought about.
    """
    return [n for n in names if n not in CADENCE_HOURS]


def due_and_held(names, state, now):
    """Split `names` into the checks to run now and the ones to carry forward.

    Returns `(due, held)`, where `held` is a list of `(name, entry, cadence)`
    for checks that exited 0 within their cadence. Their previous verdict is in
    `entry`; nothing here invents one.

    `state` of `None` -- which is what `--verbose` and `--no-state` pass --
    holds nothing back at all, so there is always one flag that runs the whole
    roster on demand.
    """
    if state is None:
        return list(names), []
    due, held = [], []
    for name in names:
        entry = state.get(name) or {}
        cadence = CADENCE_HOURS.get(name, DEFAULT_CADENCE_HOURS)
        ran_at = entry.get("ran_at")
        # A check that did not come back clean is due every sweep, whatever its
        # cadence says. A check with no record has never run under this
        # mechanism and is due as well.
        if not ran_at or entry.get("code", 0) != 0:
            due.append(name)
            continue
        hours = (now - ran_at) / 3600.0
        # `hours >= cadence` is what makes a cadence of 0 run every sweep; there
        # is deliberately no separate `cadence <= 0` clause, because a mutation
        # round showed it could never change an answer and a guard that cannot
        # fail is not a guard. The negative case is the one it does not cover:
        # two cycles share this record and their clocks are not the same clock,
        # so a record stamped slightly in the future must read as due rather
        # than as held for the length of the skew.
        if hours < 0 or hours >= cadence:
            due.append(name)
        else:
            held.append((name, entry, cadence))
    return due, held


def held_lines(held, now):
    """The report block for checks this sweep did not run. Empty list if none."""
    if not held:
        return []
    lines = [f"{len(held)} check(s) were not run: each exited 0 at the time named and its "
             f"cadence has not come round again. A check that did NOT come back clean is "
             f"run every sweep regardless, so nothing red is being carried here."]
    for name, entry, cadence in sorted(held):
        ago = (now - entry.get("ran_at", now)) / 3600.0
        due_in = max(0.0, cadence - ago)
        where, _subject = SUBJECT.get(name, ("?", "unlabelled"))
        lines.append(f"  {name:20}{where:9}ok, {ago:.1f}h ago; due again in {due_in:.1f}h")
    return lines


def unlabelled_checks(names):
    """Names in `names` with no entry in `SUBJECT`.

    Treated exactly like a name with no module: a hard error before anything
    runs. An unlabelled check would be counted in neither total, so adding a
    check and forgetting the label would quietly shrink the coverage figure
    this whole thing exists to state -- the same silent-shrink failure as a
    roster that has gone stale.
    """
    return [n for n in names if n not in SUBJECT]


#: Wall-clock ceiling per check. Above any of them by a wide margin --
#: the slowest measured is `pin_drift` at ~30s against 21 repos -- so a
#: check that hits this has hung, and a hang is reported as a failure
#: rather than waited on.
TIMEOUT_SECONDS = 240

STATUS_WORD = {0: "ok", 1: "UNREADABLE", 2: "ACT"}


def tools_dir():
    return os.path.dirname(os.path.abspath(__file__))


def unknown_checks(names, directory=None):
    """Names in `names` with no matching module in the tools directory.

    A typo here would otherwise run nothing and report nothing, which is
    the one outcome this tool must not have.
    """
    directory = directory or tools_dir()
    return [n for n in names if not os.path.isfile(os.path.join(directory, n + ".py"))]


#: Extra arguments a check is run with here, and nowhere else.
#:
#: `alerts --notify` is the only entry today and it is the one thing in this
#: file that reaches outside the cluster. The owner asked for it on
#: 2026-09-04 -- *"make Nova connect to it so that important alerts get sent
#: to me like if one server is down"* -- and this is the call every cycle
#: already makes, which is the same argument that put `cadence_control` here:
#: a pager a cycle has to remember to run is not a pager. The quiet-hours
#: window and the six-hour dedupe live in `tools.notify`, so nothing here
#: decides when his phone rings; this only decides that the question gets
#: asked once every cycle.
CHECK_ARGS = {
    "alerts": ["--notify"],
    # The second entry, added 2026-09-05 on his ask on the cycle 961 card:
    # *"Set a reminder to send me a notification on telegram 5 days before
    # expired."* The deadline it watches is the one thing in this file no
    # cycle can fix, so a finding that only ever lands in preflight output is
    # a finding read exclusively by the party that cannot act on it.
    "credential_recovery": ["--notify"],
}


def run_check(name):
    """Run one check as a subprocess. Returns (name, exit_code, output, seconds)."""
    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "tools." + name] + CHECK_ARGS.get(name, []),
            cwd=os.path.dirname(tools_dir()),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        output = (f"TIMED OUT after {TIMEOUT_SECONDS}s with no verdict. "
                  f"A hung check is not a clean check.")
        code = 1
    return name, code, output, time.monotonic() - started


def summary_line(output):
    """A pointer at what the check measured. **The verdict is the exit code.**

    The first version of this took the last non-empty line, on the belief
    that every check ends on its verdict. Measured against a real run of all
    fourteen: about half of them do not. `agentic_health`, `heartbeat_health`,
    `argocd_health`, `cli_features` and `schedule_health` each close on an
    explanatory footnote -- *"A heartbeat that is off on purpose carries
    '(disabled' in its own name"* -- which is true, useful in place, and says
    nothing about what this morning's sweep found. Collapsing a check to that
    line is a summary that cannot vary with the result, which is the
    positive-guaranteed-in-advance failure wearing a table.

    So the rule is narrower and it is a heuristic, stated rather than hidden:
    **the last line carrying a digit**, because every one of these tools
    reports its sweep as a count -- "Read 12 ArgoCD Application(s)", "Swept 11
    document(s)", "Caching healthy on all 7 day(s) judged". A count moves when
    the world moves; a footnote does not. Where no line carries a digit the
    last non-empty line is the fallback.

    It is still a guess at which line matters, and it is allowed to be one
    because nothing rests on it: the exit code is the verdict and it is exact,
    a non-clean check is reproduced in full regardless, and `--verbose` prints
    every check whole.
    """
    lines = [l.strip() for l in output.splitlines() if l.strip()]
    if not lines:
        return "(no output)"
    for line in reversed(lines):
        if any(ch.isdigit() for ch in line):
            return line
    return lines[-1]


#: The vocabulary a check uses when it says it could not judge part of its
#: own scope. These are *stems*, matched inside the shouted opening of a line
#: rather than as whole prefixes of it, and that is the whole of Cycle 710's
#: change.
#:
#: It used to be seven complete phrases, matched with `startswith`. Cycle 699's
#: reviewer found the hole in one check -- `host_memory_trend` printed
#: `CANNOT COUNT`, which begins with none of them, so on an otherwise-clean run
#: that caveat was dropped from the report entirely -- and the repair was a
#: test in that one module asserting its own lines match. That fixes one check
#: and leaves the next author to rediscover the rule, which is the same shape
#: as the Cycle 646 mistake `caveat_lines` below already documents.
#:
#: So I measured the rest instead of guessing: parsing every string literal in
#: all 31 checks turned up ten more blind lines that no marker matched, across
#: nine modules -- `CANNOT ATTRIBUTE MEMORY` (the issue #131 line, in two
#: checks), `CANNOT TREND`, `CANNOT GO GREEN`, `COULD NOT PLACE`,
#: `COULD NOT JUDGE`, `COULD NOT WRITE`, and `SERVICES UNREADABLE` in the four
#: NAS checks. That last one is the tell that a prefix list was the wrong
#: shape: the marker is *in* the line, just not at the front of it.
#:
#: A stem cannot fix that alone -- "CANNOT" appears in ordinary prose -- so the
#: anchor moves rather than disappearing: the line still has to *open* with a
#: shouted run of capitals, and the stem has to be inside that run. The
#: footnote the old docstring protects against, "a check that exits 1 is
#: UNREADABLE and never reads as clean", opens with a lowercase word and is
#: still not selected.
CAVEAT_STEMS = ("NOT JUDGED", "NOT ASKED", "NOT READ", "UNJUDGED",
                "CANNOT", "COULD NOT", "UNREADABLE")

#: The shouted opening of a line: capitals, digits and the punctuation that
#: shows up inside one of these headers, from the first character. A line that
#: starts with an ordinary sentence has a one-character head and can never
#: carry a stem.
SHOUTED_HEAD = re.compile(r"^[A-Z][A-Z0-9 '/-]*")


def shouted_head(line):
    """The run of capitals a line opens with, or "" if it opens in prose."""
    match = SHOUTED_HEAD.match(line.strip())
    return match.group(0) if match else ""


def is_caveat(line):
    """Does this line say the check could not judge part of its own scope?"""
    head = shouted_head(line)
    return any(stem in head for stem in CAVEAT_STEMS)


#: How long a *standing* finding may go without being reproduced in full.
#: The owner, comments board 2026-08-30: *"It is a bit heavy to check this
#: system every day... So spending that many tokens is wasteful."* Measured on
#: this morning's sweep: eight of the twenty-six checks exited non-zero, every
#: one of them a finding I cannot close from this loop -- server1's memory
#: (issue #131), the two NAS apps waiting on his upgrade, a Dependabot alert
#: that is already fixed and unrescanned, goreleaser v5. Between them they are
#: ~120 lines reproduced verbatim, at a 40-minute cadence, roughly 36 times a
#: day, and then carried in context for the ~90 turns that follow.
#:
#: This does not drop any of them and does not touch a single exit code. A
#: finding that has not changed since the last sweep collapses to one line
#: naming when it was first seen and when its full text last printed; the row
#: above it still says ACT, `--verbose` still prints it whole, and after this
#: many hours it prints in full again whether or not it changed. The rule is
#: the owner's own (`personality.md`): an interface problem gets an interface,
#: never less data.
REPRINT_HOURS = 24.0

#: Checks the repeat collapse above may never touch, however unchanged they are.
#:
#: The collapse is built for a *standing* finding -- a fact about the cluster
#: that I cannot close from this loop and that will still be true tomorrow.
#: `telegram_inbox` is not that. Its non-zero exit means the owner has typed
#: something to me and is waiting for an answer, and "unchanged since the last
#: sweep" is the worst possible reason to stop printing it: an unanswered
#: message is unchanged *precisely because nobody has dealt with it yet*.
#:
#: On 2026-09-04 the owner wrote at 14:25 Oslo. Cycles 913 and 914 both swept,
#: both read `telegram_inbox ACT` with `UNCHANGED since ... full text printed
#: 0.4h ago` under it, and neither opened it. It was answered at 17:10, two and
#: three quarter hours later. Nothing was broken -- the collapse did exactly
#: what it was written to do, to the one check where doing it is the failure.
#:
#: `recap_health` joined it on 2026-09-08, and its case is the same one under
#: a different name. The card it watches is the twelve-hour summary at the top
#: of the owner's Journal page; it goes stale after three hours and I rewrite
#: it in one command. On 2026-09-08 06:32 that card had been stale for 52.9
#: hours, covering cycles that had ended two days earlier, and the owner filed
#: it 🔴 Immediately: *"The Journal box for the last 12 hours summary has not
#: been updated for over 24 hours."* The check was raising the whole time and
#: every one of those sweeps collapsed it, because `finding_shape` blinds
#: digits -- so `3.1h ago` and `52.9h ago` are the same fingerprint, and the
#: one number that says how bad it has got is the one number the collapse
#: cannot see. That is `telegram_inbox` again: the text is unchanged precisely
#: because nobody has dealt with it, and here the person waiting is waiting at
#: a page rather than at a reply.
#:
#: Keep this set small and keep the bar explicit, and the bar is now two
#: clauses rather than one: a check belongs here if its finding is **someone
#: waiting on me**, and if **the wait itself is the number the fingerprint
#: blinds**. Everything else -- an alert, a full disk, a stale pin -- is a
#: standing fact about the cluster that I cannot close from this loop, and a
#: fact can be read once a day.
NEVER_COLLAPSE = frozenset({"telegram_inbox", "login_handshake", "recap_health"})

#: Where the "have I already printed this" record lives. Not in the checkout:
#: concurrent cycles each get their own `git worktree`, so a per-tree file
#: would make every cycle the first one. Not in `/data/workspace` either --
#: `tools.tidy_workspace` archives loose files at that root. `preflight` is
#: run from the bridge pod, whose `/data/claude-home` persists across cycles.
#: A run with no readable record simply prints everything, which is the safe
#: direction to fail in.
STATE_PATH = os.environ.get(
    "NOVA_PREFLIGHT_STATE",
    os.path.join(os.path.expanduser("~"), ".nova-preflight-state.json"),
)


def finding_shape(output):
    """A digit-blind fingerprint of a check's output, plus its line count.

    Exact text will not do. Most of these findings carry a number that moves
    every single run -- `1853Mi available`, `built 1334 day(s) ago`, an elapsed
    time -- so an exact comparison would say "changed" every cycle and this
    whole mechanism would never fire once.

    So digits are blinded. **Every other byte is not**, including the line
    breaks: the hash is taken over the whole joined text, so a second alert, a
    third unhealthy pod or a newly-missing module changes it and is printed in
    full. The one thing a digit-blind match forgives is the same finding
    restated with a different number in it, which is the case this exists for.

    The count is returned alongside for the message to quote and is not part
    of the comparison. It was, for one commit, and a mutation round showed
    that removing it changed nothing -- the join already carries it. A second
    guard that cannot fail is not a second guard.

    It is not a free ride even then: `REPRINT_HOURS` puts the full text back
    in front of me on a fixed clock regardless of whether anything moved.
    """
    lines = [l.rstrip() for l in output.splitlines() if l.strip()]
    blinded = "\n".join(re.sub(r"\d+", "#", l) for l in lines)
    return len(lines), hashlib.sha256(blinded.encode("utf-8")).hexdigest()[:16]


def load_state(path=None):
    """The record of what has already been printed. Unreadable is empty, never fatal."""
    try:
        with open(path or STATE_PATH) as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state, path=None):
    """Best effort. A read-only home must not take the sweep down with it."""
    path = path or STATE_PATH
    try:
        with open(path, "w") as fh:
            json.dump(state, fh)
    except OSError:
        pass


def _oslo(stamp):
    try:
        moment = dt.datetime.fromtimestamp(stamp, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return "an unreadable time"
    return moment.astimezone(ZoneInfo("Europe/Oslo")).strftime("%Y-%m-%d %H:%M Oslo")


def repeat_verdict(name, code, output, state, now):
    """(collapse?, one line about it, the state entry to keep) for one check.

    `state` is read, never mutated here -- the caller decides what to persist,
    so a `--verbose` run cannot quietly reset everybody's reprint clock.
    """
    count, shape = finding_shape(output)
    entry = state.get(name) or {}
    same = entry.get("shape") == shape
    first_seen = entry.get("first_seen", now) if same else now
    printed = entry.get("printed_at", 0.0) if same else 0.0
    hours = (now - printed) / 3600.0

    if not same or hours >= REPRINT_HOURS:
        return False, "", {"shape": shape, "lines": count,
                           "first_seen": first_seen, "printed_at": now}

    due = REPRINT_HOURS - hours
    return True, (f"UNCHANGED since {_oslo(first_seen)} ({count} line(s)); full text "
                  f"last printed {hours:.1f}h ago and prints again in {due:.1f}h. "
                  f"--verbose for it now."), {"shape": shape, "lines": count,
                                              "first_seen": first_seen,
                                              "printed_at": printed}


def caveat_lines(output):
    """The lines where a check says it could not judge part of its own scope.

    A check is allowed to exit 0 over a scope it did not fully cover, and that
    is honest: `nas_watch` cannot read nzbget's extension list because nobody
    has given this pod nzbget's password, and "locked, with no credential" is
    not a judgement of that list either way. What is *not* honest is that
    saying so costs the check a line and this report collapses a clean check
    to one -- so the caveat is in the output and structurally invisible in the
    one place a cycle reads every morning.

    Cycle 646 hit exactly that and fixed it in the wrong place. `nas_watch`
    said *"Judged the notification list of 2 service(s) of 2"* about a box with
    three code-execution surfaces, and the repair was to push the missing digit
    into that check's own summary line so `summary_line` would carry it. That
    works for one check and leaves the next one to rediscover the rule, because
    the constraint it satisfies -- *get your caveat into the last line that
    carries a digit* -- lives here and is written down nowhere a check's author
    would look. Three of those and the shape is the bug.

    So the collapse stops hiding them instead: a clean check's caveats are
    printed under its row, verbatim, however many there are. There is no cap
    and there should not be one -- the owner's rule (`personality.md`) is that
    an interface problem is solved with an interface, never by throwing data
    away, and `--verbose` is already the other end of that. Measured against a
    real sweep of all 25 checks on 2026-08-30: one clean check carried one
    caveat line. On 2026-08-31 it is four, and the rule below widens that
    again -- ten lines across nine checks that were being dropped.

    What counts as a caveat is `is_caveat`, and the reasoning for its shape is
    on `CAVEAT_STEMS`. The short version: the line has to *open* in shouted
    capitals and one of the stems has to be inside that opening. Anchoring on
    the whole marker was the previous rule and it dropped ten real blind lines
    across nine checks, because a header like `SERVICES UNREADABLE` carries the
    marker in its second word. Anchoring on the shout keeps out the footnote
    the old rule was protecting against -- *"a check that exits 1 is UNREADABLE
    and never reads as clean"* -- which opens in prose and still is not
    selected.
    """
    return [line.strip() for line in output.splitlines() if is_caveat(line)]


def missing_modules(git, directory):
    """Check modules that exist on `origin/main` and not in this tree, by name.

    The commit count is the trigger, because a tree can also carry an *older
    version* of a check that still exists and no name diff would see that. But
    where the gap is whole missing files, saying so beats saying "some unknown
    subset" -- it would have named the five Cycle 644 lost. Absent on any git
    failure, which is a smaller report and never a wrong one.
    """
    listed = git("ls-tree", "--name-only", "origin/main", "tools/")
    if listed.returncode != 0:
        return []
    try:
        here = set(os.listdir(os.path.join(directory, "tools")))
    except OSError:
        return []
    return sorted(
        os.path.basename(line)[:-3]
        for line in listed.stdout.split()
        if line.endswith(".py") and os.path.basename(line) not in here
    )


def source_revision(directory=None, fetch=True):
    """(exit code, one-line report) for the git revision these checks came from.

    The one thing a stale checkout cannot tell you is what it is missing, so
    this measures the only number that covers every absence at once: commits
    on `origin/main` that are not in `HEAD`. Zero means the roster above is
    the current one. Anything else means some unknown subset of the checks
    does not exist here.

    `fetch` is on because a local `origin/main` is itself a checkout of
    unknown age, and a guard against staleness that reads a stale reference
    is the negative-guaranteed-in-advance failure. A fetch that fails is not
    fatal -- it falls back to the local ref and says so, since a measured
    "behind by 19" off a stale ref is still a true finding.

    No git repository at all is `CANNOT SEE` and does not raise, matching
    `nas_health` on a pod with no hop: a verdict no change here could ever
    clear is one every cycle re-derives.
    """
    directory = directory or os.path.dirname(tools_dir())

    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    def git(*args):
        """Never raises. A git that hangs or is missing must not take the sweep down with it."""
        try:
            return subprocess.run(["git", "-C", directory] + list(args),
                                  capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return failed

    inside = git("rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        return 0, (f"CANNOT SEE -- {directory} is not a usable git checkout, so there is "
                   f"no revision to name.")

    head = git("rev-parse", "--short", "HEAD").stdout.strip() or "?"
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip() or "?"

    fetched = ""
    if fetch:
        got = git("fetch", "--quiet", "origin", "main")
        if got.returncode != 0:
            fetched = " (fetch failed, so this is measured against a local origin/main of unknown age)"

    counts = git("rev-list", "--left-right", "--count", "origin/main...HEAD")
    if counts.returncode != 0:
        return 1, (f"UNREADABLE -- on {branch} at {head}, and origin/main could not be "
                   f"resolved, so I cannot say whether these checks are the current ones.")
    try:
        behind, ahead = (int(n) for n in counts.stdout.split())
    except ValueError:
        return 1, (f"UNREADABLE -- on {branch} at {head}, and git answered "
                   f"{counts.stdout.strip()!r} rather than two counts.")

    where = f"on {branch} at {head}"
    if behind:
        absent = missing_modules(git, directory)
        named = (f" Missing from this tree: {', '.join(absent)}." if absent else
                 " Every check module on main exists here, so what is stale is their contents.")
        if ahead:
            fix = ("this branch carries its own work, so `git merge origin/main` "
                   "rather than checking main out")
            own = f" and {ahead} ahead"
        else:
            fix = "`git checkout main && git pull`"
            own = ""
        return 2, (f"BEHIND origin/main by {behind} commit(s){own} -- {where}{fetched}."
                   f"{named} Run {fix}, then re-run before trusting this sweep.")
    if ahead:
        return 0, (f"Current: {where}, {ahead} commit(s) ahead of origin/main and 0 behind"
                   f"{fetched} -- every check on main exists here.")
    return 0, f"Current: {where}, level with origin/main{fetched}."

def sweep_stamp(now=None, checkout=None):
    """One line saying when this sweep ran and which checkout it ran from.

    Cycles overlap, they share the bridge pod's `/tmp`, and they are the same
    model reading the same instructions -- so they invent the same scratch
    filenames. On 2026-09-06 cycle 1013 ran this sweep into `/tmp/preflight.txt`
    in the background, read that path before its own run had finished, and got
    a sweep another cycle had left there roughly ninety minutes earlier. Every
    verdict it then reasoned from was that other cycle's. It surfaced only
    because one row (`recap_health` ACT) contradicted the handoff and would not
    reproduce; the other fifty rows would have been acted on in silence.

    Nothing in the report said when it was taken, so a leftover copy and a
    fresh one are the same bytes. This is that missing sentence. It names the
    checkout as well as the clock, because two cycles running a minute apart
    have near-identical timestamps and always-different worktrees.

    The stamp is never omitted: an empty checkout prints as such rather than
    dropping the line or trailing off, since a report with no stamp reads
    exactly like the reports this exists to make legible. `tools_dir` cannot
    fail, so that fallback guards a caller passing one in, not this default.
    """
    import time as _time

    now = _time.time() if now is None else now
    when = _oslo(now)
    if checkout is None:
        checkout = os.path.dirname(tools_dir())
    return f"swept {when} from {checkout or 'an unreadable checkout'}"


def render(results, stream=None, verbose=False, state=None, now=None,
           keep=None, held=None):
    """Print the collapsed report. `results` is a list of (name, code, output, seconds).

    `state` is the repeat record from `load_state`; pass `None` to disable the
    repeat collapse entirely, which is what `--verbose` and `--no-state` do.
    `keep` is an optional dict this fills with the record to persist, so the
    caller owns the write and a run that printed nothing in full cannot mark
    everything as printed.
    """
    import time as _time

    # Resolved here rather than in the signature. A `stream=sys.stdout` default
    # binds the object that existed at import time, so anything that replaces
    # `sys.stdout` afterwards -- a test harness, a caller redirecting output --
    # is written past rather than to.
    stream = sys.stdout if stream is None else stream
    now = _time.time() if now is None else now
    worst = 0
    noisy = []
    repeated = []
    print(sweep_stamp(now), file=stream)
    print(f"{'check':20}{'where':9}{'verdict':12}{'s':>6}  summary", file=stream)
    caveated = 0
    for name, code, output, seconds in results:
        worst = max(worst, code)
        # Written before any branch below can `continue`, because this is what
        # `due_and_held` reads next sweep -- a check whose verdict never landed
        # in the record would be treated as one that has never run.
        if keep is not None:
            keep[name] = {"code": code, "ran_at": now}
        word = STATUS_WORD.get(code, f"EXIT {code}")
        where, _subject = SUBJECT.get(name, ("?", "unlabelled"))
        print(f"{name:20}{where:9}{word:12}{seconds:>6.1f}  {summary_line(output)}",
              file=stream)
        if code == 0:
            # A caveat that is *already* the summary line is not repeated: a
            # one-line report -- `source_revision` with no git checkout says
            # only `CANNOT SEE -- ...` and exits 0 -- would otherwise print
            # itself twice, once collapsed and once indented under itself.
            summary = summary_line(output)
            caveats = [line for line in caveat_lines(output) if line != summary]
            if caveats:
                caveated += 1
            for line in caveats:
                print(f"{'':41}  {line}", file=stream)
        # A check whose entire output IS its summary line has already been
        # reproduced, in the row above. `source_revision` is the live case:
        # it reports one sentence, so a "===== full output =====" block would
        # repeat it and an UNCHANGED note would cost a line to hide nothing.
        # Same reasoning as the caveat de-duplication a few lines up.
        if code != 0 and output.strip() == summary_line(output):
            continue
        exempt = name in NEVER_COLLAPSE
        if code != 0 and state is not None and not verbose and not exempt:
            collapse, note, entry = repeat_verdict(name, code, output, state, now)
            if keep is not None:
                keep[name] = {**keep[name], **entry}
            if collapse:
                print(f"{'':41}  {note}", file=stream)
                repeated.append(name)
                continue
        elif state is not None and keep is not None:
            entry = repeat_verdict(name, code, output, state, now)[2]
            # An exempt check prints in full on every sweep, so its record has
            # to say it was printed now. Otherwise its `printed_at` freezes at
            # whenever it last went through the branch above, and taking it
            # back out of NEVER_COLLAPSE would collapse it on the very next
            # sweep against a clock that had stopped months earlier.
            entry = {**entry, "printed_at": now} if exempt else entry
            keep[name] = {**keep[name], **entry}
        if code != 0 or verbose:
            noisy.append((name, code, output))

    for name, code, output in noisy:
        print(file=stream)
        print(f"===== {name}: exit {code} -- full output =====", file=stream)
        print(output.rstrip(), file=stream)

    print(file=stream)
    print(f"Ran {len(results)} check(s): {', '.join(n for n, _, _, _ in results)}.", file=stream)
    for line in held_lines(held or [], now):
        print(line, file=stream)
    on_box = [n for n, _, _, _ in results if SUBJECT.get(n, ("?",))[0] == "on-box"]
    off_box = [n for n, _, _, _ in results if SUBJECT.get(n, ("?",))[0] == "off-box"]
    print(f"{len(off_box)} watch(es) something off this box and would still answer if "
          f"server1 died: {', '.join(off_box) or 'none'}. The other {len(on_box)} run on "
          f"the box they watch, so they are status readouts rather than monitors and a "
          f"total failure of their subject silences them -- that is what happened on "
          f"2026-09-01 and it is not a fault in any of them.", file=stream)
    unclean = sum(1 for _, code, _, _ in results if code != 0)
    if worst == 0:
        print("Every check exited 0 -- nothing to act on, and each line above "
              "names what its check swept.", file=stream)
    else:
        shown = unclean - len(repeated)
        where = (f"their full output is above, unabridged"
                 if not repeated else
                 f"{shown} of them printed in full above, unabridged, and "
                 f"{len(repeated)} collapsed to a single line as unchanged")
        print(f"{unclean} check(s) did not come back clean; {where}. "
              f"Overall exit {worst}.", file=stream)
    if repeated:
        print(f"{len(repeated)} standing finding(s) were not reproduced because nothing in "
              f"them changed since the last sweep: {', '.join(repeated)}. Their rows above "
              f"still carry their real verdict, the exit code still counts them, and "
              f"`--verbose` prints them in full.", file=stream)
    if caveated:
        print(f"{caveated} check(s) exited 0 over a scope they could not fully judge; "
              f"their caveats are the indented lines above. Exit 0 is the right "
              f"status for those and it is not a claim about what they skipped.",
              file=stream)
    return worst


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", metavar="CHECK",
                        help="run just these checks, by module name")
    parser.add_argument("--list", action="store_true",
                        help="print the roster and exit")
    parser.add_argument("--no-fetch", action="store_true",
                        help="skip the git fetch in the source_revision check")
    parser.add_argument("--verbose", action="store_true",
                        help="reproduce every check in full, clean ones included")
    parser.add_argument("--no-state", action="store_true",
                        help="print every finding in full, ignoring what was printed last sweep")
    parser.add_argument("--all", action="store_true",
                        help="run every check now, ignoring its cadence; findings still collapse")
    args = parser.parse_args(argv)

    names = list(args.only or CHECKS)
    if args.list:
        for name in names:
            print(name)
        return 0

    missing = unknown_checks(names)
    if missing:
        print(f"NO SUCH CHECK: {', '.join(missing)} -- refusing to run, because a "
              f"check that never ran must not read as a check that came back clean.",
              file=sys.stderr)
        return 1

    unlabelled = unlabelled_checks(names)
    if unlabelled:
        print(f"NO SUBJECT LABEL: {', '.join(unlabelled)} -- refusing to run. Add it to "
              f"SUBJECT as on-box or off-box; an unlabelled check is counted in neither "
              f"total and would shrink the coverage figure without saying so.",
              file=sys.stderr)
        return 1

    uncadenced = uncadenced_checks(names)
    if uncadenced:
        print(f"NO CADENCE: {', '.join(uncadenced)} -- refusing to run. Add it to "
              f"CADENCE_HOURS with a reason; falling back to a default would give it a "
              f"day of staleness that nobody chose.", file=sys.stderr)
        return 1

    import time

    rev_started = time.monotonic()
    rev_code, rev_report = source_revision(fetch=not args.no_fetch)
    rev_seconds = time.monotonic() - rev_started
    state = None if (args.no_state or args.verbose) else load_state()
    now = time.time()
    cadence_state = None if (state is None or args.only or args.all) else state
    names, held = due_and_held(names, cadence_state, now)

    results = [("source_revision", rev_code, rev_report, rev_seconds)] + [None] * len(names)
    # `SOLO` checks are held out of the pool and run after it drains -- see the
    # constant. Their slot in `results` is the same either way, so the report
    # still prints in the declared order and two sweeps stay comparable.
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(run_check, name): i + 1
                   for i, name in enumerate(names) if name not in SOLO}
        for future in concurrent.futures.as_completed(futures):
            results[futures[future]] = future.result()
    for i, name in enumerate(names):
        if name in SOLO:
            results[i + 1] = run_check(name)

    keep = {} if state is not None else None
    if keep is not None:
        # Every check this sweep did not run keeps the record it already had,
        # verbatim -- the ones held back by their cadence, and the rest of the
        # roster when `--only` names a few. `save_state` replaces the whole
        # file, so an entry left out here is an entry deleted, and the next
        # sweep would read that check as one that has never run.
        keep.update({n: e for n, e in state.items() if n not in names})
    worst = render(results, verbose=args.verbose, state=state, keep=keep, now=now,
                   held=held)
    if keep:
        save_state(keep)
    return worst


if __name__ == "__main__":
    sys.exit(main())
