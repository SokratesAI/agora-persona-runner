"""`/memory` -- what each Agora persona remembers, on the owner's phone.

Idea #165, slice 3's second half. `tools.persona_memory --mirror` copies
every persona's auto-memory directory off the bridge PVC into one vault
document per persona under `MIRROR_PREFIX` (Nova's database, the one
nova-site reads). Those documents were only reachable through the vault;
this page reads them and shows every file whole, so the owner can see
exactly what a persona holds and correct it in chat.

A page of its own rather than a view inside app.js, for `/planned`'s
reason: app.js carries a must-never-grow ratchet and this needs no client
code. Pure functions here; the one vault read lives in nova_site.
"""
import html
import json
import re

# Shared with `tools.persona_memory`, which writes what this reads. They
# live here because the nova-site image carries agora_runner/ and not
# tools/, so the writer imports them from the reader and not the reverse.
MIRROR_PREFIX = "projects/sokrates/projects/agora/nova/resources/persona-memory/"
INDEX = "MEMORY.md"
FENCE = "~~~~"

_FILE_HEADING = re.compile(r"^## (.+)$")
_WRITTEN = re.compile(r"^Written (.+)\.$")


def _frontmatter(text):
    """`({key: value}, body)`. A double-quoted value is decoded as the JSON
    string the writer made it (`json.dumps(name)`); anything else is kept
    as written."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            value = value.strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            meta[key.strip()] = value
    return meta, text[end + 5:]


def parse_mirror(text):
    """One mirror document -> `{persona, personaId, mirrored, files}`.

    `files` is `[{name, written, body}]` in document order, which the
    writer already puts index-first. A fence opened and never closed keeps
    everything after it as that file's body rather than dropping it.
    """
    meta, body = _frontmatter(text)
    files, current, inside = [], None, False
    for line in body.splitlines():
        if inside:
            if line == FENCE:
                inside = False
            else:
                current["lines"].append(line)
            continue
        heading = _FILE_HEADING.match(line)
        if heading:
            current = {"name": heading.group(1), "written": "", "lines": []}
            files.append(current)
            continue
        if current is None:
            continue
        written = _WRITTEN.match(line)
        if written:
            current["written"] = written.group(1)
        elif line.startswith(FENCE):
            inside = True
    return {
        "persona": meta.get("persona", ""),
        "personaId": meta.get("persona_id", ""),
        "mirrored": meta.get("mirrored", ""),
        "files": [{"name": f["name"], "written": f["written"],
                   "body": "\n".join(f["lines"])} for f in files],
    }


def memory_payload(docs):
    """`{path: text}` under `MIRROR_PREFIX` -> `{personas, unreadable}`.

    `unreadable` counts what the vault read lost (a `VaultFiles` carries
    it), so a failed read never renders as "nobody remembers anything".
    """
    personas = [parse_mirror(text) for path, text in docs.items()
                if path.startswith(MIRROR_PREFIX) and path.endswith(".md")]
    personas.sort(key=lambda p: (p["persona"] or p["personaId"]).lower())
    return {"personas": personas,
            "unreadable": len(getattr(docs, "unreadable", None) or [])}


def render_page(payload):
    """The payload as one self-contained HTML page for his phone.

    Each persona is a collapsed block and each file inside it another, so
    the page opens as a list of names -- everything is there, nothing is
    in the way until he taps it.
    """
    esc = html.escape
    out = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'>",
        "<title>Persona memory</title><style>",
        "body{font:15px/1.4 system-ui,sans-serif;margin:0;padding:12px;background:#111;color:#eee}",
        "a{color:#8ab4f8}h1{font-size:20px;margin:4px 0 8px}.sum{color:#bbb;margin-bottom:12px}",
        "details{border-top:1px solid #333;padding:8px 0}summary{cursor:pointer}",
        ".p>summary{font-weight:600}.t{color:#999;font-size:13px;margin-left:6px}",
        ".f{margin-left:12px;padding:6px 0}.gap{color:#f28b82}",
        "pre{white-space:pre-wrap;word-break:break-word;background:#1b1b1b;padding:8px;margin:6px 0 0;font-size:13px}",
        "</style></head><body>",
        "<a href='/'>&larr; Nova</a><h1>Persona memory</h1>",
    ]
    personas = payload["personas"]
    if payload.get("unreadable"):
        out.append(f"<div class='sum gap'>{payload['unreadable']} document(s) could not "
                   "be read from the vault, so this list is incomplete.</div>")
    if not personas:
        out.append("<div class=sum>No persona has a mirrored memory yet.</div>")
    else:
        out.append(f"<div class=sum>{len(personas)} persona(s) with memory. "
                   "A copy, refreshed by a cycle -- to change what one "
                   "remembers, tell it in chat.</div>")
    for p in personas:
        name = p["persona"] or p["personaId"]
        out.append(f"<details class=p><summary>{esc(name)}"
                   f"<span class=t>{len(p['files'])} file(s) · copied {esc(p['mirrored'])}</span>"
                   "</summary>")
        for f in p["files"]:
            opened = " open" if f["name"] == INDEX else ""
            out.append(f"<details class=f{opened}><summary>{esc(f['name'])}"
                       f"<span class=t>{esc(f['written'])}</span></summary>"
                       f"<pre>{esc(f['body'])}</pre></details>")
        out.append("</details>")
    out.append("</body></html>")
    return "".join(out)
