"""Opening a project's goal conversation, and recording it on the objective.

Issue #227, after the owner's correction of 2026-09-13 21:02: *"I should not
have to approve goals. Goals, milestones, kpis and okrs should be derived
based on a conversation between you and me where we challenge each other and
then agree on something."* `allocation-model.md` spells out what that makes
mechanical: **every project has a conversation of its own**, named for it,
and it is where the objective, its key results and its KPIs get settled --
and later where they get revisited when a number stops moving.

`agora_runner.project_goals` already carries the far end of that: an
objective has a `conversation` field, and `problems()` reports an objective
that claims `agreed` while linking nothing. Nothing opened the thread or
wrote the link. This does both, in one command, so that once the prune is
settled a goal conversation per surviving project is one call each rather
than a cycle of hand-plumbing per project.

    python -m agora_runner.project_goal_thread --project Nova \
        --message-file /tmp/nova-goals.md --cycle 1532

**Run it from the runner pod (`terminal_exec`).** It needs `AGORA_TOKEN` to
post and the `COUCHDB_*` credentials to write the vault, and the bridge pod
holds neither under those names.

Three deliberate refusals, each of which is a way this could quietly do the
wrong thing:

- **A project with no section in `project-goals.md` is refused**, not
  created. The set of projects that exist is read off the Project column on
  the boards; a missing section means step 2 has not written content for it,
  and inventing an objective here to hang a thread on is exactly the
  guess-then-tick shape the correction deleted.
- **An objective that already links a conversation is refused.** The thread
  is the durable place the goal is argued; a second one splits the argument
  across two places and neither is the record.
- **A thread that already exists gets no second opening message.** Agora
  de-duplicates `POST /conversations` by name and answers `200` for one it
  already has, so the repair path -- thread opened, link never written -- is
  to record the link and stay quiet, not to post the opening move twice.

The message itself is not generated. `allocation-model.md` asks the opening
message to say what I think the project is for, what I would measure, what
is *wrong* with my own suggestion, and the questions only he can answer.
Three of those four are judgement, and a template that filled them in would
hand him a form to tick -- which is the thing he struck out.
"""

import argparse
import sys

from agora_runner.http_util import agora_internal
from agora_runner.log import log
from agora_runner.nova_conversations import ANSWER_PERSONA_ID
# The four private regexes come from the parser on purpose: a second copy of
# "what a fence looks like" is a second thing to keep in step, and this writer
# has to agree with that reader exactly or it edits the wrong lines.
from agora_runner.project_goals import (
    PROJECT_GOALS_PATH, _FENCE_CLOSE_RE, _FENCE_OPEN_RE, _FIELD_RE,
    _HEADING_RE, parse_project_goals,
)
from agora_runner.vault import vault_read_path_rev, vault_write_path


#: Part of the de-duplication key, the same way `needs_input.NAME_PREFIX` is:
#: the thread is found again by its exact name, so changing this suffix
#: orphans every goal conversation already open.
NAME_SUFFIX = " — goals"

#: A label for the needs-input page to select on later; the name is what
#: de-duplicates.
GOALS_TAG = "nova:project-goals"


def conversation_name(project):
    return f"{project.strip()}{NAME_SUFFIX}"


def opening_message(body, project, cycle=None):
    who = f"Nova, cycle {cycle}" if cycle else "Nova"
    return (f"**{project.strip()} — what this project is for, and what we measure**\n\n"
            f"{body.strip()}\n\n"
            f"— {who}. This thread is where {project.strip()}'s goal lives: "
            f"argue with it here and the agreed text is what I write to the board.")


def set_objective_conversation(markdown, project, conversation_id):
    """Write `conversation: <id>` into one project's ```objective fence.

    Returns `(markdown, problem)` -- `problem` is None on success and a
    sentence otherwise, and on a problem the markdown comes back untouched.

    It rewrites a line rather than re-rendering the document on purpose:
    every other line in `project-goals.md` is his words or a measurement
    somebody took, and a renderer that reproduces them is a renderer that
    can drop one.
    """
    wanted = (project or "").strip().lower()
    if not wanted:
        return markdown, "a project name is required"
    if not (conversation_id or "").strip():
        return markdown, "a conversation id is required"

    lines = (markdown or "").split("\n")
    out, current, fence, in_objective = [], None, None, False
    seen_section = written = False
    for line in lines:
        if fence is not None:
            if _FENCE_CLOSE_RE.match(line):
                if in_objective and not written:
                    # No `conversation:` line to replace, so add one at the
                    # end of the fence rather than the top: the statement is
                    # what a reader opens the block for.
                    out.append(f"conversation: {conversation_id.strip()}")
                    written = True
                fence, in_objective = None, False
                out.append(line)
                continue
            if in_objective:
                match = _FIELD_RE.match(line.strip())
                if match and match.group("key") == "conversation":
                    if match.group("value").strip():
                        return markdown, (
                            f"{project} already links conversation "
                            f"{match.group('value').strip()} -- a goal is argued "
                            f"in one thread, not two")
                    out.append(f"conversation: {conversation_id.strip()}")
                    written = True
                    continue
            out.append(line)
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            current = heading.group("name").strip().lower()
            if current == wanted:
                seen_section = True
        opened = _FENCE_OPEN_RE.match(line)
        if opened:
            fence = opened.group("name")
            in_objective = (fence == "objective" and current == wanted
                            and not written)
        out.append(line)

    if not seen_section:
        return markdown, f"project-goals.md has no `## {project}` section"
    if not written:
        return markdown, f"the `## {project}` section has no ```objective fence"
    return "\n".join(out), None


def _existing_conversation(markdown, project):
    section = parse_project_goals(markdown).get((project or "").strip().lower())
    if not section:
        return None
    return ((section.get("objective") or {}).get("conversation") or "").strip() or None


def open_thread(project, body, cycle=None, markdown=None, rev=None):
    """Open (or adopt) the project's goal thread and record it. `(ok, info)`."""
    # Validated against the document BEFORE anything is created, so a project
    # the write would reject never leaves an empty thread behind in Agora for
    # him to wonder about. The sentinel result is discarded; the real id is
    # written by the identical call below.
    _, problem = set_objective_conversation(markdown, project, "pending")
    if problem:
        return False, problem

    name = conversation_name(project)
    status, payload = agora_internal("POST", "/conversations", {
        "name": name, "personaId": ANSWER_PERSONA_ID})
    if status not in (200, 201):
        log(f"project_goal_thread: create failed HTTP {status}")
        return False, f"could not open the conversation (HTTP {status})"
    cid = (payload.get("conversation") or {}).get("id")
    if not cid:
        return False, "Agora answered with no conversation id"
    adopted = status == 200

    updated, problem = set_objective_conversation(markdown, project, cid)
    if problem:
        return False, problem

    if not adopted:
        tag_status, _ = agora_internal("PATCH", f"/conversations/{cid}", {
            "tags": [GOALS_TAG]})
        if tag_status not in (200, 201):
            log(f"project_goal_thread: could not tag {cid} (HTTP {tag_status})")
        post_status, posted = agora_internal("POST", f"/conversations/{cid}/notify", {
            "text": opening_message(body, project, cycle=cycle),
            "sender": "Nova", "system": False})
        if post_status not in (200, 201) or not (posted.get("message") or {}).get("id"):
            log(f"project_goal_thread: notify failed HTTP {post_status}")
            return False, (f"opened {name} but could not post the opening message "
                           f"(HTTP {post_status}) -- run again to adopt it")

    try:
        vault_write_path(PROJECT_GOALS_PATH, updated, if_rev=rev)
    except Exception as exc:  # the thread exists; say so rather than lose it
        return False, (f"posted into {name} ({cid}) but could not write the link "
                       f"back to project-goals.md: {exc} -- run again to record it")
    return True, {"conversationId": cid, "name": name, "adopted": adopted}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", required=True,
                    help="the project, spelled as its `## ` heading")
    ap.add_argument("--message-file",
                    help="the opening message, as markdown on disk")
    ap.add_argument("--message", help="the opening message inline")
    ap.add_argument("--cycle", help="your cycle number, for the signature")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the name and the message, post and write nothing")
    args = ap.parse_args(argv)

    body = args.message
    if args.message_file:
        try:
            with open(args.message_file) as handle:
                body = handle.read()
        except OSError as exc:
            print(f"could not read {args.message_file}: {exc}")
            return 2
    if not (body or "").strip():
        print("an opening message is required -- see allocation-model.md for "
              "what it has to do")
        return 2

    if args.dry_run:
        print(conversation_name(args.project))
        print()
        print(opening_message(body, args.project, cycle=args.cycle))
        return 0

    try:
        markdown, rev = vault_read_path_rev(PROJECT_GOALS_PATH)
    except Exception as exc:
        print(f"could not read project-goals.md: {exc}")
        return 2
    already = _existing_conversation(markdown, args.project)
    if already:
        print(f"{args.project} already links conversation {already}")
        return 3

    ok, info = open_thread(args.project, body, cycle=args.cycle,
                           markdown=markdown, rev=rev)
    if not ok:
        print(info)
        return 1
    what = "adopted the existing thread" if info["adopted"] else "opened"
    print(f"{what}: {info['name']}  ({info['conversationId']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
