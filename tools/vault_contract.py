"""Compares the part of the two vault clients that has to agree.

`agora_runner/vault.py` here and `bridge/vault_tool.py` in
agora-claude-bridge are separate hand-written copies of one client, talking
to one CouchDB. They are deliberately not a shared package -- the two run in
different pods with no common dependency -- so nothing has ever detected
drift between them, and Cycles 136-142, 148, 151 and 167 each wrote the same
fix into both by hand. A drift test has been in `nova/resources/ideas.md`
since Cycle 137 and this is it.

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

**What is still not covered**, stated here rather than left to be
rediscovered: chunk assembly picks its database from `_SRC_DB_KEY` in both
copies as of Cycle 169, and nothing checks that automatically -- doing so
means faking CouchDB, not calling a pure function. `_put_raw`,
`database_health` and the rest of the class are unpinned for the same
reason.

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
import importlib
import importlib.util
import json
import os
import sys

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
_PROBE_MODULES = ("_vault_contract_runner", "_vault_contract_bridge")


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
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ContractRouterMissing("cannot import %s" % (path,))
    module = importlib.util.module_from_spec(spec)
    # Registered before exec so a copy that imports itself by name resolves,
    # and so `importlib.reload` has something to work with.
    sys.modules[name] = module
    spec.loader.exec_module(module)
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
    module = _load_module(path, "_vault_contract_runner")
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
    module = _load_module(path, "_vault_contract_bridge")
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
            paths = tuple(sys.modules["_vault_contract_runner"].HEALTH_PROBE_PATHS)
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


_ADVICE = (
    "\nThese two files are hand-synced copies of one client against one\n"
    "database. Write the change into both, or move the name out of\n"
    "CONTRACT_CONSTANTS/CONTRACT_FUNCTIONS in tools/vault_contract.py\n"
    "and say in the journal why it is allowed to differ."
)


_ROUTING_ADVICE = (
    "\nThe two copies sent the same path to different databases. That is a\n"
    "file being read from, or written into, the wrong store -- not a\n"
    "formatting difference. Fix the copy that is wrong; there is no\n"
    "table in tools/vault_contract.py to relax, because both processes\n"
    "have to agree on this to share one CouchDB at all."
)


def _report(name, left_label, left, right_label, right):
    return "\n".join([
        "  %s" % name,
        "    %s: %s" % (left_label, left[name]),
        "    %s: %s" % (right_label, right[name]),
    ])


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: vault_contract.py <runner vault.py> <bridge vault_tool.py>",
              file=sys.stderr)
        return 2
    left_path, right_path = argv
    try:
        left = extract_contract(open(left_path, encoding="utf-8").read())
        right = extract_contract(open(right_path, encoding="utf-8").read())
    except ContractNameMissing as exc:
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
    if not routed:
        print("vault routing: every probed path and prefix resolved the same "
              "in both copies")
        return 0
    print("\nvault routing: %d question(s) answered differently between\n"
          "  %s\n  %s\n" % (len(routed), left_path, right_path), file=sys.stderr)
    for question, left_answer, right_answer in routed:
        print("\n".join([
            "  %s" % question,
            "    %s: %r" % (left_path, left_answer),
            "    %s: %r" % (right_path, right_answer),
        ]), file=sys.stderr)
    print(_ROUTING_ADVICE, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
