"""Copy every retained host sweep into the CPU ledger, on every cycle.

The sweep CronJob fires every 30 minutes on both nodes and the cluster keeps
16 of its Pods -- eight hours (platform-config#750). `/data/nova-host-cpu.jsonl`
is the durable copy `tools.host_cpu_history` reads to answer issue #169, and
until this module existed the only thing that wrote it was the tail end of
`tools.host_memory_trend`.

That coupling cost four hours of last night. Measured 2026-09-13 10:52 Oslo:
the ledger jumps 09-12T19:30Z straight to 09-12T23:30Z, and 23:30 is exactly
eight hours before the run that filled it -- the sweeps in between were reaped
before anything read them. `host_memory_trend` had been on a 24h cadence until
09:33 this morning, so twelve hours passed between two ingests against eight
hours of retention, and the difference is gone for good.

Cycle 1495 cut that cadence to 6h, which leaves two hours of margin. This
removes the margin question instead of tuning it, for two reasons that a
smaller cadence does not reach:

- **The ingest is cheap and the report is not.** `backfill_cpu` skips on the
  Pod name *before* `kubectl logs`, so a run with nothing to add is one
  `kubectl get pods` and no log read at all -- 0.27s, measured live. The memory
  check around it reads /proc, node capacity, pressure, cgroups and every Pod's
  working set. One cadence was governing both, and the ledger's freshness was
  hostage to the expensive half.
- **The memory check has five ways to return before it ever reaches the CPU
  ledger** -- unreadable `/proc/meminfo`, no node capacities, an unreadable
  memory ledger, memory it cannot attribute to a host. Each of those is a
  memory problem, and each of them silently stops the CPU ledger too.

It does not change what the cluster keeps. A sweep reaped before any run of
this is gone, and closing *that* would take a longer `successfulJobsHistoryLimit`
or an in-cluster writer -- neither is this. What this closes is the gap between
what the cluster still holds and what the ledger has taken from it.

Exit contract, the same one as its siblings in `tools.preflight`:

- 0 -- the ledger holds every retained sweep the cluster still has, whether or
  not this run had anything to add.
- 1 -- something was unreadable, which never reads as clean. That includes the
  case where unrecorded sweeps are sitting on the cluster and not one of their
  Pod logs could be read: a blind instrument must not print like a full one.

There is no exit 2. A gap in the ledger is not a thing a cycle can act on --
`tools.host_cpu_history` is what judges the ledger's contents, and raising
here as well would report one fact twice.
"""

import argparse
import sys

from tools.host_memory_trend import (DEFAULT_CPU_KEEP, DEFAULT_CPU_LEDGER,
                                     backfill_cpu, ledger_pods)


def report(path, keep=DEFAULT_CPU_KEEP, runner=None):
    """Ingest, then say what happened. Returns `(exit_code, lines)`."""
    before, why = ledger_pods(path)
    if before is None:
        return 1, [f"COULD NOT READ the CPU ledger at {path} — {why}"]

    written, runs, back_why = backfill_cpu(path, keep=keep, runner=runner)
    if back_why is not None:
        return 1, [f"COULD NOT INGEST — {back_why}. The Pod logs are the only "
                   "copy and the cluster keeps 8h of them, so a sweep missed "
                   "here is missed permanently."]
    if runs and not written:
        # Every unrecorded sweep was found and none of them could be read. The
        # ledger is behind the cluster and nothing here fixed it.
        return 1, [f"INGESTED NOTHING — {runs} retained sweep(s) are not in the "
                   "ledger and none of their Pod logs could be read."]
    if written:
        # Counted by re-reading rather than by adding, because `record_cpu`
        # trims the ledger to `keep` on the way in: a run that wrote 22 rows
        # into a full ledger holds 22 fewer than it did before.
        after, _why = ledger_pods(path)
        held = len(after) if after is not None else len(before)
        return 0, [f"ingested {written} CPU sample(s) from {runs} retained "
                   f"sweep(s) the ledger had never read; {path} now holds "
                   f"{held} sweep Pod(s)."]
    return 0, [f"current — every retained sweep is already in {path}, which "
               f"holds {len(before)} sweep Pod(s)."]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cpu-ledger", default=DEFAULT_CPU_LEDGER,
                        help="where the sweep's CPU rows are kept")
    parser.add_argument("--cpu-keep", type=int, default=DEFAULT_CPU_KEEP,
                        help="how many CPU samples to retain")
    args = parser.parse_args(argv)

    print("HOST CPU INGEST")
    code, lines = report(args.cpu_ledger, keep=args.cpu_keep)
    for line in lines:
        print(f"  {line}")
    return code


if __name__ == "__main__":
    sys.exit(main())
