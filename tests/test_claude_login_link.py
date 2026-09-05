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
