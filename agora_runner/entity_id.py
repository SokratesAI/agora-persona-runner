"""Stable ids for projects and milestones, so a rename is not an orphaning.

The second piece of issue #203 -- the owner's decision of 2026-09-08 that
the boards get a real schema (`projects/sokrates/projects/nova/board-records.md`,
`status: approved`). `rank_key` was the first. That spec names this one
directly: *"Stable ids for projects and milestones, so a rename is an edit
to one document instead of an orphaning event."*

The thing it replaces is a **string join**. Today a row carries the display
name of its project and its milestone, and everything downstream matches on
that text: `nova_site` lowercases both sides in three places because `Nova`
and `nova` are one project spelled two ways, and a milestone is matched to
its rows by comparing names. Rename a project on one row and it is a
different project; rename it everywhere and every reader still has to agree
on how to fold the case.

**Nothing calls this yet, on purpose**, for the same reason `rank_key` does
not: the spec is explicit that the store, the migration and all 29 readers
move in one change with no facade phase, because a facade is what creates
the window in which two stores are both live. This is the pure part of that
change and can be checked on its own.

## The id is minted, never derived

The obvious design is to hash or slugify the name and call that the id.
It is wrong, and it is wrong in exactly the way this module exists to fix:
if the id is a function of the name then renaming a project changes its id,
which is the orphaning event the spec asks to stop, moved one layer down
where it is harder to see.

So the id is minted **once**, at first sight of a name, and never changes
again. The name becomes an ordinary mutable field. A readable slug is used
as the *seed* of the id because a CouchDB document id a person may have to
read in a log is worth more than an opaque counter -- but it is a snapshot
of the name at mint time, not a live derivation, and `rename` deliberately
does not touch it. A `prj_nova` whose display name is now `Aurora` looks odd
and is correct; the alternative looks tidy and loses rows.

## What normalisation is for, and what it is not for

`normalise` folds case and collapses whitespace, and it is used for exactly
one thing: deciding whether a name the caller just read is a name already
known. It is never an id and never stored as one. That is the difference
between this and the lowercasing scattered through `nova_site` -- there the
fold *is* the key, so every reader has to perform the same fold forever, and
a reader that folds differently silently splits a project in two.

## Renaming keeps the old name resolvable

`rename_project` keeps the previous normalised name as an alias. That is not
politeness, it is what makes the one-shot migration survivable: the rows are
markdown today and carry *names*, so a board written before a rename must
still resolve to the same project after it. An alias may never be taken over
by a different entity -- `ensure` returns the existing id rather than minting
a second one, and `rename` onto a name another entity holds is refused.

## Milestones are scoped to their project

`nova_boards` already says so, and the store must not lose it: *"two projects
may each have a `Backup` milestone and they are different milestones."* So a
milestone is resolved by `(projectId, normalised name)`, never by name alone,
and `ensure_milestone` on an unknown project id is an error rather than a
mint into nowhere.

## What this cannot do

It holds no revision and performs no write. Two writers that both `ensure`
the same new name against the same registry snapshot mint two ids; that is
the store's compare-and-swap to lose, not this module's -- the same division
of labour as `rank_key`'s duplicate-key note.
"""

from __future__ import annotations

import re

#: Prefixes, so a bare id says what kind of thing it points at when it turns
#: up alone in a log line or a document id.
PROJECT_PREFIX = "prj"
MILESTONE_PREFIX = "ms"
CAPTURE_PREFIX = "cap"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_WHITESPACE = re.compile(r"\s+")

#: What a slug falls back to when a name has no ASCII alphanumerics left in
#: it at all -- an emoji, or a name written entirely in a non-Latin script.
#: The id is still unique because `_mint` disambiguates; only readability is
#: lost, and a readable id was never the guarantee.
_UNSLUGGABLE = "x"


class EntityError(ValueError):
    """A name or an id that cannot mean what the caller wants it to mean."""


def normalise(name: str) -> str:
    """The lookup key for a display name. Never an id, never stored as one.

    Casefold rather than lower: `casefold` folds the pairs `lower` misses,
    and a board that ever carries a non-English project name should not
    split into two projects over it.
    """
    if not isinstance(name, str):
        raise EntityError(f"a name must be a string, not {type(name).__name__}")
    folded = _WHITESPACE.sub(" ", name).strip().casefold()
    if not folded:
        raise EntityError("a name cannot be blank")
    return folded


def new_registry() -> dict:
    """An empty registry. Plain JSON, so it round-trips through CouchDB.

    `captures` is a third map and it is not shaped like the other two: it
    holds one integer per board, the high-water mark of the capture numbers
    issued for it, and no entry per capture. See `mint_capture`.
    """
    return {"projects": {}, "milestones": {}, "captures": {}}


def _slug(name: str) -> str:
    slug = _SLUG_STRIP.sub("-", normalise(name)).strip("-")
    return slug or _UNSLUGGABLE


def _mint(prefix: str, name: str, taken) -> str:
    """A fresh id seeded from the name, disambiguated against `taken`.

    The suffix exists because a slug is not unique over time even though a
    live name is: rename `Nova` to `Aurora` and a new project may then
    legitimately be called `Nova`, wanting an id its predecessor still holds.
    """
    base = f"{prefix}_{_slug(name)}"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _names_of(entry: dict) -> list[str]:
    """Every normalised name that resolves to `entry` -- current plus aliases."""
    return [entry["key"], *entry.get("aliases", [])]


def resolve_project(registry: dict, name: str) -> str | None:
    """The id of the project called `name`, by current name or by alias."""
    key = normalise(name)
    for pid, entry in registry["projects"].items():
        if key in _names_of(entry):
            return pid
    return None


def ensure_project(registry: dict, name: str) -> str:
    """The id of the project called `name`, minting one if it is new.

    Mutates `registry` in place and is idempotent: calling it twice with two
    spellings of one name returns the same id both times and mints once.
    """
    existing = resolve_project(registry, name)
    if existing is not None:
        return existing
    pid = _mint(PROJECT_PREFIX, name, registry["projects"])
    registry["projects"][pid] = {
        "name": name.strip(),
        "key": normalise(name),
        "aliases": [],
    }
    return pid


def resolve_milestone(registry: dict, project_id: str, name: str) -> str | None:
    """The id of `name` **within** `project_id`, by current name or alias.

    Two projects may each have a `Backup` milestone and they are different
    milestones (`nova_boards._MILESTONE_HEADING`), so the project id is part
    of the question, not a filter applied afterwards.
    """
    key = normalise(name)
    for mid, entry in registry["milestones"].items():
        if entry["projectId"] == project_id and key in _names_of(entry):
            return mid
    return None


def ensure_milestone(registry: dict, project_id: str, name: str) -> str:
    """The id of milestone `name` under `project_id`, minting one if new.

    An unknown `project_id` is an error rather than a mint: a milestone with
    no project is exactly the orphan this module exists to prevent, and
    creating one here would hide the caller's bug inside the store.
    """
    if project_id not in registry["projects"]:
        raise EntityError(f"no such project: {project_id!r}")
    existing = resolve_milestone(registry, project_id, name)
    if existing is not None:
        return existing
    mid = _mint(MILESTONE_PREFIX, name, registry["milestones"])
    registry["milestones"][mid] = {
        "name": name.strip(),
        "key": normalise(name),
        "projectId": project_id,
        "aliases": [],
    }
    return mid


def _rename(entry: dict, new_name: str, clash: str | None, what: str) -> None:
    if clash is not None:
        raise EntityError(f"{what} {clash!r} already answers to {new_name!r}")
    old_key = entry["key"]
    new_key = normalise(new_name)
    entry["name"] = new_name.strip()
    entry["key"] = new_key
    aliases = [a for a in entry.get("aliases", []) if a != new_key]
    if old_key != new_key and old_key not in aliases:
        aliases.append(old_key)
    entry["aliases"] = aliases


def rename_project(registry: dict, project_id: str, new_name: str) -> None:
    """Rename a project, keeping its id and keeping the old name resolvable.

    The id does not move -- that is the whole point of the module -- and the
    previous name stays an alias so a board written before the rename still
    resolves. Renaming onto a name another project answers to is refused
    rather than merging the two.
    """
    entry = registry["projects"].get(project_id)
    if entry is None:
        raise EntityError(f"no such project: {project_id!r}")
    other = resolve_project(registry, new_name)
    _rename(entry, new_name, None if other in (None, project_id) else other, "project")


def rename_milestone(registry: dict, milestone_id: str, new_name: str) -> None:
    """Rename a milestone, keeping its id and its old name, within its project.

    The clash check is scoped to the same project for the same reason
    `resolve_milestone` is: a `Backup` under another project is not a clash.
    """
    entry = registry["milestones"].get(milestone_id)
    if entry is None:
        raise EntityError(f"no such milestone: {milestone_id!r}")
    other = resolve_milestone(registry, entry["projectId"], new_name)
    _rename(
        entry,
        new_name,
        None if other in (None, milestone_id) else other,
        "milestone",
    )


def capture_high_water(registry: dict, board: str) -> int:
    """The highest capture number ever issued for `board`; 0 if none.

    Read separately from `mint_capture` because a migration wants to know
    whether a board has ever minted a capture without minting one, and
    because a registry stored before captures existed has no `captures`
    key at all -- reading through this is what makes that a 0 rather than
    a `KeyError` at whichever call site happens to be first.
    """
    _check_capture_board(board)
    held = registry.get("captures", {})
    if not isinstance(held, dict):
        # Not `or {}`: a `captures` of `None` or of a list is corrupt, and
        # reading it as empty restarts the counter at 1, which is exactly
        # the reissue this whole function exists to make impossible.
        raise EntityError(f"registry 'captures' must be a dict, not {held!r}")
    n = held.get(board, 0)
    if not isinstance(n, int) or isinstance(n, bool) or n < 0:
        raise EntityError(
            f"capture high-water for {board!r} must be a non-negative int, not {n!r}")
    return n


def mint_capture(registry: dict, board: str) -> str:
    """A fresh capture id for `board`. Mutates `registry` in place.

    **This is the one id in the schema that is not seeded from a name, and
    the reason is written in `board_document.to_capture_document`: a capture
    is addressed by its own words today, and the owner edits those words.**
    A slug of the text would change under an edit, which is the orphaning
    `_mint` exists to prevent, so a capture gets a number instead.

    **The counter is a high-water mark, never a count of live captures.**
    Deleting a capture must not free its id. `nova_site` reads
    `captureReplies` as a list parallel to the captures and the owner's own
    Edit route addresses one bullet; hand `cap_7` to a second capture after
    the first is closed and an old reply lands under new words, silently,
    with nothing in the store that could tell them apart. So the number only
    ever goes up, and a board with three captures whose high-water reads 40
    is correct rather than drifted.

    Unlike `ensure_project` this is not idempotent and cannot be: there is
    no name to resolve against, so every call is a new capture. A caller
    re-running a migration mints a second set of ids -- which is why
    `board_migrate` refuses a board that already holds records without
    `--force`, and not something this function can defend against.
    """
    n = capture_high_water(registry, board) + 1
    registry.setdefault("captures", {})[board] = n
    return f"{CAPTURE_PREFIX}_{n}"


def _check_capture_board(board: str) -> None:
    """A capture counter is per board, so a typo would be a third board.

    Validated against `board_document.BOARDS` rather than against "is a
    non-empty string": a mistyped `issues` would mint from its own counter
    starting at 1 and hand out ids the real board has already issued, and
    the collision would only show up as a reply under the wrong bullet.
    The import is one-way -- `board_document` does not import this module
    -- and it is the same constant `capture_document_id` refuses on.
    """
    from . import board_document

    if board not in board_document.BOARDS:
        raise EntityError(
            f"board must be one of {board_document.BOARDS}, not {board!r}")
