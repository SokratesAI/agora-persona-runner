"""Has the owner sent anything back on Telegram that nothing has read yet?

Sending has worked since 2026-09-04 (`tools.telegram`, `tools.notify`).
Receiving did not exist. The bridge's poller handled `/start` and `/status`
and dropped every other message the owner sent, so this half of his ask
reached nothing at all:

    "have Nova send me a notification on telegram to tell me that its ready
    to send and receive messages as i sometimes want to send messages back."

The bridge now appends every text he sends to a file on its volume and serves
the unread ones at `GET /inbox`. This is the reader. It is in `tools.preflight`
so every cycle asks the question once, in the same sweep it already runs,
rather than a cycle having to remember a channel exists.

**Exit 2 means he is waiting.** That is the same status the sweep uses for a
firing alert, and it is right here for the same reason: a message from him
outranks whatever the cycle was going to pick. Exit 0 is an empty inbox, exit
1 is not being able to reach the bridge -- an instrument failure never reads
as "he has not written".

**Reading does not consume.** `/inbox` is a plain read; the watermark only
moves on `--ack`, which the cycle runs *after* it has acted. A cycle that
reads his message and then dies leaves it for the next one, which is the
contract `comments.md` and `notes.md` already have: a thing he wrote stays
unread until somebody says what they did about it, not until somebody looks.
That is deliberately not a preflight side effect -- an automatic ack would
make a killed cycle indistinguishable from an answered message.

    python3 -m tools.telegram_inbox
    python3 -m tools.telegram_inbox --all
    python3 -m tools.telegram_inbox --fetch-media
    python3 -m tools.telegram_inbox --ack 41

**A photo is an ordinary message here.** As of 2026-09-04 evening the bridge
downloads an image he sends and records it on the row beside the text, so a
row can carry a `media` object and an empty `text` -- a captionless photo.
The report names it; `--fetch-media` pulls the bytes to
`/data/workspace/attachments/` and prints one path per image, which is the
same shape `tools.fetch_attachments` uses for a picture he attaches to a
board comment, and for the same reason: this harness can render an image
from a path on disk and cannot render one from a URL.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/telegram_inbox.py` works and not only
# `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from tools.telegram import DEFAULT_URL, _get, _post  # noqa: E402

#: Where a fetched image lands. The same directory `tools.fetch_attachments`
#: writes to, so a cycle has one place to look for a picture he sent whichever
#: channel it came in on.
MEDIA_DIR = "/data/workspace/attachments"


def format_messages(rows):
    """His messages, whole, one block each. Never truncated -- he wrote them."""
    out = []
    for row in rows:
        out.append("  #%s  %s" % (row.get("id"), row.get("at") or "no timestamp"))
        media = row.get("media") if isinstance(row.get("media"), dict) else None
        if media:
            # Said before the text, because on a captionless photo the text is
            # empty and the picture is the entire message.
            out.append("      [image: %s, %s byte(s)] — `python3 -m "
                       "tools.telegram_inbox --fetch-media` to see it"
                       % (media.get("name"), media.get("bytes")))
        for line in str(row.get("text") or "").splitlines():
            out.append("      " + line)
    return out


def fetch_media(rows, url=DEFAULT_URL, opener=urllib.request.urlopen, timeout=60,
                directory=MEDIA_DIR):
    """Pull every image on these rows to disk. (exit status, lines).

    Only the bridge can reach Telegram's file host -- the download URL carries
    the bot token -- so it holds the bytes on its volume and this fetches them
    over the same in-cluster address as everything else here.
    """
    wanted = [r for r in rows
              if isinstance(r.get("media"), dict) and r["media"].get("name")]
    if not wanted:
        return 0, ["no images on the unread messages"]
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as err:
        return 1, ["could not create %s: %s" % (directory, err)]
    lines, failed = [], 0
    for row in wanted:
        name = row["media"]["name"]
        request = urllib.request.Request(
            "%s/inbox/media/%s" % (url.rstrip("/"), name), method="GET")
        try:
            with opener(request, timeout=timeout) as response:
                blob = response.read()
        except Exception as err:  # HTTPError, URLError, socket timeout
            lines.append("  #%s  could not fetch %s: %s" % (row.get("id"), name, err))
            failed += 1
            continue
        path = os.path.join(directory, "telegram-%s" % name)
        try:
            with open(path, "wb") as fh:
                fh.write(blob)
        except OSError as err:
            lines.append("  #%s  could not write %s: %s" % (row.get("id"), path, err))
            failed += 1
            continue
        lines.append("  #%s  %s  (%d bytes)" % (row.get("id"), path, len(blob)))
    head = "%d image(s) from %d message(s); Read each path to see it" % (
        len(wanted) - failed, len(wanted))
    # A fetch that got nothing is an instrument failure, not an empty inbox.
    return (1 if failed else 0), [head] + lines


def fetch(url=DEFAULT_URL, opener=urllib.request.urlopen, timeout=15, everything=False):
    """(exit status, lines). 0 nothing waiting, 2 he is waiting, 1 unreachable."""
    path = "/inbox?all=1" if everything else "/inbox"
    try:
        code, body = _get(url, path, opener, timeout)
    except Exception as err:  # URLError, socket timeout, anything below it
        return 1, ["could not reach the Telegram bridge at %s: %s" % (url, err)]
    if code == 404:
        # The endpoint is younger than the deployed ConfigMap. Say which half
        # is behind rather than reporting an empty inbox, which is what a
        # caller would otherwise read this as.
        return 1, ["the bridge at %s has no /inbox endpoint — its ConfigMap predates it" % url]
    if code != 200:
        return 1, ["unexpected HTTP %s from %s/inbox" % (code, url)]
    rows = body.get("messages")
    if not isinstance(rows, list):
        return 1, ["the bridge answered /inbox without a messages list"]
    acked = body.get("acked_through", 0)
    if everything:
        head = "%s message(s) ever, read through #%s" % (body.get("total", len(rows)), acked)
        return 0, [head] + format_messages(rows)
    if not rows:
        return 0, ["nothing waiting — %s message(s) ever, read through #%s"
                   % (body.get("total", 0), acked)]
    head = ("%s message(s) from the owner on Telegram that no cycle has answered. "
            "Act on them, then `python3 -m tools.telegram_inbox --ack %s`."
            % (len(rows), rows[-1].get("id")))
    return 2, [head] + format_messages(rows)


def ack(through, url=DEFAULT_URL, opener=urllib.request.urlopen, timeout=15):
    """(exit status, line). 0 the watermark moved, 1 the bridge would not."""
    try:
        code, body = _post(url, "/inbox/ack", {"through": int(through)}, opener, timeout)
    except Exception as err:
        return 1, "could not reach the Telegram bridge at %s: %s" % (url, err)
    if code != 200:
        return 1, "the bridge refused the ack with HTTP %s: %s" % (
            code, body.get("error") or "no error field in the response")
    return 0, "read through #%s" % body.get("acked_through")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="bridge base URL (default %s)" % DEFAULT_URL)
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds per request")
    parser.add_argument("--all", action="store_true", dest="everything",
                        help="every message ever received, answered or not")
    parser.add_argument("--ack", type=int, metavar="ID",
                        help="mark everything up to this message id as dealt with")
    parser.add_argument("--json", action="store_true", help="the raw payload instead of the report")
    parser.add_argument("--fetch-media", action="store_true", dest="media",
                        help="write the images on those messages to %s" % MEDIA_DIR)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.ack is not None:
        code, line = ack(args.ack, args.url, timeout=args.timeout)
        print(line)
        return code
    if args.media:
        path = "/inbox?all=1" if args.everything else "/inbox"
        try:
            code, body = _get(args.url, path, urllib.request.urlopen, args.timeout)
        except Exception as err:
            print("could not reach the Telegram bridge at %s: %s" % (args.url, err))
            return 1
        if code != 200 or not isinstance(body.get("messages"), list):
            print("could not read the inbox: HTTP %s" % code)
            return 1
        code, lines = fetch_media(body["messages"], args.url, timeout=max(args.timeout, 60.0))
        for line in lines:
            print(line)
        return code
    if args.json:
        try:
            _, body = _get(args.url, "/inbox?all=1" if args.everything else "/inbox",
                           urllib.request.urlopen, args.timeout)
        except Exception as err:
            print("could not reach the Telegram bridge at %s: %s" % (args.url, err))
            return 1
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return 0
    code, lines = fetch(args.url, timeout=args.timeout, everything=args.everything)
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
