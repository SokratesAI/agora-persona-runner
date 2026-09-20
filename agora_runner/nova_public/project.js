/* The Pool page and the project page (issue #233, step 14).
 *
 * The sixth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js` and `diag.js`, and the largest
 * one so far at ~96 KB. The two pages travel together because the project
 * page draws the pool inside itself: `projectBoardBuilder` renders a
 * project's own candidate deck through `renderPool`, so cutting between
 * them would leave `renderPool` in `app.js` with its only two callers in
 * here. Everything the owner reaches from a project card is in this file --
 * the standings list, the drawers, the milestones, the roadmap, the TRL
 * dots, the satisfaction and lifecycle buttons, the project conversation,
 * the backlog, and every drag handle that reorders any of it.
 *
 * What it needs from `app.js` arrives as one argument, the same seam
 * `chat-dock.js`, `charts.js` and `diag.js` use and for the same reason:
 * the list is something somebody has to add to on purpose, and a name
 * missing from it is a `ReferenceError` on the first line that uses it
 * rather than a page that half draws. `app.js` calls this at the point in
 * its own body where the pool page used to be defined, and takes back the
 * two names the router still reaches, `loadPool` and `loadProject`.
 *
 * Nothing here is called at boot -- both entry points are reached only
 * from `load()`, once per navigation.
 */
(function () {
  "use strict";

  window.novaProject = function (shared) {
    var OWNER_RECORD = shared.OWNER_RECORD;
    var el = shared.el;
    var feed = shared.feed;
    var fetchPage = shared.fetchPage;
    var json = shared.json;
    var load = shared.load;
    var markNav = shared.markNav;
    var renderRowConversation = shared.renderRowConversation;
    var route = shared.route;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* The Pool page -- idea #92, phase 1.
     *
     * The owner: *"the thing that sparkes this idea is to also have a list of
     * ideas that you have generated per project, and i can approve or comment
     * on these."*
     *
     * **One candidate at a time, not a list of ten.** A wall of ten is a page
     * he has to read before he can act on any of it, and the whole value here
     * is that steering what I work on costs one tap instead of a written
     * capture. So: one card, three buttons, and the next one slides in behind
     * it. `poolAt` is where he is in the deck and it survives a decision
     * because the decided candidate leaves the pool -- staying at the same
     * index really does show the next one.
     *
     * Skip is deliberately not a decision. It moves past a card without
     * writing anything, so a candidate he does not want to think about yet
     * stays in the pool rather than being pushed into the discarded pile for
     * lack of a third answer. */
    var poolAt = 0;

    /* Whether the History panel is open, hoisted out of `renderPoolHistory`'s
     * closure for the same reason `poolAt` is hoisted out of `renderPool`'s:
     * every decision re-runs `renderPool`, which clears `feed` and rebuilds the
     * whole subtree, so anything kept in the closure is thrown away at the one
     * moment he is most likely to want it -- straight after tapping Approve.
     * Reviewer finding on this PR.
     */
    var poolHistoryOpen = false;

    function poolChip(text, className) {
      return el("span", "pool-chip " + (className || ""), text);
    }

    function renderPoolCard(candidate, payload) {
      var card = el("article", "pool-card");
      var head = el("header", "pool-head");
      head.appendChild(el("h2", "pool-title", candidate.title));
      var chips = el("div", "pool-chips");
      if (candidate.project) chips.appendChild(poolChip(candidate.project, "pool-project"));
      if (candidate.priority) {
        // `priorityKey` comes from the server for the reason board rows get
        // theirs there: the browser-side mapping lives inside a closure this
        // page cannot reach, and a second copy would drift. The word rides
        // with the glyph, which is the 2026-08-19 correction -- a colour he
        // cannot tell apart is not a signal.
        chips.appendChild(poolChip(
          candidate.priority, "chip prio prio-" + (candidate.priorityKey || "")));
      }
      head.appendChild(chips);
      card.appendChild(head);

      if (candidate.body) card.appendChild(el("p", "pool-body", candidate.body));

      /* Anything he has already noted on this candidate, oldest first. It is
       * drawn before the box rather than after it so the card reads as a
       * short thread: my proposal, then what he said about it, then the box
       * he says the next thing in. */
      (candidate.comments || []).forEach(function (said) {
        var line = el("p", "pool-said");
        if (said.dated) line.appendChild(el("span", "pool-said-when", said.dated));
        line.appendChild(document.createTextNode(said.text));
        card.appendChild(line);
      });

      var note = el("p", "pool-note");
      var box = el("textarea", "pool-comment");
      box.setAttribute("placeholder", "Why, or what you would change (optional)");
      box.setAttribute("rows", "2");
      card.appendChild(box);

      // Every other mutating trigger on this page disables itself while its
      // write is outstanding, and this one did not until a reviewer said so.
      // It is not cosmetic: two taps are two requests on two threads, both
      // pass the server's staleness check before either has emptied the
      // pool, and the loser of the compare-and-swap re-reads and boards the
      // same idea under a second number. The server is idempotent about that
      // now; this stops the second request being sent at all.
      var buttons = [];
      function decide(decision) {
        buttons.forEach(function (b) { b.disabled = true; });
        note.textContent = decision === "approve" ? "Boarding…" : "Discarding…";
        fetch("/api/pool/decide", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            index: candidate.index,
            // The title goes back with the index because the index alone is
            // not an address: a refill that ran while this card was open
            // renumbers everything below it, and the server refuses rather
            // than deciding the wrong idea.
            title: candidate.title,
            decision: decision,
            comment: box.value || ""
          })
        })
          .then(json)
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && result.message) || "failed");
            // Re-fetch rather than splicing the card out locally: the pool is
            // written by cycles too, and the honest state after a write is
            // whatever the document now says.
            loadPool();
          })
          .catch(function (err) {
            // Re-enabled on failure only: on success the page is about to be
            // repainted with the next candidate, and re-enabling a button on
            // a card that is being replaced is a live Approve pointing at an
            // idea he has already decided.
            buttons.forEach(function (b) { b.disabled = false; });
            note.textContent = "Could not save: " + err;
          });
      }

      var actions = el("div", "pool-actions");
      var approve = el("button", "pool-btn pool-approve", "Approve");
      approve.addEventListener("click", function () { decide("approve"); });
      var reject = el("button", "pool-btn pool-reject", "Reject");
      reject.addEventListener("click", function () { decide("reject"); });
      /* Comment is the third answer idea #92 asks for and Skip is the fourth,
       * not a substitute for it. Skip writes nothing on purpose; what was
       * wrong until now is that it was the *only* way past a card, so text
       * typed into the box below — on a card he was not ready to decide —
       * was thrown away by the one button that looked safe. This keeps the
       * candidate in the pool and puts his words on it.
       *
       * It re-fetches and stays on the same card rather than advancing.
       * Advancing would be one tap fewer and would show him nothing: the
       * proof that a write landed is his own sentence appearing above the
       * box, and this loop has shipped enough writes that reported success
       * without evidence. */
      var saveNote = el("button", "pool-btn pool-comment-btn", "Comment");
      saveNote.addEventListener("click", function () {
        if (!box.value.trim()) {
          note.textContent = "Nothing to note — the box is empty.";
          return;
        }
        buttons.forEach(function (b) { b.disabled = true; });
        note.textContent = "Noting…";
        fetch("/api/pool/comment", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            index: candidate.index,
            title: candidate.title,
            comment: box.value
          })
        })
          .then(json)
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && result.message) || "failed");
            loadPool();
          })
          .catch(function (err) {
            buttons.forEach(function (b) { b.disabled = false; });
            note.textContent = "Could not save: " + err;
          });
      });
      var skip = el("button", "pool-btn pool-skip", "Skip");
      skip.addEventListener("click", function () {
        poolAt += 1;
        renderPool(payload);
      });
      buttons = [approve, reject, saveNote, skip];
      actions.appendChild(approve);
      actions.appendChild(reject);
      actions.appendChild(saveNote);
      actions.appendChild(skip);
      card.appendChild(actions);
      card.appendChild(note);
      return card;
    }

    function renderPool(payload) {
      stopPolling();
      markNav();
      var candidates = (payload && payload.candidates) || [];
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el("p", "status-line",
        candidates.length
          ? "Ideas I came up with — " + candidates.length + " waiting on you"
          : "Ideas I came up with"));
      feed.textContent = "";

      if (poolAt >= candidates.length) poolAt = 0;
      if (!candidates.length) {
        feed.appendChild(el("p", "empty",
          payload && payload.missing
            ? "No pool yet — I will fill it on the next ideas run."
            : "Nothing waiting. I top this up on Tuesdays, Thursdays and Saturdays."));
      } else {
        feed.appendChild(renderPoolCard(candidates[poolAt], payload));
        if (candidates.length > 1) {
          feed.appendChild(el("p", "pool-count",
            (poolAt + 1) + " of " + candidates.length));
        }
      }

      var ask = el("button", "pool-generate", "Ask for more");
      var askNote = el("p", "pool-note");
      if (payload && payload.generateRequested) {
        ask.disabled = true;
        askNote.textContent = "Asked — the next cycle will top it up.";
      }
      ask.addEventListener("click", function () {
        askNote.textContent = "Asking…";
        fetch("/api/pool/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({})
        })
          .then(json)
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && result.message) || "failed");
            ask.disabled = true;
            // Said plainly rather than "generating", because nothing is: this
            // process has no model access and never will. The button asks and
            // a cycle answers, within about twenty minutes.
            askNote.textContent = "Asked — the next cycle will top it up.";
          })
          .catch(function (err) {
            askNote.textContent = "Could not ask: " + err;
          });
      });
      feed.appendChild(ask);
      feed.appendChild(askNote);
      feed.appendChild(renderPoolHistory());
    }

    /* What he already decided, and what he wrote when he decided it.
     *
     * The owner, capture 2026-08-25: *"I do not know if my comments on why or
     * why not a pool idea is rejected or not. As in the comments i write. I
     * can't even see what has been approved or rejected. Maybe give me a
     * history overview."*
     *
     * A decided candidate leaves the pool by design, and until now that also
     * meant it left the app: an approval became a board row among two hundred
     * others and a rejection went into a `## Discarded` table nothing here has
     * ever rendered. Behind a toggle rather than always drawn, because the
     * fetch reads his 281KB ideas file and the deck is what the page is for.
     */
    function renderPoolHistory() {
      var wrap = el("div", "pool-history");
      var toggle = el("button", "pool-history-toggle", "What I already decided");
      var body = el("div", "pool-history-body");
      // Deliberately not cached across a re-render: he has just decided
      // something, so the previous answer is the stale one.
      var loaded = false;
      wrap.appendChild(toggle);
      wrap.appendChild(body);
      toggle.textContent = poolHistoryOpen
        ? "Hide what I decided" : "What I already decided";

      toggle.addEventListener("click", function () {
        poolHistoryOpen = !poolHistoryOpen;
        toggle.textContent = poolHistoryOpen
          ? "Hide what I decided" : "What I already decided";
        open();
      });
      if (poolHistoryOpen) open();

      function open() {
        body.textContent = "";
        if (!poolHistoryOpen) return;
        if (loaded) { paint(loaded); return; }
        body.appendChild(el("p", "pool-note", "Reading…"));
        fetch("/api/pool/history")
          .then(json)
          .then(function (payload) {
            // The same in-flight guard the other fetches carry: he can be two
            // pages away by the time a 281KB read comes back.
            if (route(window.location.pathname).view !== "pool") return;
            loaded = payload || { approved: [], rejected: [] };
            if (poolHistoryOpen) paint(loaded);
          })
          .catch(function (err) {
            // The same guard as the success path above, and it was missing
            // here: he can close the panel while a 281KB read is in flight,
            // and an error landing afterwards would repaint a closed panel
            // with no tap from him. Reviewer finding on this PR.
            if (!poolHistoryOpen) return;
            body.textContent = "";
            body.appendChild(el("p", "pool-note", "Could not read it: " + err));
          });
      }

      function group(heading, items, className, render) {
        var box = el("div", "pool-history-group");
        box.appendChild(el("p", "pool-history-heading", heading));
        items.forEach(function (item) {
          var card = el("div", "pool-history-item " + className);
          render(card, item);
          box.appendChild(card);
        });
        return box;
      }

      function paint(payload) {
        body.textContent = "";
        var approved = payload.approved || [];
        var rejected = payload.rejected || [];
        if (!approved.length && !rejected.length) {
          body.appendChild(el("p", "pool-note",
            "Nothing decided yet — approve or reject one and it shows up here."));
          return;
        }
        if (approved.length) {
          body.appendChild(group("Approved", approved, "approved", function (card, item) {
            card.appendChild(el("p", "pool-history-title", item.title));
            if (item.comment) {
              card.appendChild(el("p", "pool-history-said", "You said: " + item.comment));
            }
            card.appendChild(el("p", "pool-history-meta",
              "Idea #" + item.number + (item.dated ? " · " + item.dated : "")));
          }));
        }
        if (rejected.length) {
          body.appendChild(group("Rejected", rejected, "rejected", function (card, item) {
            card.appendChild(el("p", "pool-history-title", item.title));
            // The reason column is his comment when he typed one and a bare
            // "Rejected <date>" when he did not, so it is shown as written
            // rather than labelled "You said" — some of these are mine.
            if (item.why) card.appendChild(el("p", "pool-history-said", item.why));
          }));
        }
      }

      return wrap;
    }

    /* The project page -- idea #92, phase 3.
     *
     * The owner's idea: *"each project has their own page that shows a
     * Kanban board, maybe a list of issues, notes and ideas"*. The plan he
     * approved is explicit that this phase invents no data: *"A kanban view
     * is the board rows grouped by status, which is a rendering of data
     * phase 2 already produced."* So everything drawn here came off the
     * `Project` column phase 2 added, and `/api/project` regroups the same
     * two board payloads the Issues and Ideas pages already read.
     *
     * One view over two URLs, the way the journal is: `/projects` is the
     * index and `/project/<name>` is one project. They share a render
     * because the index is just the page with no project chosen -- and the
     * project list is drawn on both, so a project with nothing filed under
     * it is one tap from the one that has everything.
     */
    /* One status column of a project's board.
     *
     * **The title is a link, and that is the whole of the crude operations
     * on this page.** Idea #166 asks for *"the same features that exist on
     * the ideas and issues board already related to crude operations,
     * comments per idea/issue"* on the project page, and these rows have
     * been plain text since they shipped. The version I did not build is a
     * second copy of the editor here: `renderRowEditor` and
     * `renderRowConversation` are wired to `loadBoard`, `boardState.open`
     * and the `details` cache, so a copy on this page is a second place
     * every future edit control has to be added -- and `prompt.md` step 2
     * is explicit that duplicating a shape is the bug rather than the fix.
     *
     * `/issues#5` already opens exactly that row with its write-up, its
     * conversation and its Edit button, and moves the filter out of the way
     * if the row is filtered off screen (`applyBoardHash`). So the row
     * reaches every crude operation in one tap, through the one
     * implementation of them.
     *
     * `board` is the page name -- `issues` or `ideas` -- because that is the
     * key `renderProject` loops over, so the address is built from it
     * directly rather than from a per-item `board` field the way the
     * backlog above does it. The backlog is one flat list of both boards and
     * has to carry the board per row; a column belongs to one board by
     * construction. */
    function renderProjectColumn(board, column) {
      var wrap = el("div", "project-column");
      wrap.appendChild(el("h3", "project-column-head",
        column.label + " · " + column.items.length));
      var list = el("ul", "project-rows");
      for (var i = 0; i < column.items.length; i++) {
        var item = column.items[i];
        var row = el("li", "project-row");
        row.appendChild(el("span", "project-row-num", "#" + item.number));
        var link = el("a", "project-row-title", item.title);
        link.setAttribute("href", "/" + board + "#" + item.number);
        row.appendChild(link);
        // No rating chip: a boarded row is placed by position (issue #202).
        list.appendChild(row);
      }
      wrap.appendChild(list);
      return wrap;
    }

    /* The row of project pills that used to sit above the ordered list is
     * gone, 2026-09-08. His ask: *"make the draggable projects clickable
     * instead of the project buttons above the draggable project list.
     * Remove the current clickable project buttons."*
     *
     * They were two lists of the same projects stacked on one screen, and the
     * lower one already carried the answer to every question the upper one
     * could be asked -- how far along, how many open, what order they are in.
     * The pills also sorted themselves by the legacy rating, which is the
     * thing this same ask removed.
     *
     * The cost, stated: on a project's own page there is no longer a way to
     * hop straight to another project. The way back is the Projects tab.
     */

    /* The portfolio, not the list -- idea #228's project-manager pass.
     *
     * The index has drawn a name and a rating chip per project since it
     * shipped. That answers "what projects are there" and nothing else, so
     * the question the page is actually opened with -- which project needs
     * me -- costs one tap per project, eleven of them, on a phone. His idea
     * asks for exactly this: *"The current project page is good, but not
     * great. It needs someone to pretend to be a project manager and really
     * think 'what do i need' and 'how do i want it?'"*
     *
     * So the standing every project page already shows is drawn here for
     * all of them at once, off `projectSummary`, which is the server
     * regrouping rows it had already built rather than measuring anything
     * new -- the number on this list and the number on the project page are
     * the same number by construction.
     *
     * The pills above stay exactly as they are. They are the navigation and
     * this is the reading; putting the counts inside the pills would have
     * made an eleven-item scannable strip into eleven sentences.
     *
     * No rating is drawn on a card: the worst-open-row chip that sat here
     * went with issue #202. The order is the server's, not a sort by how far
     * along each is: that would put the projects he cares least about at the
     * top on the day they finish.
     */
    function renderProjectStandings(payload) {
      var projects = (payload && payload.projects) || [];
      var summaries = (payload && payload.projectSummary) || {};
      var rated = (payload && payload.projectPriority) || {};
      var list = el("ul", "project-standing-rows");
      var seen = {};
      var shown = [];
      for (var i = 0; i < projects.length; i++) {
        var name = projects[i];
        var key = name.toLowerCase();
        // `Nova` and `nova` are two spellings in his cells and one project
        // everywhere else on this page -- the rating is keyed lowercase and
        // `/project/nova` already shows the rows of both -- so a second
        // standing under the second spelling is the same numbers twice.
        if (seen[key]) continue;
        seen[key] = true;
        var summary = summaries[key];
        if (!summary || !summary.total) continue;
        shown.push({ name: name, summary: summary, rating: rated[key] });
      }
      var shares = (payload && payload.projectShares) || null;
      for (var s = 0; s < shown.length; s++) {
        list.appendChild(projectStandingRow(
          shown[s].name, shown[s].summary, shown[s].rating, s, shown.length,
          shares));
      }
      if (!list.childNodes.length) return null;
      var box = el("section", "project-standings");
      box.appendChild(list);
      return box;
    }

    /* What the picker owes this project, and what it actually spent.
     *
     * Issue #214's last third: *"Show share vs actual on the projects
     * page."* The picker stopped using his hand order on 2026-09-12 and
     * started ranking on whichever project is furthest below its share, and
     * the arithmetic that decides it was printed to a cycle and to nobody
     * else. A ranking whose reason is invisible is one he cannot tell apart
     * from a broken one -- which is the complaint that opened this issue.
     *
     * Three states and they are three different facts, so none of them
     * collapses into another. `shares` is `null` when the claims ledger
     * would not read: nothing is drawn, because every project showing 0%
     * taken is also what a totally idle loop looks like. `counted` is 0 when
     * no recent cycle resolved to a project at all -- the ledger keeps the
     * slug and never the project, so about half of them are free text --
     * and then the percentages have no denominator and the line says so.
     * Otherwise it is the two numbers and the window they were taken over.
     *
     * The 14-day floor gets a clause only when it is actually rescuing this
     * project. When the ledger cannot see 14 days back it did not run at
     * all, which is the normal state today, and a card is the wrong place
     * to say that eleven times over.
     */
    function shareSentence(name, shares) {
      if (!shares) return "";
      var mine = (shares.projects || {})[name.toLowerCase()];
      if (!mine) return "";
      if (!shares.counted) {
        return "Share of cycles: owed " + fmtShare(mine.share)
          + "% — nothing in the claims ledger says which project recent cycles worked on";
      }
      var line = "Share of cycles: owed " + fmtShare(mine.share) + "%, took "
        + fmtShare(mine.actual) + "% (" + mine.cycles + " of the last "
        + shares.counted + ")";
      /* A deficit against an empty backlog is not starvation, and without
       * this clause it reads as one. Measured on the live boards
       * 2026-09-20: ten projects owed 92% of the loop between them had no
       * open row -- Marcus 47 rows, every one Done or Outdated -- and three
       * cycles running handed "Marcus owed 35%, took 0%" to the next cycle
       * as unfinished business. No cycle closes that by working harder; it
       * closes when a row is filed or the share moves, and both are his.
       * Checked for 0 specifically rather than falsiness, so a payload
       * without the field says nothing instead of saying zero. */
      if (mine.openRows === 0) {
        line += " — nothing open to take: this is not the picker skipping it";
      } else if (mine.starved) {
        line += " — " + shares.floorDays + "-day floor: next, whatever the arithmetic says";
      }
      return line;
    }

    /* A whole number unless the fraction changes the answer. 30.0 reads as
     * 30; 3.75 stays 3.8, because three projects splitting the tail of his
     * list all round to 4 and then the card says they are owed the same. */
    function fmtShare(value) {
      var n = Number(value) || 0;
      return Math.abs(n - Math.round(n)) < 0.05 ? String(Math.round(n)) : n.toFixed(1);
    }

    /* One project's standing, plus the two buttons that move it.
     *
     * Milestone M3 of idea #260 -- his ordered project list. The spec quotes
     * him asking for a drag-and-drop list, and this is not that: it is two
     * buttons, and the honest reason is that HTML5 `draggable` does nothing
     * at all on a touch screen, which is the screen he reads this page on.
     * A gesture that only works on the desktop he does not use would have
     * been the feature-shaped half rather than the working half, so the
     * ordering itself ships first and the drag gesture is still open.
     *
     * The position sent is `index + 1` of the *shown* list, which is what he
     * is looking at, and the server renumbers the whole table from it -- so
     * a project hidden here because it has no rows cannot be silently
     * reordered by a button press on another one.
     */
    function projectStandingRow(name, summary, rating, index, total, shares) {
      var li = el("li", "project-standing");
      /* The whole standing opens the project -- his ask, 2026-09-08, in the
       * same breath as deleting the pills above this list: *"make the
       * draggable projects clickable instead of the project buttons above the
       * draggable project list."*
       *
       * A real anchor wrapping the row rather than a click handler on the
       * `<li>`, so it is a link to the browser: middle-click, long-press and
       * a screen reader all get what they expect, and the delegated
       * `pushState` handler at the bottom of this file already intercepts it.
       *
       * Projects carry no move controls since issue #229: a project has no
       * priority of its own, its milestones and key results do. */
      /* A disclosure, not a link -- his ask, 2026-09-08: *"Instead of being
       * navigated to another page when i click on a project, i want a drawer
       * system where projects contains milestones and milestones contains
       * tasks."*
       *
       * It was an anchor to `/project/<name>` for half a day. The page it
       * opened is still there and still the place to read one project in
       * full; what changed is that answering "what is left in this one" no
       * longer costs a navigation and a way back. Two taps now open a project
       * and one of its milestones with the other projects still on screen
       * above and below, which is the comparison the ordered list exists to
       * support.
       *
       * `aria-expanded` and a `<button>` rather than a styled div, because
       * this is exactly the widget those are for -- and a screen reader
       * announcing "collapsed" is the whole of what the caret says visually. */
      var link = el("button", "project-standing-link");
      link.type = "button";
      link.setAttribute("aria-expanded", "false");
      var head = el("div", "project-standing-head");
      head.appendChild(el("span", "project-standing-name", name));
      var counts = summary.percentDone + "% · " + summary.open + " open";
      if (summary.blocked) counts += " · " + summary.blocked + " on you";
      head.appendChild(el("span", "project-standing-counts", counts));
      link.appendChild(head);
      var track = el("div", "project-standing-track");
      var fill = el("div", "project-standing-fill");
      fill.style.width = summary.percentDone + "%";
      track.appendChild(fill);
      // Named for a screen reader, the same as the bar on the project page:
      // a progress bar with no accessible name is a decoration.
      track.setAttribute("role", "img");
      track.setAttribute("aria-label", name + " — " + counts);
      link.appendChild(track);

      /* Filled on the first open and kept after that: re-fetching a project
       * he is folding shut and open again would blank the milestones he is
       * looking at, on a page whose whole point is that they stay put. */
      var drawer = el("div", "project-drawer");
      drawer.hidden = true;
      var loaded = false;
      link.addEventListener("click", function () {
        var opening = drawer.hidden;
        drawer.hidden = !opening;
        link.setAttribute("aria-expanded", opening ? "true" : "false");
        if (!opening || loaded) return;
        loaded = true;
        drawer.appendChild(el("p", "project-drawer-loading", "Loading…"));
        fetch("/api/project?name=" + encodeURIComponent(name))
          .then(json)
          .then(function (payload) {
            drawer.textContent = "";
            drawer.appendChild(projectDrawerBody(name, payload));
          })
          .catch(function (err) {
            drawer.textContent = "";
            /* Said, and re-openable. `loaded` goes back so the next tap tries
             * again -- a drawer stuck on an error it cannot retry is worse
             * than one that never opened. */
            loaded = false;
            drawer.appendChild(el("p", "project-drawer-error",
              "Could not load this project: " + (err && err.message ? err.message : err)));
          });
      });
      // The projected finish, on the index as well as the page, for the same
      // reason the standing is: "which project lands first" should not cost
      // one tap per project.
      var standingPace = paceSentence(summary.pace);
      if (standingPace) {
        link.appendChild(el("div", "project-standing-pace", standingPace));
      }
      var shareLine = shareSentence(name, shares);
      if (shareLine) {
        link.appendChild(el("div", "project-standing-share", shareLine));
      }
      /* No rating chip on the card. It carried the worst rating among the
       * project's open rows until issue #202, whose spec says no rating
       * appears on a boarded row and names this chip and the page's count
       * strip as going with it: the order inside a milestone is what says
       * what is next now, not a colour. */
      /* The card is the control -- his ask, 2026-09-08: *"make the whole card
       * clickable to expand, not just the progressbar."* The bar and the name
       * were inside the button already; the projected finish was not, so the
       * bottom third of the card did nothing when
       * pressed. Everything that describes the project is in the disclosure
       * now; only the move controls stay outside it, because they do
       * something else. */
      li.appendChild(link);
      li.appendChild(drawer);

      return li;
    }

    /* The second and third levels of the drawer: a project's milestones, and
     * the rows inside each one.
     *
     * The milestone order is the server's -- `project_milestones` sorts by
     * `milestone_ranks`, which is the same map the picker and
     * `tools.top_board_rows` read. His ask says the top one is the first
     * pick, and that is only true if this list and the thing that chooses
     * work agree; computing a second order here is exactly the duplication
     * this repo keeps paying for.
     *
     * The rows are grouped here rather than fetched, because they are already
     * in this payload: `_project_columns` groups every row by status, and
     * each row carries the milestone it belongs to. So the third level costs
     * no request at all.
     */
    function projectDrawerBody(project, payload) {
      var wrap = el("div", "project-drawer-body");
      var milestones = (payload && payload.milestones) || [];
      /* `boards.issues.columns` and `boards.ideas.columns`, NOT
       * `payload.columns`. Reading the latter is what made every milestone
       * report zero rows in his screenshot: the key does not exist, so the
       * list was always empty and the counts were always right about it.
       *
       * `project_payload` keeps the two boards apart because the page's tabs
       * need the per-board totals; the milestones do not care which board a
       * row came from, only that the link points at the right one. */
      var rows = [];
      ["issues", "ideas"].forEach(function (board) {
        var group = ((payload && payload.boards) || {})[board] || {};
        (group.columns || []).forEach(function (column) {
          (column.items || []).forEach(function (item) {
            rows.push({
              item: item,
              board: board === "issues" ? "issue" : "idea",
              status: column.label || "",
              statusKey: column.key || "",
            });
          });
        });
      });

      if (!milestones.length) {
        wrap.appendChild(el("p", "project-drawer-empty",
          "No milestones yet — " + rows.length + " row" + (rows.length === 1 ? "" : "s")
          + " filed under this project."));
      }
      var list = el("ol", "project-drawer-milestones");
      milestones.forEach(function (milestone, index) {
        list.appendChild(milestoneDrawer(
          project, milestone, rows, index, milestones.length));
      });
      attachDrawerMilestoneDrag(list, project);
      wrap.appendChild(list);

      // The full page keeps its place: this drawer answers "what is left",
      // and the page answers everything else -- the roadmap, the comments,
      // the lifecycle. A drawer that tried to be the page would be the page.
      var more = el("a", "project-drawer-more", "Open " + project + " →");
      more.setAttribute("href", "/project/" + encodeURIComponent(project));
      wrap.appendChild(more);
      return wrap;
    }

    function milestoneDrawer(project, milestone, rows, index, total) {
      var li = el("li", "project-drawer-milestone");
      var head = el("button", "project-drawer-milestone-head");
      head.type = "button";
      head.setAttribute("aria-expanded", "false");
      head.appendChild(el("span", "project-drawer-milestone-name", milestone.name));
      var mine = rows.filter(function (row) {
        return String(row.item.milestone || "").toLowerCase()
          === String(milestone.name || "").toLowerCase();
      });
      head.appendChild(el("span", "project-drawer-milestone-counts",
        mine.length + " row" + (mine.length === 1 ? "" : "s")));
      li.appendChild(head);

      var tasks = el("ul", "project-drawer-tasks");
      tasks.hidden = true;
      if (!mine.length) {
        tasks.appendChild(el("li", "project-drawer-empty",
          "Nothing is filed under this milestone yet."));
      }
      /* In the order the server seats them: `taskSeat` (the Order cell, or
       * for an unseated row the rating seed `row_order_seats` applies), then
       * issues before ideas, then number. Most open rows have no seat yet --
       * 172 of 203 on the issues board, measured 2026-09-11 -- so the seed is
       * the common case, not an edge. */
      mine.sort(function (a, b) {
        return (taskSeat(a) - taskSeat(b))
          || ((a.board === "issue" ? 0 : 1) - (b.board === "issue" ? 0 : 1))
          || (a.item.number - b.item.number);
      });
      /* A closed row carries a second class so `attachRowDrag`, which matches
       * a row by its exact class name, leaves it out: the server seats open
       * rows only, so a drop position counted with a done row among them
       * would land one seat off. */
      mine.forEach(function (row) {
        var task = el("li", taskIsOpen(row)
          ? "project-drawer-task" : "project-drawer-task project-drawer-task--closed");
        var link = el("a", "project-drawer-task-link",
          "#" + row.item.number + " " + (row.item.title || ""));
        link.setAttribute("href",
          "/" + (row.board === "issue" ? "issues" : "ideas") + "#" + row.item.number);
        task.appendChild(link);
        if (row.status) task.appendChild(el("span", "project-drawer-task-status", row.status));
        if (taskIsOpen(row)) task.appendChild(taskMoveControls(row, mine));
        tasks.appendChild(task);
      });
      attachTaskDrag(tasks);
      li.appendChild(tasks);
      /* The same controls the project page carries, in the drawer -- his ask,
       * 2026-09-08: *"Lets me organise/sort the milestones and tasks aswell."*
       * They write through `sendMilestonePin`, which is the single path every
       * milestone control on this site already uses, so a pin cannot mean two
       * things depending on which screen set it.
       *
       * On the right, per [[controls on the right]]: he reorders one-handed
       * with his right thumb. */
      li.appendChild(milestoneMoveControls(project, milestone, index, total));
      head.addEventListener("click", function () {
        var opening = tasks.hidden;
        tasks.hidden = !opening;
        head.setAttribute("aria-expanded", opening ? "true" : "false");
      });
      return li;
    }

    /* Where a task sits inside its milestone, on the server's own scale.
     * A seated row is its Order cell. An unseated row falls in behind every
     * seated one by rating -- immediate, high, medium, low, then unrated --
     * which is exactly how `row_order_seats` seeds a group on its first
     * placement. Most rows are still unseated, so an arrow counted against
     * any other order would send a position the server reads differently. */
    var TASK_SEED = { immediate: 0, high: 1, medium: 2, low: 3 };

    function taskSeat(row) {
      var seat = Number(row.item.order);
      if (seat > 0) return seat;
      var seed = TASK_SEED[row.item.priorityKey];
      return 1e6 + (seed === undefined ? 4 : seed);
    }

    /* Open is the server's rule, not the column's look: `row_order_seats`
     * refuses a row that is done or outdated, so an arrow on one could only
     * ever answer an error. */
    var TASK_CLOSED = { done: true, outdated: true };

    function taskIsOpen(row) {
      return !row.item.done && !TASK_CLOSED[row.statusKey];
    }

    /* Move one task up or down inside its milestone -- part 3 of issue #202
     * (`row-order-and-priority-migration.md`): *"make another cycle remove the
     * old priority system and order the tasks in the correct new order and
     * also adding functionality for me to change it."*
     *
     * The position is counted among ALL the open rows of the milestone, issues
     * and ideas together, because that is the group `set_row_order` numbers:
     * one milestone is one queue whichever board a task was filed on, the
     * same merge this list is sorted by. So an arrow moves a task past its
     * nearest open neighbour, and an issue can trade a seat with an idea.
     *
     * On the right, note first and grip last, for the reason
     * `milestoneMoveControls` gives. */
    function taskMoveControls(row, mine) {
      var peers = mine.filter(taskIsOpen);
      var index = peers.indexOf(row);
      var wrap = el("div", "project-task-move");
      var note = el("span", "project-task-move-note", "");
      wrap.appendChild(note);
      function mover(label, position, enabled) {
        var button = el("button", "project-task-move-btn", label);
        button.type = "button";
        button.setAttribute("aria-label",
          "Move #" + row.item.number + (label === "↑" ? " up" : " down"));
        if (!enabled) {
          button.disabled = true;
          return button;
        }
        button.addEventListener("click", function () {
          sendRowOrder(row.board === "issue" ? "issues" : "ideas",
            row.item.number, position, note);
        });
        return button;
      }
      wrap.appendChild(mover("↑", index, index > 0));
      wrap.appendChild(mover("↓", index + 2, index < peers.length - 1));
      wrap.appendChild(dragHandle("project-task-grip", "data-task",
        (row.board === "issue" ? "issues" : "ideas") + ":" + row.item.number));
      return wrap;
    }

    function sendRowOrder(target, number, position, note) {
      note.textContent = "Saving…";
      return fetch("/api/row/order", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        // `author` is what records the placement as his: a cycle may not move
        // a task he placed (issue #202).
        body: JSON.stringify({ target: target, number: number, position: position,
          author: OWNER_RECORD })
      })
        .then(json)
        .then(function (result) {
          if (!result || !result.ok) {
            throw new Error((result && (result.message || result.error)) || "failed");
          }
          note.textContent = "";
          // Reload, as the milestone arrows do: the seats are the server's,
          // and the list has to show what the records say.
          load();
        })
        .catch(function (err) { note.textContent = "Could not move: " + err; });
    }

    /* The same grip for a milestone -- milestone M4 of idea #260, the last
     * piece of it. The attribute differs on purpose: `data-project` on a
     * milestone row would name the wrong thing, since a milestone drag
     * sends a milestone name inside a project that the page already knows.
     */
    function milestoneDragHandle(name) {
      return dragHandle("project-milestone-grip", "data-milestone", name);
    }

    /* One grip, whatever it drags. `aria-hidden` and not focusable for the
     * reason written above: the two arrows beside it already carry the same
     * action with a real accessible name, and a third control a screen
     * reader cannot operate announces an ability it does not have. */
    function dragHandle(gripClass, attr, name) {
      var grip = el("span", gripClass, "\u2807");
      grip.setAttribute("aria-hidden", "true");
      grip.setAttribute(attr, name);
      return grip;
    }

    /* Drag one row of an ordered list to a new place with a finger.
     *
     * `attachRowDrag` is the gesture and it knows nothing about what it is
     * dragging: the two wrappers below hand it the class names of their own
     * rows and the function that writes the result. The project standings
     * (M3) and the project's milestones (M4) are both ordered lists with a
     * grip, two arrows and a note, so one gesture serves both.
     *
     * The gesture is pointer events rather than HTML5 `draggable`, which is
     * the reason M3 shipped as two buttons in the first place: `dragstart`
     * never fires on a touch screen, and a touch screen is what he reads
     * this page on.
     *
     * Where it lands is decided against the row centres measured once, at
     * `pointerdown`, rather than re-measured while the finger moves. The
     * dragged row is translated rather than re-parented, so the rows under
     * it never move and a mid-drag measurement would read the same numbers
     * anyway -- and measuring once means the target cannot oscillate when a
     * row is taller than the one it is passing.
     *
     * Three behaviours worth stating because each is a decision:
     *  - nothing is sent when the drag ends on the row's own index, so
     *    resting a thumb on the grip cannot renumber his whole table;
     *  - the drag only starts after `DRAG_SLOP` pixels, so a tap that
     *    wobbles is still a tap;
     *  - `pointercancel` (the browser taking the gesture back for a scroll)
     *    puts the row back and sends nothing, which is not the same event
     *    as letting go.
     */
    var DRAG_SLOP = 8;

    /* The same gesture on the milestone list -- milestone M4 of idea #260,
     * and the last piece of it.
     *
     * `project` is closed over rather than read off the row, because a
     * milestone position is only meaningful inside one project and the page
     * already knows which one it is drawing. The grip therefore carries the
     * milestone name and nothing else.
     *
     * There is no `attachMilestoneDrag`-shaped difference from the project
     * version beyond that and the class names, which is the whole reason
     * this shipped as one function: two copies of a pointer gesture is two
     * places for the slop, the capture and the `pointercancel` reset to
     * drift apart, and the drift would be invisible on the list nobody
     * happened to test that week.
     */
    function attachMilestoneDrag(list, project) {
      attachRowDrag(list, {
        rowClass: "project-milestone",
        gripClass: "project-milestone-grip",
        nameAttr: "data-milestone",
        draggingClass: "project-milestone--dragging",
        noteSelector: ".project-milestone-move-note",
        send: function (name, position, note) {
          sendMilestonePin(project, name, position, note);
        }
      });
    }

    /* The same gesture on the milestones in the project drawer. The drawer
     * drew `milestoneMoveControls`, grip included, from the day the arrows
     * went in, and nothing attached the gesture to it -- so the grip sat
     * there and did nothing when pressed. Same grip class, same note and the
     * same `sendMilestonePin` as the project page's list; only the row class
     * differs, and the drawer draws the same `payload.milestones` in the same
     * order, so a drop position means the same seat on both screens. */
    function attachDrawerMilestoneDrag(list, project) {
      attachRowDrag(list, {
        rowClass: "project-drawer-milestone",
        gripClass: "project-milestone-grip",
        nameAttr: "data-milestone",
        draggingClass: "project-drawer-milestone--dragging",
        noteSelector: ".project-milestone-move-note",
        send: function (name, position, note) {
          sendMilestonePin(project, name, position, note);
        }
      });
    }

    /* The same gesture on a milestone's tasks in the project drawer -- the
     * last piece of issue #202: *"give me drag-and-arrows on the task rows in
     * the project drawer to change it."* The grip carries `issues:41` or
     * `ideas:42`, because `set_row_order` needs the board as well as the
     * number, and the drop writes through `sendRowOrder` exactly as the
     * arrows do. */
    function attachTaskDrag(list) {
      attachRowDrag(list, {
        rowClass: "project-drawer-task",
        gripClass: "project-task-grip",
        nameAttr: "data-task",
        draggingClass: "project-drawer-task--dragging",
        noteSelector: ".project-task-move-note",
        send: function (key, position, note) {
          var parts = key.split(":");
          sendRowOrder(parts[0], Number(parts[1]), position, note);
        }
      });
    }

    function attachRowDrag(list, spec) {
      var drag = null;

      function rows() {
        var out = [];
        var kids = list.childNodes;
        for (var i = 0; i < kids.length; i++) {
          if (kids[i].className === spec.rowClass) out.push(kids[i]);
        }
        return out;
      }

      /* The index the dragged row would take if the finger let go now.
       * `centres` is the y-midpoint of each row at drag start; the dragged
       * row's own centre has moved by `dy`. Walking outwards from the
       * origin rather than sorting keeps a row from jumping past two
       * neighbours at once when the list is not evenly spaced. */
      function targetIndex(centres, origin, dy) {
        var here = centres[origin] + dy;
        var target = origin;
        while (target > 0 && here < centres[target - 1]) target--;
        while (target < centres.length - 1 && here > centres[target + 1]) target++;
        return target;
      }

      function centreOf(node) {
        var rect = node.getBoundingClientRect();
        return rect.top + (rect.height / 2);
      }

      function reset() {
        if (!drag) return;
        drag.row.style.transform = "";
        drag.row.classList.remove(spec.draggingClass);
        drag = null;
      }

      list.addEventListener("pointerdown", function (event) {
        // One drag at a time. A second finger landing on another grip
        // would otherwise replace `drag` and strand the first row with its
        // transform still on, until the next load repainted the list.
        if (drag) return;
        var grip = event.target;
        if (!grip || grip.className !== spec.gripClass) return;
        var row = grip.parentNode;
        while (row && row.className !== spec.rowClass) row = row.parentNode;
        if (!row) return;
        var all = rows();
        var origin = all.indexOf(row);
        if (origin < 0 || all.length < 2) return;
        var centres = [];
        for (var i = 0; i < all.length; i++) centres.push(centreOf(all[i]));
        drag = {
          row: row,
          name: grip.getAttribute(spec.nameAttr),
          origin: origin,
          centres: centres,
          startY: event.clientY,
          moved: false,
          target: origin
        };
        // Without capture the gesture ends the moment the finger leaves the
        // grip, which on a 44px control is immediately.
        if (grip.setPointerCapture && event.pointerId !== undefined) {
          try { grip.setPointerCapture(event.pointerId); } catch (err) { /* not supported */ }
        }
      });

      list.addEventListener("pointermove", function (event) {
        if (!drag) return;
        var dy = event.clientY - drag.startY;
        if (!drag.moved) {
          if (Math.abs(dy) < DRAG_SLOP) return;
          drag.moved = true;
          drag.row.classList.add(spec.draggingClass);
        }
        // The page must not scroll under a drag it has already started.
        if (event.preventDefault) event.preventDefault();
        drag.row.style.transform = "translateY(" + dy + "px)";
        drag.target = targetIndex(drag.centres, drag.origin, dy);
      });

      list.addEventListener("pointerup", function () {
        if (!drag) return;
        var moved = drag.moved;
        var target = drag.target;
        var origin = drag.origin;
        var name = drag.name;
        var note = drag.row.querySelector(spec.noteSelector);
        reset();
        if (!moved || target === origin || !note) return;
        spec.send(name, target + 1, note);
      });

      list.addEventListener("pointercancel", function () { reset(); });
    }

    /* The rating of one project, as something the owner can change --
     * his capture, 2026-09-01: *"Each project should also be able to be
     * assigned a priority, making one project and its tasks more important
     * than others."*
     *
     * `buildPrioPicker` verbatim, the same control the board rows use, so a
     * project's rating and a row's rating are picked the same way and read
     * the same colour. It saves on change with no Save button, for the
     * reason the board rows' picker gave: the only action it can take is
     * the one just chosen.
     *
     * It lives on the project page rather than on every pill of the index.
     * The index is a list to scan and a select on each entry is a list you
     * cannot scan; the same split the board already makes, where the list
     * shows chips and the held card edits.
     */
    /* Where a project stands, in one strip -- idea #228, the burndown half.
     *
     * He asked each project page for "a backlog, roadmap and maybe a
     * burndown chart", and then for a project-manager pass: *"really think
     * 'what do i need' and 'how do i want it?'"*. Four status columns answer
     * "what state is each row in". They do not answer the two questions a
     * person opening a project page actually has, which are "how far along is
     * this" and "is there anything red under it" -- for that he has to count
     * cards, on a phone, in a column that can hold sixty.
     *
     * So the numbers come first and the columns stay below them. The bar is
     * done against done-plus-open; `percentDone` is computed on the server so
     * this cannot disagree with the counts printed beside it. Dropped rows
     * are named separately rather than added to the bar, because "will never
     * be built" is scope removed and not work delivered -- `_project_summary`
     * carries the reasoning.
     *
     * The rating counts are chips with the word in them, never a bare glyph:
     * a reader who has to know a colour code has not been told anything.
     * Nothing is drawn for a project with no rows -- the page already says
     * "nothing is filed under X yet" and a 0% bar under that is noise. */
    /* When a project finishes, in words -- idea #228's dated-roadmap half.
     *
     * The server computes the date; this only writes the sentence, so the
     * page and the index cannot disagree about a number and cannot disagree
     * with each other. `pace.finishes` is an ISO date or null, and null has
     * two different causes that must not read the same: nothing left to do,
     * or nothing being closed. The first needs no line at all; the second is
     * the finding, so it says so out loud.
     *
     * The rate and the assumption travel with the date every time it is
     * shown. A date on its own reads as a commitment; "6.5/week, if nothing
     * new is added" is the same date with its own error bars attached, and
     * the assumption is a real one -- his backlog grows. */
    function paceSentence(pace) {
      if (!pace) return null;
      if (!pace.remaining) return null;
      if (!pace.finishes) {
        return "nothing closed in the last " + pace.windowDays + " days — "
          + pace.remaining + " open, no date at this rate";
      }
      return "~" + formatMonthDay(pace.finishes) + " at " + pace.perWeek
        + "/week — " + pace.remaining + " left, " + pace.assumes;
    }

    /* `2026-10-06` as `6 Oct`. The year is deliberately dropped: every other
     * date on these pages is his own `MM-DD` and a lone full ISO date beside
     * them reads as a different kind of fact than it is. */
    function formatMonthDay(iso) {
      var parts = String(iso).split("-");
      if (parts.length !== 3) return iso;
      var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
      var month = months[parseInt(parts[1], 10) - 1];
      if (!month) return iso;
      return parseInt(parts[2], 10) + " " + month;
    }

    function renderProjectSummary(payload) {
      var summary = (payload && payload.summary) || null;
      if (!summary || !summary.total) return null;
      var box = el("section", "project-summary");
      var head = el("div", "project-summary-head");
      head.appendChild(el("span", "project-summary-pct", summary.percentDone + "%"));
      var counts = summary.done + " done · " + summary.open + " open";
      if (summary.blocked) counts += " · " + summary.blocked + " on you";
      if (summary.dropped) counts += " · " + summary.dropped + " dropped";
      head.appendChild(el("span", "project-summary-counts", counts));
      box.appendChild(head);
      var track = el("div", "project-summary-track");
      var fill = el("div", "project-summary-fill");
      fill.style.width = summary.percentDone + "%";
      track.appendChild(fill);
      // Read out as one sentence rather than as a bare number: a progress bar
      // with no accessible name is a decoration to a screen reader.
      track.setAttribute("role", "img");
      track.setAttribute("aria-label",
        summary.percentDone + "% done — " + counts);
      box.appendChild(track);
      var pace = paceSentence(summary.pace);
      if (pace) box.appendChild(el("div", "project-summary-pace", pace));
      return box;
    }

    /* How many of the ordered backlog are visible before the fold. This is
     * not a cap on the data: the server sends every open row and every one is
     * in the DOM below. It only decides how many are readable without a tap.
     * It lives here and only here -- the server does not slice, so there is
     * no second copy of this number to drift from.
     */
    var PROJECT_BACKLOG_VISIBLE = 5;

    /* What to do next on this project, in order -- idea #228's backlog half.
     *
     * His idea asks for *"a backlog, roadmap and maybe a burndown chart"* and
     * for someone to *"pretend to be a project manager and really think 'what
     * do i need'"*. The columns below say what state every row is in. They do
     * not say which row is next, and that is the question a project page is
     * opened with -- on Marcus, forty open rows across two boards, answering
     * it means reading every card and holding a rating in your head.
     *
     * The order is the server's, and it is the same `nova_next.rank` that
     * decides what a cycle actually picks up. That is the point rather than
     * reuse for its own sake: if this list ordered itself, the page would be
     * telling him one thing while I did another. `_project_backlog` carries
     * the two raises that are missing and why.
     *
     * Five rows are visible and the rest are behind a `<details>`. Nothing is
     * dropped -- every open row is in the payload and every one is in the DOM
     * -- because a cap on what he can see is the mistake `personality.md`
     * names, and the fold is the interface that replaces it.
     */
    function renderProjectBacklog(payload) {
      var rows = (payload && payload.backlog) || [];
      if (!rows.length) return null;
      var box = el("section", "project-backlog");
      box.appendChild(el("h2", "project-backlog-head", "What's next · " + rows.length));

      function rowEl(item, position) {
        var li = el("li", "project-backlog-row");
        li.appendChild(el("span", "project-backlog-pos", String(position)));
        var link = el("a", "project-backlog-link", item.title);
        // The board page, anchored on the row -- the same address the Issues
        // and Ideas pages use for a card, so a tap here lands where a tap
        // there does instead of on a second detail view.
        link.setAttribute("href",
          "/" + (item.board === "issue" ? "issues" : "ideas") + "#" + item.number);
        var num = el("span", "project-backlog-num",
          (item.board === "issue" ? "issue #" : "idea #") + item.number);
        li.appendChild(num);
        li.appendChild(link);
        // No rating chip: the backlog's order IS the position (issue #202).
        // Only when it is the reason the row is down here. Every other status
        // is already the column the row sits in below.
        if (item.statusKey === "blocked-on-edvard") {
          li.appendChild(el("span", "project-backlog-blocked", "on you"));
        }
        return li;
      }

      var visible = el("ol", "project-backlog-rows");
      var i;
      for (i = 0; i < rows.length && i < PROJECT_BACKLOG_VISIBLE; i++) {
        visible.appendChild(rowEl(rows[i], i + 1));
      }
      box.appendChild(visible);

      if (rows.length > PROJECT_BACKLOG_VISIBLE) {
        var fold = el("details", "project-backlog-fold");
        var sum = el("summary", "project-backlog-more",
          "The other " + (rows.length - PROJECT_BACKLOG_VISIBLE) + ", in order");
        fold.appendChild(sum);
        var rest = el("ol", "project-backlog-rows");
        for (i = PROJECT_BACKLOG_VISIBLE; i < rows.length; i++) {
          rest.appendChild(rowEl(rows[i], i + 1));
        }
        fold.appendChild(rest);
        box.appendChild(fold);
      }
      return box;
    }

    /* Where this project sits in the order I actually work in -- idea #228,
     * the roadmap half. The list under this is ordered by rating, which is
     * what to take next *within* the project; it cannot say whether the
     * project is ahead of anything else, and that is what a roadmap answers.
     * `roadmap.md` has held that order since Cycle 226 and every ranked item
     * names the rows it is about, so this is the same order filtered to the
     * rows filed here. The rank is the roadmap's own and deliberately not
     * renumbered per project -- renumbering "3 of five" to 1 would claim this
     * project leads the roadmap. Nothing is drawn when no ranked item touches
     * the project, except the line saying how many name no row at all:
     * without it, "no roadmap items here" and "the roadmap names no rows
     * anywhere" read identically and mean different things. */
    /* This project's milestones, in the order the picker takes them, with the
     * two buttons that pin one somewhere else. Milestone M4 of idea #260, and
     * the half that had no screen: a pin has been settable since
     * `tools.milestone_pin` shipped and the ordering it overrides was drawn
     * nowhere, so the only way to see what he was pinning inside was a
     * terminal he does not have. Two buttons *and* a grip, because a drag
     * has no keyboard
     * and nothing a screen reader can operate, so deleting the arrows would
     * take the ordering away from every input except a finger, and both write
     * through `sendMilestonePin` so a pin cannot mean two things. The gesture
     * is `attachRowDrag`: HTML5 `draggable` fires nothing on a touch screen,
     * and a phone is where he reads this. A pinned milestone says so and can
     * be unpinned -- `0` is "back to the computed order". */
    function renderProjectMilestones(name, payload) {
      var items = (payload && payload.milestones) || [];
      // Nothing to order. One milestone is still drawn: it says what the
      // project is grouped into, and a list that appears only at two would
      // read as a bug on the day a second one is filed.
      if (!items.length) return null;
      var box = el("section", "project-milestones");
      box.appendChild(el("h2", "project-milestones-head", "Milestones"));
      var list = el("ol", "project-milestone-rows");
      for (var i = 0; i < items.length; i++) {
        list.appendChild(milestoneRow(name, items[i], i, items.length));
      }
      attachMilestoneDrag(list, name);
      box.appendChild(list);
      return box;
    }

    /* One milestone, its open-row count, and its move controls. */
    function milestoneRow(name, item, index, total) {
      var li = el("li", "project-milestone");
      var head = el("div", "project-milestone-head");
      head.appendChild(el("span", "project-milestone-name", item.name));
      var open = item.open || 0;
      head.appendChild(el("span", "project-milestone-counts",
        open + " open row" + (open === 1 ? "" : "s")));
      // Only on a milestone he actually pinned. The number is the position
      // he asked for, which is not always where it sits -- a pin past the
      // end of a shrinking list clamps -- so it is labelled as his pin
      // rather than as this row's place in the list.
      if (item.pin) {
        head.appendChild(el("span", "project-milestone-pin",
          "pinned " + item.pin));
      }
      li.appendChild(head);
      li.appendChild(milestoneMoveControls(name, item, index, total));
      return li;
    }

    /* Send one milestone to a 1-based position inside its project.
     *
     * The single write path for every control on the row, so a pin cannot
     * mean two things depending on which button set it. `0` unpins.
     */
    function sendMilestonePin(project, milestone, position, note) {
      note.textContent = "Saving…";
      return fetch("/api/milestone/pin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          project: project, milestone: milestone, position: position })
      })
        .then(json)
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && result.message) || "failed");
          note.textContent = "";
          // Reload rather than swapping two rows: a pin is applied against
          // the whole computed order, and moving one milestone up can move
          // another one that was pinned. The page has to show what the file
          // produces, not what the tap implied.
          load();
        })
        .catch(function (err) { note.textContent = "Could not pin: " + err; });
    }

    /* Move one milestone up or down, or take its pin off.
     *
     * `index` is 0-based in the list drawn above, so "up" is `index`
     * (1-based `index - 1 + 1`) and "down" is `index + 2`, the 1-based
     * scale on the other side of the request.
     *
     * The ends are disabled rather than hidden: a control that disappears at the top slides the
     * other one under his thumb and he presses the wrong thing.
     */
    function milestoneMoveControls(project, item, index, total) {
      var wrap = el("div", "project-milestone-move");
      var note = el("span", "project-milestone-move-note", "");
      // Note first, grip last -- the ORDER is what puts these on the right.
      // His report, 2026-09-08: "the arrows where never moved to the right".
      // An auto margin on a note appended last beats `justify-content`: it
      // takes the free space first and pushes every control before it left.
      wrap.appendChild(note);
      function mover(label, position, enabled) {
        var button = el("button", "project-milestone-move-btn", label);
        button.type = "button";
        button.setAttribute("aria-label",
          "Move " + item.name + (label === "↑" ? " up" : " down"));
        if (!enabled) {
          button.disabled = true;
          return button;
        }
        button.addEventListener("click", function () {
          sendMilestonePin(project, item.name, position, note);
        });
        return button;
      }
      wrap.appendChild(mover("↑", index, index > 0));
      wrap.appendChild(mover("↓", index + 2, index < total - 1));
      if (item.pin) {
        var clear = el("button", "project-milestone-unpin", "Unpin");
        clear.type = "button";
        clear.setAttribute("aria-label", "Unpin " + item.name);
        clear.addEventListener("click", function () {
          sendMilestonePin(project, item.name, 0, note);
        });
        wrap.appendChild(clear);
      }
      wrap.appendChild(milestoneDragHandle(item.name));
      return wrap;
    }

    function renderProjectRoadmap(payload) {
      var roadmap = (payload && payload.roadmap) || {};
      var items = roadmap.items || [];
      var orphans = roadmap.unattributed || 0;
      if (!items.length && !orphans) return null;
      var box = el("section", "project-roadmap");
      box.appendChild(el("h2", "project-roadmap-head", "On the roadmap"));

      var list = el("ol", "project-roadmap-rows");
      for (var i = 0; i < items.length; i++) {
        var item = items[i];
        var li = el("li", "project-roadmap-row");
        // The roadmap's own rank, not this list's position.
        li.appendChild(el("span", "project-roadmap-rank", String(item.rank || "")));
        var head = el("div", "project-roadmap-title", item.title);
        // Word beside the symbol, never the symbol alone.
        if (item.statusLabel) {
          head.appendChild(el("span", "project-roadmap-status",
            (item.statusSymbol ? item.statusSymbol + " " : "") + item.statusLabel));
        }
        li.appendChild(head);
        if (item.claim) li.appendChild(el("p", "project-roadmap-claim", item.claim));
        var refs = el("div", "project-roadmap-refs");
        var rows = item.rows || [];
        for (var r = 0; r < rows.length; r++) {
          var ref = rows[r];
          var link = el("a", "project-roadmap-ref",
            (ref.board === "issue" ? "issue #" : "idea #") + ref.number);
          link.setAttribute("href",
            "/" + (ref.board === "issue" ? "issues" : "ideas") + "#" + ref.number);
          refs.appendChild(link);
        }
        // Said rather than hidden: this item is partly somewhere else, and a
        // list of only the local rows reads as the whole item.
        if (item.elsewhere) {
          refs.appendChild(el("span", "project-roadmap-elsewhere",
            "+ " + item.elsewhere + " row" + (item.elsewhere === 1 ? "" : "s")
            + " outside this project"));
        }
        li.appendChild(refs);
        list.appendChild(li);
      }
      if (items.length) box.appendChild(list);
      if (!items.length) {
        box.appendChild(el("p", "project-roadmap-none",
          "Nothing on the roadmap names a row filed here."));
      }
      if (orphans) {
        box.appendChild(el("p", "project-roadmap-orphans",
          orphans + " roadmap item" + (orphans === 1 ? "" : "s")
          + " name no board row, so " + (orphans === 1 ? "it appears" : "they appear")
          + " on no project page."));
      }
      return box;
    }

    // The five levels, worst first, mirroring `PROJECT_TRL_LEVELS` in
    // `nova_boards.py`. A duplicated list is the thing this repo keeps
    // deleting, and it is duplicated here on purpose and only here: the
    // payload carries the label and the count, so the page never decides
    // what a level *means* -- this array exists to draw five dots and to
    // say what the empty one would have been, which is a rendering fact.
    var TRL_DOTS = 5;

    function renderProjectTrl(name, payload) {
      var rated = ((payload && payload.projectPriority) || {})[name.toLowerCase()];
      var level = (rated && rated.trlKey) || 0;
      var label = (rated && rated.trl) || "";
      var row = el("div", "project-trl");
      row.appendChild(el("span", "project-prio-label", "Readiness"));
      var meter = el("span", "trl-meter", "");
      // `aria-label` rather than dots alone: a dot meter is exactly the
      // "if a reader has to know the code to know what I said" failure, so
      // the word rides beside it on screen and inside it for a reader that
      // cannot see it.
      meter.setAttribute("role", "img");
      meter.setAttribute("aria-label", label
        ? "Readiness " + label + ", " + level + " of " + TRL_DOTS
        : "Readiness not assessed");
      for (var i = 1; i <= TRL_DOTS; i += 1) {
        meter.appendChild(el("span", "trl-dot" + (i <= level ? " on" : ""), ""));
      }
      row.appendChild(meter);
      row.appendChild(el("span", "project-trl-word", label || "not assessed"));
      return row;
    }

    /* His satisfaction with a project, 1-5, and the buttons that set it.
     *
     * Milestone M5 of idea #260. This is the mirror of the readiness meter
     * above: that one is drawn and never pressed, because the spec assigns
     * the TRL to Nova; this one is pressed and never written by a cycle,
     * because the spec says the score is his alone.
     *
     * The scale carries no words on purpose. Readiness names its five
     * levels because Nova sets them and knows what each one means; putting
     * words on his five would be Nova's adjectives on his judgement. So the
     * control says "2 of 5" and he means what he means by it.
     *
     * Pressing the score already set clears it. Unrated is a real state and
     * a different answer from 1 -- the spec forces a diagnosis at "2 or
     * below", and that must never fire because nobody ever pressed anything
     * -- so there has to be a way back to it, and a sixth "clear" button
     * would be a control for a thing he does once a year.
     */
    function renderProjectSatisfaction(name, payload) {
      var rated = ((payload && payload.projectPriority) || {})[name.toLowerCase()];
      var score = (rated && rated.satisfaction) || 0;
      var max = (rated && rated.satisfactionMax) || 5;
      var row = el("div", "project-sat");
      row.appendChild(el("span", "project-prio-label", "Your satisfaction"));
      var note = el("span", "project-sat-word", score ? score + " of " + max : "not rated");
      var group = el("span", "sat-meter", "");
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", "Your satisfaction with " + name);
      for (var i = 1; i <= max; i += 1) {
        group.appendChild(satButton(name, i, score, max, note));
      }
      row.appendChild(group);
      row.appendChild(note);
      return row;
    }

    function satButton(name, value, score, max, note) {
      var on = value <= score;
      var button = el("button", "sat-dot" + (on ? " on" : ""), String(value));
      button.type = "button";
      button.setAttribute("aria-pressed", value === score ? "true" : "false");
      button.setAttribute(
        "aria-label", "Score " + name + " " + value + " of " + max
          + (value === score ? " (press again to clear)" : ""));
      button.addEventListener("click", function () {
        // Pressing the current score clears it; see the comment above.
        var wanted = value === score ? 0 : value;
        note.textContent = "Saving\u2026";
        fetch("/api/project/satisfaction", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: name, score: wanted })
        })
          .then(json)
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && result.message) || "failed");
            // Reload rather than repainting the dots: what the file says is
            // what the picker will read, and the page has to show that
            // rather than what the tap implied.
            load();
          })
          .catch(function (e) {
            note.textContent = "Could not save: " + (e && e.message ? e.message : e);
          });
      });
      return button;
    }

    /* Where a project is in its life, and Nova's pending proposal for it.
     *
     * Milestone M5 of idea #260, and the third of that milestone's three
     * fields. The readiness meter above is drawn and never pressed; the
     * satisfaction row is pressed and never written by a cycle. This one is
     * both, because the spec splits it: *"lifecycle: I approve / Nova
     * proposes"*. So the stage itself is a word he cannot type here, and the
     * two buttons appear only while a proposal is actually waiting.
     *
     * No proposal draws no buttons at all, rather than a disabled pair. A
     * control that is always on screen and almost always dead reads as
     * broken, and there is nothing for him to do when nothing is proposed.
     */
    function renderProjectLifecycle(name, payload) {
      var rated = ((payload && payload.projectPriority) || {})[name.toLowerCase()];
      var stage = (rated && rated.lifecycle) || "";
      var proposed = (rated && rated.lifecycleProposed) || "";
      var row = el("div", "project-lifecycle");
      row.appendChild(el("span", "project-prio-label", "Lifecycle"));
      row.appendChild(el("span", "project-lifecycle-word", stage || "not set"));
      if (!proposed) return row;

      var note = el("span", "project-lifecycle-note",
        "Nova proposes: " + proposed);
      row.appendChild(note);
      row.appendChild(lifecycleButton(name, "approve", "Approve", proposed, note));
      row.appendChild(lifecycleButton(name, "decline", "Decline", proposed, note));
      return row;
    }

    function lifecycleButton(name, decision, label, proposed, note) {
      var button = el("button", "lifecycle-btn lifecycle-" + decision, label);
      button.type = "button";
      button.setAttribute(
        "aria-label", label + " moving " + name + " to " + proposed);
      button.addEventListener("click", function () {
        note.textContent = "Saving\u2026";
        fetch("/api/project/lifecycle", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: name, decision: decision })
        })
          .then(json)
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && result.message) || "failed");
            // Reload for `satButton`'s reason: what the file says is what
            // the picker reads, and the page has to show that.
            load();
          })
          .catch(function (e) {
            note.textContent = "Could not save: " + (e && e.message ? e.message : e);
          });
      });
      return button;
    }

    /* The project's own importance rating is gone, 2026-09-08: *"I do not
     * want the old priority anymore, only the placement sorting priority. So
     * remove the legacy priority from all places."*
     *
     * It was already the weaker of two orderings and it had been saying so
     * for a day: `nova_next.project_ranks` layers, so a project with a
     * position ranks by position and only an unplaced one falls back to this
     * rating. The ordered list is the dial now, and one dial that works beats
     * two that disagree -- which is what the caption on this control had been
     * apologising for.
     *
     * `POST /api/project/priority` and `/api/project/order` are gone from the
     * server too (issue #229): projects carry no priority.
     */


    /* The conversation about a project -- idea #92, phase 4.
     *
     * The owner's idea asks for *"somehow a conversation per project or per
     * issue/idea/note to define it more"*. The row level of that shipped in
     * August; this is the project level, and it is the last phase of #92.
     *
     * Two things it deliberately reuses rather than reinvents.
     * `renderRowConversation` draws the bubbles, so a project thread and a
     * board row thread are the same green and purple on the same page -- his
     * standing ask is that these read alike. And the thread is stored in
     * `comments.md`, which every cycle already reads at the top of its hour,
     * so a message here reaches the next cycle without anything new having to
     * collect it. That is the test this channel has to pass: the
     * `Needs Edvard` box was built, shipped and dead because nothing did.  (not-prose: quoting a literal)
     *
     * Unlike a board comment, this one may contain line breaks -- the file
     * stores his text verbatim -- so nothing here flattens what he typed.
     */
    function renderProjectThread(name, payload) {
      var section = el("section", "project-thread");
      // Its own class, not `project-board-head`: that one means "a board
      // section is here" and a test already counts on it.
      section.appendChild(el("h2", "project-thread-head", "Conversation"));
      var messages = (payload && payload.comments) || [];

      var wrap = el("div", "item-comment");
      var box = el("textarea", "item-comment-box");
      box.rows = 2;
      box.placeholder = "Say something about " + name + "…";
      var status = el("span", "item-comment-status", "");
      var send = el("button", "item-comment-send", "Comment");
      send.type = "button";

      function busy(on) {
        send.disabled = on;
        box.disabled = on;
      }

      send.addEventListener("click", function () {
        var text = box.value.trim();
        if (!text) {
          status.textContent = "Nothing to send.";
          status.className = "item-comment-status is-error";
          return;
        }
        busy(true);
        status.textContent = "sending…";
        status.className = "item-comment-status";
        fetch("/api/project/comment", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: name, text: text }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            box.value = "";
            busy(false);
            // Refetched rather than appended locally, like the board row
            // dropping its cached write-up: the page must show what the file
            // holds, not what this tab believes it sent.
            loadProject(name);
          })
          .catch(function (err) {
            status.textContent = String((err && (err.message || err)) || "failed");
            status.className = "item-comment-status is-error";
            busy(false);
          });
      });

      wrap.appendChild(box);
      var actions = el("div", "item-comment-actions");
      actions.appendChild(status);
      actions.appendChild(send);
      wrap.appendChild(actions);
      // The box above the thread, newest reply at its top. The owner, idea
      // #166: *"The input field is at the top and the comments are below it
      // with the newest reply at the top (basicly inverted for everything
      // else we have)."* A project conversation is something he checks rather
      // than reads through.
      section.appendChild(wrap);
      if (!messages.length) {
        section.appendChild(el("p", "empty",
          "Nothing said about " + name + " yet."));
      } else {
        var thread = el("div", "project-thread-messages");
        // A copy. `payload.comments` is read again every time a tab is
        // pressed, and reversing it in place would flip the order back on
        // the second press.
        renderRowConversation(thread, messages.slice().reverse());
        section.appendChild(thread);
      }
      return section;
    }

    /* Which part of a project is on screen -- idea #166. The owner: *"tabs/
     * buttons that say issues, ideas, conversation. When one of them is
     * pressed the relevant items are displayed and the others are hidden."*
     * `All` is the fourth and the default: the page he has today is the
     * combined view, and a tab strip that can only take things away is a
     * regression. Held in a variable rather than the URL -- a tab is a view
     * of a page, not a page, and it has to survive `loadProject` refetching
     * after every comment. The cost is that a tab is not linkable. */
    var projectTab = "all";
    var projectTabFor = "";

    /* The tabs this payload can actually offer. Drawn only when there is
     * something to split: on a project with no rows at all the strip would
     * read `All / Conversation`, which is two names for one screen, and an
     * empty list here means no strip.
     *
     * Separate from the drawing because the state below has to be checked
     * against it. My reviewer found the hole: holding the tab in a variable
     * and resetting it only when the *name* changes leaves it pointing at a
     * board that has since emptied -- he presses Issues, moves the last issue
     * to another project, comes back, and the boards loop skips Ideas because
     * the tab says `issues` while the strip is gone because there is nothing
     * to split. No strip, no rows, no comment box, and nothing on screen to
     * press. A tab has to be re-checked against every payload, not just
     * against the name. */
    function projectTabs(payload) {
      var boards = (payload && payload.boards) || {};
      var tabs = [{ key: "all", label: "All" }];
      if (boards.issues && boards.issues.total) {
        tabs.push({ key: "issues", label: "Issues · " + boards.issues.total });
      }
      if (boards.ideas && boards.ideas.total) {
        tabs.push({ key: "ideas", label: "Ideas · " + boards.ideas.total });
      }
      if (tabs.length < 2) return [];
      var count = ((payload && payload.comments) || []).length;
      tabs.push({ key: "conversation",
                  label: count ? "Conversation · " + count : "Conversation" });
      return tabs;
    }

    function projectTabState(name, tabs) {
      if (projectTabFor !== name) {
        projectTabFor = name;
        projectTab = "all";
      }
      var offered = tabs.some(function (tab) { return tab.key === projectTab; });
      if (!offered) projectTab = "all";
      return projectTab;
    }

    function renderProjectTabs(tabs, redraw) {
      if (!tabs.length) return null;
      var row = el("div", "filters project-tabs");
      tabs.forEach(function (tab) {
        var on = projectTab === tab.key;
        var chip = el("button", "filter project-tab" + (on ? " on" : ""), tab.label);
        chip.type = "button";
        chip.setAttribute("data-tab", tab.key);
        chip.addEventListener("click", function () {
          projectTab = tab.key;
          // Redrawn from the payload in hand, not refetched: a round trip
          // would blank the rows the page is already holding.
          redraw();
        });
        row.appendChild(chip);
      });
      return row;
    }

    /* One board section, as a builder so the loop below can cache it. */
    function projectBoardBuilder(key, board) {
      return function () {
        var names = { issues: "Issues", ideas: "Ideas" };
        var section = el("section", "project-board");
        var head = el("h2", "project-board-head", names[key] + " · " + board.total);
        var link = el("a", "project-board-link", "open board");
        link.setAttribute("href", "/" + key);
        head.appendChild(link);
        section.appendChild(head);
        var cols = el("div", "project-columns");
        for (var c = 0; c < board.columns.length; c++) {
          cols.appendChild(renderProjectColumn(key, board.columns[c]));
        }
        section.appendChild(cols);
        return section;
      };
    }

    /* The project page's sections keep their nodes (issue #233, step 10).
     *
     * A tab press calls this again with the payload in hand, and it used to
     * empty `feed` first -- so the conversation box was rebuilt and anything
     * half-typed into it went with it. Each section below reads exactly one
     * field of the payload, so signing on that field is precise: a tab press
     * keeps every section the tab does not choose, and the refetch after a
     * comment redraws the conversation and nothing else. The cache hangs off
     * `feed` under the project's name, so a different project starts empty,
     * and a section not drawn this pass stays in it -- what carries a
     * half-typed comment across all -> ideas -> all. */
    function renderProject(payload) {
      stopPolling();
      markNav();
      var name = (payload && payload.name) || "";
      var asked = (payload && payload.asked) || "";
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el("p", "status-line",
        name ? name : "Projects"));

      if (!asked) {
        // The index is where he decides which project to open, so it shows
        // where each one stands rather than telling him to pick one blind.
        feed.textContent = "";
        feed.novaProjectCards = null;
        var standings = renderProjectStandings(payload);
        if (standings) feed.appendChild(standings);
        else feed.appendChild(el("p", "empty", "Pick a project."));
        return;
      }
      // Asked for a name no row carries. Said plainly rather than 404'd: he
      // types the project into a board cell, so a name with nothing under it
      // is one he has not filed anything to yet, not a broken link.
      if (!name) {
        feed.textContent = "";
        feed.novaProjectCards = null;
        feed.appendChild(el("p", "empty",
          "Nothing is filed under “" + asked + "” yet."));
        return;
      }

      var cache = feed.novaProjectCards;
      if (!cache || cache.name !== name) {
        cache = feed.novaProjectCards = { name: name, nodes: {} };
      }
      var nodes = cache.nodes;
      var rows = [];
      function sigOf(field) {
        return name + "\u0000" + JSON.stringify(
          payload[field] === undefined ? null : payload[field]);
      }
      function section(key, sig, build) {
        var was = nodes[key];
        var node = was && was.sig === sig ? was.node : build();
        if (!node) { delete nodes[key]; return null; }
        nodes[key] = { node: node, sig: sig };
        rows.push({ node: node, key: key, sig: sig });
        return node;
      }

      // All three bars read one field, so they move together.
      var bars = sigOf("projectPriority");
      section("trl", bars, function () { return renderProjectTrl(name, payload); });
      section("satisfaction", bars, function () { return renderProjectSatisfaction(name, payload); });
      section("lifecycle", bars, function () { return renderProjectLifecycle(name, payload); });
      section("summary", sigOf("summary"), function () { return renderProjectSummary(payload); });
      // Directly under the bar: the bar says how far along the project is,
      // this says what it is broken into, which is the next question.
      section("milestones", sigOf("milestones"), function () { return renderProjectMilestones(name, payload); });
      // Above the ordered list: the roadmap says whether this project is
      // ahead of the others, the list below says what to take next inside it,
      // and the first question comes first.
      section("roadmap", sigOf("roadmap"), function () { return renderProjectRoadmap(payload); });
      section("backlog", sigOf("backlog"), function () { return renderProjectBacklog(payload); });

      var tabs = projectTabs(payload);
      var tab = projectTabState(name, tabs);
      section("tabs", name + "\u0000" + tab + "\u0000" + JSON.stringify(tabs), function () {
        return renderProjectTabs(tabs, function () { renderProject(payload); });
      });

      // Built before the boards so the jump can point at it, pushed after
      // them so the reading order does not change.
      var threadSig = sigOf("comments");
      var thread = null;
      if (tab === "all" || tab === "conversation") {
        var had = nodes.thread;
        thread = had && had.sig === threadSig ? had.node : renderProjectThread(name, payload);
        nodes.thread = { node: thread, sig: threadSig };
      }
      // Only in the combined view. On the conversation tab the thread is the
      // whole page, so a button that scrolls to it has nowhere to go.
      if (tab === "all") {
        section("jump", threadSig, function () { return projectThreadJump(thread, payload); });
      }

      var boards = (payload && payload.boards) || {};
      var drew = false;
      var order = ["issues", "ideas"];
      for (var b = 0; b < order.length; b++) {
        var key = order[b];
        if (tab !== "all" && tab !== key) continue;
        var board = boards[key];
        if (!board || !board.total) continue;
        drew = true;
        section("board-" + key, name + "\u0000" + JSON.stringify(board),
          projectBoardBuilder(key, board));
      }
      // Only when he is looking at rows. On the conversation tab there are
      // none by his own choice, and saying nothing is filed would argue with
      // the button he just pressed.
      if (!drew && tab !== "conversation") {
        var none = "Nothing is filed under “" + name + "” yet.";
        section("empty", none, function () { return el("p", "empty", none); });
      }
      // Below the boards on purpose: the rows are what the project *is* and
      // the conversation is what has been said about them -- the order a
      // board row puts its write-up above its thread.
      if (thread) rows.push({ node: thread, key: "thread", sig: threadSig });

      if (window.novaThread) return window.novaThread.render(feed, rows);
      feed.textContent = "";
      rows.forEach(function (r) { feed.appendChild(r.node); });
    }

    /* The jump from the top of a project page to its conversation. The owner,
     * comments board 2026-08-28: *"I see that the comment box is at the
     * bottom making me scroll all the way down. Not great ui."* The order
     * below it is right -- the rows are what the project is -- so the fix is
     * a way down, not a reshuffle. Its count is why it is a button and not an
     * anchor: "Conversation · 3" says there is something to read. Focusing
     * the box after the scroll is the point. */
    function projectThreadJump(thread, payload) {
      var count = ((payload && payload.comments) || []).length;
      var wrap = el("div", "project-jump-row");
      var button = el("button", "project-jump",
        count ? "Conversation · " + count : "Conversation");
      button.type = "button";
      button.addEventListener("click", function () {
        // jsdom implements neither, and the guard is the one the permalink
        // scroll already carries.
        if (thread.scrollIntoView) {
          thread.scrollIntoView({ behavior: "smooth", block: "start" });
        }
        var box = thread.querySelector(".item-comment-box");
        if (box && box.focus) box.focus();
      });
      wrap.appendChild(button);
      return wrap;
    }

    function loadProject(name) {
      fetchPage("/api/project?name=" + encodeURIComponent(name || ""))
        .then(function (payload) {
          // The same in-flight guard every other page fetch carries: two
          // taps in quick succession leave two fetches running and the
          // loser must not paint over the winner.
          var view = route(window.location.pathname).view;
          if (view !== "project" && view !== "projects") return;
          renderProject(payload);
        })
        .catch(function (err) {
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the project: " + err));
        });
    }

    function loadPool() {
      fetchPage("/api/pool")
        .then(function (payload) {
          // The same guard the plan, retro and costs fetches carry: two taps
          // in quick succession leave two fetches in flight and the loser
          // must not paint over the winner.
          if (route(window.location.pathname).view !== "pool") return;
          renderPool(payload);
        })
        .catch(function (err) {
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the pool: " + err));
        });
    }

    return {
      loadPool: loadPool,
      loadProject: loadProject,
    };
  };
})();
