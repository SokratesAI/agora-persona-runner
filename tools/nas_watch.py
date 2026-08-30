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

**nzbget is the third service and it is judged differently, because it is a
different shape.** Cycle 640 measured it: it binds `0.0.0.0:6789`, its
JSON-RPC carries `saveconfig`, and `ScriptDir` plus `Extensions` make it run
an executable on every download -- the same code-execution surface as
`CustomScript`, reached through a different door. Unlike the two *arr apps it
has a lock on that door, so there are two questions here and only one of them
needs a password:

* **Is the lock on?** `/jsonrpc/version` with no credential must answer 401.
  If it ever answers 200, anything on the LAN can call `saveconfig`, and that
  is a raise. This needs nothing handed in and runs every cycle.
* **Is an extension configured?** That is `/jsonrpc/config`, which is behind
  the lock, so it runs only when `NZBGET_USER` and `NZBGET_PASS` are in the
  environment. When they are not, it prints what it did not judge and does
  **not** raise -- an unprovisioned credential is a fact about this pod, not a
  finding about the NAS, and a check that is red from the day it ships is one
  nobody reads.

A 401 says the lock is on. It does not say the key is hard to guess, and this
deliberately does not try to find out: measuring password strength means
trying passwords against the owner's own box, which is not a thing a
monitoring check should do.

**Two things it does not see, said here rather than discovered later.** It
reads current state, so a row added and removed between two cycles leaves no
trace here. And Plex is on that box too and is not judged at all.

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

#: The same question asked of Tautulli, whose API names its agents in its own
#: vocabulary: `scripts` is the agent that takes a path under its script
#: folder and runs it when an event fires. Every other agent it ships --
#: Discord, Pushover, Telegram, a webhook -- posts somewhere and is printed
#: rather than raised, for the same reason `Webhook` is absent from `EXECUTES`
#: above.
TAUTULLI_EXECUTES = frozenset({"scripts"})


def _tautulli_label(row):
    """`friendly name | agent` for a Tautulli notifier, tolerant of a thin row."""
    name = (row.get("friendly_name") or "").strip() or "(unnamed)"
    agent = (row.get("agent_label") or row.get("agent_name") or "").strip() or "(no agent)"
    return f"{name} | {agent}"



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


def report(env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None,
           unlocked=nas.nzbget_unlocked, config=nas.nzbget_config,
           credential=nas.nzbget_credential, key=nas.tautulli_key,
           notifiers=nas.tautulli_notifiers):
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
        print("SERVICES UNREADABLE -- the SSH hop exists but no *arr service could be configured "
              "through it.", file=out)
        print(nas.UNCONFIGURED_HELP, file=out)
        # nzbget is still judged. It needs no API key, so the thing that made
        # the other two unreadable does not reach it, and an open control
        # interface is exactly the finding that must not be lost to an
        # unrelated failure one line above it.
        nzb_status, nzb_judged = _nzbget(hop, out, env=env, run=run, credential=credential, unlocked=unlocked,
                                         config=config)
        # Tautulli is judged here for the same reason nzbget is: its key comes
        # off its own config file rather than out of `nas.config`, so whatever
        # made the two *arr apps unreadable does not reach it.
        tau_status, tau_judged = _tautulli(hop, out, env=env, run=run, key=key, notifiers=notifiers)
        _print_sweep(0, nzb_judged, tau_judged, out)
        return max(1, nzb_status, tau_status)

    status = 0
    judged = []
    executing = []
    other = []
    unreadable = []
    for service in nas.unconfigured(conf_all):
        # The denominator is what `tools.nas` says exists, never what came
        # back: `nas.config` drops a service whose key discovery failed, so
        # counting its result reports "1 of 1" over an app nobody asked --
        # and this is the check that finds a Custom Script.
        unreadable.append((service, nas.UNDISCOVERED_REASON))
        status = max(status, 1)
    for service in sorted(conf_all):
        try:
            rows = get(service, conf_all[service], NOTIFICATION_PATH)
        except nas.Unreachable as exc:
            unreadable.append((service, str(exc)))
            status = max(status, 1)
            continue
        if not isinstance(rows, list):
            # A proxy or a login page can answer 200 with JSON that is not a
            # list. `[]` and "something that is not a list" are opposite
            # findings and only one of them is "no notifications".
            unreadable.append((service, f"{NOTIFICATION_PATH} answered "
                                        f"{type(rows).__name__}, not a list"))
            status = max(status, 1)
            continue
        judged.append(service)
        for row in rows:
            (executing if _executes(row) else other).append((service, row))

    if unreadable:
        # Deliberately not the words `CANNOT JUDGE`: `tools.nas_egress` already
        # prints that heading for a destination it cannot classify, and two
        # blocks opening on the same two words in one report is a reader
        # having to know which is which.
        print("NOT ASKED -- these services were never read, so nothing below is a claim "
              "about them:", file=out)
        for service, why in sorted(unreadable):
            print(f"  {service}: {why}", file=out)

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

    if not executing and not unreadable:
        # Guarded on `unreadable` too: an everything-is-fine headline printed
        # directly under a service nobody asked is the finding inverted.
        print(f"NO CODE EXECUTION CONFIGURED on {len(judged)} service(s): "
              f"{', '.join(judged) or 'none'}.", file=out)

    nzb_status, nzb_judged = _nzbget(hop, out, env=env, run=run, credential=credential, unlocked=unlocked, config=config)
    status = max(status, nzb_status)
    tau_status, tau_judged = _tautulli(hop, out, env=env, run=run, key=key, notifiers=notifiers)
    status = max(status, tau_status)
    _print_sweep(len(judged), nzb_judged, tau_judged, out)
    return status


# Every place on the NAS that can be configured to run a command: the two *arr
# notification lists, nzbget's extension settings, and Tautulli's notification
# agents. `nas.SERVICES` is the *arr pair only, so counting the sweep against it
# counts half the box.
#
# Tautulli is the fourth and it was missing for two cycles: I installed it on that
# box myself in Cycle 669, it has a Script notification agent that runs an
# executable on every event, and nothing here asked it anything. That is this
# check's own founding lesson pointed back at me -- the denominator is what
# exists, not what answered -- and the denominator moved the day I added a
# service, not the day I noticed.
SURFACES = len(nas.SERVICES) + 2


def _print_sweep(arr_judged, nzbget_judged, tautulli_judged, out):
    """The one line `tools.preflight` shows for this check.

    It has to carry nzbget, and until Cycle 646 it did not. The summary said
    *"Judged the notification list of 2 service(s) of 2"* -- true about the two
    *arr apps and a complete-sweep sentence about the box -- while nzbget's
    extension list went unjudged on every run, because reading it needs a
    credential this pod has never had. `preflight` collapses a clean check to
    its last line carrying a digit, and the three lines `_nzbget` prints below
    that summary carry none, so the `NOT JUDGED` never reached the table a
    cycle actually reads. That is `tools.nas_egress`'s and `tools.nas.config`'s
    lesson one module over: **the denominator is what exists, not what
    answered** -- and here the unjudged third is the one carrying the remote
    code-execution path.
    """
    judged = arr_judged + (1 if nzbget_judged else 0) + (1 if tautulli_judged else 0)
    print(f"Judged the code-execution surface of {judged} service(s) of {SURFACES}, read over "
          "the SSH hop. This is current state only: a notification added and removed between "
          "two cycles leaves no trace here.", file=out)
    if not nzbget_judged:
        print("  nzbget's extension list is one of those surfaces and was not judged -- so this "
              "is not a clean sweep of the box, whatever the exit status says.", file=out)
    if not tautulli_judged:
        print("  Tautulli's notification agents are one of those surfaces and were not judged -- "
              "so this is not a clean sweep of the box, whatever the exit status says.", file=out)


def _nzbget(hop, out, env=None, run=None, unlocked=nas.nzbget_unlocked, config=nas.nzbget_config,
            credential=nas.nzbget_credential):
    """The nzbget half. Returns `(exit status, was its extension list judged)`.

    The second element is not the same question as the first. A locked control
    interface with no credential here is exit 0 -- an unprovisioned credential
    is a fact about this pod, not a finding about the NAS -- and it is also
    *not a judgement of nzbget's extension list*, so it must not be counted as
    one in the sweep line. See `_print_sweep`.
    """
    kwargs = {} if run is None else {"run": run}
    try:
        open_to_anyone = unlocked(hop, **kwargs)
    except nas.Unreachable as exc:
        print(f"UNREADABLE  nzbget: {exc}", file=out)
        return 1, False

    if open_to_anyone:
        print("NZBGET CONTROL IS OPEN -- /jsonrpc answers with no credential, and it carries "
              "saveconfig.", file=out)
        print("  Anything that can reach 127.0.0.1:6789 can set ScriptDir and Extensions, which "
              "makes the NAS run an executable on every download.", file=out)
        # An open control interface is a finding about nzbget's code-execution
        # surface, so it counts as judged: the sweep line must not print a
        # short denominator beside a raise about the very service it dropped.
        return 2, True

    print("NZBGET CONTROL IS LOCKED -- /jsonrpc refuses an unauthenticated call. That is the "
          "lock being on, not the key being strong.", file=out)

    found = credential(env, ssh=hop, **kwargs)
    if found is None:
        print("  NOT JUDGED  nzbget's extension list -- reading it needs NZBGET_USER and "
              "NZBGET_PASS, and neither this pod's environment nor "
              f"{nas.NZBGET_COMPOSE_FILE} on the NAS carries both.", file=out)
        print("  This does not raise: an unprovisioned credential is a fact about this pod, not "
              "a finding about the NAS.", file=out)
        return 0, False

    try:
        conf = config(hop, found, **kwargs)
    except nas.Unreachable as exc:
        print(f"UNREADABLE  nzbget: {exc}", file=out)
        return 1, False

    configured = [(name, value) for name, value in sorted(conf.items())
                  if _runs_a_script(name) and value.strip()]
    if configured:
        print("CODE EXECUTION CONFIGURED -- nzbget is set to run a script.", file=out)
        for name, value in configured:
            print(f"  nzbget: {name} = {value}", file=out)
        print(f"    scripts are looked up in ScriptDir = {conf.get('scriptdir', '(unset)')}", file=out)
        print("  Ask him before removing it -- he may have added it himself.", file=out)
        return 2, True

    print(f"  No extension or script task is configured; {len(conf)} setting(s) read.", file=out)
    return 0, True


def _tautulli(hop, out, env=None, run=None, key=nas.tautulli_key, notifiers=nas.tautulli_notifiers):
    """The Tautulli half. Returns `(exit status, was its notifier list judged)`.

    The second element is the same distinction `_nzbget` draws: an unreadable
    key is a fact about this pod's reach, not a finding about the NAS, so it
    does not raise -- and it is also not a judgement of the list, so it must
    not be counted as one in the sweep line.
    """
    kwargs = {} if run is None else {"run": run}
    try:
        found = key(hop, env=env, **kwargs)
    except nas.Unreachable as exc:
        print(f"  NOT JUDGED  Tautulli's notification agents -- its key could not be read: {exc}.",
              file=out)
        print("  This does not raise: an unreadable key is a fact about this pod, not a finding "
              "about the NAS.", file=out)
        return 0, False

    try:
        rows = notifiers(hop, found, **kwargs)
    except nas.Unreachable as exc:
        print(f"UNREADABLE  tautulli: {exc}", file=out)
        return 1, False

    executing = [row for row in rows if (row.get("agent_name") or "").strip() in TAUTULLI_EXECUTES]
    other = [row for row in rows if row not in executing]
    if other:
        print(f"Tautulli also has {len(other)} notification agent(s) that post somewhere rather "
              "than run something on the NAS:", file=out)
        for row in other:
            print(f"  tautulli: {_tautulli_label(row)}", file=out)
    if executing:
        print("CODE EXECUTION CONFIGURED -- Tautulli is set to run a script on an event.", file=out)
        for row in executing:
            print(f"  tautulli: {_tautulli_label(row)}", file=out)
        print("  Ask him before removing it -- he may have added it himself.", file=out)
        return 2, True

    print(f"  Tautulli has no script notification agent; {len(rows)} agent(s) read.", file=out)
    return 0, True


def _runs_a_script(name):
    """Does this nzbget setting name something that gets executed?

    Matched by shape rather than by a fixed list, because the settings that
    carry a script are per-category and per-feed and per-scheduler-task --
    `Category3.Extensions`, `Feed2.Extensions`, `Task1.Command` -- so a fixed
    list would silently stop covering the box the day he adds a fifth
    category. `ScriptDir` itself is deliberately not here: it says where
    scripts are found, not that any runs, and it is printed beside a finding
    instead.
    """
    return name.endswith("extensions") or name.endswith("script") or name.endswith(".command")


def main(argv=None, env=None, out=sys.stdout, get=nas._get, ssh=nas._UNSET, run=None,
         unlocked=nas.nzbget_unlocked, config=nas.nzbget_config,
         credential=nas.nzbget_credential, key=nas.tautulli_key,
         notifiers=nas.tautulli_notifiers):
    argparse.ArgumentParser(
        prog="python3 -m tools.nas_watch",
        description=__doc__.split("\n")[0],
    ).parse_args(argv)
    return report(env=env, out=out, get=get, ssh=ssh, run=run, unlocked=unlocked, config=config,
                  credential=credential, key=key, notifiers=notifiers)


if __name__ == "__main__":
    sys.exit(main())
