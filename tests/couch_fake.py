"""A CouchDB test double that enforces the real revision rule.

Extracted from `test_vault_conditional_writes.py` (Cycle 142) so the
callers of the vault client can be tested the same honest way it is.

The distinction this module exists for: a fake that returns whatever
status the test asks for proves the code *branches* on 409. It does not
prove a 409 can ever happen -- so a caller that stops passing `if_rev`
keeps every such test green while silently overwriting whoever it raced.
Measured on Cycle 142: both of `nova_capture`'s write sites could drop
`if_rev` entirely and all 123 tests in its two suites still passed.

This one applies CouchDB's actual rule instead: a PUT whose `_rev` does
not match the stored one is rejected. Patch it in with
`patch.object(vault, "couch_req", couch.req)` and call the real code.
"""
import urllib.parse

from agora_runner import vault


class FakeCouch:
    """A CouchDB that enforces `_rev`, and counts the writes it accepted."""

    def __init__(self, docs=None):
        self.docs = dict(docs or {})
        self.rejected = 0
        self.accepted = 0
        self.reads = 0
        #: {nth file-doc read: fn(couch)} -- another writer landing just
        #: before that read is served. Read 1 is the caller's own; read 2
        #: is the lookup inside `_vault_put_raw`. **Two is the one that
        #: matters, and getting this wrong is why the first version of
        #: these tests passed against the bug.** An interloper that lands
        #: after read 2 is caught either way, because the unconditional
        #: path has already taken its revision by then -- so a test that
        #: interleaves there proves nothing about `if_rev`.
        self.interleave = {}
        #: {doc_id: status} -- a GET of that *file* doc is answered with this
        #: status instead of the document. Chunk reads and `_all_docs` are
        #: untouched, which is the point: the failure this models is one
        #: document's read failing while the database is otherwise up, so the
        #: chunk writes still succeed and the file PUT still reaches CouchDB.
        #: A fake that took the whole database down instead would fail at the
        #: chunk stage and never exercise the branch under test.
        self.unreadable = {}

    def _next_rev(self, doc_id):
        current = self.docs.get(doc_id, {}).get("_rev", "0-x")
        return f"{int(current.split('-')[0]) + 1}-x"

    def store(self, doc_id, doc):
        """Write bypassing the revision check -- 'the other writer'."""
        doc = dict(doc)
        doc["_rev"] = self._next_rev(doc_id)
        self.docs[doc_id] = doc
        return doc["_rev"]

    def req(self, method, path, body=None):
        _db, _, rest = path.partition("/")
        rest = urllib.parse.unquote(rest)
        if method == "POST" and rest.startswith("_all_docs"):
            return 200, {"rows": [
                {"key": k, "id": k, "value": {"rev": self.docs[k]["_rev"]},
                 "doc": dict(self.docs[k])}
                if k in self.docs else {"key": k, "error": "not_found"}
                for k in body["keys"]
            ]}
        if method == "GET":
            if not rest.startswith("h:"):
                self.reads += 1
                hook = self.interleave.pop(self.reads, None)
                if hook is not None:
                    hook(self)
                status = self.unreadable.get(rest)
                if status is not None:
                    return status, {"error": "server_error"}
            if rest in self.docs:
                return 200, dict(self.docs[rest])
            return 404, {"error": "not_found"}
        if method == "PUT":
            sent = (body or {}).get("_rev")
            held = self.docs.get(rest, {}).get("_rev")
            if sent != held:
                self.rejected += 1
                return 409, {"error": "conflict"}
            stored = {k: v for k, v in body.items() if k != "_rev"}
            stored["_rev"] = self._next_rev(rest)
            self.docs[rest] = stored
            self.accepted += 1
            return 201, {"ok": True}
        raise AssertionError(f"unexpected {method} {path}")

    def text(self, doc_id):
        doc = self.docs[doc_id]
        return "".join(self.docs[c]["data"] for c in doc.get("children", []))

    def seed(self, doc_id, content):
        """Put `content` at `doc_id` the way another writer would -- bypassing
        the revision check, and *advancing* the revision. Not advancing it is
        the one mistake that makes every test here pass for the wrong reason:
        a conflict is a moved revision, so a fake other-writer that leaves the
        revision alone conflicts with nobody."""
        ids = []
        for text in vault._split_chunks(content):
            chunk_id = vault._chunk_id_for(text.encode("utf-8"))
            self.docs.setdefault(
                chunk_id, {"_id": chunk_id, "data": text, "_rev": "1-x"})
            ids.append(chunk_id)
        self.store(doc_id, {"_id": doc_id, "path": doc_id, "children": ids,
                            "ctime": 1})
