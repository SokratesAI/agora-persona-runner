"""Tests for tools.ultrareview_quota.

The one that matters is the exit status. A cycle reads this tool to
decide whether it can run `claude ultrareview` without a human, and the
three ways that answer can be "no" are not interchangeable: the free
reviews are spent (`confirm`, costs money), the account is refused
outright (`blocked`, a configuration), or nothing answered (unreadable).
Collapsing any pair of them is the failure this asserts against.
"""

import io
import json

import pytest

from tools import ultrareview_quota as uq


def fake_opener(bodies):
    """Answer each path from `bodies`; a missing path raises like the network."""
    def opener(req, timeout=None):
        for path, body in bodies.items():
            if req.full_url.endswith(path):
                return io.BytesIO(json.dumps(body).encode())
        raise OSError(f"no fixture for {req.full_url}")
    return opener


def collect():
    lines = []
    return lines, lines.append


def test_proceed_is_zero_and_says_no_charge():
    quota = {"reviews_used": 0, "reviews_limit": 3, "reviews_remaining": 3,
             "is_overage": False}
    preflight = {"action": "proceed", "billing_note": "Free ultrareview 1 of 3."}
    lines, out = collect()
    assert uq.report(quota, preflight, [], out=out) == 0
    text = "\n".join(lines)
    assert "3 left of 3" in text
    assert "Free ultrareview 1 of 3." in text


def test_overage_needs_a_human_and_is_not_confused_with_blocked():
    quota = {"reviews_used": 3, "reviews_limit": 3, "reviews_remaining": 0,
             "is_overage": True}
    preflight = {"action": "confirm",
                 "confirm": {"body": "This review bills as usage credits."}}
    lines, out = collect()
    assert uq.report(quota, preflight, [], out=out) == 2
    text = "\n".join(lines)
    assert "NEEDS A HUMAN" in text
    assert "usage credits" in text
    assert "BLOCKED" not in text
    assert "in overage" in text


def test_blocked_is_reported_as_a_configuration_not_a_quota():
    quota = {"reviews_used": 0, "reviews_limit": 3, "reviews_remaining": 3,
             "is_overage": False}
    preflight = {"action": "blocked",
                 "blocked": {"message": "Ultrareview runs in Claude Code on "
                                        "the web and is unavailable when "
                                        "essential-traffic-only mode is active."}}
    lines, out = collect()
    assert uq.report(quota, preflight, [], out=out) == 2
    text = "\n".join(lines)
    assert "BLOCKED" in text
    assert "configuration, not a quota" in text
    # Three reviews are still on the counter; the refusal is not about them.
    assert "3 left of 3" in text


def test_no_verdict_is_one_not_zero():
    """An unreadable preflight must never read as "a run would proceed"."""
    lines, out = collect()
    assert uq.report(None, None, ["preflight returned HTTP 500"], out=out) == 1
    text = "\n".join(lines)
    assert "COULD NOT READ" in text
    assert "HTTP 500" in text


def test_fetch_reports_a_dead_endpoint_instead_of_raising():
    opener = fake_opener({uq.QUOTA_PATH: {"reviews_remaining": 3}})
    quota, preflight, problems = uq.fetch("tok", opener=opener)
    assert quota == {"reviews_remaining": 3}
    assert preflight is None
    assert any("preflight failed" in p for p in problems)


def test_read_token_prefers_the_first_file_that_has_one(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"claudeAiOauth": {}}))
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"claudeAiOauth": {"accessToken": "sk-real"}}))
    token, where = uq.read_token([str(empty), str(good)])
    assert token == "sk-real"
    assert where == str(good)


def test_read_token_says_where_it_looked(tmp_path):
    missing = str(tmp_path / "nope.json")
    token, why = uq.read_token([missing])
    assert token is None
    assert missing in why
