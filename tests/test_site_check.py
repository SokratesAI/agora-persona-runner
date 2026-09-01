"""`site_check` asks the live site whether the page a cycle shipped is there.

Every test here drives `findings` through a fake fetcher, because the
thing under test is the *judgement* -- which answers count as healthy and
which do not -- and a test that needed a live pod would only run in the
one place this tool is not the interesting part.

The one that matters most is `test_a_server_that_serves_the_shell_for_
everything_is_not_reported_healthy`. That is the failure this tool exists
to be immune to, and it is the failure a naive version has by
construction.
"""

import json

import pytest

from agora_runner import site_check
from agora_runner.nova_site import PAGE_ROUTES


def _shell():
    return b'<!doctype html><html><body><script src="/app.js"></script></body></html>'


def _payload(path):
    return json.dumps({key: [] for key in site_check.API_KEYS[path]}).encode("utf-8")


def healthy(base, path, timeout=None):
    """What a working site answers."""
    if path == site_check.ABSENT_PATH:
        return 404, b'{"error": "not found"}'
    if path in site_check.API_KEYS:
        return 200, _payload(path)
    if path in PAGE_ROUTES:
        return 200, _shell()
    raise AssertionError(f"the checker asked for an unexpected path: {path}")


def _with(overrides):
    """`healthy`, with specific paths answering something else."""

    def fetcher(base, path, timeout=None):
        if path in overrides:
            return overrides[path]
        return healthy(base, path, timeout)

    return fetcher


def test_a_healthy_site_produces_no_findings():
    assert site_check.findings(fetcher=healthy) == []


def test_a_server_that_serves_the_shell_for_everything_is_not_reported_healthy():
    """The control probe, and the reason this tool is not just a 200 check.

    This is a single-page app: every page route answers with the same
    `index.html`. So a server that answered *every* path with that shell
    would satisfy each page assertion individually while routing nothing
    -- exactly the shape of Cycle 53's negative that could never have been
    positive. The control has to come first and it has to stop the run.
    """
    fetcher = _with({site_check.ABSENT_PATH: (200, _shell())})
    problems = site_check.findings(fetcher=fetcher)
    assert len(problems) == 1
    assert site_check.ABSENT_PATH in problems[0]
    assert "no page check below can mean anything" in problems[0]


def test_an_unroutable_site_says_so_once_rather_than_failing_every_path():
    """Cycle 184's actual situation, and what it should have been told.

    A pod nobody can reach is one fact. Reporting it as eleven broken
    pages would bury it, and it is the finding most likely to be a wrong
    port rather than a broken site.
    """
    fetcher = _with({site_check.ABSENT_PATH: (None, b"ConnectionRefusedError: [Errno 111]")})
    problems = site_check.findings(base="http://nowhere:1", fetcher=fetcher)
    assert len(problems) == 1
    assert "could not reach http://nowhere:1" in problems[0]
    assert "ConnectionRefusedError" in problems[0]


def test_a_page_route_that_stopped_being_routed_is_found():
    fetcher = _with({"/retro": (404, b'{"error": "not found"}')})
    assert site_check.findings(fetcher=fetcher) == ["/retro: 404, expected 200"]


def test_a_page_that_answers_200_without_loading_the_client_is_found():
    """A 200 carrying something that is not the app is still a broken page."""
    fetcher = _with({"/costs": (200, b"<!doctype html><html><body>maintenance</body></html>")})
    problems = site_check.findings(fetcher=fetcher)
    assert problems == ["/costs: 200 but the body does not load /app.js"]


def test_an_api_that_answers_200_with_a_key_missing_is_found():
    """The failure a status-code check cannot see: a blank feed.

    `/api/journal` losing `entries` is a 200 with valid JSON and an empty
    app, which is indistinguishable from a healthy site to anything that
    only reads the status line.
    """
    fetcher = _with({"/api/journal": (200, json.dumps({"status": {}}).encode())})
    problems = site_check.findings(fetcher=fetcher)
    assert problems == ["/api/journal: 200 but missing entries"]


def test_an_api_that_502s_is_found():
    fetcher = _with({"/api/digest": (502, b'{"error": "vault unreadable"}')})
    assert site_check.findings(fetcher=fetcher) == ["/api/digest: 502, expected 200"]


def test_an_api_returning_html_is_reported_as_not_json():
    fetcher = _with({"/api/costs": (200, b"<html>oops</html>")})
    problems = site_check.findings(fetcher=fetcher)
    assert len(problems) == 1
    assert problems[0].startswith("/api/costs: 200 but the body is not JSON")


def test_an_api_returning_a_list_is_reported_rather_than_crashing():
    """`json.loads` succeeds on `[]`, and `"entries" in []` is a TypeError.

    A checker that raised here would take the whole run down over one bad
    endpoint, which is the opposite of what a smoke check is for.
    """
    fetcher = _with({"/api/retro": (200, b"[]")})
    problems = site_check.findings(fetcher=fetcher)
    assert problems == ["/api/retro: 200 but the payload is list, not an object"]


def test_every_page_route_the_server_registers_is_checked():
    """The list is read from `nova_site`, not copied.

    A hand-copied route list is a check that silently stops covering the
    server the first time someone adds a page to one copy and not the
    other -- and it would report success while doing it. Adding a route to
    `nova_site.PAGE_ROUTES` must be enough to make this tool test it.
    """
    asked = []

    def recording(base, path, timeout=None):
        asked.append(path)
        return healthy(base, path, timeout)

    site_check.findings(fetcher=recording)
    for route in PAGE_ROUTES:
        assert route in asked


def test_main_prints_each_problem_and_exits_nonzero(monkeypatch, capsys):
    monkeypatch.setattr(site_check, "findings", lambda base: ["a", "b"])
    assert site_check.main([]) == 1
    assert capsys.readouterr().out == "a\nb\n"


def test_main_is_silent_and_exits_zero_when_healthy(monkeypatch, capsys):
    """Same contract as `cycle_health`: nothing to say means nothing said."""
    monkeypatch.setattr(site_check, "findings", lambda base: [])
    assert site_check.main([]) == 0
    assert capsys.readouterr().out == ""


def test_the_base_url_comes_from_the_argument_then_the_environment(monkeypatch):
    seen = []
    monkeypatch.setattr(site_check, "findings", lambda base: seen.append(base) or [])
    monkeypatch.setenv("NOVA_SITE_URL", "http://from-env:9")
    site_check.main([])
    site_check.main(["http://from-arg:9"])
    monkeypatch.delenv("NOVA_SITE_URL")
    site_check.main([])
    assert seen == ["http://from-env:9", "http://from-arg:9", site_check.DEFAULT_BASE]


@pytest.mark.parametrize("path", sorted(site_check.API_KEYS))
def test_every_checked_api_path_is_one_the_server_answers(path):
    """A typo in `API_KEYS` would report a permanently broken endpoint.

    The server 404s anything it does not route, so a misspelled path here
    fails forever and reads as a broken site rather than a broken check.
    """
    source = (
        __import__("pathlib").Path(site_check.__file__).parent / "nova_site.py"
    ).read_text()
    assert f'path == "{path}"' in source


def test_the_digest_keys_are_ones_the_payload_still_carries():
    """A key deleted from the payload and left here is a permanent alarm.

    The sibling above catches a misspelled *path*; nothing caught a stale
    *key*, and `_payload` above cannot -- it builds its fixture out of
    `API_KEYS`, so a healthy site is whatever `API_KEYS` says it is and the
    assertion compares the check to itself. #236 deleted `needsEdvard` from  (not-prose: an identifier)
    `digest_payload` and left it listed here, which would have reported
    `/api/digest: 200 but missing needsEdvard` on every deploy from then on  (not-prose: an identifier)
    -- this module's own failure mode, aimed at this module.

    So this asserts against the real builder rather than against the list.
    `parse_digest` is pure, so a fixture digest is enough to name the keys.
    """
    from agora_runner import nova_site
    from unittest.mock import patch

    fixture = (
        "# Journal — Digest\n\n## Next cycle\n\n1. **[thing]** do it\n\n"
        "## Digest\n\n**Cycle 1** (2026-08-17 03:00) — a line.\n"
    )
    with patch.object(nova_site, "digest_markdown", return_value=fixture):
        served = set(nova_site.digest_payload())
    missing = set(site_check.API_KEYS["/api/digest"]) - served
    assert not missing, f"site_check requires keys /api/digest no longer sends: {missing}"
