"""The Heartbeats page: every Agora schedule, readable and switchable from Nova.

His capture, `ideas.md` 2026-08-25: *"Make a page for heartbeats and also
maybe make a page for conversations ... Then Agora can just be purely for
heartbeats."* Cycle 439 answered the bigger half -- don't merge the two apps,
make Nova the only front end and leave Agora underneath as the engine --
Cycle 441 built the conversations half, and this is the other one. After this
there is nothing left on Agora's own app that he opens it for day to day.

**The writes go to Agora's public app, not its internal one**, and that is
the opposite of `nova_conversations.send`. The internal API on :8081 accepts
only `lastRunAt`, `lastResult`, `forceRun` and `conversationId` -- it is the
runner's own bookkeeping surface -- so `enabled` is reachable only on the
public app on :8080, which carries no token guard and is the exact pair of
calls Agora's own page makes (`public/app.js`, `PATCH /heartbeats/:id` with
`{enabled}` and `POST /heartbeats/:id/run`). Measured live against :8080,
Cycle 443: a `PATCH` answered 200 with the heartbeat back.

**Deliberately not here: create, delete, and editing the task or schedule.**
Those are the four that can silently stop this loop -- a mistyped schedule or
a deleted row takes Nova off the air with nothing to notice it -- and none of
them is something he does from his phone. Switching one off and pressing run
are. Agora's own page keeps all four.
"""

from agora_runner.config import AGORA_URL
from agora_runner.http_util import agora_get, http_json
from agora_runner.log import log
from agora_runner import nova_conversations


# A cycle thread is tagged `evolve-cycle:<heartbeatId>` by the runner when
# it opens the conversation, which is the only link between a heartbeat and
# the threads it has run. `nova_conversations` already reads that prefix to
# set `cycleThread`; this reads the id after it.
CYCLE_TAG = "evolve-cycle:"


# The task text is what a heartbeat actually tells its persona to do, and
# some of them are 1,500 characters of prompt. The page shows it, so the
# whole thing is passed through rather than trimmed here: a cap in the
# reader is a decision no interface can undo, and folding it behind a tap
# is the interface's job.


def _persona_names():
    """id -> name, so a row can say who runs rather than a UUID.

    Returns an empty map rather than raising if the persona listing fails:
    a heartbeat page with unnamed personas is still the page he asked for,
    while a page that refuses to load because a *second* fetch failed is
    not. The listing itself is the one that must raise.
    """
    try:
        status, body = agora_get("/personas")
    except Exception as e:
        log(f"nova_heartbeats: persona listing raised {e}")
        return {}
    if status != 200:
        log(f"nova_heartbeats: persona listing returned {status}")
        return {}
    return {p.get("id"): (p.get("name") or "") for p in body.get("personas", []) if p.get("id")}


def _threads_by_heartbeat():
    """heartbeatId -> its conversations, newest first.

    His capture, `ideas.md` 2026-08-26: *"The heartbeat conversations should
    rather somehow be listed in the beats page as they belong there. Somehow
    underneath their relative heartbeat and as a dropdown drawer so they are
    not shown unless i want to see them."* The grouping is done here rather
    than in the page because the tag prefix is a rule about how the runner
    names a thread, and `nova_conversations` is already the one module that
    knows it.

    Swallows a failure for `_persona_names`' reason, and it matters more
    here: `conversations()` raises on a bad fetch, and letting that through
    would turn a working heartbeats page into a 502 because a *second*
    listing failed. No drawers is a worse page; no page is a broken one.
    """
    try:
        rows = nova_conversations.conversations()["conversations"]
    except Exception as e:
        log(f"nova_heartbeats: conversation listing raised {e}")
        return {}
    by_hb = {}
    for c in rows:
        for tag in c.get("tags") or []:
            if str(tag).startswith(CYCLE_TAG):
                hid = str(tag)[len(CYCLE_TAG):]
                if hid:
                    by_hb.setdefault(hid, []).append(c)
                break
    # `conversations()` already sorted newest activity first and this only
    # partitions that list, so each bucket keeps the order it arrived in.
    return by_hb


def heartbeats():
    """Every heartbeat Agora holds, the enabled ones first.

    Raises rather than returning an empty list on a failed fetch, for
    `nova_conversations.conversations`' reason: "no heartbeats" and "the
    store is unreachable" render identically and mean opposite things. The
    route turns this into a 502 he can read.
    """
    status, body = agora_get("/heartbeats")
    if status != 200:
        raise RuntimeError(f"heartbeat listing returned {status}")
    names = _persona_names()
    threads = _threads_by_heartbeat()
    rows = []
    for h in body.get("heartbeats", []):
        hid = h.get("id")
        if not hid:
            continue
        rows.append({
            "id": hid,
            "name": h.get("name") or "(unnamed)",
            "personaId": h.get("personaId") or "",
            "personaName": nova_conversations.visible_persona_name(
                h.get("personaId"), names.get(h.get("personaId"))),
            "conversationId": h.get("conversationId") or "",
            "schedule": h.get("schedule") or "",
            "task": h.get("task") or "",
            "enabled": bool(h.get("enabled")),
            # `forceRun` is Agora's flag that a run has been asked for and
            # not yet picked up. The page shows it as "queued" so a press
            # that cannot start until the current cycle ends looks like
            # something happened, which is Agora's own wording.
            "forceRun": bool(h.get("forceRun")),
            "lastRunAt": h.get("lastRunAt") or "",
            "lastResult": h.get("lastResult") or "",
            # The runner writes `lastResult` "running" when it starts and
            # overwrites it on every terminal path, so this is the one
            # honest "is a cycle in flight" signal -- Agora's own
            # `/heartbeats/:id/run` route says so in its comment.
            "running": (h.get("lastResult") or "") == "running",
            # Every thread this heartbeat has run, newest first, so the page
            # can fold them under the card they belong to. The heartbeat's
            # own current thread is included when the tag did not catch it:
            # the retrospective and research heartbeats open untagged
            # conversations, so without this their drawer would be empty
            # while `conversationId` names a live thread.
            "conversations": _with_current(
                threads.get(hid, []), h.get("conversationId") or ""),
        })
    # Enabled first, then newest run first inside each group. A disabled
    # heartbeat is history and an enabled one is the machine he is looking
    # at; sorting them together buries the live ones among the retired.
    #
    # Two stable passes rather than one composite key, because the two
    # halves sort in opposite directions and Python has one `reverse`.
    # The first version inverted the timestamp per character instead --
    # which is wrong on the one pair it looked right on: `isoformat()`
    # omits the microseconds when they are exactly zero, so a run landing
    # on a whole second is `...T20:40:06+00:00` against a neighbour's
    # `...T20:40:06.089807+00:00`, and `-ord('+')` beats `-ord('.')`, so
    # the earlier of the two sorted first. Reviewer found it.
    #
    # An ISO-8601 string from Agora, so a plain reverse sort is
    # chronological, and `""` -- a heartbeat that has never run -- is
    # less than every real stamp, so it lands last under `reverse=True`
    # without a flag of its own.
    rows.sort(key=lambda r: r["lastRunAt"], reverse=True)
    rows.sort(key=lambda r: not r["enabled"])
    return {"heartbeats": rows}


def _with_current(rows, conversation_id):
    """`rows`, plus the heartbeat's current thread if it is not already in them.

    Prepended, not appended: the list is newest first, and the thread Agora
    currently points the heartbeat at is by definition the newest one.
    """
    if not conversation_id:
        return rows
    if any(r.get("id") == conversation_id for r in rows):
        return rows
    return [{
        "id": conversation_id,
        # Deliberately blank rather than "Current thread": the page falls back
        # to the heartbeat's own name when a thread has none, and a truthy
        # placeholder here defeats that fallback and titles the opened thread
        # "Current thread" -- which is six of the seven live heartbeats,
        # since only the hourly one rotates tagged conversations. Reviewer
        # measured that against the live listing. The label the drawer shows
        # is the page's business; this is the name of a thread we did not
        # fetch, and the honest value for that is empty.
        "name": "",
        "personaName": "",
        "model": "",
        "tags": [],
        "updatedAt": "",
        "cycleThread": False,
    }] + rows


def set_enabled(heartbeat_id, enabled):
    """(ok, message). Switch one heartbeat on or off."""
    if not isinstance(heartbeat_id, str) or not heartbeat_id.strip():
        return False, "which heartbeat?"
    if not isinstance(enabled, bool):
        return False, "enabled must be true or false"
    status, body = http_json(
        "PATCH", f"{AGORA_URL}/heartbeats/{heartbeat_id.strip()}", {"enabled": enabled})
    if status == 404:
        return False, "no heartbeat with that id"
    if status != 200:
        log(f"nova_heartbeats: enable {enabled} failed HTTP {status}")
        return False, "could not change the heartbeat"
    return True, "on" if (body.get("heartbeat") or {}).get("enabled") else "off"


def run_now(heartbeat_id):
    """(ok, message). Ask for a run at the next poll.

    Agora sets `forceRun` and the runner picks it up on its next tick. It
    does not start a second run of a heartbeat already in flight, so the
    honest word for what this does is "queued" rather than "started" --
    pressing it during a cycle means the run happens when that cycle ends,
    which can be most of an hour later.
    """
    if not isinstance(heartbeat_id, str) or not heartbeat_id.strip():
        return False, "which heartbeat?"
    status, _ = http_json(
        "POST", f"{AGORA_URL}/heartbeats/{heartbeat_id.strip()}/run", {})
    if status == 404:
        return False, "no heartbeat with that id"
    if status not in (200, 201, 202):
        log(f"nova_heartbeats: run now failed HTTP {status}")
        return False, "could not queue the run"
    return True, "queued"
