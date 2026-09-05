"""The login-link flow, judged without ever reaching Anthropic.

Every test here either reads the installed binary or uses a fake transport.
Nothing runs a live exchange -- see the module docstring for why that is a
decision rather than an omission.
"""

import base64
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

    def fake_post(url, body):
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
        login.exchange(session, "code", post=lambda url, body: (401, {}))


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
        lambda url, body, timeout=30: (200, {
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
        lambda url, body, timeout=30: (200, {
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
    monkeypatch.setattr(login, "read_binary_text", lambda path=None: BUNDLE)
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

    def blows_up(url, body, timeout=30):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    monkeypatch.setattr(login, "_post_json", blows_up)
    assert login.main(["--session", str(session), "finish", "--code", "code#s"]) == 2
    assert "REFUSED" in capsys.readouterr().out
