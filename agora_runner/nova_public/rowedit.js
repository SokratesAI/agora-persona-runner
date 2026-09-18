/* The held-row editor on the owner's boards (issue #233).
 *
 * The seventeenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js`,
 * `models.js`, `richtext.js`, `bubble.js` and `askthread.js`: what a board row
 * turns into when it is held -- the title box (`renderRowEditor`), the
 * project picker with `New project…` (`renderProjectPicker`, and the cached
 * project index behind it, `loadProjects`) -- plus the conversation drawn
 * under a row's write-up (`renderRowConversation`) and `HOLD_MS`, the hold
 * that opens the editor.
 *
 * It borrows five names and hands four back. `renderBlocks` is the only one
 * held as a value, and `richtext.js` is bound far above the spot this block
 * used to sit in `app.js`, so it is bound there, in place.
 */
(function () {
  "use strict";

  window.novaRowEdit = function (shared) {
    var el = shared.el;
    var json = shared.json;
    var loadBoard = shared.loadBoard;
    var ownerLabel = shared.ownerLabel;
    var renderBlocks = shared.renderBlocks;

    /* the owner, issue #84: *"If i hold the card for more than 1 second i get
     * into edit mode"*. His number, not a tuned one. */
    var HOLD_MS = 1000;

    /* Every project name on either board, fetched once per page load.
     *
     * `/api/project` with no name returns only the index, and it is built
     * from the two cached board payloads the page has usually already paid
     * for, so this is cheap. It is memoised on the promise rather than on
     * the value so that opening three rows in a row makes one request, not
     * three -- and deliberately not cached across a save: a name typed into
     * `New project…` has to appear in the next row's list, and that is what
     * `forgetProjects` is for. */
    var projectsPromise = null;
    function loadProjects() {
      if (!projectsPromise) {
        projectsPromise = fetch("/api/project")
          .then(json)
          .then(function (payload) { return (payload && payload.projects) || []; })
          .catch(function () {
            // A failed index is not a failed editor. The picker still offers
            // the row's own project and `New project…`, which is enough to
            // move a row -- so this degrades to typing rather than to an
            // error the owner cannot act on.
            projectsPromise = null;
            return [];
          });
      }
      return projectsPromise;
    }
    function forgetProjects() { projectsPromise = null; }

    /* The project cell of one boarded row, as a control in the held-row
     * editor.
     *
     * the owner, capture 2026-09-01, rated 🔴 Immediately: *"I/you should
     * easily be able to assign issues and ideas to projects, and change
     * project if assigned wrongly ... I/you should easily be able to create
     * new projects."* Both halves are this one control: the list moves a row
     * between projects that exist, and `New project…` creates one, because
     * the server derives the project list from the cells rather than from a
     * document. There is nothing else to create.
     *
     * It saves on change rather than on Save, matching the priority picker
     * beside it -- the only action the control can take is the one just
     * chosen. The Save button next to it belongs to the title box and
     * pressing it must not also rewrite a project cell the owner only
     * scrolled past. */
    function renderProjectPicker(board, item, onSaved) {
      var wrap = el("div", "item-edit-project");
      var label = el("label", "item-edit-project-label", "Project");
      var select = el("select", "item-edit-project-select");
      var typed = el("input", "item-edit-project-new");
      var status = el("span", "item-edit-status");
      typed.type = "text";
      typed.placeholder = "New project name";
      typed.hidden = true;
      typed.setAttribute("aria-label", "New project for #" + item.number);
      select.setAttribute("aria-label", "Project of #" + item.number);
      // A sentinel that cannot collide with a real project: `set_row_project`
      // refuses a `|` outright, so no cell on either board can ever hold this.
      var NEW = "|new";
      var current = (item.project || "").trim();

      function fill(names) {
        select.textContent = "";
        var seen = [];
        // The row's own project first and always, even when the index did
        // not come back -- a picker that cannot show where the row is now
        // reads as if the row has no project.
        if (current) seen.push(current);
        (names || []).forEach(function (name) {
          if (seen.indexOf(name) === -1) seen.push(name);
        });
        seen.forEach(function (name) {
          var option = el("option", "", name);
          option.value = name;
          if (name === current) option.selected = true;
          select.appendChild(option);
        });
        var creator = el("option", "", "New project…");
        creator.value = NEW;
        select.appendChild(creator);
      }

      /* The index arrives one round trip after the editor is drawn, and the
       * editor can be gone by then -- he cancels, or the page navigates. A
       * `fill` on a detached control is not merely wasted: `el()` reaches
       * for `document`, which in a closed window is gone, so the late
       * callback throws an unhandled rejection with no test and no user
       * anywhere near it. Two existing browser tests reported exactly that
       * before this guard. `isConnected` is the honest condition -- the
       * question is whether there is still a page to paint into. */
      function fillIfLive(names) {
        if (!select.isConnected) return;
        fill(names);
      }

      fill([]);
      loadProjects().then(fillIfLive);

      function save(name) {
        status.className = "item-edit-status";
        status.textContent = "Saving…";
        select.disabled = true;
        typed.disabled = true;
        return fetch("/api/board/project", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: board, number: item.number, project: name })
        })
          .then(json)
          .then(function (payload) {
            if (!payload || !payload.ok) throw new Error((payload && payload.message) || "failed");
            // `current` and not `item.project`: the row object belongs to the
            // board payload the page is holding, and the reload below is what
            // replaces it. Writing the new name back into it looks harmless
            // and is how a picker on a *second* row would show a project that
            // is only true because this one saved.
            current = name;
            status.textContent = "";
            // A name that did not exist a moment ago is a project now, so
            // the next picker on this page has to see it.
            forgetProjects();
            onSaved();
          })
          .catch(function (err) {
            status.textContent = "Could not save: " + String((err && (err.message || err)) || err);
            status.className = "item-edit-status is-error";
            select.disabled = false;
            typed.disabled = false;
            // Back to what the server still holds, never to the name that
            // was not written -- the same snap-back the priority picker does.
            fill([]);
            loadProjects().then(fillIfLive);
            typed.hidden = true;
            typed.value = "";
          });
      }

      select.addEventListener("change", function () {
        if (select.value === NEW) {
          typed.hidden = false;
          typed.value = "";
          typed.focus();
          return;
        }
        typed.hidden = true;
        if (select.value && select.value !== current) save(select.value);
      });

      function commitTyped() {
        var name = typed.value.trim();
        // An empty box is a cancelled create, not a request to blank the
        // cell -- `set_row_project` refuses an empty name anyway, so this
        // stops a guaranteed 502 rather than adding a rule.
        if (!name) {
          typed.hidden = true;
          fill([]);
          loadProjects().then(fillIfLive);
          return;
        }
        if (name === current) { typed.hidden = true; return; }
        save(name).then(function () { typed.hidden = true; });
      }
      typed.addEventListener("blur", commitTyped);
      typed.addEventListener("keydown", function (e) {
        if (e.key === "Enter") { e.preventDefault(); commitTyped(); }
      });

      wrap.appendChild(label);
      wrap.appendChild(select);
      wrap.appendChild(typed);
      wrap.appendChild(status);
      return wrap;
    }

    /* The edit-mode panel a held row turns into: the title in a box, and
     * save, cancel and delete.
     *
     * **A boarded row's title lives in three places and this only shows
     * one of them.** The table cell is what the card renders; the wiki-link
     * beside it and the `### #84 — ...` heading over the write-up repeat
     * the same words for Obsidian's benefit. The server moves all three
     * together, which is why this posts a title rather than a patch -- the
     * page has no business knowing that his file says it three times.
     */
    function renderRowEditor(board, item, done) {
      var panel = el("div", "item-edit");
      var box = el("textarea", "item-edit-input");
      box.value = item.title || "";
      box.rows = 2;
      box.setAttribute("aria-label", "Title of #" + item.number);
      var actions = el("div", "item-edit-actions");
      var status = el("span", "item-edit-status");
      var save = el("button", "capture-act", "Save");
      var cancel = el("button", "capture-act", "Cancel");
      var archive = el("button", "capture-act", "Archive");
      var del = el("button", "capture-act is-danger", "Delete");
      save.type = "button";
      cancel.type = "button";
      archive.type = "button";
      del.type = "button";

      function busy(on, label) {
        save.disabled = on;
        cancel.disabled = on;
        archive.disabled = on;
        del.disabled = on;
        status.className = "item-edit-status";
        status.textContent = on ? label : "";
      }

      function send(url, body) {
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
            // Repaint from the file rather than patching the node: a row
            // that has just been retitled or removed is not the row this
            // closure was built from.
            loadBoard(board);
          })
          .catch(function (err) {
            status.textContent = String((err && (err.message || err)) || "failed");
            status.className = "item-edit-status is-error";
            busy(false, "");
          });
      }

      save.addEventListener("click", function () {
        var next = box.value.trim();
        // Emptying the box is not how a row is deleted -- there is a
        // button for that and it asks first, the same rule the capture
        // editor follows.
        if (!next || next === item.title) { done(); return; }
        busy(true, "saving…");
        send("/api/board/edit", { target: board, number: item.number, title: next });
      });
      cancel.addEventListener("click", done);
      del.addEventListener("click", function () {
        // The one thing on this page that cannot be undone from the page.
        // A native confirm blocks the accident in one line; #6's modal is
        // a different item and building half of it here would be worse
        // than either.
        if (!window.confirm("Delete #" + item.number + "?\n\n" + (item.title || ""))) return;
        busy(true, "deleting…");
        send("/api/board/delete", { target: board, number: item.number });
      });
      // No confirm, unlike Delete: this leaves the row, its number and its
      // write-up in his file and is undone by setting the status back.
      archive.addEventListener("click", function () {
        busy(true, "archiving…");
        send("/api/board/archive", { target: board, number: item.number });
      });

      actions.appendChild(status);
      actions.appendChild(save);
      actions.appendChild(cancel);
      actions.appendChild(archive);
      actions.appendChild(del);
      panel.appendChild(box);
      // Between the title and the buttons, because it belongs to the row
      // rather than to the title edit -- it has already saved by the time
      // Save is pressed, and sitting above the buttons is what says so.
      panel.appendChild(renderProjectPicker(board, item, function () { loadBoard(board); }));
      panel.appendChild(actions);
      return { el: panel, focus: function () { box.focus(); } };
    }

    /* The conversation appended under a board row's write-up, as the same
     * green-and-purple bubbles the notes page and the capture box use.
     *
     * The owner, `issues.md` 2026-08-26: *"i see that boarded issues does not
     * have those nice colored comments like there are now in the 'not boarded
     * yet' box, so take the best from both worlds here."* His comment and my
     * answer are appended into the row's own write-up as dated `**<author>,
     * 08-26:**` lines, so until now the page drew all three voices -- his
     * statement of the problem, his later question, my reply -- as one column
     * of identical paragraphs. `nova_boards.split_detail_conversation` is
     * what tells them apart; nothing in the file changed.
     *
     * `note-msg-mine` / `note-msg-nova` verbatim, not a board-specific
     * variant: the whole ask is that the two pages read alike, and a second
     * pair of colour rules is how they stop doing that a month from now.
     */
    function renderRowConversation(container, comments) {
      (comments || []).forEach(function (message) {
        var mine = message.author !== "Nova";
        var msg = el("article", "note-msg " + (mine ? "note-msg-mine" : "note-msg-nova"));
        var who = el("p", "note-msg-who");
        who.appendChild(el("span", "note-msg-name", ownerLabel(message.author) || "Nova"));
        // The stamp as written -- `08-26`, or `08-26 (Cycle 462)` when a
        // cycle wrote it. Re-deriving the cycle here is the duplication this
        // repo keeps filing against itself; `append_detail_note` owns it.
        // No rule of its own in style.css on purpose: `.note-msg-who` is
        // already the small dim flex row the notes page uses for exactly
        // this, and a second rule restating what it inherits is one more
        // thing to keep in step for no gain.
        if (message.stamp) who.appendChild(el("span", "note-msg-when", message.stamp));
        msg.appendChild(who);
        var text = el("div", "note-msg-body");
        renderBlocks(text, message.blocks || []);
        msg.appendChild(text);
        container.appendChild(msg);
      });
    }

    return {
      HOLD_MS: HOLD_MS,
      loadProjects: loadProjects,
      renderRowConversation: renderRowConversation,
      renderRowEditor: renderRowEditor,
    };
  };
})();
