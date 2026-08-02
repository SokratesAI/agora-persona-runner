"""
Tests for the agora_runner package (moved here 2026-07-29 from agora-config's
persona-runner.yaml embedded ConfigMap script -- Edvard's explicit ask to
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

import json
import urllib.parse
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


def test_vault_write_path_backs_up_to_lowercase_agora_backups(runner):
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
    backup_paths = [p for p in decoded if "agora/backups/" in p]
    assert len(backup_paths) == 1
    assert all("Agora/Backups" not in p for p in decoded)


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
    with patch.object(runner.vault, "vault_read_path",
                       return_value="---\nfm\n---\n\n## Entries\n\nold entry text"), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        result = runner.vault_append_path("journal.md", "new entry text", after_marker="## Entries")
    assert result == "written"
    written_content = mock_write.call_args[0][1]
    assert written_content == "---\nfm\n---\n\n## Entries\n\nnew entry text\n\nold entry text"


def test_vault_append_path_appends_at_end_when_no_marker_given(runner):
    with patch.object(runner.vault, "vault_read_path", return_value="line one"), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        runner.vault_append_path("notes.md", "line two")
    assert mock_write.call_args[0][1] == "line one\n\nline two\n"


def test_vault_append_path_appends_at_end_when_marker_not_found(runner):
    with patch.object(runner.vault, "vault_read_path", return_value="no marker here"), \
         patch.object(runner.vault, "vault_write_path", return_value="written") as mock_write:
        runner.vault_append_path("notes.md", "addition", after_marker="## Missing")
    assert mock_write.call_args[0][1] == "no marker here\n\naddition\n"


def test_vault_append_path_fails_loudly_for_a_missing_file(runner):
    with patch.object(runner.vault, "vault_read_path", return_value=None), \
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
    mock_write.assert_called_once_with("notes.md", "new content")
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
# 2026-07-31: bring back visible "thinking" (Edvard's old Slack-bridge setup
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


def test_repeated_speak_failures_auto_pause_after_cap(runner):
    runner._conversation_failures.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=RuntimeError("simulated rate limit")):
        for _ in range(runner.FAILURE_PAUSE_CAP):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)

    pause_calls = [
        c for c in calls["agora_internal"]
        if c[0] == "PATCH" and c[1] == f"/conversations/{summary['id']}" and c[2] == {"status": "paused"}
    ]
    assert len(pause_calls) == 1, "conversation should be auto-paused exactly once at the failure cap"
    assert summary["id"] not in runner._conversation_failures, "failure count should reset after pausing"


def test_failures_below_cap_do_not_pause(runner):
    runner._conversation_failures.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=RuntimeError("simulated rate limit")):
        for _ in range(runner.FAILURE_PAUSE_CAP - 1):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)

    pause_calls = [c for c in calls["agora_internal"] if c[0] == "PATCH"]
    assert len(pause_calls) == 0
    assert runner._conversation_failures[summary["id"]] == runner.FAILURE_PAUSE_CAP - 1


def test_failure_count_resets_on_success(runner):
    runner._conversation_failures.clear()
    summary, detail, calls, fake_agora_get, fake_agora_internal, fake_decide_turn = _make_poll_fixtures(runner)

    call_count = {"n": 0}

    def flaky_speak(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] <= runner.FAILURE_PAUSE_CAP - 1:
            raise RuntimeError("transient")
        return "recovered reply"

    with patch.object(runner.conversations, "agora_get", side_effect=fake_agora_get), \
         patch.object(runner.conversations, "agora_internal", side_effect=fake_agora_internal), \
         patch.object(runner.conversations, "decide_turn", side_effect=fake_decide_turn), \
         patch.object(runner.conversations, "speak", side_effect=flaky_speak):
        for _ in range(runner.FAILURE_PAUSE_CAP - 1):
            with pytest.raises(RuntimeError):
                runner.poll_conversation(summary)
        runner.poll_conversation(summary)  # succeeds now

    assert summary["id"] not in runner._conversation_failures
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

    def fake_generate_reply(persona, caps, system, history, conversation_id, model_override=None, sticky=False, on_text=None, on_thinking=None):
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


def test_auto_pause_notifies_with_system_true(runner):
    captured = {}

    def fake_notify(conversation_id, text, sender, system=False):
        captured["system"] = system
        return 200

    with patch.object(runner.conversations, "agora_internal", return_value=(200, {})), \
         patch.object(runner.conversations, "notify", side_effect=fake_notify):
        runner.auto_pause("conv-1", "Test")

    assert captured["system"] is True


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
# to visible/consecutive_ai_turns, turn-counted by run not by message); and
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
    send thought-summary parts back. This is the entire reason Edvard
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


def test_consecutive_ai_turns_counts_runs_not_messages(runner):
    """A single logical turn now streams as several messages -- the
    AI_TURN_CAP must still count actual persona handoffs, not chunks."""
    thread = [
        {"sender": "Edvard", "text": "go"},
        {"sender": "Gemini", "text": "chunk one"},
        {"sender": "Gemini", "text": "chunk two"},
        {"sender": "Gemini", "text": "chunk three"},
    ]
    assert runner.consecutive_ai_turns(thread) == 1

    thread.append({"sender": "Haiku", "text": "handoff reply"})
    assert runner.consecutive_ai_turns(thread) == 2


def test_decide_turn_ignores_activity_messages_for_last_sender(runner):
    """An activity chip trailing a persona's real text must not look like
    'the persona already replied to a fresh @mention' -- and must not
    itself satisfy an @mention (it's not a real speaker turn)."""
    personas = [{"name": "Gemini", "role": "curator"}, {"name": "Haiku", "role": "listener"}]
    thread = [
        {"sender": "Edvard", "text": "@Haiku are you there?"},
        {"sender": "Gemini", "text": "vault_read: notes.md", "activity": {"capability": "vault_read", "detail": "notes.md"}},
    ]
    assert runner.decide_turn(thread, personas) == ["Haiku"]


def test_decide_turn_auto_pauses_at_cap_counting_handoffs_not_activity_chips(runner):
    """Each handoff below carries an activity chip right after its text
    (matching how a real streamed turn looks) -- the cap must trip based on
    the AI_TURN_CAP-th real handoff, not be thrown off by the chips."""
    personas = [{"name": f"P{i}", "role": "listener"} for i in range(runner.AI_TURN_CAP + 1)]
    thread = [{"sender": "Edvard", "text": "@P0 go"}]
    for i in range(runner.AI_TURN_CAP):
        thread.append({"sender": f"P{i}", "text": f"@P{i + 1} your turn"})
        thread.append({"sender": f"P{i}", "text": f"vault_read: x",
                        "activity": {"capability": "vault_read", "detail": "x"}})
    assert runner.decide_turn(thread, personas) == [runner.PAUSE_SENTINEL]


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
    """A thinking chunk trailing a persona's real text must not look like
    'the persona already replied to a fresh @mention', same reasoning as
    the activity-chip exclusion right above."""
    personas = [{"name": "Gemini", "role": "curator"}, {"name": "Haiku", "role": "listener"}]
    thread = [
        {"sender": "Edvard", "text": "@Haiku are you there?"},
        {"sender": "Gemini", "text": "let me think about that", "thinking": True},
    ]
    assert runner.decide_turn(thread, personas) == ["Haiku"]


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
                             sticky=False, on_text=None, on_thinking=None):
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
    mock_audit.assert_called_once()
    assert "replied" in heartbeat_updates[-1]["lastResult"]


# ---------------------------------------------------------------------------
# 2026-07-25: K3s Sentinel was created via New Conversation without
# kubectlRead (fixed separately, agora#19), and even once it had the tool,
# a monitoring heartbeat reporting "all clear" every single run would be
# noise -- Edvard's explicit ask: "only send a message back to the chat if
# it finds something worth reporting. A clean working cluster should not
# trigger a message." HEARTBEAT_NO_REPORT_SENTINEL is the mechanism: a
# heartbeat's own prompt can ask for this exact string when there's
# nothing to report, and run_heartbeat suppresses notify()/audit() for it.
# ---------------------------------------------------------------------------

def test_run_heartbeat_skips_notify_when_sentinel_returned(runner):
    heartbeat = {"id": "hb1", "personaId": "p1", "conversationId": "conv-1",
                 "schedule": "every@1h", "name": "HB"}
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

    with patch.object(runner.heartbeats, "_workflow_threads", {}), \
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

    with patch.object(runner.heartbeats, "_workflow_threads", {"hb1": _AliveThread()}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.threading, "Thread") as mock_thread_ctor:
        runner.run_due_heartbeats()

    mock_thread_ctor.assert_not_called()


def test_run_due_heartbeats_non_workflow_heartbeat_still_runs_synchronously(runner):
    heartbeat = {
        "id": "hb2", "name": "Plain HB", "enabled": True, "forceRun": True,
        "schedule": "every@1h", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }
    with patch.object(runner.heartbeats, "_workflow_threads", {}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.heartbeats, "run_heartbeat") as mock_run_hb, \
         patch.object(runner.threading, "Thread") as mock_thread_ctor:
        runner.run_due_heartbeats()

    mock_run_hb.assert_called_once_with(heartbeat)
    mock_thread_ctor.assert_not_called()


def test_run_due_heartbeats_force_run_bypasses_disabled(runner):
    heartbeat = {
        "id": "hb3", "name": "Disabled HB", "enabled": False, "forceRun": True,
        "schedule": "every@1h", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }
    with patch.object(runner.heartbeats, "_workflow_threads", {}), \
         patch.object(runner.heartbeats, "agora_internal", return_value=(200, {"heartbeats": [heartbeat]})), \
         patch.object(runner.heartbeats, "run_heartbeat") as mock_run_hb, \
         patch.object(runner.threading, "Thread") as mock_thread_ctor:
        runner.run_due_heartbeats()

    mock_run_hb.assert_called_once_with(heartbeat)
    mock_thread_ctor.assert_not_called()


def test_run_due_heartbeats_disabled_without_force_run_is_skipped(runner):
    heartbeat = {
        "id": "hb4", "name": "Disabled HB", "enabled": False, "forceRun": False,
        "schedule": "every@1h", "createdAt": "2026-01-01T00:00:00+00:00",
        "lastRunAt": None, "conversationId": "c1", "personaId": "p1",
    }
    with patch.object(runner.heartbeats, "_workflow_threads", {}), \
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
         patch.object(runner.poll, "run_due_heartbeats") as mock_run_due:
        runner.poll_once()

    assert polled == ["c2"]  # c1 skipped (workflow-bound), c2 gets ordinary turn-taking
    mock_run_due.assert_called_once_with(heartbeats_body["heartbeats"])


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
    mock_write.assert_called_once_with("notes.md", "hello")
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
    reason it exists (Edvard: round-robin was meant for multi-agent
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
    of the run -- otherwise a message Edvard posts while a run is
    executing never reaches a later round of that same run."""
    participants = [{"personaId": "p1", "name": "A", "role": "curator"}]
    persona = {"id": "p1", "name": "A", "model": "anthropic:claude-haiku-4-5-20251001",
               "capabilities": dict(runner.DEFAULT_CAPS)}
    steps = [{"prompt": "", "loopCount": 3, "toolWhitelist": []}]
    fetch_count = {"n": 0}

    def fake_agora_get(path):
        fetch_count["n"] += 1
        if fetch_count["n"] == 2:
            # Simulate Edvard posting mid-run, between round 1 and round 2.
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


def _fake_vault_couch_req(method, path, body=None):
    if method == "GET" and path.endswith("_all_docs"):
        return 200, {"rows": [{"id": k} for k in _VAULT_TOOLS_FILEDOCS]}
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
        if method == "GET" and path.endswith("_all_docs"):
            return 200, {"rows": [{"id": k} for k in filedocs]}
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


def test_github_write_and_merge_tools_only_advertised_with_capability(runner):
    caps_on = dict(runner.NO_CAPS, githubWrite=True, githubMerge=True)
    caps_off = dict(runner.NO_CAPS, githubWrite=False, githubMerge=False)
    names_on = {t["name"] for t in runner.client_tool_schemas(caps_on)}
    names_off = {t["name"] for t in runner.client_tool_schemas(caps_off)}
    assert "create_pr" in names_on and "merge_pr" in names_on
    assert "create_pr" not in names_off and "merge_pr" not in names_off


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


def test_tool_to_capability_covers_github_write_and_merge(runner):
    assert runner.TOOL_TO_CAPABILITY["create_pr"] == "githubWrite"
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
