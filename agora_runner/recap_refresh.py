"""Rewrites the twelve-hour recap card when a new journal entry lands.

His `issues.md` #219, rated 🔴 Immediately: *"The 12-hour summary card is
always a bit out of date, because each cycle has to judge for itself
whether to rewrite it and a cycle short on time skips it. Regenerate it
when the journal changes instead of on a clock ... Opus must not spend
cycle time on this."*

`agora_runner/nova_recap.py` argues that grouping cycles into topics is a
judgement a mechanical rule cannot make, and that is still true -- what
changed is who makes it. It does not have to be the Opus session that is
in the middle of something else; Haiku on the subscription can read the
same raw material `tools.recap` prints and write the five or six bullets.
So the judgement stays in a model and comes out of the cycle's hour.

**The trigger is the journal, not a clock.** Every recap now stamps the
newest journal entry it was built from (`| journal 1234-cycle-1200.md`),
so "is this stale" is a comparison of two filenames rather than an age in
hours. A poll that finds the same entry does no work and costs one id
listing -- `vault_list_ids` reads keys only, measured at 0.045s for the
journal folder. `STALE_AFTER_HOURS` on the card is unchanged and still
what the reader sees; this is what stops it ever getting there.

**Single-flight, two ways, because they fail differently.** A
non-blocking lock stops this pod running two refreshes at once, and the
conditional write (`if_rev`) stops a second writer -- another pod, or a
cycle running `tools.recap --put` by hand -- being silently overwritten.
The lock cannot see the second case and the revision cannot see the
first.

**On any failure the old card stays.** No blanking, no placeholder
bullets: the card keeps its previous stamp and the page's own freshness
line then does exactly the job it was built for. A refresher that can
replace a real summary with an apology is worse than one that skips.
"""

import os
import re
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from agora_runner.config import CLAUDE_BRIDGE_TOKEN, CLAUDE_BRIDGE_URL
from agora_runner.http_util import http_json
from agora_runner.log import log
from agora_runner.nova_journal import JOURNAL_DIR, entry_seq, file_cycle
from agora_runner.nova_recap import MAX_BULLETS, RECAP_PATH, parse_recap, render
from agora_runner.vault import vault_list_ids, vault_read_path, vault_read_path_rev, vault_write_path

OSLO = ZoneInfo("Europe/Oslo")

#: How often to look. This is a poll for a *cheap* comparison, not the
#: refresh interval -- the expensive half only runs when the newest entry
#: changed. Five minutes against a ~24-minute heartbeat means the card is
#: never more than one cycle behind the journal.
POLL_SECONDS = float(os.environ.get("RECAP_POLL_SECONDS", "300"))
#: The runner restarts several times on a working afternoon and the first
#: poll after a restart would otherwise land in the middle of a deploy.
FIRST_POLL_SECONDS = float(os.environ.get("RECAP_FIRST_POLL_SECONDS", "120"))

#: The window the card claims to cover, and how many entries to read to
#: fill it. Both mirror `tools.recap`, whose docstring explains why the
#: count is approximate: an entry's heading is the cheapest date on it and
#: some entries carry none, so reading a couple too many costs nothing and
#: the card's own stamp is the exact claim.
WINDOW_HOURS = 12
CYCLE_MINUTES = 24

#: Haiku on the SUBSCRIPTION through the bridge's fast lane, never the
#: metered API -- rule 9 of `identity.md`, and the same lane and the same
#: flags `nova_conversations.model_title` already uses.
RECAP_MODEL = "claude-haiku-4-5-20251001"
#: A recap is a nicety on a page. It must never hold the bridge open for a
#: full turn window; if Haiku is slow the card keeps what it has.
RECAP_TIMEOUT_SECONDS = 120

_SYSTEM = (
    "You summarise an engineering journal for its owner, who reads it on his "
    "phone to see what has been done for him. He asked for at most six "
    "bullets covering the last twelve hours, grouped by what the work WAS -- "
    "never one bullet per cycle, because several cycles in a row are usually "
    "one piece of work, and the page below already lists the cycles one by "
    "one.\n\n"
    "Each bullet is one or two plain sentences leading with what changed for "
    "him. Name the thing before any number or identifier. Never summarise by "
    "counting ('18 improvements to X', 'several fixes') -- that is the one "
    "shape he cannot act on; say what the change lets him do instead. Do not "
    "label a bullet with a category like 'Bug fix:' or 'Feature:', do not "
    "name cycle numbers, and do not write 'completed work from an earlier "
    "cycle' -- say what the work was. Where a bullet names something he "
    "could go and open, put the URL in it.\n\n"
    "Reply with the bullets only, one per line, each starting with '- '. No "
    "preamble, no closing line."
)


_HEADING = re.compile(r"^### (?P<head>.*)$", re.M)
_WHEN = re.compile(r"(20\d\d-\d\d-\d\d)[ T](\d\d:\d\d)")
#: The fixed `PR: ... | Outcome: ...` line every entry ends with. It is
#: the one non-prose line that does not announce itself with markdown
#: punctuation, and an entry whose prose is all tables or quotes would
#: otherwise hand the model its own footer as the summary.
_FOOTER = re.compile(r"^(PR|Board|Outcome):", re.I)
_BULLET_LINE = re.compile(r"^\s*(?:[-*\u2022]\s+|\d+[.)]\s+)?(?P<text>\S.*?)\s*$")

_lock = threading.Lock()
_thread = None


def newest_entry(names=None):
    """The newest journal entry's filename, or `""`.

    Sorted by the integer sequence prefix, never as text -- `tools.recap`
    paid for that one: a text sort put `1000-cycle-933.md` between `100-`
    and `101-`, so the window read the newest entries it could see and
    could not see any of them.
    """
    if names is None:
        names = vault_list_ids(JOURNAL_DIR)
    files = [str(n).rsplit("/", 1)[-1] for n in (names or [])
             if str(n).lower().endswith(".md")]
    if not files:
        return ""
    return max(files, key=lambda name: (entry_seq(name), name))


def _window_names(names, limit):
    files = [str(n).rsplit("/", 1)[-1] for n in (names or [])
             if str(n).lower().endswith(".md")]
    files.sort(key=lambda name: (entry_seq(name), name))
    return list(reversed(files[-limit:]))


def window(names, now, hours=WINDOW_HOURS, read=vault_read_path):
    """`[{file, title, footer, lead}]` for the entries in the window, newest first.

    The same material `python3 -m tools.recap` prints for a cycle to read,
    assembled from the runner's own CouchDB client instead of the bridge's
    `vault_tool.py`, which does not exist on this pod -- plus `lead`, the
    entry's opening prose, which that tool does not print because a cycle
    reading the list can open any entry it wants and Haiku cannot. Measured
    2026-09-13: titles alone produced bullets like *"Merged 18+ improvements
    to Marcus"* and *"Completed work from a previous incomplete cycle"*,
    which is the counting he cannot act on. The first paragraph is where an
    entry says what actually changed.
    """
    cutoff = now - timedelta(hours=hours)
    limit = max(1, int(hours * 60 / CYCLE_MINUTES))
    rows = []
    for name in _window_names(names, limit):
        body = read(JOURNAL_DIR + name) or ""
        heading = _HEADING.search(body)
        title = heading.group("head").strip() if heading else ""
        stamp = _entry_when(title)
        if stamp is not None and stamp < cutoff:
            break
        footer = [l.strip() for l in body.splitlines() if l.strip().startswith("PR:")]
        rows.append({
            "file": name,
            "title": title,
            "footer": footer[-1] if footer else "",
            "lead": opening_prose(body, heading.end() if heading else 0),
        })
    return rows


#: How much of an entry's opening prose to hand the model. One paragraph is
#: usually 400-900 characters and thirty of them is a prompt Haiku reads in
#: a couple of seconds; the cap is here so one unusually long paragraph
#: cannot crowd out the twenty-nine entries after it.
LEAD_CHARS = 700


def opening_prose(body, start=0):
    """The first real paragraph after the heading, flattened to one line.

    Skips blank lines and the entry's own markdown furniture -- a quote, a
    rule, another heading -- because the sentence that says what happened is
    what this is for.
    """
    paragraph = []
    for line in (body or "")[start:].splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if (stripped.startswith(("#", ">", "---", "```", "|", "- ", "* "))
                or _FOOTER.match(stripped)):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return " ".join(paragraph)[:LEAD_CHARS]


def _entry_when(heading):
    match = _WHEN.search(heading or "")
    if not match:
        return None
    try:
        return datetime.fromisoformat(
            match.group(1) + "T" + match.group(2)).replace(tzinfo=OSLO)
    except ValueError:
        return None


def cycle_range(rows):
    """`871-901`, or a single number, or `""` -- what the card claims to cover."""
    numbers = sorted(n for n in (file_cycle(r["file"]) for r in rows) if n)
    if not numbers:
        return ""
    if numbers[0] == numbers[-1]:
        return str(numbers[0])
    return f"{numbers[0]}-{numbers[-1]}"


def prompt_for(rows):
    material = "\n\n".join(
        "\n".join(part for part in (
            f"- {r['title']}",
            f"    {r['footer']}" if r["footer"] else "",
            f"    {r['lead']}" if r.get("lead") else "",
        ) if part)
        for r in rows
    )
    return (
        f"Here are the journal entries from the last {WINDOW_HOURS} hours, newest "
        "first. Each block is one cycle: its title, then its PR/Board/Outcome "
        "footer where there is one, then its opening paragraph.\n\n<entries>\n"
        + material + "\n</entries>\n\n"
        f"Write at most {MAX_BULLETS} bullets summarising this for the owner, "
        "grouped by what the work was rather than by cycle. Lead each bullet with "
        "what changed for him, in plain words. Bullets only, one per line."
    )


def bullets_from(text):
    """Model output as a bullet list, or `[]` if there is nothing usable in it.

    `[]` is the caller's signal to keep the old card. Anything past
    `MAX_BULLETS` is a refusal rather than a silent cut, for `tools.recap`'s
    reason: a summary quietly missing its last bullet is worse than one
    that did not get written, because nothing on the page says so.
    """
    lines = []
    for raw in (text or "").splitlines():
        match = _BULLET_LINE.match(raw)
        if not match:
            continue
        candidate = match.group("text")
        # A recap is a list. A model that answers in prose gives one long
        # line with no bullet marker, and that is not a card.
        if raw.strip().startswith(("-", "*", "\u2022")) and candidate:
            lines.append(candidate)
    if not lines or len(lines) > MAX_BULLETS:
        if lines:
            log(f"recap refresh: model returned {len(lines)} bullets, "
                f"more than the {MAX_BULLETS} he asked for -- keeping the old card")
        return []
    return lines


def ask_haiku(rows, post=http_json):
    if not CLAUDE_BRIDGE_URL or not rows:
        return []
    headers = {"x-bridge-token": CLAUDE_BRIDGE_TOKEN} if CLAUDE_BRIDGE_TOKEN else {}
    body = {
        # Not a real conversation id: his Stop button cancels by
        # conversation id, and a recap must not be cancellable by it, nor
        # able to cancel his turn. Same trick as `model_title`.
        "conversation_id": "nova-recap",
        "system": _SYSTEM,
        "prompt": prompt_for(rows),
        "model": RECAP_MODEL,
        "restricted": True,
        "stateless": True,
        "allow_concurrent": True,
    }
    try:
        status, resp = post("POST", f"{CLAUDE_BRIDGE_URL}/generate", body, headers,
                            timeout=RECAP_TIMEOUT_SECONDS)
    except Exception as exc:
        log(f"recap refresh: bridge unreachable: {type(exc).__name__}: {exc}")
        return []
    if status != 200 or not isinstance(resp, dict):
        log(f"recap refresh: bridge answered HTTP {status}")
        return []
    return bullets_from(resp.get("text") or "")


def refresh_once(now=None):
    """Rewrite the card if the journal moved. Returns True if it wrote.

    Every exit that is not a write leaves the existing document exactly as
    it was.
    """
    if not _lock.acquire(blocking=False):
        return False
    try:
        now = now or datetime.now(OSLO)
        names = vault_list_ids(JOURNAL_DIR)
        newest = newest_entry(names)
        if not newest:
            log("recap refresh: no journal entries found -- keeping the old card")
            return False
        existing, rev = vault_read_path_rev(RECAP_PATH)
        if existing and parse_recap(existing).get("journal") == newest:
            return False
        rows = window(names, now)
        bullets = ask_haiku(rows)
        if not bullets:
            return False
        body = render(bullets, now, cycles=cycle_range(rows), journal=newest)
        result = vault_write_path(RECAP_PATH, body, if_rev=rev)
        if not str(result).startswith("written"):
            # A 409 here is the other writer winning, which is the correct
            # outcome of a race and not an error worth retrying into.
            log(f"recap refresh: vault refused the write ({result}) -- old card kept")
            return False
        log(f"recap refreshed from {newest} ({len(bullets)} bullets)")
        return True
    except Exception as exc:
        log(f"recap refresh failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        _lock.release()


def _run(stop):
    if stop.wait(FIRST_POLL_SECONDS):
        return
    while True:
        refresh_once()
        if stop.wait(POLL_SECONDS):
            return


def start_recap_refresh(stop=None):
    """Start the refresher once. Returns the thread, or None if already up.

    `stop` exists so a test can run the real loop and end it, rather than
    asserting on a sleep -- `catalog_refresh` takes it for the same reason
    and the runner passes neither.
    """
    global _thread
    if _thread is not None and _thread.is_alive():
        return None
    stop = stop if stop is not None else threading.Event()
    _thread = threading.Thread(target=_run, args=(stop,), daemon=True, name="recap-refresh")
    _thread.start()
    log(f"recap refresh every {POLL_SECONDS:.0f}s, first in {FIRST_POLL_SECONDS:.0f}s")
    return _thread
