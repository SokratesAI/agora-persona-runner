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
    rows = []
    for h in body.get("heartbeats", []):
        hid = h.get("id")
        if not hid:
            continue
        rows.append({
            "id": hid,
            "name": h.get("name") or "(unnamed)",
            "personaId": h.get("personaId") or "",
            "personaName": names.get(h.get("personaId")) or "",
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
        })
    # Enabled first, then newest run first inside each group. A disabled
    # heartbeat is history and an enabled one is the machine he is looking
    # at; sorting them together buries the live ones among the retired.
    # An ISO-8601 string from Agora, so a plain reverse sort is
    # chronological, and a row that has never run sorts last within its
    # group rather than first.
    rows.sort(key=lambda r: (not r["enabled"], r["lastRunAt"] == "",
                             _reverse_key(r["lastRunAt"])))
    return {"heartbeats": rows}


def _reverse_key(stamp):
    """Sort key that puts the newest ISO stamp first inside a tuple sort.

    `sort(reverse=True)` is not available here because the enabled flag
    ahead of it sorts the other way, so the timestamp is inverted instead
    of the whole key.
    """
    return tuple(-ord(c) for c in stamp)


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
