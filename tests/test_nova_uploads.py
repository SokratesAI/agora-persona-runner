"""Image uploads: the round trip, the refusals, and whether the button exists.

The last one is not padding. When I picked this work up it was a complete,
careful `nova_uploads.py` plus a complete `buildAttach` in `app.js` that
**nothing ever called** -- the helper was defined, documented, and wired to
no composer, so every byte of the server side was reachable only by curl.
`test_attach_button_is_wired_into_both_composers` is the guard for exactly
that, because it is the failure a green Python suite cannot see.
"""

import base64
import hashlib
import os
import re
from unittest.mock import patch

import pytest

from agora_runner import nova_uploads
from agora_runner.nova_uploads import (
    CONTENT_TYPES,
    UPLOAD_PREFIX,
    UploadRejected,
    read_upload,
    store_upload,
)

APP_JS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "agora_runner", "nova_public", "app.js",
)
STYLE_CSS = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "agora_runner", "nova_public", "style.css",
)

# A real 1x1 PNG, not `b"junk"`. The point of storing bytes is that they
# come back byte-identical, and a payload that is not a valid image would
# still round-trip -- so it would pass a test that proves less.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


@pytest.fixture
def store():
    """A dict standing in for the vault, honouring `if_rev=None`."""
    written = {}

    def write(path, body, if_rev="__unset__"):
        if if_rev is None and path in written:
            return "FAILED: conflict, document exists"
        written[path] = body
        return "written: " + path

    def read(path):
        return written.get(path)

    with patch.object(nova_uploads, "vault_write_path", side_effect=write), \
         patch.object(nova_uploads, "vault_read_path", side_effect=read):
        yield written


def test_round_trip_returns_the_exact_bytes(store):
    name, url, size, ctype = store_upload("shot.png", "image/png", base64.b64encode(PNG).decode())

    assert size == len(PNG)
    assert ctype == "image/png"
    assert url == "/api/upload/" + name
    assert read_upload(name) == ("image/png", PNG)


def test_name_is_the_content_hash_so_the_same_image_stores_once(store):
    encoded = base64.b64encode(PNG).decode()
    first, _, _, _ = store_upload("a.png", "image/png", encoded)
    second, _, _, _ = store_upload("b-different-filename.png", "image/png", encoded)

    assert first == second == hashlib.sha256(PNG).hexdigest()[:32] + ".png"
    # The second write is refused by `if_rev=None` and that refusal is
    # success, not an error -- which is the whole reason the name is a hash.
    assert list(store) == [UPLOAD_PREFIX + first]


def test_a_data_url_is_accepted_as_the_browser_produces_it(store):
    """`FileReader.readAsDataURL` is one call; an ArrayBuffer walk is not."""
    payload = "data:image/png;base64," + base64.b64encode(PNG).decode()
    name, _, size, _ = store_upload("shot.png", "image/png", payload)

    assert size == len(PNG)
    assert read_upload(name) == ("image/png", PNG)


def test_it_lands_in_novas_database_not_edvards(store):
    """A 2MB screenshot must not replicate onto the phone it was taken with.

    `COUCHDB_NOVA_DB` is unset under pytest, and `db_for` short-circuits to
    one database when it is -- so comparing two `db_for` answers directly
    passes whatever the prefix is, which is a positive result guaranteed in
    advance. The routing is pinned with the split configured instead.
    """
    from agora_runner import vault

    store_upload("shot.png", "image/png", base64.b64encode(PNG).decode())
    path = next(iter(store))
    assert path.startswith(UPLOAD_PREFIX)
    assert UPLOAD_PREFIX.startswith(vault.NOVA_DB_FOLDERS)

    with patch.object(vault, "COUCHDB_NOVA_DB", "nova"), \
         patch.object(vault, "COUCHDB_DB", "obsidian"):
        assert vault.db_for(path) == "nova"
        # His three capture files are the ones that must stay on the phone.
        assert vault.db_for("projects/sokrates/projects/nova/issues.md") == "obsidian"


@pytest.mark.parametrize(
    "filename, content_type, data, expected",
    [
        ("x.png", "image/png", "not base64!!", "not valid base64"),
        ("x.png", "image/png", "", "must be a base64 string"),
        ("x.png", "image/png", None, "must be a base64 string"),
        ("x.png", "image/png", base64.b64encode(b"").decode(), "must be a base64 string"),
    ],
)
def test_malformed_uploads_are_refused_with_a_readable_reason(
    store, filename, content_type, data, expected
):
    with pytest.raises(UploadRejected) as raised:
        store_upload(filename, content_type, data)
    assert expected in str(raised.value)
    assert store == {}


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..%2Fsecret.png",
        "projects/sokrates/projects/nova/issues.md",
        "nothashed.png",
        # These four end in a real image extension, which is what makes
        # them the cases that matter. Every traversal string above is
        # already refused by the extension check alone -- I deleted the
        # hex-and-length check as a mutation and all 28 tests still
        # passed, so the line was pinned by nothing until these landed.
        "../../../../nova/resources/inbox.png",
        "../issues.png",
        "..%2f..%2fsecret.png",
        "a/b.png",
        "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ.png",       # right length, not hex
        "0123456789abcdef0123456789abcde.png",        # 31 hex characters
        "0123456789abcdef0123456789abcdef.toolongext",  # 10-character extension
        "0123456789abcdef0123456789abcdef.p n g",     # spaces are not an extension
        "0123456789abcdef0123456789abcdef",           # no extension
        "",
        None,
    ],
)
def test_read_upload_refuses_anything_that_is_not_a_name_it_wrote(store, name):
    """Checked against the shape `store_upload` produces, never sanitised.

    A name is a hash and an extension. Refusing outright is cheaper than
    reasoning about what `..` means to a CouchDB `_id`, and it means a path
    that would escape the prefix cannot be *repaired* into one that does not.

    **The plant is what makes this test able to fail.** Without it every
    name here returns `None` because the fake store is empty, so deleting
    the hex-and-length check under mutation changed nothing and 32 tests
    stayed green -- a negative result guaranteed in advance. Putting a
    readable document at the exact path the traversal resolves to is what
    turns "refused" into a claim rather than a coincidence.
    """
    if name:
        store[UPLOAD_PREFIX + name] = (
            "content-type: image/png\nfilename: reachable\n\n"
            + base64.b64encode(PNG).decode()
            + "\n"
        )
    assert read_upload(name) is None


def test_a_stored_document_with_a_bad_envelope_reads_as_missing(store):
    """A truncated or hand-edited document is absent, not a 500."""
    name = "0123456789abcdef0123456789abcdef.png"
    store[UPLOAD_PREFIX + name] = "content-type: image/png\nfilename: x\n\n!!!not base64!!!\n"
    assert read_upload(name) is None

    store[UPLOAD_PREFIX + name] = "content-type: text/html\n\naGk=\n"
    assert read_upload(name) is None


def test_a_real_write_failure_is_not_swallowed_as_a_duplicate():
    """The `if_rev=None` refusal means "already stored". Nothing else does."""
    def write(path, body, if_rev="__unset__"):
        return "FAILED: the vault is unreachable"

    with patch.object(nova_uploads, "vault_write_path", side_effect=write), \
         patch.object(nova_uploads, "vault_read_path", return_value=None):
        with pytest.raises(UploadRejected) as raised:
            store_upload("x.png", "image/png", base64.b64encode(PNG).decode())
    assert "unreachable" in str(raised.value)


def _app_js():
    with open(APP_JS, encoding="utf-8") as handle:
        return handle.read()


def test_attach_button_is_wired_into_both_composers():
    """The guard for the gap that made every server-side test above moot.

    `buildAttach` returns a node. A node nobody appends is not a feature,
    and no Python test can tell the difference. Edvard named the four
    places he wants it -- *"next to a comment, issue, note or idea"* -- and
    two composers cover all four: the comment drawer, and the capture box
    whose three buttons are Issue, Idea and Note.
    """
    source = _app_js()
    # The definition matches `buildAttach(` too, so it is subtracted rather
    # than pattern-dodged -- this counts call sites, and there are two.
    calls = source.count("buildAttach(") - source.count("function buildAttach(")
    assert calls == 2, f"expected both composers to build one, found {calls}"

    # Defined once, and the returned button actually reaches the DOM.
    assert source.count("function buildAttach(") == 1
    assert "appendChild(attach.button)" in source
    assert "insertBefore(captureAttach.button" in source

    # The hidden <input type=file> has to be in the document too -- a
    # detached one still opens a picker in Chrome and never fires `change`
    # in some engines, which is the kind of bug that only shows on a phone.
    assert "appendChild(attach.input)" in source
    assert "appendChild(captureAttach.input)" in source


def test_an_attached_image_is_rendered_back_in_the_comment_thread():
    """Attaching without rendering would show him the raw markdown line."""
    source = _app_js()
    assert source.count("function appendRichText(") == 1
    # His comment, and Nova's reply quoting it back.
    assert source.count("appendRichText(") >= 3


def test_only_our_own_upload_urls_become_images():
    """A pasted `![](javascript:…)` stays the text it is.

    Nothing in `app.js` touches innerHTML, so this is not an injection
    guard so much as a promise that the one markdown construct this file
    understands is the one it writes itself.
    """
    source = _app_js()
    pattern = re.search(r"var ATTACH_RE = (/.+/g);", source)
    assert pattern, "the attach pattern moved -- this test pins its shape"
    # The literal carries JS escapes (`\/`), so the prefix is compared with
    # them stripped rather than by looking for a substring that is not there.
    assert "/api/upload/" in pattern.group(1).replace("\\", "")


def test_the_attach_button_has_styles_and_a_touch_target():
    with open(STYLE_CSS, encoding="utf-8") as handle:
        css = handle.read()
    for selector in (".attach-btn", ".attach-img", ".attach-link"):
        assert selector + " {" in css, f"{selector} is used by app.js and unstyled"
    rule = css.split(".attach-btn {", 1)[1].split("}", 1)[0]
    # 44px is the touch-target minimum the rest of this stylesheet holds to.
    assert "min-height: 44px" in rule and "min-width: 44px" in rule


def test_the_content_type_allowlist_covers_what_an_android_phone_produces():
    """Edvard is on a Galaxy S25 (comments board 2026-08-21), not iOS."""
    assert {"image/png", "image/jpeg", "image/webp", "image/heic"} <= set(CONTENT_TYPES)
    # Every extension is distinct, or two content types would collide on
    # one name and `read_upload` would hand back the wrong header.
    assert len(set(CONTENT_TYPES.values())) == len(CONTENT_TYPES)


# --- Files that are not images (Cycle 309) ------------------------------
#
# Edvard, comments board 2026-08-21 21:09: *"How about a file? It seems i
# only can upload images. Or atleas the ui forces only my Google photos to
# open and i have no option to upload files."* Two bugs behind that one
# sentence -- the picker was pinned to `image/*` in `app.js`, and
# `store_upload` refused every content type that was not an image. These
# pin the server half.

PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


def test_a_pdf_stores_and_reads_back_as_a_pdf(store):
    name, url, size, ctype = store_upload(
        "invoice.pdf", "application/pdf", base64.b64encode(PDF).decode())

    assert ctype == "application/pdf"
    assert size == len(PDF)
    assert name.endswith(".pdf")
    assert url == "/api/upload/" + name
    assert read_upload(name) == ("application/pdf", PDF)
    assert nova_uploads.is_image(ctype) is False


@pytest.mark.parametrize(
    "filename, declared, expected",
    [
        # Android's picker reports nothing at all for these two.
        ("notes.md", "", "text/markdown"),
        ("runner.log", "", "text/plain"),
        # ...and `application/octet-stream` for plenty it could have named.
        # Taking that as given would store a readable PDF as an opaque blob.
        ("invoice.pdf", "application/octet-stream", "application/pdf"),
        ("shot.jpeg", "application/octet-stream", "image/jpeg"),
        # A type it does name is taken as given.
        ("data.csv", "text/csv", "text/csv"),
        # Nothing recognisable either way is still stored, as a download.
        ("report.docx", "", "application/octet-stream"),
        ("mystery", "", "application/octet-stream"),
    ],
)
def test_the_type_is_resolved_from_the_extension_when_the_phone_sends_nothing(
        store, filename, declared, expected):
    _, _, _, ctype = store_upload(filename, declared, base64.b64encode(PDF).decode())
    assert ctype == expected


@pytest.mark.parametrize("declared", ["text/html", "image/svg+xml", "application/xml"])
def test_a_type_the_browser_would_execute_is_stored_as_a_download(store, declared):
    """Script in an uploaded `.html` or `.svg` would run as *this* origin.

    The old images-only allowlist blocked these as a side effect. Widening
    it makes the block have to be deliberate, so this is the test that says
    the widening did not quietly open an XSS on the Nova site: the file is
    still accepted -- he can send one -- and it comes back as
    `application/octet-stream`, which a browser downloads rather than runs.
    """
    name, _, _, ctype = store_upload("page." + declared[-3:], declared,
                                     base64.b64encode(b"<script>alert(1)</script>").decode())
    assert ctype == "application/octet-stream"
    assert read_upload(name)[0] == "application/octet-stream"


def test_the_stored_name_keeps_his_own_extension(store):
    """`<hash>.docx`, not `<hash>.bin`.

    The stored name is what the browser saves the download as, so an
    extension resolved from the *type* table would hand him a file his
    phone cannot open by tapping it. The type table is only the fallback
    for something that arrived with no extension at all.
    """
    encoded = base64.b64encode(PDF).decode()
    named, _, _, _ = store_upload("report.docx", "", encoded)
    assert named.endswith(".docx")

    bare, _, _, _ = store_upload("mystery", "", base64.b64encode(b"other bytes").decode())
    assert bare.endswith(".bin")


def test_the_same_bytes_under_two_spellings_of_one_extension_store_once(store):
    """`photo.jpg` and `photo.jpeg` are one document, not two.

    Reviewer finding on #283, and a regression I introduced into *existing*
    image behaviour rather than a gap in the new one. Keeping the sender's
    extension made the stored name a function of his filename as well as of
    the bytes, which is exactly what the content-addressed name exists to
    stop. `test_name_is_the_content_hash_so_the_same_image_stores_once`
    could not catch it: both filenames there share one extension.
    """
    encoded = base64.b64encode(PNG).decode()
    first, _, _, _ = store_upload("photo.jpg", "image/jpeg", encoded)
    second, _, _, _ = store_upload("photo.jpeg", "image/jpeg", encoded)
    assert first == second, "two spellings of one extension stored twice"
    assert len(store) == 1, "the vault holds one document per distinct byte string"

    # Case is part of the same question: `_extension_of` lowercases, so
    # `NOTES.MD` and `notes.md` are one name and not two.
    text = base64.b64encode(b"# notes\n").decode()
    third, _, _, _ = store_upload("NOTES.MD", "", text)
    fourth, _, _, _ = store_upload("notes.md", "text/markdown", text)
    assert third == fourth
    assert len(store) == 2


@pytest.mark.parametrize(
    "line, folds",
    [
        ("![shot.png](/api/upload/" + "a" * 32 + ".png)", True),
        ("[report.docx](/api/upload/" + "a" * 32 + ".docx)", True),
        ("here is a file [x](/api/upload/" + "a" * 32 + ".pdf)", False),
        ("[remote](https://example.com/api/upload/x.pdf)", False),
    ],
)
def test_a_file_attachment_folds_onto_the_capture_above_it_like_an_image_does(line, folds):
    """Cycle 307 stopped an attached image filing as its own capture bullet.

    That fix keyed on `![...]`, so a *file* attachment -- which has no `!`
    -- would have reintroduced exactly the bug he reported, for everything
    that is not a picture. This is the half of the change that is invisible
    until he attaches a PDF to a sentence.
    """
    assert nova_uploads.is_attachment_line(line) is folds
