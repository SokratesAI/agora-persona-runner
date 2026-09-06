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

**A subset sweep needs its complement, and this is it.** Everything above
judges the set Kubernetes *declares* -- which is the right set to judge and
is not the set this process *holds*. A credential arriving through
`envFrom`, through the base image, or through the harness that started the
process is in `os.environ` and in no declaration, so the paragraphs above
would report a clean sweep and never mention it. So the run also subtracts:
every declared `env[].name`, and every variable the kubelet injects for a
Service -- **generated from the live Service list, not from a list in this
file** -- and names what is left as `NOT SWEPT`. It is context rather than a
finding, because `PATH` is unmasked and should be; what makes it worth
printing is that it is the honest size of the hole beside the RBAC caveat,
and that `redact()` masking one of them would mean a credential arrived by a
route nothing here declares. Measured on the bridge pod 2026-08-31: 28
names, all base-image, shell or Claude Code harness variables, none of them
a credential -- so the unreadable `envFrom secretRef` on that pod delivered
nothing, which is the first evidence about it that is not a shrug.

**A Secret reaches a container two ways and this judged one of them.**
Everything above reads `os.environ`, so a Secret mounted as a *volume* was
invisible to all of it -- not badly judged, unjudged, because a file is in
no process's environment. Both redacting pods mount `nas-ssh-key` at
`/etc/nas-ssh` and nothing had ever asserted that `redact()` covers it.
`declared_secret_mounts` reads the other half of the same pod spec --
`volumes[].secret` joined to the container's `volumeMounts` -- and every
file under each mount gets the same four verdicts the environment half
gets, floor included. Measured 2026-09-06 on the bridge pod: one mount, two
files, the private key masked by the PEM pattern and the public key named
rather than raised on. **The public-key exception is a format rule, not a
filename convention**: an OpenSSH public key's first token says it is the
half meant to be handed out, which is the same kind of declaration every
pattern in `redact.py` keys off. Anything else unmasked in a Secret raises
and a person decides. Projected volumes are left out on purpose -- the only
one here is the kubelet's own ServiceAccount token, which no manifest asked
for and which the JWT pattern already catches.

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
import re
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


def declared_secret_mounts(pods):
    """`{workload: [(secret, mount_path), ...]}` for every Secret mounted as files.

    A Secret reaches a container two ways and the module above judges one of
    them. `env[].valueFrom.secretKeyRef` puts it in the environment;
    `volumes[].secret` puts it on the filesystem, and a file is in no
    process's environment, so every pass above is blind to it by
    construction. This is the same declaration, read off the other half of
    the same pod spec -- still no heuristic, still no list in this file.

    Projected volumes are deliberately left out. The one on both pods is the
    kubelet's own ServiceAccount token, which no manifest here asked for and
    which redact() already catches by its JWT shape; including it would make
    every run report a mount that nobody in this estate chose.
    """
    by_workload = {}
    for pod in pods.get("items") or []:
        workload = _workload(pod)
        if workload not in REDACTING_WORKLOADS:
            continue
        spec = pod.get("spec") or {}
        secret_volumes = {}
        for volume in spec.get("volumes") or []:
            name = (volume.get("secret") or {}).get("secretName")
            if volume.get("name") and name:
                secret_volumes[volume["name"]] = name
        found = by_workload.setdefault(workload, [])
        for container in spec.get("containers") or []:
            for mount in container.get("volumeMounts") or []:
                secret = secret_volumes.get(mount.get("name"))
                path = mount.get("mountPath")
                if secret and path and (secret, path) not in found:
                    found.append((secret, path))
    return {k: sorted(v) for k, v in by_workload.items()}


#: An OpenSSH public key says in its own first token that it is the half
#: meant to be handed out. That is a declaration in the material itself, the
#: same kind of thing every pattern in `redact.py` keys off, and it is the
#: only reason this file needs any notion of "a secret mount can hold
#: something public": `nas-ssh-key` carries `id_ed25519` and `id_ed25519.pub`
#: side by side, and raising forever on the public half would train the eye
#: to ignore the row. Anything else unmasked in a Secret raises and a person
#: decides -- which is the right behaviour for an alarm, and is why this is
#: one format rule rather than a filename convention or an exception list.
_SSH_PUBLIC_KEY = re.compile(r"^(?:ssh-[a-z0-9-]+|ecdsa-[a-z0-9-]+)\s+[A-Za-z0-9+/=]{20,}")


def judge_mounts(mounts, read=None, environ=None):
    """Sort every file under each declared Secret mount into four verdicts.

    Returns `(masked, too_short, unmasked, public, unreadable)`. `masked` and
    `unmasked` mirror `judge()` exactly, including the short-value floor: a
    two-byte file in a Secret is real and masking it would blank an ordinary
    word out of everything this loop publishes.

    `read` is `(path) -> {relative name: text}` and defaults to the real
    filesystem, so a test can hand it a mount without writing a key to disk.
    A mount path that does not exist here is not a pass -- that is the other
    pod's filesystem, and it comes back as unreadable with a reason.

    `environ` goes to `redact()` for the same reason `judge()` passes it: the
    value pass looks its literals up in an environment, and a Secret can be
    mounted as a file *and* set as a variable. Both halves must judge against
    the same environment or the report contradicts itself.
    """
    read = _read_mount if read is None else read
    masked, too_short, unmasked, public, unreadable = [], [], [], [], []
    for secret, path in mounts:
        try:
            files = read(path)
        except OSError as exc:
            unreadable.append((secret, path, type(exc).__name__))
            continue
        if files is None:
            unreadable.append((secret, path, "not mounted here"))
            continue
        for name in sorted(files):
            body = files[name]
            if body is None:
                unreadable.append((secret, path + "/" + name, "unreadable"))
            elif _SSH_PUBLIC_KEY.match(body.strip()):
                public.append((secret, name))
            elif redact(body, environ) != body:
                masked.append((secret, name))
            elif len(body.strip()) < _MIN_SECRET_LEN:
                too_short.append((secret, name, len(body.strip())))
            else:
                unmasked.append((secret, name, len(body.strip())))
    return masked, too_short, unmasked, public, unreadable


def _read_mount(path):
    """`{name: text}` for the regular files directly under `path`, or `None`.

    `None` means the path is not on this filesystem, which is the ordinary
    case for the workload this process is not inside. A file whose bytes are
    not text reads as `None` in the mapping rather than being skipped, so an
    unjudgeable file is named instead of counted clean.
    """
    if not os.path.isdir(path):
        return None
    out = {}
    for name in os.listdir(path):
        full = os.path.join(path, name)
        if not os.path.isfile(full):
            continue
        try:
            out[name] = open(full, "r", encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            out[name] = None
    return out


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


def service_link_names(services):
    """Every env name the kubelet injects for a list of Services.

    Kubernetes sets a fixed family of variables per Service in the pod's own
    namespace, plus the `kubernetes` Service in `default`. Deriving them from
    the live Service list is what keeps the complement pass below from being a
    word list: these names are generated, not guessed, and a Service added
    tomorrow is covered without anyone editing this file.

    A name generated here that the pod does not actually carry is harmless --
    the set is only ever subtracted.
    """
    names = set()
    items = list((services or {}).get("items") or [])
    items.append({"metadata": {"name": "kubernetes"},
                  "spec": {"ports": [{"name": "https", "port": 443,
                                      "protocol": "TCP"}]}})
    for service in items:
        raw = (service.get("metadata") or {}).get("name") or ""
        if not raw:
            continue
        prefix = raw.upper().replace("-", "_")
        ports = (service.get("spec") or {}).get("ports") or []
        names.update({f"{prefix}_SERVICE_HOST", f"{prefix}_SERVICE_PORT",
                      f"{prefix}_PORT"})
        for port in ports:
            named = port.get("name")
            if named:
                names.add(f"{prefix}_SERVICE_PORT_{named.upper().replace('-', '_')}")
            proto = (port.get("protocol") or "TCP").upper()
            stem = f"{prefix}_PORT_{port.get('port')}_{proto}"
            names.update({stem, f"{stem}_ADDR", f"{stem}_PORT", f"{stem}_PROTO"})
    return names


def unaccounted(environ, declared, links):
    """Env names this process holds that no readable declaration explains.

    `declared` is every `env[].name` on the pod spec -- including the plain
    ones, not only the secret-sourced -- and `links` is `service_link_names`.
    What is left arrived through `envFrom`, through the image, or through the
    harness that started the process, and **nothing readable from here says
    which**. That is the honest complement of the sweep above.
    """
    return sorted(n for n in environ if n not in declared and n not in links)


def declared_env_names(pods, workload):
    """Every `env[].name` a workload declares, secret-sourced or not."""
    names = set()
    for pod in (pods or {}).get("items") or []:
        if _workload(pod) != workload:
            continue
        for container in (pod.get("spec") or {}).get("containers") or []:
            for entry in container.get("env") or []:
                if entry.get("name"):
                    names.add(entry["name"])
    return names


def read_services(run=subprocess.run, namespace=NAMESPACE):
    """The Service list as a dict, or `None` if kubectl could not answer."""
    try:
        done = run(["kubectl", "get", "svc", "-n", namespace, "-o", "json"],
                   capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout)
    except ValueError:
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


def report(pods, environ=None, here=None, out=sys.stdout,
           services=None, read_mount=None):
    env = os.environ if environ is None else environ
    if pods is None:
        print("CANNOT READ — kubectl could not list pods in " + NAMESPACE
              + "; the declaration of what is a secret is unreadable, "
                "which is no instrument rather than no gap.", file=out)
        return 1
    by_workload, unenumerable = declared_secrets(pods)
    mounts_by_workload = declared_secret_mounts(pods)
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
            elsewhere = mounts_by_workload.get(workload) or []
            print(f"CANNOT JUDGE — {workload} declares {len(names)} secret-sourced "
                  f"variable(s) and mounts {len(elsewhere)} Secret(s) as files, and "
                  f"only that pod can read either: "
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
        mounts = mounts_by_workload.get(workload) or []
        m_masked, m_short, m_unmasked, m_public, m_unreadable = judge_mounts(
            mounts, read=read_mount, environ=env)
        for secret, name, length in m_unmasked:
            findings += 1
            print(f"NOT MASKED — {workload}: secret/{secret} mounts {name} as a "
                  f"file, it is {length} characters, and redact() returns it "
                  f"unaltered. A file is in no process's environment, so the "
                  f"value pass above never saw it.", file=out)
        for secret, name, length in m_short:
            print(f"NOT JUDGED — {workload}: secret/{secret}'s {name} is {length} "
                  f"characters, below redact()'s {_MIN_SECRET_LEN}-character floor. "
                  f"Deliberately not a finding.", file=out)
        for secret, name in m_public:
            print(f"NOT A SECRET — {workload}: secret/{secret}'s {name} is an "
                  f"OpenSSH public key, which says in its own first token that it "
                  f"is the half meant to be handed out.", file=out)
        for secret, where, why in m_unreadable:
            print(f"CANNOT JUDGE — {workload}: secret/{secret} at {where} was not "
                  f"read ({why}), so nothing under it was swept.", file=out)
        if mounts:
            print(f"{workload}: {len(m_masked)} of "
                  f"{len(m_masked) + len(m_short) + len(m_unmasked)} file(s) in "
                  f"{len(mounts)} mounted Secret(s) masked.", file=out)
        if services is None:
            print(f"CANNOT JUDGE — {workload}: the Service list is unreadable, so "
                  f"the variables this process holds that the pod spec does not "
                  f"declare could not be separated from Kubernetes' own service "
                  f"links and were not named.", file=out)
        else:
            rest = unaccounted(env, declared_env_names(pods, workload),
                               service_link_names(services))
            rest_masked = [n for n in rest
                           if isinstance(env.get(n), str) and env[n].strip()
                           and redact(env[n], env) != env[n]]
            tail = (f" redact() masks {len(rest_masked)} of them "
                    f"({', '.join(rest_masked)}), which means a credential reached "
                    f"this process through a route nothing here declares."
                    if rest_masked else "")
            print(f"NOT SWEPT — {workload}: {len(rest)} variable(s) in this "
                  f"process's environment are declared nowhere in the pod spec and "
                  f"are not Kubernetes service links, so no declaration says whether "
                  f"any of them is a credential: {', '.join(rest) or 'none'}."
                  + tail, file=out)
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
    return report(read_pods(namespace=args.namespace),
                  services=read_services(namespace=args.namespace))


if __name__ == "__main__":
    sys.exit(main())
