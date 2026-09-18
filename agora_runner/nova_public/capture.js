/* One of the owner's captures on his board, as a card (issue #233).
 *
 * The twentieth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js`,
 * `models.js`, `richtext.js`, `bubble.js`, `askthread.js`, `rowedit.js`,
 * `cycle.js` and `replies.js`: `renderCapture`, the card a bare bullet at
 * the top of `issues.md`, `ideas.md`, `notes.md` or `proposed-projects.md`
 * becomes, with its priority chip, its hold menu and its edit box.
 *
 * It borrows ten names and hands one back. Eight of the ten are function
 * declarations, which are hoisted and never reassigned; `PRIORITY_SEP` is a
 * constant and `renderBlocks` comes from `richtext.js`, and both are set
 * above this block's old spot. The action sheet and the priority picker stay
 * in `app.js`: their overlays are assigned lazily and read there directly,
 * so they are borrowed as functions rather than moved.
 */
(function () {
  "use strict";

  window.novaCapture = function (shared) {
    var PRIORITY_SEP = shared.PRIORITY_SEP;
    var bindHoldMenu = shared.bindHoldMenu;
    var buildCaptureEditor = shared.buildCaptureEditor;
    var buildPrioPicker = shared.buildPrioPicker;
    var closeActionSheet = shared.closeActionSheet;
    var convertButtons = shared.convertButtons;
    var el = shared.el;
    var loadBoard = shared.loadBoard;
    var openActionSheet = shared.openActionSheet;
    var renderBlocks = shared.renderBlocks;

    function renderCapture(board, capture, index) {
      /* `capture.done` is the cycle that closed it, or "". It only paints
       * -- Edit and Delete keep working, because the marker is text in his
       * bullet and he is allowed to change his mind about it. */
      var one = el("div", "capture-item" + (capture.done ? " capture-item-done" : ""));
      var body = el("div", "capture-body");
      /* The rating, shown and editable the same way a boarded row's is.
       * The owner, issues.md #91: *"All unboarded issues and ideas should have
       * the priority status icon shown (as they do when its chosen) in the
       * left top corner, but pressing it should open the modal like it does
       * sin the issue cards."*
       *
       * This was a read-only `.chip` painted only when he had rated the
       * capture at typing time, so the window between typing something and a
       * cycle boarding it -- often hours, and exactly when his own sense of
       * how urgent it is has to survive until I read it -- was the one place
       * on the page a rating could not be given or changed. `chipStyle: true`
       * is the board row's trigger, "Unrated" chip and all, so the two read
       * alike and there is something to press when there is no rating yet.
       *
       * **A capture has nowhere to put a Priority cell, so the rating is the
       * leading glyph of the bullet** (`nova_boards.split_capture_priority`)
       * and setting one is an ordinary text edit. That is why this needs no
       * route of its own: it rebuilds the bullet and posts it to the same
       * `/api/capture/edit` the Edit button uses, address and all, so it
       * inherits that route's index-and-text check and its 409. `capture.body`
       * is the server's own glyph-stripped text, so the round trip never
       * stacks a second glyph on a bullet that already had one. */
      var prioPicker = buildPrioPicker({
        current: capture.priority || "",
        // Named per capture, as board rows are ("Priority of #57"), so a
        // screen reader on a page of several unrated captures can tell which
        // one a trigger belongs to -- every one of them otherwise announces
        // the identical "Priority, Unrated".
        //
        // `openMenu` also stores this string as the shared popup's
        // `dataset.openFor`, and I first wrote the comment here claiming
        // uniqueness was load-bearing for that. It is not, and I checked:
        // making every capture share one label changes no behaviour, because
        // the document-level outside-click handler closes the open menu
        // before the second trigger's own handler ever reads `openFor`.
        ariaLabel: "Importance of capture " + (index + 1),
        // A capture is in no project and no milestone yet, so the sentence
        // the board row gets would be false here -- this is the rating the
        // row inherits when the bullet is boarded, and nothing more.
        caption: "Importance the row inherits when this capture is boarded.",
        chipStyle: true,
        onPick: function (label) {
          /* The reason goes on screen, not just into a reverted chip. Every
           * other write on this row -- Edit, Delete, and the same picker on a
           * boarded row -- says why it failed, and the failure this one is
           * most likely to hit is the one `/api/capture/edit` was built to
           * expect: a cycle boarding these very bullets while he is looking
           * at them, which answers 409. Reverting in silence leaves "my tap
           * did not register", "the app is broken" and "reload, a cycle just
           * took this" looking identical. Found by review on #223. */
          status.textContent = "saving…";
          status.className = "capture-item-status";
          // The row is hidden at rest, and this message exists precisely so
          // an in-flight rating does not look like a tap that missed.
          actions.hidden = false;
          var rest = capture.body || "";
          if (!rest) {
            /* A bullet that is nothing but a glyph rewrites to the empty
             * string, which that route answers with a 400 "nothing to save"
             * -- it does not delete, deletion is `/api/capture/delete`. So
             * this is not a safety guard, it is a round trip that can only
             * fail; refusing here reverts the trigger without one. I shipped
             * this comment claiming the empty edit *would* delete the
             * capture, which is wrong about the server, and corrected it. */
            var err = new Error("nothing to rate");
            fail(err);
            return Promise.reject(err);
          }
          /* `label + PRIORITY_SEP`, not `label.split(" ")[0] + " "`. That
           * older form took the rating's first token, which was its glyph
           * while the labels carried one; after Cycle 268 it takes the word
           * and drops the colon, writing `High fix the thing` -- which
           * `nova_boards.split_capture_priority` reads as unrated prose,
           * because requiring the colon is the only thing standing between
           * a rating and a bullet that opens with the word "High". The
           * rating would have vanished and the word would have stayed in
           * his sentence, in his file, permanently. */
          var next = label ? label + PRIORITY_SEP + rest : rest;
          return fetch("/api/capture/edit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              target: board, index: index, original: capture.text, text: next,
            }),
          })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (result) {
              if (!result || !result.ok) {
                throw new Error((result && (result.message || result.error)) || "failed");
              }
              status.textContent = "";
              loadBoard(board);
            })
            // `fail` paints the reason and re-enables Edit/Delete; rethrowing
            // is what makes `buildPrioPicker` revert the chip, so the two
            // halves of "it did not save" happen together.
            .catch(function (err) { fail(err); throw err; });
        },
      });
      body.appendChild(prioPicker.el);
      // After the rating trigger, so #91's "left top corner" still holds.
      if (capture.done) {
        body.appendChild(el("span", "capture-done-chip", "Done · " + capture.done));
      }
      renderBlocks(body, capture.blocks || []);
      one.appendChild(body);

      /* A cycle's answer to this capture, as its own purple bubble.
       *
       * It used to be glued onto the end of his own sentence, because the
       * board payload folded the indented reply bullet into the capture
       * text -- so the card read as one paragraph that started in his voice
       * and finished in mine, and, worse, the *address* every write on this
       * card sends was that same glued string. `nova_capture.replace_capture`
       * matches on his sentence alone, so Edit, Delete and the priority chip
       * all failed on an answered capture with "that capture is no longer in
       * the list" (his `issues.md` capture, 2026-08-25, with the screenshot).
       * The notes page has drawn this correctly all along; this is the same
       * two colours and the same markup. */
      (capture.replies || []).forEach(function (reply) {
        var answer = el("article", "note-msg note-msg-nova capture-reply");
        var head = el("p", "note-msg-who");
        head.appendChild(el("span", "note-msg-name", "Nova"));
        answer.appendChild(head);
        var text = el("div", "note-msg-body");
        renderBlocks(text, reply || []);
        answer.appendChild(text);
        one.appendChild(answer);
      });

      var actions = el("div", "capture-edit");
      var status = el("span", "capture-item-status");
      var editBtn = el("button", "capture-act", "Edit");
      var delBtn = el("button", "capture-act is-danger", "Delete");
      editBtn.type = "button";
      delBtn.type = "button";

      function fail(err) {
        status.textContent = String((err && (err.message || err)) || "failed");
        status.className = "capture-item-status is-error";
        // The row is hidden while the card is at rest, and a failure is
        // exactly when it stops being at rest. Without this the reason is
        // written into a node nobody can see and the tap looks ignored.
        actions.hidden = false;
        [editBtn, delBtn].forEach(function (b) { b.disabled = false; });
      }

      function send(url, payload) {
        status.textContent = "saving…";
        status.className = "capture-item-status";
        actions.hidden = false;
        [editBtn, delBtn].forEach(function (b) { b.disabled = true; });
        return fetch(url, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            // The bullet has moved or gone; repaint from the file rather
            // than patching the node, so what is on screen is what is in
            // the vault.
            loadBoard(board);
          })
          .catch(fail);
      }

      editBtn.addEventListener("click", function () {
        closeActionSheet();
        // The textarea carries the raw markdown, not the rendered text --
        // an edit round-trips through the same field the vault stores, so
        // saving something untouched is a no-op rather than a reformat.
        var editor = buildCaptureEditor(capture.text || "");
        var box = editor.box;
        var save = el("button", "capture-act", "Save");
        var cancel = el("button", "capture-act", "Cancel");
        save.type = "button";
        cancel.type = "button";
        one.replaceChild(editor.el, body);
        actions.hidden = false;
        actions.textContent = "";
        actions.appendChild(status);
        actions.appendChild(save);
        actions.appendChild(cancel);
        editor.focus();
        save.addEventListener("click", function () {
          var next = box.value.trim();
          if (!next) {
            // Emptying the box is not how a capture is deleted -- there is
            // a button for that, and it asks first.
            box.focus();
            return;
          }
          save.disabled = true;
          cancel.disabled = true;
          status.textContent = "saving…";
          fetch("/api/capture/edit", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              target: board, index: index, original: capture.text, text: next,
            }),
          })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (result) {
              if (!result || !result.ok) {
                throw new Error((result && (result.message || result.error)) || "failed");
              }
              loadBoard(board);
            })
            .catch(function (err) {
              status.textContent = String((err && (err.message || err)) || "failed");
              status.className = "capture-item-status is-error";
              save.disabled = false;
              cancel.disabled = false;
            });
        });
        cancel.addEventListener("click", function () { loadBoard(board); });
      });

      delBtn.addEventListener("click", function () {
        closeActionSheet();
        // Deleting is the one thing here that cannot be undone from the
        // page, so it asks. This is not the confirmation modal of #6 -- a
        // native confirm is one line and blocks the accident, and building
        // a modal for it would be a different item's work done badly.
        if (!window.confirm("Delete this capture?\n\n" + (capture.text || ""))) return;
        send("/api/capture/delete", { target: board, index: index, original: capture.text });
      });

      var converts = convertButtons(
        board, index, capture.text,
        function () { loadBoard(board); },
        fail,
        function (busy) { [editBtn, delBtn].forEach(function (b) { b.disabled = busy; }); }
      );
      converts.forEach(function (b) {
        b.addEventListener("click", function () { closeActionSheet(); });
      });

      /* The row still exists and still holds the status line -- it is where
       * "copied to ideas, but could not remove it from notes" has to land,
       * and the sheet is gone by the time that answer comes back. What it
       * no longer holds by default is the buttons: they live in the sheet,
       * and `actions` is hidden until either an edit or a failure gives it
       * something to say. */
      actions.appendChild(status);
      actions.hidden = true;
      one.appendChild(actions);

      /* Turn this bullet into a numbered row on the board below it.
       *
       * The owner, capture 2026-08-26: *"they do no seem to just stay
       * forever in the 'not boarded yet' box as unrated. Thats not what the
       * box is for. This a re ideas you have not seen before and you pick it
       * up, prioritised them and make them as their own nice item like the
       * rest."*
       *
       * It sends no `priority`, which is not the same as sending none: the
       * server reads that as "keep whatever rating the capture carries",
       * and the chip on this card is how he sets one before tapping. A
       * picker here would be a second way to do a thing this card already
       * does one tap away.
       *
       * Like the convert buttons, it disables itself for the whole
       * in-flight fetch -- a double tap on a slow phone would otherwise
       * send a second promote, and the second one is refused as stale
       * rather than boarding a duplicate, but the refusal would land on
       * screen as an error for a thing that worked. */
      var boardBtn = el("button", "capture-act", "Board it");
      boardBtn.type = "button";
      var boarding = false;
      boardBtn.addEventListener("click", function () {
        closeActionSheet();
        if (boarding) return;
        boarding = true;
        boardBtn.disabled = true;
        [editBtn, delBtn].forEach(function (b) { b.disabled = true; });
        fetch("/api/capture/promote", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: board, index: index, original: capture.text }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            /* Repaint from the file: the bullet has left one block of this
             * page and a row has arrived in another, and only the vault
             * knows what both now say. */
            loadBoard(board);
          })
          .catch(function (err) {
            boarding = false;
            boardBtn.disabled = false;
            fail(err);
          });
      });

      // Delete stays last: it is the destructive one and nothing new should
      // grow between it and the edge his thumb aims at.
      var sheetButtons = [editBtn, boardBtn].concat(converts, [delBtn]);
      bindHoldMenu(body, function (fromGesture) {
        // Not while the editor is open -- `body` has been swapped out for
        // it, and offering Edit again would replace the box he is typing in.
        if (!one.contains(body)) return;
        openActionSheet("Capture", sheetButtons, { swallowNextClick: fromGesture });
      });
      /* Focusable, and deliberately not `role="button"`. This element is his
       * sentence, with the rating control and any links inside it, and an
       * `aria-label` on a `role="button"` replaces all of that as the
       * accessible name -- so the capture text would stop being reachable
       * to a screen reader in exchange for announcing a gesture it cannot
       * make. `aria-keyshortcuts` says the same thing without eating the
       * contents. Found reviewing the merged diff. */
      body.classList.add("capture-hold");
      body.tabIndex = 0;
      body.setAttribute("aria-keyshortcuts", "Enter Space");

      return one;
    }

    return {
      renderCapture: renderCapture,
    };
  };
})();
