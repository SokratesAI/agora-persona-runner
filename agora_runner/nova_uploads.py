"""File uploads: the attachment Edvard has never had.

**Images first, then everything else.** This module was images-only until
2026-08-21 21:09, when Edvard tried to send something that was not one:
*"How about a file? It seems i only can upload images. Or atleas the ui
forces only my Google photos to open and i have no option to upload
files."* Both halves of that were true and they were separate bugs — the
picker was pinned to `image/*` in `app.js`, and the server refused any
content type that was not an image. Widening it is `resolve_content_type`
and `RENDERED_TYPES` below; the rest of this docstring is unchanged and
still describes where the bytes go.


Edvard, comments board 2026-08-21 12:07, trying to show me a bug he could
see and I could not: *"How do i send a screenshot?"* — and, three minutes
later, *"Then a good idea is to figure out the best way i can upload an
image. Maybe now is the time to bump the priority of me being able to
upload files next to a comment, issue, note or idea. Do this immediately
next cycle."*

**Where the bytes go, and why not the two obvious places.**

Not the site pod's disk: `nova-site` mounts one `emptyDir` at `/tmp` and
nothing else (measured 2026-08-21, `kubectl get deploy nova-site -n agents
-o jsonpath='{...volumes}'`). Every upload would die on the next rollout,
and this loop rolls that deployment several times a day.

Not a LiveSync binary document either, which is the tempting one because
the vault already holds PDFs. A binary attachment is `type: newnote` and
its chunks are base64'd *and padded independently*, so a whole-file
encoding is not the same string — `vault._size_checked` documents that
measurement (four PDFs, 662,428 assembled against a recorded 496,813) and
gives up its own length check rather than guess at an encoder this repo
does not vendor. Writing that format is the same guess in the direction
that actually damages something.

So an upload is a **`type: plain` text document** holding a header and
base64, written through `vault_write_path` — the one write path in this
repo that is tested, revision-guarded and collapse-guarded. The cost is
33% more stored bytes than the binary format. What it buys is that the
encoder is `base64.b64encode`, right here, and the read is its exact
inverse.

**It lands in Nova's database, not Edvard's.** The prefix routes to
`COUCHDB_NOVA_DB` (`vault.db_for`), so a 2MB screenshot does not
replicate onto the phone it was taken with, and his three capture files
stay text. What goes into *his* file is one markdown line pointing at
`/api/upload/<name>`.

**The name is the content hash**, so the same screenshot sent twice is
one document, and the URL can be cached forever by the browser without a
staleness question ever arising. It also means an upload is never
overwritten: `if_rev=None` means "this must not exist", and a second
write of identical bytes is skipped rather than raced.

**The one limit, and the danger behind it.** `MAX_UPLOAD_BYTES` exists
because `rfile.read(n)` allocates whatever `Content-Length` claims and
the runner pod's memory limit is 256Mi (measured 2026-08-09). It is not a
judgement about how big a screenshot should be — 12MiB of body is roughly
a 9MB image, which is above anything a Galaxy S25 produces, and the point
is that the number is bounded by a real ceiling rather than by taste.
"""

import base64
import binascii
import hashlib
import re

from agora_runner.vault import vault_read_path, vault_write_path

UPLOAD_PREFIX = "projects/sokrates/projects/agora/nova/resources/uploads/"

#: 12 MiB of request body, against the runner pod's measured 256Mi limit.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

#: What a phone camera or a screenshot tool actually produces. These are
#: the types the site renders inline as a picture; everything else is a
#: file, shown as a link.
IMAGE_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/avif": "avif",
}

#: Everything else Edvard might send. Edvard, comments board 2026-08-21
#: 21:09: *"How about a file? It seems i only can upload images. Or atleas
#: the ui forces only my Google photos to open and i have no option to
#: upload files."*
#:
#: The fallback is `application/octet-stream` rather than a refusal, so a
#: `.docx` or a `.log` uploads and downloads instead of being turned away
#: for not being on a list. What the table is still for is `RENDERED_TYPES`
#: below — a type that a browser *executes* in this site's own origin is
#: the one thing that must never be served back as itself.
FILE_TYPES = {
    "application/pdf": "pdf",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/csv": "csv",
    "application/json": "json",
    "application/zip": "zip",
    "application/octet-stream": "bin",
}

#: Every type a stored envelope may declare. Kept as one flat mapping
#: because `decode_envelope`, `is_upload_name` and `tools.fetch_attachments`
#: all ask the same question of it: is this a type this site wrote?
CONTENT_TYPES = {**IMAGE_TYPES, **FILE_TYPES}

#: Filename extension -> content type, for the case the browser gives us
#: nothing. Android's file picker reports `""` for `.md` and `.log` and
#: `application/octet-stream` for plenty else, so without this every
#: text file Edvard sends would be stored as an opaque blob.
EXTENSION_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "log": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "zip": "application/zip",
    **{ext: ctype for ctype, ext in IMAGE_TYPES.items()},
    "jpeg": "image/jpeg",
}

#: Types a browser will *run* if this origin hands them back — script in an
#: uploaded `.html` or `.svg` executes as the Nova site, with its cookies
#: and its API. These are stored (he can still send one) and served as
#: `application/octet-stream`, so the file downloads instead of running.
#: The old allowlist blocked these as a side effect of only allowing
#: images; widening it makes the block have to be deliberate.
RENDERED_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "application/xml",
    "text/xml",
}

#: A stored name's extension. Anything longer or stranger than this is not
#: an extension, and the name goes into a vault path, so it is checked
#: rather than sanitised.
EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")


class UploadRejected(ValueError):
    """The upload is malformed. The message is shown to Edvard verbatim."""


def _envelope(content_type, filename, encoded):
    return f"content-type: {content_type}\nfilename: {filename}\n\n{encoded}\n"


def _parse_envelope(text):
    head, _, body = text.partition("\n\n")
    fields = {}
    for line in head.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields, body.strip()


def _safe_filename(filename):
    """A display name, not a path. Nothing downstream opens a file by it."""
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c.isalnum() or c in "._- ")
    return name[:120] or "upload"


def _extension_of(filename):
    """The filename's own extension, lowercased, or `""` if it hasn't one."""
    _, dot, ext = (filename or "").rpartition(".")
    ext = ext.strip().lower()
    return ext if dot and EXTENSION.match(ext) else ""


def resolve_content_type(filename, content_type):
    """What to store this as, given what the browser claimed.

    Three cases, in the order they actually happen on a phone. A known type
    is taken as given. A blank or unknown one is looked up by extension,
    which is the `.md`/`.log` case Android reports as `""`. Anything still
    unresolved is `application/octet-stream` — a file this site will hand
    back but never interpret.

    `application/octet-stream` from the browser is deliberately *not* taken
    as given: Android sends it for files whose extension we can read
    perfectly well, and storing a PDF as an opaque blob would make it
    download rather than open.
    """
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type in RENDERED_TYPES:
        return "application/octet-stream"
    if content_type in CONTENT_TYPES and content_type != "application/octet-stream":
        return content_type
    guessed = EXTENSION_TYPES.get(_extension_of(filename), "")
    if guessed:
        return guessed
    return "application/octet-stream"


def is_image(content_type):
    """Does this render inline as a picture, or is it a file to download?"""
    return content_type in IMAGE_TYPES


def store_upload(filename, content_type, data_b64):
    """Write one file into the vault. Returns `(name, url, bytes, type)`.

    `data_b64` is the payload as the browser's `FileReader` produced it —
    either bare base64 or a full `data:` URL, because both are one line of
    client code apart and rejecting the wrong one is a round trip Edvard
    pays for on a phone.

    The fourth return value is the resolved content type, so the caller can
    tell the client whether to write an image link or a file link without
    re-deriving `resolve_content_type`'s answer from `filename`.
    """
    safe_name = _safe_filename(filename)
    content_type = resolve_content_type(safe_name, content_type)
    if not isinstance(data_b64, str) or not data_b64.strip():
        raise UploadRejected("data must be a base64 string")

    payload = data_b64.strip()
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    payload = "".join(payload.split())
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UploadRejected(f"data is not valid base64: {exc}") from exc
    if not raw:
        raise UploadRejected("data decoded to zero bytes")

    # Content-addressed, so the same screenshot sent twice is one document
    # and the URL is safe to cache forever. Truncated to 32 hex characters:
    # collisions are not an adversarial concern here and the name ends up
    # on Edvard's screen.
    digest = hashlib.sha256(raw).hexdigest()[:32]
    # His own extension wins over the table's default, because the stored
    # name is what the browser saves the download as: a `.docx` stored as
    # `<hash>.bin` is a file he cannot open by tapping it. The table is the
    # fallback for a file that arrived without one.
    name = f"{digest}.{_extension_of(safe_name) or CONTENT_TYPES[content_type]}"
    path = UPLOAD_PREFIX + name

    encoded = base64.b64encode(raw).decode("ascii")
    body = _envelope(content_type, safe_name, encoded)

    result = vault_write_path(path, body, if_rev=None)
    # `if_rev=None` means "this must not exist". For a content-addressed
    # name a refusal means the identical bytes are already stored, which is
    # success from the caller's side — it is the *point* of hashing the
    # name. Any other failure is real and has to surface.
    if isinstance(result, str) and result.startswith("FAILED"):
        if vault_read_path(path) is None:
            raise UploadRejected(result)
    return name, f"/api/upload/{name}", len(raw), content_type


#: A whole line that is nothing but an attachment link this site wrote on
#: Edvard's behalf — `![alt](/api/upload/<name>)` for an image and
#: `[alt](/api/upload/<name>)` for any other file, the two constructs
#: `buildAttach`'s `onInsert` inserts into a text box in `app.js`. It lives
#: here rather than in the caller because this module is the one that
#: *builds* that string (`store_upload` returns the URL half), so the
#: pattern and the thing it has to match cannot drift apart in a rename.
#: `app.js` carries its own copy (`ATTACH_RE`) and cannot share this one;
#: that one is anchored nowhere and matches inline, this one is anchored
#: and matches a line, so they are deliberately different questions.
#:
#: The `!` is optional, and that is the whole of what file attachments
#: changed here: without it a capture of a PDF would file as its own bullet
#: instead of folding onto the sentence above it — the bug Cycle 307 fixed
#: for images, reintroduced for everything else.
ATTACHMENT_LINE = re.compile(r"^!?\[[^\]]*\]\(/api/upload/[A-Za-z0-9._-]+\)$")


def is_attachment_line(line):
    """Is this line only a file the attach button inserted?

    Used to decide whether a line is a capture of its own or belongs to
    the one above it. Deliberately narrow: it matches the single construct
    this site generates and nothing Edvard types himself, so a plain
    markdown image, a remote URL or a stray `![` stays the ordinary text
    it has always been.
    """
    return bool(ATTACHMENT_LINE.match((line or "").strip()))


def is_upload_name(name):
    """Is `name` exactly the shape `store_upload` produces?

    A name comes off a URL path, so it is checked rather than sanitised. A
    name is a 32-hex-character hash and a short alphanumeric extension;
    anything else is not ours, and refusing is cheaper than reasoning about
    what `..` means to a CouchDB `_id`.

    The extension used to be checked against the type table. It cannot be
    any more — `store_upload` keeps the sender's own extension, so a name
    can legitimately end `.docx` or `.log`. The path guard does not weaken:
    the stem is still exactly 32 hex characters and the extension is still
    `[a-z0-9]{1,8}`, so `..`, `/` and every traversal shape are refused for
    the same reason they always were.
    """
    stem, _, ext = (name or "").rpartition(".")
    if not stem or not ext:
        return False
    if len(stem) != 32 or not all(c in "0123456789abcdef" for c in stem):
        return False
    return bool(EXTENSION.match(ext))


def decode_envelope(text):
    """`(content_type, raw_bytes)` for a stored envelope, or `None`.

    Split out so the one inverse of `store_upload`'s encoder is written
    once. `read_upload` reads the document through `agora_runner.vault`,
    which needs `COUCHDB_*` and therefore only works in the runner pod;
    `tools.fetch_attachments` reads the same document through
    `/app/bridge/vault_tool.py` from the bridge pod. Two readers, two sets
    of credentials, and exactly one decoder — a second copy of this would
    be the drift bug this repo keeps writing detectors for.

    **This is extraction plus one deliberate behaviour change.** The old
    `read_upload` returned `(content_type, b"")` for an envelope whose
    payload decoded to nothing; this returns `None`. `store_upload` refuses
    zero bytes, so no legitimately stored upload can hit it — but a caller
    that writes the result to disk would otherwise produce a 0-byte file
    and report it as a fetched image, which is a failure reported as
    success. Pinned by
    `test_an_envelope_that_decodes_to_nothing_is_a_failure_not_an_empty_file`.
    """
    if not text:
        return None
    fields, payload = _parse_envelope(text)
    content_type = fields.get("content-type", "")
    if content_type not in CONTENT_TYPES:
        return None
    try:
        raw = base64.b64decode("".join(payload.split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw:
        return None
    return content_type, raw


def read_upload(name):
    """`(content_type, raw_bytes)` for a stored upload, or `None`."""
    if not is_upload_name(name):
        return None
    return decode_envelope(vault_read_path(UPLOAD_PREFIX + name))
