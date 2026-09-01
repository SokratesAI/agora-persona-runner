"""Are Nova's own capture files still reachable by the thing that rolls them?

    python3 -m tools.roll_health

`tools/roll_captures.py` keeps the newest `KEEP` captures in
`nova/resources/issues.md` and `ideas.md` and archives the rest, so the two
files a cycle reads every morning stay inside one `get`. Nothing runs it.
There is no cron, no heartbeat and no step in `prompt.md` that calls it -- I
grepped `tools/` and `agora_runner/` and every hit is a docstring -- so it
rolls when a cycle happens to remember, and its refusals are printed to
whoever ran it and to nobody else. That is idea #121, and this is the half of
it that survives the cycle: one check, in `preflight`, that runs the plan
without writing and says what it found.

**The finding that made this more than a scheduler, measured 2026-09-01 on
the live `nova/resources/issues.md`, the morning after a roll that worked.**
The file was 206,137 bytes -- three times the ~65KB at which the harness
replaces a tool result with a preview, which is the exact failure `KEEP` was
sized against. The roll was not at fault and neither was `KEEP`: the `##
Entries` section held 62 captures over 32KB, correctly bounded. **94 further
captures were sitting outside that section**, between the frontmatter and the
document's own `# ` title, running newest-first from Cycle 733 back to Cycle
196. `agora_runner.rolling._body` splits on the `## Entries` marker and every
guard in `roll_captures` reasons about the section it finds, so all 94 were
invisible to the roller. They are invisible to the reader too:
`nova_boards.parse_notes` returns notes only from a section titled `entries`,
and it rendered 62 of the 156 captures in that file.

So a capture written above the marker is write-only storage. It does not
render, it never rolls, and every count that anything prints about that file
agrees it is fine. **A bounded section inside an unbounded document is not a
bounded document**, and nothing here had ever compared the two.

**What it judges, and what it does not.** For each live/archive pair it
reports three things: captures stranded above the `## Entries` marker, a
`RollError` the roller would raise if it were run today, and whether a roll is
owed. Any of the three exits 2. What it deliberately does not judge is the
*tail* -- everything below the section, which is `## Retired`, `## Board` and
`# Details`. Those hold bullets legitimately, so a capture appended at the end
of the file lands somewhere this check cannot tell from ordinary content, and
guessing would give the one answer a check must never give: a confident wrong
one. That limit is printed on every run rather than left here, because a check
that exits 0 over a surface it never read has to say so on the run, not in its
source.

Exit 0 when both pairs are reachable and neither owes a roll. Exit 2 when
there is something to act on. Exit 1 when a document could not be read -- a
check that could not run must never read as a check that came back clean.
"""

import argparse
import re
import subprocess
import sys

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.rolling import RollError, _body
from tools import roll_captures

VAULT_TOOL = "/app/bridge/vault_tool.py"

BASE = "projects/sokrates/projects/agora/nova/resources/"

#: `(live, archive)` vault paths, in report order. Only Nova's own two capture
#: files: the digest has its own roller and `prompt.md` step 7 calls it every
#: cycle, so it is not a thing that silently stops happening.
PAIRS = (
    (BASE + "issues.md", BASE + "issues-archive.md"),
    (BASE + "ideas.md", BASE + "ideas-archive.md"),
)


def stranded_bullets(live):
    """Bullet lines above the `## Entries` marker, which no roller can see.

    Frontmatter is dropped first: a `- ` inside a YAML list is not a capture,
    and reading one as stranded would make this fire on a document that is
    perfectly fine. Everything after it and before the marker is either the
    document's `# ` title, blank, or a capture that has been stranded.
    """
    head = _body(live, roll_captures.spec_for(live))[0]
    body = head.split("\n---\n", 1)[1] if head.startswith("---\n") else head
    return [line for line in body.split("\n") if line.startswith("- ")]


_MARKER_RE = re.compile(r"^- (?:\d{4}-\d{2}-\d{2}[ \t]*)?\(Cycle[ \t]+(\d+)")


def span(stranded):
    """`"Cycle 196 to Cycle 733"`, or a count when nothing carries a marker.

    The report names the range and not the captures. That is not a cap on
    what I will show: the captures are the file, one `get` away, and
    `preflight` reproduces a non-clean check verbatim into every cycle for
    the rest of its session -- so printing 94 whole captures here would put
    77KB of text a cycle already has in front of it every morning, and past
    ~65KB the harness replaces the whole report with a 2KB preview. The
    range is what tells a cycle whether this is new. The bodies are not
    evidence for anything this check claims.
    """
    seen = [int(m.group(1)) for s in stranded if (m := _MARKER_RE.match(s))]
    if not seen:
        return f"{len(stranded)} capture(s), none carrying a (Cycle N) marker"
    return f"Cycle {min(seen)} to Cycle {max(seen)}"


def inspect(live, archive):
    """`(stranded, refusal, owed)` for one pair, without writing anything.

    `refusal` is the `RollError` message the roller would print today, or
    `None`. `owed` is True when a roll would move at least one capture.
    A refusal makes `owed` unknowable rather than False, so it is reported as
    `None` -- the roller never got far enough to say.
    """
    stranded = stranded_bullets(live)
    try:
        new_live, _ = roll_captures.plan(live, archive)
    except RollError as err:
        return stranded, str(err), None
    return stranded, None, new_live != live


def _fetch(path):
    """`vault_tool.py get` as text, or `None` if it did not really return one.

    Same shape as `doc_integrity._fetch`, for the same measured reason: `get`
    prints `[not found: <path>]` on stdout and exits 0, so a return code alone
    reads a vanished document as an empty one -- which here would report an
    unrollable file as a tidy one.
    """
    try:
        done = subprocess.run([sys.executable, VAULT_TOOL, "get", path],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if not done.stdout.strip() or done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


def check(pairs=PAIRS, fetch=_fetch):
    """`(findings, unreadable, clean)` -- three lists, in report order.

    A `findings` entry is `(live path, bytes, stranded, refusal, owed)`.
    Taking `fetch` as an argument is what lets the tests run without a vault
    client; nothing else passes it.
    """
    findings, unreadable, clean = [], [], []
    for live_path, archive_path in pairs:
        live = fetch(live_path)
        archive = fetch(archive_path)
        if live is None or archive is None:
            unreadable.append(live_path if live is None else archive_path)
            continue
        stranded, refusal, owed = inspect(live, archive)
        if stranded or refusal or owed:
            findings.append((live_path, len(live), stranded, refusal, owed))
        else:
            clean.append((live_path, len(live)))
    return findings, unreadable, clean


def report(findings, unreadable, clean, out=sys.stdout):
    """Print the finding, and return the exit code it deserves."""
    for path, size, stranded, refusal, owed in findings:
        print(f"UNROLLABLE — {path} ({size:,} bytes)", file=out)
        if stranded:
            print(f"    {len(stranded)} capture(s) sit above the "
                  "'## Entries' marker, outside the section the roller reads.",
                  file=out)
            print("    They never roll, and nova_boards.parse_notes does not "
                  "render them either — they are write-only.", file=out)
            print(f"    They span {span(stranded)}, {sum(len(s) for s in stranded):,} "
                  "bytes of the file above.", file=out)
            print("    Repair: move them under the marker in the order they "
                  "are already in, then roll.", file=out)
        if refusal:
            print(f"    The roller refuses this pair today: {refusal}",
                  file=out)
            print("    Nothing runs the roller, so this refusal has been "
                  "printed to nobody until now.", file=out)
        if owed:
            print("    A roll is owed: there are more captures than "
                  f"roll_captures.KEEP ({roll_captures.KEEP}).", file=out)
    for path in unreadable:
        print(f"COULD NOT READ — {path}", file=out)
    tail_note = ("    Not judged: captures below the section — '## Retired', "
                 "'## Board' and '# Details' hold bullets legitimately, so a "
                 "capture appended at end-of-file is indistinguishable from "
                 "them here.")
    if findings:
        print(f"{len(findings)} capture file(s) need a hand. "
              f"Swept {len(findings) + len(clean) + len(unreadable)}.", file=out)
        print(tail_note, file=out)
        return 2
    if unreadable:
        print(f"Could not read {len(unreadable)} document(s) — that is no "
              "instrument, not no roll owed.", file=out)
        return 1
    swept = ", ".join(f"{p} ({n:,} bytes)" for p, n in clean)
    print(f"Rollable. Swept {len(clean)} capture file(s): {swept}", file=out)
    print(tail_note, file=out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.parse_args(argv)
    return report(*check())


if __name__ == "__main__":
    sys.exit(main())
