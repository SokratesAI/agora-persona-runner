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
    python3 -m tools.llm_wiki --stale             # regenerate only topics whose raw/ changed

Runs from the bridge pod: it needs `/app/bridge/vault_tool.py` and the
`claude` CLI. The model is reached through that CLI, which is the flat
subscription, never the metered API (identity.md rule 9) -- and so that
cannot change by accident, `ANTHROPIC_API_KEY` is removed from the CLI's
environment before it starts. The default model is Haiku, as the idea asks:
this is summarising, not reasoning.

It writes in two steps: one call plans the pages (keeping the names the
last run used), then one call per page writes it with every source in
view, three at a time. A single call for the whole wiki came out at about
30k characters from 60-90k of sources, an overview rather than the
textbook depth the owner asked for; a page to itself can go as deep as
its sources.

Every source has to end up cited on some page. The plan names the sources
each page draws on and is asked again once if it leaves one out; the page
call is told which sources it must cite; and a run where a source is still
cited nowhere is refused before anything is written.

The raw files stay the source of truth. Every generated page carries
`generated_by: llm_wiki` in its frontmatter, and a regeneration deletes the
generated pages it did not write again -- so a page that lost its source
disappears -- while a page without that marker (one somebody wrote by hand)
is never touched.

`--stale` is the schedule: prompt.md step 1a runs it every cycle, so a
file dropped in raw/ becomes wiki pages without anyone asking for it. A
topic is rebuilt when a raw file changed after the `generated:` stamp on
its index page (a new, edited or deleted file all count; in the stamp's own
minute, only a file the build did not include), when a source
the index names is gone from raw/, or when it has no index yet. Otherwise it
costs one `ls` and one `recent` per topic and makes no model call. The stamp
is taken when the sources are read, not when the pages are written, so a
file dropped during a run is still newer than it.

Exit 0 when the wiki was written (or printed, with --dry-run); 1 on any
failure, with nothing written unless every page parsed.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
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

SHARED_RULES = """Rules:
- Use only what the sources say. Do not add facts from memory. If sources disagree, say so.
- After each claim, cite the source file it came from in brackets, like [source: notes.md].
- Plain, direct English. Short paragraphs. Headings with ##.
"""

PLAN_INSTRUCTIONS = """You are planning a wiki from raw research sources. Do not write the pages yet.

List the pages the wiki should have: as many as the sources can fill with real depth, between 4 and 14. One must be index.md, the overview.
Name pages in lowercase with hyphens, ending in .md.
Every source file must be listed on at least one page. If a source fits no page, add a page for it.
Output ONLY one line per page, exactly in this form, and nothing else:
PAGE: <name>.md | <title> | <one sentence on what the page covers> | <the source files it draws on, comma-separated>

The topic is: {topic}

The sources follow, each starting with a line "--- SOURCE: <file> ---".
"""

PAGE_INSTRUCTIONS = """You are writing ONE page of a wiki from raw research sources: {page} ("{title}"), which covers: {scope}

Go as deep as the sources allow, like a textbook chapter: definitions, how it works, worked examples, trade-offs and common mistakes, wherever the sources support them. Do not repeat what belongs on another page; link to it as [[page-name]] instead.
""" + SHARED_RULES + """{index_rule}{must_use}
Output ONLY the page body in markdown, starting with a # title line.

The whole wiki is these pages:
{outline}

The topic is: {topic}

The sources follow, each starting with a line "--- SOURCE: <file> ---".
"""

INDEX_RULE = "- This is the index page: a short overview of the topic, then every other page as a [[page-name]] link with one line on what it covers."

OUTLINE_RE = re.compile(
    r"^PAGE:\s*([a-z0-9][a-z0-9-]*\.md)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*(?:\|\s*(.*?)\s*)?$", re.M)

CITE_RE = re.compile(r"\[source:([^\]]*)\]")

#: How many page calls run at once. Each is one `claude -p` process on the
#: bridge pod; three keeps a five-wiki run to minutes without crowding it.
WORKERS = 3


class WikiError(Exception):
    pass


def is_text(body):
    """A NUL byte or a replacement-character run means a binary upload."""
    return "\x00" not in body and body.count("\ufffd") < 8


def _sources_block(sources):
    return "\n".join(f"--- SOURCE: {name} ---\n{text.strip()}\n" for name, text in sources)


def build_plan_prompt(topic, sources, keep=()):
    """`sources` is [(name, text)]; order is kept so the prompt is stable.

    `keep` names the pages the last run wrote. Without it the model picks a
    new layout every run -- measured on the first topic: five pages, then
    one page from the same three sources, and the regeneration deleted the
    other four as stale. Links into the wiki break every time that happens.
    """
    parts = [PLAN_INSTRUCTIONS.format(topic=topic)]
    if keep:
        parts.append("The wiki already has these pages. Keep every one of them under the "
                     "same name, unless the sources no longer support it: "
                     + ", ".join(sorted(keep)) + "\n")
    parts.append(_sources_block(sources))
    return "\n".join(parts)


def parse_outline(output):
    """The plan call's output as [(page, title, scope)], index.md first.

    Refuses an outline with no index.md or a page named twice, rather than
    writing a partial wiki.
    """
    outline, seen = [], set()
    for m in OUTLINE_RE.finditer(output):
        name = m.group(1)
        if name in seen:
            raise WikiError(f"outline names {name} twice")
        seen.add(name)
        outline.append((name, m.group(2), m.group(3)))
    if not outline:
        raise WikiError("outline held no 'PAGE: <name>.md | title | scope' line")
    if "index.md" not in seen:
        raise WikiError("outline has no index.md")
    return sorted(outline, key=lambda row: row[0] != "index.md")


def parse_placement(output, names):
    """{page: [source files]} from the plan's fourth field, known names only.

    A name the model invented is dropped rather than trusted, so it can
    never stand in for a real source that went unplaced.
    """
    known = set(names)
    return {m.group(1): [f for f in re.split(r"[,\s]+", m.group(4) or "") if f in known]
            for m in OUTLINE_RE.finditer(output)}


def unplaced(placement, names):
    """Sources the plan put on no page, in source order."""
    placed = {f for files in placement.values() for f in files}
    return [n for n in names if n not in placed]


def uncited(names, pages):
    """Sources no written page cites as [source: <file>], in source order.

    This is the check that matters: Cycle 1871's rebuild left
    norway-employer-obligations.md on no page, and nothing said so.
    """
    cited = " ".join(c for body in pages.values() for c in CITE_RE.findall(body))
    return [n for n in names if not re.search(r"(?<![\w.-])" + re.escape(n) + r"(?![\w-])", cited)]


#: A number of three or more digits, with thousands separators ("69,940",
#: "1 000 000"). A separator only joins when three digits follow, so a list
#: like "2, 4, 5" stays three numbers rather than one.
NUMBER_RE = re.compile(r"\d+(?:[,. ]\d{3}(?!\d))*")


def _plain(text):
    return re.sub(r"(?<=\d)[,. ](?=\d{3}(?!\d))", "", text)


def miscited(sources, pages):
    """(page, number, cited files, files that do hold it) for each number a
    cited paragraph states that none of its cited sources contains.

    Advisory: measured Cycle 1887, 6 of 208 such numbers across the six
    wikis -- a real fact cited to the wrong file (NOK 69,940 is in the AS
    source, cited to the bookkeeping one) and the model's own worked
    examples cited as if a source said them.
    """
    text = {name: _plain(body) for name, body in sources}
    found = []
    for page, body in pages.items():
        for para in re.split(r"\n\s*\n", body):
            cites = [c.strip() for c in ",".join(CITE_RE.findall(para)).split(",") if c.strip()]
            if not cites:
                continue
            for raw in dict.fromkeys(NUMBER_RE.findall(CITE_RE.sub("", para))):
                n = _plain(raw)
                if len(n) < 3 or any(n in text.get(c, "") for c in cites):
                    continue
                found.append((page, raw, cites, [k for k, v in text.items() if n in v]))
    return found


def build_page_prompt(topic, sources, outline, page, must_use=()):
    """One page's prompt: every source whole, plus the outline to link into.

    Each page gets the whole source set rather than a slice the plan picked,
    because the one-call wiki's gap was depth -- 60-90k characters of sources
    came out as ~30k of wiki -- and a page can only go as deep as what it sees.
    """
    name, title, scope = next(row for row in outline if row[0] == page)
    listing = "\n".join(f"- [[{n[:-3]}]] {t}: {s}" for n, t, s in outline)
    head = PAGE_INSTRUCTIONS.format(
        page=name, title=title, scope=scope, topic=topic, outline=listing,
        index_rule=INDEX_RULE if name == "index.md" else "",
        must_use=("\n- This page must use and cite each of these sources: " + ", ".join(must_use))
        if must_use else "")
    return head + "\n" + _sources_block(sources)


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


STAMP_RE = re.compile(r"^generated:\s*(\d{4}-\d\d-\d\d \d\d:\d\d)\s*$", re.M)
RECENT_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d)\s+(\S.*?)(\s+\[DELETED\])?\s*$")


def front_sources(body):
    """(generated stamp, [sources]) from a generated page's frontmatter."""
    head = body.split("\n---", 1)[0]
    stamp = STAMP_RE.search(head)
    names = re.findall(r"^\s+-\s+(\S.*?)\s*$", head.split("sources:", 1)[1], re.M) if "sources:" in head else []
    return (stamp.group(1) if stamp else None), names


def why_stale(index_body, raw_names, raw_changes):
    """Why a topic needs rebuilding, or None.

    `index_body` is wiki/index.md or None; `raw_names` the files in raw/ now;
    `raw_changes` is [("YYYY-MM-DD HH:MM", name)] from `recent`, Oslo time,
    deleted files included. The stamp is the same format, so text compares.
    """
    if index_body is None:
        return "no wiki yet"
    stamp, sources = front_sources(index_body)
    if stamp is None:
        return "index.md carries no generated: stamp"
    gone = sorted(set(sources) - set(raw_names))
    if gone:
        return f"source(s) gone from raw/: {', '.join(gone)}"
    # The stamp has minute resolution and is taken as the sources are read, so
    # a change in that same minute is ambiguous: count it only for a file the
    # build did not include, or every drop-then-build would rebuild twice.
    newer = sorted({name for when, name in raw_changes
                    if when > stamp or (when == stamp and name not in sources)})
    if newer:
        return f"raw/ changed since {stamp}: {', '.join(newer)}"
    return None


def raw_changes(base, stamp, now=None):
    """[(when, name)] for raw/ files changed since `stamp`, via `recent`."""
    now = now or datetime.now(OSLO)
    since = datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=OSLO)
    hours = max(1, int((now - since).total_seconds() // 3600) + 2)
    r = _vault("recent", str(hours), f"{base}raw/")
    if r.returncode != 0:
        raise WikiError(f"recent {base}raw/ failed: {r.stderr.strip()}")
    if "[INCOMPLETE" in r.stdout:
        raise WikiError(f"recent {base}raw/ was incomplete; cannot judge staleness")
    out = []
    for line in r.stdout.splitlines():
        m = RECENT_RE.match(line)
        if m and m.group(2).startswith(f"{base}raw/"):
            out.append((m.group(1), m.group(2)[len(f"{base}raw/"):]))
    return out


def topics():
    r = _vault("ls", ROOT)
    if r.returncode != 0:
        raise WikiError(f"ls {ROOT} failed: {r.stderr.strip()}")
    return sorted({p[len(ROOT):].split("/", 1)[0] for p in r.stdout.splitlines()
                   if p.startswith(ROOT) and "/raw/" in p})


def check_topic(topic):
    base = f"{ROOT}{topic}/"
    names = [p[len(f"{base}raw/"):] for p in _ls(f"{base}raw/")]
    wiki = _ls(f"{base}wiki/")
    index = _get(f"{base}wiki/index.md") if f"{base}wiki/index.md" in wiki else None
    stamp = front_sources(index)[0] if index else None
    changes = raw_changes(base, stamp) if stamp else []
    return why_stale(index, names, changes)


def run_stale(model=DEFAULT_MODEL, only=None, out=print):
    """Rebuild every stale topic; the number that failed."""
    failed = 0
    for topic in ([only] if only else topics()):
        try:
            why = check_topic(topic)
            if why is None:
                out(f"{topic}: current")
                continue
            out(f"{topic}: rebuilding ({why})")
            run(topic, model, out=lambda line, t=topic: out(f"  {t}: {line}"))
        except (WikiError, subprocess.TimeoutExpired) as e:
            out(f"{topic}: FAILED -- {e}")
            failed += 1
    return failed


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


def write_pages(topic, sources, outline, model, ask=None, placement=None):
    """One model call per page, a few at a time; {page: body} in outline order.

    Any page failing or coming back empty fails the whole run, so a wiki is
    never half regenerated.
    """
    ask = ask or ask_model
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        bodies = pool.map(
            lambda row: ask(build_page_prompt(topic, sources, outline, row[0],
                                              (placement or {}).get(row[0], ())), model).strip(),
            outline)
        pages = dict(zip((n for n, _, _ in outline), bodies))
    for name, body in pages.items():
        if not body:
            raise WikiError(f"model wrote {name} empty")
    return pages


def dropped(outline_output, keep):
    """Pages the last run wrote that this plan does not name, sorted."""
    named = {m.group(1) for m in OUTLINE_RE.finditer(outline_output)}
    return sorted(set(keep) - named)


def restore_line(name, body, names):
    """An outline line that puts a dropped page back: its own first heading
    as the title, and the sources it cites as the page's sources."""
    text = body.split("\n---", 1)[1] if body.startswith("---") and "\n---" in body else body
    heading = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    title = (heading.group(1) if heading else name[:-3]).replace("|", "-")
    cited = " ".join(CITE_RE.findall(text))
    files = [n for n in names if re.search(r"(?<![\w.-])" + re.escape(n) + r"(?![\w-])", cited)]
    return f"\nPAGE: {name} | {title} | the same scope as the existing page | {', '.join(files)}"


def plan(topic, sources, keep, model, out=print, ask=None, hold=False, existing=None):
    """The outline and which sources each page uses.

    A plan that leaves a source on no page is asked again once, naming the
    source; a second miss refuses the run rather than quietly dropping it.
    With `hold`, a plan that drops a page in `keep` is treated the same way:
    the prompt already asks to keep them and Haiku ignored it on 09-19,
    deleting running-a-business's business-structures.md while all its
    sources were still in raw/. `run` sets `hold` only when no source the
    last build used is gone, which is the one case the prompt allows a drop.
    A page dropped twice is put back into the outline from `existing`
    ({name: body}) rather than refusing the run: on 09-19 business-finance
    was refused twice this way and a new source never reached the wiki.
    """
    ask = ask or ask_model
    names = [n for n, _ in sources]
    held = keep if hold else ()
    prompt = build_plan_prompt(topic, sources, keep)
    output = ask(prompt, model)
    missing = unplaced(parse_placement(output, names), names)
    lost = dropped(output, held)
    if missing or lost:
        notes = []
        if missing:
            out(f"plan left {', '.join(missing)} on no page; asking again")
            notes.append("Your last plan put these sources on no page: " + ", ".join(missing)
                         + ". List every source on at least one page this time, adding a page if needed.")
        if lost:
            out(f"plan dropped existing page(s) {', '.join(lost)}; asking again")
            notes.append("Your last plan dropped these existing pages, whose sources are all still here: "
                         + ", ".join(lost) + ". Keep every one of them under the same name.")
        output = ask(prompt + "\n" + "\n".join(notes) + "\nYour last plan was:\n" + output, model)
        lost = dropped(output, held)
        if lost:
            out(f"plan dropped {', '.join(lost)} twice; putting it back with the sources it cites")
            output += "".join(restore_line(n, (existing or {}).get(n, ""), names) for n in lost)
        missing = unplaced(parse_placement(output, names), names)
        if missing:
            raise WikiError(f"plan put {', '.join(missing)} on no page, twice")
    return parse_outline(output), parse_placement(output, names)


def run(topic, model=DEFAULT_MODEL, dry_run=False, out=print):
    if not TOPIC_RE.match(topic):
        raise WikiError(f"topic must be lowercase letters, digits and hyphens: {topic!r}")
    base = f"{ROOT}{topic}/"
    when = datetime.now(OSLO).strftime("%Y-%m-%d %H:%M")
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
    names = [n for n, _ in sources]
    _, built_from = front_sources(existing.get("index.md", ""))
    hold = bool(built_from) and set(built_from) <= set(names)
    outline, placement = plan(topic, sources, keep, model, out, hold=hold, existing=existing)
    out(f"outline: {len(outline)} page(s) -- {', '.join(n for n, _, _ in outline)}")
    pages = write_pages(topic, sources, outline, model, placement=placement)
    missing = uncited(names, pages)
    if missing:
        raise WikiError(f"no page cites {', '.join(missing)}; nothing written")
    for page, number, cites, holders in miscited(sources, pages):
        out(f"miscited: {page} says {number}, not in {', '.join(cites)}"
            + (f"; it is in {', '.join(holders)}" if holders else "; no source has it"))
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
    p.add_argument("topic", nargs="?")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--stale", action="store_true",
                   help="rebuild only topics whose raw/ changed since their last build")
    a = p.parse_args(argv)
    if a.stale:
        if a.dry_run:
            p.error("--stale writes; it has no --dry-run")
        return 1 if run_stale(a.model, a.topic) else 0
    if not a.topic:
        p.error("a topic, or --stale")
    try:
        run(a.topic, a.model, a.dry_run)
    except (WikiError, subprocess.TimeoutExpired) as e:
        print(f"llm_wiki: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
