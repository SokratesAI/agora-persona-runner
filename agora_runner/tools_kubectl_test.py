"""kubectl_test -- full CRUD, pinned to the `test` namespace and nowhere else.

`tools_kubectl.kubectl_read` next door is read-only over the whole cluster.
This is its mirror image: it can create, patch, delete and exec, and it can
only ever touch one namespace. Idea #230 on the owner's board asked for that --
"a dedicated `test` k8s namespace where Nova has full CRUD access, with a
stable pod off the prod image for live coding/testing sessions" -- and item 1
of that spec (the namespace and the RBAC grant) landed in platform-config#599.
This is item 2.

**The namespace is refused twice, on purpose**, the way kubectl_read blocks
Secrets twice. RBAC on the runner's ServiceAccount is the backstop and it was
verified live (yes to deployments/pods/exec in `test`, no to anything in
`agents` or `infra`). That is not a reason to let a wrong namespace leave this
process: an RBAC refusal arrives as an opaque Forbidden that reads like a
broken grant, and a grant that widens later would silently widen this tool
with it. So every path out of here that names a namespace names this one.

There are four ways a namespace can enter a kubectl invocation and all four
are closed here:

1. `namespace` -- must be absent or exactly `test`; anything else is refused
   rather than corrected, because silently rewriting `agents` to `test` would
   run a command nobody asked for.
2. a flag in `args` -- `-n`, `--namespace`, `-A` and `--all-namespaces` are
   not in the flag allowlist, so they are refused by name rather than by
   being overridden further down the argv.
3. a cluster-scoped resource -- `-n` is *ignored* on a Node or a
   ClusterRoleBinding rather than rejected, so `delete node server1` would
   have been a legal call under a namespace check alone. Only namespaced
   kinds from ALLOWED_RESOURCE_KINDS are accepted.
4. a manifest that declares its own `metadata.namespace` -- `-f` and
   `--filename` are not allowlisted, so YAML arrives through the `manifest`
   argument and is parsed here: every document must be an allowed kind and
   must either omit the namespace or name `test`. kubectl itself also
   refuses a mismatch against `-n`, which is the second layer.
"""

import subprocess

import yaml

from agora_runner.log import log, debug_log


KUBECTL_TEST_NAMESPACE = "test"

# `exec` and `delete` are the point of this tool -- it exists so a live
# session can iterate inside a pod. `port-forward` is deliberately absent:
# it does not terminate on its own, so it cannot be run under a timeout.
KUBECTL_TEST_ALLOWED_VERBS = {
    "get", "describe", "logs", "top",
    "apply", "create", "delete", "patch", "scale", "rollout", "exec",
}

# Verbs that take YAML on stdin rather than a resource name.
KUBECTL_TEST_MANIFEST_VERBS = {"apply", "create"}

# Namespaced kinds only. A kind that is not on this list is refused rather
# than passed through, because a cluster-scoped resource ignores `-n` and
# the namespace check above would pass while the command escaped the
# namespace entirely.
KUBECTL_TEST_ALLOWED_RESOURCE_KINDS = {
    "pod", "pods", "po",
    "deployment", "deployments", "deploy",
    "replicaset", "replicasets", "rs",
    "statefulset", "statefulsets", "sts",
    "daemonset", "daemonsets", "ds",
    "job", "jobs",
    "cronjob", "cronjobs", "cj",
    "service", "services", "svc",
    "endpoints", "ep",
    "ingress", "ingresses", "ing",
    "configmap", "configmaps", "cm",
    "persistentvolumeclaim", "persistentvolumeclaims", "pvc",
    "serviceaccount", "serviceaccounts", "sa",
    "event", "events", "ev",
    "networkpolicy", "networkpolicies", "netpol",
    "horizontalpodautoscaler", "horizontalpodautoscalers", "hpa",
}

# Secrets are refused here as well as by RBAC, same as kubectl_read: this
# tool can *write*, so a Secret it could create is one it could then read
# back through its own `get`.
KUBECTL_TEST_FORBIDDEN_RESOURCE_PREFIXES = ("secret",)

# No flag on this list can name a namespace, name a file, or reach the API
# server directly (`--raw`). `-o`/`--output` is here because reading YAML
# back is how a session checks what it just applied.
KUBECTL_TEST_ALLOWED_FLAGS = {
    "-o", "--output", "--field-selector", "--selector", "-l",
    "--tail", "--since", "--previous", "-c", "--container",
    "--force", "--grace-period", "--now", "--wait", "--timeout",
    "--replicas", "--type", "--patch", "-p", "--record", "--dry-run",
    "--ignore-not-found", "--show-labels", "--sort-by", "--watch=false",
}


def _refuse(message):
    return f"[kubectl_test: {message}]"


def _check_manifest(manifest):
    """`None` if every document may be applied, else the refusal message."""
    try:
        documents = list(yaml.safe_load_all(manifest))
    except yaml.YAMLError as e:
        return f"manifest is not valid YAML: {e}"
    documents = [d for d in documents if d is not None]
    if not documents:
        return "manifest is empty"
    for doc in documents:
        if not isinstance(doc, dict):
            return "every manifest document must be a mapping"
        kind = str(doc.get("kind", "")).strip().lower()
        if not kind:
            return "every manifest document needs a kind"
        if any(kind.startswith(p) for p in KUBECTL_TEST_FORBIDDEN_RESOURCE_PREFIXES):
            return "creating Secrets is never allowed through this tool"
        if kind not in KUBECTL_TEST_ALLOWED_RESOURCE_KINDS:
            return (f"kind {kind!r} is not one this tool may write -- only "
                    f"{sorted(KUBECTL_TEST_ALLOWED_RESOURCE_KINDS)}")
        metadata = doc.get("metadata") or {}
        if not isinstance(metadata, dict):
            return "metadata must be a mapping"
        declared = str(metadata.get("namespace", "")).strip()
        if declared and declared != KUBECTL_TEST_NAMESPACE:
            return (f"manifest declares namespace {declared!r} -- this tool only "
                    f"ever touches {KUBECTL_TEST_NAMESPACE!r}")
    return None


def kubectl_test(args):
    if not isinstance(args, dict):
        return _refuse("invalid arguments")
    verb = str(args.get("verb", "")).strip().lower()
    resource = str(args.get("resource", "")).strip()
    namespace = str(args.get("namespace", "")).strip()
    manifest = args.get("manifest")
    command = args.get("command") or []
    extra = args.get("args") or []

    if verb not in KUBECTL_TEST_ALLOWED_VERBS:
        return _refuse(f"verb {verb!r} not allowed -- only "
                       f"{sorted(KUBECTL_TEST_ALLOWED_VERBS)}")
    if namespace and namespace != KUBECTL_TEST_NAMESPACE:
        return _refuse(f"namespace {namespace!r} refused -- this tool only ever "
                       f"touches {KUBECTL_TEST_NAMESPACE!r}")
    if not isinstance(extra, list):
        return _refuse("'args' must be a list of strings")
    for flag in extra:
        flag_name = str(flag).split("=", 1)[0]
        if flag_name not in KUBECTL_TEST_ALLOWED_FLAGS:
            return _refuse(f"flag {flag!r} not allowed -- only "
                           f"{sorted(KUBECTL_TEST_ALLOWED_FLAGS)}")

    if verb in KUBECTL_TEST_MANIFEST_VERBS:
        if not isinstance(manifest, str) or not manifest.strip():
            return _refuse(f"{verb} needs a 'manifest' -- YAML is passed as a "
                           "string, never as a file path")
        problem = _check_manifest(manifest)
        if problem:
            return _refuse(problem)
        cmd = ["kubectl", verb, "-f", "-", "-n", KUBECTL_TEST_NAMESPACE]
        cmd.extend(str(a) for a in extra)
        stdin = manifest
    else:
        if not resource:
            return _refuse("resource is required")
        resource_kind = resource.split("/")[0].split(".")[0].strip().lower()
        if any(resource_kind.startswith(p)
               for p in KUBECTL_TEST_FORBIDDEN_RESOURCE_PREFIXES):
            return _refuse("reading or writing Secrets is never allowed "
                           "through this tool")
        if resource_kind not in KUBECTL_TEST_ALLOWED_RESOURCE_KINDS:
            return _refuse(f"resource {resource_kind!r} is not namespaced or is "
                           f"not allowed -- only "
                           f"{sorted(KUBECTL_TEST_ALLOWED_RESOURCE_KINDS)}")
        cmd = ["kubectl", verb, resource, "-n", KUBECTL_TEST_NAMESPACE]
        cmd.extend(str(a) for a in extra)
        if verb == "exec":
            if not isinstance(command, list) or not command:
                return _refuse("exec needs a non-empty 'command' list")
            cmd.append("--")
            cmd.extend(str(c) for c in command)
        stdin = None

    debug_log(f"kubectl_test: running {cmd}")
    try:
        result = subprocess.run(cmd, input=stdin, capture_output=True,
                                text=True, timeout=60)
    except FileNotFoundError:
        log(f"kubectl_test: binary not installed in this image (cmd={cmd})")
        return _refuse("binary not installed in this image")
    except subprocess.TimeoutExpired:
        log(f"kubectl_test: {cmd} timed out after 60s")
        return _refuse("timed out after 60s")
    except Exception as e:
        log(f"kubectl_test: {cmd} raised {e}")
        return f"[kubectl_test error: {e}]"
    output = (result.stdout or "") + (result.stderr or "")
    # Same reasoning as kubectl_read: a nonzero exit is the one signal that
    # the grant or the kubeconfig is wrong rather than the resource simply
    # not existing, so it is logged outside a debugging session.
    if result.returncode != 0:
        log(f"kubectl_test: {cmd} exited {result.returncode}: {output[:500]!r}")
    else:
        debug_log(f"kubectl_test: {cmd} exited 0, {len(output)} chars output")
    return output[:8000] or "[no output]"
