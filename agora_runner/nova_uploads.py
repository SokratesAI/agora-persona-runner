"""Image uploads: the attachment Edvard has never had.

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

from agora_runner.vault import vault_read_path, vault_write_path

UPLOAD_PREFIX = "projects/sokrates/projects/agora/nova/resources/uploads/"

#: 12 MiB of request body, against the runner pod's measured 256Mi limit.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

#: What a phone camera or a screenshot tool actually produces. The
#: allowlist is not a security boundary — the site serves what it is
#: given and the browser sniffs anyway — it is what stops a typo in the
#: client sending `application/json` and getting a document nothing can
#: render back.
CONTENT_TYPES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/avif": "avif",
}


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


def store_upload(filename, content_type, data_b64):
    """Write one image into the vault. Returns `(name, url, bytes)`.

    `data_b64` is the payload as the browser's `FileReader` produced it —
    either bare base64 or a full `data:` URL, because both are one line of
    client code apart and rejecting the wrong one is a round trip Edvard
    pays for on a phone.
    """
    content_type = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type not in CONTENT_TYPES:
        raise UploadRejected(
            f"content type {content_type or '(none)'} is not one of "
            f"{sorted(CONTENT_TYPES)}"
        )
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
    name = f"{digest}.{CONTENT_TYPES[content_type]}"
    path = UPLOAD_PREFIX + name

    encoded = base64.b64encode(raw).decode("ascii")
    body = _envelope(content_type, _safe_filename(filename), encoded)

    result = vault_write_path(path, body, if_rev=None)
    # `if_rev=None` means "this must not exist". For a content-addressed
    # name a refusal means the identical bytes are already stored, which is
    # success from the caller's side — it is the *point* of hashing the
    # name. Any other failure is real and has to surface.
    if isinstance(result, str) and result.startswith("FAILED"):
        if vault_read_path(path) is None:
            raise UploadRejected(result)
    return name, f"/api/upload/{name}", len(raw)


def is_upload_name(name):
    """Is `name` exactly the shape `store_upload` produces?

    A name comes off a URL path, so it is checked rather than sanitised. A
    name is a 32-hex-character hash and a known extension; anything else is
    not ours, and refusing is cheaper than reasoning about what `..` means
    to a CouchDB `_id`.
    """
    stem, _, ext = (name or "").rpartition(".")
    if not stem or not ext:
        return False
    if len(stem) != 32 or not all(c in "0123456789abcdef" for c in stem):
        return False
    return ext in set(CONTENT_TYPES.values())


def decode_envelope(text):
    """`(content_type, raw_bytes)` for a stored envelope, or `None`.

    Split out so the one inverse of `store_upload`'s encoder is written
    once. `read_upload` reads the document through `agora_runner.vault`,
    which needs `COUCHDB_*` and therefore only works in the runner pod;
    `tools.fetch_attachments` reads the same document through
    `/app/bridge/vault_tool.py` from the bridge pod. Two readers, two sets
    of credentials, and exactly one decoder — a second copy of this would
    be the drift bug this repo keeps writing detectors for.
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
