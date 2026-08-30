"""What can the home LAN reach on the NAS, and has anything new appeared?

Cycle 649, on the owner's standing capture -- *"Work on the NAS and its
security instead -- that's the highest priority right now."*

The four NAS checks that came before this one each ask a question *about a
service I already know is there*: `tools.nas_health` asks whether the four
media services are alive, `tools.nas_watch` asks whether anything is wired to
run a command, `tools.nas_egress` asks where the downloads go, and
`tools.nas_versions` asks how old the builds are. All four take the inventory
as given. Nothing asks the question underneath them: **what is listening on
that box at all.**

That matters here specifically because of what the exposure write-up found
(`nova/resources/research/nas-exposure-2026-08-29.md`): Sonarr and Radarr have
no login by the owner's own decision and bind `0.0.0.0`, so the security
boundary on that box is not authentication -- it is which ports the home LAN
can reach. A new container published on a new port is one `docker run -p`
away, and the only thing that would ever have noticed is somebody looking.

**It asks from the NAS, at the NAS's own LAN address, and that is the whole
design.** A probe of `127.0.0.1` answers a different and much less useful
question -- everything answers on loopback, including the services that are
deliberately bound there and reach nobody. Asking `192.168.0.119` from the box
itself sends the packet out and back through the LAN interface, so a service
bound to loopback refuses and a service bound to `0.0.0.0` answers. What comes
back is therefore a real statement about what his family's phones, and
anything else that gets onto that network, can open.

**The transport is the one this package already owns and it is not widened.**
`tools.nas` runs exactly one remote command, the constant string
`curl --config -`, with everything variable arriving on stdin -- that is what
makes a shell on the far side unable to be talked into anything. A port sweep
would be far easier with `ss` or `nc`, and neither is worth giving this loop a
general remote shell for. curl reads many transfers from one config when they
are separated by `next`, so the whole sweep is one SSH call: measured 8.6s for
28 ports from the bridge pod.

It does **not** use `nas._run_ssh`, and the reason is a contract difference
rather than a preference: that helper raises `Unreachable` when curl exits
non-zero, and here a non-zero curl exit is the *normal* case -- a closed port
is exit 7 and the sweep is mostly closed ports. An ssh failure (255) is still
fatal, because that is the case where nothing was measured at all.

**What raises.** A port that answers or speaks and is not in `BASELINE` raises
2: something is listening that I have no record of, and on a box whose apps
have no login that is the finding. A baseline port that answers prints and
does not raise -- his DSM UI and his media apps are supposed to be there, and
a check that goes red on the expected state is one that stops being read. A
baseline port that has gone *quiet* prints and does not raise either; whether
a service is down is `tools.nas_health`'s question, asked properly, and a
second opinion here would be two places to fix one answer. A non-baseline port
that times out cannot be told from a firewall drop, so it prints `CANNOT
JUDGE` and exits 1 -- unjudged must never read as clean.

**The candidate list is no longer the whole of what gets swept, and that gap
was real.** Cycle 649 wrote here that a listener on a port not in `CANDIDATES`
is invisible, that widening to all 65535 ports means 65535 transfers, and that
loosening the remote command to get a real scanner is the worse trade. The
first two are true and the third framed the choice as scanner-or-nothing.
There is a third option and the box hands it over: `/proc/net/tcp` and
`/proc/net/tcp6` list every TCP socket in LISTEN with the address it is bound
to. Reading them is one constant remote command with **no variable part at
all** -- strictly less attackable than the curl one, which takes stdin -- and
it is a read rather than a scan. Measured Cycle 658: the box has 29 distinct
listening TCP ports and 12 of the LAN-reachable ones were not in `CANDIDATES`,
including Heimdall on 8085 serving its dashboard to the LAN. So the sweep now probes
the union of the candidate list and whatever is actually listening.

**The table alone is not the answer either, which is why the probe stays.**
Measured the same cycle: 9993, 21596 and 56478 are bound to `192.168.0.119`
according to `/proc`, and a curl at that address from the box itself is
*refused* on all three. Something between the socket and the interface says
no. So the listener table says what exists, completely, and the probe says
what the LAN can actually open -- and only the second one is the security
question. Neither replaces the other.

What is still unjudged is UDP: this reads the TCP tables only.

Exit status, the same three meanings as every check in `tools.preflight`:

* **2** -- a port is reachable on the LAN that is not in the baseline.
* **1** -- something could not be judged: a port neither answered nor refused,
  or the sweep itself was unreadable.
* **0** -- everything reachable is something already on the record, and the
  report names what it swept and what it did not.

On a pod that cannot make the SSH hop this prints `CANNOT SEE FROM THIS POD`
and exits 0 without judging anything, the same call the other four NAS checks
make for the same reason.
"""

import argparse
import ipaddress
import socket
import subprocess
import sys

from tools import nas

#: The NAS's address on the home LAN. An env override for the same reason
#: `nas.SSH_DEFAULTS` has one -- nothing here should be pinned to one box --
#: and the default is the address `tools.nas_egress` reads out of both apps'
#: download-client rows today.
LAN_DEFAULT = "192.168.0.119"

#: Ports this sweep asks about, with what each one is when it answers. The
#: list is deliberately short and hand-picked: the media stack, the Synology
#: admin and file-sharing surfaces, and the handful of ports a self-hosted
#: service most often lands on. Every entry costs one transfer.
CANDIDATES = {
    21: "ftp",
    22: "ssh",
    80: "http",
    111: "rpcbind",
    139: "netbios / smb",
    443: "https",
    445: "smb",
    548: "afp",
    873: "rsync",
    1900: "ssdp / upnp",
    3306: "mysql",
    5000: "synology dsm (http)",
    5001: "synology dsm (https)",
    5432: "postgres",
    6379: "redis",
    6789: "nzbget",
    7878: "radarr",
    8080: "http-alt",
    8081: "http-alt",
    8096: "jellyfin",
    8123: "home assistant",
    8200: "synology dlna",
    8888: "http-alt",
    8989: "sonarr",
    9091: "transmission",
    9117: "jackett",
    32400: "plex",
    32469: "plex dlna",
    # Read off the box's own listener table and named from `netstat -tlnp`,
    # Cycle 670. Every one of these was already being swept -- the union with
    # the live listener table put them in -- but none was a candidate, so the
    # report called each one "not a candidate port" and had nothing to say
    # about what it was.
    81: "dsm nginx (http, alt)",
    444: "dsm nginx (https, alt)",
    3261: "synology iscsi target",
    3263: "synology iscsi target",
    3264: "synology iscsi target",
    3265: "synology iscsi (scsi_plugin_server)",
    5357: "dsm nginx (ws-discovery)",
    5566: "synology btrfs replication",
    8085: "heimdall",
    8181: "tautulli",
    9696: "prowlarr",
    46090: "tailscaled",
}

#: What was reachable on the LAN address when this check was written, measured
#: rather than assumed (Cycle 649, 2026-08-30 06:07 Oslo). This is the record
#: a later sweep is compared against, so a port here is not "safe" -- it is
#: "already known and already written about". Sonarr and Radarr answering with
#: no login is the subject of my issue #18, not a thing this check clears.
BASELINE = {
    22: "the SSH hop this loop itself uses",
    139: "Synology file sharing",
    445: "Synology file sharing",
    5000: "the DSM admin UI, over plain HTTP",
    5001: "the DSM admin UI, over HTTPS",
    6789: "nzbget, behind its password",
    7878: "Radarr, no login (my issue #18)",
    8989: "Sonarr, no login (my issue #18)",
    32400: "Plex, behind its login",
    # Added Cycle 670, and four of them are mine. Between Cycle 649 writing
    # the record above and this sweep I installed Prowlarr (667), Tautulli
    # (669) and left Heimdall running (666) -- so a third of what
    # this check was calling an unrecorded listener on the home LAN was a
    # service I had put there myself and written a journal entry about. It
    # raised 2 on all eleven of these every cycle. A check that cries wolf
    # eleven times is one where the twelfth line, the real one, is read as
    # part of the noise, and that is the whole failure mode this exists to
    # prevent.
    #
    # Each entry below is what `sudo netstat -tlnp` on the box says owns the
    # socket, not a guess from the port number. The four docker ones were
    # cross-checked against `docker ps --format '{{.Names}}\t{{.Ports}}'`.
    #
    # **Adding a service to that box means adding its port here**, in the
    # same cycle. There is no mechanism that does it for you and there
    # deliberately is not one: a baseline that learns from the box would
    # clear an intruder's listener the first time it saw it.
    81: "DSM's own nginx, alternate HTTP port (pid shared with 444 and 5357)",
    444: "DSM's own nginx, alternate HTTPS port",
    3261: "Synology iSCSI target, IPv6 only, no readable owner (kernel side)",
    3263: "Synology iSCSI target, IPv6 only, no readable owner (kernel side)",
    3264: "Synology iSCSI target, IPv6 only, no readable owner (kernel side)",
    3265: "Synology iSCSI, scsi_plugin_server, IPv6 only",
    5357: "DSM's own nginx, WS-Discovery",
    5566: "synobtrfsreplica, Synology Btrfs replication, IPv6 only",
    8085: "Heimdall, the dashboard I installed Cycle 666. No login: `/` answers 200 and the page carries no login form. It lists what runs on the box; it holds no credential",
    8181: "Tautulli, the Plex monitor I installed Cycle 669. `/` redirects 303 to `/home`; its API refuses an unauthenticated caller (401 on `/api/v2?cmd=get_server_info`)",
    9696: "Prowlarr, the indexer manager I installed Cycle 667. `/` answers 200; its API refuses an unauthenticated caller (401 on `/api/v1/indexer`), so the NZBgeek key it holds is not handed out the way Sonarr's is",
    46090: "tailscaled's own listener",
}

#: The one other remote command this module runs. It is a constant with no
#: variable part at all -- nothing is interpolated into it and nothing arrives
#: on its stdin -- which is a smaller surface than the `curl --config -` hop
#: beside it, not a larger one.
LISTENER_COMMAND = "cat /proc/net/tcp /proc/net/tcp6"

#: The `st` column's value for a socket in LISTEN.
_LISTEN = "0A"


def _bind_address(field):
    """`'0100007F:B55C'` -> `('127.0.0.1', 46428)`.

    `/proc` writes the address as hex words in **host** byte order, which is
    little-endian here, so each group of four bytes has to be reversed before
    it is an address. Getting that wrong does not raise -- it produces a
    plausible-looking address that is simply the wrong one -- so this is one of
    the two things `tests/test_nas_ports.py` pins against real captured rows.
    """
    raw, _, port = field.partition(":")
    packed = bytes.fromhex(raw)
    if len(packed) not in (4, 16):
        raise ValueError(f"not an address field: {field!r}")
    packed = b"".join(packed[i:i + 4][::-1] for i in range(0, len(packed), 4))
    family = socket.AF_INET if len(packed) == 4 else socket.AF_INET6
    return socket.inet_ntop(family, packed), int(port, 16)


def parse_listeners(text):
    """`{port: [bind address, ...]}` for every TCP socket in LISTEN.

    One port appears on several addresses when a service binds each interface
    separately, so the addresses are kept rather than collapsed: `127.0.0.1`
    and `0.0.0.0` are the difference between a service nobody can reach and one
    the whole LAN can, and that distinction is the entire point of the check.
    A row this does not understand is skipped rather than guessed at.
    """
    found = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 4 or not fields[0].endswith(":"):
            continue
        if fields[3] != _LISTEN:
            continue
        try:
            address, port = _bind_address(fields[1])
        except (ValueError, OSError):
            continue
        found.setdefault(port, [])
        if address not in found[port]:
            found[port].append(address)
    return found


def listeners(ssh, run=subprocess.run, timeout=30):
    """Every listening TCP port on the NAS, read off the box's own tables.

    A non-zero exit is `Unreachable` rather than an empty answer, because an
    empty listener table and an unread one look identical and mean opposite
    things -- the first would silently shrink the sweep back to the curated
    list while reporting a complete one.
    """
    argv = ["ssh", "-i", ssh["key"], *nas.SSH_OPTS, f"{ssh['user']}@{ssh['host']}",
            LISTENER_COMMAND]
    try:
        done = run(argv, input=None, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise nas.Unreachable(f"the NAS did not answer within {timeout}s") from exc
    except OSError as exc:
        raise nas.Unreachable(f"could not start ssh: {exc}") from exc
    if done.returncode != 0:
        raise nas.Unreachable(
            f"reading the listener table failed: "
            f"{(done.stderr or '').strip() or f'exit {done.returncode}'}")
    return parse_listeners(done.stdout or "")


# curl's exit codes, named where they are read rather than where they are set.
_HTTP_OK = 0          # a transfer completed; %{http_code} says what it said
_REFUSED = 7          # nothing is listening: the kernel sent a reset
_TIMED_OUT = 28       # neither: a silent listener or a firewall that drops

#: Every curl exit that means **the TCP connection was made and the thing on
#: the other end did not speak HTTP**. This was `_NOT_HTTP = 1` alone until
#: Cycle 670, and the two that were missing are the reason `tools.preflight`
#: had been printing a permanent `NOT SWEPT` for two of this box's ports:
#: measured from the NAS itself, `5566` (`synobtrfsreplica`) exits **52** and
#: `46090` (`tailscaled`) exits **56**. Both accepted the connection and then
#: said something curl could not read as a response, which is *more* evidence
#: of a listener than exit 1, not less -- and both fell into the `else` below
#: and were reported as never swept. An unswept port is the one state this
#: check must not invent, but it is also the one it must not over-report:
#: "I could not measure that" said about a listener I had in fact measured
#: sends a later cycle to re-probe a port that already answered.
_SPOKE_NOT_HTTP = (
    1,    # ssh does this: bytes arrived, none of them an HTTP response
    52,   # empty reply -- connected, then closed without sending anything
    56,   # recv failure -- connected, then the peer reset it
)

ANSWERED = "answered"
SPOKE = "spoke"
CLOSED = "closed"
SILENT = "silent"


def lan_address(env=None):
    """The address to probe, refused unless it is on a private network.

    This is a guard rather than a formality. The address is env-overridable,
    and a sweep of two dozen ports pointed at a public host is a port scan of
    somebody else's machine -- so the one thing this must never do is accept
    one.

    The predicate is `is_global`, not `is_private`, and the difference is not
    cosmetic: `100.64.0.0/10` -- the range Tailscale hands out, including the
    NAS's own `100.89.37.25` -- is in *neither* bucket on this Python, so
    requiring `is_private` refuses the address the SSH hop itself connects to.
    That is the same trap `tools.nas_egress.classify` documents from the other
    direction, and my own first draft of this function walked straight into
    it. What actually needs refusing is a publicly routable host, which is
    exactly what `is_global` names.
    """
    import os

    raw = ((env if env is not None else os.environ).get("NAS_LAN_ADDR") or LAN_DEFAULT).strip()
    try:
        parsed = ipaddress.ip_address(raw)
    except ValueError as exc:
        raise ValueError(f"NAS_LAN_ADDR must be an IP address, not {raw!r}") from exc
    if parsed.is_global:
        raise ValueError(f"refusing to sweep {raw}: this probes a private address only, "
                         "and that one is routable on the public internet")
    return raw


def config_for(address, ports):
    """The curl config that probes each port, as one string on stdin.

    `next` is what makes `write-out` per-transfer. Without it every option is
    global and the last one wins, so every result line comes back carrying the
    last port's number -- which is exactly what the first draft of this did.
    """
    blocks = []
    for port in ports:
        blocks.append("\n".join([
            f'url = "http://{address}:{port}/"',
            "connect-timeout = 2",
            "max-time = 4",
            "output = /dev/null",
            f'write-out = "PORT {port} %{{http_code}} %{{exitcode}}\\n"',
            "silent",
            "show-error",
        ]))
    # `next` separates transfers and must not trail the last one: a `next`
    # with no url after it makes curl exit on "no URL specified".
    return "\nnext\n".join(blocks) + "\n"


def sweep(ssh, address, ports, run=subprocess.run, timeout=180):
    """`{port: (state, http_code)}` for one pass over `ports`.

    curl's own exit status is ignored on purpose -- it reports the last
    transfer that failed, and most of these are supposed to fail. ssh's 255 is
    not ignored, because that is the case where nothing was measured.
    """
    argv = ["ssh", "-i", ssh["key"], *nas.SSH_OPTS, f"{ssh['user']}@{ssh['host']}",
            "curl --config -"]
    try:
        done = run(argv, input=config_for(address, ports), capture_output=True,
                   text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise nas.Unreachable(f"the NAS did not answer within {timeout}s") from exc
    except OSError as exc:
        raise nas.Unreachable(f"could not start ssh: {exc}") from exc
    if done.returncode == 255:
        raise nas.Unreachable(
            f"ssh to {ssh['host']} failed: {(done.stderr or '').strip() or 'no detail'}")

    seen = {}
    for line in (done.stdout or "").splitlines():
        parts = line.split()
        if len(parts) != 4 or parts[0] != "PORT":
            continue
        try:
            port, code, exit_code = int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            continue
        if exit_code == _HTTP_OK:
            seen[port] = (ANSWERED, code)
        elif exit_code in _SPOKE_NOT_HTTP:
            seen[port] = (SPOKE, None)
        elif exit_code == _REFUSED:
            seen[port] = (CLOSED, None)
        elif exit_code == _TIMED_OUT:
            seen[port] = (SILENT, None)
        else:
            # Any other curl code is a real result this does not understand,
            # and guessing at it would be the thing every other check here
            # refuses to do. Left out of `seen` so it reports as unswept.
            continue
    return seen


def _label(port, state, code, bound=None):
    what = CANDIDATES.get(port)
    if what is None:
        where = ", ".join(bound) if bound else None
        what = f"not a candidate port, listening on {where}" if where else "unknown to this check"
    if state == ANSWERED:
        return f"{port}/tcp  HTTP {code}  ({what})"
    if state == SPOKE:
        return f"{port}/tcp  listening, not HTTP  ({what})"
    if state == SILENT:
        return f"{port}/tcp  no answer and no refusal  ({what})"
    return f"{port}/tcp  closed  ({what})"


def report(env=None, out=sys.stdout, ssh=nas._UNSET, run=subprocess.run):
    """Print the report and return the exit status."""
    hop = nas.ssh_config(env) if ssh is nas._UNSET else ssh
    if hop is None:
        print("CANNOT SEE FROM THIS POD -- no port was judged, and this does not raise the "
              "status.", file=out)
        print("  The hop needs an `ssh` binary on PATH and the sealed key at "
              f"{nas.SSH_DEFAULTS['key']}; this pod has one or neither.", file=out)
        print("Judged 0 port(s). Nothing here is a claim about the NAS.", file=out)
        return 0

    try:
        address = lan_address(env)
    except ValueError as exc:
        print(f"CANNOT SEE  {exc}", file=out)
        print("Judged 0 port(s). An unswept LAN address is not a clean sweep.", file=out)
        return 1

    try:
        found = listeners(hop, run=run)
    except nas.Unreachable as exc:
        print(f"CANNOT SEE  the listener table did not read, so the sweep is back to the "
              f"curated candidate list and its old blind spot: {exc}", file=out)
        found = None

    ports = sorted(set(CANDIDATES) | set(found or {}))
    try:
        seen = sweep(hop, address, ports, run=run)
    except nas.Unreachable as exc:
        print(f"CANNOT SEE  the sweep did not run: {exc}", file=out)
        print(f"Judged 0 of {len(ports)} port(s). An unswept port is not a closed one.",
              file=out)
        return 1

    unswept = [p for p in ports if p not in seen]
    new, known, quiet, unjudged = [], [], [], []
    for port in ports:
        state, code = seen.get(port, (None, None))
        if state in (ANSWERED, SPOKE):
            (known if port in BASELINE else new).append((port, state, code))
        elif state == SILENT:
            (known if port in BASELINE else unjudged).append((port, state, code))
        elif state == CLOSED and port in BASELINE:
            quiet.append((port, state, code))

    status = 0
    if new:
        print("NEW LISTENER ON THE HOME LAN -- something is reachable on the NAS that is not "
              "on the record.", file=out)
        for port, state, code in new:
            print(f"  {_label(port, state, code, (found or {}).get(port))}", file=out)
        print("  Sonarr and Radarr on this box have no login by his own decision, so a port "
              "being reachable is the control, not authentication. Ask him what this is before "
              "assuming it is unwanted -- he runs containers on that box himself.", file=out)
        status = 2

    if unjudged:
        print("CANNOT JUDGE -- these neither answered nor refused, which a silent listener and "
              "a firewall that drops both look like:", file=out)
        for port, state, code in unjudged:
            print(f"  {_label(port, state, code, (found or {}).get(port))}", file=out)
        status = max(status, 1)

    if unswept:
        print("NOT SWEPT -- curl returned no result this understands for these, so they are "
              "neither open nor closed here:", file=out)
        for port in unswept:
            print(f"  {port}/tcp  ({CANDIDATES.get(port, 'not a candidate port')})", file=out)
        status = max(status, 1)

    if quiet:
        print("GONE QUIET -- on the record and not answering now. Whether a service is down is "
              "tools.nas_health's question, so this prints and does not raise:", file=out)
        for port, state, code in quiet:
            print(f"  {_label(port, state, code, (found or {}).get(port))}  was: {BASELINE[port]}", file=out)

    if known:
        print("ON THE RECORD -- reachable and already written about, printed and not raised:",
              file=out)
        for port, state, code in known:
            print(f"  {_label(port, state, code, (found or {}).get(port))}  {BASELINE[port]}", file=out)

    if not new and not unjudged and not unswept:
        print(f"NOTHING NEW IS REACHABLE on {address}: every port that answered is one already "
              "on the record.", file=out)

    # The caveat goes above the summary, not below it: `tools.preflight`
    # collapses a clean check to its last line carrying a digit, so a
    # `NOT JUDGED` line printed last becomes the summary and the sentence
    # saying what was actually swept disappears from the one table I read
    # every cycle. Same rule Cycle 646 learnt one module over.
    if found is None:
        print(f"NOT JUDGED  the other {65535 - len(ports)} TCP port(s) -- without the listener "
              "table this is a curated candidate list, not a full sweep, so a clean run here "
              "is not a claim that nothing else is open.", file=out)
        status = max(status, 1)
    else:
        print("NOT JUDGED  every UDP socket on the box -- this reads the TCP listener tables "
              "only, so a UDP service is invisible to it.", file=out)
    listed = "" if found is None else (
        f"{len(found)} of them read from the box's own TCP listener table, so no listening TCP "
        "port was left out, ")
    print(f"Judged {len(seen)} of {len(ports)} port(s) on {address}, {listed}asked from the "
          "NAS itself so a loopback-only service refuses. This is current state only.", file=out)
    return status


def main(argv=None, env=None, out=sys.stdout, ssh=nas._UNSET, run=subprocess.run):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_ports",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, ssh=ssh, run=run)


if __name__ == "__main__":
    sys.exit(main())
