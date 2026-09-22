"""The preflight check that watches whether Marcus's chat still reaches a model.

The exit code is the whole product of this module, so every test asserts on
it. `SUBSCRIPTION_PREFIX` and the two env-var names are imported rather than
re-spelled, so a test cannot keep passing against a constant the module has
moved away from.

The failure this file is really built around is the one `prompt.md` names: a
negative result guaranteed in advance, and its mirror. "The coach is fine" and
"I could not tell" look identical from outside and mean opposite things -- an
unreadable Deployment, an unreachable Agora, a non-200 and a body that is not
a listing each get their own test asserting exit 1, and none of them is
allowed to collapse into either the clean path or the fallback verdict.
"""

import json
import subprocess
import urllib.error

import pytest

from tools.marcus_coach_health import (
    BASE_URL_VAR,
    CONVERSATION_VAR,
    SUBSCRIPTION_PREFIX,
    main,
    read_coach_config,
    read_listing,
    report,
)

CONV = "cc484b5a-ad53-420e-93f1-5efacdfb4760"
BASE = "http://agora.agents.svc.cluster.local:8080"


def _deployment(env):
    return json.dumps({"spec": {"template": {"spec": {"containers": [
        {"name": "marcus", "env": [{"name": k, "value": v} for k, v in env.items()]},
    ]}}}})


class _Run:
    """The slice of `subprocess.run`'s result this module actually uses."""

    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _runner(result):
    def run(cmd, **kwargs):
        run.cmd = cmd
        if isinstance(result, Exception):
            raise result
        return result
    return run


class _Response:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _opener(result):
    def opener(url, timeout=None):
        opener.url = url
        if isinstance(result, Exception):
            raise result
        return result
    return opener


def _lines(fn, *args):
    out = []
    code = fn(*args, out=out.append)
    return code, "\n".join(out)


# --- read_coach_config -------------------------------------------------

def test_config_comes_off_the_running_deployment():
    run = _runner(_Run(_deployment({BASE_URL_VAR: BASE, CONVERSATION_VAR: CONV})))
    assert read_coach_config(run=run) == (BASE, CONV)
    assert run.cmd[:3] == ["kubectl", "get", "deployment"]


@pytest.mark.parametrize("result", [
    _Run("", returncode=1),
    _Run("not json at all"),
    _Run("[]"),
    OSError("kubectl: not found"),
    subprocess.TimeoutExpired("kubectl", 15),
])
def test_an_unreadable_deployment_is_none_not_an_empty_config(result):
    # None and ("", "") are different answers: one is "I could not look", the
    # other is "I looked and the coach is switched off". Collapsing them would
    # report a broken instrument as a broken feature.
    assert read_coach_config(run=_runner(result)) is None


def test_a_nonzero_exit_is_none_even_when_stdout_parses():
    # The discriminating case for the exit-code guard: kubectl can exit
    # non-zero having still printed something JSON-shaped, and a partial
    # answer is not an answer. Without this, every "unreadable" case above is
    # carried by `json.loads` raising instead, and the guard is untested.
    result = _Run(_deployment({BASE_URL_VAR: BASE, CONVERSATION_VAR: CONV}), returncode=1)
    assert read_coach_config(run=_runner(result)) is None


def test_a_deployment_without_the_vars_reads_as_empty_strings():
    assert read_coach_config(run=_runner(_Run(_deployment({"PORT": "8080"})))) == ("", "")


# --- read_listing ------------------------------------------------------

def test_listing_asks_for_the_active_filter_only():
    rows = [{"id": CONV}]
    opener = _opener(_Response(json.dumps(rows).encode()))
    assert read_listing(BASE, opener=opener) == rows
    assert opener.url.full_url == f"{BASE}/conversations?active=true"


def test_the_listing_carries_the_agent_token(monkeypatch):
    """Agora is to refuse an untokened read on :8080 (issue #287), and this
    reads the coach's listing there from the bridge pod."""
    from agora_runner import http_util
    monkeypatch.setattr(http_util, "AGORA_TOKEN", "tok")
    opener = _opener(_Response(b"[]"))
    read_listing(BASE, opener=opener)
    assert opener.url.get_header("X-agora-token") == "tok"


def test_listing_accepts_the_wrapped_shape():
    body = json.dumps({"conversations": [{"id": CONV}]}).encode()
    assert read_listing(BASE, opener=_opener(_Response(body))) == [{"id": CONV}]


@pytest.mark.parametrize("result", [
    _Response(b"[]", status=500),
    _Response(b"<html>proxy error</html>"),
    _Response(b""),
    _Response(json.dumps({"conversations": "nope"}).encode()),
    urllib.error.URLError("connection refused"),
])
def test_an_unreadable_listing_is_none_not_an_empty_listing(result):
    # An empty list is a real answer meaning "the coach conversation is gone";
    # None means "I could not look". A proxy's HTML error page must not be
    # read as the former.
    assert read_listing(BASE, opener=_opener(result)) is None


# --- report ------------------------------------------------------------

def test_a_listed_subscription_conversation_is_clean():
    rows = [{"id": CONV, "model": f"{SUBSCRIPTION_PREFIX}claude-sonnet-5"}]
    code, text = _lines(report, (BASE, CONV), rows)
    assert code == 0
    assert f"{SUBSCRIPTION_PREFIX}claude-sonnet-5" in text
    assert "NOT JUDGED" in text


def test_an_unreadable_deployment_exits_one():
    code, text = _lines(report, None, None)
    assert code == 1
    assert "CANNOT SEE" in text


def test_an_unreadable_listing_exits_one_rather_than_naming_a_fallback():
    code, text = _lines(report, (BASE, CONV), None)
    assert code == 1
    assert "CANNOT SEE" in text
    assert "FALLBACK" not in text


@pytest.mark.parametrize("config, missing", [
    (("", CONV), BASE_URL_VAR),
    ((BASE, ""), CONVERSATION_VAR),
    (("", ""), CONVERSATION_VAR),
])
def test_an_unconfigured_coach_is_the_fallback(config, missing):
    code, text = _lines(report, config, None)
    assert code == 2
    assert missing in text


def test_a_conversation_missing_from_the_listing_is_the_fallback():
    # The live failure cycle 1468 found: archived, so absent from ?active=true.
    code, text = _lines(report, (BASE, CONV), [{"id": "some-other-id", "model": "x"}])
    assert code == 2
    assert "archived" in text
    assert f"/conversations/{CONV}" in text


@pytest.mark.parametrize("model", [
    "anthropic:claude-sonnet-5",
    "openai:gpt-4",
    "",
    None,
    123,
])
def test_a_model_outside_the_subscription_provider_is_the_fallback(model):
    code, text = _lines(report, (BASE, CONV), [{"id": CONV, "model": model}])
    assert code == 2
    assert SUBSCRIPTION_PREFIX in text


def test_a_row_that_is_not_an_object_does_not_match_the_conversation():
    code, _ = _lines(report, (BASE, CONV), ["not-a-row", None])
    assert code == 2


# --- main --------------------------------------------------------------

def test_main_does_not_ask_agora_when_the_coach_is_unconfigured(monkeypatch):
    # Reaching for the listing with no conversation id would print an address
    # the verdict does not depend on.
    monkeypatch.setattr("tools.marcus_coach_health.read_coach_config",
                        lambda *a, **k: ("", ""))
    def refuse(*a, **k):
        raise AssertionError("read_listing must not run without a config")
    monkeypatch.setattr("tools.marcus_coach_health.read_listing", refuse)
    assert main([]) == 2


def test_main_wires_the_deployment_flags_through(monkeypatch, capsys):
    seen = {}

    def fake_config(deployment, namespace):
        seen["args"] = (deployment, namespace)
        return (BASE, CONV)

    monkeypatch.setattr("tools.marcus_coach_health.read_coach_config", fake_config)
    monkeypatch.setattr("tools.marcus_coach_health.read_listing",
                        lambda url: [{"id": CONV, "model": f"{SUBSCRIPTION_PREFIX}x"}])
    assert main(["--deployment", "marcus-2", "--namespace", "test"]) == 0
    assert seen["args"] == ("marcus-2", "test")
    capsys.readouterr()
