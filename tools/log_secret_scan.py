"""Is a credential sitting in a running pod's log right now?

    python3 -m tools.log_secret_scan

Cycle 1120, on the owner's issue #178. On 2026-09-03 three failed `vault-backup`
Jobs printed a live GitHub token into their pod logs, because the token
reached git inside the clone URL. The *cause* was fixed the same day
(platform-config#613 moved it to `GIT_ASKPASS`), and the logs already written
stayed until the Jobs aged out of `failedJobsHistoryLimit`. What had no fix at
all was the noticing: `tools.secret_scan` reads repositories, `tools.redact_coverage`
reads this pod's own environment, and `tools.scanning_alerts` reads GitHub's
scanners. **None of them can see a pod log**, so a secret printed into one is
invisible to every instrument this loop owns and stays invisible until a
person happens to run `kubectl logs`. That is the gap this closes.

**It never prints what it matched, and that is the point rather than a
courtesy.** A check that reported the offending line would copy the live
credential into a preflight report, a journal entry and the vault, which is
strictly worse than leaving it in one pod log. It prints where — namespace,
pod, container, which line, which pattern — and the fix is always to go and
look with `kubectl logs` yourself.

**It reads the tail, not the whole log, and says so.** `--tail` bounds the
bytes pulled from 100-odd containers; a credential printed at startup by a
pod that has since logged more than that is outside what this read. Saying
"clean" without saying how far back it looked would be the wider-than-the-
measurement claim `personality.md` warns about, so the summary line always
carries the bound.

**A pod whose log it cannot read is named and never counts as clean.** The
whole failure mode here is a negative result that was guaranteed in advance:
a sweep that silently skips the pod holding the secret prints exactly what a
clean cluster prints. Pods that have never started a container are a separate
category — they are skipped by name, because a log that does not exist yet is
not a log that was refused.

**The negative was controlled before it was believed.** A sweep that reads
117 containers and reports nothing is the exact shape of a check whose
negative result was guaranteed in advance, so the first live run swapped one
pattern for a literal known to be in three real `vault-backup` logs
(`tools.mutate`, Cycle 1120): it named all three by pod and container and
exited 2. The clean reading afterwards is a measurement rather than a
tautology.

Exit status, matching `tools.cronjob_health` and the rest of the sweep:
**2 means a credential-shaped string is in a live pod log**, 1 means something
was unreadable — kubectl refused, no pods came back at all, or a started pod's
log could not be read — and 0 means every log it could read was clean over the
window it read, naming the window either way.
"""

import argparse
import concurrent.futures
import json
import re
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

#: How many lines of each container's log to pull. 117 containers were live
#: when this was written and `preflight` kills a check at 240s, so the bound
#: is real rather than decorative. 5000 lines is about 400KB from the
#: chattiest pod here and the whole sweep measured well inside the budget.
DEFAULT_TAIL = 5000

#: How many `kubectl logs` calls to have in flight. The calls are IO-bound
#: against one API server; 8 is what took the sweep from ~35s serial to ~6s.
WORKERS = 8

#: Each entry is (name, compiled pattern). The names are what gets printed —
#: never the match. Every pattern is anchored on a vendor prefix rather than
#: on entropy, because an entropy rule on a log full of base64 and container
#: digests reports everything, and a check that cries wolf every cycle is one
#: nobody reads. The literals are built by concatenation so that this file
#: itself carries no token-shaped string for GitHub's secret scanner to flag —
#: `tools/sync_contract.py` had an alert open for 22 days on exactly that.
_GH = "gh" + "[pousr]_[A-Za-z0-9]{30,}"
_PAT = "github" + "_pat_[A-Za-z0-9_]{30,}"
_ANT = "sk-" + "ant-[A-Za-z0-9_-]{20,}"
_AWS = "AKIA" + "[0-9A-Z]{16}"
PATTERNS = (
    ("github token", re.compile(_GH)),
    ("github fine-grained token", re.compile(_PAT)),
    ("anthropic key", re.compile(_ANT)),
    ("aws access key id", re.compile(_AWS)),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    # A password inside a URL. The userinfo has to hold a colon and neither
    # half may contain `/`, `@` or whitespace, which is what keeps this off
    # ordinary `http://host:8080/path` lines: a port has no `@` after it.
    ("credential in a url", re.compile(r"://[^/\s@:]+:[^/\s@]+@[^/\s]+")),
)


def _run(runner, args, timeout=120):
    """(stdout, None) or (None, why)."""
    try:
        proc = runner(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"kubectl failed: {exc}"
    if proc.returncode != 0:
        return None, f"kubectl failed: {proc.stderr.strip() or proc.stdout.strip()}"
    return proc.stdout, None


def read_containers(runner=subprocess.run):
    """Every container of every pod, as (list, None) or (None, why).

    Each row carries `started`: whether any container of the pod has ever run.
    A pod still pulling its image has no log to refuse, so it is skipped by
    name rather than counted as unreadable — those are different findings.
    """
    out, why = _run(runner, ["kubectl", "get", "pods", "-A", "-o", "json"])
    if why:
        return None, why
    try:
        body = json.loads(out)
    except ValueError as exc:
        return None, f"kubectl returned something that is not JSON: {exc}"
    if not isinstance(body, dict):
        return None, "kubectl returned JSON that is not an object"

    rows = []
    for item in body.get("items") or []:
        meta = item.get("metadata") or {}
        spec = item.get("spec") or {}
        status = item.get("status") or {}
        phase = (status.get("phase") or "").strip()
        states = {}
        for group in ("containerStatuses", "initContainerStatuses"):
            for cs in status.get(group) or []:
                states[cs.get("name")] = cs
        for group in ("containers", "initContainers"):
            for container in spec.get(group) or []:
                name = container.get("name") or "?"
                cs = states.get(name) or {}
                # `started` is not the `started` field: a container that ran
                # and exited is False there and still has a log worth reading.
                # What matters is whether a container ever began, which is
                # exactly "it is not still Waiting with no restarts".
                #
                # Key presence, never truthiness: kubelet writes
                # `{"running": {"startedAt": ...}}` today, and a truthiness
                # test would read an empty `running` block as a container that
                # never started -- which is the silent skip this whole check
                # exists to prevent.
                state = cs.get("state") or {}
                ever_ran = ("running" in state or "terminated" in state
                            or (cs.get("restartCount") or 0) > 0)
                rows.append({
                    "namespace": meta.get("namespace") or "?",
                    "pod": meta.get("name") or "?",
                    "container": name,
                    "phase": phase,
                    "started": ever_ran,
                })
    return rows, None


def scan_text(text):
    """[(pattern name, line number)] for every credential-shaped match.

    Line numbers are 1-based and relative to the text handed in, which is a
    tail rather than the whole log — the caller says so when it prints them.
    """
    hits = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            if pattern.search(line):
                hits.append((name, lineno))
    return hits


def read_log(row, tail, runner=subprocess.run):
    """(text, None) or (None, why) for one container's tail."""
    return _run(runner, [
        "kubectl", "logs", "-n", row["namespace"], row["pod"],
        "-c", row["container"], f"--tail={tail}",
    ], timeout=60)


def report(results, tail, skipped):
    """(lines, status) from [(row, hits, why)] — never printing a match."""
    lines = []
    found = [(row, hits) for row, hits, why in results if hits]
    unreadable = [(row, why) for row, hits, why in results if why]

    for row, hits in sorted(found, key=lambda p: (p[0]["namespace"], p[0]["pod"])):
        where = f"{row['namespace']}/{row['pod']} [{row['container']}]"
        for name, lineno in hits:
            lines.append(f"CREDENTIAL IN A LOG  {where} line {lineno} of the "
                         f"tail looks like a {name}")
    if found:
        lines.append("The match itself is deliberately not printed. Read it with "
                     "`kubectl logs -n <ns> <pod> -c <container>`, fix whatever "
                     "prints it, then delete the pod or Job so the written log "
                     "goes with it — a log already on disk outlives the fix.")

    for row, why in sorted(unreadable, key=lambda p: (p[0]["namespace"], p[0]["pod"])):
        lines.append(f"COULD NOT READ  {row['namespace']}/{row['pod']} "
                     f"[{row['container']}] — {why}")

    for row in sorted(skipped, key=lambda r: (r["namespace"], r["pod"])):
        lines.append(f"not judged  {row['namespace']}/{row['pod']} "
                     f"[{row['container']}] has never started a container "
                     f"(phase {row['phase'] or '?'}), so it has no log yet")

    read = len(results) - len(unreadable)
    lines.append(f"Read the newest {tail} line(s) of {read} container log(s); "
                 f"{len(found)} carried a credential-shaped string, "
                 f"{len(unreadable)} could not be read, {len(skipped)} had no log yet. "
                 f"A secret printed further back than {tail} lines is outside this.")
    if found:
        return lines, 2
    return lines, (1 if unreadable else 0)


def main(argv=None, runner=subprocess.run):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tail", type=int, default=DEFAULT_TAIL,
                        help="lines of each container log to read")
    parser.add_argument("--workers", type=int, default=WORKERS,
                        help="how many kubectl calls to have in flight")
    args = parser.parse_args(argv)

    rows, why = read_containers(runner)
    if why:
        print(f"COULD NOT READ  {why}")
        return 1
    if not rows:
        # An empty list from a working kubectl is not a clean cluster, it is
        # no instrument: this cluster demonstrably runs pods.
        print("COULD NOT READ  kubectl returned no pods at all")
        return 1

    live = [r for r in rows if r["started"]]
    skipped = [r for r in rows if not r["started"]]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(read_log, row, args.tail, runner): row for row in live}
        for future in concurrent.futures.as_completed(futures):
            row = futures[future]
            try:
                text, log_why = future.result()
            except Exception as exc:  # noqa: BLE001 - one bad pod must not kill the sweep
                text, log_why = None, f"kubectl failed: {exc}"
            if log_why:
                results.append((row, [], log_why))
            else:
                results.append((row, scan_text(text or ""), None))

    lines, status = report(results, args.tail, skipped)
    for line in lines:
        print(line)
    return status


if __name__ == "__main__":
    sys.exit(main())
