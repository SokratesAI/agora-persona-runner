/* The `/plan` page (issue #233, step 18).
 *
 * The tenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js` and `home.js`. The scoreboard, the ranked roadmap
 * strip, the plan documents and the live "next up" list are only ever drawn
 * by this page, so they move together. The router calls `loadPlan`, and
 * that is the whole seam back.
 *
 * What it needs from `app.js` arrives as one argument, the same seam every
 * module before it uses. Two of the twelve borrowed names are values rather
 * than functions -- `feed` and `statusEl` -- and both are assigned at the
 * very top of `app.js` and never reassigned, so where this is bound does
 * not matter for them.
 */
(function () {
  "use strict";

  window.novaPlan = function (shared) {
    var el = shared.el;
    var feed = shared.feed;
    var fetchPage = shared.fetchPage;
    var json = shared.json;
    var markNav = shared.markNav;
    var renderBlocks = shared.renderBlocks;
    var renderSpans = shared.renderSpans;
    var route = shared.route;
    var savedCopyLine = shared.savedCopyLine;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* The `/plan` page: `roadmap.md` and `goals.md`, which until now reached
     * the owner only through Obsidian (issues.md #7, goals.md's own G2).
     *
     * No chart, no tiles, no summary line -- unlike every other non-journal
     * page here. Those exist because their source is a ledger of numbers and
     * a reader cannot hold 110 rows in their head. This source is two
     * arguments written to be argued with, and the useful thing to do with an
     * argument is show it. A tile saying "5 items on the roadmap" would be
     * this page's version of the noise he has twice asked me to stop putting
     * at the top of his files.
     *
     * The server sends blocks and spans, never HTML, and every node below is
     * built with textContent -- the same guarantee the journal card makes, and
     * the reason nothing here touches innerHTML. */
    /* One scoreboard row: a goal's name, this week's number, its target, and a
     * bar showing the gap between them.
     *
     * The bar encodes `now` and `target` on one shared scale and nothing else.
     * It is deliberately not a "percent complete" meter: three of the five
     * goals have no baseline to have progressed *from*, so any completion
     * figure would be a number I invented rather than one the file carries.
     * Length is `now / max(now, target)` and a tick sits at the target — read
     * it as "here is where I am, here is the line", which is true whichever
     * direction is good.
     *
     * Every value on the row is also printed as text. The colour and the tick
     * are a second encoding of a verdict the word "On target" already gives,
     * because a bar the owner has to decode a colour to read is the same failure
     * as the bare priority symbols he asked me to stop using. */
    /* A goal's past readings, as a line and as words.
     *
     * Idea #38 asked to "come back to the goals and see how much work has been
     * done towards them" and for "some history in some charts". Until now the
     * only number on this page was the current one — the weekly review wrote
     * `now:` over last week's on the way past, so nothing here could show a
     * direction. `goal-history.json` keeps the earlier readings and this draws
     * them.
     *
     * Two points is a line and one point is a dot, and both are drawn: a goal
     * measured once is a true state of the slate, and hiding its row until it
     * has "enough" history would make the chart appear a week after the goal.
     *
     * The dates and values are also printed as text under the line, for the
     * same reason the bar above prints its numbers — a shape the owner has to
     * squint at is not something I have told him. The line is the summary; the
     * text is the record. */
    var SPARK_W = 240;
    var SPARK_H = 34;
    var SPARK_PAD = 3;

    function svgEl(tag, className) {
      var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
      if (className) node.setAttribute("class", className);
      return node;
    }

    function sparkPoints(history) {
      var values = history.map(function (point) { return point.value; });
      var lo = Math.min.apply(null, values);
      var hi = Math.max.apply(null, values);
      var span = hi - lo;
      var inner = SPARK_H - SPARK_PAD * 2;
      return history.map(function (point, i) {
        // One point has no width to spread over and a flat series has no
        // height; both sit on the middle line rather than dividing by zero.
        var x = history.length === 1
          ? SPARK_W / 2
          : SPARK_PAD + (i / (history.length - 1)) * (SPARK_W - SPARK_PAD * 2);
        var y = span === 0
          ? SPARK_H / 2
          : SPARK_H - SPARK_PAD - ((point.value - lo) / span) * inner;
        return { x: x, y: y, point: point };
      });
    }

    function goalSparkline(goal) {
      var history = goal.history || [];
      if (!history.length) return null;

      var box = el("div", "goal-history");
      var chart = svgEl("svg", "goal-spark");
      chart.setAttribute("viewBox", "0 0 " + SPARK_W + " " + SPARK_H);
      chart.setAttribute("preserveAspectRatio", "none");
      // The line already has a text twin below it, so it is decoration to a
      // screen reader rather than content it should try to describe.
      chart.setAttribute("aria-hidden", "true");

      var marks = sparkPoints(history);
      if (marks.length > 1) {
        var line = svgEl("polyline", "goal-spark-line");
        line.setAttribute("points", marks.map(function (m) { return m.x + "," + m.y; }).join(" "));
        chart.appendChild(line);
      }
      marks.forEach(function (m, i) {
        var dot = svgEl("circle", "goal-spark-dot" + (i === marks.length - 1 ? " last" : ""));
        dot.setAttribute("cx", String(m.x));
        dot.setAttribute("cy", String(m.y));
        dot.setAttribute("r", i === marks.length - 1 ? "3" : "2");
        chart.appendChild(dot);
      });
      box.appendChild(chart);

      var unit = goal.unit ? " " + goal.unit : "";
      var words = history.map(function (point) {
        return point.date.slice(5) + " " + point.value + unit;
      }).join("  →  ");
      box.appendChild(el("p", "goal-history-text", words));
      return box;
    }

    function scoreboardRow(goal) {
      var row = el("li", "goal-row");
      var head = el("div", "goal-head");
      head.appendChild(el("span", "goal-name", goal.name));
      if (goal.onTarget === true) head.appendChild(el("span", "goal-verdict on", "On target"));
      else if (goal.onTarget === false) head.appendChild(el("span", "goal-verdict off", "Off target"));
      row.appendChild(head);
      if (goal.measure) row.appendChild(el("p", "goal-measure", goal.measure));

      var figures = el("p", "goal-figures");
      var now = goal.now === "" || goal.now == null ? "not measured yet" : String(goal.now);
      figures.appendChild(el("span", "goal-now", now + (goal.unit ? " " + goal.unit : "")));
      if (goal.target !== "" && goal.target != null) {
        figures.appendChild(el("span", "goal-target", "target " + goal.target));
      } else {
        figures.appendChild(el("span", "goal-target", "no target set"));
      }
      row.appendChild(figures);

      // A bar needs both numbers to say anything. One of them missing is the
      // ordinary case for a goal whose number is still a sentence, and the
      // row above already carries it.
      var nowValue = goal.nowValue;
      var targetValue = goal.targetValue;
      if (typeof nowValue === "number" && typeof targetValue === "number") {
        var scale = Math.max(Math.abs(nowValue), Math.abs(targetValue));
        var track = el("div", "goal-track");
        var fill = el("div", "goal-fill" + (goal.onTarget === true ? " on" : goal.onTarget === false ? " off" : ""));
        // A scale of zero means both numbers are zero, which is on target and
        // has no gap to draw — a full bar says that better than an empty one.
        fill.style.width = (scale === 0 ? 100 : (Math.abs(nowValue) / scale) * 100) + "%";
        track.appendChild(fill);
        var tick = el("div", "goal-tick");
        tick.style.left = (scale === 0 ? 100 : (Math.abs(targetValue) / scale) * 100) + "%";
        track.appendChild(tick);
        row.appendChild(track);
      }

      var spark = goalSparkline(goal);
      if (spark) row.appendChild(spark);
      row.appendChild(goalVerdict(goal));
      return row;
    }

    /* The words for what the owner has said about a goal, and the word for
     * the tap that says it. `goals.md` has told him since it was written
     * that "nothing here is settled until you edit it", and editing it meant
     * Obsidian on a phone -- so in ten days he settled nothing.
     *
     * The word travels with the state (personality.md, his ask on 08-20:
     * pair the symbol with the word). "Proposed" is not a failure state and
     * is not styled like one; it is me still waiting on him. */
    var GOAL_STATE_WORDS = {
      proposed: "🟡 Awaiting your call",
      approved: "🟢 Approved",
      declined: "⚪ Struck"
    };

    /* Approve / Strike on one goal, plus the state it is in now.
     *
     * Both buttons are always present, including on a goal already in that
     * state: this is the one control on the page whose whole purpose is to
     * be reversible, and hiding the way back would make "Struck" a decision
     * he could not take back without Obsidian -- which is the thing being
     * fixed. The button for the current state is the one that reads as
     * pressed, and tapping it again is a no-op the server answers "already".
     *
     * Buttons go disabled while a write is in flight so a double-tap cannot
     * race two writes at one goal, and on failure the state snaps back to
     * what the server still holds rather than showing a tick never written. */
    function goalVerdict(goal) {
      var box = el("div", "goal-verdict-row");
      var state = el("span", "goal-state " + (goal.status || "proposed"),
        GOAL_STATE_WORDS[goal.status] || GOAL_STATE_WORDS.proposed);
      box.appendChild(state);
      var note = el("span", "goal-state-note", "");
      var buttons = [];

      function draw() {
        buttons.forEach(function (b) {
          b.el.className = "goal-state-btn" + (goal.status === b.status ? " current" : "");
          b.el.setAttribute("aria-pressed", goal.status === b.status ? "true" : "false");
        });
        state.className = "goal-state " + (goal.status || "proposed");
        state.textContent = GOAL_STATE_WORDS[goal.status] || GOAL_STATE_WORDS.proposed;
      }

      [["approved", "Approve"], ["declined", "Strike"]].forEach(function (pair) {
        var button = el("button", "goal-state-btn", pair[1]);
        button.type = "button";
        button.setAttribute("data-goal-status", pair[0]);
        button.addEventListener("click", function () {
          var was = goal.status;
          buttons.forEach(function (b) { b.el.disabled = true; });
          note.textContent = "Saving…";
          fetch("/api/goal/status", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: goal.name, status: pair[0] })
          })
            .then(json)
            .then(function (payload) {
              if (!payload || !payload.ok) throw new Error((payload && payload.message) || "failed");
              goal.status = pair[0];
              note.textContent = "";
            })
            .catch(function (err) {
              goal.status = was;
              note.textContent = "Could not save: " + err;
            })
            .then(function () {
              buttons.forEach(function (b) { b.el.disabled = false; });
              draw();
            });
        });
        buttons.push({ el: button, status: pair[0] });
        box.appendChild(button);
      });

      box.appendChild(note);
      draw();
      return box;
    }

    function renderScoreboard(goals) {
      var box = el("section", "goal-board");
      box.appendChild(el("h3", "goal-board-title", "Where the goals stand"));
      var list = el("ul", "goal-list");
      goals.forEach(function (goal) {
        list.appendChild(scoreboardRow(goal));
      });
      box.appendChild(list);
      box.appendChild(el("p", "goal-board-note", "The reasoning behind each number is below."));
      return box;
    }

    /* One card of the roadmap's ranked strip: its rank, its title, a status
     * chip, the one-sentence claim and the board row it came from.
     *
     * The chip prints the symbol and the word together, always, and the server
     * sends both or neither -- the owner cannot tell the coloured circles apart by
     * colour and asked for the word beside the symbol on 2026-08-20. A status
     * the server did not recognise arrives with both fields empty and gets no
     * chip at all, which is the page declining to guess rather than defaulting
     * to Backlog.
     *
     * There is no bar and no number here on purpose. This strip answers "what
     * is next and where is it", and the scoreboard above it is the only thing
     * on this page with a figure worth drawing.
     *
     * The board reference under the claim is a link. It names the row this card
     * came from -- `issue #131, idea #179` -- and until now it was plain text on
     * a page whose whole complaint (issue #96) is that it does not connect to
     * anything. The href comes from the server, never from this text. */
    function rankedCard(item) {
      var card = el("li", "rank-card");
      var head = el("div", "rank-head");
      if (item.rank) head.appendChild(el("span", "rank-num", String(item.rank)));
      head.appendChild(el("span", "rank-title", item.title));
      if (item.statusLabel) {
        head.appendChild(
          el("span", "rank-chip", (item.statusSymbol ? item.statusSymbol + " " : "") + item.statusLabel)
        );
      }
      card.appendChild(head);
      if (item.claim) card.appendChild(el("p", "rank-claim", item.claim));
      // `boardSpans` carries `issue #131` as a link to `/issues#131`, the same
      // way a journal card's `Board:` footer does -- the server parses it, this
      // never reads a number out of the text. A card written before the server
      // sent spans still has the plain string, so that is the fallback rather
      // than a blank line.
      if (item.boardSpans && item.boardSpans.length) {
        var board = el("p", "rank-board");
        renderSpans(board, item.boardSpans);
        card.appendChild(board);
      } else if (item.board) {
        card.appendChild(el("p", "rank-board", item.board));
      }
      return card;
    }

    /* The ranked strip, in two lists: what is still ahead, then what is not.
     *
     * The server splits them (`nova_plan._split_ranked`) because the heading
     * "What I would do next, in order" is a claim about every card under it,
     * and a ✅ chip on the card does not retract it. On 2026-08-25 three of
     * the five cards were finished and the strip said all five were next.
     *
     * The finished list is kept on the page rather than dropped. The file
     * numbers these items once and never renumbers, so a strip that showed
     * only 1 and 4 would read as though 2, 3 and 5 had gone missing -- and
     * seeing what has closed is half of why the owner asked for the page.
     *
     * When nothing is open the empty list is the whole message: the document
     * has been overtaken and needs rewriting, which is exactly what a stale
     * `roadmap.md` looks like from the outside. */
    function renderRanked(items, done) {
      var box = el("section", "rank-strip");
      var open = items || [];
      var closed = done || [];
      box.appendChild(el("h3", "rank-strip-title", "What I would do next, in order"));
      if (open.length) {
        var list = el("ol", "rank-list");
        open.forEach(function (item) {
          list.appendChild(rankedCard(item));
        });
        box.appendChild(list);
        box.appendChild(el("p", "rank-strip-note", "The argument for each one is below."));
      } else {
        box.appendChild(
          el("p", "empty", "Nothing on this list is still open — it needs rewriting.")
        );
      }
      if (closed.length) {
        box.appendChild(el("h3", "rank-strip-title rank-done-title", "Already finished"));
        var doneList = el("ol", "rank-list rank-done-list");
        closed.forEach(function (item) {
          doneList.appendChild(rankedCard(item));
        });
        box.appendChild(doneList);
      }
      return box;
    }

    /* One section of a plan document, folded under its own heading.
     *
     * `/plan` was 4,961 words in one scroll with no entry point but the top
     * -- issue #96, in the owner's words "just a huge wall of text. I hate
     * that." The scoreboard and the ranked strip above answer the page's two
     * questions; this puts the reasoning behind a control instead of
     * deleting it, which is the half he has twice asked to keep.
     *
     * The server decides what is open, not this function: `section.open` is
     * true for the standfirst and for the newest entry of a dated stack.
     * Doing it here would mean matching heading text in two places.
     *
     * Two things stay unfolded on purpose. Level 0 has no heading -- it is
     * the standfirst, and in `goals.md` it is the paragraph saying the slate
     * is a proposal, so a `<summary>` would have nothing to print and the
     * one sentence that stops him misreading the page would be behind a
     * click. And a heading with an empty body renders plainly: a `<details>`
     * that opens onto nothing is a control that lies. */
    function planSection(section) {
      var headingTag = section.level >= 3 ? "h4" : "h3";
      var blocks = section.blocks || [];
      if (!section.heading || !blocks.length) {
        var plain = el("section", "plan-section");
        if (section.heading) plain.appendChild(el(headingTag, "plan-heading", section.heading));
        renderBlocks(plain, blocks);
        return plain;
      }
      var fold = el("details", "plan-section plan-fold");
      if (section.open) fold.open = true;
      var summary = el("summary", "plan-summary");
      summary.appendChild(el(headingTag, "plan-heading", section.heading));
      fold.appendChild(summary);
      var body = el("div", "plan-fold-body");
      renderBlocks(body, blocks);
      fold.appendChild(body);
      return fold;
    }

    /* Issue #227's own title, as one sentence: how many projects have no
     * goal, and which. It sits directly under the card title because it is
     * the one fact this card cannot show any other way -- a project with no
     * objective has no section in the document, so the prose below reads as
     * complete however many are missing.
     *
     * Nothing at all when the payload has no `coverage`, which is what the
     * server sends when it could not read both boards. A count built from
     * an unread board would be smaller and would look better, which is
     * exactly why it is not drawn. `total` of zero is the same case one step
     * on: two boards holding no project between them is not a page that
     * should announce "0 of 0". */
    function renderCoverage(cov) {
      if (!cov || !cov.total) return null;
      var missing = cov.missing || [];
      var wrap = el("p", "plan-coverage");
      if (!missing.length) {
        wrap.appendChild(el("span", "plan-coverage-count",
          "All " + cov.total + " projects have a goal."));
        return wrap;
      }
      wrap.appendChild(el("span", "plan-coverage-count",
        missing.length + " of " + cov.total + " projects have no goal:"));
      missing.forEach(function (entry) {
        var chip = el("span", "plan-coverage-chip", entry.project);
        /* The open-row count is the part that says whether this matters:
         * a project with no goal and no open rows is a pruning question,
         * one with nine is work nobody has said the point of. */
        chip.appendChild(el("span", "plan-coverage-rows",
          entry.openRows + " open"));
        wrap.appendChild(chip);
      });
      return wrap;
    }

    function renderPlanDocument(doc) {
      var card = el("article", "plan-card");
      var head = el("header", "plan-head");
      head.appendChild(el("h2", "plan-title", doc.title));
      if (doc.updated) head.appendChild(el("p", "plan-updated", "Updated " + doc.updated));
      card.appendChild(head);
      if (doc.missing) {
        card.appendChild(el("p", "empty", "Not written yet."));
        return card;
      }
      var coverage = renderCoverage(doc.coverage);
      if (coverage) card.appendChild(coverage);
      // Above the prose, because it is the answer and the prose is the
      // argument for it. A document with no `goal` blocks gets nothing here
      // and renders exactly as it did before this existed.
      if ((doc.scoreboard || []).length) card.appendChild(renderScoreboard(doc.scoreboard));
      if ((doc.ranked || []).length || (doc.rankedDone || []).length) {
        card.appendChild(renderRanked(doc.ranked, doc.rankedDone));
      }
      (doc.sections || []).forEach(function (section) {
        card.appendChild(planSection(section));
      });
      return card;
    }

    /* The live half of the plan page -- what a cycle waking up right now
     * would take, and which project it is filed under.
     *
     * The owner, 2026-08-30 survey, rating my legibility 2 of 5: *"I have no
     * idea on your plan for the next cycle or what different projects are
     * currently prioritised"*. Everything under this card is prose I wrote
     * on 2026-08-16 and have not rewritten since, which is exactly how a
     * hand-maintained plan fails -- so this one is computed on every request
     * from his two boards and the claims ledger and cannot go stale without
     * the boards going stale.
     *
     * Order is `prompt.md` step 2's, not a new opinion: his unprocessed
     * captures first, then the ranked board. `Now` is above both because it
     * is the only line on this page about this minute rather than the next
     * hour. */
    function nextRow(row) {
      var li = el("li", "next-row");
      var num = (row.board === "issue" ? "issue #" : "idea #") + row.number;
      li.appendChild(el("span", "next-num", num));
      li.appendChild(el("span", "next-title", row.title));
      if (row.priority) li.appendChild(el("span", "next-chip", row.priority));
      if (row.project) li.appendChild(el("span", "next-chip next-project", row.project));
      if (row.heldBy) li.appendChild(el("span", "next-chip next-held", "cycle " + row.heldBy + " is on it"));
      return li;
    }

    function renderNextUp(payload) {
      // Deliberately not a `plan-card`: that class means "one of the two prose
      // documents" to every existing page test, and quietly becoming a third
      // one would make those tests count this card as a document.
      var card = el("article", "next-card");
      card.appendChild(el("h2", "next-card-title", "What happens next"));
      card.appendChild(el("p", "next-card-note", "Computed from your two boards every time you open this page — nothing here is hand-written."));

      var active = payload.active || [];
      var now = el("section", "next-block");
      now.appendChild(el("h3", "next-heading", "Right now"));
      if (!active.length) {
        // Not "nothing is happening": an empty ledger means no cycle holds
        // anything this minute, which between cycles is the normal state.
        now.appendChild(el("p", "empty", payload.claimsReadable
          ? "No cycle is holding an item this minute."
          : "I could not read the claims ledger, so I cannot say."));
      } else {
        var live = el("ul", "next-list");
        active.forEach(function (claim) {
          var li = el("li", "next-row");
          li.appendChild(el("span", "next-num", "cycle " + claim.cycle));
          li.appendChild(el("span", "next-title", claim.title || claim.item));
          live.appendChild(li);
        });
        now.appendChild(live);
      }
      card.appendChild(now);

      var captures = payload.captures || [];
      if (captures.length) {
        var cap = el("section", "next-block");
        cap.appendChild(el("h3", "next-heading", "Your unfiled notes — these come first"));
        var capList = el("ul", "next-list");
        captures.forEach(function (capture) {
          var li = el("li", "next-row");
          li.appendChild(el("span", "next-num", capture.board));
          li.appendChild(el("span", "next-title", capture.text));
          capList.appendChild(li);
        });
        cap.appendChild(capList);
        card.appendChild(cap);
      }

      var rows = payload.next || [];
      var upcoming = el("section", "next-block");
      upcoming.appendChild(el("h3", "next-heading", "Then, in this order"));
      if (!rows.length) {
        upcoming.appendChild(el("p", "empty", "Nothing open on either board."));
      } else {
        var list = el("ul", "next-list");
        rows.forEach(function (row) { list.appendChild(nextRow(row)); });
        upcoming.appendChild(list);
      }
      card.appendChild(upcoming);

      var projects = payload.projects || [];
      if (projects.length) {
        var proj = el("section", "next-block");
        proj.appendChild(el("h3", "next-heading", "Projects, most urgent first"));
        var plist = el("ul", "next-list");
        projects.forEach(function (project) {
          var li = el("li", "next-row");
          li.appendChild(el("span", "next-num", project.name));
          li.appendChild(el("span", "next-title", project.top));
          li.appendChild(el("span", "next-chip", project.open + " open"));
          plist.appendChild(li);
        });
        proj.appendChild(plist);
        card.appendChild(proj);
      }
      return card;
    }

    function renderPlan(payload) {
      stopPolling();
      markNav();
      var docs = payload.documents || [];
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el("p", "status-line", "What I would do next, and what it is for"));
      if (payload.replayed) statusEl.appendChild(savedCopyLine());
      feed.textContent = "";
      // The live card is rendered whether or not the two prose documents
      // loaded: it is the half he said was missing, and a failed fetch of
      // the roadmap is no reason to hide what happens next.
      if (payload.nextUp) feed.appendChild(renderNextUp(payload.nextUp));
      if (!docs.length) {
        if (!payload.nextUp) feed.appendChild(el("p", "empty", "Nothing here yet."));
        return;
      }
      docs.forEach(function (doc) {
        feed.appendChild(renderPlanDocument(doc));
      });
    }

    function loadPlan() {
      // Two fetches, joined here rather than merged on the server: the plan
      // documents are cached because they change on the day I rewrite them,
      // and the live card must not inherit that. A failed `/api/next` still
      // paints the prose -- `nextUp` is simply absent.
      Promise.all([
        fetchPage("/api/plan"),
        fetchPage("/api/next").catch(function () { return null; })
      ])
        .then(function (both) {
          var payload = both[0] || {};
          payload.nextUp = both[1];
          // The same guard the retro and costs fetches carry: two taps in
          // quick succession leave two fetches in flight and the loser must
          // not paint over the winner.
          if (route(window.location.pathname).view !== "plan") return;
          renderPlan(payload);
        })
        .catch(function (err) {
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the plan: " + err));
        });
    }

    return {
      loadPlan: loadPlan,
    };
  };
})();
