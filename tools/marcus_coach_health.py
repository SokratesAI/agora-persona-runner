"""Is Marcus's chat still a real conversation, or has it gone quiet on the fallback?

The owner's issue #157 says Marcus's chat is *"rule-based/keyword-matched, not a
real conversation"*. That stopped being true when the app started asking an
Agora conversation for every turn -- but only while that conversation keeps
answering. `askCoach` in `SokratesAI/marcus`'s `src/coach.ts` refuses to send
on two conditions, and on either one the browser falls back to the built-in
keyword reply. So the exact state the owner filed comes back, silently, and the
only symptom on his phone is a small "built-in reply" note under each bubble.

**That is not hypothetical: it ran for an unknown length of time.** Cycle 1468
went to probe the chat end to end on 2026-09-12 and found the `Marcus - coach`
conversation (`cc484b5a`) carrying `archived: true`, so every turn was being
refused with *"conversation not in the active listing"* and the whole chat was
on the fallback. It was found by a cycle that happened to be there for another
reason, and that cycle wrote down the open question this module answers:
*"whether anything should be watching it"*. Nothing was.

**The two refusals, and why both are this check's business.**

- *Not in the active listing.* Agora has no `GET /conversations/<id>`, so the
  app reads the filtered listing, and an archived or deleted conversation is
  absent from it. Absent means refused.
- *Not pinned to a `claude-cli:` model.* `identity.md` rule 9 is the owner's hard
  rule -- production never spends the metered API -- so `askCoach` refuses any
  model that is not the subscription provider, including a conversation with
  no model at all, which would silently fall through to Agora's default. That
  refusal is correct and must stay; what is wrong is nobody noticing it fired.

The `claude-cli:` prefix is checked here because it is the owner's pricing rule,
not because `coach.ts` spells it that way. If the app's refusal ever loosens,
this check should not follow it -- a chat that answers by spending the metered
balance is a worse outcome than a chat on the fallback.

**It reads the running Deployment, never a manifest in git.** The conversation
id the app actually uses is the one in the pod's environment; a manifest
ArgoCD has not synced names a conversation nothing is asking. Same call
`tools.helm_repo_health` makes, for the same reason.

**What it deliberately does not do: ask the coach a question.** A real turn is
a model call against the subscription and it writes a message into the
conversation the owner reads, so a check on every sweep would put a trail of
Nova's own probes into his chat history. The two refusals above are both
readable without sending anything, and they are the two that produce silence.
What that costs is honest and printed: a conversation that is listed and
correctly pinned can still fail inside the runner, and this cannot see it.

**Exit contract**, the same as its siblings in `tools.preflight`. 2 when the
chat is on the fallback for a reason a cycle could act on. 1 when something
was unreadable -- an unreachable Agora or a Deployment that could not be read
must never report as a healthy coach, which is the negative-result-guaranteed-
in-advance failure `prompt.md` spends four paragraphs on. 0 when the
conversation is listed and pinned to the subscription provider.
"""

import argparse
import json
import subprocess
import urllib.error
import urllib.request

# Repo root on sys.path so `python3 tools/marcus_coach_health.py` works and not
# only `-m`. See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

DEFAULT_DEPLOYMENT = "marcus"
DEFAULT_NAMESPACE = "agents"
TIMEOUT = 15

#: The env vars `coach.ts` reads to build its config. Both absent means the
#: coach is switched off deliberately; one absent means it is misconfigured.
BASE_URL_VAR = "AGORA_BASE_URL"
CONVERSATION_VAR = "MARCUS_COACH_CONVERSATION_ID"

#: The owner's hard rule, `identity.md` rule 9: the subscription provider, never
#: the metered one. Every `anthropic:` model has an identical `claude-cli:`
#: twin, so a conversation on the wrong side of this is a config mistake with
#: a one-word fix, not a model that had to be given up.
SUBSCRIPTION_PREFIX = "claude-cli:"


def read_coach_config(deployment=DEFAULT_DEPLOYMENT, namespace=DEFAULT_NAMESPACE,
                      run=subprocess.run):
    """`(base_url, conversation_id)` off the running Deployment, or None.

    None means "I could not read it", never "it is unset": kubectl missing,
    a non-zero exit and a body that is not JSON are three different problems
    and none of them is evidence about the coach. An env var that is genuinely
    absent comes back as an empty string in the tuple, which the report tells
    apart from this.
    """
    try:
        done = run(["kubectl", "get", "deployment", deployment, "-n", namespace,
                    "-o", "json"], capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    try:
        spec = json.loads(done.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(spec, dict):
        # A `kubectl get` that exits 0 and prints something other than an
        # object is not a Deployment, and `spec.get` below would raise on it.
        return None
    found = {}
    containers = (spec.get("spec", {}).get("template", {})
                      .get("spec", {}).get("containers") or [])
    for container in containers:
        for entry in container.get("env") or []:
            name, value = entry.get("name"), entry.get("value")
            if name in (BASE_URL_VAR, CONVERSATION_VAR) and isinstance(value, str):
                found.setdefault(name, value)
    return (found.get(BASE_URL_VAR, ""), found.get(CONVERSATION_VAR, ""))


def read_listing(base_url, opener=urllib.request.urlopen):
    """The `?active=true` conversation rows, or None if they could not be read.

    Exactly the listing `askCoach` reads, so a listing this cannot parse is a
    listing the app cannot parse either.
    """
    url = f"{base_url.rstrip('/')}/conversations?active=true"
    try:
        with opener(url, timeout=TIMEOUT) as response:
            if getattr(response, "status", 200) != 200:
                return None
            body = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, UnicodeDecodeError):
        return None
    if isinstance(body, list):
        return body
    if isinstance(body, dict) and isinstance(body.get("conversations"), list):
        return body["conversations"]
    return None


def report(config, rows, out=print):
    """One block and the exit code. Reads nothing and writes nothing."""
    if config is None:
        out("CANNOT SEE  the Marcus Deployment could not be read, so I do not "
            "know which conversation the coach is asking. That is not the same "
            "as a healthy coach and is not reported as one.")
        return 1

    base_url, conversation_id = config
    if not base_url or not conversation_id:
        missing = [n for n, v in ((BASE_URL_VAR, base_url),
                                  (CONVERSATION_VAR, conversation_id)) if not v]
        out(f"ON THE FALLBACK  the Marcus Deployment sets no {' and no '.join(missing)}, "
            "so askCoach returns `unconfigured` and every chat turn is answered "
            "by the built-in keyword reply — which is the state the owner filed as "
            "issue #157.")
        return 2

    if rows is None:
        out("CANNOT SEE  Agora's active conversation listing could not be read "
            f"at {base_url}, so I cannot tell whether the coach would answer. "
            "That is not the same as a healthy coach and is not reported as one.")
        return 1

    row = next((c for c in rows
                if isinstance(c, dict) and c.get("id") == conversation_id), None)
    if row is None:
        out(f"ON THE FALLBACK  conversation {conversation_id} is not in Agora's "
            f"active listing ({len(rows)} row(s) read), so askCoach refuses every "
            "turn and the chat is answered by the built-in keyword reply. It is "
            "archived or gone.")
        out("         to put it back, unarchive it on the public app: "
            f"curl -X PATCH -H 'Content-Type: application/json' -d '{{\"archived\":false}}' "
            f"{base_url.rstrip('/')}/conversations/{conversation_id}")
        return 2

    model = row.get("model")
    if not isinstance(model, str) or not model.startswith(SUBSCRIPTION_PREFIX):
        shown = model if isinstance(model, str) and model else "unset"
        out(f"ON THE FALLBACK  conversation {conversation_id} is on model "
            f"`{shown}`, which is not the `{SUBSCRIPTION_PREFIX}` subscription "
            "provider, so askCoach refuses to send and the chat is answered by "
            "the built-in keyword reply.")
        out("         that refusal is correct — identity.md rule 9 keeps "
            "production off the metered API. The fix is to pin the conversation "
            f"back to its `{SUBSCRIPTION_PREFIX}` twin, not to relax the refusal.")
        return 2

    out(f"OK  Marcus's coach conversation {conversation_id} is in Agora's active "
        f"listing and pinned to `{model}`, so a chat turn reaches a model rather "
        "than the built-in keyword reply.")
    out("         NOT JUDGED: whether the runner answers. This reads the two "
        "conditions askCoach refuses on without sending anything, because a real "
        "turn writes a message into the chat history the owner reads.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deployment", default=DEFAULT_DEPLOYMENT,
                        help="Marcus's Deployment (default: %(default)s)")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE,
                        help="its namespace (default: %(default)s)")
    args = parser.parse_args(argv)
    config = read_coach_config(args.deployment, args.namespace)
    rows = None
    if config and config[0] and config[1]:
        rows = read_listing(config[0])
    return report(config, rows)


if __name__ == "__main__":
    raise SystemExit(main())
