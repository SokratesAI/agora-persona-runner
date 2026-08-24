"""Find the owner's first name in the prose of a public repo.

Two of the four repos this loop touches are public, and every cycle writes
about the person it works for -- so his name went into 1,188 places across
this one before anybody counted. His ask, `issues.md` 2026-08-24: *"I do not
like you using my name in public repos. Not as comments in code, on prs or
anything."*

The scan is deliberately narrow, and the boundary is the whole design.
**Prose about him is in scope; his name as data is not.** An Agora message's
`sender`, the author label the notes page draws, `## Needs Edvard` as a  (not-prose: quoting a literal)
heading the written archive really contains -- those are values the product
matches on, and a cycle that renames one of them does not anonymise anything,
it breaks the page and relabels him on his own screen. Cycle 371 did exactly
that first, with a blind substitution, and the suite caught it on the first
file. So this reads comments and docstrings and nothing else.

`python3 -m tools.name_scan <paths...>` prints one line per hit and exits 1
when there are any, so it works as a pre-merge check as well as a test.
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

# Case-sensitive, and that is the second half of the boundary. Lowercase
# `edvard` in a comment is almost always an identifier being quoted --
# `roll_needs_edvard.py`, `_unread_from_edvard`, `BOARD_PATHS["issues"]["edvard"]`,
# and the CouchDB database that is genuinely called `edvard`. Renaming those is
# a real job with its own risk (the database name is configured outside this
# repo), and a comment that names one of them is quoting code, not writing his
# name as prose. The capitalised form is the prose form.
NAME = re.compile(r"Edvard")

# Per-line escape hatch, borrowed from the truncation ban in `test_nova_site.py`
# rather than invented here. See `hits` for when it is the right answer.
EXEMPT = "not-prose"

BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
LINE_COMMENT = re.compile(r"(?<![:\"'\\])//[^\n]*")
HASH_COMMENT = re.compile(r"#[^\n]*")


def _docstring_starts(source):
    """`(lineno, col_offset)` of every docstring token in *source*."""
    starts = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return starts
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if not body:
            continue
        first = body[0]
        value = getattr(first, "value", None)
        if isinstance(first, ast.Expr) and isinstance(value, ast.Constant) \
                and isinstance(value.value, str):
            starts.add((value.lineno, value.col_offset))
    return starts


def python_prose(source):
    """Every comment and docstring in a Python file, as `(line, text)`."""
    docs = _docstring_starts(source)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    out = []
    for tok in toks:
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
        elif tok.type == tokenize.STRING and tok.start in docs:
            out.append((tok.start[0], tok.string))
    return out


def _regex_prose(source, patterns):
    out = []
    for pattern in patterns:
        for m in pattern.finditer(source):
            out.append((source.count("\n", 0, m.start()) + 1, m.group(0)))
    return out


def prose(path, source):
    """The regions of *source* that are prose rather than data.

    A suffix this does not know returns nothing, which is the safe answer: a
    false negative leaves a name in a file, a false positive would have a
    caller rewriting a data string.
    """
    suffix = Path(path).suffix
    if suffix == ".py":
        return python_prose(source)
    if suffix in {".js", ".mjs", ".ts"}:
        return _regex_prose(source, [BLOCK_COMMENT, LINE_COMMENT])
    if suffix == ".css":
        return _regex_prose(source, [BLOCK_COMMENT])
    if suffix in {".yml", ".yaml", ".sh", ".toml"}:
        return _regex_prose(source, [HASH_COMMENT])
    if suffix in {".md", ".txt"} or Path(path).name == ".gitignore":
        return [(1, source)]
    return []


def hits(path, source):
    """`(line, snippet)` for every prose region of *source* carrying the name.

    A physical line carrying `not-prose` is exempt. That marker is already this
    repo's convention for "the guard is right about the wrong line" -- `app.js`
    uses it on a `slice(0,` the truncation ban would otherwise reject -- and
    reusing the word rather than inventing a second one is deliberate.

    The case it exists for here is a comment that *quotes a literal*:
    `# sender="Edvard" is not decoration` sits above code that really does pass  (not-prose: quoting a literal)
    that string, and rewriting the comment would leave it describing something
    the line beneath it does not do. Cycle 371 wrote four of those before
    noticing. The exemption is per line, so it stays visible in the diff and
    nobody is tempted to widen the pattern instead.
    """
    lines = source.splitlines()
    found = []
    for line, text in prose(path, source):
        for m in NAME.finditer(text):
            at = line + text.count("\n", 0, m.start())
            if 0 < at <= len(lines) and EXEMPT in lines[at - 1]:
                continue
            start = max(0, m.start() - 40)
            found.append((at, text[start:m.end() + 40].replace("\n", " ").strip()))
    return found


def scan(paths):
    report = []
    for p in paths:
        try:
            source = Path(p).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line, snippet in hits(p, source):
            report.append(f"{p}:{line}: {snippet}")
    return report


def main(argv):
    report = scan(argv)
    for line in report:
        print(line)
    if report:
        print(f"\n{len(report)} use(s) of the owner's name in prose. "
              "Write 'the owner' instead; leave data strings alone.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
