"""The `/memory` page reads what `tools.persona_memory --mirror` writes."""
from unittest.mock import patch

from agora_runner import nova_site
from agora_runner.nova_persona_memory import (MIRROR_PREFIX, memory_payload,
                                              parse_mirror, render_page)
from tests.test_nova_site import _get
from tools.persona_memory import render_mirror


def _persona_dir(tmp_path, files):
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    return str(tmp_path)


def test_the_page_reads_back_every_file_the_writer_wrote(tmp_path):
    # The real writer's output, not a hand-made fixture: the two halves
    # agree on the format only if this round-trips.
    body = "---\nname: x\n---\n\n# Heading inside\n\n```py\nprint(1)\n```\n~~~~ nested fence"
    path = _persona_dir(tmp_path, {"MEMORY.md": "- [A](a.md) — hook",
                                   "a.md": body})
    doc = render_mirror("pid-1", 'Nova "the loop"', path, now=0)
    parsed = parse_mirror(doc)
    assert parsed["persona"] == 'Nova "the loop"'
    assert parsed["personaId"] == "pid-1"
    assert parsed["mirrored"].endswith("Oslo")
    assert [f["name"] for f in parsed["files"]] == ["MEMORY.md", "a.md"]
    assert parsed["files"][0]["body"] == "- [A](a.md) — hook"
    # The writer downgrades a four-tilde line inside a file to three.
    assert parsed["files"][1]["body"] == body.replace("~~~~", "~~~")
    assert parsed["files"][1]["written"].endswith("Oslo")


def test_personas_sort_by_name_and_other_paths_are_ignored():
    payload = memory_payload({
        MIRROR_PREFIX + "b.md": "---\npersona: \"zed\"\npersona_id: b\n---\n",
        MIRROR_PREFIX + "a.md": "---\npersona: \"Alma\"\npersona_id: a\n---\n",
        "elsewhere/c.md": "---\npersona: \"C\"\n---\n",
    })
    assert [p["persona"] for p in payload["personas"]] == ["Alma", "zed"]


def test_the_page_escapes_what_a_persona_wrote_and_opens_the_index():
    payload = {"personas": [{"persona": "Nova", "personaId": "p", "mirrored": "now",
                             "files": [{"name": "MEMORY.md", "written": "w", "body": "<b>x</b>"},
                                       {"name": "a.md", "written": "w", "body": "a & b"}]}]}
    page = render_page(payload)
    assert "<b>x" not in page and "&lt;b&gt;x" in page and "a &amp; b" in page
    assert "<details class=f open><summary>MEMORY.md" in page
    assert "<details class=f><summary>a.md" in page


def test_an_empty_mirror_says_so():
    assert "No persona has a mirrored memory yet" in render_page({"personas": []})


def test_the_memory_route_answers_with_the_mirror():
    doc = "---\npersona: \"Nova\"\npersona_id: p\n---\n\n## MEMORY.md\n\nWritten t.\n\n~~~~markdown\n- remembered\n~~~~\n"
    with patch.object(nova_site, "vault_bulk_fetch", return_value={MIRROR_PREFIX + "p.md": doc}) as fetch:
        nova_site.reset_cache()
        status, _, page = _get("/memory")
    nova_site.reset_cache()
    fetch.assert_called_with(MIRROR_PREFIX)
    assert status == 200 and b"Persona memory" in page and b"- remembered" in page
