"""Is the owner's home NAS up, and are Sonarr and Radarr answering on it?

Cycle 638, on my own capture -- *"The NAS is the one thing the owner calls
highest priority and the only part of this system nothing checks
automatically"*. `tools.nas` can already ask the NAS real questions, and it
works: Cycle 631 measured `nas status` returning live versions. What did not
exist is anything that runs it **unprompted**. Every other corner of this
estate has a check in `tools.preflight`; the NAS had none, so the only way a
cycle learned the media stack was down was by going to look.

Two halves, and the split is the whole design because the two pods this loop
owns can each see only one of them:

* **Reachability.** A TCP connect to the NAS's SSH port and the banner it
  answers with. Needs nothing but a route, so it runs on the bridge pod --
  where `tools.preflight` actually runs -- and it is the half that answers
  "is the box up". Measured Cycle 638 from the bridge pod: 0.059s to a
  `SSH-2.0-OpenSSH_8.2` banner. **Time it and print the time**, because a
  refusal that returns in ~0.0002s came from this pod's own kernel (the
  `allow-nas-ssh-egress` NetworkPolicy) and a refusal with real latency came
  from the NAS. Those are opposite findings and the exception text is
  identical.
* **The applications.** Sonarr's and Radarr's own `/api/v3/system/status`,
  reached the way `tools.nas` reaches them: an SSH hop to the NAS and a
  `curl` made *on* the NAS, because port 8989 is not open from here. Needs an
  `ssh` binary and the sealed key, which today exist on the runner pod and
  not on the bridge pod.

**On a pod that cannot make the SSH hop this prints `CANNOT SEE FROM THIS
POD` and does not raise the exit status.** That is a deliberate call and it
matches two siblings rather than inventing a rule: `security_alerts` does not
raise on an alert already fixed, and `agentic_health` does not raise on a run
GitHub refused to start, both because a finding no pull request can close
makes every cycle re-derive it. The missing `ssh` binary is that shape --
it is closed by an image rebuild, not by anything a check can prompt. The
reachability half is a *complete* measurement of a real question, so exiting
0 on it is honest; the summary line says which half was judged so "checked
and clean" can never be confused with "never looked".

Exit status, the same three meanings as every check in `tools.preflight`:

* **2** -- the NAS did not answer, or a service on it is down or refused its
  key. Something to act on.
* **1** -- something that should have been readable was not. A hop that
  exists but fails is this, not a clean sweep.
* **0** -- nothing to act on, and the report names what was swept.
"""

import argparse
import socket
import sys
import time

from tools import nas

#: The banner an SSH server sends before anything else. Reading it is what
#: separates "something is listening on 22" from "sshd is up": a half-open
#: forward or a hung box accepts a connection and then says nothing.
BANNER_PREFIX = b"SSH-"

#: A connect that fails faster than this came from the local kernel rather
#: than from the network. Measured on the bridge pod: an allowed host answers
#: in 0.059s and a NetworkPolicy denial returns in 0.000188s, which is two
#: orders of magnitude apart, so the boundary does not need to be precise.
LOCAL_DENY_SECONDS = 0.005


def probe(host, port=22, timeout=10.0, connect=socket.create_connection):
    """Connect to `host:port` and read the greeting.

    Returns `(ok, detail, seconds)`. `ok` is False for any failure; `detail`
    always names what happened, including how long it took, because the
    duration is what distinguishes a local denial from a dead NAS.
    """
    started = time.monotonic()
    try:
        sock = connect((host, port), timeout)
    except OSError as exc:
        seconds = time.monotonic() - started
        where = ("refused by this pod's own kernel in %.6fs -- no packet left, so this is a "
                 "NetworkPolicy on our side and not the NAS" % seconds
                 if seconds < LOCAL_DENY_SECONDS
                 else "no answer after %.3fs" % seconds)
        return False, f"{host}:{port} {where}: {exc}", seconds
    try:
        greeting = sock.recv(255)
    except OSError as exc:
        return False, f"{host}:{port} accepted the connection and then failed: {exc}", time.monotonic() - started
    finally:
        try:
            sock.close()
        except OSError:
            pass
    seconds = time.monotonic() - started
    if not greeting.startswith(BANNER_PREFIX):
        return False, (f"{host}:{port} answered in {seconds:.3f}s but did not greet as SSH: "
                       f"{greeting[:40]!r} -- something else is on that port"), seconds
    return True, f"{host}:{port} answered {greeting.strip().decode('ascii', 'replace')} in {seconds:.3f}s", seconds


def report(env=None, out=sys.stdout, connect=socket.create_connection, get=nas._get, ssh=nas._UNSET,
           run=None):
    """Print the report and return the exit status."""
    hop = nas.ssh_config(env) if ssh is nas._UNSET else ssh
    # The host is knowable without a hop: `ssh_config` returns None when this
    # pod cannot make one, and the address is still the address.
    host = (hop or {}).get("host") or (env or {}).get("NAS_SSH_HOST") or nas.SSH_DEFAULTS["host"]

    status = 0
    reachable, detail, _ = probe(host, connect=connect)
    if reachable:
        print(f"REACHABLE  {detail}", file=out)
    else:
        print("NAS UNREACHABLE -- the box did not answer on its SSH port.", file=out)
        print(f"  {detail}", file=out)
        status = 2

    services_judged = 0
    if hop is None:
        print(file=out)
        print("CANNOT SEE FROM THIS POD -- Sonarr and Radarr were not judged, and this does "
              "not raise the status.", file=out)
        print("  The hop needs an `ssh` binary on PATH and the sealed key at "
              f"{nas.SSH_DEFAULTS['key']}; this pod has one or neither.", file=out)
        print("  The runner pod has both, so `python3 -m tools.nas status` there answers this "
              "half by hand today.", file=out)
        print("  Closing it means an ssh client in the bridge image and the sealed key mounted "
              "on the bridge pod -- an image rebuild and a manifest change, not something a "
              "check can prompt.", file=out)
    elif reachable:
        conf_all = nas.config(env, ssh=hop) if run is None else nas.config(env, ssh=hop, run=run)
        if not conf_all:
            print(file=out)
            print("SERVICES UNREADABLE -- the SSH hop exists but no service could be configured "
                  "through it.", file=out)
            print(nas.UNCONFIGURED_HELP, file=out)
            status = max(status, 1)
        else:
            lines, ok = nas.status(conf_all, get=get)
            services_judged = len(lines)
            print(file=out)
            if ok:
                print("SERVICES OK", file=out)
            else:
                print("SERVICE DOWN -- a service on the NAS is unreachable or refused its key.", file=out)
                status = 2
            for line in lines:
                print(f"  {line}", file=out)
    else:
        print(file=out)
        print("Sonarr and Radarr were not judged: the SSH hop exists but the NAS itself did not "
              "answer, so there is nothing to hop through.", file=out)

    print(file=out)
    print(f"Judged 1 host ({host}) and {services_judged} service(s) on it, from this pod. "
          "Reachability is a TCP connect plus the SSH banner; the services are read over the "
          "hop, not over a direct HTTP call, because port 8989 is not open from here.", file=out)
    return status


def main(argv=None, env=None, out=sys.stdout, connect=socket.create_connection, get=nas._get,
         ssh=nas._UNSET, run=None):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_health",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, connect=connect, get=get, ssh=ssh, run=run)


if __name__ == "__main__":
    sys.exit(main())
