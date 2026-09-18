/* The page for one cycle, `/cycle/N` (issue #233).
 *
 * The eighteenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js`,
 * `models.js`, `richtext.js`, `bubble.js`, `askthread.js` and `rowedit.js`:
 * `renderCyclePage`, and the helpers a journal card shares with it -- the
 * runtime and outcome lines (`appendRuntime`, `appendOutcome`), the parts of
 * a multi-part cycle (`appendParts`, `settledPart`), and the title rules
 * (`cleanTitle`, `hasBrief`).
 *
 * It borrows eight names and hands seven back. `nextBodyId` is a counter
 * both sides increment, so it is not handed over as a value -- a copy would
 * count on its own and two bodies could share an id. `app.js` keeps it and
 * hands over `takeBodyId`, which increments the one real counter.
 * `renderBlocks` and `renderSpans` come from `richtext.js`, bound far above
 * this block's old spot, so it is bound there, in place.
 */
(function () {
  "use strict";

  window.novaCycle = function (shared) {
    var cycleTarget = shared.cycleTarget;
    var el = shared.el;
    var outcomeClass = shared.outcomeClass;
    var renderBlocks = shared.renderBlocks;
    var renderComments = shared.renderComments;
    var renderSpans = shared.renderSpans;
    var shortOutcome = shared.shortOutcome;
    var takeBodyId = shared.takeBodyId;

    /* `/cycle/81` is a page, not a card.
     *
     * the owner, inside issue #59: "its not the link thats the problem, its the
     * single view that is bad ui... Please do some propper ui research and
     * testing with this as the current solution does not make sense, is hard
     * to understand and wasteful". And on the comments board, Cycle 81: "i do
     * not like the double entry Journal cards. If a double entry is necessary
     * like for cycle 81, have it be combined into one card that has tabs or
     * something similar. Its confusing that its two separate cards."
     *
     * Both are the same page. What it used to render was the feed's card with
     * `expanded` set, and every part of that card exists to help someone scan
     * a feed of 115 of them -- which is the one thing this page has none of:
     *
     *  - The journal text was still shut. `setExpanded` re-derives the drawer
     *    from the drawer's own `aria-expanded`, which is `false` on a card
     *    built one line earlier, so opening the card never opened the drawer
     *    inside it. You navigated to a URL that names one entry and got a
     *    button asking whether you wanted to read it. That is the "wasteful".
     *  - The permalink `#` pointed at the page it was already on.
     *  - The chevron collapsed the only thing on the page, leaving a back link
     *    and nothing else.
     *  - A cycle with an addendum drew two of all of that, both headed
     *    "Cycle 81", both carrying the same PR and outcome.
     *
     * So: one `<article>` per cycle, heading once, meta once, prose open. The
     * parts run oldest-first, because on a page you are reading rather than
     * scanning and the addendum is the later half of the same hour.
     *
     * Tabs are what he suggested and I did not use them, which is a call he
     * can reverse in a sentence. Two parts of one cycle are one continuous
     * account -- the addendum is usually "the deploy I could not see came up
     * healthy" -- and a tab would hide half of a page whose whole job is to
     * show the thing you asked for. A dated subheading keeps both readable in
     * one scroll and still says plainly that they were written at different
     * times. The feed is untouched and still draws two cards; that is a
     * separate question about a scanning surface and it stays filed. */
    /* What to call one part of a multi-part cycle.
     *
     * The titles are prose a cycle typed into its own heading, and fourteen
     * cycles have written more than one entry with no convention between them:
     * `(addendum)`, `addendum`, `verification`, `postscript`, `· addendum
     * (2026-08-11 05:24)`, `(2026-08-11 05:09)`, and one 90-character sentence.
     * Measured off the live pod, not guessed. Printing them raw beside a
     * timestamp gives "· addendum (2026-08-11 05:24) · 2026-08-11 05:24 Oslo".
     *
     * So: drop a leading bullet, drop the wrapping parentheses, drop a
     * parenthesised date the heading is about to print anyway, and fall back
     * to a plain word when nothing is left. Cycle 6 has three entries, which
     * is why anything past the first is "Addendum" rather than the pair-shaped
     * "The second half". */
    /** Whether two parts reached the same answer. Compared by *content*, not
     *  by identity: eleven of the fourteen multi-part cycles repeat their
     *  parent's PR and outcome verbatim, and those are the ones that must not
     *  draw a second row -- an identity check calls every one of them a
     *  disagreement and puts the duplicate straight back. */
    function sameOutcome(a, b) {
      return (a.pr || "") === (b.pr || "")
        && (a.board || "") === (b.board || "")
        && (a.outcome || "") === (b.outcome || "")
        && (a.outcomeDetail || "") === (b.outcomeDetail || "");
    }

    /** The outcome pill, the PR references and the qualifier beside them.
     *  Shared so a part's own row and the cycle's row cannot drift apart. */
    /** How long the cycle actually ran, when the server is sure (#59).
     *
     *  `runtimeSeconds` is absent rather than null on a cycle whose session
     *  could not be told apart from a neighbouring cycle's, so presence is
     *  the whole test -- see `nova_runtimes.cycle_runtimes` for why roughly
     *  a third of the archive has no number and the recent feed nearly
     *  always does. Minutes, because the shortest cycle on record is 7 and
     *  seconds would imply a precision the join does not have. */
    function appendRuntime(row, entry) {
      var seconds = entry && entry.runtimeSeconds;
      if (!seconds) return;
      var minutes = Math.round(seconds / 60);
      var text = minutes < 1 ? "ran under a minute" : "ran " + minutes + " min";
      row.appendChild(el("span", "runtime", text));
    }

    /* `opts.withOutcome === false` draws the row without the outcome pill and
     * its qualifier. The owner, comments board 2026-08-23, on cycle 340's card:
     * "What is this new grey title? ... This is ugly and seems like information
     * i do not need or want" -- and, to the proposal to drop the pill from the
     * card, "Sure. Cut it".
     *
     * The pill looked fine for months because almost every outcome is one of
     * four short words, and `merged` in green beside a PR link reads as a
     * badge. Nothing enforces that: the footer's Outcome field is free text,
     * cycle 340 wrote a whole clause into it, and the card rendered 84
     * characters of uppercased grey where a word goes. So the pill was always
     * one long outcome away from being a second title, which is exactly what
     * he saw.
     *
     * It stays on `/cycle/<n>` and on a disagreeing part's own row inside the
     * drawer: those are places you have opened on purpose, where the cycle's
     * settled word is the thing you came for. The feed is the place that has
     * to stay scannable. */
    function appendOutcome(row, entry, opts) {
      var withOutcome = !opts || opts.withOutcome !== false;
      /* On the card, only a recognised status word is drawn -- see
       * `shortOutcome`. That is the half of the pill the owner asked back on
       * 2026-08-24; the half he cut, free text rendered as a badge, stays
       * cut. Where the full pill is drawn (`/cycle/<n>`, a disagreeing
       * part's own row) nothing changes: those are pages opened on purpose. */
      var word = withOutcome ? entry.outcome : shortOutcome(entry.outcome);
      if (word) {
        row.appendChild(el("span", outcomeClass(word), word));
      }
      /* With the pill suppressed, `isRealPr` gates the PR too. "none" is only
       * ever readable as the object of the footer's sentence -- `PR: none |
       * Outcome: no-op` -- and once the outcome half is gone it answers a
       * question nothing on the card asked. Cycle 340's card, the one the owner
       * complained about, is exactly this case. Where the pill is drawn, so
       * is the `none`: there it still says something. */
      if (entry.pr && (withOutcome || isRealPr(entry.pr))) {
        var pr = el("span", "pr");
        // prSpans carries the same text with each reference linkified; the
        // plain string is the fallback for a payload from an older build.
        if (entry.prSpans && entry.prSpans.length) renderSpans(pr, entry.prSpans);
        else pr.textContent = entry.pr;
        row.appendChild(pr);
      }
      // The board item this cycle worked on, when it named one (ideas.md
      // #68). Same shape as the PR badge beside it, and the same fallback
      // for a payload from an older build; the difference is where it goes.
      if (entry.board) {
        var board = el("span", "board");
        if (entry.boardSpans && entry.boardSpans.length) renderSpans(board, entry.boardSpans);
        else board.textContent = entry.board;
        row.appendChild(board);
      }
      // The qualifier goes with the pill it qualifies -- "stuck — CI outage,
      // merged nothing" on its own, with no "stuck" beside it, is a fragment.
      if (withOutcome && entry.outcomeDetail) {
        row.appendChild(el("span", "outcome-detail", entry.outcomeDetail));
      }
      return row;
    }

    /* the owner, issues #86: "Journal cards like cycle 209 seems to have two
     * titles. Only one is enough."
     *
     * A card draws the entry's own `### ` heading title and, directly under
     * it, the brief. When the brief comes from the digest line those are two
     * sentences written for two different purposes, saying the same thing --
     * cycle 209's heading is "the owner asked for tabs three times and I finally
     * built them" and its digest brief is "You asked three times for the
     * double journal entries to be one card with tabs, and now they are."
     * The digest line is the one he reads, so it is the one that stays.
     *
     * The digest-line half of that shipped in #86. The entry half did not:
     * when a card has no digest line its brief is the entry's own first
     * paragraph, and that paragraph opens by restating the heading, so the
     * card still showed two sentences saying one thing. The owner, comments
     * board 2026-08-22, on a screenshot of cycle 329: "I'm a bit confused by
     * the Nova cycle ui. Sometimes there are two titles and they repeat
     * eachoter with different words. See image. I like the one with the
     * colored backline" -- the coloured backline is `.entry-brief` -- and
     * then, two minutes later: "The one line summary can be cut."
     *
     * So the rule is the brief, from either source, not the digest line
     * specifically. Measured on the live feed the same night: all 385 entries
     * in `/api/journal` have a **non-empty** `briefSpans`, and so do all 269
     * digest lines -- an entry whose body holds no plain paragraph would get
     * an empty one, `nova_journal` sets the field either way. So on a fresh
     * payload the heading title no longer appears on a card. The branch stays
     * rather than the call site being deleted because a card with no brief
     * would otherwise be labelled by nothing, and `lint_entry`'s title check
     * still guards that. */
    function hasBrief(digestLine, entry) {
      if (digestLine && digestLine.briefSpans && digestLine.briefSpans.length) return true;
      return !!(entry && entry.briefSpans && entry.briefSpans.length);
    }

    function cleanTitle(title) {
      var text = String(title || "")
        .replace(/^[\s·—–-]+/, "")
        .replace(/\(\s*\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?\s*\)/g, "")
        .trim();
      // Only when the parentheses wrap the whole of what is left; `(addendum)`
      // loses them, `a fix (and the bug under it)` keeps them.
      if (/^\([^()]*\)$/.test(text)) text = text.slice(1, -1).trim();
      text = text.replace(/^[\s·—–-]+/, "").trim();
      return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
    }

    function partLabel(title, index) {
      return cleanTitle(title) || (index === 0 ? "The cycle" : "Addendum");
    }

    /** The cycle's own answer: the last part that declares a PR or an outcome.
     *
     *  Not the first. Cycle 102's base entry carries neither and its addendum
     *  carries `#86 / merged`, so reading the earliest part shows a cycle that
     *  merged a PR as having done nothing. An addendum exists precisely to
     *  record what the earlier entry could not yet know. */
    function settledPart(ordered) {
      /* "The last part that declares anything" was the first rule here and it
       * is wrong on real data, because the footer is mandatory: a part with
       * nothing of its own to report still writes `PR: none | Outcome: no-op`,
       * and that is a statement about the *part*, not about the cycle.
       *
       * Cycle 105 is the case, and it is the cycle that shipped the rule:
       * its first entry merged `#89`, its addendum struck a non-bug off the
       * list and filed `none / no-op`. Under the old rule the card announced
       * a cycle that merged a PR as a no-op, with `merged #89` demoted to a
       * row two taps down. Cycle 6 is worse -- shipped, then merged three
       * PRs, then a closing note about an unrelated incident.
       *
       * So a real PR reference outranks a later `none`. Where no part has
       * one, the old rule still applies, which is what keeps cycle 102
       * (nothing, then `#86 / merged`) reading off its addendum. */
      var named = ordered.reduce(function (best, part) {
        return isRealPr(part.pr) ? part : best;
      }, null);
      return named || ordered.reduce(function (best, part) {
        return (part.pr || part.outcome) ? part : best;
      }, ordered[0]);
    }

    /** Whether a `PR:` field names something, as opposed to saying it does
     *  not. Five entries write a qualifier after it ("none (status note)"),
     *  so this cannot be an equality test. */
    function isRealPr(pr) {
      return !!String(pr || "").trim() && !/^none\b/i.test(String(pr).trim());
    }

    /** Every part of a cycle, in the order it was written, appended to
     *  `container`.
     *
     *  Shared by the feed card's drawer and the cycle page so the two cannot
     *  drift -- they are the same account, and the only difference is whether
     *  you had to tap to see it. A single-part cycle gets no tab strip: there
     *  is nothing to tell apart, and a control that switches between one
     *  thing is the same noise as a permalink to the page you are on.
     *
     *  **Tabs, because the owner asked three times.** Comments board at cycle
     *  81: "If a double entry is necessary like for cycle 81, have it be
     *  combined into one card that has tabs or something similar." Inside
     *  issue #59: "they should be combined into one with tabs. Please do some
     *  propper ui research and testing with this as the current solution does
     *  not make sense, is hard to understand and wasteful."
     *
     *  Two cycles answered that with dated subheadings instead and each
     *  invited him to reverse it "in one sentence". He had already spent the
     *  sentence, twice, and then a third time to say the result was hard to
     *  understand -- so re-arguing it a third time is the loop overruling its
     *  own user by attrition. The standing objection was that a tab hides the
     *  addendum, which is usually "the deploy I could not see came up
     *  healthy", i.e. the cycle's real answer. That objection is already
     *  answered by the card and page above this: `settledPart` puts the
     *  settled PR and outcome in the meta row, outside the tabs, where it is
     *  visible whichever tab is open. Tabs hide prose, not the conclusion.
     *
     *  Every panel stays in the DOM and only `hidden` is toggled, so switching
     *  tabs is a class change rather than a re-render and nothing below has to
     *  be rebuilt. **It does not keep the shut half findable**: `hidden` is
     *  `display: none`, which find-in-page and select-all skip, the same trap
     *  `.prio-menu[hidden]` already documents in the stylesheet. An earlier
     *  version of this comment claimed otherwise and was wrong. Reaching both
     *  halves at once is what `/cycle/N` is for, and if that stops being a
     *  good enough answer the fix is a real "show both" control, not a
     *  sentence here saying the problem does not exist. */
    function appendParts(container, ordered, settled, fold) {
      if (ordered.length < 2) {
        var only = el("div", "entry-body");
        renderBlocks(only, ordered[0].blocks);
        container.appendChild(only);
        return;
      }

      var strip = el("div", "tabs entry-tabs");
      strip.setAttribute("role", "tablist");
      strip.setAttribute("aria-label", "Parts of this cycle");
      container.appendChild(strip);

      var tabs = [];
      var panels = [];

      ordered.forEach(function (part, index) {
        var when = [part.date, part.time].filter(Boolean).join(" ");
        var label = partLabel(part.title, index);
        var seq = takeBodyId();
        var tabId = "part-tab-" + seq;
        var panelId = "part-panel-" + seq;

        /* The tab carries the same text the subheading did -- the cycle's own
         * heading prose plus when it was written -- because that is what tells
         * the two halves apart, and it is the one thing a tab label has to do.
         * Cycle 75's runs to ninety characters, so the strip wraps rather than
         * scrolls; `.tabs` already does that. */
        var tab = el("button", "tab entry-part-tab", when ? label + " · " + when : label);
        tab.type = "button";
        tab.id = tabId;
        tab.setAttribute("role", "tab");
        tab.setAttribute("aria-controls", panelId);
        strip.appendChild(tab);
        tabs.push(tab);

        var panel = el("div", "entry-part-panel");
        panel.id = panelId;
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tabId);

        /* A part that reached a different answer than the cycle's settled one
         * keeps its own row, inside its own panel. Cycle 6 is the case: three
         * parts, three different PR/outcome pairs -- `no-op`, then `merged`,
         * then `shipped` -- and the meta row above the tabs can only be one of
         * them. Where a part agrees with the settled answer (the common shape)
         * it stays silent, so the common cycle draws no duplicate. */
        if ((part.pr || part.outcome) && !sameOutcome(part, settled)) {
          var partMeta = appendOutcome(el("div", "entry-meta entry-meta-part"), part);
          if (partMeta.childNodes.length) panel.appendChild(partMeta);
        }

        var body = el("div", "entry-body");
        renderBlocks(body, part.blocks);
        panel.appendChild(body);
        container.appendChild(panel);
        panels.push(panel);
      });

      /** Show one part. Index is always in range -- every caller derives it
       *  from `tabs`, which is built from `ordered` one loop above. */
      function select(index) {
        if (fold) fold.part = index;
        tabs.forEach(function (tab, i) {
          var on = i === index;
          tab.classList.toggle("on", on);
          tab.setAttribute("aria-selected", on ? "true" : "false");
          /* Roving tabindex: one stop for the whole strip, then arrow keys
           * within it. A tablist where every tab is a tab stop makes a
           * keyboard user press Tab three times to get past cycle 6. */
          tab.tabIndex = on ? 0 : -1;
          panels[i].hidden = !on;
        });
      }

      strip.addEventListener("click", function (event) {
        var index = tabs.indexOf(event.target);
        if (index !== -1) select(index);
      });

      strip.addEventListener("keydown", function (event) {
        var current = tabs.indexOf(event.target);
        if (current === -1) return;
        var next = null;
        if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (current + 1) % tabs.length;
        if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (current - 1 + tabs.length) % tabs.length;
        if (event.key === "Home") next = 0;
        if (event.key === "End") next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        select(next);
        tabs[next].focus();
      });

      /* The first part, because both surfaces read forwards: `ordered` is
       * oldest-first and an addendum is the later half of the same hour, not
       * an alternative to it -- unless this cycle's card already had a tab
       * open, in which case a poll re-render must not throw it away. Bounded,
       * because a cycle can gain a part between two polls. */
      var start = fold && fold.part ? fold.part : 0;
      select(start < ordered.length ? start : 0);
    }

    function renderCyclePage(cycleNumber, entries, digestLine, comments) {
      var card = el("article", "entry is-page");
      card.id = "cycle-" + cycleNumber;

      // Newest-first off the wire; a page reads forwards.
      var parts = entries.slice().reverse();
      var first = parts[0];

      var head = el("header", "entry-head");
      if (first.emoji) {
        var emoji = el("span", "entry-emoji", first.emoji);
        emoji.setAttribute("aria-hidden", "true");
        head.appendChild(emoji);
      }
      /* `h2`, matching the feed's card, because index.html already spends the
       * document's `h1` on the "Nova" wordmark. A second `h1` is legal HTML
       * and still leaves a screen reader with two top-level headings and no
       * way to tell which one is the page. So: wordmark h1, cycle h2, the
       * cycle's parts h3 -- one hierarchy, on both views. */
      head.appendChild(el("h2", "cycle-link", "Cycle " + cycleNumber));
      card.appendChild(head);

      /* A one-part cycle's title has nowhere else to go. Twenty-six of them
       * carry a real one -- "The heartbeat was never late; the clock on the
       * card was invented" -- and the first version of this page dropped every
       * one, because titles were only rendered as part subheadings and a
       * single part gets none. A multi-part cycle keeps them in the
       * subheadings, where they say which half you are in.
       *
       * `cleanTitle` rather than the raw string: eleven entries have a title
       * that is only their own timestamp, and it renders as nothing rather
       * than as a date printed twice. `hasBrief` because a card that draws a
       * brief already has a sentence doing this job -- see its own comment,
       * issues #86, and the owner's 2026-08-22 captures. */
      if (parts.length === 1 && cleanTitle(first.title) && !hasBrief(digestLine, first)) {
        card.appendChild(el("p", "entry-title", cleanTitle(first.title)));
      }

      /* The meta row is drawn once for the cycle. The stamp is the earliest
       * part's, because that is when the cycle began -- but the PR and the
       * outcome come from the *last* part that declares one.
       *
       * The first version of this took all four from the earliest part, on
       * the assumption that "an addendum repeats its parent's PR and outcome
       * verbatim". That assumption is false and the live journal says so:
       * cycle 102's base entry carries no PR and no outcome at all, and its
       * addendum carries `#86 / merged` -- so `/cycle/102` would have shown a
       * cycle that merged a PR as having done nothing. Which is the right way
       * round, once stated: an addendum exists precisely to record what the
       * earlier entry could not yet know, so it holds the cycle's settled
       * word. (Measured across all 115 cycles: 4 of the 14 multi-part ones
       * are affected.) */
      var settled = settledPart(parts);
      var meta = el("div", "entry-meta");
      var stamp = [first.date, first.time].filter(Boolean).join(" ");
      if (stamp) meta.appendChild(el("time", "stamp", stamp));
      appendRuntime(meta, first);
      appendOutcome(meta, settled);
      if (meta.childNodes.length) card.appendChild(meta);

      /* The digest line, whole and open. In the feed it is two drawers -- a
       * brief that fits a collapsed card, then the remainder -- because a card
       * has to be short enough to scan past. Here it is the standfirst. */
      var briefSpans = (digestLine && digestLine.briefSpans) || first.briefSpans;
      if (briefSpans && briefSpans.length) {
        var brief = el("p", "entry-brief");
        renderSpans(brief, briefSpans);
        card.appendChild(brief);
      }
      if (digestLine && digestLine.restSpans && digestLine.restSpans.length) {
        var rest = el("p", "entry-digest");
        renderSpans(rest, digestLine.restSpans);
        card.appendChild(rest);
      }

      appendParts(card, parts, settled);

      /* One comment thread for the cycle, same as the feed gives the anchor-
       * owning card. `renderComments` appends its drawer to the container it
       * is handed, so the foot is built first or the drawer opens above the
       * button that opens it. */
      var foot = el("div", "entry-foot");
      card.appendChild(foot);
      var commenting = renderComments(card, cycleTarget(cycleNumber), comments);
      foot.appendChild(commenting.toggle);

      function setCommentsOpen(open, byTap) {   // `byTap` as on the feed card
        card.className = open ? "entry is-page is-commenting" : "entry is-page";
        commenting.toggle.setAttribute("aria-expanded", open ? "true" : "false");
        if (open && byTap) commenting.seen();   // same contract as the feed card's
      }
      setCommentsOpen(false);

      /* The only thing left to click. Nothing on this page collapses, so the
       * card has no toggle listener -- a tap on the prose does nothing, which
       * is what a tap on prose should do. */
      card.addEventListener("click", function (event) {
        if (event.target.closest("a")) return;
        if (event.target.closest(".comment-drawer")) return;
        if (event.target.closest(".comment-toggle")) {
          setCommentsOpen(commenting.toggle.getAttribute("aria-expanded") !== "true", true);
        }
      });
      return card;
    }

    return {
      appendOutcome: appendOutcome,
      appendParts: appendParts,
      appendRuntime: appendRuntime,
      cleanTitle: cleanTitle,
      hasBrief: hasBrief,
      renderCyclePage: renderCyclePage,
      settledPart: settledPart,
    };
  };
})();
