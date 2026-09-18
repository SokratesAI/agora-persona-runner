"""Turn a vault topic's raw sources into a written wiki -- idea #9.

The owner's shape, ideas.md #9: *"use the Vault to create a folder about a
topic. In that folder two folders are created, one for all 'raw' files
gathered from webscraping research or other downloads and the other folder
for the generated llm wiki that the agent created."*

    projects/sokrates/wiki/<topic>/raw/    anything you drop in: notes, scraped pages
    projects/sokrates/wiki/<topic>/wiki/   written by this tool, regenerated whole

The folder is in the owner's Obsidian vault on purpose (it is not under a
`NOVA_DB_FOLDERS` prefix), so the wiki reaches his phone.

    python3 -m tools.llm_wiki <topic>             # regenerate wiki/ from raw/
    python3 -m tools.llm_wiki <topic> --dry-run   # print the pages, write nothing

Runs from the bridge pod: it needs `/app/bridge/vault_tool.py` and the
`claude` CLI. The model is reached through that CLI, which is the flat
subscription, never the metered API (identity.md rule 9) -- and so that
cannot change by accident, `ANTHROPIC_API_KEY` is removed from the CLI's
environment before it starts. The default model is Haiku, as the idea asks:
this is summarising, not reasoning.

The raw files stay the source of truth. Every generated page carries
`generated_by: llm_wiki` in its frontmatter, and a regeneration deletes the
generated pages it did not write again -- so a page that lost its source
disappears -- while a page without that marker (one somebody wrote by hand)
is never touched.

Exit 0 when the wiki was written (or printed, with --dry-run); 1 on any
failure, with nothing written unless every page parsed.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

VAULT_TOOL = "/app/bridge/vault_tool.py"
ROOT = "projects/sokrates/wiki/"
DEFAULT_MODEL = "claude-haiku-4-5"
MARKER = "generated_by: llm_wiki"
OSLO = ZoneInfo("Europe/Oslo")

#: Haiku 4.5 takes 200k tokens. At ~4 characters a token, 600k characters of
#: sources plus the instructions stays inside it. Past that the CLI would
#: fail or the model would silently lose the tail, so refuse and say so.
MAX_SOURCE_CHARS = 600_000

TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")
PAGE_RE = re.compile(r"^=== PAGE: ([a-z0-9][a-z0-9-]*\.md) ===[ \t]*$", re.M)

INSTRUCTIONS = """You are writing a small wiki from raw research sources.

Rules:
- Use only what the sources say. Do not add facts from memory. If sources disagree, say so.
- After each claim, cite the source file it came from in brackets, like [source: notes.md].
- Plain, direct English. Short paragraphs. Headings with ##.
- Write 1 to 8 pages. One page must be index.md: a short overview of the topic and a list of the other pages as [[page-name]] links.
- Name pages in lowercase with hyphens, ending in .md.
- Output ONLY the pages, each starting with a line exactly like:
=== PAGE: index.md ===
and nothing before the first such line.

The topic is: {topic}

The sources follow, each starting with a line "--- SOURCE: <file> ---".
"""


class WikiError(Exception):
    pass


def is_text(body):
    """A NUL byte or a replacement-character run means a binary upload."""
    return "\x00" not in body and body.count("\ufffd") < 8


def build_prompt(topic, sources, keep=()):
    """`sources` is [(name, text)]; order is kept so the prompt is stable.

    `keep` names the pages the last run wrote. Without it the model picks a
    new layout every run -- measured on the first topic: five pages, then
    one page from the same three sources, and the regeneration deleted the
    other four as stale. Links into the wiki break every time that happens.
    """
    parts = [INSTRUCTIONS.format(topic=topic)]
    if keep:
        parts.append("The wiki already has these pages. Write every one of them again under the "
                     "same name, unless the sources no longer support it: "
                     + ", ".join(sorted(keep)) + "\n")
    for name, text in sources:
        parts.append(f"--- SOURCE: {name} ---\n{text.strip()}\n")
    return "\n".join(parts)


def parse_pages(output):
    """Split the model's output into {page name: body}.

    Refuses output with no index.md or a page named twice, rather than
    writing a partial wiki.
    """
    marks = list(PAGE_RE.finditer(output))
    if not marks:
        raise WikiError("model output held no '=== PAGE: <name>.md ===' line")
    pages = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(output)
        name = m.group(1)
        if name in pages:
            raise WikiError(f"model wrote {name} twice")
        body = output[m.end():end].strip()
        if not body:
            raise WikiError(f"model wrote {name} empty")
        pages[name] = body
    if "index.md" not in pages:
        raise WikiError("model wrote no index.md")
    return pages


def render_page(body, model, sources, when):
    lines = ["---", MARKER, f"model: {model}", f"generated: {when}", "sources:"]
    lines += [f"  - {name}" for name in sources]
    lines += ["---", "", body.rstrip(), ""]
    return "\n".join(lines)


def stale_pages(existing, written):
    """Generated pages from an earlier run that this run did not write.

    `existing` is {name: body}. A page without the marker was written by a
    person and is never returned.
    """
    return sorted(
        name for name, body in existing.items()
        if name not in written and MARKER in body.split("\n---", 1)[0]
    )


def _vault(*args):
    return subprocess.run(
        [sys.executable, VAULT_TOOL, *args],
        capture_output=True, text=True, timeout=180,
    )


def _ls(prefix):
    r = _vault("ls", prefix)
    if r.returncode != 0:
        raise WikiError(f"ls {prefix} failed: {r.stderr.strip()}")
    return [p for p in r.stdout.splitlines() if p.startswith(prefix) and not p.endswith("/")]


def _get(path):
    r = _vault("get", path)
    if r.returncode != 0:
        raise WikiError(f"get {path} failed: {r.stderr.strip()}")
    return r.stdout


def _put(path, text):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
    try:
        r = _vault("put", path, f.name, "--allow-shrink")
    finally:
        os.unlink(f.name)
    if r.returncode != 0:
        raise WikiError(f"put {path} failed: {r.stderr.strip() or r.stdout.strip()}")


def ask_model(prompt, model):
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
    r = subprocess.run(
        ["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence"],
        input=prompt, capture_output=True, text=True, timeout=900, env=env,
    )
    if r.returncode != 0:
        raise WikiError(f"claude exited {r.returncode}: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout


def run(topic, model=DEFAULT_MODEL, dry_run=False, out=print):
    if not TOPIC_RE.match(topic):
        raise WikiError(f"topic must be lowercase letters, digits and hyphens: {topic!r}")
    base = f"{ROOT}{topic}/"
    sources, skipped = [], []
    for path in sorted(_ls(f"{base}raw/")):
        body = _get(path)
        name = path[len(f"{base}raw/"):]
        (sources if is_text(body) else skipped).append((name, body))
    for name, _ in skipped:
        out(f"skipped (not text): {name}")
    if not sources:
        raise WikiError(f"no text sources in {base}raw/ -- put some files there first")
    size = sum(len(t) for _, t in sources)
    if size > MAX_SOURCE_CHARS:
        raise WikiError(f"sources are {size:,} characters, over the {MAX_SOURCE_CHARS:,} "
                        "a Haiku context holds; split the topic")
    existing = {p[len(f"{base}wiki/"):]: _get(p) for p in _ls(f"{base}wiki/")}
    keep = stale_pages(existing, {})  # every generated page, since nothing is written yet
    out(f"{len(sources)} source(s), {size:,} characters -> {model}")
    pages = parse_pages(ask_model(build_prompt(topic, sources, keep), model))
    when = datetime.now(OSLO).strftime("%Y-%m-%d %H:%M")
    names = [n for n, _ in sources]
    if dry_run:
        for name, body in pages.items():
            out(f"=== {base}wiki/{name} ===\n{render_page(body, model, names, when)}")
        return pages
    for name, body in pages.items():
        _put(f"{base}wiki/{name}", render_page(body, model, names, when))
        out(f"wrote {base}wiki/{name}")
    for name in stale_pages(existing, pages):
        r = _vault("delete", f"{base}wiki/{name}")
        out(f"removed stale {base}wiki/{name}" if r.returncode == 0
            else f"could not remove {base}wiki/{name}: {r.stderr.strip()}")
    return pages


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("topic")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args(argv)
    try:
        run(a.topic, a.model, a.dry_run)
    except (WikiError, subprocess.TimeoutExpired) as e:
        print(f"llm_wiki: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
