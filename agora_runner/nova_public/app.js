/* Nova's journal, rendered.
 *
 * Every node here is built with document.createElement and textContent.
 * There is no innerHTML in this file and there should never be one: the
 * server sends structured blocks (nova_journal.render_blocks) precisely
 * so that markup is something this client cannot produce, rather than
 * something it has to remember to escape. If you add a feature that
 * wants innerHTML, add a block type instead.
 */
(function () {
  "use strict";

  var feed = document.getElementById("feed");
  var statusEl = document.getElementById("status");
  var needsEl = document.getElementById("needs");

  /** `/cycle/49` -> 49. Anything else -> null (show the whole feed). */
  function routedCycle(pathname) {
    var match = /^\/cycle\/(\d+)\/?$/.exec(pathname || "");
    return match ? parseInt(match[1], 10) : null;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function renderSpans(parent, spans) {
    (spans || []).forEach(function (span) {
      if (span.kind === "code") parent.appendChild(el("code", null, span.text));
      else if (span.kind === "strong") parent.appendChild(el("strong", null, span.text));
      else if (span.kind === "link") {
        // The href is a separate field from the server, never parsed out of
        // the text here -- same reason nothing in this file touches
        // innerHTML. New tab because leaving the PWA for GitHub and having
        // to navigate back is the worse of the two on a phone.
        var anchor = el("a", "pr-link", span.text);
        anchor.href = span.url;
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        parent.appendChild(anchor);
      } else parent.appendChild(document.createTextNode(span.text));
    });
  }

  /** Blocks -> nodes. Consecutive `li` blocks are gathered into one list;
   * the server emits them flat because a bullet run is just adjacency. */
  function renderBlocks(parent, blocks) {
    var list = null;
    (blocks || []).forEach(function (block) {
      if (block.type === "li") {
        if (!list) {
          list = el("ul");
          parent.appendChild(list);
        }
        var item = el("li");
        renderSpans(item, block.spans);
        list.appendChild(item);
        return;
      }
      list = null;
      if (block.type === "code") {
        var pre = el("pre");
        pre.appendChild(el("code", null, block.text));
        parent.appendChild(pre);
        return;
      }
      var para = el("p");
      renderSpans(para, block.spans);
      parent.appendChild(para);
    });
  }

  /** merged/shipped read as wins, stuck/no-op as not. Anything unrecognised
   * gets the neutral class rather than being guessed at. */
  function outcomeClass(outcome) {
    var value = (outcome || "").toLowerCase();
    if (/merged|shipped/.test(value)) return "badge badge-good";
    if (/stuck|no-op|none/.test(value)) return "badge badge-warn";
    return "badge";
  }

  function renderStatus(status) {
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));

    var parts = [];
    if (status.cycle !== null && status.cycle !== undefined) parts.push("Cycle " + status.cycle);
    if (status.runningDays) parts.push("running " + status.runningDays + " days");
    if (status.lastWokeTime) parts.push("last woke " + status.lastWokeTime);
    else if (status.lastWokeDate) parts.push("last woke " + status.lastWokeDate);
    statusEl.appendChild(el("p", "status-line", parts.join(" · ")));

    if (status.lastOutcome) {
      var line = el("p", "status-sub");
      line.appendChild(el("span", outcomeClass(status.lastOutcome), status.lastOutcome));
      if (status.lastPr) line.appendChild(el("span", "status-pr", status.lastPr));
      if (status.lastOutcomeDetail) {
        line.appendChild(el("span", "status-pr", status.lastOutcomeDetail));
      }
      statusEl.appendChild(line);
    }
  }

  function renderNeeds(digest, comments) {
    // Item 3: pinned when it has something, completely absent when it
    // doesn't -- not a box saying "Nothing".
    if (!digest || !digest.hasNeedsEdvard) {
      needsEl.hidden = true;
      return;
    }
    var body = needsEl.querySelector(".needs-body");
    body.textContent = "";
    renderBlocks(body, digest.needsEdvardBlocks);

    /* Edvard, 2026-08-10: "the 'needs Edvard' is still missing a comment
     * block, so its hard for me to answer it. [...] Where did you intend
     * me to answer it? [...] I want a reply button on it."
     *
     * Idea #56 sat in this block unanswered for eight cycles. Reading that
     * as him not getting to it was wrong: the block asked a question and
     * gave him nowhere to type, and the capture box below it files
     * backlog bullets, which is not what an answer is.
     *
     * Unlike a journal card the box is open rather than behind the 💬
     * toggle, and that is the one deliberate difference. A card's drawer
     * is folded because there are seventy-odd cards and almost none of
     * them want a comment. This section exists *only* when I am asking him
     * something -- it is `hidden` otherwise -- so there is no state in
     * which an always-visible answer field is noise, and a fold is exactly
     * the thing that hid the problem for eight cycles. */
    var old = needsEl.querySelector(".comment-drawer");
    if (old) needsEl.removeChild(old);
    renderComments(needsEl, needsTarget(), (comments && comments.needs) || []);

    needsEl.hidden = false;
  }

  var nextBodyId = 0;

  /** The first paragraph of the entry, for cycles with no digest line.
   *
   * That is not the corner case it sounds like: measured against the live
   * files, 40 of 57 entries have none, because the digest is rewritten
   * every cycle and its older lines have been dropped over time. So this
   * is the summary for most of the feed, not a fallback for a few. Without
   * it those 40 cards collapse to a row of dates. */
  function firstParagraph(blocks) {
    var found = (blocks || []).filter(function (block) { return block.type === "p"; })[0];
    if (!found) return "";
    return (found.spans || []).map(function (span) { return span.text; }).join("");
  }

  /* The comment drawer (ideas.md #44): "add a button with a chat bubble
   * icon that opens a multiline text input so that i can add a comment
   * more directly towards your cycles".
   *
   * It hangs off the card rather than the page because the cycle it is
   * about is the whole point -- the capture box at the top already exists
   * for anything that isn't about a particular cycle, and the difference
   * between the two is exactly what he was describing.
   *
   * Existing comments are shown above the box. He did not ask for that,
   * and it is here for one concrete reason rather than completeness: with
   * a write-only box there is no way to tell a saved comment from a lost
   * one except by opening Obsidian, which is the thing this feature exists
   * to avoid.
   *
   * The icon is an emoji rather than an SVG because this file may not
   * produce markup -- see the header. A glyph is textContent; an <svg>
   * would need innerHTML or createElementNS, and the first is banned here
   * for a good reason and the second buys nothing at this size. */
  /* `target` is what the drawer is attached to, so the same drawer serves
   * both a journal card and the Needs Edvard block:
   *   body(text)  -> the /api/comment payload naming that target
   *   pick(data)  -> that target's comments out of /api/comments
   *   placeholder, ariaLabel -> the words for it
   * Everything below is target-agnostic on purpose; the two differ only in
   * which four things they hand in. */
  function renderComments(container, target, comments) {
    var drawer = el("div", "comment-drawer");

    var list = el("div", "comment-list");
    drawer.appendChild(list);

    var box = el("textarea", "comment-text");
    box.rows = 3;
    box.placeholder = target.placeholder;
    box.setAttribute("autocapitalize", "sentences");
    drawer.appendChild(box);

    var actions = el("div", "comment-actions");
    var status = el("p", "comment-status");
    status.setAttribute("role", "status");
    actions.appendChild(status);
    var send = el("button", "comment-send", "Comment");
    send.type = "button";
    actions.appendChild(send);
    drawer.appendChild(actions);

    var toggle = el("button", "comment-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-label", target.ariaLabel);

    function paint(items) {
      list.textContent = "";
      (items || []).forEach(function (comment) {
        var item = el("div", comment.acknowledged ? "comment is-acknowledged" : "comment");
        var head = el("p", "comment-meta");
        head.appendChild(el("span", "comment-stamp", comment.stamp || ""));
        if (comment.acknowledged) head.appendChild(el("span", "comment-ack", "read"));
        item.appendChild(head);
        // The text is Edvard's own prose and the server sends it as plain
        // text, so each blank-line-separated paragraph becomes its own <p>.
        // Nothing here interprets it as markdown.
        String(comment.text || "").split(/\n{2,}/).forEach(function (para) {
          if (para.trim()) item.appendChild(el("p", "comment-body", para));
        });
        /* Nova's answer to this comment, or the fact that one is coming.
         * The bridge serialises every CLI call, so a reply posted while a
         * cycle is running can be forty minutes behind -- saying nothing
         * would read as broken. */
        if (comment.reply) {
          var reply = el("div", "comment-reply");
          var meta = el("p", "comment-meta");
          meta.appendChild(el("span", "comment-who", "Nova"));
          meta.appendChild(el("span", "comment-stamp", comment.replyStamp || ""));
          reply.appendChild(meta);
          String(comment.reply).split(/\n{2,}/).forEach(function (para) {
            if (para.trim()) reply.appendChild(el("p", "comment-body", para));
          });
          item.appendChild(reply);
        } else if (comment.replyPending) {
          item.appendChild(el("p", "comment-waiting", "Nova is replying…"));
        }
        list.appendChild(item);
      });
      var count = (items || []).length;
      toggle.textContent = count ? "💬 " + count : "💬";
      list.hidden = !count;
      watch((items || []).some(function (c) { return c.replyPending; }));
    }

    /* Poll only while the server says a reply is still coming, and stop the
     * moment it isn't. No cap and no give-up: the wait is bounded by the
     * bridge finishing, `replyPending` goes false either way (the worker
     * clears it even when the reply fails), and a timer that expired early
     * would leave a "replying…" line that never resolves. One handle, so a
     * repaint from the journal refresh cannot stack a second timer. */
    var timer = null;
    function watch(pendingNow) {
      if (!pendingNow) {
        if (timer) { clearTimeout(timer); timer = null; }
        return;
      }
      if (timer) return;
      timer = setTimeout(function () {
        timer = null;
        // See stopPolling: a render discards this drawer, and this is how
        // its poll is discarded with it.
        fetch("/api/comments")
          .then(function (r) { return r.json(); })
          .then(function (payload) { paint(target.pick(payload)); })
          .catch(function () { watch(true); });
      }, 8000);
      livePolls.push(timer);
    }

    paint(comments);

    function fit() {
      box.style.height = "auto";
      box.style.height = box.scrollHeight + "px";
    }

    function submit() {
      var text = box.value.trim();
      if (!text) {
        box.focus();
        return;
      }
      send.disabled = true;
      status.textContent = "saving…";
      status.className = "comment-status";
      fetch("/api/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(target.body(text)),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          // Only cleared once the server confirms the write -- the same
          // rule the capture box follows, for the same reason: a box that
          // wiped itself on a failure would lose what it exists to catch.
          box.value = "";
          fit();
          status.textContent = "saved";
          return fetch("/api/comments")
            .then(function (r) { return r.json(); })
            .then(function (payload) {
              paint(target.pick(payload));
            });
        })
        .catch(function (err) {
          status.textContent = String(err.message || err);
          status.className = "comment-status is-error";
        })
        .then(function () { send.disabled = false; });
    }

    send.addEventListener("click", submit);
    box.addEventListener("input", fit);
    /* Deliberately no Enter-to-send here, unlike the capture box.
     *
     * The two boxes look alike and are not: a capture is one line per item,
     * so Enter meaning "file it" costs nothing. Edvard asked for this one
     * to be "a multiline text input", and his own example runs to two
     * sentences -- so Enter has to be a newline, or every paragraph break
     * in the thing he asked to be able to write would need a modifier he
     * does not have on a phone keyboard. Consistency between the two boxes
     * would be consistency against what each is for. */

    container.appendChild(drawer);
    return { toggle: toggle, drawer: drawer };
  }

  function cycleTarget(cycle) {
    return {
      placeholder: "Say something about cycle " + cycle + "…",
      ariaLabel: "Comment on cycle " + cycle,
      body: function (text) { return { cycle: cycle, text: text }; },
      pick: function (data) { return ((data && data.byCycle) || {})[String(cycle)]; },
    };
  }

  function needsTarget() {
    return {
      placeholder: "Answer…",
      ariaLabel: "Reply to Needs Edvard",
      body: function (text) { return { target: "needs", text: text }; },
      pick: function (data) { return (data && data.needs) || []; },
    };
  }

  function renderEntry(entry, digestLine, expanded, anchored, comments) {
    var card = el("article", "entry");
    // Only the first card for a cycle takes the anchor id. Six cycles have
    // written more than one entry, and giving each the same element id made
    // the document invalid and getElementById reachable to only one of them.
    if (anchored && entry.cycle !== null && entry.cycle !== undefined) {
      card.id = "cycle-" + entry.cycle;
    }

    var bodyId = "entry-body-" + nextBodyId++;

    // The button holds the title row only. Everything that used to sit
    // inside it -- stamp, outcome, PR references -- moved out to the meta
    // row below, because the PR references are now links and an <a> inside
    // a <button> is invalid, the same reason the permalink was already
    // outside. Nothing is lost by moving them: the whole card is the tap
    // target now, so the button no longer has to be large to be reachable.
    var toggle = el("button", "entry-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-controls", bodyId);

    if (entry.emoji) {
      var emoji = el("span", "entry-emoji", entry.emoji);
      // Decorative: the text beside it already says what the cycle did, and
      // a screen reader announcing "police car light" helps nobody.
      emoji.setAttribute("aria-hidden", "true");
      toggle.appendChild(emoji);
    }

    var heading = el("h2");
    heading.appendChild(el("span", "cycle-link", entry.cycle !== null && entry.cycle !== undefined
      ? "Cycle " + entry.cycle
      : entry.title || "Note"));
    toggle.appendChild(heading);

    toggle.appendChild(el("span", "chevron", "▾"));

    var head = el("header", "entry-head");
    head.appendChild(toggle);
    // The permalink cannot live inside the button -- an <a> nested in a
    // <button> is invalid and phones disagree about which one a tap hits.
    if (entry.cycle !== null && entry.cycle !== undefined) {
      var link = el("a", "entry-permalink", "#");
      link.href = "/cycle/" + entry.cycle;
      link.setAttribute("aria-label", "Permalink to cycle " + entry.cycle);
      head.appendChild(link);
    }
    card.appendChild(head);

    var meta = el("div", "entry-meta");
    var stamp = [entry.date, entry.time].filter(Boolean).join(" ");
    if (stamp) meta.appendChild(el("time", "stamp", stamp + " Oslo"));
    if (entry.outcome) {
      meta.appendChild(el("span", outcomeClass(entry.outcome), entry.outcome));
    }
    if (entry.pr) {
      var pr = el("span", "pr");
      // prSpans carries the same text with each reference linkified. The
      // plain string is the fallback for a payload served by an older
      // build, so a stale cache degrades to exactly what it showed before.
      if (entry.prSpans && entry.prSpans.length) renderSpans(pr, entry.prSpans);
      else pr.textContent = entry.pr;
      meta.appendChild(pr);
    }
    // The qualifier five entries carry ("stuck — CI outage, merged nothing")
    // goes beside the pill, not inside it. Nothing is dropped.
    if (entry.outcomeDetail) meta.appendChild(el("span", "outcome-detail", entry.outcomeDetail));
    if (meta.childNodes.length) card.appendChild(meta);

    if (entry.title && entry.cycle !== null && entry.cycle !== undefined) {
      card.appendChild(el("p", "entry-title", entry.title));
    }

    /* Edvard, issues.md 2026-08-09: "a 2-3 line short precise Digest for
     * each cycle as a title for each journey card ... Then, when a journey
     * card is opened, the Digest is revealed. Below that, a 'read the full
     * journal' button to expand the full journal ... So its a drawer within
     * a drawer."
     *
     * Three levels, and the brief is the one that was missing. Until now
     * the collapsed card carried the whole digest line clamped to three
     * lines by CSS, so it always broke off mid-sentence -- the clamp is
     * why he asked. The brief comes from the server already cut on a
     * sentence boundary (nova_journal.split_brief), and the remainder is
     * this first drawer rather than something thrown away. */
    var briefSpans = (digestLine && digestLine.briefSpans) || entry.briefSpans;
    if (briefSpans && briefSpans.length) {
      var brief = el("p", "entry-brief");
      renderSpans(brief, briefSpans);
      card.appendChild(brief);
    } else {
      /* A payload with no briefSpans, which is reachable rather than
       * theoretical: sw.js is network-first and caches /api responses, so
       * opening the app with the tailnet down after this deploy pairs the
       * new app.js with the last payload the old build served.
       *
       * `is-unsplit` restores the CSS line clamp for that card only. Without
       * it the fallback degrades to something worse than what it replaced --
       * a whole 2000-character digest line as an unclamped card title -- and
       * "degrades to exactly what it showed before" is the only thing that
       * makes a fallback worth keeping. */
      var summaryText = digestLine ? digestLine.text : firstParagraph(entry.blocks);
      if (summaryText) card.appendChild(el("p", "entry-brief is-unsplit", summaryText));
    }

    // Drawer one: the rest of the digest line. Absent for the 55 entries
    // that have no digest line -- their remainder is the journal entry
    // itself, and printing the same paragraph in both drawers is worse
    // than opening straight onto the button.
    var restSpans = digestLine && digestLine.restSpans;
    if (restSpans && restSpans.length) {
      var rest = el("p", "entry-digest");
      renderSpans(rest, restSpans);
      card.appendChild(rest);
    }

    // Drawer two.
    var journalToggle = el("button", "journal-toggle", "Read the full journal");
    journalToggle.type = "button";
    journalToggle.setAttribute("aria-controls", bodyId);
    card.appendChild(journalToggle);

    var body = el("div", "entry-body");
    body.id = bodyId;
    renderBlocks(body, entry.blocks);
    card.appendChild(body);

    /* One comment button per cycle, on the card that owns the anchor.
     *
     * A comment is stored keyed by cycle number, so an entry with no number
     * has nowhere for one to land -- offering the button there would be a
     * box that silently drops what he typed. And the six cycles that wrote
     * a second entry are still one cycle: two buttons would be two places
     * to look for the same conversation, which is the confusion the
     * duplicate-looking cards caused before Cycle 64 split them. */
    var commenting = null;
    if (anchored && entry.cycle !== null && entry.cycle !== undefined) {
      commenting = renderComments(card, cycleTarget(entry.cycle), comments);
      head.appendChild(commenting.toggle);
    }

    function setCommentsOpen(open) {
      if (!commenting) return;
      card.classList.toggle("is-commenting", open);
      commenting.toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    setCommentsOpen(false);

    function setJournalOpen(open) {
      card.classList.toggle("is-reading", open);
      journalToggle.setAttribute("aria-expanded", open ? "true" : "false");
      journalToggle.textContent = open ? "Close the full journal" : "Read the full journal";
    }

    function setExpanded(open) {
      card.className = open ? "entry is-expanded" : "entry is-collapsed";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      // Closing the card closes the drawer inside it, so reopening never
      // lands you back in the middle of a 115-line entry you had left open.
      setJournalOpen(open && journalToggle.getAttribute("aria-expanded") === "true");
      // Assigning className above drops every other state class, so the
      // comment drawer has to be re-asserted rather than left to survive.
      // Unlike the journal drawer it is *not* closed by collapsing the
      // card: half-typed text would go with it.
      setCommentsOpen(!!commenting && commenting.toggle.getAttribute("aria-expanded") === "true");
    }
    setJournalOpen(false);
    setExpanded(!!expanded);

    /* Edvard, issues.md 2026-08-09: "i want to click anywhere on it to
     * expand/close it, not just the header."
     *
     * The listener sits on the card and the button has none of its own. A
     * button's click bubbles to here, including the synthetic one it fires
     * for Enter and Space, so keyboard support keeps working through the
     * same path rather than a second one that could drift.
     *
     * Two clicks are deliberately not a toggle. A tap on a link has
     * somewhere else to go -- the permalink and the PR references. And the
     * click that ends a drag-select would otherwise collapse the card out
     * from under the text just selected, which on a long entry means
     * losing your place to copy a sentence. */
    card.addEventListener("click", function (event) {
      if (event.target.closest("a")) return;
      var selection = window.getSelection();
      if (selection && !selection.isCollapsed && String(selection)) return;
      /* "If the full journal text is clicked or the button, the full
       * journal is closed again." Both land here rather than on their own
       * listeners, because the card's listener would otherwise fire too and
       * collapse the whole card out from under the tap. One listener, one
       * decision about what the tap meant. */
      /* The chat bubble opens its drawer without expanding the card: he
       * asked for a way to comment on a cycle, not to read it first. Both
       * this and the drawer below return before the collapse at the end,
       * for the same reason the journal toggle does. */
      if (commenting && event.target.closest(".comment-toggle")) {
        setCommentsOpen(commenting.toggle.getAttribute("aria-expanded") !== "true");
        return;
      }
      // A tap in the box, on Comment, or on an existing comment is not a
      // tap on the card. Without this, focusing the textarea would collapse
      // the card out from under it.
      if (event.target.closest(".comment-drawer")) return;
      if (event.target.closest(".journal-toggle")) {
        setJournalOpen(journalToggle.getAttribute("aria-expanded") !== "true");
        return;
      }
      if (event.target.closest(".entry-body")) {
        setJournalOpen(false);
        return;
      }
      setExpanded(toggle.getAttribute("aria-expanded") !== "true");
    });
    return card;
  }

  /* Every drawer that is waiting on a reply schedules its own poll, and a
   * render throws every drawer away and builds new ones. Without this the
   * discarded drawers keep polling into detached DOM -- one more immortal
   * poller per tap for as long as the reply takes. The new drawers pick the
   * poll straight back up if it is still pending, so cancelling here loses
   * nothing. */
  var livePolls = [];

  function stopPolling() {
    livePolls.forEach(function (handle) { clearTimeout(handle); });
    livePolls = [];
  }

  function render(journal, digest, comments) {
    stopPolling();
    renderStatus(journal.status || {});
    renderNeeds(digest, comments);

    var commentsByCycle = (comments && comments.byCycle) || {};

    var byCycle = {};
    ((digest && digest.lines) || []).forEach(function (line) {
      byCycle[line.cycle] = line;
    });

    var wanted = routedCycle(window.location.pathname);
    var entries = journal.entries || [];
    if (wanted !== null) {
      entries = entries.filter(function (entry) {
        return entry.cycle === wanted;
      });
    }

    /* Edvard, issues.md 2026-08-09: "Why are the journals for cycles 55-57
     * listed twice in the Nova app?" They are not duplicates -- each of
     * those cycles wrote a second entry when it went back to verify its own
     * deploy. What made them look identical is that a digest line was
     * looked up by cycle number, so both cards showed the same heading and
     * byte-identical summary text.
     *
     * 55, 56 and 57 are exactly the cycles that have both a second entry
     * and a digest line. Cycles 6, 12 and 30 also have addenda and never
     * looked wrong, because they have no digest line to hand out twice --
     * which is why he named three and not six.
     *
     * A digest line describes the work of the cycle, so it belongs to that
     * cycle's own run: its earliest entry, which is its last one in this
     * newest-first feed. Every addendum then summarises itself out of its
     * own first paragraph, and the two cards say different things. The
     * first card of a cycle also takes the anchor id, for the same reason
     * an id can only belong to one element. */
    var digestOwner = {};
    var anchorOwner = {};
    entries.forEach(function (entry, index) {
      if (entry.cycle === null || entry.cycle === undefined) return;
      digestOwner[entry.cycle] = index;
      if (!(entry.cycle in anchorOwner)) anchorOwner[entry.cycle] = index;
    });

    feed.textContent = "";
    if (wanted !== null) {
      var back = el("a", "back", "← all cycles");
      back.href = "/";
      feed.appendChild(back);
      if (!entries.length) feed.appendChild(el("p", "empty", "No entry for cycle " + wanted + "."));
    }
    // A single cycle you navigated to deliberately opens expanded; there is
    // nothing to scan past on that page, which is the only reason to collapse.
    entries.forEach(function (entry, index) {
      var line = digestOwner[entry.cycle] === index ? byCycle[entry.cycle] : null;
      // A cycle's comments belong to the card that owns its anchor, so a
      // cycle with an addendum shows them once rather than on both cards --
      // the same ownership rule the digest line follows just above.
      var own = anchorOwner[entry.cycle] === index;
      feed.appendChild(renderEntry(
        entry, line, wanted !== null, own, commentsByCycle[String(entry.cycle)]
      ));
    });
  }

  function load() {
    Promise.all([
      fetch("/api/journal").then(function (r) { return r.json(); }),
      fetch("/api/digest").then(function (r) { return r.json(); }).catch(function () { return null; }),
      // Tolerated the same way the digest is: the journal is the page, and
      // a comments read that fails should cost the bubbles, not the feed.
      fetch("/api/comments").then(function (r) { return r.json(); }).catch(function () { return null; }),
    ])
      .then(function (results) { render(results[0], results[1], results[2]); })
      .catch(function (err) {
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the journal: " + err));
      });
  }

  /* The capture box (item 6). Two buttons rather than a target toggle plus
   * a submit: it is one tap fewer on a phone, which is the whole point of
   * the feature. The text is only cleared once the server confirms the
   * write -- a failed capture that wiped the box would lose the thought it
   * exists to catch. */
  (function captureBox() {
    var form = document.getElementById("capture-form");
    if (!form) return;
    var textEl = document.getElementById("capture-text");
    var captureStatus = document.getElementById("capture-status");
    var buttons = Array.prototype.slice.call(form.querySelectorAll(".capture-btn"));

    /* Edvard, issues.md 2026-08-09: "the input box for the Nova pwa is too
     * small and not rescalable so i can't see my entire input text if its
     * more than 3 lines." CSS `resize: vertical` was already there and does
     * nothing on iOS -- mobile browsers render no resize handle at all, so
     * the box could only ever be dragged on a desktop he does not use it
     * from. Growing it as he types removes the gesture instead of fixing it.
     *
     * The height is cleared before it is read: scrollHeight of a fixed-height
     * box is its content height *or* its current height, whichever is larger,
     * so without this the box grows and never shrinks back. */
    function fit() {
      textEl.style.height = "auto";
      textEl.style.height = textEl.scrollHeight + "px";
    }

    function setStatus(text, isError) {
      captureStatus.textContent = text;
      captureStatus.className = isError ? "capture-status is-error" : "capture-status";
    }

    function send(target) {
      var text = textEl.value.trim();
      if (!text) {
        textEl.focus();
        return;
      }
      buttons.forEach(function (b) { b.disabled = true; });
      setStatus("saving…", false);
      fetch("/api/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: target, text: text }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          textEl.value = "";
          fit();
          setStatus("saved to " + target, false);
          // The capture may be the top bullet of a file the feed shows.
          load();
        })
        .catch(function (err) { setStatus(String(err.message || err), true); })
        .then(function () { buttons.forEach(function (b) { b.disabled = false; }); });
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () { send(button.getAttribute("data-target")); });
    });
    form.addEventListener("submit", function (event) { event.preventDefault(); });
    // Enter sends as an issue; Shift+Enter keeps the newline, since several
    // lines become several bullets server-side.
    textEl.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send("issues");
      }
    });
    textEl.addEventListener("input", fit);
    fit();
  })();

  // Back/forward between /cycle/N and / without a round trip.
  window.addEventListener("popstate", load);
  document.addEventListener("click", function (event) {
    var anchor = event.target.closest && event.target.closest("a[href^='/']");
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    history.pushState(null, "", anchor.getAttribute("href"));
    load();
    window.scrollTo(0, 0);
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  load();
})();
