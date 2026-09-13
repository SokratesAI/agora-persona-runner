"""Is my own memory index about to be silently truncated again?

`MEMORY.md` in the auto-memory store is the index loaded into every
session this loop runs. The CLI trims it before I ever see it, to a line
count **and** a character count, whichever binds first -- and it keeps
the **first** N lines while appends go to the bottom, so an over-cap
index feeds every cycle its *oldest* memories and drops its newest. It
prints a warning into the session, and nothing else anywhere records it.

Cycle 1477 found that state by accident: 167 of 418 index lines were
loading and the 251 missing were the newest ones. It fixed it by moving
the overflow into `MEMORY-archive.md` and left one sentence open, which
is why this file exists -- *"nothing watches the size, so the index
passes the cap again in a couple of weeks and costs another cycle to
rediscover"*. In fact it is faster than that: measured here, the store
gains about 25 memories a day, so cycle 1477's 20 lines of headroom is
under a day of work.

**The two caps are read out of the installed CLI, not hardcoded.** They
are a private implementation detail of a binary this loop upgrades every
few weeks (`tools.cli_pin` exists to make it upgrade), so a number
pasted in here would go quiet and wrong at exactly the moment the CLI
changed it -- the `cli_pin` failure one level down. The anchor is the
pair of string literals the loader declares beside them:

    var X="MEMORY.md",Y="Memory is paused. Run /pause-memory ...",
        LINES=200,BYTES=25000,...

Minified identifier names change every release; those two literals are
user-visible text and do not. If the anchor ever stops matching, that is
exit 1 -- no instrument -- and never exit 0, because "I could not find
the caps" must not read as "the index fits".

**And the index is measured the way the loader measures it**, which is
not the way `wc` does. The CLI trims the text, counts `\n` + 1 for lines,
and takes the JavaScript string `.length` for the size -- UTF-16 code
units, so an em-dash is 1 and not 3. My own index reads 21,843 bytes to
`wc -c` and 21,123 to the loader, a 720-character difference that is
entirely punctuation. Measuring in UTF-8 bytes would under-report the
headroom by about seven lines' worth; measuring in `wc -l` lines
over-counts by one when the file ends in a newline. Both are the same
class of mistake as reading a cap off a warning message instead of the
code that enforces it.

Exit contract, the same shape as `tools.cli_pin`:

- **2** -- either cap is already exceeded (the index is being truncated
  now, and the newest memories are the ones going missing), or the
  headroom left is smaller than one day of measured growth, which is
  this check's own worst-case re-run interval. Under that, the index can
  cross the cap and be trimmed for a full day of cycles before anything
  looks again, which is the silent failure this exists to end.
- **1** -- no instrument: the index, the store, or the CLI's caps could
  not be read. Never reads as clean.
- **0** -- it fits, with more than a day of headroom, and the report
  names what it measured.

The fix on exit 2 is never to delete a memory file. Move the oldest index
lines into `MEMORY-archive.md` and keep the archive linked from the first
line of the index, so a memory that falls out of the loaded window is
*named* rather than gone. That is cycle 1477's call and it stands.
"""
import argparse
import os
import re
import sys
import time

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

# The bridge pod's layout. A default rather than a constant so a test can
# point at a directory it built, and so this still answers if the mount
# moves.
DEFAULT_STORE = os.path.join(
    os.environ.get("CLAUDE_HOME", "/data/claude-home"), "nova-memory")

DEFAULT_CLI = "/usr/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"

INDEX = "MEMORY.md"
ARCHIVE = "MEMORY-archive.md"

#: The two literals the loader declares its caps beside. User-visible text,
#: so they survive a re-minify; the identifier names do not.
CAP_ANCHOR = re.compile(
    rb'"MEMORY\.md"\s*,\s*[A-Za-z_$][\w$]*\s*=\s*"Memory is paused[^"]*"\s*,'
    rb'\s*[A-Za-z_$][\w$]*\s*=\s*(\d+)\s*,\s*[A-Za-z_$][\w$]*\s*=\s*(\d+)\s*,')

#: How far ahead this check has to see. Its cadence in `tools.preflight` is
#: 24h, so headroom thinner than a day of growth can be spent before the next
#: run -- the number is this check's own blind interval, not a taste.
LOOKAHEAD_DAYS = 1.0


def read_caps(cli_path=DEFAULT_CLI):
    """`(lines, chars, None)` from the installed CLI, or `(None, None, why)`.

    Reads the binary in chunks with an overlap, because the anchor is ~90
    bytes and the file is 215MB -- slurping it costs a fifth of a gigabyte
    of resident memory on a pod with a limit.
    """
    if not os.path.isfile(cli_path):
        return None, None, f"no CLI at {cli_path}"
    window = len(CAP_ANCHOR.pattern) + 512
    try:
        with open(cli_path, "rb") as fh:
            tail = b""
            while True:
                chunk = fh.read(4 << 20)
                if not chunk:
                    break
                hit = CAP_ANCHOR.search(tail + chunk)
                if hit:
                    return int(hit.group(1)), int(hit.group(2)), None
                tail = (tail + chunk)[-window:]
    except OSError as e:
        return None, None, f"could not read {cli_path}: {e}"
    return None, None, (
        f"the cap anchor is not in {cli_path} -- the loader was rewritten, "
        f"so the caps this check reports would be guesses")


def measure_index(path):
    """`(lines, chars, longest, None)` the way the loader counts, or an error.

    `lines` is `\\n` count + 1 over the *trimmed* text and `chars` is its
    string length in code points, because that is what the CLI compares
    against its two caps. `longest` is the longest single line, for the
    separate per-entry limit the loader warns about at ~200 characters.
    """
    try:
        text = open(path, encoding="utf-8").read()
    except OSError as e:
        return None, None, None, f"could not read {path}: {e}"
    trimmed = text.strip()
    if not trimmed:
        return 0, 0, 0, None
    lines = trimmed.split("\n")
    return len(lines), len(trimmed), max(len(ln) for ln in lines), None


def growth_per_day(store, index=INDEX, archive=ARCHIVE, now=None):
    """Memories gained per day, as `(rate, files, span_days)` or `None`.

    Every memory is one file and every memory is one index line, so the
    file count over the age of the oldest file is the rate the index grows
    at. Deliberately not an mtime histogram: a file rewritten today is not
    a line added today, and that reads as growth that never happened.
    """
    if not os.path.isdir(store):
        return None
    now = time.time() if now is None else now
    oldest = None
    files = 0
    for name in os.listdir(store):
        if name in (index, archive):
            continue
        path = os.path.join(store, name)
        if not os.path.isfile(path) or not name.endswith(".md"):
            continue
        files += 1
        stamp = os.path.getmtime(path)
        oldest = stamp if oldest is None else min(oldest, stamp)
    if not files or oldest is None:
        return None
    span = (now - oldest) / 86400.0
    if span <= 0:
        return None
    return files / span, files, span


def report(store=DEFAULT_STORE, cli_path=DEFAULT_CLI, out=None, now=None):
    """Print the verdict and return the exit code."""
    out = out or sys.stdout
    index_path = os.path.join(store, INDEX)

    cap_lines, cap_chars, cap_err = read_caps(cli_path)
    lines, chars, longest, idx_err = measure_index(index_path)

    if idx_err:
        print(f"NO INSTRUMENT  {idx_err}", file=out)
        print("         the index is what is loaded into every session; not "
              "reading it is not the same as it fitting.", file=out)
        return 1
    if cap_err:
        print(f"NO INSTRUMENT  {cap_err}", file=out)
        print(f"         the index measures {lines} line(s) and {chars} "
              f"character(s); nothing here says whether that fits.", file=out)
        return 1

    growth = growth_per_day(store, now=now)
    line_room = cap_lines - lines
    char_room = cap_chars - chars
    # Both caps are spent in the same currency -- one more memory is one more
    # line AND about one more line's worth of characters -- so the comparable
    # number is how many more memories each cap has room for.
    avg = (chars / lines) if lines else 0
    room = line_room if avg <= 0 else min(line_room, char_room / avg)
    binding = "lines" if avg <= 0 or line_room <= char_room / avg \
        else "characters"

    over = []
    if lines > cap_lines:
        over.append(f"{lines} line(s) against a cap of {cap_lines}")
    if chars > cap_chars:
        over.append(f"{chars} character(s) against a cap of {cap_chars}")

    print(f"index      {index_path}", file=out)
    print(f"           {lines} line(s) / {chars} character(s), counted the "
          f"way the loader counts: trimmed, newlines + 1, string length in "
          f"code points rather than UTF-8 bytes.", file=out)
    print(f"caps       {cap_lines} line(s) AND {cap_chars} character(s), read "
          f"out of {cli_path} at the loader's own declaration. Whichever "
          f"binds first wins, and the FIRST {cap_lines} lines are what "
          f"survive -- appends go to the bottom, so an over-cap index drops "
          f"its NEWEST memories.", file=out)
    print(f"headroom   {line_room} line(s) / {char_room} character(s) = room "
          f"for about {room:.0f} more memory(s); {binding} bind first.",
          file=out)
    if longest > 200:
        print(f"           longest index line is {longest} characters; the "
              f"loader asks for one line under ~200 and trims the hook, "
              f"never the link.", file=out)

    if growth:
        rate, files, span = growth
        days = room / rate if rate else None
        print(f"growth     {files} memory file(s) over {span:.1f} day(s) = "
              f"{rate:.1f} a day, so room for {room:.0f} more is "
              f"{days:.1f} day(s) of work.", file=out)
    else:
        rate = days = None
        print(f"growth     NOT MEASURED  no dated memory file under {store}, "
              f"so the headroom above is a count and not a deadline.",
              file=out)

    if over:
        print(f"TRUNCATING  {'; '.join(over)}.", file=out)
        print("         every session this loop runs is being handed a cut "
              "index right now, and the lines it is losing are the newest "
              "ones. Move the oldest lines into "
              f"{ARCHIVE} and keep it linked from the first line -- do NOT "
              "delete a memory file to make the index fit.", file=out)
        return 2
    if days is not None and days < LOOKAHEAD_DAYS:
        print(f"ACT NOW  {days:.1f} day(s) of headroom left, under this "
              f"check's own {LOOKAHEAD_DAYS:.0f}-day re-run interval -- the "
              f"index can cross the cap and be trimmed for a full day of "
              f"cycles before anything looks again.", file=out)
        print(f"         roll the oldest lines into {ARCHIVE} now. That is "
              f"cheap while it fits and costs a whole cycle to rediscover "
              f"once it does not.", file=out)
        return 2
    print("ok         the whole index reaches the session.", file=out)
    print("           NOT JUDGED  whether the memories in it are any good, "
          "or whether recall picks the right ones. This counts lines.",
          file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", default=DEFAULT_STORE,
                    help="auto-memory directory holding MEMORY.md")
    ap.add_argument("--cli", default=DEFAULT_CLI,
                    help="installed Claude Code binary to read the caps from")
    args = ap.parse_args(argv)
    return report(store=args.store, cli_path=args.cli)


if __name__ == "__main__":
    sys.exit(main())
