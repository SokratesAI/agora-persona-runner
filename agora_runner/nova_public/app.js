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

  var navEl = document.getElementById("nav");
  var menuBtn = document.getElementById("menu-btn");
  var scrim = document.getElementById("scrim");

  /* The sidebar (Edvard, issues.md 2026-08-11: "Move the Journal, issues
   * & ideas tabs buttons to a sidebar that opens from a hamburger button
   * ... Add slide animations").
   *
   * Deliberately the whole of the feature's JavaScript: the three links
   * are the same anchors they always were, so `markNav` and the delegated
   * click handler at the bottom of this file still route them without
   * knowing they moved. The slide itself is CSS. All that is new is one
   * boolean, mirrored onto the four elements that have to agree about it. */
  function menuOpen() {
    return !!navEl && navEl.classList.contains("open");
  }

  function setMenu(open) {
    if (!navEl) return;
    navEl.classList.toggle("open", open);
    navEl.setAttribute("aria-hidden", open ? "false" : "true");
    if (scrim) scrim.classList.toggle("open", open);
    if (document.body) document.body.classList.toggle("nav-open", open);
    if (menuBtn) {
      menuBtn.classList.toggle("open", open);
      menuBtn.setAttribute("aria-expanded", open ? "true" : "false");
      menuBtn.setAttribute("aria-label", open ? "Close menu" : "Open menu");
    }
  }

  /* Which page the URL asks for. Three views over four URLs:
   * `/` and `/cycle/49` are the journal, `/issues` and `/ideas` are the
   * two board pages Edvard asked for in issues.md #57.
   *
   * The server serves the same shell for all of them (nova_site's GET
   * handler) and this decides what to fetch, so a board page survives a
   * cold load and a bookmark rather than only being reachable by tapping
   * the nav. */
  function route(pathname) {
    var path = (pathname || "/").replace(/\/+$/, "") || "/";
    var cycle = /^\/cycle\/(\d+)$/.exec(path);
    if (cycle) return { view: "journal", cycle: parseInt(cycle[1], 10), board: null };
    if (path === "/issues") return { view: "board", cycle: null, board: "issues" };
    if (path === "/ideas") return { view: "board", cycle: null, board: "ideas" };
    if (path === "/costs") return { view: "costs", cycle: null, board: null };
    if (path === "/retro") return { view: "retro", cycle: null, board: null };
    return { view: "journal", cycle: null, board: null };
  }

  /** `/cycle/49` -> 49. Anything else -> null (show the whole feed). */
  function routedCycle(pathname) {
    return route(pathname).cycle;
  }

  function markNav() {
    var here = route(window.location.pathname);
    // Every view but the journal is named after its own path, so the two
    // single-page views need no branch of their own -- which is what a
    // third one turning the chain into a nested ternary made worth doing.
    var want = here.view === "board"
      ? "/" + here.board
      : here.view === "journal" ? "/" : "/" + here.view;
    var tabs = navEl ? navEl.querySelectorAll(".nav-tab") : [];
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute("href") === want;
      tabs[i].classList.toggle("on", on);
      // `aria-current` rather than only a class: the nav is three links
      // and the active one is otherwise distinguishable by colour alone.
      if (on) tabs[i].setAttribute("aria-current", "page");
      else tabs[i].removeAttribute("aria-current");
    }
  }

  /* How much of the journal a cold load asks for, and how much a tap on
   * "Show older entries" adds.
   *
   * #84 made the *poll* free -- 227,520 gzipped bytes down to 6,048 -- and
   * left the first load exactly as it was: every entry ever written.
   * Measured against the live pod at 06:11 Oslo on 2026-08-11, that was
   * 109 entries, 678,027 bytes raw and 187,148 gzipped -- and it grows by
   * one entry an hour, so any figure written here is already low. That is
   * the half of "Nova takes a long time to load when i refresh it" that
   * was still true.
   *
   * The window is a single number rather than an accumulating list of
   * pages, and every request the page makes -- first load, poll, and
   * "show older" -- asks for the same `?limit=windowSize` from offset
   * zero. Fetching one page and appending it would move fewer bytes when
   * someone reads a long way back, and it would also mean the poll and the
   * pager disagreeing about what is loaded every time a new entry shifts
   * the offsets underneath them. One window has no such offset to get
   * wrong: a poll is a 304 against exactly what is on screen, and a new
   * entry arriving simply lands at the top of it.
   *
   * It is not free of state, and the first review of this said so. Widening
   * the window re-renders the whole feed, which builds every card again
   * from scratch. Unsent text is carried across (see `drafts`) and since
   * 2026-08-11 so is every card's expanded/collapsed state (see `folds`
   * below), so a tap on the pager no longer closes what was open.
   */
  var PAGE = 20;
  var windowSize = PAGE;

  /* Which cards were open, so that rebuilding the feed puts them back.
   *
   * Edvard, issues.md 2026-08-11: "The Nova site closes all drawers on what
   * seems like every 30 sec or so. Is this a refresh bug?"
   *
   * A card's open/closed state lived only in the DOM, so every path that
   * rebuilds the feed -- the 30-second poll when an entry really did
   * arrive, the pager above, a tap on Journal from a board -- silently
   * closed whatever he was reading. The comment above says so in as many
   * words and treated it as the price of one window; it is not, and this is
   * the state the drafts store already keeps for half-typed text.
   *
   * Keyed by cycle number rather than by position, because a new entry
   * arriving at the top is exactly when this matters -- keyed by index, the
   * card he had open would hand its state to the one that pushed it down.
   * An entry with no cycle number gets no memory: there is one (Edvard's
   * first message), nothing else can address it either, and inventing a key
   * from its title would make two untitled notes share one.
   */
  var folds = {};

  function foldFor(cycle) {
    /* `part` joins the three booleans because a poll rebuilds the feed from
     * scratch: without it, Edvard taps to the addendum, a routine poll lands,
     * and the tab silently reverts to the first part under him while the card
     * and drawer correctly stay open. Found by the reviewer, not by me. */
    if (cycle === null || cycle === undefined) {
      return { expanded: false, journal: false, comments: false, part: 0 };
    }
    var key = "cycle-" + cycle;
    if (!folds[key]) folds[key] = { expanded: false, journal: false, comments: false, part: 0 };
    return folds[key];
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* Make a pager fire when it is scrolled to, instead of when it is tapped.
   *
   * Edvard, issues.md #71: "Make it more lazy load when i scroll down
   * instead of a button i press."
   *
   * The button stays. It is not a fallback nobody reaches -- it is the
   * keyboard path, it is what a screen reader announces, and it is what
   * runs in any engine without an IntersectionObserver. So the observer
   * does not get its own copy of the widening logic; it clicks the button,
   * which means there is exactly one thing that can happen when the end of
   * the feed is reached and no second version of it to drift.
   *
   * Re-entry is already handled twice over and neither of them is a check
   * written here. It disconnects before it clicks, so one observer fires
   * once; and a click handler's first act is to disable the button, and a
   * disabled button does not dispatch a click at all -- so a second batch
   * already queued when `disconnect` landed cannot widen the window twice.
   * A third guard reading `if (!node.disabled)` was in the first draft of
   * this, and removing it failed no test out of 155, because it could not
   * be reached in a state where it changed the answer. It was deleted
   * rather than given a test, which would have been a test of dead code.
   *
   * Disconnecting also matters on its own: `render` throws this node away
   * and builds a new one, so an observer left attached is watching a node
   * that is no longer in the document and never can be again. Firing is not
   * the only way that happens -- the 30-second poll re-renders the feed
   * whenever a new entry lands, and a reader who never scrolled to the
   * pager leaves one observer and one detached subtree behind every time.
   * On a phone left open all day that is hundreds. So `attached` holds the
   * live one and every attach disconnects its predecessor: there is one
   * pager on screen, so there is one observer.
   *
   * **It can fire the moment it is attached, and that is intended.** The
   * spec delivers an initial observation on `observe()`, so on a viewport
   * tall enough to show all twenty collapsed cards the first window widens
   * with no scroll at all. That is the screen being filled, not the cold
   * load growing: it is bounded, because each widening adds twenty more
   * cards and the viewport does not grow with them, so it stops as soon as
   * the content is taller than the screen plus the margin. Twenty collapsed
   * cards is already ~1400px against a phone's ~850px, so Edvard's own
   * first load does not trigger it at all -- but a desktop's does, and it
   * is the path no test had until the reviewer pointed at it.
   *
   * `rootMargin` starts the fetch 300px before the pager is actually on
   * screen, so the entries are usually there by the time the reader gets
   * to where they go. That number is a guess at a comfortable feel, not a
   * measurement, and it is one line to change. */
  var attached = null;

  function loadWhenScrolledTo(node) {
    if (typeof window.IntersectionObserver !== "function") return;
    if (attached) attached.disconnect();
    var observer = new window.IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i += 1) {
        if (!entries[i].isIntersecting) continue;
        observer.disconnect();
        node.click();
        return;
      }
    }, { rootMargin: "300px 0px" });
    attached = observer;
    observer.observe(node);
    /* He asked for the button to stop being something he presses, so when
     * the observer is actually attached it stops looking like one: no box,
     * no border, dim centred text. It is still a real focusable button
     * underneath -- the styling changes, the element does not -- because
     * something has to remain reachable without a mouse wheel, and because
     * `display: none` would make it stop intersecting and the whole thing
     * would silently never fire. */
    node.classList.add("more-auto");
    node.textContent = "↓ " + node.textContent.replace(/^Show /, "").toLowerCase();
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
        //
        // Unless it is one of our own pages: a `Board:` reference points at
        // `/ideas#68`, which is this app. Opening that in a tab would be
        // the same wrong answer in the other direction, and the delegated
        // handler at the bottom of this file already routes `a[href^='/']`
        // through pushState, so an internal link only has to *not* say
        // `target`.
        var internal = String(span.url || "").charAt(0) === "/";
        var anchor = el("a", internal ? "board-link" : "pr-link", span.text);
        anchor.href = span.url;
        if (!internal) {
          anchor.target = "_blank";
          anchor.rel = "noopener noreferrer";
        }
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
      if (block.type === "quote") {
        var quote = el("blockquote");
        renderSpans(quote, block.spans);
        parent.appendChild(quote);
        return;
      }
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

  function statusParts(status) {
    var parts = [];
    if (status.cycle !== null && status.cycle !== undefined) parts.push("Cycle " + status.cycle);
    if (status.runningDays) parts.push("running " + status.runningDays + " days");
    if (status.lastWokeTime) parts.push("last woke " + status.lastWokeTime);
    else if (status.lastWokeDate) parts.push("last woke " + status.lastWokeDate);
    return parts;
  }

  /* The last status the page actually managed to fetch, so a failed fetch
   * can show it as stale instead of replacing it with nothing. */
  var lastStatus = null;

  function renderStatus(status) {
    lastStatus = status;
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));

    /* Replayed out of the service worker's cache: the content is worth
     * showing and its currency is not something this page can vouch for.
     * Marked exactly the way `renderStatusUnreachable` marks it, because it
     * is the same fact -- the network was down -- arriving through a path
     * that happens to look successful. The badges below are suppressed for
     * the same reason: they are claims about *now*, and this payload is
     * evidence about whenever it was cached. */
    var replayed = !!status.replayed;
    renderedReplayed = replayed;

    statusEl.appendChild(el("p", replayed ? "status-line is-stale" : "status-line",
      statusParts(status).join(" · ") + (replayed ? " — as of the last load" : "")));

    if (replayed) {
      var saved = el("p", "status-sub");
      saved.appendChild(el("span", "badge badge-error", "can't reach Nova"));
      saved.appendChild(el("span", "status-pr", "showing a saved copy"));
      statusEl.appendChild(saved);
    }

    if (status.lastOutcome) {
      var line = el("p", "status-sub");
      line.appendChild(el("span", outcomeClass(status.lastOutcome), status.lastOutcome));
      if (status.lastPr) line.appendChild(el("span", "status-pr", status.lastPr));
      if (status.lastOutcomeDetail) {
        line.appendChild(el("span", "status-pr", status.lastOutcomeDetail));
      }
      statusEl.appendChild(line);
    }

    /* The other half of #72: "Nova is 1 behind agora." The header names the
     * newest cycle that has *written*, and for the first 20-45 minutes of
     * every hour that is one behind the cycle actually running -- which
     * looked, on this page, exactly like the cycle having died. This says
     * which of the two it is, and it says it from Agora's own heartbeat
     * record rather than from the clock: the server sets `running` only
     * while `lastResult` is "running" and the run is newer than the newest
     * entry. It cannot be true at the same time as `stalled`; the server
     * drops the claim once the grace window passes, so a killed cycle
     * whose heartbeat is stuck on "running" forever reads as stalled here,
     * not as working.
     *
     * `!status.stalled` is checked here too even though the server cannot
     * currently emit the pair, because the failure if it ever did is not a
     * cosmetic one: the page would say the loop is working and that it has
     * been silent for four hours, in two lines a centimetre apart, and the
     * reassuring one is the lie. A second lock on that door costs one
     * clause. My own browser test for it failed on the first run -- the
     * comment above already asserted the two were exclusive, and only the
     * server made it so.
     *
     * Not shown on a replayed payload, for the same reason the stall badge
     * is not: "a cycle is running" is a claim about right now, and a saved
     * copy cannot make it. */
    if (status.running && !status.stalled && !replayed) {
      var live = el("p", "status-sub");
      live.appendChild(el("span", "badge badge-live", "cycle running now"));
      live.appendChild(el("span", "status-pr", "its entry arrives when it finishes"));
      statusEl.appendChild(live);
    }

    /* Shown only once the server calls the loop stalled, which it will not
     * do while a cycle is merely mid-flight -- an entry is written at the
     * end of an hour, so "one behind agora" is what a healthy loop looks
     * like from here and a badge every hour is a badge nobody reads (#72).
     * The server owns that judgement; this renders it and does not
     * second-guess the number. */
    if (status.stalled && !replayed) {
      var hours = status.silentIntervals;
      var quiet = el("p", "status-sub");
      quiet.appendChild(el("span", "badge badge-warn", "no entry for "
        + hours + (hours === 1 ? " hour" : " hours")));
      statusEl.appendChild(quiet);
    }

    /* The server saying it cannot see the journal, which until now it had
     * no way to say -- so it said "the loop has stopped" instead, because
     * a rebuild that keeps failing and a loop that stopped writing look
     * identical from inside the payload.
     *
     * Rendered as an error rather than a warning, and beside the same
     * "as of the last load" idea `renderStatusUnreachable` uses, because
     * it is the same failure one hop further back: there, this page could
     * not reach the server; here, the server could not reach the vault.
     * The line above it is real and worth keeping -- it just stopped being
     * current at some point the page cannot pin down. */
    if (status.recordStale) {
      var frozen = el("p", "status-sub");
      frozen.appendChild(el("span", "badge badge-error", "can't read the journal"));
      frozen.appendChild(el("span", "status-pr", "showing the last thing Nova could see"));
      statusEl.appendChild(frozen);
    }

    /* Edvard, comments board 2026-08-14: "Should be displayed if the
     * return fetch came in with missing journals."
     *
     * The badge above is a clock and this one is evidence. They answer
     * different questions and neither replaces the other: a loop that
     * died five minutes ago leaves no hole at all, because a hole only
     * appears once a later cycle writes an entry over the top of it, and
     * a loop running one long cycle leaves no hole either. So a stall is
     * the only thing a clock can catch, and a cycle that woke and failed
     * is the only thing a hole can catch.
     *
     * This one cannot cry wolf, which is the point. "Cycle 128 wrote no
     * entry" is a fact about the record rather than a guess about the
     * present, so it does not appear and retract as a cycle finishes.
     * The server has already cut the list to holes recent enough to act
     * on -- see `cycle_health.recent_gaps` -- and this renders that
     * judgement without second-guessing the window, the same way the
     * stall badge defers to `stalled`. */
    var holes = status.recentMissingCycles || [];
    if (holes.length) {
      var missed = el("p", "status-sub");
      missed.appendChild(el("span", "badge badge-warn", holes.length === 1
        ? "cycle " + holes[0] + " wrote no entry"
        : holes.length + " cycles wrote no entry"));
      statusEl.appendChild(missed);
    }
  }

  /* Edvard, comments board 2026-08-14: "Or a display error if the fetch
   * failed, also".
   *
   * The header's whole job is to say whether the loop is alive, and it was
   * the one part of this page that said nothing at all when it could not
   * find out. A first load that failed left "loading…" standing forever; a
   * failed poll left the last good line up, unmarked, still asserting a
   * cycle number and a wake time that nothing had confirmed since. Both of
   * those read as health.
   *
   * That is worse than a missing feature, because the moment nova-site is
   * unreachable is exactly the moment a stall badge would matter most: the
   * page answers "everything is fine" with no evidence, at the one time it
   * has none. Silence here is not neutral, it is the reassuring answer.
   *
   * The previous line is kept rather than blanked, explicitly marked as the
   * last thing that was seen. Blanking it would throw away the only real
   * information on screen, and "Cycle 197 · last woke 19:17" is worth
   * having as long as it is not passed off as current. */
  function renderStatusUnreachable(detail) {
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    if (lastStatus) {
      var parts = statusParts(lastStatus);
      if (parts.length) {
        statusEl.appendChild(el("p", "status-line is-stale",
          parts.join(" · ") + " — as of the last load"));
      }
    }
    var line = el("p", "status-sub");
    line.appendChild(el("span", "badge badge-error", "can't reach Nova"));
    if (detail) line.appendChild(el("span", "status-pr", detail));
    statusEl.appendChild(line);
  }

  function fetchFailureDetail(err) {
    var text = (err && err.message) || String(err || "");
    return text.replace(/^Error:\s*/, "");
  }

  /* One ask at a time, each with its own "Done" — issue #93, rated
   * 🔴 Immediately: *"I can't cross it out, it says it does not need me."*
   *
   * The block was write-only from his side. A cycle put an item in and only
   * a cycle could take it out, so an ask he had already answered stayed on
   * his page until some later cycle was *sure* — and being sure costs more
   * than leaving it, which is the mechanism that filled the block in the
   * first place. This is the other direction.
   *
   * `item.text` is the raw paragraph the server sent and goes back
   * unchanged. Not the index: a cycle rewrites this block every forty
   * minutes and renumbers it, so the position he tapped may name a
   * different ask by the time the request lands. The server refuses a text
   * that no longer matches exactly one item, which turns that race into a
   * visible failure instead of the wrong ask being retired.
   *
   * `blocks` is the fallback for a payload from before this shipped, so a
   * cached `app.js` against a new server — or the reverse — still shows him
   * the asks rather than an empty box. */
  function renderNeedsItems(body, items, blocks) {
    if (!items || !items.length) {
      renderBlocks(body, blocks);
      return;
    }
    items.forEach(function (item) {
      var row = el("div", "needs-item");
      var prose = el("div", "needs-item-body");
      renderBlocks(prose, item.blocks);
      row.appendChild(prose);

      var note = el("p", "needs-item-note");
      var done = el("button", "needs-done", "Done");
      done.type = "button";
      done.title = "Clear this from the block — it is kept in Nova's archive";
      done.addEventListener("click", function () {
        done.disabled = true;
        note.textContent = "Clearing…";
        fetch("/api/needs/dismiss", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: item.text })
        })
          .then(json)
          .then(function (payload) {
            if (!payload || !payload.ok) {
              throw new Error((payload && payload.message) || "failed");
            }
            /* Hide it now rather than waiting for the reload. The digest
             * cache is dropped server-side on a successful write, so the
             * next poll agrees — but the tap has to feel like it worked. */
            row.hidden = true;
            load();
          })
          .catch(function (err) {
            done.disabled = false;
            note.textContent = "Could not clear it: " + fetchFailureDetail(err);
          });
      });
      row.appendChild(done);
      row.appendChild(note);
      body.appendChild(row);
    });
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
    renderNeedsItems(body, digest.needsEdvardItems, digest.needsEdvardBlocks);

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
  /* Unsent comment text, keyed by which box it was typed into, so it
   * survives the re-render that discards the box. See `renderComments`. */
  var drafts = {};

  /* Whether a folded thread has been opened, keyed the same way and here for
   * the same reason: a full `render` throws the drawer away, and the fold
   * state is something he asked for by tapping. The 8s reply poll and the
   * refetch after a send both repaint inside the existing drawer, so those
   * were never the problem -- a new journal entry landing is, and one lands
   * about every forty minutes. Without this, opening the earlier replies to
   * read them would silently undo itself while he was reading them. */
  var expanded = {};

  /* "40 seconds" / "3 minutes" / "1 hour 5 minutes" -- how long a reply has
   * been in flight. Deliberately coarse above a minute: the point is to let
   * Edvard tell a slow answer from a stuck one, and a ticking second count
   * reads as a stopwatch on something he cannot hurry. Anything missing or
   * nonsensical falls back to "a moment", because a wait line that renders
   * "NaN minutes" is worse than the fixed sentence it replaced. */
  function waitedFor(seconds) {
    /* `typeof` rather than `Number()` alone, because Number() is generous in
     * exactly the directions that hurt: Number(null), Number([]) and
     * Number("") are all 0, and Number(true) is 1, so a null the server
     * never means to send would render as a confident "0 seconds". The
     * comment above promises a fallback and this is what makes it true. */
    if (typeof seconds !== "number") return "a moment";
    var total = Math.floor(seconds);
    if (!isFinite(total) || total < 0) return "a moment";
    if (total < 60) return total + " second" + (total === 1 ? "" : "s");
    var minutes = Math.floor(total / 60);
    if (minutes < 60) return minutes + " minute" + (minutes === 1 ? "" : "s");
    var hours = Math.floor(minutes / 60);
    var rest = minutes % 60;
    var text = hours + " hour" + (hours === 1 ? "" : "s");
    if (rest) text += " " + rest + " minute" + (rest === 1 ? "" : "s");
    return text;
  }

  function renderComments(container, target, comments) {
    var drawer = el("div", "comment-drawer");

    /* Edvard, comments board 2026-08-16, three times inside fifteen
     * minutes: *"it creates a very long list of previous conversations.
     * Something must be done with this, immediately!"*, *"I still see it
     * with a long conversation of previous messages that is not relevant
     * anymore"*, and *"I see all of this text which is quite a lot to
     * scroll past every single time i want to read your newest journals,
     * which is 6-8 times a day."*
     *
     * He is describing the Needs Edvard block, and the cause is that its
     * drawer is the one drawer that is never folded (see `renderNeeds`) --
     * so every reply he has ever made to it, since 2026-08-10, is painted
     * open at the top of the page, above the newest journal card. A cycle
     * card has the same thread and nobody notices, because a card's drawer
     * is shut until you open it.
     *
     * So the fold goes on the thread rather than on the drawer, and the
     * line it folds at is the one the file already draws: a comment under
     * `## Acknowledged` is one a cycle has acted on and retired, which is
     * exactly "not relevant anymore" in his words. What is left open is
     * the trailing run he has not had an answer to yet, which is never
     * more than a couple of items and always includes one he just typed --
     * so the confirmation that a comment saved, which is why this list is
     * shown at all, survives the fold.
     *
     * Retired ones are not dropped. Losing them would trade his complaint
     * for a worse one, and `earlier` is one tap away. */
    var earlierBtn = el("button", "comment-earlier");
    earlierBtn.type = "button";
    earlierBtn.hidden = true;
    earlierBtn.addEventListener("click", function () {
      expanded[target.key] = !expanded[target.key];
      paint(lastItems);
    });
    drawer.appendChild(earlierBtn);

    var list = el("div", "comment-list");
    drawer.appendChild(list);

    var box = el("textarea", "comment-text");
    box.rows = 3;
    /* A render throws every drawer away and builds a new one, so anything
     * typed and not yet sent dies with the old node. `poll` avoids that by
     * refusing to re-render while there is text in a box -- which works for
     * a background timer and cannot work for "Show older entries", where
     * the re-render is the thing the reader just asked for. So the text
     * outlives the node instead. Cleared only when the server confirms the
     * write, the same rule `submit` already follows for the box itself. */
    if (drafts[target.key]) box.value = drafts[target.key];
    box.addEventListener("input", function () { drafts[target.key] = box.value; });
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

    /* Where the fold falls: everything before the last unretired comment.
     *
     * Not `filter(acknowledged)`, which would be the same thing only while
     * the file stays tidy. A cycle that retires an older comment and leaves
     * a newer one open would, under a filter, hide a message from the
     * middle of a conversation and leave the two either side of it touching
     * -- so the thread would read as continuous while a turn of it was
     * missing. Cutting at the last retired item can only ever hide a
     * prefix, which is what a "show earlier" control claims to be doing. */
    var lastItems = comments;

    function foldPoint(items) {
      /* The *leading* run of retired comments, and only that.
       *
       * I first cut at the last retired comment instead, on the assumption
       * that retirement runs in order, so everything before the newest
       * retired one must be retired too. That assumption is false and
       * nothing enforces it: `tools/ack_comment.py` retires one comment by
       * `(cycle, stamp)`, whichever a cycle chose to act on, so a cycle can
       * answer a newer message and leave an older one open. `_oldest_first`
       * in `nova_comments.py` exists precisely because the two sections are
       * not chronological with respect to each other.
       *
       * Under the old cut, `[live 08:00, retired 09:00, live 10:00]` hid the
       * 08:00 one — a message still waiting on an answer from me, buried
       * behind "Show 2 earlier replies" where he would never look for it.
       * That is the exact opposite of what this fold is for.
       *
       * Stopping at the first live comment costs a little folding — a
       * retired message sitting mid-thread stays on screen — and buys the
       * property the fold actually needs: nothing I have not answered is
       * ever hidden. */
      var cut = 0;
      var items_ = items || [];
      while (cut < items_.length && items_[cut].acknowledged) cut += 1;
      // Never fold the whole thread away to nothing: with every comment
      // retired there is no live exchange to keep open, and an empty list
      // under a "show 6 earlier" button reads as a broken drawer.
      return cut >= (items || []).length ? 0 : cut;
    }

    function paint(items) {
      lastItems = items;
      list.textContent = "";
      var cut = target.fold ? foldPoint(items) : 0;
      var showEarlier = !!expanded[target.key];
      if (cut && !showEarlier) {
        items = (items || []).slice(cut);
      }
      earlierBtn.hidden = !cut;
      earlierBtn.textContent = showEarlier
        ? "Hide earlier replies"
        : "Show " + cut + (cut === 1 ? " earlier reply" : " earlier replies");
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
         * would read as broken.
         *
         * It is a sibling of the comment it answers, not a child of it:
         * Edvard, issues.md 2026-08-10, "they should be below each other
         * on the same indentation. So the comments alternates between blue
         * and green downwards." Which comment a reply belongs to is now
         * carried by the order alone, and the order is the conversation. */
        var after = null;
        if (comment.reply) {
          var reply = el("div", "comment comment-reply");
          var meta = el("p", "comment-meta");
          meta.appendChild(el("span", "comment-who", "Nova"));
          meta.appendChild(el("span", "comment-stamp", comment.replyStamp || ""));
          reply.appendChild(meta);
          String(comment.reply).split(/\n{2,}/).forEach(function (para) {
            if (para.trim()) reply.appendChild(el("p", "comment-body", para));
          });
          after = reply;
        } else if (comment.replyWaiting) {
          /* Past the server's threshold, so this is no longer a reply being
           * written in the ordinary way -- but nothing here knows why, and
           * this line used to claim it did ("Queued behind a running
           * cycle"). Replies take a parallel lane past the bridge lock
           * almost always, so that cause was usually false. Report the one
           * fact the server has -- how long it has been -- and name no
           * cause; the elapsed time is also what tells him apart a slow
           * answer from a stuck one, which the fixed sentence never could. */
          after = el("p", "comment-waiting",
            "Still working on this — " + waitedFor(comment.replyWaitingSeconds) +
            " so far. The answer appears here on its own.");
        } else if (comment.replyPending) {
          after = el("p", "comment-waiting", "Nova is replying…");
        } else if (comment.replyFailed) {
          /* The line used to just vanish, which reads exactly like an
           * answer that never came. A comment that got no reply is still in
           * `## New`, so the next cycle does read it. */
          after = el("p", "comment-waiting", "Couldn't answer this one — the next cycle will read it.");
        }
        list.appendChild(item);
        if (after) list.appendChild(after);
      });
      // Both of these read the whole thread, not the shown slice. The
      // count on the 💬 toggle is how many comments a cycle has, which the
      // fold does not change; and a reply still being written on a folded
      // comment has to keep the poll alive, or hiding it would stop its
      // answer ever arriving on screen.
      var count = (lastItems || []).length;
      toggle.textContent = count ? "💬 " + count : "💬";
      list.hidden = !(items || []).length;
      watch((lastItems || []).some(function (c) { return c.replyPending; }));
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
        fetchPage("/api/comments")
          .then(function (payload) {
            /* A replayed payload is the worker's saved copy, cached before
             * the reply -- often before the comment -- existed. It parses
             * cleanly and looks exactly like an answer, so without this
             * branch `pick` finds no pending reply in it, `paint` reads that
             * as "the wait is over" and calls `watch(false)`, and the poll
             * stops for good: the drawer sits on a stale list until he
             * reloads the page. That is the same failure the `.catch` below
             * exists for, arriving as a 200 instead of a 500.
             *
             * So it is not repainted at all, rather than repainted and
             * re-watched. A saved copy is strictly older than what is on
             * screen -- the drawer was drawn from a live payload and may
             * hold a reply this one predates -- so painting it would take
             * information away. Keep what is shown, keep waiting. */
            if (payload && payload.replayed) { watch(true); return; }
            paint(target.pick(payload));
          })
          // `watch(true)` is the keep-waiting path, and a 500 used to walk
          // straight past it: the error body parsed, `pick` found nothing
          // in it, and the drawer stopped polling as though it had been
          // told the reply was not coming.
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
          delete drafts[target.key];
          fit();
          status.textContent = "saved";
          // The save already succeeded; this only repaints the bubbles to
          // include it. Swallowed on purpose, and it is the one place in
          // this file where swallowing is right: letting it reach the
          // `.catch` below would replace "saved" with an error message
          // for a comment that is safely written, and he would send it
          // again.
          return fetchPage("/api/comments")
            .then(function (payload) {
              /* Same reason as the poll's branch, and worse here: this
               * refetch runs moments after a write the server confirmed, so
               * a saved copy is guaranteed to predate the comment he just
               * sent. Repainting from one would blank his comment out of the
               * list under a "saved" status line -- the exact thing that
               * makes him send it twice. */
              if (payload && payload.replayed) return;
              paint(target.pick(payload));
            })
            .catch(function () {});
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
      key: "cycle:" + cycle,
      placeholder: "Say something about cycle " + cycle + "…",
      ariaLabel: "Comment on cycle " + cycle,
      body: function (text) { return { cycle: cycle, text: text }; },
      pick: function (data) { return ((data && data.byCycle) || {})[String(cycle)]; },
    };
  }

  function needsTarget() {
    return {
      key: "needs",
      placeholder: "Answer…",
      ariaLabel: "Reply to Needs Edvard",
      /* Only this target folds. A cycle card's drawer is already shut, so
       * folding its thread as well would hide history behind two taps to
       * fix a problem it does not have. This block is pinned open above the
       * newest journal card and is the one he has to scroll past 6-8 times
       * a day. */
      fold: true,
      body: function (text) { return { target: "needs", text: text }; },
      pick: function (data) { return (data && data.needs) || []; },
    };
  }

  /* One card per cycle, however many entries that cycle wrote.
   *
   * Edvard, on the comments board at cycle 81: "i do not like the double
   * entry Journal cards. If a double entry is necessary like for cycle 81,
   * have it be combined into one card that has tabs or something similar.
   * Its confusing that its two separate cards."
   *
   * Cycle 105 answered this on `/cycle/N` and deliberately left the feed
   * alone, which left the surface he was actually looking at still drawing
   * two. This is the other half. `parts` arrives newest-first off the wire,
   * the same slice the page gets, and the card reads them forwards.
   *
   * Tabs are what he suggested and this is not tabs, which he can reverse in
   * one sentence. Two parts of one cycle are one continuous account -- the
   * second is almost always "the deploy I could not see came up healthy" --
   * and a tab would hide half of a drawer you opened to read the whole
   * thing. Dated subheadings inside one drawer say the same thing without
   * asking you to find the other half.
   *
   * Everything that used to decide which of two cards owned the cycle's
   * digest line, its anchor id and its comment thread is gone with them.
   * There is one card, so it owns all three. */
  function renderEntry(parts, digestLine, comments) {
    var ordered = parts.slice().reverse();
    var entry = ordered[0];
    var settled = settledPart(ordered);

    var card = el("article", "entry");
    /* Edvard, comments board at cycle 156, asking for an eight-cycle report
     * card: "They should appear like a journal card, but stand out in both
     * color and form to show that they are just summaries." The server
     * decides which entries those are (`nova_journal.parse_heading`); this
     * only carries its answer into the class, so the two cannot drift. */
    if (entry.kind === "report") card.className = "entry is-report";
    if (entry.cycle !== null && entry.cycle !== undefined) {
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

    /* No chevron. Edvard, issues.md #59: "Remove the arrow that shows if
     * the dropdown is open/closed." The card already answers that twice
     * over -- collapsed shows a one-line brief and a "Read the full
     * journal" button, expanded shows the prose and "Close the full
     * journal" -- so the arrow restated what the button beside it said in
     * words. `aria-expanded` on the toggle is the accessible answer and
     * is untouched; the arrow was decoration, not the affordance. */

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

    /* The stamp is the earliest part's, because that is when the cycle
     * began; the outcome is the settled one. `appendOutcome` carries the
     * pill, the linkified PR references and the qualifier five entries
     * have ("stuck — CI outage, merged nothing"), and is the same call the
     * page makes, so the two cannot say different things about one cycle. */
    var meta = el("div", "entry-meta");
    var stamp = [entry.date, entry.time].filter(Boolean).join(" ");
    if (stamp) meta.appendChild(el("time", "stamp", stamp));
    appendRuntime(meta, entry);
    appendOutcome(meta, settled);
    if (meta.childNodes.length) card.appendChild(meta);

    /* A one-part cycle's title has nowhere else to go; a multi-part cycle's
     * titles are the subheadings inside the drawer, where they say which
     * half you are in. `cleanTitle` because eleven entries have a title that
     * is only their own timestamp, which the stamp above already prints.
     * `hasDigestBrief` because a card with a digest line already has a
     * sentence doing this job -- see its own comment, and issues #86. */
    if (ordered.length === 1 && entry.cycle !== null && entry.cycle !== undefined
        && cleanTitle(entry.title) && !hasDigestBrief(digestLine)) {
      card.appendChild(el("p", "entry-title", cleanTitle(entry.title)));
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

    /* Drawer two. A cycle that wrote more than once says so on the button,
     * because that is where you decide whether to open it -- and because
     * the subheadings you find inside otherwise arrive unannounced. */
    var openLabel = ordered.length > 1
      ? "Read the full journal (" + ordered.length + " entries)"
      : "Read the full journal";
    var journalToggle = el("button", "journal-toggle", openLabel);
    journalToggle.type = "button";
    journalToggle.setAttribute("aria-controls", bodyId);
    card.appendChild(journalToggle);

    /* The drawer wraps the parts rather than being one of them, so the tab
     * strip is hidden and shown with the prose it divides.
     *
     * `fold` is read here rather than at its old declaration forty lines
     * down: `var` hoists, so the name existed and was `undefined`, and
     * passing it in silently disabled the tab memory while every test still
     * passed. It is the same object either way -- `foldFor` memoises per
     * cycle -- so this is a move, not a second one. */
    var fold = foldFor(entry.cycle);
    var body = el("div", "entry-parts");
    body.id = bodyId;
    appendParts(body, ordered, settled, fold);
    card.appendChild(body);

    /* One comment button per cycle, which is now simply one per card.
     *
     * A comment is stored keyed by cycle number, so an entry with no number
     * has nowhere for one to land -- offering the button there would be a
     * box that silently drops what he typed. */
    var commenting = null;
    if (entry.cycle !== null && entry.cycle !== undefined) {
      /* Bottom right of the card rather than beside the permalink in the
       * head -- Edvard, ideas.md 2026-08-10: "Move the Journal chat bubble
       * icon to the bottom right of the Journal cards."
       *
       * The foot is appended *before* renderComments, because renderComments
       * appends the drawer to the same container: build it after and the
       * drawer opens above the button that opened it. */
      var foot = el("div", "entry-foot");
      card.appendChild(foot);
      commenting = renderComments(card, cycleTarget(entry.cycle), comments);
      foot.appendChild(commenting.toggle);
    }

    /* The three setters are the only places a card changes state, so they
     * are also the only places that have to remember it -- a tap goes
     * through one of these whether it came from the card's own listener or
     * from `setExpanded` re-asserting a drawer. See `folds`. */

    function setCommentsOpen(open) {
      if (!commenting) return;
      fold.comments = open;
      card.classList.toggle("is-commenting", open);
      commenting.toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    setCommentsOpen(fold.comments);

    function setJournalOpen(open) {
      fold.journal = open;
      card.classList.toggle("is-reading", open);
      journalToggle.setAttribute("aria-expanded", open ? "true" : "false");
      journalToggle.textContent = open ? "Close the full journal" : openLabel;
    }

    function setExpanded(open) {
      fold.expanded = open;
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
    /* Order matters: `setExpanded` re-derives the journal drawer from the
     * button it has just been given, so the drawer has to be put back
     * before the card is, or a card restored open would restore shut. */
    setJournalOpen(fold.journal);
    setExpanded(fold.expanded);

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
      /* A tap on a part tab has somewhere else to go, the same as a link.
       * Without this the card's listener fires too and collapses the whole
       * card out from under the tab you just pressed -- the drawer shuts,
       * and the part you asked for flashes into view and disappears with
       * it. Found in a real browser; every jsdom test passed, because they
       * assert which panel is `hidden` and the panel is correct right up
       * until the card closes over it. The guard lives here rather than as
       * a `stopPropagation` in the strip because this file keeps one
       * listener that decides what a tap meant, and a second one drifts. */
      if (event.target.closest(".entry-tabs")) return;
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

  /* `/cycle/81` is a page, not a card.
   *
   * Edvard, inside issue #59: "its not the link thats the problem, its the
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

  function appendOutcome(row, entry) {
    if (entry.outcome) row.appendChild(el("span", outcomeClass(entry.outcome), entry.outcome));
    if (entry.pr) {
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
    if (entry.outcomeDetail) row.appendChild(el("span", "outcome-detail", entry.outcomeDetail));
    return row;
  }

  /* Edvard, issues #86: "Journal cards like cycle 209 seems to have two
   * titles. Only one is enough."
   *
   * A card draws the entry's own `### ` heading title and, directly under
   * it, the brief. When the brief comes from the digest line those are two
   * sentences written for two different purposes, saying the same thing --
   * cycle 209's heading is "Edvard asked for tabs three times and I finally
   * built them" and its digest brief is "You asked three times for the
   * double journal entries to be one card with tabs, and now they are."
   * The digest line is the one he reads, so it is the one that stays.
   *
   * The 55 archival entries with no digest line are untouched: their brief
   * is their own first paragraph, which is prose rather than a second
   * title, and dropping the heading there would leave the card unlabelled. */
  function hasDigestBrief(digestLine) {
    return !!(digestLine && digestLine.briefSpans && digestLine.briefSpans.length);
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
   *  **Tabs, because Edvard asked three times.** Comments board at cycle
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
      var seq = nextBodyId++;
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
     * than as a date printed twice. `hasDigestBrief` because a card with a
     * digest line already has a sentence doing this job -- see its own
     * comment, and issues #86. */
    if (parts.length === 1 && cleanTitle(first.title) && !hasDigestBrief(digestLine)) {
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

    function setCommentsOpen(open) {
      card.className = open ? "entry is-page is-commenting" : "entry is-page";
      commenting.toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    setCommentsOpen(false);

    /* The only thing left to click. Nothing on this page collapses, so the
     * card has no toggle listener -- a tap on the prose does nothing, which
     * is what a tap on prose should do. */
    card.addEventListener("click", function (event) {
      if (event.target.closest("a")) return;
      if (event.target.closest(".comment-drawer")) return;
      if (event.target.closest(".comment-toggle")) {
        setCommentsOpen(commenting.toggle.getAttribute("aria-expanded") !== "true");
      }
    });
    return card;
  }

  function render(journal, digest, comments) {
    stopPolling();
    markNav();
    // What the page is now showing, so the poll below can tell "nothing
    // changed" from "changed while he was typing".
    renderedVersion = (journal && journal.version) || null;
    renderedComments = JSON.stringify(comments);
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

    /* One card per cycle, newest cycle first.
     *
     * A cycle's entries are usually adjacent on the wire but are not
     * required to be -- an addendum is written whenever the cycle that owns
     * it comes back, which can be after the next cycle has already filed
     * its own entry. So the group takes the position of the cycle's newest
     * part, and later parts join it wherever they turn up.
     *
     * An entry with no cycle number (Edvard's own notes) is its own group:
     * there is nothing to key it on, and collapsing them all under `null`
     * would merge unrelated notes into one card. */
    var groups = [];
    var groupIndex = {};
    entries.forEach(function (entry) {
      var cycle = entry.cycle;
      if (cycle === null || cycle === undefined) {
        groups.push([entry]);
        return;
      }
      if (cycle in groupIndex) {
        groups[groupIndex[cycle]].push(entry);
        return;
      }
      groupIndex[cycle] = groups.length;
      groups.push([entry]);
    });

    feed.textContent = "";
    /* The comments read is tolerated on purpose -- the journal is the page,
     * and a comments failure should cost the bubbles, not the feed. But
     * tolerating it silently is what made a 502 look like "nobody has
     * commented", which is a different and much more convincing lie than
     * "this did not load". The `json` check above turns the failure into a
     * null; this is the only thing that says so on screen.
     *
     * `null` is reachable only from that catch: the endpoint answers with
     * an object, and a 304 is never asked for on this one. */
    if (comments === null) {
      feed.appendChild(el("p", "empty", "Comments could not be loaded — the entries below are complete, the replies are not."));
    }
    if (wanted !== null) {
      var back = el("a", "back", "← all cycles");
      back.href = "/";
      feed.appendChild(back);
      if (!entries.length) feed.appendChild(el("p", "empty", "No entry for cycle " + wanted + "."));
    }
    if (wanted !== null) {
      if (entries.length) {
        feed.appendChild(renderCyclePage(wanted, entries, byCycle[wanted],
          commentsByCycle[String(wanted)]));
      }
      return;
    }
    /* The hole in the record, marked where it happened (#72). Edvard found
     * cycles 127 and 128 himself, by noticing the numbers on this feed jump
     * from 126 to 129 -- so the gap is put back exactly where he was
     * already looking, rather than summarised in a counter at the top.
     *
     * The server decides what counts as missing; this only decides where
     * to put it. That matters because a window is a contiguous slice of
     * the corpus but an unnumbered entry is not: filling in every number
     * between two cards from the client's own arithmetic would invent gaps
     * for Edvard's own notes, which have no cycle number to be missing.
     *
     * Where a hole belongs is decided by the cycle numbers, not by which
     * two cards happen to be adjacent. The feed is not sorted by cycle: a
     * card takes the position of its cycle's *newest* part, so an addendum
     * filed after the next cycle has already written puts a lower number
     * above a higher one. Reading the gap off the previous card then
     * announced "Cycles 142, 143 ran and wrote no entry" with 144's own
     * card sitting underneath it. So the invariant is the one a reader can
     * actually check by scrolling: **a hole is never drawn above a card
     * newer than it.** Each one is anchored under the *last* card in the
     * feed that is newer than the hole -- not the numerically smallest
     * such card, which in a scrambled feed can still have two newer cards
     * below it. It is drawn only when the window also holds a card older
     * than the hole: a gap that runs off either end of the window belongs
     * to entries nobody has loaded yet, and pinning it to the edge would
     * claim a boundary this page cannot see. */
    var missing = {};
    (journal.status && journal.status.missingCycles || []).forEach(function (n) {
      missing[n] = true;
    });
    var cycles = groups.map(function (parts) { return parts[0].cycle; });
    var markers = {};
    Object.keys(missing).forEach(function (key) {
      var n = Number(key);
      var above = -1;
      cycles.forEach(function (cycle, i) {
        if (typeof cycle === "number" && cycle > n) above = i;
      });
      /* No guard for `above` finding nothing: a hole newer than every card
       * anchors to -1, and the render loop below only ever asks for indexes
       * it is drawing, so it is dropped there. A guard here would be a
       * branch no observation could distinguish. */
      var below = cycles.some(function (cycle) {
        return typeof cycle === "number" && cycle < n;
      });
      if (!below) return;
      (markers[above] = markers[above] || []).push(n);
    });
    groups.forEach(function (parts, index) {
      var cycle = parts[0].cycle;
      feed.appendChild(renderEntry(parts, byCycle[cycle], commentsByCycle[String(cycle)]));
      if (!markers[index]) return;
      var gap = markers[index].sort(function (a, b) { return a - b; });
      feed.appendChild(el("p", "cycle-gap", gap.length === 1
        ? "Cycle " + gap[0] + " ran and wrote no entry"
        : "Cycles " + gap.join(", ") + " ran and wrote no entry"));
    });

    /* `total` is the whole corpus, `entries.length` is what came back in
     * this window, so the pager disappears on its own at the last page and
     * never appears at all on a server that does not paginate. */
    var total = journal.total;
    if (wanted === null && typeof total === "number" && entries.length < total) {
      var more = el("button", "more", "Show older entries");
      more.type = "button";
      more.addEventListener("click", function () {
        more.disabled = true;
        more.textContent = "Loading…";
        windowSize += PAGE;
        load();
      });
      feed.appendChild(more);
      loadWhenScrolledTo(more);
    }
  }

  /* The last full payload for each versioned endpoint, so a 304 can be
   * answered from memory rather than by asking again without the header.
   *
   * The server has answered `If-None-Match` with a 304 since #77 and
   * nothing has ever sent one. Measured against the live pod on
   * 2026-08-11, one poll is 227,520 gzipped bytes -- journal 184,658,
   * digest 36,814, comments 6,048 -- and it repeats every 30 seconds for
   * as long as the tab is visible. That is 27MB an hour on a phone to
   * learn that nothing changed, which it usually has not: a cycle writes
   * one entry an hour and this polls 120 times in it. Conditional, the
   * same poll is the 6,048 bytes of comments and two empty 304s.
   *
   * The version is read out of the payload rather than the ETag header,
   * for the reason `_versioned` puts it in both: a response served from
   * the service worker's cache has no headers the page can reach, and a
   * poll that could not find its etag would silently go back to asking
   * for all 184KB. The two strings are the same by construction.
   */
  var lastPayload = { journal: null, digest: null };

  /* Four `.catch` blocks on GETs in this file already append a written
   * "Could not load ..." line, and until now not one of them could fire.
   * The other two GET catches recover rather than report -- the feed's
   * comments read degrades to null, the drawer's poll keeps waiting --
   * and those could not fire either.
   *
   * `fetch` rejects only when the request never completed; a 500 or a 502
   * is a perfectly successful response, and the error body the server
   * sends is valid JSON, so `r.json()` resolved and the page went on to
   * render an object with no `entries` in it. That is the whole reason a
   * server error has always looked like an empty page rather than a
   * message: the messages were there, the condition that reaches them
   * never was.
   *
   * Note this is the read side only, and the POSTs below are genuinely
   * fine without it: they check `result.ok` out of the parsed body, and
   * the server sends `{"ok": false, "message": ...}` on a rejected write
   * deliberately. The generic 502 sends `{"error": ...}` with no `ok` at
   * all, which that same check also catches -- so the POST path is right
   * on purpose in the first case and right by accident in the second.
   */
  function json(r) {
    if (r.ok) return r.json();
    // The server's own message when it sent one, because "the digest file
    // is not valid markdown" is worth more on screen than "HTTP 500". The
    // body is not guaranteed to be JSON at all (a proxy's 502 page is
    // not), so failing to read it falls back to the status.
    return r.json().then(
      function (body) {
        throw new Error((body && (body.error || body.message)) || "HTTP " + r.status);
      },
      function () {
        throw new Error("HTTP " + r.status);
      }
    );
  }

  /* Whether `sw.js` served this out of its cache instead of the network.
   *
   * Defensive about `headers` because the test doubles in this repo -- and
   * a 304, which carries none -- are plain objects with only the fields the
   * page reads. Missing means "not replayed", which is the safe direction:
   * the page goes on believing a live answer is live.
   */
  /* A copy of `source` with `extra`'s fields over the top. `Object.assign`
   * with an object literal, spelled out, because this file is ES5 throughout
   * and the point here is to leave the original untouched. */
  function shallow(source, extra) {
    var out = {};
    Object.keys(source).forEach(function (k) { out[k] = source[k]; });
    Object.keys(extra).forEach(function (k) { out[k] = extra[k]; });
    return out;
  }

  function isReplayed(r) {
    return !!(r && r.headers && r.headers.get && r.headers.get("X-Nova-Replayed"));
  }

  /* The saved-copy banner, once, because three more pages need the same two
   * facts said in the same words.
   *
   * `renderStatus` builds its own rather than calling this, and that is not
   * an oversight: the journal header additionally dims its status line and
   * suppresses its badges, because every one of those is a claim about *now*
   * and a replayed payload is evidence about whenever it was cached. A board,
   * a cost chart and a retro ledger are records. Their content stands exactly
   * as it is and only its currency needs marking, so they get the banner and
   * nothing else. */
  function savedCopyLine() {
    var saved = el("p", "status-sub");
    saved.appendChild(el("span", "badge badge-error", "can't reach Nova"));
    saved.appendChild(el("span", "status-pr", "showing a saved copy"));
    return saved;
  }

  /* `fetch(url).then(json)` for the pages that are not the journal, with the
   * service worker's replay stamp carried through on the payload itself.
   *
   * On the payload rather than passed alongside it, because these pages
   * re-render from a payload they already hold -- the board alone re-renders
   * on search, sort, tab and every row toggle, all of which call
   * `renderBoard(board, payload)` with the same closed-over object. A flag
   * threaded through render arguments would have to be threaded through
   * fourteen call sites and would fall off the first one somebody added; on
   * the payload it survives every re-render for free, which is the correct
   * behaviour anyway. A phone that is still offline is still looking at a
   * saved copy after it sorts the column.
   *
   * The opposite of what `fetchVersioned` does above, and deliberately: it
   * must *not* store the mark, because it memoises payloads in `lastPayload`
   * and hands that memo back on a 304, so a stored mark would outlive the
   * outage by up to an hour. There is no memo here -- each visit to these
   * pages fetches afresh -- so the mark cannot outlive the response it came
   * on. `shallow` rather than assignment for the same reason it is used
   * there: the response body is left untouched. */
  function fetchPage(url) {
    return fetch(url).then(function (r) {
      return json(r).then(function (body) {
        if (isReplayed(r) && body) return shallow(body, { replayed: true });
        return body;
      });
    });
  }

  /* `/api/comments` is deliberately *not* routed through the above, and the
   * reason I first wrote down was wrong, so here is the true one.
   *
   * The wrong version: "it is only fetched on the journal page, whose header
   * already carries the mark." Only one of its three call sites makes that
   * true -- the one inside `fetchAll`, which fires in lockstep with the
   * journal read, so a replayed comments payload arrives with a replayed
   * journal payload and the header says so. The other two are independent:
   * the reply drawer's 8s `watch()` poll, and the refetch after a comment is
   * posted. Both can be replayed during a blip too short to fail two
   * consecutive 30s journal polls, so the header stays green while the
   * drawer paints a cached answer.
   *
   * That gap is now closed, and this paragraph is kept because the fix only
   * makes sense against it. Both independent call sites go through
   * `fetchPage` and both refuse to act on a payload it marks: the poll keeps
   * waiting, the post-comment refetch leaves the list alone. Marking the
   * fetch was never the fix on its own -- a banner over a drawer that had
   * silently stopped polling would still have left him waiting forever --
   * so what the mark buys is the ability to tell a saved copy from an
   * answer, and the two branches below are what act on it.
   *
   * `fetchAll`'s call site is deliberately left plain. It fires in lockstep
   * with the journal read, so a replayed comments payload arrives with a
   * replayed journal payload and the header already says so; and it renders
   * the drawer from scratch rather than deciding whether a wait is over, so
   * there is no wait for a stale payload to end early. */

  function fetchVersioned(url, key) {
    var known = lastPayload[key] && lastPayload[key].version;
    // `no-store` keeps this the only conditional request in play. Neither
    // response carries `Cache-Control`, so whether the browser's own HTTP
    // cache revalidates is a heuristic that differs per browser -- and a
    // heuristic hit would answer this poll from a cache instead of asking
    // the server, which is the one thing a poll must not do.
    var init = { cache: "no-store" };
    if (known) init.headers = { "If-None-Match": known };
    return fetch(url, init).then(function (r) {
      // 304 carries no body. Returning the remembered payload keeps every
      // caller working on a whole object, so `render` and the version
      // comparison in `poll` need to know nothing about any of this.
      // Checked before `json` and it has to stay that way: a 304 is not
      // `ok`, so an ok-check in front of this would turn every successful
      // conditional poll -- the common case, once the page has loaded
      // once -- into an error.
      if (r.status === 304 && lastPayload[key]) return lastPayload[key];
      return json(r).then(function (body) {
        // The service worker answered a dead network out of its cache and
        // stamped the response so this can tell. `no-store` above rules out
        // the browser's own HTTP cache but not the worker, which sits in
        // front of it -- so without the stamp a resumed phone renders an
        // arbitrarily old payload as current. Carried on `status` because
        // that is the object `renderStatus` is handed; `/api/digest` has no
        // `status` and needs none, its content is not a claim about now.
        //
        // **Remembered clean, returned marked**, and that distinction is the
        // whole bug rather than a nicety. Mutating `body` here would store
        // the mark in `lastPayload`, and `lastPayload` is what the 304 branch
        // above hands back -- so the *next* poll, on a network that has come
        // back, replays the mark. The etag is deliberately stable while the
        // loop is quiet (`journal_descriptor`), so that 304 is the common
        // case, and "can't reach Nova" would stick to the header for up to an
        // hour after the app was last actually offline. That is the flash
        // Edvard reported, inverted onto the banner meant to explain it.
        lastPayload[key] = body;
        if (isReplayed(r) && body && body.status) {
          return shallow(body, { status: shallow(body.status, { replayed: true }) });
        }
        return body;
      });
    });
  }

  /* A deep link asks for its own cycle by number rather than for a window,
   * because the entry it wants is usually older than the first page and the
   * page has no way to know how far back that is. */
  function journalUrl() {
    var wanted = routedCycle(window.location.pathname);
    if (wanted !== null) return "/api/journal?cycle=" + wanted;
    return "/api/journal?limit=" + windowSize;
  }

  /* The digest takes the same window as the feed, so the summaries that
   * come back are the summaries of the cards on screen -- 266KB of the
   * digest's 271KB is its lines, and the page shows twenty cycles of them.
   * Asked for alongside the journal rather than after it: the server
   * resolves the window to a cycle range on its side, so neither request
   * has to wait to find out what the other got. */
  function digestUrl() {
    var wanted = routedCycle(window.location.pathname);
    if (wanted !== null) return "/api/digest?cycle=" + wanted;
    return "/api/digest?limit=" + windowSize;
  }

  function fetchAll() {
    return Promise.all([
      fetchVersioned(journalUrl(), "journal"),
      fetchVersioned(digestUrl(), "digest").catch(function () { return null; }),
      // Tolerated the same way the digest is: the journal is the page, and
      // a comments read that fails should cost the bubbles, not the feed.
      // Not conditional: it is uncached and unversioned on purpose, because
      // it changes underneath itself while a reply is being written.
      fetch("/api/comments").then(json).catch(function () { return null; }),
    ]);
  }

  /* ---- The board pages: Issues and Ideas (issues.md #57) ----------------
   *
   * Edvard: "I need more visualisations in the Nova app. Create more
   * pages to contain more, such as issue list, idea list (separate
   * pages) ..."
   *
   * Two tabs per page, because the two files are genuinely different
   * documents rather than two halves of one list: his is boarded (a
   * numbered item with a status and a written-up detail section), mine is
   * a flat stream of one-line captures with a date and a cycle number.
   * Merging them into one list would have to invent a status for mine and
   * a cycle for his. "Who wrote it" is also the thing you sort by in your
   * head when you go looking for something.
   *
   * The rows come down with the page; a detail body does not, and is
   * fetched on the tap that opens it. `issues.md` is 68KB and ~60KB of
   * that is `# Details` -- the same shape as the journal and the digest
   * before #85 and #86, and the same fix, applied before it became a
   * complaint rather than after.
   */
  var BOARD_NOTES = 30;
  var boardState = {
    tab: "edvard",
    filter: "open",
    notes: BOARD_NOTES,
    open: null,
    details: {},
    // The three halves of ideas.md #70/#71, kept on one state object
    // because they compose: search cuts the list down, the toggles cut
    // it further, sort orders what is left. `query` is what is typed;
    // `matches` is what the server said about the write-ups for that
    // exact string, or null when no answer is in yet.
    query: "",
    matches: null,
    matchedQuery: null,
    toggles: {},
    sort: "filed",
    desc: false,
    // Whether the filter modal (Edvard, 2026-08-14: "make the filters
    // into a modal... remove all the filter buttons") is open, so a
    // re-render triggered by tapping an option inside it -- every filter
    // and toggle click already calls `renderBoard` -- knows to rebuild
    // the modal's contents in place instead of leaving it showing stale
    // counts and "on" states, or closing it under the reader's thumb.
    filtersOpen: false,
  };

  /* `outdated` is the fifth status, from issues.md #85: "Some of them are
   * implemented and some of them are outdated. We need to clean it up.
   * Maybe we need a new status called 'outdated', so i can go through them
   * and delete them myself." A cycle sets it; only he acts on it. So it
   * has to leave Open -- a row nobody will ever build is not open work, and
   * leaving it there is the pile he asked to shrink -- without becoming
   * Done, which would claim it shipped. It gets its own filter instead,
   * because "go through them myself" is a list he has to be able to reach.
   * Nothing here touches the hold-menu: edit and delete are ungated on
   * every row and always have been, which `delete_row`'s own docstring
   * gives the reason for -- "deleting a finished item is the most likely
   * thing Edvard wants". So an outdated row stays deletable because there
   * was never a gate, not because this filter spared it. */
  function isOutdated(item) { return item.statusKey === "outdated"; }

  var FILTERS = [
    {
      key: "open",
      label: "Open",
      match: function (i) { return i.statusKey !== "done" && !isOutdated(i); },
    },
    { key: "done", label: "Done", match: function (i) { return i.statusKey === "done"; } },
    { key: "outdated", label: "Outdated", match: isOutdated },
    { key: "all", label: "All", match: function () { return true; } },
  ];

  /* The extra filters, on top of Open/Done/All rather than instead of it
   * -- Edvard, ideas.md #71: "filter the list based on different
   * parameters like date, this week, priority etc. Invent 5-6 more."
   * These are the ones I wrote back to him that need no data the page
   * does not already hold. They are toggles and they AND together, so
   * "unrated and untouched for a week" is one tap each rather than a
   * combination somebody has to have thought of in advance. */
  var STALE_DAYS = 7;
  var WEEK_DAYS = 7;

  /* `updated` is the board table's fourth column and it carries **no
   * year** -- every row on both live files reads `08-14`, not
   * `2026-08-14`. The first version of this required `YYYY-MM-DD`, which
   * matches nothing on either board, so both date filters and the Age
   * sort would have shipped silently dead with every test green. That is
   * the Cycle 190 failure exactly, and this time the fixture is what
   * caught it.
   *
   * A bare `MM-DD` is this year unless that puts it in the future, in
   * which case it is last year's -- a board written in December and read
   * in January is the only case that matters and it is real. A day or
   * two of future is tolerated rather than rolled back a year, because a
   * timezone difference between the writer and the reader is far more
   * likely than a row filed eleven months ahead. Both shapes are
   * accepted so a later change to the column does not break this again.
   */
  var FUTURE_GRACE_DAYS = 2;

  function itemAgeDays(item) {
    var text = (item.updated || "").trim();
    var stamp = null;
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
      stamp = Date.parse(text + "T00:00:00Z");
    } else if (/^\d{2}-\d{2}$/.test(text)) {
      var now = new Date();
      stamp = Date.parse(now.getUTCFullYear() + "-" + text + "T00:00:00Z");
      if (!isNaN(stamp) && stamp - now.getTime() > FUTURE_GRACE_DAYS * 86400000) {
        stamp = Date.parse(now.getUTCFullYear() - 1 + "-" + text + "T00:00:00Z");
      }
    }
    // A row with no date has no age rather than an age of zero --
    // returning 0 would make it the newest thing on the board, which is
    // the opposite of true.
    if (stamp === null || isNaN(stamp)) return null;
    return Math.floor((Date.now() - stamp) / 86400000);
  }

  var TOGGLES = [
    {
      key: "unrated",
      label: "Unrated",
      match: function (i) { return !i.priority; },
    },
    {
      key: "week",
      label: "This week",
      match: function (i) {
        var age = itemAgeDays(i);
        return age !== null && age <= WEEK_DAYS;
      },
    },
    {
      key: "stale",
      label: "Untouched " + STALE_DAYS + "d",
      match: function (i) {
        var age = itemAgeDays(i);
        return age !== null && age > STALE_DAYS && i.statusKey !== "done" && !isOutdated(i);
      },
    },
    {
      key: "worked",
      label: "Nova worked on it",
      // `where` is the `## Done` table's PR column, and `statusKey`
      // carries "in progress" for a row a cycle has started. Both mean
      // this loop has actually touched the row, which is the backwards
      // reading of the board links in ideas.md #68.
      match: function (i) { return !!i.where || i.statusKey === "in-progress"; },
    },
  ];

  /* Sort fields. `filed` is the number, which is the order the board is
   * already in and therefore the one that has to stay the default --
   * changing what an unsorted board looks like is not what #70 asked
   * for. Priority sorts by the rank of the chip, and unrated sorts
   * *last* in both directions, deliberately: "nobody has looked at this"
   * is not a low priority, it is the absence of one, and #69 already
   * settled that it must not fall into a bucket. */
  var PRIORITY_RANK = { immediately: 4, high: 3, medium: 2, low: 1 };

  var SORTS = [
    /* Not the number: the file's own row order, which is what the board
     * has always shown and is not the same thing -- `## Board` is
     * newest-first and `## Done` is appended after it, so #51 sits below
     * #56 while being the lower number. Sorting by the number instead
     * reordered the default view, which is a change #70 did not ask for
     * and which three existing tests caught. `index` is stamped on in
     * `visibleItems` before anything filters the list. */
    { key: "filed", label: "Filed", value: function (i) { return i.index; } },
    {
      key: "priority",
      label: "Priority",
      value: function (i) { return PRIORITY_RANK[i.priorityKey] || 0; },
      unrated: function (i) { return !i.priority; },
    },
    {
      key: "age",
      label: "Age",
      value: function (i) {
        var age = itemAgeDays(i);
        return age === null ? 0 : -age;
      },
      unrated: function (i) { return itemAgeDays(i) === null; },
    },
    { key: "status", label: "Status", value: function (i) { return i.statusKey || ""; } },
    { key: "title", label: "Title", value: function (i) { return (i.title || "").toLowerCase(); } },
  ];

  function currentSort() {
    return SORTS.filter(function (s) { return s.key === boardState.sort; })[0] || SORTS[0];
  }

  function sortItems(items) {
    var sort = currentSort();
    var dir = boardState.desc ? -1 : 1;
    // `slice` because `payload.items` is the cached list every other
    // render reads; sorting in place would make the order depend on
    // which tab you looked at first.
    return items.slice().sort(function (a, b) {
      if (sort.unrated) {
        var au = sort.unrated(a), bu = sort.unrated(b);
        // Always last, whichever way the arrow points -- so `dir` is
        // deliberately not applied here.
        if (au !== bu) return au ? 1 : -1;
      }
      var av = sort.value(a), bv = sort.value(b);
      if (av < bv) return -1 * dir;
      if (av > bv) return 1 * dir;
      // Ties fall back to the file's row order so the sort is total and
      // a re-render never reshuffles rows that compare equal.
      return (a.index - b.index) * dir;
    });
  }

  /* The list Edvard is actually looking at: status filter, then the
   * toggles, then the search, then the order. Search is last of the
   * cuts because it is the only one that can be waiting on the server:
   * until `matches` holds an answer for the string in the box, the title
   * match stands alone, so typing narrows the list immediately and the
   * write-up hits arrive a moment later rather than the box doing
   * nothing until they do. */
  function visibleItems(items) {
    // The file's row order, stamped on before anything cuts the list
    // down, so a filtered view still sorts and breaks ties the way the
    // whole board would. Stamped on the row itself rather than on a
    // copy: `renderPriorityPicker` writes back to the object it was
    // handed, and a copy would take that write with it.
    items.forEach(function (item, index) { item.index = index; });
    var shown = items.filter(currentFilter().match);
    TOGGLES.forEach(function (toggle) {
      if (boardState.toggles[toggle.key]) shown = shown.filter(toggle.match);
    });
    var query = boardState.query.trim().toLowerCase();
    if (query) {
      var matched = boardState.matchedQuery === query && boardState.matches
        ? boardState.matches
        : [];
      shown = shown.filter(function (i) {
        return (i.title || "").toLowerCase().indexOf(query) !== -1
          || matched.indexOf(i.number) !== -1;
      });
    }
    return sortItems(shown);
  }

  function boardTitles(board) {
    return board === "ideas"
      ? { page: "Ideas", mine: "Nova's ideas" }
      : { page: "Issues", mine: "Nova's issues" };
  }

  function currentFilter() {
    return FILTERS.filter(function (f) { return f.key === boardState.filter; })[0] || FILTERS[0];
  }

  function renderBoardStatus(board, payload) {
    var titles = boardTitles(board);
    var items = (payload && payload.items) || [];
    /* Three buckets, not two. The tally used to derive `done` as
     * `total - open`, so the moment a row went outdated it would have been
     * counted as shipped -- the one reading that must never happen, since
     * outdated means the opposite. */
    var outdated = items.filter(isOutdated).length;
    var done = items.filter(function (i) { return i.statusKey === "done"; }).length;
    var open = items.length - done - outdated;
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    /* The page name is bold and the counts are not -- Edvard, issues.md #83:
     * "Make the header for issues and ideas bold". The whole line used to be
     * one dim string, so "Issues" read as part of the tally rather than as
     * the title of the page you are on. Only the name moves; the counts stay
     * `--dim` because they are what the name has to stand out against, and
     * bolding both would be the same flat line again. */
    var line = el("p", "status-line");
    line.appendChild(el("strong", "status-page", titles.page));
    line.appendChild(document.createTextNode(
      " — " + open + " open, " + done + " done, "
        + (outdated ? outdated + " outdated, " : "")
        + ((payload && payload.notesTotal) || 0) + " of my own notes"
    ));
    statusEl.appendChild(line);
    if (payload && payload.replayed) statusEl.appendChild(savedCopyLine());
  }

  /* The four ratings, spelled with the characters themselves rather than
   * with escapes. They have to be byte-identical to `PRIORITY_LABELS` in
   * `nova_boards.py` -- the server checks a submitted rating against that
   * dict and rejects anything else, and a row already rated by a cycle is
   * matched against this list to preselect the option. The first version
   * of this line used Python's `\\U########` form, which is not a
   * JavaScript escape at all: JS drops the backslash and keeps the digits,
   * so three of the four became `U0001f535 Medium` and every write except
   * Low failed. `tests/test_board_priority.py` now reads this line and
   * compares it to the Python side, because nothing else could. */
  var PRIORITIES = ["", "⚪ Low", "🔵 Medium", "🟠 High", "🔴 Immediately"];

  /* Parallel to `PRIORITIES`, mirroring `nova_boards.priority_key` --
   * the CSS class suffix each rating carries (`.prio-high` etc). The
   * server sends `item.priorityKey` for a row's *current* rating, but a
   * picker's chip trigger has to relabel itself the instant something
   * else is picked, before any server round trip, so it needs the same
   * mapping client-side. Only board-row triggers use it (`chipStyle`
   * below); the capture box's trigger shows a glyph, never a class that
   * depends on which rating is selected. */
  var PRIORITY_KEYS = ["", "low", "medium", "high", "immediate"];

  /* A small custom dropdown, not a native <select> -- Edvard, 2026-08-14:
   * the closed control must show only the glyph (or a dash), but the open
   * list must still spell out each rating's word. No native form control
   * can show one thing closed and another open: a <select> renders its
   * selected <option>'s own text in both the box and the popup, so a
   * version of this built on <select> could satisfy one of those asks but
   * never both -- which is exactly the bug he found (the popup was as
   * wordless as the box).
   *
   * One popup, shared by every picker on the page, appended straight to
   * <body> rather than living under each trigger, and centered on the
   * viewport rather than anchored to whichever trigger opened it -- see
   * the comment on `openMenu` below for why. */
  var prioMenuOverlay = null;
  var prioMenuBackdrop = null;
  function getPrioMenuOverlay() {
    if (prioMenuOverlay) return prioMenuOverlay;
    prioMenuBackdrop = el("div", "prio-menu-backdrop");
    prioMenuBackdrop.hidden = true;
    document.body.appendChild(prioMenuBackdrop);
    prioMenuOverlay = el("div", "prio-menu");
    prioMenuOverlay.setAttribute("role", "listbox");
    prioMenuOverlay.hidden = true;
    document.body.appendChild(prioMenuOverlay);
    return prioMenuOverlay;
  }

  /* Two ways the trigger can look, picked by `opts.chipStyle`:
   *
   * The default (the capture box) is `opts.triggerClass` -- a fixed-shape
   * button, `.capture-prio`'s circle, showing only the glyph. That is the
   * shape a fresh, usually-unrated capture needs.
   *
   * `chipStyle: true` (board rows) is the opposite: Edvard, 2026-08-14,
   * after the ball-only version shipped there -- "i liked the old issue
   * priority status better... make it into a button that opens the
   * modal, but the visual design is not changed from the old design."
   * The old design (cycle 171) was a read-only `.chip.prio.prio-<key>`
   * spelling out the rating in full, shown only when a row had one at
   * all. This keeps exactly that classing and text, on a <button>
   * instead of a <span>, plus an "Unrated" chip for rows that have
   * nothing yet -- the one thing the read-only original could not do,
   * because there was nothing to tap to give it a first rating.
   *
   * `onPick` may return a promise; the trigger disables and the label
   * updates optimistically while it settles, and a rejection reverts it
   * to what it was before the click rather than showing a choice that
   * was never saved. */
  function buildPrioPicker(opts) {
    var current = opts.current || "";
    function glyph(label) { return label ? label.split(" ")[0] : "–"; }
    function keyOf(label) {
      var i = PRIORITIES.indexOf(label);
      return i === -1 ? "" : PRIORITY_KEYS[i];
    }

    var trigger = document.createElement("button");
    trigger.type = "button";
    if (opts.triggerId) trigger.id = opts.triggerId;
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-label", opts.ariaLabel);

    function render(label) {
      if (opts.chipStyle) {
        trigger.className = label ? ("chip prio prio-" + keyOf(label)) : "chip prio";
        trigger.textContent = label || "Unrated";
      } else {
        trigger.className = opts.triggerClass;
        trigger.textContent = glyph(label);
      }
    }
    render(current);

    function setValue(label) {
      current = label;
      render(label);
    }

    function pick(label) {
      closeMenu();
      var previous = current;
      setValue(label);
      var result = opts.onPick(label);
      if (result && typeof result.then === "function") {
        trigger.disabled = true;
        result.catch(function () { setValue(previous); }).then(function () { trigger.disabled = false; });
      }
    }

    // Centered and full-width rather than anchored under the trigger
    // (Edvard, 2026-08-14, after using the anchored version live: the
    // native picker it replaced read as a real dialog, and a small
    // anchored dropdown read as a lesser thing next to it) -- so CSS
    // alone centers `.prio-menu`, and this only has to show it and the
    // dimming backdrop behind it.
    function openMenu() {
      var menu = getPrioMenuOverlay();
      menu.textContent = "";
      PRIORITIES.forEach(function (label) {
        var item = document.createElement("button");
        item.type = "button";
        item.className = "prio-option";
        item.setAttribute("role", "option");
        item.textContent = label || "– Unrated";
        item.setAttribute("aria-selected", label === current ? "true" : "false");
        item.addEventListener("click", function (e) {
          e.stopPropagation();
          pick(label);
        });
        menu.appendChild(item);
      });
      prioMenuBackdrop.hidden = false;
      menu.hidden = false;
      menu.dataset.openFor = opts.ariaLabel;
      trigger.setAttribute("aria-expanded", "true");
      document.addEventListener("click", onDocClick, true);
      document.addEventListener("keydown", onKeydown, true);
    }

    function closeMenu() {
      var menu = prioMenuOverlay;
      if (!menu || menu.hidden) return;
      menu.hidden = true;
      prioMenuBackdrop.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      document.removeEventListener("click", onDocClick, true);
      document.removeEventListener("keydown", onKeydown, true);
    }

    // The backdrop is not inside `prioMenuOverlay`, so a tap on it falls
    // through to here and closes the popup like any other outside tap --
    // no separate handler needed for "tap outside to dismiss".
    function onDocClick(e) {
      if (e.target === trigger) return;
      if (prioMenuOverlay && prioMenuOverlay.contains(e.target)) return;
      closeMenu();
    }
    function onKeydown(e) {
      if (e.key === "Escape") { closeMenu(); trigger.focus(); }
    }

    trigger.addEventListener("click", function (e) {
      e.stopPropagation();
      var alreadyOpenHere = prioMenuOverlay && !prioMenuOverlay.hidden
        && prioMenuOverlay.dataset.openFor === opts.ariaLabel;
      if (alreadyOpenHere) closeMenu(); else openMenu();
    });

    return { el: trigger, getValue: function () { return current; }, setValue: setValue };
  }

  /* The rating cell of one boarded row, as something Edvard can change --
   * the row's own priority indicator in `.item-meta-row`, not a second
   * control hidden inside the write-up (Edvard, 2026-08-14: "on issues and
   * ideas the priority button should be the priority tag instead, not a
   * separate button"). `note` is a sibling element the caller places; this
   * only fills it in. No save button on the picker itself, because the
   * only action it can take is the one just chosen, and a button would be
   * a second thing to get wrong. It goes disabled while the write is in
   * flight so a double-tap cannot race two writes at one cell, and on
   * failure it snaps back to what the server still holds rather than
   * showing a rating that was never written. */
  function renderPriorityPicker(board, item, note) {
    return buildPrioPicker({
      current: item.priority || "",
      ariaLabel: "Priority of #" + item.number,
      chipStyle: true,
      onPick: function (chosen) {
        note.textContent = "Saving…";
        return fetch("/api/board/priority", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: board, number: item.number, priority: chosen })
        })
          .then(json)
          .then(function (payload) {
            if (!payload || !payload.ok) throw new Error((payload && payload.message) || "failed");
            item.priority = chosen;
            note.textContent = "";
            // The trigger in the head is built from `item`, so the row has
            // to be redrawn for the change to be visible without a reload
            // -- which is the whole point of editing it here.
            loadBoard(board);
          })
          .catch(function (err) {
            note.textContent = "Could not save: " + err;
            throw err;
          });
      },
    });
  }

  /* Edvard, issue #84: *"If i hold the card for more than 1 second i get
   * into edit mode"*. His number, not a tuned one. */
  var HOLD_MS = 1000;

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
    var del = el("button", "capture-act is-danger", "Delete");
    save.type = "button";
    cancel.type = "button";
    del.type = "button";

    function busy(on, label) {
      save.disabled = on;
      cancel.disabled = on;
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

    actions.appendChild(status);
    actions.appendChild(save);
    actions.appendChild(cancel);
    actions.appendChild(del);
    panel.appendChild(box);
    panel.appendChild(actions);
    return { el: panel, focus: function () { box.focus(); } };
  }

  /* One row of Edvard's board. Closed it is the number, the title and a
   * status chip; open it reveals the write-up, which is a second request
   * the first time a row is opened and memory after that. */
  function renderBoardItem(board, item) {
    var row = el("article", "item item-" + item.statusKey);
    // What `/ideas#68` scrolls to. One board per page, so the number is
    // unique on screen.
    row.id = "item-" + item.number;

    // A <button> (the toggle) cannot contain another <button> (the
    // priority trigger) -- nested interactive controls are invalid HTML.
    // That ruled out a real <button> for `head` once the priority trigger
    // needed to sit inside it, level with the status chip (Edvard,
    // 2026-08-14: "the priority status button needs to be placed on the
    // same horizontal as the progress status, on its right side" -- a
    // sibling next to the whole head, tried first, could only ever line
    // up with the head's first line, not specifically the status chip's).
    // `role="button"` plus a manual Enter/Space handler below is what a
    // <div> needs to behave like the <button> it replaced.
    var head = el("div", "item-head");
    head.setAttribute("role", "button");
    head.setAttribute("tabindex", "0");
    head.setAttribute("aria-expanded", boardState.open === item.number ? "true" : "false");

    var titleRow = el("div", "item-title-row");
    titleRow.appendChild(el("span", "item-number", "#" + item.number));
    titleRow.appendChild(el("span", "item-title", item.title));
    head.appendChild(titleRow);

    // Status and priority on one line, priority pinned to its right --
    // `justify-content: space-between` in style.css is what does that,
    // now that both chips are finally siblings in the same flex row
    // instead of one living outside the head entirely.
    var metaRow = el("div", "item-meta-row");
    metaRow.appendChild(el("span", "chip chip-" + item.statusKey, item.status));

    // Every rating on both boards was set by a cycle, not by Edvard
    // (issues.md capture, 2026-08-14). A finished row keeps the original
    // cycle-171 read-only chip if it has a rating and nothing if it does
    // not -- unrated getting no chip at all, rather than a grey "none"
    // one, is what tells Edvard which open rows still want a rating; a
    // done row is not one he is going to visit for that. `item.done`
    // alone is not the editable test, it only means the row is in the
    // `## Done` table and most finished rows never move there --
    // `statusKey` is what the server refuses a write on. An outdated row
    // is closed the same way, and the server refuses a rating on it for
    // the same reason (`_CLOSED_STATUS_KEYS` in `nova_boards.py`).
    var editable = !item.done && item.statusKey !== "done" && !isOutdated(item);
    var prioNote = el("span", "item-prio-note", "");
    if (editable) {
      metaRow.appendChild(renderPriorityPicker(board, item, prioNote).el);
    } else if (item.priority) {
      metaRow.appendChild(el("span", "chip prio prio-" + item.priorityKey, item.priority));
    }
    head.appendChild(metaRow);
    if (editable) head.appendChild(prioNote);

    // Below the status/priority line rather than beside it (Edvard,
    // 2026-08-14: "the date should be placed below them").
    if (item.updated) head.appendChild(el("span", "item-updated", item.updated));

    row.appendChild(head);

    var body = el("div", "item-body");
    if (boardState.open !== item.number) body.hidden = true;
    row.appendChild(body);

    function fill() {
      body.textContent = "";
      if (item.where) body.appendChild(el("p", "item-where", "Landed in " + item.where));
      var blocks = boardState.details[board + ":" + item.number];
      if (!blocks) {
        body.appendChild(el("p", "empty", "Loading…"));
        fetch("/api/board?name=" + board + "&item=" + item.number)
          .then(json)
          .then(function (payload) {
            boardState.details[board + ":" + item.number] =
              ((payload && payload.item) || {}).blocks || [];
            if (boardState.open === item.number) fill();
          })
          .catch(function (err) {
            body.textContent = "";
            body.appendChild(el("p", "empty", "Could not load #" + item.number + ": " + err));
          });
        return;
      }
      if (!blocks.length) {
        body.appendChild(el("p", "empty", "No write-up yet — only the board row."));
        return;
      }
      renderBlocks(body, blocks);
      body.appendChild(commentBox());
    }

    /* The comment thread, idea #64: *"Lets me have the same comment
     * conversation on ideas, notes and issues like the Journal."*
     *
     * **There is no thread to render, and that is the design rather than
     * a missing half.** A comment is appended to the row's own write-up,
     * which `renderBlocks` above has just drawn -- so his comment and my
     * reply to it appear as part of the write-up, in order, in the file
     * he reads in Obsidian when the app is down. The journal's drawer
     * needs its own store because a journal entry is immutable; a board
     * row's write-up is not.
     *
     * So this is only the composer, and it goes *after* the write-up for
     * the reason `append_detail_note` puts the note at the end: the
     * write-up is his statement of the problem and the conversation
     * accumulates under it.
     */
    function commentBox() {
      var wrap = el("div", "item-comment");
      var box = el("textarea", "item-comment-box");
      box.rows = 2;
      box.placeholder = "Comment on #" + item.number + "…";
      var status = el("span", "item-comment-status", "");
      var send = el("button", "item-comment-send", "Comment");
      send.type = "button";

      function busy(on) {
        send.disabled = on;
        box.disabled = on;
      }

      send.addEventListener("click", function () {
        var text = box.value.trim().replace(/\s*\n\s*/g, " ");
        if (!text) {
          status.textContent = "Nothing to send.";
          status.className = "item-comment-status is-error";
          return;
        }
        busy(true);
        status.textContent = "sending…";
        status.className = "item-comment-status";
        fetch("/api/board/comment", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ target: board, number: item.number, text: text }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            box.value = "";
            // The write-up on screen is the one from before the comment.
            // Drop the cached copy so `fill` refetches it and he sees his
            // own sentence land, rather than being told it saved and
            // shown a body that does not contain it.
            delete boardState.details[board + ":" + item.number];
            busy(false);
            fill();
          })
          .catch(function (err) {
            status.textContent = String((err && (err.message || err)) || "failed");
            status.className = "item-comment-status is-error";
            busy(false);
          });
      });

      wrap.appendChild(box);
      var foot = el("div", "item-comment-foot");
      foot.appendChild(status);
      foot.appendChild(send);
      wrap.appendChild(foot);
      return wrap;
    }

    /* The hold gesture. A press that lasts `HOLD_MS` opens the editor;
     * anything shorter is the ordinary tap that opens the write-up.
     *
     * **The timer is cleared on every way a press can end, including the
     * ones that are not "let go".** A `setTimeout` still pending when the
     * node is gone fires into a detached closure -- and in the browser
     * tests it fires inside whatever unrelated file happens to be running
     * a second later, which is a real failure this suite has already had.
     * So `end` runs on leave, on scroll, and on cancel, not just on up.
     *
     * **Mouse and touch both, because the same page is a phone and a
     * laptop.** A touch device fires the mouse events too, after a delay,
     * and both paths land in the same idempotent `start`/`end` pair, so
     * the double delivery costs a cleared timer and nothing else.
     */
    var holdTimer = null;
    var held = false;
    function endHold() {
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
    }
    function startHold() {
      endHold();
      held = false;
      holdTimer = setTimeout(function () {
        holdTimer = null;
        held = true;
        openEditor();
      }, HOLD_MS);
    }
    function openEditor() {
      if (row.querySelector(".item-edit")) return;
      var editor = renderRowEditor(board, item, function () {
        // Cancel is a repaint too. The row's title may have changed under
        // this card while the box was open, and re-rendering is the only
        // way to be sure the card and the file agree.
        loadBoard(board);
      });
      // The head stays on screen and stops being a control. Hiding it was
      // the first version and rendering the page killed it: `.item-head`
      // sets `display: flex`, which beats the `[hidden]` user-agent rule,
      // so it stayed visible and tappable in a real browser while jsdom --
      // which loads no stylesheet -- reported it hidden and the test
      // passed. Keeping it is also the better answer: the number and the
      // chips are how he can see *which* row is in the box.
      row.classList.add("is-editing");
      head.setAttribute("aria-disabled", "true");
      row.insertBefore(editor.el, head.nextSibling);
      editor.focus();
    }
    head.addEventListener("mousedown", startHold);
    head.addEventListener("touchstart", startHold);
    ["mouseup", "mouseleave", "touchend", "touchmove", "touchcancel"].forEach(
      function (name) { head.addEventListener(name, endHold); });

    // Factored out of the click handler below so `role="button"`'s
    // keyboard activation (further down) can reach the same toggle
    // without also going through the hold/editor guards a mouse or touch
    // press needs -- a keyboard press has no hold gesture to have already
    // acted on.
    function toggle() {
      var opening = boardState.open !== item.number;
      // One open row at a time. These write-ups run to several screens
      // and a page of them all open is the scroll problem issues.md #42
      // already complained about on the journal cards.
      //
      // Closing the others is a sweep over the rendered list rather than
      // each row closing itself, because a row's handler only ever holds
      // its own nodes: the first version set `boardState.open` and left
      // the previously open body on screen, which the browser test
      // caught. The state and the DOM have to be changed together.
      var others = feed.querySelectorAll(".item-head");
      for (var i = 0; i < others.length; i++) {
        if (others[i] === head) continue;
        others[i].setAttribute("aria-expanded", "false");
        others[i].closest(".item").querySelector(".item-body").hidden = true;
      }
      boardState.open = opening ? item.number : null;
      head.setAttribute("aria-expanded", opening ? "true" : "false");
      body.hidden = !opening;
      if (opening) fill();
    }
    head.addEventListener("click", function () {
      // A hold has already done something with this press; letting the
      // tap handler also run would open the write-up underneath the
      // editor that just appeared.
      if (held) { held = false; return; }
      // While the editor is open the row is not a toggle. Without this the
      // write-up opens underneath the box he is typing in.
      if (row.querySelector(".item-edit")) return;
      toggle();
    });
    // `role="button"` on a <div> gets none of a real <button>'s built-in
    // keyboard activation -- Space and Enter do nothing without this. No
    // hold gesture to guard against, but the editor-open guard still
    // applies: the row is not a toggle while it is open, keyboard or not.
    head.addEventListener("keydown", function (e) {
      if (e.key === "Enter" || e.key === " ") {
        if (row.querySelector(".item-edit")) return;
        e.preventDefault();
        toggle();
      }
    });
    if (boardState.open === item.number) fill();
    return row;
  }

  /* One not-boarded capture, with Edvard's edit and delete on it
   * (issues.md #66). Two halves of that item live here: the rule between
   * rows, and the controls.
   *
   * **The separator is between the captures, not around the block.** His
   * words are "a clear separation of the not boarded issues" -- the block
   * already had a border, and what ran together was one bullet against
   * the next, since a capture is usually a single unpunctuated line. Two
   * one-line thoughts stacked with only a paragraph margin between them
   * read as one thought with a line break.
   *
   * **A capture is addressed by its position *and* its text, and both
   * halves are load-bearing.** The board is rewritten by cycles
   * constantly, so a position alone points at a different bullet the
   * moment anything above it is boarded; but two captures can read the
   * same, so text alone would rewrite whichever came first and report
   * success. Sending both means the server can refuse a disagreement
   * instead of resolving it. A stale address is a 409 and the page
   * re-reads, which is the honest outcome. */
  function renderCapture(board, capture, index) {
    var one = el("div", "capture-item");
    var body = el("div", "capture-body");
    /* The rating, shown and editable the same way a boarded row's is.
     * Edvard, issues.md #91: *"All unboarded issues and ideas should have
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
      ariaLabel: "Priority of capture " + (index + 1),
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
        var next = label ? label.split(" ")[0] + " " + rest : rest;
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
    renderBlocks(body, capture.blocks || []);
    one.appendChild(body);

    var actions = el("div", "capture-edit");
    var status = el("span", "capture-item-status");
    var editBtn = el("button", "capture-act", "Edit");
    var delBtn = el("button", "capture-act is-danger", "Delete");
    editBtn.type = "button";
    delBtn.type = "button";

    function fail(err) {
      status.textContent = String((err && (err.message || err)) || "failed");
      status.className = "capture-item-status is-error";
      [editBtn, delBtn].forEach(function (b) { b.disabled = false; });
    }

    function send(url, payload) {
      status.textContent = "saving…";
      status.className = "capture-item-status";
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
      // The textarea carries the raw markdown, not the rendered text --
      // an edit round-trips through the same field the vault stores, so
      // saving something untouched is a no-op rather than a reformat.
      var box = el("textarea", "capture-input");
      box.value = capture.text || "";
      box.rows = 2;
      var save = el("button", "capture-act", "Save");
      var cancel = el("button", "capture-act", "Cancel");
      save.type = "button";
      cancel.type = "button";
      one.replaceChild(box, body);
      actions.textContent = "";
      actions.appendChild(status);
      actions.appendChild(save);
      actions.appendChild(cancel);
      box.focus();
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
      // Deleting is the one thing here that cannot be undone from the
      // page, so it asks. This is not the confirmation modal of #6 -- a
      // native confirm is one line and blocks the accident, and building
      // a modal for it would be a different item's work done badly.
      if (!window.confirm("Delete this capture?\n\n" + (capture.text || ""))) return;
      send("/api/capture/delete", { target: board, index: index, original: capture.text });
    });

    actions.appendChild(status);
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);
    one.appendChild(actions);
    return one;
  }

  function renderBoardEdvard(board, payload) {
    var wrap = el("div", "board");
    var captures = payload.captures || [];
    if (captures.length) {
      var box = el("section", "captures");
      box.appendChild(el("h2", "captures-title", "Not boarded yet"));
      captures.forEach(function (capture, index) {
        box.appendChild(renderCapture(board, capture, index));
      });
      wrap.appendChild(box);
    }

    var items = payload.items || [];
    wrap.appendChild(renderBoardControls(board, payload, items));

    /* The rows live in their own container so a keystroke in the search
     * box can replace them without replacing the box -- see
     * `refreshBoardRows`. */
    boardRows = el("div", "board-rows");
    renderBoardRows(board, items);
    wrap.appendChild(boardRows);
    return wrap;
  }

  /* The rows currently on the page, or null when the board is not the
   * thing showing. */
  var boardRows = null;

  function renderBoardRows(board, items) {
    boardRows.textContent = "";
    var shown = visibleItems(items);
    if (!shown.length) {
      boardRows.appendChild(el(
        "p", "empty",
        boardState.query.trim() ? "Nothing matches “" + boardState.query.trim() + "”."
          : "Nothing here."
      ));
    }
    shown.forEach(function (item) { boardRows.appendChild(renderBoardItem(board, item)); });
  }

  /* Redraw only what a search changed. Edvard, issues.md, 2026-08-15:
   * "When i use the search bar in Nova, my keyboard is closed on every
   * letter input so i have to open the keyboard each letter."
   *
   * `renderBoard` starts with `feed.textContent = ""`, so every keystroke
   * used to destroy the very input being typed into and build a fresh
   * one. Removing the focused element from the document dismisses the
   * soft keyboard, and the `setTimeout(input.focus)` that used to sit at
   * the end of `renderBoardControls` cannot bring it back: a phone opens
   * the keyboard for a focus that happens inside a user gesture, not for
   * one that arrives a task later. On a desktop browser the caret was
   * restored and the bug was invisible, which is why it shipped.
   *
   * A search changes which rows show and nothing else -- the chip counts
   * are computed against the status filter, not the query, and the sort
   * control does not read it -- so the rows are the only thing that has
   * to be rebuilt. Nothing here touches the input, so there is no focus
   * to restore.
   *
   * What this does *not* promise: the rows themselves are still rebuilt
   * from scratch, so an open row-title editor loses whatever was typed
   * into it, exactly as it did when the whole board re-rendered. That is
   * unchanged rather than fixed, and it is written down here because
   * "only the rows are redrawn" is otherwise easy to read as "nothing a
   * reader is holding is disturbed". */
  function refreshBoardRows(board, payload) {
    if (!boardRows || !boardRows.isConnected) {
      renderBoard(board, payload);
      return;
    }
    renderBoardRows(board, payload.items || []);
  }

  /* The search box, the filter chips and the sort control, in that order
   * -- Edvard's two asks (ideas.md #70 and #71) are one strip on the
   * page because they are one question: which rows do I want, and in
   * what order. Rebuilt on every board render like everything else here
   * -- but deliberately *not* on a keystroke, which redraws the rows
   * underneath it and leaves this strip standing. See
   * `refreshBoardRows`. */

  /* The status filters (Open/Done/All) and the extra toggles, as one
   * group of buttons -- unchanged from when they lived directly on the
   * page, down to the class names, so the only thing that moved is where
   * this container ends up mounted. Rebuilt fresh on every call rather
   * than cached, because a count or an "on" state can go stale the
   * instant any of these buttons, or the status/board tabs above them,
   * is tapped. */
  function buildFilterChips(board, payload, items) {
    var chips = el("div", "filters");
    FILTERS.forEach(function (filter) {
      var count = items.filter(filter.match).length;
      var chip = el("button", "filter" + (filter.key === boardState.filter ? " on" : ""),
        filter.label + " (" + count + ")");
      chip.type = "button";
      chip.setAttribute("aria-pressed", filter.key === boardState.filter ? "true" : "false");
      chip.addEventListener("click", function () {
        boardState.filter = filter.key;
        renderBoard(board, payload);
      });
      chips.appendChild(chip);
    });
    TOGGLES.forEach(function (toggle) {
      var on = !!boardState.toggles[toggle.key];
      // Counted against the status filter rather than the whole board,
      // so "Unrated (0)" under Done means what it says instead of
      // advertising rows the current view cannot show.
      var count = items.filter(currentFilter().match).filter(toggle.match).length;
      var chip = el("button", "filter filter-extra" + (on ? " on" : ""),
        toggle.label + " (" + count + ")");
      chip.type = "button";
      chip.setAttribute("aria-pressed", on ? "true" : "false");
      chip.addEventListener("click", function () {
        boardState.toggles[toggle.key] = !on;
        renderBoard(board, payload);
      });
      chips.appendChild(chip);
    });
    return chips;
  }

  /* The filter modal -- Edvard, 2026-08-14: "make the filters into a
   * modal same as the priority. remove all the filter buttons and place
   * a new filter button... next to the arrow button on the search. the
   * filter button opens a modal with the filter options."
   *
   * "Same as the priority" means the same shared popup and backdrop
   * (`getPrioMenuOverlay`) and the same centred, wider `.prio-menu`
   * chrome -- not the same *behaviour*. The priority popup is single-pick
   * and closes itself the instant an option is chosen; these seven
   * buttons compose (ideas.md #71 -- "unrated and untouched for a week"
   * is one tap each) and closing after the first tap would undo the one
   * thing that made them worth inventing. So this popup only closes on an
   * outside tap, Escape, or its own close button, and stays open and
   * live across every tap inside it -- each one already calls
   * `renderBoard`, and `boardState.filtersOpen` is what tells the next
   * `renderBoardControls` to refresh this popup's contents in place
   * rather than leave it showing counts and "on" states from before the
   * tap that just happened. */
  function populateFiltersModal(board, payload, items) {
    var overlay = getPrioMenuOverlay();
    overlay.textContent = "";
    overlay.removeAttribute("role");
    overlay.dataset.openFor = "filters";
    var head = el("div", "modal-head");
    head.appendChild(el("h2", "modal-title", "Filters"));
    var closeBtn = el("button", "modal-close", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close filters");
    closeBtn.addEventListener("click", closeFiltersModal);
    head.appendChild(closeBtn);
    overlay.appendChild(head);
    overlay.appendChild(buildFilterChips(board, payload, items));
  }

  var filtersModalHandlers = null;
  function closeFiltersModal() {
    if (!boardState.filtersOpen) return;
    boardState.filtersOpen = false;
    if (prioMenuOverlay) prioMenuOverlay.hidden = true;
    if (prioMenuBackdrop) prioMenuBackdrop.hidden = true;
    if (filtersModalHandlers) {
      document.removeEventListener("click", filtersModalHandlers.onDocClick, true);
      document.removeEventListener("keydown", filtersModalHandlers.onKeydown, true);
      filtersModalHandlers = null;
    }
  }

  function openFiltersModal(board, payload, items) {
    boardState.filtersOpen = true;
    populateFiltersModal(board, payload, items);
    prioMenuBackdrop.hidden = false;
    prioMenuOverlay.hidden = false;
    function onDocClick(e) {
      if (prioMenuOverlay.contains(e.target)) return;
      closeFiltersModal();
    }
    function onKeydown(e) {
      if (e.key === "Escape") closeFiltersModal();
    }
    filtersModalHandlers = { onDocClick: onDocClick, onKeydown: onKeydown };
    document.addEventListener("click", onDocClick, true);
    document.addEventListener("keydown", onKeydown, true);
  }

  function renderBoardControls(board, payload, items) {
    var bar = el("div", "board-controls");

    var search = el("div", "board-search");
    var input = document.createElement("input");
    input.type = "search";
    input.className = "board-search-input";
    input.placeholder = "Search titles and write-ups";
    input.setAttribute("aria-label", "Search this board");
    input.value = boardState.query;
    search.appendChild(input);
    /* Built whether or not there is anything to clear, and hidden rather
     * than absent. Adding it beside the input on the first keystroke
     * would not detach the input, but removing it on the last one moves
     * the caret's own neighbour under it mid-edit, and `hidden` says the
     * same thing to a screen reader for none of that. */
    var clear = el("button", "board-search-clear", "×");
    clear.type = "button";
    clear.hidden = !boardState.query;
    clear.setAttribute("aria-label", "Clear the search");
    clear.addEventListener("click", function () {
      boardState.query = "";
      boardState.matches = null;
      boardState.matchedQuery = null;
      input.value = "";
      clear.hidden = true;
      refreshBoardRows(board, payload);
      // Synchronous, inside the tap, so the keyboard stays up and he can
      // type the next query without reaching for the box again.
      input.focus();
    });
    search.appendChild(clear);
    input.addEventListener("input", function () {
      boardState.query = input.value;
      clear.hidden = !input.value;
      runBoardSearch(board, payload);
      refreshBoardRows(board, payload);
    });
    bar.appendChild(search);

    /* "on each option, on a horisontal line, a description of the option
     * ('priority') and on the right side of it a button with a
     * upwards/downwards facing arrow to click and have it turn (with
     * clockwise animation) which flips the order of the sorting."
     *
     * A native `<select>` cannot hold a button inside an option, so the
     * row is the sort field on the left and one arrow on the right --
     * which is the same control he described, minus a per-option arrow
     * that would have meant five directions for one list. Tapping the
     * arrow flips the order and it rotates to show it. */
    var sortRow = el("div", "board-sort");
    sortRow.appendChild(el("span", "board-sort-label", "Sort"));
    var select = document.createElement("select");
    select.className = "board-sort-select";
    select.setAttribute("aria-label", "Sort this board by");
    SORTS.forEach(function (sort) {
      var option = document.createElement("option");
      option.value = sort.key;
      option.textContent = sort.label;
      if (sort.key === boardState.sort) option.selected = true;
      select.appendChild(option);
    });
    select.addEventListener("change", function () {
      boardState.sort = select.value;
      renderBoard(board, payload);
    });
    sortRow.appendChild(select);
    var arrow = el("button", "board-sort-dir" + (boardState.desc ? " desc" : ""), "↑");
    arrow.type = "button";
    arrow.setAttribute("aria-pressed", boardState.desc ? "true" : "false");
    arrow.setAttribute(
      "aria-label",
      boardState.desc ? "Sorted descending — tap for ascending"
        : "Sorted ascending — tap for descending"
    );
    arrow.addEventListener("click", function () {
      boardState.desc = !boardState.desc;
      renderBoard(board, payload);
    });
    sortRow.appendChild(arrow);

    // Next to the arrow, as asked. Three bars of shrinking width -- the
    // same ribbon shape the term "filter" usually draws as -- rather than
    // a word, so it reads at a glance next to a row that is otherwise all
    // icons and a select. `on` (an accent border, the same signal every
    // other active control on this page already gives) fires only for the
    // toggles: Open/Done/All always has exactly one of the three selected,
    // so highlighting it here would light up on every load and mean
    // nothing.
    var anyToggleOn = TOGGLES.some(function (t) { return !!boardState.toggles[t.key]; });
    var filterBtn = el("button", "board-filter-btn" + (anyToggleOn ? " on" : ""));
    filterBtn.type = "button";
    filterBtn.setAttribute("aria-haspopup", "dialog");
    filterBtn.setAttribute("aria-expanded", boardState.filtersOpen ? "true" : "false");
    filterBtn.setAttribute("aria-label", "Filters");
    filterBtn.appendChild(el("span", "filter-bar"));
    filterBtn.appendChild(el("span", "filter-bar"));
    filterBtn.appendChild(el("span", "filter-bar"));
    filterBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      if (boardState.filtersOpen) closeFiltersModal(); else openFiltersModal(board, payload, items);
    });
    sortRow.appendChild(filterBtn);
    bar.appendChild(sortRow);

    // The modal is a standing popup, not rebuilt by this function's own
    // return value -- keep it in step with whatever just changed (a tap
    // inside it always re-runs this whole function) rather than let it
    // show the counts and "on" states from before that tap.
    if (boardState.filtersOpen) populateFiltersModal(board, payload, items);

    return bar;
  }

  /* The write-up half of the search. Titles are matched in the page
   * because the page has them; the detail bodies are 60KB and never come
   * down with the list, so the server is asked instead. Debounced, and
   * the answer is stamped with the query it answered -- a slow reply for
   * "bad" must not be shown as the result for "badge". */
  var searchTimer = null;

  function runBoardSearch(board, payload) {
    var query = boardState.query.trim().toLowerCase();
    if (searchTimer) clearTimeout(searchTimer);
    if (!query) {
      boardState.matches = null;
      boardState.matchedQuery = null;
      return;
    }
    searchTimer = setTimeout(function () {
      fetch("/api/board?name=" + board + "&q=" + encodeURIComponent(query))
        .then(json)
        .then(function (result) {
          // The same guard every other loader in this file carries, and
          // for the same reason: a debounce plus a round trip is long
          // enough to tap the nav, and without this the answer repaints
          // the old board over whatever page is showing now, while
          // `markNav` -- which reads the URL -- highlights the new one.
          if (route(window.location.pathname).board !== board) return;
          if (!result || result.query !== query) return;
          boardState.matches = result.matches || [];
          boardState.matchedQuery = query;
          // Rows only, for the same reason the keystroke redraws rows
          // only: this lands 200ms after he stopped typing, into a box he
          // is still holding the keyboard open over.
          refreshBoardRows(board, payload);
        })
        .catch(function () {
          // A failed search leaves the title matches standing rather
          // than emptying the board: fewer rows than there should be is
          // recoverable, a page that says "nothing matches" is not.
        });
    }, 200);
  }

  function renderBoardNova(board, payload) {
    var wrap = el("div", "board");
    var notes = payload.notes || [];
    if (!notes.length) wrap.appendChild(el("p", "empty", "No notes yet."));
    notes.forEach(function (note) {
      var card = el("article", "note");
      var head = el("div", "note-head");
      if (note.date) head.appendChild(el("span", "note-date", note.date));
      if (note.cycle !== null && note.cycle !== undefined) {
        var link = el("a", "note-cycle", "Cycle " + note.cycle);
        link.href = "/cycle/" + note.cycle;
        head.appendChild(link);
      }
      card.appendChild(head);
      var body = el("div", "note-body");
      renderBlocks(body, note.blocks || []);
      card.appendChild(body);
      wrap.appendChild(card);
    });
    var total = payload.notesTotal;
    if (typeof total === "number" && notes.length < total) {
      /* Not "older": my two capture files switched from prepending to
       * appending partway through, so the tail of the file is the newest
       * material rather than the oldest. Measured 2026-08-11 -- the first
       * ~120 notes descend from Cycle 63 to 27, the rest ascend to 102.
       * The list says what the file says and the button does not claim an
       * order the data does not have. Filed to normalise the files. */
      var more = el("button", "more", "Show more notes");
      more.type = "button";
      more.addEventListener("click", function () {
        more.disabled = true;
        more.textContent = "Loading…";
        boardState.notes += BOARD_NOTES;
        load();
      });
      wrap.appendChild(more);
      loadWhenScrolledTo(more);
    }
    return wrap;
  }

  function renderBoard(board, payload) {
    stopPolling();
    markNav();
    needsEl.hidden = true;
    renderBoardStatus(board, payload);
    feed.textContent = "";

    var titles = boardTitles(board);
    var tabs = el("div", "tabs");
    [
      { key: "edvard", label: "Edvard's " + titles.page.toLowerCase() },
      { key: "nova", label: titles.mine },
    ].forEach(function (tab) {
      var button = el("button", "tab" + (boardState.tab === tab.key ? " on" : ""), tab.label);
      button.type = "button";
      button.setAttribute("aria-pressed", boardState.tab === tab.key ? "true" : "false");
      button.addEventListener("click", function () {
        if (boardState.tab === tab.key) return;
        boardState.tab = tab.key;
        renderBoard(board, payload);
      });
      tabs.appendChild(button);
    });
    feed.appendChild(tabs);
    feed.appendChild(
      boardState.tab === "nova"
        ? renderBoardNova(board, payload)
        : renderBoardEdvard(board, payload)
    );
  }

  /* `/ideas#68` -> that row open, and scrolled to. A journal card's board
   * badge links here (ideas.md #68): "Journal cards in Nova should mark
   * the issue or idea number they worked on ... With links." The point of
   * the link is the write-up, not the page it sits on.
   *
   * Consumed once per navigation rather than on every render, so tapping
   * a filter chip afterwards does not drag the page back to the row the
   * URL named. `boardHashPending` carries it from here to after the DOM
   * exists, because the row cannot be scrolled to before it is built.
   *
   * The filter is the part that would otherwise fail silently. The board
   * opens on `Open`, and an item a journal entry worked on is often
   * already ✅ Done, so the row the URL names is not on screen at all --
   * the link would land on the right page showing everything except the
   * thing it was pointing at. A URL is more specific than a default, so
   * the default gives way. */
  var boardHashPending = null;

  function applyBoardHash(payload) {
    var wanted = /^#(\d+)$/.exec(window.location.hash || "");
    if (!wanted) return;
    var number = parseInt(wanted[1], 10);
    var target = ((payload && payload.items) || []).filter(function (item) {
      return item.number === number;
    })[0];
    // No such item: leave the page exactly as it would have rendered. A
    // stale or mistyped number is not a reason to open something else.
    if (!target) return;
    boardState.tab = "edvard";
    boardState.open = number;
    if (!currentFilter().match(target)) boardState.filter = "all";
    boardHashPending = number;
  }

  function scrollToBoardHash() {
    if (boardHashPending === null) return;
    var row = document.getElementById("item-" + boardHashPending);
    boardHashPending = null;
    if (row && row.scrollIntoView) row.scrollIntoView();
  }

  function loadBoard(board) {
    // Which row is open and how far back the notes go belong to the board
    // being looked at, not to the session: carried across, tapping from
    // Issues to Ideas would open whichever idea happens to share a number
    // with the issue that was open. `details` is keyed by board and is a
    // real cache, so it stays.
    if (boardState.board !== board) {
      boardState.board = board;
      boardState.open = null;
      boardState.notes = BOARD_NOTES;
      // The search belongs to the board too, and `matches` is the half
      // that is actively wrong if it is carried over: it is a list of
      // row *numbers*, answered by the server for the other file, and
      // `visibleItems` would apply #58-from-Issues to whatever #58 is on
      // Ideas. The chips and the sort field are reset alongside it
      // because a box that still says "gemini" over a board that was
      // never searched is the same lie in a quieter form. The sort
      // deliberately goes back to the file's own order, so switching
      // boards always lands on the view the board had before #70.
      boardState.query = "";
      boardState.matches = null;
      boardState.matchedQuery = null;
      boardState.toggles = {};
      boardState.sort = "filed";
      boardState.desc = false;
    }
    fetchPage("/api/board?name=" + board + "&limit=" + boardState.notes)
      .then(function (payload) {
        // Two taps in quick succession leave two fetches in flight, and
        // before there were three views to land on, whichever resolved
        // last simply won. Now it can paint Issues over Ideas while the
        // nav highlights Ideas, because `markNav` reads the URL and this
        // did not.
        if (route(window.location.pathname).board !== board) return;
        applyBoardHash(payload);
        renderBoard(board, payload);
        scrollToBoardHash();
      })
      .catch(function (err) {
        markNav();
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the board: " + err));
      });
  }

  /* ---- The costs page (issues.md #57, page 2) --------------------------
   *
   * Edvard, 2026-08-08: "I want you to figure out the optimal method of
   * quota spendage for projects. I do not know the optimal way. Figure
   * this out by trial and error and gained experience." Every cycle has
   * been writing its own cost into a ledger since; this is the first time
   * either of us can see the shape of it rather than one row at a time.
   *
   * Two charts and no third, because there are exactly two questions:
   * what one cycle costs, and how close the week is to running out. They
   * are different units, so they are two charts sharing one time axis
   * rather than one chart with two y-scales.
   *
   * Every mark is built with createElementNS for the same reason nothing
   * in this file uses innerHTML: an SVG string assembled out of numbers is
   * still markup this client would be producing.
   */
  var SVG_NS = "http://www.w3.org/2000/svg";

  /* The two series colours. Validated against this app's own dark surface
   * (#12131a) rather than chosen: lightness band, chroma floor, contrast,
   * and colour-vision separation for the pair -- worst adjacent deltaE
   * 25.2 under protanopia, 21.4 under tritanopia, 26.3 for normal vision.
   * The app's --accent (#7aa2f7) and --warn (#e8b75c) both fail the
   * lightness band against this surface, which is why these are their own
   * two values and not the theme's. */
  var SERIES_A = "#5d86dd";
  var SERIES_B = "#bd8b2f";
  var GRID = "#2a2d3a";
  var AXIS_INK = "#7d8296";

  function svgEl(tag, attrs) {
    var node = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  function fmtTokens(n) {
    if (!isFinite(n)) return "—";
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (Math.abs(n) >= 1e3) return Math.round(n / 1e3) + "k";
    return String(Math.round(n));
  }

  function fmtMinutes(seconds) {
    if (!isFinite(seconds)) return "—";
    return (seconds / 60).toFixed(1) + " min";
  }

  /* The reader's own clock, deliberately. The ledger stores UTC and the
   * payload carries epoch milliseconds precisely so that the one place
   * that knows what timezone the reader is in gets to decide -- Nova
   * writes Oslo time everywhere for the same reason, and here the browser
   * already knows. */
  function fmtDay(ms) {
    return new Date(ms).toLocaleDateString(undefined, { day: "numeric", month: "short" });
  }

  function fmtStamp(ms) {
    return new Date(ms).toLocaleString(undefined, {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
    });
  }

  /* One chart's frame: the box, its grid, and its two axes.
   *
   * `viewBox` is 360 wide because that is roughly a phone, and the SVG
   * scales up from there -- so a font-size in user units is about the
   * pixel size it will have on the device this is actually read on, and
   * larger on a desktop. Sizing the other way round (a wide viewBox
   * scaled down) is what makes hand-written SVG unreadable on a phone.
   */
  var CHART = { w: 360, h: 168, left: 30, right: 6, top: 10, bottom: 16 };

  function chartFrame(title, subtitle) {
    var figure = el("figure", "chart");
    figure.appendChild(el("figcaption", "chart-title", title));
    if (subtitle) figure.appendChild(el("p", "chart-sub", subtitle));
    var plot = el("div", "chart-plot");
    var svg = svgEl("svg", {
      viewBox: "0 0 " + CHART.w + " " + CHART.h,
      class: "chart-svg",
      role: "img",
      "aria-label": title + (subtitle ? ". " + subtitle : ""),
    });
    plot.appendChild(svg);
    figure.appendChild(plot);
    return { figure: figure, plot: plot, svg: svg };
  }

  function plotBox() {
    return {
      x0: CHART.left,
      x1: CHART.w - CHART.right,
      y0: CHART.top,
      y1: CHART.h - CHART.bottom,
    };
  }

  /* Horizontal gridlines and their value labels. Recessive on purpose:
   * the grid is a reading aid, not data, so it never competes with a
   * mark. */
  function drawYAxis(svg, box, ticks, label) {
    ticks.forEach(function (tick) {
      svg.appendChild(svgEl("line", {
        x1: box.x0, x2: box.x1, y1: tick.y, y2: tick.y,
        stroke: GRID, "stroke-width": 1,
      }));
      var text = svgEl("text", {
        x: box.x0 - 4, y: tick.y + 3, "text-anchor": "end",
        class: "chart-axis-label",
      });
      text.textContent = label(tick.value);
      svg.appendChild(text);
    });
  }

  function drawXDates(svg, box, from, to) {
    [
      { at: from, x: box.x0, anchor: "start" },
      { at: to, x: box.x1, anchor: "end" },
    ].forEach(function (mark) {
      var text = svgEl("text", {
        x: mark.x, y: CHART.h - 4, "text-anchor": mark.anchor,
        class: "chart-axis-label",
      });
      text.textContent = fmtDay(mark.at);
      svg.appendChild(text);
    });
  }

  /* The hover layer, shared by both charts.
   *
   * `points` is `[{x, at, lines}]` already in user units; the overlay
   * finds the nearest one by x and moves a crosshair to it. One
   * implementation for a bar chart and a line chart because "which moment
   * is under my finger" is the same question in both, and a phone has no
   * pointer to hover with -- pointerdown counts, which is why this listens
   * for that as well as for pointermove.
   */
  function attachHover(chart, box, points, when) {
    if (!points.length) return;
    var rule = svgEl("line", {
      y1: box.y0, y2: box.y1, stroke: AXIS_INK, "stroke-width": 1,
      "stroke-dasharray": "2 2", class: "chart-rule", x1: box.x0, x2: box.x0,
    });
    rule.style.opacity = "0";
    chart.svg.appendChild(rule);

    var tip = el("div", "chart-tip");
    tip.hidden = true;
    chart.plot.appendChild(tip);

    var overlay = svgEl("rect", {
      x: box.x0, y: box.y0, width: box.x1 - box.x0, height: box.y1 - box.y0,
      fill: "transparent", class: "chart-overlay",
    });

    function show(event) {
      var bounds = chart.svg.getBoundingClientRect();
      if (!bounds.width) return;
      var user = ((event.clientX - bounds.left) / bounds.width) * CHART.w;
      var best = points[0];
      points.forEach(function (point) {
        if (Math.abs(point.x - user) < Math.abs(best.x - user)) best = point;
      });
      rule.setAttribute("x1", best.x);
      rule.setAttribute("x2", best.x);
      rule.style.opacity = "1";
      tip.textContent = "";
      // `when` overrides the stamp for a series whose x is a date rather
      // than a moment: the retro ledger stores days, so the default would
      // print "14 Aug, 02:00" -- a real-looking time that corresponds to
      // nothing, invented by the midnight-UTC conversion.
      tip.appendChild(el("p", "chart-tip-when", (when || fmtStamp)(best.at)));
      best.lines.forEach(function (line) {
        var row = el("p", "chart-tip-row");
        row.appendChild(el("span", "chart-tip-swatch"));
        row.lastChild.style.background = line.color;
        row.appendChild(el("span", "chart-tip-label", line.label));
        row.appendChild(el("span", "chart-tip-value", line.value));
        tip.appendChild(row);
      });
      tip.hidden = false;
      // Kept inside the plot: the tip is ~120px wide against a 360-unit
      // box, so pinning it to the side the pointer is *not* on is what
      // stops it covering the mark it describes.
      var left = best.x / CHART.w > 0.5;
      tip.style.left = left ? "4px" : "auto";
      tip.style.right = left ? "auto" : "4px";
    }

    function hide() {
      rule.style.opacity = "0";
      tip.hidden = true;
    }

    overlay.addEventListener("pointermove", show);
    overlay.addEventListener("pointerdown", show);
    overlay.addEventListener("pointerleave", hide);
    chart.svg.appendChild(overlay);
  }

  /* What one cycle costs, as a bar per cycle placed at the moment it ran.
   *
   * Placed by time rather than evenly spaced, which is the whole reason
   * this is worth looking at: the loop has been idle for days at a stretch
   * and run fourteen cycles in one, and a bar per cycle in a neat row
   * would draw both stretches identically. The gaps are the finding.
   */
  function renderCycleChart(payload, domain) {
    var rows = payload.cycles || [];
    var chart = chartFrame(
      "What a cycle costs",
      "Weighted tokens per cycle, placed when it ran"
    );
    if (!rows.length) {
      chart.figure.appendChild(el("p", "empty", "No cycles in the ledger yet."));
      return chart.figure;
    }
    var box = plotBox();
    var from = domain.from;
    var to = domain.to;
    var span = Math.max(to - from, 1);
    var max = rows.reduce(function (best, row) { return Math.max(best, row[4]); }, 0) || 1;

    var x = function (at) { return box.x0 + ((at - from) / span) * (box.x1 - box.x0); };
    var y = function (value) { return box.y1 - (value / max) * (box.y1 - box.y0); };

    drawYAxis(chart.svg, box, [
      { value: max, y: y(max) },
      { value: max / 2, y: y(max / 2) },
      { value: 0, y: box.y1 },
    ], fmtTokens);

    // Wide enough to see, never wide enough to overlap its neighbour --
    // which means the *narrowest* gap between two cycles, not the median
    // one. Sized on the median, 31 of the real ledger's 109 gaps are
    // tighter than the bar, so the busiest stretches render as a solid
    // smear and the gaps this chart exists to show stop being visible.
    var gaps = [];
    for (var i = 1; i < rows.length; i++) gaps.push(x(rows[i][0]) - x(rows[i - 1][0]));
    var width = gaps.length ? Math.min.apply(null, gaps) : 4;
    width = Math.max(1, Math.min(width - 0.4, 8));

    var points = [];
    rows.forEach(function (row) {
      var height = Math.max(box.y1 - y(row[4]), 0.6);
      chart.svg.appendChild(svgEl("rect", {
        x: x(row[0]) - width / 2, y: box.y1 - height,
        width: width, height: height, rx: Math.min(width / 2, 2),
        fill: SERIES_A,
      }));
      points.push({
        x: x(row[0]), at: row[0],
        lines: [
          { color: SERIES_A, label: "Weighted", value: fmtTokens(row[4]) },
          { color: "transparent", label: "Ran for", value: row[1] + " min" },
          { color: "transparent", label: "Turns", value: String(row[2]) },
        ],
      });
    });
    drawXDates(chart.svg, box, from, to);
    attachHover(chart, box, points);
    return chart.figure;
  }

  /* How much of each quota window has been spent, over time.
   *
   * Two series on one axis because both are a percentage of their own
   * window -- the comparison is the point, and it is the one comparison
   * this data supports without a second scale. The five-hour line sawtooths
   * because it resets five times a day; the seven-day line is the one that
   * decides whether the week runs out early.
   */
  function renderQuotaChart(payload, domain) {
    var rows = (payload.quota || []).filter(function (row) {
      return row[1] !== null || row[3] !== null;
    });
    var chart = chartFrame(
      "How much quota is left",
      "Percent of each window used, at every reading"
    );
    if (!rows.length) {
      chart.figure.appendChild(el("p", "empty", "No quota readings yet."));
      return chart.figure;
    }
    var box = plotBox();
    var from = domain.from;
    var to = domain.to;
    var span = Math.max(to - from, 1);
    var x = function (at) { return box.x0 + ((at - from) / span) * (box.x1 - box.x0); };
    var y = function (pct) { return box.y1 - (pct / 100) * (box.y1 - box.y0); };

    drawYAxis(chart.svg, box, [
      { value: 100, y: y(100) },
      { value: 50, y: y(50) },
      { value: 0, y: box.y1 },
    ], function (v) { return v + "%"; });

    [
      { index: 1, color: SERIES_A, label: "5-hour" },
      { index: 3, color: SERIES_B, label: "7-day" },
    ].forEach(function (series) {
      var d = "";
      var open = false;
      rows.forEach(function (row) {
        var value = row[series.index];
        if (value === null || value === undefined) {
          // A reading that predates this field is a hole, not a zero. The
          // path stops and starts again rather than drawing a line down to
          // the axis and back, which would read as the quota emptying.
          open = false;
          return;
        }
        d += (open ? "L" : "M") + x(row[0]).toFixed(1) + " " + y(value).toFixed(1) + " ";
        open = true;
      });
      chart.svg.appendChild(svgEl("path", {
        d: d.trim(), fill: "none", stroke: series.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
    });

    var points = rows.map(function (row) {
      return {
        x: x(row[0]), at: row[0],
        lines: [
          { color: SERIES_A, label: "5-hour", value: row[1] === null ? "—" : row[1] + "%" },
          { color: SERIES_B, label: "7-day", value: row[3] === null ? "—" : row[3] + "%" },
        ],
      };
    });
    drawXDates(chart.svg, box, from, to);
    attachHover(chart, box, points);

    // Two series, so a legend is not optional -- identity must not rest on
    // colour alone.
    var legend = el("div", "chart-legend");
    [
      { color: SERIES_A, label: "5-hour window" },
      { color: SERIES_B, label: "7-day window" },
    ].forEach(function (series) {
      var key = el("span", "legend-key");
      var swatch = el("span", "legend-swatch");
      swatch.style.background = series.color;
      key.appendChild(swatch);
      key.appendChild(el("span", "legend-label", series.label));
      legend.appendChild(key);
    });
    chart.figure.appendChild(legend);
    return chart.figure;
  }

  function statTile(label, value, note) {
    var tile = el("div", "tile");
    tile.appendChild(el("p", "tile-label", label));
    tile.appendChild(el("p", "tile-value", value));
    if (note) tile.appendChild(el("p", "tile-note", note));
    return tile;
  }

  function renderCostTiles(payload) {
    var summary = payload.summary || {};
    var quota = payload.quota || [];
    var latest = quota.length ? quota[quota.length - 1] : null;
    var row = el("div", "tiles");
    row.appendChild(statTile("Cycles", String(summary.cycles || (payload.cycles || []).length)));
    row.appendChild(statTile(
      "Median cycle", fmtTokens(summary.median_weighted), "weighted tokens"
    ));
    row.appendChild(statTile("Median length", fmtMinutes(summary.median_duration_seconds)));
    if (latest) {
      row.appendChild(statTile(
        "7-day used",
        (latest[3] === null ? "—" : latest[3] + "%"),
        latest[4] === null || latest[4] === undefined ? null : "pace " + latest[4]
      ));
    }
    return row;
  }

  /* Where the tokens actually go. Five shares of one total, which is a
   * table and not a chart: five slices would need five validated hues to
   * say what five rows say in one line each, and the ranking is the
   * finding (cache reads dominate, and they are the cheapest per token). */
  function renderCostShare(payload) {
    var share = (payload.summary || {}).cost_share;
    if (!share) return null;
    var names = {
      input_tokens: "Input",
      output_tokens: "Output",
      cache_read_tokens: "Cache read",
      cache_write_5m_tokens: "Cache write (5m)",
      cache_write_1h_tokens: "Cache write (1h)",
    };
    var wrap = el("section", "share");
    wrap.appendChild(el("h2", "share-title", "Where the cost goes"));
    Object.keys(share)
      .filter(function (key) { return share[key] > 0; })
      .sort(function (a, b) { return share[b] - share[a]; })
      .forEach(function (key) {
        var row = el("div", "share-row");
        row.appendChild(el("span", "share-label", names[key] || key));
        var track = el("span", "share-track");
        var fill = el("span", "share-fill");
        fill.style.width = Math.max(share[key], 0.5) + "%";
        fill.style.background = SERIES_A;
        track.appendChild(fill);
        row.appendChild(track);
        row.appendChild(el("span", "share-value", share[key].toFixed(1) + "%"));
        wrap.appendChild(row);
      });
    var weights = payload.weights || {};
    if (weights.output_tokens) {
      wrap.appendChild(el(
        "p", "share-note",
        "Weighted, not raw: output counts " + weights.output_tokens +
        "x an input token and a cache read " + weights.cache_read_tokens + "x."
      ));
    }
    return wrap;
  }

  /* The first and last moment either series knows about.
   *
   * Computed once and handed to both charts, because the comment above
   * says they share a time axis and until the reviewer checked, they did
   * not: each worked out its own domain from its own rows, and the two
   * series do not cover the same days -- the cycle ledger reaches back to
   * 08-03 and the quota history only to 08-08, so the same date sat at a
   * different x in the two stacked charts and any correlation a reader
   * drew between them was false. Sharing the domain also makes the
   * quota chart's empty left third say something true: nothing was
   * recorded there.
   */
  function timeDomain(payload) {
    var ends = [];
    [payload.cycles || [], payload.quota || []].forEach(function (rows) {
      if (rows.length) ends.push(rows[0][0], rows[rows.length - 1][0]);
    });
    if (!ends.length) return { from: 0, to: 1 };
    return { from: Math.min.apply(null, ends), to: Math.max.apply(null, ends) };
  }

  function renderCosts(payload) {
    stopPolling();
    markNav();
    needsEl.hidden = true;
    var summary = payload.summary || {};
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    statusEl.appendChild(el(
      "p", "status-line",
      "Costs — " + (summary.cycles || 0) + " cycles, "
        + fmtTokens(summary.total_weighted) + " weighted tokens all told"
    ));
    if (payload.replayed) statusEl.appendChild(savedCopyLine());
    feed.textContent = "";
    var domain = timeDomain(payload);
    feed.appendChild(renderCostTiles(payload));
    feed.appendChild(renderCycleChart(payload, domain));
    feed.appendChild(renderQuotaChart(payload, domain));
    var share = renderCostShare(payload);
    if (share) feed.appendChild(share);
    if (payload.generatedAt) {
      feed.appendChild(el(
        "p", "chart-sub", "Ledger published " + fmtStamp(payload.generatedAt)
      ));
    }
  }

  function loadCosts() {
    fetchPage("/api/costs")
      .then(function (payload) {
        // The same guard the board fetch carries: two taps in quick
        // succession leave two fetches in flight and the loser must not
        // paint over the winner.
        if (route(window.location.pathname).view !== "costs") return;
        renderCosts(payload);
      })
      .catch(function (err) {
        markNav();
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the costs: " + err));
      });
  }

  /* ---- The retrospective page (issues.md, 2026-08-13) ------------------
   *
   * Edvard: "Rate yourself on a scale from 1 to 10 on how you feel its
   * going, how effective do you think you are, whats good, whats bad,
   * whats the overall feeling (which is the most important metric).
   * Actually note down data and compare it to previous retros (lets also
   * make a page that shows these data as graphs)."
   *
   * The comparison is the ask, so the chart is one chart with all three
   * lines on one 1-10 axis, not three charts side by side: the question
   * is whether they move together, and that is only readable when they
   * share an axis.
   */

  /* The third series colour, and it took a measurement to find.
   *
   * SERIES_A (blue) and SERIES_B (amber) already straddle the axis that
   * red-green deficiency collapses, so most third hues land on top of one
   * of them for somebody. Measured as CIEDE2000 between this and each of
   * the existing two, under normal vision and under simulated protanopia,
   * deuteranopia and tritanopia: teal #38a3a5 falls to 4.7, pink #c2739f
   * to 3.0, violet #b07de0 to 2.4 -- all indistinguishable from a
   * neighbour to a real reader. This one's worst adjacent delta is 16.8
   * (deuteranopia, against the amber) and its contrast against the app's
   * surface is 10.8:1. It is lighter than the pair (L* 79.7 against 56.7
   * and 61.2), which is what buys the separation and is a second channel
   * rather than a compromise. The legend below carries identity anyway --
   * three lines is past where colour alone should be asked to. */
  var SERIES_C = "#8fd694";

  /* Ordered to match nova_retro.SCORE_KEYS, and the overall feeling is
   * last because it is drawn last: three integer scores on a 1-10 axis
   * overlap exactly whenever two of them are equal, and the line he
   * called the most important metric should be the one on top. It is also
   * the thickest, for the same reason. */
  var RETRO_SERIES = {
    going: { color: SERIES_A, width: 2 },
    effectiveness: { color: SERIES_B, width: 2 },
    feeling: { color: SERIES_C, width: 3 },
  };

  function retroSeries(payload) {
    return (payload.scoreKeys || []).map(function (entry) {
      var style = RETRO_SERIES[entry.key] || { color: SERIES_A, width: 2 };
      return { key: entry.key, label: entry.label, color: style.color, width: style.width };
    });
  }

  function renderRetroChart(payload) {
    var rows = payload.retros || [];
    var series = retroSeries(payload);
    var chart = chartFrame(
      "How it has been going",
      "Each Friday's self-rating, 1 to 10"
    );
    if (!rows.length) {
      chart.figure.appendChild(el("p", "empty", "No retrospectives yet."));
      return chart.figure;
    }
    var box = plotBox();
    var range = payload.range || [1, 10];
    var lo = range[0];
    var hi = range[1];
    var from = rows[0].at;
    var to = rows[rows.length - 1].at;
    // One retro is a single moment, so the domain has no width and every
    // x would be NaN. Give it a week either side, which is what the axis
    // would show once the second retro lands.
    if (to === from) {
      from -= 3.5 * 24 * 3600 * 1000;
      to += 3.5 * 24 * 3600 * 1000;
    }
    var span = to - from;
    var x = function (at) { return box.x0 + ((at - from) / span) * (box.x1 - box.x0); };
    var y = function (v) { return box.y1 - ((v - lo) / (hi - lo)) * (box.y1 - box.y0); };

    drawYAxis(chart.svg, box, [
      { value: hi, y: y(hi) },
      { value: Math.round((hi + lo) / 2), y: y(Math.round((hi + lo) / 2)) },
      { value: lo, y: box.y1 },
    ], function (v) { return String(v); });

    series.forEach(function (line) {
      var d = "";
      var open = false;
      rows.forEach(function (row) {
        var value = (row.scores || {})[line.key];
        if (typeof value !== "number") {
          // Same rule as the quota chart: a missing score is a hole, not
          // a zero, and a line drawn down to the axis and back would read
          // as a week that went catastrophically.
          open = false;
          return;
        }
        d += (open ? "L" : "M") + x(row.at).toFixed(1) + " " + y(value).toFixed(1) + " ";
        open = true;
      });
      if (!d) return;
      chart.svg.appendChild(svgEl("path", {
        d: d.trim(), fill: "none", stroke: line.color, "stroke-width": line.width,
        "stroke-linejoin": "round", "stroke-linecap": "round",
      }));
      // A dot per retro as well as the line. With one retro there is no
      // line to see at all, and with five there are still only five real
      // observations -- marking them stops the eye reading the segments
      // between as data.
      rows.forEach(function (row) {
        var value = (row.scores || {})[line.key];
        if (typeof value !== "number") return;
        chart.svg.appendChild(svgEl("circle", {
          cx: x(row.at).toFixed(1), cy: y(value).toFixed(1), r: line.width,
          fill: line.color,
        }));
      });
    });

    drawXDates(chart.svg, box, from, to);
    attachHover(chart, box, rows.map(function (row) {
      return {
        x: x(row.at), at: row.at,
        lines: series.map(function (line) {
          var value = (row.scores || {})[line.key];
          return {
            color: line.color,
            label: line.label,
            value: typeof value === "number" ? value + "/" + hi : "—",
          };
        }),
      };
    }), fmtDay);

    var legend = el("div", "chart-legend");
    series.forEach(function (line) {
      var key = el("span", "legend-key");
      var swatch = el("span", "legend-swatch");
      swatch.style.background = line.color;
      key.appendChild(swatch);
      key.appendChild(el("span", "legend-label", line.label));
      legend.appendChild(key);
    });
    chart.figure.appendChild(legend);
    return chart.figure;
  }

  function renderRetroTiles(payload) {
    var rows = payload.retros || [];
    var latest = rows.length ? rows[rows.length - 1] : null;
    var row = el("div", "tiles");
    row.appendChild(statTile("Retros", String(rows.length)));
    if (!latest) return row;
    var hi = (payload.range || [1, 10])[1];
    retroSeries(payload).forEach(function (line) {
      var value = (latest.scores || {})[line.key];
      row.appendChild(statTile(
        line.label,
        typeof value === "number" ? value + "/" + hi : "—",
        latest.date
      ));
    });
    return row;
  }

  /* One retro, in full. The chart answers "is it getting better"; this
   * answers "why", and the two are on one page because the score without
   * the sentence behind it is the thing he specifically did not ask for. */
  function renderRetroCard(payload, row) {
    var hi = (payload.range || [1, 10])[1];
    var card = el("article", "retro-card");
    var head = el("header", "retro-head");
    head.appendChild(el("h2", "retro-date", row.date));
    if (row.cycle) head.appendChild(el("p", "retro-cycle", "Cycle " + row.cycle));
    card.appendChild(head);

    var scores = el("div", "retro-scores");
    retroSeries(payload).forEach(function (line) {
      var value = (row.scores || {})[line.key];
      var pill = el("span", "retro-pill");
      var swatch = el("span", "legend-swatch");
      swatch.style.background = line.color;
      pill.appendChild(swatch);
      pill.appendChild(el("span", "retro-pill-label", line.label));
      pill.appendChild(el(
        "span", "retro-pill-value",
        typeof value === "number" ? value + "/" + hi : "—"
      ));
      scores.appendChild(pill);
    });
    card.appendChild(scores);

    if (row.overall) card.appendChild(el("p", "retro-overall", row.overall));
    [
      { label: "What is good", text: row.good },
      { label: "What is bad", text: row.bad },
    ].forEach(function (part) {
      if (!part.text) return;
      card.appendChild(el("h3", "retro-sub", part.label));
      card.appendChild(el("p", "retro-text", part.text));
    });
    if ((row.changes || []).length) {
      card.appendChild(el("h3", "retro-sub", "What I am changing"));
      var list = el("ul", "retro-changes");
      row.changes.forEach(function (change) {
        list.appendChild(el("li", "retro-change", change));
      });
      card.appendChild(list);
    }
    return card;
  }

  function renderRetro(payload) {
    stopPolling();
    markNav();
    needsEl.hidden = true;
    var rows = payload.retros || [];
    var latest = rows.length ? rows[rows.length - 1] : null;
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    statusEl.appendChild(el(
      "p", "status-line",
      rows.length
        ? "Retrospectives — " + rows.length + ", newest " + latest.date
        : "Retrospectives — none yet"
    ));
    if (payload.replayed) statusEl.appendChild(savedCopyLine());
    feed.textContent = "";
    if (!rows.length) {
      feed.appendChild(el(
        "p", "empty",
        "The first retrospective runs on a Friday morning. Nothing to compare yet."
      ));
      return;
    }
    feed.appendChild(renderRetroTiles(payload));
    feed.appendChild(renderRetroChart(payload));
    // Newest first, which is the opposite of the chart's left-to-right
    // and is right for both: a chart is read forwards and a feed is read
    // from the top.
    rows.slice().reverse().forEach(function (row) {
      feed.appendChild(renderRetroCard(payload, row));
    });
  }

  function loadRetro() {
    fetchPage("/api/retro")
      .then(function (payload) {
        // The same guard the board and costs fetches carry: two taps in
        // quick succession leave two fetches in flight and the loser must
        // not paint over the winner.
        if (route(window.location.pathname).view !== "retro") return;
        renderRetro(payload);
      })
      .catch(function (err) {
        markNav();
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the retrospectives: " + err));
      });
  }

  function load() {
    var here = route(window.location.pathname);
    if (here.view === "board") {
      loadBoard(here.board);
      return;
    }
    if (here.view === "costs") {
      loadCosts();
      return;
    }
    if (here.view === "retro") {
      loadRetro();
      return;
    }
    markNav();
    fetchAll()
      .then(function (results) {
        // Same guard, other direction: a board fetch started before a tap
        // on Journal must not land after this one.
        if (route(window.location.pathname).view !== "journal") return;
        render(results[0], results[1], results[2]);
      })
      .catch(function (err) {
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the journal: " + err));
        // Nothing else is on screen on a cold load -- the header is still
        // saying "loading…" -- so this reports on the first failure. There
        // is no previous answer to protect, and a header stuck on "loading…"
        // is the least informative thing the page could leave up.
        pollFailures = POLL_FAILURES_BEFORE_STALE;
        renderStatusUnreachable(fetchFailureDetail(err));
      });
  }

  /* Edvard, issues.md 2026-08-10: "Nova takes a long time to load when i
   * refresh it. And i have to refresh it to see new messages."
   *
   * The second half. A cycle writes an entry every hour and the page had
   * no way to find out -- the only poll in this file belongs to a comment
   * drawer waiting on its own reply, so an open tab showed whatever was
   * true when it loaded.
   *
   * Three things keep this from being a page that fidgets:
   *
   * - It re-renders only when something actually changed. `version` is the
   *   server's etag, carried inside the payload because the service worker
   *   can serve this response from its cache and the header would be lost
   *   with it. Comments have no cache to key on, so they are compared as
   *   text; they are 6KB.
   * - It never interrupts. A render throws every card away and builds new
   *   ones, so typing into a comment box mid-poll would lose what was
   *   typed. Anything with text in it defers the update to the next round
   *   rather than dropping it -- the version comparison is against what was
   *   rendered, so the change is still pending next time.
   * - It stops while the tab is hidden and catches up the moment it is
   *   looked at again, which is the phone case: the app is opened, not
   *   refreshed. That is the actual shape of his complaint.
   */
  var POLL_MS = 30000;
  var POLL_FAILURES_BEFORE_STALE = 2;
  var pollFailures = 0;
  var renderedVersion = null;
  /* Whether what is on screen right now came out of the service worker's
   * cache. Kept beside `renderedVersion` and compared the same way, because
   * coming back from a replayed payload is a change the version cannot
   * express: the bytes are identical, the etag is identical, and the only
   * thing that moved is whether they are current. Without this the "showing
   * a saved copy" banner is raised and never lowered. */
  var renderedReplayed = false;
  var renderedComments = null;
  var pollTimer = null;

  function typing() {
    var boxes = document.querySelectorAll("textarea");
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].value.trim()) return true;
    }
    return false;
  }

  function poll() {
    // The poll is the journal's. On a board page it would fetch the feed
    // and render it straight over the list -- the same "never interrupt"
    // rule the typing check below exists for, one level up.
    if (route(window.location.pathname).view !== "journal") return schedulePoll();
    if (document.hidden || typing()) return schedulePoll();
    fetchAll()
      .then(function (results) {
        var journal = results[0];
        var comments = JSON.stringify(results[2]);
        // Normalised the same way `render` stores it. A payload with no
        // `version` at all -- an older server, or the tailnet serving the
        // last build's response to this build's app.js -- would otherwise
        // compare `undefined` against `null` and count as changed on every
        // single poll, throwing away every open drawer twice a minute.
        var version = (journal && journal.version) || null;
        var replayedNow = !!(journal && journal.status && journal.status.replayed);
        var changed = version !== renderedVersion || comments !== renderedComments
          || replayedNow !== renderedReplayed;
        // Re-checked after the fetch as well as before it: a request takes
        // long enough for him to have started typing during one.
        // A poll that came back is the only thing that clears the header's
        // error state, and it clears it by rendering the answer it just got
        // -- below, or on the next change. Reset here rather than inside the
        // `changed` branch: an unchanged payload is still a reachable
        // server, and that is the case that would otherwise stay red
        // forever once the loop went quiet.
        if (pollFailures >= POLL_FAILURES_BEFORE_STALE) renderStatus(journal.status || {});
        pollFailures = 0;
        if (changed && !typing()) {
          /* New entries land at the top, so a naive re-render shoves
           * whatever he was reading down the page by exactly the height
           * that was added. Holding the offset by that delta keeps the
           * card under his thumb where it was. */
          var before = document.body.scrollHeight;
          var top = window.scrollY;
          render(journal, results[1], results[2]);
          if (top > 0) window.scrollTo(0, top + (document.body.scrollHeight - before));
        }
      })
      /* Not on the first failure, and the threshold is doing real work
       * rather than hedging. A phone drops a single request routinely --
       * waking the tab, changing network -- and turning the header red for
       * one of those would be the same flash-and-retract Edvard reported in
       * the first place. Two consecutive misses is 30 seconds of a server
       * that is genuinely not answering, which is the thing worth showing
       * and is not something a handover between cells produces. */
      .catch(function (err) {
        pollFailures += 1;
        if (pollFailures >= POLL_FAILURES_BEFORE_STALE) {
          renderStatusUnreachable(fetchFailureDetail(err));
        }
      })
      .then(schedulePoll);
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = setTimeout(poll, POLL_MS);
  }

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) poll();
  });

  /* The capture box (item 6). One button per target rather than a target
   * toggle plus a submit: it is one tap fewer on a phone, which is the
   * whole point of the feature, and it is why a third target cost one
   * button rather than a redesign. The text is only cleared once the
   * server confirms the write -- a failed capture that wiped the box would
   * lose the thought it exists to catch.
   *
   * Nothing here names the targets: `send` takes whatever `data-target`
   * the button carries and the server rejects anything not in
   * CAPTURE_TARGETS, so the Note button (Edvard, issues.md 2026-08-12)
   * needed no change in this file at all. */
  (function captureBox() {
    var form = document.getElementById("capture-form");
    if (!form) return;
    var textEl = document.getElementById("capture-text");
    var captureStatus = document.getElementById("capture-status");
    var buttons = Array.prototype.slice.call(form.querySelectorAll(".capture-btn"));

    /* Edvard, issues.md 2026-08-14: "i want that aswell both when i input
     * in the textbox in the Nova app". Unrated is the default and stays
     * first -- most captures are a sentence he wants written down, not a
     * rating exercise, and forcing a choice would put a decision in front
     * of the box he types into. It resets after a send for the same
     * reason: the next thought is not the same urgency by default.
     *
     * `buildPrioPicker` (above) is what keeps the closed button wordless
     * while the open list still spells out each rating -- a native
     * <select> could not do both at once. onPick has nothing async to do
     * here; the composer only remembers the choice until send() reads it. */
    var prioPicker = buildPrioPicker({
      current: "",
      ariaLabel: "Priority",
      triggerClass: "capture-prio",
      triggerId: "capture-prio",
      onPick: function () {},
    });
    /* Appended last, so it renders at the far right of the button row,
     * on the same line as Issue/Idea/Note (Edvard, 2026-08-14). issues.md
     * 2026-08-14 split this into its own row above the buttons because at
     * 390px the select was 136px wide -- "🔴 Immediately" set its
     * intrinsic width -- which left room for exactly one of the three
     * buttons on the first line. That measurement no longer holds: the
     * control is a fixed 44px circle now, not a word, so it rejoins the
     * group it was split out of. See `.capture-submit` in style.css. */
    document.querySelector(".capture-submit").appendChild(prioPicker.el);


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
        body: JSON.stringify({ target: target, text: text, priority: prioPicker.getValue() }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          textEl.value = "";
          prioPicker.setValue("");
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
    /* Enter is a newline. Edvard, issues.md #90: *"When i press enter on my
     * keyboard, it automatically submits my input text as an issue in the
     * Nova text input field. Pressing enter should create a new line, not
     * submit."*
     *
     * This used to be Enter-sends / Shift+Enter-newline, on the reasoning
     * that a capture is one line per item so Enter meaning "file it" costs
     * nothing. It cost plenty. He types this box on a phone, where a soft
     * keyboard has a return key and no reachable Shift+Enter at all, so the
     * escape hatch existed only on a desktop he does not capture from --
     * and the failure is destructive rather than annoying: half a sentence
     * is filed as its own issue and the rest has nowhere to go.
     *
     * It also had to guess a target, and guessed `issues` for a box with
     * three buttons. An idea typed and Entered was filed as a bug.
     *
     * Cmd/Ctrl+Enter keeps a keyboard send for the desktop case, where the
     * modifier is the conventional "submit this composer" chord and cannot
     * be hit by accident mid-sentence. It still has to pick a target, so it
     * picks the same one the leftmost button does. The button is the path
     * that has to work, and it is the only one that works on a phone. */
    textEl.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        send(buttons.length ? buttons[0].getAttribute("data-target") : "issues");
      }
    });
    textEl.addEventListener("input", fit);
    fit();
  })();

  if (menuBtn) {
    menuBtn.addEventListener("click", function () { setMenu(!menuOpen()); });
  }
  if (scrim) {
    scrim.addEventListener("click", function () { setMenu(false); });
  }
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && menuOpen()) setMenu(false);
  });

  // Back/forward between /cycle/N and / without a round trip.
  window.addEventListener("popstate", load);
  document.addEventListener("click", function (event) {
    var anchor = event.target.closest && event.target.closest("a[href^='/']");
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    // Whatever was tapped, the page underneath is about to change, so the
    // drawer has done its job. This covers the three links inside it and
    // also a per-cycle link in the feed, which cannot be reached with it
    // open but costs nothing to be right about.
    setMenu(false);
    history.pushState(null, "", anchor.getAttribute("href"));
    load();
    window.scrollTo(0, 0);
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  load();
  schedulePoll();
})();
