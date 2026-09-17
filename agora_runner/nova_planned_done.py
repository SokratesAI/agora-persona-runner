"""Planned vs. done, one line per cycle (idea #312, under issue #227).

The agreed Nova key result `nova-kr-trust-cycles-shown` measures the share
of cycles in a 3-month window that show up in a planned vs. done view, and
until this there was no such view: the journal shows what a cycle did and
nothing kept what it set out to do past a day.

Two sources, joined on the cycle number. **Planned** is every row of
`claims-history.json` for that cycle -- the item it claimed and the note it
wrote. **Done** is its journal entry's title, beside the outcome its
release recorded. A cycle number inside the window with no journal entry
is a **gap**, and it is drawn as a line rather than skipped, because a view
that leaves silent cycles out reads 100% forever.

The plan half only starts where the history does (cycle 1682, 2026-09-16).
A line older than that has no plan because nothing kept one, not because
the cycle planned nothing, and the payload says where the history starts so
the page can say so. No I/O here.
"""
import html
import json
from datetime import date, timedelta

WINDOW_DAYS = 90


def _rows(history_text):
    if not (history_text or "").strip():
        return []
    rows = json.loads(history_text).get("rows")
    if not isinstance(rows, list):
        raise ValueError("claims-history.json has no `rows` list")
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("cycle"), int)]


def _entry_day(entry):
    try:
        return date.fromisoformat(entry.get("date") or "")
    except ValueError:
        return None


def planned_done(history_text, entries, today=None):
    """The page's payload: `lines` newest first, plus the counts.

    `entries` are the journal payload's entries. A `silence` entry is the
    journal's own marker for a cycle that wrote nothing, so it does not
    count as a cycle showing up.
    """
    today = today or date.today()
    start = today - timedelta(days=WINDOW_DAYS)
    rows = _rows(history_text)

    written = {}
    for entry in entries:
        cycle = entry.get("cycle")
        if not isinstance(cycle, int) or entry.get("kind") == "silence":
            continue
        day = _entry_day(entry)
        if day is None or day < start:
            continue
        written.setdefault(cycle, entry)

    planned = {}
    for row in rows:
        at = str(row.get("at") or "")[:10]
        try:
            if date.fromisoformat(at) < start:
                continue
        except ValueError:
            pass
        planned.setdefault(row["cycle"], []).append({
            "item": row.get("item", ""),
            "note": row.get("note", ""),
            "state": row.get("state", ""),
            "outcome": row.get("outcome", ""),
        })

    cycles = set(written) | set(planned)
    if not cycles:
        return {"lines": [], "total": 0, "shown": 0, "share": None,
                "windowDays": WINDOW_DAYS, "historyFromCycle": None}
    # A cycle newer than the newest entry may still be running: it has
    # claimed and not yet written. Starting at the newest entry means a
    # silent cycle shows as a gap once any later cycle has written, and
    # never while it might just be unfinished.
    newest = max(written) if written else max(cycles)
    lines = []
    for cycle in range(newest, min(cycles) - 1, -1):
        entry = written.get(cycle)
        lines.append({
            "cycle": cycle,
            "planned": planned.get(cycle, []),
            "done": entry.get("title", "") if entry else "",
            "date": entry.get("date", "") if entry else "",
            "time": entry.get("time", "") if entry else "",
            "gap": entry is None,
        })
    shown = sum(1 for line in lines if not line["gap"])
    return {
        "lines": lines,
        "total": len(lines),
        "shown": shown,
        "share": round(100.0 * shown / len(lines), 1),
        "windowDays": WINDOW_DAYS,
        "historyFromCycle": min((r["cycle"] for r in rows), default=None),
    }


def render_page(payload):
    """The payload as one self-contained HTML page for his phone."""
    esc = html.escape
    out = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Planned vs. done</title><style>",
        "body{font:15px/1.4 system-ui,sans-serif;margin:0;padding:12px;background:#111;color:#eee}",
        "a{color:#8ab4f8}h1{font-size:20px;margin:4px 0 8px}.sum{color:#bbb;margin-bottom:12px}",
        ".l{border-top:1px solid #333;padding:8px 0}.c{font-weight:600}.t{color:#999;font-size:13px}",
        ".p,.d{margin-top:2px}.k{color:#999;font-size:12px;text-transform:uppercase;margin-right:6px}",
        ".gap{color:#f28b82}.none{color:#777}",
        "</style></head><body>",
        "<a href='/'>&larr; Nova</a><h1>Planned vs. done</h1>",
    ]
    if payload["total"]:
        out.append(
            f"<div class=sum>{payload['shown']} of {payload['total']} cycles in the last "
            f"{payload['windowDays']} days wrote a journal entry ({payload['share']}%). "
            "A red line is a cycle that wrote nothing.")
        if payload["historyFromCycle"] is not None:
            out.append(
                f" Plans are kept from cycle {payload['historyFromCycle']} on; an older "
                "cycle shows no plan because nothing kept one then.")
        out.append("</div>")
    else:
        out.append("<div class=sum>Nothing to show yet.</div>")
    for line in payload["lines"]:
        cls = "l gap" if line["gap"] else "l"
        stamp = f" <span class=t>{esc(line['date'])} {esc(line['time'])}</span>" if line["date"] else ""
        out.append(f"<div class='{cls}'><span class=c>Cycle {line['cycle']}</span>{stamp}")
        if line["planned"]:
            for plan in line["planned"]:
                what = esc(plan["item"]) + (f" — {esc(plan['note'])}" if plan["note"] else "")
                out.append(f"<div class=p><span class=k>Planned</span>{what}</div>")
                if plan["outcome"]:
                    out.append(f"<div class=p><span class=k>{esc(plan['state'] or 'ended')}</span>{esc(plan['outcome'])}</div>")
        else:
            out.append("<div class='p none'><span class=k>Planned</span>no plan kept</div>")
        if line["gap"]:
            out.append("<div class=d><span class=k>Done</span>no journal entry</div>")
        else:
            out.append(f"<div class=d><span class=k>Done</span><a href='/cycle/{line['cycle']}'>{esc(line['done'])}</a></div>")
        out.append("</div>")
    out.append("</body></html>")
    return "".join(out)
