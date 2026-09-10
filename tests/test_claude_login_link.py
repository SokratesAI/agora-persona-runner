"""The login-link flow, judged without ever reaching Anthropic.

Every test here either reads the installed binary or uses a fake transport.
Nothing runs a live exchange -- see the module docstring for why that is a
decision rather than an omission.
"""

import base64
import io
import hashlib
import json

import pytest

from tools import claude_login_link as login


# The real config block out of claude.exe 2.1.261, verbatim enough to parse.
BUNDLE = (
    'var _={BASE_API_URL:"https://api.anthropic.com",'
    'CONSOLE_AUTHORIZE_URL:"https://platform.claude.com/oauth/authorize",'
    'CLAUDE_AI_AUTHORIZE_URL:"https://claude.com/cai/oauth/authorize",'
    'TOKEN_URL:"https://platform.claude.com/v1/oauth/token",'
    'MANUAL_REDIRECT_URL:"https://platform.claude.com/oauth/code/callback",'
    'CLIENT_ID:"9d1c250a-e61b-44d9-88ed-5944d1962f5e"};'
)

# The axios version literal and the header line that names it, both verbatim
# out of the same binary. They live far apart in the real bundle; the reader
# must find each on its own, so they are two separate strings here too.
AXIOS = 'var Ce="1.15.2";' + 'N.set("User-Agent","axios/"+Ce,!1);'
BUNDLE_WITH_UA = BUNDLE + AXIOS


def test_extract_reads_the_four_constants_the_manual_flow_needs():
    config = login.extract_oauth_config(BUNDLE)
    assert config["authorize_url"] == "https://claude.com/cai/oauth/authorize"
    assert config["token_url"] == "https://platform.claude.com/v1/oauth/token"
    assert config["manual_redirect_url"] == "https://platform.claude.com/oauth/code/callback"
    assert config["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"


def test_extract_refuses_rather_than_falling_back_when_a_release_moves_a_key():
    """The whole point of reading the binary. A remembered client id would send
    the owner to an authorize page for the wrong application and the failure would
    look like he mistyped something."""
    without_client = BUNDLE.replace("CLIENT_ID:", "RENAMED_BY_A_RELEASE:")
    with pytest.raises(login.CannotSee) as raised:
        login.extract_oauth_config(without_client)
    assert "CLIENT_ID" in str(raised.value)


def test_the_authorize_url_the_console_flow_uses_is_not_the_one_we_build():
    """CONSOLE_AUTHORIZE_URL sits in the same object and is the API-console
    login, not the subscription one. Matching the wrong key would produce a URL
    that works and logs in against the metered account -- identity.md rule 9."""
    config = login.extract_oauth_config(BUNDLE)
    assert "platform.claude.com/oauth/authorize" not in config["authorize_url"]


def test_pkce_challenge_is_rfc7636_s256_unpadded():
    verifier, challenge = login.pkce_pair("a-known-verifier")
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(b"a-known-verifier").digest()
    ).decode().rstrip("=")
    assert verifier == "a-known-verifier"
    assert challenge == expected
    assert "=" not in challenge


def test_minted_verifiers_differ():
    assert login.pkce_pair()[0] != login.pkce_pair()[0]


def test_authorize_url_carries_the_manual_redirect_not_a_localhost_callback():
    """This is the one line that makes a phone login possible: a localhost
    redirect needs the browser on this pod, and there is no browser here."""
    config = login.extract_oauth_config(BUNDLE)
    url = login.authorize_url(config, "CHALLENGE", "STATE", ["user:profile"])
    assert "redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback" in url
    assert "localhost" not in url
    assert "code_challenge_method=S256" in url
    assert "response_type=code" in url
    assert "state=STATE" in url


def test_scopes_are_space_joined_in_the_query():
    config = login.extract_oauth_config(BUNDLE)
    url = login.authorize_url(config, "C", "S", ["user:profile", "user:inference"])
    assert "scope=user%3Aprofile+user%3Ainference" in url


def test_scopes_come_from_the_running_credential(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"claudeAiOauth": {"scopes": ["user:profile", "user:design:write"]}}))
    scopes, source = login.live_scopes(str(path))
    assert scopes == ["user:profile", "user:design:write"]
    assert str(path) in source


def test_missing_credential_falls_back_and_says_so(tmp_path):
    scopes, source = login.live_scopes(str(tmp_path / "absent.json"))
    assert scopes == login.FALLBACK_SCOPES
    assert "fallback" in source


def test_pasted_code_splits_on_the_hash_the_callback_page_shows():
    assert login.split_pasted_code("  abc123#st-ate \n") == ("abc123", "st-ate")
    assert login.split_pasted_code("abc123") == ("abc123", None)


def test_exchange_sends_the_fields_the_cli_sends():
    seen = {}

    def fake_post(url, body, user_agent=None):
        seen["url"] = url
        seen["body"] = body
        return 200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60}

    session = {
        "code_verifier": "verifier",
        "state": "state",
        "client_id": "cid",
        "token_url": "https://token.example/v1/oauth/token",
        "redirect_uri": "https://redirect.example/callback",
    }
    login.exchange(session, "the-code", post=fake_post)
    assert seen["url"] == "https://token.example/v1/oauth/token"
    assert seen["body"] == {
        "grant_type": "authorization_code",
        "code": "the-code",
        "redirect_uri": "https://redirect.example/callback",
        "client_id": "cid",
        "code_verifier": "verifier",
        "state": "state",
    }


def test_exchange_raises_on_a_non_200():
    session = {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r",
    }
    with pytest.raises(login.CannotSee):
        login.exchange(session, "code", post=lambda url, body, user_agent=None: (401, {}))


def test_credential_expiries_are_epoch_ms_from_now():
    payload = {
        "access_token": "at",
        "refresh_token": "rt",
        "expires_in": 3600,
        "refresh_token_expires_in": 2592000,
        "scope": "user:profile user:inference",
    }
    credential = login.credential_from_response(payload, now_ms=1_000_000)
    assert credential["expiresAt"] == 1_000_000 + 3600 * 1000
    assert credential["refreshTokenExpiresAt"] == 1_000_000 + 2592000 * 1000
    assert credential["scopes"] == ["user:profile", "user:inference"]


def test_a_response_without_a_refresh_expiry_carries_no_field():
    """The 2026-08-17 snapshot's shape. Inventing a date here would hand
    tools.credential_recovery a deadline nobody was told."""
    credential = login.credential_from_response(
        {"access_token": "a", "refresh_token": "r", "expires_in": 60}, now_ms=0
    )
    assert "refreshTokenExpiresAt" not in credential


def test_describe_never_prints_a_token():
    credential = {
        "accessToken": "sk-ant-oat-SECRET",
        "refreshToken": "sk-ant-ort-ALSOSECRET",
        "expiresAt": 0,
        "scopes": ["user:profile"],
    }
    text = "\n".join(login.describe(credential))
    assert "SECRET" not in text
    assert "17 chars" in text


def test_finish_refuses_a_code_whose_state_is_not_ours(tmp_path, capsys):
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "v", "state": "mine", "client_id": "c",
        "token_url": "u", "redirect_uri": "r",
    })
    status = login.main(["--session", str(session), "finish", "--code", "code#theirs"])
    assert status == 2
    assert "state" in capsys.readouterr().out


def test_the_session_file_is_owner_only(tmp_path):
    """The verifier is half a credential until it is spent."""
    path = tmp_path / "s.json"
    login.save_session(str(path), {"code_verifier": "v"})
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_finish_writes_nothing_without_install(tmp_path, capsys, monkeypatch):
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r",
    })
    monkeypatch.setattr(
        login, "_post_json",
        lambda url, body, timeout=30, user_agent=None: (200, {
            "access_token": "at", "refresh_token": "rt", "expires_in": 60,
        }),
    )
    target = tmp_path / "creds.json"
    status = login.main(["--session", str(session), "finish", "--code", "code#s"])
    assert status == 0
    assert not target.exists()
    assert "nothing written" in capsys.readouterr().out

    status = login.main([
        "--session", str(session), "finish", "--code", "code#s", "--install", str(target),
    ])
    assert status == 0
    assert json.loads(target.read_text())["claudeAiOauth"]["accessToken"] == "at"


def test_the_installed_binary_still_carries_the_constants():
    """The one live read here, and it is a read. If a CLI release moves these,
    this fails before a cycle hands the owner a broken link."""
    import os

    if not os.path.exists(login.DEFAULT_BINARY):
        pytest.skip("no Claude CLI on this box")
    config = login.extract_oauth_config(login.read_binary_text())
    assert config["manual_redirect_url"].endswith("/oauth/code/callback")
    assert len(config["client_id"]) == 36


# --- the reviewer's four findings, each with a test that fails without the fix


LOCAL_BLOCK = (
    'function u(){return{BASE_API_URL:t,'
    'CLAUDE_AI_AUTHORIZE_URL:"http://localhost:4000/oauth/authorize",'
    'TOKEN_URL:"http://localhost:8000/v1/oauth/token",'
    'MANUAL_REDIRECT_URL:"http://localhost:3000/oauth/code/callback",'
    'CLIENT_ID:"22422756-60c9-4084-8eb7-27705fd5cf9a"}}'
)


def test_extraction_reads_the_production_object_even_when_it_is_second():
    """The bundle carries the same key names twice. First-match works today
    only because production happens to sit first in the file; if a release
    reorders them, an unanchored search mints a link for the local dev app."""
    reordered = LOCAL_BLOCK + BUNDLE
    config = login.extract_oauth_config(reordered)
    assert config["client_id"] == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    assert "localhost" not in config["token_url"]


def test_extraction_refuses_a_bundle_with_no_production_object():
    with pytest.raises(login.CannotSee):
        login.extract_oauth_config(LOCAL_BLOCK)


def test_carried_fields_come_across_from_the_credential_on_disk():
    """The token endpoint returns none of these. Dropping them is the exact
    bug agora-claude-bridge/bridge/credentials.py records as 'Not logged in'."""
    carry = {"subscriptionType": "max", "rateLimitTier": "default_claude_max_5x", "clientId": "cid"}
    credential = login.credential_from_response(
        {"access_token": "a", "refresh_token": "r", "expires_in": 60}, now_ms=0, carry_over=carry
    )
    for name in login.CARRIED_FIELDS:
        assert credential[name] == carry[name]


def test_carried_from_reads_only_the_three_fields(tmp_path):
    path = tmp_path / "creds.json"
    path.write_text(json.dumps({"claudeAiOauth": {
        "accessToken": "SECRET", "subscriptionType": "max", "rateLimitTier": "t",
    }}))
    assert login.carried_from(str(path)) == {"subscriptionType": "max", "rateLimitTier": "t"}


def test_carried_from_is_empty_when_there_is_nothing_to_carry(tmp_path):
    assert login.carried_from(str(tmp_path / "absent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("not json")
    assert login.carried_from(str(bad)) == {}


def test_finish_warns_when_a_field_the_cli_writes_cannot_be_supplied(tmp_path, capsys, monkeypatch):
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "created_at": 0,
    })
    monkeypatch.setattr(login, "DEFAULT_CREDENTIALS", str(tmp_path / "absent.json"))
    monkeypatch.setattr(
        login, "_post_json",
        lambda url, body, timeout=30, user_agent=None: (200, {
            "access_token": "at", "refresh_token": "rt", "expires_in": 60,
        }),
    )
    login.main(["--session", str(session), "finish", "--code", "code#s"])
    out = capsys.readouterr().out
    assert "WARNING" in out
    for name in login.CARRIED_FIELDS:
        assert name in out


def test_start_refuses_to_clobber_an_unspent_link(tmp_path, capsys, monkeypatch):
    """A second start silently invalidates a link already on his phone, and the
    failure lands an hour later as a state mismatch blaming the wrong thing."""
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    creds = str(tmp_path / "absent.json")
    assert login.main(["--session", str(session), "start", "--credentials", creds]) == 0
    first = json.loads(session.read_text())["state"]

    assert login.main(["--session", str(session), "start", "--credentials", creds]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert json.loads(session.read_text())["state"] == first

    assert login.main([
        "--session", str(session), "start", "--credentials", creds, "--force",
    ]) == 0
    assert json.loads(session.read_text())["state"] != first


def test_an_expired_session_is_not_treated_as_live(tmp_path):
    path = tmp_path / "s.json"
    login.save_session(str(path), {"created_at": 1000.0})
    assert login.live_session(str(path), ttl=60, now=1030.0) is not None
    assert login.live_session(str(path), ttl=60, now=2000.0) is None


def test_finish_says_refused_rather_than_raising_on_a_malformed_body(tmp_path, capsys, monkeypatch):
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "created_at": 0,
    })

    def blows_up(url, body, timeout=30, user_agent=None):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(login, "_post_json", blows_up)
    assert login.main(["--session", str(session), "finish", "--code", "code#s"]) == 2
    assert "REFUSED" in capsys.readouterr().out


def test_the_token_exchange_user_agent_is_read_out_of_the_binary():
    """Not a table of constants -- the same rule the four OAuth URLs follow."""
    assert login.read_token_user_agent(BUNDLE_WITH_UA) == "axios/1.15.2"


def test_the_user_agent_reader_refuses_rather_than_guessing():
    """Both lookups have to land on exactly one thing. A minified name that has
    picked up a second meaning is not a name any more, and the wrong User-Agent
    is precisely what gets this request blocked."""
    with pytest.raises(login.CannotSee):
        login.read_token_user_agent(BUNDLE)  # no axios anchor at all
    with pytest.raises(login.CannotSee):
        login.read_token_user_agent(BUNDLE_WITH_UA.replace('"axios/"+Ce', '"axios/"+Renamed'))
    with pytest.raises(login.CannotSee):
        login.read_token_user_agent(BUNDLE_WITH_UA + 'var Ce="9.9.9";')
    # axios bundled twice under two minified names: two anchors, and picking
    # the first would be a fact about layout rather than about which one the
    # token exchange uses.
    with pytest.raises(login.CannotSee):
        login.read_token_user_agent(
            BUNDLE_WITH_UA + 'var Qx="0.1.2";' + 'h.set("User-Agent","axios/"+Qx,!1);'
        )


def test_post_json_sends_the_user_agent_it_is_given(monkeypatch):
    """urllib's default is `Python-urllib/3.x`, and the live token endpoint
    answers that with `403 error code: 1010` -- Cloudflare, before Anthropic
    sees the request at all. Measured from this pod 2026-09-09."""
    seen = {}

    class FakeResponse:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout=None):
        seen["headers"] = dict(request.header_items())
        return FakeResponse()

    monkeypatch.setattr(login.urllib.request, "urlopen", fake_urlopen)

    login._post_json("https://token.example", {}, user_agent="axios/1.15.2")
    assert seen["headers"]["User-agent"] == "axios/1.15.2"

    seen.clear()
    login._post_json("https://token.example", {})
    assert "User-agent" not in seen["headers"]


def test_exchange_sends_the_session_user_agent():
    """It travels on the session so `finish` sends what `start` was built
    against, rather than re-reading a binary that may have rolled between."""
    seen = {}

    def fake_post(url, body, user_agent=None):
        seen["user_agent"] = user_agent
        return 200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60}

    session = {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "user_agent": "axios/1.15.2",
    }
    login.exchange(session, "the-code", post=fake_post)
    assert seen["user_agent"] == "axios/1.15.2"


def test_start_writes_the_user_agent_into_the_session(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    assert login.main([
        "--session", str(session), "start",
        "--credentials", str(tmp_path / "absent.json"),
    ]) == 0
    assert json.loads(session.read_text())["user_agent"] == "axios/1.15.2"
    assert "axios/1.15.2" in capsys.readouterr().out


def test_finish_fills_in_a_user_agent_a_older_session_never_had(tmp_path, monkeypatch, capsys):
    """The sessions on disk today were minted before this field existed. Sending
    them out with urllib's default is the 1010 again, so finish reads it rather
    than letting the request go."""
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "created_at": 0,
    })
    seen = {}

    def fake_post(url, body, timeout=30, user_agent=None):
        seen["user_agent"] = user_agent
        return 200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60}

    monkeypatch.setattr(login, "_post_json", fake_post)
    assert login.main(["--session", str(session), "finish", "--code", "code#s"]) == 0
    assert seen["user_agent"] == "axios/1.15.2"
    assert "predates" in capsys.readouterr().out


def test_finish_warns_rather_than_refusing_when_the_binary_is_gone(tmp_path, monkeypatch, capsys):
    """An unreadable binary must not cost a working exchange. CI has no
    /usr/bin/claude and neither would a recovery box holding only the session --
    which is exactly the disaster this flow exists for."""
    def no_binary(path=None):
        raise OSError(2, "No such file or directory", "/usr/bin/claude")

    monkeypatch.setattr(login, "read_binary_text", no_binary)
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "v", "state": "s", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "created_at": 0,
    })
    seen = {}

    def fake_post(url, body, timeout=30, user_agent=None):
        seen["user_agent"] = user_agent
        return 200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60}

    monkeypatch.setattr(login, "_post_json", fake_post)
    assert login.main(["--session", str(session), "finish", "--code", "code#s"]) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "1010" in out
    assert seen["user_agent"] is None


def test_the_dedupe_key_carries_the_link_so_a_new_one_is_never_held(tmp_path, capsys, monkeypatch):
    """Two links minted the same day are two messages, not one repeated one.

    The failure this pins is not hypothetical: on 2026-09-10 the owner's code
    came back `invalid_grant`, `--force` invalidated the link he was holding,
    and the replacement was held by a 24-hour dedupe on a constant key. He was
    left with no link at all and `start` exited 0.
    """
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    creds = str(tmp_path / "absent.json")
    seen = []

    def fake_notify(text, key, dedupe_hours=None, **kw):
        seen.append({"key": key, "dedupe_hours": dedupe_hours, "text": text})
        return 0, "sent"

    from tools import notify as notify_tool
    monkeypatch.setattr(notify_tool, "notify", fake_notify)

    assert login.main([
        "--session", str(session), "start", "--credentials", creds, "--notify",
    ]) == 0
    first_state = json.loads(session.read_text())["state"]

    assert login.main([
        "--session", str(session), "start", "--credentials", creds,
        "--notify", "--force",
    ]) == 0
    second_state = json.loads(session.read_text())["state"]

    assert first_state != second_state
    assert len(seen) == 2
    assert seen[0]["key"] != seen[1]["key"], "two links must not share a dedupe key"
    assert seen[0]["key"].endswith(":" + first_state)
    assert seen[1]["key"].endswith(":" + second_state)
    # The window matches the life of the thing it is announcing, in hours.
    assert seen[0]["dedupe_hours"] == login.SESSION_TTL_SECONDS / 3600
    assert seen[1]["dedupe_hours"] == login.SESSION_TTL_SECONDS / 3600


def test_the_key_is_a_function_of_the_link_and_nothing_else():
    """Two links never share a key, one link always maps to the same one.

    Named for what it checks. It was named for a re-announcement case that
    `_cmd_start` cannot reach -- reviewer finding, see `notify_key_for`.
    """
    assert login.notify_key_for("k", "abc") == login.notify_key_for("k", "abc")
    assert login.notify_key_for("k", "abc") != login.notify_key_for("k", "abd")
    assert login.notify_key_for("k", "abc").startswith("k")


def test_a_held_link_is_not_reported_as_delivered(tmp_path, capsys, monkeypatch):
    """`notify` answers 3 when it holds a message. That is a link he does not
    have, so `start` must not exit 0 on it -- the old code mapped 3 to 0."""
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    creds = str(tmp_path / "absent.json")

    from tools import notify as notify_tool
    monkeypatch.setattr(
        notify_tool, "notify",
        lambda text, key, dedupe_hours=None, **kw: (3, "held: quiet hours"))

    status = login.main([
        "--session", str(session), "start", "--credentials", creds, "--notify",
    ])
    out = capsys.readouterr().out
    assert status == 3, "a held link must not read as a delivered one"
    assert "REFUSED" in out
    assert "held: quiet hours" in out
    # The session is still minted, so `finish` works if he gets the URL another way.
    assert json.loads(session.read_text())["state"]


def test_a_refusal_body_reaches_the_caller_instead_of_the_exception(monkeypatch):
    """`urlopen` raises on 4xx, so `exchange`'s `status != 200` branch could
    never run and the only line that said *why* went out with the exception.
    Measured live 2026-09-10: the 400 that three cycles read as
    `HTTP Error 400: Bad Request` carried
    `{"error": "invalid_grant", "error_description": "Invalid 'code' in request."}`."""
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://token.example", 400, "Bad Request", {},
            io.BytesIO(b'{"error": "invalid_grant", '
                       b'"error_description": "Invalid \'code\' in request."}'),
        )

    monkeypatch.setattr(login.urllib.request, "urlopen", fake_urlopen)
    status, payload = login._post_json("https://token.example", {})
    assert status == 400
    assert payload["error"] == "invalid_grant"
    assert payload["error_description"] == "Invalid 'code' in request."


def test_an_unparsable_refusal_body_still_travels_as_evidence(monkeypatch):
    """Cloudflare's 1010 block is HTML. A body we cannot parse is still the
    finding, so it must not surface as a `ValueError` from the parse line."""
    import urllib.error

    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            "https://token.example", 403, "Forbidden", {},
            io.BytesIO(b"<html>error code: 1010</html>"),
        )

    monkeypatch.setattr(login.urllib.request, "urlopen", fake_urlopen)
    status, payload = login._post_json("https://token.example", {})
    assert status == 403
    assert "1010" in payload["error_description"]


def test_exchange_names_the_reason_not_just_the_status():
    """`token exchange failed (400)` is the same sentence for a spent code, an
    expired one and a rate limit, and they have different next steps."""
    def fake_post(url, body, user_agent=None):
        return 400, {"error": "invalid_grant", "error_description": "Invalid 'code' in request."}

    session = {"code_verifier": "v", "state": "s", "client_id": "c",
               "token_url": "u", "redirect_uri": "r"}
    with pytest.raises(login.CannotSee) as refusal:
        login.exchange(session, "spent", post=fake_post)
    text = str(refusal.value)
    assert "400" in text
    assert "invalid_grant" in text
    assert "Invalid 'code' in request." in text


def test_the_code_is_matched_on_this_sessions_state_not_on_arrival_order():
    """A stale reply to an invalidated link arrives newest-first too. Picking
    by arrival would hand `finish` a code whose state does not match, and the
    real one -- one message further down -- would never be tried."""
    rows = [
        {"id": 1, "text": "old-code#previous-state"},
        {"id": 2, "text": "please send me a new link"},
        {"id": 3, "text": "right-code#live-state"},
        {"id": 4, "text": "thanks"},
    ]
    assert login.code_from_messages(rows, "live-state") == ("right-code", 3, "live-state")
    assert login.code_from_messages(rows, "previous-state") == ("old-code", 1, "previous-state")
    assert login.code_from_messages(rows, "never-minted") == (None, None, None)
    assert login.code_from_messages(rows, None) == (None, None, None)
    # Two links live at once: the newest reply wins, and it names which of the
    # two states it answered so the caller spends the matching verifier.
    assert login.code_from_messages(
        rows, ["previous-state", "live-state"]) == ("right-code", 3, "live-state")


def test_a_bare_code_with_no_state_is_not_claimed_by_any_session():
    """`split_pasted_code` returns None for the state half when he pastes only
    the code, and None must not match a session whose state is missing."""
    rows = [{"id": 9, "text": "just-the-code"}]
    assert login.code_from_messages(rows, "live-state") == (None, None, None)
    assert login.code_from_messages("not a list", "live-state") == (None, None, None)


def test_await_code_returns_the_code_as_soon_as_it_arrives():
    reads = []
    ticks = {"t": 0.0}

    def fetch_rows():
        reads.append(ticks["t"])
        if len(reads) < 3:
            return []
        return [{"id": 7, "text": "the-code#live-state"}]

    def sleep(seconds):
        ticks["t"] += seconds

    assert login.await_code(
        "live-state", timeout=600, poll=15,
        fetch_rows=fetch_rows, sleep=sleep, now=lambda: ticks["t"],
    ) == ("the-code", 7, "live-state")
    assert len(reads) == 3
    assert ticks["t"] == 30.0


def test_await_code_gives_up_at_the_deadline_and_never_sleeps_past_it():
    ticks = {"t": 0.0}

    def sleep(seconds):
        ticks["t"] += seconds

    assert login.await_code(
        "live-state", timeout=40, poll=15,
        fetch_rows=lambda: [], sleep=sleep, now=lambda: ticks["t"],
    ) == (None, None, None)
    # 15 + 15 + 10: the last sleep is clamped to what is left, so a caller
    # that asked for 40 seconds is never held for 45.
    assert ticks["t"] == 40.0


def test_an_unreadable_inbox_is_retried_rather_than_ending_the_wait(capsys):
    ticks = {"t": 0.0}
    attempts = {"n": 0}

    def fetch_rows():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("bridge down")
        return [{"id": 4, "text": "the-code#live-state"}]

    def sleep(seconds):
        ticks["t"] += seconds

    assert login.await_code(
        "live-state", timeout=600, poll=15,
        fetch_rows=fetch_rows, sleep=sleep, now=lambda: ticks["t"],
    ) == ("the-code", 4, "live-state")
    assert "bridge down" in capsys.readouterr().out


def test_wait_acks_only_a_code_it_actually_spent(tmp_path, monkeypatch, capsys):
    """An ack on a failed exchange hides the one message a later cycle needs
    to retry with."""
    from tools import telegram_inbox

    session = tmp_path / "session.json"
    session.write_text(json.dumps({
        "code_verifier": "v", "state": "live-state", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "user_agent": "axios/1.15.2",
        "created_at": 0,
    }))
    acked = []
    monkeypatch.setattr(telegram_inbox, "ack", lambda through, *a, **k: acked.append(through) or (0, "read through #%s" % through))
    monkeypatch.setattr(login, "await_code",
                        lambda states, timeout, poll=15.0: ("the-code", 11, "live-state"))

    monkeypatch.setattr(login, "_post_json", lambda *a, **k: (400, {"error": "invalid_grant"}))
    assert login.main(["--session", str(session), "wait", "--timeout", "1"]) == 2
    assert acked == []

    monkeypatch.setattr(login, "_post_json", lambda *a, **k: (200, {
        "access_token": "at", "refresh_token": "rt", "expires_in": 60,
    }))
    assert login.main(["--session", str(session), "wait", "--timeout", "1"]) == 0
    assert acked == [11]


def test_wait_refuses_a_session_with_no_state_rather_than_matching_none(tmp_path, capsys):
    session = tmp_path / "session.json"
    session.write_text(json.dumps({"code_verifier": "v", "created_at": 0}))
    assert login.main(["--session", str(session), "wait", "--timeout", "1"]) == 1
    assert "no state" in capsys.readouterr().out


def test_a_forced_mint_keeps_the_link_it_replaced_spendable(tmp_path, monkeypatch, capsys):
    """The bug this cost, in one test. On 2026-09-10 at 13:41 a `start --force`
    replaced the 10:17 session fifty seconds before he pasted the code for the
    10:17 link. The code was seconds old and good; the verifier that could
    have spent it had just been overwritten, so the reply was unusable and he
    had to be asked to tap a second link."""
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    creds = str(tmp_path / "absent.json")
    assert login.main(["--session", str(session), "start", "--credentials", creds]) == 0
    first = json.loads(session.read_text())
    assert login.main([
        "--session", str(session), "start", "--credentials", creds, "--force",
    ]) == 0
    second = json.loads(session.read_text())
    assert second["state"] != first["state"]

    # Both verifiers are still on disk, and each state finds its own -- not
    # merely "a session", which would exchange the wrong verifier and fail.
    assert login.session_for_state(second, first["state"])["code_verifier"] \
        == first["code_verifier"]
    assert login.session_for_state(second, second["state"])["code_verifier"] \
        == second["code_verifier"]

    # And `finish` spends the replaced one, which is the whole point: before
    # this it answered REFUSED on the state.
    sent = {}

    def post(url, body, timeout=30, user_agent=None):
        sent.update(body)
        return 200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60}

    monkeypatch.setattr(login, "_post_json", post)
    assert login.main([
        "--session", str(session), "finish", "--code", "the-code#" + first["state"],
    ]) == 0
    assert sent["code_verifier"] == first["code_verifier"]


def test_a_state_no_session_ever_minted_is_still_refused(tmp_path, monkeypatch, capsys):
    """Widening `finish` to every session on disk must not widen it to any
    state at all -- a code carrying a state nothing here minted has no
    verifier behind it and cannot be exchanged."""
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE_WITH_UA)
    session = tmp_path / "session.json"
    creds = str(tmp_path / "absent.json")
    assert login.main(["--session", str(session), "start", "--credentials", creds]) == 0
    monkeypatch.setattr(login, "_post_json",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("no exchange should be attempted")))
    assert login.main([
        "--session", str(session), "finish", "--code", "the-code#never-minted",
    ]) == 2
    assert "matches no unspent session" in capsys.readouterr().out


def test_supersede_drops_what_the_ttl_can_never_retire():
    """`unspent_sessions` keeps an undated session so a code for it can still
    be spent; `supersede` drops it, because a session with no clock never
    expires and carrying it forward would grow the file without bound."""
    document = {
        "state": "newest", "created_at": 1000.0,
        "superseded": [
            {"state": "recent", "created_at": 990.0},
            {"state": "ancient", "created_at": 10.0},
            {"state": "undated"},
        ],
    }
    live = [s["state"] for s in login.unspent_sessions(document, ttl=60, now=1010.0)]
    assert live == ["newest", "recent", "undated"], live
    carried = [s["state"] for s in login.supersede(document, ttl=60, now=1010.0)]
    assert carried == ["newest", "recent"], carried


def test_a_file_written_before_superseded_existed_reads_as_one_session():
    """Every session file on disk today has no `superseded` key. It must read
    as a one-session document rather than as an error or an empty list."""
    legacy = {"state": "s", "code_verifier": "v", "created_at": 0}
    assert login.sessions_in(legacy) == [legacy]
    assert login.session_for_state(legacy, "s") == legacy
    assert login.sessions_in({}) == [{}]
    assert login.sessions_in(None) == []


def test_wait_watches_every_state_the_file_carries(tmp_path, monkeypatch, capsys):
    """He answers the link he is looking at, which is not always the newest."""
    session = tmp_path / "session.json"
    login.save_session(str(session), {
        "code_verifier": "new-v", "state": "newest", "client_id": "c",
        "token_url": "u", "redirect_uri": "r", "user_agent": "axios/1.15.2",
        "created_at": 0,
        "superseded": [{
            "code_verifier": "old-v", "state": "older", "client_id": "c",
            "token_url": "u", "redirect_uri": "r", "user_agent": "axios/1.15.2",
            "created_at": 0,
        }],
    })
    watched = {}

    def fake_await(states, timeout, poll=15.0):
        watched["states"] = list(states)
        return "the-code", 12, "older"

    monkeypatch.setattr(login, "await_code", fake_await)
    monkeypatch.setattr(login, "_post_json", lambda url, body, **k: (
        200, {"access_token": "at", "refresh_token": "rt", "expires_in": 60})
        if body["code_verifier"] == "old-v" else (400, {"error": "invalid_grant"}))
    from tools import telegram_inbox
    monkeypatch.setattr(telegram_inbox, "ack", lambda through, *a, **k: (0, "ok"))
    assert login.main(["--session", str(session), "wait", "--timeout", "1"]) == 0
    assert watched["states"] == ["newest", "older"], watched
