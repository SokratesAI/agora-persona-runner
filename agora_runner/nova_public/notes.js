/* The Notes page (issue #233, step 16).
 *
 * The eighth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js` and
 * `beats.js`. `/notes` is his conversation with Nova in `notes.md`: the
 * transcript, the hold menu on each message, and the capture box moved in
 * under it. Nothing else in `app.js` reaches into it -- the router calls
 * `loadNotes` and that is the whole seam back.
 *
 * What it needs from `app.js` arrives as one argument, the same seam every
 * module before it uses: a name missing from the list is a
 * `ReferenceError` on the first line that uses it rather than a page that
 * half draws. Every name it borrows is a function or a value `app.js`
 * assigns in its first forty lines, so it is bound where the page used to
 * be defined.
 */
(function () {
  "use strict";

  window.novaNotes = function (shared) {
    var OWNER_LABEL = shared.OWNER_LABEL;
    var bindHoldMenu = shared.bindHoldMenu;
    var buildCaptureEditor = shared.buildCaptureEditor;
    var captureHome = shared.captureHome;
    var closeActionSheet = shared.closeActionSheet;
    var convertButtons = shared.convertButtons;
    var el = shared.el;
    var feed = shared.feed;
    var fetchPage = shared.fetchPage;
    var json = shared.json;
    var load = shared.load;
    var loadWhenScrolledTo = shared.loadWhenScrolledTo;
    var markNav = shared.markNav;
    var openActionSheet = shared.openActionSheet;
    var renderBlocks = shared.renderBlocks;
    var route = shared.route;
    var savedCopyLine = shared.savedCopyLine;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* The Notes page.
     *
     * the owner, issues.md 2026-08-21: *"I do not have a notes page that shows
     * any overview of the notes made."*
     *
     * His third capture file had a button that writes to it and nothing
     * that reads it back, so a note he had left was invisible from the app
     * the moment he tapped save.
     *
     * Deliberately not a board. A note is *"never numbered, never boarded"*
     * (`notes.md`'s own contract), so there is no priority chip, no row
     * editor and no comment thread here.
     *
     * **It is a conversation now**, which is the second thing he asked for
     * -- `notes.md` 2026-08-24: *"I want the notes page to be more like a
     * conversation. So that alternating posts are green (mine) and purple
     * (Nova cycle response). Just like the comments. The page should have
     * the conversation above the input box for issues/ideas/notes and be
     * ordered with the latest note at the bottom. And when i navigate to
     * it, it should not start at the top and i have to scroll all the way
     * down, but like a message app like agora where i can scroll upwards.
     * Messages are lazy loaded so when i scroll up they load so it loads
     * faster. I want to use the notes page to have a "conversation" with
     * the cycles, even though it takes some time to get a response."*
     *
     * Five separate things, and each one is somewhere below:
     *
     * 1. Green for him, purple for a cycle -- the same two colours the
     *    comment threads already use (`--good` and `--nova`), so the app
     *    says the same thing the same way in both places. Both sides carry
     *    the speaker's name in words as well, because a reader who has to
     *    know a colour code to know who spoke has not been told.
     * 2. Oldest at the top, newest at the bottom. `nova_notes.notes_payload`
     *    does the ordering; this file does not re-derive it.
     * 3. The composer below the transcript rather than above it. It is the
     *    shell's one `#capture` section, moved into the feed for this page
     *    only and moved home by `load()` on the way out -- one box with one
     *    set of handlers, not a second copy that would drift from it.
     * 4. Opens at the bottom, on the newest message.
     * 5. Older messages arrive as he scrolls up.
     *
     * On 5, and this is a deliberate narrowing of what he asked for: the
     * *fetch* is not windowed, the *render* is. `notes.md` is 17KB and the
     * server sends all of it in one response, which is not the slow part of
     * anything -- `nova_notes.notes_payload`'s own docstring measured that
     * and it is still true. What he described is a page that opens on the
     * newest message instead of the oldest, and that is a scroll position
     * plus a render window, both of which live here. When the file does
     * outgrow one fetch, `/api/notes` takes a `limit` and this loop asks
     * for one; today that parameter would be a cap with nothing measured
     * behind it.
     */

    // How many messages the page opens with, and how many more each scroll
    // to the top reveals. Not a limit on anything -- every note is one
    // scroll away and `notesShown` only ever grows.
    var NOTES_PAGE = 12;
    var notesShown = NOTES_PAGE;
    var notesPayload = null;
    var notesThread = null;

    function renderNoteMessage(note) {
      var msg = el("article", "note-msg note-msg-mine" + (note.waiting ? " note-msg-waiting" : ""));
      var who = el("p", "note-msg-who");
      who.appendChild(el("span", "note-msg-name", OWNER_LABEL));
      // "Waiting" is the one piece of state a note has that he cannot see
      // from the transcript itself: a note with no purple reply under it is
      // either unanswered or answered badly, and only the file knows which.
      if (note.waiting) who.appendChild(el("span", "badge badge-warn", "Waiting"));
      msg.appendChild(who);
      var body = el("div", "note-msg-body");
      renderBlocks(body, note.blocks || []);
      msg.appendChild(body);
      var out = [msg];
      (note.responses || []).forEach(function (response) {
        var reply = el("article", "note-msg note-msg-nova");
        var head = el("p", "note-msg-who");
        head.appendChild(el("span", "note-msg-name", "Nova"));
        // The cycle that answered, taken from the reply itself rather than
        // re-derived here -- `nova_notes._response_cycle` owns the shape of
        // a reply line and a second reading of it in this file is the
        // duplication this repo keeps filing against itself.
        if (response.cycle !== null && response.cycle !== undefined) {
          var link = el("a", "note-msg-cycle", "Cycle " + response.cycle);
          link.href = "/cycle/" + response.cycle;
          head.appendChild(link);
        }
        reply.appendChild(head);
        var text = el("div", "note-msg-body");
        renderBlocks(text, response.blocks || []);
        reply.appendChild(text);
        out.push(reply);
      });
      // A note moved under `## Read` with nothing written under it is a
      // real state -- half the contract done -- and saying so beats a
      // transcript that just goes quiet.
      if (!note.waiting && !(note.responses || []).length) {
        out.push(el("p", "note-reply-missing", "Moved to Read with no reply written."));
      }
      /* Edit, delete and convert -- but only while nothing has acted on it.
       *
       * `note.index` is the capture-list position, and the server sets it
       * to `null` for anything the edit/delete/convert endpoints cannot address: every note
       * under `## Read`, and any waiting note whose two parsers disagreed.
       * Rewriting a note a cycle has already answered would leave the reply
       * underneath it answering text that no longer exists, so the missing
       * index is the right answer rather than a limitation to work around.
       *
       * The two boards have had these since issues #66; the notes page was
       * built without them and that is the gap he hit -- *"i have no way of
       * changing it or editing it"*. */
      if (note.waiting && typeof note.index === "number") {
        msg.appendChild(noteActions(note, body));
      }
      return out;
    }

    function noteActions(note, holdTarget) {
      var actions = el("div", "capture-edit note-acts");
      var status = el("span", "capture-item-status");
      var editBtn = el("button", "capture-act", "Edit");
      var delBtn = el("button", "capture-act is-danger", "Delete");
      editBtn.type = "button";
      delBtn.type = "button";

      function busy(on) { [editBtn, delBtn].forEach(function (b) { b.disabled = on; }); }
      function fail(err) {
        status.textContent = String((err && (err.message || err)) || "failed");
        status.className = "capture-item-status is-error";
        // Same as the board captures: the row is hidden at rest, and a
        // reason painted into a hidden node is a tap that looks ignored.
        actions.hidden = false;
        busy(false);
      }
      // Repaint from the file, never patch the node: the same rule the board
      // captures follow, and here it also re-derives every remaining note's
      // index, which every deletion above it has just shifted.
      function reload() { loadNotes(); }

      function send(url, body) {
        status.textContent = "saving…";
        status.className = "capture-item-status";
        // The board captures' `send` got this and this one did not -- a
        // straight asymmetry, and "saving…" written into a hidden row is a
        // tap with nothing to show for it.
        actions.hidden = false;
        busy(true);
        return fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            reload();
          })
          .catch(fail);
      }

      editBtn.addEventListener("click", function () {
        closeActionSheet();
        // The raw markdown, not the rendered blocks -- saving something
        // untouched has to be a no-op rather than a reformat of his line.
        var editor = buildCaptureEditor(note.text || "");
        var box = editor.box;
        var save = el("button", "capture-act", "Save");
        var cancel = el("button", "capture-act", "Cancel");
        save.type = "button";
        cancel.type = "button";
        actions.hidden = false;
        actions.textContent = "";
        actions.appendChild(editor.el);
        actions.appendChild(status);
        actions.appendChild(save);
        actions.appendChild(cancel);
        editor.focus();
        save.addEventListener("click", function () {
          var next = box.value.trim();
          // Emptying the box is not how a note is deleted -- there is a
          // button for that, and it asks first.
          if (!next) { box.focus(); return; }
          save.disabled = true;
          cancel.disabled = true;
          send("/api/capture/edit", {
            target: "notes", index: note.index, original: note.text, text: next,
          });
        });
        cancel.addEventListener("click", reload);
      });

      delBtn.addEventListener("click", function () {
        closeActionSheet();
        if (!window.confirm("Delete this note?\n\n" + (note.text || ""))) return;
        send("/api/capture/delete", { target: "notes", index: note.index, original: note.text });
      });

      var converts = convertButtons("notes", note.index, note.text, reload, fail, busy);
      converts.forEach(function (b) {
        b.addEventListener("click", function () { closeActionSheet(); });
      });

      // Same shape as a board capture: the row survives as the place a
      // failure and the edit box land, and is hidden until one of those
      // needs it. His words were "do this for issues, ideas and notes", so
      // the third surface uses the same gesture and the same sheet.
      actions.appendChild(status);
      actions.hidden = true;

      var sheetButtons = [editBtn].concat(converts, [delBtn]);
      if (holdTarget) {
        bindHoldMenu(holdTarget, function (fromGesture) {
          /* Not while the editor is open. A board capture gets this for free
           * -- its editor *replaces* the held node -- but a note's editor is
           * a sibling in `actions`, so the message stays right above the box
           * and stays holdable. Reopening would call `actions.textContent =
           * ""` and rebuild the box from `note.text`, throwing away
           * everything typed, with no confirm. Found reviewing the merged
           * diff. */
          if (actions.querySelector(".capture-input")) return;
          openActionSheet("Note", sheetButtons, { swallowNextClick: fromGesture });
        });
        // Same as a board capture, and the reason bites harder here: a
         // `role="button"` with a label on every waiting message would make
         // the transcript itself unreadable to a screen reader.
        holdTarget.classList.add("capture-hold");
        holdTarget.tabIndex = 0;
        holdTarget.setAttribute("aria-keyshortcuts", "Enter Space");
      }
      return actions;
    }

    /* The composer, moved under the transcript for this page only.
     *
     * `#capture` is a single section in `index.html`, above the feed on
     * every page, and `captureBox()` binds its handlers once at startup. So
     * this moves that node rather than building a second one: two composers
     * would need two sets of handlers, and the second copy is the drift
     * this repo keeps filing against itself. `captureHome()` in `load()` is
     * the other half -- every navigation puts it back before the next page
     * clears the feed out from under it.
     */
    function moveCaptureInto(parent) {
      var capture = document.getElementById("capture");
      if (capture) parent.appendChild(capture);
    }

    function renderNotes(payload, options) {
      var opts = options || {};
      stopPolling();
      markNav();
      notesPayload = payload;
      var notes = payload.notes || [];
      var waiting = payload.waitingTotal || 0;
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el(
        "p",
        "status-line",
        waiting === 1
          ? "1 note waiting for a cycle to pick it up"
          : waiting + " notes waiting for a cycle to pick them up"
      ));
      if (payload.replayed) statusEl.appendChild(savedCopyLine());
      /* **Before the feed is emptied, not after.** `load()` calls
       * `captureHome()` on every navigation, which covers arriving here
       * and leaving -- and misses the case this page creates for itself:
       * `showOlderNotes` re-renders in place, with the composer already
       * inside the feed from the render before it, so `textContent = ""`
       * detaches the one composer the whole app has and `moveCaptureInto`
       * below then finds nothing to put back. It is gone from every page
       * until a reload. Scrolling up is the central interaction of this
       * feature, so that was the dominant path through it. Found by the
       * reviewer, which reproduced it in a real DOM rather than reasoning
       * about it, after I had merged. The rule the two calls make together
       * is worth stating once: **the composer is outside the feed whenever
       * the feed is cleared, without exception.** */
      captureHome();
      /* Under Preact the kept thread stays attached: taking it out of the
       * document and putting it back would blur an Edit box he is typing in
       * and close his keyboard, even though the box itself survives. */
      var kept = window.novaThread && notes.length && notesThread && notesThread.parentNode === feed
        ? notesThread : null;
      Array.prototype.slice.call(feed.childNodes).forEach(function (n) {
        if (n !== kept) feed.removeChild(n);
      });
      if (!notes.length) {
        feed.appendChild(el("p", "empty", "No notes yet. Type below and tap Note."));
        moveCaptureInto(feed);
        return;
      }
      if (notesShown > notes.length) notesShown = notes.length;
      var first = notes.length - notesShown;
      var window_ = notes.slice(first);
      /* Under Preact the thread is one element kept for the life of the page,
       * and its rows go through `thread.js` (issue #233, step 6): a note whose
       * content did not change keeps the node already on screen, so "Load
       * older notes" adds the older ones above instead of rebuilding every
       * note -- and an Edit he had open stays open. A note is keyed by its
       * place in the whole list, which the older ones do not shift. */
      var rows = window.novaThread ? [] : null;
      var thread = rows && notesThread ? notesThread : el("div", "note-thread");
      if (rows) notesThread = thread;
      var add = function (node, key, sig) {
        if (rows) rows.push({ node: node, key: key, sig: sig });
        else thread.appendChild(node);
      };
      if (notesShown < notes.length) {
        /* The scroll-up handle. A button as well as a scroll trigger, on
         * purpose: an IntersectionObserver that fires on its own is the
         * lazy load he asked for, and a tappable control is what still
         * works when it does not -- the same belt-and-braces the board
         * pager uses. */
        var older = el("button", "more note-older", "Load older notes");
        older.type = "button";
        older.addEventListener("click", showOlderNotes);
        // NaN never equals itself, so the pager is always the fresh one and
        // the watcher below is never pointed at a node that is not drawn.
        add(older, "older", NaN);
        watchForOlderNotes(older);
      } else {
        add(el("p", "note-start", "The beginning of our notes."), "start", NaN);
      }
      window_.forEach(function (note, i) {
        var sig = JSON.stringify(note);
        renderNoteMessage(note).forEach(function (node, part) {
          add(node, "n" + (first + i) + "." + part, sig);
        });
      });
      if (rows) window.novaThread.render(thread, rows);
      if (thread.parentNode !== feed) feed.appendChild(thread);
      moveCaptureInto(feed);
      // Opening at the bottom is the point of the whole page -- "it should
      // not start at the top and i have to scroll all the way down". Not on
      // a re-render that grew the window, though: that would throw him back
      // to the newest message the instant he reached the oldest one.
      if (!opts.keepScroll) scrollNotesToLatest();
    }

    function scrollNotesToLatest() {
      // Twice: once now, and once after the layout that follows the images
      // and fonts settling. A single call lands short on a phone, which
      // reads as "it still starts in the wrong place".
      var toBottom = function () {
        window.scrollTo(0, document.documentElement.scrollHeight);
      };
      toBottom();
      window.setTimeout(toBottom, 0);
    }

    function showOlderNotes() {
      if (!notesPayload) return;
      // The same guard `loadNotes` puts on its own fetch, for the same
      // reason and one layer further in: this is reached from a click, and
      // a click can be dispatched at a detached button by anything that
      // kept a reference to it. Repainting the conversation over another
      // page is the one thing that must not happen, so it is refused here
      // as well as prevented in `load()`.
      if (route(window.location.pathname).view !== "notes") return;
      var notes = notesPayload.notes || [];
      if (notesShown >= notes.length) return;
      // Keep his eye on the message he was reading: the document grows
      // upwards, so scroll down by exactly the height that appeared above
      // him. Without this, revealing older notes silently teleports him.
      var before = document.documentElement.scrollHeight;
      var at = window.pageYOffset || document.documentElement.scrollTop || 0;
      notesShown += NOTES_PAGE;
      renderNotes(notesPayload, { keepScroll: true });
      var grew = document.documentElement.scrollHeight - before;
      window.scrollTo(0, at + grew);
    }

    /* `loadWhenScrolledTo` is the journal's own infinite-scroll helper and
     * this is deliberately the same mechanism pointed the other way -- the
     * journal watches a pager at the bottom of the feed for older entries,
     * this watches one at the top. Sharing it rather than writing a second
     * observer means the notes pager inherits its disconnect-before-click
     * and its one-live-observer rule, both of which took a reviewer to get
     * right the first time. */
    function watchForOlderNotes(node) {
      loadWhenScrolledTo(node);
    }

    function loadNotes() {
      // Every fresh open starts on the newest window, which is what the
      // page promises -- `notesShown` only grows, so without this a visit
      // after a scroll-up session would render 36 messages and call it
      // "the newest handful".
      notesShown = NOTES_PAGE;
      fetchPage("/api/notes")
        .then(function (payload) {
          // The guard the retro, costs and plan fetches carry: two taps in
          // quick succession leave two fetches in flight and the loser must
          // not paint over the winner.
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
