"""Rewrites the twelve-hour recap card whenever the journal changes.

His issue #219, rated 🔴 Immediately: *"The 12-hour summary card is always
a bit out of date, because each cycle has to judge for itself whether to
rewrite it and a cycle short on time skips it. Regenerate it when the
journal changes instead of on a clock: the runner's own loop notices the
newest journal entry is newer than the recap's stamp and asks Haiku
(subscription, bridge fast lane, never the metered API) for the 5-6
bullets ... Opus must not spend cycle time on this."*

Three things follow from that sentence and each one is a design decision
here rather than a preference.

**The trigger is the journal, not a clock.** `nova_recap.STALE_AFTER_HOURS`
is three hours and its own comment admits it is not a measurement. The
newest journal entry's filename *is* one: it changes exactly when a cycle
files an entry and never otherwise, so `journal <name>` in the recap's
stamp turns "is this card describing the current window" into a string
comparison. A tick on an unchanged journal reads one id listing and stops,
which is why this can run every few minutes without costing anything.

**A daemon thread in the runner, the same shape as `catalog_refresh`.**
That module's reasoning transfers whole: a rule that lives only in
`prompt.md` competes with everything else a cycle could do that hour and
loses to correct prioritisation rather than to neglect. The recap has been
the proof -- `recap_health` exists solely because nothing asked any cycle
to rewrite the card.

**Haiku writes the bullets, through the bridge's fast lane.** The same lane
`nova_conversations.model_title` uses: `claude-cli:` on the subscription,
stateless, no tools, `allow_concurrent` so it never queues behind a cycle
holding the lock. Rule 9 of `identity.md` -- production never spends the
metered API -- and his own parenthesis says the same thing.

**On failure the old card stays.** Every exit below either writes a
complete new document or writes nothing at all. A blank card is strictly
worse than a card whose stamp says it is four hours old, because the stamp
at least tells the reader what he is looking at.

**Single flight.** `_lock` is taken without blocking: a second tick that
arrives while a generation is in progress returns immediately rather than
queueing behind it, so a slow bridge cannot pile ticks up. Concurrent
cycles do not fire this at all -- it runs in the runner pod, one process.
"""

import os
import re
import threading

from agora_runner.config import CLAUDE_BRIDGE_TOKEN, CLAUDE_BRIDGE_URL
from agora_runner.http_util import http_json
from agora_runner.log import log
from agora_runner.nova_journal import JOURNAL_DIR, entry_seq
from agora_runner.nova_recap import MAX_BULLETS, RECAP_PATH, parse_recap, render

# Every few minutes, because the cost of a tick that finds nothing is one
# id listing. The heartbeat is ~24 minutes, so this is well inside "the
# card is current by the time he opens the page after a cycle finishes".
REFRESH_INTERVAL_SECONDS = float(os.environ.get("RECAP_REFRESH_SECONDS", "240"))
# The runner restarts several times on a working afternoon and the first
# tick reads the journal; `catalog_refresh` waits for the same reason.
FIRST_REFRESH_SECONDS = float(os.environ.get("RECAP_FIRST_REFRESH_SECONDS", "120"))

# How many entries go in front of Haiku. `tools.recap` computes this from
# 12 hours at a 24-minute heartbeat and gets 30; the same number here, and
# the window is approximate by construction for the reason that module
# gives -- an entry heading carries a timestamp only sometimes.
WINDOW_ENTRIES = 30

MODEL = "claude-haiku-4-5-20251001"
# The card is a nicety on a timer. It must never hold a bridge request open
# for a full turn window; if Haiku is slow the reader keeps the card he has.
TIMEOUT_SECONDS = 180

SYSTEM = (
    "You write a six-bullet summary of an autonomous agent's last twelve "
    "hours of work, for the person who owns it. Reply with the bullets "
    "only: one per line, each starting with '- ', no heading, no preamble, "
    "no closing line."
)

_HEADING = re.compile(r"^###\s+(?P<text>\S.*)$")
_PR_LINE = re.compile(r"^PR:\s*(?P<text>\S.*)$")
_BULLET = re.compile(r"^[-*•]\s+(?P<text>\S.*)$")
#: `SokratesAI/agora-persona-runner#1046` in an entry's PR footer. Expanded
#: to a full URL before Haiku sees it, because of his capture 2026-09-04:
#: *"the bullet that mentions the tailnet start page has been created, i
#: immediately want to check it out but I'm left without a url or any
#: clickable link."* A model cannot invent a link that was not in front of
#: it, and `#1046` is not one.
_PR_REF = re.compile(r"\b(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)#(?P<number>\d+)\b")

_lock = threading.Lock()
_thread = None


def newest_entry(paths):
    """The newest journal entry's filename, or "" -- the stamp's fact.

    Sorted by the integer sequence prefix, never as text, and `tools.recap`
    carries the bill for that: the folder passed 999 entries on 2026-09-04
    and a text sort puts `1000-cycle-933.md` between `100-` and `101-`, so
    `[-1]` named a day-and-a-half-old entry while twenty newer ones sat in
    the middle of the list. Here that would not make the card stale -- it
    would make it *look* current forever, because the marker would stop
    changing.
    """
    names = [p.rsplit("/", 1)[-1] for p in (paths or []) if p.endswith(".md")]
    if not names:
        return ""
    return max(names, key=lambda name: (entry_seq(name), name))


def raw_material(names, read):
    """`- <file>  <heading>` lines for the newest entries, newest first.

    `read` takes a full vault path and returns markdown or None, so the
    caller injects `vault_read_path` and a test injects a dict. An entry
    that will not read is skipped rather than failing the tick: the summary
    is of a window, and a window missing one of thirty entries is still a
    summary. An entry with no heading is skipped for a different reason --
    there is nothing about it to summarise.
    """
    rows = []
    for name in sorted(names, key=lambda n: (entry_seq(n), n), reverse=True)[:WINDOW_ENTRIES]:
        body = read(JOURNAL_DIR + name) or ""
        heading = ""
        pr = ""
        for line in body.splitlines():
            line = line.strip()
            if not heading:
                match = _HEADING.match(line)
                if match:
                    heading = match.group("text").strip()
                    continue
            if not pr:
                match = _PR_LINE.match(line)
                if match:
                    pr = match.group("text").strip()
        if not heading:
            continue
        rows.append(f"- {heading}" + (f"\n    PR: {pr_links(pr)}" if pr else ""))
    return rows


def pr_links(text):
    """`owner/repo#N` expanded to the URL it names, in place."""
    return _PR_REF.sub(
        lambda m: f"https://github.com/{m.group('owner')}/{m.group('repo')}"
                  f"/pull/{m.group('number')}",
        text or "")


def prompt_for(rows):
    """The user turn. The instruction is here, not only in `system`.

    Measured through the real bridge on 2026-09-11 for the title lane and
    it applies unchanged: with the instruction in `system` alone the CLI
    wraps the input in its own helpful-agent prompt and Haiku *answers* the
    material instead of labelling it. Fenced as an excerpt, it reads as
    something to summarise.
    """
    return (
        "Below are the journal entry titles an autonomous agent filed over "
        f"its last twelve hours, newest first. Write at most {MAX_BULLETS} "
        "bullets summarising what was DONE.\n"
        "Group by what the work WAS, not one bullet per entry -- several "
        "entries in a row are usually one piece of work, and one bullet per "
        "entry is exactly what the owner asked not to read.\n"
        "Open each bullet with a bold two-to-five-word label in **asterisks**, "
        "then a sentence in plain past tense. Keep any URL or /path that "
        "appears in the material, so he can go and look at the thing.\n"
        "Do NOT reply to the entries, comment on them, or add a closing "
        f"line. Output at most {MAX_BULLETS} lines, each starting with "
        "'- '.\n\n<entries>\n"
        + "\n".join(rows)
        + "\n</entries>\n\nBullets:"
    )


def bullets_from(text):
    """Model output as a bullet list, or `[]` if it is not one.

    `[]` is the caller's "keep the old card". Everything that is not a
    bullet line is dropped -- a preamble, a heading, a closing sentence --
    and the list is cut to his ceiling rather than refused at it, because
    the alternative here is no card update at all. `tools.recap` refuses a
    seventh bullet instead, and that difference is deliberate: there a
    cycle is standing by to fix it.
    """
    bullets = []
    for line in (text or "").splitlines():
        match = _BULLET.match(line.strip())
        if not match:
            continue
        bullet = match.group("text").strip()
        # A model that decides to explain itself in bullets is not writing
        # a card. One that long is prose with a dash in front of it.
        if len(bullet) > 400:
            continue
        bullets.append(bullet)
    return bullets[:MAX_BULLETS]


def ask_haiku(prompt):
    """The bullets Haiku wrote, or `[]`. Never raises."""
    if not CLAUDE_BRIDGE_URL:
        return []
    headers = {"x-bridge-token": CLAUDE_BRIDGE_TOKEN} if CLAUDE_BRIDGE_TOKEN else {}
    body = {
        # Not a real conversation id, for the reason the title lane gives:
        # his Stop button cancels by conversation id, and a card must not be
        # killable by it, nor kill his turn.
        "conversation_id": "nova-recap",
        "system": SYSTEM,
        "prompt": prompt,
        "model": MODEL,
        "restricted": True,
        "stateless": True,
        "allow_concurrent": True,
    }
    try:
        status, resp = http_json("POST", f"{CLAUDE_BRIDGE_URL}/generate", body,
                                 headers, timeout=TIMEOUT_SECONDS)
    except Exception as exc:
        log(f"recap refresh: bridge unreachable: {type(exc).__name__}: {exc}")
        return []
    if status != 200 or not isinstance(resp, dict):
        log(f"recap refresh: bridge answered HTTP {status}")
        return []
    return bullets_from(resp.get("text") or "")


def refresh_once(now=None):
    """One tick. Returns True only if a new card was written."""
    from agora_runner.vault import vault_list_ids, vault_read_path, vault_write_path

    if not _lock.acquire(blocking=False):
        log("recap refresh: a generation is already running, skipping this tick")
        return False
    try:
        paths = vault_list_ids(JOURNAL_DIR)
        newest = newest_entry(paths)
        if not newest:
            log("recap refresh: the journal folder listed no entries")
            return False
        current = parse_recap(vault_read_path(RECAP_PATH) or "")
        if current.get("journal") == newest:
            return False
        names = [p.rsplit("/", 1)[-1] for p in paths if p.endswith(".md")]
        rows = raw_material(names, vault_read_path)
        if not rows:
            log("recap refresh: no readable entries in the window")
            return False
        bullets = ask_haiku(prompt_for(rows))
        if not bullets:
            # The old card stays, and its own stamp says how old it is.
            log(f"recap refresh: no bullets from {MODEL}, keeping the card "
                f"written at {current.get('written') or 'an unknown time'}")
            return False
        vault_write_path(RECAP_PATH, render(bullets, now=now, journal=newest))
        log(f"recap refreshed from {newest}: {len(bullets)} bullet(s)")
        return True
    except Exception as exc:
        log(f"recap refresh failed: {type(exc).__name__}: {exc}")
        return False
    finally:
        _lock.release()


def _loop(stop):
    if stop.wait(FIRST_REFRESH_SECONDS):
        return
    while True:
        refresh_once()
        if stop.wait(REFRESH_INTERVAL_SECONDS):
            return


def start_recap_refresh(stop=None):
    """Start the refresher once. Returns the thread, or None if it is off."""
    global _thread
    if REFRESH_INTERVAL_SECONDS <= 0:
        log("recap refresh disabled (RECAP_REFRESH_SECONDS <= 0)")
        return None
    if _thread is not None and _thread.is_alive():
        return _thread
    _thread = threading.Thread(target=_loop, args=(stop or threading.Event(),),
                               name="nova-recap-refresh", daemon=True)
    _thread.start()
    log(f"recap refresh every {REFRESH_INTERVAL_SECONDS}s "
        f"(first in {FIRST_REFRESH_SECONDS}s)")
    return _thread


__all__ = ["newest_entry", "raw_material", "prompt_for", "bullets_from",
           "ask_haiku", "refresh_once", "start_recap_refresh"]
