"""Does `redact()` actually mask every credential Kubernetes mounted here?

Run it from the runner checkout, every cycle, before you pick:

    python3 -m tools.redact_coverage

**Exit 2 means a credential this pod holds would be published verbatim**,
1 means the pod spec could not be read (which never reads as clean), 0
means every secret-sourced value visible from here is masked and the
summary names the workload it swept.

`agora_runner/redact.py` is the last thing between a live credential and
an Agora conversation, and it works two ways. Some credentials have a
*format* -- `sk-ant-`, `ghp_`, a JWT -- and a pattern catches those
wherever they appear. The rest have none, so Cycle 560 added a pass that
looks the literal up in this process's own environment, **selected by
whether the variable's NAME sounds like a secret**: `TOKEN`, `SECRET`,
`PASSWORD`, `_PASS`, `API_KEY`, `ACCESS_KEY`, `CREDENTIAL`.

That name vocabulary is a guess, and it is a guess that decays. It was
measured against the live pods once, by hand, on 2026-08-28, and nothing
has re-measured it since -- so the day someone mounts `GROQ_KEY` or
`NAS_SUDO_PW` next to the others, the value pass goes blind to it and
**nothing anywhere says so**. That is the shape this loop keeps paying
for: a guard that reports itself working while guarding a set that
quietly stopped being the right set. Issue #117 is a live example waiting
to happen -- a Groq key is already minted and wants wiring into this
cluster.

**The fix is not a longer word list.** It is to stop guessing at all:
Kubernetes has already declared which values are secrets. A container's
`env[].valueFrom.secretKeyRef` names, exactly and without heuristics,
every variable whose value came out of a Secret object. So this reads
that declaration off the running pod, reads each of those values out of
its own environment, and asserts `redact()` removes it. No entropy score,
no vocabulary, no list in this file to maintain.

**Three verdicts, because "not masked" is two different things.** A
declared secret that `redact()` leaves whole and that is long enough to
mask safely is a finding and raises. A declared secret **shorter than
`redact._MIN_SECRET_LEN`** is named and does *not* raise: `COUCHDB_USER`
is five alphabetic characters on the runner pod today, and masking a
five-letter word would blank it out of every ordinary sentence this loop
publishes, which is the over-redaction the owner's keep-everything rule
forbids. Saying "declared secret, too short to mask" is the honest answer
and it is a different answer from "we forgot".

**What it cannot judge, it names rather than skips.** Two workloads run a
`redact()` -- the runner pod (`audit()`) and the bridge pod (its own
copy) -- and a process can only read its *own* environment, so a run here
measures one of them. The other's declared names are printed under
`CANNOT JUDGE` with the command to measure them there, which `preflight`
surfaces under a clean row rather than collapsing away. Same for
`envFrom.secretRef`, which names a Secret and not its keys: enumerating
those keys means reading the Secret object, and this loop's RBAC refuses
that at both the tool and the cluster level. An unenumerable Secret is a
caveat, never a clean sweep of it.

This module lives in `agora_runner/` rather than in `tools/` for that
reason alone: `tools/` is not in the runner image, so a check that could
only ever run on one of the two pods it is about would print `CANNOT
JUDGE` at the other one forever with no way to close it.
`tools/redact_coverage.py` is a two-line wrapper so `preflight`'s roster
can name it.
"""

import argparse
import json
import os
import subprocess
import sys

from agora_runner.redact import _MIN_SECRET_LEN, redact

#: The workloads whose output passes through a `redact()`. Everything else in
#: the namespace holds credentials too -- the newspaper generator holds the
#: Gemini key -- but nothing it prints reaches a conversation through this
#: filter, so judging it here would report a coverage gap that does not exist.
#: Matched against the pod's `app` label, falling back to the name prefix,
#: because a ReplicaSet hash is not a workload identity.
REDACTING_WORKLOADS = ("agora-persona-runner", "agora-claude-bridge")

NAMESPACE = "agents"


def _workload(pod):
    """The workload name for a pod, from its `app` label or its name prefix."""
    labels = (pod.get("metadata") or {}).get("labels") or {}
    app = labels.get("app") or labels.get("app.kubernetes.io/name")
    if app:
        return app
    name = (pod.get("metadata") or {}).get("name") or ""
    for known in REDACTING_WORKLOADS:
        if name.startswith(known):
            return known
    return name


def declared_secrets(pods):
    """`(by_workload, unenumerable)` read off the running pod specs.

    `by_workload` maps a workload in `REDACTING_WORKLOADS` to the sorted env
    names it sources from a Secret. `unenumerable` is one `(workload, secret)`
    pair per `envFrom.secretRef` -- that declaration names a Secret and not the
    variables it sets, and reading the Secret to find out is refused by RBAC.
    """
    by_workload = {}
    unenumerable = []
    for pod in pods.get("items") or []:
        workload = _workload(pod)
        if workload not in REDACTING_WORKLOADS:
            continue
        names = by_workload.setdefault(workload, set())
        for container in (pod.get("spec") or {}).get("containers") or []:
            for entry in container.get("env") or []:
                if ((entry.get("valueFrom") or {}).get("secretKeyRef")):
                    names.add(entry.get("name"))
            for source in container.get("envFrom") or []:
                ref = (source.get("secretRef") or {}).get("name")
                if ref and (workload, ref) not in unenumerable:
                    unenumerable.append((workload, ref))
    return {k: sorted(n for n in v if n) for k, v in by_workload.items()}, unenumerable


def judge(names, environ):
    """Sort declared secret names into masked / too short / unmasked / absent.

    Absent means the variable is declared on a pod whose environment this
    process does not have -- the other workload, or a container in this one
    that is not the one running. It is never a pass.
    """
    masked, too_short, unmasked, absent = [], [], [], []
    for name in names:
        value = environ.get(name)
        if not isinstance(value, str) or not value.strip():
            absent.append(name)
        elif redact(value, environ) != value:
            masked.append(name)
        elif len(value.strip()) < _MIN_SECRET_LEN:
            too_short.append((name, len(value.strip())))
        else:
            unmasked.append((name, len(value.strip())))
    return masked, too_short, unmasked, absent


def running_workload(environ=None, hostname=None):
    """Which of `REDACTING_WORKLOADS` this process is inside, or `None`.

    The pod's own hostname is its pod name, which starts with the workload
    name. `NOVA_WORKLOAD` overrides it so a test -- or a shell on a third
    pod -- can say so explicitly rather than being guessed at wrongly.
    """
    env = os.environ if environ is None else environ
    override = (env.get("NOVA_WORKLOAD") or "").strip()
    if override:
        return override if override in REDACTING_WORKLOADS else None
    host = hostname if hostname is not None else (env.get("HOSTNAME") or "")
    for known in REDACTING_WORKLOADS:
        if host.startswith(known):
            return known
    return None


def read_pods(run=subprocess.run, namespace=NAMESPACE):
    """The pod list as a dict, or `None` if kubectl could not answer."""
    try:
        done = run(["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
                   capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
        return None


def report(pods, environ=None, here=None, out=sys.stdout):
    env = os.environ if environ is None else environ
    if pods is None:
        print("CANNOT READ — kubectl could not list pods in " + NAMESPACE
              + "; the declaration of what is a secret is unreadable, "
                "which is no instrument rather than no gap.", file=out)
        return 1
    by_workload, unenumerable = declared_secrets(pods)
    if not by_workload:
        print("CANNOT READ — no pod in " + NAMESPACE + " matched "
              + ", ".join(REDACTING_WORKLOADS)
              + "; nothing was judged.", file=out)
        return 1
    here = running_workload(env) if here is None else here
    findings = 0
    for workload in sorted(by_workload):
        names = by_workload[workload]
        if workload != here:
            print(f"CANNOT JUDGE — {workload} declares {len(names)} secret-sourced "
                  f"variable(s) and only that pod can read their values: "
                  f"{', '.join(names)}. Run `python3 -m tools.redact_coverage` there.",
                  file=out)
            continue
        masked, too_short, unmasked, absent = judge(names, env)
        for name, length in unmasked:
            findings += 1
            print(f"NOT MASKED — {workload}: {name} comes out of a Secret, is "
                  f"{length} characters, and redact() returns it unaltered. "
                  f"Anything this process publishes carrying it publishes it whole.",
                  file=out)
        for name, length in too_short:
            print(f"NOT JUDGED — {workload}: {name} comes out of a Secret but is "
                  f"{length} characters, below redact()'s {_MIN_SECRET_LEN}-character "
                  f"floor; masking it would blank an ordinary word. Deliberately "
                  f"not a finding.", file=out)
        for name in absent:
            print(f"CANNOT JUDGE — {workload}: {name} is declared on this workload "
                  f"and is not in this process's environment, so its value was "
                  f"not read.", file=out)
        print(f"{workload}: {len(masked)} of {len(names)} declared secret(s) masked "
              f"({', '.join(masked) or 'none'}).", file=out)
    for workload, secret in unenumerable:
        print(f"CANNOT JUDGE — {workload} also takes envFrom secret/{secret}, which "
              f"names no keys; reading the Secret to enumerate them is refused by "
              f"RBAC, so those variables were not swept.", file=out)
    if findings:
        print(f"{findings} credential(s) this pod holds would be published verbatim.",
              file=out)
        return 2
    if here is None:
        print("CANNOT JUDGE — this process is in neither redacting workload, so "
              "no value was read anywhere.", file=out)
        return 1
    print(f"Every secret-sourced value readable from {here} is masked by redact(). "
          f"Swept {len(by_workload.get(here, []))} declared secret(s) here; "
          f"{sum(len(v) for k, v in by_workload.items() if k != here)} more are "
          f"declared on the other redacting workload and are not readable from here.",
          file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--namespace", default=NAMESPACE)
    args = parser.parse_args(argv)
    return report(read_pods(namespace=args.namespace))


if __name__ == "__main__":
    sys.exit(main())
