"""Did the Nova app start cleanly in the browser he opened it in?

The quality-bets trial (work/product-manager/resources/quality-bets.md) left
Compatibility -- "works in his phone's browser" -- with no meter, and wrote it
down as a manual check at each review because no cycle can open his phone.
The owner, goal thread 0256140f, 2026-09-17 16:00: *"implement analytics that run
in the background when i open the app ... so i do not have to do any manual
tasks. I hate manual tasks."*

So the page reports on itself. `index.html` collects every script error and
unhandled rejection from its first line -- before `app.js`, so an `app.js` that
fails to load or to parse is caught too -- and five seconds after load posts
them with nothing else to `/api/app/open`. `nova_site` appends one row here per
open made by a real browser (`nova_demos.opened_by_a_person`), so a cycle's own
headless probe never counts as him.

**What one row claims, and what it does not.** `clean` means no script error
reached `window` in the first five seconds in that browser. It does not see a
layout that renders wrong without throwing, and an error the app catches itself
never reaches `window` at all. It is the must-be floor -- the app starts -- not
a visual check.
"""
import json
import re
from datetime import datetime, timedelta, timezone

APP_OPENS_PATH = "projects/sokrates/projects/agora/nova/resources/app-opens.json"

#: Rows kept. Every write re-sends the whole document, so an uncapped ledger
#: grows each open for ever; 500 is months of opens at a few dozen a day.
MAX_ROWS = 500
#: Errors kept per row, and characters per error -- enough to find the line.
MAX_ERRORS = 5
MAX_ERROR_CHARS = 300

WINDOW_DAYS = 7


def browser_label(user_agent):
    """A short name a reader recognises: "Safari 18 on iOS", "Chrome 131 on Android".

    Order matters: every Chromium UA also says Safari, and Edge and Samsung
    Internet also say Chrome, so the specific names are tested first."""
    ua = user_agent or ""
    if "iPhone" in ua or "iPad" in ua:
        system = "iOS"
    elif "Android" in ua:
        system = "Android"
    elif "Mac OS X" in ua:
        system = "macOS"
    elif "Windows" in ua:
        system = "Windows"
    elif "Linux" in ua:
        system = "Linux"
    else:
        system = "unknown system"
    for name, pattern in (("Edge", r"Edg(?:A|iOS)?/(\d+)"),
                          ("Samsung Internet", r"SamsungBrowser/(\d+)"),
                          ("Firefox", r"(?:Firefox|FxiOS)/(\d+)"),
                          ("Chrome", r"(?:Chrome|CriOS)/(\d+)"),
                          ("Safari", r"Version/(\d+).*Safari/")):
        match = re.search(pattern, ua)
        if match:
            return f"{name} {match.group(1)} on {system}"
    return f"unknown browser on {system}"


def load(text):
    """The rows in a ledger document; `[]` for an empty or new one.

    A present document of the wrong shape raises, because reading it as
    empty would let the next `record` overwrite it."""
    if not (text or "").strip():
        return []
    rows = json.loads(text).get("opens")
    if not isinstance(rows, list):
        raise ValueError(f"{APP_OPENS_PATH} has no `opens` list")
    return rows


def dumps(rows):
    return json.dumps({"opens": rows}, indent=1) + "\n"


def clean_errors(errors):
    """The client's error list, bounded and stringified; junk becomes []."""
    if not isinstance(errors, list):
        return []
    return [str(e)[:MAX_ERROR_CHARS] for e in errors[:MAX_ERRORS]]


def row(user_agent, errors, now=None):
    now = now or datetime.now(timezone.utc)
    errors = clean_errors(errors)
    return {"at": now.isoformat(timespec="seconds"),
            "browser": browser_label(user_agent),
            "clean": not errors,
            "errors": errors,
            "ua": (user_agent or "")[:MAX_ERROR_CHARS]}


def record(user_agent, errors, read=None, write=None, now=None):
    """Append one open. Read-modify-write on the revision it read, retried
    once: a lost race re-reads rather than overwriting the other writer."""
    if read is None or write is None:
        from agora_runner.vault import vault_read_path_rev, vault_write_path
        read = read or vault_read_path_rev
        write = write or vault_write_path
    for attempt in (1, 2):
        text, rev = read(APP_OPENS_PATH)
        rows = (load(text) + [row(user_agent, errors, now)])[-MAX_ROWS:]
        try:
            write(APP_OPENS_PATH, dumps(rows), if_rev=rev)
            return
        except Exception:
            if attempt == 2:
                raise


def clean_share(rows, now=None, days=WINDOW_DAYS):
    """`(percent of opens that started clean or None, detail)` over `days`.

    `None` when nothing was opened in the window: no opens is no reading,
    and a 0 or a 100 there would be a number nobody measured."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).isoformat(timespec="seconds")
    recent = [r for r in rows if str(r.get("at", "")) >= cutoff]
    if not recent:
        return None, f"no app opens recorded in the last {days} days"
    clean = sum(1 for r in recent if r.get("clean") is True)
    by_browser = {}
    for r in recent:
        total, ok = by_browser.get(r.get("browser"), (0, 0))
        by_browser[r.get("browser")] = (total + 1, ok + (r.get("clean") is True))
    per = ", ".join(f"{name}: {ok} of {total}"
                    for name, (total, ok) in sorted(by_browser.items(), key=str))
    return (round(100 * clean / len(recent)),
            f"{clean} of {len(recent)} opens in the last {days} days started "
            f"with no script error ({per})")


if __name__ == "__main__":
    from agora_runner.vault import vault_read_path_rev
    value, detail = clean_share(load(vault_read_path_rev(APP_OPENS_PATH)[0]))
    print(f"{value}% -- {detail}" if value is not None else detail)
