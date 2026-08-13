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
"""
import ast
import json
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
        return json.dumps(ast.literal_eval(node), sort_keys=True, default=str)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
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
    if not drifted:
        print("vault contract: %d names in sync" % len(left))
        return 0
    print("vault contract: %d of %d names have drifted between\n"
          "  %s\n  %s\n" % (len(drifted), len(left), left_path, right_path),
          file=sys.stderr)
    for name in drifted:
        print(_report(name, left_path, left, right_path, right), file=sys.stderr)
    print("\nThese two files are hand-synced copies of one client against one\n"
          "database. Write the change into both, or move the name out of\n"
          "CONTRACT_CONSTANTS/CONTRACT_FUNCTIONS in tools/vault_contract.py\n"
          "and say in the journal why it is allowed to differ.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
