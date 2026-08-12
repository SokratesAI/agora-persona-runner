"""Nova's own site: the journal, styled, on the tailnet.

Agora ideas.md #34 -- items 1-4 (journal timeline, status header, digest
strip, per-cycle deep links) and item 6, the capture box.

**The two writes, and where their boundary actually is.** Everything here
was GET until the capture box; `POST /api/capture` and `POST /api/comment`
(ideas.md #44) are the only routes in this module that change anything.
Their safety is not the tailnet alone -- it is that both endpoints are too
narrow to misuse:

- The tailnet is the *authentication* boundary. Reaching this port at all
  means being on Edvard's tailnet, which in practice means his own
  devices. A shared token would have to live in the served JavaScript or
  be typed on a phone, so it would add real friction and no real secrecy.
- The endpoint's shape is the *authorization* boundary, and it is what is
  actually load-bearing. `target` indexes a two-entry dict of literal
  paths (nova_capture.CAPTURE_TARGETS); `/api/comment` does not even take
  one, writing only to nova_comments.COMMENTS_PATH. No path, no marker and
  no position ever comes from the client in either. The worst a request
  can do is add a bullet or a comment to a file Edvard reads and can
  delete, and the vault's daily git snapshot holds the prior version
  regardless.
- `Content-Type: application/json` is required, which is a CSRF defence
  rather than a formality: it is not a CORS "simple request", so a
  browser must preflight it, and this server answers no OPTIONS and sends
  no CORS headers. A page on another origin therefore cannot post here
  even from a browser that is on the tailnet.

Tailscale's identity headers are recorded in the audit entry rather than
trusted, because whether this Ingress forwards them has not been
measured. If a future cycle confirms they arrive, they are the basis for
tightening this further -- see the audit call in do_POST.

**Why this lives in the runner's repo rather than in its own.** Idea #34
sketched a separate `nova-pwa` service, and named the thing that makes
that expensive: the vault client already exists twice (here and in the
bridge) with nothing detecting drift between them, so a third service
reading CouchDB would mean a third copy -- a bug knowingly introduced.
Staying in this repo keeps it at two.

**But it no longer runs in the runner's process.** As of 2026-08-09 it
has its own entrypoint (`run_nova_site.py`) and its own Deployment,
built from this same image. The reasoning is in nova_site_main.py; the
short version is that the runner's `Recreate` + 2880s drain exists to
protect a cycle's reply, and the site inherited it, so the site was down
for the length of every cycle. Sharing the repo was always the point;
sharing the process was incidental.

**Why a second port instead of a second path on 8082.** The /invoke
Service is documented as having no public-facing surface at all, and it
carries /invoke, /mcp and /tool-activity. The Tailscale operator's
Ingress does not reliably filter by path, so exposing 8082 at all would
expose those three. A separate port gets its own Service, its own
Ingress and a NetworkPolicy scoped to this port only -- exactly the
shape platform-config already uses to expose Agora's 8080 while leaving
its 8081 unreachable.

**The limitation this used to record is fixed.** It read: "this
deployment is `Recreate` with a 2880s grace period, so while a cycle is
draining the pod is Terminating and out of the Service's endpoints --
the site is unreachable for that window", and called that the one
argument for splitting the site out later. The capture box turned it
from cosmetic into functional, and the split happened. The site's own
Deployment is `RollingUpdate` with `maxUnavailable: 0`, so a Nova cycle
no longer takes it down and neither does a deploy.

**There is a caching layer now, and the note that used to sit here was
out of date by an order of magnitude.** It read "the full 204KB journal
assembles from CouchDB in 285ms, which is cheaper than the staleness a
cache would buy". Measured against the live pod on 2026-08-10, with 95
entries rather than 70: `/api/journal` takes 3.0-3.5s -- 1.9s of vault
bulk fetch, 1.5s of parsing -- and it was recomputed identically on
every load. Edvard reported it as the app taking a long time to load.
See `cached_payload`.

**Responses are gzipped when the client asks.** Measured against the
live pod on 2026-08-10, a cold load of this site was 588,998 bytes and
none of it was compressed -- `/api/journal` alone was 453,239 -- while
every browser that fetched it was already sending
`Accept-Encoding: gzip, deflate, br, zstd` and getting nothing back.
Compressed, the same load is 154,726 bytes. This is the largest payload
anywhere in this system and it is the page Edvard reads on a phone.

Only gzip: the runtime has no brotli or zstd binding (checked), and the
stdlib gives gzip and raw deflate. That is a smaller ceiling than the
Brotli that agora#50 negotiates via Express, and it is also why this is
less likely to go wrong here -- the encoding this serves is the encoding
its tests exercise. Cycle 70 shipped four passing gzip tests for a path
production never took, because `compression` handed real browsers
Brotli. There is no such gap to fall into with one encoding, but the
test asserting a real browser's header gets `gzip` back is there so the
claim stays checked rather than argued.
"""

import gzip
import hashlib
import json
import mimetypes
import os
import threading
import time
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agora_runner.audit import audit
from agora_runner.config import NOVA_PORT, OSLO
from agora_runner.log import log
from agora_runner.nova_capture import (
    CAPTURE_TARGETS,
    MAX_BODY_BYTES,
    amend,
    capture,
    clean_capture_text,
)
from agora_runner.nova_comments import (
    add_comment,
    add_needs_comment,
    clean_comment_text,
    comments_by_cycle,
    format_stamp,
    needs_comments,
)
from agora_runner.nova_journal import (
    build_status,
    parse_digest,
    parse_journal,
    render_blocks,
)
from agora_runner.nova_replies import (
    WAITING_AFTER_SECONDS,
    enqueue as enqueue_reply,
    failed as failed_replies,
    pending_since,
)
from agora_runner.nova_boards import BOARD_PATHS, parse_board, parse_notes
from agora_runner.nova_costs import costs_payload as shape_costs
from agora_runner.nova_sources import (
    board_markdown,
    comments_markdown,
    cost_ledger_json,
    digest_markdown,
    journal_markdown,
)
from agora_runner.tools_mcp import handle_http as handle_mcp_http
from agora_runner.vault import database_health

PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nova_public")

# An explicit route -> filename map rather than joining the request path
# onto PUBLIC_DIR. Nothing the client sends reaches the filesystem, so
# path traversal is not something this has to defend against -- it
# cannot be expressed.
STATIC_ROUTES = {
    "/app.js": "app.js",
    "/style.css": "style.css",
    "/manifest.webmanifest": "manifest.webmanifest",
    "/sw.js": "sw.js",
    "/icon.svg": "icon.svg",
}

# gzip's header and trailer are a fixed 18 bytes, so a short body comes
# back *bigger*: `/api/comments` is 15 bytes on the live pod and gzips to
# 35. Measured crossover on realistic JSON is around 100 bytes. The
# threshold is 1024 because that is what Express's `compression` uses by
# default, which is the same compressor now sitting in front of Agora
# (agora#50) -- one number for both halves of this system beats two
# defensible ones. This is a limit with a measurement behind it, not a
# tidiness cap: below it, compressing costs bytes rather than saving them.
MIN_COMPRESS_BYTES = 1024

# zlib's default. Measured on the live 453,239-byte journal: level 1 is
# 3.0x in 5.9ms, level 6 is 3.6x in 13.8ms, level 9 is 3.6x in 20.9ms.
# Level 9 buys 880 more bytes for 7ms more CPU, and level 6's 13.8ms sits
# against the ~285ms this endpoint already spends assembling itself out
# of CouchDB.
COMPRESS_LEVEL = 6

# Everything this server sends is text except the SVG, which is also
# text. Listed explicitly rather than compressing whatever is not on a
# deny-list, so a future binary route has to opt in rather than silently
# getting spent CPU for nothing.
COMPRESSIBLE_TYPES = (
    "application/json",
    "application/manifest+json",
    "image/svg+xml",
    "text/",
)


def accepts_gzip(header):
    """Does this `Accept-Encoding` value permit gzip?

    Parsed rather than substring-matched because `gzip;q=0` is how the
    header spells *"not this one"* -- it contains the string "gzip" and
    means the opposite. `*` stands in for anything not otherwise named,
    and carries a q-value of its own for the same reason.
    """
    if not header:
        return False
    wildcard = False
    for part in header.split(","):
        token, _, params = part.strip().partition(";")
        token = token.strip().lower()
        if token not in ("gzip", "*"):
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    quality = 0.0
        if token == "gzip":
            return quality > 0
        wildcard = quality > 0
    return wildcard


def journal_payload():
    """Every entry, with its markdown kept and its blocks not yet built.

    Rendering used to happen here, for all 158 entries, so that
    `journal_page` could slice out the twenty a reader sees -- 1.07MB
    built and serialised per process to answer a request for 7% of it,
    growing by an entry an hour. `_rendered` builds the blocks for the
    window that asked instead, and this stays the parse the etag and the
    status are computed over.

    `body` stays in the cached entry and never leaves it: it is what the
    blocks get built from later, and the client has no use for markdown it
    is not allowed to interpret -- sending both is the same text twice.
    """
    markdown, times = journal_markdown(with_times=True)
    entries = parse_journal(markdown, times)
    # `times` is keyed by the cycle number in the *filename*, which is the
    # only reliable answer to "did this cycle write an entry": the heading
    # is written by hand and the filename is not, so the two disagree
    # whenever a cycle gets the shape wrong, and calling that a gap would
    # accuse a cycle of silence directly above its own words.
    #
    # This used to name the three live entries whose headings the parser
    # could not read a number out of. `normalise_entry` now repairs those
    # before they are parsed, so the two sources agree today -- which is
    # exactly why this stays keyed on the filename rather than being
    # simplified away. It is the independent check, and the next cycle to
    # invent a new way of mis-writing a heading is the reason for it.
    # See `build_status`.
    status = build_status(entries, known_cycles=times.keys() if times else None)
    return {"entries": [dict(entry) for entry in entries], "status": status}


def _rendered(entry):
    """One entry as the client gets it: blocks built, `body` left behind.

    The blocks are memoised onto the cached entry, so a window costs its
    rendering once per process rather than once per request, and only the
    windows somebody actually read are ever built. Two threads racing here
    both render and both store the same value -- the duplicate work is
    cheaper than a lock on the read path, and `render_blocks` is pure.
    """
    blocks = entry.get("blocks")
    if blocks is None:
        blocks = render_blocks(entry.get("body", ""))
        entry["blocks"] = blocks
    out = dict(entry)
    out.pop("body", None)
    return out


def digest_payload():
    payload = parse_digest(digest_markdown())
    payload["needsEdvardBlocks"] = render_blocks(payload["needsEdvard"])
    return payload


def comments_payload():
    """Every comment, grouped by the cycle it is about.

    Keyed by cycle number as a string because that is what JSON object keys
    are; the client looks up `byCycle[String(entry.cycle)]`. The comment
    text is sent as plain text rather than rendered blocks -- unlike the
    journal, this is Edvard's own prose and nothing here interprets it as
    markdown, so there is no markup for the client to be unable to build.
    """
    markdown = comments_markdown()
    grouped = comments_by_cycle(markdown)
    # A reply the worker is still waiting on the bridge for. Sent from the
    # server rather than remembered by the client, so the "replying…" line
    # survives a reload, a second device, and the minutes this can take
    # while a Nova cycle holds the bridge's lock -- see nova_replies.
    queued = pending_since()
    gave_up = failed_replies()
    now = time.time()
    for cycle, items in grouped.items():
        for comment in items:
            key = (cycle, comment.get("stamp"))
            asked_at = queued.get(key)
            comment["replyPending"] = asked_at is not None
            # Two different waits, and the card must not call the second one
            # the first: under the threshold a reply is genuinely being
            # written, over it the bridge is busy with a cycle and this is a
            # queue. Saying "Nova is replying…" for forty minutes is what
            # Edvard reported as the conversation not working at all.
            comment["replyWaiting"] = (
                asked_at is not None and (now - asked_at) >= WAITING_AFTER_SECONDS
            )
            # And when it is not coming at all, say so rather than letting
            # the line disappear as if the answer had arrived.
            comment["replyFailed"] = asked_at is None and key in gave_up
    return {
        "byCycle": {str(cycle): items for cycle, items in grouped.items()},
        # Replies to the digest's Needs Edvard block, which belong to no
        # cycle and so cannot ride in `byCycle`.
        "needs": needs_comments(markdown),
    }


def board_payload(name):
    """Everything on one board page, before it is cut to a window.

    Detail bodies are rendered here rather than on request: the cache
    holds one payload per board and `board_page` slices it, so a tap on a
    row costs a dict lookup instead of a parse. The bodies are the bulk
    of the file -- `issues.md` is 68KB and its `# Details` section is
    ~60KB of that -- which is precisely why they never go out with the
    list. See `board_page`.
    """
    edvard_markdown, nova_markdown, nova_archive_markdown = board_markdown(name)
    board = parse_board(edvard_markdown)
    details = {
        str(number): render_blocks(body) for number, body in board["details"].items()
    }
    # Live first, then the rolled-off older half -- both files are
    # newest-first and the archive holds only what is older than the live
    # file's oldest, so appending preserves the order rather than
    # requiring a sort. `parse_notes` is deliberately run twice instead
    # of over a concatenation; `board_markdown` says why.
    notes = [
        dict(note, blocks=render_blocks(note.pop("text")))
        for note in parse_notes(nova_markdown) + parse_notes(nova_archive_markdown)
    ]
    return {
        "name": name,
        # Rendered *and* raw. The blocks are what the page draws; the raw
        # text is how an edit or a delete addresses the bullet, since
        # `nova_capture.replace_capture` matches on text rather than on an
        # index that a cycle boarding the file would shift underneath it.
        "captures": [
            {"text": text, "blocks": render_blocks(text)} for text in board["captures"]
        ],
        "items": board["items"],
        "details": details,
        "notes": notes,
    }


def costs_payload():
    """What a cycle costs, over time (issues.md #57, page 2).

    The whole endpoint, because the ledger arrives as JSON and leaves as
    JSON: no parse step, no render step, one fetch and one reshape. It is
    cached like the rest for the ordinary reason -- the vault read is the
    slow part and the document changes once an hour, at the end of a
    cycle -- and it is *not* windowed, which is the one thing that
    deserves saying out loud on a page that plots a growing series.

    Measured against the live ledger, 2026-08-11: 96,853 bytes of vault
    document shape to 35,769 bytes of payload, 9,272 gzipped, for 110
    cycles and 728 quota readings. Dropping the keys is what does that
    (`nova_costs` explains the row format), and it holds because both
    series grow by a bounded amount per cycle -- one cycle row, a handful
    of quota readings -- against a page whose entire point is the shape of
    the whole history. A window here would be the first one on this server
    that hides data the reader came for. When it does need one, the honest
    cut is by time, not by count.
    """
    return shape_costs(cost_ledger_json())


def board_page(payload, limit=None, item=None):
    """One board, as the page actually asks for it.

    Three shapes, and the split is the whole point of this endpoint:

    - `item=57` -- one detail body. This is what a tap on a row fetches.
    - `limit=n` -- the list: every row (they are one line each, 60 of
      them, ~5KB in total) plus the newest `n` of my own notes and no
      detail bodies at all.
    - neither -- everything, which is what an app.js served out of a
      service worker's cache from before this shipped would ask for.

    My notes are windowed and his rows are not because they are different
    sizes for a structural reason: a row is a title, a note is a
    paragraph. 294 notes in `nova/resources/issues.md` is 147KB and it
    grows by two or three every cycle; the rows are bounded by how many
    things Edvard has ever filed.
    """
    if item is not None:
        blocks = (payload.get("details") or {}).get(str(item))
        row = next(
            (i for i in payload.get("items") or [] if i.get("number") == item), None
        )
        return {
            "name": payload.get("name"),
            "item": dict(row or {"number": item}, blocks=blocks or []),
            "found": blocks is not None or row is not None,
        }
    notes = payload.get("notes") or []
    page = dict(payload, notes=notes if limit is None else notes[:limit])
    page["notesTotal"] = len(notes)
    if limit is not None:
        page["details"] = {}
    return page


# How long a served payload may be before the next request kicks a
# refresh behind itself. Not a staleness budget for Edvard -- the client
# polls, so what he sees is bounded by the poll interval plus one rebuild
# -- it is how often an *active* reader makes the site rebuild. At 15s a
# session polling every 30s rebuilds once per poll and never waits for one.
CACHE_FRESH_SECONDS = 15

_cache = {}
_cache_lock = threading.Lock()
_refreshing = set()
_build_locks = {}
# Bumped by `invalidate`. A refresh that started before an invalidation
# must not be allowed to write its result afterwards -- see `_refresh`.
_generation = {}


def _versioned(payload):
    """`(body, etag)` for a payload, with the etag also inside it.

    The client cannot read the ETag header when a response comes back out
    of the service worker's cache, so the version has to be in the
    document as well. Hashing the payload *before* the version is added
    keeps that non-circular, and the hash still covers everything the
    client renders.

    Weak, because gzip and identity are different bytes for the same
    payload and `_send` chooses between them per request. A weak etag
    claims semantic equivalence, which is exactly what is true here and
    all a conditional GET needs.
    """
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    etag = 'W/"' + digest + '"'
    payload = dict(payload, version=etag)
    return payload, json.dumps(payload), etag


def cached_payload(name, build):
    """`(payload, body, etag)` -- the last build, served immediately while
    the next one is computed behind the request.

    `/api/journal` costs 3.0-3.5s every time it is asked (measured against
    the live pod, 2026-08-10: 1.9s of vault bulk fetch, 1.5s of parsing,
    95 entries) and it was recomputed identically on every load. That is
    what Edvard reported as "Nova takes a long time to load when i
    refresh it".

    Stale-while-revalidate rather than a TTL, because a TTL only moves
    the 3.5s to whoever arrives first after it expires -- with 24 cycles
    a day writing one entry each, almost every visit is that visitor. The
    refresh is request-driven rather than a timer, so a site nobody is
    looking at costs nothing.

    The first request of a process still pays the full build: there is
    nothing stale to serve, and serving empty would be worse than slow.
    """
    now = time.time()
    with _cache_lock:
        entry = _cache.get(name)
        if entry is not None and now - entry[3] >= CACHE_FRESH_SECONDS and name not in _refreshing:
            _refreshing.add(name)
            thread = threading.Thread(
                target=_refresh, args=(name, build), name=f"nova-site-{name}", daemon=True
            )
        else:
            thread = None
    if thread is not None:
        thread.start()
    if entry is not None:
        return entry[0], entry[1], entry[2]
    # Cold: one build, however many requests arrive at once. This is a
    # ThreadingHTTPServer and a cold load now asks for two payloads in
    # parallel, both of which want the journal -- `/api/digest` needs the
    # journal window to know which cycles its lines belong to. Without
    # this the first load of a process pays the 3.0-3.5s journal build
    # twice, concurrently, for one answer.
    with _build_lock(name):
        with _cache_lock:
            entry = _cache.get(name)
        if entry is not None:
            return entry[0], entry[1], entry[2]
        return _refresh(name, build)


# What gets built at startup. There is an obvious way to write this list
# -- warm what the page asks for on a cold load -- and it is wrong: what
# matters is what the page asks for *and* is served out of this cache,
# and those are not the same list (see `warm_cache`). A module constant
# rather than a literal inside the function so the test that checks it
# against the request path has something to read.
WARM_PAYLOADS = (
    ("journal", journal_payload),
    ("digest", digest_payload),
)


def warm_cache():
    """Build what a first visit asks for, before anyone asks for it.

    `cached_payload` removed the cost of the *second* load and left the
    first one exactly where it was: "The first request of a process still
    pays the full build". That reads like a rare edge until you notice how
    often this process is new. Every cycle that merges into
    `agora-persona-runner` rolls the nova-site pod, which is most hours of
    most days -- so the visitor who pays the cold build is not an unlucky
    first-ever reader, it is Edvard, on his phone, on the next visit after
    almost any cycle. That is issues.md #71, "The Nova app takes 6-7
    seconds to load", still standing after the cache landed.

    Measured against the live pod 2026-08-12, 26 minutes after the #125
    deploy, nothing having visited since: `/api/journal?limit=20` answered
    in **5.70s** cold and **0.009s** on the next three requests. The digest
    was 0.57s and comments 0.07s cold. So the whole of what he is waiting
    for is one build that nobody had asked for yet. (The 3.0-3.5s in
    `cached_payload` is the 2026-08-10 number for the same build; it grows
    with the journal.)

    The two payloads here are the two of `fetchAll`'s three requests that
    are actually served out of this cache. `/api/comments` is the third
    and is not warmed: its handler deliberately does not go through
    `cached_payload` at all -- it is the one payload that changes
    underneath itself -- so warming it would spend a vault read at every
    process start that no request can ever collect. That is worth spelling
    out because the wrong version of this function is the obvious one:
    warm what the page asks for. What matters is what the page asks for
    *and* reads from here, and those are not the same list.

    Boards are also not warmed: they are fetched on a sidebar press rather
    than at startup, and measured 0.53s and 0.39s cold, which is a wait
    nobody has reported.

    Sequential, on one thread, and never on the request path. Serving is
    already underway when this starts, so a visitor arriving mid-warm is
    not made to wait for it -- they take the cold path into the same
    `_build_lock` this is holding and get the one build it produces, which
    is the behaviour that lock already existed for.
    """
    for name, build in WARM_PAYLOADS:
        try:
            cached_payload(name, build)
        except Exception as e:
            # A vault that is unreachable at startup must cost the warm and
            # nothing else: the server is already listening, and the next
            # real request takes the cold path exactly as it does today.
            # Raising here would kill a daemon thread noisily and leave the
            # two payloads after this one unbuilt for no gain.
            log(f"nova-site warm {name} failed: {e}")


def _build_lock(name):
    """The lock serialising cold builds of one payload. Created under the
    cache lock so two threads racing to make it end up with the same one."""
    with _cache_lock:
        lock = _build_locks.get(name)
        if lock is None:
            lock = _build_locks[name] = threading.Lock()
        return lock


def invalidate(name):
    """Drop one cached payload so the next request rebuilds it cold.

    Edvard, `issues.md` 2026-08-12: *"When i create a new issues, the
    'not boarded yet' block for issues is not refreshed automatically.
    This is probably a problem for ideas aswell."* It is, and it is
    deterministic rather than flaky, which is why waiting and retrying
    never fixed it for him.

    `cached_payload` is stale-while-revalidate: it *always* returns the
    entry it is holding and kicks the rebuild off behind the request.
    That is exactly right for a poll -- it is what stopped the site
    costing 3.5s a load -- and exactly wrong immediately after a write.
    The reload that `app.js` fires on a successful capture is therefore
    guaranteed to render the board as it was *before* the capture, every
    single time. Nothing is stale by a fixed number of seconds here; the
    write simply is not in the answer yet.

    Dropping the entry rather than back-dating its timestamp is the whole
    point: a back-dated entry is still served stale once and merely
    schedules a refresh, which is the bug again. With no entry,
    `cached_payload` takes the cold path and *waits* for the true answer.
    Only the request that follows the write pays it, and a board build is
    two vault reads rather than the journal's 3.5s.
    """
    with _cache_lock:
        _cache.pop(name, None)
        _generation[name] = _generation.get(name, 0) + 1
        # Deliberately left in `_refreshing`: a rebuild already in flight
        # read the vault before the write landed, so its result is stale
        # and `_refresh` will now discard it. Clearing the flag here would
        # let a *second* refresh start while the first is still running.


def reset_cache():
    """Drop every cached payload. For tests, which share one process: a
    payload warmed by one test is exactly the stale copy the next one
    would be served, and two tests asserting a vault failure is a 502 got
    a 200 instead."""
    with _cache_lock:
        _cache.clear()
        _refreshing.clear()
        _generation.clear()
        # `_build_locks` is deliberately not cleared. Dropping a lock a
        # cold build is currently holding does not release it -- it just
        # hands the next caller a different lock object, and the two build
        # in parallel, which is the one thing the lock exists to stop. The
        # dict is keyed by payload name, so it holds three entries in
        # production and never grows.


def _refresh(name, build):
    # Read before the build, compared after it. An `invalidate` landing
    # while this build is in flight means the build read the vault before
    # the write it is meant to pick up, so storing its result would put
    # the pre-write answer back into an empty cache and reinstate the
    # exact bug `invalidate` exists to fix.
    with _cache_lock:
        started_at = _generation.get(name, 0)
    try:
        payload, body, etag = _versioned(build())
    except Exception as e:
        with _cache_lock:
            _refreshing.discard(name)
        # A background refresh that raises must not take the thread's
        # process down or poison the cache -- the last good payload keeps
        # being served, which is the whole point of serving it stale.
        log(f"nova-site {name} refresh failed: {e}")
        raise
    with _cache_lock:
        stale = _generation.get(name, 0) != started_at
        if not stale:
            _cache[name] = (payload, body, etag, time.time())
        _refreshing.discard(name)
    # Returned either way: a caller on the cold path asked for *an*
    # answer and this one is no older than the request that wanted it.
    # What the generation check protects is the shared cache, not this
    # return value.
    return payload, body, etag


def journal_page(payload, limit=None, offset=0, cycle=None, now=None):
    """One window of the journal, plus how many entries there are in all.

    The cold load is the half the 304 poll of #84 did not touch: 109
    entries was 678,027 bytes raw / 187,148 gzipped off the live pod at
    06:11 Oslo on 2026-08-11, it grows by one entry every hour, and the
    reader sees twenty of them before they scroll. `status` is not
    sliced -- it is a handful of fields computed over the whole corpus and
    the header renders it on every page.

    `cycle` is what keeps `/cycle/49` working on a cold load. Without it a
    deep link into an entry older than the first page would have to page
    backwards through the feed to find its own subject.

    No `limit` means every entry, which is what this endpoint has always
    done. The client always sends one; the default exists so an app.js
    served out of a service worker's cache from before this shipped still
    renders a whole feed instead of silently losing everything past the
    first page.
    """
    entries = payload.get("entries") or []
    if cycle is not None:
        picked = [entry for entry in entries if entry.get("cycle") == cycle]
    elif limit is None:
        picked = entries
    else:
        end = offset + limit
        # A window never splits a cycle. Six cycles wrote a second entry
        # when they went back to verify their own deploy, and the client
        # hands a cycle's digest line to its *earliest* entry -- which it
        # finds by looking at the entries in front of it. Cut a pair in
        # half and the page can only see the addendum, so the summary
        # renders on the wrong card and then visibly jumps to the right one
        # the moment the window grows past it. Extending the slice to the
        # end of the cycle is cheaper than teaching the client to reason
        # about entries it was not sent.
        while end < len(entries) and entries[end].get("cycle") is not None \
                and entries[end].get("cycle") == entries[end - 1].get("cycle"):
            end += 1
        picked = entries[offset:end]
    return {
        "entries": [_rendered(entry) for entry in picked],
        "status": _with_silence(payload.get("status", {}), now),
        "total": len(entries),
    }


def _with_silence(status, now=None):
    """`status` plus how long the loop has been quiet, judged right now.

    The live half of #72, and it is computed here rather than in
    `build_status` because the payload that holds `status` is cached and
    warmed at startup: a stall judged at build time would be frozen at
    "healthy" for the whole life of a process, which is precisely the
    hours when it would need to say otherwise. Computed here it is never
    more than one request stale -- though *reaching* a client that polls
    with `If-None-Match` takes one more thing, because the journal content
    does not change during a stall and so neither does the base etag. See
    `journal_descriptor`.

    `stalled` waits `STALL_GRACE_INTERVALS` rather than asking whether
    this hour has an entry yet. A cycle writes its entry at the *end* of
    its hour, so between waking and finishing there is a real 20-30
    minute window where agora has started cycle N and this page can only
    see N-1 -- Edvard's #72 is exactly that ambiguity, and a check that
    cannot tell a running cycle from a dead one would raise a false alarm
    every single hour. `silentIntervals` is reported whether or not it
    crossed the threshold, so the two questions stay separable.

    `None` (no entry carries a usable write time) is deliberately not
    flattened into `0`: "nothing to judge" and "judged, and fine" are
    different answers, and only the second is reassurance.
    """
    from agora_runner.cycle_health import HEARTBEAT_MINUTES, STALL_GRACE_INTERVALS

    out = dict(status)
    written = out.get("lastWrittenAt") or ""
    silent = None
    if written:
        try:
            stamp = datetime.fromisoformat(written)
        except ValueError:
            stamp = None
        if stamp is not None:
            elapsed = (now or datetime.now(OSLO)) - stamp
            # An entry stamped in the future is a clock disagreement, not a
            # stalled loop -- zero rather than a negative the client would
            # have to guard. The same call `cycle_health.stalled_for` makes.
            silent = max(0, int(elapsed.total_seconds() // (HEARTBEAT_MINUTES * 60)))
    out["silentIntervals"] = silent
    out["stalled"] = silent is not None and silent >= STALL_GRACE_INTERVALS
    return out


def digest_page(payload, journal, limit=None, offset=0, cycle=None):
    """The digest, with `lines` cut to the cycles the journal window covers.

    `/api/digest` was 270,793 bytes raw, 38,782 gzipped on the wire, off
    the live pod at 07:03 Oslo on 2026-08-11 -- and `lines` was 266,393 of
    that, 98% of a payload the reader sees twenty cycles of. It grows by
    one line an hour, exactly the way `/api/journal` did before #85. Those
    three numbers describe the payload as it was *before* this commit,
    including the dead third copy of every line's text that went with it,
    so they are the baseline rather than a description of the code below.

    **Cut by the journal window's cycle range, not by a line count.** The
    two lists do not run in step: 46 digest lines describe 110 journal
    entries, cycles that never got a digest line leave gaps, and a cycle
    with an addendum spends two entries on one line. Taking the first
    twenty lines would hand the page a set of cycles its feed does not
    contain, and -- worse -- silently drop the summary of whichever cycle
    straddles the boundary. So the window is computed by asking
    `journal_page` for the same slice the feed is showing and keeping
    every line inside the cycles it came back with. The two stay aligned
    because it is literally the same function answering.

    A range rather than exact membership: a digest line whose cycle has no
    entry is invisible to the client either way, and a range cannot fall
    out of step with a feed that is itself contiguous.

    No `limit` means every line, for the same reason `journal_page` says
    it: an app.js served out of a service worker's cache from before this
    shipped asks the old way and must still get a whole answer.
    """
    lines = payload.get("lines") or []
    if cycle is not None:
        picked = [line for line in lines if line.get("cycle") == cycle]
    elif limit is None:
        picked = lines
    else:
        window = journal_page(journal, limit=limit, offset=offset)
        cycles = [
            entry.get("cycle") for entry in window["entries"]
            if entry.get("cycle") is not None
        ]
        if cycles:
            low, high = min(cycles), max(cycles)
            picked = [
                line for line in lines
                if line.get("cycle") is not None and low <= line["cycle"] <= high
            ]
        else:
            picked = []
    return dict(payload, lines=picked, totalLines=len(lines))


def page_etag(base_etag, descriptor):
    """A slice's own etag, derived from the whole payload's.

    It has to differ per window or a client that has just asked for forty
    entries gets a 304 against the twenty it already had. Derived rather
    than recomputed over the slice because the base etag already covers
    every byte the slice can contain, and hashing 187KB per request to
    learn that would be paying twice.
    """
    digest = hashlib.sha256((base_etag + "|" + descriptor).encode("utf-8")).hexdigest()[:16]
    return 'W/"' + digest + '"'


def journal_descriptor(page, limit, offset, cycle):
    """What `/api/journal`'s etag must vary by, beyond the payload itself.

    The window, obviously -- a client that just asked for forty entries
    must not be handed a 304 against the twenty it had.

    And the silence, which is the one that is easy to miss and was.
    `stalled` is judged per request against the clock, but the journal
    content it is judged *from* does not change while the loop is quiet --
    that silence is precisely the failure being reported. So the base etag
    is byte-identical across a stall, and a client polling with
    `If-None-Match` would be answered 304 for as long as the stall lasted:
    the warning would render only in a tab opened *after* the loop died,
    and never in the one already sitting open on Edvard's phone, which is
    the case the feature exists for. Folding the interval count in means
    the etag turns over at each hour boundary, which is exactly when the
    answer changes and no more often.
    """
    window = f"cycle={cycle}" if cycle is not None else f"{offset}:{limit}"
    return f"{window}|silent={(page.get('status') or {}).get('silentIntervals')}"


def _int_param(query, name, default):
    """A non-negative int from the query string, or `default`.

    A limit larger than the journal is not an error and needs no ceiling:
    the slice is bounded by the number of entries that exist.
    """
    values = query.get(name)
    if not values:
        return default
    try:
        value = int(values[0])
    except ValueError:
        return default
    return value if value >= 0 else default


class NovaSiteHandler(BaseHTTPRequestHandler):
    server_version = "nova-site"

    def log_message(self, *args):  # quiet default request logging
        pass

    def _send(self, status, body, content_type, etag=None):
        if isinstance(body, str):
            body = body.encode("utf-8")

        compressible = content_type.startswith(COMPRESSIBLE_TYPES)
        encoded = None
        if compressible and len(body) >= MIN_COMPRESS_BYTES:
            if accepts_gzip(self.headers.get("Accept-Encoding")):
                # mtime=0 rather than the default: gzip stamps the current
                # time into its header, so the same bytes would otherwise
                # produce a different response every second.
                encoded = gzip.compress(body, COMPRESS_LEVEL, mtime=0)

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        if etag:
            self.send_header("ETag", etag)
        if compressible:
            # Sent whether or not this particular response was compressed:
            # it is a statement that the body *varies* by the request
            # header, which is what stops a shared cache handing a gzipped
            # body to a client that never asked for one.
            self.send_header("Vary", "Accept-Encoding")
        if encoded is not None:
            self.send_header("Content-Encoding", "gzip")
            body = encoded
        # After the swap, so this is the length of what actually goes on
        # the wire -- including for HEAD, which sends the header and no
        # body and must still describe the GET it stands in for.
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send(status, json.dumps(payload), "application/json")

    def _send_cached_json(self, name, build):
        """A cached payload, as a 304 when the client already has it.

        The client polls for new entries, so most of these requests are a
        reader asking whether anything changed. Answering that with 160KB
        is what makes polling expensive enough to talk yourself out of.
        """
        _, body, etag = cached_payload(name, build)
        self._send_json_or_304(body, etag)

    def _send_journal(self, query):
        """`/api/journal`, sliced to the window the client asked for."""
        payload, _, base = cached_payload("journal", journal_payload)
        cycle = _int_param(query, "cycle", None)
        limit = _int_param(query, "limit", None)
        offset = _int_param(query, "offset", 0)
        page = journal_page(payload, limit=limit, offset=offset, cycle=cycle)
        etag = page_etag(base, journal_descriptor(page, limit, offset, cycle))
        # The version travels inside the document as well as in the header,
        # for the reason `_versioned` puts it in both: a response served out
        # of the service worker's cache has no headers the page can read.
        page["version"] = etag
        self._send_json_or_304(json.dumps(page), etag)

    def _send_digest(self, query):
        """`/api/digest`, cut to the cycles the feed is showing.

        Takes the same window parameters as `/api/journal` and is asked
        for with the same ones, so the page never has to wait for the feed
        before it can ask for the summaries -- both requests go out
        together on a cold load, as they always have.
        """
        payload, _, base = cached_payload("digest", digest_payload)
        cycle = _int_param(query, "cycle", None)
        limit = _int_param(query, "limit", None)
        offset = _int_param(query, "offset", 0)
        journal = None
        if cycle is None and limit is not None:
            journal, _, journal_etag = cached_payload("journal", journal_payload)
            # The window is resolved out of the journal, so the answer can
            # change while the digest file does not -- an addendum written
            # anywhere in the window pushes its oldest cycle out and pulls
            # another one in. Keyed on the digest alone, a poll in that gap
            # gets a 304 and the newly-visible card renders with no summary
            # until the next digest write, up to an hour later.
            base = base + "|" + journal_etag
        page = digest_page(payload, journal, limit=limit, offset=offset, cycle=cycle)
        etag = page_etag(base, f"cycle={cycle}" if cycle is not None else f"{offset}:{limit}")
        page["version"] = etag
        self._send_json_or_304(json.dumps(page), etag)

    def _send_board(self, query):
        """`/api/board?name=issues` -- one backlog page (issues.md #57).

        `name` indexes `BOARD_PATHS`, whose values are dicts of literal paths;
        nothing a request carries ever addresses a vault document, which
        is the same rule the capture box follows. An unknown name is a
        400 rather than an empty board, because it can only be a bug in
        the page or someone poking at the API by hand.
        """
        name = (query.get("name") or ["issues"])[0]
        if name not in BOARD_PATHS:
            self._send_json(400, {"error": f"name must be one of {sorted(BOARD_PATHS)}"})
            return
        payload, _, base = cached_payload(
            "board:" + name, lambda: board_payload(name)
        )
        item = _int_param(query, "item", None)
        limit = _int_param(query, "limit", None)
        page = board_page(payload, limit=limit, item=item)
        etag = page_etag(base, f"item={item}" if item is not None else f"limit={limit}")
        page["version"] = etag
        self._send_json_or_304(json.dumps(page), etag)

    def _send_json_or_304(self, body, etag):
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            # A 304 must carry the headers that would decide which cached
            # representation it validates, and the body varies by encoding.
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return
        self._send(200, body, "application/json", etag=etag)

    def _send_static(self, filename):
        path = os.path.join(PUBLIC_DIR, filename)
        try:
            with open(path, "rb") as handle:
                body = handle.read()
        except OSError:
            self._send_json(404, {"error": "not found"})
            return
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if content_type.startswith("text/") or filename.endswith((".js", ".webmanifest")):
            content_type += "; charset=utf-8"
        self._send(200, body, content_type)

    def do_GET(self):
        path, _, raw_query = self.path.partition("?")
        path = path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(raw_query)

        # `/cycle/49` is a real URL so an Agora reply can link straight at
        # one entry (item 4). The server has no per-cycle view -- it serves
        # the same shell and app.js reads the path -- but the URL must
        # resolve, or the link is dead on a cold load.
        if path == "/" or path.startswith("/cycle/") or path in ("/issues", "/ideas", "/costs"):
            self._send_static("index.html")
            return
        if path in STATIC_ROUTES:
            self._send_static(STATIC_ROUTES[path])
            return
        try:
            if path == "/api/journal":
                self._send_journal(query)
                return
            if path == "/api/digest":
                self._send_digest(query)
                return
            if path == "/api/board":
                self._send_board(query)
                return
            if path == "/api/costs":
                self._send_cached_json("costs", costs_payload)
                return
            if path == "/api/comments":
                # Deliberately not cached. It is 6KB and 20-78ms against
                # the live pod, so there is nothing here to save -- and it
                # is the one payload that changes underneath itself, from
                # the reply worker in this same process and from the box
                # that has just posted. A stale window on this endpoint
                # would buy nothing and cost a comment looking lost.
                self._send_json(200, comments_payload())
                return
            if path == "/api/health":
                # Never cached, and that is the entire point of it. The
                # thing this answers -- "which database did you resolve,
                # and can you reach it" -- is asked precisely when a
                # config flip has just happened, so an answer up to
                # CACHE_FRESH_SECONDS old is the wrong answer at exactly
                # the moment it matters.
                health = database_health()
                unreachable = [
                    role for role, db in health["databases"].items() if not db["reachable"]
                ]
                health["ok"] = not unreachable
                self._send_json(200 if health["ok"] else 503, health)
                return
        except Exception as e:
            log(f"nova-site {path} failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        self._send_json(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def _read_json_body(self):
        """Body -> dict, or None having already sent the error response.

        The length is checked *before* the read, not after: `rfile.read(n)`
        allocates whatever Content-Length claims, and this pod's memory
        limit is 256Mi.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0:
            self._send_json(411, {"error": "a Content-Length is required"})
            return None
        if length > MAX_BODY_BYTES:
            self._send_json(413, {"error": f"body over {MAX_BODY_BYTES} bytes"})
            return None
        # Not a formality -- see the CSRF note in the module docstring.
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "expected a JSON object"})
            return None
        return payload

    def _post_amend(self, payload, delete):
        """`/api/capture/edit` and `/api/capture/delete` -- issues.md #66.

        *"The reported issues should be able to be edited and deleted by
        me."* Same boundaries as the capture box: `target` is checked
        against CAPTURE_TARGETS, both texts must be strings, and nothing a
        client sends addresses a document.

        **Two routes rather than one endpoint with an empty `text`.**
        Deleting is the destructive half and it should be impossible to
        reach by accident -- an edit that arrives with its text field
        somehow blank is answered as a bad request here, not quietly
        carried out as a delete. The two share one implementation because
        the vault write genuinely is the same read-modify-write; it is the
        *request* that must be unambiguous, not the code underneath it.

        A stale `original` -- the capture already boarded, or edited from
        Obsidian -- is a 409, not a 502. Nothing failed; the thing being
        addressed moved, and the page should re-read rather than retry.
        """
        target = payload.get("target")
        index = payload.get("index")
        original = payload.get("original")
        text = "" if delete else payload.get("text")
        if target not in CAPTURE_TARGETS:
            self._send_json(400, {"error": f"target must be one of {sorted(CAPTURE_TARGETS)}"})
            return
        if not isinstance(original, str) or not original.strip():
            self._send_json(400, {"error": "original must be a non-empty string"})
            return
        # `True` is an int in Python and would silently address capture 1.
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            self._send_json(400, {"error": "index must be a non-negative number"})
            return
        if not delete:
            if not isinstance(text, str):
                self._send_json(400, {"error": "text must be a string"})
                return
            if not clean_capture_text(text):
                self._send_json(400, {"error": "nothing to save"})
                return

        try:
            ok, message = amend(target, index, original, text)
        except Exception as e:
            log(f"nova-site capture amend failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            # Exactly as for a new capture: the board Edvard is looking at
            # has gone stale and `app.js` reloads on the next tick.
            invalidate("board:" + target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"{'Delete' if delete else 'Edit'} in {target} · {'ok' if ok else message}",
            before=original[:MAX_BODY_BYTES],
            after=text[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        stale = "no longer" in message
        self._send_json(409 if stale else 502, {"ok": False, "message": message})

    def _post_comment(self, payload):
        """`/api/comment` -- Edvard replying to one cycle (ideas.md #44).

        `{"target": "needs"}` instead of a `cycle` answers the digest's
        Needs Edvard block (2026-08-10) -- see `nova_comments`.

        The same two boundaries as the capture box, and the same reason
        they hold: the tailnet authenticates, and the endpoint's shape
        authorizes. `cycle` is coerced to an int, `target` is checked
        against a one-value allow-list, and `text` must be a string, so
        nothing a client sends addresses a document -- the path is the
        module-level COMMENTS_PATH constant and there is no target to
        choose. The worst a request can do is add a comment to a file Nova
        reads and Edvard can delete.
        """
        cycle = payload.get("cycle")
        target = payload.get("target")
        text = payload.get("text")
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return
        if target is not None:
            if target != "needs":
                self._send_json(400, {"error": "target must be 'needs'"})
                return
            if not clean_comment_text(text):
                self._send_json(400, {"error": "nothing to comment"})
                return
            self._store_comment(lambda: add_needs_comment(text), text, "Needs Edvard")
            return
        # `True` is an int in Python and would silently become cycle 1.
        if isinstance(cycle, bool) or not isinstance(cycle, (int, str)):
            self._send_json(400, {"error": "cycle must be a number"})
            return
        try:
            cycle = int(cycle)
        except ValueError:
            self._send_json(400, {"error": f"cycle must be a number, got {cycle!r}"})
            return
        if cycle < 0:
            self._send_json(400, {"error": "cycle must not be negative"})
            return
        if not clean_comment_text(text):
            self._send_json(400, {"error": "nothing to comment"})
            return

        # The stamp is minted here rather than inside `add_comment` because
        # it is this comment's identity: it is what the reply worker uses
        # to find the comment again, and a second call to `format_stamp`
        # can land in the next minute.
        stamp = format_stamp()
        if self._store_comment(lambda: add_comment(cycle, text, stamp), text, f"cycle {cycle}"):
            # Only cycle comments get a reply. A `Needs Edvard` answer is a
            # decision for a cycle to act on, not a conversation -- replying
            # to it would put a paragraph where a piece of work belongs.
            enqueue_reply(cycle, stamp)

    def _store_comment(self, store, text, label):
        """Write one comment and audit it, whichever target it names. -> ok.

        `store` is a no-argument callable so this stays ignorant of which
        writer it is driving and what that writer's signature looks like;
        `text` and `label` are only ever used to describe the write in the
        audit trail.

        Every bad request is answered by the caller, so anything `store`
        rejects from here is the vault failing rather than the client
        asking for something wrong -- which is what makes 502 correct
        below without having to read the failure message to decide.
        """
        try:
            ok, message = store()
        except Exception as e:
            log(f"nova-site comment failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return False

        audit(
            "Nova",
            "",
            "nova_comment",
            f"Comment on {label} · {'ok' if ok else message}",
            after=text[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})
        return ok

    def _handle_mcp(self):
        """One MCP JSON-RPC request from the reply turn's own CLI session.

        The runner serves the identical endpoint (invoke_server.py) for
        persona turns; both delegate to tools_mcp.handle_http so there is
        one implementation of the auth and envelope rules rather than two
        that can drift.

        This is *not* covered by `_read_json_body`: that helper enforces
        `Content-Type: application/json` as a CSRF defence for the browser
        endpoints, and the caller here is the Claude CLI in another pod,
        not a browser. What guards it instead is the bearer token, which
        is minted per turn and revoked when the turn ends -- a request
        without a live grant gets a 401 and reaches no tool.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(400, {"error": "bad content-length"})
            return
        try:
            status, payload = handle_mcp_http(
                self.headers.get("Authorization", ""), self.rfile.read(length)
            )
        except Exception as e:
            log(f"nova-site /mcp failed: {e}")
            self._send_json(500, {"error": str(e)[:300]})
            return
        if payload is None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send_json(status, payload)

    def do_POST(self):
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/mcp":
            self._handle_mcp()
            return
        if path not in (
            "/api/capture", "/api/capture/edit", "/api/capture/delete", "/api/comment"
        ):
            self._send_json(404, {"error": "not found"})
            return

        payload = self._read_json_body()
        if payload is None:
            return
        if path == "/api/comment":
            self._post_comment(payload)
            return
        if path in ("/api/capture/edit", "/api/capture/delete"):
            self._post_amend(payload, delete=path.endswith("delete"))
            return
        target = payload.get("target")
        text = payload.get("text")
        if target not in CAPTURE_TARGETS:
            self._send_json(400, {"error": f"target must be one of {sorted(CAPTURE_TARGETS)}"})
            return
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return

        try:
            ok, message = capture(target, text)
        except Exception as e:
            log(f"nova-site capture failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            # The board page Edvard is looking at has just gone stale, and
            # `app.js` reloads it on the very next tick. Without this the
            # reload is served the pre-capture payload -- see `invalidate`.
            # `board:notes` never exists (notes have no board page yet) and
            # popping a missing key is a no-op, so this needs no special
            # case and cannot forget a target that grows one later.
            invalidate("board:" + target)

        # Recorded whether or not it succeeded, and the Tailscale identity
        # headers go in as evidence rather than as a check -- nothing here
        # trusts them yet. A future cycle reading real values in the
        # Activity feed is what would justify tightening the boundary.
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Capture to {target} · {'ok' if ok else message}",
            after=text[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})


def start_nova_site():
    server = ThreadingHTTPServer(("0.0.0.0", NOVA_PORT), NovaSiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log(f"nova site listening on :{NOVA_PORT}")
    # After the socket is being served, never before: the readiness probe
    # must be answerable while this runs, or a warm that takes six seconds
    # is six seconds of the pod looking dead rather than six seconds saved.
    threading.Thread(target=warm_cache, name="nova-site-warm", daemon=True).start()
    return server
