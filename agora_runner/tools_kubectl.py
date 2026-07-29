"""kubectl_read -- read-only, allowlisted cluster introspection (Issues.md #3)."""

import subprocess

from agora_runner.log import log, debug_log


KUBECTL_ALLOWED_VERBS = {"get", "describe", "logs", "top"}
KUBECTL_ALLOWED_FLAGS = {
    "-o", "--output", "--field-selector", "--selector", "-l", "--tail", "--since",
    "--all-namespaces",
}
KUBECTL_FORBIDDEN_RESOURCE_PREFIXES = ("secret",)


def kubectl_read(args):
    if not isinstance(args, dict):
        return "[kubectl: invalid arguments]"
    verb = str(args.get("verb", "")).strip().lower()
    resource = str(args.get("resource", "")).strip()
    namespace = str(args.get("namespace", "")).strip()
    extra = args.get("args") or []
    if verb not in KUBECTL_ALLOWED_VERBS:
        return f"[kubectl: verb {verb!r} not allowed -- only {sorted(KUBECTL_ALLOWED_VERBS)}]"
    if not resource:
        return "[kubectl: resource is required]"
    resource_kind = resource.split("/")[0].split(".")[0].strip().lower()
    if any(resource_kind.startswith(p) for p in KUBECTL_FORBIDDEN_RESOURCE_PREFIXES):
        return "[kubectl: reading Secrets is never allowed through this tool]"
    if not isinstance(extra, list):
        return "[kubectl: 'args' must be a list of strings]"
    for flag in extra:
        flag_name = str(flag).split("=", 1)[0]
        if flag_name not in KUBECTL_ALLOWED_FLAGS:
            return f"[kubectl: flag {flag!r} not allowed -- only {sorted(KUBECTL_ALLOWED_FLAGS)}]"
    cmd = ["kubectl", verb, resource]
    cmd.extend(str(a) for a in extra)
    cmd.extend(["-n", namespace] if namespace else ["--all-namespaces"])
    debug_log(f"kubectl_read: running {cmd}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        log(f"kubectl_read: binary not installed in this image (cmd={cmd})")
        return "[kubectl: binary not installed in this image]"
    except Exception as e:
        log(f"kubectl_read: {cmd} raised {e}")
        return f"[kubectl error: {e}]"
    output = (result.stdout or "") + (result.stderr or "")
    # Always logged, not debug-gated: a nonzero exit is the one signal that
    # this tool is silently misconfigured (missing RBAC grant, wrong
    # kubeconfig context, etc.) rather than the resource genuinely not
    # existing -- worth seeing even outside a debugging session.
    if result.returncode != 0:
        log(f"kubectl_read: {cmd} exited {result.returncode}: {output[:500]!r}")
    else:
        debug_log(f"kubectl_read: {cmd} exited 0, {len(output)} chars output")
    return output[:8000] or "[no output]"
