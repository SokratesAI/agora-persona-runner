"""The Nova app registering its own push subscription (issues.md #119).

The bug these guard against is not a crash -- it is a silent one. Before
this, `agora/public/app.js` was the only code in either repo that called
`pushManager.subscribe`, so every notification the owner received went to
a subscription his Agora app had made, and nothing would have reported
the day that stopped being true.
"""

from unittest.mock import patch

from agora_runner import nova_push


def test_vapid_key_passes_the_public_key_through():
    with patch.object(nova_push, "agora_get", return_value=(200, {"publicKey": "BFakeKey"})):
        assert nova_push.vapid_key() == {"publicKey": "BFakeKey"}


def test_vapid_key_is_empty_when_agora_has_no_keys():
    # Agora answers 200 with no key when VAPID is unconfigured; the page
    # must read that as "do not subscribe", not as a key of `None`.
    with patch.object(nova_push, "agora_get", return_value=(200, {})):
        assert nova_push.vapid_key() == {}


def test_vapid_key_is_empty_when_agora_is_unreachable():
    with patch.object(nova_push, "agora_get", return_value=(503, None)):
        assert nova_push.vapid_key() == {}


def test_store_subscription_posts_the_body_to_agora():
    seen = {}

    def fake(method, path, payload=None):
        seen["call"] = (method, path, payload)
        return 201, {}

    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "x", "auth": "y"}}
    with patch.object(nova_push, "agora_public", side_effect=fake):
        ok, body = nova_push.store_subscription(sub)
    assert ok is True
    assert body == {"ok": True}
    # Whole body, unmodified: web-push needs `keys` as well as `endpoint`,
    # and a subscription missing them is accepted here and undeliverable
    # later, which is exactly the silent failure this file is about.
    assert seen["call"] == ("POST", "/subscribe", sub)


def test_store_subscription_refuses_a_body_with_no_endpoint():
    called = []
    with patch.object(nova_push, "agora_public", side_effect=lambda *a, **k: called.append(a)):
        ok, body = nova_push.store_subscription({"keys": {}})
    assert ok is False
    assert "endpoint" in body["error"]
    assert called == []


def test_store_subscription_reports_an_agora_failure():
    with patch.object(nova_push, "agora_public", return_value=(500, {})):
        ok, body = nova_push.store_subscription({"endpoint": "https://example.invalid/x"})
    assert ok is False
    assert "500" in body["error"]


def test_send_posts_title_body_and_url_to_agora():
    seen = {}

    def fake(method, path, payload=None):
        seen.update({"method": method, "path": path, "payload": payload})
        return 200, {"status": "sent"}

    with patch.object(nova_push, "agora_internal", fake):
        assert nova_push.send("answered your comment", url="/cycle/1446") == (True, "sent")
    assert seen["method"] == "POST"
    assert seen["path"] == "/push"
    assert seen["payload"] == {
        "title": "Nova", "body": "answered your comment", "url": "/cycle/1446"}


def test_send_leaves_url_out_when_there_is_none():
    # An absent key is what an older service worker falls back to `/` on; a
    # literal null would be a value it has to reject rather than a key it can
    # miss. Both the empty string and no argument at all mean absent.
    seen = {}

    def fake(method, path, payload=None):
        seen.update(payload or {})
        return 200, {"status": "sent"}

    with patch.object(nova_push, "agora_internal", fake):
        nova_push.send("hi")
        assert "url" not in seen
        nova_push.send("hi", url="")
        assert "url" not in seen


def test_send_refuses_empty_text_without_calling_agora():
    called = []

    def fake(method, path, payload=None):
        called.append(path)
        return 200, {"status": "sent"}

    with patch.object(nova_push, "agora_internal", fake):
        assert nova_push.send("")[0] is False
        assert nova_push.send("   ")[0] is False
        assert nova_push.send(None)[0] is False
    assert called == []


def test_send_reports_a_withheld_push_as_a_success():
    # Quiet hours. He asked not to be buzzed, so nothing failed -- and a caller
    # that logged this as a failure would send someone looking for a bug.
    with patch.object(nova_push, "agora_internal",
                      return_value=(200, {"status": "withheld", "quietHours": True})):
        assert nova_push.send("hi") == (True, "withheld")


def test_send_reports_no_subscription_as_a_failure():
    with patch.object(nova_push, "agora_internal", return_value=(404, {"error": "none"})):
        ok, detail = nova_push.send("hi")
    assert ok is False
    assert "404" in detail
