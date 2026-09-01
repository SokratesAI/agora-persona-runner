"""Send the owner a WhatsApp message, as a command.

The bridge is a two-endpoint HTTP service in the `infra` namespace and
the message goes to the owner's own "Message yourself" chat, because it paired
as a linked device on that account. Every caller so far hand-rolled the
same curl, which is what he asked to stop doing:

    "it is a good idea to build such a tool to make it easier for you
    and your cycles to do so."  -- the owner, ideas.md, 2026-09-01

    python3 -m tools.whatsapp status
    python3 -m tools.whatsapp send 'the newspaper job is dead again'
    python3 -m tools.whatsapp send --file /tmp/alert.txt
    printf '%s' "$body" | python3 -m tools.whatsapp send -
    python3 -m tools.whatsapp send --file /tmp/alert.txt --dry-run

The three things a hand-rolled curl gets wrong, and why each one is here:

**Quoting.** A message written straight into a shell argument goes
through the shell first, and backticks in it become command
substitution -- the text vanishes and the command still succeeds. That
has bitten this loop twice in other files. `--file` and `-` take the
bytes without a shell in the middle, so a message with backticks, quotes
or newlines in it arrives as written.

**A refusal that looks like a success.** `curl` exits 0 on an HTTP 503,
so the pairing-is-down case -- the one that actually happens -- prints
nothing and reads as sent. Here it is exit 2, on a line that says which
of the three refusals it was.

**Which failure it was.** Unreachable, not paired, and send-failed are
different problems with different owners, and the bridge separates them
already: 503 means WhatsApp is not connected, 502 means it is and the
send threw. Exit 1 is reserved for "I could not reach the bridge or
could not read its answer", so an instrument problem never reads as a
clean send -- the same contract as the checks in `tools.preflight`.

Two things it deliberately does not do. It does not add the robot
prefix: the server prepends `🤖 ` itself, because on a linked device a
bot message and one the owner typed are otherwise identical, and a second
prefix here would double it. And it does not take a recipient -- the
service sends to `OWNER_WHATSAPP_JID` and refuses a `{to, text}` body on
purpose, so there is no argument to pass.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/whatsapp.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

DEFAULT_URL = "http://whatsapp-bridge.infra.svc.cluster.local:8080"

# `express.json({limit: "64kb"})` in the bridge's own server. Over it, express
# answers 413 with an HTML body, so the caller gets a parse error instead of a
# refusal. Refusing here names the real limit and the real length.
MAX_TEXT_BYTES = 64 * 1024 - 512  # headroom for the JSON envelope


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
        return 1, f"could not reach the WhatsApp bridge at {url}: {err}"
    if status == 200:
        return 0, f"sent, {len(text.strip())} character(s)"
    if status == 503:
        return 2, "the bridge is up but WhatsApp is not connected — nothing was sent"
    if status == 502:
        return 2, "the bridge is connected and the send itself failed — nothing was sent"
    detail = body.get("error") or "no error field in the response"
    return 2, f"the bridge refused with HTTP {status}: {detail}"


def status_of(url=DEFAULT_URL, opener=urllib.request.urlopen, timeout=15):
    """(exit status, line). 0 ready to send, 2 up but unpaired, 1 unreachable.

    Reads /health rather than /healthz on purpose: /healthz is the
    readiness probe and answers 200 as soon as the HTTP server binds,
    whether or not WhatsApp is connected, so it cannot answer the only
    question a caller has here.
    """
    try:
        code, body = _get(url, "/health", opener, timeout)
    except Exception as err:
        return 1, f"could not reach the WhatsApp bridge at {url}: {err}"
    if code == 200:
        return 0, f"ready — WhatsApp is connected at {url}"
    if code == 503:
        return 2, f"the bridge is up at {url} and WhatsApp is not connected: {body.get('status', 'not_ready')}"
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

    sub.add_parser("status", help="is the bridge connected to WhatsApp?")
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
