"""Is any *arr app on the NAS pointed at a download client off the home LAN?

Cycle 641, on the owner's standing capture -- *"Work on the NAS and its
security instead -- that's the highest priority right now."*

`tools.nas_watch` asks whether anything on that box is configured to **run a
command**. This asks the neighbouring question it deliberately left alone:
whether anything is configured to **hand its work, and the credentials that
go with it, to a machine that is not on his network**.

The reason it is a separate question rather than another row in that check is
the consequence, which is not code execution and is not nothing. Sonarr and
Radarr keep no login by his own decision (`journal-digest.md`,
`[nas-auth-is-off-and-he-has-accepted-it]`), both bind `0.0.0.0`, and both
serve their API key unauthenticated from `/initialize.js` -- so any device on
his LAN can write their configuration. A download client row carries the host,
the port **and the username and password** the app authenticates to it with.
Repoint `host` at a machine on the internet and that machine receives his
nzbget credential on the next request, plus every download the apps ask for.
It is one form submission and nothing anywhere would have said so.

Cycle 636 read this list by hand, once, and found one `Nzbget` row on each
app. **A measurement of a mutable list decays the moment it is taken** -- that
is the sentence Cycle 639 wrote about the notification list one door over,
and it is just as true here. This asks every cycle instead, from
`tools.preflight`, over the read-only SSH hop `tools.nas` already owns.

Measured live from the bridge pod before this file existed: one row on each
app, `Nzbget` at `192.168.0.119:6789`, `useSsl` false -- a private address on
his own LAN, so today's answer is a real clean sweep rather than one that was
guaranteed in advance. A row pointing anywhere else would have shown.

**What raises and what does not.** The verdict is *where the destination is*,
not whether the list changed. A host that resolves to loopback, an RFC1918 or
link-local address, or a bare name with no public DNS meaning, is on his own
network and prints without raising -- him adding a second download client is
an ordinary thing to do, and a check that goes red on a legitimate action is a
check that stops being read. A host that is a **globally routable IP address**
raises, enabled or not: the anomaly is the row existing, and turning it on is
one more unauthenticated POST. A host that is a **dotted name this pod cannot
resolve** is neither -- it prints `CANNOT JUDGE` and exits 1, because an
unjudged destination must never read as a judged one.

**Three things it does not see, said here rather than discovered later.** It
reads current state, so a row added and removed between two cycles leaves no
trace. It judges the destination's *location*, never its trustworthiness: a
compromised machine on his LAN passes this check, and should. And it judges
Sonarr and Radarr only -- nzbget is the download client rather than a caller
of one, and Plex is on that box and is not judged at all.

Deliberately out of scope, with the measurement rather than a guess: indexers
and root folders. Both are the same shape of mutable list, and both were read
live this cycle (`nzbgeek` over HTTPS on each app; `/tv` and `/movies`). An
indexer is *meant* to be a host on the internet, so the rule above says
nothing there, and a root-folder rule needs a table of which paths are
allowed -- a second copy of a truth that goes stale exactly the way the thing
it watches does. Neither earns a raise it can defend, so neither is here.

Exit status, the same three meanings as every check in `tools.preflight`:

* **2** -- a download client on the NAS points at a globally routable address.
* **1** -- something that should have been readable was not: a service was
  unconfigured, refused its key, answered something that is not a list, or
  named a destination this pod cannot classify.
* **0** -- every destination swept is on his own network, and the report names
  what it swept.

On a pod that cannot make the SSH hop this prints `CANNOT SEE FROM THIS POD`
and exits 0 without judging anything, the same call `tools.nas_health` and
`tools.nas_watch` make for the same reason.
"""

import argparse
import ipaddress
import sys

from tools import nas

#: The endpoint each app lists its configured download clients on. Sonarr v3
#: and Radarr v4 both serve it under `/api/v3`, which is why one path covers
#: both -- the same reason `tools.nas_watch` needs only one notification path.
DOWNLOAD_CLIENT_PATH = "/api/v3/downloadclient"

LOCAL = "local"
OFF_LAN = "off-lan"
UNJUDGED = "unjudged"


def classify(host):
    """Where does this download-client host live? One of the three above.

    An IP literal is judged by whether it is **globally routable**, which is
    `is_global` and deliberately not `not is_private`. The two differ exactly
    where this check lives: on this Python, `100.64.0.0/10` -- the range
    Tailscale hands out, including the NAS's own `100.89.37.25` -- is neither
    private nor global, so `is_private` would have raised on his own tailnet
    (my own test caught that before this shipped). `is_global` also clears
    loopback, link-local and the documentation ranges, none of which can
    receive anything from his box.

    A name is judged by whether it could mean anything off his LAN.
    `localhost`, an mDNS `.local` name and a dotless container or host name
    resolve only inside his own network, so they are local. Anything else is
    a real DNS name this pod has no resolver for and no business guessing at.
    A value with no letter in it at all is neither an address nor a hostname,
    so it is unjudged rather than read as a dotless LAN name.
    """
    # `str()` rather than a bare `.strip()`: this reads a home box's JSON and a
    # numeric or null `host` must classify, not raise.
    host = str(host or "").strip()
    if not host:
        return UNJUDGED
    try:
        return OFF_LAN if ipaddress.ip_address(host).is_global else LOCAL
    except ValueError:
        pass
    lowered = host.lower().rstrip(".")
    if not any(char.isalpha() for char in lowered):
        # Not an address and not a hostname either -- a bare `6789` is
        # malformed config, and reading it as a dotless LAN name would clear
        # it. Neither cleared nor raised.
        return UNJUDGED
    if lowered == "localhost" or lowered.endswith(".local") or "." not in lowered:
        return LOCAL
    return UNJUDGED


def _field(row, name):
    for field in row.get("fields") or []:
        if (field.get("name") or "").strip().lower() == name:
            return field.get("value")
    return None


def _row_label(row):
    """`name | implementation -> host:port` for a download client row.

    Every field is read defensively: this parses a home box's JSON, and a
    missing key must not turn a finding into a traceback.
    """
    name = (row.get("name") or "").strip() or "(unnamed)"
    impl = (row.get("implementation") or "").strip() or "(no implementation)"
    host = str(_field(row, "host") or "").strip() or "(no host)"
    port = _field(row, "port")
    where = host if port is None else f"{host}:{port}"
    state = "enabled" if row.get("enable") else "disabled"
    return f"{name} | {impl} -> {where} ({state})"


def report(env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None):
    """Print the report and return the exit status."""
    hop = nas.ssh_config(env) if ssh is nas._UNSET else ssh
    if hop is None:
        print("CANNOT SEE FROM THIS POD -- no download client was judged, and this does not "
              "raise the status.", file=out)
        print("  The hop needs an `ssh` binary on PATH and the sealed key at "
              f"{nas.SSH_DEFAULTS['key']}; this pod has one or neither.", file=out)
        print("Judged 0 service(s). Nothing here is a claim about the NAS.", file=out)
        return 0

    conf_all = nas.config(env, ssh=hop) if run is None else nas.config(env, ssh=hop, run=run)
    if not conf_all:
        print("SERVICES UNREADABLE -- the SSH hop exists but no *arr service could be configured "
              "through it.", file=out)
        print(nas.UNCONFIGURED_HELP, file=out)
        print("Judged 0 of 2 service(s). An unreadable service is not a clean sweep.", file=out)
        return 1

    status = 0
    judged = []
    buckets = {LOCAL: [], OFF_LAN: [], UNJUDGED: []}
    for service in sorted(conf_all):
        try:
            rows = get(service, conf_all[service], DOWNLOAD_CLIENT_PATH)
        except nas.Unreachable as exc:
            print(f"UNREADABLE  {service}: {exc}", file=out)
            status = max(status, 1)
            continue
        if not isinstance(rows, list):
            # A proxy or a login page can answer 200 with JSON that is not a
            # list. `[]` and "something that is not a list" are opposite
            # findings and only one of them is "no download clients".
            print(f"UNREADABLE  {service}: {DOWNLOAD_CLIENT_PATH} answered "
                  f"{type(rows).__name__}, not a list", file=out)
            status = max(status, 1)
            continue
        judged.append(service)
        for row in rows:
            buckets[classify(_field(row, "host"))].append((service, row))

    if buckets[OFF_LAN]:
        print("DOWNLOAD CLIENT OFF THE LAN -- an app on the NAS is configured to hand its "
              "downloads, and the credentials it authenticates with, to a machine on the "
              "internet.", file=out)
        for service, row in buckets[OFF_LAN]:
            print(f"  {service}: {_row_label(row)}", file=out)
        print("  These apps have no login by the owner's own decision, so anything on his LAN "
              "can add one of these. Ask him before removing it -- he may have added it "
              "himself.", file=out)
        status = 2

    if buckets[UNJUDGED]:
        print("CANNOT JUDGE -- this destination is a name this pod cannot resolve, so it is "
              "neither cleared nor raised:", file=out)
        for service, row in buckets[UNJUDGED]:
            print(f"  {service}: {_row_label(row)}", file=out)
        status = max(status, 1)

    if buckets[LOCAL]:
        print("ON HIS OWN NETWORK -- printed, not raised:", file=out)
        for service, row in buckets[LOCAL]:
            print(f"  {service}: {_row_label(row)}", file=out)

    if not buckets[OFF_LAN] and not buckets[UNJUDGED]:
        print(f"EVERY DOWNLOAD CLIENT IS ON HIS OWN NETWORK on {len(judged)} service(s): "
              f"{', '.join(judged) or 'none'}.", file=out)

    print(f"Judged the download clients of {len(judged)} service(s) of {len(conf_all)}, read "
          "over the SSH hop. This is current state only, and it judges where a destination is "
          "rather than whether it can be trusted.", file=out)
    return status


def main(argv=None, env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_egress",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, get=get, ssh=ssh, run=run)


if __name__ == "__main__":
    sys.exit(main())
