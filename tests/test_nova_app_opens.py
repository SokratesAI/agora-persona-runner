import json
from datetime import datetime, timezone

import pytest

from agora_runner import nova_app_opens as ao

NOON = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)"


def test_a_report_needs_a_real_boolean():
    assert ao.report({}) is None
    assert ao.report({"booted": "yes"}) is None
    assert ao.report([True]) is None
    assert ao.report({"booted": False}) == (False, [])


def test_errors_are_strings_and_capped_in_length():
    booted, errors = ao.report({"booted": True, "errors": ["x" * 1000, "", 7]})
    assert booted is True
    assert errors == ["x" * ao.MAX_TEXT, "7"]


def test_opens_from_one_browser_on_one_day_fold_into_one_row():
    rows = ao.fold([], PHONE, True, [], NOON)
    rows = ao.fold(rows, PHONE, False, ["TypeError: x is undefined"], NOON)
    assert len(rows) == 1
    r = rows[0]
    assert (r["opens"], r["booted"], r["with_errors"]) == (2, 1, 1)
    assert r["last_errors"] == ["TypeError: x is undefined"]
    assert r["day"] == "2026-09-17"


def test_another_browser_or_another_day_gets_its_own_row():
    rows = ao.fold([], PHONE, True, [], NOON)
    rows = ao.fold(rows, "curl/8", True, [], NOON)
    rows = ao.fold(rows, PHONE, True, [], datetime(2026, 9, 18, 8, tzinfo=timezone.utc))
    assert [(r["day"], r["ua"]) for r in rows] == [
        ("2026-09-17", PHONE), ("2026-09-17", "curl/8"), ("2026-09-18", PHONE)]


def test_record_writes_on_the_revision_it_read_and_keeps_old_rows():
    store = {"text": ao.dumps(ao.fold([], "old", True, [], NOON)), "rev": "1"}
    writes = []

    def read(path):
        assert path == ao.APP_OPENS_PATH
        return store["text"], store["rev"]

    def write(path, text, if_rev=None):
        writes.append(if_rev)
        store["text"] = text

    ao.record(PHONE, True, [], read=read, write=write, now=NOON)
    rows = json.loads(store["text"])["opens"]
    assert [r["ua"] for r in rows] == ["old", PHONE]
    assert writes == ["1"]


def test_a_malformed_ledger_raises_instead_of_being_overwritten():
    with pytest.raises(ValueError):
        ao.load('{"something": []}')
    assert ao.load("") == []


def _post(body, user_agent=PHONE):
    import io
    from unittest.mock import MagicMock, patch
    from agora_runner import nova_site
    raw = json.dumps(body).encode()
    handler = nova_site.NovaSiteHandler.__new__(nova_site.NovaSiteHandler)
    handler.path = "/api/app/opened"
    handler.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json",
                       "User-Agent": user_agent}
    handler.rfile = io.BytesIO(raw)
    handler._send_json = MagicMock()
    with patch.object(nova_site.nova_app_opens, "record") as record, \
            patch.object(nova_site.threading, "Thread") as thread:
        thread.side_effect = lambda target, args, daemon: MagicMock(
            start=lambda: target(*args))
        handler._handle_post()
    return handler._send_json.call_args[0], record


def test_the_route_records_the_open_with_the_browsers_own_user_agent():
    (status, body), record = _post({"booted": True, "errors": ["boom"]})
    assert (status, body) == (200, {"ok": True})
    record.assert_called_once_with(PHONE, True, ["boom"])


def test_the_route_refuses_a_body_that_is_not_a_report():
    (status, _), record = _post({"errors": []})
    assert status == 400
    record.assert_not_called()


def test_the_reporter_in_the_page_reads_the_flag_app_js_sets_last():
    from pathlib import Path
    public = Path(__file__).resolve().parent.parent / "agora_runner" / "nova_public"
    page = (public / "index.html").read_text()
    assert page.index('"/api/app/opened"') < page.index('<script src="/app.js"></script>')
    assert "window.novaBooted === true" in page
    assert (public / "app.js").read_text().rstrip().endswith("window.novaBooted = true;")
