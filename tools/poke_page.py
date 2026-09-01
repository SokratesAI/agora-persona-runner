"""Touch the Nova app in a real phone browser, instead of only looking at it.

`tools.see_page` loads each route in Chromium and asserts the page really
rendered. That is the right check for "did this draw at all", and it is
structurally blind to the bugs this app actually ships, because every one
of them lives in the transition between two renders rather than in either
render:

- Cycle 211: typing in the board search closed the keyboard on every
  letter, because the page rebuilt the input the user was typing into.
  267 green browser tests did not see it. A throwaway Chromium script
  written that cycle found it in one run -- and was then deleted, so the
  five UI cycles after it shipped without the instrument that had just
  proved its worth. That is what this module is: that script, checked in.
- Cycles 205/206: `sw.js` marks a replayed response with
  `X-Nova-Replayed` so the page can say "showing a saved copy". Nothing
  in this loop had ever run `sw.js` under a real service-worker
  registration -- jsdom does not implement workers -- so both PRs shipped
  on a reasoned claim about what a phone would do.

    python3 -m tools.poke_page                     # every probe
    python3 -m tools.poke_page search-focus
    python3 -m tools.poke_page --base http://127.0.0.1:8111 offline-banner

Exit 0 if every probe passed. The probes are in `tools/browser/poke.js`;
this module chooses the browser flags, runs it, and prints the verdict.

## Why the offline probes need a `--base` they can reach on localhost

A browser will not register a service worker on an insecure origin, so
`http://nova-site:8083` cannot be probed directly. The offline probes
therefore stand a plain TCP forwarder on `127.0.0.1` in front of
whatever `--base` names and drive the browser at that -- localhost is a
secure context, which costs nothing, where a
`--unsafely-treat-insecure-origin-as-secure` override would weaken the
very thing under test.

Closing that forwarder is also how they lose the network, and that is
not a stylistic choice. Two emulated approaches were tried first and
both silently measured nothing:

- `context.setOffline(true)` reaches only the page. The worker has its
  own network stack, keeps fetching successfully, and never enters the
  `catch` that replays -- so the probe reads "no banner" whether or not
  the banner works.
- `context.newCDPSession(worker)`, which would steer the worker's own
  target, is rejected by this Playwright: `page: expected Page or Frame`.

Killing the socket needs no cooperation from any of that, and is a
closer model of a phone losing its tailnet than either.

## What a probe has to be able to do

Fail. Both probes here were false within the last week: `search-focus`
counted one blur per keystroke before runner#201, and `offline-banner`
would have found no banner before runner#205. A probe whose green is
guaranteed in advance measures nothing, which is the trap `see_page`'s
own docstring is mostly about.

Run this from `Bash` (the bridge pod) -- it is the pod with `node` and it
reaches `nova-site:8083`. The Chromium sysroot is shared with
`see_page`; `tools/browser/bootstrap.sh` rebuilds it if it is gone.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from tools.see_page import BrowserMissing, DEFAULT_BASE, PHONE_WIDTH, browser_root, render_env

PROBES = (
    "nav-reachable",
    "chat-scroll-lock",
    "search-focus",
    "search-focus-ideas",
    "replay-header",
    "offline-banner",
    "offline-banner-issues",
)


def chrome_args() -> list:
    """The two flags this sandbox needs, and deliberately nothing else.

    No security override belongs here: the offline probes get their
    secure context from a `127.0.0.1` forwarder instead, so the browser
    goes on enforcing everything a real phone enforces.
    """
    return ["--no-sandbox", "--disable-dev-shm-usage"]


def poke(names, root=None, base=DEFAULT_BASE, width=PHONE_WIDTH) -> list:
    root = root or browser_root()
    env = render_env(root)
    env["NOVA_SITE"] = base
    env["NOVA_WIDTH"] = str(width)
    env["NOVA_CHROME_ARGS"] = " ".join(chrome_args())
    # Same reason `see_page` copies `shot.js`: `node` runs with cwd=root,
    # so an edit to the version-controlled file would otherwise silently
    # not take effect in the one tool whose job is telling the truth.
    shutil.copyfile(Path(__file__).resolve().parent / "browser" / "poke.js", root / "poke.js")
    proc = subprocess.run(
        ["node", "poke.js", *names],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.startswith("{")]
    if not rows:
        raise RuntimeError(f"poke.js produced no probes: {proc.stderr.strip()[:400]}")
    return rows


def problems(rows, wanted) -> list:
    """What is wrong with this run, including probes that never reported.

    A probe that vanished is the failure mode worth spelling out: it
    looks exactly like a run that was never asked for it.
    """
    found = []
    seen = {row["probe"] for row in rows}
    for row in rows:
        if not row["ok"]:
            found.append(f"{row['probe']}: FAILED {json.dumps(row['detail'], sort_keys=True)}")
        for err in row.get("errors", []):
            found.append(f"{row['probe']}: console error: {err}")
    for name in wanted:
        if name not in seen:
            found.append(f"{name}: did not report -- treat as failed, not as skipped")
    return found


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    base = DEFAULT_BASE
    width = PHONE_WIDTH
    if "--base" in args:
        at = args.index("--base")
        base = args[at + 1]
        del args[at : at + 2]
    if "--width" in args:
        at = args.index("--width")
        width = int(args[at + 1])
        del args[at : at + 2]
    names = args or list(PROBES)
    try:
        rows = poke(names, base=base, width=width)
    except (BrowserMissing, RuntimeError) as exc:
        print(exc)
        return 1
    for row in rows:
        print(
            f"{row['probe']:<28} {'ok' if row['ok'] else 'FAIL'}  "
            f"{json.dumps(row['detail'], sort_keys=True)}"
        )
    found = problems(rows, names)
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
