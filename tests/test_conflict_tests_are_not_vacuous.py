"""A test that patches a 409 in must be joined by one that earns a 409.

Three cycles in a row filed the same defect by hand: code that branches on
CouchDB's 409 was covered only by tests that *handed* it the string
"FAILED(409)". Those tests prove the branch runs. They cannot prove the
branch is reachable -- so when `nova_capture` stopped sending `if_rev` at
all, and could no longer provoke a conflict from any database on earth, all
123 tests covering it stayed green (Cycle 142, measured by deleting the
`if_rev` argument from both write sites).

The canned stubs are not the bug and this guard does not ban them. Feeding
the write mock a 409 is the cheap, readable way to pin what the *recovery*
does -- that it retries once, that it re-reads rather than resending, that
it gives up on a non-409. Deleting those would lose real coverage. What was
missing is the other half: something, somewhere, that makes a real CouchDB
reject a real stale revision, so that the branch has a reachable door in
front of it. `tests/couch_fake.FakeCouch` is that something.

So the rule enforced below is a pairing rule, not a prohibition:

    if a module is handed a canned 409, that same module must also be
    exercised by at least one test file that uses FakeCouch.

Both halves are static: the canned side is a `patch`/`patch.object` of a
vault write function whose `return_value` or `side_effect` carries a literal
mentioning 409; the earned side is a module patched inside a file that
imports FakeCouch. That makes this guard cheap and, more importantly,
blind to intent -- it cannot be satisfied by a comment promising the
coverage exists, which is what the previous three versions of this rule
were made of.

What it deliberately does not check: that the FakeCouch test covers the
*same write site*. A module-level pairing is coarse. It is also the level a
regression actually happens at -- somebody adds a new conflict branch and
reaches for the stub next to it -- and a finer rule would need to know which
function each test exercises, which is the point where a static guard starts
guessing and stops being trustworthy.
"""
import ast
import pathlib

TESTS = pathlib.Path(__file__).parent

#: Patching one of these with a canned 409 is what this guard pairs up.
#: Narrow on purpose: `couch_req` is left out because the honest fake
#: patches exactly that, and a guard that flagged the fix would be worse
#: than no guard.
WRITE_FUNCS = {"vault_write_path", "vault_append_path"}

#: This file, and the fake itself, talk *about* the pattern.
EXEMPT = {"test_conflict_tests_are_not_vacuous.py", "couch_fake.py"}


def _patched_module(call):
    """The module named as the first argument of a patch.object call."""
    func = call.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "object" or not call.args:
        return None
    target = call.args[0]
    # `runner.tools_dispatch` and `tools_dispatch` are the same subject, so
    # the last segment is the name to compare on.
    if isinstance(target, ast.Attribute):
        return target.attr
    return getattr(target, "id", None)


def _patched_attr(call):
    """The attribute name being replaced, i.e. patch.object(mod, "<this>")."""
    if len(call.args) < 2:
        return None
    second = call.args[1]
    return second.value if isinstance(second, ast.Constant) else None


def _mentions_409(node):
    """Any string literal under `node` that names a 409."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if "409" in sub.value:
                return True
    return False


def _scan():
    """(modules handed a canned 409, modules exercised through FakeCouch)."""
    canned, earned = {}, set()
    for path in sorted(TESTS.glob("test_*.py")) + [TESTS / "couch_fake.py"]:
        if path.name in EXEMPT:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        uses_fake = "FakeCouch" in source
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            module = _patched_module(node)
            if module is None:
                continue
            if uses_fake:
                earned.add(module)
            if _patched_attr(node) not in WRITE_FUNCS:
                continue
            for kw in node.keywords:
                if kw.arg in ("return_value", "side_effect") and _mentions_409(kw.value):
                    canned.setdefault(module, set()).add(path.name)
    return canned, earned


def test_every_canned_409_is_paired_with_one_a_real_database_would_send():
    canned, earned = _scan()
    assert canned, (
        "found no canned 409 stubs at all -- either they were all removed "
        "(delete this guard) or the scanner stopped matching (fix it), and "
        "a guard that silently matches nothing is the vacuous test it exists "
        "to forbid")
    unpaired = {mod: sorted(files) for mod, files in canned.items() if mod not in earned}
    assert not unpaired, (
        "these modules are handed a canned 409 but nothing makes a real "
        f"conflict reach them: {unpaired}. A stub that returns "
        "'FAILED(409)' proves the recovery branch runs; it cannot prove the "
        "branch is reachable, so the module can stop sending `if_rev` "
        "entirely and stay green. Add a test using tests.couch_fake."
        "FakeCouch that patches this module and lands a competing writer "
        "between the read and the write.")
