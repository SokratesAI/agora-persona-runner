"""terminal_exec -- unrestricted shell in this pod (Issues.md #1, terminalExec capability)."""

import os
import subprocess

from agora_runner.log import log


# --------------------------------------------------------------------------
# terminal_exec (2026-07-29, terminalExec) -- Issues.md's #1 open item:
# "no tool grants direct shell access". Runs in this same pod, the same
# one kubectl_read/github_read/create_pr already run in -- Edvard's
# explicit call (asked directly rather than assumed) was to build it here
# rather than an isolated per-command sandbox, so it has no narrower
# blast radius than the union of every other tool in this file plus
# whatever else a shell can do with this pod's kubectl RBAC and GH bot
# token. Deliberately unrestricted -- no verb/flag allowlist like
# kubectl_read/github_read -- the whole point is letting a persona skip a
# purpose-built tool and fix it directly when it's buggy or missing. The
# only guardrails are a timeout (never hangs the poll loop forever) and
# output truncation (never blows the context window); every invocation
# is logged unconditionally (not debug-gated) and audited with the full
# command, since this is the single highest-blast-radius tool here.
# --------------------------------------------------------------------------
TERMINAL_EXEC_TIMEOUT_DEFAULT = 60
TERMINAL_EXEC_TIMEOUT_MAX = 300
TERMINAL_EXEC_OUTPUT_MAX = 8000
TERMINAL_WORKSPACE = "/tmp/agent-workspace"


def terminal_exec(args):
    """Runs `command` via bash -lc in a persistent per-pod scratch
    workspace, so a multi-call task (clone, edit, test) can build on its
    own previous calls within one pod lifetime -- the workspace does not
    survive a pod restart (Recreate strategy, no PVC)."""
    if not isinstance(args, dict):
        return "[terminal_exec: invalid arguments]"
    command = str(args.get("command", "")).strip()
    if not command:
        return "[terminal_exec: 'command' is required]"
    timeout = args.get("timeout")
    try:
        timeout = int(timeout) if timeout else TERMINAL_EXEC_TIMEOUT_DEFAULT
    except (TypeError, ValueError):
        timeout = TERMINAL_EXEC_TIMEOUT_DEFAULT
    timeout = max(1, min(timeout, TERMINAL_EXEC_TIMEOUT_MAX))
    cwd = TERMINAL_WORKSPACE
    subdir = str(args.get("cwd", "")).strip()
    if subdir:
        if subdir.startswith("/") or ".." in subdir:
            return "[terminal_exec: 'cwd' must be a relative path inside the workspace, no '..']"
        cwd = os.path.join(TERMINAL_WORKSPACE, subdir)
    os.makedirs(cwd, exist_ok=True)
    log(f"terminal_exec: running (timeout={timeout}s, cwd={cwd}): {command[:300]!r}")
    try:
        result = subprocess.run(
            ["bash", "-lc", command], capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        log(f"terminal_exec: timed out after {timeout}s: {command[:300]!r}")
        return f"[terminal_exec: timed out after {timeout}s]"
    except Exception as e:
        log(f"terminal_exec: {command[:300]!r} raised {e}")
        return f"[terminal_exec error: {e}]"
    output = (result.stdout or "") + (result.stderr or "")
    log(f"terminal_exec: exited {result.returncode}, {len(output)} chars output for: {command[:300]!r}")
    truncated = output[:TERMINAL_EXEC_OUTPUT_MAX]
    if len(output) > TERMINAL_EXEC_OUTPUT_MAX:
        truncated += f"\n[... output truncated at {TERMINAL_EXEC_OUTPUT_MAX} chars, {len(output)} total]"
    if not truncated:
        return f"[exit {result.returncode}, no output]"
    return f"[exit {result.returncode}]\n{truncated}"
