"""Compares the parts of this repo's hand-synced twins that have to agree.

Two files here have a second hand-written copy in agora-claude-bridge, in a
different pod with no common dependency, deliberately not shared as a
package. Nothing detected drift between either pair until Cycle 168, and
Cycles 136-142, 148, 151 and 167 each wrote the same vault fix into both by
hand. A drift test has been in `nova/resources/ideas.md` since Cycle 137 and
this is it. `PAIRS` at the bottom of this file is the list; the CLI takes the
two repository roots and looks the paths up there, so adding the third pair
(the CI workflows) is a table entry rather than a new argument.

The two pairs are checked in completely different ways, because they are
different shapes of problem, and the second one is the cheap one:

  * the **vault clients** are stateful classes-or-modules configured out of
    the environment, so they get a syntax comparison of the names that must
    match plus a driven comparison of the routing decisions -- see below;
  * **redaction** is a pure function of one string, so it needs no
    configuration at all. `compare_redaction` puts one table of
    credential-shaped strings through both copies and compares the output.

`agora_runner/vault.py` here and `bridge/vault_tool.py` in
agora-claude-bridge are separate hand-written copies of one client, talking
to one CouchDB.

Most of the two files may differ and should: one is a library the runner
imports, the other is a CLI with a `VaultClient` class and argv parsing.
What may *not* differ is the handful of names below, because each one is a
question both processes ask of the same stored bytes:

  * the routing tuples decide which of two databases a path lives in, so a
    disagreement writes Edvard's file into Nova's store or the reverse;
    note the *inputs* only -- see the limit below;
  * the chunker decides how a document is cut into chunk ids, so a
    disagreement stops the two reusing each other's chunks and brings the
    write amplification straight back;
  * `_appended` decides where a capture lands relative to its marker, which
    is the failure that took Cycles 112-114 to repair;
  * the internal-prefix and id-range constants decide which docs count as
    files at all.

Comments and docstrings are stripped before comparing, because the two
copies explain themselves to different readers and always have. What is
compared is the value of each constant and the syntax tree of each function
body -- so a reworded comment is silence and a changed number is noise.

Syntax comparison alone cannot see any of that, because only module-level
names are compared. The bridge keeps `db_for`, `dbs_for_prefix`,
`assemble`, `_put_raw` and `database_health` as methods on `VaultClient`
while the runner has them as plain functions, so none of them can be named
in the tables below at all -- `extract_contract` would raise on every
bridge run. The consequence, reproduced by the second reader on the diff
that added this file rather than guessed: delete the `lowered in
NOVA_DB_FILES` branch of the bridge's `db_for` and the name comparison
still prints `16 names in sync`, while `journal-digest.md` stops resolving
to Nova's database on that copy. The routing *inputs* were pinned; the
routing *decision* was not.

So there is a second comparison, added in Cycle 169 and run by the same
command: `compare_routing` imports both files, configures each copy the way
its own process configures it, and puts one table of paths and prefixes
through both. It compares answers rather than syntax, so it does not care
that one is a method and the other a function -- which is the whole reason
it exists. It covers `db_for` and `dbs_for_prefix` under both database
configurations (routing on, and routing off, which is a separate early
return in both copies).

A third comparison, added in Cycle 173, covers chunk assembly:
`compare_assembly` drives each copy's assembler with its chunk fetcher
replaced by a recorder, so the answer to every question is the *database
each copy went looking for the chunks in*. That is the one decision in
assembly that can send an intact file's chunks to a store that does not
hold them, and an intact file that reports itself corrupt is
indistinguishable from a corrupt one. It is driven rather than read for
the same reason routing is: the runner has `vault_assemble` as a plain
function and the bridge has `assemble` as a method, so no name comparison
can pair them.

Unlike routing, this one also states what the answers *should* be, not
only that the two agree -- `_ASSEMBLY_EXPECTED` resolves per
configuration. Agreement alone is worthless here: both copies dropping the
`_SRC_DB_KEY` branch would agree on every row of the table, and that is
precisely the drift Cycle 169 found by hand.

A fourth, added in Cycle 174, covers writes: `compare_writes` drives each
copy's `_put_raw` with its whole CouchDB seam replaced by a fake that
answers from a script, so the answer to every question is the *sequence of
requests the copy made* plus the string it returned. Like assembly and
unlike routing, it states what the answers should be rather than only that
the two agree -- and here that is not a refinement but the entire point.
Every decision it asks about has one legitimate-looking alternative, so two
copies that drifted the same way agree on the whole table. Cycle 167 fixed
one of these decisions in both copies by hand in the same hour.

The one thing `compare_writes` deliberately does not compare is the wording
of `FAILED(unreadable: ...)`; see `_collapse_prose` for why that is a false
alarm rather than a check.

**What is still not covered**, stated here rather than left to be
rediscovered: `database_health` and the rest of the class are unpinned. The
write fake does not extend to it for free -- it is a third seam again,
since the runner hands `couch_req` a bare database name with no document
after it and the bridge reaches `_req` without going through
`VaultClient._doc` -- so it needs its own driver rather than another row.

And the disclosure the earlier version of this docstring carried, kept
because dropping it was a step down in candour on the very diff that
claimed to fix the divergence: the runner half of that `_SRC_DB_KEY` fix
is **not live-exploitable today and changes nothing for a running
process.** `vault_assemble`'s only production caller is
`vault_read_path_rev`, whose doc comes from `couch_get_doc`, which does not
stamp the key -- so the new branch falls through to the old behaviour on
every real read. It matters during a migration, when `_vault_file_docs`
stamps docs out of whichever database really held them, and it matters now
because the two copies agreeing is the property this file exists to keep.
Both are real reasons; neither is "a user saw this break".
"""
import ast
import contextlib
import copy
import importlib
import importlib.util
import json
import os
import sys
import time
import urllib.parse

# Names that must mean the same thing in both copies. Where the two spell a
# name differently, the alias is listed second; the comparison is by the
# canonical name on the left, so a rename in one repo is not reported as a
# value change.
CONTRACT_CONSTANTS = (
    ("NOVA_DB_FOLDERS", ()),
    ("NOVA_DB_FILES", ()),
    ("NOVA_DB_TARGETS", ()),
    ("HEALTH_PROBE_PATHS", ()),
    ("HEALTH_TIMEOUT_SECONDS", ()),
    ("INTERNAL_PREFIXES", ("_INTERNAL_PREFIXES",)),
    ("_ID_MAX", ()),
    ("_SRC_DB_KEY", ()),
    ("CHUNK_MIN_BYTES", ()),
    ("CHUNK_MAX_BYTES", ()),
    ("CHUNK_BOUNDARY_MASK", ()),
    ("APPEND_ATTEMPTS", ()),
)

CONTRACT_FUNCTIONS = (
    ("_is_chunk_boundary", ()),
    ("_bytes_prefix", ()),
    ("_split_chunks", ()),
    ("_appended", ()),
)


_UNEVALUATED = object()


class ContractNameMissing(LookupError):
    """A name the contract requires is not defined at module level.

    Its own error rather than a diff entry, because a name that vanished is
    not a value that changed: either the contract is out of date or the file
    handed in is not a vault client, and both need a human before any
    comparison of the remainder means anything.
    """


def _toplevel(tree):
    """Module-level assignments and functions, by name.

    Only module level. A same-named method inside `VaultClient` is a
    different thing from the module-level function the other copy defines,
    and silently pairing them would compare two unrelated bodies.
    """
    out = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
    return out


def _resolve(defined, name, aliases):
    for candidate in (name,) + tuple(aliases):
        if candidate in defined:
            return defined[candidate]
    return None


def _strip_docstring(body):
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        return body[1:]
    return body


def _normalise_value(node, defined, seen=()):
    """A constant's value as comparable text.

    `ast.literal_eval` first, so `(2048, 16384)` and a reflowed version of
    the same tuple are one string. Names are followed one level -- the two
    copies both define `NOVA_DB_TARGETS` as `NOVA_DB_FOLDERS + NOVA_DB_FILES`
    rather than as a literal, and comparing that expression textually would
    pass even if both operands had changed. `seen` stops a definition that
    refers to itself from recursing forever; it resolves to the raw tree
    instead, which compares fine and is the honest answer.
    """
    try:
        value = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        value = _UNEVALUATED
    if value is not _UNEVALUATED:
        # The type name is part of the answer, not decoration. JSON renders a
        # tuple and a list identically, and these tuples are handed straight
        # to `str.startswith`, which accepts a tuple and raises TypeError on a
        # list. One copy switching the brackets is a crash in the other pod
        # that a value-only comparison would call in sync.
        return "%s:%s" % (type(value).__name__,
                          json.dumps(value, sort_keys=True, default=str))
    if isinstance(node, ast.Name) and node.id in defined and node.id not in seen:
        return _normalise_value(defined[node.id], defined, seen + (node.id,))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return "%s + %s" % (_normalise_value(node.left, defined, seen),
                            _normalise_value(node.right, defined, seen))
    return ast.dump(node)


def _normalise_function(node, canonical):
    """A function body as comparable text: no docstring, no comments.

    Comments never reach the tree at all, which is the point -- the two
    copies carry different prose around identical code and always will. The
    name is rewritten to the canonical one so an alias is not a difference,
    but argument names are left alone: two copies of one function whose
    parameters disagree are a real finding, not a formatting one.
    """
    clone = ast.parse(ast.unparse(node)).body[0]
    clone.name = canonical
    clone.body = _strip_docstring(clone.body)
    if not clone.body:
        clone.body = [ast.Pass()]
    clone.decorator_list = []
    return ast.dump(ast.parse(ast.unparse(clone)))


def extract_contract(source):
    """The contract as `{name: normalised text}` for one vault client.

    Raises `ContractNameMissing` listing every absent name at once, rather
    than the first -- a contract that has fallen behind the code usually
    falls behind in more than one place, and reporting one name per run
    turns that into one cycle per name.
    """
    tree = ast.parse(source)
    defined = _toplevel(tree)
    contract = {}
    missing = []
    for name, aliases in CONTRACT_CONSTANTS:
        node = _resolve(defined, name, aliases)
        if node is None:
            missing.append(name)
        else:
            contract[name] = _normalise_value(node, defined)
    for name, aliases in CONTRACT_FUNCTIONS:
        node = _resolve(defined, name, aliases)
        if node is None or not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            missing.append(name)
        else:
            contract[name] = _normalise_function(node, name)
    if missing:
        raise ContractNameMissing(
            "not defined at module level: " + ", ".join(missing))
    return contract


def compare(left_source, right_source):
    """Names whose normalised form differs, ascending. Empty means in sync."""
    left = extract_contract(left_source)
    right = extract_contract(right_source)
    return sorted(n for n in left if left[n] != right[n])


# ---------------------------------------------------------------------------
# Behaviour: same question, both copies, compare the answers.
# ---------------------------------------------------------------------------

# The two database names handed to both copies. Deliberately NOT the live
# `obsidian`/`nova`: if a copy ever stopped taking its configuration where
# this tool supplies it -- reading the environment inside `db_for`, say --
# every answer would come back as a live name instead, and
# `_routing_answers` raises rather than reporting a clean run. An instrument
# that cannot fail is the one thing this file is not allowed to be.
PROBE_DB = "probe-edvard-db"
PROBE_NOVA_DB = "probe-nova-db"

# Both copies open with `if not <nova db>: return <db>`, so routing-off is a
# second, separately-written branch and gets its own pass.
ROUTING_CONFIGS = ((PROBE_DB, PROBE_NOVA_DB), (PROBE_DB, ""))

# Paths beyond the shared `HEALTH_PROBE_PATHS`, which is read off the runner
# copy at run time so this table cannot fall behind it. These are the edges
# that tuple does not carry: the empty and absent path both copies guard
# with `(path or "")`, the lowercasing every route depends on, and a folder
# name that is a prefix of Nova's folder without being inside it.
EXTRA_PROBE_PATHS = (
    "",
    None,
    "PROJECTS/Sokrates/Projects/Agora/NOVA/journal/191-cycle-169.md",
    "projects/sokrates/projects/agora/nova",
    "projects/sokrates/projects/agora/novaX/file.md",
    "projects/sokrates/projects/nova/notes.md",
    "unrelated/file.md",
)

# `dbs_for_prefix` has three branches -- inside Nova's folder, an ancestor
# of it, everything else -- and the ancestor one is why a whole-vault
# listing does not quietly lose every Nova file.
PROBE_PREFIXES = (
    "",
    None,
    "projects/",
    "projects/sokrates/projects/agora/",
    "projects/sokrates/projects/agora/nova/",
    "projects/sokrates/projects/agora/nova/journal/",
    "projects/sokrates/projects/agora/journal-digest.md",
    "projects/sokrates/projects/agora/journal-digest.md.bak",
    "projects/sokrates/projects/nova/",
    "unrelated/",
)


# The environment variables the two copies are configured through, and the
# module names this tool loads them under. Both have to be put back: run
# from the command line the process exits immediately and nothing notices,
# but the test suite calls `compare_routing` in the same interpreter as
# every other test, and `agora_runner.config` is a module of constants that
# a dozen of them import.
_PROBE_ENV = ("COUCHDB_DB", "COUCHDB_NOVA_DB",
              "CDB_BASE", "CDB_USER", "CDB_PASS", "CDB_DB", "CDB_NOVA_DB")
_PROBE_MODULES = ("_sync_contract_runner", "_sync_contract_bridge",
                  "_sync_contract_runner_redact", "_sync_contract_bridge_redact",
                  "_sync_contract_runner_assemble",
                  "_sync_contract_bridge_assemble",
                  "_sync_contract_runner_write", "_sync_contract_bridge_write")


@contextlib.contextmanager
def _process_state_restored():
    """Put the environment, `sys.modules` and `agora_runner.config` back.

    Driving two copies of a client means configuring them the way their own
    processes are configured, which is process-global state, and
    `agora_runner.config` reads it once at import -- so this reloads it,
    which replaces the values every other importer of that module sees.
    Measured before this existed: `config.COUCHDB_DB` was left reading
    `probe-edvard-db` for the remainder of the interpreter. Nothing failed,
    because no test that depends on it happened to run afterwards. That is
    the kind of green that stops being green when somebody adds a test in
    the wrong alphabetical position.
    """
    saved_env = {k: os.environ.get(k) for k in _PROBE_ENV}
    saved_modules = {k: sys.modules.get(k) for k in _PROBE_MODULES}
    try:
        yield
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        # After the environment is back, not before -- this re-reads it.
        config = sys.modules.get("agora_runner.config")
        if config is not None:
            importlib.reload(config)


class ContractRouterMissing(LookupError):
    """A copy does not expose routing this tool can drive.

    Same reasoning as `ContractNameMissing`: a router that cannot be found
    is not a routing difference, and reporting it as one would let a
    renamed function read as agreement.
    """


def _load_module(path, name):
    """The file at `path`, imported under `name`.

    The `spec is None` guard below does not cover a path that simply is not
    there: `spec_from_file_location` happily builds a spec for a file that
    does not exist, and the failure only surfaces from `exec_module` as a
    bare `FileNotFoundError`. So a repo that moved `redact.py` produced a
    Python traceback in the CI log rather than the exit code 2 this tool
    documents. Second reader on #154; the author had hit the same traceback
    by hand ten minutes earlier and read past it.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ContractRouterMissing("cannot import %s" % (path,))
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so a copy that imports itself by name resolves,
    # and so `importlib.reload` has something to work with.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except OSError as exc:
        sys.modules.pop(name, None)
        raise ContractRouterMissing("cannot read %s: %s" % (path, exc)) from exc
    return module


def _runner_router(path, db, nova_db):
    """The runner copy, configured the way its own process configures it.

    `agora_runner/vault.py` does `from agora_runner.config import COUCHDB_DB,
    COUCHDB_NOVA_DB`, which copies the values at import, and config reads the
    environment at *its* import. So both have to be re-executed, in that
    order, or the second configuration below silently reuses the first.
    """
    os.environ["COUCHDB_DB"] = db
    os.environ["COUCHDB_NOVA_DB"] = nova_db
    importlib.reload(importlib.import_module("agora_runner.config"))
    module = _load_module(path, "_sync_contract_runner")
    try:
        return module.db_for, module.dbs_for_prefix
    except AttributeError as exc:
        raise ContractRouterMissing(
            "%s: no module-level %s" % (path, exc.name)) from exc


def _bridge_router(path, db, nova_db):
    """The bridge copy, likewise: `VaultClient.__init__` reads `CDB_*`.

    The real constructor rather than a hand-built instance, because which
    env var each copy reads is part of what may drift. The three credential
    vars are required by `_env` and unused by routing; nothing here opens a
    socket.
    """
    os.environ.update({
        "CDB_BASE": "http://vault-contract.invalid",
        "CDB_USER": "probe",
        "CDB_PASS": "probe",
        "CDB_DB": db,
        "CDB_NOVA_DB": nova_db,
    })
    module = _load_module(path, "_sync_contract_bridge")
    client_class = getattr(module, "VaultClient", None)
    if client_class is None:
        raise ContractRouterMissing("%s: no VaultClient" % (path,))
    client = client_class()
    try:
        return client.db_for, client.dbs_for_prefix
    except AttributeError as exc:
        raise ContractRouterMissing(
            "%s: VaultClient has no %s" % (path, exc.name)) from exc


def _routing_answers(db_for_fn, dbs_for_prefix_fn, paths):
    """`{question: answer}` for one copy under one configuration."""
    answers = {}
    for path in paths:
        answers["db_for(%r)" % (path,)] = db_for_fn(path)
    for prefix in PROBE_PREFIXES:
        answers["dbs_for_prefix(%r)" % (prefix,)] = list(dbs_for_prefix_fn(prefix))
    return answers


def _check_the_probe_reached_both(agreed, db, nova_db):
    """Raise if the two copies agreed on a database this tool never supplied.

    Agreement is the only answer this comparison can get wrong by luck. If
    both copies say `obsidian` no matter what they are configured with --
    because one reads the environment at call time, or both were handed a
    default -- then every question matches and the run reports a clean
    comparison it never actually made. A *difference* needs no such guard:
    it is reported as drift above whatever the names are.

    So this states what it observed and not why. A stray name has more than
    one cause and this tool cannot tell them apart from here.
    """
    allowed = {db, nova_db} - {""}
    for question, answer in sorted(agreed.items()):
        named = [answer] if isinstance(answer, str) else answer
        stray = [n for n in named if n not in allowed]
        if stray:
            raise ContractRouterMissing(
                "both copies answered %s with %r, which is not a database "
                "this comparison configured (%s). Two copies agreeing on a "
                "name neither was given is not evidence that they agree -- "
                "refusing to report a comparison that may not have reached "
                "either of them" % (question, stray, sorted(allowed)))


def compare_routing(runner_path, bridge_path):
    """Routing questions the two copies answer differently, ascending.

    Empty means every path and prefix in the tables above resolved to the
    same database in both, under both configurations. This is what the name
    comparison structurally cannot do: routing lives in a method on one side
    and a plain function on the other, so no AST comparison can pair them.
    """
    drifted = []
    agreed = {}
    with _process_state_restored():
        for db, nova_db in ROUTING_CONFIGS:
            runner = _runner_router(runner_path, db, nova_db)
            bridge = _bridge_router(bridge_path, db, nova_db)
            paths = tuple(sys.modules["_sync_contract_runner"].HEALTH_PROBE_PATHS)
            paths += EXTRA_PROBE_PATHS
            left = _routing_answers(*runner, paths)
            right = _routing_answers(*bridge, paths)
            label = "routing on" if nova_db else "routing off"
            drifted += [
                ("%s: %s" % (label, q), left[q], right[q])
                for q in sorted(left) if left[q] != right[q]
            ]
            agreed[(db, nova_db)] = {
                q: left[q] for q in left if left[q] == right[q]}
    # After every configuration, and only when nothing drifted. The guard
    # exists to stop *agreement* being reported as a clean comparison, so a
    # run that already found real drift has nothing left for it to protect
    # -- and raising here would replace a named routing bug with an
    # instrumentation error, which is a finding masked by its own safety
    # net. Second reader on #153 caught that; it used to raise inside the
    # loop, so drift found under one configuration was discarded by a guard
    # tripping under the next.
    if not drifted:
        for (db, nova_db), answers in agreed.items():
            _check_the_probe_reached_both(answers, db, nova_db)
    return drifted


# ---------------------------------------------------------------------------
# Chunk assembly: which database a document's content chunks are read from.
# ---------------------------------------------------------------------------


class ContractAssemblerMissing(LookupError):
    """A copy has no assembler this tool can drive, or no chunk fetcher.

    Its own error for the reason `ContractRouterMissing` is: a copy that
    cannot be driven has not been compared, and reporting that as agreement
    is the failure this whole file exists to prevent.
    """


# Chunk ids are content hashes with no path in them, which is exactly why
# `db_for` can never route one and the fetcher takes an explicit database.
_PROBE_CHUNKS = ("h:probe-chunk-a", "h:probe-chunk-b")

# Which database each row must resolve to, as a token rather than a name,
# because two of the four answers depend on how routing is configured.
#
#   stamp    -- the database the doc says it was read from
#   explicit -- the `db=` argument the caller passed
#   nova     -- whatever `db_for` calls Nova's database in this configuration
#   edvard   -- likewise, Edvard's
_ASSEMBLY_EXPECTED = {
    "stamp": lambda db, nova_db: PROBE_NOVA_DB,
    "explicit": lambda db, nova_db: PROBE_DB,
    "nova": lambda db, nova_db: nova_db or db,
    "edvard": lambda db, nova_db: db,
}


def _assembly_questions(src_db_key):
    """`(label, doc, path argument, db argument, expected)`, one per row.

    Built against the copy's *own* `_SRC_DB_KEY` rather than a literal, so
    this table measures where assembly looks and not whether the constant
    still spells itself the same way. That second question is already
    `CONTRACT_CONSTANTS`' job, and asking it twice in two places is how the
    two answers start disagreeing.
    """
    stamped = {"children": list(_PROBE_CHUNKS), src_db_key: PROBE_NOVA_DB}
    return (
        # The row Cycle 169 fixed by hand in the runner: the stamp wins over
        # a path that routes the other way. Recomputing the route here is
        # what makes an intact file report itself corrupt mid-migration.
        ("stamped doc, path routes the other way",
         dict(stamped), "unrelated/file.md", None, "stamp"),
        ("stamped doc, no path anywhere",
         dict(stamped), None, None, "stamp"),
        # An explicit argument is the caller saying it already knows; it has
        # to beat the stamp, or `vault_bulk_fetch` loses the database it
        # just read the doc out of.
        #
        # The path here routes to Nova's database *and* the stamp names it,
        # so `explicit` is the only one of the three that answers Edvard's.
        # It read `unrelated/file.md` for one commit, which routes to the
        # same database the explicit argument names -- a copy that ignored
        # the argument entirely and fell through to the path scored the row
        # anyway. Other rows still caught every reordering the second reader
        # on #156 could build, but a row that needs another row to mean
        # anything is not the row it says it is.
        ("explicit db beats the stamp",
         dict(stamped),
         "projects/sokrates/projects/agora/nova/resources/inbox.md",
         PROBE_DB, "explicit"),
        ("unstamped, path argument inside Nova's folder",
         {"children": list(_PROBE_CHUNKS)},
         "projects/sokrates/projects/agora/nova/journal/191-cycle-169.md",
         None, "nova"),
        ("unstamped, path argument outside it",
         {"children": list(_PROBE_CHUNKS)}, "unrelated/file.md", None,
         "edvard"),
        # The two fallbacks after the path argument, in the order both
        # copies try them.
        ("unstamped, path off the doc's own `path`",
         {"children": list(_PROBE_CHUNKS),
          "path": "projects/sokrates/projects/agora/nova/resources/inbox.md"},
         None, None, "nova"),
        ("unstamped, path off the doc's `_id`",
         {"children": list(_PROBE_CHUNKS),
          "_id": "projects/sokrates/projects/agora/journal-digest.md"},
         None, None, "nova"),
    )


def _chunk_recorder(asked):
    """A stand-in fetcher that records its database and answers every id.

    Every id, because a missing chunk raises `VaultIncompleteDocument` in
    both copies before they return -- and an exception would lose the one
    thing this probe came for.
    """
    def fetch(chunk_ids, db):
        asked.append(db)
        return {chunk_id: "" for chunk_id in chunk_ids}
    return fetch


def _runner_assembler(path, db, nova_db):
    """`(ask, src_db_key)` for the runner copy, configured as its own process.

    Same import dance as `_runner_router` and for the same reason: both
    `agora_runner.config` and the module reading it copy their values at
    import time.
    """
    os.environ["COUCHDB_DB"] = db
    os.environ["COUCHDB_NOVA_DB"] = nova_db
    importlib.reload(importlib.import_module("agora_runner.config"))
    module = _load_module(path, "_sync_contract_runner_assemble")
    for name in ("vault_assemble", "_fetch_chunks", "_SRC_DB_KEY"):
        if not hasattr(module, name):
            raise ContractAssemblerMissing(
                "%s: no module-level %s" % (path, name))
    asked = []
    module._fetch_chunks = _chunk_recorder(asked)

    def ask(doc, path_arg, db_arg):
        del asked[:]
        module.vault_assemble(doc, path_arg, db_arg)
        return list(asked)

    return ask, module._SRC_DB_KEY


def _bridge_assembler(path, db, nova_db):
    """`(ask, src_db_key)` for the bridge copy.

    The recorder goes on the *instance*, which shadows the bound method
    without touching the class -- so a second `VaultClient` built later in
    the same interpreter is the real one.
    """
    os.environ.update({
        "CDB_BASE": "http://vault-contract.invalid",
        "CDB_USER": "probe",
        "CDB_PASS": "probe",
        "CDB_DB": db,
        "CDB_NOVA_DB": nova_db,
    })
    module = _load_module(path, "_sync_contract_bridge_assemble")
    client_class = getattr(module, "VaultClient", None)
    if client_class is None:
        raise ContractAssemblerMissing("%s: no VaultClient" % (path,))
    if not hasattr(module, "_SRC_DB_KEY"):
        raise ContractAssemblerMissing(
            "%s: no module-level _SRC_DB_KEY" % (path,))
    client = client_class()
    for name in ("assemble", "_fetch_chunks"):
        if not hasattr(client, name):
            raise ContractAssemblerMissing(
                "%s: VaultClient has no %s" % (path, name))
    asked = []
    client._fetch_chunks = _chunk_recorder(asked)

    def ask(doc, path_arg, db_arg):
        del asked[:]
        client.assemble(doc, path_arg, db_arg)
        return list(asked)

    return ask, module._SRC_DB_KEY


def _assembly_answers(ask, src_db_key):
    """`{question: [databases asked, in order]}` for one copy.

    The whole list rather than one name: a copy that asked twice, or asked
    nobody, is a different bug from a copy that asked the wrong database,
    and collapsing them would hide both behind the same answer.
    """
    answers = {}
    for label, doc, path_arg, db_arg, _ in _assembly_questions(src_db_key):
        answers[label] = ask(doc, path_arg, db_arg)
    return answers


def _check_the_assembly_probe_bites(agreed, src_db_key, db, nova_db):
    """Raise if the two copies agreed on an answer that is simply wrong.

    Routing gets away with checking only that the two agree, because a
    routing answer this tool never configured is visibly stray. Assembly
    cannot: every database in play here is one of the two, so two copies
    that both stopped honouring `_SRC_DB_KEY` answer this table identically
    and land on real, configured, wrong names. That is not a hypothetical --
    it is the state the runner was in until Cycle 169, and the bridge was
    the only reason anyone noticed.

    So this states the expected answer per row. It runs only on rows the
    two copies agreed about, for the reason `compare_routing` gives: a
    named difference must not be replaced by an instrumentation error.
    """
    for label, _, _, _, token in _assembly_questions(src_db_key):
        if label not in agreed:
            continue
        want = [_ASSEMBLY_EXPECTED[token](db, nova_db)]
        if agreed[label] != want:
            raise ContractAssemblerMissing(
                "both copies read the chunks for %r from %r, expected %r. "
                "Two copies that drifted the same way agree on every row of "
                "this table, so agreement is not evidence here -- refusing "
                "to report a comparison whose answers are wrong in both"
                % (label, agreed[label], want))


def compare_assembly(runner_path, bridge_path):
    """Assembly questions the two copies answer differently, ascending.

    Empty means both copies went looking for a document's chunks in the
    same database on every row, under both configurations, *and* that
    database was the right one.
    """
    drifted = []
    agreed = {}
    with _process_state_restored():
        for db, nova_db in ROUTING_CONFIGS:
            runner_ask, runner_key = _runner_assembler(runner_path, db, nova_db)
            bridge_ask, bridge_key = _bridge_assembler(bridge_path, db, nova_db)
            left = _assembly_answers(runner_ask, runner_key)
            right = _assembly_answers(bridge_ask, bridge_key)
            label = "assembly, routing on" if nova_db else "assembly, routing off"
            drifted += [
                ("%s: %s" % (label, q), left[q], right[q])
                for q in sorted(left) if left[q] != right[q]
            ]
            agreed[(db, nova_db, runner_key)] = {
                q: left[q] for q in left if left[q] == right[q]}
    if not drifted:
        for (db, nova_db, key), answers in agreed.items():
            _check_the_assembly_probe_bites(answers, key, db, nova_db)
    return drifted


# ---------------------------------------------------------------------------
# Writes: the last unpinned half of the vault pair, and the first probe that
# needs a fake rather than a recorder.
# ---------------------------------------------------------------------------
#
# Routing and assembly could be driven by replacing one collaborator with
# something that writes down its argument. A write cannot: `_put_raw` asks
# CouchDB three separate questions and every decision it makes is a reaction
# to an answer. So this replaces the whole seam -- `couch_req` in the runner,
# `VaultClient._doc` in the bridge -- with a fake that answers from a script
# and records what it was asked, and the answer to each question is the
# *sequence of requests the copy made plus the string it returned*.
#
# This is the second of the two probe shapes, and unmistakably so. Every
# decision below has exactly one legitimate alternative -- carry `ctime`
# forward or stamp it now, honour `if_rev` or the lookup, treat a chunk 409
# as success or as failure -- and both alternatives are real, configured,
# plausible answers. Two copies that drifted the same way agree on the whole
# table. That is not hypothetical either: Cycle 167 fixed exactly one of
# these decisions (the lookup that carries `ctime`) in both copies by hand in
# the same hour, and a check that only asked whether they agreed would have
# been green the whole time it was broken. So `_WRITE_QUESTIONS` states the
# expected request sequence for every row, and `_check_the_write_probe_bites`
# holds both copies to it.

# The write probe's clock. `ctime` and `mtime` are `int(time.time() * 1000)`
# in both copies, so without freezing this the two copies disagree by
# whatever the wall clock did between them -- and, worse, the one decision
# this probe exists to pin (an existing document's `ctime` survives the
# overwrite) is invisible when "now" and "then" are both just numbers.
_WRITE_NOW = 1755100000.0
_WRITE_NOW_MS = int(_WRITE_NOW * 1000)

# Deliberately far in the past and nowhere near `_WRITE_NOW`, so a row that
# expects the old value cannot pass by coincidence.
_WRITE_OLD_CTIME = 1600000000000
_WRITE_OLD_REV = "3-alreadythere"
_WRITE_CALLER_REV = "9-whatthecallerread"

# One chunk: `CHUNK_MIN_BYTES` is 2048 in both copies and this is far under
# it, which keeps every expected request sequence readable. Chunking itself
# is `_split_chunks`, already pinned by the syntax comparison.
_WRITE_CONTENT = "### 2026-01-01 00:00 (Oslo) -- probe\n\nPR: none | Outcome: probe\n"
_WRITE_SIZE = len(_WRITE_CONTENT.encode("utf-8"))

# Inside Nova's folder, so both copies must resolve every request in the row
# to Nova's database -- including the chunk PUT, which takes an explicit
# database rather than a route. A chunk written to the other store is a file
# doc pointing at chunks nobody can fetch, which reads as corruption.
_WRITE_PATH = "projects/sokrates/projects/agora/nova/journal/900-cycle-900.md"
_WRITE_MIXED_CASE_PATH = (
    "Projects/Sokrates/Projects/Agora/Nova/Journal/900-Cycle-900.md")

# Tokens the normaliser substitutes in, so the expected sequences below are
# literals a reader can check rather than values computed twice.
_CHUNK = "<chunk>"
_NOVA = "<nova db>"

# `if_rev` is a three-way argument whose "unconditional" value is a private
# module-level sentinel, so the table names it and each copy's own object is
# looked up when the row runs.
_WRITE_UNCONDITIONAL = "<unconditional>"


class ContractWriterMissing(LookupError):
    """A copy does not expose a write this tool can drive.

    Same reasoning as `ContractRouterMissing`, and the same consequence: a
    copy that could not be driven has not been compared, and a comparison
    that silently covered one copy is worse than no comparison.
    """


def _write_doc(_rev=None, ctime=_WRITE_NOW_MS, path=_WRITE_PATH):
    """The file document a healthy write PUTs, as both copies build it."""
    doc = {
        "_id": path, "path": path, "data": "", "children": [_CHUNK],
        "size": _WRITE_SIZE, "ctime": ctime, "mtime": _WRITE_NOW_MS,
        "type": "plain", "eden": {},
    }
    if _rev is not None:
        doc["_rev"] = _rev
    return doc


def _write_chunk_doc():
    return {"_id": _CHUNK, "data": _WRITE_CONTENT, "type": "leaf",
            "children": []}


def _lookup(path=_WRITE_PATH):
    return ("GET", _NOVA, path, None)


def _chunk_scan():
    return ("POST", _NOVA, "_all_docs", {"keys": [_CHUNK]})


def _chunk_write():
    return ("PUT", _NOVA, _CHUNK, _write_chunk_doc())


# `(status, body)` for each of the four things the fake is asked. `_ALL_DOCS`
# is a token rather than a body because the answer has to echo the chunk ids
# it was handed, and those are content hashes this table does not know.
_ABSENT = (404, {"error": "not_found"})
_PRESENT = (200, {"_id": _WRITE_PATH, "_rev": _WRITE_OLD_REV,
                  "ctime": _WRITE_OLD_CTIME, "mtime": _WRITE_OLD_CTIME,
                  "children": [], "type": "plain"})
_OK = (201, {"ok": True})

_WRITE_SCRIPT_DEFAULTS = {
    "lookup": _ABSENT, "chunks": "none", "chunk_put": _OK, "doc_put": _OK,
}


def _script(**overrides):
    out = dict(_WRITE_SCRIPT_DEFAULTS)
    out.update(overrides)
    return out


# `(label, path, if_rev, script, expected return, expected requests)`.
#
# Every row is a decision with a documented failure behind it, and the
# expected column is what makes agreement mean something. Read it as: this is
# what a correct client does, not merely what both copies happen to do.
_WRITE_QUESTIONS = (
    # The baseline. Order matters as much as content: the existence scan
    # comes before the chunk PUT, or the dedup that stops an append leaving
    # a second copy of the file behind is not happening.
    ("new file, unconditional",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(),
     "written",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH, _write_doc()))),

    # Cycle 167's bug, and the one row that would have been green under an
    # agreement-only check while both copies were wrong. Losing this makes
    # every overwritten file claim it was created today, silently, on a write
    # that succeeds.
    ("overwrite carries the old ctime forward and adopts its revision",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(lookup=_PRESENT),
     "written",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH,
       _write_doc(_rev=_WRITE_OLD_REV, ctime=_WRITE_OLD_CTIME)))),

    # The conditional-write contract (bridge#48): what the caller read beats
    # what the pre-write lookup found. A copy that let the lookup win is the
    # silent clobber `if_rev` was added to stop -- and it still returns
    # "written", so nothing anywhere reports it.
    ("if_rev beats the lookup, and ctime still survives",
     _WRITE_PATH, _WRITE_CALLER_REV, _script(lookup=_PRESENT),
     "written",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH,
       _write_doc(_rev=_WRITE_CALLER_REV, ctime=_WRITE_OLD_CTIME)))),

    # `if_rev=None` means "there should be nothing here". The document must
    # go out with no `_rev` at all, which is how CouchDB is asked to refuse.
    # Sending the found revision instead turns "create if absent" into
    # "overwrite whatever is there", which is the loser of a two-cycle race
    # having its entry disappear.
    ("if_rev=None sends no revision even when one was found",
     _WRITE_PATH, None, _script(lookup=_PRESENT),
     "written",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH, _write_doc(ctime=_WRITE_OLD_CTIME)))),

    # A database that will not answer must not be read as an empty slot.
    # Nothing may be written at all -- not even a chunk, which is why the
    # expected sequence is one request long.
    ("a lookup that fails writes nothing",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(lookup=(500, {})),
     "FAILED(unreadable: <error>)",
     (_lookup(),)),

    # The dedup. A chunk already in the store holds exactly this text,
    # because the id is the hash of the text -- rewriting it is the write
    # amplification chunking exists to remove.
    ("a chunk that already exists is not rewritten",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(chunks="all"),
     "written",
     (_lookup(), _chunk_scan(), ("PUT", _NOVA, _WRITE_PATH, _write_doc()))),

    # A 409 on a content-addressed id means somebody else stored this exact
    # text between the scan and the PUT. Treating it as failure aborts a
    # perfectly good write, and does so most often on the common path.
    ("a 409 on a chunk is success",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(chunk_put=(409, {})),
     "written",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH, _write_doc()))),

    # The opposite, and the more dangerous direction: a chunk that genuinely
    # failed must stop the file document. A file doc pointing at a chunk that
    # is not there is `VaultIncompleteDocument`, which is silent until
    # somebody reads the file.
    ("a chunk that fails stops the file document",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(chunk_put=(500, {})),
     "FAILED(chunk <chunk>: 500)",
     (_lookup(), _chunk_scan(), _chunk_write())),

    # 409 is the only status where retrying is right rather than a spin, so
    # it is named and not just numbered. `vault_tool.py` turns this string
    # into exit code 3, which is what `--if-rev-file` callers branch on.
    ("a 409 on the file document is named, not numbered",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(doc_put=(409, {})),
     "FAILED(409 conflict: %s changed since it was read)" % _WRITE_PATH,
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH, _write_doc()))),

    ("any other failure on the file document is reported as itself",
     _WRITE_PATH, _WRITE_UNCONDITIONAL, _script(doc_put=(503, {})),
     "FAILED(503)",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH, _write_doc()))),

    # Every id in this vault is lowercase, because LiveSync wrote most of
    # them. A copy that stored the caller's casing creates a second document
    # for a file that already exists, and the two clients then disagree about
    # which one is the file.
    ("the path is lowered before anything is looked up or stored",
     _WRITE_MIXED_CASE_PATH, _WRITE_UNCONDITIONAL, _script(),
     "written",
     (_lookup(), _chunk_scan(), _chunk_write(),
      ("PUT", _NOVA, _WRITE_PATH, _write_doc()))),
)


class _FakeCouch:
    """Answers a `_put_raw` from a script and records what it was asked.

    The two copies reach this through different seams -- a module-level
    function taking `"<db>/<quoted id>"` and a method taking them apart --
    so both are normalised to `(method, db, doc id, body)` before anything is
    recorded. What that normalisation drops is the URL quoting, which is
    `urllib.parse.quote(..., safe="")` in both and is not what this probe is
    about.
    """

    def __init__(self, file_doc_id, script):
        self.file_doc_id = file_doc_id
        self.script = script
        self.log = []

    def request(self, method, db, doc_id, body):
        # Deep-copied because both copies build the document once and hand
        # the same object down; recording the reference would let a later
        # mutation rewrite history.
        self.log.append((method, db, doc_id, copy.deepcopy(body)))
        if method == "GET":
            return self.script["lookup"]
        if doc_id == "_all_docs":
            keys = list((body or {}).get("keys", ()))
            if self.script["chunks"] == "all":
                return (200, {"rows": [{"key": k, "value": {"rev": "1-x"}}
                                       for k in keys]})
            return (200, {"rows": [{"key": k, "error": "not_found"}
                                   for k in keys]})
        if doc_id == self.file_doc_id:
            return self.script["doc_put"]
        return self.script["chunk_put"]


@contextlib.contextmanager
def _write_clock_frozen():
    """`time.time` pinned to `_WRITE_NOW` for the duration.

    Process-global and restored in a `finally`, for the reason
    `_process_state_restored` gives about `agora_runner.config`: this tool
    runs inside the runner's own test suite, and a clock left frozen would
    be a failure with no connection to the test that caused it.
    """
    saved = time.time
    time.time = lambda: _WRITE_NOW
    try:
        yield
    finally:
        time.time = saved


def _runner_writer(path, db, nova_db):
    """`(ask, chunk_id)` for the runner copy, configured as its own process."""
    os.environ["COUCHDB_DB"] = db
    os.environ["COUCHDB_NOVA_DB"] = nova_db
    importlib.reload(importlib.import_module("agora_runner.config"))
    module = _load_module(path, "_sync_contract_runner_write")
    for name in ("_vault_put_raw", "couch_req", "_chunk_id_for", "_ANY_REV"):
        if not hasattr(module, name):
            raise ContractWriterMissing("%s: no module-level %s" % (path, name))

    def ask(probe_path, if_rev, script):
        fake = _FakeCouch(probe_path.lower(), script)

        def couch_req(method, req_path, body=None, timeout=60):
            # `"<db>/<quoted doc id>"`. The id is a vault path full of
            # slashes, but `quote(safe="")` escapes every one of them, so the
            # first slash is always the database boundary.
            req_db, _, quoted = req_path.partition("/")
            return fake.request(method, req_db,
                                urllib.parse.unquote(quoted), body)

        module.couch_req = couch_req
        rev = module._ANY_REV if if_rev == _WRITE_UNCONDITIONAL else if_rev
        result = module._vault_put_raw(probe_path, _WRITE_CONTENT, if_rev=rev)
        return result, tuple(fake.log)

    return ask, module._chunk_id_for(_WRITE_CONTENT.encode("utf-8"))


def _bridge_writer(path, db, nova_db):
    """`(ask, chunk_id)` for the bridge copy.

    The fake goes on the *instance*, shadowing the bound method without
    touching the class, exactly as `_bridge_assembler` does -- so a second
    `VaultClient` built later in the same interpreter is the real one.
    """
    os.environ.update({
        "CDB_BASE": "http://vault-contract.invalid",
        "CDB_USER": "probe",
        "CDB_PASS": "probe",
        "CDB_DB": db,
        "CDB_NOVA_DB": nova_db,
    })
    module = _load_module(path, "_sync_contract_bridge_write")
    client_class = getattr(module, "VaultClient", None)
    if client_class is None:
        raise ContractWriterMissing("%s: no VaultClient" % (path,))
    if not hasattr(module, "_ANY_REV"):
        raise ContractWriterMissing("%s: no module-level _ANY_REV" % (path,))
    client = client_class()
    for name in ("_put_raw", "_doc", "_chunk_id_for"):
        if not hasattr(client, name):
            raise ContractWriterMissing(
                "%s: VaultClient has no %s" % (path, name))

    def ask(probe_path, if_rev, script):
        fake = _FakeCouch(probe_path.lower(), script)
        client._doc = lambda method, doc_id, body=None, db=None: fake.request(
            method, db or client.db_for(doc_id), doc_id, body)
        rev = module._ANY_REV if if_rev == _WRITE_UNCONDITIONAL else if_rev
        result = client._put_raw(probe_path, _WRITE_CONTENT, if_rev=rev)
        return result, tuple(fake.log)

    return ask, client._chunk_id_for(_WRITE_CONTENT.encode("utf-8"))


def _collapse_prose(result):
    """The one part of a write's answer the two copies may word differently.

    `FAILED(unreadable: ...)` carries whatever `VaultUnreadableDocument` was
    raised with, and that sentence is an explanation aimed at whoever reads
    the log -- the same category as the comments and docstrings the syntax
    comparison strips, and for the same reason. The two copies happen to word
    it identically today, by hand, with nothing holding them there, so
    comparing it would report the first reworded exception as drift.

    What is not collapsed is everything the contract is actually made of: the
    `FAILED(` prefix every caller branches on, the `unreadable` reason that
    tells it apart from a conflict, and -- the part that does the real work
    on this row -- the requests that were and were not made.
    """
    if result.startswith("FAILED(unreadable:"):
        return "FAILED(unreadable: <error>)"
    return result


def _write_answers(ask):
    """`{question: (returned string, requests made)}` for one copy.

    The chunk id and the database name are left raw, because this is what
    the drift comparison runs on: two copies that hash the same bytes to
    different ids, or send one request to the wrong store, differ here and
    are meant to. What is *not* left raw is `_collapse_prose`'s one string --
    see there for why comparing it would be a false alarm rather than a
    check.
    """
    answers = {}
    for label, probe_path, if_rev, script, _, _ in _WRITE_QUESTIONS:
        result, log = ask(probe_path, if_rev, script)
        answers[label] = (_collapse_prose(result), log)
    return answers


def _normalised(answer, chunk_id, nova_db):
    """One raw answer with this copy's chunk id and database name tokenised.

    Only for the correctness check. The tokens are what let
    `_WRITE_QUESTIONS` state an expected request sequence as a literal
    instead of recomputing one -- and a recomputed expectation is how a table
    ends up agreeing with the bug it was meant to catch.
    """
    result, log = answer

    def token(value):
        if value == chunk_id:
            return _CHUNK
        if value == nova_db:
            return _NOVA
        return value

    def body(value):
        if isinstance(value, dict):
            return {k: body(v) for k, v in value.items()}
        if isinstance(value, list):
            return [body(v) for v in value]
        return token(value)

    # Already through `_collapse_prose` -- the answers this is handed are the
    # ones the drift comparison ran on, so collapsing again here would be a
    # second copy of that decision to keep in sync.
    result = result.replace(chunk_id, _CHUNK)
    return result, tuple(
        (method, token(req_db), token(doc_id), body(req_body))
        for method, req_db, doc_id, req_body in log
    )


def _check_the_write_probe_bites(agreed, chunk_id, nova_db):
    """Raise if the two copies agreed on a write that is simply wrong.

    Every decision in `_WRITE_QUESTIONS` has a legitimate-looking
    alternative, so agreement alone is free here in a way it is not for
    routing. Runs only on rows the two copies agreed about, for the reason
    `compare_routing` gives: a named difference must not be replaced by an
    instrumentation error.
    """
    for label, _, _, _, want_result, want_log in _WRITE_QUESTIONS:
        if label not in agreed:
            continue
        got_result, got_log = _normalised(agreed[label], chunk_id, nova_db)
        if (got_result, got_log) != (want_result, tuple(want_log)):
            raise ContractWriterMissing(
                "both copies answered %r with %r making %r, expected %r "
                "making %r. Two copies that drifted the same way agree on "
                "every row of this table, so agreement is not evidence here "
                "-- refusing to report a comparison whose answers are wrong "
                "in both"
                % (label, got_result, got_log, want_result, tuple(want_log)))


def compare_writes(runner_path, bridge_path):
    """Write questions the two copies answer differently, ascending.

    Empty means both copies made the same sequence of CouchDB requests and
    returned the same string on every row, under both database
    configurations, *and* that sequence was the right one.
    """
    drifted = []
    agreed = {}
    with _process_state_restored(), _write_clock_frozen():
        for db, nova_db in ROUTING_CONFIGS:
            runner_ask, runner_chunk = _runner_writer(runner_path, db, nova_db)
            bridge_ask, bridge_chunk = _bridge_writer(bridge_path, db, nova_db)
            left = _write_answers(runner_ask)
            right = _write_answers(bridge_ask)
            label = "writes, routing on" if nova_db else "writes, routing off"
            drifted += [
                ("%s: %s" % (label, q), left[q], right[q])
                for q in sorted(left) if left[q] != right[q]
            ]
            if runner_chunk != bridge_chunk:
                drifted.append(
                    ("%s: chunk id for identical bytes" % label,
                     runner_chunk, bridge_chunk))
            agreed[(db, nova_db, runner_chunk)] = {
                q: left[q] for q in left if left[q] == right[q]}
    if not drifted:
        for (db, nova_db, chunk_id), answers in agreed.items():
            _check_the_write_probe_bites(answers, chunk_id, nova_db or db)
    return drifted


_WRITE_ADVICE = (
    "\nThe two vault clients no longer write the same way. Every row of this\n"
    "table is a decision that succeeds either way and reports nothing when\n"
    "it is wrong -- a creation date silently reset, a conditional write that\n"
    "quietly overwrote the winner, a file document pointing at a chunk that\n"
    "was never stored. Write the change into both copies, or drop the row\n"
    "from _WRITE_QUESTIONS in tools/sync_contract.py and say in the journal\n"
    "why the two clients are allowed to write differently."
)


# ---------------------------------------------------------------------------
# Redaction: the second hand-synced pair, and a pure function of its input.
# ---------------------------------------------------------------------------

# (label, text, must_change). One entry per pattern family in `_PATTERNS`,
# plus the cases that must come back untouched. None of these are real: every
# secret-shaped one is the right shape and the wrong bytes.
#
# `must_change` is what stops this comparison being decoration, and it is the
# same problem `_check_the_probe_reached_both` solves for routing. Agreement
# is free to a degenerate function: two copies that both stopped redacting
# agree on every line of this table, and so do two that redact everything.
# One direction is a leaked credential and the other is Edvard's standing
# rule that nothing is thrown away to make the output tidier. Both are
# checked, and only when nothing drifted -- a real difference is reported as
# itself rather than masked by an instrumentation error.
REDACTION_PROBES = (
    ("anthropic oauth token",
     "auth: sk-ant-oat01-Zx9Kq2Lm4Np7Rt0Vw3Yb6Ec8Hj1Ug5Sd", True),
    ("github classic pat",
     "cloning with ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5 now", True),
    ("github fine-grained pat",
     "GITHUB=github_pat_11ABCDEFG0abcdefghij_KLMNOPQRSTUVWX", True),
    # Built from pieces, following the same comment in
    # tests/test_agora_persona_runner.py: gitleaks' own jwt rule keys on a
    # contiguous `eyJ...`, so a literal one fails this repo's secret scan in
    # the file whose whole subject is not leaking secrets. The scanner needs
    # contiguous text; redact() does not.
    ("jwt",
     "cookie=eyJhbGciOiJIUzI1NiJ9." + "ey" + "JzdWIiOiJub3ZhIn0"
     + ".QWJjRGVmR2hpSmts", True),
    ("aws access key id", "id AKIAIOSFODNN7EXAMPLE here", True),
    ("aws session key id", "id ASIAIOSFODNN7EXAMPLE here", True),
    ("private key block",
     "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNza\nAAAA\n"
     "-----END OPENSSH PRIVATE KEY-----", True),
    ("named value, bare",
     "ANTHROPIC_API_KEY=notarealkeyvalue1234", True),
    ("named value, quoted json",
     '{"couchdb_password": "notarealpassword", "db": "nova"}', True),
    ("named value, yaml colon",
     "  CDB_PASS: notarealpassword1234\n  CDB_USER: nova", True),
    # Two patterns over one string, in order, which is where the label got
    # rewritten by the pass after the one that earned it. The probe cannot
    # assert *which* label survives -- it compares two copies, it does not
    # know what is right -- but it does hold them to the same answer, and
    # `must_change` holds them to giving one at all.
    ("credential inside a name the value pattern also matches",
     '{"accessToken": "sk-ant-oat01-Zx9Kq2Lm4Np7Rt0Vw3Yb6Ec8Hj1Ug5Sd"}', True),
    # The pass-through half. A cycle reading these should be able to say why
    # each one is not a credential without running the regexes.
    ("ordinary prose",
     "The password rotation is documented in decisions/adr-0012.md.", False),
    ("named value below the length floor", "TOKEN = short", False),
    ("a word that is only a topic", "secrets, passwords and api keys", False),
    ("empty string", "", False),
    ("not a string at all", None, False),
    ("also not a string", 42, False),
)


class ContractRedactorMissing(LookupError):
    """A copy does not expose a `redact` this tool can drive.

    Same reasoning as `ContractNameMissing` and `ContractRouterMissing`: a
    filter that cannot be found is not a filter that agrees.
    """


def _redactor(path, name):
    module = _load_module(path, name)
    fn = getattr(module, "redact", None)
    if not callable(fn):
        raise ContractRedactorMissing("%s: no callable redact()" % (path,))
    return fn


def _check_the_probe_still_bites(agreed):
    """Raise if the two copies agree by not doing the job at all.

    `agreed` is `{label: (probe text, shared answer)}`. A `must_change` probe
    that came back byte-identical means both copies passed a credential
    through; a pass-through probe that came back altered means both copies
    are eating material that is not a credential. Either way the comparison
    above found nothing, and reporting that as "in sync" would be true and
    useless.

    It states what it observed and not why, for the same reason the routing
    guard does: a filter that stopped filtering has more than one cause and
    this tool cannot tell them apart from here.
    """
    for label, must_change, text, answer in agreed:
        if must_change and answer == text:
            raise ContractRedactorMissing(
                "both copies returned the %s probe unchanged (%r). Two copies "
                "agreeing that a credential needs no redaction is not evidence "
                "that they agree -- refusing to report a comparison in which "
                "the filter under test did nothing" % (label, text))
        if not must_change and answer != text:
            raise ContractRedactorMissing(
                "both copies altered the %s probe, which is not credential-"
                "shaped: %r became %r. Redaction is the one exception to "
                "keeping output whole, so both copies over-redacting is a "
                "finding, not a clean run" % (label, text, answer))


def compare_redaction(runner_path, bridge_path):
    """Probes the two copies answer differently, as (label, left, right).

    Empty means every string in `REDACTION_PROBES` came out of both copies
    byte-identical. Behaviour rather than syntax, like `compare_routing` --
    but unlike routing this needs no configuration, because `redact` reads
    nothing but its argument.
    """
    with _process_state_restored():
        left = _redactor(runner_path, "_sync_contract_runner_redact")
        right = _redactor(bridge_path, "_sync_contract_bridge_redact")
        drifted = []
        agreed = []
        for label, text, must_change in REDACTION_PROBES:
            left_answer = left(text)
            right_answer = right(text)
            if left_answer != right_answer:
                drifted.append((label, left_answer, right_answer))
            else:
                agreed.append((label, must_change, text, left_answer))
    if not drifted:
        _check_the_probe_still_bites(agreed)
    return drifted


# ---------------------------------------------------------------------------
# The CI workflows: their shipping half, which is one pipeline written twice
# ---------------------------------------------------------------------------
#
# `.github/workflows/build.yaml` exists in both repos and most of it differs
# on purpose: one compiles `agora_runner`, the other `bridge`, and only the
# runner has a browser suite. What may not differ is everything from the
# secret scan down to the manifest commit -- the concurrency group that stops
# two merges racing, the image and config-repo names, the build-push job and
# the update-manifest job. Those were deliberately written against
# `${{ github.event.repository.name }}` so the two copies could be textually
# identical, which is what makes comparing them cheap.
#
# The comparison is on the PARSED workflow, not the source. That is not a
# nicety: the two files carry different prose around the same steps (the race
# comment above `concurrency` is written from each repo's point of view), and
# a text diff is 100% noise. Parsing drops comments for free and leaves the
# steps, which is the only part that runs.
WORKFLOW_PROBES = (
    # YAML 1.1 reads a bare `on:` as the boolean True, so this key is looked
    # up both ways. Every parser in this repo has hit that at least once.
    ("triggers", lambda doc: doc.get("on", doc.get(True))),
    ("concurrency", lambda doc: doc.get("concurrency")),
    ("image and config repo", lambda doc: doc.get("env")),
    ("secret scan", lambda doc: _workflow_step(doc, "test", "Secret scan")),
    ("build-push job", lambda doc: doc.get("jobs", {}).get("build-push")),
    ("update-manifest job", lambda doc: doc.get("jobs", {}).get("update-manifest")),
    # Not the jobs' contents -- just that neither copy has quietly lost one of
    # the four. An extra repo-specific job is allowed and does not drift.
    ("pipeline jobs", lambda doc: sorted(
        set(doc.get("jobs", {})) & {"test", "vault-drift", "build-push", "update-manifest"})),
)


class ContractWorkflowUnreadable(LookupError):
    """A copy could not be parsed, or a probe found nothing in either copy.

    The second half is the one that matters. Every probe here reads a key out
    of a mapping and `None == None`, so a job renamed on both sides -- or a
    probe written against a key that never existed -- reports agreement
    without having compared anything. Cycle 53's lesson in one guard: a
    negative result only counts if a positive result was possible.
    """


def _workflow_step(doc, job, step_name):
    """The one step called `step_name` in `job`, or None if there isn't one."""
    for step in doc.get("jobs", {}).get(job, {}).get("steps", []) or []:
        if isinstance(step, dict) and step.get("name") == step_name:
            return step
    return None


def _parse_workflow(path):
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - exercised in CI, not here
        raise ContractWorkflowUnreadable(
            "pyyaml is not installed, so the workflow pair cannot be parsed "
            "(pip install pyyaml)") from exc
    try:
        with open(path, encoding="utf-8") as handle:
            doc = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise ContractWorkflowUnreadable("%s: %s" % (path, exc)) from exc
    if not isinstance(doc, dict):
        raise ContractWorkflowUnreadable("%s: not a workflow mapping" % (path,))
    return doc


def compare_workflow(left_path, right_path):
    """[(label, left answer, right answer)] for every probe that disagrees."""
    left, right = _parse_workflow(left_path), _parse_workflow(right_path)
    drifted, blind = [], []
    for label, probe in WORKFLOW_PROBES:
        left_answer, right_answer = probe(left), probe(right)
        if left_answer is None and right_answer is None:
            blind.append(label)
        elif left_answer != right_answer:
            drifted.append((label, left_answer, right_answer))
    if blind:
        raise ContractWorkflowUnreadable(
            "%s found nothing in either copy, so %s comparing nothing: the "
            "key was renamed on both sides, or never existed. Fix the probe "
            "in WORKFLOW_PROBES." % (
                ", ".join(blind), "they are" if len(blind) > 1 else "it is"))
    return drifted


# (label, path inside the runner, path inside the bridge). The CLI takes the
# two repository roots and joins these, so a new pair is one line here.
PAIRS = (
    ("vault client", "agora_runner/vault.py", "bridge/vault_tool.py"),
    ("redaction", "agora_runner/redact.py", "bridge/redact.py"),
    ("ci workflow", ".github/workflows/build.yaml", ".github/workflows/build.yaml"),
)


_ADVICE = (
    "\nThese two files are hand-synced copies of one client against one\n"
    "database. Write the change into both, or move the name out of\n"
    "CONTRACT_CONSTANTS/CONTRACT_FUNCTIONS in tools/sync_contract.py\n"
    "and say in the journal why it is allowed to differ."
)


_ROUTING_ADVICE = (
    "\nThe two copies sent the same path to different databases. That is a\n"
    "file being read from, or written into, the wrong store -- not a\n"
    "formatting difference. Fix the copy that is wrong; there is no\n"
    "table in tools/sync_contract.py to relax, because both processes\n"
    "have to agree on this to share one CouchDB at all."
)


def _report(name, left_label, left, right_label, right):
    return "\n".join([
        "  %s" % name,
        "    %s: %s" % (left_label, left[name]),
        "    %s: %s" % (right_label, right[name]),
    ])


_REDACTION_ADVICE = (
    "\nThe two copies of redact() answered the same string differently. One\n"
    "of them is publishing something the other strips, or stripping\n"
    "something the other keeps. There is no table in tools/sync_contract.py\n"
    "to relax: both processes publish into the same conversation feed, so\n"
    "the weaker filter is the one that decides what Edvard sees."
)


_ASSEMBLY_ADVICE = (
    "\nThe two copies went looking for one document's content chunks in\n"
    "different databases. Chunk ids carry no path, so a chunk fetched from\n"
    "the wrong store is not found -- and a chunk that is merely in the\n"
    "other database is indistinguishable from one that was never written,\n"
    "so an intact file reports itself corrupt. There is no table in\n"
    "tools/sync_contract.py to relax: both copies read one CouchDB and a\n"
    "document's chunks live wherever the document does."
)


def check_vault_pair(left_path, right_path):
    """Exit code for the vault client pair: 0 in sync, 1 drift, 2 unreadable."""
    try:
        left = extract_contract(open(left_path, encoding="utf-8").read())
        right = extract_contract(open(right_path, encoding="utf-8").read())
    # OSError as well: a copy that moved is a legible "cannot read", not a
    # traceback in the CI log. Same reasoning as `_load_module`.
    except (ContractNameMissing, OSError) as exc:
        print("vault contract: %s" % exc, file=sys.stderr)
        return 2
    drifted = sorted(n for n in left if left[n] != right[n])
    if drifted:
        print("vault contract: %d of %d names have drifted between\n"
              "  %s\n  %s\n" % (len(drifted), len(left), left_path, right_path),
              file=sys.stderr)
        for name in drifted:
            print(_report(name, left_path, left, right_path, right), file=sys.stderr)
        print(_ADVICE, file=sys.stderr)
        return 1
    # Flushed because the failure below prints to stderr, which is not
    # buffered: without this the CI log reports the routing drift above the
    # "in sync" line it followed, which reads as the opposite of what ran.
    print("vault contract: %d names in sync" % len(left), flush=True)

    # Both comparisons or neither: the name half passing is exactly the
    # state that read as "in sync" while a deleted routing branch went
    # unnoticed, so returning 0 above would reinstate that.
    try:
        routed = compare_routing(left_path, right_path)
    except ContractRouterMissing as exc:
        print("vault routing: %s" % exc, file=sys.stderr)
        return 2
    if routed:
        print("\nvault routing: %d question(s) answered differently between\n"
              "  %s\n  %s\n" % (len(routed), left_path, right_path),
              file=sys.stderr)
        for question, left_answer, right_answer in routed:
            print("\n".join([
                "  %s" % question,
                "    %s: %r" % (left_path, left_answer),
                "    %s: %r" % (right_path, right_answer),
            ]), file=sys.stderr)
        print(_ROUTING_ADVICE, file=sys.stderr)
        return 1
    print("vault routing: every probed path and prefix resolved the same "
          "in both copies", flush=True)

    # Third and last: where each copy goes looking for a document's chunks.
    # All three or none, for the reason the routing half gives -- the two
    # halves above passing is exactly the state that read as "in sync" while
    # assembly recomputed a route it had been handed.
    try:
        assembled = compare_assembly(left_path, right_path)
    except (ContractAssemblerMissing, ContractRouterMissing) as exc:
        print("vault assembly: %s" % exc, file=sys.stderr)
        return 2
    if not assembled:
        # Questions times configurations, because every row is driven under
        # both. Counting the rows alone understated the run by half, which
        # is a check under-claiming what it did -- the same class of thing
        # as over-claiming, one direction friendlier. Second reader on #156.
        print("vault assembly: %d probed reads resolved to the same database "
              "in both copies"
              % (len(_assembly_questions("_probe")) * len(ROUTING_CONFIGS)),
              flush=True)
        return _check_writes(left_path, right_path)
    print("\nvault assembly: %d question(s) answered differently between\n"
          "  %s\n  %s\n" % (len(assembled), left_path, right_path),
          file=sys.stderr)
    for question, left_answer, right_answer in assembled:
        print("\n".join([
            "  %s" % question,
            "    %s: %r" % (left_path, left_answer),
            "    %s: %r" % (right_path, right_answer),
        ]), file=sys.stderr)
    print(_ASSEMBLY_ADVICE, file=sys.stderr)
    return 1


def _check_writes(left_path, right_path):
    """The fourth and last stage of the vault pair. Reached only when the
    three before it passed, for the reason each of those gives: any stage
    returning 0 on its own reinstates a partial "in sync" line.
    """
    try:
        drifted = compare_writes(left_path, right_path)
    except (ContractWriterMissing, ContractRouterMissing) as exc:
        print("vault writes: %s" % exc, file=sys.stderr)
        return 2
    if not drifted:
        print("vault writes: %d probed writes made the same requests in both "
              "copies" % (len(_WRITE_QUESTIONS) * len(ROUTING_CONFIGS)),
              flush=True)
        return 0
    print("\nvault writes: %d question(s) answered differently between\n"
          "  %s\n  %s\n" % (len(drifted), left_path, right_path),
          file=sys.stderr)
    for question, left_answer, right_answer in drifted:
        print("\n".join([
            "  %s" % question,
            "    %s: %r" % (left_path, left_answer),
            "    %s: %r" % (right_path, right_answer),
        ]), file=sys.stderr)
    print(_WRITE_ADVICE, file=sys.stderr)
    return 1


def check_redaction_pair(left_path, right_path):
    """Exit code for the redaction pair: 0 in sync, 1 drift, 2 undrivable."""
    try:
        drifted = compare_redaction(left_path, right_path)
    # ContractRouterMissing too: `_load_module` is shared, so an unreadable
    # path arrives as that one even on this pair.
    except (ContractRedactorMissing, ContractRouterMissing) as exc:
        print("redaction: %s" % exc, file=sys.stderr)
        return 2
    if not drifted:
        print("redaction: %d probes redacted identically in both copies"
              % len(REDACTION_PROBES))
        return 0
    print("\nredaction: %d probe(s) answered differently between\n"
          "  %s\n  %s\n" % (len(drifted), left_path, right_path), file=sys.stderr)
    for label, left_answer, right_answer in drifted:
        print("\n".join([
            "  %s" % label,
            "    %s: %r" % (left_path, left_answer),
            "    %s: %r" % (right_path, right_answer),
        ]), file=sys.stderr)
    print(_REDACTION_ADVICE, file=sys.stderr)
    return 1


_WORKFLOW_ADVICE = (
    "\nThe two build pipelines disagree about a part that is meant to be one\n"
    "pipeline written twice. Everything from the secret scan down to the\n"
    "manifest commit is repo-independent by construction, so a difference\n"
    "here is one repo shipping under rules the other does not -- an\n"
    "unserialised merge race, an unscanned commit, a digest written\n"
    "differently. Write the change into both, or drop the probe from\n"
    "WORKFLOW_PROBES in tools/sync_contract.py and say in the journal why\n"
    "the two pipelines are allowed to differ there."
)


def check_workflow_pair(left_path, right_path):
    """Exit code for the CI workflow pair: 0 in sync, 1 drift, 2 unreadable."""
    try:
        drifted = compare_workflow(left_path, right_path)
    except ContractWorkflowUnreadable as exc:
        print("ci workflow: %s" % exc, file=sys.stderr)
        return 2
    if not drifted:
        # Flushed for the reason `check_vault_pair` gives: stdout is buffered
        # in CI and stderr is not, so an unflushed success line lands after
        # the next pair's failure and reads as though it followed it.
        print("ci workflow: %d probes match in both pipelines"
              % len(WORKFLOW_PROBES), flush=True)
        return 0
    print("\nci workflow: %d probe(s) differ between\n  %s\n  %s\n"
          % (len(drifted), left_path, right_path), file=sys.stderr)
    for label, left_answer, right_answer in drifted:
        print("\n".join([
            "  %s" % label,
            "    %s: %r" % (left_path, left_answer),
            "    %s: %r" % (right_path, right_answer),
        ]), file=sys.stderr)
    print(_WORKFLOW_ADVICE, file=sys.stderr)
    return 1


_CHECKERS = {
    "vault client": check_vault_pair,
    "redaction": check_redaction_pair,
    "ci workflow": check_workflow_pair,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: sync_contract.py <runner repo root> <bridge repo root>",
              file=sys.stderr)
        return 2
    runner_root, bridge_root = argv
    worst = 0
    for label, runner_rel, bridge_rel in PAIRS:
        left_path = os.path.join(runner_root, runner_rel)
        right_path = os.path.join(bridge_root, bridge_rel)
        # Every pair is run even after one fails. They are independent files
        # and a cycle that has to fix two of them wants to know that now, not
        # after a second red build.
        worst = max(worst, _CHECKERS[label](left_path, right_path))
    return worst


if __name__ == "__main__":
    sys.exit(main())
