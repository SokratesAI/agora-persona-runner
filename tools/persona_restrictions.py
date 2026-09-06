"""Which personas can run a shell on the bridge pod right now?

Idea #168 asked for `--restricted` to become a real sandbox for the
personas that should not run commands. Cycle 696 built the mechanism and
Cycle 1050 turned it on for the three personas the row names -- Haiku,
Plain assistant and Study buddy. The gap this file closes is the one
between those two cycles: for six days the only written answer to "which
personas are restricted" was a sentence in `tools_mcp.py`'s security
posture saying *"no persona is started restricted yet"*, and nothing
re-checked it. A fact about live objects, kept in prose, in the module
that documents what this loop is exposed to.

So this is a register rather than a guard, and the distinction is the
whole design. It reads Agora and prints, for every persona, whether a
turn of it executes inside the bridge pod's Claude Code session with that
pod's shell. It does not compare against a baseline list of ids, because
that list would be the same drift-prone prose one layer down -- a cycle
that restricts a fourth persona would have to remember to edit it, which
is exactly what nobody did the first time.

**What decides the answer**, and it is two fields rather than one:

- The provider. Only `claude-cli:` runs on the bridge pod at all. A
  `gemini:` or `anthropic:` persona is a stateless HTTP call made from
  the runner process and reaches the world through Agora's capability
  tools alone, so it has no shell no matter what the flag says.
- `claudeCliRestricted`. When true the runner asks the bridge for its
  full known-tool denylist *and* the CLI's own `--restricted`, which
  removes the built-in tools that run commands or code. The MCP
  capability tools survive on purpose (`bridge/cli.py`), so a restricted
  persona is narrowed, not crippled.

That pair is `agora_runner.turns.runs_on_the_bridge`, and this file calls
it rather than re-spelling the rule -- a second copy of the test would
pass while disagreeing with the thing it describes.

**Measured Cycle 1050, end to end, because the flag being set is not the
same as the shell being gone.** Devil's advocate (unrestricted, haiku)
was asked to run `id -un && hostname` and answered `bridge /
agora-claude-bridge-6cdfbdb87-tbwsx` off a real `Bash` call. Plain
assistant (restricted, same model, same question) had no Bash tool at all
and listed the 16 `mcp__agora__*` tools it still holds. The positive
result was possible, so the negative one counts.

**Read from `AGORA_PUBLIC` (:8080), not the internal :8081 API.** The
public app serves `GET /personas` and `GET /personas/{id}` with no token,
and the bridge pod -- where `tools.*` run -- holds no `AGORA_TOKEN` at
all, so an internal read here would be a 401 on every call. Measured from
the bridge pod, 2026-09-06. The list response deliberately does not carry
`claudeCliRestricted`, which is why this fetches each persona's detail
rather than reading the roster once.

**Not in `preflight`, on purpose.** The answer changes when somebody
changes it, not hourly, and `preflight`'s cost is the reason it exists.

Exit contract. **Exit 0 is any state that was fully read** -- including
"every persona has a shell", which is what the platform looked like
before this morning and is a legitimate configuration the owner chose.
There is no exit 2, and inventing one would mean picking a set of
personas that *ought* to be restricted, which is his call and not this
file's. **Exit 1 is no instrument**: the roster was unreadable, or a
persona's detail was, and neither is a measurement of anything.
"""
import argparse
import json
import sys
import urllib.request

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner.heartbeat_liveness import AGORA_PUBLIC  # noqa: E402
from agora_runner.turns import runs_on_the_bridge  # noqa: E402


def _get(path, opener=None, timeout=30):
    """`(payload, error)` for one Agora GET. Never raises."""
    target = AGORA_PUBLIC.rstrip("/") + path
    try:
        with (opener or urllib.request.urlopen)(target, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8")), None
    except Exception as e:
        return None, f"could not read {target}: {e}"


def fetch(opener=None):
    """`(personas, error)`. Each row is the persona's own detail record,
    because the roster response does not carry `claudeCliRestricted`."""
    payload, error = _get("/personas", opener=opener)
    if error:
        return [], error
    roster = payload.get("personas", []) if isinstance(payload, dict) else payload
    if not isinstance(roster, list):
        return [], "the personas roster was not a list"
    out = []
    for row in roster:
        pid = (row or {}).get("id")
        if not pid:
            # A row with no id cannot be fetched and cannot be judged. Say
            # so rather than dropping it, or the count silently shrinks.
            return [], "a persona row carried no id"
        detail, error = _get(f"/personas/{pid}", opener=opener)
        if error:
            return [], error
        persona = detail.get("persona") if isinstance(detail, dict) else None
        if not isinstance(persona, dict):
            return [], f"no persona record at /personas/{pid}"
        out.append(persona)
    return out, None


def judge(personas, error=None):
    """`(text, exit status)`."""
    if error:
        return f"NO INSTRUMENT -- {error}", 1

    shell, restricted, off_bridge = [], [], []
    for p in personas:
        name = p.get("name") or "(unnamed)"
        model = p.get("model") or "(no model)"
        line = f"  {name} -- {model}"
        if runs_on_the_bridge(p):
            shell.append(line)
        elif str(model).startswith("claude-cli:"):
            restricted.append(line)
        else:
            off_bridge.append(line)

    lines = []
    if shell:
        lines.append(
            f"HAS THE BRIDGE POD'S SHELL -- {len(shell)} persona(s). A turn of "
            "one of these runs inside a Claude Code session on the bridge pod "
            "with that pod's own shell, the same reach a Nova cycle has.")
        lines.extend(sorted(shell))
    else:
        lines.append("HAS THE BRIDGE POD'S SHELL -- none.")
    if restricted:
        lines.append(
            f"RESTRICTED -- {len(restricted)} claude-cli persona(s) carry "
            "claudeCliRestricted, so the bridge passes the CLI's own "
            "--restricted and its full known-tool denylist. The mcp__agora__* "
            "capability tools survive; the shell does not.")
        lines.extend(sorted(restricted))
    if off_bridge:
        lines.append(
            f"NEVER ON THE BRIDGE -- {len(off_bridge)} persona(s) on another "
            "provider. A stateless HTTP call from the runner process, so there "
            "is no shell to take away and the flag would change nothing.")
        lines.extend(sorted(off_bridge))
    lines.append(
        f"Read {len(personas)} persona(s) from {AGORA_PUBLIC}. Whether a "
        "persona SHOULD be restricted is the owner's call and this tool does "
        "not have an opinion, which is why it never exits 2.")
    return "\n".join(lines), 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.parse_args(argv)
    personas, error = fetch()
    text, status = judge(personas, error=error)
    print(text)
    return status


if __name__ == "__main__":
    sys.exit(main())
