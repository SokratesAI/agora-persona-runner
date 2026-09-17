import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from agora_runner import nova_app_opens as opens

NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1")
ANDROID_CHROME = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36")
IOS_CHROME = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) CriOS/131.0 Mobile/15E148 Safari/604.1")
EDGE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0")


@pytest.mark.parametrize("ua, label", [
    (IPHONE, "Safari 18 on iOS"),
    (ANDROID_CHROME, "Chrome 131 on Android"),
    # Every Chromium UA also says Safari, and Edge also says Chrome.
    (IOS_CHROME, "Chrome 131 on iOS"),
    (EDGE, "Edge 131 on Windows"),
    ("", "unknown browser on unknown system"),
])
def test_browser_label_names_the_specific_browser_first(ua, label):
    assert opens.browser_label(ua) == label


class FakeVault:
    def __init__(self, text=""):
        self.text, self.rev, self.writes = text, 1, 0

    def read(self, path):
        assert path == opens.APP_OPENS_PATH
        return self.text, self.rev

    def write(self, path, content, if_rev=None):
        assert if_rev == self.rev
        self.text, self.rev, self.writes = content, self.rev + 1, self.writes + 1


def test_record_appends_a_clean_and_a_failed_open():
    vault = FakeVault()
    opens.record(IPHONE, [], read=vault.read, write=vault.write, now=NOW)
    opens.record(ANDROID_CHROME, ["TypeError: x is undefined at /app.js:10"],
                 read=vault.read, write=vault.write, now=NOW)
    rows = opens.load(vault.text)
    assert [r["clean"] for r in rows] == [True, False]
    assert rows[1]["errors"] == ["TypeError: x is undefined at /app.js:10"]
    assert rows[0]["browser"] == "Safari 18 on iOS"


def test_record_bounds_what_a_client_can_send():
    vault = FakeVault()
    opens.record(IPHONE, ["e" * 5000] * 50, read=vault.read, write=vault.write, now=NOW)
    errors = opens.load(vault.text)[0]["errors"]
    assert len(errors) == opens.MAX_ERRORS
    assert all(len(e) == opens.MAX_ERROR_CHARS for e in errors)
    # Junk instead of a list is no errors, not a crash.
    assert opens.row(IPHONE, "not a list", NOW)["clean"] is True


def test_record_keeps_only_the_newest_rows():
    vault = FakeVault(opens.dumps([{"at": str(i)} for i in range(opens.MAX_ROWS)]))
    opens.record(IPHONE, [], read=vault.read, write=vault.write, now=NOW)
    rows = opens.load(vault.text)
    assert len(rows) == opens.MAX_ROWS
    assert rows[0] == {"at": "1"} and rows[-1]["browser"] == "Safari 18 on iOS"


def test_a_document_of_the_wrong_shape_is_never_overwritten():
    vault = FakeVault(json.dumps({"something": []}))
    with pytest.raises(ValueError):
        opens.record(IPHONE, [], read=vault.read, write=vault.write, now=NOW)
    assert vault.writes == 0


def test_clean_share_counts_the_window_and_names_each_browser():
    rows = [opens.row(IPHONE, [], NOW), opens.row(IPHONE, ["boom"], NOW),
            opens.row(ANDROID_CHROME, [], NOW),
            opens.row(IPHONE, ["old"], datetime(2026, 9, 1, tzinfo=timezone.utc))]
    value, detail = opens.clean_share(rows, now=NOW)
    assert value == 67
    assert "2 of 3 opens" in detail
    assert "Safari 18 on iOS: 1 of 2" in detail and "Chrome 131 on Android: 1 of 1" in detail


def test_no_opens_is_no_reading_not_zero():
    value, detail = opens.clean_share([], now=NOW)
    assert value is None and "no app opens" in detail
