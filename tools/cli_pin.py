"""Is the Claude Code CLI pin in the bridge Dockerfile still current?

Cycle 427, on the owner's idea #124. The bridge pins
`ARG CLAUDE_CODE_VERSION` and the loop runs *inside* that binary, so the
pin is the one dependency in this system whose staleness degrades the
loop itself. It sat at 2.1.226 from 8 August to 25 August -- nineteen
releases, seventeen days -- and the note on the row is precise about why
that rated Immediately: *"The reason it is Immediately is not the size of
the change, it is that the pin drifted nineteen releases without anyone
noticing, including me."*

Nothing read that number. It was found by a research run that happened to
open the per-version changelog, which is the same shape as the security
advisory `tools.security_alerts` was built for: a fact that arrives only
as a side effect of an unrelated command is not reported, it is
occasionally noticed.

    python3 -m tools.cli_pin

**Two versions, and they are not the same question.** The Dockerfile pin
is what the next image build will install. `claude --version` is what
this pod is running right now. They disagree whenever an image has not
rolled since the pin moved, and the Dockerfile's own comment records the
inverse failure -- an unpinned install line that looked like it tracked
the registry while Docker's layer cache froze it at 2.1.197 for months.
So both are printed, and a disagreement is called out rather than
averaged into one number.

**The staleness window is derived, not chosen.** The CLI publishes most
weekdays, so "behind by any amount" fires nearly every cycle and a check
that always fires is a check nobody reads. The window is the *slowest
recurring job in this loop* -- the weekly goal review -- because a pin
that has been behind for less than that has not yet outlived a single
scheduled opportunity to notice it. Seven days is that cadence, not a
comfort number, and `--max-age-days` moves it if the cadence moves.

Exit status, matching `tools.security_alerts` so a cycle can read it
without parsing the text: 0 when the pin is current or the gap is inside
the window, 2 when the pin is stale and wants bumping, 1 when something
was unreadable. "I could not check" never reads as "nothing here".
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

PACKAGE = "@anthropic-ai/claude-code"
REGISTRY = "https://registry.npmjs.org/%40anthropic-ai%2Fclaude-code"
REPO = "SokratesAI/agora-claude-bridge"
PIN_RE = re.compile(r"^ARG CLAUDE_CODE_VERSION=(\S+)$", re.MULTILINE)
DEFAULT_MAX_AGE_DAYS = 7


def dockerfile_candidates():
    """Where the bridge Dockerfile might be, most local first.

    A concurrent cycle has its own worktree and the shared checkout may
    not be the one it is working in, so `$NOVA_WORKSPACE` leads.
    """
    seen, out = set(), []
    for root in (os.environ.get("NOVA_WORKSPACE"), "/data/workspace"):
        if not root:
            continue
        path = os.path.join(root, "agora-claude-bridge", "Dockerfile")
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def read_pin(runner=subprocess.run):
    """Return (version, where) or (None, why-not).

    Falls back to GitHub when no checkout is on disk, because a cycle
    that cannot find the file locally is the case where guessing is
    worst.
    """
    for path in dockerfile_candidates():
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        match = PIN_RE.search(text)
        if match:
            return match.group(1), path
        return None, f"{path} has no `ARG CLAUDE_CODE_VERSION=` line"

    try:
        proc = runner(
            ["gh", "api", f"repos/{REPO}/contents/Dockerfile",
             "--jq", ".content"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"no checkout on disk and gh failed: {exc}"
    if proc.returncode != 0:
        return None, f"no checkout on disk and gh failed: {proc.stderr.strip()}"
    import base64
    try:
        text = base64.b64decode(proc.stdout).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        return None, f"could not decode the Dockerfile from GitHub: {exc}"
    match = PIN_RE.search(text)
    if not match:
        return None, f"{REPO}'s Dockerfile has no `ARG CLAUDE_CODE_VERSION=` line"
    return match.group(1), f"{REPO}@HEAD"


def running_version(runner=subprocess.run):
    """What `claude` on this PATH actually is, or None if it is not here.

    Absent is a normal answer -- the runner pod has no CLI at all -- and
    is reported as unknown rather than as a disagreement.
    """
    try:
        proc = runner(["claude", "--version"], capture_output=True,
                      text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = re.search(r"(\d+\.\d+\.\d+)", proc.stdout)
    return match.group(1) if match else None


def fetch_registry(opener=urllib.request.urlopen):
    """dist-tags.latest, the version list, and the publish-time map.

    Returns (latest, versions, times, None) or (None, None, None, why).

    **The full packument, not the abbreviated one**, and that is the
    whole reason this is a function rather than a line. npm's
    `application/vnd.npm.install-v1+json` header cuts the document from
    1.3MB to 450KB by dropping the `time` map -- which is exactly the
    field the staleness window needs. Cycle 427 sent that header, and the
    tool answered "0 releases behind, publish date unknown" for a pin
    that was nineteen releases and seventeen days behind. It reported the
    stale pin anyway, because the unknown-date path fails towards noise;
    the numbers beside it were still wrong, and only the mutation check
    (put the old pin back, read what it says) surfaced that.
    """
    request = urllib.request.Request(REGISTRY)
    try:
        with opener(request, timeout=60) as response:
            body = json.load(response)
    except Exception as exc:  # noqa: BLE001 -- any network shape is "unreadable"
        return None, None, None, f"could not reach the npm registry: {exc}"
    latest = (body.get("dist-tags") or {}).get("latest")
    if not latest:
        return None, None, None, "the npm registry returned no dist-tags.latest"
    return latest, body.get("versions") or {}, body.get("time") or {}, None


def version_key(version):
    """Sortable tuple, or None for anything that is not plain x.y.z.

    Prereleases and oddities sort out rather than being guessed at -- a
    miscounted gap is worse than an uncounted one.
    """
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    return tuple(int(p) for p in match.groups()) if match else None


def releases_between(pinned, latest, versions):
    """How many published versions sit strictly after `pinned`.

    Counted off the `versions` map rather than `time`, because `time`
    carries `created`/`modified` and keeps entries for versions that have
    been unpublished. Returns None when either end is unorderable, so the
    caller reports the gap as uncountable instead of printing a wrong
    number.
    """
    low, high = version_key(pinned), version_key(latest)
    if low is None or high is None:
        return None
    count = 0
    for version in versions:
        key = version_key(version)
        if key is not None and low < key <= high:
            count += 1
    return count


def older_of(pinned, running):
    """Which version the staleness verdict is actually about.

    Returns (version, human label).

    **The reviewer's finding, and it was live in the sandbox when it was
    made.** The first version of this printed a warning when the running
    binary disagreed with the pin and then computed the exit status from
    the pin alone. So on the cycle that bumps the pin, the tool says
    `pinned 2.1.245, latest 2.1.245` and exits 0 -- "nothing to do" --
    while the binary the loop is executing inside is 2.1.226 and
    seventeen days old. That is the one state this tool exists to catch,
    reported as clean, by the field the docstring promises a cycle can
    read without parsing the text.

    So the verdict is taken on whichever of the two is older. A pin that
    has moved and an image that has not rolled are still two different
    problems and the ⚠ line still says which one you are looking at; they
    just no longer have two different exit codes, because from the loop's
    point of view "the CLI I am running is stale" is one fact.

    A running binary that is *newer* than the pin (a hand-installed CLI,
    a pod that outlived a revert) is not the tool's business and does not
    change the subject: the pin is what the next build installs.
    """
    if running is None:
        return pinned, "the pinned version"
    pin_key, run_key = version_key(pinned), version_key(running)
    if pin_key is None or run_key is None or run_key >= pin_key:
        return pinned, "the pinned version"
    return running, "the running binary"


def parse_time(stamp):
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def main(argv=None, now=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS,
        help="how long the pin may be behind before this exits 2 "
             "(default: the loop's slowest recurring job, one week)",
    )
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    pinned, where = read_pin()
    if pinned is None:
        print(f"COULD NOT READ THE PIN — {where}")
        return 1

    latest, versions, times, error = fetch_registry()
    if error:
        print(f"pinned {pinned} (from {where})")
        print(f"COULD NOT READ THE REGISTRY — {error}")
        return 1

    running = running_version()
    print(f"{PACKAGE}: pinned {pinned} (from {where}), latest {latest}")
    if running is None:
        print("  running binary: not on this PATH (normal off the bridge pod)")
    elif running != pinned:
        print(f"  ⚠ the running binary is {running}, not the pinned {pinned} "
              "— an image has not rolled since the pin moved")
    else:
        print(f"  running binary agrees: {running}")

    subject, label = older_of(pinned, running)
    if subject == latest:
        print("The pin is current and it is what is running. Nothing to do.")
        return 0

    behind = releases_between(subject, latest, versions)
    published = parse_time(times.get(subject))
    age_days = (now - published).total_seconds() / 86400 if published else None

    gap = f"{behind} release(s) behind" if behind is not None \
        else "behind by an uncountable number of releases (non-numeric version)"
    age = f", {label} published {age_days:.1f} day(s) ago" \
        if age_days is not None else ", publish date unknown"
    print(f"  {gap}{age}")

    if age_days is None:
        print(f"STALE, ASSUMED — the registry has no publish date for {subject}, "
              "so the age check could not run and this fails towards noise "
              "rather than towards silence.")
        return 2
    if age_days > args.max_age_days:
        print(f"STALE — {label} ({subject}) has been behind for longer than "
              f"{args.max_age_days:g} day(s), which is the slowest recurring "
              "job in this loop. Bump ARG CLAUDE_CODE_VERSION in "
              f"{REPO}'s Dockerfile, and run the stream-json contract check "
              "the comment above that line prescribes before merging. If the "
              "pin is already current, the image has not rolled and that is "
              "where to look.")
        return 2

    print("Behind, but inside the window — the CLI publishes most weekdays "
          "and a check that fires every day is one nobody reads.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
