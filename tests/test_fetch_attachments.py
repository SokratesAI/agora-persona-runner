"""The step between a markdown link and an image a cycle can actually look at.

The failure this guards is not a crash. It is a cycle reading
`![shot.jpg](/api/upload/89f92e….jpg)` in `comments.md`, seeing a dead
string, and telling Edvard it is blind — which happened twice in fifteen
minutes on 2026-08-21 while the bytes sat in the vault the whole time. So
the tests that matter here are the ones that pin *reporting*: an
attachment that cannot be fetched has to be named and counted, because a
tool that silently skips a broken link recreates the exact blindness it
was written to remove.
"""

import base64

import pytest

from agora_runner.nova_uploads import UPLOAD_PREFIX, CONTENT_TYPES
from tools import fetch_attachments

NAME = "a" * 32 + ".jpg"
OTHER = "b" * 32 + ".png"
RAW = b"\xff\xd8\xff\xe0 not really a jpeg, but real bytes"


def envelope(content_type, raw, filename="shot.jpg"):
    encoded = base64.b64encode(raw).decode("ascii")
    return f"content-type: {content_type}\nfilename: {filename}\n\n{encoded}\n"


def store(mapping):
    """A stand-in for `vault_tool.py get`: path -> body, or None."""
    return lambda path: mapping.get(path)


def test_finds_links_with_the_heading_they_sit_under():
    text = (
        "# Comments\n"
        "## New\n"
        f"### Cycle 303 · 2026-08-21 16:06\n\n![shot.jpg](/api/upload/{NAME})\n\n"
        f"### Cycle 300 · 2026-08-21 11:52\n\n![b](/api/upload/{OTHER})\n"
    )
    assert fetch_attachments.find_links(text) == [
        (NAME, "Cycle 303 · 2026-08-21 16:06"),
        (OTHER, "Cycle 300 · 2026-08-21 11:52"),
    ]


def test_the_same_image_linked_twice_is_fetched_once():
    text = f"![a](/api/upload/{NAME})\nand again ![a](/api/upload/{NAME})\n"
    assert fetch_attachments.find_links(text) == [(NAME, "")]


def test_a_file_with_no_attachments_finds_nothing():
    assert fetch_attachments.find_links("### Cycle 300\n\nJust text.\n") == []


def test_round_trip_writes_the_original_bytes(tmp_path):
    text = f"### Cycle 303\n\n![shot.jpg](/api/upload/{NAME})\n"
    results = fetch_attachments.fetch(
        text, str(tmp_path),
        getter=store({UPLOAD_PREFIX + NAME: envelope("image/jpeg", RAW)}),
    )
    (name, heading, path, detail) = results[0]
    assert name == NAME
    assert heading == "Cycle 303"
    assert open(path, "rb").read() == RAW
    assert "image/jpeg" in detail and str(len(RAW)) in detail


def test_a_link_the_vault_does_not_have_is_reported_not_skipped(tmp_path):
    """The whole point: a dead link becomes visible instead of invisible."""
    text = f"![gone](/api/upload/{NAME})\n"
    results = fetch_attachments.fetch(text, str(tmp_path), getter=store({}))
    assert len(results) == 1
    assert results[0][2] is None
    assert results[0][3] == "not in the vault"


def test_vault_tool_not_found_output_counts_as_absent(tmp_path, monkeypatch):
    """`vault_tool.py get` prints `[not found: ...]` and exits 0."""
    calls = []

    class Proc:
        returncode = 0
        stdout = "[not found: projects/...]\n"
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return Proc()

    monkeypatch.setattr(fetch_attachments.subprocess, "run", fake_run)
    results = fetch_attachments.fetch(f"![x](/api/upload/{NAME})\n", str(tmp_path))
    assert calls, "the tool must actually shell out to the vault client"
    assert results[0][2] is None
    assert results[0][3] == "not in the vault"


def test_a_corrupt_envelope_is_reported_not_written(tmp_path):
    text = f"![x](/api/upload/{NAME})\n"
    results = fetch_attachments.fetch(
        text, str(tmp_path),
        getter=store({UPLOAD_PREFIX + NAME: "content-type: image/jpeg\n\n!!!not base64!!!\n"}),
    )
    assert results[0][2] is None
    assert results[0][3] == "envelope did not decode"
    assert list(tmp_path.iterdir()) == []


def test_a_link_that_is_not_an_upload_name_is_refused_without_a_vault_call(tmp_path):
    """`is_upload_name` is the path guard -- `..` must never reach the vault."""
    def explode(path):
        raise AssertionError(f"must not fetch {path}")

    results = fetch_attachments.fetch(
        "![x](/api/upload/..)\n", str(tmp_path), getter=explode,
    )
    assert results[0][3] == "not an upload name"


def test_main_exits_1_when_something_could_not_be_fetched(tmp_path, monkeypatch, capsys):
    source = tmp_path / "comments.md"
    source.write_text(f"### Cycle 303\n\n![x](/api/upload/{NAME})\n", encoding="utf-8")
    monkeypatch.setattr(
        fetch_attachments, "_vault_get", lambda path, vault_tool: None,
    )
    code = fetch_attachments.main([str(source), "--dir", str(tmp_path / "out")])
    assert code == 1
    assert "FAILED" in capsys.readouterr().out


def test_main_exits_0_and_says_so_when_there_are_none(tmp_path, capsys):
    source = tmp_path / "notes.md"
    source.write_text("- just a note\n", encoding="utf-8")
    assert fetch_attachments.main([str(source), "--dir", str(tmp_path / "out")]) == 0
    assert "no attachments" in capsys.readouterr().out


@pytest.mark.parametrize("ext", sorted(set(CONTENT_TYPES.values())))
def test_every_extension_the_upload_side_can_store_is_one_this_side_can_find(ext):
    """The two halves drift the moment someone adds a format to one of them."""
    name = "c" * 32 + "." + ext
    assert fetch_attachments.find_links(f"![x](/api/upload/{name})") == [(name, "")]
    from agora_runner.nova_uploads import is_upload_name
    assert is_upload_name(name)
