"""Is the owner's home NAS up, and are Sonarr and Radarr answering on it?

Cycle 637, on my own capture -- *"The NAS is the one thing the owner calls
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
  "is the box up". Measured Cycle 637 from the bridge pod: 0.059s to a
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
* **The other three services on that box.** nzbget, Plex and Bazarr also run there and
  were judged by nothing here until Cycle 648: this check printed *"Judged 1
  host and 2 service(s) on it"* with no denominator, so a dead nzbget and a
  dead Plex both read as a clean NAS. Neither needs a credential to answer
  that it is alive -- nzbget's `/jsonrpc/version` returns 401 when it is up
  and locked, and Plex's `/identity` returns 200 unauthenticated -- so the
  one thing this could never see is exactly the thing it can. **What it
  reads is that they answered, and nothing else:** `tools.nas_watch` owns
  the verdict on nzbget's lock and `tools.nas_versions` owns Plex's version,
  and a second copy of either opinion here would be two places to fix one
  answer.

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


#: The services on that box that answer with no credential at all. This is the
#: probe list *and* what the summary line names, deliberately in one place --
#: a second copy of it here would be the duplication this whole check is about.
#: The return value of each call is thrown away on purpose: see the docstring.
CREDENTIAL_FREE = ("nzbget", "plex", "bazarr")


def liveness(hop, nzbget, plex, bazarr):
    """Probe nzbget, Plex and Bazarr over the hop. Returns `(lines, judged, status)`.

    `judged` means a verdict was reached, up or down -- the same meaning
    `nas.status` gives it for the two *arr apps, so the summary line's
    denominator answers "did I look at all five" and the `SERVICE DOWN`
    heading answers "were they up". A service that raises `Unreachable` is
    down and is the same 2 as a dead Sonarr, because it is the same finding.
    """
    calls = {"nzbget": nzbget, "plex": plex, "bazarr": bazarr}
    lines, judged, status = [], 0, 0
    for name in CREDENTIAL_FREE:
        judged += 1
        try:
            calls[name](hop)
        except nas.Unreachable as exc:
            lines.append(f"{name} did not answer over the hop: {exc}")
            status = 2
        else:
            lines.append(f"{name} answered over the hop")
    return lines, judged, status


def report(env=None, out=sys.stdout, connect=socket.create_connection, get=nas._get, ssh=nas._UNSET,
           run=None, nzbget=nas.nzbget_unlocked, plex=nas.plex_version,
           bazarr=nas.bazarr_key):
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
        print("CANNOT SEE FROM THIS POD -- none of the "
              f"{len(nas.MEDIA_SERVICES)} service(s) on the box were judged, and this does "
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
        # Outside the `conf_all` branch on purpose: neither of these needs an
        # API key, so a key discovery that failed must not take them with it.
        alive_lines, alive_judged, alive_status = liveness(hop, nzbget, plex, bazarr)
        services_judged += alive_judged
        status = max(status, alive_status)
        print(file=out)
        if alive_status:
            print("SERVICE DOWN -- a service on the NAS did not answer at all.", file=out)
        for line in alive_lines:
            print(f"  {line}", file=out)
    else:
        print(file=out)
        print(f"None of the {len(nas.MEDIA_SERVICES)} service(s) on the box were judged: the "
              "SSH hop exists but the NAS itself did not answer, so there is nothing to hop "
              "through.", file=out)

    print(file=out)
    print(f"Judged 1 host ({host}) and {services_judged} service(s) of "
          f"{len(nas.MEDIA_SERVICES)} on it, from this pod. "
          "Reachability is a TCP connect plus the SSH banner; the services are read over the "
          "hop, not over a direct HTTP call, because port 8989 is not open from here. "
          f"{', '.join(CREDENTIAL_FREE)} are judged alive only -- "
          "tools.nas_watch owns nzbget's lock and tools.nas_versions owns the plex and "
          "bazarr versions.",
          file=out)
    return status


def main(argv=None, env=None, out=sys.stdout, connect=socket.create_connection, get=nas._get,
         ssh=nas._UNSET, run=None, nzbget=nas.nzbget_unlocked, plex=nas.plex_version,
         bazarr=nas.bazarr_key):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_health",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, connect=connect, get=get, ssh=ssh, run=run,
                  nzbget=nzbget, plex=plex, bazarr=bazarr)


if __name__ == "__main__":
    sys.exit(main())
