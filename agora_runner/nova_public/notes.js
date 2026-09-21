/* The Notes page, on note records (idea #333).
 *
 * the owner, idea #333: *"Rebuild the /notes page as real note records
 * (CouchDB, like board-records.md) with comments -- create/read/edit/
 * archive/delete a note, swipe left to archive, archived-notes list."*
 *
 * Each note is a card with the Journal card's markup and classes (`entry`,
 * `entry-head`, `entry-meta`), a sender's name on the note and on every
 * comment under it, newest note first. Reads are `/api/notes/records`;
 * every write is `/api/notes/<action>`, which decides the author from the
 * port the request came in on and never from the body. Edit, archive and
 * delete send the `rev` the page read, so a stale tab gets a 409 and
 * re-reads instead of overwriting a newer change.
 *
 * What it needs from `app.js` arrives as one argument, the same seam every
 * split-out module uses: a name missing from the list is a `ReferenceError`
 * on the first line that uses it rather than a page that half draws.
 */
(function () {
  "use strict";

  window.novaNotes = function (shared) {
    var bindHoldMenu = shared.bindHoldMenu;
    var buildCaptureEditor = shared.buildCaptureEditor;
    var captureHome = shared.captureHome;
    var closeActionSheet = shared.closeActionSheet;
    var el = shared.el;
    var feed = shared.feed;
    var fetchPage = shared.fetchPage;
    var markNav = shared.markNav;
    var openActionSheet = shared.openActionSheet;
    var route = shared.route;
    var savedCopyLine = shared.savedCopyLine;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    // Same thresholds as the chat list's swipe (`chat-dock.js`), so one
    // gesture means one distance everywhere in the app.
    var SWIPE_ARM_PX = 12;
    var SWIPE_ARCHIVE_PX = 96;

    var showArchived = false;

    function noteWrite(action, body) {
      return fetch("/api/notes/" + action, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (result) {
          if (!r.ok) throw new Error((result && result.error) || ("HTTP " + r.status));
          return result;
        });
      });
    }

    function stamp(iso) {
      if (!iso) return "";
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleString("en-GB", {
        timeZone: "Europe/Oslo", day: "numeric", month: "short",
        hour: "2-digit", minute: "2-digit",
      });
    }

    function noteText(text) {
      var body = el("div", "note-text");
      String(text || "").split(/\n{2,}/).forEach(function (para) {
        if (para.trim()) body.appendChild(el("p", null, para));
      });
      return body;
    }

    function renderComment(comment) {
      var row = el("div", "note-comment");
      var head = el("div", "note-comment-head");
      head.appendChild(el("strong", "note-sender", comment.author));
      head.appendChild(el("time", "stamp", stamp(comment.created)));
      row.appendChild(head);
      row.appendChild(noteText(comment.text));
      return row;
    }

    function noteCommentBox(note, fail) {
      var form = el("form", "note-comment-form");
      var input = el("input", "capture-input");
      input.type = "text";
      input.placeholder = "Comment";
      input.setAttribute("aria-label", "Comment on this note");
      var send = el("button", "capture-act", "Send");
      send.type = "submit";
      form.appendChild(input);
      form.appendChild(send);
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = input.value.trim();
        if (!text) return;
        send.disabled = true;
        noteWrite("comment", { id: note.id, text: text })
          .then(loadNotes)
          .catch(function (err) { send.disabled = false; fail(err); });
      });
      return form;
    }

    function swipeToArchive(card, note, fail) {
      var startX = 0;
      var startY = 0;
      var dx = 0;
      var sliding = false;
      var decided = false;

      function reset() {
        card.style.transition = "transform 160ms ease";
        card.style.transform = "";
        card.classList.remove("note-card--armed");
        setTimeout(function () { card.style.transition = ""; }, 180);
        sliding = false;
        decided = false;
        dx = 0;
      }

      function move(e) {
        if (!sliding) return;
        dx = e.clientX - startX;
        if (!decided) {
          // Vertical wins ties: the page scrolls, and a scroll that turns
          // into an archive because the finger drifted is the failure.
          var dy = Math.abs(e.clientY - startY);
          if (Math.abs(dx) < SWIPE_ARM_PX && dy < SWIPE_ARM_PX) return;
          if (dy > Math.abs(dx)) { end(); return; }
          decided = true;
        }
        if (e.preventDefault) e.preventDefault();
        card.style.transform = "translateX(" + Math.min(0, dx) + "px)";
        card.classList.toggle("note-card--armed", dx <= -SWIPE_ARCHIVE_PX);
      }

      function end() {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        if (dx > -SWIPE_ARCHIVE_PX) { reset(); return; }
        card.style.transition = "transform 160ms ease, opacity 160ms ease";
        card.style.transform = "translateX(-110%)";
        card.style.opacity = "0";
        noteWrite("archive", { id: note.id, rev: note.rev, archived: true })
          .then(loadNotes)
          .catch(function (err) { card.style.opacity = ""; reset(); fail(err); });
      }

      card.addEventListener("pointerdown", function (e) {
        if (e.button !== undefined && e.button !== 0) return;
        if (e.target && e.target.closest && e.target.closest("button, a, textarea, input")) return;
        startX = e.clientX;
        startY = e.clientY;
        dx = 0;
        sliding = true;
        decided = false;
        window.addEventListener("pointermove", move, { passive: false });
        window.addEventListener("pointerup", end);
        window.addEventListener("pointercancel", end);
      });
    }

    function renderNoteCard(note) {
      var card = el("article", "entry note-card");
      card.id = "note-" + note.id.replace(/[^A-Za-z0-9_-]/g, "-");
      var head = el("header", "entry-head");
      head.appendChild(el("h2", "note-sender", note.author));
      card.appendChild(head);
      var meta = el("div", "entry-meta");
      meta.appendChild(el("time", "stamp", stamp(note.created)));
      card.appendChild(meta);
      var body = noteText(note.text);
      card.appendChild(body);

      var status = el("p", "capture-item-status is-error");
      status.hidden = true;
      function fail(err) {
        status.textContent = String((err && (err.message || err)) || "failed");
        status.hidden = false;
      }

      var comments = el("div", "note-comments");
      (note.comments || []).forEach(function (c) { comments.appendChild(renderComment(c)); });
      card.appendChild(comments);

      if (note.archived) {
        var restore = el("button", "capture-act", "Un-archive");
        restore.type = "button";
        restore.addEventListener("click", function () {
          restore.disabled = true;
          noteWrite("archive", { id: note.id, rev: note.rev, archived: false })
            .then(loadNotes)
            .catch(function (err) { restore.disabled = false; fail(err); });
        });
        card.appendChild(restore);
      } else {
        card.appendChild(noteCommentBox(note, fail));
        swipeToArchive(card, note, fail);
      }
      card.appendChild(status);

      var editBtn = el("button", "capture-act", "Edit");
      var delBtn = el("button", "capture-act is-danger", "Delete");
      editBtn.type = "button";
      delBtn.type = "button";
      editBtn.addEventListener("click", function () {
        closeActionSheet();
        var editor = buildCaptureEditor(note.text || "");
        var save = el("button", "capture-act", "Save");
        var cancel = el("button", "capture-act", "Cancel");
        save.type = "button";
        cancel.type = "button";
        var box = el("div", "capture-edit");
        box.appendChild(editor.el);
        box.appendChild(save);
        box.appendChild(cancel);
        card.replaceChild(box, body);
        editor.focus();
        save.addEventListener("click", function () {
          var next = editor.box.value.trim();
          // Emptying the box is not how a note is deleted.
          if (!next) { editor.box.focus(); return; }
          save.disabled = true;
          noteWrite("edit", { id: note.id, rev: note.rev, text: next })
            .then(loadNotes)
            .catch(function (err) { save.disabled = false; fail(err); });
        });
        cancel.addEventListener("click", loadNotes);
      });
      delBtn.addEventListener("click", function () {
        closeActionSheet();
        if (!window.confirm("Delete this note?\n\n" + (note.text || ""))) return;
        noteWrite("delete", { id: note.id, rev: note.rev })
          .then(loadNotes)
          .catch(fail);
      });
      bindHoldMenu(card, function (fromGesture) {
        if (card.querySelector(".capture-edit")) return;
        openActionSheet("Note", [editBtn, delBtn], { swallowNextClick: fromGesture });
      });
      return card;
    }

    function noteComposer() {
      var form = el("form", "entry note-compose");
      var editor = buildCaptureEditor("");
      var status = el("p", "capture-item-status is-error");
      status.hidden = true;
      var send = el("button", "capture-act", "Save note");
      send.type = "submit";
      form.appendChild(editor.el);
      form.appendChild(send);
      form.appendChild(status);
      form.addEventListener("submit", function (e) {
        e.preventDefault();
        var text = editor.box.value.trim();
        if (!text) { editor.box.focus(); return; }
        send.disabled = true;
        // The text stays in the box until the store has it, so a refused
        // write is a message beside what he typed, never a lost note.
        noteWrite("create", { text: text })
          .then(loadNotes)
          .catch(function (err) {
            send.disabled = false;
            status.textContent = String((err && (err.message || err)) || "failed");
            status.hidden = false;
          });
      });
      return form;
    }

    function renderNotes(payload) {
      stopPolling();
      markNav();
      // The shared capture box goes home rather than into this page: a note
      // is written by this page's own composer, straight into the store.
      captureHome();
      var notes = payload.notes || [];
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      var toggle = el("button", "capture-act note-archive-toggle",
        showArchived ? "Back to notes" : "Archived notes");
      toggle.type = "button";
      toggle.addEventListener("click", function () {
        showArchived = !showArchived;
        loadNotes();
      });
      statusEl.appendChild(toggle);
      if (payload.replayed) statusEl.appendChild(savedCopyLine());
      feed.textContent = "";
      if (!showArchived) feed.appendChild(noteComposer());
      if (!notes.length) {
        feed.appendChild(el("p", "empty", showArchived ? "No archived notes." : "No notes."));
        return;
      }
      notes.forEach(function (note) { feed.appendChild(renderNoteCard(note)); });
    }

    function loadNotes() {
      fetchPage("/api/notes/records" + (showArchived ? "?archived=1" : ""))
        .then(function (payload) {
          // Two taps in quick succession leave two fetches in flight, and
          // the loser must not paint over the winner.
          if (route(window.location.pathname).view !== "notes") return;
          renderNotes(payload);
        })
        .catch(function (err) {
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the notes: " + err));
        });
    }

    return {
      loadNotes: loadNotes,
    };
  };
})();
