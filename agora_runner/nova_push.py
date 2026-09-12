"""Push subscription for the Nova app itself.

Until now `agora/public/app.js` was the only code in either repo that
called `pushManager.subscribe`, so every notification that reached
the owner's phone -- including every cycle reply -- was delivered to a
subscription his *Agora* PWA had created. The owner has said he will never open
Agora again (issues.md #119), and nothing would have told either of us
when that channel went quiet. See
`nova/resources/research/agora-decision-2026-08-28.md`.

This is deliberately a proxy, not a second push service. Agora keeps the
VAPID keypair, the subscription store and the web-push sender; Nova only
needs its own origin's subscription to end up in that same store, because
a subscription belongs to the origin that created it and Nova's pages are
served from a different host than Agora's.
"""

from agora_runner.http_util import agora_get, agora_internal, agora_public


def vapid_key():
    """`{"publicKey": ...}` for the browser, or `{}` when Agora has no keys.

    A missing key is not an error worth a 500 on this site: Agora itself
    logs a warning and reports not-ready when VAPID is unconfigured, and
    the caller in `app.js` simply does not subscribe.
    """
    status, body = agora_get("/vapid-public-key")
    if status != 200 or not isinstance(body, dict):
        return {}
    key = body.get("publicKey")
    return {"publicKey": key} if isinstance(key, str) and key else {}


def store_subscription(payload):
    """Hand a `PushSubscription.toJSON()` to Agora's subscription store.

    Answers `{"ok": bool}` rather than the upstream body, which is empty.
    """
    if not isinstance(payload, dict) or not payload.get("endpoint"):
        return False, {"error": "a subscription with an endpoint is required"}
    status, _body = agora_public("POST", "/subscribe", payload)
    if status not in (200, 201):
        return False, {"error": f"agora /subscribe answered {status}"}
    return True, {"ok": True}


def send(body, url=None, title="Nova"):
    """Buzz his phone about something that is not a conversation.

    Every other notification this loop sends is a message in a thread, and
    Agora's `/conversations/:id/notify` pushes it as a side effect of appending
    it. A reply to a comment on a journal card has no thread: it is stored in
    the vault and drawn on the card, so until now the only way to find out one
    had arrived was to open the app and look. That is the half of `ideas.md`
    #182 the chat-bubble dot did not cover -- *"I'm not able to read all the
    'waiting on you' and all the answers for the journals."*

    `url` is a path on this site that the tap should land on, e.g.
    `/cycle/1446`. Agora's `POST /push` leaves it out of the payload entirely
    when it is empty, and Nova's service worker falls back to `/`.

    Returns `(ok, detail)` and never raises. A notification is the last thing
    to happen on a path that has already done the useful work -- the reply is
    stored either way -- so every way this can fail has to leave the caller
    alone. `withheld` is a success: quiet hours mean he asked not to be buzzed.
    """
    if not isinstance(body, str) or not body.strip():
        return False, "a push needs some text"
    payload = {"title": title, "body": body.strip()}
    if isinstance(url, str) and url:
        payload["url"] = url
    status, response = agora_internal("POST", "/push", payload)
    if status != 200:
        return False, f"agora /push answered {status}"
    state = response.get("status") if isinstance(response, dict) else None
    return True, state or "sent"
