"""A demo has to live on an origin Nova's own PWA does not claim.

The owner's note, 2026-08-31: a demo served under `nova.tailc83eb3.ts.net`
could not be installed as its own Android app. Nova's
`manifest.webmanifest` declares `scope: "/"`, the installed Nova WebAPK
registers the whole origin, and Chrome opens the demo's URL in Nova
instead of offering to install it.

These tests do not re-spell `PUBLIC_BASE`. They read Nova's real manifest
and Nova's real site URL and assert the relationship between them and the
demo base -- so if someone later narrows Nova's scope, or moves the site,
the tests follow the facts rather than a copy of the answer.
"""

import json
import pathlib
import re
from urllib.parse import urlsplit

from agora_runner.nova_demos import PUBLIC_BASE

ROOT = pathlib.Path(__file__).resolve().parent.parent


def nova_manifest():
    return json.loads((ROOT / "agora_runner" / "nova_public" / "manifest.webmanifest").read_text())


def nova_site_origin():
    """Where Nova's own site is served, read from the off-box watcher.

    That module's `DEFAULT_URL` is an independent statement of the same
    fact -- it is the URL a machine outside the cluster polls to see
    whether Nova is alive -- so using it here means the test breaks if the
    site moves, rather than agreeing with a constant copied beside it.
    """
    text = (ROOT / "offbox" / "nova_watch.py").read_text()
    m = re.search(r'DEFAULT_URL = "(https://[^"]+)"', text)
    assert m, "nova_watch.py no longer states the site URL this test reads"
    parts = urlsplit(m.group(1))
    return f"{parts.scheme}://{parts.netloc}"


def test_nova_scope_would_swallow_a_demo_path():
    """The premise. If this stops holding, the whole fix is unnecessary."""
    scope = nova_manifest()["scope"]
    assert "/demo/installable/".startswith(scope), (
        f"Nova's manifest scope is {scope!r}, which no longer covers /demo/ -- "
        "a demo could live back on Nova's own origin"
    )


def test_demo_base_is_not_on_novas_own_origin():
    assert urlsplit(PUBLIC_BASE).scheme == "https", (
        "a service worker needs a secure context, and without one the demo "
        "cannot be installed however the origins are arranged"
    )
    demo_origin = f"{urlsplit(PUBLIC_BASE).scheme}://{urlsplit(PUBLIC_BASE).netloc}"
    assert demo_origin != nova_site_origin(), (
        f"demos are served from {demo_origin}, the same origin as Nova's own "
        f"PWA, whose manifest scope is {nova_manifest()['scope']!r} -- an "
        "installed Nova WebAPK captures every demo URL there"
    )


def test_demo_base_is_a_tailnet_host_edvard_can_reach():
    """Different origin is necessary and not sufficient: it still has to be
    a host on his tailnet, since that is the only network his phone and the
    cluster share."""
    assert urlsplit(PUBLIC_BASE).netloc.endswith(".ts.net"), (
        f"{PUBLIC_BASE} is not a tailnet host, so his phone cannot open it"
    )
