"""Watch this loop from a machine that is not the one it runs on.

The owner's idea #113, and the second half of his issue #103. The design this
implements is `nova/resources/research/backup-node-2026-08.md`; read that
before changing the shape here, because the shape is the whole point.

**Why it cannot live in the cluster.** On 2026-08-24 the box went down and
his phone stayed quiet. The stall notifier existed and was innocent: it runs
inside `nova-site`, which posts to `agora`, which signs the Web Push — three
hops, all three on `server1`, which is the only node there is. A watcher for
a machine cannot run on that machine. So this program is written to be run
somewhere else (his NAS, in Docker) and to hold everything it needs to reach
his phone without asking anything of the box it is watching.

**Two verdicts, kept apart on purpose.**

- ``UNREACHABLE`` -- the cluster does not answer. Node down, network down,
  Tailscale down.
- ``SILENT`` -- the cluster answers and the loop is not writing.

Merging them would be the fourth time in a week this loop paid for one
number standing in for two causes (`agentic_health`'s three-red streak,
`ci_health`'s billing-versus-outage). Their actions point opposite ways:
one is "the box or the link is gone", the other is "the box is up and the
loop is wedged", and a message that cannot tell him which is a message that
costs him a trip to the laptop either way.

**The grace exists for one of them only.** This program sits on his home
internet, so his broadband dropping, the NAS rebooting, or Tailscale
re-authing look exactly like Hetzner dying. ``UNREACHABLE`` therefore needs
``UNREACHABLE_GRACE`` consecutive failed polls before it says anything.
``SILENT`` needs no grace: it is already measured in whole heartbeat
intervals by the server, which is a far longer clock than a poll.

**Dedupe is copied from `agora_runner.stall_notice`, not reinvented.** For
``SILENT`` the key is ``lastWrittenAt``, the write time of the newest journal
entry: while the loop is down that stamp does not move, so every later poll
finds the same key and sends nothing, and it moves again only when a cycle
writes -- the same event that ends the stall. ``UNREACHABLE`` has no such
stamp to read, so it keys on the first failure of the current outage, which
gives the same one-message-per-outage shape.

Everything that decides is a pure function of an observation and the state
carried between polls. The network and the phone are injected, so the whole
path including the rate limiter is testable here, on the box, without the
NAS existing and without a push subscription.
"""

import json
import os
import time
import urllib.error
import urllib.request

# What it polls. `/api/journal?limit=1` rather than `/api/health`: the health
# route answers "which database did you resolve and can you reach it", and
# carries no `stalled` at all. The journal route's `status` object is where
# `stalled`, `silentIntervals` and `lastWrittenAt` are computed, and `limit=1`
# is what keeps it small -- the unlimited form is 1.49MB.
DEFAULT_URL = "https://agora.tailc83eb3.ts.net/api/journal?limit=1"

# Seconds between polls. The thing being detected is measured in heartbeat
# intervals -- tens of minutes -- so polling faster buys little, and every
# poll is a wake on hardware in his living room.
DEFAULT_INTERVAL = 300

# Consecutive failed polls before `UNREACHABLE` is believed. Two, so a single
# blip on his side cannot ring the alarm; with the default interval that is a
# ten-minute detection floor, which is well inside a heartbeat interval.
UNREACHABLE_GRACE = 2

# How long to wait on the box before calling a poll failed. Deliberately far
# below the interval: a hung socket must not stack polls.
DEFAULT_TIMEOUT = 20


class Unreachable(Exception):
    """The box did not answer, whatever the reason. Cause is in `str(...)`."""


def fetch_status(url=DEFAULT_URL, timeout=DEFAULT_TIMEOUT, opener=None):
    """The `status` object the site publishes, or raise `Unreachable`.

    Every failure mode collapses to one exception on purpose. A DNS failure,
    a refused connection, a 503 and a body that is not JSON all mean the same
    thing from his living room -- "I cannot see the box" -- and giving them
    separate verdicts would put a networking taxonomy on his phone.
    """
    get = opener or urllib.request.urlopen
    try:
        with get(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as error:  # noqa: BLE001 -- see docstring
        raise Unreachable(f"{type(error).__name__}: {error}") from error
    status = payload.get("status")
    if not isinstance(status, dict):
        raise Unreachable("the response carried no status object")
    return status


def unreachable_text(failures, since, detail):
    """What he reads when the box is gone. Says which of the two it is."""
    return (
        f"Nova's server has not answered {failures} checks in a row, since "
        f"{since}. This is UNREACHABLE, not a stalled loop: from here the box, "
        f"the network or Tailscale is down, and I cannot tell which from "
        f"outside. Last error: {detail}.\n\n"
        "This watcher runs at home, so it can still reach you with the server "
        "dark — that is the whole reason it exists.\n\n"
        "No more of these until the box answers again."
    )


def silent_text(status):
    """What he reads when the box is up and the loop is wedged.

    Close to `stall_notice.notice_text` in wording on purpose -- it is the
    same event -- but it says where the message came from, because the two
    senders fail in different circumstances and he should know which one
    reached him.
    """
    cycle = status.get("cycle")
    intervals = status.get("silentIntervals")
    stamped = " ".join(
        part for part in (status.get("lastWokeDate") or "",
                          status.get("lastWokeTime") or "") if part
    )
    who = f"Cycle {cycle}" if cycle is not None else "the last cycle"
    plural = "interval" if intervals == 1 else "intervals"
    return (
        f"Nova is answering but has stopped writing. The newest journal entry "
        f"is {who}'s" + (f", written at {stamped}" if stamped else "")
        + f", and that is {intervals} heartbeat {plural} ago with nothing "
        "since.\n\n"
        "This is SILENT, not UNREACHABLE: the server is up and the loop is "
        "wedged, so it is the agora-persona-runner pod worth looking at, not "
        "the machine.\n\n"
        "No more of these until a cycle writes again."
    )


class Watch:
    """Polls, decides, and sends at most one message per outage per verdict.

    `fetch` and `send` are injected so a test drives the entire path -- the
    grace, both dedupe keys, and the recovery that re-arms them -- with no
    socket and no phone. The defaults are the production wiring.
    """

    def __init__(self, fetch=None, send=None, url=DEFAULT_URL,
                 interval=DEFAULT_INTERVAL, grace=UNREACHABLE_GRACE):
        self._fetch = fetch or (lambda: fetch_status(url))
        self._send = send
        self._interval = interval
        self._grace = grace
        self._failures = 0
        self._down_since = None
        self._notified_unreachable = None
        self._notified_silent = None

    def poll(self, now=None):
        """One check. Returns the verdict sent (`str`) or `None`.

        Never raises. This is the loop body of a program that has to be
        running when the thing it watches is not; a transient failure on his
        home network costs a check, never the process.
        """
        now = time.time() if now is None else now
        try:
            status = self._fetch()
        except Unreachable as error:
            return self._on_unreachable(str(error), now)
        except Exception as error:  # noqa: BLE001 -- see docstring
            return self._on_unreachable(f"{type(error).__name__}: {error}", now)
        return self._on_answer(status)

    def _on_unreachable(self, detail, now):
        self._failures += 1
        if self._down_since is None:
            self._down_since = now
        if self._failures < self._grace:
            return None
        # Keyed on the first failure of this outage, so the whole outage is
        # one key and one message, however long it runs.
        key = self._down_since
        if key == self._notified_unreachable:
            return None
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(key))
        if self._deliver("UNREACHABLE", unreachable_text(self._failures, when, detail)):
            self._notified_unreachable = key
            return "UNREACHABLE"
        return None

    def _on_answer(self, status):
        # The box answered, so any UNREACHABLE run is over. Clearing
        # `_down_since` is what re-arms the alarm: the next outage stamps a
        # new first-failure time, which is a key the dedupe has not seen.
        # There is deliberately no second reset of `_notified_unreachable`
        # here -- I wrote one, and mutating it away changed no test, because
        # it cannot change an outcome. A line that cannot fail is a guarantee
        # that is not there.
        self._failures = 0
        self._down_since = None
        if not status.get("stalled"):
            return None
        key = status.get("lastWrittenAt") or ""
        if not key:
            # `stall_notice.due` refuses this case for the same reason:
            # without a stamp there is nothing to dedupe on, and an
            # undedupeable alarm is one he will mute.
            return None
        if key == self._notified_silent:
            return None
        if self._deliver("SILENT", silent_text(status)):
            self._notified_silent = key
            return "SILENT"
        return None

    def _deliver(self, verdict, text):
        """True if the message actually went. A failed send is not recorded.

        Deliberate: recording a send that failed would mark this outage
        announced and go quiet for the rest of it, which is the one failure
        mode a watchdog may not have.
        """
        try:
            self._send(verdict, text)
            return True
        except Exception as error:  # noqa: BLE001
            print(f"nova-watch: sending {verdict} failed: {error!r}", flush=True)
            return False

    def run(self, sleep=time.sleep, polls=None):
        """Poll forever, or `polls` times. The whole program, once wired."""
        count = 0
        while polls is None or count < polls:
            verdict = self.poll()
            if verdict:
                print(f"nova-watch: sent {verdict}", flush=True)
            count += 1
            if polls is None or count < polls:
                sleep(self._interval)


def web_push_sender(subscription, public_key, private_key, subject):
    """A `send(verdict, text)` that rings his phone without touching the box.

    Web Push is sender-side-only: this signs a request with the VAPID keypair
    and POSTs it to the endpoint inside the subscription record, which belongs
    to Google, Apple or Mozilla. Nothing in that path goes through Hetzner —
    which is exactly why an alarm built on it can fire during the outage it
    exists for.

    `pywebpush` is imported here rather than at module scope so the decision
    logic above is importable and testable without the dependency installed.
    """
    from pywebpush import webpush

    def send(verdict, text):
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": f"Nova: {verdict}", "body": text}),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
        )

    return send


def sender_from_env(env=None):
    """Build the production sender from the environment, or explain what is missing.

    Named separately from `main` so the "have I been configured" answer is a
    return value a test can read, rather than a process that exits.
    """
    env = os.environ if env is None else env
    missing = [
        name for name in ("NOVA_WATCH_SUBSCRIPTION", "VAPID_PRIVATE_KEY")
        if not env.get(name)
    ]
    if missing:
        return None, f"not configured: {', '.join(missing)} is unset"
    try:
        subscription = json.loads(env["NOVA_WATCH_SUBSCRIPTION"])
    except ValueError as error:
        return None, f"NOVA_WATCH_SUBSCRIPTION is not JSON: {error}"
    if not subscription.get("endpoint"):
        return None, "NOVA_WATCH_SUBSCRIPTION carries no endpoint"
    return web_push_sender(
        subscription,
        env.get("VAPID_PUBLIC_KEY", ""),
        env["VAPID_PRIVATE_KEY"],
        # The `sub` claim VAPID requires. Not a real address in a public repo:
        # the push services accept any mailto or https URL the sender can be
        # contacted at, and the deployment sets its own.
        env.get("VAPID_SUBJECT", "https://agora.tailc83eb3.ts.net"),
    ), None


def main(argv=None, env=None):
    env = os.environ if env is None else env
    send, problem = sender_from_env(env)
    if problem:
        # Refuse to start rather than run a watcher that cannot speak. A
        # silent watchdog is worse than none: it looks like coverage.
        print(f"nova-watch: {problem}", flush=True)
        return 2
    url = env.get("NOVA_WATCH_URL", DEFAULT_URL)
    interval = int(env.get("NOVA_WATCH_INTERVAL", DEFAULT_INTERVAL))
    print(f"nova-watch: polling {url} every {interval}s", flush=True)
    Watch(send=send, url=url, interval=interval).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
