"""Which documents does the site render as current state, and which of
those has a job that refreshes them?

Cycle 401, from a gap Cycle 394 measured and nothing reported. The Plan
page opens with a numbered strip headed *"What I would do next, in
order"*, drawn from `roadmap.md`. That file sat nine days stale on
the owner's phone, because the word "roadmap" appeared in none of the three
weekly prompts and nothing in the hourly one either. Its sibling
`goals.md` is refreshed every Monday. Both are rendered identically, and
nothing anywhere could tell the two situations apart.

The single stale roadmap is not the point -- that is one Monday's work,
and Cycle 394 already handed it to `weekly-reprioritise.md`. The point is
the shape: **a document that makes a claim about *now* needs a job that
renews the claim, and losing that job is silent.** It is the same shape
as a Dependabot alert with no reader (`tools.security_alerts`, whose own
docstring names this file's failure), and it will recur, because a prompt
is edited far more often than the page that reads its output.

    python3 -m tools.doc_owners

**The owner is derived, never declared.** A cycle could have written
`owner: monday` beside each document, and that field would then be the
thing going stale -- a document could lose its refreshing paragraph while
its own frontmatter still swore it had one. So this asks the prompts
instead: it fetches Nova's four cycle prompts out of the vault and a
document is *owned* by any prompt whose text names it. Delete the
paragraph and the owner disappears from this report on the next run, with
no second place to update.

**Three outcomes, and they are kept apart for the same reason
`security_alerts` keeps its three apart.** `owned and fresh` means a job
names the document and the document has been written inside that job's
window. `stale` means it has an owner and the owner has not run -- a real
job that is failing or being skipped. `no owner` means nothing refreshes
it at all, which is the roadmap case and is the worse of the two, because
waiting will never fix it. Collapsing the last two into "out of date"
would print the same line for a job that missed a week and a job that
does not exist.

**Age is measured from the document's own `updated:` stamp, not from when
its bytes last changed** -- or rather from whichever of the two is older.
The first live run of this tool called `roadmap.md` fresh at 6.0 days
while `/plan` was showing the owner `Updated 2026-08-16`, eleven days
back, because something had written to the file on the 21st without
renewing the claim in it. A staleness check that reads the clock the
reader cannot see is the roadmap failure one more level down. See
`claim_age`.

**The staleness window is computed from the owner's cadence, not chosen.**
A number picked per document would be me inventing a limit, which
`personality.md` has a section about. Each prompt declares the weekdays
its heartbeat fires on; the window is the longest gap between two
consecutive firings, plus one day of grace. Monday-only is 7 + 1 = 8
days; Mon/Wed/Fri is 3 + 1 = 4; the hourly prompt is 1 + 1 = 2. When a
document has more than one owner the *shortest* window wins: every prompt
that names a document is a prompt whose cycle is expected to look at it,
so the tightest of those promises is the one a reader relies on.

That was the second choice, and the first live run is why. The longest
window looks safer -- it calls a document stale only once every owner has
missed a turn, so it false-alarms least. It also makes the tightest
promise unenforceable. `journal-digest.md` is rewritten every cycle and
is named by the Monday prompt as well, so under the longest window the
one document here that is never more than twenty minutes old would have
had eight days to go wrong before this said anything.

Exit 0: every rendered document has an owner and is inside its window.
Exit 2: at least one has no owner, or is past its window.
Exit 1: something could not be read, which is no instrument rather than
no findings, and it says which.
"""

import re
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

# Repo root on sys.path so `python3 tools/x.py` works and not only `-m`.
# See tests/test_tools_run_as_scripts.py.
import sys as _sys, pathlib as _pathlib  # noqa: E402
_sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parents[1]))

from agora_runner import nova_idea_pool, nova_journal, nova_plan

OSLO = ZoneInfo("Europe/Oslo")

# The vault client that works from the shell a cycle actually runs in.
# `agora_runner.vault` is the same wire format and cannot be used here:
# the bridge pod holds its CouchDB credentials under `CDB_*` while that
# module reads `COUCHDB_*`, so every call from `Bash` answers HTTP 401 --
# measured this cycle, on the first run of this tool. `prompt.md` records
# the same split for `agora_runner.cycle_health`, which has to run in the
# runner pod for the mirror-image reason. `tools.top_board_rows` shells
# out for exactly this, and so does this.
VAULT_TOOL = "/app/bridge/vault_tool.py"

# How far back to ask for write times. A document older than this is not
# unmeasurable -- it is *at least* this old, which is well past every
# window in `PROMPTS` and so decides the verdict on its own. See
# `_mtimes`.
RECENT_HOURS = 24 * 90

# The prompts a Nova cycle runs from, and the weekdays their heartbeats
# fire on (0 = Monday, matching `datetime.weekday()`). Read off the
# heartbeat schedules recorded in `prompt.md` step 2: retro is
# `cron@0 6 * * 1,3,5`, research `cron@0 6 * * 2,4,6`, reprioritise
# `cron@0 7 * * 1`, and the hourly loop is `every@20m`, which fires on
# every day there is.
PROMPTS = (
    ("prompt.md", "the hourly cycle", (0, 1, 2, 3, 4, 5, 6)),
    ("weekly-retro.md", "the retrospective run", (0, 2, 4)),
    ("weekly-research.md", "the ideas & research run", (1, 3, 5)),
    ("weekly-reprioritise.md", "the goals & reprioritise run", (0,)),
)

PROMPT_PREFIX = "projects/sokrates/projects/agora/nova/resources/"

# Documents the site presents as a statement about *now*. Membership here
# is a judgement and it is the only judgement in this file: a board is
# the owner's own writing, so an old row on it is a fact about him rather
# than a claim of mine going stale, and a ledger is appended by machine
# where "out of date" means nothing. These four are the ones where the
# page speaks in my voice about the present, and a stale one is the page
# telling him something untrue.
#
# The paths are imported rather than retyped so that moving a document
# updates this file for free -- the one kind of drift a registry like
# this is otherwise guaranteed to acquire.
DOCUMENTS = (
    ("Roadmap", nova_plan.ROADMAP_PATH, "/plan", "what I would work next, in order"),
    ("Goals", nova_plan.GOALS_PATH, "/plan", "the scoreboard and its measures"),
    ("Digest", nova_journal.DIGEST_PATH, "/", "what the last cycles did"),
    ("Idea pool", nova_idea_pool.POOL_PATH, "/pool", "ideas waiting for your decision"),
)


def longest_gap_days(weekdays):
    """Days between the two consecutive firings that are furthest apart,
    over a repeating week. `(0,)` is 7; `(0, 2, 4)` is 3 (Friday to
    Monday); a job that fires every day is 1, and the caller subtracts
    nothing -- see `window_days`.
    """
    days = sorted(set(weekdays))
    if not days:
        raise ValueError("a prompt with no firing days has no cadence")
    gaps = [b - a for a, b in zip(days, days[1:])]
    gaps.append(days[0] + 7 - days[-1])
    return max(gaps)


def window_days(weekdays):
    """How old a document may be before its owner has visibly missed a
    turn: one full cadence gap, plus a day of grace so that a job which
    ran late rather than not at all does not read as a failure.

    The hourly prompt lands at 1 + 1 = 2 days, which is deliberately
    generous for something that runs 72 times a day -- the check is for a
    document nobody renews, not for a quiet night.
    """
    return longest_gap_days(weekdays) + 1


def owners(path, prompt_texts):
    """Every prompt whose text names this document, as `(file, role,
    weekdays)`.

    Matching is on the basename, not the full vault path, because the
    prompts refer to these documents by name in prose far more often than
    by path -- `roadmap.md` appears in `weekly-reprioritise.md` without a
    folder in front of it. That makes basename collisions load-bearing:
    two vault documents called `issues.md` exist (mine and the owner's) and
    neither is registered here for exactly that reason. `check_registry`
    refuses a registry that reintroduces the ambiguity.
    """
    name = path.rsplit("/", 1)[-1]
    return [
        (prompt_file, role, weekdays)
        for prompt_file, role, weekdays in PROMPTS
        if name in prompt_texts.get(prompt_file, "")
    ]


def check_registry(documents=DOCUMENTS):
    """The basenames in `DOCUMENTS` must be distinct, or `owners` would
    credit one document with another's owner and report both as fine.
    Raises rather than warning: a registry that cannot be matched
    unambiguously has no correct output to print.
    """
    names = [path.rsplit("/", 1)[-1] for _, path, _, _ in documents]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ValueError(
            "two registered documents share a basename, so owners() cannot "
            f"tell them apart: {', '.join(duplicates)}"
        )


def age_days(written, now):
    """Days since the document was last written, or `None` when no write
    time was found for it -- which is unknown, not zero.
    """
    if written is None:
        return None
    return (now - written).total_seconds() / 86400


def declared_date(text):
    """The `updated:` stamp from the document's own frontmatter as an Oslo
    datetime, or `None` when there is no stamp or it is not a plain date.

    Parsed at **midnight** rather than end of day: a stamp is a claim about
    a day, and taking its earliest instant makes the resulting age the
    largest the stamp can honestly support. That direction is the same one
    `parse_recent` picks when a row will not parse -- for a staleness check,
    erring toward "look at this" is the safe error.
    """
    stamp = nova_plan._updated(text or "")
    try:
        when = datetime.strptime(stamp.strip(), "%Y-%m-%d")
    except ValueError:
        return None
    return when.replace(tzinfo=OSLO)


def claim_age(write_age, stamp_age):
    """How old the document's *claim* is: the older of the two ages, or
    whichever one exists.

    **This is the whole point of the file and it was reading the wrong
    clock.** `vault_tool.py recent` reports when the bytes last changed,
    and a byte can change for reasons that renew nothing -- a typo, a
    section appended by a tool, a reformat. The page does not show that
    date. It shows the `updated:` stamp out of the frontmatter, and that
    stamp is what a cycle bumps when it actually rewrites the thinking.
    Measured Cycle 510 on the live vault: `roadmap.md` was last written
    2026-08-21 and stamps itself `2026-08-16`, so this tool called it
    6.0d old and fresh while `/plan` told the owner it was eleven days
    old -- two numbers off one document, and the alarm was reading the
    one nobody sees.

    Taking the older of the two rather than the stamp alone keeps the
    opposite failure covered: a cycle that rewrites a document and
    forgets to bump the stamp still ages by its bytes, and a stamp dated
    in the future cannot make anything look younger than it is.
    """
    ages = [a for a in (write_age, stamp_age) if a is not None]
    return max(ages) if ages else None


def verdict(found, age, owner_list, blind=False):
    """One of `missing`, `unknown owner`, `no owner`, `stale`, `fresh`.

    `blind` says at least one prompt could not be fetched. It only ever
    matters when nothing readable named the document, and then it is the
    whole answer: **an owner that failed to load looks exactly like an
    owner that does not exist**, and those are opposite findings. The
    reviewer proved this against the live registry -- drop
    `weekly-reprioritise.md` alone and both `/plan` documents printed
    "no cycle prompt names roadmap.md, so nothing refreshes it and
    waiting will not help", which is false and is this tool's own most
    alarming sentence. That is the roadmap failure re-created one layer
    down, inside the check written to report it.

    Order matters. A document that is not in the vault at all is reported
    as missing rather than as stale, because "the page renders nothing"
    and "the page renders something old" send a reader to different
    places. And `no owner` is checked before age deliberately: a document
    nobody refreshes is the finding whether or not it happens to be young
    today, because nothing will stop it ageing.

    `age is None` here means the vault listed the file but reported no
    write inside `RECENT_HOURS`, so it is *at least* that old -- far past
    every window, hence stale rather than unknown.
    """
    if not found:
        return "missing"
    if not owner_list:
        return "unknown owner" if blind else "no owner"
    if age is None:
        return "stale"
    limit = min(window_days(weekdays) for _, _, weekdays in owner_list)
    return "stale" if age > limit else "fresh"


def report(documents, prompt_texts, written, present, now, blind=False,
           declared=None):
    """One row per registered document: `(name, page, claim, verdict,
    age, owners, limit)`. Pure -- every read this needs has already
    happened, which is what makes the whole judgement testable without a
    vault.

    `declared` is `{path: datetime}` from each document's own `updated:`
    stamp; see `claim_age` for why the verdict is taken off the older of
    that and the write time. It defaults to empty so a caller with no
    stamps behaves exactly as this did before.
    """
    declared = declared or {}
    rows = []
    for name, path, page, claim in documents:
        owner_list = owners(path, prompt_texts)
        write_age = age_days(written.get(path), now)
        stamp_age = age_days(declared.get(path), now)
        age = claim_age(write_age, stamp_age)
        limit = (
            min(window_days(weekdays) for _, _, weekdays in owner_list)
            if owner_list
            else None
        )
        rows.append(
            {
                "name": name,
                "path": path,
                "page": page,
                "claim": claim,
                "verdict": verdict(path in present, age, owner_list, blind),
                "age": age,
                "writeAge": write_age,
                "stampAge": stamp_age,
                "owners": owner_list,
                "limit": limit,
            }
        )
    return rows


def stamp_explains(row):
    """True when this row is stale *because of* its `updated:` stamp -- the
    bytes alone would have read as fresh.

    Only printed in that case, and deliberately not whenever the two
    numbers merely disagree. `goals.md` is edited by the /plan page every
    time the owner ticks a goal, and `idea-pool.md` by its own refresh, so
    a note on every divergence would be a permanent line under two rows
    where nothing is wrong. It earns its place exactly when a reader would
    otherwise ask why a document written this week is being called stale.
    """
    if row["verdict"] != "stale":
        return False
    write_age, limit = row.get("writeAge"), row.get("limit")
    if write_age is None or limit is None:
        return False
    return write_age <= limit


def _age_text(age):
    if age is None:
        return f"not written in {RECENT_HOURS // 24}d"
    if age < 1:
        return f"{age * 24:.0f}h old"
    return f"{age:.1f}d old"


def render(rows, unreadable):
    """The report as text. Findings first and in full; the clean rows
    named on one line, so "checked and fine" can never be mistaken for
    "never looked" -- the same rule `security_alerts` prints under.
    """
    lines = []
    bad = [r for r in rows if r["verdict"] != "fresh"]
    good = [r for r in rows if r["verdict"] == "fresh"]

    for row in bad:
        if row["verdict"] == "no owner":
            lines.append(
                f"NO OWNER  {row['name']} ({row['page']}) — no cycle prompt names "
                f"{row['path'].rsplit('/', 1)[-1]}, so nothing refreshes it and "
                f"waiting will not help. It is {_age_text(row['age'])} and the page "
                f"presents it as {row['claim']}."
            )
        elif row["verdict"] == "unknown owner":
            lines.append(
                f"UNKNOWN   {row['name']} ({row['page']}) — no prompt that loaded "
                f"names {row['path'].rsplit('/', 1)[-1]}, and at least one prompt "
                f"did not load, so I cannot tell an absent owner from an unread "
                f"one. Fix the read below and run this again."
            )
        elif row["verdict"] == "missing":
            lines.append(
                f"MISSING   {row['name']} ({row['page']}) — {row['path']} is not in "
                f"the vault, so the page has nothing to draw."
            )
        else:
            who = ", ".join(f"{role} ({f})" for f, role, _ in row["owners"])
            lines.append(
                f"STALE     {row['name']} ({row['page']}) — {_age_text(row['age'])}, "
                f"past the {row['limit']}d window of its tightest owner. Owned by {who}, so "
                f"the job exists and has not run."
            )

    for row in bad:
        if not stamp_explains(row):
            continue
        lines.append(
            f"          ...and the stamp is why: the bytes are only "
            f"{_age_text(row['writeAge'])}, so the write time alone would have "
            f"read as fresh. Something edited {row['path'].rsplit('/', 1)[-1]} "
            f"without renewing the claim in it, and {row['page']} shows the "
            "stamp."
        )

    if good:
        lines.append(
            "Owned and fresh: "
            + "; ".join(
                f"{r['name']} {_age_text(r['age'])} (<{r['limit']}d, "
                + ", ".join(role for _, role, _ in r["owners"])
                + ")"
                for r in good
            )
        )
    if not bad:
        lines.append(
            f"Nothing to act on. Every one of the {len(rows)} document(s) the site "
            "renders as current state has a job that refreshes it."
        )
    for note in unreadable:
        lines.append(f"⚠ could not read {note}")
    return "\n".join(lines)


def _vault(*args):
    """`vault_tool.py <args>` as text, or `None` if it did not return one.

    A missing file exits **0** and prints `[not found: <path>]` on stdout
    (`tools.top_board_rows._fetch` records the measurement), so the return
    code alone reads an absent prompt as an empty one -- and an empty
    prompt names no document, which would report every document as
    unowned. That is the one wrong answer this tool must not produce, so
    the `[not found:` prefix is checked as well as the exit status.
    """
    try:
        done = subprocess.run(
            [sys.executable, VAULT_TOOL, *args],
            capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    if not done.stdout.strip() or done.stdout.lstrip().startswith("[not found:"):
        return None
    return done.stdout


_RECENT_ROW_RE = re.compile(
    r"^(?P<when>\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(?P<path>\S.*)$"
)


def parse_recent(text):
    """`{path: datetime}` from `vault_tool.py recent`'s listing.

    Its rows are `YYYY-MM-DD HH:MM  <path>` in **Oslo** time, under a
    `[N file(s) modified ...]` header this skips. A row that does not
    parse is dropped rather than guessed at: an unparsed row leaves the
    document with no write time, which reads as very old, and erring
    toward "look at this" is the right direction for a staleness check.

    **The newest row for a path wins, and that is not defensive coding.**
    One path can appear twice in this listing with two different times --
    measured on the first live run of this tool, where
    `journal-digest.md` came back at both `2026-08-25 04:34` and
    `2026-08-18 15:32`, so the vault holds two file docs for it. The
    listing is newest-first, so a plain `found[path] = ...` keeps the
    *oldest* of the pair, and the check reported a digest rewritten
    minutes earlier as 6.6 days old. It was inside its window, so it
    printed as fine -- a wrong number that happened to land on the right
    verdict, which is the version of this that never gets found.
    """
    found = {}
    for line in text.splitlines():
        match = _RECENT_ROW_RE.match(line.strip())
        if not match:
            continue
        try:
            when = datetime.strptime(match["when"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        when = when.replace(tzinfo=OSLO)
        path = match["path"]
        if path not in found or when > found[path]:
            found[path] = when
    return found


def _read_state(documents):
    """`(prompt_texts, written, present, unreadable)` -- every vault read
    this needs, and nothing else. Split out so `report` and `render` stay
    pure and testable without a vault behind them.
    """
    prompt_texts, unreadable = {}, []
    for prompt_file, _, _ in PROMPTS:
        text = _vault("get", PROMPT_PREFIX + prompt_file)
        if text is None:
            unreadable.append(prompt_file)
            continue
        prompt_texts[prompt_file] = text

    written, present = {}, set()
    for prefix in sorted({p.rsplit("/", 1)[0] + "/" for _, p, _, _ in documents}):
        listing = _vault("ls", prefix)
        if listing is None:
            unreadable.append(f"listing of {prefix}")
        else:
            present.update(line.strip() for line in listing.splitlines() if line.strip())
        recent = _vault("recent", str(RECENT_HOURS), prefix)
        if recent is None:
            unreadable.append(f"write times under {prefix}")
        else:
            written.update(parse_recent(recent))

    # The stamp each document makes about itself. A document that cannot
    # be read is *not* recorded as unreadable here: its write time and its
    # presence in the listing were both measured above, so the verdict
    # still stands on real evidence and only the drift note is lost.
    # Adding it to `unreadable` would drop the whole run to exit 1 -- no
    # instrument -- over a detail line.
    declared = {}
    for _, path, _, _ in documents:
        if path not in present:
            continue
        text = _vault("get", path)
        when = declared_date(text) if text is not None else None
        if when is not None:
            declared[path] = when
    return prompt_texts, written, present, unreadable, declared


def main(argv=None):
    check_registry()
    prompt_texts, written, present, unreadable, declared = _read_state(DOCUMENTS)
    if not prompt_texts:
        print(
            "refusing to report: none of the cycle prompts could be read, so "
            "every document would look unowned. "
            + ", ".join(unreadable),
            file=sys.stderr,
        )
        return 1

    rows = report(
        DOCUMENTS, prompt_texts, written, present, datetime.now(tz=OSLO),
        blind=any(f in unreadable for f, _, _ in PROMPTS),
        declared=declared,
    )
    print(render(rows, unreadable))

    # A prompt this could not read is a document whose owner is unknown
    # rather than absent, so it is exit 1 -- no instrument -- even when
    # every row it *could* measure came back fine.
    if unreadable:
        return 1
    return 2 if any(r["verdict"] != "fresh" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
