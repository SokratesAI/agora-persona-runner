"""Is anything on the NAS configured to run a command when an event fires?

Cycle 639, on the owner's standing capture -- *"Work on the NAS and its
security instead -- that's the highest priority right now."*

He has decided Sonarr and Radarr keep no login, and that decision stands
(`journal-digest.md`, `[nas-auth-is-off-and-he-has-accepted-it]`). Both apps
bind `0.0.0.0`, both serve `/initialize.js` unauthenticated with the API key
in it, so **every device on his home LAN can write their configuration
today**. That is the accepted position, not a bug to re-open.

What follows from accepting it is that the compensating control has to be
*detection*, and there was none. The highest-consequence thing reachable
through that open door is `Settings -> Connect -> Custom Script`: a
notification whose implementation is `CustomScript` runs an arbitrary
executable on the NAS, as the container's user, every time an episode
imports. Adding one is a form submission. Nothing anywhere would have said
so.

Cycle 636 measured zero notification rows in both apps and wrote it down as
a real negative -- correctly, because one injected row would have shown in
that query. **A measurement of a mutable list decays the moment it is
taken**, and that one was taken by hand, once, and then quoted by two cycles
as if it were still true. This asks the question every cycle instead, from
`tools.preflight`, on the pod that check already runs on.

Measured live from the bridge pod before this file existed: `GET
/api/v3/notification` answers on both apps over the SSH hop and returns `[]`
on each.

**What raises and what does not.** A `CustomScript` row is code execution and
exits 2. Every other notification -- Discord, Pushover, a webhook -- is
printed in full and exits 0, because the owner adding a Discord notification
is an ordinary thing to do and a check that goes permanently red on a
legitimate action is a check that stops being read. The claim this makes is
narrow and it is the one it can defend: *nothing on the NAS is configured to
run a command on an event.*

**Two things it does not see, said here rather than discovered later.** It
reads current state, so a row added and removed between two cycles leaves no
trace here. And it judges Sonarr and Radarr only -- nzbget has no comparable
list and is not reached by `tools.nas`.

Exit status, the same three meanings as every check in `tools.preflight`:

* **2** -- a code-execution notification exists on one of the apps.
* **1** -- something that should have been readable was not: the SSH hop
  exists but a service was unconfigured, refused its key, or did not answer.
* **0** -- nothing to act on, and the report names what was swept.

On a pod that cannot make the SSH hop this prints `CANNOT SEE FROM THIS POD`
and exits 0 without judging anything, the same call `tools.nas_health` makes
for the same reason: that gap is closed by an image rebuild, not by anything
a check can prompt.
"""

import argparse
import sys

from tools import nas

#: The endpoint each app lists its configured notifications on. Sonarr v3 and
#: Radarr v4 both serve it under `/api/v3`, which is why one path covers both.
NOTIFICATION_PATH = "/api/v3/notification"

#: Implementations that run something on the NAS. This is the whole point of
#: the check, so it is a set of exactly what executes rather than a guess at
#: what looks risky: `CustomScript` is the *arr Connect type that takes a path
#: to an executable and runs it on every subscribed event.
#:
#: `Webhook` is deliberately NOT here. It sends an HTTP POST to a URL, which
#: is exfiltration rather than execution -- a real thing to notice, and it is
#: printed like every other row, but it does not run code on his box and
#: raising on it would blur the one claim this check makes.
EXECUTES = frozenset({"CustomScript"})


def _row_label(row):
    """`name | implementation` for a notification, tolerant of a thin row.

    The API returns `name` and `implementation` on every row it has ever
    served, but this reads a home box's JSON and a missing field must not
    turn a finding into a crash.
    """
    name = (row.get("name") or "").strip() or "(unnamed)"
    impl = (row.get("implementation") or "").strip() or "(no implementation)"
    return f"{name} | {impl}"


def _executes(row):
    return (row.get("implementation") or "").strip() in EXECUTES


def report(env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None):
    """Print the report and return the exit status."""
    hop = nas.ssh_config(env) if ssh is nas._UNSET else ssh
    if hop is None:
        print("CANNOT SEE FROM THIS POD -- no notification list was judged, and this does not "
              "raise the status.", file=out)
        print("  The hop needs an `ssh` binary on PATH and the sealed key at "
              f"{nas.SSH_DEFAULTS['key']}; this pod has one or neither.", file=out)
        print("Judged 0 service(s). Nothing here is a claim about the NAS.", file=out)
        return 0

    conf_all = nas.config(env, ssh=hop) if run is None else nas.config(env, ssh=hop, run=run)
    if not conf_all:
        print("SERVICES UNREADABLE -- the SSH hop exists but no service could be configured "
              "through it.", file=out)
        print(nas.UNCONFIGURED_HELP, file=out)
        print("Judged 0 service(s) of 2. An unreadable service is not a clean sweep.", file=out)
        return 1

    status = 0
    judged = []
    executing = []
    other = []
    for service in sorted(conf_all):
        try:
            rows = get(service, conf_all[service], NOTIFICATION_PATH)
        except nas.Unreachable as exc:
            print(f"UNREADABLE  {service}: {exc}", file=out)
            status = max(status, 1)
            continue
        if not isinstance(rows, list):
            # A proxy or a login page can answer 200 with JSON that is not a
            # list. `[]` and "something that is not a list" are opposite
            # findings and only one of them is "no notifications".
            print(f"UNREADABLE  {service}: {NOTIFICATION_PATH} answered "
                  f"{type(rows).__name__}, not a list", file=out)
            status = max(status, 1)
            continue
        judged.append(service)
        for row in rows:
            (executing if _executes(row) else other).append((service, row))

    if executing:
        print("CODE EXECUTION CONFIGURED -- a notification on the NAS runs a command when an "
              "event fires.", file=out)
        for service, row in executing:
            print(f"  {service}: {_row_label(row)}", file=out)
            path = (row.get("fields") or [])
            for field in path:
                if (field.get("name") or "").lower() == "path":
                    print(f"    runs: {field.get('value')}", file=out)
        print("  These apps have no login by the owner's own decision, so anything on his LAN "
              "can add one of these. Ask him before removing it -- he may have added it "
              "himself.", file=out)
        status = 2

    if other:
        print("OTHER NOTIFICATIONS -- printed, not judged; none of these run a command on the "
              "NAS:", file=out)
        for service, row in other:
            print(f"  {service}: {_row_label(row)}", file=out)

    if not executing:
        print(f"NO CODE EXECUTION CONFIGURED on {len(judged)} service(s): "
              f"{', '.join(judged) or 'none'}.", file=out)

    print(f"Judged the notification list of {len(judged)} service(s) of {len(conf_all)}, read "
          "over the SSH hop. This is current state only: a notification added and removed "
          "between two cycles leaves no trace here.", file=out)
    return status


def main(argv=None, env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_watch",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, get=get, ssh=ssh, run=run)


if __name__ == "__main__":
    sys.exit(main())
