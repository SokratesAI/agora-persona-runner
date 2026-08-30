"""How old is the code the NAS media apps are actually running?

Cycle 642, on the owner's standing capture -- *"Work on the NAS and its
security instead -- that's the highest priority right now."*

The three NAS checks already here ask what those apps are *configured* to do:
`tools.nas_health` asks whether they answer at all, `tools.nas_watch` whether
anything is set to run a command on an event, `tools.nas_egress` whether
anything is set to hand downloads off his LAN. None of them asks the question
underneath all three -- **whether the code answering those requests is still
getting security fixes**.

That question matters here more than it would anywhere else in this estate,
and the reason is his own decision rather than a defect. Sonarr and Radarr keep
no login (`journal-digest.md`, `[nas-auth-is-off-and-he-has-accepted-it]`),
both bind `0.0.0.0`, and both serve their API key unauthenticated from
`/initialize.js`. With authentication deliberately off, the app's own patch
level is the remaining control on that surface.

**Nothing in this loop judged it before this file.** The string was visible --
`tools.nas_health` prints `sonarr 3.0.9.1549` every cycle through `nas.status`
-- and no cycle ever compared it against anything, which is the half that
turns a number into a finding. `tools.pin_drift` and
`tools.eol_watch` read version strings out of files in GitHub repos -- a `FROM`
line, an `ARG *_VERSION`, a `uses:` ref -- and `tools.running_images` reads the
live Kubernetes cluster. The NAS containers are in neither place: they are
hand-run `docker compose` services on a box outside the cluster, pinned to
`:latest` tags, so there is no manifest anywhere in this org that names them.
Cycles 630 and 636 read the two version numbers by hand, once, and two cycles
have quoted that reading since. **A measurement of a mutable value decays the
moment it is taken** -- the sentence `tools.nas_watch` was built on, applied
one layer down to the thing doing the answering.

Measured live from the bridge pod before this file existed, and it is worse
than the record said. Sonarr **3.0.9.1549**, built **2022-08-06**, against
upstream `v4.0.19.2979`. Radarr **4.3.2.6857**, built **2023-01-04**, against
upstream `v6.3.0.10514` -- the journal had Radarr as one major behind and it is
two. Both are `-ls` LinuxServer builds on `:latest` tags that have not been
pulled since 2025-08-13.

**What raises and what does not.** The verdict is a **major** gap against the
project's own newest release, and nothing narrower. A minor or patch gap is
printed and does not raise: these projects ship continuously, so a check that
fires on a point release fires every week and stops being read -- the same call
`tools.pin_drift` makes on a patch, moved one level up because there is no
pull request here to close it, only a decision of his to pull a new image. A
major gap is the finding that has been true for over a year and is the one an
unauthenticated LAN-facing app cannot afford.

**The build date is printed beside the gap and deliberately does not raise on
its own.** It is the stronger sentence -- "the running build is from August
2022" needs no upstream comparison and cannot be argued with -- but a raise on
age alone needs a threshold in days that I would be inventing, and a version
comparison already answers the question with the project's own opinion in it.
So the age is context, and the gap is the verdict.

**Three things it does not judge, said here rather than discovered later.**
nzbget is on that box and is not here: its version is behind its password
(`/jsonrpc/version` answers 401 unauthenticated, measured Cycle 640), so
reading it needs `NZBGET_USER`/`NZBGET_PASS` handed in, and a check that is
`NOT JUDGED` on every normal cycle is one nobody reads. Plex is on that box and
is not judged at all -- it is a Synology package rather than one of these two
APIs. And an upstream release this pod cannot read is never cleared: it prints
and exits 1, because an unjudged version must not look like a current one.

Exit status, the same three meanings as every check in `tools.preflight`:

* **2** -- an app on the NAS is a major version behind its own project.
* **1** -- something that should have been readable was not: a service never
  came back from key discovery, refused its key, answered something that is
  not a status object, carried no version, carried a version string that
  cannot be compared, or its upstream release could not be read.
* **0** -- no app swept is a major behind its own project, and the report
  names what it swept and how old each build is.

On a pod that cannot make the SSH hop this prints `CANNOT SEE FROM THIS POD`
and exits 0 without judging anything, the same call `tools.nas_health`,
`tools.nas_watch` and `tools.nas_egress` make for the same reason.
"""

import argparse
import datetime
import sys

from tools import nas
from tools import pin_drift

#: The status endpoint both apps serve, and the one `tools.nas.status` already
#: reads. Sonarr v3 and Radarr v4 both answer it under `/api/v3`, which is why
#: one path covers both -- the same reason `tools.nas_watch` needs only one.
STATUS_PATH = "/api/v3/system/status"

#: Which GitHub project's releases answer "what is current" for each service.
#: The running string is read off the app itself and never from a table here;
#: this maps a service to its upstream, which is the one fact about it that a
#: file can hold without going stale the way a pinned version does.
UPSTREAM = {"sonarr": "Sonarr/Sonarr", "radarr": "Radarr/Radarr"}


def build_age_days(build_time, now=None):
    """Whole days between a `buildTime` stamp and now, or None if unparseable.

    The stamps these apps return are UTC with a trailing `Z`, which
    `fromisoformat` refuses on Pythons before 3.11, so the suffix is
    normalised rather than relied on. A stamp this cannot parse yields None
    and the age is simply not printed -- the age is context, never a verdict,
    so failing to read it must not change any status.
    """
    raw = str(build_time or "").strip()
    if not raw:
        return None
    try:
        stamp = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.timezone.utc)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return (now - stamp).days


def _age_phrase(days):
    if days is None:
        return "build date unreadable"
    return f"built {days} day(s) ago"


def report(env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None,
           latest_release=pin_drift.latest_release, now=None):
    """Print the report and return the exit status."""
    hop = nas.ssh_config(env) if ssh is nas._UNSET else ssh
    if hop is None:
        print("CANNOT SEE FROM THIS POD -- no version was judged, and this does not raise "
              "the status.", file=out)
        print("  The hop needs an `ssh` binary on PATH and the sealed key at "
              f"{nas.SSH_DEFAULTS['key']}; this pod has one or neither.", file=out)
        print("Judged 0 service(s). Nothing here is a claim about the NAS.", file=out)
        return 0

    conf_all = nas.config(env, ssh=hop) if run is None else nas.config(env, ssh=hop, run=run)
    if not conf_all:
        print("SERVICES UNREADABLE -- the SSH hop exists but no *arr service could be "
              "configured through it.", file=out)
        print(nas.UNCONFIGURED_HELP, file=out)
        print(f"Judged 0 of {len(nas.SERVICES)} service(s). An unreadable service is not a "
              "clean sweep.", file=out)
        return 1

    status = 0
    judged, behind, current, unjudged = [], [], [], []
    # `nas.unconfigured` is the shared form of this: one transient failure
    # fetching sonarr's `/initialize.js` silently removes sonarr from the
    # sweep, and a denominator of `len(conf_all)` then reports "1 of 1" over
    # it. My reviewer found it here by making one service undiscoverable --
    # the four-year-old Sonarr this file exists to find disappeared and the
    # report exited 0 -- and Cycle 643 found the same shape still live in
    # `tools.nas_watch` and `tools.nas_egress`, which is why the reconciliation
    # moved into `tools.nas` rather than being written a third time.
    for service in nas.unconfigured(conf_all):
        unjudged.append((service, nas.UNDISCOVERED_REASON))
    for service in sorted(conf_all):
        try:
            info = get(service, conf_all[service], STATUS_PATH)
        except nas.Unreachable as exc:
            unjudged.append((service, f"{STATUS_PATH} is unreachable -- {exc}"))
            continue
        if not isinstance(info, dict):
            # A proxy or a login page can answer 200 with JSON that is not an
            # object. "no version field" and "this is not a status object" are
            # different failures and neither is a clean read.
            unjudged.append((service, f"{STATUS_PATH} answered {type(info).__name__}, "
                                      "not a status object"))
            continue
        running = str(info.get("version") or "").strip()
        if not running:
            unjudged.append((service, f"{STATUS_PATH} carried no `version` field"))
            continue
        repo = UPSTREAM.get(service)
        if repo is None:
            unjudged.append((service, "no upstream project is mapped for this service"))
            continue
        latest, why_not = latest_release(repo)
        if not latest:
            unjudged.append((service, f"upstream {repo} could not be read -- {why_not}"))
            continue
        age = _age_phrase(build_age_days(info.get("buildTime"), now=now))
        line = f"{service} {running} ({age}) against {repo} {latest}"
        verdict = pin_drift.gap(running, latest)
        if verdict is None:
            # A version string with no leading number -- `nightly`, `develop`,
            # a docker tag someone typed by hand. `gap` answers None, which is
            # not "major", so testing for equality alone would file it under
            # "not a major behind" and clear it. An unjudged version must
            # never read as a judged one; that is the contract in the
            # docstring and I broke it in my own first draft.
            unjudged.append((service, f"version {running!r} cannot be compared against "
                                      f"{repo} {latest}"))
            continue
        judged.append(service)
        if verdict == "major":
            behind.append(line)
        else:
            current.append(line)

    if behind:
        print("MAJOR VERSION BEHIND -- an app on the NAS is running a major line its own "
              "project has moved off, so it is no longer getting security fixes.", file=out)
        for line in behind:
            print(f"  {line}", file=out)
        print("  These apps have no login by the owner's own decision and bind 0.0.0.0, so "
              "the patch level is the remaining control on that surface. The fix is his: "
              "pull a newer image and restart the container. A major upgrade migrates the "
              "database, so it is not one to do unattended.", file=out)
        status = 2

    if unjudged:
        print("CANNOT JUDGE -- neither cleared nor raised:", file=out)
        for service, why in unjudged:
            print(f"  {service}: {why}", file=out)
        status = max(status, 1)

    if current:
        # Deliberately not "on its current major": a minor gap and a build
        # ahead of the newest release both land here, and calling either of
        # them current would be a wider claim than the check took.
        print("NOT A MAJOR BEHIND -- printed, not raised:", file=out)
        for line in current:
            print(f"  {line}", file=out)

    if not behind and not unjudged:
        print(f"NO APP IS A MAJOR BEHIND ITS OWN PROJECT on {len(judged)} service(s): "
              f"{', '.join(judged) or 'none'}.", file=out)

    print(f"Judged the running version of {len(judged)} service(s) of {len(nas.SERVICES)}, read "
          "over the SSH hop. nzbget is not judged -- its version is behind its password -- "
          "and Plex is not judged at all. A minor or patch gap is printed and does not "
          "raise.", file=out)
    return status


def main(argv=None, env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None,
         latest_release=pin_drift.latest_release, now=None):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_versions",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, get=get, ssh=ssh, run=run,
                  latest_release=latest_release, now=now)


if __name__ == "__main__":
    sys.exit(main())
