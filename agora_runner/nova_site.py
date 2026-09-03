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
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
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
    promote_capture,
    archive_row,
    edit_row,
    remove_row,
    set_priority,
    set_project,
    set_project_priority,
    project_priorities,
)
from agora_runner.nova_comments import (
    add_comment,
    ENTRY_KEY_RE,
    add_entry_comment,
    add_needs_comment,
    add_project_comment,
    clean_comment_text,
    comments_by_cycle,
    comments_by_entry,
    format_stamp,
    needs_comments,
    project_comments,
)
from agora_runner.cycle_number import cycle_starts
from agora_runner.nova_journal import (
    build_status,
    parse_digest,
    parse_journal,
    render_blocks,
    resolved_ask_cycles,
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
    PRIORITY_ORDER,
    # Which statuses close a row. Imported rather than respelled here:
    # `nova_boards` owns that answer, and a second copy would disagree with
    # it the first time a status is added.
    _CLOSED_STATUS_KEYS,
    rank_projects,
    STATUS_LABELS,
    board_projects,
    canonical_priority,
    is_relayed,
    parse_board,
    unanswered_comment_bodies,
    parse_notes,
    priority_key,
    split_capture_done,
    split_capture_priority,
    split_detail_conversation,
)
from agora_runner.nova_conversation_reads import mark_seen as mark_conversation_seen
from agora_runner.nova_conversations import (
    autotitle as conversation_autotitle,
    conversations as conversation_list,
    create as conversation_create,
    starting_name as conversation_starting_name,
    folder_create as conversation_folder_create,
    model_choice as conversation_model_choice,
    move as conversation_move,
    remove as conversation_remove,
    rename as conversation_rename,
    send as conversation_send,
    set_model as conversation_set_model,
    step_output as conversation_step_output,
    thread as conversation_thread,
    watching as conversation_watching,
)
from agora_runner.nova_heartbeats import (
    heartbeats as heartbeat_list,
    run_now as heartbeat_run_now,
    set_enabled as heartbeat_set_enabled,
)
from agora_runner.nova_ask import (
    ask as ask_question, thread as ask_thread, watching as ask_watching,
)
from agora_runner.nova_idea_pool import (
    STALE_CANDIDATE,
    comment as pool_comment,
    decide as pool_decide,
    history_payload as pool_history,
    pool_payload,
    request_generate as pool_request_generate,
)
from agora_runner.nova_catalog import catalog_page, parse_catalog
from agora_runner.heartbeat_liveness import liveness
from agora_runner.nova_demos import (DEMOS_PATH, OPENED_AT,
                                     dumps as dumps_demos, load as load_demos,
                                     lookup as lookup_demo, mark_opened,
                                     opened_by_a_person)
from agora_runner.vault import vault_read_path, vault_read_path_rev, vault_write_path
from agora_runner.nova_notes import notes_payload
from agora_runner.nova_costs import costs_payload as shape_costs
from agora_runner.nova_next import next_payload, rank
from agora_runner.nova_plan import GOAL_STATUSES, set_goal_status
from agora_runner.nova_push import store_subscription, vapid_key
from agora_runner.nova_plan import plan_payload as shape_plan
from agora_runner.nova_retro import retros_payload as shape_retros
from agora_runner.nova_runtimes import attach_runtimes
from agora_runner.nova_boards import BOARD_PATHS
from agora_runner.nova_sources import (
    claims_ledger_json,
    board_markdown,
    catalog_markdown,
    comments_markdown,
    cost_ledger_json,
    digest_markdown,
    journal_markdown,
    plan_markdown,
    goal_history_json,
    retro_ledger_json,
)
from agora_runner.ticket_docs import read_rows
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
    "/asks",
    "/pool",
    "/costs",
    "/retro",
    "/plan",
    "/heartbeats",
    "/catalog",
    "/diag",
    "/projects",
)
# `/project/Nova` is a real URL for the same reason `/cycle/49` is: the
# project page has to survive a bookmark and a cold load, not only a tap
# on the index. Both are served the same shell and read by `app.js`.
# `/conversation/<id>` is the URL a push notification opens. Until
# 2026-08-30 a notification click focused whatever Nova tab happened to
# be open and navigated nowhere, so the owner landed on the issues page
# he had left open and lost the message he had just read.
PAGE_ROUTE_PREFIXES = ("/cycle/", "/project/", "/conversation/")

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


def catalog_payload():
    return catalog_page(parse_catalog(catalog_markdown()))


def _drop_legacy_reply(comment):
    """One comment on its way out of `/api/comments`, without `reply`.

    `parse_comments` derives `reply`/`replyStamp` from `replies[0]` and
    keeps them because `_verify_replied` and `nova_replies` both mean the
    auto-reply when they say "the reply". Those are server-side readers.
    The browser is not one of them: both of its uses are the fallback
    `if (!(replies && replies.length) && comment.reply)`, which cannot
    fire against this producer -- an empty `replies` makes `reply` `""`,
    and a non-empty one makes the first half false. So every byte the
    field carries on the wire is a second copy of `replies[0].text`.

    Measured against the live pod 2026-08-28: 155 of 159 comments carry a
    non-empty `reply`, all 155 byte-identical to a text already in their
    own `replies`, 65,607 bytes of a 258,352-byte `byCycle`. The route is
    deliberately uncached and `refreshMail` fetches it on every
    navigation, so that quarter is paid again on every page the owner
    opens -- which is what he reported as the app being slow to load
    comments.

    Dropped here rather than in `parse_comments` because the server-side
    readers above are real and this is only about the wire.
    """
    return {k: v for k, v in comment.items() if k not in ("reply", "replyStamp")}


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
    by_entry = comments_by_entry(markdown)
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
            # Whether the comment says of itself that Sokrates relayed it.
            #
            # `top_board_rows` already reads this to stop a relayed board
            # comment taking the rank-above-every-rating key (Cycle 626).
            # The drawer did not: a comment Sokrates posts on the owner's
            # behalf renders here byte-identically to one he typed, so the
            # one place he actually reads these was the one place that
            # still collapsed the two. Same signal, same asymmetry -- see
            # `nova_boards.is_relayed` for why a self-declared marker is
            # safe to act on when acting on it only ever demotes the
            # claimant.
            comment["relayed"] = is_relayed(comment.get("text"))
    return {
        "byCycle": {
            str(cycle): [_drop_legacy_reply(c) for c in items]
            for cycle, items in grouped.items()
        },
        # Replies to the digest's Needs Edvard block, which belong to no  (not-prose: quoting a literal)
        # cycle and so cannot ride in `byCycle`.
        # Same key on this list rather than only on `byCycle`, so the one
        # field a reader could act on is on both. **Not because the two
        # lists otherwise agree -- they do not, and the comment here used to
        # say they did.** `needs` items carry no `replyPending`,
        # `replyWaiting`, `replyWaitingSeconds` or `replyFailed` either:
        # those are set inside the loop above, which walks `grouped` alone.
        # So this narrows an existing disagreement rather than preventing a
        # new one, and the honest reason to do it is that `relayed` is a
        # claim about who wrote the text, which is true of a comment
        # wherever it is listed.
        "needs": [
            dict(_drop_legacy_reply(c), relayed=is_relayed(c.get("text")))
            for c in needs_comments(markdown)
        ],
        # Journal entries with no cycle number -- a retrospective, an ideas
        # run, a silence marker. Keyed by the entry's own `date time`, which
        # is what the card has instead of a number. They carry no
        # `replyPending`/`replyWaiting` for the same reason `needs` does not:
        # those are set in the loop over `grouped` above, and nothing
        # enqueues an automatic reply for these.
        "byEntry": {
            key: [_drop_legacy_reply(c) for c in items]
            for key, items in by_entry.items()
        },
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


def _split_details(bodies):
    """`{number: body}` -> the rendered write-ups and their conversations.

    Two dicts rather than one nested payload, because `board_page` windows
    them on different terms: a list request drops both, an `item=` request
    sends the one row's worth of each. Keyed by string, as `details`
    already was -- the page addresses a row by `board + ":" + number`.

    A row with no comments gets no key at all, so `detailComments` is
    empty for almost every board and costs nothing to carry.
    """
    prose = {}
    comments = {}
    for number, body in bodies.items():
        written, messages = split_detail_conversation(body)
        prose[str(number)] = render_blocks(written)
        if messages:
            comments[str(number)] = [
                {
                    "author": message["author"],
                    "stamp": message["stamp"],
                    "blocks": render_blocks(message["text"]),
                }
                for message in messages
            ]
    return prose, comments



def _rows_from_store(name, parsed):
    """His board's rows out of `nova_tickets`, or the parsed ones.

    Returns `parsed` unchanged whenever the store does not answer with
    exactly the same rows in the same order. That is deliberately strict:
    a row-projection view that has drifted is not a smaller answer to fall
    back from, it is a different board, and the page has no way to tell.
    `tools.ticket_drift` is what reports the drift; this only decides
    which of the two the owner is shown, and it shows the file.

    The comparison is the whole value of reading the store at all today.
    The rows carry ten fields and the markdown carries those ten plus the
    bodies, so agreeing here means the store reproduces the list exactly
    -- measured on every payload build rather than once a cycle.
    """
    path = BOARD_PATHS[name]["edvard"]
    try:
        rows = read_rows(path)
    except Exception as problem:
        # Every failure mode is the same decision: draw the file. A
        # narrower except would let a new CouchDB error empty his board,
        # which is the one outcome this function exists to prevent.
        log(f"nova-site {name} rows unreadable from the ticket store: {problem}")
        return parsed
    if rows != parsed:
        # Said out loud rather than absorbed. A fallback nothing reports
        # is the failure this loop keeps filing against itself: the page
        # would look right forever while the store the migration is
        # supposed to end up on quietly stopped agreeing with the file.
        log(
            f"nova-site {name} rows disagree with the ticket store: "
            f"{len(parsed)} parsed against {len(rows)} stored; drawing the file"
        )
        return parsed
    return rows

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
    # **His rows come out of the ticket store, not out of the markdown.**
    # This is the first reader of the one-document-per-ticket migration
    # the owner approved -- until now the store was written on every board
    # write and read by nothing, so a drift in it was invisible to
    # everything except `tools.ticket_drift` running once a cycle.
    #
    # Only the rows move. The write-ups, his captures and my own two files
    # are still parsed out of the markdown this call has already fetched,
    # so this saves no fetch today and is not meant to: what it buys is
    # that the list the page draws is the store's answer, checked against
    # the markdown on every build by the assertion below rather than once
    # a cycle by a tool.
    #
    # `parse_board` stays the fallback and stays the source of truth. A
    # store that is unreachable, behind, or missing a row must not empty
    # his board -- the markdown is the file he edits and it is always
    # right.
    board["items"] = _rows_from_store(name, board["items"])
    # Which rows he asked a question on and nobody answered. Stamped onto
    # the row here rather than worked out again by whoever needs it,
    # because two things already rank these rows with `nova_next.rank` --
    # `tools.top_board_rows`, which reads the markdown and computes this,
    # and `_project_backlog`, which reads this payload and until now could
    # not. `rank` looks the flag up with `.get`, so its absence removed the
    # raise silently: the project page's "What's next" list put a row he is
    # waiting on an answer to wherever its rating fell, while the ranking I
    # actually pick work from put it first. Same function on the markdown
    # this call has already read, so there is one definition of waiting and
    # no second fetch.
    waiting_bodies = unanswered_comment_bodies(edvard_markdown)
    for item in board["items"]:
        if item["number"] in waiting_bodies:
            item["waiting"] = True
            # A comment Sokrates relayed on his behalf is still owed a
            # reply and still does not jump the queue -- his own ask,
            # 2026-08-29. Carried rather than dropped because `rank` reads
            # both keys together and a `waiting` with no `relayed` beside
            # it would raise the relays this loop was told not to raise.
            item["relayed"] = is_relayed(waiting_bodies[item["number"]])
    # The write-up and the conversation appended under it, told apart
    # here rather than on the page: `render_blocks` flattens both into the
    # same list of paragraphs, and once that has happened nothing
    # downstream can tell his question from my answer from the problem
    # statement above them both. His capture, 2026-08-26: *"boarded issues
    # does not have those nice colored comments like there are now in the
    # 'not boarded yet' box"*.
    details, detail_comments = _split_details(board["details"])
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
    # A write-up that has been rolled into the archive is still that
    # row's write-up. Nothing moves a `# Details` body out of the live
    # file yet -- `tools/roll_captures.py` moves the older *captures* and
    # stops there -- and that is precisely the order this has to happen
    # in: `mine` is the live file only, so the day the roller learns to
    # move a body, every row it moved would draw an empty write-up on the
    # page with nothing failing anywhere. The page has to be able to read
    # a rolled body before the roller may write one.
    #
    # Live wins on a collision. A number in both files is a row whose
    # write-up was rolled and then written again, and the live file is
    # the newer of the two. `parse_board` over an archive that has no
    # `## Board` table returns no items, so this adds bodies and never
    # rows -- an archived row is still a row on the live board.
    for number, body in parse_board(nova_archive_markdown)["details"].items():
        mine["details"].setdefault(number, body)
    nova_details, nova_detail_comments = _split_details(mine["details"])
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
        "detailComments": detail_comments,
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
        "novaDetailComments": nova_detail_comments,
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


#: The order a project page stacks its status columns in. Open work
#: first, then the two closed buckets, so the top of the page is the
#: part with something to do. `board_projects` derives the *projects*
#: from the rows and this deliberately does not derive the *statuses*
#: the same way: a column order read off the data reorders itself every
#: time a row changes status, and a board whose columns move is unusable
#: as a glance. A status not named here still renders -- it lands in an
#: `other` column at the end rather than being dropped, which is the
#: same choice `status_key` makes for an emoji it has never seen.
PROJECT_STATUS_ORDER = (
    "in-progress",
    "blocked-on-edvard",
    "backlog",
    "done",
    "outdated",
)


def _project_columns(items):
    """Group one board's rows into the status columns above.

    Returns a list of `{key, label, items}` and drops a column with
    nothing in it, so a project with no blocked rows does not render an
    empty heading. The label comes from `STATUS_LABELS` so the page
    cannot spell a status differently from the file it was parsed out
    of -- the drift `STATUS_LABELS` exists to prevent.
    """
    buckets = {}
    for item in items:
        buckets.setdefault(item.get("statusKey") or "none", []).append(item)
    columns = []
    for key in PROJECT_STATUS_ORDER:
        if buckets.get(key):
            columns.append({
                "key": key,
                "label": STATUS_LABELS.get(key, key),
                "items": buckets.pop(key),
            })
    for key in sorted(buckets):
        columns.append({
            "key": key,
            "label": buckets[key][0].get("status") or key,
            "items": buckets[key],
        })
    return columns


#: What `/api/project/comment` refuses in a project name, and why each one
#: is a danger rather than tidiness. A name goes verbatim into a `###`
#: heading in `comments.md`, so a line break in it would split that heading
#: and file the body outside any comment -- the splice class `doc_integrity`
#: exists to catch, arriving through a supported route. A `·` is the
#: separator `_PROJECT_HEADING_RE` splits the stamp on, so a name carrying
#: one parses back as a different, shorter name. The length cap is the
#: weakest of the three and is here because the field is free text from a
#: phone with no list to check it against; 120 is well past any project
#: name and short enough to stay one line. None of this bounds
#: `board_projects`, which reads names off cells the app never wrote.
PROJECT_NAME_MAX = 120


def _project_thread(markdown, project):
    """One project's comments, flattened into the shape a bubble needs.

    `[{author, stamp, blocks}]`, oldest first, exactly what
    `renderRowConversation` in `app.js` already draws for a board row --
    reused verbatim rather than given a project-shaped variant, because the
    whole point of the owner's *"just like the comments"* is that these
    pages read alike, and a second bubble renderer is how they stop doing
    that a month from now.

    A comment and the replies inside it become sibling messages here. They
    are nested in the file, because a reply belongs to the comment it
    answers and that is what stops two cycles writing over each other; on a
    page they are a conversation and nesting them would draw a thread
    inside a thread.
    """
    out = []
    for comment in project_comments(markdown, project):
        out.append({
            "author": "Edvard",
            "stamp": comment["stamp"],
            "blocks": render_blocks(comment["text"]),
        })
        for reply in comment.get("replies") or []:
            # Every reply block sits under a `#### Nova` heading, so the
            # author is Nova and is written here rather than read off the
            # reply. `split_replies`'s `author` field is a *role* --
            # `commentator` for the first block, `cycle` for later ones --
            # and passing it through would print the word "commentator" on
            # his page as if it were a name.
            out.append({
                "author": "Nova",
                "stamp": reply.get("stamp") or "",
                "blocks": render_blocks(reply.get("text") or ""),
            })
    return out


def _project_summary(items):
    """How a project is actually going, as numbers rather than columns.

    His idea #228 asks each project page for *"a backlog, roadmap and
    maybe a burndown chart"*, and this is the honest half of the burndown.
    A real burndown is a line: open rows against time. The rows carry one
    date each -- `Updated`, as `MM-DD` with no year -- and it is the date
    the row was last *touched*, not the date it was opened or closed, so
    there is no history in the data to draw a line from. Inventing one
    would mean back-dating rows off a field that does not mean what the
    chart would claim. So this reports where the project stands *today*,
    which is the burndown's last point and the only point I can measure.

    **Dropped rows leave the denominator.** `done` and `outdated` both
    close a row, but they are not the same news: `outdated` is "will never
    be built", which is scope removed rather than work delivered. Counting
    it as progress would let a project reach 100% by abandoning
    everything. So `percentDone` is `done / (done + open)` and `dropped`
    is reported beside it rather than folded into it.

    The open rows are counted by rating in `PRIORITY_ORDER`, which is the
    question a project page is actually asked -- "is there anything red
    under this project" -- and which four status columns do not answer,
    because a column is sorted by state and a person triaging is sorted by
    rating. Unrated is listed last with the word "Unrated" rather than a
    blank, since `PRIORITY_LABELS[""]` is the empty string and a count
    beside nothing reads as a rendering bug.
    """
    done = 0
    dropped = 0
    blocked = 0
    counts = {}
    for item in items:
        key = item.get("statusKey") or ""
        if key == "outdated":
            dropped += 1
            continue
        if key in _CLOSED_STATUS_KEYS:
            done += 1
            continue
        if key == "blocked-on-edvard":
            blocked += 1
        rating = item.get("priorityKey") or ""
        counts[rating] = counts.get(rating, 0) + 1
    open_rows = sum(counts.values())
    priorities = []
    for key in PRIORITY_ORDER:
        if counts.get(key):
            priorities.append({
                "key": key,
                "label": PRIORITY_LABELS.get(key, key),
                "count": counts[key],
            })
    if counts.get(""):
        priorities.append({"key": "", "label": "Unrated", "count": counts[""]})
    tracked = done + open_rows
    return {
        "total": len(items),
        "done": done,
        "dropped": dropped,
        "open": open_rows,
        "blocked": blocked,
        "percentDone": int(round(done * 100.0 / tracked)) if tracked else 0,
        "priorities": priorities,
    }


def _project_backlog(rows):
    """The project's open rows, in the order a cycle would take them.

    His idea #228 asks each project page for *"a backlog"*, and the half
    the status columns cannot give him is an *order*. Four columns say
    what state each row is in; none of them says which row is next, and
    on a project with forty open rows that is the only question the page
    is really asked.

    So this is the same ranking `tools.top_board_rows` prints when a cycle
    picks its work -- `nova_next.rank`, imported rather than re-spelled,
    because a page that ordered the backlog its own way would be telling
    him one thing while I did another. A row blocked on him sinks below
    every actionable one whatever its rating, then rating, then the oldest
    `Updated` first, then issues before ideas.

    **A row he has asked a question on comes first, and one raise is
    still missing and is named rather than quietly dropped.**
    `board_payload` now stamps `waiting` and `relayed` on his rows off
    the same `unanswered_comment_bodies` the tool uses, so the raise that
    matters here is live: a row where his comment is the last word tops
    this list, and a comment Sokrates relayed on his behalf does not.
    What is still absent is `heldBy` and `replyHeldBy`, the two sinks for
    a row or a comment another live cycle is holding, and both belong
    absent -- which cycle is mid-flight is not a fact about the project.
    `rank` reads them with `.get`, so their absence removes the sink and
    changes nothing else.

    Closed rows are left out: `done` is delivered and `outdated` is scope
    he dropped, and neither is something to do next. That is the same cut
    `_project_summary` makes for the bar above it, so the number beside
    this list and the "open" count in the summary can never disagree.
    """
    return rank([
        row for row in rows
        if not row.get("done")
        and (row.get("statusKey") or "") not in _CLOSED_STATUS_KEYS
    ])


# A row reference inside a `\`\`\`next` block's `board:` field, which the
# roadmap writes as free prose -- `issue #131, issue #130, idea #179`. The
# plural is allowed because the field is typed by hand and `issues #131`
# means the same thing; the number is what carries the identity.
_ROADMAP_ROW_RE = re.compile(r"\b(issue|idea)s?\s*#\s*(\d+)")


def _roadmap_refs(text):
    """A `board:` field -> `{("issue", 131), ("idea", 179)}`.

    A set rather than a list: the field is prose he and I both edit, and
    naming one row twice is a typo rather than two rows.
    """
    return {
        (match.group(1), int(match.group(2)))
        for match in _ROADMAP_ROW_RE.finditer(text or "")
    }


def _project_roadmap(rows, plan):
    """The ranked roadmap items that touch this project -- idea #228's last half.

    His idea asks each project page for *"a backlog, roadmap and maybe a
    burndown chart"*. The other three are built. The roadmap half stalled
    on something I wrote on this row on 09-01: *"a roadmap is a claim
    about when, and every row on your boards carries exactly one date --
    the day it was last touched"*, so I asked him for target dates or an
    order and the row has waited on that since.

    **That was the wrong thing to wait for.** An order already exists and
    he already reads it: `roadmap.md` is the five things I would do next,
    in order, with the reasoning under each, and it has been the strip at
    the top of `/plan` since Cycle 226. Every one of those items already
    names the rows it is about, in its `board:` field. So "what is this
    project's roadmap" is answerable today, from data both pages already
    hold: it is the ranked items whose rows are filed under this project,
    in the order the roadmap itself sets.

    A dated roadmap is still a better roadmap and still needs him. This
    is not that, and the page does not claim it is -- it says what is
    ranked, not when it lands.

    Two things this deliberately reports rather than hides. An item is
    included only when at least one row it names is *under this project*,
    because an item about `issue #131` is not Marcus's roadmap for
    happening to sit above it in the file. And `unattributed` counts the
    open items that name **no** row at all -- those can appear on no
    project page ever, which is a fact about the roadmap rather than
    about this project, and a subset with no complement beside it reads
    as a complete list.
    """
    documents = (plan or {}).get("documents") or []
    ranked = []
    for document in documents:
        if document.get("key") == "roadmap":
            ranked = document.get("ranked") or []
            break

    mine = {
        ("issue" if row.get("board") == "issue" else "idea", row.get("number"))
        for row in rows
    }
    items = []
    unattributed = 0
    for card in ranked:
        refs = _roadmap_refs(card.get("board"))
        if not refs:
            unattributed += 1
            continue
        here = sorted(ref for ref in refs if ref in mine)
        if not here:
            continue
        items.append({
            "rank": card.get("rank", ""),
            "title": card.get("title", ""),
            "claim": card.get("claim", ""),
            "statusSymbol": card.get("statusSymbol", ""),
            "statusLabel": card.get("statusLabel", ""),
            "rows": [{"board": kind, "number": number} for kind, number in here],
            "elsewhere": len(refs) - len(here),
        })
    return {"items": items, "unattributed": unattributed}


def project_payload(name=None):
    """One project's board rows, grouped by status (idea #92, phase 3).

    The plan is explicit that this phase adds no data: *"`/project/<name>`
    assembling what already exists ... A kanban view is the board rows
    grouped by status, which is a rendering of data phase 2 already
    produced. Nothing here is new data; if it turns out to need new data,
    that is a signal the phase is wrong."* So this reads the two board
    payloads the cache already holds and regroups them -- it parses
    nothing, fetches nothing, and adding a project is still typing a name
    into a `Project` cell.

    `name` is matched case-insensitively against the project cell,
    because the cell is free text he types on a phone and `nova` and
    `Nova` are one project. The name that comes back out is the one
    spelled on the rows, not the one asked for, so the page's heading
    reads the way his board reads.

    With no `name`, only `projects` is filled in -- that is the index at
    `/projects`, and it costs the same two payloads either way.
    """
    boards = {}
    known = []
    for board in ("issues", "ideas"):
        # `cached_payload` answers `(payload, body, etag)`, not the payload.
        # Binding the whole tuple is what made every request to this endpoint
        # 500 with `'tuple' object has no attribute 'get'` from the day the
        # page shipped -- and no test could see it, because the fixture
        # replaced `cached_payload` with one that returns the payload alone.
        # `"board:" + board`, the same key `_send_board` uses, and not the
        # bare name this read until 2026-08-28. Two spellings meant two
        # cache entries over one build: `/projects` paid the full cold
        # board build a second time after `warm_cache` had just paid it,
        # and every `invalidate("board:" + target)` in this module -- all
        # five of them -- missed this copy entirely, so a row edited from
        # the app stayed stale here until the next stale-while-revalidate
        # refresh happened to land.
        payload, _body, _etag = cached_payload(
            "board:" + board, lambda b=board: board_payload(b)
        )
        boards[board] = payload
        for project in board_projects(payload.get("items") or []):
            if project not in known:
                known.append(project)

    wanted = (name or "").strip()
    matched = None
    for project in known:
        if project.lower() == wanted.lower():
            matched = project
            break

    # The ratings, and the ordering they buy. `projects` stays a list of
    # plain names rather than becoming a list of objects: `app.js` and four
    # tests index it as strings, and a shape change there would be a
    # rewrite of the index page to deliver a sort. The ratings ride
    # alongside in `projectPriority`, keyed lowercase the same way the name
    # match above is, so a page can look one up without knowing how he
    # spelled it.
    meta = project_priorities()
    known = rank_projects(known, meta)
    result = {
        "projects": known,
        "projectPriority": {
            name.lower(): {
                "priority": (meta.get(name.lower()) or {}).get("priority") or "",
                "priorityKey": (meta.get(name.lower()) or {}).get("priorityKey") or "",
            }
            for name in known
        },
        "name": matched,
        "asked": wanted,
        "boards": {},
    }
    if not wanted:
        return result
    # The thread hangs off the name he asked for, not off `matched`, so a
    # project he has started talking about before filing a row under it
    # still shows its conversation instead of an empty page. `comments.md`
    # is read uncached for the same reason `/api/comments` is: it is the
    # one document he writes to from the app, and a stale thread reads as
    # a lost message.
    result["comments"] = _project_thread(comments_markdown(), wanted)
    # An unknown name is answered rather than 404'd: the page shows the
    # index with "no rows are filed under X yet", which is the true and
    # useful thing to say about a project he has typed into one cell and
    # not yet used. Returning nothing would make a fresh project look
    # like a broken link.
    matched_rows = []
    for board in ("issues", "ideas"):
        rows = [
            item for item in (boards[board].get("items") or [])
            if (item.get("project") or "").strip().lower() == wanted.lower()
        ]
        result["boards"][board] = {
            "total": len(rows),
            "columns": _project_columns(rows),
        }
        # Copies, tagged with which board they came from. `rank` breaks a
        # tie with `board == "issue"`, and the items here are the ones the
        # board cache holds -- mutating them in place would put the key on
        # the Issues and Ideas pages' own payloads too.
        matched_rows.extend(
            dict(item, board=("issue" if board == "issues" else "idea"))
            for item in rows
        )
    # One summary across both boards, not one each: he asked how the
    # *project* is going, and an issue and an idea are both a row of work
    # under it. The per-board totals are still on `boards[*].total` for the
    # tab labels.
    result["summary"] = _project_summary(matched_rows)
    # One ordered queue across both boards, for the same reason the summary
    # is one: "what is next on this project" does not have an issues answer
    # and an ideas answer.
    result["backlog"] = _project_backlog(matched_rows)
    # The roadmap half of the same idea, and the same cache the `/plan`
    # page reads -- `"plan"`, the key `_send_cached_json` already uses and
    # `invalidate("plan")` already clears. A second key over the same
    # build is the bug the board comment above describes, one page down.
    plan, _plan_body, _plan_etag = cached_payload("plan", plans_payload)
    result["roadmap"] = _project_roadmap(matched_rows, plan)
    return result


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


def next_up_payload():
    """What happens next, for the one reader who cannot run the tool.

    The owner, 2026-08-30 survey: *"I have no idea on your plan for the
    next cycle or what different projects are currently prioritised"*.
    Both halves are answered by `tools/top_board_rows.py` at the start of
    every cycle and neither has ever left the terminal.

    Three reads rather than the two board payloads the cache already
    holds, and that is deliberate: the ranking needs the board *markdown*
    (an unanswered comment is read off the write-up under the row, which
    the list payload does not carry), so reusing the cached payload would
    mean re-deriving `waiting` from a different shape of the same file --
    two answers to one question, which is the drift this repo keeps
    paying for. Cached like the rest at 15 seconds, which is short enough
    that a claim taken mid-cycle shows up while he is looking at it.
    """
    return next_payload(
        board_markdown("issues")[0],
        board_markdown("ideas")[0],
        claims_ledger_json(),
        datetime.now(OSLO),
    )


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
        threads = (
            payload.get("novaDetailComments") if mine else payload.get("detailComments")
        )
        blocks = (bodies or {}).get(str(item))
        row = next((i for i in rows or [] if i.get("number") == item), None)
        return {
            "name": payload.get("name"),
            # `comments` is the exchange appended under the write-up, drawn
            # as bubbles rather than as more prose. A row nobody has
            # commented on has no key here, so this is `[]` for most rows
            # and `found` deliberately does not consult it -- a row can be
            # real with an empty thread, and a thread cannot exist without
            # a row.
            "item": dict(
                row or {"number": item},
                blocks=blocks or [],
                comments=(threads or {}).get(str(item)) or [],
            ),
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
        # The conversations go out on the same terms as the write-ups they
        # were split out of: the list request is the one that must not
        # carry ~60KB of bodies, and a thread is part of that body.
        page["detailComments"] = {}
        page["novaDetailComments"] = {}
    return page


# How long a served payload may be before the next request kicks a
# refresh behind itself. Not a staleness budget for the owner -- the client
# polls, so what he sees is bounded by the poll interval plus one rebuild
# -- it is how often an *active* reader makes the site rebuild. At 15s a
# session polling every 30s rebuilds once per poll and never waits for one.
CACHE_FRESH_SECONDS = 15

#: How long the demo proxy waits on a dev server before giving up. Ten
#: seconds because a cold `npm run dev` compiles on the first request and
#: the browser's own patience is the only thing this is racing -- shorter
#: would turn a slow first paint into "the demo is broken", which is the
#: reading this feature can least afford.
DEMO_PROXY_TIMEOUT = 10

#: The most a proxied response may buffer. This pod has a 256Mi limit and
#: the whole body is read before a byte goes out, so an unbounded read is
#: not a slow demo -- it is the entire site dying on someone's screen
#: recording. 32MB is well past any page asset and well under the limit.
DEMO_MAX_BYTES = 32 * 1024 * 1024

#: How long a read of the demo registry is reused. The registry is one
#: CouchDB round trip and a demo page is forty assets, so uncached this
#: costs forty vault fetches per page load -- each with `vault.py`'s 60s
#: timeout, which is six times the upstream timeout chosen right above to
#: stop a demo reading as broken. Two seconds because the thing it goes
#: stale against is a cycle running `tools.demo start`, which is a human
#: waiting at a terminal, not a page being loaded.
DEMO_REGISTRY_TTL = 2

#: Response headers worth carrying across. `Content-Encoding` is the one
#: that actually bites: an upstream serving a precompressed asset without
#: it renders as binary in the browser. The rest are cheap and correct.
DEMO_PASS_HEADERS = ("Content-Encoding", "Content-Disposition",
                     "Accept-Ranges", "Vary", "Content-Language")

#: Request headers worth carrying to the demo. `Range` is what lets a
#: `<video>` in a demo seek at all; `Accept` and `Accept-Language` are
#: what a content-negotiating dev server reads.
DEMO_FORWARD_HEADERS = ("Range", "Accept", "Accept-Language")

_demo_registry_cache = {"at": 0.0, "text": None}
_demo_registry_lock = threading.Lock()

#: When anyone last asked for each demo, and when this process started.
#: Idea #136's idle half: nothing else in the system knows whether a demo
#: is being looked at, because every request for one arrives here. It is
#: deliberately in memory rather than in the registry -- a demo page is
#: forty assets and the registry is a CouchDB document, so writing it
#: through would be forty round trips per page load, which is the cost
#: `DEMO_REGISTRY_TTL` above exists to avoid. The consequence is that a
#: site roll forgets it, which is why the start time is published
#: alongside: `nova_demos.idle_seconds` uses it as a floor so a roll
#: restarts the idle clock instead of making every demo instantly
#: reapable.
_demo_last_seen = {}
_demo_last_seen_lock = threading.Lock()
SITE_STARTED_AT = time.time()


def demo_activity():
    """What `/api/demo/activity` answers. Epoch seconds, UTC-agnostic."""
    with _demo_last_seen_lock:
        return {"started_at": SITE_STARTED_AT, "last_seen": dict(_demo_last_seen)}


def _demo_registry():
    """The registry text, reused for `DEMO_REGISTRY_TTL` seconds."""
    now = time.time()
    with _demo_registry_lock:
        if _demo_registry_cache["text"] is not None and \
                now - _demo_registry_cache["at"] < DEMO_REGISTRY_TTL:
            return _demo_registry_cache["text"]
    text = vault_read_path(DEMOS_PATH)
    with _demo_registry_lock:
        _demo_registry_cache.update(at=now, text=text)
    return text


#: Slugs this process has already written a durable `opened_at` for, or has
#: tried to and is not going to try again this second. It is a cache of a
#: fact that cannot go back -- a demo does not become unopened -- so a stale
#: entry can only cost a write that was already unnecessary.
_demo_opened_marked = set()
_demo_opened_lock = threading.Lock()


def _record_durable_open(slug):
    """Write `opened_at` into the registry row for `slug`, once, off-thread.

    `_demo_last_seen` above answers *how recently* somebody looked and lives
    in this pod's memory on purpose -- forty assets a page, one CouchDB
    document. **Whether anyone has ever looked is a different question and
    the memory is the wrong place for it**: a site roll wiped it, so every
    demo went back to "no recorded open" and off the two-hour idle clock
    onto the eighteen-hour one, and `tools.demo list` -- the one thing that
    says whether a hand-over actually reached the owner -- printed "no
    recorded open" about a demo he had opened. Cycle 606 filed it; this is
    it fixed.

    Three things it is careful about, in the order they bite.

    **It is one write per slug per process, not per request.** The set is
    claimed before the vault is touched, so the forty assets of a demo page
    produce one attempt between them.

    **It runs on its own thread and never on the request's.** A vault write
    is a CouchDB round trip against `vault.py`'s 60s timeout, and the thing
    waiting is a browser loading a demo. Failing has to be free.

    **A lost compare-and-swap is not retried here.** `tools.demo` writes
    this same document to allocate ports, and re-reading and re-applying
    inside a request handler is how a proxy grows a retry loop against a
    document a cycle is holding. The slug is dropped from the set instead,
    so the next asset on the same page tries again -- and if the whole page
    loses, the demo simply stays on the long clock, which is where it was
    before this existed and is the safe direction.
    """
    with _demo_opened_lock:
        if slug in _demo_opened_marked:
            return
        _demo_opened_marked.add(slug)
    try:
        text, rev = vault_read_path_rev(DEMOS_PATH)
        registry = load_demos(text)
        if not mark_opened(registry, slug):
            return
        # **A lost swap is a return value here, not an exception**, and my
        # reviewer caught this version writing the `except` branch as if it
        # were both. `vault_write_path` answers `"written"` or a
        # `"FAILED(...)"` string -- `vault.py`'s own contract, and the one
        # every other caller in this repo branches on -- so a 409 would have
        # returned normally, left the slug marked, and silently reproduced
        # the exact bug this function exists to fix.
        result = vault_write_path(DEMOS_PATH, dumps_demos(registry), if_rev=rev)
    except Exception as e:
        result = f"FAILED({e})"
    if result != "written":
        log(f"nova-site could not record the open of demo {slug!r}: {result}")
        with _demo_opened_lock:
            _demo_opened_marked.discard(slug)
        return
    # The cached registry text predates the write by definition, and
    # `_serve_demo` reads `opened_at` off it to decide whether to call this
    # at all. Left alone it would re-enter for `DEMO_REGISTRY_TTL` seconds
    # and be stopped only by the set above -- which is correct and is also
    # the guard, not the answer.
    with _demo_registry_lock:
        _demo_registry_cache.update(at=0.0, text=None)


def _start_durable_open(slug):
    """Run `_record_durable_open` off the request thread.

    One line, and it is its own function so that a test of the proxy can
    replace the *spawning* without replacing what gets spawned. Patching
    `_record_durable_open` itself still starts a thread, and a thread that
    outlives the test has outlived the test's patches -- which is the exact
    failure `tests/conftest.py`'s teardown check exists to catch, and which
    it caught here on the first run.
    """
    threading.Thread(target=_record_durable_open, args=(slug,),
                     daemon=True).start()


def _demo_opener():
    """A urllib opener that does not follow redirects. See `_serve_demo`."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None
    return urllib.request.build_opener(_NoRedirect)

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
    # The cache key `_send_board` builds, spelled out rather than derived,
    # so the scanner in the test has a literal to compare against.
    ("board:issues", lambda: board_payload("issues")),
    ("board:ideas", lambda: board_payload("ideas")),
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

    Boards were left out of this list on the same measurement, and the
    measurement has since moved by an order of magnitude. On 2026-08-12
    they were 0.53s and 0.39s cold, "a wait nobody has reported". Measured
    against the live pod 2026-08-28, six minutes into a process that had
    served nothing since it started: `/api/board?name=ideas&limit=30`
    answered in **5.05s**, and `/api/board?name=issues` in **3.15s**,
    against 0.03-0.09s on every request after. The boards grew; the
    justification did not follow them. The owner reported it as "The Nova
    app has become extremely slow. Opening, navigating, loading comments,
    anything" (`issues.md`, 2026-08-27), and *navigating* is this: a
    sidebar press into a board, five seconds, on the first press after
    almost any cycle merges.

    So they are warmed now. `/projects` reads the same two payloads and
    was spelling the key without the `board:` prefix, which made it a
    second cache entry over one build and put it outside every
    `invalidate` in this module; it uses the same key now, so warming
    reaches it too.

    The general lesson is worth more than the two lines: **an exclusion justified by a number needs the number
    re-read, not the reasoning re-read.** Nothing here was wrong when it
    was written and nothing about it looked stale afterwards.

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
                 record_age=None, search=None, asks=False):
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

    No `limit` means every entry. That is this function's contract and it
    is deliberately not where the bound lives -- `_journal_limit` supplies
    `JOURNAL_DEFAULT_LIMIT` at the HTTP edge, so an unwindowed call here is
    still how `?limit=all` and every in-process caller reads the corpus.

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

    That last sentence is what makes the HTTP default safe on a search.
    `/api/journal?q=X` with no `limit` now returns 20 matches rather than
    all of them, which is a real behaviour change and not one the page can
    see -- `app.js` resets its window to `PAGE` and always sends `q` and
    `limit` together. It is safe because `total` is still the true match
    count, so "N entries mention X" stays correct while the page holds
    twenty of them; it would not be safe if a caller counted `entries`.
    A reviewer found this on runner#452 and nothing covered it, which is
    why it is written down here with a test rather than left to be
    rediscovered.

    `asks` is the owner's capture of 2026-09-01: *"Drop the current 'needs
    me' functionality (the yellow 'N WAITING ON YOU' button/list) ... and
    replace it with a simple filter that just lists the journal entries
    that need his input."* It is a filter over the same corpus rather than
    a new payload, so `/asks` is the feed with one predicate applied and
    every card renders exactly as it does on the front page.

    **It selects every card that raised an ask, answered or not**, which is
    the same call `open_asks` makes one layer down and for the same reason:
    an ask is answered when he has commented on that card, comments live in
    a different document with its own cache, and folding them in here would
    keep the filter claiming he had not replied until the *journal* cache
    next rebuilt. The client holds both payloads and intersects them.

    **A card a later cycle declared resolved is dropped**, the one
    exception, and it is the same call `open_asks` makes: that fact lives in
    the journal like everything else here, so applying it costs no second
    payload and cannot go stale against one. The owner asked for exactly
    this (`issues.md` 2026-09-02) -- an ask a later cycle already settled
    was staying on this page until he commented on a card about finished
    work.

    Like `cycle`, it ignores `offset` and `limit` -- the whole point is to
    see all of them at once, and there are eight, not eight hundred.
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
    if asks:
        # `cycle is not None` for the reason `open_asks` skips those
        # entries: an entry with no cycle number has no card of its own to
        # reply on, so an ask written into one has nowhere to be answered.
        closed = resolved_ask_cycles(entries)
        picked = [
            entry for entry in entries
            if entry.get("ask")
            and entry.get("cycle") is not None
            and entry["cycle"] not in closed
        ]
        return {
            "entries": [_rendered(entry) for entry in picked],
            "status": _with_silence(
                payload.get("status", {}), now, record_age=record_age
            ),
            # The number of asks, not the number of entries: the page says
            # how many cards it is showing, and `total` is what it reads.
            "total": len(picked),
            "asks": True,
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

    No `limit` means every line. That reason used to be given as "an
    app.js served out of a service worker's cache from before windowing
    shipped asks the old way", copied from `journal_page`, and
    `journal_page` no longer says it -- the bound there lives at the HTTP
    edge and that docstring says so. Two contradictory explanations of the
    same `limit is None` branch, one file apart, is the stale-prose trap
    this codebase keeps paying for, so this one now carries its own real
    reason: `_send_digest` deliberately does not default the window, and
    `limit is None` is the branch that serves the file rather than asking
    the journal which cycles to summarise. See `_send_digest`.
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


def journal_descriptor(page, limit, offset, cycle, search=None, asks=False):
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
    if asks:
        # Its own window key, not `0:None`: the ask filter and an unwindowed
        # read of the whole corpus are different answers off the same base
        # etag, and without this the second one asked for is served 304 with
        # the first one's rows still on screen.
        window = "asks"
    else:
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


# What `/api/journal` sends when the caller did not say how many entries
# it wants. It sent every entry ever written until 2026-08-27 -- 4,031,475
# bytes over 600 entries, measured against the live pod at 13:05 Oslo that
# day, growing by one entry an hour -- and `nova-site` was OOMKilled on it
# the day before, which is what took the pod's memory ceiling from 256Mi to
# 512Mi. Raising the ceiling was a bandage on a payload with no bound at
# all; this is the bound.
#
# 20 is not a number invented here: it is `PAGE` in `app.js`, the window
# the page asks for on a cold load and widens by on every tap of the pager.
# So the client sees exactly what it saw before, and the callers that never
# said what they wanted -- `site_check`, and any probe pointed at this
# endpoint -- get 122,274 bytes instead of 4,031,475.
#
# Nothing loses the whole corpus: `?limit=all` still serves every entry, and
# `?cycle=N` still ignores the window entirely so a deep link into an old
# entry works on a cold load. The capability is intact and now has to be
# asked for, which is the difference between a default and a cap.
#
# So this is a default, not a ceiling, and the distinction is worth being
# exact about: `?limit=all` still builds the whole 4MB body in memory, and
# a reviewer was right that calling this "the bound" overstates what it
# does. What it removes is the *accidental* route to that spike -- nothing
# in this repo asks for `all`, and everything that used to get it was
# asking for nothing in particular. A real ceiling would have to refuse or
# stream, and neither belongs on a corpus the owner is entitled to read
# whole.
JOURNAL_DEFAULT_LIMIT = 20


def _journal_limit(query):
    """`?limit=` for the journal: an int, `None` for `all`, else the default.

    `_int_param` cannot express this on its own -- it answers `default` for
    anything it cannot parse, so `all` and a typo would mean the same thing.
    The literal is checked before the parse so that "every entry" stays
    reachable and stays deliberate.
    """
    raw = (query.get("limit") or [None])[0]
    if raw is not None and raw.strip().lower() == "all":
        return None
    return _int_param(query, "limit", JOURNAL_DEFAULT_LIMIT)


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
        """`/api/journal`, sliced to the window the client asked for.

        A caller that asks for no window gets `JOURNAL_DEFAULT_LIMIT` rather
        than the whole archive; see that constant for the bytes.
        """
        payload, _, base, age = cached_entry("journal", journal_payload)
        cycle = _int_param(query, "cycle", None)
        limit = _journal_limit(query)
        offset = _int_param(query, "offset", 0)
        search = (query.get("q") or [None])[0]
        asks = (query.get("asks") or ["0"])[0] == "1"
        page = journal_page(
            payload, limit=limit, offset=offset, cycle=cycle, record_age=age,
            search=search, asks=asks,
        )
        etag = page_etag(
            base, journal_descriptor(page, limit, offset, cycle, search, asks)
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

        It deliberately does **not** take the feed's default window, and the
        1,681,533 bytes it answers unwindowed (13:07 Oslo, 2026-08-27) are
        left on the floor on purpose. `limit=None` here does not mean "a
        window nobody named" -- it takes the branch that skips the journal
        entirely and serves the file, and a default would route those callers
        through `journal_page` instead. With an unreadable or empty journal
        that resolves to an empty cycle range, so the digest answers 200 with
        zero lines: a plausible wrong answer in place of the whole file,
        which is the silent-fallback failure this codebase has paid for more
        than any other. `test_api_digest_returns_the_handoff_and_the_digest_lines`
        caught exactly that when Cycle 535 tried it. Bounding this one wants
        a slice over `lines` that does not consult the feed; it is written up
        rather than guessed at here.
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
        # Before the `rstrip` below, deliberately. `/demo/foo/` and
        # `/demo/foo` are different URLs to a browser resolving a relative
        # asset -- `style.css` under the first is `/demo/foo/style.css` and
        # under the second is `/demo/style.css` -- so this route has to see
        # the trailing slash the rest of the site is happy to throw away.
        if path == "/demo" or path.startswith("/demo/"):
            self._serve_demo(path, raw_query)
            return
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
            if path == "/api/catalog":
                # Cached like the boards, and this one changes even less
                # often: `nova/catalog.md` is rewritten when a cycle runs
                # `tools.catalog`, not when anybody taps. 2.3KB of
                # markdown, so no window and no partial read.
                self._send_cached_json("catalog", catalog_payload)
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
            if path == "/api/next":
                self._send_cached_json("next", next_up_payload)
                return
            if path == "/api/comments":
                # Still deliberately not cached, and the reason is
                # unchanged: this is the one payload that changes
                # underneath itself, from the reply worker in this same
                # process and from the box that has just posted, so a
                # CACHE_FRESH_SECONDS window would cost a comment looking
                # lost. It is built on every request, exactly as before.
                #
                # What did change is the sentence that used to sit here --
                # "it is 6KB and 20-78ms, so there is nothing here to
                # save". Measured against the live pod 2026-08-28 21:37
                # Oslo: **195,114 bytes, 57,466 gzipped, 0.40-0.81s**.
                # `comments.md` is 157KB and grows every cycle, so that
                # number was a fact with an expiry date and nothing was
                # reading it. This endpoint is one of `fetchAll`'s three
                # boot requests and was the only response on the whole
                # site with no ETag -- every other one, including 167KB of
                # `app.js`, revalidates to a 0-byte 304.
                #
                # An ETag is not a freshness window. The body is still
                # rebuilt per request and a changed thread is still sent
                # whole; what stops is re-sending 57KB to a client that
                # already has those exact bytes.
                # `no-cache` is the same header `_send_static` sends and it
                # is not a cache window either: it means *store this, and
                # revalidate before every use*. Without it the browser is
                # left to heuristics, and with no `Last-Modified` to guess
                # from it may decline to store the response at all -- in
                # which case it never sends `If-None-Match`, the 304 below
                # never fires, and this whole change is dead on his phone
                # while passing every test here.
                # `version` is inside the body as well as on the header,
                # for `_send_board`'s reason: `fetchVersioned` in app.js
                # reads the etag off the *payload* and echoes it as
                # `If-None-Match`, because it does not trust the browser's
                # own cache to revalidate a poll. So the header alone would
                # have been a 304 nothing ever asks for. The hash is taken
                # before `version` is inserted -- it cannot cover a field
                # derived from itself -- which is the same order
                # `_send_board` uses.
                payload = comments_payload()
                etag = '"' + hashlib.sha256(
                    json.dumps(payload).encode("utf-8")
                ).hexdigest()[:16] + '"'
                payload["version"] = etag
                self._send_json_or_304(
                    json.dumps(payload), etag, cache_control="no-cache"
                )
                return
            if path == "/api/ask":
                # Never cached, for `/api/comments`' reason and one more:
                # this endpoint is polled *because* it is expected to
                # change, and a CACHE_FRESH_SECONDS window would show
                # the owner a thread that stays "waiting" after the answer
                # has already landed.
                # `limit` is how he scrolls back: the dock opens on
                # `MAX_THREAD` and asks for one page more each time he
                # reaches the top. `nova_ask.thread` clamps it.
                self._send_json(200, ask_thread((query.get("limit") or [""])[0]))
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
                # Same `limit` as `/api/ask`, same clamp, same reason.
                payload = conversation_thread(
                    wanted, (query.get("limit") or [""])[0])
                # Opening a thread is what marks it seen. It is here rather
                # than inside `conversation_thread` so the reader stays a
                # reader: this route is the only caller that means "he is
                # looking at it", and the dock's own poller comes through it.
                # `mark_seen` no-ops unless a new message has actually
                # arrived, so a thread sitting open costs no vault writes.
                try:
                    mark_conversation_seen(
                        wanted, payload.get("newestAt"),
                        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
                except Exception as e:
                    # Never an error to him: a marker that did not store
                    # leaves the row highlighted, which is the harmless
                    # direction, and the thread he asked for is already built.
                    log(f"nova-site conversation marker failed: {e}")
                self._send_json(200, payload)
                return
            if path == "/api/conversations/model":
                # Which model answers in this thread, plus the catalog he
                # may switch it to. Its own route rather than a field on
                # `/api/conversations/thread` because that one is polled
                # every four seconds and this answer only changes when he
                # changes it -- `nova_conversations.model_choice` carries
                # the whole reasoning, including why the conversation
                # listing is the only place the current model can be read.
                self._send_json(200, conversation_model_choice(
                    (query.get("id") or [""])[0]))
                return
            if path == "/api/conversations/step":
                # What one tool call returned, asked for when he opens that
                # row in the drawer. It is a separate route from the thread
                # for one measured reason, which
                # `nova_conversations._steps` carries in full: an output is
                # capped at 20,000 characters and a window holds forty
                # calls, so folding outputs into the thread would put up to
                # 800KB on his phone for a drawer he may never open.
                found = conversation_step_output(
                    (query.get("id") or [""])[0],
                    (query.get("tool") or [""])[0],
                    (query.get("limit") or [""])[0])
                if found is None:
                    # 404 rather than an empty body: a call that returned
                    # nothing and a call that has scrolled out of Agora's
                    # retention are different answers, and the drawer says
                    # which.
                    self._send_json(404, {"error": "no such tool call in this thread"})
                    return
                self._send_json(200, found)
                return
            if path == "/api/push/key":
                # Agora owns the VAPID keypair and the subscription store;
                # this site only needs its own origin to appear in that
                # store. See nova_push for why that is a proxy and not a
                # second push service.
                self._send_json(200, vapid_key())
                return
            if path == "/api/heartbeats":
                # Not cached, for `/api/conversations`' reason: this is the
                # list he opens to see whether the loop is still running, so
                # a CACHE_FRESH_SECONDS window would show him a `lastRunAt`
                # from before the run he is asking about.
                self._send_json(200, heartbeat_list())
                return
            if path == "/api/project":
                # Not cached itself -- `project_payload` reads the two
                # board payloads through `cached_payload`, so the caching
                # already happened one layer down and a second cache here
                # would only add a window in which a row he just re-filed
                # is still on the old project page.
                name = (query.get("name") or [""])[0]
                self._send_json(200, project_payload(name))
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
            if path == "/api/demo/activity":
                # Never cached: `tools.demo reap --idle` decides whether to
                # kill a running demo on this answer, and a stale one would
                # kill a demo somebody asked for CACHE_FRESH_SECONDS ago.
                self._send_json(200, demo_activity())
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
                # Idea #117: every heartbeat's cadence against when it last
                # fired. `tools.heartbeat_health` has judged this since Cycle
                # 475, but only when a cycle runs it -- so the one heartbeat
                # it could never vouch for is the hourly loop's own, because a
                # loop that has stopped runs no check. This process is alive
                # on its own schedule, so it can.
                #
                # **It is a sibling of `ok`, never a term in it.** A weekly
                # heartbeat switched off does not make this service unhealthy,
                # and `tools.site_check` reads the top-level `ok` as the
                # database verdict; folding the two together would merge two
                # causes with different fixes into one red light, which is the
                # mistake `agentic_health` had to unpick a layer down. The 503
                # stays a database 503 for the same reason.
                health["heartbeats"] = liveness()
                self._send_json(200 if health["ok"] else 503, health)
                return
        except Exception as e:
            log(f"nova-site {path} failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        self._send_json(404, {"error": "not found"})

    def _serve_demo(self, path, raw_query):
        """`/demo/<slug>/...` -- reverse-proxy to a demo's dev server.

        Idea #135. The owner asked to be handed a link in a meeting and open
        it; the link is a path on the hostname he already has installed,
        rather than a tailnet device per demo that would outlive the demo by
        weeks. `nova_demos` holds slug -> pod IP and port; the hop from this
        pod to the bridge pod's IP is the one Cycle 442 measured rather than
        assumed.

        Three things this deliberately does not do, so nobody has to
        rediscover them from a blank page:

        * **GET only.** A dev server's HTML, JS, CSS and images are all GETs,
          which is the whole first slice. A demo that posts a form gets a 405
          that says so, which is a readable failure rather than a hang.
        * **No websockets.** `BaseHTTPRequestHandler` cannot upgrade a
          connection, so Vite's hot reload will not connect. The page still
          loads; it just does not live-reload.
        * **No rewriting of the body.** An asset referenced as `/style.css`
          resolves against Nova, not the demo, and 404s. Relative URLs work.
          Rewriting HTML to fix that is a guess about someone else's markup,
          and the redirect below removes the common cause.
        """
        rest = path[len("/demo"):]
        slug, _, upstream_path = rest.lstrip("/").partition("/")
        if not slug:
            self._send_json(404, {"error": "no demo named in the path"})
            return
        # `/demo/foo` -> `/demo/foo/`, so the browser resolves the page's
        # relative assets under the demo rather than under the site root.
        if "/" not in rest.lstrip("/"):
            self.send_response(302)
            self.send_header("Location", f"/demo/{slug}/" + (f"?{raw_query}" if raw_query else ""))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        try:
            demo = lookup_demo(load_demos(_demo_registry()), slug)
        except Exception as e:
            log(f"nova-site /demo/{slug} registry read failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        if demo is None:
            self._send_json(404, {"error": f"no demo named {slug!r} is running"})
            return
        # Recorded here rather than at the top of the method, deliberately:
        # a request for a slug nobody registered is not somebody looking at
        # a demo, and counting it would keep a row alive that no longer
        # exists. Recorded before the upstream call rather than after, also
        # deliberately -- a demo that is being watched while its dev server
        # is briefly failing is still being watched, and reaping it because
        # its own 502s did not count is the wrong answer.
        # And only a browser counts. A cycle fetching its own demo through
        # this route to prove it works -- which `prompt.md` requires before
        # the link is handed over -- would otherwise record the demo as
        # already opened and put it back on the short idle clock. See
        # `nova_demos.opened_by_a_person`.
        if opened_by_a_person(self.headers.get("User-Agent")):
            with _demo_last_seen_lock:
                _demo_last_seen[slug] = time.time()
            # And the durable half, which survives this pod. `demo` came out
            # of a cache up to `DEMO_REGISTRY_TTL` seconds old, so this test
            # is only an optimisation -- `_record_durable_open` re-reads and
            # is the thing that decides.
            if not demo.get(OPENED_AT) and slug not in _demo_opened_marked:
                _start_durable_open(slug)
        # `.get`, not `[...]`, because this route is dispatched above
        # `do_GET`'s `try` -- a hand-edited registry row missing either
        # field would be a traceback and a dropped connection rather than
        # the readable failure the docstring above promises.
        host, port = demo.get("host"), demo.get("port")
        if not host or not port:
            self._send_json(502, {
                "error": f"the registry row for {slug!r} has no host or port"})
            return
        origin = f"http://{host}:{port}"
        target = f"{origin}/{upstream_path}"
        if raw_query:
            target += f"?{raw_query}"
        request = urllib.request.Request(target)
        for name in DEMO_FORWARD_HEADERS:
            if self.headers.get(name):
                request.add_header(name, self.headers[name])
        try:
            with _demo_opener().open(request, timeout=DEMO_PROXY_TIMEOUT) as up:
                status, headers, body = up.status, up.headers, up.read(DEMO_MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            # A 404 from the dev server is the demo's answer, not a fault
            # here, so it is passed through rather than replaced.
            status, headers, body = e.code, e.headers, e.read(DEMO_MAX_BYTES + 1)
        except Exception as e:
            log(f"nova-site /demo/{slug} upstream failed: {e}")
            self._send_json(502, {
                "error": f"demo {slug!r} is registered on "
                         f"{demo['host']}:{demo['port']} but did not answer: {e}"[:300]})
            return
        if len(body) > DEMO_MAX_BYTES:
            self._send_json(502, {
                "error": f"{target} is larger than the {DEMO_MAX_BYTES}-byte "
                         "cap the demo proxy buffers; this pod has 256Mi"})
            return
        content_type = headers.get("Content-Type", "application/octet-stream")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        # A redirect is passed to the browser rather than followed here,
        # with its target moved back under `/demo/<slug>/`. Following it
        # server-side returns the right bytes at the wrong URL: a static
        # server answers `/sub` with a 301 to `/sub/`, and a browser that
        # never saw the redirect resolves that page's relative links one
        # directory too high. The rewrite is only applied to a same-origin
        # path, so a demo redirecting off-site still goes off-site.
        location = headers.get("Location")
        if location:
            # Two forms mean "somewhere else inside this demo" and both have
            # to come back under the prefix. A framework that has never
            # heard of a reverse proxy writes the second one, and sending it
            # unrewritten points his phone at a pod IP it cannot route to.
            # `//host/x` is deliberately not rewritten: it is protocol-
            # relative and genuinely off-site.
            if location.startswith(origin):
                location = f"/demo/{slug}" + (location[len(origin):] or "/")
            elif location.startswith("/") and not location.startswith("//"):
                location = f"/demo/{slug}{location}"
            self.send_header("Location", location)
        self.send_header("Content-Length", str(len(body)))
        # A demo is a thing being edited while it is looked at. Anything
        # cached here is the owner reloading and seeing the previous build,
        # which reads as "the demo is broken" and is the one failure this
        # feature cannot afford.
        self.send_header("Cache-Control", "no-store")
        for name in DEMO_PASS_HEADERS:
            if headers.get(name):
                self.send_header(name, headers[name])
        self.end_headers()
        self.wfile.write(body)

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

    def _post_goal_status(self, payload):
        """`POST /api/goal/status` -- the owner ticks or strikes one goal.

        `goals.md` has told him since 2026-08-16 that *"nothing here is
        settled until you edit it"*, and the only way to edit it was
        Obsidian on a phone. He has not, in ten days, which is why the top
        row of his own board (idea #38) has sat at "In progress" with its
        remaining half described as his. Goal **G2** counts the things he
        still has to leave this app to do and names this as one of four.

        Addressing is by the goal's `name`, which is the block's only
        required field and the exact string the row on `/plan` is drawn
        from -- so a stale page addresses a goal that has been renamed and
        gets the same 409 a stale capture reply gets. Nothing failed; the
        address moved.
        """
        name = payload.get("name")
        status = payload.get("status")
        if not isinstance(name, str) or not name.strip():
            self._send_json(400, {"error": "name must be a non-empty string"})
            return
        if status not in GOAL_STATUSES:
            self._send_json(400, {"error": f"status must be one of {list(GOAL_STATUSES)}"})
            return

        try:
            ok, message = set_goal_status(name, status)
        except Exception as e:
            log(f"nova-site goal status failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            invalidate("plan")

        audit(
            "Nova",
            "",
            "nova_plan",
            f"Goal {name} -> {status} · {'ok' if ok else message}",
            before=name[:MAX_BODY_BYTES],
            after=status,
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        stale = "no longer" in message
        self._send_json(409 if stale else 502, {"ok": False, "message": message})

    def _post_promote(self, payload):
        """`POST /api/capture/promote` -- turn one capture into a board row.

        The owner, capture 2026-08-26: *"they do no seem to just stay
        forever in the 'not boarded yet' box as unrated. Thats not what
        the box is for. This a re ideas you have not seen before and you
        pick it up, prioritised them and make them as their own nice item
        like the rest."*

        **`priority` is optional and its absence is not "unrated".** Left
        out, the capture's own rating rides across; sent, it overrides,
        because the ask is that the thing gets *rated* on the way past.
        `""` is a real value meaning no rating, which is why the check
        below is `is not None` rather than a truth test -- the same
        distinction `canonical_priority` already draws.
        """
        target = payload.get("target")
        index = payload.get("index")
        original = payload.get("original")
        priority = payload.get("priority")
        if target not in BOARD_PATHS:
            self._send_json(400, {"error": f"target must be one of {sorted(BOARD_PATHS)}"})
            return
        # `True` is an int in Python and would silently address capture 1.
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            self._send_json(400, {"error": "index must be a non-negative number"})
            return
        if not isinstance(original, str) or not original.strip():
            self._send_json(400, {"error": "original must be a non-empty string"})
            return
        if priority is not None and canonical_priority(priority) is None:
            self._send_json(400, {"error": "priority must be one of the four ratings"})
            return

        try:
            ok, message = promote_capture(target, index, original, priority)
        except Exception as e:
            log(f"nova-site capture promote failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            # The bullet left the capture list and a row arrived on the
            # board, and both of those are drawn by the same cached page.
            _invalidate_capture_target(target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Promote a capture in {target} to a board row · {'ok' if ok else message}",
            before=original[:MAX_BODY_BYTES],
            after=message,
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        stale = STALE_CAPTURE in message
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

    def _post_pool_comment(self, payload):
        """`POST /api/pool/comment` -- the owner noting something on a candidate.

        Idea #92's third answer, and the one phase 1 shipped as Skip:
        *"i can approve or comment on these"*. Skip writes nothing, so
        anything typed into the comment box on a card he was not ready to
        decide went nowhere. This keeps the candidate in the pool and puts
        the text on it, where the Tue/Thu/Sat refill run reads it.

        Same `index` + `title` address as `/api/pool/decide` and the same
        409 on a stale one, because the failure is identical: a refill that
        ran while the page was open renumbers everything below it, and
        annotating the wrong idea is worse than refusing.
        """
        index = payload.get("index")
        title = payload.get("title")
        comment = payload.get("comment") or ""
        if not isinstance(title, str) or not title.strip():
            self._send_json(400, {"error": "title must be a non-empty string"})
            return
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            self._send_json(400, {"error": "index must be a non-negative number"})
            return
        if not isinstance(comment, str) or not comment.strip():
            self._send_json(400, {"error": "comment must be a non-empty string"})
            return
        if len(comment.encode("utf-8")) > MAX_BODY_BYTES:
            self._send_json(400, {"error": "comment is too long"})
            return

        dated = datetime.now(OSLO).strftime("%Y-%m-%d")
        try:
            ok, message = pool_comment(index, title, comment, dated)
        except Exception as e:
            log(f"nova-site pool comment failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_idea_pool",
            f"Pool comment · {'ok' if ok else message}",
            before=title[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        if ok:
            self._send_json(200, {"ok": True, "message": message})
            return
        # Nothing here writes to his ideas file, so there is no half-done
        # state and no cache to invalidate -- a failure means the pool
        # document is exactly as it was.
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

    def _post_project(self, payload):
        """`POST /api/board/project` -- the owner moving a row to a project.

        His capture, 2026-09-01, rated \U0001f534 Immediately: *"I/you should
        easily be able to assign issues and ideas to projects, and change
        project if assigned wrongly ... I/you should easily be able to
        create new projects."* Every `Project` cell on both boards today
        was written by a cycle running `tools.board_project`, so this is
        the first way he can disagree with one without opening Obsidian.

        **The one place this deliberately differs from `_post_priority`:
        the name is not checked against a list.** A rating is one of four
        labels, so writing anything else through would be a client
        putting arbitrary text into a cell of his file. A project name is
        free text by design -- `board_projects` derives the project list
        from the cells, so typing a name no row carries is how a project
        is created, and a fixed set here would be the constant that
        design ruled out. What bounds it is `set_row_project`, which
        refuses a `|`, a line break, a `*` and anything past 40
        characters -- the four things that would break out of the cell
        rather than merely be unexpected in it.

        The other two boundaries are unchanged and are the ones that
        matter for a path: `target` is a key into `BOARD_PATHS`, never a
        path, and `number` is `int` and not `bool`, since `True` is an
        int in Python and would address row 1.
        """
        target = payload.get("target")
        number = payload.get("number")
        project = payload.get("project")
        if target not in BOARD_PATHS:
            self._send_json(400, {"error": f"target must be one of {sorted(BOARD_PATHS)}"})
            return
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            self._send_json(400, {"error": "number must be a positive integer"})
            return
        if not isinstance(project, str) or not project.strip():
            self._send_json(400, {"error": "project must be a non-empty string"})
            return
        project = project.strip()

        try:
            ok, message = set_project(target, number, project)
        except Exception as e:
            log(f"nova-site project failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            # Two caches, not one. `/api/board` is the page he is looking
            # at; `/api/project` is built by re-reading both boards
            # through the same `board:<name>` keys, so invalidating the
            # board is what makes a brand-new project name appear in the
            # index -- there is no separate project cache to clear.
            invalidate("board:" + target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Move #{number} on {target} \u00b7 {'ok' if ok else message}",
            after=project,
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        self._send_json(200 if ok else 502, {"ok": ok, "message": message})

    def _post_project_priority(self, payload):
        """`POST /api/project/priority` -- rating a project, not a row.

        The last line of his 2026-09-01 capture: *"Each project should also
        be able to be assigned a priority, making one project and its tasks
        more important than others."*

        **This writes to a document neither board owns, and that is the
        decision worth naming.** Cycle 770 built the project *picker* on the
        deliberate design that the set of projects is read off the `Project`
        cells, so there is no second list that can disagree with the rows. A
        project-level rating cannot live there: it belongs to the project
        rather than to any one of its rows, and writing it onto every row
        would be thirty cells that have to stay equal. So `PROJECT_META_PATH`
        is a second document, and it is scoped as narrowly as it can be --
        it holds ratings only, and a name in it that no row carries still
        does not bring a project into existence.

        `project` is free text bounded by `set_project_priority`, the same
        boundary `_post_project` leans on and for the same reason. `priority`
        *is* checked against the four labels here, the same as
        `_post_priority`: four labels is a closed set, so anything else in
        that cell is a client writing arbitrary text into his file.
        """
        project = payload.get("project")
        priority = payload.get("priority")
        if not isinstance(project, str) or not project.strip():
            self._send_json(400, {"error": "project must be a non-empty string"})
            return
        project = project.strip()
        priority = canonical_priority(priority)
        if priority is None:
            self._send_json(
                400, {"error": f"priority must be one of {sorted(PRIORITY_LABELS.values())}"})
            return

        try:
            ok, message = set_project_priority(project, priority)
        except Exception as e:
            log(f"nova-site project priority failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        # Nothing to invalidate: `project_payload` reads the ratings
        # uncached, the same call `/api/comments` makes, because this is one
        # small table and a stale rating is a page ordered against the
        # picker he is looking at.
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Rate project {project} \u00b7 {'ok' if ok else message}",
            after=priority,
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
        # **Every caller says who it is; there is no default.** This used to
        # fall back to the owner's own name on the reasoning that the page is
        # his, and it is -- but the page is not the only caller. Cycle 479 posted
        # two notes on idea #38 from a shell without the field, and both landed
        # in his live `ideas.md` signed with his name. The damage is not the
        # byline: `unanswered_comment_bodies` calls a row waiting when the last
        # note on it is his, and that flag outranks a 🔴 in
        # `tools.top_board_rows`, so my own comment put idea #38 at the top of
        # his board as a question he had never asked. A default that is right
        # for one caller and silently wrong for the other is not a default, and
        # the one caller it was for now states it in `app.js`.
        author = payload.get("author")
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

    def _post_conversation_watching(self, payload):
        """`POST /api/conversations/watching` -- this thread is on his screen.

        The same vouch as `_post_ask_watching` above, for the conversation the
        dock actually has open rather than the one tagged `nova-ask`. Until
        this existed the dock could only vouch while the ask thread was
        showing, so every other thread in the switcher still buzzed his phone
        from the other app -- which is the whole of the capture that built the
        vouch in the first place.

        Same contract as its neighbour and for the same reason: never an error
        to him, never audited, and a refusal simply means no suppression.
        """
        try:
            ok, reason = conversation_watching((payload or {}).get("id"))
        except Exception as e:
            log(f"nova-site conversations/watching failed: {e}")
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
        """`/api/conversations/new` -- start a thread with Nova.

        Answers with the new id so the page can open it without re-listing.

        No `personaId` is read any more, and one sent is ignored rather than
        honoured: `issues.md` #119 makes this app Nova-only, and a route that
        still accepted an id would be the picker surviving underneath the
        screen that no longer offers it.
        """
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            self._send_json(400, {"error": "name must be a string"})
            return
        # What the store will call it, computed once in `nova_conversations`
        # so the header the page paints and the row the switcher lists are
        # the same string. His capture #139: he does not always know what a
        # conversation is about before he starts it, so a blank name is an
        # answer rather than a missing field.
        name = conversation_starting_name(name)
        try:
            ok, message = conversation_create(name)
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
            ("a conversation needs", "that name is longer"))
        # `result`, not `conversationId`: the page's one chat writer reads
        # `result.result` off every `/api/conversations/*` write, and this
        # route answering under a name of its own is why a conversation he
        # started opened as `?id=undefined` (404) and refused his first
        # message with "conversationId and text must be strings".
        self._send_json(200 if ok else (400 if bad_input else 502),
                        {"ok": ok, "result": message if ok else None,
                         "name": name if ok else None,
                         "message": message})

    def _post_conversation_autotitle(self, payload):
        """`/api/conversations/autotitle` -- name a thread after its first message.

        `issues.md` #139. It is `rename` with the title derived rather than
        typed, and with one refusal `rename` does not have: it will not touch
        a thread whose name is anything but the placeholder, so a title he
        chose is never overwritten by something he said afterwards.

        `name` is what the page believes the thread is called. See
        `nova_conversations.autotitle` for why that is read off the page and
        not off the store.
        """
        self._conversation_write(
            payload, conversation_autotitle, "autotitle",
            ("which conversation", "that conversation already has a name",
             "there was no title"),
            lambda p: (p.get("id"), p.get("name"), p.get("text")))

    def _post_conversation_rename(self, payload):
        """`/api/conversations/rename` -- change what a thread is called.

        His capture, `issues.md` 2026-08-27 (🔴 Immediately): *"I need the
        chat bubble to be able to start ned conversations, delete them,
        change name, organize like move to a folder."* Audited for
        `_post_conversation_new`'s reason -- it changes what the machine
        shows him, and nothing else records that a thread was renamed.
        """
        self._conversation_write(
            payload, conversation_rename, "rename",
            ("which conversation", "a conversation needs", "that name is longer"),
            lambda p: (p.get("id"), p.get("name")))

    def _post_conversation_move(self, payload):
        """`/api/conversations/move` -- file a thread under a folder.

        An empty `folderId` is the top level and is a legal move, not a
        missing argument: taking a conversation *out* of a folder has to be
        expressible or a mis-filed thread is stuck there.
        """
        self._conversation_write(
            payload, conversation_move, "move",
            ("which conversation", "which folder", "that folder does not exist"),
            lambda p: (p.get("id"), p.get("folderId") or ""))

    def _post_conversation_folder(self, payload):
        """`/api/conversations/folder` -- a new folder for the switcher."""
        self._conversation_write(
            payload, conversation_folder_create, "folder",
            ("a folder needs", "that name is longer"),
            lambda p: (p.get("name"),))

    def _post_conversation_model(self, payload):
        """`/api/conversations/model` -- point one thread at another model.

        Idea #95's first line: *"It is hard to change model for a
        conversation because that means changing the model for all other
        conversations that personas is in."* Agora fixed that in the data
        model on 08-21; this is the control that reaches it from the app he
        actually opens. Audited for `_post_conversation_rename`'s reason,
        and one of its own: which model answers him decides whether a reply
        spends the prepaid balance, so who changed it and when is worth a
        row.
        """
        self._conversation_write(
            payload, conversation_set_model, "model",
            ("which conversation", "which model", "Agora does not have"),
            lambda p: (p.get("id"), p.get("model")))

    def _post_conversation_delete(self, payload):
        """`/api/conversations/delete` -- remove a thread for good.

        The only destructive write on this page. The confirmation is the
        page's; what belongs here is the audit line, because after this
        call there is no conversation left to look at and the audit row is
        the only record that it existed.
        """
        self._conversation_write(
            payload, conversation_remove, "delete",
            ("which conversation", "that conversation is already gone"),
            lambda p: (p.get("id"),))

    def _conversation_write(self, payload, fn, label, bad_input_prefixes, args_of):
        """The shared body of the five writes above.

        They differ only in which function they call and which refusals are
        his fault rather than the store's, so the split between 400 and 502
        -- `_post_conversation_send`'s split -- is written once. A refusal
        the caller could fix by typing something else is a 400; anything
        else is a 502, because a 400 tells him to retype a message that was
        never the problem.
        """
        payload = payload or {}
        try:
            args = args_of(payload)
        except Exception:
            self._send_json(400, {"error": "bad request"})
            return
        if any(a is not None and not isinstance(a, str) for a in args):
            self._send_json(400, {"error": "every field must be a string"})
            return
        try:
            ok, message = fn(*args)
        except Exception as e:
            log(f"nova-site conversations/{label} failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Conversation {label} · {'ok' if ok else message}",
            after=" ".join(a for a in args if a)[:MAX_BODY_BYTES],
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        bad_input = not ok and message.startswith(tuple(bad_input_prefixes))
        self._send_json(200 if ok else (400 if bad_input else 502),
                        {"ok": ok, "result": message if ok else None,
                         "message": message})

    def _post_heartbeat_enabled(self, payload):
        """`/api/heartbeats/enabled` -- switch one heartbeat on or off.

        The one write on this page that changes what the machine does, so
        it is audited like the capture writes are: `lastRunAt` tells him
        *that* a heartbeat stopped running and nothing else would tell him
        *why*.
        """
        heartbeat_id = payload.get("heartbeatId")
        enabled = payload.get("enabled")
        if not isinstance(heartbeat_id, str) or not isinstance(enabled, bool):
            self._send_json(400, {"error": "heartbeatId must be a string and enabled a boolean"})
            return
        try:
            ok, message = heartbeat_set_enabled(heartbeat_id, enabled)
        except Exception as e:
            log(f"nova-site heartbeats/enabled failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Heartbeat {'enabled' if enabled else 'disabled'} · {'ok' if ok else message}",
            after=heartbeat_id,
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        # A refusal about the id or the flag cannot succeed on a retry, so
        # it is a 400 rather than a 502 -- `_post_conversation_send`'s split.
        bad_input = not ok and message.startswith(
            ("which heartbeat", "enabled must", "no heartbeat with"))
        self._send_json(200 if ok else (400 if bad_input else 502),
                        {"ok": ok, "message": message})

    def _post_heartbeat_run(self, payload):
        """`/api/heartbeats/run` -- ask for a run at the next poll."""
        heartbeat_id = payload.get("heartbeatId")
        if not isinstance(heartbeat_id, str):
            self._send_json(400, {"error": "heartbeatId must be a string"})
            return
        try:
            ok, message = heartbeat_run_now(heartbeat_id)
        except Exception as e:
            log(f"nova-site heartbeats/run failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return
        audit(
            "Nova",
            "",
            "nova_capture",
            f"Heartbeat run requested · {'ok' if ok else message}",
            after=heartbeat_id,
            output=self.headers.get("Tailscale-User-Login") or "(no tailscale identity header)",
            is_error=not ok,
        )
        bad_input = not ok and message.startswith(("which heartbeat", "no heartbeat with"))
        self._send_json(200 if ok else (400 if bad_input else 502),
                        {"ok": ok, "message": message})

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

    def _post_board_archive(self, payload):
        """`/api/board/archive` -- his capture of 2026-09-03.

        *"We should be able to archive issues and ideas... Add a archive
        button next to the delete when in edit mode."*

        A third route beside edit and delete rather than a flag on either,
        for the reason `_post_board_amend` already gives about those two:
        the three do different things to the row and a caller that meant
        one of them must not be able to get another by leaving a field
        out. It sets `⚫ Outdated`, which `agora_runner.nova_capture.archive_row`
        explains is a status that already existed and had no way to be
        reached from the page.

        A number that is not an open row on that board is a 409, the same
        call the edit and delete route makes: nothing failed, the row
        moved or is already closed, and the page should re-read.
        """
        target = payload.get("target")
        number = payload.get("number")
        if target not in BOARD_PATHS:
            self._send_json(400, {"error": f"target must be one of {sorted(BOARD_PATHS)}"})
            return
        # `True` is an int in Python and would address row 1.
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            self._send_json(400, {"error": "number must be a positive integer"})
            return

        try:
            ok, message = archive_row(target, number)
        except Exception as e:
            log(f"nova-site board archive failed: {e}")
            self._send_json(502, {"error": str(e)[:300]})
            return

        if ok:
            invalidate("board:" + target)

        audit(
            "Nova",
            "",
            "nova_capture",
            f"Archive #{number} on {target} · {'ok' if ok else message}",
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
        `{"target": "entry", "entry": "2026-09-02 07:09"}` comments on a
        journal entry that carries no cycle number (issues.md 2026-09-02).

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
            if target not in ("needs", "entry"):
                self._send_json(400, {"error": "target must be 'needs' or 'entry'"})
                return
            if not clean_comment_text(text):
                self._send_json(400, {"error": "nothing to comment"})
                return
            if target == "entry":
                # A journal entry with no cycle number -- a retrospective, an
                # ideas run, a silence marker. The key is the entry's own
                # `date time` and it is validated here as well as in
                # `add_entry_comment`, so a malformed key is a 400 the box can
                # show rather than a write that lands under a heading no
                # reader will ever look under.
                key = payload.get("entry")
                if not isinstance(key, str) or not ENTRY_KEY_RE.match(key.strip()):
                    self._send_json(
                        400, {"error": "entry must be a 'YYYY-MM-DD HH:MM' key"}
                    )
                    return
                key = key.strip()
                self._store_comment(
                    lambda: add_entry_comment(key, text), text, f"entry {key}"
                )
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

    def _post_project_comment(self, payload):
        """`/api/project/comment` -- the owner talking about a project (idea #92, phase 4).

        His idea #92 asks for *"somehow a conversation per project or per
        issue/idea/note to define it more"*. The row-level half of that is
        `/api/board/comment`; this is the project level, and it writes to
        `comments.md` because a project is a name on a cell rather than a
        document with somewhere to append to. `nova_comments.project_comments`
        carries the reasoning.

        Same two boundaries as `/api/comment` and the same reason they hold:
        the path is the module-level `COMMENTS_PATH` and there is no target
        to choose, so the worst a request can do is add a comment to a file
        Nova reads and the owner can delete. The project name is free text
        by design -- it is matched against the `Project` cells he types
        himself -- so it is length-capped rather than allow-listed, and an
        unknown name is accepted: he can open a thread on a project before
        the first row is filed under it, which is the order an idea
        actually arrives in.

        Deliberately no auto-reply. `enqueue_reply` is keyed on a cycle
        number and a project thread has none; a cycle answers here the way
        it answers a board row, in its own time.
        """
        project = payload.get("project")
        text = payload.get("text")
        if not isinstance(text, str):
            self._send_json(400, {"error": "text must be a string"})
            return
        if not isinstance(project, str) or not project.strip():
            self._send_json(400, {"error": "project must be a name"})
            return
        project = project.strip()
        if len(project) > PROJECT_NAME_MAX:
            self._send_json(
                400, {"error": f"project must be at most {PROJECT_NAME_MAX} characters"}
            )
            return
        if "\n" in project or "\r" in project or "·" in project:
            self._send_json(
                400, {"error": "project must be one line and must not contain ·"}
            )
            return
        if not clean_comment_text(text):
            self._send_json(400, {"error": "nothing to comment"})
            return
        self._store_comment(
            lambda: add_project_comment(project, text), text, f"project {project}"
        )

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
        if path == "/demo" or path.startswith("/demo/"):
            # The generic 404 below would read as "that demo is not
            # running", which is a different and much more confusing
            # problem than "this proxy does not carry POSTs yet".
            self._send_json(405, {"error": "the demo proxy serves GET only"})
            return
        if path not in (
            "/api/capture", "/api/capture/edit", "/api/capture/delete",
            "/api/capture/convert", "/api/capture/promote", "/api/comment",
            "/api/board/priority", "/api/board/project",
            "/api/project/priority",
            "/api/board/edit", "/api/board/delete", "/api/board/archive",
            "/api/capture/comment",
            "/api/board/comment", "/api/ask", "/api/ask/watching",
            "/api/conversations/send", "/api/conversations/new",
            "/api/conversations/watching", "/api/conversations/rename",
            "/api/conversations/autotitle",
            "/api/conversations/move", "/api/conversations/delete",
            "/api/conversations/folder", "/api/conversations/model",
            "/api/heartbeats/enabled", "/api/heartbeats/run",
            "/api/pool/decide", "/api/pool/comment", "/api/pool/generate",
            "/api/goal/status", "/api/push/subscribe",
            "/api/project/comment",
        ):
            self._send_json(404, {"error": "not found"})
            return

        payload = self._read_json_body()
        if payload is None:
            return
        if path == "/api/ask":
            self._post_ask(payload)
            return
        if path == "/api/push/subscribe":
            ok, body = store_subscription(payload)
            self._send_json(200 if ok else 502, body)
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
        if path == "/api/conversations/watching":
            self._post_conversation_watching(payload)
            return
        if path == "/api/conversations/autotitle":
            self._post_conversation_autotitle(payload)
            return
        if path == "/api/conversations/rename":
            self._post_conversation_rename(payload)
            return
        if path == "/api/conversations/move":
            self._post_conversation_move(payload)
            return
        if path == "/api/conversations/folder":
            self._post_conversation_folder(payload)
            return
        if path == "/api/conversations/model":
            self._post_conversation_model(payload)
            return
        if path == "/api/conversations/delete":
            self._post_conversation_delete(payload)
            return
        if path == "/api/heartbeats/enabled":
            self._post_heartbeat_enabled(payload)
            return
        if path == "/api/heartbeats/run":
            self._post_heartbeat_run(payload)
            return
        if path == "/api/comment":
            self._post_comment(payload)
            return
        if path == "/api/project/comment":
            self._post_project_comment(payload)
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
        if path == "/api/capture/promote":
            self._post_promote(payload)
            return
        if path == "/api/board/priority":
            self._post_priority(payload)
            return
        if path == "/api/board/project":
            self._post_project(payload)
            return
        if path == "/api/project/priority":
            self._post_project_priority(payload)
            return
        if path in ("/api/board/edit", "/api/board/delete"):
            self._post_board_amend(payload, delete=path.endswith("delete"))
            return
        if path == "/api/board/archive":
            self._post_board_archive(payload)
            return
        if path == "/api/board/comment":
            self._post_board_comment(payload)
            return
        if path == "/api/goal/status":
            self._post_goal_status(payload)
            return
        if path == "/api/pool/decide":
            self._post_pool_decide(payload)
            return
        if path == "/api/pool/comment":
            self._post_pool_comment(payload)
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
        # `is True` rather than truthiness: the default is one bullet per
        # line and a client that sends `"false"` or `1` by accident must
        # not silently glue a whole paste into one item.
        one_item = payload.get("oneItem") is True

        try:
            ok, message = capture(target, text, priority, one_item=one_item)
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
