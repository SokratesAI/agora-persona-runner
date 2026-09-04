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
    python3 -m tools.telegram_inbox --ack 41
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/telegram_inbox.py` works and not only
# `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from tools.telegram import DEFAULT_URL, _get, _post  # noqa: E402


def format_messages(rows):
    """His messages, whole, one block each. Never truncated -- he wrote them."""
    out = []
    for row in rows:
        out.append("  #%s  %s" % (row.get("id"), row.get("at") or "no timestamp"))
        for line in str(row.get("text") or "").splitlines() or [""]:
            out.append("      " + line)
    return out


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
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.ack is not None:
        code, line = ack(args.ack, args.url, timeout=args.timeout)
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
