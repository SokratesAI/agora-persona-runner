"""
Tests for the agora_runner package (moved here 2026-07-29 from agora-config's
persona-runner.yaml embedded ConfigMap script -- the owner's explicit ask to
migrate off a single giant embedded script onto a real repo + built image +
real modules, both for editability and because a monolithic YAML-embedded
script was a bad substrate for the planned self-evolution loop to work
against). `runner` below is the package's flat re-export facade
(agora_runner/__init__.py) so every test written against the old single-file
`runner.X` shape keeps working unchanged -- new code should import from the
specific submodule that actually owns a name instead.

Covers the three Issues.md bugs fixed 2026-07-22:
  1. Gemini web search never actually ran (save_memory's always-on function
     declaration silently killed google_search on every request). Revisited
     2026-07-23 twice: first, google_search's free-tier quota turned out to
     be zero/near-zero regardless (confirmed live -- every model 429s the
     instant google_search is in the request, succeeds instantly without
     it), so both providers' server-side/hosted search tools were replaced
     with one shared client tool (web_search). The first replacement
     implementation (a no-key DuckDuckGo HTML scrape) got anti-bot-blocked
     on the very first live query, so it was replaced again with
     web_search_tinyfish -- a real documented JSON API, free tier, no
     scraping.
  2. No fallback when a Gemini model returns 429 -- now cascades down
     GEMINI_FALLBACK_CHAIN and notes the substitution in the reply text.
  3. New kubectl_read / github_read tools -- allowlisted verbs/subcommands,
     Secrets categorically refused, tools shared between both providers via
     the existing client_tool_schemas() plumbing. Live-verified working
     (RBAC read access + repo-read-token auth both confirmed in-cluster).
"""

import contextlib
import base64
import io
import json
import os
import signal
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import sys

import agora_runner
import agora_runner.log
import agora_runner.audit
# agora_runner/__init__.py's flat re-export shadows these two submodules'
# own attribute slot on the package with the like-named function each one's
# main export happens to share (agora_runner.log ends up being the log()
# function, not the log module, since both are named "log" -- same for
# audit). `import agora_runner.log as x` still reads through that shadowed
# attribute (it's sugar for "import agora_runner.log; x = agora_runner.log"),
# so the only way to reach the real submodule object is straight out of
# sys.modules, bypassing the parent package's attribute entirely. Needed to
# patch something THEY call internally (as opposed to calling log()/audit()
# themselves, which every other test still does via `runner.`).
log_module = sys.modules["agora_runner.log"]
audit_module = sys.modules["agora_runner.audit"]


def _url_targets_model(url: str, model_id: str) -> bool:
    """Exact match on the /models/{id}:generateContent segment — a bare
    substring check would false-positive on prefix collisions like
    'gemini-3.5-flash' inside 'gemini-3.5-flash-lite'."""
    return f"/models/{model_id}:generateContent" in url


@pytest.fixture(scope="module")
def runner():
    return agora_runner


# ---------------------------------------------------------------------------
# Issue #1 — Gemini web search
# ---------------------------------------------------------------------------

def test_gemini_never_sends_google_search(runner):
    """2026-07-23: google_search's free-tier quota is zero/near-zero
    (confirmed live -- every model 429s the instant google_search is in the
    request, succeeds instantly without it). Gemini must never send it,
    regardless of the webSearch capability -- that capability now maps to
    the shared web_search client tool instead."""
    caps = {"webSearch": True, "vaultRead": False, "vaultWrite": False, "codeExecution": False}
    calls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls.append(body)
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        runner.gemini_generate(
            "gemini-flash-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert calls, "no request was sent"
    for body in calls:
        for tool in body.get("tools", []):
            assert "google_search" not in tool

    # webSearch=True must instead surface the shared client tool.
    sent_tool_names = {
        name
        for body in calls
        for tool in body.get("tools", [])
        for name in [t["name"] for t in tool.get("function_declarations", [])]
    }
    assert "web_search" in sent_tool_names


def test_web_search_in_client_tool_schemas_when_capability_on(runner):
    caps = {"webSearch": True, "vaultRead": False, "vaultWrite": False, "codeExecution": False}
    names = {t["name"] for t in runner.client_tool_schemas(caps)}
    assert "web_search" in names


def test_web_search_absent_when_capability_off(runner):
    caps = dict(runner.NO_CAPS)
    names = {t["name"] for t in runner.client_tool_schemas(caps)}
    assert "web_search" not in names


def test_execute_tool_dispatches_web_search(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "web_search_tinyfish", return_value="tinyfish ran") as mock_tf, \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("web_search", {"query": "sokrates ai"}, persona, "c1")
    assert result == "tinyfish ran"
    mock_tf.assert_called_once_with("sokrates ai")


def test_web_search_tinyfish_parses_a_realistic_response(runner):
    sample_response = {
        "query": "sokrates ai",
        "results": [
            {"position": 1, "site_name": "Example", "title": "Example Result One",
             "snippet": "This is the first snippet.", "url": "https://example.com/a"},
            {"position": 2, "site_name": "Example", "title": "Example Result Two",
             "snippet": "Second snippet here.", "url": "https://example.com/b"},
        ],
        "total_results": 2,
        "page": 1,
    }
    with patch.object(runner.tools_search, "TINYFISH_API_KEY", "fake-key"), \
         patch.object(runner.tools_search, "http_json", return_value=(200, sample_response)):
        result = runner.web_search_tinyfish("anything")

    assert "Example Result One" in result
    assert "https://example.com/a" in result
    assert "first" in result
    assert "Example Result Two" in result
    assert "https://example.com/b" in result


def test_web_search_tinyfish_handles_no_results_gracefully(runner):
    with patch.object(runner.tools_search, "TINYFISH_API_KEY", "fake-key"), \
         patch.object(runner.tools_search, "http_json", return_value=(200, {"results": []})):
        result = runner.web_search_tinyfish("obscure query")
    assert "no results" in result.lower()


def test_web_search_tinyfish_handles_non_200_gracefully(runner):
    with patch.object(runner.tools_search, "TINYFISH_API_KEY", "fake-key"), \
         patch.object(runner.tools_search, "http_json", return_value=(503, {"error": "unavailable"})):
        result = runner.web_search_tinyfish("anything")
    assert "503" in result


def test_web_search_tinyfish_degrades_without_key(runner):
    with patch.object(runner.tools_search, "TINYFISH_API_KEY", ""):
        result = runner.web_search_tinyfish("anything")
    assert "not configured" in result.lower()


def test_anthropic_never_sends_hosted_web_search(runner):
    """Anthropic's own server-side web_search tool was dropped in favor of
    the same shared web_search client tool Gemini uses (Issues #1 and #3:
    one search implementation, not two provider-specific ones)."""
    caps = {"webSearch": True, "vaultRead": False, "vaultWrite": False, "codeExecution": False}
    calls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls.append(body)
        return 200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )

    assert calls
    for body in calls:
        for tool in body.get("tools", []):
            assert tool.get("type", "").startswith("web_search") is False
    tool_names = {t.get("name") for body in calls for t in body.get("tools", [])}
    assert "web_search" in tool_names


# ---------------------------------------------------------------------------
# Issue #2 — Gemini 429 fallback cascade
# ---------------------------------------------------------------------------

def test_429_cascades_to_next_model_in_chain(runner):
    caps = dict(runner.NO_CAPS)
    attempted_urls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        attempted_urls.append(url)
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {"message": "rate limited"}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "flash replied"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})):
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert "flash replied" in result
    assert any(_url_targets_model(u, "gemini-flash-latest") for u in attempted_urls)
    # The originally-requested model must have been tried first, not skipped.
    assert any(_url_targets_model(u, "gemini-pro-latest") for u in attempted_urls)


# ---------------------------------------------------------------------------
# 2026-07-24: live logs showed 503 ("This model is currently experiencing
# high demand") failing turns outright with ZERO fallback attempt, several
# times in one session -- only 429 triggered the cascade before this fix,
# even though a 503 is exactly the kind of error a different model is
# likely to answer fine. This was the actual root cause of "pauses all the
# time even though cascading should work."
# ---------------------------------------------------------------------------

def test_503_cascades_to_next_model_in_chain(runner):
    caps = dict(runner.NO_CAPS)
    attempted_urls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        attempted_urls.append(url)
        if _url_targets_model(url, "gemini-pro-latest"):
            return 503, {"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "flash replied"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})):
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert "flash replied" in result
    assert any(_url_targets_model(u, "gemini-flash-latest") for u in attempted_urls)


def test_503_fallback_note_says_unavailable_not_rate_limited(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 503, {"error": {"code": 503, "status": "UNAVAILABLE"}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "actual reply"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})):
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=True,
        )

    assert "temporarily unavailable" in result
    assert "rate-limited" not in result


def test_500_also_cascades(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 500, {"error": {"code": 500, "status": "INTERNAL"}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "flash replied"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})):
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert "flash replied" in result


def test_400_does_not_cascade_and_is_not_mistaken_for_transient(runner):
    """A genuine bad-request error (e.g. malformed content) is not a
    per-model quota/availability problem -- cascading to another model
    would just fail identically. Must still raise, but as a plain
    RuntimeError, not GeminiRateLimited."""
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 400, {"error": {"code": 400, "message": "Requests ending with a model turn are not supported."}}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        with pytest.raises(RuntimeError) as exc_info:
            runner.gemini_generate(
                "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
                caps, {"name": "Test", "capabilities": caps}, "conv-1",
            )
    assert not isinstance(exc_info.value, runner.GeminiRateLimited)


def test_code_execution_with_function_declarations_sets_include_server_side_tool_invocations(runner):
    """2026-07-27: codeExecution (a Gemini built-in tool) combined with any
    other capability (which surfaces function_declarations via
    client_tool_schemas) 400s on every model with 'Please enable
    tool_config.include_server_side_tool_invocations to use Built-in tools
    with Function calling' -- found live on the Agora persona's own
    conversations. This 400 isn't a GeminiRateLimited, so it skipped the
    429/503 fallback cascade entirely and burned a FAILURE_PAUSE_CAP strike
    directly, auto-pausing the conversation with a misleading '(rate limit
    or outage)' reason even while the cascade itself worked fine."""
    caps = {"webSearch": True, "vaultRead": False, "vaultWrite": False, "codeExecution": True}
    calls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls.append(body)
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        runner.gemini_generate(
            "gemini-flash-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert calls, "no request was sent"
    body = calls[0]
    assert any("code_execution" in t for t in body["tools"])
    assert any("function_declarations" in t for t in body["tools"])
    assert body["toolConfig"]["includeServerSideToolInvocations"] is True


def test_no_capabilities_sends_no_tools_or_tool_config(runner):
    """Every capability off (and no active_step) means build_tools() returns
    an empty list entirely -- note that codeExecution alone would NOT hit
    this path, since client_tool_schemas' always-on save_memory declaration
    means any(caps.values()) being True (including from codeExecution
    itself) always yields at least one function_declarations tool -- see
    the combo test above."""
    caps = dict(runner.NO_CAPS)
    calls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls.append(body)
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        runner.gemini_generate(
            "gemini-flash-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert calls, "no request was sent"
    body = calls[0]
    assert "tools" not in body
    assert "toolConfig" not in body


def test_429_fallback_note_names_both_models(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "actual reply"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})):
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=True,
        )

    assert "Gemini Pro" in result
    assert "rate-limited" in result
    assert "switched to" in result
    assert "actual reply" in result


def test_429_fallback_note_when_not_sticky_says_this_reply_only(runner):
    """2026-07-24: sticky is now per-conversation and defaults False. The
    non-sticky note must not claim a permanent switch, and must not PATCH
    the conversation."""
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "actual reply"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal") as mock_internal:
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert "Gemini Pro" in result
    assert "rate-limited" in result
    assert "switched to" not in result
    assert "this reply used" in result
    assert "actual reply" in result
    mock_internal.assert_not_called()


def test_no_fallback_note_when_first_model_succeeds(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {"candidates": [{"content": {"parts": [{"text": "no issues here"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal") as mock_internal:
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert result == "no issues here"
    mock_internal.assert_not_called()


# ---------------------------------------------------------------------------
# 2026-07-23 redesign: the winning fallback persists onto the conversation
# instead of resetting to the original model on every subsequent turn.
# ---------------------------------------------------------------------------

def test_successful_fallback_persists_model_onto_conversation(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "flash replied"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})) as mock_internal:
        runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=True,
        )

    mock_internal.assert_called_once_with(
        "PATCH", "/conversations/conv-1", {"model": "gemini:gemini-flash-latest"}
    )


def test_fallback_does_not_persist_when_not_sticky(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "flash replied"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal") as mock_internal:
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=False,
        )

    assert "flash replied" in result
    mock_internal.assert_not_called()


def test_multi_hop_fallback_persists_the_final_landing_model(runner):
    """pro-latest and flash-latest are both down; 3.6-flash finally answers
    -- the persisted model must be the one that actually worked, not an
    intermediate hop that also failed."""
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-3.6-flash"):
            return 200, {"candidates": [{"content": {"parts": [{"text": "third time's the charm"}]}}]}
        return 429, {"error": {}}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})) as mock_internal:
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=True,
        )

    assert "third time's the charm" in result
    mock_internal.assert_called_once_with(
        "PATCH", "/conversations/conv-1", {"model": "gemini:gemini-3.6-flash"}
    )


def test_no_persist_attempt_when_conversation_id_is_falsy(runner):
    """Ask/preview invokes pass no real conversation id (Decisions/0005,
    always tool-less/ephemeral) -- the fallback must still work, just
    without trying to PATCH a nonexistent conversation."""
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "preview reply"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal") as mock_internal:
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, None,
        )

    assert "preview reply" in result
    mock_internal.assert_not_called()


def test_429_cascade_starts_from_the_chosen_model_position(runner):
    """A persona deliberately set to a cheap/fast model must never be
    silently upgraded to something 'better' — the cascade only degrades
    further down the chain, never up."""
    caps = dict(runner.NO_CAPS)
    start_index = runner.GEMINI_FALLBACK_CHAIN.index("gemini-3.1-flash-lite")
    better_models = runner.GEMINI_FALLBACK_CHAIN[:start_index]
    attempted = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        attempted.append(url)
        return 429, {"error": {}}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        with pytest.raises(runner.GeminiRateLimited):
            runner.gemini_generate_with_fallback(
                "gemini-3.1-flash-lite", False, "system", [{"role": "user", "content": "hi"}],
                caps, {"name": "Test", "capabilities": caps}, "conv-1",
            )

    for better in better_models:
        assert not any(_url_targets_model(u, better) for u in attempted), (
            f"cascade tried {better!r}, which ranks above the persona's chosen model"
        )


def test_all_models_rate_limited_raises(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 429, {"error": {}}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        with pytest.raises(runner.GeminiRateLimited):
            runner.gemini_generate_with_fallback(
                "gemini-3.5-flash-lite", False, "system", [{"role": "user", "content": "hi"}],
                caps, {"name": "Test", "capabilities": caps}, "conv-1",
            )


def test_unknown_model_id_has_no_fallback(runner):
    """An unrecognized model isn't in GEMINI_MODEL_FALLBACK, so a 429 on it
    is tried exactly once and then raised -- no assumption that some known
    model is an appropriate substitute for a model we don't recognize."""
    caps = dict(runner.NO_CAPS)
    attempts = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        attempts.append(url)
        return 429, {"error": {}}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        with pytest.raises(runner.GeminiRateLimited):
            runner.gemini_generate_with_fallback(
                "custom-model", False, "system", [{"role": "user", "content": "hi"}],
                caps, {"name": "Test", "capabilities": caps}, "conv-1",
            )
    assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Issue #3 — kubectl_read / github_read tools
# ---------------------------------------------------------------------------

def test_kubectl_read_rejects_secrets(runner):
    for resource in ("secret", "secrets", "secret/agora-vapid", "secrets.v1"):
        result = runner.kubectl_read({"verb": "get", "resource": resource})
        assert "never allowed" in result

    result = runner.kubectl_read({"verb": "get", "resource": "secretstore"})
    assert "never allowed" in result, "resource-kind prefix match should catch lookalikes too"


def test_kubectl_read_rejects_disallowed_verbs(runner):
    result = runner.kubectl_read({"verb": "delete", "resource": "pods"})
    assert "not allowed" in result
    result = runner.kubectl_read({"verb": "exec", "resource": "pods"})
    assert "not allowed" in result


def test_kubectl_read_rejects_disallowed_flags(runner):
    result = runner.kubectl_read({"verb": "get", "resource": "pods", "args": ["--raw=/api/v1/secrets"]})
    assert "not allowed" in result


def test_kubectl_read_builds_expected_command(runner):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        class R:
            stdout = "pod/foo   Running\n"
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.kubectl_read({
            "verb": "get", "resource": "pods", "namespace": "agents", "args": ["-o=wide"],
        })

    assert captured["cmd"] == ["kubectl", "get", "pods", "-o=wide", "-n", "agents"]
    assert "Running" in result


def test_kubectl_read_defaults_to_all_namespaces(runner):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout):
        captured["cmd"] = cmd
        class R:
            stdout = "ok"
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        runner.kubectl_read({"verb": "get", "resource": "pods"})

    assert "--all-namespaces" in captured["cmd"]
    assert "-n" not in captured["cmd"]


def test_kubectl_read_missing_binary_degrades_gracefully(runner):
    with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError()):
        result = runner.kubectl_read({"verb": "get", "resource": "pods"})
    assert "not installed" in result


def test_github_read_rejects_disallowed_commands(runner):
    result = runner.github_read({"command": "release", "subcommand": "delete"})
    assert "not allowed" in result
    result = runner.github_read({"command": "secret", "subcommand": "list"})
    assert "not allowed" in result


def test_github_read_rejects_write_flags_on_api(runner):
    result = runner.github_read({"command": "api", "subcommand": "/repos/x/y", "args": ["--method", "POST"]})
    assert "only read (GET) requests" in result
    result = runner.github_read({"command": "api", "subcommand": "/repos/x/y", "args": ["-XDELETE"]})
    assert "only read (GET) requests" in result


def test_github_read_forces_get_on_api_calls(runner):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, env):
        captured["cmd"] = cmd
        class R:
            stdout = "{}"
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.tools_github, "GITHUB_READONLY_TOKEN", "fake-token"):
        with patch.object(runner.subprocess, "run", side_effect=fake_run):
            runner.github_read({"command": "api", "subcommand": "/repos/SokratesAI/agora"})

    assert captured["cmd"] == ["gh", "api", "/repos/SokratesAI/agora", "--method", "GET"]


def test_github_read_without_token_degrades_gracefully(runner):
    with patch.object(runner.tools_github, "GITHUB_READONLY_TOKEN", ""):
        result = runner.github_read({"command": "pr", "subcommand": "list"})
    assert "no token configured" in result


# ---------------------------------------------------------------------------
# 2026-07-23: kubectl_read/github_read debug logging -- a nonzero exit code
# usually means the tool is silently misconfigured (missing RBAC grant,
# expired/wrong-scope token) rather than the query being bad, so it's
# logged always, not just under DEBUG_LOGGING.
# ---------------------------------------------------------------------------

def test_kubectl_read_logs_nonzero_exit(runner):
    logged = []

    def fake_run(cmd, capture_output, text, timeout):
        class R:
            stdout = ""
            stderr = "Error from server (Forbidden): pods is forbidden"
            returncode = 1
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run), \
         patch.object(runner.tools_kubectl, "log", side_effect=lambda msg: logged.append(msg)):
        runner.kubectl_read({"verb": "get", "resource": "pods"})

    assert any("exited 1" in m for m in logged)
    assert any("Forbidden" in m for m in logged)


def test_github_read_logs_nonzero_exit(runner):
    logged = []

    def fake_run(cmd, capture_output, text, timeout, env):
        class R:
            stdout = ""
            stderr = "HTTP 401: Bad credentials"
            returncode = 1
        return R()

    with patch.object(runner.tools_github, "GITHUB_READONLY_TOKEN", "fake-token"), \
         patch.object(runner.subprocess, "run", side_effect=fake_run), \
         patch.object(runner.tools_github, "log", side_effect=lambda msg: logged.append(msg)):
        runner.github_read({"command": "pr", "subcommand": "list"})

    assert any("exited 1" in m for m in logged)
    assert any("Bad credentials" in m for m in logged)


def test_github_read_logs_missing_token(runner):
    logged = []
    with patch.object(runner.tools_github, "GITHUB_READONLY_TOKEN", ""), \
         patch.object(runner.tools_github, "log", side_effect=lambda msg: logged.append(msg)):
        runner.github_read({"command": "pr", "subcommand": "list"})
    assert any("GITHUB_READONLY_TOKEN not set" in m for m in logged)


def test_github_read_missing_binary_degrades_gracefully(runner):
    with patch.object(runner.tools_github, "GITHUB_READONLY_TOKEN", "fake-token"):
        with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError()):
            result = runner.github_read({"command": "pr", "subcommand": "list"})
    assert "not installed" in result


def test_tools_shared_between_anthropic_and_gemini(runner):
    """Both providers must see the same custom tool set for the same
    capabilities — kubectl_read/github_read (and every other client tool)
    flow through the one client_tool_schemas() function, not a per-provider
    copy that could silently drift."""
    caps = {
        "webSearch": False, "vaultRead": False, "vaultWrite": False,
        "codeExecution": False, "kubectlRead": True, "githubRead": True,
    }
    schemas = runner.client_tool_schemas(caps)
    names = {t["name"] for t in schemas}
    assert "kubectl_read" in names
    assert "github_read" in names
    # save_memory is always present regardless of capability toggles.
    assert "save_memory" in names


def test_github_read_schema_documents_the_api_path_convention(runner):
    """2026-07-23: live E2E testing found the model repeatedly confusing
    command='api''s subcommand (the full request path) with a regular
    subcommand like 'list'/'view', and also re-passing the path in `args` on
    top of `subcommand` -- both cause 'accepts 1 arg(s), received 2'. Two
    independent live turns hit this same mistake, so the schema must spell
    out the convention explicitly rather than relying on the model to infer
    it from the generic subcommand description."""
    caps = {"webSearch": False, "vaultRead": False, "vaultWrite": False,
            "codeExecution": False, "kubectlRead": False, "githubRead": True}
    schema = next(t for t in runner.client_tool_schemas(caps) if t["name"] == "github_read")
    blob = json.dumps(schema)
    assert "full request path" in blob or "full request path" in schema["description"]
    assert "api" in schema["description"]


# ---------------------------------------------------------------------------
# 2026-07-24: live incident -- Gemini wrote vault_write with a different
# casing than the vault's established convention ("projects/..." vs the
# real "Projects/..."), which silently flipped that one document's `path`
# field (same doc, no new copy) and broke Obsidian/LiveSync's phone-side
# rendering, looking like duplicated folders. Fixed at the code level
# (vault_write_path/_vault_put_raw always lowercase now, see their
# docstrings) and by telling the model explicitly, so it doesn't have to
# rediscover the convention by trial and error mid-conversation like it did
# live.
# ---------------------------------------------------------------------------

def test_vault_tool_schemas_document_the_lowercase_convention(runner):
    caps = {"webSearch": False, "vaultRead": True, "vaultWrite": True,
            "codeExecution": False, "kubectlRead": False, "githubRead": False}
    schemas = {t["name"]: t for t in runner.client_tool_schemas(caps)}
    assert "lowercase" in schemas["vault_read"]["description"]
    assert "lowercase" in schemas["vault_list"]["description"]
    assert "lowercase" in schemas["vault_write"]["description"]


def test_vault_write_path_lowercases_the_path_field_not_just_the_id(runner):
    """The actual bug: `_id` was already always lowercased for lookup, but
    `path` (what LiveSync renders with) kept whatever casing the caller
    passed. Both must match now."""
    captured = {}

    def fake_couch_req(method, path, body=None):
        if method == "PUT" and body is not None and body.get("type") == "plain":
            captured["doc"] = body
        return 200, {}

    with patch.object(runner.vault, "couch_get_doc", return_value=(404, {})), \
         patch.object(runner.vault, "couch_req", side_effect=fake_couch_req):
        runner.vault_write_path("Projects/Sokrates/Issues.md", "content")

    assert captured["doc"]["_id"] == "projects/sokrates/issues.md"
    assert captured["doc"]["path"] == "projects/sokrates/issues.md"


def test_vault_write_path_does_not_copy_previous_content_into_the_vault(runner):
    """Every overwrite used to write a second document holding the old
    content under agora/backups/. The owner asked for it to stop
    (2026-08-05) -- "since the switch to Nova, this is just noise" -- and
    the folder was deleted. The daily GitHub snapshot is the recovery
    path now."""
    put_paths = []

    def fake_couch_req(method, path, body=None):
        if method == "PUT":
            put_paths.append(path)
        return 200, {}

    with patch.object(runner.vault, "couch_get_doc", return_value=(200, {
        "children": ["h:old"], "path": "projects/sokrates/issues.md", "_rev": "1-abc",
    })), \
         patch.object(runner.vault, "vault_assemble", return_value="old content"), \
         patch.object(runner.vault, "couch_req", side_effect=fake_couch_req):
        runner.vault_write_path("projects/sokrates/issues.md", "new content")

    decoded = [urllib.parse.unquote(p) for p in put_paths]
    assert [p for p in decoded if "backups/" in p.lower()] == []


# ---------------------------------------------------------------------------
# 2026-07-31: live incident -- the Agora Evolve workflow's cycle journal
# (append-only, newest-entry-at-top by convention) was silently losing
# every prior entry, run after run. vault_write is a full-file overwrite;
# the Coder persona read the journal, then called vault_write with only
# its OWN new entry, and the whole file -- including every earlier
# cycle's history -- was replaced. Prior versions were recoverable from
# vault_write_path's own per-write backups, but the live journal (the one
# thing each new run actually reads) never accumulated. Fixed with a
# purpose-built append tool rather than relying on prompt wording, since
# a smaller model dropping the convention is a predictable failure mode,
# not a one-off.
# ---------------------------------------------------------------------------

def test_vault_append_path_inserts_after_marker_when_found(runner):
    with patch.object(runner.vault, "vault_read_path_rev",
                       return_value=("---\nfm\n---\n\n## Entries\n\nold entry text", "1-x")), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        result = runner.vault_append_path("journal.md", "new entry text", after_marker="## Entries")
    assert result == "written"
    written_content = mock_write.call_args[0][1]
    assert written_content == "---\nfm\n---\n\n## Entries\n\nnew entry text\n\nold entry text"


def test_vault_append_path_appends_at_end_when_no_marker_given(runner):
    with patch.object(runner.vault, "vault_read_path_rev", return_value=("line one", "1-x")), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        runner.vault_append_path("notes.md", "line two")
    assert mock_write.call_args[0][1] == "line one\n\nline two\n"


# A marker that matches nothing used to append at the END of the file
# instead -- silently, with no error. That is exactly how the identical
# bug in the bridge's own vault tool (agora-claude-bridge#10) buried three
# of Nova's journal entries at the bottom of a file whose header promises
# newest-first; The owner read it as the loop having stopped writing. The
# old behaviour had a passing test asserting it, which is why it survived.
# Asking for a position and quietly getting the opposite end of the file
# is the same class of mistake as appending to a file that doesn't exist,
# which this function already refuses to do.

def test_vault_append_path_fails_and_writes_nothing_when_marker_not_found(runner):
    # vault_write_path returns a real string here so that a fall-through to
    # the end-of-file append is caught by the FAILED assertion too -- against
    # a bare MagicMock, `result.startswith("FAILED")` is quietly truthy.
    with patch.object(runner.vault, "vault_read_path_rev", return_value=("no marker here", "1-x")), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        result = runner.vault_append_path("notes.md", "addition", after_marker="## Missing")
    mock_write.assert_not_called()
    assert result.startswith("FAILED")
    assert "## Missing" in result


def test_vault_append_path_still_appends_at_end_when_marker_is_empty(runner):
    """Omitting the marker is the documented way to append at the end, and
    must keep working -- the failure above is only for a marker that was
    actually asked for."""
    with patch.object(runner.vault, "vault_read_path_rev", return_value=("line one", "1-x")), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        result = runner.vault_append_path("notes.md", "line two", after_marker="")
    assert result == "written"
    assert mock_write.call_args[0][1] == "line one\n\nline two\n"


def test_vault_append_path_fails_loudly_for_a_missing_file(runner):
    with patch.object(runner.vault, "vault_read_path_rev", return_value=(None, None)), \
         patch.object(runner.vault, "vault_write_path") as mock_write:
        result = runner.vault_append_path("missing.md", "content")
    assert result.startswith("FAILED")
    mock_write.assert_not_called()


def test_execute_tool_vault_append_dispatches_and_audits(runner):
    persona = {"name": "Gemini"}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old content") as mock_read, \
         patch.object(runner.tools_dispatch, "vault_append_path", return_value="written") as mock_append, \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "vault_append", {"path": "journal.md", "content": "new entry", "after_marker": "## Entries"},
            persona, "c1",
        )
    assert result == "written"
    mock_read.assert_called_once_with("journal.md")
    mock_append.assert_called_once_with("journal.md", "new entry", "## Entries")
    mock_audit.assert_called_once_with(
        "Gemini", "c1", "vault_append", "journal.md", before="old content", after="new entry"
    )


# 2026-08-03: the audit log is the only durable record of what a persona
# did to the owner's vault, and it was lying in exactly the cases worth
# reviewing -- every write branch passed before/after unconditionally, so
# a call that wrote nothing still produced an entry carrying the new
# content as the "after" side, which Agora's Activity feed renders as a
# completed diff. Latent until #35 made vault_append's FAILED path
# genuinely reachable (a marker matching no line now writes nothing).


def test_execute_tool_vault_append_audit_claims_no_diff_when_write_failed(runner):
    persona = {"name": "Gemini"}
    failure = "FAILED(after_marker not found in journal.md: '## Missing' -- nothing written)"
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old content"), \
         patch.object(runner.tools_dispatch, "vault_append_path", return_value=failure), \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "vault_append", {"path": "journal.md", "content": "new entry", "after_marker": "## Missing"},
            persona, "c1",
        )
    assert result == failure
    # The attempt is still audited -- that a persona tried to write is
    # real -- but with the failure in the detail and no before/after.
    mock_audit.assert_called_once()
    args, kwargs = mock_audit.call_args
    assert args[:3] == ("Gemini", "c1", "vault_append")
    assert "journal.md" in args[3] and "FAILED" in args[3]
    assert "before" not in kwargs and "after" not in kwargs


def test_execute_tool_vault_write_audit_claims_no_diff_when_write_failed(runner):
    persona = {"name": "Gemini"}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old content"), \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="FAILED(409)"), \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "vault_write", {"path": "notes.md", "content": "new body"}, persona, "c1",
        )
    assert result == "FAILED(409)"
    args, kwargs = mock_audit.call_args
    assert args[2] == "vault_write"
    assert "FAILED(409)" in args[3]
    assert "before" not in kwargs and "after" not in kwargs


def test_execute_tool_scoped_write_audit_claims_no_diff_when_write_failed(runner):
    persona = {"name": "Coder"}
    step = {"filepath": "projects/notes.md"}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old content"), \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="FAILED(500)"), \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "scoped_write", {"content": "new body"}, persona, "c1", active_step=step,
        )
    assert result == "FAILED(500)"
    args, kwargs = mock_audit.call_args
    assert args[2] == "scoped_write"
    assert "FAILED(500)" in args[3]
    assert "before" not in kwargs and "after" not in kwargs


def test_execute_tool_vault_write_still_audits_the_diff_on_success(runner):
    """The success path is what the Activity feed is for -- the fix must
    not cost it its before/after pair."""
    persona = {"name": "Gemini"}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old content"), \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="written"), \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "vault_write", {"path": "notes.md", "content": "new body"}, persona, "c1",
        )
    assert result == "written"
    mock_audit.assert_called_once_with(
        "Gemini", "c1", "vault_write", "notes.md", before="old content", after="new body"
    )


def test_vault_append_tool_schema_present_only_with_vault_write_capability(runner):
    caps_on = {"webSearch": False, "vaultRead": False, "vaultWrite": True,
               "codeExecution": False, "kubectlRead": False, "githubRead": False}
    caps_off = dict(runner.NO_CAPS)
    names_on = {t["name"] for t in runner.client_tool_schemas(caps_on)}
    names_off = {t["name"] for t in runner.client_tool_schemas(caps_off)}
    assert "vault_append" in names_on
    assert "vault_append" not in names_off


def test_capability_gated_tools_absent_when_off(runner):
    caps = dict(runner.NO_CAPS)
    schemas = runner.client_tool_schemas(caps)
    names = {t["name"] for t in schemas}
    assert "kubectl_read" not in names
    assert "github_read" not in names
    assert "vault_write" not in names
    assert names == {"save_memory"}


def test_execute_tool_dispatches_kubectl_and_github(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "kubectl_read", return_value="kubectl ran") as mock_k, \
         patch.object(runner.tools_dispatch, "github_read", return_value="gh ran") as mock_g, \
         patch.object(runner.tools_dispatch, "audit"):
        assert runner.execute_tool("kubectl_read", {"verb": "get", "resource": "pods"}, persona, "c1") == "kubectl ran"
        assert runner.execute_tool("github_read", {"command": "pr", "subcommand": "list"}, persona, "c1") == "gh ran"
    mock_k.assert_called_once()
    mock_g.assert_called_once()


def test_execute_tool_vault_write_audits_before_and_after_content(runner):
    """Activity feed diff view (Agora Issues.md 'diff view for writes'):
    execute_tool must read the pre-overwrite content itself and pass both
    versions to audit() so the UI can render a real before/after diff, not
    just the path that was touched."""
    persona = {"name": "Gemini"}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old content") as mock_read, \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="written") as mock_write, \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "vault_write", {"path": "notes.md", "content": "new content"}, persona, "c1"
        )
    assert result == "written"
    mock_read.assert_called_once_with("notes.md")
    # `allow_shrink=False` is the whole point of the collapse guard being on
    # by default: an ordinary tool call must not opt out of it by omission.
    mock_write.assert_called_once_with("notes.md", "new content",
                                       allow_shrink=False)
    mock_audit.assert_called_once_with(
        "Gemini", "c1", "vault_write", "notes.md", before="old content", after="new content"
    )


def test_execute_tool_vault_write_audits_empty_before_for_a_new_file(runner):
    """vault_read_path returns None for a file that doesn't exist yet —
    audit() must still get a string, not None, so the frontend diff always
    has two strings to compare."""
    persona = {"name": "Gemini"}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value=None), \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="written"), \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        runner.execute_tool("vault_write", {"path": "new.md", "content": "content"}, persona, "c1")
    mock_audit.assert_called_once_with(
        "Gemini", "c1", "vault_write", "new.md", before="", after="content"
    )


def test_audit_includes_before_after_only_when_given(runner):
    with patch.object(audit_module, "agora_internal", return_value=(200, {})) as mock_internal:
        runner.audit("Gemini", "c1", "vault_write", "notes.md", before="old", after="new")
    payload = mock_internal.call_args[0][2]
    assert payload["before"] == "old"
    assert payload["after"] == "new"

    with patch.object(audit_module, "agora_internal", return_value=(200, {})) as mock_internal:
        runner.audit("Gemini", "c1", "vault_read", "notes.md")
    payload = mock_internal.call_args[0][2]
    assert "before" not in payload
    assert "after" not in payload


def test_audit_marks_only_narration_ephemeral(runner):
    """Agora retains ephemeral entries on a budget of their own. An ordinary
    capability call must never carry the flag, or the trail this store
    exists to keep would evict itself."""
    with patch.object(audit_module, "agora_internal", return_value=(200, {})) as mock_internal:
        runner.audit("Gemini", "c1", "vault_write", "notes.md")
    assert "ephemeral" not in mock_internal.call_args[0][2]

    with patch.object(audit_module, "agora_internal", return_value=(200, {})) as mock_internal:
        runner.audit("Nova", "c1", "Bash", "ls", ephemeral=True)
    assert mock_internal.call_args[0][2]["ephemeral"] is True


# ---------------------------------------------------------------------------
# Defaults / capability plumbing
# ---------------------------------------------------------------------------

def test_new_capabilities_default_off(runner):
    assert runner.DEFAULT_CAPS["kubectlRead"] is False
    assert runner.DEFAULT_CAPS["githubRead"] is False
    assert runner.NO_CAPS["kubectlRead"] is False
    assert runner.NO_CAPS["githubRead"] is False


def test_default_caps_and_no_caps_have_same_keys(runner):
    # Every capability flag must exist in both maps -- a key present in one
    # but not the other silently drops that capability wherever the missing
    # map is used as a fallback/reference (e.g. dict(DEFAULT_CAPS) callers).
    assert set(runner.DEFAULT_CAPS.keys()) == set(runner.NO_CAPS.keys())


def test_gemini_fallback_chain_has_no_duplicates(runner):
    assert len(runner.GEMINI_FALLBACK_CHAIN) == len(set(runner.GEMINI_FALLBACK_CHAIN))


def test_gemini_fallback_chain_entries_all_have_max_output_tokens(runner):
    for model_id in runner.GEMINI_FALLBACK_CHAIN:
        assert model_id in runner.GEMINI_MAX_OUTPUT_TOKENS, (
            f"{model_id} is in the fallback chain but has no known output-token ceiling"
        )


# ---------------------------------------------------------------------------
# 2026-07-23 hotfix: Anthropic multi-text-block responses (server-side
# web_search can produce a preamble text block before the search, then more
# text after -- all in one response, no tool_use round-trip since it's
# server-side). The old code returned only the FIRST text block, silently
# dropping the actual answer (found live: Learning-Agent's web-search
# replies were just "I'll search for..." with nothing after).
# ---------------------------------------------------------------------------

def test_anthropic_generate_joins_multiple_text_blocks(runner):
    caps = {"webSearch": True, "vaultRead": False, "vaultWrite": False, "codeExecution": False}

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {
            "stop_reason": "end_turn",
            "content": [
                {"type": "text", "text": "I'll search for current information about a9s."},
                {"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {}},
                {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": []},
                {"type": "text", "text": "a9s is a database-operations platform..."},
            ],
        }

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "tell me about a9s"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )

    assert "I'll search for current information about a9s." in result
    assert "a9s is a database-operations platform..." in result


def test_anthropic_generate_single_text_block_still_works(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "hello"}]}

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )
    assert result == "hello"


# ---------------------------------------------------------------------------
# 2026-07-31: bring back visible "thinking" (the owner's old Slack-bridge setup
# streamed thought blocks as thread replies to a "Thinking..." placeholder;
# Agora never had this for either provider). Anthropic already excluded
# thinking blocks from round_text correctly -- they just had nowhere to go.
# ---------------------------------------------------------------------------

def test_anthropic_generate_calls_on_thinking_with_thinking_blocks(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "let me consider this...", "signature": "sig"},
                {"type": "text", "text": "the answer"},
            ],
        }

    thoughts = []
    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", True, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1", on_thinking=thoughts.append,
        )
    assert result == "the answer"
    assert thoughts == ["let me consider this..."]


def test_anthropic_generate_no_on_thinking_does_not_crash_on_thinking_blocks(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "internal", "signature": "sig"},
                {"type": "text", "text": "answer"},
            ],
        }

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", True, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )
    assert result == "answer"


def test_anthropic_generate_skips_empty_text_blocks_when_joining(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": ""}, {"type": "text", "text": "real answer"}],
        }

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )
    assert result == "real answer"


# ---------------------------------------------------------------------------
# 2026-07-23 hotfix: retry-storm auto-pause. A failed turn never appends a
# reply, so without a cap the turn-taking rule sees "still needs a reply"
# forever and poll_conversation retries every POLL_INTERVAL_SECONDS with no
# backoff. Found live: two rate-limited Gemini conversations retried
# nonstop for 8+ hours, each retry cascading through the entire
# GEMINI_FALLBACK_CHAIN -- which is what actually exhausted every Gemini
# model's quota, not just the one each conversation was configured for.
# ---------------------------------------------------------------------------

def _make_poll_fixtures(runner, conversation_id="conv-1"):
    summary = {"id": conversation_id, "name": "Test", "archived": False, "status": "active"}
    detail = {
        "name": "Test",
        "personas": None,
        "messages": [{"sender": "Edvard", "text": "hi", "forgotten": False}],
    }
    calls = {"agora_internal": []}

    def fake_agora_get(path):
        return 200, detail

    def fake_agora_internal(method, path, payload=None):
        calls["agora_internal"].append((method, path, payload))
        return 200, {}

    def fake_decide_turn(thread, personas):
        return ["Test"]

    return summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn


def test_repeated_speak_failures_back_off_without_pausing(runner):
    """The owner, 2026-08-05: auto-pause is gone -- it blocked the conversation
    until he resumed it by hand. The conversation must stay active and the
    retry must simply be deferred."""
    runner._conversation_failures.clear()
    runner._conversation_backoff.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=RuntimeError("simulated rate limit")):
        for _ in range(runner.FAILURE_BACKOFF_CAP):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)

    pause_calls = [
        c for c in calls["agora_internal"]
        if c[0] == "PATCH" and c[1] == f"/conversations/{summary['id']}" and c[2] == {"status": "paused"}
    ]
    assert pause_calls == [], "nothing may pause a conversation any more"
    assert summary["id"] in runner._conversation_backoff, "should be backing off at the cap"
    assert runner._conversation_backoff[summary["id"]][0] > time.monotonic()


def test_backoff_notice_surfaces_the_real_exception(runner):
    # The label used to be a generic "(rate limit or outage)" guess, with
    # the real exception only ever reaching stdout via poll.py's own
    # try/except -- never the conversation the owner actually reads.
    runner._conversation_failures.clear()
    runner._conversation_backoff.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=RuntimeError("simulated 503 from provider")):
        for _ in range(runner.FAILURE_BACKOFF_CAP):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)

    notify_calls = [
        c for c in calls["agora_internal"]
        if c[0] == "POST" and c[1] == f"/conversations/{summary['id']}/notify"
    ]
    assert notify_calls, "back_off should have posted a system notice"
    text = notify_calls[-1][2]["text"]
    assert "RuntimeError" in text
    assert "simulated 503 from provider" in text
    assert "Paused" not in text and "menu" not in text, "must not tell him to resume anything"


def test_failures_below_cap_do_not_back_off(runner):
    runner._conversation_failures.clear()
    runner._conversation_backoff.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=RuntimeError("simulated rate limit")):
        for _ in range(runner.FAILURE_BACKOFF_CAP - 1):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)

    pause_calls = [c for c in calls["agora_internal"] if c[0] == "PATCH"]
    assert len(pause_calls) == 0
    assert summary["id"] not in runner._conversation_backoff
    assert runner._conversation_failures[summary["id"]] == runner.FAILURE_BACKOFF_CAP - 1


def test_failure_count_resets_on_success(runner):
    runner._conversation_failures.clear()
    runner._conversation_backoff.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    call_count = {"n": 0}

    def flaky_speak(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= runner.FAILURE_BACKOFF_CAP - 1:
            raise RuntimeError("transient")
        return "recovered reply"

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=flaky_speak):
        for _ in range(runner.FAILURE_BACKOFF_CAP - 1):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)
        runner.poll_conversation(summary)  # succeeds now

    assert summary["id"] not in runner._conversation_failures
    assert summary["id"] not in runner._conversation_backoff
    pause_calls = [c for c in calls["agora_internal"] if c[0] == "PATCH"]
    assert len(pause_calls) == 0, "should never have paused -- recovered before hitting the cap"


def test_failure_counts_are_tracked_independently_per_conversation(runner):
    runner._conversation_failures.clear()
    summary_a, _, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner, "conv-a")
    summary_b = {**summary_a, "id": "conv-b"}

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=RuntimeError("simulated rate limit")):
        with pytest.raises(RuntimeError):
            runner.poll_conversation(summary_a)
        with pytest.raises(RuntimeError):
            runner.poll_conversation(summary_a)
        with pytest.raises(RuntimeError):
            runner.poll_conversation(summary_b)

    assert runner._conversation_failures["conv-a"] == 2
    assert runner._conversation_failures["conv-b"] == 1


# ---------------------------------------------------------------------------
# 2026-07-23 debug logging: every 429 must log Google's actual response
# body, not just the status code -- this was the single biggest gap in
# diagnosing whether a 429 is a genuine per-model quota block, a
# request-routing bug (all "different" model calls actually hitting the
# same model), or an account-wide burst throttle. Before this fix, a 429
# raised GeminiRateLimited immediately with zero visibility into what
# Google actually said.
# ---------------------------------------------------------------------------

def test_gemini_429_logs_the_response_body(runner):
    caps = dict(runner.NO_CAPS)
    logged = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 429, {
            "error": {
                "message": "Quota exceeded for metric: generate_content_free_tier_requests, "
                           "limit: 5, model: gemini-3-flash-preview",
            }
        }

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "log", side_effect=lambda msg: logged.append(msg)):
        with pytest.raises(runner.GeminiRateLimited):
            runner.gemini_generate(
                "gemini-3-flash-preview", False, "system", [{"role": "user", "content": "hi"}],
                caps, {"name": "Test", "capabilities": caps}, "conv-1",
            )

    body_logs = [m for m in logged if "429 detail" in m]
    assert body_logs, "no log line captured Google's actual 429 response body"
    assert "gemini-3-flash-preview" in body_logs[0]
    assert "Quota exceeded" in body_logs[0]


def test_anthropic_non_200_logs_the_response_body(runner):
    caps = dict(runner.NO_CAPS)
    logged = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.anthropic, "log", side_effect=lambda msg: logged.append(msg)):
        with pytest.raises(RuntimeError):
            runner.anthropic_generate(
                "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
                caps, {"name": "Test", "id": "p1"}, "conv-1",
            )

    body_logs = [m for m in logged if "detail" in m]
    assert body_logs
    assert "rate_limit_error" in body_logs[0]


def test_debug_log_silent_by_default(runner):
    printed = []
    with patch.object(log_module, "DEBUG_LOGGING", False), \
         patch("builtins.print", side_effect=lambda *a, **k: printed.append(a)):
        runner.debug_log("should not appear")
    assert printed == []


def test_debug_log_prints_when_enabled(runner):
    printed = []
    with patch.object(log_module, "DEBUG_LOGGING", True), \
         patch("builtins.print", side_effect=lambda *a, **k: printed.append(a)):
        runner.debug_log("should appear")
    assert any("should appear" in str(a) for a in printed)


def test_persist_patch_result_is_logged(runner):
    caps = dict(runner.NO_CAPS)
    logged = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})), \
         patch.object(runner.providers.gemini, "log", side_effect=lambda msg: logged.append(msg)):
        runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=True,
        )

    persist_logs = [m for m in logged if "persist" in m]
    assert persist_logs, "persistence PATCH result should always be logged"
    assert "HTTP 200" in persist_logs[0]


# ---------------------------------------------------------------------------
# 2026-07-24: sticky fallback made per-conversation (Agora Issues: "Gemini
# fallback should be customizable, sticky or not"). speak() reads the
# conversation's own stickyFallback field; heartbeats always force
# non-sticky regardless of that field, since a scheduled proactive message
# shouldn't permanently downgrade a persona other conversations may share.
# ---------------------------------------------------------------------------

def test_speak_reads_sticky_fallback_from_conversation_detail(runner):
    persona = {"id": "p1", "name": "Test", "model": "gemini:gemini-pro-latest",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}],
              "name": "Test", "stickyFallback": True}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None, sticky=False, on_text=None, on_thinking=None):
        captured["sticky"] = sticky
        return "reply text"

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", return_value=200):
        runner.speak(conversation, detail, [], "Test")

    assert captured["sticky"] is True


def test_speak_defaults_sticky_false_when_conversation_field_unset(runner):
    persona = {"id": "p1", "name": "Test", "model": "gemini:gemini-pro-latest",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None, sticky=False, on_text=None, on_thinking=None):
        captured["sticky"] = sticky
        return "reply text"

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", return_value=200):
        runner.speak(conversation, detail, [], "Test")

    assert captured["sticky"] is False


def test_heartbeat_always_non_sticky_even_if_bound_conversation_is_sticky(runner):
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "gemini:gemini-pro-latest",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": True}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None, sticky=False, on_text=None, on_thinking=None, unattended=False):
        captured["sticky"] = sticky
        return "heartbeat reply"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=200), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    assert captured["sticky"] is False


def _run_heartbeat_capturing_model(runner, detail, persona):
    """Runs run_heartbeat and returns the model_override generate_reply was
    handed. This is the path Nova's own cycles run on."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None, unattended=False):
        captured["model_override"] = model_override
        return "heartbeat reply"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=200), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    return captured["model_override"]


def test_heartbeat_takes_its_model_from_the_bound_conversation(runner):
    """Idea #95 slice 1. Nova's persona curates one conversation per cycle,
    so resolving a heartbeat turn's model off the persona is the coupling
    this slice removes -- and heartbeats are the path Nova itself runs on."""
    persona = {"id": "p1", "name": "Test", "model": "gemini:gemini-pro-latest",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "model": "claude-cli:claude-opus-5"}

    assert _run_heartbeat_capturing_model(runner, detail, persona) == "claude-cli:claude-opus-5"


def test_heartbeat_falls_back_to_the_persona_when_the_conversation_has_no_model(runner):
    persona = {"id": "p1", "name": "Test", "model": "gemini:gemini-pro-latest",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "model": ""}

    assert _run_heartbeat_capturing_model(runner, detail, persona) is None


# ---------------------------------------------------------------------------
# 2026-07-24: image/file attachments were stored and rendered in the UI
# (agora#12) but the persona-runner never actually read them -- an
# image-only message became a genuinely empty turn, which Gemini rejects
# with 400 "Requests ending with a model turn are not supported" (found
# live, and NOT caught by the 429/503 fallback since it isn't a rate or
# availability problem). Fixed by fetching attachment bytes and building
# real image content for both providers; non-image/failed-fetch
# attachments degrade to a text placeholder so a turn is never empty.
# ---------------------------------------------------------------------------

def test_merge_history_carries_attachments_through(runner):
    thread = [
        {"sender": "Edvard", "text": "", "attachments": [
            {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 123},
        ]},
    ]
    merged = runner.merge_history(thread, "Test", False)
    assert len(merged) == 1
    assert merged[0]["attachments"] == [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 123}
    ]


def test_merge_history_defaults_attachments_to_empty_list(runner):
    thread = [{"sender": "Edvard", "text": "hi"}]
    merged = runner.merge_history(thread, "Test", False)
    assert merged[0]["attachments"] == []


# ---------------------------------------------------------------------------
# 2026-07-24: found live -- a persona asked an unrelated question shortly
# after an auto-pause notice answered about THAT notice instead of the
# actual question, having read it as if it were a real previous reply.
# Sender-name matching ("Agora") can't distinguish a control-plane notice
# from a real persona literally named Agora (the legacy Main-thread
# migration creates exactly one), so this needs the conversation-store's
# own `system` flag on the Message, not a name check.
# ---------------------------------------------------------------------------

def test_merge_history_excludes_system_messages(runner):
    thread = [
        {"sender": "Edvard", "text": "a real question"},
        {"sender": "Gemini", "text": "a real reply"},
        {"sender": "Agora", "text": "paused notice", "system": True},
        {"sender": "Edvard", "text": "whats this?"},
    ]
    merged = runner.merge_history(thread, "Gemini", False)
    all_content = " ".join(m["content"] for m in merged)
    assert "paused notice" not in all_content
    assert "a real question" in all_content
    assert "whats this?" in all_content


def test_merge_history_system_message_does_not_break_role_merging(runner):
    """A system message sitting between two same-role turns must not
    prevent them from merging (it's simply invisible to this function)."""
    thread = [
        {"sender": "Edvard", "text": "first"},
        {"sender": "Agora", "text": "notice", "system": True},
        {"sender": "Edvard", "text": "second"},
    ]
    merged = runner.merge_history(thread, "Test", False)
    assert len(merged) == 1
    assert merged[0]["content"] == "first\n\nsecond"


def test_back_off_notifies_with_system_true(runner):
    captured = {}

    def fake_notify(conversation_id, text, sender, system=False):
        captured["system"] = system
        return 200

    with patch.object(runner.conversations, "agora_internal", return_value=(200, {})), \
         patch.object(runner.conversations, "notify", side_effect=fake_notify):
        runner.back_off("conv-1", "Test", runner.FAILURE_BACKOFF_CAP, "RuntimeError: x")

    assert captured["system"] is True
    runner._conversation_backoff.pop("conv-1", None)


def test_backoff_skips_the_poll_entirely_until_it_expires(runner):
    """The whole point: while backing off, speak() must not be called at all
    -- that's the fallback-chain cascade the retry storm was made of."""
    runner._conversation_failures.clear()
    runner._conversation_backoff.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)
    detail["messages"] = [{"id": "m1", "sender": "Edvard", "text": "hi", "forgotten": False}]
    speak_calls = {"n": 0}

    def counting_speak(*args, **kwargs):
        speak_calls["n"] += 1
        raise RuntimeError("still down")

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=counting_speak):
        for _ in range(runner.FAILURE_BACKOFF_CAP):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)
        assert speak_calls["n"] == runner.FAILURE_BACKOFF_CAP
        # Ten more ticks with his same message still last in the thread --
        # without the id check this cleared the backoff every time.
        for _ in range(10):
            runner.poll_conversation(summary)
        assert speak_calls["n"] == runner.FAILURE_BACKOFF_CAP, "backoff must suppress every retry"

        # A genuinely new message from him resumes immediately.
        detail["messages"].append({"id": "m2", "sender": "Edvard", "text": "still there?", "forgotten": False})
        with pytest.raises(RuntimeError):
            runner.poll_conversation(summary)
        assert speak_calls["n"] == runner.FAILURE_BACKOFF_CAP + 1

    runner._conversation_backoff.clear()
    runner._conversation_failures.clear()


def test_backoff_delay_doubles_and_is_capped(runner):
    runner._conversation_backoff.clear()
    seen = []
    with patch.object(runner.conversations, "notify", return_value=(200, "m")):
        for failures in range(runner.FAILURE_BACKOFF_CAP, runner.FAILURE_BACKOFF_CAP + 12):
            runner.back_off("conv-x", "Test", failures, "RuntimeError: x")
            seen.append(round(runner._conversation_backoff["conv-x"][0] - time.monotonic()))
    assert seen[0] == runner.FAILURE_BACKOFF_SECONDS
    assert seen[1] == runner.FAILURE_BACKOFF_SECONDS * 2
    assert seen[2] == runner.FAILURE_BACKOFF_SECONDS * 4
    assert max(seen) == runner.FAILURE_BACKOFF_MAX_SECONDS, "must stop doubling at the ceiling"
    runner._conversation_backoff.clear()


def test_no_turn_owed_neither_speaks_nor_patches(runner):
    """decide_turn returning [] must end the tick silently -- no speak, and no
    PATCH. Auto-pause used to live on this path via PAUSE_SENTINEL; the owner
    asked for pausing gone (2026-08-05) and the sentinel went with the
    persona-to-persona chain it capped (agora#67)."""
    summary, detail, calls, fake_agora_get, fake_agora_internal, _ = _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", return_value=[]), \
         patch.object(runner.conversations, "speak", side_effect=AssertionError("must not speak")):
        runner.poll_conversation(summary)

    assert calls["agora_internal"] == [], "no PATCH and no pause notice -- just stop"


def test_notify_sends_system_field_in_request_body(runner):
    captured = {}

    def fake_agora_internal(method, path, payload=None):
        captured["payload"] = payload
        return 200, {}

    with patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        runner.notify("conv-1", "hi", "Agora", system=True)

    assert captured["payload"]["system"] is True


def test_merge_history_accumulates_attachments_across_merged_turns(runner):
    thread = [
        {"sender": "Edvard", "text": "look at this", "attachments": [
            {"id": "att-1", "filename": "a.jpg", "mimeType": "image/jpeg", "size": 1},
        ]},
        {"sender": "Edvard", "text": "and this too", "attachments": [
            {"id": "att-2", "filename": "b.jpg", "mimeType": "image/jpeg", "size": 1},
        ]},
    ]
    merged = runner.merge_history(thread, "Test", False)
    assert len(merged) == 1, "consecutive same-role turns must still merge into one"
    assert [a["id"] for a in merged[0]["attachments"]] == ["att-1", "att-2"]
    assert merged[0]["content"] == "look at this\n\nand this too"


def test_gemini_parts_builds_inline_data_for_image_attachment(runner):
    message = {"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}
    with patch.object(runner.providers.gemini, "fetch_attachment_bytes", return_value=b"fake-jpeg-bytes"):
        parts = runner._gemini_parts(message)
    assert len(parts) == 1
    assert parts[0]["inline_data"]["mime_type"] == "image/jpeg"
    import base64 as b64
    assert parts[0]["inline_data"]["data"] == b64.b64encode(b"fake-jpeg-bytes").decode()


def test_gemini_parts_never_empty_for_image_only_message(runner):
    """The exact live crash: an image with no caption must never produce
    zero parts (Gemini rejects a genuinely empty turn with 400)."""
    message = {"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}
    with patch.object(runner.providers.gemini, "fetch_attachment_bytes", return_value=b"fake-jpeg-bytes"):
        parts = runner._gemini_parts(message)
    assert len(parts) > 0


def test_gemini_parts_placeholder_for_non_image_attachment(runner):
    message = {"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "notes.pdf", "mimeType": "application/pdf", "size": 1},
    ]}
    parts = runner._gemini_parts(message)
    assert len(parts) == 1
    assert "notes.pdf" in parts[0]["text"]
    assert "inline_data" not in parts[0]


def test_gemini_parts_placeholder_when_fetch_fails(runner):
    message = {"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}
    with patch.object(runner.providers.gemini, "fetch_attachment_bytes", return_value=None):
        parts = runner._gemini_parts(message)
    assert len(parts) == 1
    assert "photo.jpg" in parts[0]["text"]


def test_gemini_parts_plain_text_unaffected_when_no_attachments(runner):
    message = {"role": "user", "content": "hello", "attachments": []}
    parts = runner._gemini_parts(message)
    assert parts == [{"text": "hello"}]


def test_anthropic_content_plain_string_when_no_attachments(runner):
    """Backward compatible: a message with no attachments must still be a
    plain string, not a single-element block list."""
    message = {"role": "user", "content": "hello", "attachments": []}
    assert runner._anthropic_content(message) == "hello"


def test_anthropic_content_builds_image_block(runner):
    message = {"role": "user", "content": "check this out", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}
    with patch.object(runner.providers.anthropic, "fetch_attachment_bytes", return_value=b"fake-jpeg-bytes"):
        blocks = runner._anthropic_content(message)
    assert {"type": "text", "text": "check this out"} in blocks
    image_blocks = [b for b in blocks if b["type"] == "image"]
    assert len(image_blocks) == 1
    assert image_blocks[0]["source"]["media_type"] == "image/jpeg"
    assert image_blocks[0]["source"]["type"] == "base64"


def test_anthropic_content_never_empty_for_image_only_message(runner):
    message = {"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}
    with patch.object(runner.providers.anthropic, "fetch_attachment_bytes", return_value=b"fake-jpeg-bytes"):
        blocks = runner._anthropic_content(message)
    assert len(blocks) > 0


def test_fetch_attachment_bytes_returns_none_on_non_200(runner):
    with patch.object(runner.http_util, "http_bytes", return_value=(404, b"")):
        result = runner.fetch_attachment_bytes("att-1")
    assert result is None


def test_fetch_attachment_bytes_returns_bytes_on_success(runner):
    with patch.object(runner.http_util, "http_bytes", return_value=(200, b"raw-bytes")):
        result = runner.fetch_attachment_bytes("att-1")
    assert result == b"raw-bytes"


def test_gemini_generate_sends_real_image_content_end_to_end(runner):
    """Full path: a caption-less image message reaches gemini_generate and
    produces a request whose contents actually include inline_data, not an
    empty turn."""
    caps = dict(runner.NO_CAPS)
    history = [{"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}]
    captured_body = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        captured_body.update(body)
        return 200, {"candidates": [{"content": {"parts": [{"text": "nice photo"}]}}]}

    with patch.object(runner.providers.gemini, "fetch_attachment_bytes", return_value=b"fake-jpeg-bytes"), \
         patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        result = runner.gemini_generate(
            "gemini-flash-latest", False, "system", history,
            caps, {"name": "Test", "capabilities": caps}, "conv-1",
        )

    assert result == "nice photo"
    sent_parts = captured_body["contents"][0]["parts"]
    assert any("inline_data" in p for p in sent_parts)


def test_anthropic_generate_sends_real_image_content_end_to_end(runner):
    caps = dict(runner.NO_CAPS)
    history = [{"role": "user", "content": "", "attachments": [
        {"id": "att-1", "filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
    ]}]
    captured_body = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        captured_body.update(body)
        return 200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "nice photo"}]}

    with patch.object(runner.providers.anthropic, "fetch_attachment_bytes", return_value=b"fake-jpeg-bytes"), \
         patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", history,
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )

    assert result == "nice photo"
    sent_content = captured_body["messages"][0]["content"]
    assert any(b.get("type") == "image" for b in sent_content)


# ---------------------------------------------------------------------------
# 2026-07-24: live streaming. A turn now posts each text block as its own
# message the moment it's generated (Claude Code's own "text, then a
# clickable tool-use summary, then more text" pattern was the explicit
# reference), instead of the whole reply landing in one message at the end.
# Covers: on_text fires per round with the right is_final; the Gemini
# fallback note lands on only the first streamed chunk; turn-taking stays
# correct once one "turn" can be several messages (activity chips invisible
# to the `visible` filter decide_turn builds); and
# a turn that fails partway through rolls its own preamble back out.
# ---------------------------------------------------------------------------

def test_anthropic_generate_streams_preamble_before_tool_use_then_final_text(runner):
    caps = {"webSearch": False, "vaultRead": True, "vaultWrite": False, "codeExecution": False}
    calls = {"n": 0}

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Let me check that file first."},
                    {"type": "tool_use", "id": "t1", "name": "vault_read", "input": {"path": "notes.md"}},
                ],
            }
        return 200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Here's what it says."}]}

    chunks = []
    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.anthropic, "execute_tool", return_value="file contents"):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "check the file"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
            on_text=lambda chunk, is_final: chunks.append((chunk, is_final)),
        )

    assert chunks == [
        ("Let me check that file first.", False),
        ("Here's what it says.", True),
    ]
    # Return value is unchanged by streaming -- only the final round's text.
    assert result == "Here's what it says."


def test_anthropic_generate_no_on_text_behaves_exactly_as_before(runner):
    """Callers that don't stream (the tool-less /invoke path) must see zero
    behavior change -- on_text is opt-in."""
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        return 200, {"stop_reason": "end_turn", "content": [{"type": "text", "text": "hello"}]}

    with patch.object(runner.providers.anthropic, "http_json", side_effect=fake_http_json):
        result = runner.anthropic_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "id": "p1"}, "conv-1",
        )
    assert result == "hello"


def test_gemini_generate_streams_preamble_before_function_call_then_final_text(runner):
    caps = {"webSearch": False, "vaultRead": True, "vaultWrite": False, "codeExecution": False}
    calls = {"n": 0}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        calls["n"] += 1
        if calls["n"] == 1:
            return 200, {"candidates": [{"content": {"parts": [
                {"text": "Let me look that up."},
                {"functionCall": {"name": "vault_read", "args": {"path": "notes.md"}}},
            ]}}]}
        return 200, {"candidates": [{"content": {"parts": [{"text": "Found it."}]}}]}

    chunks = []
    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "execute_tool", return_value="file contents"):
        result = runner.gemini_generate(
            "gemini-flash-latest", False, "system", [{"role": "user", "content": "look it up"}],
            caps, {"name": "Test"}, "conv-1",
            on_text=lambda chunk, is_final: chunks.append((chunk, is_final)),
        )

    assert chunks == [("Let me look that up.", False), ("Found it.", True)]
    assert result == "Found it."


def test_gemini_generate_requests_include_thoughts_when_thinking_on(runner):
    """2026-07-31: thinkingBudget alone makes Gemini think for real but
    returns none of it -- includeThoughts is what actually makes the API
    send thought-summary parts back. This is the entire reason the owner
    never saw Gemini's thoughts in Agora; not a UI gap, a missing request
    param."""
    caps = dict(runner.NO_CAPS)
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["thinkingConfig"] = body["generationConfig"].get("thinkingConfig")
        return 200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        runner.gemini_generate(
            "gemini-flash-latest", True, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test"}, "conv-1",
        )
    assert captured["thinkingConfig"] == {"thinkingBudget": -1, "includeThoughts": True}


def test_gemini_generate_omits_thinking_config_when_thinking_off(runner):
    caps = dict(runner.NO_CAPS)
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["generationConfig"] = body["generationConfig"]
        return 200, {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}

    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        runner.gemini_generate(
            "gemini-flash-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test"}, "conv-1",
        )
    assert "thinkingConfig" not in captured["generationConfig"]


def test_gemini_generate_splits_thought_parts_from_the_answer(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        return 200, {"candidates": [{"content": {"parts": [
            {"text": "reasoning about the problem...", "thought": True},
            {"text": "the real answer"},
        ]}}]}

    thoughts = []
    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        result = runner.gemini_generate(
            "gemini-flash-latest", True, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test"}, "conv-1", on_thinking=thoughts.append,
        )
    assert result == "the real answer"
    assert thoughts == ["reasoning about the problem..."]


def test_gemini_generate_with_fallback_threads_on_thinking_through(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        return 200, {"candidates": [{"content": {"parts": [
            {"text": "thinking...", "thought": True},
            {"text": "answer"},
        ]}}]}

    thoughts = []
    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json):
        result = runner.gemini_generate_with_fallback(
            "gemini-flash-latest", True, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", on_thinking=thoughts.append,
        )
    assert result == "answer"
    assert thoughts == ["thinking..."]


def test_gemini_fallback_note_prefixes_only_the_first_streamed_chunk(runner):
    caps = dict(runner.NO_CAPS)

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if _url_targets_model(url, "gemini-pro-latest"):
            return 429, {"error": {}}
        return 200, {"candidates": [{"content": {"parts": [{"text": "actual reply"}]}}]}

    chunks = []
    with patch.object(runner.providers.gemini, "http_json", side_effect=fake_http_json), \
         patch.object(runner.providers.gemini, "agora_internal", return_value=(200, {})):
        result = runner.gemini_generate_with_fallback(
            "gemini-pro-latest", False, "system", [{"role": "user", "content": "hi"}],
            caps, {"name": "Test", "capabilities": caps}, "conv-1", sticky=True,
            on_text=lambda chunk, is_final: chunks.append((chunk, is_final)),
        )

    assert len(chunks) == 1
    assert "Gemini Pro" in chunks[0][0]
    assert "actual reply" in chunks[0][0]
    assert chunks[0][1] is True
    # Return value keeps its own independently-computed note too (unchanged
    # from before streaming existed) -- both must agree, not just one.
    assert "Gemini Pro" in result
    assert "actual reply" in result


# ---------------------------------------------------------------------------
# 2026-08-01: claude_cli provider -- calls agora-claude-bridge instead of
# Anthropic's Messages API directly. Structurally different from the other
# two providers: the bridge holds a persistent CLI session per
# conversation_id, so only the LAST history entry is sent as this turn's
# prompt, not the full history -- see providers/claude_cli.py's own
# docstring.
# ---------------------------------------------------------------------------

def test_claude_cli_generate_sends_only_the_last_history_entry(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "the answer", "thinking": ""}

    history = [
        {"role": "user", "content": "first message, long ago"},
        {"role": "assistant", "content": "an old reply"},
        {"role": "user", "content": "the actual new message"},
    ]
    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        result = runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system prompt", history,
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )
    assert result == "the answer"
    assert captured["body"]["prompt"] == "the actual new message"
    assert captured["body"]["system"] == "system prompt"
    assert captured["body"]["conversation_id"] == "conv-1"
    assert captured["body"]["model"] == "claude-haiku-4-5-20251001"
    assert captured["body"]["restricted"] is False


def test_claude_cli_generate_defaults_unrestricted_when_persona_field_absent(runner):
    """2026-08-01 design call: unrestricted by default, same as an
    interactive Claude Code session -- restriction is an explicit
    per-persona opt-in via claudeCliRestricted, not a silent default."""
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )
    assert captured["body"]["restricted"] is False


def test_claude_cli_generate_sends_restricted_true_when_persona_opts_in(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test", "claudeCliRestricted": True}, "conv-1",
        )
    assert captured["body"]["restricted"] is True


def test_claude_cli_generate_defaults_stateless_false_when_persona_field_absent(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )
    assert captured["body"]["stateless"] is False


def test_claude_cli_generate_sends_stateless_true_when_persona_opts_in(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test", "claudeCliStateless": True}, "conv-1",
        )
    assert captured["body"]["stateless"] is True


def test_claude_cli_generate_calls_on_text_and_on_thinking(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        return 200, {"text": "final answer", "thinking": "reasoning about it"}

    text_calls = []
    thinking_calls = []
    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", True, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
            on_text=lambda chunk, is_final: text_calls.append((chunk, is_final)),
            on_thinking=lambda chunk: thinking_calls.append(chunk),
        )
    assert text_calls == [("final answer", True)]
    assert thinking_calls == ["reasoning about it"]


def test_claude_cli_generate_skips_on_thinking_when_bridge_returns_none(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        return 200, {"text": "final answer", "thinking": ""}

    thinking_calls = []
    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
            on_thinking=lambda chunk: thinking_calls.append(chunk),
        )
    assert thinking_calls == []


def test_claude_cli_generate_raises_usage_limited_on_429(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        return 429, {"error": "usage_limit", "detail": "resets in 4 hours"}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        with pytest.raises(runner.ClaudeBridgeUsageLimited, match="resets in 4 hours"):
            runner.claude_cli_generate(
                "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
                dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
            )


def test_claude_cli_generate_raises_runtime_error_on_other_failures(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        return 502, {"error": "cli_error", "detail": "boom"}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        with pytest.raises(RuntimeError, match="claude_cli 502"):
            runner.claude_cli_generate(
                "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
                dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
            )


def test_claude_cli_generate_raises_on_empty_history(runner):
    with pytest.raises(RuntimeError, match="empty history"):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )


# ---------------------------------------------------------------------------
# claude_cli attachments -- 2026-08-10. anthropic/gemini built real image
# blocks from 2026-07-24 and this provider built nothing, so an image sent to
# a claude-cli persona reached the model as if it had never been attached.
# ---------------------------------------------------------------------------

def _cli_body(runner, history, fetch=b"\x89PNG-bytes"):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "ok", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "fetch_attachment_bytes",
                      return_value=fetch), \
         patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", history,
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )
    return captured["body"]


def test_claude_cli_generate_sends_an_image_attachment_as_base64(runner):
    body = _cli_body(runner, [{"role": "user", "content": "what is this?", "attachments": [
        {"id": "att-1", "filename": "photo.png", "mimeType": "image/png"}]}])
    assert body["prompt"] == "what is this?"
    assert body["attachments"] == [{
        "filename": "photo.png",
        "mimeType": "image/png",
        "data": base64.b64encode(b"\x89PNG-bytes").decode(),
    }]


def test_claude_cli_generate_omits_the_attachments_key_when_there_are_none(runner):
    """The bridge treats an absent key as "invoke the CLI exactly as
    before", so every ordinary text turn -- including every Nova cycle --
    must keep looking identical on the wire."""
    body = _cli_body(runner, [{"role": "user", "content": "hi"}])
    assert "attachments" not in body


def test_claude_cli_generate_sends_a_non_image_attachment_without_data(runner):
    """Mirrors _anthropic_content/_gemini_parts: the bytes are never
    fetched for a non-image, and the bridge renders the "[attached file:
    ...]" note from the entry that has no `data`."""
    body = _cli_body(runner, [{"role": "user", "content": "read this", "attachments": [
        {"id": "att-2", "filename": "notes.pdf", "mimeType": "application/pdf"}]}])
    assert body["attachments"] == [{"filename": "notes.pdf", "mimeType": "application/pdf"}]


def test_claude_cli_generate_sends_an_image_whose_fetch_failed_without_data(runner):
    body = _cli_body(runner, [{"role": "user", "content": "look", "attachments": [
        {"id": "att-3", "filename": "gone.png", "mimeType": "image/png"}]}], fetch=None)
    assert body["attachments"] == [{"filename": "gone.png", "mimeType": "image/png"}]


def test_claude_cli_generate_accepts_an_image_only_message_with_no_caption(runner):
    """An image with no caption used to raise "no content to send" -- the
    same empty-turn crash _gemini_parts documents on the other side."""
    body = _cli_body(runner, [{"role": "user", "content": "", "attachments": [
        {"id": "att-4", "filename": "photo.png", "mimeType": "image/png"}]}])
    assert body["prompt"] == ""
    assert len(body["attachments"]) == 1


def test_claude_cli_generate_normalises_a_null_caption_to_empty_string(runner):
    """merge_history copies `text` through unnormalised, and the bridge
    crashes on a None prompt (`message[:120]` -- TypeError, HTTP 500, one
    per retry). Agora coerces today, so this pins the boundary, not a bug."""
    body = _cli_body(runner, [{"role": "user", "content": None, "attachments": [
        {"id": "att-5", "filename": "photo.png", "mimeType": "image/png"}]}])
    assert body["prompt"] == ""


def test_claude_cli_generate_still_raises_when_there_is_no_content_at_all(runner):
    with pytest.raises(RuntimeError, match="no content to send"):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system",
            [{"role": "user", "content": "", "attachments": []}],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )


def test_claude_cli_generate_only_sends_the_newest_messages_attachments(runner):
    """Unlike the stateless APIs this provider sends only this turn; an
    older message's image already reached the CLI session when it was the
    newest one, and resending it would duplicate it."""
    body = _cli_body(runner, [
        {"role": "user", "content": "older", "attachments": [
            {"id": "old", "filename": "old.png", "mimeType": "image/png"}]},
        {"role": "assistant", "content": "a reply"},
        {"role": "user", "content": "newest", "attachments": [
            {"id": "new", "filename": "new.png", "mimeType": "image/png"}]},
    ])
    assert [a["filename"] for a in body["attachments"]] == ["new.png"]


def test_claude_cli_generate_sends_bridge_token_header_when_configured(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["headers"] = headers
        return 200, {"text": "ok", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "CLAUDE_BRIDGE_TOKEN", "secret-token"), \
         patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-haiku-4-5-20251001", False, "system", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Test"}, "conv-1",
        )
    assert captured["headers"]["x-bridge-token"] == "secret-token"


def test_generate_reply_dispatches_claude_cli_provider(runner):
    """reply.py's own dispatch -- model string 'claude-cli:<id>' routes here,
    same pattern as 'anthropic:'/'gemini:'."""
    persona = {"name": "Test", "model": "claude-cli:claude-haiku-4-5-20251001"}

    with patch.object(runner.reply, "claude_cli_generate", return_value="dispatched reply") as mock_gen:
        result = runner.generate_reply(
            persona, dict(runner.NO_CAPS), "system", [{"role": "user", "content": "hi"}], "conv-1",
        )
    assert result == "dispatched reply"
    mock_gen.assert_called_once()


def test_decide_turn_speaks_without_being_mentioned(runner):
    """A conversation holds one persona, so the owner never has to name it.
    The @mention requirement went with the second persona (agora#67)."""
    personas = [{"name": "Gemini", "role": "curator"}]
    thread = [{"sender": "Edvard", "text": "no name in this message at all"}]
    assert runner.decide_turn(thread, personas) == ["Gemini"]


def test_decide_turn_speaks_for_a_lone_persona_that_is_not_a_curator(runner):
    """The old code answered a non-curator only when @mentioned by name, so
    a lone listener would have gone silent once the @mention path went."""
    personas = [{"name": "Haiku", "role": "listener"}]
    thread = [{"sender": "Edvard", "text": "hello"}]
    assert runner.decide_turn(thread, personas) == ["Haiku"]


def test_decide_turn_does_not_reply_to_a_persona(runner):
    """The last visible message being a persona's is the end of the turn.

    Note what this does and does not pin: the old code also returned [] here,
    because it filtered a self-mention out before looking for a chain. What it
    pins is that the rule is now the sender check alone -- mutate that check
    away and this test is the one that fails. Proving the *chain* is gone would
    need a fixture with a second persona in the thread, which Agora refuses to
    create (agora#67), so there is no honest way to write it."""
    personas = [{"name": "Gemini", "role": "curator"}]
    thread = [
        {"sender": "Edvard", "text": "go"},
        {"sender": "Gemini", "text": "done, over to @Gemini"},
    ]
    assert runner.decide_turn(thread, personas) == []


def test_decide_turn_ignores_activity_messages_for_last_sender(runner):
    """An activity chip trailing the owner's message is a UI event, not a
    reply -- the persona's turn is still owed."""
    personas = [{"name": "Gemini", "role": "curator"}]
    thread = [
        {"sender": "Edvard", "text": "are you there?"},
        {"sender": "Gemini", "text": "vault_read: notes.md", "activity": {"capability": "vault_read", "detail": "notes.md"}},
    ]
    assert runner.decide_turn(thread, personas) == ["Gemini"]


def test_merge_history_excludes_activity_messages(runner):
    thread = [
        {"sender": "Edvard", "text": "a real question"},
        {"sender": "Gemini", "text": "vault_write: notes.md",
         "activity": {"capability": "vault_write", "detail": "notes.md"}},
        {"sender": "Gemini", "text": "a real reply"},
    ]
    merged = runner.merge_history(thread, "Gemini", False)
    all_content = " ".join(m["content"] for m in merged)
    assert "vault_write: notes.md" not in all_content
    assert "a real question" in all_content
    assert "a real reply" in all_content


def test_merge_history_excludes_thinking_messages(runner):
    thread = [
        {"sender": "Edvard", "text": "a real question"},
        {"sender": "Gemini", "text": "pondering...", "thinking": True},
        {"sender": "Gemini", "text": "a real reply"},
    ]
    merged = runner.merge_history(thread, "Gemini", False)
    all_content = " ".join(m["content"] for m in merged)
    assert "pondering..." not in all_content
    assert "a real question" in all_content
    assert "a real reply" in all_content


def test_decide_turn_ignores_thinking_messages_for_last_sender(runner):
    """A thinking chunk is not something a persona said to anyone, so it
    must not stand in for the reply that is still owed -- same reasoning as
    the activity-chip exclusion right above."""
    personas = [{"name": "Gemini", "role": "curator"}]
    thread = [
        {"sender": "Edvard", "text": "are you there?"},
        {"sender": "Gemini", "text": "let me think about that", "thinking": True},
    ]
    assert runner.decide_turn(thread, personas) == ["Gemini"]


def test_notify_sends_push_field_defaulting_true(runner):
    captured = {}

    def fake_agora_internal(method, path, payload=None):
        captured["payload"] = payload
        return 200, {"message": {"id": "m1"}}

    with patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        status, message_id = runner.notify("conv-1", "hi", "Agora")

    assert captured["payload"]["push"] is True
    assert status == 200
    assert message_id == "m1"


def test_notify_push_false_is_sent_through(runner):
    captured = {}

    def fake_agora_internal(method, path, payload=None):
        captured["payload"] = payload
        return 200, {"message": {"id": "m1"}}

    with patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        runner.notify("conv-1", "chunk", "Agora", push=False)

    assert captured["payload"]["push"] is False


def test_notify_thinking_defaults_false_and_is_sent_through_when_true(runner):
    captured = {}

    def fake_agora_internal(method, path, payload=None):
        captured["payload"] = payload
        return 200, {"message": {"id": "m1"}}

    with patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        runner.notify("conv-1", "chunk", "Agora")
    assert captured["payload"]["thinking"] is False

    with patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        runner.notify("conv-1", "a thought", "Agora", push=False, thinking=True)
    assert captured["payload"]["thinking"] is True
    assert captured["payload"]["push"] is False


def test_speak_streams_each_chunk_with_push_only_on_the_final_one(runner):
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}
    notify_calls = []

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None):
        on_text("preamble", False)
        on_text("final answer", True)
        return "final answer"

    def fake_notify(conversation_id, text, sender, system=False, push=True):
        notify_calls.append((text, push))
        return 200, f"mid-{len(notify_calls)}"

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", side_effect=fake_notify):
        reply = runner.speak(conversation, detail, [], "Test")

    assert notify_calls == [("preamble", False), ("final answer", True)]
    assert reply == "final answer"


def _speak_capturing_model(runner, detail, persona, model_override=None):
    """Runs speak() and returns the model_override generate_reply was
    handed -- which is what actually decides the model in reply.py."""
    seen = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None):
        seen["model_override"] = model_override
        return "ok"

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", return_value=(200, "mid-1")):
        runner.speak({"id": "conv-1"}, detail, [], "Test", model_override)
    return seen["model_override"]


def test_speak_prefers_the_conversations_model_over_the_personas(runner):
    """Idea #95 slice 1. One persona curates many conversations -- Nova's
    own, one per cycle -- so resolving off the persona moved all of them
    together whenever a model was picked in any one of them."""
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test",
              "model": "gemini:gemini-flash-latest"}

    assert _speak_capturing_model(runner, detail, persona) == "gemini:gemini-flash-latest"


def test_speak_falls_back_to_the_persona_when_the_conversation_has_no_model(runner):
    """Every conversation stored before the create route copied the model
    looks like this, and Agora's joined view sends the curator's model in
    that case -- but an empty string must not win over the persona here."""
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test",
              "model": ""}

    assert _speak_capturing_model(runner, detail, persona) is None


def test_speak_lets_an_explicit_per_message_override_beat_the_conversations_model(runner):
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test",
              "model": "gemini:gemini-flash-latest"}

    picked = _speak_capturing_model(runner, detail, persona, "claude-cli:claude-opus-5")
    assert picked == "claude-cli:claude-opus-5"


def test_speak_streams_thinking_chunks_with_thinking_true_and_push_false(runner):
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}
    notify_calls = []

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None):
        on_thinking("pondering the question...")
        on_text("final answer", True)
        return "final answer"

    def fake_notify(conversation_id, text, sender, system=False, push=True, thinking=False):
        notify_calls.append((text, push, thinking))
        return 200, f"mid-{len(notify_calls)}"

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", side_effect=fake_notify):
        reply = runner.speak(conversation, detail, [], "Test")

    assert notify_calls == [
        ("pondering the question...", False, True),
        ("final answer", True, False),
    ]
    assert reply == "final answer"


def test_speak_rolls_back_thinking_chunks_too_when_a_later_round_fails(runner):
    """A thinking chunk is a real posted message like any streamed text
    chunk -- a failed turn must roll it back too, same reasoning as the
    text-chunk rollback test right below."""
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}
    notify_calls = {"n": 0}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None):
        on_thinking("thinking that got posted")
        raise RuntimeError("simulated failure")

    def fake_notify(conversation_id, text, sender, system=False, push=True, thinking=False):
        notify_calls["n"] += 1
        return 200, f"mid-{notify_calls['n']}"

    deleted = []

    def fake_agora_internal(method, path, payload=None):
        if method == "DELETE":
            deleted.append(path)
        return 200, {}

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", side_effect=fake_notify), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        with pytest.raises(RuntimeError):
            runner.speak(conversation, detail, [], "Test")

    assert deleted == ["/conversations/conv-1/messages/mid-1"]


def test_speak_rolls_back_posted_chunks_when_a_later_round_fails(runner):
    """A turn that streams a preamble, then fails on a later round, must not
    leave that preamble as the thread's last message -- decide_turn would
    read it as 'the persona already replied' and never retry."""
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None):
        on_text("preamble that got posted", False)
        raise RuntimeError("simulated failure on the next round")

    deleted = []

    def fake_agora_internal(method, path, payload=None):
        if method == "DELETE":
            deleted.append(path)
        return 200, {}

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        with pytest.raises(RuntimeError):
            runner.speak(conversation, detail, [], "Test")

    assert deleted == ["/conversations/conv-1/messages/mid-1"]


def test_speak_rollback_is_best_effort_and_still_raises_the_original_error(runner):
    """If the DELETE cleanup call itself fails, the original generation
    error must still propagate -- a rollback failure shouldn't mask it."""
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    conversation = {"id": "conv-1"}
    detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}], "name": "Test"}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None):
        on_text("preamble", False)
        raise RuntimeError("original failure")

    def fake_agora_internal(method, path, payload=None):
        if method == "DELETE":
            raise RuntimeError("delete also failed")
        return 200, {}

    with patch.object(runner.conversations, "fetch_persona", return_value=persona), \
         patch.object(runner.conversations, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.conversations, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal):
        with pytest.raises(RuntimeError, match="original failure"):
            runner.speak(conversation, detail, [], "Test")


def test_run_heartbeat_is_not_streamed(runner):
    """2026-07-25: unlike speak(), a heartbeat must NOT stream -- see
    HEARTBEAT_NO_REPORT_SENTINEL's docstring. generate_reply must be
    called without on_text (or with it None), since posting chunks live
    would defeat the ability to suppress the whole reply once it's known
    to be the no-report sentinel."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None, unattended=False):
        captured["on_text"] = on_text
        return "a real report"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    assert captured["on_text"] is None


def test_run_heartbeat_posts_a_real_report_and_records_success(runner):
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    notify_calls = []

    def fake_notify(conversation_id, text, sender, system=False, push=True):
        notify_calls.append(text)
        return 200, "mid-1"

    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", return_value="found a CrashLoopBackOff in agents/foo"), \
         patch.object(runner.heartbeats, "notify", side_effect=fake_notify), \
         patch.object(runner.heartbeats, "audit") as mock_audit, \
         patch.object(runner.heartbeats, "agora_internal", side_effect=fake_agora_internal):
        runner.run_heartbeat(heartbeat)

    assert notify_calls == ["found a CrashLoopBackOff in agents/foo"]
    # Opening chip, then the closing one added 2026-08-05.
    chips = [call.args[3] for call in mock_audit.call_args_list]
    assert chips[0] == "HB (every@1h)"
    assert chips[1].startswith("HB finished in ")
    assert "replied" in heartbeat_updates[-1]["lastResult"]


# ---------------------------------------------------------------------------
# 2026-08-02: regular (non-workflow) heartbeats claim their run up front,
# the same way #25 made workflow-mode ones. The Evolve loop runs on a
# plain heartbeat since v2, so the path that had NO duplicate protection
# was the one doing the long, expensive, PR-opening runs.
# ---------------------------------------------------------------------------

def test_run_heartbeat_claims_run_before_executing(runner):
    """The claim PATCH (forceRun cleared, lastResult "running") must land
    BEFORE generate_reply, not only after. A cycle takes ~11 minutes;
    for that whole window the persisted state otherwise still read
    "still forced, never ran"."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@6h", "name": "Agora Evolve", "forceRun": True}
    persona = {"id": "p1", "name": "Evolve", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}

    events = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            events.append(("patch", payload))
        return 200, {}

    def fake_generate_reply(*args, **kwargs):
        events.append(("run", None))
        return "did a thing"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", side_effect=fake_agora_internal):
        runner.run_heartbeat(heartbeat)

    kinds = [kind for kind, _ in events]
    assert kinds == ["patch", "run", "patch"], kinds
    claim = events[0][1]
    assert claim["forceRun"] is False
    assert claim["lastResult"] == "running"
    # Anchors the next scheduled run to run START -- deliberate, same as
    # the workflow path.
    assert claim["lastRunAt"] <= events[-1][1]["lastRunAt"]


def test_run_heartbeat_claim_survives_the_run_being_killed_mid_flight(runner):
    """The bug this exists for, end to end. Evolve merges a PR into its
    own repo -> the deploy rolls the pod hosting its own in-flight cycle
    -> the process dies before run_heartbeat's final PATCH. Observed
    live twice on 2026-08-02: the replacement pod read `forceRun: true`
    (never cleared) and immediately started the same cycle over.

    A kill is not an exception -- run_heartbeat's `except Exception`
    never sees it -- so BaseException is the faithful simulation. What
    must hold is that the PERSISTED state left behind is already
    claimed, so the next poll doesn't find the heartbeat due."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@6h", "name": "Agora Evolve", "forceRun": True,
                 "enabled": True, "createdAt": "2026-08-02T00:00:00+00:00"}
    persona = {"id": "p1", "name": "Evolve", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}

    # Stands in for Agora's persisted heartbeat row.
    persisted = dict(heartbeat)

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            persisted.update(payload)
        return 200, {}

    def killed(*args, **kwargs):
        raise KeyboardInterrupt("SIGTERM: pod rolled mid-cycle")

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=killed), \
         patch.object(runner.heartbeats, "agora_internal", side_effect=fake_agora_internal):
        with pytest.raises(KeyboardInterrupt):
            runner.run_heartbeat(heartbeat)

    assert persisted["forceRun"] is False
    assert persisted["lastResult"] == "running"

    # ...and the replacement pod, re-reading exactly that persisted row,
    # must not consider the heartbeat due again.
    ran = []
    with patch.object(runner.heartbeats, "run_heartbeat", side_effect=lambda hb: ran.append(hb)):
        runner.run_due_heartbeats([persisted])
    assert ran == [], "a restart re-ran a cycle that was already claimed"


def test_run_heartbeat_claims_run_even_when_persona_missing(runner):
    """The claim is unconditional -- before fetch_persona -- so a
    heartbeat pointing at a deleted persona consumes its forceRun
    instead of being retried on every single poll tick forever."""
    heartbeat = {"id": "hb1", "personaId": "ghost", "conversationId": "conv-1",
                 "schedule": "every@6h", "name": "HB", "forceRun": True}
    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona", return_value=None), \
         patch.object(runner.heartbeats, "agora_internal", side_effect=fake_agora_internal):
        runner.run_heartbeat(heartbeat)

    assert heartbeat_updates[0]["lastResult"] == "running"
    assert "persona not found" in heartbeat_updates[-1]["lastResult"]


# ---------------------------------------------------------------------------
# 2026-07-25: K3s Sentinel was created via New Conversation without
# kubectlRead (fixed separately, agora#19), and even once it had the tool,
# a monitoring heartbeat reporting "all clear" every single run would be
# noise -- the owner's explicit ask: "only send a message back to the chat if
# it finds something worth reporting. A clean working cluster should not
# trigger a message." HEARTBEAT_NO_REPORT_SENTINEL is the mechanism: a
# heartbeat's own prompt can ask for this exact string when there's
# nothing to report, and run_heartbeat suppresses notify()/audit() for it.
# ---------------------------------------------------------------------------

def test_run_heartbeat_uses_rotated_conversation_id(runner):
    """2026-08-02: regular (non-workflow) heartbeats get the same
    per-cycle conversation rotation workflow-mode heartbeats already had
    -- a simple heartbeat can now be the whole Evolve loop, and it needs
    the same bounded-transcript protection."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "c-old",
                 "schedule": "every@6h", "name": "HB", "rotateConversationEachRun": True}
    persona = {"id": "p1", "name": "Test", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    old_detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}],
                  "messages": [], "stickyFallback": False}
    new_detail = {"personas": [{"personaId": "p1", "name": "Test", "role": "curator"}],
                  "messages": [], "stickyFallback": False}

    def fake_agora_get(path):
        if path.startswith("/conversations/c-old"):
            return 200, old_detail
        if path.startswith("/conversations/c-new"):
            return 200, new_detail
        return 200, {}

    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kwargs):
        captured["conversation_id"] = conversation_id
        return "a real report"

    notify_calls = []

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.heartbeats, "rotate_cycle_conversation", return_value="c-new") as mock_rotate, \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", side_effect=lambda cid, *a, **kw: notify_calls.append(cid) or (200, "m1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    mock_rotate.assert_called_once_with(heartbeat, old_detail["personas"])
    assert captured["conversation_id"] == "c-new"
    assert notify_calls == ["c-new"]


def test_run_heartbeat_folds_edvards_last_message_into_the_trigger(runner):
    """2026-08-02: claude-cli only ever sees history[-1] (the synthetic
    trigger) -- without this, a real message the owner typed into the
    conversation would be invisible to a claude-cli persona regardless
    of timing."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@6h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {
        "personas": [],
        "messages": [{"sender": "Edvard", "text": "please check on the deploy", "id": "m1"}],
        "stickyFallback": False,
    }
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kwargs):
        captured["history"] = history
        return "ok, checked"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "m1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    assert "please check on the deploy" in captured["history"][-1]["content"]


def test_run_heartbeat_trigger_stays_generic_when_last_message_is_from_persona(runner):
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@6h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {
        "personas": [],
        "messages": [{"sender": "Test", "text": "previous cycle's reply", "id": "m1"}],
        "stickyFallback": False,
    }
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kwargs):
        captured["history"] = history
        return "ok"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "m1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    assert "previous cycle's reply" not in captured["history"][-1]["content"]
    assert captured["history"][-1]["content"] == "[Automatic heartbeat trigger — address Edvard directly.]"


# ---------------------------------------------------------------------------
# 2026-08-05, the owner's two asks in one message: tell the persona when *he*
# started the run rather than the schedule, and give him a visible end to a
# run he currently has to guess at.
# ---------------------------------------------------------------------------

def _heartbeat_run(runner, heartbeat, reply="did a thing", raises=None, system_extra=""):
    """Drives run_heartbeat and returns (system prompt, last history entry,
    chip labels) so a test can assert on what the persona saw and what
    the owner saw."""
    persona = {"id": "p1", "name": "Nova", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kwargs):
        captured["system"] = system
        captured["history"] = history
        if raises is not None:
            raise raises
        return reply

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "build_system",
                      side_effect=lambda p, d, extra: extra + system_extra), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "m1")), \
         patch.object(runner.heartbeats, "audit") as mock_audit, \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    return captured["system"], captured["history"][-1]["content"], \
        [call.args[3] for call in mock_audit.call_args_list]


def test_run_heartbeat_tells_the_persona_edvard_triggered_it_by_hand(runner):
    """forceRun is the only trace of "the owner pressed Run", and the claim
    PATCH clears it server-side -- so if run_heartbeat doesn't read it from
    its own snapshot, nothing downstream can ever know."""
    system, trigger, chips = _heartbeat_run(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@6h", "name": "Nova", "forceRun": True})

    assert "manual trigger" in system
    # The schedule is still named -- "he started this rather than waiting for
    # every@6h" is the useful sentence, not "you have no schedule".
    assert "rather than waiting for the every@6h schedule" in system
    assert "It is not a direct reply to Edvard" in system
    # claude-cli personas only ever see history[-1], so the system prompt
    # alone would not reach the loop this was asked for.
    assert trigger.startswith("[Manual heartbeat trigger")
    assert chips[0] == "Nova (manual trigger)"


def test_run_heartbeat_still_reads_as_scheduled_when_it_is(runner):
    system, trigger, chips = _heartbeat_run(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@6h", "name": "Nova"})

    assert "an automatic scheduled turn (every@6h)" in system
    assert "manual" not in system.lower()
    assert trigger == "[Automatic heartbeat trigger — address Edvard directly.]"
    assert chips[0] == "Nova (every@6h)"


def test_run_heartbeat_closes_the_run_with_a_chip(runner):
    _system, _trigger, chips = _heartbeat_run(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@6h", "name": "Nova"}, reply="x" * 40)

    assert len(chips) == 2
    assert chips[1].startswith("Nova finished in ")
    assert chips[1].endswith("replied 40 chars")


def test_run_heartbeat_closes_the_run_even_when_it_fails(runner):
    """The case the owner actually loses time to: no reply is coming, and
    without this the thread's last entry stays the opening chip forever."""
    _system, _trigger, chips = _heartbeat_run(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@6h", "name": "Nova"}, raises=RuntimeError("bridge 503"))

    assert len(chips) == 2
    assert chips[1].startswith("Nova finished in ")
    assert "failed: bridge 503" in chips[1]


def test_run_heartbeat_leaves_a_silent_monitoring_run_silent(runner):
    """HEARTBEAT_NO_REPORT_SENTINEL exists so a clean check touches nothing.
    A "finished" chip every 10 minutes would be exactly the noise it
    prevents -- and there is no opening chip left dangling either."""
    _system, _trigger, chips = _heartbeat_run(
        runner,
        {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
         "schedule": "every@10m", "name": "Watchdog"},
        reply=runner.HEARTBEAT_NO_REPORT_SENTINEL,
        system_extra=runner.HEARTBEAT_NO_REPORT_SENTINEL)

    assert chips == []


def test_run_heartbeat_reports_a_crashed_monitoring_run(runner):
    """The sentinel buys silence for "nothing to report", not for a crash."""
    _system, _trigger, chips = _heartbeat_run(
        runner,
        {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
         "schedule": "every@10m", "name": "Watchdog"},
        raises=RuntimeError("boom"),
        system_extra=runner.HEARTBEAT_NO_REPORT_SENTINEL)

    assert len(chips) == 1
    assert "failed: boom" in chips[0]


def _rotating_heartbeat_run(runner, old_messages, persona_name="Test", older=None,
                            last_run_at=None):
    """Drives run_heartbeat through a real rotation (c-old -> c-new, the
    new conversation genuinely empty, as a freshly created one always is)
    and returns the history generate_reply was called with.

    `last_run_at` is the heartbeat's PREVIOUS run time, which is the
    boundary the cross-cycle lookback uses to tell a message written
    since the last run from one already offered to it. Left None by
    default -- "no previous run", so everything unanswered counts."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "c-old",
                 "schedule": "every@6h", "name": "HB", "rotateConversationEachRun": True,
                 "lastRunAt": last_run_at}
    persona = {"id": "p1", "name": persona_name, "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    personas = [{"personaId": "p1", "name": persona_name, "role": "curator"}]
    old_detail = {"personas": personas, "messages": old_messages, "stickyFallback": False}
    new_detail = {"personas": personas, "messages": [], "stickyFallback": False}

    # `older` maps an earlier cycle-conversation's id -> its messages,
    # as the pending-message lookback would find them via /conversations.
    listing = {"conversations": [
        {"id": cid, "name": f"HB — {cid}", "tags": [runner.cycle_tag("hb1")],
         "createdAt": f"2026-08-02T0{n}:00:00+00:00"}
        for n, cid in enumerate(sorted(older or {}))
    ]}

    def fake_agora_get(path):
        if path.startswith("/conversations/c-old"):
            return 200, old_detail
        if path.startswith("/conversations/c-new"):
            return 200, new_detail
        if path == "/conversations":
            return 200, listing
        for cid, messages in (older or {}).items():
            if path.startswith(f"/conversations/{cid}"):
                return 200, {"personas": personas, "messages": messages}
        return 200, {}

    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, **kwargs):
        captured["history"] = history
        return "ok"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.heartbeats, "rotate_cycle_conversation", return_value="c-new"), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "m1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)
    return captured["history"]


def test_run_heartbeat_carries_edvards_message_across_a_rotation(runner):
    """2026-08-02: rotation replaces `detail` with a brand-new EMPTY
    conversation, so the fold-in of the owner's last message could never
    fire on a rotating heartbeat -- the two halves of #27 cancelled each
    other out and anything he typed between cycles was dropped silently.
    Evolve's own heartbeat rotates, so this was live."""
    history = _rotating_heartbeat_run(
        runner, [{"sender": "Edvard", "text": "stop merging your own PRs", "id": "m1"}])

    assert "stop merging your own PRs" in history[-1]["content"]
    assert "the previous cycle's conversation" in history[-1]["content"]


def test_a_message_typed_while_the_cycle_was_running_is_still_carried(runner):
    """This test used to assert the opposite, on the theory that a persona
    message underneath the owner's meant he had been answered. In a cycle
    transcript that theory is wrong, and wrong in the most common case
    there is: he watches a run work, types something at minute ten, and at
    minute forty the run posts its own report -- built from a trigger
    assembled before he spoke, so it cannot possibly be a reply to him.
    The thread stops ending on him, and the old rule dropped his message
    silently and permanently.

    Nothing here is ever a reply to him: poll_once skips these
    conversations precisely so that ordinary turn-taking never answers in
    them, which is what makes "somebody replied below it" meaningless."""
    history = _rotating_heartbeat_run(runner, [
        {"sender": "Edvard", "text": "stop merging your own PRs", "id": "m1"},
        {"sender": "Test", "text": "Cycle 30 done — merged #40", "id": "m2"},
    ])

    assert "stop merging your own PRs" in history[-1]["content"]


def test_everything_he_typed_in_the_previous_cycle_is_carried_oldest_first(runner):
    """Not just his newest line. Two separate thoughts typed twenty
    minutes apart during one run are two messages, and reading only the
    last of them loses the first as completely as reading neither."""
    history = _rotating_heartbeat_run(runner, [
        {"sender": "Edvard", "text": "check the newspaper pod", "id": "m1"},
        {"sender": "Test", "text": "Bash: kubectl get pods", "id": "m2", "activity": True},
        {"sender": "Edvard", "text": "and the digest is stale", "id": "m3"},
    ])

    content = history[-1]["content"]
    assert content.index("check the newspaper pod") < content.index("and the digest is stale")


def test_run_heartbeat_carries_a_message_from_behind_a_dead_cycle(runner):
    """2026-08-02: #28's one-step lookback lands on an EMPTY conversation
    whenever the previous cycle was killed before it replied -- which has
    happened twice in one day, because merging into this repo rolls the
    pod running the cycle. Everything the owner typed the cycle before that
    was then dropped silently and forever. That is exactly why he says he
    can only reach this loop through vault files."""
    history = _rotating_heartbeat_run(runner, [], older={
        "c-cycle3": [{"sender": "Test", "text": "cycle 3's report", "id": "m1"},
                     {"sender": "Edvard", "text": "look at the PWA next", "id": "m2"}],
    })

    assert "look at the PWA next" in history[-1]["content"]


def test_a_message_already_offered_to_an_earlier_run_is_not_carried_again(runner):
    """The boundary of "already seen". An old cycle conversation ends on
    the owner forever -- it gets answered in the NEW conversation, never in
    itself -- so without a boundary every cycle would re-surface the same
    line for the rest of its life. Until 2026-08-05 that boundary was "a
    persona replied here"; it is now "this arrived before my last run",
    which is the same protection without also blinding the walk."""
    history = _rotating_heartbeat_run(runner, [], last_run_at="2026-08-04T00:00:00+00:00", older={
        "c-cycle2": [{"sender": "Edvard", "text": "ancient and long since answered",
                      "id": "m1", "ts": "2026-08-03T09:00:00+00:00"}],
        "c-cycle1": [{"sender": "Edvard", "text": "even older, also answered",
                      "id": "m0", "ts": "2026-08-02T09:00:00+00:00"}],
    })

    assert history[-1]["content"] == "[Automatic heartbeat trigger — address Edvard directly.]"


def test_a_message_written_into_an_older_thread_since_the_last_run_is_carried(runner):
    """The regression that cost a whole cycle on 2026-08-05. The owner typed
    a one-line note into a transcript from an earlier cycle; the walk
    stopped before reaching it (the previous cycle had replied, as a
    healthy one always does), so ordinary turn-taking answered it instead
    -- and for this loop that means a full, PR-opening Claude Code run.
    Reaching it here is what earns the right to skip it in poll_once."""
    history = _rotating_heartbeat_run(runner, [], last_run_at="2026-08-04T00:00:00+00:00", older={
        "c-cycle2": [{"sender": "Edvard", "text": "old news",
                      "id": "m1", "ts": "2026-08-03T09:00:00+00:00"},
                     {"sender": "Test", "text": "cycle 2 answered it",
                      "id": "m2", "ts": "2026-08-03T09:01:00+00:00"}],
        "c-cycle1": [{"sender": "Edvard", "text": "use the sealed secrets in platform-config",
                      "id": "m0", "ts": "2026-08-05T11:42:14+00:00"}],
    })

    assert "use the sealed secrets in platform-config" in history[-1]["content"]
    assert "old news" not in history[-1]["content"]


def test_every_unanswered_cycle_conversation_is_carried_oldest_first(runner):
    """Two dead cycles in a row, the owner writing into both: he gets read
    once, in the order he wrote, not just his newest line."""
    history = _rotating_heartbeat_run(runner, [], older={
        "c-cycle3": [{"sender": "Test", "text": "cycle 3's report", "id": "m1"},
                     {"sender": "Edvard", "text": "first thing", "id": "m2"}],
        "c-cycle4": [{"sender": "Edvard", "text": "second thing", "id": "m3"}],
    })

    content = history[-1]["content"]
    assert content.index("first thing") < content.index("second thing")


def test_lookback_ignores_activity_chips_left_by_a_cycle_that_never_replied(runner):
    """A cycle killed mid-run can leave tool-call chips behind having
    said nothing to anyone -- treating those as a reply would make the
    lookback stop exactly where it most needs to keep going."""
    history = _rotating_heartbeat_run(runner, [], older={
        "c-cycle3": [{"sender": "Test", "text": "ran a tool", "id": "m1", "activity": True},
                     {"sender": "Edvard", "text": "did that merge work?", "id": "m2"}],
        "c-cycle2": [{"sender": "Edvard", "text": "the one before", "id": "m0"}],
    })

    assert "did that merge work?" in history[-1]["content"]
    assert "the one before" in history[-1]["content"]


def test_the_lookback_walk_stays_bounded_by_cycle_lookback(runner):
    """Cost control, restated. The walk used to be lazy -- the healthy
    case (previous cycle replied) fetched nothing at all. Since
    2026-08-05 it runs every cycle, because being able to promise it
    reaches every un-archived cycle conversation is what lets poll_once
    stop firing whole runs at them. That trade is only defensible while
    the fan-out stays bounded: one listing plus at most CYCLE_LOOKBACK
    message fetches, however many old conversations exist."""
    heartbeat = {"id": "hb1", "conversationId": "c-new"}
    # Nothing from the owner anywhere: this test is about the fan-out, and a
    # carried message would only make the empty-result assertion below
    # measure something it isn't trying to measure.
    previous = {"personas": [], "messages": [
        {"sender": "Test", "text": "cycle 4's report", "id": "m2"}]}
    listing = {"conversations": [
        {"id": f"c-old{n}", "name": f"old {n}", "tags": [runner.cycle_tag("hb1")],
         "createdAt": f"2026-08-0{n}T01:00:00+00:00"}
        for n in range(1, 9)  # eight, comfortably more than the lookback
    ]}

    def fake_agora_get(path):
        if path == "/conversations":
            return 200, listing
        return 200, {"personas": [], "messages": []}

    with patch.object(runner.heartbeats, "agora_get",
                      side_effect=fake_agora_get) as mock_get:
        carried = runner.pending_across_cycles(heartbeat, previous)

    assert carried == []
    message_fetches = [c for c in mock_get.call_args_list
                       if c.args[0] != "/conversations"]
    assert len(message_fetches) == runner.heartbeats.CYCLE_LOOKBACK


def test_pending_across_cycles_drops_the_oldest_when_over_the_char_cap(runner):
    """The owner's own constraint on a long-lived channel: "i do not want
    Claude to read that every time as it can quickly be megabytes of
    tokens." Newest wins; nothing is truncated mid-sentence."""
    heartbeat = {"id": "hb1", "conversationId": "c-new"}
    previous = {"personas": [], "messages": [
        {"sender": "Edvard", "text": "N" * 3000, "id": "m2"}]}
    listing = {"conversations": [
        {"id": "c-old2", "name": "older", "tags": [runner.cycle_tag("hb1")],
         "createdAt": "2026-08-02T01:00:00+00:00"}]}

    def fake_agora_get(path):
        if path == "/conversations":
            return 200, listing
        return 200, {"personas": [], "messages": [
            {"sender": "Edvard", "text": "O" * 3000, "id": "m1"}]}

    with patch.object(runner.heartbeats, "agora_get", side_effect=fake_agora_get):
        carried = runner.pending_across_cycles(heartbeat, previous)

    assert [text[0] for _source, text in carried] == ["N"]


def test_pending_user_turn(runner):
    assert runner.pending_user_turn([]) is None
    assert runner.pending_user_turn([{"role": "user", "content": "hi"}]) == "hi"
    assert runner.pending_user_turn(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]) is None


def test_run_heartbeat_skips_notify_when_sentinel_returned(runner):
    # 2026-08-03: `task` now carries the sentinel instruction. That was
    # always how a heartbeat opts in (see the section comment above --
    # "a heartbeat's own prompt can ask for this exact string"), but the
    # setup never modelled it because nothing read it. The chip is now
    # posted at trigger time for everything EXCEPT heartbeats that opted
    # in this way, so the opt-in has to be real for this to still be a
    # test of the sentinel rather than of an unrelated default.
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB",
                 "task": f"Reply with exactly {runner.HEARTBEAT_NO_REPORT_SENTINEL} "
                         "if the cluster is healthy."}
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}

    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", return_value=runner.HEARTBEAT_NO_REPORT_SENTINEL), \
         patch.object(runner.heartbeats, "notify") as mock_notify, \
         patch.object(runner.heartbeats, "audit") as mock_audit, \
         patch.object(runner.heartbeats, "agora_internal", side_effect=fake_agora_internal):
        runner.run_heartbeat(heartbeat)

    mock_notify.assert_not_called()
    mock_audit.assert_not_called()
    assert "not posted" in heartbeat_updates[-1]["lastResult"]


# ---------------------------------------------------------------------------
# 2026-08-03, the owner: "Tool usage and heartbeats does show in the
# conversations, but are displayed after the process is finished... They are
# there to show that something is processing, but if its displayed after the
# process is done they serve no purpose other than hindsight logging. I want
# to see them immediately when they are triggered."
# ---------------------------------------------------------------------------

def _heartbeat_call_order(runner, *, task=None, reply="all done", raises=None):
    """Runs a heartbeat, returning the order of the calls that reach the chat."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@6h", "name": "HB"}
    if task:
        heartbeat["task"] = task
    persona = {"id": "p1", "name": "Test", "model": "claude-cli:claude-opus-5",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}

    calls = []

    def fake_generate_reply(*_args, **_kwargs):
        calls.append("generate_reply")
        if raises:
            raise raises
        return reply

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", side_effect=lambda *a, **k: calls.append("notify")), \
         patch.object(runner.heartbeats, "audit", side_effect=lambda *a, **k: calls.append("audit")), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)
    return calls


def test_heartbeat_chip_is_posted_before_the_model_is_called(runner):
    """The whole point of the chip is to show a run is in flight, so it has
    to land before the slow part, not after it. For a claude-cli cycle
    generate_reply is the ~45 minutes the owner spends staring at nothing."""
    assert _heartbeat_call_order(runner) == \
        ["audit", "generate_reply", "notify", "audit"]


def test_heartbeat_chip_survives_a_run_that_dies_midway(runner):
    """A cycle killed or failed after it started used to leave NO trace in
    the conversation at all -- the failure mode that made the owner think the
    loop had stopped. The up-front chip is now that trace -- and since
    2026-08-05 a closing one says the run is over, not still going."""
    assert _heartbeat_call_order(runner, raises=RuntimeError("boom")) == \
        ["audit", "generate_reply", "audit"]


def test_sentinel_heartbeat_posts_nothing_before_the_reply(runner):
    """Monitoring heartbeats keep their defining property: nothing lands in
    the chat until the reply is in hand, because a clean run must leave it
    untouched. A run that does report then opens and closes like any other
    -- the "only one chip" this test used to assert was about not
    double-posting the opening one, not about withholding the closing one."""
    task = f"Reply with exactly {runner.HEARTBEAT_NO_REPORT_SENTINEL} if healthy."
    assert _heartbeat_call_order(runner, task=task) == \
        ["generate_reply", "notify", "audit", "audit"]


def test_run_heartbeat_sentinel_match_is_whitespace_and_case_tolerant(runner):
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", return_value=f"  no_issues_found\n"), \
         patch.object(runner.heartbeats, "notify") as mock_notify, \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Decisions/0009 — Workflows: a data-driven Workflow/Step entity a Heartbeat
# can run instead of a single curator turn. Covers: the concurrency fix
# (the poll loop is otherwise fully sequential/blocking — a workflow runs on
# its own thread), the round-robin execution engine (continuous across step
# and sub-workflow boundaries), per-step tool-whitelist narrowing, and the
# scoped_write tool (path-locked server-side, never from model-supplied
# args, never gated by persona.capabilities.vaultWrite).
# ---------------------------------------------------------------------------

class _FakeThread:
    """Stand-in for threading.Thread that runs nothing — just records how
    it was constructed and whether .start() was called, so the concurrency
    fix can be asserted without ever spawning a real thread."""

    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return False


def test_run_due_heartbeats_spawns_thread_for_workflow_heartbeat(runner):
    heartbeat = {
        "id": "hb1", "name": "WF HB", "enabled": True, "forceRun": True,
        "workflowId": "wf1", "schedule": "every@1h",
        "createdAt": "2026-01-01T00:00:00+00:00", "lastRunAt": None,
        "conversationId": "c1", "personaId": "p1",
    }
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", {}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "run_heartbeat") as mock_run_hb:
        runner.run_due_heartbeats()

    assert len(created) == 1
    assert created[0].target is runner.run_workflow_heartbeat
    assert created[0].args == (heartbeat,)
    assert created[0].daemon is True
    assert created[0].started is True
    mock_run_hb.assert_not_called()


def test_run_due_heartbeats_skips_already_running_workflow(runner):
    heartbeat = {
        "id": "hb1", "name": "WF HB", "enabled": True, "forceRun": True,
        "workflowId": "wf1", "schedule": "every@1h",
        "createdAt": "2026-01-01T00:00:00+00:00", "lastRunAt": None,
        "conversationId": "c1", "personaId": "p1",
    }

    class _AliveThread:
        def is_alive(self):
            return True

    with patch.object(runner.heartbeats, "_heartbeat_threads", {"hb1": [_AliveThread()]}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread") as mock_thread_ctor:
        runner.run_due_heartbeats()

    mock_thread_ctor.assert_not_called()


def _plain_hb(**over):
    hb = {
        "id": "hb1", "name": "Nova", "enabled": True, "forceRun": False,
        "schedule": "every@18m", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": "2026-08-23T19:00:00+00:00", "conversationId": "c1",
        "personaId": "p1",
    }
    hb.update(over)
    return hb


class _AliveStub:
    def is_alive(self):
        return True


class _DeadStub:
    def is_alive(self):
        return False


def test_a_second_nova_cycle_spawns_while_the_first_is_still_running(runner):
    """The thing the owner's 18-minute cadence actually needs.

    Opening the bridge's invocation lock (CLAUDE_CLI_CONCURRENT) does
    nothing on its own: this guard sits a layer above it and used to drop
    the tick before the bridge was ever called, so a 45-minute cycle ate
    two of every three 18-minute slots.
    """
    heartbeat = _plain_hb(lastRunAt="2026-08-23T19:00:00+00:00")
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", {"hb1": [_AliveStub()]}), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", {"hb1": "older"}), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()

    assert len(created) == 1
    assert created[0].started is True


def test_a_fourth_concurrent_run_is_refused(runner):
    """3 is a bound on a runaway -- a hung run's thread never dies, and
    every later tick would stack another on it."""
    heartbeat = _plain_hb()
    running = [_AliveStub(), _AliveStub(), _AliveStub()]

    with patch.object(runner.heartbeats, "_heartbeat_threads", {"hb1": running}), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", {"hb1": "older"}), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread") as mock_thread_ctor, \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()

    mock_thread_ctor.assert_not_called()


def test_one_slot_cannot_spawn_twice_before_its_claim_lands(runner):
    """`run_heartbeat` PATCHes `lastRunAt` from inside its own thread, so
    for a moment after `thread.start()` a tick still reads the OLD
    `lastRunAt` and finds the SAME slot due. At a limit of 1 the thread
    guard covered that; at 3 a burst of ticks inside the window would
    spawn three runs for one slot."""
    heartbeat = _plain_hb(lastRunAt="2026-08-23T19:00:00+00:00")
    marks = {}
    threads = {}
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        t.is_alive = lambda: True
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", threads), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", marks), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()   # spawns for this slot
        runner.run_due_heartbeats()   # same lastRunAt: claim has not landed
        runner.run_due_heartbeats()

    assert len(created) == 1

    # ...and the moment the claim does land, the next slot spawns.
    heartbeat["lastRunAt"] = "2026-08-23T19:18:00+00:00"
    with patch.object(runner.heartbeats, "_heartbeat_threads", threads), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", marks), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()

    assert len(created) == 2


def test_a_run_that_dies_without_claiming_does_not_wedge_the_heartbeat(runner):
    """The regression the spawn-mark introduced, found in review of #308.

    `_heartbeat_spawn_marks` is only ever REPLACED by a different
    `lastRunAt`. So a run that dies without moving it -- claim PATCH
    fails in an Agora blip, then the thread dies before the final PATCH,
    which is the case `run_heartbeat` explicitly calls "not fatal" --
    left a mark that matched every later tick forever, and the heartbeat
    never ran again. The old one-at-a-time guard could not do this: a
    dead thread always meant "spawn on the next tick".
    """
    heartbeat = _plain_hb(lastRunAt="2026-08-23T19:00:00+00:00")
    threads = {}
    marks = {}
    created = []
    liveness = {"alive": True}

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        t.is_alive = lambda: liveness["alive"]
        created.append(t)
        return t

    def tick():
        with patch.object(runner.heartbeats, "_heartbeat_threads", threads), \
             patch.object(runner.heartbeats, "_heartbeat_spawn_marks", marks), \
             patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
             patch.object(runner.heartbeats, "agora_internal",
                          return_value=(200, {"heartbeats": [heartbeat]})), \
             patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
             patch.object(runner.heartbeats, "schedule_due", return_value=True):
            runner.run_due_heartbeats()

    tick()
    assert len(created) == 1

    # The run dies. `lastRunAt` never moved, because both PATCHes failed.
    liveness["alive"] = False
    tick()
    tick()

    assert len(created) == 3, "a dead unclaimed run wedged the heartbeat for good"


def _scheduler_lines(runner, heartbeat, threads, marks, drops, limit=3,
                     spawned=None, ticks=1):
    """Run `run_due_heartbeats` `ticks` times and return what log() printed.

    Every scheduling decision used to go through debug_log(), which is
    off on the runner deployment, so these lines are the whole of what an
    operator can see. `spawned` collects each thread constructed.
    """
    printed = []

    def ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        if spawned is not None:
            spawned.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", threads), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", marks), \
         patch.object(runner.heartbeats, "_heartbeat_dropped_ticks", drops), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", limit), \
         patch.object(runner.heartbeats, "log", side_effect=printed.append), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        for _ in range(ticks):
            runner.run_due_heartbeats()
    return printed


def test_a_started_run_says_so_where_debug_logging_is_off(runner):
    """Nothing in this module announced that a run had STARTED.

    `heartbeat <name>: <result>` is printed by a run that finished, so a
    cycle that never began and a cycle that was never due looked
    identical in the log. At 18 minutes that makes the first symptom of a
    scheduling bug a missing cycle with no evidence at all.
    """
    printed = _scheduler_lines(runner, _plain_hb(), {}, {}, {})

    starts = [line for line in printed if "starting run" in line]
    assert len(starts) == 1, printed
    assert "Nova" in starts[0]
    assert "1 now in flight (limit 3)" in starts[0]


def test_dropped_ticks_are_reported_logarithmically_not_every_five_seconds(runner):
    """The poll loop ticks every POLL_INTERVAL_SECONDS (5s by default), so
    a 45-minute cycle holding the last slot is ~540 ticks. Logging each
    one buries the signal in itself; logging none of them is what this
    change is fixing."""
    threads = {"hb1": [_AliveStub(), _AliveStub(), _AliveStub()]}
    drops = {}
    spawned = []

    printed = _scheduler_lines(runner, _plain_hb(), threads, {"hb1": "older"},
                               drops, spawned=spawned, ticks=540)

    assert spawned == []
    dropped = [line for line in printed if "due tick(s) dropped" in line]
    assert len(dropped) == 10, printed[:12]   # 1,2,4,...,512
    assert "1 due tick(s) dropped" in dropped[0]
    assert "3 run(s) in flight, limit 3" in dropped[0]
    assert drops["hb1"] == 540, "the silent drops still have to be counted"


def test_a_wedged_heartbeat_keeps_saying_so_and_never_goes_quiet(runner):
    """A run thread that hangs has no timeout, on purpose, so "the total is
    reported on the next start" is a promise that can never come due. Under
    a log-once rule that is one line and then permanent silence through an
    ongoing outage — which is the original bug, back again, in the exact
    case this change was written for."""
    threads = {"hb1": [_AliveStub(), _AliveStub(), _AliveStub()]}
    drops = {}

    first = _scheduler_lines(runner, _plain_hb(), threads, {"hb1": "older"},
                             drops, ticks=64)
    later = _scheduler_lines(runner, _plain_hb(), threads, {"hb1": "older"},
                             drops, ticks=960)   # ~80 more minutes wedged

    assert [ln for ln in later if "due tick(s) dropped" in ln], (
        "a heartbeat still wedged an hour later printed nothing at all")
    assert "1024 due tick(s) dropped since the last start" in later[-1], later[-3:]
    # Rarer as it goes on, and that is the design: 7 lines over the first
    # 64 declined ticks, 4 over the next 960. It thins out; it never stops.
    assert len(later) < len(first) < 20, "and it must not become a flood either"


def test_the_next_start_reports_how_many_ticks_were_dropped(runner):
    """The count is the half that makes the single line above enough:
    without it the log says the schedule started slipping and never says
    by how much."""
    heartbeat = _plain_hb()
    threads = {"hb1": [_AliveStub()]}
    drops = {}

    _scheduler_lines(runner, heartbeat, threads, {}, drops, limit=1, ticks=3)
    assert drops["hb1"] == 3

    threads["hb1"] = [_DeadStub()]
    printed = _scheduler_lines(runner, heartbeat, threads, {}, drops, limit=1)

    starts = [line for line in printed if "starting run" in line]
    assert len(starts) == 1, printed
    assert "3 due tick(s) dropped since the last start" in starts[0]
    assert "hb1" not in drops, "the counter has to reset, or it only ever grows"


def test_force_run_is_exempt_from_the_spawn_mark(runner):
    """The owner pressing "run now" is a new request, not one slot read
    twice -- and under the old guard it silently no-opped whenever a
    cycle was already in flight."""
    heartbeat = _plain_hb(forceRun=True, lastRunAt="2026-08-23T19:00:00+00:00")
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", {"hb1": [_AliveStub()]}), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks",
                      {"hb1": "2026-08-23T19:00:00+00:00"}), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor):
        runner.run_due_heartbeats()

    assert len(created) == 1


def test_a_workflow_heartbeat_never_overlaps_itself(runner):
    """v1's duplicate PRs came from a workflow step re-entering itself.
    The switch the owner asked for is about Nova's own cycle."""
    heartbeat = _plain_hb(workflowId="wf1")

    with patch.object(runner.heartbeats, "_heartbeat_threads", {"hb1": [_AliveStub()]}), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", {"hb1": "older"}), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread") as mock_thread_ctor, \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()

    mock_thread_ctor.assert_not_called()


def test_finished_runs_are_pruned_from_the_registry(runner):
    """Without pruning, the list is "runs ever started" and the limit
    would permanently wedge the heartbeat after three cycles."""
    heartbeat = _plain_hb()
    threads = {"hb1": [_DeadStub(), _DeadStub(), _DeadStub()]}
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", threads), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", {}), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()

    assert len(created) == 1
    assert threads["hb1"] == [created[0]]


def test_a_heartbeat_that_has_never_run_still_starts(runner):
    """The bug that stopped all three weekly Nova heartbeats dead.

    `lastRunAt` is None on a heartbeat that has never run, and
    `_heartbeat_spawn_marks.get(id)` also returns None when no mark is
    recorded. The guard compared the two and read "I already spawned this
    slot" on the very first due tick -- so the run never started, so
    `lastRunAt` stayed None, so every later tick matched too. Measured live
    2026-08-25: created 08-24, `lastRunAt: null`, and the runner log showed
    256 dropped ticks against `lastRunAt=None`.
    """
    heartbeat = _plain_hb(id="hb-new", name="Nova - ideas & research",
                          schedule="cron@0 6 * * 2,4,6", lastRunAt=None)
    marks = {}
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", {}), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", marks), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        runner.run_due_heartbeats()

    assert len(created) == 1, "a never-run heartbeat must start on its first due tick"


def test_the_spawn_mark_still_guards_a_never_run_heartbeat(runner):
    """The other half, so the fix above cannot be "delete the guard".

    Once the first run is in flight and `lastRunAt` has not moved yet, a
    second due tick must still be dropped -- that window is the whole
    reason the mark exists, and None is a legitimate value inside it.
    """
    heartbeat = _plain_hb(id="hb-new", schedule="cron@0 6 * * 2,4,6",
                          lastRunAt=None)
    marks = {}
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads",
                      {"hb-new": [_AliveStub()]}), \
         patch.object(runner.heartbeats, "_heartbeat_spawn_marks", marks), \
         patch.object(runner.heartbeats, "HEARTBEAT_MAX_CONCURRENT", 3), \
         patch.object(runner.heartbeats, "agora_internal",
                      return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "schedule_due", return_value=True):
        marks["hb-new"] = None  # this slot was already spawned against None
        runner.run_due_heartbeats()

    assert created == [], "an unclaimed in-flight slot must not spawn twice"


def test_default_concurrency_is_one_unless_the_bridge_lane_is_open(monkeypatch):
    """The default has to preserve today's behaviour exactly: with the
    bridge lock shut, a second cycle would only queue behind the first
    and burn its own 45-minute cap waiting."""
    import importlib
    import agora_runner.config as config_module

    try:
        monkeypatch.delenv("HEARTBEAT_MAX_CONCURRENT", raising=False)
        monkeypatch.delenv("CLAUDE_CLI_CONCURRENT", raising=False)
        assert importlib.reload(config_module).HEARTBEAT_MAX_CONCURRENT == 1

        monkeypatch.setenv("CLAUDE_CLI_CONCURRENT", "1")
        assert importlib.reload(config_module).HEARTBEAT_MAX_CONCURRENT == 3

        monkeypatch.setenv("HEARTBEAT_MAX_CONCURRENT", "5")
        assert importlib.reload(config_module).HEARTBEAT_MAX_CONCURRENT == 5

        # A typo must not silently stop the heartbeat loop.
        monkeypatch.setenv("HEARTBEAT_MAX_CONCURRENT", "three")
        assert importlib.reload(config_module).HEARTBEAT_MAX_CONCURRENT == 3
    finally:
        # A failed assertion above must not leave `agora_runner.config`
        # reloaded from this test's env for the rest of the session.
        monkeypatch.delenv("HEARTBEAT_MAX_CONCURRENT", raising=False)
        monkeypatch.delenv("CLAUDE_CLI_CONCURRENT", raising=False)
        importlib.reload(config_module)


def test_run_due_heartbeats_spawns_thread_for_plain_heartbeat_too(runner):
    """2026-08-08: an ordinary heartbeat used to run inline on the poll
    loop, freezing every conversation for the length of the run. That was
    tolerable while the only long one was the 6-hourly Nova cycle (~15m
    measured, ~4% of the day); at every@72m it is ~21% of the day, so
    both paths now go to a thread."""
    heartbeat = {
        "id": "hb2", "name": "Plain HB", "enabled": True, "forceRun": True,
        "schedule": "every@1h", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", {}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "run_heartbeat") as mock_run_hb:
        runner.run_due_heartbeats()

    assert len(created) == 1
    assert created[0].target is mock_run_hb
    assert created[0].args == (heartbeat,)
    assert created[0].daemon is True
    assert created[0].started is True
    # Dispatched, not executed inline — the poll loop must be free again
    # before the run finishes.
    mock_run_hb.assert_not_called()


def test_run_due_heartbeats_skips_a_plain_heartbeat_already_running(runner):
    """The claim PATCH is not enough on its own here. It sets lastRunAt to
    the run's START, so an anchored schedule whose next slot arrives while
    the run is still going reads as due — and before the runs were
    threaded, that could not happen because the loop was blocked."""
    heartbeat = {
        "id": "hb2", "name": "Plain HB", "enabled": True, "forceRun": True,
        "schedule": "every@72m@22:00", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }

    class _AliveThread:
        def is_alive(self):
            return True

    with patch.object(runner.heartbeats, "_heartbeat_threads", {"hb2": [_AliveThread()]}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread") as mock_thread_ctor, \
         patch.object(runner.heartbeats, "run_heartbeat") as mock_run_hb:
        runner.run_due_heartbeats()

    mock_thread_ctor.assert_not_called()
    mock_run_hb.assert_not_called()


def test_run_due_heartbeats_force_run_bypasses_disabled(runner):
    heartbeat = {
        "id": "hb3", "name": "Disabled HB", "enabled": False, "forceRun": True,
        "schedule": "every@1h", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }
    created = []

    def fake_thread_ctor(target=None, args=(), daemon=None):
        t = _FakeThread(target=target, args=args, daemon=daemon)
        created.append(t)
        return t

    with patch.object(runner.heartbeats, "_heartbeat_threads", {}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread", side_effect=fake_thread_ctor), \
         patch.object(runner.heartbeats, "run_heartbeat"):
        runner.run_due_heartbeats()

    assert len(created) == 1
    assert created[0].started is True


def test_run_due_heartbeats_disabled_without_force_run_is_skipped(runner):
    heartbeat = {
        "id": "hb4", "name": "Disabled HB", "enabled": False, "forceRun": False,
        "schedule": "every@1h", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }
    with patch.object(runner.heartbeats, "_heartbeat_threads", {}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.heartbeats, "run_heartbeat") as mock_run_hb, \
         patch.object(runner.threading, "Thread") as mock_thread_ctor:
        runner.run_due_heartbeats()

    mock_run_hb.assert_not_called()
    mock_thread_ctor.assert_not_called()


def test_workflow_bound_conversation_ids_only_counts_enabled_workflow_heartbeats(runner):
    heartbeats_list = [
        {"enabled": True, "workflowId": "wf1", "conversationId": "c1"},
        {"enabled": False, "workflowId": "wf2", "conversationId": "c2"},  # disabled
        {"enabled": True, "conversationId": "c3"},  # no workflowId -- plain heartbeat
        {"enabled": True, "workflowId": "wf3", "conversationId": "c4"},
    ]
    assert runner.workflow_bound_conversation_ids(heartbeats_list) == {"c1", "c4"}


def test_poll_once_skips_workflow_bound_conversations_but_still_runs_heartbeats(runner):
    conversations_body = {"conversations": [{"id": "c1", "name": "Evolve"}, {"id": "c2", "name": "Normal Chat"}]}
    heartbeats_body = {"heartbeats": [{"enabled": True, "workflowId": "wf1", "conversationId": "c1"}]}

    def fake_agora_get(path):
        if path == "/conversations":
            return 200, conversations_body
        return 404, {}

    def fake_agora_internal(method, path, payload=None):
        if method == "GET" and path == "/heartbeats":
            return 200, heartbeats_body
        return 200, {}

    polled = []
    with patch.object(runner.poll, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.poll, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.poll, "poll_conversation", side_effect=lambda s: polled.append(s["id"])), \
         patch.object(runner.poll, "acknowledge_deferred"), \
         patch.object(runner.poll, "run_due_heartbeats") as mock_run_due:
        runner.poll_once()

    assert polled == ["c2"]  # c1 skipped (workflow-bound), c2 gets ordinary turn-taking
    mock_run_due.assert_called_once_with(heartbeats_body["heartbeats"])


def test_cycle_bound_conversation_ids_only_counts_enabled_rotating_heartbeats(runner):
    """Keyed on rotateConversationEachRun, not on being heartbeat-bound
    at all: a non-rotating heartbeat's conversation (K3s Sentinel) is a
    durable channel the owner may chat in and must keep ordinary
    turn-taking."""
    heartbeats_list = [
        {"enabled": True, "rotateConversationEachRun": True, "conversationId": "c1"},
        {"enabled": False, "rotateConversationEachRun": True, "conversationId": "c2"},  # disabled
        {"enabled": True, "conversationId": "c3"},  # non-rotating -- K3s Sentinel
        {"enabled": True, "rotateConversationEachRun": True},  # never bound
        {"enabled": True, "rotateConversationEachRun": True, "conversationId": "c5"},
    ]
    assert runner.cycle_bound_conversation_ids(heartbeats_list) == {"c1", "c5"}


def test_poll_once_answers_live_cycle_conversation_and_a_plain_heartbeats(runner):
    """A non-rotating heartbeat's conversation (K3s Sentinel) and a normal
    chat have always answered right away, and still do. Since 2026-08-19
    the live cycle transcript joins them at the owner's ask.

    This test used to assert the opposite for `cycle9`, on his 2026-08-03
    report that replying there fired an immediate full cycle. He reversed
    that deliberately -- routine notes go through the app's capture flow
    now, so a message here is him addressing that session on purpose. The
    part he kept is pinned by the retired-conversation test below."""
    conversations_body = {"conversations": [
        {"id": "cycle9", "name": "Nova — Cycle 9"},
        {"id": "sentinel", "name": "K3s Sentinel"},
        {"id": "chat", "name": "Normal Chat"},
    ]}
    heartbeats_body = {"heartbeats": [
        {"enabled": True, "rotateConversationEachRun": True, "conversationId": "cycle9"},
        {"enabled": True, "conversationId": "sentinel"},
    ]}

    def fake_agora_get(path):
        if path == "/conversations":
            return 200, conversations_body
        return 404, {}

    def fake_agora_internal(method, path, payload=None):
        if method == "GET" and path == "/heartbeats":
            return 200, heartbeats_body
        return 200, {}

    polled = []
    with patch.object(runner.poll, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.poll, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.poll, "poll_conversation", side_effect=lambda s: polled.append(s["id"])), \
         patch.object(runner.poll, "acknowledge_deferred"), \
         patch.object(runner.poll, "run_due_heartbeats") as mock_run_due:
        runner.poll_once()

    assert polled == ["cycle9", "sentinel", "chat"]
    mock_run_due.assert_called_once_with(heartbeats_body["heartbeats"])


@contextlib.contextmanager
def _run_in_flight_for(runner, heartbeat_id):
    """Make `heartbeat_id` look like it has a run executing right now.

    `_run_in_flight` reads `run_due_heartbeats`' own thread registry
    rather than a second notion of "busy", so a test that wants the
    in-flight branch has to put something alive in that registry. A stub
    with `is_alive()` is enough and does not start a real thread -- a real
    one would have to be joined, and its liveness would be a race.
    """
    threads = runner.heartbeats._heartbeat_threads
    previous = threads.get(heartbeat_id)

    class _Alive:
        def is_alive(self):
            return True

    threads[heartbeat_id] = [_Alive()]
    try:
        yield
    finally:
        if previous is None:
            threads.pop(heartbeat_id, None)
        else:
            threads[heartbeat_id] = previous


def test_poll_once_answers_a_retired_cycle_conversation_too(runner):
    """2026-08-20: this test used to assert the opposite, and the reversal
    is the owner's, twice over.

    The original rule (2026-08-05) skipped every cycle transcript because
    one line typed into an old thread fired a full Claude Code cycle nine
    seconds later, on quota he had not chosen to spend. On 2026-08-19 he
    took the live transcript back out of the skip set; on 2026-08-20 he
    took the rest -- "you should actually answer my responds and do
    actual work immediately. Like the good old days." What made the old
    rule necessary was routine notes landing here, and those go through
    the app's capture flow now.

    So every un-archived cycle transcript is polled, and the one thing
    that still is not is a transcript whose own run is in flight -- see
    the in-flight test below, which is now the only row this skip set
    holds."""
    tag = runner.cycle_tag("hb1")
    conversations_body = {"conversations": [
        {"id": "cycle9", "name": "Nova — Cycle 9", "tags": [tag],
         "createdAt": "2026-08-05T10:00:00+00:00"},
        {"id": "cycle8", "name": "Nova — Cycle 8", "tags": [tag],
         "createdAt": "2026-08-05T04:00:00+00:00"},
        {"id": "cycle3", "name": "Nova — Cycle 3", "tags": [tag],
         "createdAt": "2026-08-01T04:00:00+00:00", "archived": True},
        {"id": "chat", "name": "Normal Chat"},
    ]}
    heartbeats_body = {"heartbeats": [
        {"id": "hb1", "enabled": True, "rotateConversationEachRun": True,
         "conversationId": "cycle9"},
    ]}

    def fake_agora_get(path):
        if path == "/conversations":
            return 200, conversations_body
        return 404, {}

    def fake_agora_internal(method, path, payload=None):
        if method == "GET" and path == "/heartbeats":
            return 200, heartbeats_body
        return 200, {}

    polled = []
    with patch.object(runner.poll, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.poll, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.poll, "poll_conversation", side_effect=lambda s: polled.append(s["id"])), \
         patch.object(runner.poll, "acknowledge_deferred"), \
         patch.object(runner.poll, "run_due_heartbeats"):
        runner.poll_once()

    assert polled == ["cycle9", "cycle8", "cycle3", "chat"]


def test_message_in_an_in_flight_cycle_conversation_still_reaches_the_next_trigger(runner):
    """The two halves of the deal, asserted together: poll_once must NOT
    answer the owner in a transcript whose own cycle is still running, and
    run_heartbeat MUST then carry what he wrote into the next scheduled
    run's trigger.

    Skipping is only defensible because of the second half. If either
    side is ever changed alone, his message is silently dropped -- which
    is the exact bug #28/#30 were opened for -- so they are pinned in
    one test rather than two that could drift apart.

    2026-08-20: the deferred set shrank to exactly this one case, so the
    fixture had to shrink with it. `c-live` is `hb1`'s current transcript
    AND `hb1` has a live run thread, which is the only remaining reason
    to hold an answer back; `c-old` is retired and is now answered on the
    spot like anything else."""
    conversations_body = {"conversations": [
        {"id": "c-live", "name": "Nova — Cycle 10"},
        {"id": "c-old", "name": "Nova — Cycle 9",
         "tags": [runner.cycle_tag("hb1")], "createdAt": "2026-08-19T09:00:00Z"},
    ]}
    heartbeats_body = {"heartbeats": [
        {"id": "hb1", "enabled": True, "rotateConversationEachRun": True,
         "conversationId": "c-live"},
    ]}

    polled = []
    with _run_in_flight_for(runner, "hb1"), \
         patch.object(runner.poll, "agora_get",
                      side_effect=lambda p: (200, conversations_body) if p == "/conversations" else (404, {})), \
         patch.object(runner.poll, "agora_internal",
                      side_effect=lambda m, p, payload=None: (200, heartbeats_body)), \
         patch.object(runner.poll, "poll_conversation", side_effect=lambda s: polled.append(s["id"])), \
         patch.object(runner.poll, "acknowledge_deferred"), \
         patch.object(runner.poll, "run_due_heartbeats"):
        runner.poll_once()

    # The transcript its own cycle is writing into is left alone; the
    # retired one gets an ordinary answer.
    assert polled == ["c-old"]

    history = _rotating_heartbeat_run(
        runner, [{"sender": "Edvard", "text": "look at the PWA next", "id": "m1"}])

    assert "look at the PWA next" in history[-1]["content"]


def test_every_cycle_conversation_answers_edvard_on_the_spot(runner):
    """The owner's ask, 2026-08-20, after getting the Noted chip in a retired
    cycle's thread: "What? I thought i could have a conversation with you
    again? ... you should actually answer my responds and do actual work
    immediately."

    Pinned as its own test because it is the assertion that reverses
    runner#45, and a future cycle reading only that PR's tests would
    otherwise re-derive the old rule and put the skip back. The workflow
    conversation stays skipped -- that rule is a different one and this
    ask did not touch it."""
    conversations_body = {"conversations": [
        {"id": "c-live", "name": "Nova — Cycle 10"},
        {"id": "c-old", "name": "Nova — Cycle 9",
         "tags": [runner.cycle_tag("hb1")], "createdAt": "2026-08-19T09:00:00Z"},
        {"id": "wf-conv", "name": "Some Workflow"},
    ]}
    heartbeats_body = {"heartbeats": [
        {"id": "hb1", "enabled": True, "rotateConversationEachRun": True,
         "conversationId": "c-live"},
        {"id": "hb2", "enabled": True, "workflowId": "wf1", "conversationId": "wf-conv"},
    ]}

    polled, acked, chipped = [], [], []
    with patch.object(runner.poll, "agora_get",
                      side_effect=lambda p: (200, conversations_body) if p == "/conversations" else (404, {})), \
         patch.object(runner.poll, "agora_internal",
                      side_effect=lambda m, p, payload=None: (200, heartbeats_body)), \
         patch.object(runner.poll, "poll_conversation",
                      side_effect=lambda s: polled.append(s["id"]) or True), \
         patch.object(runner.poll, "acknowledge_deferred",
                      side_effect=lambda s: acked.append(s["id"])), \
         patch.object(runner.poll, "mark_answered_live",
                      side_effect=lambda s: chipped.append(s["id"])), \
         patch.object(runner.poll, "run_due_heartbeats"):
        runner.poll_once()

    assert polled == ["c-live", "c-old"]   # the workflow one stays skipped
    assert acked == []                     # nothing is deferred, so no chip
    assert chipped == ["c-live", "c-old"]  # both are marked answered


def test_no_answered_live_chip_when_the_turn_did_not_speak(runner):
    """poll_conversation returns falsy for every reason it declined to
    reply (archived, backoff, last sender not the owner). Stamping the chip
    anyway would tell the next run a message had been answered when
    nothing was said, and that message would then never be carried --
    the silent drop #28/#30 exist to prevent."""
    conversations_body = {"conversations": [{"id": "c-live", "name": "Nova — Cycle 10"}]}
    heartbeats_body = {"heartbeats": [
        {"id": "hb1", "enabled": True, "rotateConversationEachRun": True,
         "conversationId": "c-live"},
    ]}

    chipped = []
    with patch.object(runner.poll, "agora_get",
                      side_effect=lambda p: (200, conversations_body) if p == "/conversations" else (404, {})), \
         patch.object(runner.poll, "agora_internal",
                      side_effect=lambda m, p, payload=None: (200, heartbeats_body)), \
         patch.object(runner.poll, "poll_conversation", return_value=None), \
         patch.object(runner.poll, "acknowledge_deferred"), \
         patch.object(runner.poll, "mark_answered_live",
                      side_effect=lambda s: chipped.append(s["id"])), \
         patch.object(runner.poll, "run_due_heartbeats"):
        runner.poll_once()

    assert chipped == []


def test_a_live_answered_message_is_not_carried_into_the_next_trigger(runner):
    """The other half of the split: without this, a message answered in
    real time is answered a SECOND time by the next scheduled cycle,
    which is the expensive surprise run runner#45 was opened to stop --
    arriving through the front door instead of the back."""
    detail = {"messages": [
        {"sender": "Edvard", "text": "how did that go?", "ts": "2026-08-19T20:00:00Z"},
        {"sender": "Nova", "text": "it went fine", "ts": "2026-08-19T20:00:05Z"},
        {"sender": "Nova", "ts": "2026-08-19T20:00:06Z",
         "activity": {"capability": runner.deferred.ANSWERED_LIVE_CAPABILITY}},
        {"sender": "Edvard", "text": "and the deploy?", "ts": "2026-08-19T20:05:00Z"},
    ]}

    assert runner.heartbeats._unread_from_edvard(detail) == "and the deploy?"


def test_a_message_typed_mid_cycle_is_still_carried(runner):
    """The case the chip must NOT swallow. A message typed while a cycle
    is running is deferred rather than answered live (the heartbeat drops
    out of the live set for the length of its run), so the cycle's own
    report lands underneath him and the thread stops ending on him. That
    report stamps no chip, which is the whole reason the marker is a chip
    and not "a persona spoke after him"."""
    detail = {"messages": [
        {"sender": "Edvard", "text": "check the token too", "ts": "2026-08-19T20:00:00Z"},
        {"sender": "Nova", "text": "Cycle 267 report...", "ts": "2026-08-19T20:40:00Z"},
    ]}

    assert runner.heartbeats._unread_from_edvard(detail) == "check the token too"


def test_capabilities_for_step_empty_whitelist_is_unrestricted(runner):
    persona = {"capabilities": {
        "webSearch": True, "vaultRead": True, "vaultWrite": True,
        "codeExecution": False, "kubectlRead": True, "githubRead": False,
    }}
    caps = runner.capabilities_for_step(persona, {"toolWhitelist": []})
    assert caps == persona["capabilities"]


def test_capabilities_for_step_narrows_to_whitelist(runner):
    persona = {"capabilities": {
        "webSearch": True, "vaultRead": True, "vaultWrite": True,
        "codeExecution": False, "kubectlRead": True, "githubRead": True,
    }}
    caps = runner.capabilities_for_step(persona, {"toolWhitelist": ["vault_read"]})
    assert caps["vaultRead"] is True
    assert caps["webSearch"] is False
    assert caps["vaultWrite"] is False
    assert caps["kubectlRead"] is False
    assert caps["githubRead"] is False


def test_client_tool_schemas_advertises_scoped_write_only_with_active_step_and_filepath(runner):
    caps = dict(runner.NO_CAPS)
    without_step = runner.client_tool_schemas(caps, None)
    assert not any(t["name"] == "scoped_write" for t in without_step)

    no_filepath = runner.client_tool_schemas(caps, {"toolWhitelist": ["scoped_write"]})
    assert not any(t["name"] == "scoped_write" for t in no_filepath)

    not_whitelisted = runner.client_tool_schemas(caps, {"toolWhitelist": [], "filepath": "notes.md"})
    assert not any(t["name"] == "scoped_write" for t in not_whitelisted)

    with_step = runner.client_tool_schemas(caps, {"toolWhitelist": ["scoped_write"], "filepath": "notes.md"})
    scoped = next(t for t in with_step if t["name"] == "scoped_write")
    assert "filename" not in scoped["input_schema"]["properties"]  # exact file — no filename param

    folder_step = runner.client_tool_schemas(caps, {"toolWhitelist": ["scoped_write"], "filepath": "agora/scratch/"})
    scoped_folder = next(t for t in folder_step if t["name"] == "scoped_write")
    assert "filename" in scoped_folder["input_schema"]["properties"]


def test_execute_tool_scoped_write_exact_file_ignores_model_supplied_path(runner):
    active_step = {"filepath": "notes.md", "toolWhitelist": ["scoped_write"]}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value="old"), \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="written") as mock_write, \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "scoped_write", {"path": "elsewhere.md", "content": "hello"}, {"name": "P"}, "c1", active_step,
        )
    assert result == "written"
    # `scoped_write` shares `_conditional_write` and deliberately offers no
    # override — a workflow step writing its own scoped file has no reason
    # to replace one with a fraction of itself.
    mock_write.assert_called_once_with("notes.md", "hello", allow_shrink=False)
    mock_audit.assert_called_once_with("P", "c1", "scoped_write", "notes.md", before="old", after="hello")


def test_execute_tool_scoped_write_folder_locks_after_first_call(runner):
    active_step = {"filepath": "agora/scratch/", "toolWhitelist": ["scoped_write"]}
    with patch.object(runner.tools_dispatch, "vault_read_path", return_value=""), \
         patch.object(runner.tools_dispatch, "vault_write_path", return_value="written") as mock_write, \
         patch.object(runner.tools_dispatch, "audit"):
        runner.execute_tool("scoped_write", {"filename": "draft.md", "content": "v1"}, {"name": "P"}, "c1", active_step)
        runner.execute_tool("scoped_write", {"filename": "other.md", "content": "v2"}, {"name": "P"}, "c1", active_step)

    assert mock_write.call_args_list[0].args == ("agora/scratch/draft.md", "v1")
    assert mock_write.call_args_list[1].args == ("agora/scratch/draft.md", "v2")
    assert active_step["_locked_path"] == "agora/scratch/draft.md"


def test_execute_tool_scoped_write_folder_requires_filename_on_first_call(runner):
    active_step = {"filepath": "agora/scratch/", "toolWhitelist": ["scoped_write"]}
    result = runner.execute_tool("scoped_write", {"content": "v1"}, {"name": "P"}, "c1", active_step)
    assert "error" in result
    assert "_locked_path" not in active_step


def test_execute_tool_scoped_write_rejects_path_traversal_in_filename(runner):
    active_step = {"filepath": "agora/scratch/", "toolWhitelist": ["scoped_write"]}
    result = runner.execute_tool(
        "scoped_write", {"filename": "../../etc/passwd", "content": "x"}, {"name": "P"}, "c1", active_step,
    )
    assert "error" in result
    assert "_locked_path" not in active_step


def test_execute_tool_scoped_write_with_no_active_step_errors(runner):
    result = runner.execute_tool("scoped_write", {"content": "x"}, {"name": "P"}, "c1", None)
    assert "error" in result


def test_run_workflow_steps_round_robin_continues_across_steps(runner):
    participants = [
        {"personaId": "p1", "name": "A", "role": "curator"},
        {"personaId": "p2", "name": "B", "role": "listener"},
        {"personaId": "p3", "name": "C", "role": "listener"},
    ]
    personas_by_id = {
        p["personaId"]: {"id": p["personaId"], "name": p["name"],
                          "model": "anthropic:claude-haiku-4-5-20251001", "capabilities": dict(runner.DEFAULT_CAPS)}
        for p in participants
    }
    steps = [
        {"prompt": "s1", "loopCount": 2, "toolWhitelist": []},
        {"prompt": "s2", "loopCount": 2, "toolWhitelist": []},
    ]
    notified = []

    with patch.object(runner.workflows, "fetch_persona_uncached", side_effect=lambda pid: personas_by_id[pid]), \
         patch.object(runner.workflows, "agora_get", return_value=(200, {"messages": []})), \
         patch.object(runner.workflows, "generate_reply", side_effect=lambda persona, *a, **k: f"reply from {persona['name']}"), \
         patch.object(runner.workflows, "notify", side_effect=lambda cid, text, sender, **k: notified.append(sender) or (200, "mid")):
        last_idx, rounds_run, replies_posted = runner.run_workflow_steps(
            steps, "c1", {"memory": ""}, participants,
        )

    assert notified == ["A", "B", "C", "A"]
    assert rounds_run == 4
    assert replies_posted == 4
    assert last_idx == 0


def test_run_workflow_steps_scopes_round_robin_to_step_personaids(runner):
    """2026-07-30 fix: a step with personaIds only round-robins among
    that subset, not the whole conversation's participants -- the whole
    reason it exists (the owner: round-robin was meant for multi-agent
    discussion, not a pipeline step with one designated owner)."""
    participants = [
        {"personaId": "p1", "name": "Coder", "role": "curator"},
        {"personaId": "p2", "name": "Reviewer", "role": "listener"},
    ]
    personas_by_id = {
        p["personaId"]: {"id": p["personaId"], "name": p["name"],
                          "model": "anthropic:claude-haiku-4-5-20251001", "capabilities": dict(runner.DEFAULT_CAPS)}
        for p in participants
    }
    steps = [
        {"prompt": "coder only", "loopCount": 3, "toolWhitelist": [], "personaIds": ["p1"]},
        {"prompt": "reviewer only", "loopCount": 2, "toolWhitelist": [], "personaIds": ["p2"]},
    ]
    notified = []

    with patch.object(runner.workflows, "fetch_persona_uncached", side_effect=lambda pid: personas_by_id[pid]), \
         patch.object(runner.workflows, "agora_get", return_value=(200, {"messages": []})), \
         patch.object(runner.workflows, "generate_reply", side_effect=lambda persona, *a, **k: f"reply from {persona['name']}"), \
         patch.object(runner.workflows, "notify", side_effect=lambda cid, text, sender, **k: notified.append(sender) or (200, "mid")):
        runner.run_workflow_steps(steps, "c1", {"memory": ""}, participants)

    # Step 1 is Coder-only for all 3 rounds; step 2 is Reviewer-only for
    # both its rounds -- Reviewer never appears in step 1's output and
    # Coder never appears in step 2's, unlike unscoped round-robin which
    # would have interleaved them.
    assert notified == ["Coder", "Coder", "Coder", "Reviewer", "Reviewer"]


def test_run_workflow_steps_skips_step_when_personaids_match_nobody(runner):
    participants = [{"personaId": "p1", "name": "A", "role": "curator"}]
    steps = [{"prompt": "", "loopCount": 1, "toolWhitelist": [], "personaIds": ["ghost-not-in-conversation"]}]

    with patch.object(runner.workflows, "notify") as mock_notify:
        last_idx, rounds_run, replies_posted = runner.run_workflow_steps(steps, "c1", {}, participants)

    mock_notify.assert_not_called()
    assert rounds_run == 0
    assert replies_posted == 0
    assert last_idx == -1


def test_run_workflow_steps_refetches_conversation_every_round(runner):
    """2026-07-30 fix: each round re-fetches the conversation fresh
    instead of working from a static snapshot taken once at the start
    of the run -- otherwise a message the owner posts while a run is
    executing never reaches a later round of that same run."""
    participants = [{"personaId": "p1", "name": "A", "role": "curator"}]
    persona = {"id": "p1", "name": "A", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.DEFAULT_CAPS)}
    steps = [{"prompt": "", "loopCount": 3, "toolWhitelist": []}]
    fetch_count = {"n": 0}

    def fake_agora_get(path):
        fetch_count["n"] += 1
        if fetch_count["n"] == 2:
            # Simulate the owner posting mid-run, between round 1 and round 2.
            return 200, {"messages": [{"sender": "Edvard", "text": "stop and check X"}]}
        return 200, {"messages": []}

    seen_histories = []

    def fake_generate_reply(persona, caps, system, history, *a, **k):
        seen_histories.append(list(history))
        return "ack"

    with patch.object(runner.workflows, "fetch_persona_uncached", return_value=persona), \
         patch.object(runner.workflows, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.workflows, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.workflows, "notify", return_value=(200, "mid")):
        runner.run_workflow_steps(steps, "c1", {}, participants)

    assert fetch_count["n"] == 3
    assert not any("stop and check X" in str(m) for m in seen_histories[0])
    assert any("stop and check X" in str(m) for m in seen_histories[1])


def test_run_workflow_steps_skips_notify_for_sentinel_reply(runner):
    participants = [{"personaId": "p1", "name": "A", "role": "curator"}]
    persona = {"id": "p1", "name": "A", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    steps = [{"prompt": "", "loopCount": 1, "toolWhitelist": []}]

    with patch.object(runner.workflows, "fetch_persona_uncached", return_value=persona), \
         patch.object(runner.workflows, "agora_get", return_value=(200, {"messages": []})), \
         patch.object(runner.workflows, "generate_reply", return_value=runner.HEARTBEAT_NO_REPORT_SENTINEL), \
         patch.object(runner.workflows, "notify") as mock_notify:
        last_idx, rounds_run, replies_posted = runner.run_workflow_steps(steps, "c1", {}, participants)

    mock_notify.assert_not_called()
    assert rounds_run == 1
    assert replies_posted == 0


def test_run_workflow_steps_composition_recurses(runner):
    participants = [
        {"personaId": "p1", "name": "A", "role": "curator"},
        {"personaId": "p2", "name": "B", "role": "listener"},
    ]
    personas_by_id = {
        p["personaId"]: {"id": p["personaId"], "name": p["name"],
                          "model": "anthropic:claude-haiku-4-5-20251001", "capabilities": dict(runner.DEFAULT_CAPS)}
        for p in participants
    }
    outer_steps = [{"prompt": "", "loopCount": 1, "toolWhitelist": [], "workflowRef": "sub1"}]
    sub_workflow = {"id": "sub1", "steps": [{"prompt": "inner", "loopCount": 1, "toolWhitelist": []}]}
    notified = []

    with patch.object(runner.workflows, "fetch_persona_uncached", side_effect=lambda pid: personas_by_id[pid]), \
         patch.object(runner.workflows, "fetch_workflow", return_value=sub_workflow), \
         patch.object(runner.workflows, "agora_get", return_value=(200, {"messages": []})), \
         patch.object(runner.workflows, "generate_reply", return_value="inner reply"), \
         patch.object(runner.workflows, "notify", side_effect=lambda cid, text, sender, **k: notified.append(sender) or (200, "mid")):
        last_idx, rounds_run, replies_posted = runner.run_workflow_steps(
            outer_steps, "c1", {}, participants,
        )

    assert notified == ["A"]
    assert rounds_run == 1
    assert replies_posted == 1
    assert last_idx == 0


def test_run_workflow_steps_unknown_workflow_ref_is_skipped_not_fatal(runner):
    participants = [{"personaId": "p1", "name": "A", "role": "curator"}]
    outer_steps = [{"prompt": "", "loopCount": 1, "toolWhitelist": [], "workflowRef": "ghost"}]

    with patch.object(runner.workflows, "fetch_workflow", return_value=None), \
         patch.object(runner.workflows, "notify") as mock_notify:
        last_idx, rounds_run, replies_posted = runner.run_workflow_steps(
            outer_steps, "c1", {}, participants,
        )

    mock_notify.assert_not_called()
    assert rounds_run == 0
    assert replies_posted == 0
    assert last_idx == -1


def test_run_workflow_steps_depth_cap_raises(runner):
    with pytest.raises(RuntimeError, match="recursion depth"):
        runner.run_workflow_steps(
            [], "c1", {}, [{"personaId": "p1", "name": "A"}],
            depth=runner.WORKFLOW_MAX_DEPTH + 1,
        )


def test_run_workflow_heartbeat_records_summary_in_last_result(runner):
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c1", "workflowId": "wf1"}
    workflow = {"id": "wf1", "name": "Discuss", "steps": [{"prompt": "", "loopCount": 1, "toolWhitelist": []}]}
    detail = {"personas": [{"personaId": "p1", "name": "A", "role": "curator"}], "messages": []}
    persona = {"id": "p1", "name": "A", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}

    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.workflows, "fetch_workflow", return_value=workflow), \
         patch.object(runner.workflows, "agora_get", return_value=(200, detail)), \
         patch.object(runner.workflows, "fetch_persona_uncached", return_value=persona), \
         patch.object(runner.workflows, "generate_reply", return_value="a real reply"), \
         patch.object(runner.workflows, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.workflows, "audit") as mock_audit, \
         patch.object(runner.workflows, "agora_internal", side_effect=fake_agora_internal):
        runner.run_workflow_heartbeat(heartbeat)

    assert "workflow: 1 steps, 1 rounds, 1 replies posted" in heartbeat_updates[-1]["lastResult"]
    mock_audit.assert_called_once()


def test_run_workflow_heartbeat_claims_run_before_executing(runner):
    """2026-08-02: the heartbeat must be marked as claimed (forceRun
    cleared, lastRunAt set) BEFORE the multi-step run starts, not only
    after it finishes. A workflow run takes minutes; while it was in
    flight the persisted state still said "still forced, never ran", so
    a pod restart (which drops heartbeats.py's in-process
    `_heartbeat_threads` guard) re-ran the same cycle and produced
    duplicate work."""
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c1", "workflowId": "wf1", "forceRun": True}
    workflow = {"id": "wf1", "name": "Discuss", "steps": [{"prompt": "", "loopCount": 1, "toolWhitelist": []}]}
    detail = {"personas": [{"personaId": "p1", "name": "A", "role": "curator"}], "messages": []}

    events = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            events.append(("patch", payload))
        return 200, {}

    def fake_run_workflow_steps(steps, conversation_id, detail, participants, **kwargs):
        events.append(("run", None))
        return -1, 1, 1

    with patch.object(runner.workflows, "fetch_workflow", return_value=workflow), \
         patch.object(runner.workflows, "agora_get", return_value=(200, detail)), \
         patch.object(runner.workflows, "run_workflow_steps", side_effect=fake_run_workflow_steps), \
         patch.object(runner.workflows, "audit"), \
         patch.object(runner.workflows, "agora_internal", side_effect=fake_agora_internal):
        runner.run_workflow_heartbeat(heartbeat)

    kinds = [kind for kind, _ in events]
    assert kinds == ["patch", "run", "patch"], kinds
    claim = events[0][1]
    assert claim["forceRun"] is False
    assert claim["lastResult"] == "running"
    # The claim anchors the next schedule to run START -- deliberate, so
    # a workflow that outlives its own interval still gets a real
    # lastRunAt while it is in flight.
    assert claim["lastRunAt"]
    assert claim["lastRunAt"] <= events[-1][1]["lastRunAt"]


def test_run_workflow_heartbeat_claims_run_even_when_workflow_missing(runner):
    """The claim is unconditional -- it happens before fetch_workflow, so
    a heartbeat pointing at a deleted workflow still consumes its
    forceRun instead of being retried forever."""
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c1", "workflowId": "ghost", "forceRun": True}
    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.workflows, "fetch_workflow", return_value=None), \
         patch.object(runner.workflows, "agora_internal", side_effect=fake_agora_internal):
        runner.run_workflow_heartbeat(heartbeat)

    assert heartbeat_updates[0]["lastResult"] == "running"
    assert "workflow not found" in heartbeat_updates[-1]["lastResult"]


def test_run_workflow_heartbeat_logs_when_the_claim_patch_fails(runner):
    """A failed claim PATCH silently reopens the duplicate window the
    claim exists to close. The run deliberately continues (a transient
    Agora blip shouldn't block the whole cycle), but it must say so --
    otherwise the next duplicate run has no evidence trail."""
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c1", "workflowId": "wf1", "forceRun": True}
    workflow = {"id": "wf1", "name": "Discuss", "steps": [{"prompt": "", "loopCount": 1, "toolWhitelist": []}]}
    detail = {"personas": [{"personaId": "p1", "name": "A", "role": "curator"}], "messages": []}
    logs = []

    def failing_claim(method, path, payload=None):
        # Only the claim (the first PATCH, lastResult "running") fails.
        if payload and payload.get("lastResult") == "running":
            return 503, {}
        return 200, {}

    with patch.object(runner.workflows, "fetch_workflow", return_value=workflow), \
         patch.object(runner.workflows, "agora_get", return_value=(200, detail)), \
         patch.object(runner.workflows, "run_workflow_steps", return_value=(-1, 1, 1)), \
         patch.object(runner.workflows, "audit"), \
         patch.object(runner.workflows, "log", side_effect=lambda m: logs.append(m)), \
         patch.object(runner.workflows, "agora_internal", side_effect=failing_claim):
        runner.run_workflow_heartbeat(heartbeat)

    claim_warnings = [m for m in logs if "claim PATCH failed" in m]
    assert claim_warnings, logs
    assert "503" in claim_warnings[0]
    # ...and the cycle still ran rather than aborting on the blip.
    assert any("1 replies posted" in m for m in logs)


def test_run_workflow_heartbeat_uses_rotated_conversation_id(runner):
    """2026-08-02: when rotate_cycle_conversation hands back a different
    id than the heartbeat's own conversationId, the workflow must run
    against the NEW one (and re-fetch `detail` for it), not the stale
    one the heartbeat still nominally points at in the dict passed in."""
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c-old", "workflowId": "wf1",
                 "rotateConversationEachRun": True}
    workflow = {"id": "wf1", "name": "Discuss", "steps": [{"prompt": "", "loopCount": 1, "toolWhitelist": []}]}
    old_detail = {"personas": [{"personaId": "p1", "name": "A", "role": "curator"}], "messages": ["stale"]}
    new_detail = {"personas": [{"personaId": "p1", "name": "A", "role": "curator"}], "messages": []}
    persona = {"id": "p1", "name": "A", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}

    get_calls = []

    def fake_agora_get(path):
        get_calls.append(path)
        if path.startswith("/conversations/c-old"):
            return 200, old_detail
        if path.startswith("/conversations/c-new"):
            return 200, new_detail
        return 200, {}

    steps_calls = []

    def fake_run_workflow_steps(steps, conversation_id, detail, participants, **kwargs):
        steps_calls.append((conversation_id, detail))
        return -1, 1, 1

    with patch.object(runner.workflows, "fetch_workflow", return_value=workflow), \
         patch.object(runner.workflows, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.workflows, "rotate_cycle_conversation", return_value="c-new") as mock_rotate, \
         patch.object(runner.workflows, "run_workflow_steps", side_effect=fake_run_workflow_steps), \
         patch.object(runner.workflows, "audit") as mock_audit, \
         patch.object(runner.workflows, "agora_internal", return_value=(200, {})):
        runner.run_workflow_heartbeat(heartbeat)

    mock_rotate.assert_called_once_with(heartbeat, old_detail["personas"])
    assert steps_calls == [("c-new", new_detail)]
    mock_audit.assert_called_once()
    assert mock_audit.call_args[0][1] == "c-new"


def test_run_workflow_heartbeat_records_failure_when_workflow_missing(runner):
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c1", "workflowId": "ghost"}
    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.workflows, "fetch_workflow", return_value=None), \
         patch.object(runner.workflows, "agora_internal", side_effect=fake_agora_internal):
        runner.run_workflow_heartbeat(heartbeat)

    assert "workflow not found" in heartbeat_updates[-1]["lastResult"]


def test_run_workflow_heartbeat_records_failure_when_conversation_has_no_personas(runner):
    heartbeat = {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
                 "conversationId": "c1", "workflowId": "wf1"}
    workflow = {"id": "wf1", "name": "Discuss", "steps": []}
    heartbeat_updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == f"/heartbeats/{heartbeat['id']}":
            heartbeat_updates.append(payload)
        return 200, {}

    with patch.object(runner.workflows, "fetch_workflow", return_value=workflow), \
         patch.object(runner.workflows, "agora_get", return_value=(200, {"personas": [], "messages": []})), \
         patch.object(runner.workflows, "agora_internal", side_effect=fake_agora_internal):
        runner.run_workflow_heartbeat(heartbeat)

    assert "no personas" in heartbeat_updates[-1]["lastResult"]


# ---------------------------------------------------------------------------
# 2026-07-26 vault-tools.md suite — vault_bulk_fetch, parse_frontmatter, and
# the search/frontmatter-query/validate/batch-edit/stub/duplicate/token-
# metrics/git-history tools built on top of them.
# ---------------------------------------------------------------------------

_VAULT_TOOLS_FILEDOCS = {
    "notes/a.md": {"path": "notes/a.md", "children": ["chunk:a"]},
    "notes/b.md": {"path": "notes/b.md", "children": ["chunk:b"]},
    "inbox.md": {"path": "inbox.md", "children": ["chunk:inbox"]},
}
_VAULT_TOOLS_CHUNKS = {
    "chunk:a": (
        "---\ntype: note\ntags: [x, y]\nstatus: active\n---\n# Title A\n"
        "Hello world foo bar, with enough additional body text to comfortably "
        "clear the forty-character stub-detection threshold used in these tests."
    ),
    "chunk:b": "short",
    "chunk:inbox": "# Inbox\n\n## For Claude\n",
}


def _all_docs_rows(path, ids):
    """What a real `_all_docs` returns for this query, honouring
    `startkey`/`endkey`.

    The fakes used to answer every `_all_docs` GET with the whole
    dictionary, which meant a listing scoped to one folder and a listing
    of the entire vault were indistinguishable to them. `_vault_file_docs`
    now asks CouchDB for a key range instead of filtering client-side, so
    a wrong range would be invisible to a fake that ignores it -- and the
    failure mode is files silently missing from a listing, which is the
    exact bug tests/test_vault_hides_deleted_files.py exists about.
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    start = json.loads(query.get("startkey", ['""'])[0])
    end = json.loads(query.get("endkey", ['"\U0010FFFF"'])[0])
    return [{"id": i} for i in sorted(ids) if start <= i <= end]


def _fake_vault_couch_req(method, path, body=None):
    if method == "GET" and "_all_docs" in path:
        return 200, {"rows": _all_docs_rows(path, _VAULT_TOOLS_FILEDOCS)}
    if method == "POST" and "_all_docs" in path:
        rows = []
        for key in body["keys"]:
            if key in _VAULT_TOOLS_FILEDOCS:
                rows.append({"id": key, "doc": _VAULT_TOOLS_FILEDOCS[key]})
            elif key in _VAULT_TOOLS_CHUNKS:
                rows.append({"id": key, "doc": {"data": _VAULT_TOOLS_CHUNKS[key]}})
        return 200, {"rows": rows}
    return 404, {}


def test_vault_bulk_fetch_assembles_content_via_batched_all_docs(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        files = runner.vault_bulk_fetch("")
    assert files["notes/a.md"].startswith("---\ntype: note")
    assert files["notes/b.md"] == "short"
    assert set(files) == {"notes/a.md", "notes/b.md", "inbox.md"}


def test_parse_frontmatter_handles_flat_and_list_values(runner):
    fields, body = runner.parse_frontmatter(_VAULT_TOOLS_CHUNKS["chunk:a"])
    assert fields["type"] == "note"
    assert fields["tags"] == ["x", "y"]
    assert fields["status"] == "active"
    assert body.strip().startswith("# Title A")


def test_parse_frontmatter_no_block_returns_empty_fields(runner):
    fields, body = runner.parse_frontmatter("just text, no frontmatter")
    assert fields == {}
    assert body == "just text, no frontmatter"


def test_vault_search_finds_matches_across_files(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_search("hello world")
    assert "notes/a.md" in result
    assert "Hello world foo bar" in result


def test_vault_search_empty_query_short_circuits(runner):
    assert "empty query" in runner.vault_search("   ")


def test_vault_search_no_matches(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_search("zzz_nonexistent_zzz")
    assert "no matches" in result


def test_vault_query_frontmatter_filters_by_field_and_value(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_query_frontmatter("status", "active")
    assert "notes/a.md" in result
    assert "notes/b.md" not in result


def test_vault_query_frontmatter_field_presence_only(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_query_frontmatter("type")
    assert "notes/a.md" in result


def test_vault_validate_frontmatter_schema_flags_missing_type_and_exempts_capture_files(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_validate_frontmatter_schema("")
    assert "notes/b.md" in result  # no frontmatter block at all
    assert "inbox.md" not in result  # exempt root capture file
    assert "notes/a.md" not in result  # has a 'type' key


def test_vault_find_stub_notes_flags_short_bodies(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_find_stub_notes("", min_chars=40)
    assert "notes/b.md" in result
    assert "notes/a.md" not in result


def test_vault_find_duplicate_titles_detects_collision(runner):
    filedocs = {
        "a.md": {"path": "a.md", "children": ["chunk:a"]},
        "b.md": {"path": "b.md", "children": ["chunk:b"]},
    }
    chunks = {"chunk:a": "# Same Title\nbody", "chunk:b": "# Same Title\nother body"}

    def fake(method, path, body=None):
        if method == "GET" and "_all_docs" in path:
            return 200, {"rows": _all_docs_rows(path, filedocs)}
        if method == "POST" and "_all_docs" in path:
            rows = []
            for key in body["keys"]:
                if key in filedocs:
                    rows.append({"id": key, "doc": filedocs[key]})
                elif key in chunks:
                    rows.append({"id": key, "doc": {"data": chunks[key]}})
            return 200, {"rows": rows}
        return 404, {}

    with patch.object(runner.vault, "couch_req", side_effect=fake):
        result = runner.vault_find_duplicate_titles("")
    assert "same title" in result.lower()
    assert "a.md" in result and "b.md" in result


def test_vault_get_token_metrics_reports_totals(runner):
    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req):
        result = runner.vault_get_token_metrics("")
    assert "3 file(s)" in result
    assert "tokens total" in result


def test_vault_update_frontmatter_batch_rewrites_only_the_matching_line(runner):
    written = {}

    def fake_vault_write_path(path, content):
        written[path] = content
        return "written"

    with patch.object(runner.vault, "couch_req", side_effect=_fake_vault_couch_req), \
         patch.object(runner.vault, "vault_write_path", side_effect=fake_vault_write_path):
        result = runner.vault_update_frontmatter_batch(
            "status", "archived", match_field="type", match_value="note",
        )

    assert "notes/a.md" in written
    assert "status: archived" in written["notes/a.md"]
    assert "tags: [x, y]" in written["notes/a.md"]  # untouched
    assert "# Title A" in written["notes/a.md"]  # body untouched
    assert "notes/b.md" not in written  # no frontmatter block, skipped
    assert "1 file(s)" in result


def test_vault_update_frontmatter_batch_requires_field(runner):
    assert "field is required" in runner.vault_update_frontmatter_batch("", "value")


def test_vault_git_revision_history_lists_commits(runner):
    def fake_run(cmd, capture_output, text, timeout, env):
        class R:
            stdout = json.dumps([
                {"sha": "abc1234567", "commit": {"message": "fix stuff", "author": {"date": "2026-07-25T00:00:00Z"}}},
            ])
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.vault, "GITHUB_READONLY_TOKEN", "fake-token"), \
         patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.vault_git_revision_history()
    assert "abc1234" in result
    assert "fix stuff" in result


def test_vault_git_revision_history_sha_mode_returns_diff(runner):
    def fake_run(cmd, capture_output, text, timeout, env):
        class R:
            stdout = json.dumps({
                "files": [{"filename": "notes/a.md", "additions": 1, "deletions": 0, "patch": "+hello"}],
            })
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.vault, "GITHUB_READONLY_TOKEN", "fake-token"), \
         patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.vault_git_revision_history(sha="abc123")
    assert "notes/a.md" in result
    assert "+hello" in result


def test_vault_git_revision_history_without_token_degrades_gracefully(runner):
    with patch.object(runner.vault, "GITHUB_READONLY_TOKEN", ""):
        result = runner.vault_git_revision_history()
    assert "no token configured" in result


def test_vault_summarize_recent_agent_work_expands_recent_commits(runner):
    def fake_run(cmd, capture_output, text, timeout, env):
        class R:
            returncode = 0
            stderr = ""
            if "/commits/" in cmd[2]:
                stdout = json.dumps({"files": [{"filename": "notes/a.md"}]})
            else:
                stdout = json.dumps([
                    {"sha": "abc1234567",
                     "commit": {"message": "did stuff", "author": {"date": "2026-07-25T00:00:00Z"}}},
                ])
        return R()

    with patch.object(runner.vault, "GITHUB_READONLY_TOKEN", "fake-token"), \
         patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.vault_summarize_recent_agent_work(24)
    assert "did stuff" in result
    assert "notes/a.md" in result


# ---------------------------------------------------------------------------
# 2026-07-26 manageAgora capability — create_persona/create_conversation/
# create_heartbeat/create_workflow, gated behind the new capability and
# calling the runner's internal-app agent surface (ADR 0007).
# ---------------------------------------------------------------------------

def test_manage_agora_tools_only_advertised_with_capability(runner):
    caps_on = dict(runner.NO_CAPS, manageAgora=True)
    caps_off = dict(runner.NO_CAPS, manageAgora=False)
    names_on = {t["name"] for t in runner.client_tool_schemas(caps_on)}
    names_off = {t["name"] for t in runner.client_tool_schemas(caps_off)}
    for tool in ("create_persona", "create_conversation", "create_heartbeat", "create_workflow"):
        assert tool in names_on
        assert tool not in names_off


def test_execute_tool_create_persona_calls_internal_api(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "agora_internal", return_value=(201, {"persona": {"id": "p1"}})) as mock_call, \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool(
            "create_persona", {"name": "New Persona", "model": "anthropic:claude-sonnet-5"}, persona, "c1",
        )
    assert "p1" in result
    assert mock_call.call_args[0][0] == "POST"
    assert mock_call.call_args[0][1] == "/personas"


def test_execute_tool_create_conversation_defaults_model_to_own_persona(runner):
    persona = {"name": "Test", "model": "anthropic:claude-sonnet-5"}
    with patch.object(runner.tools_dispatch, "agora_internal", return_value=(201, {"conversation": {"id": "c2"}})) as mock_call, \
         patch.object(runner.tools_dispatch, "audit"):
        runner.execute_tool("create_conversation", {"name": "New Channel"}, persona, "c1")
    payload = mock_call.call_args[0][2]
    assert payload["model"] == "anthropic:claude-sonnet-5"


def test_execute_tool_create_heartbeat_requires_conversation_target(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool(
            "create_heartbeat", {"name": "HB", "personaId": "p1", "schedule": "daily@08:00"}, persona, "c1",
        )
    assert "conversationId or newConversationName is required" in result


def test_execute_tool_create_heartbeat_with_new_conversation_name(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "agora_internal", return_value=(201, {"heartbeat": {"id": "h1"}})) as mock_call, \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool(
            "create_heartbeat",
            {"name": "HB", "personaId": "p1", "schedule": "daily@08:00", "newConversationName": "Fresh Channel"},
            persona, "c1",
        )
    assert "h1" in result
    payload = mock_call.call_args[0][2]
    assert payload["newConversationName"] == "Fresh Channel"


def test_execute_tool_create_heartbeat_reuses_existing_instead_of_duplicating(runner):
    """Issues.md: 'creating heartbeats using agent tool seems to create two
    heartbeats instead of one' -- root cause is FAILURE_PAUSE_CAP's retry
    path resending an entire turn (including this tool call) when a LATER
    round errors after this one already succeeded. Same name + personaId
    should be treated as the same heartbeat, not a new one."""
    persona = {"name": "Test"}
    existing = {"heartbeats": [{"id": "h1", "name": "HB", "personaId": "p1"}]}

    def fake_agora_internal(method, path, payload=None):
        if method == "GET":
            return 200, existing
        raise AssertionError("POST /heartbeats should not be called for a duplicate")

    with patch.object(runner.tools_dispatch, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "create_heartbeat",
            {"name": "HB", "personaId": "p1", "schedule": "daily@08:00", "newConversationName": "Fresh Channel"},
            persona, "c1",
        )
    assert "h1" in result
    assert "already exists" in result
    mock_audit.assert_called_once()


def test_execute_tool_create_heartbeat_ignores_same_name_different_persona(runner):
    persona = {"name": "Test"}
    existing = {"heartbeats": [{"id": "h1", "name": "HB", "personaId": "someone-else"}]}
    with patch.object(runner.tools_dispatch, "agora_internal",
                       side_effect=[(200, existing), (201, {"heartbeat": {"id": "h2"}})]) as mock_call, \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool(
            "create_heartbeat",
            {"name": "HB", "personaId": "p1", "schedule": "daily@08:00", "newConversationName": "Fresh Channel"},
            persona, "c1",
        )
    assert "h2" in result
    assert mock_call.call_count == 2


def test_execute_tool_create_workflow_calls_internal_api(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "agora_internal", return_value=(201, {"workflow": {"id": "w1"}})), \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("create_workflow", {"name": "WF"}, persona, "c1")
    assert "w1" in result


def test_tool_to_capability_covers_every_new_tool(runner):
    for tool in (
        "vault_search", "vault_query_frontmatter", "vault_validate_frontmatter_schema",
        "vault_find_stub_notes", "vault_find_duplicate_titles", "vault_get_token_metrics",
        "vault_git_revision_history", "vault_summarize_recent_agent_work",
    ):
        assert runner.TOOL_TO_CAPABILITY[tool] == "vaultRead"
    assert runner.TOOL_TO_CAPABILITY["vault_update_frontmatter_batch"] == "vaultWrite"
    for tool in ("create_persona", "create_conversation", "create_heartbeat", "create_workflow"):
        assert runner.TOOL_TO_CAPABILITY[tool] == "manageAgora"


# ---------------------------------------------------------------------------
# 2026-07-26 create_pr / merge_pr (githubWrite/githubMerge) — real GitHub
# writes via the bot account, GitHub REST API directly (no git/gh CLI).
# ---------------------------------------------------------------------------

def test_github_api_without_token_degrades_gracefully(runner):
    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", ""):
        data, err = runner._github_api("GET", "/repos/SokratesAI/agora")
    assert data is None
    assert "no token configured" in err


def test_github_api_surfaces_http_error(runner):
    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", return_value=(404, {"message": "Not Found"})):
        data, err = runner._github_api("GET", "/repos/SokratesAI/nope")
    assert data is None
    assert "HTTP 404" in err


def test_create_pr_requires_repo_branch_and_files(runner):
    assert "required" in runner.create_pr("", "branch", [], "msg", "title")
    assert "required" in runner.create_pr("agora", "", [{"path": "a.md", "content": "x"}], "msg", "title")
    assert "required" in runner.create_pr("agora", "branch", [], "msg", "title")


def test_create_pr_rejects_branch_same_as_base(runner):
    result = runner.create_pr("agora", "main", [{"path": "a.md", "content": "x"}], "msg", "title")
    assert "must not be the same as base" in result


def test_create_pr_happy_path_creates_branch_writes_files_opens_pr(runner):
    calls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls.append((method, url, body))
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "base-sha-123"}}
        if method == "GET" and url.endswith("/git/ref/heads/my-branch"):
            return 404, {"message": "Not Found"}
        if method == "POST" and url.endswith("/git/refs"):
            return 201, {"ref": "refs/heads/my-branch"}
        if method == "GET" and "/contents/" in url:
            return 404, {"message": "Not Found"}
        if method == "PUT" and "/contents/" in url:
            return 200, {"content": {"sha": "new-file-sha"}}
        if method == "GET" and url.endswith("state=open"):
            return 200, []
        if method == "POST" and url.endswith("/pulls"):
            return 201, {"number": 42, "html_url": "https://github.com/SokratesAI/agora/pull/42"}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.create_pr(
            "agora", "my-branch", [{"path": "notes.md", "content": "hello"}],
            "add notes", "Add notes",
        )

    assert "created PR #42" in result
    assert "https://github.com/SokratesAI/agora/pull/42" in result
    branch_creates = [c for c in calls if c[0] == "POST" and c[1].endswith("/git/refs")]
    assert len(branch_creates) == 1


def test_create_pr_reuses_existing_branch_and_open_pr(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "base-sha-123"}}
        if method == "GET" and url.endswith("/git/ref/heads/my-branch"):
            return 200, {"object": {"sha": "existing-branch-sha"}}
        if method == "GET" and "/contents/" in url:
            return 200, {"sha": "current-file-sha"}
        if method == "PUT" and "/contents/" in url:
            return 200, {"content": {"sha": "updated-file-sha"}}
        if method == "GET" and url.endswith("state=open"):
            return 200, [{"number": 7, "html_url": "https://github.com/SokratesAI/agora/pull/7"}]
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.create_pr(
            "agora", "my-branch", [{"path": "notes.md", "content": "more"}],
            "update notes", "Add notes",
        )

    assert "pushed 1 file(s) to existing PR #7" in result


def test_create_pr_surfaces_file_write_failure(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if method == "GET" and url.endswith("/git/ref/heads/main"):
            return 200, {"object": {"sha": "base-sha-123"}}
        if method == "GET" and url.endswith("/git/ref/heads/my-branch"):
            return 200, {"object": {"sha": "existing-branch-sha"}}
        if method == "GET" and "/contents/" in url:
            return 404, {"message": "Not Found"}
        if method == "PUT" and "/contents/" in url:
            return 422, {"message": "invalid request"}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.create_pr(
            "agora", "my-branch", [{"path": "notes.md", "content": "hello"}], "msg", "title",
        )
    assert "failed writing notes.md" in result


def test_merge_pr_refuses_when_not_open(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if url.endswith("/pulls/42"):
            return 200, {"state": "closed", "head": {"sha": "headsha"}}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.merge_pr("agora", 42)
    assert "not open" in result


def test_merge_pr_refuses_with_no_check_runs(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if url.endswith("/pulls/42"):
            return 200, {"state": "open", "head": {"sha": "headsha"}}
        if url.endswith("/commits/headsha/check-runs"):
            return 200, {"check_runs": []}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.merge_pr("agora", 42)
    assert "no CI checks found" in result


def test_merge_pr_refuses_while_checks_pending(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if url.endswith("/pulls/42"):
            return 200, {"state": "open", "head": {"sha": "headsha"}}
        if url.endswith("/commits/headsha/check-runs"):
            return 200, {"check_runs": [{"name": "build", "status": "in_progress", "conclusion": None}]}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.merge_pr("agora", 42)
    assert "still running" in result
    assert "build" in result


def test_merge_pr_refuses_on_failing_check(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if url.endswith("/pulls/42"):
            return 200, {"state": "open", "head": {"sha": "headsha"}}
        if url.endswith("/commits/headsha/check-runs"):
            return 200, {"check_runs": [{"name": "test", "status": "completed", "conclusion": "failure"}]}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.merge_pr("agora", 42)
    assert "failing checks" in result
    assert "test" in result


def test_merge_pr_succeeds_when_all_checks_green(runner):
    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        if url.endswith("/pulls/42"):
            return 200, {"state": "open", "head": {"sha": "headsha"}}
        if url.endswith("/commits/headsha/check-runs"):
            return 200, {"check_runs": [
                {"name": "build", "status": "completed", "conclusion": "success"},
                {"name": "test", "status": "completed", "conclusion": "success"},
            ]}
        if method == "PUT" and url.endswith("/pulls/42/merge"):
            return 200, {"sha": "mergedsha4567"}
        raise AssertionError(f"unexpected call {method} {url}")

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.merge_pr("agora", 42)
    assert "merged PR #42 (squash)" in result
    assert "mergeds" in result


def test_github_comment_requires_repo_number_and_body(runner):
    assert "required" in runner.github_comment("", 42, "hi")
    assert "required" in runner.github_comment("agora", 0, "hi")
    assert "must not be empty" in runner.github_comment("agora", 42, "")
    assert "must not be empty" in runner.github_comment("agora", 42, "   ")


def test_github_comment_posts_to_issues_endpoint_for_prs_too(runner):
    calls = []

    def fake_http_json(method, url, body=None, headers=None, timeout=30):
        calls.append((method, url, body))
        return 201, {"html_url": "https://github.com/SokratesAI/agora/pull/42#issuecomment-1"}

    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", side_effect=fake_http_json):
        result = runner.github_comment("agora", 42, "looks good")

    # PRs and issues share one numbering space, so the issues endpoint is
    # correct for both -- this is the whole reason there's no pr/issue branch.
    assert calls == [
        ("POST", "https://api.github.com/repos/SokratesAI/agora/issues/42/comments",
         {"body": "looks good"}),
    ]
    assert "commented on agora#42" in result
    assert "issuecomment-1" in result


def test_github_comment_surfaces_api_failure(runner):
    with patch.object(runner.tools_github, "GITHUB_BOT_TOKEN", "fake-token"), \
         patch.object(runner.tools_github, "http_json", return_value=(404, {"message": "Not Found"})):
        result = runner.github_comment("agora", 999, "hello")
    assert "could not comment on agora#999" in result
    assert "HTTP 404" in result


def test_github_write_and_merge_tools_only_advertised_with_capability(runner):
    caps_on = dict(runner.NO_CAPS, githubWrite=True, githubMerge=True)
    caps_off = dict(runner.NO_CAPS, githubWrite=False, githubMerge=False)
    names_on = {t["name"] for t in runner.client_tool_schemas(caps_on)}
    names_off = {t["name"] for t in runner.client_tool_schemas(caps_off)}
    assert "create_pr" in names_on and "merge_pr" in names_on
    assert "create_pr" not in names_off and "merge_pr" not in names_off


def test_github_comment_advertised_with_github_write_capability(runner):
    names_on = {t["name"] for t in runner.client_tool_schemas(dict(runner.NO_CAPS, githubWrite=True))}
    names_off = {t["name"] for t in runner.client_tool_schemas(dict(runner.NO_CAPS, githubWrite=False))}
    assert "github_comment" in names_on
    assert "github_comment" not in names_off
    # githubMerge alone must not smuggle it in -- it rides githubWrite only.
    names_merge_only = {
        t["name"] for t in runner.client_tool_schemas(dict(runner.NO_CAPS, githubMerge=True))
    }
    assert "github_comment" not in names_merge_only


def test_execute_tool_create_pr_dispatches_with_audit(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "create_pr", return_value="created PR #1: url") as mock_fn, \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "create_pr",
            {"repo": "agora", "branch": "fix-x", "files": [{"path": "a.md", "content": "y"}],
             "commit_message": "msg", "title": "title"},
            persona, "c1",
        )
    assert result == "created PR #1: url"
    mock_fn.assert_called_once_with("agora", "fix-x", [{"path": "a.md", "content": "y"}], "msg", "title", "", "main")
    mock_audit.assert_called_once()


def test_execute_tool_merge_pr_dispatches_with_audit(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "merge_pr", return_value="merged PR #5 (squash), sha=abc1234") as mock_fn, \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("merge_pr", {"repo": "agora", "pr_number": 5}, persona, "c1")
    assert "merged PR #5" in result
    mock_fn.assert_called_once_with("agora", 5, "squash")


def test_execute_tool_github_comment_dispatches_with_audit(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "github_comment",
                      return_value="commented on agora#7: url") as mock_fn, \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool(
            "github_comment", {"repo": "agora", "issue_number": 7, "body": "nice"}, persona, "c1",
        )
    assert result == "commented on agora#7: url"
    mock_fn.assert_called_once_with("agora", 7, "nice")
    mock_audit.assert_called_once()


def test_tool_to_capability_covers_github_write_and_merge(runner):
    assert runner.TOOL_TO_CAPABILITY["create_pr"] == "githubWrite"
    assert runner.TOOL_TO_CAPABILITY["github_comment"] == "githubWrite"
    assert runner.TOOL_TO_CAPABILITY["merge_pr"] == "githubMerge"


# ---------------------------------------------------------------------------
# 2026-07-29 terminal_exec (terminalExec) — Issues.md #1, unrestricted shell
# access in this same pod. Deliberately no command allowlist (unlike
# kubectl_read/github_read), so these tests cover the guardrails that do
# exist: capability gating, timeout clamping, workspace-escape rejection,
# and output truncation/exit-code reporting — not command validation.
# ---------------------------------------------------------------------------

def test_terminal_exec_only_advertised_with_capability(runner):
    caps_on = dict(runner.NO_CAPS, terminalExec=True)
    caps_off = dict(runner.NO_CAPS, terminalExec=False)
    names_on = {t["name"] for t in runner.client_tool_schemas(caps_on)}
    names_off = {t["name"] for t in runner.client_tool_schemas(caps_off)}
    assert "terminal_exec" in names_on
    assert "terminal_exec" not in names_off


def test_tool_to_capability_covers_terminal_exec(runner):
    assert runner.TOOL_TO_CAPABILITY["terminal_exec"] == "terminalExec"


def test_no_caps_defaults_terminal_exec_off(runner):
    assert runner.NO_CAPS["terminalExec"] is False


def test_terminal_exec_requires_command(runner):
    result = runner.terminal_exec({"command": ""})
    assert "required" in result
    result = runner.terminal_exec({})
    assert "required" in result


def test_terminal_exec_rejects_invalid_args(runner):
    result = runner.terminal_exec("not a dict")
    assert "invalid arguments" in result


def test_terminal_exec_runs_command_and_reports_exit_code(runner):
    def fake_run(cmd, capture_output, text, timeout, cwd):
        assert cmd == ["bash", "-lc", "echo hi"]
        class R:
            stdout = "hi\n"
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.terminal_exec({"command": "echo hi"})
    assert result == "[exit 0]\nhi\n"


def test_terminal_exec_reports_nonzero_exit(runner):
    def fake_run(cmd, capture_output, text, timeout, cwd):
        class R:
            stdout = ""
            stderr = "not found\n"
            returncode = 127
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.terminal_exec({"command": "nope"})
    assert result.startswith("[exit 127]")
    assert "not found" in result


def test_terminal_exec_no_output_reports_exit_code_only(runner):
    def fake_run(cmd, capture_output, text, timeout, cwd):
        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.terminal_exec({"command": "true"})
    assert result == "[exit 0, no output]"


def test_terminal_exec_clamps_timeout_to_max(runner):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, cwd):
        captured["timeout"] = timeout
        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        runner.terminal_exec({"command": "sleep 1", "timeout": 99999})
    assert captured["timeout"] == runner.TERMINAL_EXEC_TIMEOUT_MAX


def test_terminal_exec_defaults_timeout_when_unset_or_invalid(runner):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, cwd):
        captured["timeout"] = timeout
        class R:
            stdout = ""
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        runner.terminal_exec({"command": "echo hi", "timeout": "not-a-number"})
    assert captured["timeout"] == runner.TERMINAL_EXEC_TIMEOUT_DEFAULT


def test_terminal_exec_times_out_gracefully(runner):
    def fake_run(cmd, capture_output, text, timeout, cwd):
        raise runner.subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.terminal_exec({"command": "sleep 999", "timeout": 5})
    assert "timed out after 5s" in result


def test_terminal_exec_rejects_cwd_escape(runner):
    result = runner.terminal_exec({"command": "pwd", "cwd": "../../etc"})
    assert "no '..'" in result
    result = runner.terminal_exec({"command": "pwd", "cwd": "/etc"})
    assert "relative path" in result


def test_terminal_exec_uses_workspace_subdir(runner):
    captured = {}

    def fake_run(cmd, capture_output, text, timeout, cwd):
        captured["cwd"] = cwd
        class R:
            stdout = "ok"
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        runner.terminal_exec({"command": "ls", "cwd": "myrepo"})
    assert captured["cwd"] == f"{runner.TERMINAL_WORKSPACE}/myrepo"


def test_terminal_exec_truncates_long_output(runner):
    huge = "x" * (runner.TERMINAL_EXEC_OUTPUT_MAX + 500)

    def fake_run(cmd, capture_output, text, timeout, cwd):
        class R:
            stdout = huge
            stderr = ""
            returncode = 0
        return R()

    with patch.object(runner.subprocess, "run", side_effect=fake_run):
        result = runner.terminal_exec({"command": "cat bigfile"})
    assert "truncated" in result
    assert str(len(huge)) in result


def test_terminal_exec_missing_binary_degrades_gracefully(runner):
    with patch.object(runner.subprocess, "run", side_effect=FileNotFoundError()):
        result = runner.terminal_exec({"command": "echo hi"})
    assert "terminal_exec error" in result


def test_execute_tool_terminal_exec_dispatches_with_audit(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "terminal_exec", return_value="[exit 0]\nhi") as mock_fn, \
         patch.object(runner.tools_dispatch, "audit") as mock_audit:
        result = runner.execute_tool("terminal_exec", {"command": "echo hi"}, persona, "c1")
    assert result == "[exit 0]\nhi"
    mock_fn.assert_called_once_with({"command": "echo hi"})
    mock_audit.assert_called_once()
    audit_args = mock_audit.call_args[0]
    assert audit_args[2] == "terminal_exec"
    assert audit_args[3] == "echo hi"


def test_build_system_includes_terminal_exec_blurb_when_capability_on(runner):
    persona = {
        "name": "Test", "personality": "You are Test.",
        "capabilities": {"terminalExec": True},
    }
    system = runner.build_system(persona)
    assert "terminal_exec" in system
    assert "Terminal access" in system


def test_build_system_omits_terminal_exec_blurb_when_capability_off(runner):
    persona = {
        "name": "Test", "personality": "You are Test.",
        "capabilities": {"terminalExec": False},
    }
    system = runner.build_system(persona)
    assert "Terminal access" not in system


# ---------------------------------------------------------------------------
# 2026-07-26 list_personas / list_models — fixes a real live bug: a
# manageAgora persona had no way to learn its own personaId (needed by
# create_heartbeat) or another persona's id, and no way to learn valid
# model-id strings (needed by create_persona/create_conversation) short of
# guessing. Confirmed live in the "Agora" conversation: the persona tried
# vault_search for "personaId" and read kubectl pod logs looking for a
# hint, then gave up — persona ids/models live in Agora's own datastore,
# not the vault or cluster, so no amount of vault/cluster access could
# ever have found them.
# ---------------------------------------------------------------------------

def test_list_personas_and_list_models_only_advertised_with_manage_agora(runner):
    caps_on = dict(runner.NO_CAPS, manageAgora=True)
    caps_off = dict(runner.NO_CAPS, manageAgora=False)
    names_on = {t["name"] for t in runner.client_tool_schemas(caps_on)}
    names_off = {t["name"] for t in runner.client_tool_schemas(caps_off)}
    assert "list_personas" in names_on and "list_models" in names_on
    assert "list_personas" not in names_off and "list_models" not in names_off


def test_execute_tool_list_personas_formats_id_name_model(runner):
    persona = {"name": "Test"}
    fake_personas = {"personas": [
        {"id": "abc-123", "name": "Agora", "model": "anthropic:claude-haiku-4-5-20251001"},
        {"id": "def-456", "name": "Marcus", "model": "gemini:gemini-flash-latest"},
    ]}
    with patch.object(runner.tools_dispatch, "agora_get", return_value=(200, fake_personas)), \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("list_personas", {}, persona, "c1")
    assert "abc-123 | Agora | anthropic:claude-haiku-4-5-20251001" in result
    assert "def-456 | Marcus | gemini:gemini-flash-latest" in result


def test_execute_tool_list_personas_handles_empty_and_error(runner):
    persona = {"name": "Test"}
    with patch.object(runner.tools_dispatch, "agora_get", return_value=(200, {"personas": []})), \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("list_personas", {}, persona, "c1")
    assert "no personas exist yet" in result

    with patch.object(runner.tools_dispatch, "agora_get", return_value=(500, {})), \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("list_personas", {}, persona, "c1")
    assert "list_personas failed" in result


def test_execute_tool_list_models_formats_id_label(runner):
    persona = {"name": "Test"}
    fake_models = {"models": [{"id": "anthropic:claude-sonnet-5", "label": "Claude Sonnet 5"}]}
    with patch.object(runner.tools_dispatch, "agora_get", return_value=(200, fake_models)), \
         patch.object(runner.tools_dispatch, "audit"):
        result = runner.execute_tool("list_models", {}, persona, "c1")
    assert "anthropic:claude-sonnet-5 | Claude Sonnet 5" in result


def test_tool_to_capability_covers_list_personas_and_list_models(runner):
    assert runner.TOOL_TO_CAPABILITY["list_personas"] == "manageAgora"
    assert runner.TOOL_TO_CAPABILITY["list_models"] == "manageAgora"


def test_build_system_gives_persona_its_own_id_when_manage_agora_on(runner):
    persona = {
        "id": "self-id-789", "name": "Agora", "personality": "You are Agora.",
        "capabilities": {"manageAgora": True},
    }
    system = runner.build_system(persona)
    assert "self-id-789" in system
    assert "list_models" in system
    assert "list_personas" in system


def test_build_system_omits_manage_agora_blurb_when_capability_off(runner):
    persona = {
        "id": "self-id-789", "name": "Agora", "personality": "You are Agora.",
        "capabilities": {"manageAgora": False},
    }
    system = runner.build_system(persona)
    assert "Manage Agora" not in system


# --- Draining on SIGTERM (2026-08-03) -------------------------------------
#
# run_heartbeat posts the persona's reply (notify()) only AFTER
# generate_reply returns -- minutes of work for a claude-cli persona. With
# no SIGTERM handler, Python's default disposition killed the process
# instantly, so a redeploy landing mid-cycle destroyed the reply in flight.
# Observed three cycles running on the Evolve heartbeat 2026-08-02, each
# time because merging into this repo rolled the pod running the merge.

main_module = sys.modules["agora_runner.main"]


@pytest.fixture
def drainable_main():
    """Restores the real signal handlers and the shutdown flag, so a test
    that fires a genuine SIGTERM at the pytest process can't leak either
    into the rest of the suite."""
    previous_term = signal.getsignal(signal.SIGTERM)
    previous_int = signal.getsignal(signal.SIGINT)
    previous_flag = main_module._shutdown_requested
    previous_interval = main_module.POLL_INTERVAL_SECONDS
    main_module.POLL_INTERVAL_SECONDS = 0
    # `main()` also starts the catalog refresher (Cycle 451), a daemon thread
    # on an hourly timer. Nothing here is about it, and conftest fails any
    # test that leaves a thread running past its own patches -- so it is
    # stubbed for every drain test at once rather than in each of them. The
    # wire itself is asserted in tests/test_catalog_refresh.py.
    previous_refresh = main_module.start_catalog_refresh
    main_module.start_catalog_refresh = lambda: None
    try:
        yield main_module
    finally:
        main_module.start_catalog_refresh = previous_refresh
        main_module.POLL_INTERVAL_SECONDS = previous_interval
        main_module._shutdown_requested = previous_flag
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def test_main_finishes_the_in_flight_tick_after_sigterm(drainable_main):
    """The real regression. A genuine SIGTERM is delivered to this process
    partway through a tick; what must hold is that the REST of that tick
    still runs (that's where notify() lives) and that main then stops
    instead of starting another one."""
    events = []

    def fake_poll_once():
        events.append("tick")
        if events.count("tick") == 1:
            os.kill(os.getpid(), signal.SIGTERM)  # the pod is rolled mid-cycle
            events.append("reply-posted")  # stands in for notify() + the PATCH
        if events.count("tick") > 5:
            # main() catches Exception, so this has to be a BaseException to
            # actually escape and fail the test rather than loop forever.
            raise KeyboardInterrupt("main kept polling after SIGTERM")

    with patch.object(drainable_main, "poll_once", side_effect=fake_poll_once), \
         patch.object(drainable_main, "start_invoke_server", lambda: None), \
         patch.object(drainable_main, "log", lambda *a, **k: None):
        drainable_main.main()

    assert events == ["tick", "reply-posted"], \
        "the tick in flight when SIGTERM arrived did not run to completion"
    assert drainable_main.shutdown_requested() is True


def test_main_waits_for_an_in_flight_heartbeat_thread_before_exiting(drainable_main):
    """Issue #15, re-fought. The drain protected the reply a cycle was
    producing only while runs were synchronous — "the tick in flight" and
    "the run in flight" were the same object. Now that a run has its own
    thread the tick returns in milliseconds, and exiting on that would
    kill the cycle exactly as the pre-drain code did (Cycles 3, 20, 21,
    22, 23 each lost a reply this way)."""
    from agora_runner import heartbeats as hb_module
    events = []

    def slow_run():
        time.sleep(0.3)
        events.append("reply-posted")  # stands in for notify() + the PATCH

    thread = threading.Thread(target=slow_run, daemon=True)

    def fake_poll_once():
        events.append("tick")
        if events.count("tick") == 1:
            thread.start()  # a heartbeat run is now in flight
            os.kill(os.getpid(), signal.SIGTERM)  # the pod is rolled mid-cycle

    with patch.object(hb_module, "_heartbeat_threads", {"hb1": [thread]}), \
         patch.object(drainable_main, "poll_once", side_effect=fake_poll_once), \
         patch.object(drainable_main, "start_invoke_server", lambda: None), \
         patch.object(drainable_main, "log", lambda *a, **k: None), \
         patch.object(hb_module, "log", lambda *a, **k: None):
        drainable_main.main()
        events.append("exited")

    assert events == ["tick", "reply-posted", "exited"], \
        "main exited before the in-flight heartbeat thread had posted its reply"


def test_main_starts_no_new_tick_when_the_signal_lands_while_idle(drainable_main):
    """A signal arriving during the sleep used to buy one more full tick
    on the way out. Harmless when a tick only ever finished work; now it
    could CLAIM a fresh heartbeat run (moving lastRunAt, marking it
    "running") moments before the process goes away, which is precisely
    the half-started cycle the claim exists to prevent."""
    ticks = []

    def fake_poll_once():
        ticks.append(len(ticks))
        if len(ticks) > 3:
            raise KeyboardInterrupt("main kept polling after SIGTERM")

    def fake_sleep(_seconds):
        # The signal lands while the loop is idle between ticks, not
        # during one.
        os.kill(os.getpid(), signal.SIGTERM)

    with patch.object(drainable_main, "poll_once", side_effect=fake_poll_once), \
         patch.object(drainable_main, "_sleep_between_ticks", side_effect=fake_sleep), \
         patch.object(drainable_main, "start_invoke_server", lambda: None), \
         patch.object(drainable_main, "log", lambda *a, **k: None):
        drainable_main.main()

    assert ticks == [0], f"expected exactly one tick, got {len(ticks)}"


def test_main_keeps_polling_when_no_signal_arrives(drainable_main):
    """The drain must not turn the poll loop into a one-shot."""
    ticks = []

    def fake_poll_once():
        ticks.append(len(ticks))
        if len(ticks) == 3:
            raise KeyboardInterrupt("stop the test loop")

    with patch.object(drainable_main, "poll_once", side_effect=fake_poll_once), \
         patch.object(drainable_main, "start_invoke_server", lambda: None), \
         patch.object(drainable_main, "log", lambda *a, **k: None):
        with pytest.raises(KeyboardInterrupt):
            drainable_main.main()

    assert len(ticks) == 3


def test_sleep_between_ticks_returns_early_once_shutdown_is_requested(drainable_main):
    """PEP 475 makes a plain time.sleep RESUME after a signal instead of
    returning, so an idle pod would otherwise sit out the full interval
    before noticing it was asked to stop."""
    drainable_main._shutdown_requested = True
    started = time.monotonic()
    drainable_main._sleep_between_ticks(30)
    assert time.monotonic() - started < 1.0


# ---------------------------------------------------------------------------
# tool_activity.py + /tool-activity -- live tool-use chips for claude-cli
# personas (2026-08-03). The owner, asked whether he wanted every tool call or
# only the ones that change something: "All. I want to know whats going on.
# It takes away my feeling of control if everything is hidden."
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_grants():
    from agora_runner import tool_activity
    tool_activity._grants.clear()
    yield tool_activity
    tool_activity._grants.clear()


def test_grant_returns_a_token_and_report_posts_a_chip(clean_grants):
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    assert token
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        assert clean_grants.report(token, "Bash", "pytest tests/") is True
    # ephemeral: narration is retained on its own budget in Agora, so a
    # cycle's few hundred chips cannot evict the capability audit trail.
    # The output fields are inert for a call-start chip -- they carry the
    # tool's return value, which does not exist yet when this one is sent.
    assert posted == [("Nova", "conv-1", "Bash", "pytest tests/",
                       {"ephemeral": True, "tool_use_id": "", "output": None,
                        "is_error": False})]


def test_grant_is_declined_when_there_is_no_conversation_to_post_into(clean_grants):
    """The /invoke path builds a reply with conversation_id=None -- no chip
    can render, so the bridge should not be asked to report at all."""
    assert clean_grants.grant("Nova", None) is None
    assert clean_grants.grant("Nova", "") is None


def test_report_is_refused_after_the_call_that_minted_the_token_ends(clean_grants):
    """The grant is the whole of this endpoint's auth: it must stop working
    the moment the generate() call it belongs to returns."""
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    clean_grants.revoke(token)
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        assert clean_grants.report(token, "Bash", "rm -rf /") is False
    assert posted == []


def test_report_is_refused_for_a_token_that_was_never_issued(clean_grants):
    posted = []
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        assert clean_grants.report("made-up-token", "Bash", "ls") is False
    assert posted == []


def test_a_grant_cannot_post_into_a_conversation_it_was_not_issued_for(clean_grants):
    """The conversation is bound at grant time and never taken from the
    request -- a compromised or buggy bridge cannot address another chat."""
    posted = []
    token = clean_grants.grant("Nova", "conv-mine")
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        clean_grants.report(token, "Bash", "ls")
    assert posted[0][1] == "conv-mine"


def test_grants_are_unique_per_call(clean_grants):
    tokens = {clean_grants.grant("Nova", "conv-1") for _ in range(20)}
    assert len(tokens) == 20


def test_every_tool_call_is_reported_however_many_there_are(clean_grants):
    """There is no ceiling here, on purpose. This used to stop at 400 chips
    and go silent; The owner struck that down on 2026-08-04 -- "limiting the tool
    calls (which limits your ability) just because you think it will improve
    the ui is against everything we stand for". Volume is handled by
    collapsing narration in the UI (agora#38), not by dropping it here."""
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        for i in range(2000):
            assert clean_grants.report(token, "Bash", f"step-{i}") is True

    # No gap, no cap notice, nothing substituted for a real call.
    assert [p[3] for p in posted] == [f"step-{i}" for i in range(2000)]
    # Everything this module posts is narration: Agora retains it on a budget
    # separate from the capability audit trail, which is what stops 2000 of
    # these evicting it (agora#37).
    assert all(p[-1]["ephemeral"] is True for p in posted)


def test_claude_cli_generate_sends_an_activity_callback(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "the answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Nova"}, "conv-1",
        )
    activity = captured["body"]["activity"]
    assert activity["url"].endswith("/tool-activity")
    assert activity["token"]


def test_claude_cli_generate_revokes_the_grant_when_the_bridge_call_fails(runner):
    """A failed or timed-out generate() must not leave a live token behind
    for the rest of this process's life."""
    from agora_runner import tool_activity
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        raise RuntimeError("bridge unreachable")

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        with pytest.raises(RuntimeError):
            runner.claude_cli_generate(
                "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
                dict(runner.NO_CAPS), {"name": "Nova"}, "conv-1",
            )
    assert captured["body"]["activity"]["token"] not in tool_activity._grants


def test_claude_cli_generate_revokes_the_grant_after_a_successful_call(runner):
    from agora_runner import tool_activity
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "the answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Nova"}, "conv-1",
        )
    assert captured["body"]["activity"]["token"] not in tool_activity._grants


def test_claude_cli_generate_omits_the_callback_when_there_is_no_conversation(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "the answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
            dict(runner.NO_CAPS), {"name": "Nova"}, None,
        )
    assert "activity" not in captured["body"]


# --- the /tool-activity endpoint itself ---

def _tool_activity_handler(body):
    from agora_runner import invoke_server
    handler = invoke_server.InvokeHandler.__new__(invoke_server.InvokeHandler)
    handler.path = "/tool-activity"
    raw = json.dumps(body).encode()
    handler.rfile = io.BytesIO(raw)
    handler.headers = {"Content-Length": str(len(raw))}
    sent = {}

    def fake_send(status, payload):
        sent["status"] = status
        sent["payload"] = payload
    handler._send = fake_send
    return handler, sent


def test_tool_activity_endpoint_records_a_chip(clean_grants):
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    handler, sent = _tool_activity_handler(
        {"token": token, "capability": "Bash", "detail": "pytest tests/"})
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        handler.do_POST()
    assert sent["status"] == 202
    assert posted == [("Nova", "conv-1", "Bash", "pytest tests/",
                       {"ephemeral": True, "tool_use_id": "", "output": None,
                        "is_error": False})]


def test_tool_activity_endpoint_records_what_a_tool_returned(clean_grants):
    """The owner's issue 1, asked three times: "I need to see the command with
    all metadata and also the output from that command, such as the return
    of a echo command". The output arrives as its own report, tagged with
    the id of the call it belongs to."""
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    handler, sent = _tool_activity_handler({
        "token": token, "capability": "Bash",
        "toolUseId": "toolu_a", "output": "hi\n",
    })
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        handler.do_POST()
    assert sent["status"] == 202
    assert posted[0][4]["tool_use_id"] == "toolu_a"
    assert posted[0][4]["output"] == "hi\n"
    assert posted[0][4]["is_error"] is False


def test_tool_activity_endpoint_marks_a_failed_tool_call(clean_grants):
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    handler, sent = _tool_activity_handler({
        "token": token, "capability": "Bash", "toolUseId": "toolu_b",
        "output": "not found", "isError": True,
    })
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        handler.do_POST()
    assert posted[0][4]["is_error"] is True


def test_audit_sends_output_as_its_own_field_not_folded_into_detail():
    """detail is a one-line chip label truncated at 500; output is a
    transcript. Folding output into detail would silently cut a test run's
    result off at 500 characters."""
    import importlib
    audit_mod = importlib.import_module("agora_runner.audit")
    sent = {}
    with patch.object(audit_mod, "agora_internal",
                      lambda m, u, body: sent.update(body)):
        audit_mod.audit("Nova", "c1", "Bash", "", ephemeral=True,
                        tool_use_id="toolu_a", output="x" * 4000)
    assert sent["toolUseId"] == "toolu_a"
    assert len(sent["output"]) == 4000
    assert sent["detail"] == ""
    assert "isError" not in sent


def test_audit_omits_the_output_fields_for_an_ordinary_capability_call():
    """Every other audit() caller in the runner passes none of this, and
    their payloads must be byte-identical to before."""
    import importlib
    audit_mod = importlib.import_module("agora_runner.audit")
    sent = {}
    with patch.object(audit_mod, "agora_internal",
                      lambda m, u, body: sent.update(body)):
        audit_mod.audit("Nova", "c1", "vault_write", "notes.md", after="hello")
    assert "toolUseId" not in sent
    assert "output" not in sent
    assert "isError" not in sent


def test_tool_activity_endpoint_rejects_an_unknown_token(clean_grants):
    posted = []
    handler, sent = _tool_activity_handler(
        {"token": "not-a-real-token", "capability": "Bash", "detail": "ls"})
    with patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        handler.do_POST()
    assert sent["status"] == 401
    assert posted == []


def test_tool_activity_endpoint_requires_a_token_and_a_capability(clean_grants):
    token = clean_grants.grant("Nova", "conv-1")
    for body in ({"capability": "Bash"}, {"token": token}, {}):
        handler, sent = _tool_activity_handler(body)
        handler.do_POST()
        assert sent["status"] == 400


def test_tool_activity_endpoint_does_not_require_the_agora_token(clean_grants):
    """This endpoint exists precisely so the bridge never needs AGORA_TOKEN
    -- requiring it here would defeat the whole reason for the callback."""
    posted = []
    token = clean_grants.grant("Nova", "conv-1")
    handler, sent = _tool_activity_handler({"token": token, "capability": "Read", "detail": "/x"})
    from agora_runner import invoke_server
    with patch.object(invoke_server, "AGORA_TOKEN", "a-real-secret"), \
         patch.object(clean_grants, "audit", lambda *a, **k: posted.append(a + (k,))):
        handler.do_POST()
    assert sent["status"] == 202
    assert len(posted) == 1


def test_invoke_endpoint_still_requires_the_agora_token(clean_grants):
    """The new route must not have opened up the old one."""
    from agora_runner import invoke_server
    handler, sent = _tool_activity_handler({"messages": []})
    handler.path = "/invoke"
    with patch.object(invoke_server, "AGORA_TOKEN", "a-real-secret"):
        handler.do_POST()
    assert sent["status"] == 401


def test_audit_sends_a_written_passage_whole():
    """A passage the persona wrote between two tool calls is prose, not a
    chip label. Clipping it at DETAIL_CHARS_MAX would end every second one
    mid-sentence -- the "block of text" complaint moved one hop upstream."""
    import importlib
    audit_mod = importlib.import_module("agora_runner.audit")
    passage = "word " * 400
    sent = {}
    with patch.object(audit_mod, "agora_internal",
                      lambda m, u, body: sent.update(body)):
        audit_mod.audit("Nova", "c1", audit_mod.NARRATION_TEXT, passage, ephemeral=True)
    assert sent["detail"] == passage
    assert len(sent["detail"]) > audit_mod.DETAIL_CHARS_MAX


def test_audit_still_clips_an_ordinary_chip_label():
    """The ceiling only lifts for narration text. A `Bash` chip carrying a
    3000-character heredoc is still a one-line label and still gets cut."""
    import importlib
    audit_mod = importlib.import_module("agora_runner.audit")
    sent = {}
    with patch.object(audit_mod, "agora_internal",
                      lambda m, u, body: sent.update(body)):
        audit_mod.audit("Nova", "c1", "Bash", "x" * 3000, ephemeral=True)
    assert len(sent["detail"]) == audit_mod.DETAIL_CHARS_MAX


# ---------------------------------------------------------------------------
# 2026-08-05 — credentials must not reach the Activity feed.
#
# agora-claude-bridge got this filter in Cycle 21, for the tools that run
# inside the bridge. Every other provider's tools run in THIS process and
# publish through audit(), which had no filter at all: `terminal_exec`
# audits the command verbatim, and `vault_write` audits the whole file as
# before/after so Agora can render a diff. A real OAuth token has already
# reached a conversation once this way.
# ---------------------------------------------------------------------------

def _audited(*args, **kwargs):
    """One audit() call, returning the payload that would go to Agora."""
    sent = {}
    with patch.object(audit_module, "agora_internal",
                      lambda m, u, body: sent.update(body)):
        audit_module.audit(*args, **kwargs)
    return sent


# A real JWT's payload segment starts `eyJ` too, and gitleaks' own jwt rule
# keys on exactly that -- so writing one out as a literal fails this repo's
# secret scan, in the file whose whole subject is not leaking secrets. Built
# from pieces instead: the scanner needs contiguous text, redact() does not.
_REALISTIC_JWT = "eyJhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiIxMjM0NTY3ODkwIn0" \
    + ".dBjftJeZ4CVPmB92K27u"


def test_redact_replaces_each_credential_shape_with_a_visible_marker(runner):
    redact = audit_module.redact
    cases = [
        ("sk-ant-oat01-" + "A" * 40, "anthropic key"),
        ("ghp_" + "b" * 36, "github token"),
        ("github_pat_" + "c" * 40, "github token"),
        (_REALISTIC_JWT, "jwt"),
        # The pattern anchors on the header segment only, so a payload that
        # isn't base64 JSON -- an opaque session cookie of the same shape --
        # is still caught.
        ("eyJhbGciOiJSUzI1NiJ9.c29tZS1vcGFxdWUtcGF5bG9hZA.c2lnbmF0dXJlLWhlcmU", "jwt"),
        ("AKIAIOSFODNN7EXAMPLE", "aws key id"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "private key"),
    ]
    for secret, label in cases:
        out = redact(f"before {secret} after")
        assert secret not in out, f"{label} survived redaction"
        assert f"[redacted: {label}]" in out
        # Never a silent deletion -- the surrounding text still reads.
        assert out.startswith("before ") and out.endswith(" after")


def test_redact_keeps_the_variable_name_and_drops_only_the_value(runner):
    """Knowing that ANTHROPIC_API_KEY is *set* is exactly the kind of thing
    the owner wants to be able to see; the value is the part that must not ship."""
    out = audit_module.redact("ANTHROPIC_API_KEY=hunter2-and-then-some\nHOME=/root")
    assert "ANTHROPIC_API_KEY=[redacted: value]" in out
    assert "hunter2-and-then-some" not in out
    assert "HOME=/root" in out


def test_redact_leaves_ordinary_text_alone(runner):
    """A filter that mangles normal chip labels is worse than no filter --
    these are the strings the feed is actually made of."""
    redact = audit_module.redact
    for text in ("Read vault file · journal.md",
                 "kubectl get pods -n agents",
                 "gh pr merge --repo SokratesAI/agora-persona-runner 43 --squash",
                 "Nova finished in 38m — replied 4212 chars"):
        assert redact(text) == text


def test_redact_finds_the_value_when_json_has_quoted_the_name_too(runner):
    """`"couchdb_password": "x"` -- the shape the comment claimed to cover.

    The separator pattern went straight from the name to `[=:]`, so the
    closing quote of a JSON key stopped it matching at all. Found by the
    drift probes in tools/sync_contract.py, not by a person. The quote is
    kept in group 2 so the replacement puts it back and the document is
    still parseable, which is the half a `not in out` assertion misses.
    """
    out = audit_module.redact(
        '{"couchdb_password": "notarealpassword", "db": "nova"}')
    assert out == '{"couchdb_password": "[redacted: value]", "db": "nova"}'
    assert json.loads(out)["couchdb_password"] == "[redacted: value]"


def test_redact_covers_the_name_this_system_keeps_its_own_password_under(runner):
    """`CDB_PASS` is neither PASSWD nor PASSWORD, and it is the live one."""
    out = audit_module.redact("CDB_PASS: notarealpassword1234\nCDB_USER: nova")
    assert "CDB_PASS: [redacted: value]" in out
    assert "notarealpassword1234" not in out
    assert "CDB_USER: nova" in out


def test_redact_does_not_eat_the_english_word_pass(runner):
    """Why `_PASS` carries its underscore. Over-redacting prose is the
    failure the owner's keep-everything rule is actually about, so the widening
    above has to be pinned from both sides or the next cycle drops the
    underscore to catch one more case."""
    for text in ("second pass: completed successfully",
                 "a first pass at the digest: reasonable",
                 "The password rotation is documented in decisions/adr-0012.md."):
        assert audit_module.redact(text) == text


def test_redact_passes_non_strings_through(runner):
    """audit() hands over whatever a tool returned; callers shouldn't have
    to type-check first."""
    assert audit_module.redact(None) is None
    assert audit_module.redact("") == ""


def test_audit_redacts_a_terminal_command_carrying_a_bearer_token(runner):
    """execute_tool audits `terminal_exec`'s command verbatim -- this is the
    live path, not a hypothetical one."""
    token = "sk-ant-oat01-" + "Z" * 40
    sent = _audited("Nova", "c1", "terminal_exec",
                    f'curl -H "Authorization: Bearer {token}" https://api.anthropic.com')
    assert token not in sent["detail"]
    assert "[redacted: anthropic key]" in sent["detail"]
    assert "https://api.anthropic.com" in sent["detail"]


def test_audit_redacts_the_vault_write_diff_it_publishes(runner):
    """before/after carry the whole file so the feed can render a diff. A
    vault note holding a token would otherwise publish it in full."""
    token = "ghp_" + "q" * 36
    sent = _audited("Nova", "c1", "vault_write", "notes.md",
                    before="nothing here", after=f"deploy key: {token}\n")
    assert token not in sent["after"]
    assert "[redacted: github token]" in sent["after"]
    assert sent["before"] == "nothing here"


def test_audit_redacts_tool_output(runner):
    """The bridge scrubs its own reports before sending them, but report()
    is not the only way output reaches audit() and the bridge could be a
    version behind at any moment.

    Asserted whole rather than by substring, because the substring version
    was passing on mangled output: `_PATTERNS` runs in order, the github
    pattern left `[redacted: github token]` behind, and the value pattern
    then matched the marker as if it were the value, so what actually
    reached the feed was `GITHUB_TOKEN=[redacted: value] github token]` --
    which contains the string this test used to look for. The secret was
    gone either way; the label named the wrong pattern and had a stray
    bracket. Cycle 170.
    """
    token = "ghp_" + "w" * 36
    sent = _audited("Nova", "c1", "Bash", "printenv",
                    tool_use_id="t1", output=f"GITHUB_TOKEN={token}")
    assert token not in sent["output"]
    assert sent["output"] == "GITHUB_TOKEN=[redacted: github token]"


def test_audit_redacts_before_it_truncates(runner):
    """Order matters: clipping at DETAIL_CHARS_MAX first would leave the
    leading half of a token sitting in the feed, unmatched by any pattern."""
    token = "ghp_" + "e" * 36
    # Positioned so that 30 characters of the token would survive a
    # truncate-then-redact ordering -- enough to be a usable prefix, and
    # short enough that the {16,} pattern no longer matches it.
    detail = "x" * (audit_module.DETAIL_CHARS_MAX - 30) + token
    sent = _audited("Nova", "c1", "terminal_exec", detail)
    assert token[:20] not in sent["detail"]
    assert "[redacted: github token]" in sent["detail"]
    assert len(sent["detail"]) <= audit_module.DETAIL_CHARS_MAX


# --- the "seen, queued" chip (deferred.py) ---------------------------------


def _ack(runner, messages, summary=None, status=200):
    """Run acknowledge_deferred against one conversation tail, returning
    the audit() calls it made. Clears the fetch-avoidance cache first --
    it is module-level and would otherwise leak between tests."""
    runner.deferred._last_message_at.clear()
    summary = summary or {"id": "cycle9", "name": "Nova — Cycle 9",
                          "lastMessageAt": "2026-08-05T15:40:00Z",
                          "personas": [{"name": "Nova", "role": "curator"}]}
    with patch.object(runner.deferred, "agora_get",
                      return_value=(status, {"messages": messages})) as mock_get, \
         patch.object(runner.deferred, "audit") as mock_audit:
        runner.acknowledge_deferred(summary)
    return mock_audit.call_args_list, mock_get.call_args_list


def test_a_message_in_a_skipped_cycle_conversation_gets_acknowledged(runner):
    """runner#45 replaced an expensive answer with no answer at all, and
    on a phone "queued for six hours" and "ignored" look identical. One
    chip, one HTTP call, and the run still happens on schedule."""
    calls, _ = _ack(runner, [
        {"sender": "Nova", "text": "Cycle 30 done", "ts": "2026-08-05T15:30:00Z"},
        {"sender": "Edvard", "text": "quota is 50% left", "ts": "2026-08-05T15:40:00Z"},
    ])

    assert len(calls) == 1
    persona, conversation_id, capability, detail = calls[0].args
    assert (persona, conversation_id) == ("Nova", "cycle9")
    assert capability == runner.deferred.QUEUED_CAPABILITY
    assert "next run" in detail


def test_the_acknowledgment_is_not_posted_twice(runner):
    """Dedupe with no local state at all: the chip comes back in the
    conversation's own messages, so "have I already said this?" is a
    property of the thread, not of this process. An in-process memo would
    re-post the chip after every pod restart."""
    calls, _ = _ack(runner, [
        {"sender": "Edvard", "text": "quota is 50% left", "ts": "2026-08-05T15:40:00Z"},
        {"sender": "Nova", "text": "Queued: Noted", "ts": "2026-08-05T15:40:05Z",
         "activity": {"capability": runner.deferred.QUEUED_CAPABILITY}},
    ])

    assert calls == []


def test_a_second_message_after_an_acknowledgment_is_acknowledged_again(runner):
    """The dedupe is keyed on his NEWEST message, not on the chip merely
    existing somewhere in the thread -- otherwise the first acknowledgment
    would silence every message he ever writes there afterwards."""
    calls, _ = _ack(runner, [
        {"sender": "Edvard", "text": "quota is 50% left", "ts": "2026-08-05T15:40:00Z"},
        {"sender": "Nova", "text": "Queued: Noted", "ts": "2026-08-05T15:40:05Z",
         "activity": {"capability": runner.deferred.QUEUED_CAPABILITY}},
        {"sender": "Edvard", "text": "and one more thing", "ts": "2026-08-05T15:52:00Z"},
    ])

    assert len(calls) == 1


def test_a_cycle_talking_to_itself_is_not_acknowledged(runner):
    """A running cycle fills its own transcript with chips and passages.
    None of that is the owner, and acknowledging it would post a chip every
    five seconds for forty-five minutes."""
    calls, _ = _ack(runner, [
        {"sender": "Nova", "text": "Bash: pytest", "ts": "2026-08-05T15:30:00Z",
         "activity": {"capability": "Bash"}},
        {"sender": "Nova", "text": "Tests are green.", "ts": "2026-08-05T15:31:00Z"},
    ])

    assert calls == []


def test_a_forgotten_message_does_not_earn_an_acknowledgment(runner):
    calls, _ = _ack(runner, [
        {"sender": "Edvard", "text": "ignore this", "ts": "2026-08-05T15:40:00Z",
         "forgotten": True},
    ])

    assert calls == []


def test_an_activity_flag_that_is_not_a_dict_is_survivable(runner):
    """decide_turn only ever tests `activity` for truthiness, so plenty of
    callers and fixtures set it to a bare True. Reading `.capability` off
    that would raise inside the poll loop."""
    calls, _ = _ack(runner, [
        {"sender": "Edvard", "text": "look at this", "ts": "2026-08-05T15:40:00Z"},
        {"sender": "Nova", "text": "ran something", "ts": "2026-08-05T15:41:00Z",
         "activity": True},
    ])

    assert len(calls) == 1


def test_an_unchanged_conversation_is_never_re_fetched(runner):
    """The whole cost of this feature is one message fetch, and poll_once
    runs every five seconds. Without this gate, every skipped conversation
    would be re-read twelve times a minute forever."""
    summary = {"id": "cycle9", "name": "Nova — Cycle 9",
               "lastMessageAt": "2026-08-05T15:40:00Z",
               "personas": [{"name": "Nova", "role": "curator"}]}
    messages = [{"sender": "Nova", "text": "quiet", "ts": "2026-08-05T15:40:00Z"}]

    runner.deferred._last_message_at.clear()
    with patch.object(runner.deferred, "agora_get",
                      return_value=(200, {"messages": messages})) as mock_get, \
         patch.object(runner.deferred, "audit"):
        runner.acknowledge_deferred(summary)
        runner.acknowledge_deferred(summary)
        runner.acknowledge_deferred(summary)

    assert mock_get.call_count == 1
    # ...and a new message reopens it.
    with patch.object(runner.deferred, "agora_get",
                      return_value=(200, {"messages": messages})) as mock_get, \
         patch.object(runner.deferred, "audit"):
        runner.acknowledge_deferred(dict(summary, lastMessageAt="2026-08-05T15:45:00Z"))

    assert mock_get.call_count == 1


def test_a_failed_fetch_is_retried_rather_than_treated_as_handled(runner):
    """A transient blip must not mark the conversation as seen -- that
    would swallow his message until he happened to write again."""
    summary = {"id": "cycle9", "lastMessageAt": "2026-08-05T15:40:00Z", "personas": []}
    _calls, gets = _ack(runner, [], summary=summary, status=503)

    assert len(gets) == 1
    assert runner.deferred._last_message_at == {}


def test_the_acknowledgment_reads_only_the_tail_of_the_conversation(runner):
    """Measured 2026-08-05: a live cycle transcript is ~206 KB at the
    general FETCH_LIMIT of 40 and ~22 KB at 10, because a tail of tool
    chips carries its output verbatim -- and this runs on a five-second
    tick for as long as the cycle lasts."""
    _calls, gets = _ack(runner, [])

    assert f"limit={runner.deferred.ACK_TAIL_LIMIT}" in gets[0].args[0]
    assert runner.deferred.ACK_TAIL_LIMIT < runner.FETCH_LIMIT


def test_poll_once_acknowledges_a_cycle_thread_but_never_a_workflow_one(runner):
    """The two skip sets are kept apart on purpose. A cycle transcript
    defers the owner's message to the next scheduled run and can promise him
    one; a workflow-bound conversation makes no such promise, and saying
    it did would be a lie in the one place he already cannot see what
    happened.

    Since 2026-08-20 the cycle transcript under test has to be one whose
    own run is IN FLIGHT -- every other cycle transcript, live or
    retired, is answered on the spot now and so has nothing to defer."""
    conversations_body = {"conversations": [
        {"id": "cycle10", "name": "Nova — Cycle 10"},
        {"id": "cycle9", "name": "Nova — Cycle 9",
         "tags": [runner.cycle_tag("hb1")], "createdAt": "2026-08-19T09:00:00Z"},
        {"id": "wf1conv", "name": "Some Workflow"},
        {"id": "chat", "name": "Normal Chat"},
    ]}
    heartbeats_body = {"heartbeats": [
        {"id": "hb1", "enabled": True, "rotateConversationEachRun": True,
         "conversationId": "cycle10"},
        {"enabled": True, "workflowId": "wf1", "conversationId": "wf1conv"},
    ]}

    acked = []
    with _run_in_flight_for(runner, "hb1"), \
         patch.object(runner.poll, "agora_get",
                      side_effect=lambda p: (200, conversations_body) if p == "/conversations" else (404, {})), \
         patch.object(runner.poll, "agora_internal",
                      side_effect=lambda m, p, payload=None: (200, heartbeats_body)), \
         patch.object(runner.poll, "poll_conversation"), \
         patch.object(runner.poll, "acknowledge_deferred",
                      side_effect=lambda s: acked.append(s["id"])), \
         patch.object(runner.poll, "run_due_heartbeats"):
        runner.poll_once()

    assert acked == ["cycle10"]


def test_an_archived_cycle_thread_is_not_acknowledged(runner):
    conversations_body = {"conversations": [
        {"id": "cycle9", "name": "Nova — Cycle 9", "archived": True},
    ]}
    heartbeats_body = {"heartbeats": [
        {"enabled": True, "rotateConversationEachRun": True, "conversationId": "cycle9"},
    ]}

    acked = []
    with patch.object(runner.poll, "agora_get",
                      side_effect=lambda p: (200, conversations_body) if p == "/conversations" else (404, {})), \
         patch.object(runner.poll, "agora_internal",
                      side_effect=lambda m, p, payload=None: (200, heartbeats_body)), \
         patch.object(runner.poll, "poll_conversation"), \
         patch.object(runner.poll, "acknowledge_deferred",
                      side_effect=lambda s: acked.append(s["id"])), \
         patch.object(runner.poll, "run_due_heartbeats"):
        runner.poll_once()

    assert acked == []


# ---------------------------------------------------------------------------
# tools_mcp.py + /mcp -- one toolset for every agent (2026-08-06).
# The owner: "There are different tools for you and Gemini? That should not be
# the case. Gemini and other agents should use the same custom tools as you
# do." A claude-cli persona had none of Agora's capability tools while
# build_system described all of them to it in prose; these serve the same
# client_tool_schemas/execute_tool pair the other providers use, over MCP.
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_mcp_grants():
    from agora_runner import tools_mcp
    tools_mcp._grants.clear()
    yield tools_mcp
    tools_mcp._grants.clear()


ALL_CAPS = {"vaultRead": True, "vaultWrite": True, "kubectlRead": True,
            "githubRead": True, "githubWrite": True, "githubMerge": True,
            "terminalExec": True, "manageAgora": True, "webSearch": True}


def _mcp_handler(body, token):
    from agora_runner import invoke_server
    handler = invoke_server.InvokeHandler.__new__(invoke_server.InvokeHandler)
    handler.path = "/mcp"
    raw = json.dumps(body).encode()
    handler.rfile = io.BytesIO(raw)
    handler.headers = {"Content-Length": str(len(raw)),
                       "Authorization": f"Bearer {token}"}
    sent = {}

    def fake_send(status, payload):
        sent["status"] = status
        sent["payload"] = payload
    handler._send = fake_send
    handler.send_response = lambda status: sent.setdefault("status", status)
    handler.send_header = lambda *a: None
    handler.end_headers = lambda: None
    return handler, sent


def test_mcp_grant_is_refused_when_the_persona_has_no_capabilities(clean_mcp_grants):
    """An MCP server advertising nothing is strictly worse than no MCP
    server: it costs the CLI a handshake to learn it is useless."""
    assert clean_mcp_grants.grant({"name": "Chat"}, dict(agora_runner.NO_CAPS), "conv-1") is None
    assert clean_mcp_grants._grants == {}


def test_mcp_rejects_an_unknown_token(clean_mcp_grants):
    status, payload = clean_mcp_grants.handle("not-a-real-token",
                                              {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 401
    assert "expired" in payload["error"]


def test_mcp_rejects_a_revoked_token(clean_mcp_grants):
    """The turn is over; the bridge pod must not keep tool access."""
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    clean_mcp_grants.revoke(token)
    status, _ = clean_mcp_grants.handle(token, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert status == 401


def test_mcp_initialize_echoes_the_client_protocol_version(clean_mcp_grants):
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    status, payload = clean_mcp_grants.handle(token, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2099-01-01"},
    })
    assert status == 200
    assert payload["result"]["protocolVersion"] == "2099-01-01"
    assert payload["result"]["serverInfo"]["name"] == "agora"


def test_mcp_initialize_falls_back_when_the_client_sends_no_version(clean_mcp_grants):
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    _, payload = clean_mcp_grants.handle(token, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    })
    assert payload["result"]["protocolVersion"] == clean_mcp_grants.DEFAULT_PROTOCOL_VERSION


def test_mcp_notification_gets_no_result_body(clean_mcp_grants):
    """notifications/initialized carries no `id`, and JSON-RPC forbids
    replying to it. The CLI sends exactly this between initialize and
    tools/list (measured live, v2.1.197)."""
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    status, payload = clean_mcp_grants.handle(
        token, {"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert status == 202
    assert payload is None


def test_mcp_tools_list_is_the_same_toolset_gemini_gets(clean_mcp_grants, runner):
    """The point of the whole module: not a second definition of the tools,
    the same one, translated at the edge (input_schema -> inputSchema)."""
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    _, payload = clean_mcp_grants.handle(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    served = payload["result"]["tools"]
    expected = runner.tools_schemas.client_tool_schemas(ALL_CAPS)

    assert [t["name"] for t in served] == [t["name"] for t in expected]
    assert {"vault_read", "kubectl_read", "create_pr", "merge_pr",
            "terminal_exec"} <= {t["name"] for t in served}
    for tool, source in zip(served, expected):
        assert tool["inputSchema"] == source["input_schema"]
        assert "input_schema" not in tool


def test_mcp_tools_list_respects_the_capability_gate(clean_mcp_grants):
    """Same gate the other providers run under -- a read-only persona must
    not be handed merge_pr just because it reached this endpoint."""
    caps = dict(agora_runner.NO_CAPS)
    caps["vaultRead"] = True
    token = clean_mcp_grants.grant({"name": "Reader"}, caps, "conv-1")
    _, payload = clean_mcp_grants.handle(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in payload["result"]["tools"]}
    assert "vault_read" in names
    assert "vault_write" not in names
    assert "merge_pr" not in names
    assert "terminal_exec" not in names


def test_mcp_grant_freezes_capabilities_for_the_turn(clean_mcp_grants):
    """A persona edited mid-cycle must not widen its own reach in flight."""
    caps = dict(agora_runner.NO_CAPS)
    caps["vaultRead"] = True
    token = clean_mcp_grants.grant({"name": "Reader"}, caps, "conv-1")
    caps["terminalExec"] = True  # the caller's dict changes underneath us
    _, payload = clean_mcp_grants.handle(token, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert "terminal_exec" not in {t["name"] for t in payload["result"]["tools"]}


def test_mcp_tools_call_runs_the_shared_dispatcher(clean_mcp_grants):
    persona = {"name": "Nova"}
    token = clean_mcp_grants.grant(persona, ALL_CAPS, "conv-1")
    seen = {}

    def fake_execute(name, args, p, conversation_id, active_step=None):
        seen.update(name=name, args=args, persona=p, conversation_id=conversation_id)
        return "the file contents"

    with patch.object(clean_mcp_grants, "execute_tool", side_effect=fake_execute):
        _, payload = clean_mcp_grants.handle(token, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "vault_read", "arguments": {"path": "a.md"}},
        })

    assert seen == {"name": "vault_read", "args": {"path": "a.md"},
                    "persona": persona, "conversation_id": "conv-1"}
    assert payload["result"]["content"] == [{"type": "text", "text": "the file contents"}]
    assert payload["result"]["isError"] is False


def test_mcp_tools_call_reports_a_failure_as_a_result_not_a_jsonrpc_error(clean_mcp_grants):
    """MCP draws this line deliberately and it matters here: a JSON-RPC
    error is a broken server the CLI may stop talking to, while an isError
    result reaches the model, which can read it and try something else."""
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    with patch.object(clean_mcp_grants, "execute_tool",
                      side_effect=RuntimeError("couchdb is down")):
        _, payload = clean_mcp_grants.handle(token, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "vault_read", "arguments": {"path": "a.md"}},
        })
    assert "error" not in payload
    assert payload["result"]["isError"] is True
    assert "couchdb is down" in payload["result"]["content"][0]["text"]


def test_mcp_tools_call_tolerates_missing_or_malformed_arguments(clean_mcp_grants):
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    with patch.object(clean_mcp_grants, "execute_tool", side_effect=lambda n, a, *r, **k: repr(a)):
        _, payload = clean_mcp_grants.handle(token, {
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "vault_read", "arguments": "not-a-dict"},
        })
    assert payload["result"]["content"][0]["text"] == "{}"


def test_mcp_unknown_method_is_a_jsonrpc_error(clean_mcp_grants):
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    _, payload = clean_mcp_grants.handle(token, {"jsonrpc": "2.0", "id": 9, "method": "resources/list"})
    assert payload["error"]["code"] == -32601


# --- the /mcp endpoint itself ---

def test_mcp_endpoint_reads_the_bearer_token_from_the_header(clean_mcp_grants):
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    handler, sent = _mcp_handler({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, token)
    handler.do_POST()
    assert sent["status"] == 200
    assert sent["payload"]["result"]["tools"]


def test_mcp_endpoint_401s_without_a_valid_bearer_token(clean_mcp_grants):
    clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    handler, sent = _mcp_handler({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, "wrong")
    handler.do_POST()
    assert sent["status"] == 401


def test_mcp_endpoint_sends_no_body_for_a_notification(clean_mcp_grants):
    """A body here would be a protocol violation; the handler has to take
    the send_response path rather than _send."""
    token = clean_mcp_grants.grant({"name": "Nova"}, ALL_CAPS, "conv-1")
    handler, sent = _mcp_handler({"jsonrpc": "2.0", "method": "notifications/initialized"}, token)
    handler.do_POST()
    assert sent["status"] == 202
    assert "payload" not in sent


# --- claude_cli hands the grant to the bridge ---

def test_claude_cli_generate_sends_an_mcp_block_for_a_capable_persona(runner, clean_mcp_grants):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        # Still live while the bridge call is in flight -- that is the
        # whole window in which the CLI can actually use it.
        captured["live"] = body["mcp"]["token"] in clean_mcp_grants._grants
        return 200, {"text": "the answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
            ALL_CAPS, {"name": "Nova"}, "conv-1",
        )
    assert captured["body"]["mcp"]["url"].endswith("/mcp")
    assert captured["live"] is True
    # ...and revoked the moment it returns.
    assert captured["body"]["mcp"]["token"] not in clean_mcp_grants._grants


def test_claude_cli_generate_revokes_the_mcp_grant_when_the_bridge_call_fails(runner,
                                                                             clean_mcp_grants):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        raise RuntimeError("bridge unreachable")

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        with pytest.raises(RuntimeError):
            runner.claude_cli_generate(
                "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
                ALL_CAPS, {"name": "Nova"}, "conv-1",
            )
    assert captured["body"]["mcp"]["token"] not in clean_mcp_grants._grants


def test_claude_cli_generate_omits_the_mcp_block_for_a_persona_with_no_capabilities(runner):
    captured = {}

    def fake_http_json(method, url, body=None, headers=None, timeout=300):
        captured["body"] = body
        return 200, {"text": "the answer", "thinking": ""}

    with patch.object(runner.providers.claude_cli, "http_json", side_effect=fake_http_json):
        runner.claude_cli_generate(
            "claude-opus-5", False, "sys", [{"role": "user", "content": "hi"}],
            dict(agora_runner.NO_CAPS), {"name": "Chat"}, "conv-1",
        )
    assert "mcp" not in captured["body"]


# ---------------------------------------------------------------------------
# Anchored interval schedules — the owner's issues.md capture, 2026-08-08:
# "run every 6 hours from 12:00, so it runs 12:00 the first time, then 18:00,
# then 24:00. Currently it runs 6 hours after the previous job was finished.
# So all Nova heartbeats starts at somewhat random timestamps."
# ---------------------------------------------------------------------------

from agora_runner.config import OSLO
from agora_runner.turns import last_anchored_occurrence, schedule_due


def _oslo(text):
    """A wall-clock Oslo instant, as UTC — the shape schedule_due takes."""
    return datetime.fromisoformat(text).replace(tzinfo=OSLO).astimezone(timezone.utc)


def test_anchored_interval_fires_on_the_clock_not_after_the_last_run():
    # Last run started 12:04 and ran long; the next slot is 18:00 sharp, not
    # 18:04 and not 18:20-something. This is the whole point of the anchor.
    late_finish = _oslo("2026-08-08T12:41:00").isoformat()
    assert not schedule_due("every@6h@12:00", late_finish, late_finish, _oslo("2026-08-08T17:59:00"))
    assert schedule_due("every@6h@12:00", late_finish, late_finish, _oslo("2026-08-08T18:00:00"))


def test_anchored_interval_does_not_refire_within_the_same_slot():
    ran_at = _oslo("2026-08-08T18:00:03").isoformat()
    assert not schedule_due("every@6h@12:00", ran_at, ran_at, _oslo("2026-08-08T23:59:00"))
    assert schedule_due("every@6h@12:00", ran_at, ran_at, _oslo("2026-08-09T00:00:00"))


def test_a_run_claimed_exactly_on_the_slot_does_not_immediately_refire():
    # lastRunAt landing on the slot instant to the microsecond is the case
    # that separates > from >=; with >= the heartbeat re-fires on the very
    # next poll tick, in a loop, for the whole slot. daily@ uses > for the
    # same reason.
    on_the_dot = _oslo("2026-08-08T18:00:00").isoformat()
    assert not schedule_due("every@6h@12:00", on_the_dot, on_the_dot, _oslo("2026-08-08T18:00:05"))
    assert schedule_due("every@6h@12:00", on_the_dot, on_the_dot, _oslo("2026-08-09T00:00:00"))


def test_anchored_interval_slots_wrap_past_midnight():
    # 12:00 + 6h twice lands on 00:00 the NEXT day, and the grid keeps going
    # from there — 06:00 is a slot too, not a 12:00-to-18:00-only window.
    assert last_anchored_occurrence("12:00", timedelta(hours=6),
                                    _oslo("2026-08-09T07:30:00")) == _oslo("2026-08-09T06:00:00")
    assert last_anchored_occurrence("12:00", timedelta(hours=6),
                                    _oslo("2026-08-09T02:00:00")) == _oslo("2026-08-09T00:00:00")


def test_anchored_slots_are_the_same_clock_times_every_day():
    day_one = {last_anchored_occurrence("12:00", timedelta(hours=6),
                                        _oslo(f"2026-08-08T{h:02d}:30:00")).astimezone(OSLO).strftime("%H:%M")
               for h in range(24)}
    day_two = {last_anchored_occurrence("12:00", timedelta(hours=6),
                                        _oslo(f"2026-08-09T{h:02d}:30:00")).astimezone(OSLO).strftime("%H:%M")
               for h in range(24)}
    assert day_one == day_two == {"00:00", "06:00", "12:00", "18:00"}


def test_the_occurrence_never_moves_backwards():
    # The floor comparison in schedule_due is only sound if the slot the grid
    # reports is monotone in time. Minute by minute across two days, over a
    # midnight boundary, it must never go back.
    start = _oslo("2026-08-08T00:00:00")
    seen = [last_anchored_occurrence("13:00", timedelta(hours=4), start + timedelta(minutes=m))
            for m in range(60 * 48)]
    assert seen == sorted(seen)


def test_a_non_dividing_interval_is_why_agora_rejects_one():
    # 7h doesn't divide 24h, so each day's grid disagrees with the previous
    # day's across midnight: 19:00 is the last slot when you ask at 23:30,
    # but at 00:30 the answer is 22:00 — later, so schedule_due fires again.
    # Agora's isValidSchedule refuses to create this; the assertion is here
    # so anyone relaxing that rule sees exactly what it was holding back.
    late = last_anchored_occurrence("12:00", timedelta(hours=7), _oslo("2026-08-08T23:30:00"))
    after_midnight = last_anchored_occurrence("12:00", timedelta(hours=7), _oslo("2026-08-09T00:30:00"))
    assert late.astimezone(OSLO).strftime("%m-%d %H:%M") == "08-08 19:00"
    assert after_midnight.astimezone(OSLO).strftime("%m-%d %H:%M") == "08-08 22:00"
    assert after_midnight > late  # a slot that did not exist an hour earlier


def test_anchored_interval_keeps_its_clock_time_across_a_dst_shift():
    # 2026-10-25 is the Oslo autumn shift (CEST +02:00 -> CET +01:00). The
    # 12:00 slot must still be 12:00 local, i.e. a different UTC instant than
    # the day before, which is what naive-then-localise buys us.
    before = last_anchored_occurrence("12:00", timedelta(hours=6), _oslo("2026-10-24T12:30:00"))
    after = last_anchored_occurrence("12:00", timedelta(hours=6), _oslo("2026-10-25T12:30:00"))
    assert before.astimezone(OSLO).strftime("%H:%M") == "12:00"
    assert after.astimezone(OSLO).strftime("%H:%M") == "12:00"
    assert before.utcoffset() == after.utcoffset() == timedelta(0)
    assert (after - before) == timedelta(hours=25)


def test_createdat_still_floors_the_first_run_of_an_anchored_heartbeat():
    created = _oslo("2026-08-08T13:00:00").isoformat()
    assert not schedule_due("every@6h@12:00", None, created, _oslo("2026-08-08T14:00:00"))
    assert schedule_due("every@6h@12:00", None, created, _oslo("2026-08-08T18:00:00"))


def test_unanchored_and_daily_schedules_are_untouched():
    ran_at = _oslo("2026-08-08T12:41:00").isoformat()
    assert not schedule_due("every@6h", ran_at, ran_at, _oslo("2026-08-08T18:00:00"))
    assert schedule_due("every@6h", ran_at, ran_at, _oslo("2026-08-08T18:41:00"))
    assert schedule_due("every@30m", ran_at, ran_at, _oslo("2026-08-08T13:11:00"))
    assert schedule_due("daily@08:00", ran_at, ran_at, _oslo("2026-08-09T08:00:00"))
    assert not schedule_due("daily@08:00", ran_at, ran_at, _oslo("2026-08-09T07:00:00"))


def test_anchored_minutes_work_too():
    ran_at = _oslo("2026-08-08T12:07:00").isoformat()
    assert not schedule_due("every@30m@00:00", ran_at, ran_at, _oslo("2026-08-08T12:29:00"))
    assert schedule_due("every@30m@00:00", ran_at, ran_at, _oslo("2026-08-08T12:30:00"))


# ---------------------------------------------------------------------------
# cron schedules -- the owner's issues.md #37, second half: "Maybe have it so we
# can tweak it as cronjobs or just as advanced timesetting. But it has to be
# user friendly." Cron is the storage format; the picker in the heartbeat form
# is the user-friendly half. These pin the three things the anchored interval
# could not say: weekdays only, two fixed times a day, and a daytime-only
# window.
# ---------------------------------------------------------------------------

from agora_runner.turns import last_cron_occurrence, parse_cron_field


def test_weekdays_only_skips_the_weekend():
    # 2026-08-08 is a Saturday, 2026-08-10 a Monday.
    ran = _oslo("2026-08-07T08:00:01").isoformat()
    assert not schedule_due("cron@0 8 * * 1-5", ran, ran, _oslo("2026-08-08T08:00:00"))
    assert not schedule_due("cron@0 8 * * 1-5", ran, ran, _oslo("2026-08-09T23:00:00"))
    assert schedule_due("cron@0 8 * * 1-5", ran, ran, _oslo("2026-08-10T08:00:00"))


def test_two_fixed_times_a_day():
    slots = {last_cron_occurrence("0 8,20 * * *", _oslo(f"2026-08-10T{h:02d}:30:00"))
             .astimezone(OSLO).strftime("%m-%d %H:%M") for h in range(24)}
    assert slots == {"08-09 20:00", "08-10 08:00", "08-10 20:00"}


def test_a_daytime_only_window():
    # "not at night" as an hour range with a step -- 08:00 to 22:00, every 2h.
    slots = sorted({last_cron_occurrence("0 8-22/2 * * *", _oslo(f"2026-08-10T{h:02d}:30:00"))
                    .astimezone(OSLO).strftime("%H:%M") for h in range(24)})
    assert slots == ["08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"]


def test_the_cron_occurrence_never_moves_backwards():
    # Same soundness requirement as the anchored grid: schedule_due compares
    # the reported slot against a floor, so a slot that ever goes backwards
    # would let a heartbeat re-fire.
    start = _oslo("2026-08-08T00:00:00")
    seen = [last_cron_occurrence("0 8,20 * * 1-5", start + timedelta(minutes=m))
            for m in range(60 * 24 * 5)]
    assert seen == sorted(seen)


def test_a_cron_slot_is_never_in_the_future():
    # Two slots inside the SAME hour is the case the minute-level guard exists
    # for: asked at 08:30, the last occurrence is 08:00, not the 08:45 that has
    # not happened yet. Without it the heartbeat fires fifteen minutes early
    # and keeps doing so for every slot after. Found by mutation -- the
    # weekday/twice-a-day tests all have one minute per hour, so none of them
    # could see it.
    assert last_cron_occurrence("0,45 8 * * *", _oslo("2026-08-10T08:30:00")) == _oslo("2026-08-10T08:00:00")
    assert last_cron_occurrence("0,45 8 * * *", _oslo("2026-08-10T08:45:00")) == _oslo("2026-08-10T08:45:00")
    assert last_cron_occurrence("0,45 8 * * *", _oslo("2026-08-10T08:00:00")) == _oslo("2026-08-10T08:00:00")
    # And structurally, across a whole day at the finest granularity anyone
    # would pick: the slot reported may never be later than the instant asked
    # about, or schedule_due fires ahead of the clock.
    for m in range(60 * 24):
        now = _oslo("2026-08-10T00:00:00") + timedelta(minutes=m)
        assert last_cron_occurrence("*/15 * * * *", now) <= now


def test_a_cron_slot_does_not_refire_within_itself():
    on_the_dot = _oslo("2026-08-10T08:00:00").isoformat()
    assert not schedule_due("cron@0 8 * * *", on_the_dot, on_the_dot, _oslo("2026-08-10T08:00:05"))
    assert schedule_due("cron@0 8 * * *", on_the_dot, on_the_dot, _oslo("2026-08-11T08:00:00"))


def test_cron_keeps_its_clock_time_across_a_dst_shift():
    # 2026-10-25 is the Oslo autumn shift. 08:00 stays 08:00 local, so the two
    # instants are 25 hours apart, not 24.
    before = last_cron_occurrence("0 8 * * *", _oslo("2026-10-24T09:00:00"))
    after = last_cron_occurrence("0 8 * * *", _oslo("2026-10-25T09:00:00"))
    assert before.astimezone(OSLO).strftime("%H:%M") == "08:00"
    assert after.astimezone(OSLO).strftime("%H:%M") == "08:00"
    assert (after - before) == timedelta(hours=25)


def test_day_of_month_and_day_of_week_are_ORed_not_ANDed():
    # Vixie cron's rule, and the one people get wrong. "1 * 1" is the 1st of
    # the month AND every Monday. 2026-09-01 is a Tuesday, so it matches on
    # day-of-month alone; 2026-09-07 is a Monday that is not the 1st.
    both = "0 8 1 * 1"
    assert last_cron_occurrence(both, _oslo("2026-09-01T09:00:00")) == _oslo("2026-09-01T08:00:00")
    assert last_cron_occurrence(both, _oslo("2026-09-07T09:00:00")) == _oslo("2026-09-07T08:00:00")
    # With only day-of-month restricted, day-of-week is ignored entirely.
    assert last_cron_occurrence("0 8 1 * *", _oslo("2026-09-07T09:00:00")) == _oslo("2026-09-01T08:00:00")


def test_sunday_is_both_0_and_7():
    assert parse_cron_field("0", 4) == parse_cron_field("7", 4) == {0}
    assert parse_cron_field("1-7", 4) == {0, 1, 2, 3, 4, 5, 6}
    assert parse_cron_field("*", 4) == {0, 1, 2, 3, 4, 5, 6}


def test_cron_fields_reject_what_they_cannot_mean():
    # index 1 is the hour field, 0-23.
    for bad in ["24", "5-2", "*/0", "5/15", "", "abc", "-1"]:
        with pytest.raises(ValueError):
            parse_cron_field(bad, 1)
    with pytest.raises(ValueError):
        parse_cron_field("60", 0)  # minutes are 0-59


def test_a_malformed_cron_never_fires_and_never_raises():
    # A hand-edited heartbeat file must not take down the poll loop for every
    # other heartbeat -- schedule_due swallows the parse error and says "no".
    ran = _oslo("2026-08-08T12:00:00").isoformat()
    for bad in ["cron@not a cron at all", "cron@0 8 * *", "cron@99 8 * * *", "cron@"]:
        assert schedule_due(bad, ran, ran, _oslo("2026-08-10T08:00:00")) is False


def test_createdat_still_floors_the_first_run_of_a_cron_heartbeat():
    created = _oslo("2026-08-10T09:00:00").isoformat()
    assert not schedule_due("cron@0 8 * * *", None, created, _oslo("2026-08-10T12:00:00"))
    assert schedule_due("cron@0 8 * * *", None, created, _oslo("2026-08-11T08:00:00"))


def test_an_unreachable_cron_never_fires_rather_than_guessing():
    # 30 February: parseable, valid per-field, and matches no day that exists.
    # The walk gives up after CRON_LOOKBACK_DAYS and returns None; the
    # heartbeat simply never becomes due, which is the safe direction.
    assert last_cron_occurrence("0 8 30 2 *", _oslo("2026-08-10T09:00:00")) is None
    ran = _oslo("2026-08-08T12:00:00").isoformat()
    assert not schedule_due("cron@0 8 30 2 *", ran, ran, _oslo("2026-08-10T08:00:00"))


def test_the_older_schedule_syntaxes_are_untouched_by_cron():
    ran = _oslo("2026-08-08T12:41:00").isoformat()
    assert schedule_due("every@6h", ran, ran, _oslo("2026-08-08T18:41:00"))
    assert schedule_due("every@6h@12:00", ran, ran, _oslo("2026-08-08T18:00:00"))
    assert schedule_due("daily@08:00", ran, ran, _oslo("2026-08-09T08:00:00"))


# ---------------------------------------------------------------------------
# Incremental conversation polling (agora#51's ?after+?rev, runner side)
#
# The poll loop re-read the whole FETCH_LIMIT window for every conversation
# every POLL_INTERVAL_SECONDS, and almost every tick nothing had changed.
# Measured against the live pod 2026-08-10: five polled conversations,
# 247,890 bytes per tick, 4.28 GB/day to learn nothing.
#
# Every test below drives a fake server that reimplements the real contract
# from agora's src/server.ts -- the same prefix fingerprint over
# (id, forgotten, text), the same "both params or neither" rule, the same
# incremental flag. The point is to pin that the client agrees with the
# *server*, not merely with whatever this file expects it to send.
# ---------------------------------------------------------------------------

import hashlib
from urllib.parse import parse_qsl, urlparse


def _fake_messages_server(runner, all_messages):
    """Mirrors GET /conversations/:id/messages. `all_messages` is mutated by
    the test to simulate what happened in the conversation between ticks."""
    requests = []

    def prefix_rev(end_index):
        h = hashlib.sha1()
        for m in all_messages[: end_index + 1]:
            h.update(json.dumps([m["id"], m.get("forgotten") is True, m["text"]]).encode())
        return h.hexdigest()[:16]

    def fake_agora_get(path):
        query = dict(parse_qsl(urlparse(path).query))
        requests.append(query)
        limit = int(query.get("limit", 0))
        after, client_rev = query.get("after", ""), query.get("rev", "")
        window = all_messages[-limit:] if limit > 0 else list(all_messages)

        after_index = -1
        if after and client_rev:
            after_index = next(
                (i for i, m in enumerate(all_messages) if m["id"] == after), -1)
        incremental = after_index >= 0 and prefix_rev(after_index) == client_rev
        messages = all_messages[after_index + 1:] if incremental else window

        return 200, {
            "name": "Test",
            "personas": None,
            "incremental": incremental,
            "rev": prefix_rev(len(all_messages) - 1),
            "totalMessages": len(all_messages),
            "messages": [dict(m) for m in messages],
        }

    return fake_agora_get, requests


def _msg(n, sender="Edvard", text=None):
    return {"id": f"m{n}", "sender": sender, "text": text or f"message {n}", "forgotten": False}


@pytest.fixture
def polling(runner):
    """poll_conversation with turn-taking stubbed out -- these tests are
    about what it fetches and what window it hands on, not about replying.
    Records the thread decide_turn actually saw on each tick."""
    runner.conversations._message_window_cache.clear()
    summary = {"id": "conv-1", "name": "Test", "archived": False, "status": "active"}
    seen = []

    def fake_decide_turn(thread, personas):
        seen.append([dict(m) for m in thread])
        return []          # no turn -> poll_conversation returns right after

    try:
        yield summary, seen, fake_decide_turn
    finally:
        runner.conversations._message_window_cache.clear()


def test_the_first_poll_asks_for_a_window_and_the_next_one_asks_for_the_delta(runner, polling):
    summary, seen, fake_decide_turn = polling
    messages = [_msg(1), _msg(2)]
    fake_agora_get, requests = _fake_messages_server(runner, messages)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        runner.poll_conversation(summary)

    assert "after" not in requests[0] and "rev" not in requests[0], \
        "nothing is cached yet, so the first tick must ask for the plain window"
    assert requests[1]["after"] == "m2", "should stand on the last message it holds"
    assert requests[1]["rev"], "after without rev is ignored by the server"
    assert seen[1] == seen[0], "an unchanged conversation must yield an unchanged window"


def test_a_delta_is_appended_to_the_window_rather_than_replacing_it(runner, polling):
    """The whole risk of this change: decide_turn and merge_history want the
    window, and an incremental response carries only what is new. If the
    delta were passed straight through, every turn decision after the first
    tick would be made on a one-message history."""
    summary, seen, fake_decide_turn = polling
    messages = [_msg(1), _msg(2)]
    fake_agora_get, requests = _fake_messages_server(runner, messages)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        messages.append(_msg(3, sender="Nova"))
        runner.poll_conversation(summary)

    assert [m["id"] for m in seen[0]] == ["m1", "m2"]
    assert [m["id"] for m in seen[1]] == ["m1", "m2", "m3"], \
        "the new message must arrive on top of the window we already held"


def test_an_edit_behind_us_replaces_the_window_instead_of_appending(runner, polling):
    """The owner edits or deletes an older message: the server's fingerprint of
    the prefix stops matching, it answers incremental=False with the whole
    window, and we must replace. Appending here would duplicate history."""
    summary, seen, fake_decide_turn = polling
    messages = [_msg(1), _msg(2)]
    fake_agora_get, requests = _fake_messages_server(runner, messages)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        messages[0]["text"] = "edited after the fact"
        runner.poll_conversation(summary)

    assert [m["id"] for m in seen[1]] == ["m1", "m2"], "no duplication"
    assert seen[1][0]["text"] == "edited after the fact", "must show the edit, not our stale copy"


def test_a_forget_behind_us_also_replaces_the_window(runner, polling):
    """`forgotten` is in the server's fingerprint precisely so this path
    works without the client having to notice it happened."""
    summary, seen, fake_decide_turn = polling
    messages = [_msg(1), _msg(2)]
    fake_agora_get, requests = _fake_messages_server(runner, messages)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        messages[0]["forgotten"] = True
        runner.poll_conversation(summary)

    assert [m["id"] for m in seen[1]] == ["m1", "m2"]
    assert seen[1][0]["forgotten"] is True


def test_the_window_never_grows_past_fetch_limit(runner, polling):
    """Appending deltas forever would turn the cache into the whole
    conversation, which is the unbounded response ?limit exists to prevent."""
    summary, seen, fake_decide_turn = polling
    messages = [_msg(n) for n in range(runner.FETCH_LIMIT)]
    fake_agora_get, requests = _fake_messages_server(runner, messages)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        for n in range(runner.FETCH_LIMIT, runner.FETCH_LIMIT + 5):
            messages.append(_msg(n))
            runner.poll_conversation(summary)

    assert len(seen[-1]) == runner.FETCH_LIMIT
    assert [m["id"] for m in seen[-1]] == [f"m{n}" for n in range(5, runner.FETCH_LIMIT + 5)], \
        "the window must be the newest FETCH_LIMIT, exactly as a full fetch would return"


def test_the_incremental_window_matches_what_a_full_fetch_would_have_returned(runner, polling):
    """The strongest form of the claim: after a run of mixed appends and
    edits, the window built incrementally is identical to the one the server
    would hand a client that had never cached anything."""
    summary, seen, fake_decide_turn = polling
    messages = [_msg(n) for n in range(3)]
    fake_agora_get, requests = _fake_messages_server(runner, messages)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        for n in range(3, 12):
            messages.append(_msg(n))
            if n == 7:
                messages[1]["text"] = "edited mid-run"
            runner.poll_conversation(summary)
        incremental_window = seen[-1]

        runner.conversations._message_window_cache.clear()   # a client with no cache
        runner.poll_conversation(summary)
        full_window = seen[-1]

    assert incremental_window == full_window


def test_a_server_that_never_heard_of_rev_still_works(runner, polling):
    """Deploy ordering: the runner may roll before agora does. No rev in the
    response means nothing is cached, so every tick asks for a plain window --
    exactly the old behaviour, no error path."""
    summary, seen, fake_decide_turn = polling

    def old_server(path):
        assert "after=" not in path, "must not send after to a server that gave us no rev"
        return 200, {"name": "Test", "personas": None, "messages": [_msg(1), _msg(2)]}

    with patch.object(runner.conversations, "agora_get", side_effect=old_server), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn):
        runner.poll_conversation(summary)
        runner.poll_conversation(summary)

    assert [m["id"] for m in seen[1]] == ["m1", "m2"]
    assert runner.conversations._message_window_cache == {}


def test_pruning_drops_windows_for_conversations_that_are_gone(runner):
    """The Nova heartbeat rotates into a new conversation every cycle, so an
    unpruned cache grows by a 40-message window a cycle for as long as the
    pod lives."""
    runner.conversations._message_window_cache.clear()
    runner.conversations._message_window_cache.update({
        "still-here": ("m1", "rev1", [_msg(1)]),
        "long-gone": ("m9", "rev9", [_msg(9)]),
    })

    runner.prune_message_window_cache(["still-here", "brand-new"])

    assert set(runner.conversations._message_window_cache) == {"still-here"}


# --- Metered-provider guard (2026-08-10, the owner's "hard rule" capture in
# issues.md: the prepaid Anthropic balance had $16 left and will not be
# refilled, so no scheduled thing may spend it). Two halves, tested apart:
# reply.py must refuse, AND the unattended call sites must actually ask it
# to. A guard nobody passes `unattended=True` to is worth nothing, and a
# test of only the first half passes either way.

def test_unattended_turn_on_metered_provider_is_refused_before_any_spend(runner):
    """The refusal must happen in dispatch, not in the provider -- the point
    is that no request reaches api.anthropic.com at all."""
    persona = {"name": "Test", "model": "anthropic:claude-haiku-4-5-20251001"}

    with patch.object(runner.reply, "anthropic_generate") as mock_gen:
        with pytest.raises(runner.reply.MeteredProviderBlocked) as excinfo:
            runner.generate_reply(
                persona, dict(runner.NO_CAPS), "system", [{"role": "user", "content": "hi"}],
                "conv-1", unattended=True,
            )
    mock_gen.assert_not_called()
    # Names the twin to move to, so the fix is in the error rather than in
    # someone's memory of this rule.
    assert "claude-cli:claude-haiku-4-5-20251001" in str(excinfo.value)


def test_attended_turn_on_metered_provider_still_runs(runner):
    """The owner kept testing and research allowed -- a person typing in the app
    is bounded by the person. Blocking this would be over-reading the rule."""
    persona = {"name": "Test", "model": "anthropic:claude-haiku-4-5-20251001"}

    with patch.object(runner.reply, "anthropic_generate", return_value="billed reply") as mock_gen:
        result = runner.generate_reply(
            persona, dict(runner.NO_CAPS), "system", [{"role": "user", "content": "hi"}], "conv-1",
        )
    assert result == "billed reply"
    mock_gen.assert_called_once()


def test_unattended_turn_on_subscription_provider_is_untouched(runner):
    """The guard is provider-scoped, not a blanket ban on unattended turns --
    Nova's own hourly cycle is exactly this call and must keep working."""
    persona = {"name": "Nova", "model": "claude-cli:claude-opus-5"}

    with patch.object(runner.reply, "claude_cli_generate", return_value="cycle reply") as mock_gen:
        result = runner.generate_reply(
            persona, dict(runner.NO_CAPS), "system", [{"role": "user", "content": "hi"}],
            "conv-1", unattended=True,
        )
    assert result == "cycle reply"
    mock_gen.assert_called_once()


def test_allow_metered_unattended_env_flag_reopens_the_path(runner):
    """A deliberate override the owner can set in one config line, so this is a
    guarded capability rather than a deleted one."""
    persona = {"name": "Test", "model": "anthropic:claude-sonnet-5"}

    with patch.object(runner.reply, "ALLOW_METERED_UNATTENDED", True), \
         patch.object(runner.reply, "anthropic_generate", return_value="overridden") as mock_gen:
        result = runner.generate_reply(
            persona, dict(runner.NO_CAPS), "system", [{"role": "user", "content": "hi"}],
            "conv-1", unattended=True,
        )
    assert result == "overridden"
    mock_gen.assert_called_once()


def test_run_heartbeat_declares_itself_unattended(runner):
    """Second half: the call site. Without this the guard above is dead code."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "claude-cli:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None, unattended=False):
        captured["unattended"] = unattended
        return "a real report"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.heartbeats, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)

    assert captured["unattended"] is True


def test_blocked_heartbeat_records_the_refusal_as_its_result(runner):
    """End to end through the real call site: a scheduled anthropic persona
    must fail loudly into lastResult (which the app shows) rather than
    silently spending or silently doing nothing."""
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    updates = []

    def fake_agora_internal(method, path, payload=None):
        if method == "PATCH" and path == "/heartbeats/hb1":
            updates.append(payload)
        return 200, {}

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.reply, "anthropic_generate") as mock_gen, \
         patch.object(runner.heartbeats, "notify") as mock_notify, \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", side_effect=fake_agora_internal):
        runner.run_heartbeat(heartbeat)

    mock_gen.assert_not_called()
    mock_notify.assert_not_called()
    assert "metered" in updates[-1]["lastResult"]


def test_workflow_round_declares_itself_unattended(runner):
    """The other unattended call site -- workflows are heartbeat-triggered
    only, so they drain exactly the same way."""
    captured = {}

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None,
                             sticky=False, on_text=None, on_thinking=None, active_step=None,
                             unattended=False):
        captured["unattended"] = unattended
        return "step reply"

    steps = [{"prompt": "go", "loopCount": 1, "personaIds": ["p1"], "toolWhitelist": []}]
    persona = {"id": "p1", "name": "Step", "model": "claude-cli:claude-sonnet-5",
               "capabilities": dict(runner.NO_CAPS)}
    participants = [{"personaId": "p1"}]

    with patch.object(runner.workflows, "fetch_persona_uncached", return_value=persona), \
         patch.object(runner.workflows, "generate_reply", side_effect=fake_generate_reply), \
         patch.object(runner.workflows, "notify", return_value=(200, "mid-1")), \
         patch.object(runner.workflows, "audit"), \
         patch.object(runner.workflows, "agora_get", return_value=(200, {"messages": []})):
        runner.workflows.run_workflow_steps(
            steps, "conv-1", {"personas": participants, "messages": []}, participants,
        )

    assert captured["unattended"] is True


def test_schedule_minutes_is_the_one_definition_of_an_every_interval():
    """Extracted from `schedule_due` so `cycle_health` measures a stalled
    loop in the interval that is actually running, rather than in a
    constant nobody updates when the owner changes the cadence."""
    from agora_runner.turns import schedule_minutes

    assert schedule_minutes("every@40m") == 40
    assert schedule_minutes("every@60m") == 60
    assert schedule_minutes("every@6h") == 360
    # The anchor says *when* the grid lands, never how long the wait is.
    assert schedule_minutes("every@60m@19:00") == 60
    assert schedule_minutes("every@6h@12:00") == 360


def test_schedule_minutes_refuses_everything_with_no_single_interval():
    """`None` rather than a guess. A `cron@` heartbeat genuinely has no
    interval, and both callers have to decide what to do about that --
    `schedule_due` computes the occurrence instead, `nova_health_note`
    falls back to the constant."""
    from agora_runner.turns import schedule_minutes

    for schedule in ("cron@0 8 * * 1-5", "daily@08:00", "", None, "hourly",
                     "every@", "every@abc", "every@m", "every@-5m", "every@0m"):
        assert schedule_minutes(schedule) is None, schedule


def test_a_hand_edited_every_schedule_no_longer_takes_the_poll_loop_down():
    """`every@abc` used to raise ValueError straight out of `schedule_due`,
    which runs inside the loop that polls *every* heartbeat -- so one bad
    schedule stopped all the others. The cron@ branch has guarded against
    exactly this since it was written; this branch never did.

    `every@0m` is the same guard from the other side: it made `delta` zero,
    so the heartbeat read as due on every single pass of the poll loop.
    """
    ran = _oslo("2026-08-08T12:00:00").isoformat()
    now = _oslo("2026-08-09T12:00:00")

    assert not schedule_due("every@abc", ran, ran, now)
    assert not schedule_due("every@", ran, ran, now)
    assert not schedule_due("every@0m", ran, ran, now)
    # A day later on a valid schedule is still due -- the guard is narrow.
    assert schedule_due("every@6h", ran, ran, now)


def test_run_heartbeat_hands_the_journal_health_check_its_own_schedule(runner):
    """The wire, not the calculation.

    `cycle_health` measures a silent loop in heartbeat intervals, and
    `nova_health_note` takes the interval from the schedule it is handed.
    Nothing pinned `run_heartbeat` actually handing one over -- so dropping
    the argument at the call site left every test of the calculation green
    while the check went back to measuring against a 60-minute constant,
    which is the exact bug. A reintroduction with no failure anywhere is
    the one shape a mutation check exists to catch.
    """
    seen = {}

    def spy(persona, previous_run_at, schedule=None):
        seen["schedule"] = schedule
        return ""

    with patch.object(runner.heartbeats, "nova_health_note", side_effect=spy):
        _heartbeat_run(runner, {
            "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
            "schedule": "every@40m@19:00", "name": "Nova"})

    assert seen["schedule"] == "every@40m@19:00"


# ---------------------------------------------------------------------------
# 2026-08-14, the owner: "Did you fix the notification for agora heartbeats?
# So i can turn them off?" -- pushNotifications:false on the heartbeat
# mutes the phone push for its reply. The message is still posted.
# ---------------------------------------------------------------------------

def _run_heartbeat_capturing_push(runner, heartbeat):
    persona = {"id": "p1", "name": "Test", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    detail = {"personas": [], "messages": [], "stickyFallback": False}
    calls = []

    def fake_notify(conversation_id, text, sender, system=False, push=True, thinking=False):
        calls.append({"text": text, "push": push})
        return 200, "mid-1"

    with patch.object(runner.heartbeats, "fetch_persona", return_value=persona), \
         patch.object(runner.heartbeats, "agora_get", return_value=(200, detail)), \
         patch.object(runner.heartbeats, "generate_reply", return_value="a real report"), \
         patch.object(runner.heartbeats, "notify", side_effect=fake_notify), \
         patch.object(runner.heartbeats, "audit"), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {})):
        runner.run_heartbeat(heartbeat)
    return calls


def test_run_heartbeat_mutes_push_when_notifications_are_off(runner):
    """The reply must still be posted -- only the phone buzz is withheld,
    exactly like quiet hours. Dropping the message instead would throw
    away the cycle's whole reply, which is not what turning a
    notification off means."""
    calls = _run_heartbeat_capturing_push(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@1h", "name": "HB", "pushNotifications": False,
    })
    assert [c["text"] for c in calls] == ["a real report"]
    assert calls[0]["push"] is False


def test_run_heartbeat_pushes_when_field_is_absent(runner):
    """Absent means notify. Every heartbeat that existed before this field
    was added has no such key, and must keep buzzing the phone."""
    calls = _run_heartbeat_capturing_push(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@1h", "name": "HB",
    })
    assert calls[0]["push"] is True


def test_run_heartbeat_pushes_when_notifications_are_on(runner):
    calls = _run_heartbeat_capturing_push(runner, {
        "id": "hb1", "personaId": "p1", "conversationId": "conv-1",
        "schedule": "every@1h", "name": "HB", "pushNotifications": True,
    })
    assert calls[0]["push"] is True


# ---------------------------------------------------------------------------
# The reviewer's finding on this change: run_heartbeat honoured
# pushNotifications and run_workflow_heartbeat did not, while the Studio drew
# the heartbeat as muted either way. A mute that lies is worse than no mute --
# nothing on screen tells you it is not working.
# ---------------------------------------------------------------------------

def _run_workflow_heartbeat_capturing_push(runner, heartbeat, steps=None, sub_workflow=None):
    steps = steps or [{"prompt": "", "loopCount": 1, "toolWhitelist": []}]
    workflow = {"id": "wf1", "name": "Discuss", "steps": steps}
    detail = {"personas": [{"personaId": "p1", "name": "A", "role": "curator"}], "messages": []}
    persona = {"id": "p1", "name": "A", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.NO_CAPS)}
    pushes = []

    def fake_fetch_workflow(workflow_id):
        return sub_workflow if sub_workflow and workflow_id == "sub1" else workflow

    def fake_notify(conversation_id, text, sender, system=False, push=True, thinking=False):
        pushes.append(push)
        return 200, "mid-1"

    with patch.object(runner.workflows, "fetch_workflow", side_effect=fake_fetch_workflow), \
         patch.object(runner.workflows, "agora_get", return_value=(200, detail)), \
         patch.object(runner.workflows, "fetch_persona_uncached", return_value=persona), \
         patch.object(runner.workflows, "generate_reply", return_value="a real reply"), \
         patch.object(runner.workflows, "notify", side_effect=fake_notify), \
         patch.object(runner.workflows, "audit"), \
         patch.object(runner.workflows, "agora_internal", return_value=(200, {})):
        runner.run_workflow_heartbeat(heartbeat)
    return pushes


def test_workflow_heartbeat_mutes_push_when_notifications_are_off(runner):
    pushes = _run_workflow_heartbeat_capturing_push(runner, {
        "id": "hb1", "name": "WF HB", "schedule": "every@1h",
        "conversationId": "c1", "workflowId": "wf1", "pushNotifications": False,
    })
    assert pushes == [False]


def test_workflow_heartbeat_pushes_when_field_is_absent(runner):
    pushes = _run_workflow_heartbeat_capturing_push(runner, {
        "id": "hb1", "name": "WF HB", "schedule": "every@1h",
        "conversationId": "c1", "workflowId": "wf1",
    })
    assert pushes == [True]


def test_workflow_heartbeat_mute_reaches_a_sub_workflow(runner):
    """The engine recurses into workflowRef steps, so a flag threaded only
    into the top level would leave every nested round still buzzing."""
    pushes = _run_workflow_heartbeat_capturing_push(
        runner,
        {"id": "hb1", "name": "WF HB", "schedule": "every@1h",
         "conversationId": "c1", "workflowId": "wf1", "pushNotifications": False},
        steps=[{"prompt": "", "loopCount": 1, "toolWhitelist": [], "workflowRef": "sub1"}],
        sub_workflow={"id": "sub1", "steps": [{"prompt": "inner", "loopCount": 1, "toolWhitelist": []}]},
    )
    assert pushes == [False]


def test_poll_conversation_reports_whether_it_actually_spoke(runner):
    """poll_once decides on this return value whether to stamp the
    answered-live chip, so "did we speak" has to be readable from the
    outside. Every early return is a reason we did not, and the truthy
    one only lands after speak() has succeeded.

    Written because the two ends of that contract sit in different
    modules: a refactor that drops the `return True` breaks nothing that
    fails loudly -- poll_once just silently stops stamping, and every
    live-answered message quietly starts getting answered twice."""
    runner._conversation_failures.clear()
    runner._conversation_backoff.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = \
        _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", return_value="a reply"):
        assert runner.poll_conversation(summary) is True

    # An archived conversation is the cheapest of the "did not speak"
    # branches and stands in for all of them.
    assert not runner.poll_conversation({"id": "c1", "archived": True})


def test_a_running_cycle_keeps_its_own_conversation_out_of_the_live_set(runner):
    """The race that made the first draft of this change unmergeable.

    Heartbeat runs have had their own thread since 2026-08-08, so
    poll_once keeps ticking every five seconds while a cycle executes --
    and rotate_cycle_conversation points `conversationId` at the new
    transcript at the START of the run. Answering live there would call
    the bridge with the same conversation_id the running cycle is already
    resumed on, from the main thread, within five seconds, and again on
    every tick until the backoff cap.

    So while the run is in flight the conversation is deferred, exactly
    as it was before this change; it only goes live once the thread is
    done. Asserted through poll_once rather than the helper, because the
    bug this pins is that poll_conversation gets called at all."""
    conversations_body = {"conversations": [{"id": "c-live", "name": "Nova — Cycle 10"}]}
    heartbeats_body = {"heartbeats": [
        {"id": "hb1", "enabled": True, "rotateConversationEachRun": True,
         "conversationId": "c-live"},
    ]}

    class _StillRunning:
        def is_alive(self):
            return True

    def _poll(acked_into, polled_into):
        with patch.object(runner.poll, "agora_get",
                          side_effect=lambda p: (200, conversations_body) if p == "/conversations" else (404, {})), \
             patch.object(runner.poll, "agora_internal",
                          side_effect=lambda m, p, payload=None: (200, heartbeats_body)), \
             patch.object(runner.poll, "poll_conversation",
                          side_effect=lambda s: polled_into.append(s["id"]) or True), \
             patch.object(runner.poll, "acknowledge_deferred",
                          side_effect=lambda s: acked_into.append(s["id"])), \
             patch.object(runner.poll, "mark_answered_live"), \
             patch.object(runner.poll, "run_due_heartbeats"):
            runner.poll_once()

    runner.heartbeats._heartbeat_threads["hb1"] = [_StillRunning()]
    try:
        acked, polled = [], []
        _poll(acked, polled)
        assert polled == [], "answered live while its own cycle was mid-run"
        assert acked == ["c-live"], "should still get the deferred chip meanwhile"
    finally:
        runner.heartbeats._heartbeat_threads.pop("hb1", None)

    # Same fixture, no run in flight: it goes live.
    acked, polled = [], []
    _poll(acked, polled)
    assert polled == ["c-live"]
    assert acked == []


def test_since_and_the_answered_live_chip_filter_together(runner):
    """Reviewer finding: the two cutoffs in _unread_from_edvard had no
    test exercising them at once, and the existing `since` fixtures use
    `+00:00` suffixes while the new chip ones used `Z`. Both filters are
    plain string comparisons, so the mixed case is the one worth pinning.

    Here `since` retires the first message and the chip retires the
    second, leaving only the third -- which no single-filter test can
    distinguish from either filter doing all the work alone."""
    detail = {"messages": [
        {"sender": "Edvard", "text": "before the last run", "ts": "2026-08-19T19:00:00+00:00"},
        {"sender": "Edvard", "text": "answered live", "ts": "2026-08-19T20:00:00+00:00"},
        {"sender": "Nova", "ts": "2026-08-19T20:00:06+00:00",
         "activity": {"capability": runner.deferred.ANSWERED_LIVE_CAPABILITY}},
        {"sender": "Edvard", "text": "still waiting", "ts": "2026-08-19T20:05:00+00:00"},
    ]}

    got = runner.heartbeats._unread_from_edvard(detail, since="2026-08-19T19:30:00+00:00")
    assert got == "still waiting"
