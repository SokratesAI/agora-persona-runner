"""Send the owner a Telegram message, as a command.

The bridge is a two-endpoint HTTP service in the `infra` namespace and the
message goes to the owner's own chat with the bot. Every caller so far
hand-rolled the same curl, which is what he asked to stop doing:

    "it is a good idea to build such a tool to make it easier for you
    and your cycles to do so."  -- the owner, ideas.md, 2026-09-01

    python3 -m tools.telegram status
    python3 -m tools.telegram send 'the newspaper job is dead again'
    python3 -m tools.telegram send --file /tmp/alert.txt
    printf '%s' "$body" | python3 -m tools.telegram send -
    python3 -m tools.telegram send --file /tmp/alert.txt --dry-run

This was `tools.whatsapp` until 2026-09-04, when the owner asked for the
WhatsApp deployment shut down and replaced with a Telegram one. The CLI,
the exit codes and the reasoning below are unchanged -- only the service
it talks to moved, which is the whole point of the bridge having had a
two-endpoint contract.

The three things a hand-rolled curl gets wrong, and why each one is here:

**Quoting.** A message written straight into a shell argument goes
through the shell first, and backticks in it become command
substitution -- the text vanishes and the command still succeeds. That
has bitten this loop twice in other files. `--file` and `-` take the
bytes without a shell in the middle, so a message with backticks, quotes
or newlines in it arrives as written.

**A refusal that looks like a success.** `curl` exits 0 on an HTTP 503,
so the not-configured case -- the one that actually happens -- prints
nothing and reads as sent. Here it is exit 2, on a line that says which
of the three refusals it was.

**Which failure it was.** Unreachable, not configured, and send-failed are
different problems with different owners, and the bridge separates them
already: 503 means it cannot send yet (no bot token, or the owner has not
messaged the bot), 502 means it tried and Telegram refused. Exit 1 is
reserved for "I could not reach the bridge or could not read its answer",
so an instrument problem never reads as a clean send -- the same contract
as the checks in `tools.preflight`.

Two things it deliberately does not do. It does not add the robot
prefix: the server prepends it, because a bot message and one the owner
typed are otherwise easy to confuse in a chat he also writes in, and a
second prefix here would double it. And it does not take a recipient --
the service sends to the one owner chat and refuses a `{to, text}` body
on purpose, so there is no argument to pass.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/telegram.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

DEFAULT_URL = "http://telegram-bridge.infra.svc.cluster.local:8080"

# Inherited from the WhatsApp bridge, where it was express's own 64kb body
# limit. The Telegram bridge has no body limit of its own -- it splits the text
# into 3900-character messages -- so this ceiling is now about the far end
# rather than the near one: 64KB is seventeen messages in a burst, and
# Telegram's documented per-chat limit is around twenty a minute, so past this
# the tail is throttled rather than sent. I have not measured that limit myself;
# what I can defend is that the number is unchanged and no caller has hit it.
MAX_TEXT_BYTES = 64 * 1024 - 512  # headroom for the JSON envelope

#: The longest message this loop may put on his phone, in characters. He
#: asked for it on Telegram on 2026-09-04 at 14:25 Oslo, after a nine-line
#: write-up:
#:
#:     "In the future, messages to telegram must be shorter. The 'Yes - done,
#:     and it was the nameapace cap that was holding it, not an oversight' is
#:     enough for me. I do not want the details here."
#:
#: His own example of "enough for me" is 89 characters; this leaves a couple
#: of sentences' headroom above it and is well under the ~900 he was
#: answering. It sits beside `MAX_TEXT_BYTES` and is a different kind of
#: number: that one is the bridge's ceiling, this one is his.
#:
#: It is enforced in `check_text`, which `send` calls, so it holds for the
#: bare `python3 -m tools.telegram send` CLI as well as for the policy layer
#: in `tools.notify`. A cap only in `notify` would have guarded the path no
#: cycle actually types.
MAX_CHARS = 280


def read_text(args, stdin=None):
    """The message, from an argument, a file, or stdin. Exactly one."""
    given = [g for g in (args.text is not None, args.file is not None) if g]
    if len(given) != 1:
        raise ValueError("give the message as an argument, or --file, not both and not neither")
    if args.file is not None:
        with open(args.file, encoding="utf-8") as fh:
            return fh.read()
    if args.text == "-":
        return (stdin if stdin is not None else sys.stdin).read()
    return args.text


def check_text(text):
    """None if the text is sendable, else why it is not.

    Both refusals are ones the server would make too -- this makes them
    locally, so a caller finds out before the message is on the wire and
    gets the measurement rather than a status code.
    """
    if not text.strip():
        return "refusing to send an empty message"
    size = len(text.encode("utf-8"))
    if size > MAX_TEXT_BYTES:
        return f"message is {size} bytes, over the bridge's {MAX_TEXT_BYTES}-byte limit"
    return too_long(text)


def too_long(text, limit: int = MAX_CHARS):
    """None if the text is short enough for his phone, else why it is not.

    Measured on the stripped text, because a trailing newline is not
    something he reads. Refusing rather than truncating is deliberate:
    cutting the tail drops whichever sentence the author thought mattered
    and leaves him a message that stops mid-word, where a refusal makes the
    author write the short one, which is what he asked for. The one caller
    that cannot rewrite itself is `tools.alerts --notify`, and it builds a
    page that fits this by construction rather than being silenced by it.
    """
    length = len(text.strip())
    if length > limit:
        return (
            f"refusing to send {length} characters: he asked on 2026-09-04 for "
            f"Telegram messages of at most {limit}. Say the outcome in a sentence "
            f"and leave the detail in the journal."
        )
    return None


def _post(url, path, payload, opener, timeout):
    """(status, body-dict). Raises URLError when the bridge is unreachable."""
    request = urllib.request.Request(
        url.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            return response.status, _body(response)
    except urllib.error.HTTPError as err:
        return err.code, _body(err)


def _get(url, path, opener, timeout):
    request = urllib.request.Request(url.rstrip("/") + path, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            return response.status, _body(response)
    except urllib.error.HTTPError as err:
        return err.code, _body(err)


def _body(response):
    try:
        return json.loads(response.read().decode() or "{}")
    except Exception:
        return {}


def send(text, url=DEFAULT_URL, opener=urllib.request.urlopen, timeout=15):
    """(exit status, line). 0 sent, 2 the bridge refused, 1 unreachable."""
    refusal = check_text(text)
    if refusal:
        return 2, refusal
    try:
        status, body = _post(url, "/send", {"text": text.strip()}, opener, timeout)
    except Exception as err:  # URLError, socket timeout, anything below it
        return 1, f"could not reach the Telegram bridge at {url}: {err}"
    if status == 200:
        return 0, f"sent, {len(text.strip())} character(s)"
    if status == 503:
        return 2, "the bridge is up but cannot send yet — nothing was sent"
    if status == 502:
        return 2, "the bridge is configured and Telegram refused the send — nothing was sent"
    detail = body.get("error") or "no error field in the response"
    return 2, f"the bridge refused with HTTP {status}: {detail}"


def status_of(url=DEFAULT_URL, opener=urllib.request.urlopen, timeout=15):
    """(exit status, line). 0 ready to send, 2 up but unconfigured, 1 unreachable.

    Reads /health rather than /healthz on purpose: /healthz is the
    readiness probe and answers 200 as soon as the HTTP server binds,
    whether or not the bot token and the owner chat id are there, so it
    cannot answer the only question a caller has here.
    """
    try:
        code, body = _get(url, "/health", opener, timeout)
    except Exception as err:
        return 1, f"could not reach the Telegram bridge at {url}: {err}"
    if code == 200:
        return 0, f"ready — the bridge can send at {url}"
    if code == 503:
        # The bridge answers /health with a `hint` naming which half is
        # missing -- the token, or an owner who has not messaged the bot yet.
        # Those have different owners, so passing it through is the point.
        return 2, f"the bridge is up at {url} and cannot send yet: {body.get('hint', 'not ready')}"
    return 1, f"unexpected HTTP {code} from {url}/health"


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"bridge base URL (default {DEFAULT_URL})")
    parser.add_argument("--timeout", type=float, default=15.0, help="seconds per request")
    sub = parser.add_subparsers(dest="command", required=True)

    send_cmd = sub.add_parser("send", help="send the owner a message")
    send_cmd.add_argument("text", nargs="?", help="the message, or - to read stdin")
    send_cmd.add_argument("--file", help="read the message from this file instead")
    send_cmd.add_argument("--dry-run", action="store_true", help="print what would be sent and send nothing")

    sub.add_parser("status", help="can the bridge actually send?")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "status":
        code, line = status_of(args.url, timeout=args.timeout)
        print(line)
        return code

    try:
        text = read_text(args)
    except (ValueError, OSError) as err:
        print(str(err))
        return 2

    if args.dry_run:
        refusal = check_text(text)
        if refusal:
            print(refusal)
            return 2
        print(f"would send to {args.url}/send, {len(text.strip())} character(s):")
        print(f"🤖 {text.strip()}")
        return 0

    code, line = send(text, args.url, timeout=args.timeout)
    print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
