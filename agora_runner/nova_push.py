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

from agora_runner.http_util import agora_get, agora_public


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
