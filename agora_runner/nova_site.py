"""Nova's own site: the journal, styled, on the tailnet.

Agora ideas.md #34 -- items 1-4 (journal timeline, status header, digest
strip, per-cycle deep links) and item 6, the capture box.

**The two writes, and where their boundary actually is.** Everything here
was GET until the capture box; `POST /api/capture` and `POST /api/comment`
(ideas.md #44) are the only routes in this module that change anything.
Their safety is not the tailnet alone -- it is that both endpoints are too
narrow to misuse:

- The tailnet is the *authentication* boundary. Reaching this port at all
  means being on the owner's tailnet, which in practice means his own
  devices. A shared token would have to live in the served JavaScript or
  be typed on a phone, so it would add real friction and no real secrecy.
- The endpoint's shape is the *authorization* boundary, and it is what is
  actually load-bearing. `target` indexes a two-entry dict of literal
  paths (nova_capture.CAPTURE_TARGETS); `/api/comment` does not even take
  one, writing only to nova_comments.COMMENTS_PATH. No path, no marker and
  no position ever comes from the client in either. The worst a request
  can do is add a bullet or a comment to a file the owner reads and can
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
every load. The owner reported it as the app taking a long time to load.
See `cached_payload`.

**Responses are gzipped when the client asks.** Measured against the
live pod on 2026-08-10, a cold load of this site was 588,998 bytes and
none of it was compressed -- `/api/journal` alone was 453,239 -- while
every browser that fetched it was already sending
`Accept-Encoding: gzip, deflate, br, zstd` and getting nothing back.
Compressed, the same load is 154,726 bytes. This is the largest payload
anywhere in this system and it is the page the owner reads on a phone.

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
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agora_runner.audit import audit
from agora_runner.config import NOVA_CYCLE_HEARTBEAT_ID, NOVA_PORT, OSLO
from agora_runner.log import log
from agora_runner.nova_uploads import (
    MAX_UPLOAD_BYTES,
    UploadRejected,
    is_image,
    read_upload,
    store_upload,
)
from agora_runner.nova_capture import (
    CAPTURE_TARGETS,
    MAX_BODY_BYTES,
    STALE_CAPTURE,
    amend,
    capture,
    clean_capture_text,
    comment_on_capture,
    comment_on_row,
    convert_capture,
    edit_row,
    remove_row,
    set_priority,
)
from agora_runner.nova_comments import (
    add_comment,
    add_needs_comment,
    clean_comment_text,
    comments_by_cycle,
    format_stamp,
    needs_comments,
)
from agora_runner.cycle_number import cycle_starts
from agora_runner.nova_journal import (
    build_status,
    parse_digest,
    parse_journal,
    render_blocks,
    with_start_times,
)
from agora_runner.nova_replies import (
    WAITING_AFTER_SECONDS,
    enqueue as enqueue_reply,
    failed as failed_replies,
    pending_since,
    recover as recover_replies,
)
from agora_runner.nova_boards import (
    BOARD_PATHS,
    PRIORITY_LABELS,
    canonical_priority,
    parse_board,
    parse_notes,
    priority_key,
    split_capture_done,
    split_capture_priority,
)
from agora_runner.nova_conversations import (
    conversations as conversation_list,
    create as conversation_create,
    personas as conversation_personas,
    send as conversation_send,
    thread as conversation_thread,
)
from agora_runner.nova_ask import (
    ask as ask_question, thread as ask_thread, watching as ask_watching,
)
from agora_runner.nova_idea_pool import (
    STALE_CANDIDATE,
    decide as pool_decide,
    history_payload as pool_history,
    pool_payload,
    request_generate as pool_request_generate,
)
from agora_runner.nova_notes import notes_payload
from agora_runner.nova_costs import costs_payload as shape_costs
from agora_runner.nova_plan import plan_payload as shape_plan
from agora_runner.nova_retro import retros_payload as shape_retros
from agora_runner.nova_runtimes import attach_runtimes
from agora_runner.nova_sources import (
    board_markdown,
    comments_markdown,
    cost_ledger_json,
    digest_markdown,
    journal_markdown,
    plan_markdown,
    goal_history_json,
    retro_ledger_json,
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
    # Apache ECharts 5.5.1, Apache-2.0, vendored rather than pulled off a
    # CDN: this app is served over a tailnet and is meant to survive a dead
    # link, and a CDN script tag is a chart page that goes blank when the
    # phone is off the internet. `app.js` loads it lazily on the first
    # chart, so the 1.0 MB is not in the shell's critical path.
    "/vendor/echarts.min.js": os.path.join("vendor", "echarts.min.js"),
    # Mermaid 11.17.2, MIT, vendored for the same reason ECharts is: a CDN
    # script tag would make a diagram in the chat go blank the moment the
    # phone is off the internet. It is 3.5 MB, three and a half times the
    # chart library, so `app.js` loads it only when a ```mermaid block
    # actually appears in a message and `sw.js` deliberately leaves it out
    # of the install-time precache.
    "/vendor/mermaid.min.js": os.path.join("vendor", "mermaid.min.js"),
}

# The page routes the server answers with the SPA shell. A module
# constant rather than a literal inside `do_GET` because `site_check`
# reads it: a smoke check that hand-copies this list stops testing the
# server the moment someone adds a route to one copy and not the other,
# and it would report success while doing it.
#
# `/cycle/<n>` is a prefix rather than an exact path, so it is matched
# separately in `do_GET` and carries a representative path here for
# anything walking the list.
PAGE_ROUTES = (
    "/",
    "/issues",
    "/ideas",
    "/notes",
    "/pool",
    "/costs",
    "/retro",
    "/plan",
    "/ask",
    "/conversations",
    "/diag",
)
PAGE_ROUTE_PREFIXES = ("/cycle/",)

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
    # The card's time is when the cycle *woke*, not when it filed -- the
    # owner's capture, 2026-08-24: *"I want the time slot on the journals to
    # be when they started, as it seems to show when they ended."* `times`
    # is the vault document's write time and a cycle writes last, so a card
    # was reading up to 45 minutes late; the Agora conversation each cycle
    # runs inside is created before the session opens and carries the other
    # end. See `nova_journal.with_start_times` for why neither of the two
    # stamps already here could answer this.
    #
    # A second fetch on this build, paid alongside the ledger below and for
    # the same reason: this payload is cached per process and warmed before
    # the first visit. Failure is `{}` inside `cycle_starts`, which
    # `with_start_times` reads as "keep every write time" -- so an
    # unreachable Agora costs this page its precision and nothing else.
    # `stamps` deliberately does not replace `times`: `build_status` below
    # reads `times.keys()` as the answer to "which cycles wrote an entry",
    # and that must stay keyed on filenames rather than on conversations.
    stamps = with_start_times(times, cycle_starts(NOVA_CYCLE_HEARTBEAT_ID))
    # `journal_markdown` always hands back an entries body -- no preamble
    # from either source -- so there is nothing to strip, and asking the
    # parser to look for one lets an entry that quotes `## Entries` in its
    # prose cut every newer entry off the front of the feed.
    entries = parse_journal(markdown, stamps, written_by_cycle=times)
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
    entries = [dict(entry) for entry in entries]
    # The one extra fetch on this build, and it is paid where the journal
    # build already is: this payload is cached per process and warmed
    # before the first visit, so the ledger costs a warm rather than a
    # request rather than being re-read per window a reader scrolls to.
    #
    # Swallowed on purpose, and the first draft did not: `cycle_runtimes`
    # raises on a ledger that will not parse, which is right for the costs
    # page -- there, a ledger is the entire subject and an empty chart is
    # a worse lie than a 502. Here it is a decoration on somebody else's
    # page. Wiring the strict version straight in made a malformed file in
    # a *different* document take down the journal, which is the one page
    # that has to render when things are broken; the whole test suite said
    # so at once. An absent ledger was already no runtimes rather than an
    # error, so this only widens that to a corrupt one.
    #
    # `Exception` and not a tuple, which is a wider net than this file uses
    # anywhere else, so it needs its reason: the *fetch* can fail on its own
    # too, not just the parse. Narrowing it to the parse errors left the
    # background refresh thread dying on a ledger read -- one test's thread
    # warning, and in production a journal that silently stopped refreshing
    # because a document it does not render was unreadable. There is no
    # failure of this call worth a degraded journal, so there is no
    # exception type worth re-raising. It is logged, never swallowed
    # silently, and `journal_markdown` above is still free to raise.
    try:
        entries = attach_runtimes(entries, cost_ledger_json())
    except Exception as problem:  # noqa: BLE001 -- deliberate, see above
        # `log`, not `print`: it flushes. Nothing sets PYTHONUNBUFFERED and
        # `run.py` has no `-u`, so a bare print to a container's stdout is
        # block-buffered and can sit unwritten in the background refresh
        # thread -- which would make "logged, never silent" false in exactly
        # the case this catch exists for. Every other handler in this file
        # already uses it.
        log(f"nova-site runtimes unavailable, journal unaffected: {problem}")
    return {"entries": entries, "status": status}


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
    return parse_digest(digest_markdown())


def comments_payload():
    """Every comment, grouped by the cycle it is about.

    Keyed by cycle number as a string because that is what JSON object keys
    are; the client looks up `byCycle[String(entry.cycle)]`. The comment
    text is sent as plain text rather than rendered blocks -- unlike the
    journal, this is the owner's own prose and nothing here interprets it as
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
            # written, over it something is holding it up. Saying "Nova is
            # replying…" for forty minutes is what the owner reported as the
            # conversation not working at all.
            #
            # What this cannot see is *why* it is held up, and the card used
            # to assert one anyway -- "Queued behind a running cycle". The
            # bridge only serialises a reply behind a cycle when the OAuth
            # refresh window is close (bridge `allow_concurrent` /
            # `refresh_window_clear`, a 15-minute margin on a roughly
            # 8-hourly refresh), so the stated cause is wrong the large
            # majority of the time and nothing here is measuring it. The
            # elapsed second is the thing this server actually knows, so it
            # is what goes out; the card reports the wait and names no cause.
            comment["replyWaiting"] = (
                asked_at is not None and (now - asked_at) >= WAITING_AFTER_SECONDS
            )
            # Clamped, because both ends of that subtraction are `time.time()`
            # and a wall clock can step backwards -- an NTP correction between
            # `enqueue` and this read would put a negative number on the wire.
            # The one consumer today happens to treat that as "a moment", so
            # this is about the payload being honest on its own rather than
            # about the current card: a wait cannot have lasted -50 seconds.
            comment["replyWaitingSeconds"] = (
                max(0, int(now - asked_at)) if asked_at is not None else 0
            )
            # And when it is not coming at all, say so rather than letting
            # the line disappear as if the answer had arrived.
            comment["replyFailed"] = asked_at is None and key in gave_up
    return {
        "byCycle": {str(cycle): items for cycle, items in grouped.items()},
        # Replies to the digest's Needs Edvard block, which belong to no  (not-prose: quoting a literal)
        # cycle and so cannot ride in `byCycle`.
        "needs": needs_comments(markdown),
    }


def _capture_replies(board, index):
    """The cycle replies under capture `index`, or `[]`.

    Tolerant of a board dict that predates `captureReplies` -- the page
    should lose the answers, never the owner's own bullets.
    """
    replies = (board.get("captureReplies") or [])
    return replies[index] if index < len(replies) else []


def _capture_parts(text):
    """One raw capture bullet -> `(done, priority, body)`.

    Both markers are prefixes on the same line, and the order matters:
    a closed bullet reads `DONE (Cycle 247): High: the original text`,
    so reading the rating first sees `D` and reports the capture unrated.
    Strip the done marker, then hand what is left to the rating matcher.
    """
    done, rest = split_capture_done(text)
    priority, body = split_capture_priority(rest)
    return done, priority, body


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
    # My own two files get parsed as a board as well as a note stream --
    # issue #97, the half of it the owner kept: *"making your board like
    # mine and giving yourself more tidiness is an improvement"*. Until
    # now `parse_board` was only ever pointed at his file, so my side of
    # the page could only ever be a flat bullet list and there was no way
    # to say a thing I filed was open, rated, or finished.
    #
    # The notes stream is untouched and stays underneath. That is the
    # design rather than a step on the way to migrating it: 654 issue
    # bullets and 221 idea bullets are a log, and a board built out of all
    # of them would be a worse board than none. A cycle boards the few
    # that are real work; the rest stay history.
    mine = parse_board(nova_markdown)
    # Rendered inline rather than fetched per row, which is the opposite
    # of what `details` above does and is deliberate. His `# Details` is
    # ~60KB and is stripped from the list for that reason; mine is 0 bytes
    # today and every row on it will have been put there by a cycle that
    # judged it worth tracking. When it grows past a page it takes the
    # same treatment -- `board_page` is where that would go.
    nova_details = {
        str(number): render_blocks(body) for number, body in mine["details"].items()
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
        # `text` stays the raw bullet, rating glyph and all, because
        # `nova_capture.replace_capture` matches an edit or a delete on
        # exactly this string. The split is presentational: `body` is what
        # the card shows and `priority` is the chip beside it.
        # `done` is the cycle that closed it, or "". `prompt.md` step 6
        # asks a cycle to prefix a capture it finished with `DONE (Cycle
        # N):` and nothing had ever read that, so every closed bullet
        # went on rendering here under "Not boarded yet" -- at Cycle 251
        # all five captures on `issues.md` were finished work. Stripping
        # the marker out of `body` keeps it out of the card's prose; the
        # page paints it as a chip and sinks those cards below the open
        # ones.
        # `replies` are the cycle answers written under his bullet, each
        # rendered on its own so the page can draw them as separate
        # bubbles the way the notes page already does. They are
        # deliberately *not* part of `text`: `text` is the address an
        # edit, a delete or a rating sends back, and gluing my answer
        # onto it made every one of those writes fail with "no longer in
        # the list" (his capture, 2026-08-25).
        "captures": [
            {
                "text": text,
                "body": body,
                "priority": priority,
                "priorityKey": priority_key(priority),
                "done": done,
                "blocks": render_blocks(body),
                "replies": [render_blocks(reply) for reply in replies],
            }
            # Indexed rather than `zip`ped: `zip` stops at the shorter of
            # the two, so a board dict built without the replies list --
            # any older caller, or a test fixture -- would drop every
            # capture off the page instead of drawing them without
            # answers.
            for text, replies, (done, priority, body) in (
                (bullet, _capture_replies(board, position), _capture_parts(bullet))
                for position, bullet in enumerate(board["captures"])
            )
        ],
        "items": board["items"],
        "details": details,
        # One lowercased blob per row: its title plus its whole write-up,
        # in raw markdown. The owner, ideas.md #71: "Ability to search
        # through issues or ideas" -- and the first of the six kinds I
        # wrote back to him was free text over the *detail*, not just the
        # title, because a row title is four words and everything you
        # would actually search for is in the body. The page cannot do
        # that itself: `board_page` strips `details` from the list for
        # the same reason it always has (60KB of the 68KB file), so the
        # match has to happen on this side. Built here rather than per
        # request so a search costs a substring scan over a dict the
        # cache already holds, and stripped by `board_page` so it never
        # goes out with the list.
        "searchText": {
            str(item["number"]): (
                item["title"] + "\n" + board["details"].get(item["number"], "")
            ).lower()
            for item in board["items"]
        },
        "notes": notes,
        "novaItems": mine["items"],
        "novaDetails": nova_details,
        # The same blob, for my own rows. Built here rather than left out
        # because `board_page` windows `novaDetails` away on every list
        # request (runner#355), so from Cycle 407 onward the page holds my
        # row *titles* and none of my write-ups -- which is exactly the
        # position his rows have always been in, and the reason his search
        # happens on this side. Two dicts rather than one keyed by
        # `mine:<n>`, because his #1 and my #1 are both real rows and a
        # shared key space is the collision `board_page`'s `item` branch
        # already refuses.
        "novaSearchText": {
            str(item["number"]): (
                item["title"] + "\n" + mine["details"].get(item["number"], "")
            ).lower()
            for item in mine["items"]
        },
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


def retros_payload():
    """Every Friday retrospective, scores and prose (issues.md, 2026-08-13).

    Same shape of endpoint as `costs_payload` -- one fetch, one reshape,
    JSON in and JSON out -- and cached for a weaker reason than that one:
    this document changes once a *week*, not once an hour, so the cache
    is almost always serving something that cannot have moved.

    Not warmed at startup, unlike the journal and the digest. Warming
    buys back the cold build for the page a visitor lands on, and nobody
    lands here on a cold load: it is reached from the nav, by which time
    the process is warm. Spending a vault read at every process start for
    a page opened once a week is the wrong side of that trade.
    """
    return shape_retros(retro_ledger_json())


def plans_payload():
    """The roadmap and the goals, as one page (`issues.md` #7).

    Same shape as the two above -- one fetch, one reshape -- and cached
    for the same reason as `retros_payload` rather than `costs_payload`:
    these two documents are rewritten *when the reasoning changes*, which
    their own frontmatter says is not every cycle, so the cache is almost
    always serving something that cannot have moved.

    Not warmed at startup, for the reason the retro is not: nobody lands
    here cold. It is reached from the nav, by which time the process has
    long since served the journal.
    """
    return shape_plan(plan_markdown(), goal_history_json())


def board_page(payload, limit=None, item=None, search=None, mine=False):
    """One board, as the page actually asks for it.

    Four shapes, and the split is the whole point of this endpoint:

    - `item=57` -- one detail body. This is what a tap on a row fetches.
      `mine=True` asks the same question of my own board instead of his.
    - `search="badge"` -- just the numbers of the rows whose title or
      write-up contains that text (ideas.md #71). Nothing else: the page
      already holds every row, so the answer it is missing is only which
      of them match, and sending rows back would double the list.
    - `limit=n` -- the list: every row (they are one line each, 60 of
      them, ~5KB in total) plus the newest `n` of my own notes and no
      detail bodies at all.
    - neither -- everything, which is what an app.js served out of a
      service worker's cache from before this shipped would ask for.

    My notes are windowed and his rows are not because they are different
    sizes for a structural reason: a row is a title, a note is a
    paragraph. 294 notes in `nova/resources/issues.md` is 147KB and it
    grows by two or three every cycle; the rows are bounded by how many
    things the owner has ever filed.
    """
    if item is not None:
        # `mine=1` addresses my own board rather than his. A separate flag
        # rather than a wider number space, because his #1 and my #1 are
        # both real rows on the same page and answering one query with
        # whichever dict happened to hold the key is a collision waiting
        # for the first row I board.
        rows = payload.get("novaItems") if mine else payload.get("items")
        bodies = payload.get("novaDetails") if mine else payload.get("details")
        blocks = (bodies or {}).get(str(item))
        row = next((i for i in rows or [] if i.get("number") == item), None)
        return {
            "name": payload.get("name"),
            "item": dict(row or {"number": item}, blocks=blocks or []),
            "found": blocks is not None or row is not None,
        }
    if search is not None:
        needle = search.strip().lower()
        # `mine=1` searches my board instead of his, for the same reason
        # the `item` branch above takes the flag: the answer is a list of
        # row numbers and the two boards share a number space, so a search
        # that ignored the flag would answer the Nova tab with his #38.
        blobs = payload.get("novaSearchText" if mine else "searchText") or {}
        # An empty query matches nothing rather than everything. The page
        # only asks when the owner has typed something, so "" here is a bug
        # somewhere above, and answering it with all 71 rows would look
        # exactly like a working search.
        matches = (
            sorted(int(n) for n, text in blobs.items() if needle in text)
            if needle
            else []
        )
        return {"name": payload.get("name"), "query": needle, "matches": matches}
    notes = payload.get("notes") or []
    page = dict(payload, notes=notes if limit is None else notes[:limit])
    page["notesTotal"] = len(notes)
    # Never goes out with a page: it is every write-up on the board again,
    # lowercased, which is the exact payload `details` is stripped to
    # avoid. Dropped unconditionally rather than under `if limit`, because
    # the no-argument shape is the pre-#85 client and it has no use for it.
    page.pop("searchText", None)
    page.pop("novaSearchText", None)
    if limit is not None:
        page["details"] = {}
        # Mine goes out on the same terms as his. Reviewer finding on
        # runner#354: that PR shipped my write-ups inline on every load
        # with a comment saying they would take this treatment once they
        # grew, which is a decision nobody was ever going to come back
        # to. The rows stay -- they are one line each, and dropping those
        # would empty the tab rather than window it.
        page["novaDetails"] = {}
    return page


# How long a served payload may be before the next request kicks a
# refresh behind itself. Not a staleness budget for the owner -- the client
# polls, so what he sees is bounded by the poll interval plus one rebuild
# -- it is how often an *active* reader makes the site rebuild. At 15s a
# session polling every 30s rebuilds once per poll and never waits for one.
CACHE_FRESH_SECONDS = 15

# How old the served payload may be before "there is no newer entry in it"
# stops being evidence about the loop and becomes evidence about this
# process. `_with_silence` measures a live `now` against a `lastWrittenAt`
# that came out of the cache, so every second the refresh fails to land is
# counted as a second of the loop being quiet -- and past two intervals
# that is reported as a stall the loop is not in. Measured 2026-08-15:
# `nova-site-preview` had been serving `stalled: true, silentIntervals: 9`
# for hours off a payload built at 19:17 the previous evening, while the
# loop was writing an entry every hour.
#
# 300s is twenty consecutive missed refreshes at CACHE_FRESH_SECONDS. A
# reader is bounded by their poll interval plus one rebuild, so nothing
# healthy comes near it; anything past it means the rebuild itself is
# failing, which is a different fact and deserves to be said as one.
RECORD_TRUST_SECONDS = 300

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
    """`(payload, body, etag)` from `cached_entry`, without the age."""
    payload, body, etag, _ = cached_entry(name, build)
    return payload, body, etag


def cached_entry(name, build):
    """`(payload, body, etag, age_seconds)` -- the last build, served
    immediately while the next one is computed behind the request.

    The age belongs to *the copy this call returns*, which is why it comes
    back from here rather than from a second look at the cache. Reading it
    afterwards is a race, and one this cycle's own test caught: the
    background refresh started on the line above can land in between, so
    the caller would stamp a stale body with the fresh copy's age. That is
    the wrong direction to be wrong in -- the one thing the age is for is
    saying "do not trust the timestamps in this body".

    `/api/journal` costs 3.0-3.5s every time it is asked (measured against
    the live pod, 2026-08-10: 1.9s of vault bulk fetch, 1.5s of parsing,
    95 entries) and it was recomputed identically on every load. That is
    what the owner reported as "Nova takes a long time to load when i
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
        return entry[0], entry[1], entry[2], max(0.0, now - entry[3])
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
            return entry[0], entry[1], entry[2], max(0.0, time.time() - entry[3])
        # Built for this request, so its age is zero rather than unknown.
        return _refresh(name, build) + (0.0,)


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
    first-ever reader, it is the owner, on his phone, on the next visit after
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

    The owner, `issues.md` 2026-08-12: *"When i create a new issues, the
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


def _invalidate_capture_target(target):
    """Drop whichever cached page shows captures for `target`.

    Two of the three capture files are boards and cache under
    `board:<name>`; `notes.md` is not a board and caches under `notes`,
    and `board:notes` has never existed. The new-capture path in
    `_post_capture` has said both halves since notes got a page of their
    own; the edit and delete path said only the first, so a note edited or
    deleted from the app left `/notes` serving the copy from before the
    write -- the same deterministic staleness `invalidate` above was built
    for, in the one place a second caller had to remember it. One function
    now, called by everything that writes a capture file.
    """
    invalidate("board:" + target)
    if target == "notes":
        invalidate("notes")


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
    # Same reason, separate lock: a cadence fetched by one test is the
    # stale value the next one reads, and unlike a payload it is a single
    # number with no name to key an assertion off, so the leak would show
    # up as an unrelated badge assertion failing.
    reset_cadence()


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


def journal_page(payload, limit=None, offset=0, cycle=None, now=None,
                 record_age=None, search=None):
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

    `search` is the owner's capture, issues.md 2026-08-25: *"I want to be
    able to search through journals. Give me a button or a input field
    somewhere."* It is matched here rather than in the page for the same
    reason the board's write-up search is: the text he would actually
    search for is the entry prose, and the prose is the part the feed
    never sends -- twenty entries arrive on a cold load out of 400-odd, so
    a client-side filter could only ever search the fifth of the archive
    already on screen, which is the fifth he can already see. `body` is
    the raw markdown and stays behind in the cached entry (`_rendered`
    drops it), so this is a substring scan over text the process is
    holding anyway.

    A search ignores `cycle` and `offset` and treats `limit` as a cap on
    how many matches come back, because "the newest N entries containing
    X" is the only window a search box asks for. `total` stays the number
    of matches, so the page can say how many there were even when it was
    handed fewer.
    """
    entries = payload.get("entries") or []
    if search is not None and search.strip():
        needle = search.strip().lower()
        matched = [
            entry for entry in entries
            if needle in (entry.get("title") or "").lower()
            or needle in (entry.get("body") or "").lower()
        ]
        picked = matched if limit is None else matched[:limit]
        return {
            "entries": [_rendered(entry) for entry in picked],
            "status": _with_silence(
                payload.get("status", {}), now, record_age=record_age
            ),
            "total": len(matched),
            # Echoed back so a slow answer for "cycle" cannot be shown as
            # the result for "cycle number" -- the same guard the board
            # search carries, and for the same reason: this is typed into,
            # so every keystroke has a request in flight behind it.
            "query": needle,
        }
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
        "status": _with_silence(payload.get("status", {}), now, record_age=record_age),
        "total": len(entries),
    }


# How long a fetched cadence is served before a background refresh is
# started. The owner changes the cadence by hand in Agora, not by deploying,
# so this process cannot wait for a restart to notice -- but the value it
# is tracking changes a few times a week at most, so anything shorter than
# minutes would be a poll loop against Agora dressed up as a cache.
CADENCE_FRESH_SECONDS = 300
# ((minutes-or-None, lastRunAt-or-None, lastResult-or-None), fetched_at)
# once anything has been fetched. The name is older than the payload: it
# held the cadence alone until the run state joined it off the same fetch.
_cadence = None
_cadence_lock = threading.Lock()
_cadence_refreshing = False


def _refresh_cadence():
    global _cadence, _cadence_refreshing
    try:
        from agora_runner.cycle_health import nova_heartbeat_snapshot

        snapshot = nova_heartbeat_snapshot()
    except Exception as e:
        # Never raised at a reader. A page that cannot render because the
        # freshness of one badge could not be established is a worse
        # answer than a badge judged against the fallback, which is
        # exactly what this function did for its whole life before now.
        log(f"nova-site: heartbeat lookup failed: {e}")
        snapshot = (None, None, None)
    with _cadence_lock:
        _cadence = (snapshot, time.time())
        _cadence_refreshing = False


def cadence_minutes():
    """The interval `_with_silence` measures in -- live if known, else the constant.

    **Never blocks and never touches the network on the request path.**
    The first caller of a process gets `HEARTBEAT_MINUTES` and starts a
    background fetch; every caller after the fetch lands gets the real
    cadence, and a stale value is served while it is re-fetched. That is
    the same stale-while-revalidate bargain as `cached_payload`, for the
    same reason: this is called on every `/api/journal`, and a reader
    should never wait on Agora to find out whether a badge is red.

    Serving the fallback cold is not a compromise, it is the status quo --
    `HEARTBEAT_MINUTES` is what this measured in unconditionally until
    now, and it has been wrong twice, because the cadence is the owner's to
    change and he has changed it four times since 2026-08-08. The lookup
    itself is `cycle_health.nova_cadence_minutes`, shared with the copy
    that talks to Nova; what belongs here is only the caching, because
    only this side is on a request path.
    """
    from agora_runner.cycle_health import HEARTBEAT_MINUTES

    return heartbeat_snapshot()[0] or HEARTBEAT_MINUTES


def heartbeat_snapshot():
    """`(minutes, lastRunAt, lastResult)` from the cache, refreshed behind it.

    The caching half of `cycle_health.nova_heartbeat_snapshot`, split out
    of `cadence_minutes` when the same fetch gained a second reader --
    `_with_silence` needs the run state for the "running now" badge, and a
    second `/heartbeats` call on the request path to get it would be a
    network hop bought for a field the first call already returned.

    Same stale-while-revalidate bargain as before, unchanged: the first
    caller of a process gets `(None, None, None)` and starts a background
    fetch. Every field's caller must therefore treat `None` as "not known
    yet" rather than as an answer -- `cadence_minutes` falls back to the
    constant, and `_running_now` declines to claim a cycle is running,
    which is the safe direction: a running cycle unreported for one
    request is the status quo, and a *dead* cycle reported as running is
    the false reassurance #72 is about.
    """
    global _cadence_refreshing

    now = time.time()
    with _cadence_lock:
        entry = _cadence
        stale = entry is None or now - entry[1] >= CADENCE_FRESH_SECONDS
        if stale and not _cadence_refreshing:
            _cadence_refreshing = True
            thread = threading.Thread(
                target=_refresh_cadence, name="nova-site-cadence", daemon=True
            )
        else:
            thread = None
    if thread is not None:
        try:
            thread.start()
        except RuntimeError as e:
            # The in-flight flag is set inside the lock, before the thread
            # exists, so nothing else clears it if the thread never runs --
            # and the wedge is permanent and silent: the badge would be
            # frozen on the fallback for the life of the process with
            # nothing in the logs after this line. `start` raises when the
            # OS refuses a thread, which is memory pressure, and this
            # platform has been OOM-killed twice (cycles 127 and 128).
            with _cadence_lock:
                _cadence_refreshing = False
            log(f"nova-site: could not start the cadence refresh: {e}")
    if entry is not None and entry[0] is not None:
        return entry[0]
    return (None, None, None)


def reset_cadence():
    """Forget the fetched cadence. For tests -- see `reset_cache`."""
    global _cadence, _cadence_refreshing
    with _cadence_lock:
        _cadence = None
        _cadence_refreshing = False


def _in_flight(written, last_run_at, last_result, now=None):
    """Is a cycle in flight right now, as a fact about Agora's own record.

    The same three-condition test `_running_now` has always made, minus the
    `stalled` veto and plus an age bound -- and pulling it out is the fix for
    the false alarm the owner reported on 2026-08-24.

    `_running_now` defers to `stalled`, which is correct for a badge and
    exactly wrong for the stall *notice*: it means the one fact that
    disproves a stall is consulted only after the stall has already been
    declared. At 17:38 the site pushed "Nova has stopped writing" into Cycle
    373's own conversation, between two of that cycle's tool calls, because
    the newest entry was 44 minutes old and 44 minutes is two intervals at a
    20-minute cadence. Agora's record said `lastResult: "running"` the whole
    time.

    The age bound is what makes this safe to trust in the other direction. A
    killed cycle never writes its closing PATCH, so `lastResult` stays
    `"running"` forever -- which is precisely why `_running_now` had a veto
    at all. `lastRunAt` dates that claim, and a run older than
    `MAX_CYCLE_MINUTES` has been killed by the turn cap whatever the record
    says, so the claim expires on its own rather than needing the stall
    clock to overrule it.

    No lower bound on the age deliberately: a `lastRunAt` stamped in the
    future is a clock disagreement, and the caller's second bound (silence
    no longer than one cadence plus one cycle) is what stops a stuck record
    from silencing the alarm, so this one does not have to.
    """
    from agora_runner.cycle_health import MAX_CYCLE_MINUTES

    if last_result != "running" or not last_run_at:
        return False
    try:
        ran = datetime.fromisoformat(last_run_at)
        stamp = datetime.fromisoformat(written or "")
    except ValueError:
        return False
    if ran.tzinfo is None:
        ran = ran.replace(tzinfo=OSLO)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=OSLO)
    if ran <= stamp:
        return False
    age = (now or datetime.now(OSLO)) - ran
    return age <= timedelta(minutes=MAX_CYCLE_MINUTES)


def _running_now(written, last_run_at, last_result, stalled, now=None):
    """Is a cycle in flight right now -- measured, not inferred from the clock.

    The half of #72 that had no answer for 130 cycles. The owner: *"Nova is 1
    behind agora."* A cycle writes its entry at the end of its hour, so
    for the first 20-45 minutes of every hour the newest entry names N-1
    while agora is running N, and that looks on screen exactly like N
    having died. `stalled` handles the case where it really did die; this
    handles the far commoner case where it did not, and until now the page
    said nothing at all in that window.

    The evidence is Agora's own heartbeat record, which the site was
    already fetching for the cadence: `lastResult` is set to `"running"`
    when the run is claimed and overwritten with the outcome when it
    returns. So this is a fact about the runner, not a guess drawn from
    how long ago the last entry was written.

    Three conditions, and each one is load-bearing:

    - `lastResult == "running"`. The literal Agora writes; any other value
      is a run that finished, however it finished.
    - `lastRunAt` strictly newer than the newest journal entry. Without
      it, the ~15 minutes between a cycle writing its entry and its
      heartbeat being PATCHed with the outcome would render as a cycle
      still working, one hour out of every one.
    - **not `stalled`.** A killed cycle never writes its closing PATCH, so
      `lastResult` stays `"running"` forever -- exactly the case the badge
      would be lying about, and the only one that matters. Deferring to
      the stall clock means the claim expires on its own after the grace
      window, and the page swaps a "running" badge for a stall badge
      rather than reassuring him past the point anything is true. This is
      why the function takes `stalled` instead of computing it: the two
      must never be able to disagree.

    `False` when anything is missing, including the cold-cache
    `(None, None, None)`. Silence is the status quo here and a wrong
    "running" is worse than a late one.

    The three conditions now live in `_in_flight`, which the stall decision
    also uses; the `stalled` veto stays here and only here, so the badge and
    the stall flag still cannot disagree.
    """
    if stalled:
        return False
    return _in_flight(written, last_run_at, last_result, now)


def _with_silence(status, now=None, minutes=None, record_age=None, heartbeat=None):
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
    see N-1 -- the owner's #72 is exactly that ambiguity, and a check that
    cannot tell a running cycle from a dead one would raise a false alarm
    every single hour. `silentIntervals` is reported whether or not it
    crossed the threshold, so the two questions stay separable.

    `None` (no entry carries a usable write time) is deliberately not
    flattened into `0`: "nothing to judge" and "judged, and fine" are
    different answers, and only the second is reassurance.

    `minutes` is the interval to divide by, defaulting to the live cadence
    -- see `cadence_minutes`. It is a parameter so a test can state the
    cadence it is testing against instead of arranging for a network call
    to be answered.
    """
    from agora_runner.cycle_health import STALL_GRACE_INTERVALS

    # One cache read, not two, and only on the path production actually
    # takes. `minutes` and `heartbeat` come out of the same fetch, so a
    # caller that states the cadence (every test of the arithmetic does)
    # is a caller that is not asking about the live loop either --
    # fetching on its behalf would put a background network call behind
    # an assertion about subtraction.
    if minutes is None:
        from agora_runner.cycle_health import HEARTBEAT_MINUTES

        snapshot = heartbeat_snapshot()
        minutes = snapshot[0] or HEARTBEAT_MINUTES
        if heartbeat is None:
            heartbeat = snapshot[1:]
    if heartbeat is None:
        heartbeat = (None, None)
    now = now or datetime.now(OSLO)
    out = dict(status)
    written = out.get("lastWrittenAt") or ""
    silent = None
    silent_minutes = None
    if written:
        try:
            stamp = datetime.fromisoformat(written)
        except ValueError:
            stamp = None
        if stamp is not None:
            elapsed = now - stamp
            silent_minutes = max(0.0, elapsed.total_seconds() / 60)
            # An entry stamped in the future is a clock disagreement, not a
            # stalled loop -- zero rather than a negative the client would
            # have to guard. The same call `cycle_health.stalled_for` makes.
            silent = max(0, int(elapsed.total_seconds() // (minutes * 60)))
    out["silentIntervals"] = silent

    # The two halves of that subtraction come from different moments:
    # `now` is live and `lastWrittenAt` came out of a cache that may not
    # have refreshed in hours. So a rebuild that keeps failing looks
    # exactly like a loop that stopped writing, and the page says the
    # second when only the first is true -- confidently, and about the one
    # thing it exists to be trusted on.
    #
    # `stale` is not a softer stall, it is a different claim: *this
    # process cannot see the journal*. It is the failed-fetch state of
    # issue #81 one hop further back -- Cycle 198 gave the client an
    # honest answer for "I cannot reach the server" and left the server
    # with none for "I cannot reach the vault". Silence there is not
    # neutral either; here it was worse than silence, because it was a
    # false alarm rather than a missing one.
    #
    # `silentIntervals` is still reported: nothing newer has been seen,
    # which remains true and is what makes the age worth showing. What
    # goes away is the verdict drawn from it.
    #
    # A boolean and not the age in minutes, deliberately. Anything in this
    # payload that is not folded into the etag is frozen at whatever the
    # last non-304 poll said, and a counter that stops counting while
    # claiming to be a live age is the same shape of lie this block
    # removes. The page gets the fact; the age stays a parameter.
    stale = record_age is not None and record_age >= RECORD_TRUST_SECONDS
    out["recordStale"] = stale

    # A cycle that is demonstrably running right now explains the silence,
    # and the grace interval no longer covers that on its own -- see
    # `MAX_CYCLE_MINUTES`. Two bounds, and both are needed:
    #
    # - `_in_flight` believes Agora's "running" for at most one turn cap, so
    #   a killed cycle stops explaining anything ~45 minutes after it died.
    # - the silence itself must still be short enough for *one* in-flight
    #   cycle to account for: one cadence interval to wake, plus at most one
    #   turn to write. Past that, two consecutive cycles have failed to write
    #   and it is a stall whatever the heartbeat record claims -- which is
    #   what stops a scheduler that keeps claiming runs into a dead runner
    #   from muting the alarm for good.
    from agora_runner.cycle_health import MAX_CYCLE_MINUTES

    in_flight = _in_flight(written, heartbeat[0], heartbeat[1], now)
    explained = (
        in_flight
        and silent_minutes is not None
        and silent_minutes < minutes + MAX_CYCLE_MINUTES
    )
    out["stalled"] = (
        not stale
        and silent is not None
        and silent >= STALL_GRACE_INTERVALS
        and not explained
    )
    out["running"] = not stale and _running_now(
        written, heartbeat[0], heartbeat[1], out["stalled"], now
    )
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


def board_descriptor(args):
    """What `/api/board`'s etag must vary by, beyond the payload itself.

    Every argument `board_page` was called with, and it takes the same
    dict that call took rather than a copy of it -- which is the whole
    point, and the reason this is a function rather than three f-strings
    in `_send_board`.

    It used to be three: one per branch of `board_page`, each naming by
    hand the parameters that branch happened to read. That is a rule
    nothing enforces, so it was wrong within a week of being written.
    `q=` shipped without `mine`, so the Nova tab's search and his search
    -- two lists of row numbers over two boards that share a number
    space -- hashed to one cache entry, and the second reader was
    answered 304 against the first one's matches. The `item=` variant,
    three lines from it in the endpoint, had `mine` and was right.
    Nothing could have told you which was which except reading both.

    Deriving it from the arguments makes the class impossible rather
    than making the fourth instance of it detectable: a parameter that
    reaches `board_page` reaches the etag by construction, whether or
    not the branch that consumes it is the one you were thinking about
    when you added it. `test_board_etag_descriptor.py` pins the other
    half -- that this dict really is `board_page`'s full signature --
    because the failure this replaces is not a typo, it is an argument
    somebody adds later and does not think about here.

    Over-varying is the safe direction and is what this deliberately
    does, in **all three** branches rather than the one it is tempting
    to describe. Each branch reads a subset: `item=` ignores `limit` and
    `search`, `search=` ignores `limit` and `item`, and the list ignores
    `item`, `search` and `mine`. Hashing all four means any request
    carrying a parameter its own branch does not read gets a fresh etag
    for a byte-identical body -- one wasted re-fetch of a payload the
    client already had.

    Dormant today: `app.js` sends exactly one of `item[&mine]`,
    `q[&mine]` or `limit` per URL and never combines them, so no live
    request has a spare parameter to be charged for. It would wake up
    for a client that carried stray query parameters across a
    navigation, and the cost then is still one re-fetch. Under-varying
    costs a wrong answer that looks like a right one. The two are not
    comparable, and that is the whole trade being made here.
    """
    return "&".join(f"{name}={args[name]!r}" for name in sorted(args))


def journal_descriptor(page, limit, offset, cycle, search=None):
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
    and never in the one already sitting open on the owner's phone, which is
    the case the feature exists for. Folding the interval count in means
    the etag turns over at each hour boundary, which is exactly when the
    answer changes and no more often.

    And `running`, for the third instance of that same trap. It turns over
    twice an hour -- on when a cycle is claimed, off when it writes its
    entry -- and neither edge lines up with an interval boundary. Left
    out, the badge would render only in a tab opened mid-cycle, and
    the owner's phone, which is already polling, is the one place it would
    never appear. That phone is the whole reason #72 was filed.

    And `recordStale` on top of that, for a sharper version of the same
    trap. It turns over when the *rebuild* starts failing, which does not
    line up with an interval boundary at all -- so keyed on the interval
    count alone, a client already polling would be answered 304 for up to
    a full hour after the site stopped being able to see the journal, and
    would go on showing the stall this flag exists to retract. The flag is
    folded in rather than the age behind it: the age changes every second
    and would turn every conditional poll back into a full 184KB answer.
    """
    status = page.get("status") or {}
    window = f"cycle={cycle}" if cycle is not None else f"{offset}:{limit}"
    # Two different queries against the same journal build the same base
    # etag, so without this the second one is answered 304 with the
    # first one's rows still on screen -- a wrong answer that looks like
    # a right one, which is the trade the docstring above settles in
    # favour of over-varying.
    if search is not None:
        window += f"|q={search!r}"
    return (f"{window}|silent={status.get('silentIntervals')}"
            f"|stale={bool(status.get('recordStale'))}"
            f"|running={bool(status.get('running'))}")


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

    def _send(self, status, body, content_type, etag=None, cache_control=None):
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
        if cache_control:
            self.send_header("Cache-Control", cache_control)
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
        payload, _, base, age = cached_entry("journal", journal_payload)
        cycle = _int_param(query, "cycle", None)
        limit = _int_param(query, "limit", None)
        offset = _int_param(query, "offset", 0)
        search = (query.get("q") or [None])[0]
        page = journal_page(
            payload, limit=limit, offset=offset, cycle=cycle, record_age=age,
            search=search,
        )
        etag = page_etag(
            base, journal_descriptor(page, limit, offset, cycle, search)
        )
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
        args = {
            "item": _int_param(query, "item", None),
            "limit": _int_param(query, "limit", None),
            "search": (query.get("q") or [None])[0],
            "mine": (query.get("mine") or ["0"])[0] == "1",
        }
        page = board_page(payload, **args)
        etag = page_etag(base, board_descriptor(args))
        page["version"] = etag
        self._send_json_or_304(json.dumps(page), etag)

    def _send_json_or_304(self, body, etag, content_type="application/json",
                          cache_control=None):
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            # A 304 must carry the headers that would decide which cached
            # representation it validates, and the body varies by encoding.
            self.send_header("Vary", "Accept-Encoding")
            if cache_control:
                self.send_header("Cache-Control", cache_control)
            self.end_headers()
            return
        self._send(200, body, content_type, etag=etag, cache_control=cache_control)

    def _send_static(self, filename):
        """The shell, and why it is conditional.

        The service worker is network-first for `/app.js` and
        `/style.css` as well as for `/api`, and its own note says why:
        cache-first would pin the app to the first build it ever saw,
        and network-first "costs one conditional request on a tailnet
        and cannot do that."

        It did not cost one conditional request. Until this function
        passed an etag, no static response carried a validator at all,
        so there was nothing for a returning client to make its request
        conditional *on* -- every navigation to `/issues` re-downloaded
        the whole shell. Measured against the live site 2026-08-20,
        gzipped and on the wire: `app.js` 76,254 bytes and `style.css`
        19,637 bytes, against 13,429 for the board payload those two
        pages exist to render. 87KB of the ~110KB a board page costs was
        bytes the phone already had, re-fetched because the server never
        said which build they came from. That is the owner's capture --
        "Issues and ideas takes a while to load" -- and it is why the fix
        is not lazy-loading the rows: the rows were never the weight.

        Hashed per request rather than cached. These files are read off
        disk on every request already, and the hash is measured rather
        than guessed at: 0.14ms for `app.js` (243,899 bytes) and 0.58ms
        for the 1.0MB `echarts.min.js`, against a read that has to
        happen anyway. A cache keyed on a
        path would be one more thing that can serve a stale build after
        a deploy, which is the exact failure the service worker's
        author refused to accept in exchange for the same saving.

        `/vendor/echarts.min.js` gets this too and gains the most from
        it -- 1,030,855 bytes, re-fetched on every visit to a chart page.
        """
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
        # Strong rather than `W/`: this is the file's own bytes, not a
        # slice derived from a payload etag, so byte-equality is exactly
        # what it asserts.
        etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
        # `no-cache` means "store it, but ask me every time", not "do not
        # store it". Without it the response has a validator and no
        # freshness rule, and a browser then invents one -- heuristic
        # freshness off `Last-Modified`, which we do not send either, so
        # the behaviour is up to the implementation and one of the
        # permitted answers is serving a build the owner has already
        # replaced without asking. That is precisely the trap the service
        # worker's own comment refuses. `no-cache` buys the saving and
        # keeps the guarantee: one conditional request, always the
        # current build.
        self._send_json_or_304(body, etag, content_type, cache_control="no-cache")

    def do_GET(self):
        path, _, raw_query = self.path.partition("?")
        path = path.rstrip("/") or "/"
        query = urllib.parse.parse_qs(raw_query)

        # `/cycle/49` is a real URL so an Agora reply can link straight at
        # one entry (item 4). The server has no per-cycle view -- it serves
        # the same shell and app.js reads the path -- but the URL must
        # resolve, or the link is dead on a cold load.
        if path in PAGE_ROUTES or path.startswith(PAGE_ROUTE_PREFIXES):
            self._send_static("index.html")
            return
        if path in STATIC_ROUTES:
            self._send_static(STATIC_ROUTES[path])
            return
        if path.startswith("/api/upload/"):
            self._send_upload(path[len("/api/upload/"):])
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
            if path == "/api/notes":
                # Cached like the boards and for the same reason: the
                # vault read is the slow part and the file changes when
                # the owner types a note or a cycle answers one, not
                # between two taps. It is also the smallest payload on
                # the site -- 11KB of markdown -- so nothing here wants
                # a window.
                self._send_cached_json("notes", notes_payload)
                return
            if path == "/api/costs":
                self._send_cached_json("costs", costs_payload)
                return
            if path == "/api/retro":
                self._send_cached_json("retro", retros_payload)
                return
            if path == "/api/plan":
                self._send_cached_json("plan", plans_payload)
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
            if path == "/api/ask":
                # Never cached, for `/api/comments`' reason and one more:
                # this endpoint is polled *because* it is expected to
                # change, and a CACHE_FRESH_SECONDS window would show
                # the owner a thread that stays "waiting" after the answer
                # has already landed.
                self._send_json(200, ask_thread())
                return
            if path == "/api/conversations":
                # Not cached, for `/api/ask`'s reason: this is the list he
                # opens to see whether anything has answered, so a
                # CACHE_FRESH_SECONDS window would show him a thread that
                # is still where it was before the reply landed.
                self._send_json(200, conversation_list())
                return
            if path == "/api/conversations/thread":
                # `id` rather than a path segment so the route table stays a
                # set of exact strings -- `PAGE_ROUTE_PREFIXES` exists for
                # the one case that genuinely needed a prefix and adding a
                # second kind of matching for a query parameter would be a
                # cost with no reader.
                wanted = (query.get("id") or [""])[0]
                self._send_json(200, conversation_thread(wanted))
                return
            if path == "/api/conversations/personas":
                self._send_json(200, conversation_personas())
                return
            if path == "/api/pool":
                # Not cached, and for `/api/comments`' reason: the owner
                # decides a candidate and the page re-fetches immediately,
                # so a CACHE_FRESH_SECONDS window would show him the card
                # he just approved. It is one small vault read.
                self._send_json(200, pool_payload())
                return
            if path == "/api/pool/history":
                # What he decided and what he wrote when he decided it. Not
                # cached for the same reason `/api/pool` is not: he taps
                # Approve and the very next thing he may do is open History
                # to check it landed.
                self._send_json(200, pool_history())
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

    def _send_upload(self, name):
        """Serve one stored image. `name` is validated in `read_upload`.

        Outside the `try` in `do_GET` on purpose: this is the one GET that
        answers with bytes rather than JSON, so the JSON-shaped 502 that
        block ends in would be a broken image with an explanation nothing
        renders.
        """
        try:
            found = read_upload(urllib.parse.unquote(name))
        except Exception as e:
            log(f"nova-site /api/upload/{name} failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        if found is None:
            self._send_json(404, {"error": "not found"})
            return
        content_type, raw = found
        # `immutable`, which nothing else on this site gets: the name *is*
        # the sha256 of these bytes, so the response can never go stale
        # without the URL changing. Unlike `/app.js`, where the same URL
        # legitimately means different bytes after every deploy, there is
        # no build here to serve a superseded copy of.
        etag = '"' + hashlib.sha256(raw).hexdigest()[:16] + '"'
        self._send_json_or_304(
            raw, etag, content_type,
            cache_control="public, max-age=31536000, immutable")

    def _post_upload(self):
        """`/api/upload` -- the owner attaching a screenshot to what he types.

        It reads its own body rather than going through `_read_json_body`,
        for the one reason that matters: that reader caps at
        `MAX_BODY_BYTES`, which is 64KiB because a capture is a sentence
        typed on a phone. An image is four orders of magnitude past that,
        so sharing the reader would mean raising the cap for the capture
        box too, and the cap on the capture box is the thing standing
        between a 256Mi pod and an unbounded `rfile.read`.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length <= 0:
            self._send_json(411, {"error": "a Content-Length is required"})
            return
        if length > MAX_UPLOAD_BYTES:
            self._send_json(
                413,
                {"error": f"file over {MAX_UPLOAD_BYTES // (1024 * 1024)}MB"})
            return
        content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != "application/json":
            self._send_json(415, {"error": "Content-Type must be application/json"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"invalid JSON: {e}"})
            return
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "expected a JSON object"})
            return

        try:
            name, url, size, stored_type = store_upload(
                payload.get("filename"),
                payload.get("contentType") or payload.get("content_type"),
                payload.get("data"),
            )
        except UploadRejected as e:
            self._send_json(400, {"ok": False, "error": str(e)})
            return
        except Exception as e:
            log(f"nova-site /api/upload failed: {e}")
            self._send_json(502, {"ok": False, "error": str(e)[:300]})
            return
        # `isImage` rather than leaving the client to re-guess from the
        # filename: the server is the one that resolved the type (an
        # extension lookup when the phone sent nothing), so it is the only
        # side that knows whether this renders inline or is a download.
        self._send_json(200, {
            "ok": True, "name": name, "url": url, "bytes": size,
            "contentType": stored_type, "isImage": is_image(stored_type),
        })

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
            # Exactly as for a new capture: the board the owner is looking at
            # has gone stale and `app.js` reloads on the next tick.
            _invalidate_capture_target(target)

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

    def _post_capture_comment(self, payload):
        """`POST /api/capture/comment` -- answer an unboarded capture in place.

        The gap this closes is the top of the ranking. `top_board_rows`
        puts his bare bullets above every boarded row, and until now the
        only comment route was `/api/board/comment`, which addresses a row
        by its number. A capture has no number, so the highest-ranked
        class of item on either board was the one class with nowhere to
        put an answer -- filed in six consecutive handoffs and fixed in
        none of them.

        Addressing is `_post_amend`'s, not `_post_board_comment`'s:
        `target` keys into `CAPTURE_TARGETS`, `index` says which bullet and
        `original` says it has not moved. Same 409 for a capture that was
        boarded while the page was open -- nothing failed, the address
        moved.

        The line-break rule is `_post_board_comment`'s and for the same
        reason: a reply is one indented bullet, and a break in it would
        split into a bullet and a stray paragraph that the next parser
        reads as a continuation of something else.

        There is no `author`. On these files a bare bullet is his and an
        indented one is a cycle's -- that is the contract every parser of
        them already reads, so a name in the payload could only ever
        disagree with the shape of the line it writes.
        """
        target = payload.get("target")
        index = payload.get("index")
        original = payload.get("original")
        text = payload.get("text")
        if target not in CAPTURE_TARGETS:
            self._send_json(400, {"error": f"target must be one of {sorted(CAPTURE_TARGETS)}"})
            return
        # `True` is an int in Python and would silently address capture 1.
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            self._send_json(400, {"error": "index must be a non-negative number"})
            return
        if not isinstance(original, str) or not original.strip():
            self._send_json(400, {"error": "original must be a non-empty string"})
            return
        if not isinstance(text, str) or not text.strip():
            self._send_json(400, {"error": "text must be a non-empty string"})
            return
        if "\n" in text or "\r" in text:
            self._send_json(400, {"error": "a reply cannot contain a line break"})
            return

        try:
            ok, message = comment_on_capture(target, index, original, text)
        except Exception as e:
            log(f"nova-site capture comment failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            _invalidate_capture_target(target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Reply under a capture in {target} · {'ok' if ok else message}",
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

    def _post_convert(self, payload):
        """`POST /api/capture/convert` -- move one capture to another file.

        The owner, capture 2026-08-24: *"The note i sent regarding the
        rebuilding the notes page was sent as a note, but its actually an
        idea, but i have no way of changing it or editing it."*

        Same boundaries as `_post_amend`, and one more: `from` and `to`
        are both keys into CAPTURE_TARGETS, so neither addresses a
        document. `original` is the whole safety of the operation -- it is
        what `replace_capture` matches on, so a bullet a cycle boarded
        while this page was open is a 409 rather than a wrong line moved.
        """
        source = payload.get("from")
        dest = payload.get("to")
        index = payload.get("index")
        original = payload.get("original")
        for name, value in (("from", source), ("to", dest)):
            if value not in CAPTURE_TARGETS:
                self._send_json(
                    400, {"error": f"{name} must be one of {sorted(CAPTURE_TARGETS)}"})
                return
        if source == dest:
            self._send_json(400, {"error": "from and to must differ"})
            return
        if not isinstance(original, str) or not original.strip():
            self._send_json(400, {"error": "original must be a non-empty string"})
            return
        # `True` is an int in Python and would silently address capture 1.
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            self._send_json(400, {"error": "index must be a non-negative number"})
            return

        try:
            ok, message = convert_capture(source, index, original, dest)
        except Exception as e:
            # Both sides here too, and that is the point of the `finally`
            # shape rather than a tidy early return: an exception can land
            # *after* the destination write succeeded, and a cached page
            # would then hide the copy that really is there. Reviewer
            # finding on this PR.
            _invalidate_capture_target(source)
            _invalidate_capture_target(dest)
            log(f"nova-site capture convert failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        # Both sides on any outcome but a refused write: the destination
        # may hold the copy even when the source removal failed, which is
        # exactly the state the message describes, and a page that still
        # shows the old payload would contradict it.
        _invalidate_capture_target(source)
        _invalidate_capture_target(dest)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Convert {source} -> {dest} · {'ok' if ok else message}",
            before=original[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        # **Not `_post_amend`'s predicate, and copying it was a bug.** There
        # 409 means "nothing happened, the address moved, re-read" -- and
        # `STALE_CAPTURE` can appear here *inside* the half-done message,
        # after the destination write already landed. Answering 409 would
        # tell the page nothing changed while a copy sits in `dest`. So the
        # only 409 here is a refusal that never wrote anything, which is a
        # destination write that failed before `amend` was reached.
        # Reviewer finding on this PR.
        wrote_destination = message.startswith("copied to ")
        stale = not wrote_destination and STALE_CAPTURE in message
        self._send_json(409 if stale else 502, {"ok": False, "message": message})

    def _post_pool_decide(self, payload):
        """`POST /api/pool/decide` -- the owner approving or rejecting a candidate.

        Idea #92, phase 1. `index` alone is not an address: a refill that
        ran while the page was open renumbers everything below it, so
        `title` is sent back too and `nova_idea_pool.find_candidate`
        refuses when the two disagree. That is the same guard `original`
        gives `/api/capture/convert`, for the same failure -- deciding the
        wrong idea is worse than refusing to decide.

        Nothing here can reach a model, and nothing downstream of it can
        either: approve and reject are both markdown writes into a file
        that already exists. See `nova_idea_pool`'s module docstring for
        why that boundary is permanent rather than a phase-1 shortcut.
        """
        index = payload.get("index")
        title = payload.get("title")
        decision = payload.get("decision")
        comment = payload.get("comment") or ""
        if decision not in ("approve", "reject"):
            self._send_json(400, {"error": "decision must be approve or reject"})
            return
        if not isinstance(title, str) or not title.strip():
            self._send_json(400, {"error": "title must be a non-empty string"})
            return
        # `True` is an int in Python and would silently address candidate 1.
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            self._send_json(400, {"error": "index must be a non-negative number"})
            return
        if not isinstance(comment, str):
            self._send_json(400, {"error": "comment must be a string"})
            return
        if len(comment.encode("utf-8")) > MAX_BODY_BYTES:
            self._send_json(400, {"error": "comment is too long"})
            return

        dated = datetime.now(OSLO).strftime("%m-%d")
        try:
            ok, message = pool_decide(index, title, decision, comment, dated)
        except Exception as e:
            _invalidate_capture_target("ideas")
            log(f"nova-site pool decide failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        # An approve writes a row onto his ideas board, so the cached board
        # payload is stale whether or not the pool write that followed it
        # succeeded -- and the half-done case is exactly when a page still
        # showing the old board would contradict the message.
        _invalidate_capture_target("ideas")
        audit(
            "Nova",
            "",
            "nova_idea_pool",
            f"Pool {decision} · {'ok' if ok else message}",
            before=title[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        # 409 only when nothing was written. `STALE_CANDIDATE` is the one
        # refusal that happens before either write, so it is the only one
        # where "re-read and try again" is honest advice -- the half-done
        # message names a row that really is on his board.
        self._send_json(
            409 if message == STALE_CANDIDATE else 502, {"ok": False, "message": message})

    def _post_pool_generate(self, payload):
        """`POST /api/pool/generate` -- the owner asking for more candidates.

        It sets a flag and returns. It does **not** generate: this process
        has no Claude access and must never get one (rule 9, production
        never spends the metered API), so the button asks and the next
        cycle to read the pool answers within twenty minutes. A page load
        that could become a model call is out of scope permanently.
        """
        try:
            ok, message = pool_request_generate()
        except Exception as e:
            log(f"nova-site pool generate failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_idea_pool",
            f"Pool generate requested · {'ok' if ok else message}",
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})

    def _post_priority(self, payload):
        """`POST /api/board/priority` -- the owner re-rating a row I rated.

        His capture, 2026-08-14: *"i want that aswell ... when they are
        boarded its possible for me to change the priority."* Every rating
        on both boards today was set by Cycle 188, not by him, so this is
        the first way he can disagree with one without opening Obsidian.

        Same two boundaries as the capture box and for the same reason:
        `target` is a key into a dict of literal paths, never a path, and
        `priority` is checked against the four labels here rather than
        written through -- a client cannot put arbitrary text into a cell
        of his file. `number` is `int` only; `True` is an int in Python
        and would address row 1, which is the trap `_post_amend` names.
        """
        target = payload.get("target")
        number = payload.get("number")
        priority = payload.get("priority")
        if target not in BOARD_PATHS:
            self._send_json(400, {"error": f"target must be one of {sorted(BOARD_PATHS)}"})
            return
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            self._send_json(400, {"error": "number must be a positive integer"})
            return
        priority = canonical_priority(priority)
        if priority is None:
            self._send_json(
                400, {"error": f"priority must be one of {sorted(PRIORITY_LABELS.values())}"})
            return

        try:
            ok, message = set_priority(target, number, priority)
        except Exception as e:
            log(f"nova-site priority failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            # The board the owner is looking at now shows the old rating, and
            # `app.js` reloads on the next tick -- exactly the staleness
            # the capture box invalidates for.
            invalidate("board:" + target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Rate #{number} on {target} \u00b7 {'ok' if ok else message}",
            after=priority or "(unrated)",
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})

    def _post_board_comment(self, payload):
        """`POST /api/board/comment` -- idea #64, the comment half.

        *"Lets me have the same comment conversation on ideas, notes and
        issues like the Journal."* Rated 🔴 Immediately, open since
        08-12, and skipped by every cycle since -- which he noticed and
        filed, which is why this cycle is here.

        **Almost nothing new happens on this route, and that is the
        finding rather than a shortcut.** The comment goes into the row's
        own write-up, which an expanded board row already fetches and
        renders, so there is no read route, no new payload field and no
        second store to keep in step with this one.

        Same three boundaries as the other board writes: `target` keys
        into `BOARD_PATHS` and is never a path, `number` is `int` and not
        `bool` (`True` would address row 1), and the text is refused if it
        carries a line break -- `append_detail_note` refuses it again for
        its own reason, but a 400 here tells the page *why* where a
        `None` from the writer only says the write did not happen.

        A row that has no write-up cannot take a comment, and that is a
        409 rather than a 502 for `_post_board_amend`'s reason: nothing
        failed, there is simply nothing there to comment under, and the
        page should say so rather than retry.

        **`_amend_board` fails in two ways and only one of them is that**,
        which the first version of this route missed while its docstring
        claimed otherwise (reviewer). The other is a genuine write
        failure: `WRITE_ATTEMPTS` exhausted against a losing
        compare-and-swap, which returns `could not write to ...` and is a
        502. That is not a hypothetical here -- the concurrent writer is a
        cycle appending to these same write-ups in step 6, which is the
        argument for the retry loop in the first place, so the case that
        can actually exhaust it is the one this route was reporting as
        benign. `app.js` reads `ok` and not the status, so nothing on
        screen changed either way; what changed is that a real failure was
        indistinguishable from an empty row in the log.
        """
        target = payload.get("target")
        number = payload.get("number")
        text = payload.get("text")
        if target not in BOARD_PATHS:
            self._send_json(400, {"error": f"target must be one of {sorted(BOARD_PATHS)}"})
            return
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            self._send_json(400, {"error": "number must be a positive integer"})
            return
        if not isinstance(text, str) or not text.strip():
            self._send_json(400, {"error": "text must be a non-empty string"})
            return
        if "\n" in text or "\r" in text:
            self._send_json(400, {"error": "a comment cannot contain a line break"})
            return
        # The page is his, so an unstated author is him; a cycle posting a
        # reply says so. Anything else is refused rather than written into
        # his board under a name neither of us used.
        author = payload.get("author") or "Edvard"
        if author not in ("Edvard", "Nova"):
            self._send_json(400, {"error": "author must be 'Edvard' or 'Nova'"})
            return

        # `MM-DD` in Oslo, because `append_detail_note` takes the date from
        # its caller rather than a clock -- a module that reaches for one
        # reaches for it in UTC, and this line lands in a file the owner reads.
        dated = datetime.now(OSLO).strftime("%m-%d")
        try:
            ok, message = comment_on_row(target, number, text.strip(), dated, author)
        except Exception as e:
            log(f"nova-site board comment failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            invalidate("board:" + target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Comment on #{number} on {target} · {'ok' if ok else message}",
            after=text.strip()[:300],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        stale = "is not a row" in message
        self._send_json(200 if ok else (409 if stale else 502), {"ok": ok, "message": message})

    def _post_ask_watching(self):
        """`POST /api/ask/watching` -- the questions thread is on his screen.

        Agora withholds the phone push while this stays fresh, which is what
        stops the other app buzzing for a reply he is watching arrive here.
        See `nova_ask.watching`.

        Deliberately never an error to him: the page fires this on a timer and
        paints nothing from it, so a dead Agora must not put "could not load"
        over a thread he can read. Not audited for the same reason -- one row
        every four seconds is not a record of anything.

        That is not the same as saying a failure is harmless. My reviewer's
        point, and it is right: the dangerous direction is a *stale or wrong*
        vouch, which drops a notification he wanted. Nothing on this path can
        cause that -- a refused ping means no suppression -- so the guards that
        matter are the two in `pingAskWatching` and the TTL on Agora's side.
        """
        try:
            ok, reason = ask_watching()
        except Exception as e:
            log(f"nova-site ask/watching failed: {e}")
            self._send_json(200, {"watching": False, "reason": str(e)[:200]})
            return
        self._send_json(200, {"watching": ok, "reason": reason})

    def _post_ask(self, payload):
        """`/api/ask` -- the owner's question goes into the questions
        conversation and the Sonnet persona answers it on the next poll
        tick. See `nova_ask` for why that is the whole mechanism.

        Unlike every other write on this handler, nothing here touches the
        vault, so there is no cache to invalidate: the page re-reads the
        thread from Agora, which is the only copy.
        """
        text = payload.get("text")
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return
        try:
            ok, message = ask_question(text)
        except Exception as e:
            log(f"nova-site ask failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Question asked · {'ok' if ok else message}",
            after=text.strip()[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        # A refusal here is the text itself being unusable (empty, or past
        # MAX_QUESTION_CHARS), which is a 400 and not a 502 -- retrying it
        # unchanged cannot work.
        bad_text = not ok and message.startswith(("a question needs", "that is longer"))
        self._send_json(200 if ok else (400 if bad_text else 502),
                        {"ok": ok, "message": message})

    def _post_conversation_send(self, payload):
        """`/api/conversations/send` -- a message into an existing thread.

        `_post_ask`'s shape, one conversation id wider. Nothing here touches
        the vault, so there is no cache to invalidate: the page re-reads the
        thread from Agora, which is the only copy.
        """
        conversation_id = payload.get("conversationId")
        text = payload.get("text")
        if not isinstance(conversation_id, str) or not isinstance(text, str):
            self._send_json(400, {"error": "conversationId and text must be strings"})
            return
        try:
            ok, message = conversation_send(conversation_id, text)
        except Exception as e:
            log(f"nova-site conversations/send failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Message sent · {'ok' if ok else message}",
            after=text.strip()[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        # A refusal on the text itself cannot succeed on a retry, so it is a
        # 400 rather than a 502 -- `_post_ask`'s split, same reasoning.
        bad_text = not ok and message.startswith(
            ("a message needs", "that is longer", "which conversation"))
        self._send_json(200 if ok else (400 if bad_text else 502),
                        {"ok": ok, "message": message})

    def _post_conversation_new(self, payload):
        """`/api/conversations/new` -- start a thread with a chosen persona.

        Answers with the new id so the page can open it without re-listing.
        """
        name = payload.get("name")
        persona_id = payload.get("personaId")
        if not isinstance(name, str) or not isinstance(persona_id, str):
            self._send_json(400, {"error": "name and personaId must be strings"})
            return
        try:
            ok, message = conversation_create(name, persona_id)
        except Exception as e:
            log(f"nova-site conversations/new failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Conversation started · {'ok' if ok else message}",
            after=name.strip()[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        bad_input = not ok and message.startswith(
            ("a conversation needs", "that name is longer", "pick who"))
        self._send_json(200 if ok else (400 if bad_input else 502),
                        {"ok": ok, "conversationId": message if ok else None,
                         "message": message})

    def _post_board_amend(self, payload, delete):
        """`/api/board/edit` and `/api/board/delete` -- issue #84.

        *"I need to be able to edit and especially delete boarded ideas
        and issues from the agora app."* Until now a row became read-only
        the moment a cycle numbered it, so anything he typed and then
        regretted could only be taken back in Obsidian.

        **Two routes, for the reason `_post_amend` already gives**: an
        edit arriving with a blank title is a bad request here, never a
        quiet delete. The scope boundary is his too, from #85 -- *"This is
        only for the ones i have reported"* -- and it falls out of the
        addressing rather than being enforced separately: `target` keys
        into his two board files, and my own capture files are not boarded
        at all.

        A number that is not on either table is a 409 and not a 502.
        Nothing failed; the row moved or a cycle removed it, and the page
        should re-read rather than retry.
        """
        target = payload.get("target")
        number = payload.get("number")
        title = "" if delete else payload.get("title")
        if target not in BOARD_PATHS:
            self._send_json(400, {"error": f"target must be one of {sorted(BOARD_PATHS)}"})
            return
        # `True` is an int in Python and would address row 1.
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            self._send_json(400, {"error": "number must be a positive integer"})
            return
        if not delete:
            if not isinstance(title, str) or not title.strip():
                self._send_json(400, {"error": "title must be a non-empty string"})
                return
            # A row is one line of a markdown table. Either character ends
            # the edit somewhere the author did not mean it to.
            if "|" in title or "\n" in title:
                self._send_json(400, {"error": "a title cannot contain | or a line break"})
                return

        try:
            if delete:
                ok, message = remove_row(target, number)
            else:
                ok, message = edit_row(target, number, title)
        except Exception as e:
            log(f"nova-site board amend failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            invalidate("board:" + target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"{'Delete' if delete else 'Edit'} #{number} on {target} "
            f"· {'ok' if ok else message}",
            after=title[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        stale = "is not a row" in message
        self._send_json(409 if stale else 502, {"ok": False, "message": message})

    def _post_comment(self, payload):
        """`/api/comment` -- the owner replying to one cycle (ideas.md #44).

        `{"target": "needs"}` instead of a `cycle` answers the digest's
        Needs Edvard block (2026-08-10) -- see `nova_comments`.  (not-prose: quoting a literal)

        The same two boundaries as the capture box, and the same reason
        they hold: the tailnet authenticates, and the endpoint's shape
        authorizes. `cycle` is coerced to an int, `target` is checked
        against a one-value allow-list, and `text` must be a string, so
        nothing a client sends addresses a document -- the path is the
        module-level COMMENTS_PATH constant and there is no target to
        choose. The worst a request can do is add a comment to a file Nova
        reads and the owner can delete.
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
            # Only cycle comments get a reply. A `Needs Edvard` answer is a  (not-prose: quoting a literal)
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
        # Before the allowlist below, because it carries its own body
        # reader with its own cap -- see `_post_upload`.
        if path == "/api/upload":
            self._post_upload()
            return
        if path not in (
            "/api/capture", "/api/capture/edit", "/api/capture/delete",
            "/api/capture/convert", "/api/comment",
            "/api/board/priority", "/api/board/edit", "/api/board/delete",
            "/api/capture/comment",
            "/api/board/comment", "/api/ask", "/api/ask/watching",
            "/api/conversations/send", "/api/conversations/new",
            "/api/pool/decide", "/api/pool/generate",
        ):
            self._send_json(404, {"error": "not found"})
            return

        payload = self._read_json_body()
        if payload is None:
            return
        if path == "/api/ask":
            self._post_ask(payload)
            return
        if path == "/api/ask/watching":
            self._post_ask_watching()
            return
        if path == "/api/conversations/send":
            self._post_conversation_send(payload)
            return
        if path == "/api/conversations/new":
            self._post_conversation_new(payload)
            return
        if path == "/api/comment":
            self._post_comment(payload)
            return
        if path in ("/api/capture/edit", "/api/capture/delete"):
            self._post_amend(payload, delete=path.endswith("delete"))
            return
        if path == "/api/capture/comment":
            self._post_capture_comment(payload)
            return
        if path == "/api/capture/convert":
            self._post_convert(payload)
            return
        if path == "/api/board/priority":
            self._post_priority(payload)
            return
        if path in ("/api/board/edit", "/api/board/delete"):
            self._post_board_amend(payload, delete=path.endswith("delete"))
            return
        if path == "/api/board/comment":
            self._post_board_comment(payload)
            return
        if path == "/api/pool/decide":
            self._post_pool_decide(payload)
            return
        if path == "/api/pool/generate":
            self._post_pool_generate(payload)
            return
        target = payload.get("target")
        text = payload.get("text")
        if target not in CAPTURE_TARGETS:
            self._send_json(400, {"error": f"target must be one of {sorted(CAPTURE_TARGETS)}"})
            return
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return
        priority = payload.get("priority") or ""
        priority = canonical_priority(priority)
        if priority is None:
            self._send_json(
                400, {"error": f"priority must be one of {sorted(PRIORITY_LABELS.values())}"})
            return

        try:
            ok, message = capture(target, text, priority)
        except Exception as e:
            log(f"nova-site capture failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            # The board page the owner is looking at has just gone stale, and
            # `app.js` reloads it on the very next tick. Without this the
            # reload is served the pre-capture payload -- see `invalidate`.
            # `board:notes` never exists -- notes are not a board -- and
            # popping a missing key is a no-op, so this line stays exactly
            # as it was for the two targets that are boards.
            # Notes have a page of their own, cached under its own name
            # (`/api/notes`), and `board:notes` never exists. Both halves
            # live in `_invalidate_capture_target` now, because the edit
            # and delete path needed the same pair and had only the first.
            _invalidate_capture_target(target)

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
    # Same reasoning as the warm above -- off the startup path, because it
    # reads the vault -- and for the same reason it belongs at start at
    # all: the previous process's reply queue died with it, and until this
    # runs, a comment it was holding shows the owner nothing. See
    # `nova_replies.recover`.
    threading.Thread(target=recover_replies, name="nova-site-recover", daemon=True).start()
    return server
