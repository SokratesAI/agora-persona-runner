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

  /* The owner's name, in the two forms this app actually needs (his
   * issues.md #98: he does not want his own first name standing as a
   * static string in a repo anyone can read).
   *
   * Before this there were eight literals of it in this file and they were
   * doing two different jobs. `OWNER_RECORD` is a stored value: it is what
   * `/api/board/comment` and `/api/board/order` write down as the author, and
   * what an Agora message's `sender` carries back, so every row already in
   * CouchDB and every line already in his markdown is keyed on this exact
   * string -- renaming it would relabel history, not anonymise it.
   * `OWNER_LABEL` is what a reader sees, and nothing about it has to match
   * anything: the ask thread has drawn "You" over his own bubbles since it
   * was built, so the rest of the app now says the same word.
   *
   * The point of the split is that his name appears once here instead of at
   * every rendering site, and the one place it is left is labelled as data. */
  var OWNER_RECORD = "Edvard";
  var OWNER_LABEL = "You";

  /* Record value -> what to print. Anyone else keeps their own name, which
   * is why this is a mapping rather than a constant: a board note carries
   * whichever author wrote it, and only his becomes "You". */
  function ownerLabel(author) {
    return author === OWNER_RECORD ? OWNER_LABEL : author;
  }

  var feed = document.getElementById("feed");
  var statusEl = document.getElementById("status");
  var mailEl = document.getElementById("mail");
  /* The journal's comment filter, declared up here with the other page state:
     `render` reads it and runs long before `buildJournalFilter` is reached, so
     a `var` beside the builder would be `undefined` on the first paint and
     filter every card off the feed. */
  var journalFilter = "all";
  var journalFilterNode = null;
  var journalFilterButton = null;

  var navEl = document.getElementById("nav");
  var menuBtn = document.getElementById("menu-btn");
  var scrim = document.getElementById("scrim");

  /* The sidebar (the owner, issues.md 2026-08-11: "Move the Journal, issues
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
   * two board pages the owner asked for in issues.md #57.
   *
   * The server serves the same shell for all of them (nova_site's GET
   * handler) and this decides what to fetch, so a board page survives a
   * cold load and a bookmark rather than only being reachable by tapping
   * the nav. */
  function route(pathname) {
    var path = (pathname || "/").replace(/\/+$/, "") || "/";
    var cycle = /^\/cycle\/(\d+)$/.exec(path);
    if (cycle) return { view: "journal", cycle: parseInt(cycle[1], 10), board: null };
    /* `/` is the landing page and `/journal` is the feed, as of idea #274.
     * The owner, 2026-09-08: *"Lets make a landing page instead!"* Both are
     * real URLs -- the feed is bookmarked, linked from three "← all
     * entries" links below, and is where `/cycle/<n>` belongs -- so this is
     * two routes and not a redirect. `/cycle/<n>`, `/asks` and `/replies`
     * stay `view: "journal"` above and below, because they are the feed
     * filtered, and the default at the bottom of this function is still
     * the feed: an unknown path lands on content rather than on a
     * dashboard about content. */
    if (path === "/journal") return { view: "journal", cycle: null, board: null };
    if (path === "/issues") return { view: "board", cycle: null, board: "issues" };
    if (path === "/ideas") return { view: "board", cycle: null, board: "ideas" };
    if (path === "/notes") return { view: "notes", cycle: null, board: null };
    /* `/asks` -- the journal, filtered to the cards that asked him something.
     *
     * The owner, capture 2026-09-01: *"Drop the current 'needs me'
     * functionality (the yellow 'N WAITING ON YOU' button/list) ... and
     * replace it with a simple filter that just lists the journal entries
     * that need his input. No fancy list/carousel behavior, just a plain
     * filtered view."* So it is `view: "journal"` and not a view of its
     * own: the cards render through exactly the same path the feed uses,
     * which is what "plain" means here and is why the panel it replaces
     * -- a second, differently-shaped rendering of the same asks -- is
     * deleted rather than moved. */
    if (path === "/asks") return { view: "journal", cycle: null, board: null, asks: true };
    /* `/replies` -- the journal, filtered to the cards carrying a reply he
     * has not read.
     *
     * The owner, capture 2026-09-08: *"Make the 'N new replies' pill on the
     * Journal page a filter like the 'waiting on you' pill -- a route
     * (/replies) showing the journal cards that have comment replies I have
     * not seen yet -- instead of the inline panel it opens today."* Same
     * shape as `/asks` above and for the same reason: `view: "journal"`, so
     * the cards render through the one path the feed uses.
     *
     * The one way it is unlike `/asks`: the server cannot compute this set.
     * The read marks are this browser's, so the page has to send the cycle
     * numbers -- see `journalUrl`. */
    if (path === "/replies") return { view: "journal", cycle: null, board: null, replies: true };
    if (path === "/pool") return { view: "pool", cycle: null, board: null };
    if (path === "/costs") return { view: "costs", cycle: null, board: null };
    /* `/alerts` -- idea #122, "give the K3s sentinel somewhere to report".
     * The Sentinel is gone and Prometheus rules replaced it, but its
     * findings still only ever appeared inside a cycle's own preflight
     * output. This is the page he can open instead. */
    if (path === "/alerts") return { view: "alerts", cycle: null, board: null };
    if (path === "/retro") return { view: "retro", cycle: null, board: null };
    if (path === "/plan") return { view: "plan", cycle: null, board: null };
    // `/conversation/<id>` -- the URL a push notification opens, so the tap
    // lands on the thread the notification was about instead of on whatever
    // page a Nova tab was already showing. Decoded for the same reason
    // `/project/<name>` is: an id travels through `encodeURIComponent` in
    // sw.js and must be looked up as the id the listing carries.
    var conv = /^\/conversation\/(.+)$/.exec(path);
    if (conv) {
      var convId = conv[1];
      try { convId = decodeURIComponent(convId); } catch (e) { /* leave it raw */ }
      return { view: "conversations", cycle: null, board: null, conversationId: convId };
    }
    if (path === "/heartbeats") return { view: "heartbeats", cycle: null, board: null };
    if (path === "/catalog") return { view: "catalog", cycle: null, board: null };
    if (path === "/diag") return { view: "diag", cycle: null, board: null };
    if (path === "/settings") return { view: "settings", cycle: null, board: null };
    if (path === "/galaxy") return { view: "galaxy", cycle: null, board: null };
    if (path === "/projects") return { view: "projects", cycle: null, board: null, project: null };
    // `/project/Nova` -- idea #92 phase 3. Decoded here rather than left
    // raw because a project name is free text the owner types into a
    // board cell, so `Sokrates Post` reaches this as `Sokrates%20Post`
    // and would otherwise be looked up under a name no row carries.
    var project = /^\/project\/(.+)$/.exec(path);
    if (project) {
      var name = project[1];
      try { name = decodeURIComponent(name); } catch (e) { /* leave it raw */ }
      return { view: "project", cycle: null, board: null, project: name };
    }
    if (path === "/") return { view: "home", cycle: null, board: null };
    return { view: "journal", cycle: null, board: null };
  }

  /** `/cycle/49` -> 49. Anything else -> null (show the whole feed). */
  function routedCycle(pathname) {
    return route(pathname).cycle;
  }

  /** Whether the URL is the open-asks filter. */
  function routedAsks(pathname) {
    return !!route(pathname).asks;
  }

  /** Whether the URL is the unread-replies filter. */
  function routedReplies(pathname) {
    return !!route(pathname).replies;
  }

  function markNav() {
    var here = route(window.location.pathname);
    // Hidden on `/asks` for the reason a deep link hides it: the URL is
    // already a filter, and a second one narrowing it would answer with
    // the newest matches across the whole archive rather than within the
    // asks -- `journal_page` treats `q` and `asks` as separate windows.
    setJournalSearchVisible(here.view === "journal" && here.cycle === null
      && !here.asks && !here.replies);
    // Every view but the journal is named after its own path, so the two
    // single-page views need no branch of their own -- which is what a
    // third one turning the chain into a nested ternary made worth doing.
    var want = here.view === "board"
      ? "/" + here.board
      // Both project views highlight the one nav tab there is. A tab per
      // project would be a nav that grows every time he files a row.
      : here.view === "project" || here.view === "projects" ? "/projects"
      : here.view === "journal" ? "/journal"
      // The landing page is the only view whose path is not its own name.
      : here.view === "home" ? "/" : "/" + here.view;
    var tabs = navEl ? navEl.querySelectorAll(".nav-tab") : [];
    for (var i = 0; i < tabs.length; i++) {
      var on = tabs[i].getAttribute("href") === want;
      tabs[i].classList.toggle("on", on);
      // `aria-current` rather than only a class: the nav is three links
      // and the active one is otherwise distinguishable by colour alone.
      if (on) tabs[i].setAttribute("aria-current", "page");
      else tabs[i].removeAttribute("aria-current");
      // The groups are `<details>`, closed on load, so the highlight above
      // would otherwise be drawn inside a fold nobody can see. Opening the
      // one fold that holds the current page is the exception to "default
      // closed" the owner's ask implies rather than states: they asked for
      // a short drawer, and the group you are standing in is the one row
      // that is not noise. Only ever opened here -- a fold the owner shuts
      // themselves stays shut, because `markNav` runs on navigation, and
      // navigating is what changes which fold is current.
      if (on) {
        var fold = tabs[i].closest ? tabs[i].closest(".nav-fold") : null;
        if (fold) fold.open = true;
      }
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
   * the owner, issues.md 2026-08-11: "The Nova site closes all drawers on what
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
   * An entry with no cycle number gets no memory: there is one (the owner's
   * first message), nothing else can address it either, and inventing a key
   * from its title would make two untitled notes share one.
   */
  var folds = {};

  function foldFor(cycle) {
    /* `part` joins the three booleans because a poll rebuilds the feed from
     * scratch: without it, the owner taps to the addendum, a routine poll lands,
     * and the tab silently reverts to the first part under him while the card
     * and drawer correctly stay open. Found by the reviewer, not by me. */
    if (cycle === null || cycle === undefined) {
      return { expanded: false, journal: false, comments: false, part: 0, ask: null };
    }
    var key = "cycle-" + cycle;
    if (!folds[key]) folds[key] = { expanded: false, journal: false, comments: false, part: 0, ask: null };
    return folds[key];
  }

  /* The read marks and unread counts live in `replies.js` (issue #233).
   * Bound here, where the block used to sit; it borrows nothing, and every
   * caller of these names runs after this line. The guard is for a cached
   * tab that has `app.js` from this build and no `replies.js` yet. */
  var repliesModule = window.novaReplies ? window.novaReplies() : {};
  var askAlreadyOpened = repliesModule.askAlreadyOpened;
  var localStore = repliesModule.localStore;
  var markAskOpened = repliesModule.markAskOpened;
  var markRepliesRead = repliesModule.markRepliesRead;
  var seedRepliesRead = repliesModule.seedRepliesRead;
  var unreadOn = repliesModule.unreadOn;
  var unreadReplies = repliesModule.unreadReplies;
  var unreadSummary = repliesModule.unreadSummary;

  /* "3 cycles since you last looked · 2 PRs merged" used to live here, from
   * ideas board #115. Removed at his ask, 2026-09-08.
   *
   * It answered a question he had stopped having. The line existed because
   * the app opened on the newest card with no sense of how much had been
   * missed, back when he read it once or twice a day; he now works with the
   * app open beside him, so it reported one cycle almost every time it said
   * anything, and a status line that says "1" is a line asking to be
   * ignored. `#changed`, `nova.lastSeen.v1` and the dismissal mark go with
   * it -- an unused element and two unread storage keys are how a page
   * accumulates furniture nobody can explain a year later.
   *
   * Nothing is left behind as a no-op: both call sites go with the
   * functions, because a function that does nothing is one more thing the
   * next reader has to check before they can rule it out.
   */


  /* The unread-reply badge, in its own node outside `statusEl` so it
   * survives a page that is not the journal.
   *
   * the owner, capture 2026-08-25: *"I want to have a status the Nova header
   * if i have unread Journal comments."* The header is one element shared by
   * every page, and this badge lived inside `renderStatus`, which only the
   * journal view calls -- every other view opens by wiping `statusEl` and
   * writing its own line into it. So the status he asked for existed on one
   * of thirteen pages, and the twelve others silently dropped it. That is the
   * half of *"I have read the replies, but the status still shows that i have
   * not read them"* that cycle 474 did not reach: from Issues, Ideas or Beats
   * there was no badge to tap and no way to find out a reply had arrived.
   *
   * `#mail` is a sibling of the header rather than a child, because a child
   * would be destroyed by the very `statusEl.textContent = ""` that each page
   * runs on entry -- which is exactly how the badge got lost in the first
   * place. Painting into a node nobody else clears is the fix; hooking twelve
   * header builders would be twelve places to forget. */
  function paintMail(replayed) {
    /* The badge is gone; the unread signal it carried is a dot on the filter
     * button now, and this is the call that keeps it in step with the read
     * marks. */
    paintFilterState();
    if (!mailEl) return;
    mailEl.textContent = "";
    paintMailInto(replayed);
    if (mailEl.childNodes.length) mailEl.removeAttribute("hidden");
    else mailEl.setAttribute("hidden", "");
  }

  function paintMailInto(replayed) {
    /* Replies he has not opened yet, pointing at the card holding the oldest.
     *
     * the owner, capture 2026-08-25: *"I want to have a status the Nova header
     * if i have unread Journal comments."* This is that status. It reads the
     * same `lastCommentsByCycle` the ask pill above does, and it is the header
     * half of a pair -- the card's 💬 button carries the per-card count.
     *
     * Suppressed on a replayed payload for the ask pill's reason: the page is
     * showing a saved copy and already says so one line up, and a count of
     * what is new is a claim about right now.
     *
     * That guard is `status.replayed` -- the *journal* came out of the
     * worker's cache -- and not "the comments payload was replayed", which is
     * a weaker case and deliberately not covered. A cached comments payload
     * can only be missing replies, never carrying extra ones, so the count it
     * yields is at worst too low; suppressing on it would trade a badge that
     * under-reports for no badge at all, which is the same thing from the
     * reader's side and costs a real notification when only that one route
     * was stale. */
    /* The "N new replies" pill used to be built here.
     *
     * Gone on 2026-09-13: *"we make the status button for filtering on
     * comments also go away, but we should make a new filter button next to
     * the search button"*. The count and the filter are the same job, and the
     * filter is the one that can say *which* cards -- so `#mail` is empty on
     * every page now and `buildJournalFilter` carries the unread signal as a
     * lit dot on the button. `paintMail` and the node stay: the journal calls
     * it, and a node nobody paints into costs nothing while the filter owns
     * the state. */
  }

  /* The wordmark, which is also the way home.
   *
   * His capture, 2026-09-13: *"Make the Nova title navigate to the
   * homepage."* Fifteen render functions build this heading, so it is a
   * helper rather than fifteen edits -- and it is a real `<a>` rather than a
   * click handler on the `<h1>`, because a heading that navigates is
   * invisible to the keyboard and announces itself as a heading to a screen
   * reader. The static copy in index.html carries the same markup, so the
   * shell is a link before app.js has run. */
  function wordmark() {
    var heading = el("h1", "wordmark");
    var home = el("a", "wordmark-home", "Nova");
    home.setAttribute("href", "/");
    heading.appendChild(home);
    return heading;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* The attach button every composer on this site mounts lives in
   * `attach.js`, loaded before this file: `window.novaAttach.build(opts)`.
   * It reads none of this file's state, so it moved out whole rather than
   * being converted in place (issue #233, step 11). */

  /* Append `text` to `container` as paragraphs, rendering an attached
   * image as an image.
   *
   * Deliberately not a markdown renderer. It recognises exactly two
   * constructs -- `![alt](/api/upload/<name>)` and `[alt](/api/upload/
   * <name>)`, which are the two lines `buildAttach` writes, an image and
   * any other file -- and everything else stays the plain text it
   * has always been. The comment painter's own note says "nothing here
   * interprets it as markdown", and that stays true of everything the owner
   * types himself; what changed is that this site now generates one
   * specific line on his behalf and has to be able to read it back.
   *
   * The URL is required to start with `/api/upload/` rather than being
   * escaped, so a pasted `![](javascript:…)` or a remote tracker URL is
   * shown as the text it is instead of being turned into an element. */
  var ATTACH_RE = /(!?)\[([^\]]*)\]\((\/api\/upload\/[A-Za-z0-9._-]+)\)/g;

  /* One attachment -> one node, used by both readers of that construct:
   * `appendRichText`, which parses raw comment text here in the browser,
   * and `renderSpans`, which is handed an `attach` span already parsed by
   * `nova_journal.render_inline` on the server. Written once because the
   * two paths must not disagree about what an attachment looks like --
   * the same file appears in a journal comment and in a board write-up,
   * and a thumbnail in one place and a bare URL in the other reads as a
   * bug in whichever one he happens to look at second. */
  function attachNode(url, alt, isImage) {
    var link = el("a", "attach-link");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    if (isImage) {
      var img = el("img", "attach-img");
      img.src = url;
      img.alt = alt || "attached image";
      // Lazy, because a thread can hold many of these and they are the
      // heaviest thing on the page by an order of magnitude.
      img.loading = "lazy";
      link.appendChild(img);
      return link;
    }
    // A file rather than a picture. It gets its name and a paperclip
    // instead of a thumbnail, because there is nothing to show and a
    // bare URL would be the 32-hex hash, which tells him nothing about
    // what he sent.
    link.className = "attach-link attach-file";
    link.textContent = "📎 " + (alt || "attached file");
    link.setAttribute("download", alt || "");
    return link;
  }

  /* The rich-text renderer lives in `richtext.js` (issue #233). Bound here,
   * where the block used to sit, after `ATTACH_RE` and `attachNode`. The
   * guard is for a cached tab that has `app.js` from this build and no
   * `richtext.js` yet. */
  var richTextModule = window.novaRichText ? window.novaRichText({
    ATTACH_RE: ATTACH_RE,
    attachNode: attachNode,
    el: el,
  }) : {};
  var appendRichText = richTextModule.appendRichText;
  var loadWhenScrolledTo = richTextModule.loadWhenScrolledTo;
  var renderBlocks = richTextModule.renderBlocks;
  var renderSpans = richTextModule.renderSpans;
  var stopScrollWatch = richTextModule.stopScrollWatch;

  /** merged/shipped read as wins, stuck/no-op as not. Anything unrecognised
   * gets the neutral class rather than being guessed at. */
  function outcomeClass(outcome) {
    var value = (outcome || "").toLowerCase();
    if (/merged|shipped/.test(value)) return "badge badge-good";
    if (/stuck|no-op|none/.test(value)) return "badge badge-warn";
    return "badge";
  }

  /* The outcomes that are safe to draw as a badge, as a closed list rather
   * than a length guess. The owner, `issues.md` 2026-08-24, after a run of
   * cycles died without writing anything: "Earlier we did have some mention
   * about this in Nova but i said to take the statuses away. But now, i miss
   * the status fields. Please bring them back."
   *
   * What he had cut (#300) was the pill rendering *free text*: the footer's
   * Outcome field is unconstrained, cycle 340 wrote a whole clause into it,
   * and the card drew 84 characters of uppercased grey where a word goes --
   * a second title above the blue summary. Bringing the field back unchanged
   * re-earns that complaint the next time a cycle writes a sentence there.
   *
   * Measured against the live journal, re-taken cycle 362 off `/api/journal`:
   * of 414 outcomes on record, 405 are exactly one of the seven words below
   * -- merged 326, shipped 49, report 14, stuck 7, no-op 6, research 2,
   * open 1 -- and 9 are clauses. So the vocabulary is what the loop actually
   * writes, and a value outside it is the shape that got the pill cut: it
   * stays off the card, exactly as it is today.
   *
   * The list held `none`, `blocked` and `partial` for one cycle and they are
   * gone. A reviewer asked what corroborated them and the answer was
   * nothing: zero occurrences in 414 entries, and the footer instruction
   * they were credited to (`tests/test_nova_site.py`) offers only merged /
   * shipped / stuck / no-op -- its `none` is the *PR* field's value, not an
   * outcome. `none` was the actively harmful one. `isRealPr` exists to stop
   * the header drawing the word "none", and admitting it here would have
   * drawn it as a badge instead, which is #300's complaint coming back
   * through the door it was thrown out of. A word earns its place here by
   * appearing in the archive, not by sounding like something a cycle
   * might write.
   *
   * Returns the value to draw, or "" for "not a status word". */
  function shortOutcome(outcome) {
    var value = String(outcome || "").trim();
    return /^(merged|shipped|report|research|stuck|no-op|open)$/i.test(value)
      ? value
      : "";
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

  /* The comments the page last saw, keyed by cycle. Held here rather than
   * passed down every call because `renderStatus` has two callers and only
   * one of them is `render` -- the poll re-renders the header on its own to
   * clear the offline state, and a header that lost the ask pill every time
   * that happened would flicker it away twice a minute. */
  var lastCommentsByCycle = {};

  /* Whether a comments payload has ever arrived. `/api/comments` is
   * tolerated when it fails -- it resolves to null and costs the bubbles,
   * not the feed -- and without this the header would read that empty
   * answer set as "he has replied to nothing" and raise the pill on every
   * open ask. Same failure as the replayed case one level in: a claim about
   * what he has done, made from a payload that never came. */
  var haveComments = false;

  /* Cycles whose ask he has already answered in the cycle's own chat
   * thread, as `{ "1068": true }`. His issue #165: the ask goes out in the
   * reply, the reply is what his phone buzzes with, and answering it there
   * left the badge up until he repeated himself in a comment box.
   *
   * `/api/asks/chat` is tolerated exactly like `/api/comments` -- a read
   * that fails leaves this empty, so every ask reads as open. That is the
   * safe direction: showing him a question he has answered costs a scroll,
   * hiding one he has not costs him the question. */
  var chatAnsweredCycles = {};

  function setChatAnswered(payload) {
    var cycles = (payload && payload.cycles) || [];
    var next = {};
    for (var i = 0; i < cycles.length; i++) next[String(cycles[i])] = true;
    chatAnsweredCycles = next;
  }

  /* Whether this card's ask has an answer anywhere -- a comment on the
   * card, or a message from him in the cycle's thread. One function so the
   * header count and the `/asks` feed can never disagree about it. */
  function askAnswered(cycle, commentsByCycle) {
    var answers = commentsByCycle && commentsByCycle[String(cycle)];
    if (answers && answers.length) return true;
    return !!chatAnsweredCycles[String(cycle)];
  }

  /* The oldest ask the owner has not replied to, or null.
   *
   * `status.asks` is every card that raised one, newest first and with no
   * opinion about which are still open (see `open_asks`); a card is
   * answered once he has commented on it, which is what the comments
   * payload knows. Intersecting the two here rather than on the server is
   * what makes the pill disappear the moment his reply lands, instead of
   * whenever the journal cache next rebuilds.
   *
   * Last match wins because the list is newest first, so the survivor is
   * the one that has waited longest -- which is the whole point. An ask
   * scrolls out of the twenty-entry window in a day and stops being
   * something he can stumble across; #94's sat unanswered for a day with
   * the row it blocks at the top of his board. */
  function openAsks(status, commentsByCycle) {
    var asks = (status && status.asks) || [];
    var open = [];
    for (var i = 0; i < asks.length; i++) {
      if (askAnswered(asks[i].cycle, commentsByCycle)) continue;
      open.push(asks[i]);
    }
    return open;
  }

  /* One status field, and where it points.
   *
   * the owner, capture 2026-08-22: *"The status fields at the top, we are
   * keeping them. Please have them shown horisontal listed, not vertical.
   * Also clicking them navigates me down to the Journal it references."*
   *
   * Two asks, and the second one is the reason this is a function rather
   * than a CSS change. Each field was a `<p class="status-sub">` appended
   * straight to the header, so they stacked and none of them was a target
   * for a click. They now go into one wrapping flex row (`.status-subs`),
   * and a field that names a cycle is an `<a>` to that cycle's card.
   *
   * `#cycle-N` and not `/cycle/N`: the card is already on this page, in
   * the feed below, with its own id (`card.id = "cycle-" + entry.cycle`),
   * so the anchor scrolls him down to the entry the field is talking
   * about — which is what he asked for, in his words, "navigates me down
   * to". The header renders on pages that carry no feed as well, so the
   * click falls back to the `/cycle/N` permalink when the card is not in
   * the document; a dead in-page anchor that silently does nothing is the
   * failure worth spending four lines on.
   *
   * `cycle` is null for the fields that reference nothing — "can't reach
   * Nova", "can't read the journal", and "cycle running", whose entry does
   * not exist yet. Those stay plain, because a link that goes nowhere in
   * particular is worse than no link. */
  function statusField(cycle) {
    if (cycle === null || cycle === undefined) return el("p", "status-sub");
    var field = el("a", "status-sub");
    field.href = "/cycle/" + cycle;
    field.addEventListener("click", function (ev) {
      var card = document.getElementById("cycle-" + cycle);
      if (!card || !card.scrollIntoView) return;   // no feed here: follow the permalink
      ev.preventDefault();
      card.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    return field;
  }

  function renderStatus(status, commentsByCycle) {
    lastStatus = status;
    if (commentsByCycle) {
      lastCommentsByCycle = commentsByCycle;
      haveComments = true;
      seedRepliesRead(commentsByCycle);
    }
    statusEl.textContent = "";
    statusEl.appendChild(wordmark());
    /* Appended at the end, and only if anything went into it — an empty
     * flex row still eats its own top margin. */
    var subs = el("div", "status-subs");

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
      var saved = statusField(null);
      saved.appendChild(el("span", "badge badge-error", "can't reach Nova"));
      saved.appendChild(el("span", "status-pr", "showing a saved copy"));
      subs.appendChild(saved);
    }

    /* The "N waiting on you" pill used to be here.
     *
     * Gone on 2026-09-13: *"the status pills for the 'waiting on you' is
     * still there, I thought we remove all functionality related to that?
     * The new method is conversations."* A cycle that needs him will open a
     * thread (issue #209); a header count of unanswered asks is the
     * mechanism that replaces. `/asks` is no longer linked from anywhere. */

    /* The outcome pill used to live here too (`merged` / `no-op` / ...),
     * restored 2026-08-24 after the owner missed it, then dropped again
     * for good on 2026-09-01: "4 is redundant from the latest journal
     * which I can already see at the top" -- the newest card is always
     * the top card in the feed below, and it already carries this same
     * outcome-and-PR pair. Unlike 2026-08-23's removal, there is nothing
     * standing in for it up here on purpose; the card is the field now.
     * `shortOutcome` / `outcomeClass` stay defined -- the card still uses
     * them for its own badge. */

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
    /* The "cycle running" pill came out on 2026-09-13: *"remove the status
     * pill for cycle running now that we have the Galaxy."* The strip on the
     * landing page draws every live session with what it is working on, which
     * is the same fact with the detail this pill could never carry. */

    /* The stall badge ("no entry for N hours") and the gap badge ("cycle
     * 265 wrote no entry") both used to render here, and the owner asked for
     * both to go, capture 2026-08-20: *"I do not like he statuses on the
     * top of Nova. The message 'cycle 265 wrote no entry' just stands
     * there forever. Please remove all those statuses as i do not want
     * them. They are more for you than me. Actually, i do like the 'cycle
     * is running' status."*
     *
     * They were built for him and they were not for him. A gap badge is a
     * true fact about the record that he can do nothing about -- the run
     * that failed to write is over -- so it sits at the top of his page
     * permanently, which is exactly what he says it does. The server still
     * computes `stalled`, `silentIntervals` and `recentMissingCycles` and
     * still serves them in `/api/status`; a cycle that wants journal health
     * reads `cycle_health.missing_cycles` directly, which is where a fact
     * for me rather than for him belongs. Only the rendering is gone.
     *
     * `recordStale` below survives on purpose and is not the same kind of
     * thing. It does not report the loop's health, it reports that *this
     * page* cannot see current data -- removing it would make a stale page
     * pass itself off as live, which is a regression he did not ask for.
     * If he wants it gone too, that is one sentence and one line. */

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
      var frozen = statusField(null);
      frozen.appendChild(el("span", "badge badge-error", "can't read the journal"));
      frozen.appendChild(el("span", "status-pr", "showing the last thing Nova could see"));
      subs.appendChild(frozen);
    }

    if (subs.childNodes.length) statusEl.appendChild(subs);

    paintMail(replayed);
  }

  /* the owner, comments board 2026-08-14: "Or a display error if the fetch
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
    statusEl.appendChild(wordmark());
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
   * both a journal card and the Needs Edvard block:  (not-prose: quoting a literal)
   *   body(text)  -> the /api/comment payload naming that target
   *   pick(data)  -> that target's comments out of /api/comments
   *   placeholder, ariaLabel -> the words for it
   * Everything below is target-agnostic on purpose; the two differ only in
   * which four things they hand in. */
  /* Unsent comment text, keyed by which box it was typed into, so it
   * survives the re-render that discards the box. See `renderComments`. */
  var drafts = {};
  /* The same, for attachments picked and not yet sent. Separate from
   * `drafts` because it holds objects rather than a string, and because
   * the two are cleared by different things: text by `box.value = ""`,
   * attachments by `attach.clear()`. */
  var attachDrafts = {};

  /* There used to be an `expanded` map here, holding whether a folded thread
   * had been opened. It is gone with the "Show earlier replies" control it
   * served -- the owner, 2026-08-16 20:04: *"I see that a solution to the
   * comments has been to introduce a 'show/hide' comments bar, but that was
   * a failure. Remove it and try something else."* See `renderComments`. */

  /* "40 seconds" / "3 minutes" / "1 hour 5 minutes" -- how long a reply has
   * been in flight. Deliberately coarse above a minute: the point is to let
   * the owner tell a slow answer from a stuck one, and a ticking second count
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

    /* the owner, comments board 2026-08-16, three times inside fifteen
     * minutes: *"it creates a very long list of previous conversations.
     * Something must be done with this, immediately!"*, *"I still see it
     * with a long conversation of previous messages that is not relevant
     * anymore"*, and *"I see all of this text which is quite a lot to
     * scroll past every single time i want to read your newest journals,
     * which is 6-8 times a day."*
     *
     * He is describing the Needs Edvard block, and the cause is that its  (not-prose: quoting a literal)
     * drawer was the one drawer that was never folded (that block is gone) --
     * so every reply he has ever made to it, since 2026-08-10, is painted
     * open at the top of the page, above the newest journal card. A cycle
     * card has the same thread and nobody notices, because a card's drawer
     * is shut until you open it.
     *
     * My first answer was a fold: keep every reply, hide the leading run of
     * retired ones behind a "Show N earlier replies" button. He rejected it
     * the same day, 20:04 -- *"I see that a solution to the comments has
     * been to introduce a 'show/hide' comments bar, but that was a failure.
     * Remove it and try something else."* -- and 20:03, on the block as a
     * whole: *"I think the architecture around the 'needs the owner' block
     * needs to be rethinked as it seems poorly designed."*
     *
     * He is right and the fold was me refusing to answer the question. He
     * asked for old answered replies not to be on the page; I kept them on
     * the page and put a control in front of them, which adds a widget to
     * the thing he said was too long. It also failed outright: I guarded
     * against folding a thread away to nothing, and since I retire every
     * comment I answer, all-retired is the *steady state* -- so the guard
     * fired every time and folded exactly zero of the twelve replies.
     *
     * So: a retired comment is not rendered in this drawer at all. No
     * control, no count, nothing to tap. `## Acknowledged` is the file
     * already saying "acted on, no longer live", which is "not relevant
     * anymore" in his words, and a filter is the honest reading of it.
     *
     * The one property that has to survive is that nothing waiting on me is
     * ever hidden, and a filter gives it outright rather than by argument:
     * only retired comments disappear, and a comment he just typed is never
     * retired. Retirement is not chronological -- `tools/ack_comment.py`
     * retires one comment by `(cycle, stamp)` -- which is what sank the
     * previous cut-at-a-point approach; a filter does not care about order.
     *
     * Nothing is lost: the comments stay in `comments.md`, and every cycle
     * reads them. They are simply not his to scroll past. */

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
    /* The attach button rides in the same row as Comment, before it, so
     * the primary action stays at the right edge where it already was.
     *
     * Its tray goes above the row and below the box, where the previews sit
     * directly under the sentence they belong to.
     *
     * `attachDrafts` is the picture half of `drafts` and exists for the
     * same reason: a render throws this whole drawer away and builds a new
     * one, so an image attached and then left unsent while a poll fires
     * would vanish while the typed text survived. */
    var attach = window.novaAttach.build({
      // Send is blocked while the image is going up, or the comment sends
      // without it -- see `busy` in `buildAttach`.
      onBusy: function (isBusy) { send.disabled = isBusy; },
      onStatus: function (text, isError) {
        status.textContent = text;
        status.className = isError ? "comment-status is-error" : "comment-status";
      },
      onChange: function (list) {
        if (list.length) attachDrafts[target.key] = list;
        else delete attachDrafts[target.key];
      },
    });
    if (attachDrafts[target.key]) attach.restore(attachDrafts[target.key]);
    // Appended, not inserted: `actions` is not a child of `drawer` yet at
    // this point, so this lands between the box and the row that follows.
    drawer.appendChild(attach.tray);
    actions.appendChild(attach.input);
    actions.appendChild(attach.button);
    /* Speak instead of typing, in the second place he writes me prose
     * (ideas.md #221). The recogniser is `dictate.js`, the same one the
     * chat dock runs; the button reveals itself only on a browser that
     * has one, so a desktop Firefox shows the row it always had. */
    var mic = window.novaDictation.button(el, "comment-mic");
    actions.appendChild(mic);
    window.novaDictation.wire({
      button: mic,
      onText: window.novaDictation.appendTo(box),
      onStatus: function (text) {
        status.textContent = text;
        status.className = text ? "comment-status is-error" : "comment-status";
      },
    });
    var send = el("button", "comment-send", "Comment");
    send.type = "button";
    actions.appendChild(send);
    drawer.appendChild(actions);

    var toggle = el("button", "comment-toggle");
    toggle.type = "button";
    toggle.setAttribute("aria-label", target.ariaLabel);

    var lastItems = comments;
    // Set only by `tapped`, read only by `paint`. See both.
    var openedByTap = false;

    /* Which comments this drawer shows. A retired one is dropped outright.
     *
     * A filter is what the previous cut-at-a-point version deliberately
     * avoided, on the argument that dropping a retired comment from the
     * middle of a thread leaves the two either side touching, so the thread
     * reads as continuous while a turn of it is missing. That argument is
     * about preserving a conversation. It stopped applying the moment he
     * said the conversation itself is the problem -- these are answered
     * asks he does not want on the page, not a discussion he is following.
     *
     * The property that does still matter is that nothing waiting on me is
     * ever hidden, and a filter gives it directly: `acknowledged` is set
     * only by a cycle that acted on the comment, so a message he just typed
     * can never be filtered out. */
    function shown(items) {
      if (!target.fold) return items || [];
      return (items || []).filter(function (c) { return !c.acknowledged; });
    }

    function paint(items) {
      lastItems = items;
      list.textContent = "";
      items = shown(items);

      /* One flat list in the order things were actually said.
       *
       * the owner, issues.md 2026-08-23: *"a Nova cycle reply posted at 14:01
       * rendered between two of my comments timestamped 13:31 and 13:40
       * instead of after both — thread isn't sorting strictly by time."*
       *
       * He is describing this loop, which used to append each comment and
       * then its own replies immediately after it. A reply is stored inside
       * the comment it answers (`comments.md` nests it under the `###`
       * heading), so painting in storage order pins every answer to the
       * position of the question, however much later it was written. The
       * server already sorts his comments oldest-first; the replies were the
       * part that never entered that ordering.
       *
       * So the nodes are collected with the stamp they carry and sorted once
       * at the end. `order` is the tiebreak, which keeps this stable without
       * relying on the engine's sort being stable: a reply that carries no
       * stamp of its own inherits its comment's, so it stays directly under
       * the question rather than jumping to the top of the thread on `""`. */
      var thread = [];
      function place(stamp, node) {
        thread.push({ stamp: stamp || "", order: thread.length, node: node });
      }

      (items || []).forEach(function (comment) {
        var item = el("div", comment.acknowledged ? "comment is-acknowledged" : "comment");
        var head = el("p", "comment-meta");
        head.appendChild(el("span", "comment-stamp", comment.stamp || ""));
        if (comment.acknowledged) head.appendChild(el("span", "comment-ack", "read"));
        /* A comment Sokrates posted on his behalf, not one he typed.
         * The server reads the disclosure sentence the relay opens with
         * (`nova_boards.is_relayed`); the ranking already demotes these,
         * and this is the same fact where he actually reads them. Symbol
         * and word together, never the arrow alone -- a reader who does
         * not know the code still reads the sentence. */
        if (comment.relayed) {
          head.appendChild(el("span", "comment-relay", "↩ relayed by Sokrates"));
        }
        item.appendChild(head);
        // Markdown since 2026-09-13: `appendRichText` renders tables, lists,
        // headings, quotes, code and the four inline marks, plus the two
        // things it already knew -- the attach line this site writes on his
        // behalf, and a fenced ```mermaid block. The reader is shared with the
        // chat on purpose: a table that drew in one thread and printed as
        // pipes in another would read as a bug in whichever he saw second.
        appendRichText(item, "comment-body", comment.text);
        /* Nova's answer to this comment, or the fact that one is coming.
         * The bridge serialises every CLI call, so a reply posted while a
         * cycle is running can be forty minutes behind -- saying nothing
         * would read as broken.
         *
         * It is a sibling of the comment it answers, not a child of it:
         * The owner, issues.md 2026-08-10, "they should be below each other
         * on the same indentation. So the comments alternates between blue
         * and green downwards." Which comment a reply belongs to is now
         * carried by the order alone, and the order is the conversation. */
        /* Every `#### Nova` block under his comment, each its own bubble.
         * There used to be one, painted from `comment.reply`, and a second
         * block appended by a cycle ended up inside it as the literal text
         * `#### Nova · 2026-08-21 16:23`. `comment.reply` is still the
         * first of them, so an old cached app.js against a new server
         * shows what it always did rather than nothing. */
        var after = [];
        var replies = comment.replies;
        if (!(replies && replies.length) && comment.reply) {
          replies = [{ author: "commentator", stamp: comment.replyStamp, text: comment.reply }];
        }
        (replies || []).forEach(function (answer) {
          var cycleReply = answer.author === "cycle";
          var reply = el("div", cycleReply ? "comment comment-reply comment-reply-cycle"
                                           : "comment comment-reply");
          var meta = el("p", "comment-meta");
          // Named as well as coloured: the colour is the glance and the
          // word is what makes it readable without knowing the code.
          meta.appendChild(el("span", "comment-who", cycleReply ? "Nova · cycle" : "Nova"));
          meta.appendChild(el("span", "comment-stamp", answer.stamp || ""));
          reply.appendChild(meta);
          // Same treatment as his own comment above: a reply that quotes
          // the image he attached should show it, not the raw line.
          appendRichText(reply, "comment-body", answer.text);
          after.push({ stamp: answer.stamp || comment.stamp, node: reply });
        });
        if (after.length) {
          // Answered. The waiting lines below are the unanswered states.
        } else if (comment.replyWaiting) {
          /* Past the server's threshold, so this is no longer a reply being
           * written in the ordinary way -- but nothing here knows why, and
           * this line used to claim it did ("Queued behind a running
           * cycle"). Replies take a parallel lane past the bridge lock
           * almost always, so that cause was usually false. Report the one
           * fact the server has -- how long it has been -- and name no
           * cause; the elapsed time is also what tells him apart a slow
           * answer from a stuck one, which the fixed sentence never could. */
          /* The three waiting lines below carry the comment's own stamp
           * rather than a time of their own, so they stay pinned directly
           * under the question they are about. They are not a turn of the
           * conversation -- they are a status on one comment, and adjacency
           * is the only thing that says which. */
          after.push({ stamp: comment.stamp, node: el("p", "comment-waiting",
            "Still working on this — " + waitedFor(comment.replyWaitingSeconds) +
            " so far. The answer appears here on its own.") });
        } else if (comment.replyPending) {
          after.push({ stamp: comment.stamp, node: el("p", "comment-waiting", "Nova is replying…") });
        } else if (comment.replyFailed) {
          /* The line used to just vanish, which reads exactly like an
           * answer that never came. A comment that got no reply is still in
           * `## New`, so the next cycle does read it. */
          after.push({ stamp: comment.stamp, node: el("p", "comment-waiting", "Couldn't answer this one — the next cycle will read it.") });
        }
        place(comment.stamp, item);
        after.forEach(function (entry) { place(entry.stamp, entry.node); });
      });

      thread.sort(function (a, b) {
        if (a.stamp === b.stamp) return a.order - b.order;
        return a.stamp < b.stamp ? -1 : 1;
      });
      thread.forEach(function (entry) { list.appendChild(entry.node); });
      // Both of these read the whole thread, not the shown slice. The
      // count on the 💬 toggle is how many comments a cycle has, which the
      // fold does not change; and a reply still being written on a folded
      // comment has to keep the poll alive, or hiding it would stop its
      // answer ever arriving on screen.
      var count = (lastItems || []).length;
      toggle.textContent = count ? "💬 " + count : "💬";
      /* A reply that lands while he is looking at the open thread is not
       * unread, and telling him it is, is the "notification for something I
       * already have open" he filed.
       *
       * the owner, `issues.md` 2026-08-26: *"When i have a journal comments
       * drawer open, i do not need notifications as i allready have it
       * open."* He screenshotted it: the drawer open, three of my replies
       * arriving over four minutes, and the chip relighting after each one.
       * Opening the drawer marks it read once, at the tap; nothing marked
       * anything read afterwards, so every later poll counted the new reply.
       *
       * Two conditions, and they are the two `setCommentsOpen`'s comment
       * already argued for rather than a loosening of it. `openedByTap` is
       * *he* opened this, not the app -- the ask auto-open and the two
       * re-assertion paths still cannot consume a reply, and the tests under
       * that comment still pin it. `!document.hidden` is the sightline the
       * flag alone does not give: a drawer he tapped open and then locked his
       * phone on keeps its chip. That is the same guard `pingAskWatching`
       * uses for the same question one feature over.
       *
       * A feed re-render builds a new drawer and `openedByTap` starts false
       * again, which is the honest reading -- what survives the render is
       * `fold.comments`, a state, and the comment above is right that a state
       * is not a sightline. */
      if (openedByTap && !document.hidden) {
        if (markRepliesRead(target.readKey, lastItems) && lastStatus) {
          renderStatus(lastStatus, null);
        }
      }
      /* The unread chip.
       *
       * the owner, same capture: *"Journals should also show if i have some
       * unread by highlightong the comment button somehow, maybe with the
       * amount of unread messages."* So the count stays what it always was --
       * how many comments the card has -- and the unread number arrives beside
       * it rather than replacing it. Replacing it would make a card he has
       * caught up on look empty.
       *
       * The chip says **"all new"** rather than repeating the count when every
       * comment on the card is unread, which is his other half of the same
       * report: *"the number for the amount of unread messages is listed twice
       * (see the two purple 3 numbers)"*. It was one number twice -- the total
       * and the unread count are equal on a card he has never opened, and
       * `has-unread` colours the whole toggle, so both read as purple and both
       * read as unread. Two identical numbers side by side cannot say which is
       * which, so one of them becomes a word. `2 new` beside `💬 5` still says
       * two different things and looks it.
       *
       * `===` and not `>=`, which is what I wrote first. The two quantities
       * are not the same kind of thing -- `count` is comments, `unread` is
       * replies -- so one comment carrying five unread replies gives 5 and 1,
       * and "all new" there would be a claim about the comments made from a
       * measurement of the replies. `5 new` beside `💬 1` is two honest
       * numbers. "all new" is only ever printed when the chip would otherwise
       * have repeated the count exactly, which is the case he reported.
       *
       * `target.readKey` and not a guard on it: every target this drawer is
       * built with carries one, so there is no drawer here without a key. A
       * null-check would have looked like defence and been dead code -- I
       * wrote one, mutated it away, and all eight tests stayed green. It was
       * `target.cycle` until entry threads existed, and those have no cycle
       * number at all -- `String(undefined)` would have filed every one of
       * them under one shared key. */
      var unread = unreadReplies(target.readKey, lastItems);
      if (unread) {
        toggle.appendChild(el("span", "comment-unread",
          unread === count ? "all new" : unread + " new"));
      }
      // `classList.toggle` with a force argument, so a repaint that clears the
      // chip clears the highlight with it.
      toggle.classList.toggle("has-unread", !!unread);
      // The chip is a number with no word next to it, which is the failure
      // `personality.md` names for the priority glyphs: if the reader has to
      // know the code to know what it says, it has not been said. Sighted
      // readers get the colour and the position; this is that sentence.
      toggle.setAttribute("aria-label",
        unread ? target.ariaLabel + " — " + unread + " unread" : target.ariaLabel);
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
      /* A tray with a screenshot in it and nothing typed is a comment. It
       * used to be one by accident -- the markdown line was *in* the box,
       * so `box.value` was non-empty -- and moving the attachments out
       * would have made "send me just this picture" hit the empty-box
       * guard and silently do nothing. */
      if (!text && !attach.count()) {
        box.focus();
        return;
      }
      var body = [text, attach.markdown()].filter(Boolean).join("\n\n");
      send.disabled = true;
      status.textContent = "saving…";
      status.className = "comment-status";
      fetch("/api/comment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(target.body(body)),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          // Only cleared once the server confirms the write -- the same
          // rule the capture box follows, for the same reason: a box that
          // wiped itself on a failure would lose what it exists to catch.
          box.value = "";
          delete drafts[target.key];
          attach.clear();
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
     * so Enter meaning "file it" costs nothing. The owner asked for this one
     * to be "a multiline text input", and his own example runs to two
     * sentences -- so Enter has to be a newline, or every paragraph break
     * in the thing he asked to be able to write would need a modifier he
     * does not have on a phone keyboard. Consistency between the two boxes
     * would be consistency against what each is for. */

    /* Opening the drawer is what marks this card's replies read -- he is
     * looking at them. Repaints the toggle so the chip clears under his
     * finger, and re-renders the header so its count does too; without the
     * second one the badge would insist on a reply that is open on screen
     * until the next poll, which is the "cycle 265 wrote no entry" complaint
     * again in miniature. `renderStatus(lastStatus, null)` re-uses the held
     * comments, so this costs no fetch. */
    function seen() {
      if (!markRepliesRead(target.readKey, lastItems)) return;
      paint(lastItems);
      if (lastStatus) renderStatus(lastStatus, null);
    }

    /* Whether he opened this drawer himself, which `paint` needs and cannot
     * work out for itself -- `aria-expanded` says open and says nothing about
     * who opened it, and that difference is the whole of the reviewer finding
     * `setCommentsOpen` documents. Closing clears it, so a drawer he shut and
     * the app later re-asserted is back to being merely open. */
    function tapped(open) { openedByTap = !!open; }

    container.appendChild(drawer);
    return { toggle: toggle, drawer: drawer, seen: seen, tapped: tapped };
  }

  /* The key a card with no cycle number files a comment under: its own
   * `date time`, off the heading the entry was written with.
   *
   * the owner, issues.md 2026-09-02: *"The retrospective needs my input, but
   * I have no ability to give it as those do not have a comment section.
   * Please make them and other special journals have a comment section like
   * the rest of the journals."*
   *
   * Returns "" when either half is missing, and the caller draws no box in
   * that case -- the same call the cycle branch has always made, for the
   * same reason: a box that has nowhere to file what he types silently
   * drops it. The server validates the shape again, so a key this builds
   * wrongly is a 400 he can see rather than a write nobody can find. */
  function entryKeyOf(entry) {
    var date = (entry && entry.date) || "";
    var time = (entry && entry.time) || "";
    if (!date || !time) return "";
    var key = date + " " + time;
    return /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/.test(key) ? key : "";
  }

  function entryTarget(key, label) {
    var name = label || "this entry";
    return {
      key: "entry:" + key,
      entry: key,
      // What the "replies I have read" store files this drawer under. A
      // separate field from `key` because that one also keys the unsent
      // draft, and the two are stored in different places for different
      // lifetimes.
      readKey: "entry:" + key,
      placeholder: "Say something about " + name + "…",
      ariaLabel: "Comment on " + name,
      body: function (text) { return { target: "entry", entry: key, text: text }; },
      pick: function (data) { return ((data && data.byEntry) || {})[key]; },
    };
  }

  function cycleTarget(cycle) {
    return {
      key: "cycle:" + cycle,
      cycle: cycle,
      readKey: String(cycle),
      placeholder: "Say something about cycle " + cycle + "…",
      ariaLabel: "Comment on cycle " + cycle,
      body: function (text) { return { cycle: cycle, text: text }; },
      pick: function (data) { return ((data && data.byCycle) || {})[String(cycle)]; },
    };
  }

  /* One card per cycle, however many entries that cycle wrote.
   *
   * the owner, on the comments board at cycle 81: "i do not like the double
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
    /* the owner, comments board at cycle 156, asking for an eight-cycle report
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

    /* No chevron. The owner, issues.md #59: "Remove the arrow that shows if
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
     * began; the PR and the board item are the settled part's.
     * `appendOutcome` is the same call the page makes, so the two cannot
     * say different things about one cycle -- with `withOutcome: false`,
     * because the pill and its qualifier are cut from the feed card. See
     * that function for the owner's ask. Everything that decides *which* part
     * these come from is unchanged and still tested through the PR badge. */
    var meta = el("div", "entry-meta");
    var stamp = [entry.date, entry.time].filter(Boolean).join(" ");
    if (stamp) meta.appendChild(el("time", "stamp", stamp));
    appendRuntime(meta, entry);
    appendOutcome(meta, settled, { withOutcome: false });
    if (meta.childNodes.length) card.appendChild(meta);

    /* The brief is drawn further down, but whether it exists decides
     * whether the heading title is drawn at all -- see `hasBrief`. The
     * `is-unsplit` fallback below counts: it fills the same slot, so a
     * card that falls back to it would otherwise carry two labels again,
     * which is the whole bug. A multi-part cycle's titles are unaffected:
     * they are the subheadings inside the drawer, where they say which
     * half you are in. */
    var briefSpans = (digestLine && digestLine.briefSpans) || entry.briefSpans;
    var unsplitSummary = (briefSpans && briefSpans.length)
      ? "" : (digestLine ? digestLine.text : firstParagraph(entry.blocks));
    if (ordered.length === 1 && entry.cycle !== null && entry.cycle !== undefined
        && cleanTitle(entry.title) && !hasBrief(digestLine, entry) && !unsplitSummary) {
      card.appendChild(el("p", "entry-title", cleanTitle(entry.title)));
    }

    /* the owner, comments board 2026-08-16: "remove the 'needs the owner' block
     * entirely. If you need something from me, it should be added in the
     * Journal card somehow and i'll answer in the comment of a journal card.
     * [...] add a new yellow block below the title or somehow higlight your
     * issue so that i see it."
     *
     * Below the title, above the brief, and yellow -- his layout, not a
     * reading of it. The card's own comment drawer is opened for it further
     * down, because an ask he cannot see the answer box for is the exact
     * failure that left idea #56 unanswered for eight cycles. */
    /* Every part's ask, not the first one's. A cycle that wrote an addendum
     * is two entries on one card, and stopping at the first match dropped
     * the second ask off the page entirely -- the server has already cut it
     * out of that part's prose, so there is nowhere else for it to appear.
     * Silently losing a question is the failure this whole change exists to
     * stop. */
    var asked = [];
    for (var ai = 0; ai < ordered.length; ai++) {
      if (ordered[ai].askSpans && ordered[ai].askSpans.length) asked.push(ordered[ai]);
    }
    /* Read here rather than forty lines down, because the ask block below
     * needs it. It was already moved up once for the same reason and the
     * comment there records why that matters: `var` hoists, so reading
     * `fold` above its assignment gets `undefined` silently and disables
     * the memory while every test still passes. */
    var fold = foldFor(entry.cycle);
    /* The yellow "Needs input" block used to be built here.
     *
     * He killed it on 2026-09-13: *"I still see the needs input boxes on the
     * journals. I do not want them as i want them as a conversation asking me
     * about it instead."* That conversation is issue #209 and it is not built
     * yet, so the question must not vanish with the box: the server cuts an
     * ask out of the entry's prose, and this was the only place it was drawn.
     * The words are kept as ordinary paragraphs -- no yellow, no label, no
     * toggle -- and go for good once #209 asks them in a thread instead.
     *
     * Held in a list rather than appended here, because `card` is built
     * further down; `var` hoisting would make an append here silently target
     * `undefined`. */
    var askLines = asked.map(function (part) {
      var line = el("p", "entry-ask-plain");
      renderSpans(line, part.askSpans);
      return line;
    });

    /* the owner, issues.md 2026-08-09: "a 2-3 line short precise Digest for
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
    if (briefSpans && briefSpans.length) {
      var brief = el("p", "entry-brief");
      renderSpans(brief, briefSpans);
      card.appendChild(brief);
    } else if (unsplitSummary) {
      /* A payload with no briefSpans, which is reachable rather than
       * theoretical: sw.js is network-first and caches /api responses, so
       * opening the app with the tailnet down after this deploy pairs the
       * new app.js with the last payload the old build served.
       *
       * `is-unsplit` restores the CSS line clamp for that card only. Without
       * it the fallback degrades to something worse than what it replaced --
       * a whole 2000-character digest line as an unclamped card title -- and
       * "degrades to exactly what it showed before" is the only thing that
       * makes a fallback worth keeping.
       *
       * Both this and the title block above read `unsplitSummary`, computed
       * once where the title decision is made -- two copies of the same
       * expression is how the title came to be drawn beside a brief in the
       * first place. */
      card.appendChild(el("p", "entry-brief is-unsplit", unsplitSummary));
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

    askLines.forEach(function (line) { card.appendChild(line); });

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
     * `fold` is read above the ask block rather than at its old declaration
     * here: `var` hoists, so the name existed and was `undefined`, and
     * passing it in silently disabled the tab memory while every test still
     * passed. It is the same object either way -- `foldFor` memoises per
     * cycle -- so that was a move, not a second one, and this is the same
     * move again for the same reason. */
    var body = el("div", "entry-parts");
    body.id = bodyId;
    appendParts(body, ordered, settled, fold);
    card.appendChild(body);

    /* One comment button per cycle, which is now simply one per card.
     *
     * A comment used to be stored keyed by cycle number alone, so an entry
     * with no number had nowhere for one to land and got no button --
     * offering it would have been a box that silently drops what he typed.
     * A retrospective is written by its own heartbeat and carries no cycle
     * number, which is how the one journal card he most wanted to answer
     * was the one card with no way to (issues.md 2026-09-02). Those file
     * under the entry's own `date time` now. The condition is still "is
     * there a key", not "is there a number". */
    var entryKey = entry.cycle === null || entry.cycle === undefined
      ? entryKeyOf(entry)
      : "";
    var commenting = null;
    if ((entry.cycle !== null && entry.cycle !== undefined) || entryKey) {
      /* Bottom right of the card rather than beside the permalink in the
       * head -- the owner, ideas.md 2026-08-10: "Move the Journal chat bubble
       * icon to the bottom right of the Journal cards."
       *
       * The foot is appended *before* renderComments, because renderComments
       * appends the drawer to the same container: build it after and the
       * drawer opens above the button that opened it. */
      var foot = el("div", "entry-foot");
      card.appendChild(foot);
      commenting = renderComments(
        card,
        entryKey
          ? entryTarget(entryKey, entry.title || "this entry")
          : cycleTarget(entry.cycle),
        comments
      );
      foot.appendChild(commenting.toggle);
    }

    /* The three setters are the only places a card changes state, so they
     * are also the only places that have to remember it -- a tap goes
     * through one of these whether it came from the card's own listener or
     * from `setExpanded` re-asserting a drawer. See `folds`. */

    /* `byTap` is the whole difference between "he opened this" and "the app
     * did", and marking replies read may only ever follow the first.
     *
     * Three of the five callers here are the app re-asserting a drawer, not
     * him touching one, and each of them would silently eat an unread reply:
     * the ask auto-open two blocks down force-opens the drawer on the first
     * render of a card carrying a question, `setExpanded` re-asserts it on
     * every repaint, and `fold.comments` survives the card being collapsed
     * and the feed being rebuilt. So a reply landing on a thread he happens
     * to have left open -- which is the case this feature is most for --
     * would be marked read by the 30-second poll before it ever painted.
     * Both found by the reviewer; my own comment here previously argued the
     * opposite, on the grounds that an open drawer is on his screen. It is
     * not: open is a state, not a sightline.
     *
     * That still holds and the two tests under it still pass. What changed on
     * 2026-08-26 is what happens *after* a tap: `commenting.tapped` records
     * that this drawer is one he opened himself, so the replies that land in
     * it while he is looking do not come back as unread. See the paint-time
     * branch in `commentDrawer` -- and note the flag is only ever set here,
     * on the `byTap` path, so the three app-driven callers above are exactly
     * as unable to consume a reply as they were. */
    function setCommentsOpen(open, byTap) {
      if (!commenting) return;
      fold.comments = open;
      card.classList.toggle("is-commenting", open);
      commenting.toggle.setAttribute("aria-expanded", open ? "true" : "false");
      if (open && byTap) commenting.seen();
      if (byTap) commenting.tapped(open);
    }
    /* An ask opens its own card's drawer, once. `fold.askSeen` keeps it from
     * reopening on every render within a load; `askAlreadyOpened` keeps it
     * from reopening on the next load, which is the bug he reported -- see
     * `ASK_OPENED_KEY`. A box that reopens itself is the pinned-open drawer
     * this replaced.
     *
     * The mark is written where the drawer is actually opened, not where the
     * ask is drawn: a card whose drawer this never opened has nothing to
     * remember, and marking it would silently spend the one auto-open it is
     * owed. */
    var askKey = entry.cycle === null || entry.cycle === undefined ? null : String(entry.cycle);
    if (asked.length && !fold.askSeen && !askAlreadyOpened(askKey)) {
      fold.askSeen = true;
      markAskOpened(askKey);
      fold.comments = true;
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

    /* the owner, issues.md 2026-08-09: "i want to click anywhere on it to
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
        setCommentsOpen(commenting.toggle.getAttribute("aria-expanded") !== "true", true);
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
    // Emptied in place, never replaced: `beats.js` and `home.js` hold this
    // same array, and a new one would leave their timers where nothing clears
    // them.
    livePolls.length = 0;
  }

  /* The cycle page lives in `cycle.js` (issue #233). Bound here, where the
   * block used to sit, after `richtext.js` and every other name it borrows.
   * `takeBodyId` hands over the counter itself rather than its value. The
   * guard is for a cached tab that has `app.js` from this build and no
   * `cycle.js` yet. */
  var cycleModule = window.novaCycle ? window.novaCycle({
    cycleTarget: cycleTarget,
    el: el,
    outcomeClass: outcomeClass,
    renderBlocks: renderBlocks,
    renderComments: renderComments,
    renderSpans: renderSpans,
    shortOutcome: shortOutcome,
    takeBodyId: function () { return nextBodyId++; },
  }) : {};
  var appendOutcome = cycleModule.appendOutcome;
  var appendParts = cycleModule.appendParts;
  var appendRuntime = cycleModule.appendRuntime;
  var cleanTitle = cycleModule.cleanTitle;
  var hasBrief = cycleModule.hasBrief;
  var renderCyclePage = cycleModule.renderCyclePage;
  var settledPart = cycleModule.settledPart;

  /* The recap card, drawn on the landing page.
   *
   * The owner, capture 2026-09-04, 🔴 Immediately: "I want a stick Journal
   * card at the top that summarizes the last 12 hours. Keep it short as I
   * just want this to quickly glance over what has been done. ... max 5-6
   * bullets as many cycles work on the same problem/project."
   *
   * It draws what the server says and computes nothing: the bullets, the
   * time it was written and whether that is stale all come down in the
   * payload, because a number on the screen should have one definition
   * and one test.
   *
   * A recap that is missing draws nothing at all rather than an empty
   * card. He asked for a glance, and an empty box is a thing to read.
   */
  /* The twelve-hour summary is the landing page's card and nothing else's,
   * his ask 2026-09-13: *"Remove the 12 hour summary from all pages than the
   * homepage."* `renderHome` draws it from its own payload, so the feed's
   * copy -- its own `/api/recap` fetch, a cache and a placer -- had nothing
   * left that could reach it. */

  /* A bullet's text, with whatever it points at as a real tap target.
   *
   * His capture 2026-09-04 12:29, on the card built the cycle before:
   * *"the bullet that mentions the tailnet start page has been created, i
   * immediately want to check it out but I'm left without a url or any
   * clickable link so i have to search for it. It should be very easy for
   * me to click the link. You can look at this board as a 'news board' or
   * your place to market to me what you have created."*
   *
   * The split into linked and unlinked runs is `nova_recap.link_parts` --
   * server-side, one definition, one test, and this never sees markdown.
   * `fallback` is the same sentence as one string and is what draws when
   * an older payload has no `parts` at all: a card that renders without
   * links is a small loss, a card that renders empty is the whole thing.
   *
   * An off-site link opens in a new tab because the page it leaves is the
   * one he was reading; a path on this site navigates in place, which is
   * what every other link here does. */
  function paintRecapParts(host, parts, fallback) {
    if (!parts || !parts.length) {
      if (fallback) host.appendChild(document.createTextNode(fallback));
      return;
    }
    parts.forEach(function (part) {
      if (!part || !part.text) return;
      if (!part.href) {
        host.appendChild(document.createTextNode(part.text));
        return;
      }
      var link = el("a", "recap-link", part.text);
      link.href = part.href;
      if (/^https?:/i.test(part.href)) {
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
      host.appendChild(link);
    });
  }

  /* Collapsed unless he opens it, and the state does not survive the page.
   *
   * His ask, 2026-09-08: *"i want the 12 hours summary for the journals to
   * be collapsable and default collapsed as it takes up a lot of space."*
   * Default-collapsed is the whole of it -- remembering that he opened it
   * once would put a screenful of summary back above the feed on the next
   * load, which is the thing he is asking to get rid of. Opening it is one
   * tap, and the heading says what is inside. */
  var recapFold = { open: false };

  /* The same card, open by default, on the landing page only.
   *
   * A deliberate reversal rather than an inconsistency: on the feed the
   * recap sits *above* a screenful of entries, which is what he asked to
   * get out of his way; on `/` it is the content, and a landing page whose
   * summary is folded shut is a page that says nothing. Two fold objects
   * rather than one flag, so each page also remembers what he last did to
   * it *there* -- opening it on the landing page must not reopen it above
   * the feed he folded it away from. */
  var homeRecapFold = { open: true };

  function renderRecap(recap, fold) {
    if (!recap || !recap.bullets || !recap.bullets.length) return null;
    fold = fold || recapFold;
    var card = el("section", "recap" + (fold.open ? "" : " recap--shut"));
    /* The whole head is the control, not a chevron beside it: on a phone
     * the title is the thing under his thumb, and a 44px target he has to
     * aim for beside it is a target he misses. */
    var head = el("button", "recap-head");
    head.type = "button";
    head.setAttribute("aria-expanded", fold.open ? "true" : "false");
    head.addEventListener("click", function () {
      fold.open = !fold.open;
      card.classList.toggle("recap--shut", !fold.open);
      head.setAttribute("aria-expanded", fold.open ? "true" : "false");
    });
    head.appendChild(el("h2", "recap-title", "Last 12 hours"));
    var stampText = recap.writtenLabel
      ? "as of " + recap.writtenLabel
      : "written at an unknown time";
    if (recap.cycles) stampText += " · cycles " + recap.cycles;
    var stamp = el("span", "recap-stamp", stampText);
    /* The one thing this card must never do is read as current when it is
     * not. The stamp is always there; this only marks the case where the
     * gap is big enough that the window it describes has moved. */
    if (recap.stale) stamp.className = "recap-stamp stale";
    head.appendChild(stamp);
    card.appendChild(head);
    var body = el("div", "recap-body");
    var list = el("ul", "recap-list");
    recap.bullets.forEach(function (bullet) {
      var item = el("li", "recap-item");
      if (bullet.lead) {
        var lead = el("strong", "recap-lead");
        paintRecapParts(lead, bullet.leadParts, bullet.lead);
        item.appendChild(lead);
        if (bullet.text) item.appendChild(document.createTextNode(" "));
      }
      paintRecapParts(item, bullet.parts, bullet.text);
      list.appendChild(item);
    });
    body.appendChild(list);
    if (recap.stale) {
      body.appendChild(el("p", "recap-note",
        "This was written more than " + recap.staleAfterHours
        + " hours ago, so newer cycles are not in it — the feed below is."));
    }
    card.appendChild(body);
    return card;
  }

  /* The journal feed's kept thread and the cards already built for it,
   * beside the sig each was built from (issue #233, step 8). */
  var journalThread = null;
  var journalCards = {};

  function render(journal, digest, comments) {
    /* Drop an answer to a query he has already typed past. `load()`
     * guards against a different *view* and nothing else, and the broader,
     * older query does more work, so finishing last is the ordinary case;
     * without this the feed reverts to the shorter word's results, count
     * line and all. `runBoardSearch` has had the same guard since it
     * shipped -- *displaying* the answered query is not *checking* it.
     * Before `stopPolling`, deliberately: below that line the tab would be
     * left with no poll timer. Every path that changes `journalQuery`
     * starts a fresh `load`. */
    var live = journalQuery.trim().toLowerCase() || null;
    if (routedCycle(window.location.pathname) === null
        && (journal.query || null) !== live) return;
    stopPolling();
    markNav();
    // What the page is now showing, so the poll below can tell "nothing
    // changed" from "changed while he was typing".
    renderedVersion = (journal && journal.version) || null;
    renderedComments = JSON.stringify(comments);
    // Set from the map, not from a payload: `setChatAnswered` has already
    // run for this render, and the poll compares the same normalised form.
    renderedChat = JSON.stringify(chatAnsweredCycles);
    var commentsByCycle = (comments && comments.byCycle) || {};
    var commentsByEntry = (comments && comments.byEntry) || {};

    // `null` and not the empty object when the fetch itself failed: "no
    // comments" and "no answer about the comments" are different, and only
    // the first one licenses the header to say he owes a reply.
    renderStatus(journal.status || {}, comments ? commentsByCycle : null);

    var byCycle = {};
    ((digest && digest.lines) || []).forEach(function (line) {
      byCycle[line.cycle] = line;
    });

    var wanted = routedCycle(window.location.pathname);
    var entries = journal.entries || [];
    /* The query the answer on screen was actually built from, not the one
     * in the box. He types faster than the round trip, so the two differ
     * for a couple of hundred milliseconds on every keystroke, and the
     * count is the one thing on this page that would be a lie rather than
     * merely stale if it read the box: "3 entries mention 'billing'"
     * under the results for "billin". The server echoes `query` back for
     * exactly this. */
    var answered = journal.query || null;
    if (journalSearchCount) {
      journalSearchCount.hidden = !answered;
      if (answered) {
        /* His own capitalisation, not the server's. The query comes back
         * lower-cased because that is what it was matched with, so a line
         * built from it tells him `TAILSCALE` found "tailscale" -- and the
         * guard at the top of this function has already established that
         * the box and the answer are the same word, so there is nothing
         * left for the echoed copy to decide. */
        var typed = (journalSearchInput && journalSearchInput.value.trim()) || answered;
        var found = typeof journal.total === "number" ? journal.total : entries.length;
        journalSearchCount.textContent = found === 0
          ? "No entry mentions “" + typed + "”"
          : found === 1
            ? "1 entry mentions “" + typed + "”"
            : found + " entries mention “" + typed + "”";
      }
    }
    if (wanted !== null) {
      entries = entries.filter(function (entry) {
        return entry.cycle === wanted;
      });
    }

    /* The comment filter. Applied here with the other feed filters so the
     * count under the search box and the cards below it always agree. */
    var commentFiltered = journalFilter !== "all" && wanted === null;
    if (commentFiltered) {
      /* Against the held list, never against the live read marks. Expanding a
       * card's comments writes its read mark and repaints; judging membership
       * here would drop the card out from under his thumb, which is the
       * second half of what he reported. `journalFilterCycles` carries the
       * whole reason. */
      var keep = journalFilterCycles();
      entries = entries.filter(function (entry) {
        if (entry.cycle === null || entry.cycle === undefined) return false;
        return keep.indexOf(entry.cycle) !== -1;
      });
    }

    /* `/asks`: the cards that asked him something and have not been
     * answered.
     *
     * The server sends every card carrying an ask and deliberately does
     * not decide which are still open -- an ask is answered when he has
     * commented on that card, and comments live in a different document
     * with its own cache, so folding it in there would leave the page
     * claiming he had not replied until the journal cache next rebuilt.
     * This is the same intersection `openAsks` makes for the header, done
     * against the same payload, so the count in the header and the number
     * of cards here can never disagree.
     *
     * A failed comments read leaves `commentsByCycle` empty and every ask
     * therefore reads as open. That is the safe direction -- showing an
     * answered question costs him a scroll, hiding an open one costs him
     * the question -- and the "Comments could not be loaded" line below
     * already says so on screen. */
    var filtered = routedAsks(window.location.pathname);
    if (filtered) {
      entries = entries.filter(function (entry) {
        return !askAnswered(entry.cycle, commentsByCycle);
      });
    }

    /* `/replies`: the cards carrying a reply he has not read.
     *
     * No filter of its own -- the server was handed the cycle numbers and
     * every entry that came back is one of them. The reason it needs a flag
     * anyway is the two things `filtered` also buys `/asks`: no recap card,
     * and no pager. It is deliberately not folded into `filtered`, because
     * the line under that flag says "N entries are waiting on you" and these
     * are not asks. */
    var repliesOnly = routedReplies(window.location.pathname);

    /* One card per cycle, newest cycle first.
     *
     * A cycle's entries are usually adjacent on the wire but are not
     * required to be -- an addendum is written whenever the cycle that owns
     * it comes back, which can be after the next cycle has already filed
     * its own entry. So the group takes the position of the cycle's newest
     * part, and later parts join it wherever they turn up.
     *
     * An entry with no cycle number (the owner's own notes) is its own group:
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


    /* Under Preact the cards go through `thread.js` (issue #233, step 8):
     * a card whose content did not change keeps the node on screen, so a
     * poll that adds one entry -- or a comment landing on one card -- stops
     * rebuilding every card and shutting the drawers he had open. A card is
     * keyed by its cycle, or by its date and time when it has none, and
     * signed with everything `renderEntry` reads; the node is kept beside
     * its sig, so building is skipped as well as redrawing. Where Preact
     * did not load, `thread` is the feed and `add` appends. */
    var rows = window.novaThread ? [] : null;
    var thread = rows
      ? (journalThread && journalThread.parentNode === feed
          ? journalThread : el("div", "journal-thread"))
      : feed;
    if (rows) journalThread = thread;
    Array.prototype.slice.call(feed.childNodes).forEach(function (n) {
      if (n !== thread) feed.removeChild(n);
    });
    var builtCards = {};
    var add = function (node, key, sig) {
      if (rows) rows.push({ node: node, key: key, sig: sig });
      else thread.appendChild(node);
    };
    // On every path out of here after the clear above, the single-cycle
    // page's early return included.
    var flush = function () {
      journalCards = builtCards;
      if (!rows) return;
      window.novaThread.render(thread, rows);
      if (thread.parentNode !== feed) feed.appendChild(thread);
    };
    /* The twelve-hour summary belongs to the landing page alone, his ask
     * 2026-09-13: *"Remove the 12 hour summary from all pages than the
     * homepage."* `renderHome` draws its own copy and this page draws none.
     * The card sits above the search box, outside the feed, so the feed's
     * own clear never reached it. Taken down by hand. */
    var stale = document.querySelector(".recap");
    if (stale) stale.remove();
    /* A comments failure should cost the bubbles, not the feed -- but
     * tolerating it silently made a 502 look like "nobody has commented",
     * a more convincing lie than "this did not load". `null` comes only
     * from that catch: the endpoint answers with an object. */
    if (journalFilter !== "all" && !groups.length) {
      add(el("p", "empty", journalFilter === "unread"
        ? "No journal card has a reply you have not read."
        : "No journal card carries a comment."), "filter-empty", journalFilter);
    }
    if (comments === null) {
      add(el("p", "empty", "Comments could not be loaded — the entries below are complete, the replies are not."), "comments-failed", 1);
    }
    if (filtered) {
      var backAll = el("a", "back", "← all entries");
      backAll.href = "/journal";
      add(backAll, "back-all", 1);
      add(el("p", "empty", entries.length === 0
        ? "Nothing is waiting on you."
        : entries.length === 1
          ? "1 entry is waiting on you."
          : entries.length + " entries are waiting on you."), "asks-count", entries.length);
    }
    if (repliesOnly) {
      var backFeed = el("a", "back", "← all entries");
      backFeed.href = "/journal";
      add(backFeed, "back-feed", 1);
      /* Counted in cards, not in replies. The pill above counts replies,
       * because that is the number that arrived; this page is a list of
       * cards, and saying "7" over three of them is the badge pointing at a
       * number the screen does not contain -- which is the complaint the
       * pill has already been rebuilt for once. */
      /* A failed comments read is its own line rather than a count of zero.
       * This page is the one place on the site where that failure is total:
       * the set of cards is computed from the comments payload, so without
       * it there is no list -- and "No unread replies" would be the page
       * answering a question it could not read. */
      add(el("p", "empty", comments === null
        ? "Could not tell which replies are unread — this list is built from the replies payload, and it did not load."
        : entries.length === 0
          ? "No unread replies."
          : entries.length === 1
            ? "1 card has replies you have not read."
            : entries.length + " cards have replies you have not read."),
        "replies-count", comments === null ? NaN : entries.length);
    }
    if (wanted !== null) {
      var back = el("a", "back", "← all cycles");
      back.href = "/journal";
      add(back, "back-cycle", wanted);
      if (!entries.length) add(el("p", "empty", "No entry for cycle " + wanted + "."), "cycle-empty", wanted);
    }
    if (wanted !== null) {
      if (entries.length) {
        /* NaN, deliberately: this page is one card built from the whole
         * window and it has no cheap identity to compare, so it is drawn
         * fresh every time rather than kept on a sig that could be wrong. */
        add(renderCyclePage(wanted, entries, byCycle[wanted],
          commentsByCycle[String(wanted)]), "cycle-page", NaN);
      }
      flush();
      return;
    }
    /* The hole in the record, marked where it happened (#72) -- put back
     * where he was already looking rather than summarised in a counter.
     * The server decides what counts as missing; this only decides where.
     * Filling in every number between two cards from the client's own
     * arithmetic would invent gaps for his own notes, which have no cycle
     * number to be missing. The feed is not sorted by cycle -- a card
     * takes the position of its cycle's *newest* part -- so the invariant
     * is the one a reader can check by scrolling: **a hole is never drawn
     * above a card newer than it.** Each is anchored under the *last* card
     * newer than the hole, not the numerically smallest, which in a
     * scrambled feed can still have two newer cards below it. Drawn only
     * when the window also holds a card older than the hole: a gap running
     * off either end belongs to entries nobody has loaded yet. */
    var missing = {};
    /* Not on `/asks`. A hole is drawn between the two cards it sits
     * between, and on a filtered feed the cards either side of it are not
     * adjacent in the record -- so every real gap inside the range would
     * be redrawn here, in a view whose whole point is that it is short.
     * The gap is not wrong, it is just answering a question this page is
     * not asking. */
    (!filtered && journal.status && journal.status.missingCycles || []).forEach(function (n) {
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
      /* `parts` is newest-first and `renderEntry` reads it reversed, so the
       * card's identity comes from the *last* element -- the same one it
       * uses for the anchor and the title. Keying off `parts[0]` here would
       * hand a two-part entry the wrong thread. */
      var head = parts[parts.length - 1];
      var thread = cycle === null || cycle === undefined
        ? commentsByEntry[entryKeyOf(head)]
        : commentsByCycle[String(cycle)];
      var key = cycle === null || cycle === undefined
        ? "e" + entryKeyOf(head) + "." + index : "c" + cycle;
      var sig = JSON.stringify([parts, byCycle[cycle] || null, thread || null]);
      // `rows &&`: where Preact did not load the feed is rebuilt as it
      // always was and `fold` restores the card's open state. Keeping a
      // node there would leave two mechanisms doing the same job.
      var built = rows ? journalCards[key] : null;
      var node = built && built.sig === sig
        ? built.node : renderEntry(parts, byCycle[cycle], thread);
      if (rows) builtCards[key] = { sig: sig, node: node };
      add(node, key, sig);
      if (!markers[index]) return;
      var gap = markers[index].sort(function (a, b) { return a - b; });
      add(el("p", "cycle-gap", gap.length === 1
        ? "Cycle " + gap[0] + " ran and wrote no entry"
        : "Cycles " + gap.join(", ") + " ran and wrote no entry"),
        "gap" + index, gap.join(","));
    });

    /* `total` is the whole corpus, `entries.length` is what came back in
     * this window, so the pager disappears on its own at the last page and
     * never appears at all on a server that does not paginate. */
    var total = journal.total;
    /* `!filtered`: `/asks` sends no window, so there is nothing more to
     * fetch -- and `total` is the number of asks the server found while
     * `entries` is the ones he has not answered, so the two differ by
     * exactly the answered ones and the pager would otherwise be drawn
     * permanently, offering to load entries that are already here. */
    /* `!commentFiltered` for the reason `!filtered` is here, and it is the
     * bug he reported: the server was handed the exact cycle numbers, so
     * there is nothing older to fetch -- but `total` is the whole corpus, so
     * the condition held forever, `loadWhenScrolledTo` clicked the pager the
     * moment it intersected, and on a feed with no cards it always does. */
    if (wanted === null && !filtered && !repliesOnly && !commentFiltered
        && typeof total === "number" && entries.length < total) {
      // A search is not a window onto the newest entries, so "older" is
      // the wrong word for what the next twenty are -- they are the next
      // twenty matches, and they can be from any month.
      var more = el("button", "more",
        answered ? "Show more matches" : "Show older entries");
      more.type = "button";
      more.addEventListener("click", function () {
        more.disabled = true;
        more.textContent = "Loading…";
        windowSize += PAGE;
        load();
      });
      // NaN so the pager is always the fresh node the watcher below is
      // pointed at, the same reason the Notes page's pager passes it.
      add(more, "more", NaN);
      loadWhenScrolledTo(more);
    }
    flush();
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
  /* `opts.poll` marks a request that must not be answered from the worker's
   * cache.
   *
   * His report, 2026-09-09: *"when i send you a message they dissapears
   * along with the spinner after 2sec and i have to close and reopen the
   * chat to see it."*
   *
   * That is mine, from the day before. The thread became cache-first so a
   * reopen paints instantly (#898) -- and the rule I wrote was "cache unless
   * the request carries `If-None-Match`", on the reasoning that the page's
   * poll always carries one. The dock's poll does not: `fetchPage` is a
   * plain `fetch`. So every four-second poll was answered from bytes taken
   * before he pressed Send, and the repaint wiped the message and the
   * loader off his screen until he closed the dock and opened it again.
   *
   * The cache-first rule was only ever meant for the cold load, where the
   * page has nothing. This header is what says "this is not that". */
  function fetchPage(url, opts) {
    var init = (opts && opts.poll) ? { headers: { "X-Nova-Poll": "1" } } : undefined;
    return fetch(url, init).then(function (r) {
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
        // the owner reported, inverted onto the banner meant to explain it.
        lastPayload[key] = body;
        if (isReplayed(r) && body && body.status) {
          return shallow(body, { status: shallow(body.status, { replayed: true }) });
        }
        return body;
      });
    });
  }

  /* ---- Searching the journal -----------------------------------------
   *
   * The owner, issues.md 2026-08-25: "I want to be able to search through
   * journals. Give me a button or a input field somewhere."
   *
   * An input field rather than a button, and the same shape as the one
   * already on the two board pages, because that is the control he has
   * been using there for a week -- a second idiom for the same act would
   * be a thing to learn rather than a thing to use.
   *
   * The box lives *outside* `<main id="feed">`, next to the capture
   * composer, and is built once and never rebuilt. `render` empties the
   * feed on every paint -- the 30-second poll, a new entry arriving, a
   * tap on the pager -- so a box inside it would lose the caret and the
   * keyboard mid-word, which is the same failure the `drafts` and `folds`
   * stores above exist to prevent for the cards.
   *
   * Matching happens on the server (`nova_site.journal_page`): a cold
   * load holds twenty of 400-odd entries and none of their prose, so a
   * filter in the page could only ever search what is already on screen.
   */
  var journalQuery = "";
  var journalSearchTimer = null;
  var journalSearchNode = null;
  var journalSearchInput = null;
  var journalSearchCount = null;

  /* The journal's comment filter -- his ask, 2026-09-13: *"make a new filter
   * button next to the search button that contains filters that we can use on
   * the journals, so journals with a comment is a filter, journals with unread
   * comments is a sub filter on that again and it should light up when i have
   * unread comments."*
   *
   * "all" | "comments" | "unread", in this browser only. It replaces two
   * things at once: the header's unread-reply pill, and the `/replies` route
   * behind it -- both were a count somewhere else pointing at the page you are
   * already on. */

  /* The cycles the filter is asking the server for, and the reason this is
   * held rather than recomputed on every paint.
   *
   * His report, 2026-09-13: *"The unread comments filter on the Journal is
   * buggy. It takes a long time to load and also it shows a loading... Text
   * all the time. Also, when i expand the comments of the unread comments it
   * vanishes. I think when i open them they become read and therefore not
   * part of the filter anymore so they vanishes."*
   *
   * Both halves are this list. It used to be no list at all: the filter ran
   * client-side over whatever `?limit=windowSize` had returned, so it could
   * only ever hide cards inside the newest twenty. Measured against the live
   * pod on 2026-09-13, the newest cycle carrying any comment is 1415 and the
   * newest written is 1522 -- so 107 cards in a row match nothing, the feed
   * comes back empty, the pager is therefore the only node in the viewport,
   * and `loadWhenScrolledTo` clicks it the moment it intersects. It grows the
   * window by twenty, repaints empty, and intersects again. That is the
   * "loading… all the time": an auto-pager walking the whole archive twenty
   * at a time, 102KB at `limit=20` and 626KB by the time it reaches 1415.
   *
   * `/replies` never had that problem because it hands the server the cycle
   * numbers (`?cycles=`) instead of a window. This filter replaced that route
   * and did not inherit the mechanism; now it does.
   *
   * And holding the list is the second half. Recomputed live, opening a card
   * writes its read mark, `unreadOn` goes empty, and the next paint -- which
   * the tap itself triggers -- drops the card out from under his thumb. The
   * set is frozen when he picks the filter and only ever grows after that, so
   * a reply arriving while he reads still turns up and a card he has just
   * opened stays where it was until he changes the filter or leaves. */
  var journalFilterAsked = null;

  function journalFilterCycles() {
    var live = [];
    Object.keys(lastCommentsByCycle || {}).forEach(function (key) {
      var items = lastCommentsByCycle[key];
      if (!items || !items.length) return;
      if (journalFilter === "unread" && !unreadOn(key, items).length) return;
      var cycle = parseInt(key, 10);
      if (!isNaN(cycle)) live.push(cycle);
    });
    if (journalFilterAsked === null) journalFilterAsked = [];
    live.forEach(function (cycle) {
      if (journalFilterAsked.indexOf(cycle) === -1) journalFilterAsked.push(cycle);
    });
    /* Newest first, so the URL is stable between two fetches that found the
     * same cards rather than varying with `Object.keys` order -- the same
     * reason `unreadSummary` sorts the list it sends to `/replies`. */
    journalFilterAsked.sort(function (a, b) { return b - a; });
    return journalFilterAsked;
  }

  /* The dot on the button. Drawn from the same read marks the card chips use,
   * so it goes out as he reads rather than on a tap. */
  function paintFilterState() {
    if (!journalFilterButton) return;
    var unread = unreadSummary(lastCommentsByCycle);
    journalFilterButton.classList.toggle("has-unread", !!unread.count);
    journalFilterButton.classList.toggle("is-on", journalFilter !== "all");
    journalFilterButton.setAttribute("aria-label", journalFilter === "all"
      ? (unread.count ? "Filter the journal — " + unread.count + " unread replies" : "Filter the journal")
      : "Filter the journal — " + (journalFilter === "unread" ? "unread replies only" : "cards with comments"));
    if (!journalFilterNode) return;
    [].forEach.call(journalFilterNode.querySelectorAll(".journal-filter-option"), function (option) {
      var on = option.dataset.filter === journalFilter;
      option.setAttribute("aria-checked", on ? "true" : "false");
    });
    var unreadOption = journalFilterNode.querySelector('[data-filter="unread"]');
    if (unreadOption) unreadOption.classList.toggle("has-unread", !!unread.count);
  }

  function buildJournalFilter() {
    var wrap = el("div", "journal-filter");
    var button = el("button", "journal-filter-toggle", "\u2630");
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-haspopup", "true");

    var menu = el("div", "journal-filter-menu");
    menu.hidden = true;
    menu.setAttribute("role", "radiogroup");
    menu.setAttribute("aria-label", "Filter the journal");
    /* Three rows and the third is indented, because "unread" is a narrowing
     * of "with comments" rather than a third peer -- his words: "a sub filter
     * on that again". */
    [["all", "All journals", ""],
     ["comments", "With comments", ""],
     ["unread", "Unread comments", "journal-filter-sub"]].forEach(function (spec) {
      var option = el("button", "journal-filter-option " + spec[2], spec[1]);
      option.type = "button";
      option.setAttribute("role", "radio");
      option.dataset.filter = spec[0];
      option.addEventListener("click", function () {
        journalFilter = spec[0];
        /* A fresh pick asks the question again. Held only for as long as one
         * filter is on -- see `journalFilterCycles`. */
        journalFilterAsked = null;
        menu.hidden = true;
        button.setAttribute("aria-expanded", "false");
        paintFilterState();
        windowSize = PAGE;
        load();
      });
      menu.appendChild(option);
    });

    button.addEventListener("click", function () {
      var opening = menu.hidden;
      menu.hidden = !opening;
      button.setAttribute("aria-expanded", opening ? "true" : "false");
    });
    /* A tap anywhere else shuts it, the same way every other popover on this
     * page behaves. */
    document.addEventListener("click", function (event) {
      if (menu.hidden) return;
      if (wrap.contains(event.target)) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
    });

    wrap.appendChild(button);
    wrap.appendChild(menu);
    journalFilterNode = menu;
    journalFilterButton = button;
    paintFilterState();
    return wrap;
  }

  function buildJournalSearch() {
    var box = el("section", "journal-search");
    box.id = "journal-search";
    var row = el("div", "journal-search-row");

    /* The box is a magnifying glass until he asks for it -- his ask,
     * 2026-09-08: *"that search input should be collapsed aswell to a
     * button with a search icon in it. So when i click that button, the
     * search input appears with a sliding effect both in and out."*
     *
     * The input is never removed or rebuilt, only slid: it holds the query,
     * the caret and the debounce timer, and the feed repaints under it every
     * thirty seconds. Rebuilding it on each open is the same class of bug
     * the comment on this whole section is about. The slide is `max-width`
     * on the input, which is what lets the glass sit still while the field
     * grows out of it. */
    var toggle = el("button", "journal-search-toggle", "\u2315");
    toggle.type = "button";
    toggle.setAttribute("aria-label", "Search the journal");
    toggle.setAttribute("aria-expanded", "false");
    toggle.setAttribute("aria-controls", "journal-search-input");

    var input = document.createElement("input");
    input.type = "search";
    input.id = "journal-search-input";
    input.className = "journal-search-input";
    input.placeholder = "Search the journal";
    input.setAttribute("aria-label", "Search the journal");
    row.appendChild(input);
    /* On the right, after the field -- his ask, 2026-09-08. It is the same
     * hand that reaches for Send in the composer, and the field grows away
     * from it towards the left edge rather than pushing it across the row. */
    row.appendChild(toggle);
    /* Next to the search button, his ask. */
    row.appendChild(buildJournalFilter());

    // Built whether or not there is anything to clear and hidden rather
    // than absent, for the reason the board's clear button carries:
    // removing it on the last keystroke moves the caret's own neighbour
    // out from under his thumb mid-edit.
    box.appendChild(row);

    /* Shut, and shut is also the state the clear button belongs to: an ×
     * floating beside a magnifying glass with no field between them is a
     * control for something that is not on screen. */
    box.classList.add("journal-search--shut");
    /* One button doing both jobs -- his ask, 2026-09-08: *"make it turn
     * into the x button when the input is open to make the input close."*
     * The separate clear × is gone with it: two ×s side by side, one
     * emptying the field and one closing it, is a choice nobody wants to
     * make on a phone. Closing empties it anyway. */
    toggle.addEventListener("click", function () {
      var opening = box.classList.contains("journal-search--shut");
      box.classList.toggle("journal-search--shut", !opening);
      toggle.setAttribute("aria-expanded", opening ? "true" : "false");
      toggle.textContent = opening ? "\u00D7" : "\u2315";
      toggle.setAttribute("aria-label",
        opening ? "Close the search" : "Search the journal");
      if (opening) {
        // Synchronous, inside the tap, so the phone keyboard comes up with
        // it rather than needing a second tap on the field.
        input.focus();
        return;
      }
      /* Closing throws the query away rather than hiding it. A filtered
       * feed under a collapsed box is a page silently showing three of four
       * hundred entries with nothing on screen saying why. */
      if (journalQuery) {
        journalQuery = "";
        input.value = "";
        if (journalSearchTimer) clearTimeout(journalSearchTimer);
        windowSize = PAGE;
        load();
      }
    });

    // `role="status"` so the count is announced when it changes rather
    // than only being visible -- the result of a search is a number, and
    // the number is the whole answer when it is zero.
    var count = el("p", "journal-search-count");
    count.setAttribute("role", "status");
    count.hidden = true;
    box.appendChild(count);

    input.addEventListener("input", function () {
      journalQuery = input.value;
      if (journalSearchTimer) clearTimeout(journalSearchTimer);
      // The same 200ms the board search waits, and for the same reason:
      // a request per keystroke against a 400-entry substring scan, from
      // a phone, is a queue of answers he has already typed past.
      journalSearchTimer = setTimeout(function () {
        journalSearchTimer = null;
        windowSize = PAGE;
        load();
      }, 200);
    });

    journalSearchInput = input;
    journalSearchCount = count;
    return box;
  }

  /* Shown on the journal feed and nowhere else. Called from `markNav`,
   * which every view's renderer already calls, so a page that knows
   * nothing about this box still hides it -- adding a line to each of the
   * eleven renderers would have meant the twelfth one forgetting. A deep
   * link (`/cycle/49`) hides it too: that URL asks the server for one
   * entry by number, so there is no window for a query to narrow. */
  function setJournalSearchVisible(on) {
    if (!journalSearchNode) {
      if (!on) return;
      journalSearchNode = buildJournalSearch();
      feed.parentNode.insertBefore(journalSearchNode, feed);
    }
    journalSearchNode.hidden = !on;
  }

  /* A deep link asks for its own cycle by number rather than for a window,
   * because the entry it wants is usually older than the first page and the
   * page has no way to know how far back that is. */
  function journalUrl() {
    var wanted = routedCycle(window.location.pathname);
    if (wanted !== null) return "/api/journal?cycle=" + wanted;
    if (routedAsks(window.location.pathname)) return "/api/journal?asks=1";
    /* `/replies` asks for named cycles, and the names come from this
     * browser: which replies he has seen lives in `localStorage`, so the
     * server cannot compute the set and the page has to send it. It is
     * `unreadSummary`'s own list, so the cards on this page and the count
     * on the pill can never disagree about which cards those are.
     *
     * `haveComments` is false until the comments payload lands, which is
     * why `fetchAll` waits for it on this route and only on this route --
     * an empty list here would ask for nothing and draw an empty page over
     * a mailbox that has three replies in it. */
    if (routedReplies(window.location.pathname)) {
      return "/api/journal?cycles="
        + unreadSummary(lastCommentsByCycle).cycles.join(",");
    }
    var q = journalQuery.trim();
    /* A comment filter asks for its cards by number, the same way `/replies`
     * does and for the same reason: the matches are scattered across the
     * whole archive, so a window onto the newest twenty cannot find them.
     * See `journalFilterCycles` for what that cost him.
     *
     * A typed query wins, because a search is already a specific act and its
     * result set is small enough for the filter below to narrow in the page.
     * The server can do one or the other, not both. */
    if (journalFilter !== "all" && !q) {
      return "/api/journal?cycles=" + journalFilterCycles().join(",");
    }
    var url = "/api/journal?limit=" + windowSize;
    if (q) url += "&q=" + encodeURIComponent(q);
    return url;
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
    /* No digest on `/asks`, for the same reason a search gets none:
     * `/api/digest?limit=N` resolves its window out of the *newest* N
     * cycles, and the asks are scattered across the archive -- so most
     * cards would get no summary and the odd one would get somebody
     * else's. Without a line, a card renders its own prose, which is what
     * he is on this page to read. */
    if (routedAsks(window.location.pathname)) return null;
    // Same reason as `/asks` above: the cards here are scattered across the
    // archive, and `?limit=N` resolves its window out of the newest N.
    if (routedReplies(window.location.pathname)) return null;
    // And a comment filter, which is now the same kind of request: the cards
    // it asks for are picked by number, not off the top of the feed.
    if (journalFilter !== "all" && !journalQuery.trim()) return null;
    return "/api/digest?limit=" + windowSize;
  }

  function fetchAll() {
    /* No digest while a search is running, and this is a real decision
     * rather than an omission. `/api/digest?limit=N` resolves its window
     * out of the *newest* N cycles, and a search answers with whichever
     * cycles matched -- so asking for both would hand the summaries of
     * cycles 405-424 to a result set from August 9th, and `render` keys
     * them by cycle number, so most cards would get no summary and the
     * odd one would get somebody else's. Sending none means every
     * matching card renders its own prose directly, which is what a
     * search result should show anyway: he is looking for the entry, not
     * for the one-line version of it. */
    var searching = (!!journalQuery.trim() && routedCycle(window.location.pathname) === null)
      || digestUrl() === null;
    /* `/replies` and a comment filter are the two requests whose journal URL
     * is built out of another payload, so they are the two where the reads
     * are serial. The comments read is tolerated everywhere else on this
     * page -- it costs the bubbles, never the feed -- and here it costs the
     * feed too, because without it there is no set of cycles to ask for. A
     * failure therefore has to draw the "comments could not be loaded" line
     * rather than an empty page claiming he has read everything, which is why
     * it resolves to `null` here as well and `render` treats such a page with
     * no comments as unknown rather than as none.
     *
     * Asked from `journalUrl` itself rather than by re-deriving which routes
     * those are: `?cycles=` is exactly the shape that needs the list, and a
     * deep link or `/asks` returns before either branch can reach it. */
    if (journalUrl().indexOf("?cycles=") !== -1 && !haveComments) {
      return fetchVersioned("/api/comments", "comments")
        .catch(function () { return null; })
        .then(function (comments) {
          if (comments && comments.byCycle) {
            lastCommentsByCycle = comments.byCycle;
            haveComments = true;
          }
          return Promise.all([
            fetchVersioned(journalUrl(), "journal"),
            Promise.resolve(null),
            Promise.resolve(comments),
            fetchVersioned("/api/asks/chat", "askchat").catch(function () { return null; }),
          ]);
        });
    }
    return Promise.all([
      fetchVersioned(journalUrl(), "journal"),
      searching
        ? Promise.resolve(null)
        : fetchVersioned(digestUrl(), "digest").catch(function () { return null; }),
      // Tolerated the same way the digest is: the journal is the page, and
      // a comments read that fails should cost the bubbles, not the feed.
      // Conditional as of 2026-08-28: the payload carries a `version` now,
      // so a boot that finds the thread unchanged costs a 304 instead of
      // 57KB gzipped -- it was the largest uncacheable thing this function
      // pulled. It is still built fresh on the server on every request, so
      // "it changes underneath itself while a reply is being written" is
      // unaffected: the etag moves the moment the payload does, including
      // for the reply worker's own `replyWaitingSeconds`.
      fetchVersioned("/api/comments", "comments").catch(function () { return null; }),
      /* Which open asks he has already answered in the cycle's own thread
       * (issue #165). Tolerated like the two above: this one costs the
       * server an Agora round trip per open ask, so a slow or failing
       * Agora must cost the badge, never the feed. */
      fetchVersioned("/api/asks/chat", "askchat").catch(function () { return null; }),
    ]);
  }

  /* ---- The board pages: Issues and Ideas (issues.md #57) ----------------
   *
   * the owner: "I need more visualisations in the Nova app. Create more
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
    // The bubbles under the write-up, keyed the same way `details` is.
    // A second dict rather than a field on the blocks list because they
    // arrive together and are dropped together -- see the delete beside
    // every `delete boardState.details[...]`.
    comments: {},
    // The three halves of ideas.md #70/#71, kept on one state object
    // because they compose: search cuts the list down, the toggles cut
    // it further, sort orders what is left. `query` is what is typed;
    // `matches` is what the server said about the write-ups for that
    // exact string, or null when no answer is in yet.
    query: "",
    matches: null,
    matchedQuery: null,
    toggles: {},

    /* Which project's rows to show, "" for all. A single pick rather
     * than a toggle: the toggles above AND together, and two projects
     * ANDed is always the empty board, so composing them the same way
     * would give a control whose every multi-tap answer is nothing. */
    project: "",
    sort: "filed",
    desc: false,
    // Whether the filter modal (the owner, 2026-08-14: "make the filters
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
   * thing the owner wants". So an outdated row stays deletable because there
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
   * -- the owner, ideas.md #71: "filter the list based on different
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
      // "blocked on edvard" belongs here for a stronger reason than
      // either: it means a cycle did all the work there was and the only
      // step left is his. Leaving it out would drop the row out of this
      // filter at the exact moment it becomes the answer to it.
      match: function (i) {
        return !!i.where || i.statusKey === "in-progress"
          || i.statusKey === "blocked-on-edvard";
      },
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
      // The sort key stays `priority` -- it is in the URL hash and in the
      // board payload, so renaming it would silently drop a bookmarked
      // sort back to Filed. Only the word he reads changes.
      label: "Importance",
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

  /* The list the owner is actually looking at: status filter, then the
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
    // copy, so anything that writes back to the object it was handed
    // keeps it.
    items.forEach(function (item, index) { item.index = index; });
    var shown = items.filter(currentFilter().match);
    TOGGLES.forEach(function (toggle) {
      if (boardState.toggles[toggle.key]) shown = shown.filter(toggle.match);
    });
    if (boardState.project) {
      shown = shown.filter(function (i) { return i.project === boardState.project; });
    }
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
    statusEl.appendChild(wordmark());
    /* The page name is bold and the counts are not -- the owner, issues.md #83:
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

  /* What separates a rating from the capture text it rides in front of,
   * byte-identical to `nova_boards.CAPTURE_PRIORITY_SEP` and pinned to it
   * by `tests/test_board_priority.py` for exactly the reason `PRIORITIES`
   * above is: this side writes the bullet and the Python side parses it
   * back, and nothing else would notice them drifting apart. The colon is
   * not decoration -- without it `High fix the sort order` parses as
   * unrated prose and the rating is lost in his file. */
  var PRIORITY_SEP = ": ";

  /* Parallel to `PRIORITIES`, mirroring `nova_boards.priority_key` --
   * the CSS class suffix each rating carries (`.prio-high` etc). The
   * server sends `item.priorityKey` for a row's *current* rating, but a
   * picker's chip trigger has to relabel itself the instant something
   * else is picked, before any server round trip, so it needs the same
   * mapping client-side. Only board-row triggers use it (`chipStyle`
   * below); the capture box's trigger shows a glyph, never a class that
   * depends on which rating is selected. */
  var PRIORITY_KEYS = ["", "low", "medium", "high", "immediate"];

  /* A small custom dropdown, not a native <select> -- the owner, 2026-08-14:
   * the closed control had to stay compact while the open list still
   * spelled out each rating's word. No native form control can show one
   * thing closed and another open: a <select> renders its selected
   * <option>'s own text in both the box and the popup, so a version of
   * this built on <select> could satisfy one of those asks but never
   * both -- which is exactly the bug he found (the popup was as wordless
   * as the box).
   *
   * That 2026-08-14 ask said the closed control should show *only* the
   * glyph, and that is no longer true of either control: Cycle 274 put
   * the word back beside the glyph everywhere, on his 2026-08-20
   * correction. The custom dropdown is still the right shape -- the two
   * controls still differ, the closed one showing `🟠 High` and the open
   * list a full column of options -- but read the sentence above as the
   * history of why this is not a <select>, not as a live spec.
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
   * `chipStyle: true` (board rows) is the opposite: The owner, 2026-08-14,
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
    /* Board-row chips (`chipStyle: true`) still read `🟠 High` -- collapsed,
     * a chip is the rating's only on-screen representation, which is why
     * the owner asked for the word there (2026-08-19: *"Please do not use
     * these symbols '🟠' as i can't really see the difference as they are
     * colors. Please use the full word"*), word restored beside the glyph
     * in Cycle 268/274.
     *
     * The capture box's closed trigger (`chipStyle` unset) does not carry
     * that same load: tapping it opens `.prio-menu`, which already spells
     * out every option in full, so the trigger only has to preview the
     * pick. The owner, 2026-08-22: the word made the closed button wide
     * enough to push the row's other buttons out of position, and "the
     * button should just show the color" -- the dropdown is where the
     * word has to be. `glyphOf` gives that trigger just the leading glyph
     * (or a dash, unrated), and `keyOf`'s chip-only coloring is untouched. */
    function glyphOf(label) {
      if (!label) return "–";
      var sp = label.indexOf(" ");
      return sp === -1 ? label : label.slice(0, sp); // not-prose: a priority glyph, never a card's text
    }
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
        trigger.textContent = glyphOf(label);
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
    // (the owner, 2026-08-14, after using the anchored version live: the
    // native picker it replaced read as a real dialog, and a small
    // anchored dropdown read as a lesser thing next to it) -- so CSS
    // alone centers `.prio-menu`, and this only has to show it and the
    // dimming backdrop behind it.
    function openMenu() {
      var menu = getPrioMenuOverlay();
      menu.textContent = "";
      /* What this rating actually does, said where it is being set.
       *
       * The owner, 2026-09-07: *"Nova still uses the old priority system,
       * letting me set priority like medium and high even though we decided
       * this is deprecated."* The rating was not deprecated -- it was kept
       * and quietly given a second job as the importance term inside the
       * milestone ranking -- and the defect he was pointing at is that the
       * app never said so. One label, four jobs, and the UI showed the name
       * it had when it was the only lever. So each caller passes the
       * sentence that is true where it sits. All four callers pass one and
       * there is no branch for a caller that does not: the overlay is
       * shared and rebuilt on every open, so an optional caption would
       * leave `aria-describedby` pointing at an id the last picker
       * removed. A new caller owes a sentence. */
      var caption = el("p", "prio-caption", opts.caption);
      // Not `role="option"`: the listbox's children are the choices, and a
      // paragraph announced as a selectable one would be a fifth rating.
      // It describes the box instead.
      caption.id = "prio-menu-caption";
      menu.setAttribute("aria-describedby", caption.id);
      menu.appendChild(caption);
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

  /* The held-row editor lives in `rowedit.js` (issue #233). Bound here,
   * where the block used to sit, after `richtext.js` and every other name it
   * borrows. The guard is for a cached tab that has `app.js` from this build
   * and no `rowedit.js` yet. */
  var rowEditModule = window.novaRowEdit ? window.novaRowEdit({
    el: el,
    json: json,
    loadBoard: loadBoard,
    ownerLabel: ownerLabel,
    renderBlocks: renderBlocks,
  }) : {};
  var HOLD_MS = rowEditModule.HOLD_MS;
  var loadProjects = rowEditModule.loadProjects;
  var renderRowConversation = rowEditModule.renderRowConversation;
  var renderRowEditor = rowEditModule.renderRowEditor;

  /* One row of the owner's board. Closed it is the number, the title and a
   * status chip; open it reveals the write-up, which is a second request
   * the first time a row is opened and memory after that. */
  function renderBoardItem(board, item) {
    var row = el("article", "item item-" + item.statusKey);
    // What `/ideas#68` scrolls to. One board per page, so the number is
    // unique on screen.
    row.id = "item-" + item.number;

    // A <button> cannot contain another <button>, which ruled out a real
    // one for `head` once the priority trigger had to sit inside it, level
    // with the status chip (the owner, 2026-08-14: "the priority status
    // button needs to be placed on the same horizontal as the progress
    // status, on its right side" -- a sibling beside the whole head could
    // only line up with the head's first line). `role="button"` plus the
    // Enter/Space handler below is what a <div> needs instead.
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

    // No rating on a boarded row -- issue #202 and his correction of
    // 2026-09-10: *"When you board my ideas you then break it down to
    // milestones and tasks and then order them"*. The rating is his intent
    // at capture time; once a row is boarded its place is what the project
    // drawer's arrows set, and a chip here would be a second ordering.
    // The size badge, milestone M2 of the picking redesign. Lettered and
    // deliberately not coloured: a rating and a status both step down in
    // weight, and size has no better direction -- XL is not a worse row
    // than S -- so colour would assert a judgement the field does not
    // make. Drawn only when the row has one; the absence reads as
    // "nobody has sized this".
    if (item.size) {
      metaRow.appendChild(el("span", "chip size size-" + item.sizeKey, item.size));
    }
    head.appendChild(metaRow);

    // Below the status/priority line, not beside it (the owner, 2026-08-14:
    // "the date should be placed below them").
    if (item.updated) head.appendChild(el("span", "item-updated", item.updated));

    row.appendChild(head);

    var body = el("div", "item-body");
    if (boardState.open !== item.number) body.hidden = true;
    row.appendChild(body);

    /* the owner, capture 2026-08-22: *"I can't delete, edit or upload a file
     * to a boarded issues."* #4 is an ordinary open row; the only way into
     * the editor was a one-second hold, an invisible gesture with no label.
     * A phone gesture is not measurable from in here, so the repair lands
     * whichever theory is true: if the hold breaks on his device the button
     * reaches the editor anyway, and if he simply never knew the hold
     * existed, the button says so. The hold stays -- he asked for it. */
    function actionBar() {
      var bar = el("div", "item-actions");
      var edit = el("button", "capture-act", "Edit / Delete");
      edit.type = "button";
      edit.addEventListener("click", function (event) {
        // The body sits inside the row but outside `head`, so this does
        // not reach the head's toggle -- stopping it anyway keeps that
        // true if the markup is ever rearranged.
        event.stopPropagation();
        openEditor();
      });
      bar.appendChild(edit);
      return bar;
    }

    function fill() {
      body.textContent = "";
      body.appendChild(actionBar());
      if (item.where) body.appendChild(el("p", "item-where", "Landed in " + item.where));
      var key = board + ":" + item.number;
      var blocks = boardState.details[key];
      if (!blocks) {
        body.appendChild(el("p", "empty", "Loading…"));
        fetch("/api/board?name=" + board + "&item=" + item.number)
          .then(json)
          .then(function (payload) {
            var one = (payload && payload.item) || {};
            boardState.details[key] = one.blocks || [];
            boardState.comments[key] = one.comments || [];
            if (boardState.open === item.number) fill();
          })
          .catch(function (err) {
            body.textContent = "";
            body.appendChild(el("p", "empty", "Could not load #" + item.number + ": " + err));
          });
        return;
      }
      // Not an early return any more, and that is the whole of the bug
      // this line used to carry into the new feature: a row whose body is
      // *only* a conversation now renders an empty write-up, and stopping
      // here would have hidden his comments and the box to answer them
      // behind "No write-up yet".
      if (!blocks.length) {
        body.appendChild(el("p", "empty", "No write-up yet — only the board row."));
      } else {
        renderBlocks(body, blocks);
      }
      renderRowConversation(body, boardState.comments[key]);
      body.appendChild(commentBox());
    }

    /* The comment thread, idea #64: *"Lets me have the same comment
     * conversation on ideas, notes and issues like the Journal."*
     *
     * This used to say there was no thread to render and that this was
     * the design: a comment is appended into the row's own write-up, so
     * drawing the write-up drew the conversation with it, in order, in
     * the same text he reads in Obsidian. That is still how the *file*
     * works and it is still right. What was wrong was calling the
     * rendering finished, and he said so on 2026-08-26: *"boarded issues
     * does not have those nice colored comments like there are now in the
     * 'not boarded yet' box, so take the best from both worlds here."*
     * Three voices as one undifferentiated column of paragraphs is not a
     * conversation, and the capture box beside it had already proved the
     * better shape. `renderRowConversation` above draws them; this is
     * still only the composer.
     *
     * It goes *after* the thread for the reason `append_detail_note` puts
     * the note at the end: the write-up is his statement of the problem
     * and the conversation accumulates under it.
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
        if (!text && !attach.count()) {
          status.textContent = "Nothing to send.";
          status.className = "item-comment-status is-error";
          return;
        }
        // A space, not a blank line: a board comment may not contain a
        // line break at all -- the server refuses one -- which is why the
        // line above flattens his typing too.
        var body = [text, attach.markdown(" ")].filter(Boolean).join(" ");
        busy(true);
        status.textContent = "sending…";
        status.className = "item-comment-status";
        fetch("/api/board/comment", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // The comment box on a board row is his, so it says so. The server
          // has no default author any more -- a caller that does not name
          // itself is refused rather than written down as him.
          body: JSON.stringify({ target: board, number: item.number, text: body, author: OWNER_RECORD }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            box.value = "";
            attach.clear();
            // The write-up on screen is the one from before the comment.
            // Drop the cached copy so `fill` refetches it and he sees his
            // own sentence land, rather than being told it saved and
            // shown a body that does not contain it.
            delete boardState.details[board + ":" + item.number];
            delete boardState.comments[board + ":" + item.number];
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
      /* the owner, issues.md: *"I can't delete, edit or upload a file to a
       * boarded issues."* Cycle 318 did the delete and the edit; this is
       * the third verb, and it is the same button the journal drawer and
       * the capture box already carry.
       *
       * The picked files sit in a tray under the box rather than as
       * markdown inside it (Cycle 377), so he can see what he is about to
       * send and cross out the one he changed his mind about -- and the
       * one thing that knows how to upload stays one function. `send` is
       * disabled while the POST is in flight for the reason
       * `buildAttach`'s `busy` gives: the click handler reads the tray
       * synchronously, so a Comment tapped mid-upload files the text
       * without the picture and the picture attaches to nothing.
       *
       * No draft store here, unlike the journal drawer. This composer has
       * none -- `fill()` rebuilds the panel on every poll and always has
       * -- so an attach-then-wait loses the chip exactly as a
       * type-then-wait already loses the sentence. Giving the picture a
       * safety net the typing does not have would be the more confusing
       * of the two. Filed rather than smuggled in here. */
      var attach = window.novaAttach.build({
        onBusy: function (isBusy) { busy(isBusy); },
        onStatus: function (text, isError) {
          status.textContent = text;
          status.className = isError
            ? "item-comment-status is-error"
            : "item-comment-status";
        },
      });
      wrap.appendChild(attach.tray);
      foot.appendChild(attach.input);
      foot.appendChild(attach.button);
      /* The third place he writes me prose gets the same mic (ideas.md
       * #221). `fill()` rebuilds this panel on every poll, which is why
       * `dictate.js` stops a recogniser whose button has left the page. */
      var mic = window.novaDictation.button(el, "item-comment-mic");
      foot.appendChild(mic);
      window.novaDictation.wire({
        button: mic,
        onText: window.novaDictation.appendTo(box),
        onStatus: function (text) {
          status.textContent = text;
          status.className = text ? "item-comment-status is-error" : "item-comment-status";
        },
      });
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

  /* One not-boarded capture, with the owner's edit and delete on it
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
  /* The three capture files, and what a button offering to move a line
   * into one of them should say. Kept as one list because the notes page
   * and the two board pages both need it and a second copy of three
   * strings is the drift this repo keeps filing against itself. */
  var CAPTURE_KINDS = [
    { target: "issues", verb: "Make issue" },
    { target: "ideas", verb: "Make idea" },
    { target: "notes", verb: "Make note" },
  ];

  /* Buttons that move one capture into each of the other two files.
   *
   * The owner, 2026-08-24: *"The note i sent regarding the rebuilding the
   * notes page was sent as a note, but its actually an idea, but i have
   * no way of changing it or editing it."* He chooses which of the three
   * buttons to press before he has finished thinking, and until now that
   * choice was permanent.
   *
   * `onDone` repaints from the file rather than patching the node: the
   * line has left one page and arrived on another, and only the vault
   * knows what both now say. `onFail` gets the message verbatim, because
   * the one failure worth reading -- the copy landed and the removal did
   * not -- tells him exactly which of the two to delete. */
  function convertButtons(source, index, original, onDone, onFail, disable) {
    /* **These disable themselves, not just the caller's Edit and Delete.**
     * The first version handed the caller's `disable` the two buttons it
     * already knew about and left its own live for the whole in-flight
     * fetch -- so a second tap, which is an ordinary thing to do on a
     * phone with a slow connection, ran the whole conversion again. The
     * destination write is unconditional, so that lands a *second* copy
     * in the target file and the removal then fails because the first tap
     * already took the line. Found by review, which walked the double-tap
     * through both calls rather than reading the handler. */
    var mine = [];
    var pending = false;
    var setBusy = function (on) {
      pending = on;
      mine.forEach(function (b) { b.disabled = on; });
      if (disable) disable(on);
    };
    return CAPTURE_KINDS.filter(function (kind) { return kind.target !== source; })
      .map(function (kind) {
        var btn = el("button", "capture-act", kind.verb);
        btn.type = "button";
        mine.push(btn);
        btn.addEventListener("click", function () {
          /* `disabled` is the guard a real browser honours; this is the one
           * that does not depend on the browser honouring it. A click event
           * that arrives some other way -- synthesised, or from an assistive
           * technology -- would otherwise start a second unconditional
           * write to the destination file. */
          if (pending) return;
          setBusy(true);
          fetch("/api/capture/convert", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              from: source, to: kind.target, index: index, original: original,
            }),
          })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (result) {
              if (!result || !result.ok) {
                throw new Error((result && (result.message || result.error)) || "failed");
              }
              onDone();
            })
            .catch(function (err) {
              setBusy(false);
              onFail(err);
            });
        });
        return btn;
      });
  }

  /* The editing box for one capture or note, the size of the composer.
   *
   * The owner, 2026-08-24: *"Editing issues and ideas (and probably the
   * same for notes) is hard. The edit input box is very small (see image)
   * should be the same width and height like the main input box and also
   * uploaded images just show like a url text, it should show like the
   * miniature images like when i upload them."*
   *
   * Both halves of that are here. The box was `rows = 2` with no growth,
   * so a capture with a 90-character upload URL in it showed about a
   * third of itself -- and `.capture textarea` had already been given
   * exactly this treatment for exactly this complaint on 2026-08-09
   * ("too small and not rescalable"), which is the shape being borrowed
   * rather than invented. `fit()` is that same function: clear the height
   * before reading `scrollHeight`, or the box grows and never shrinks.
   *
   * The textarea keeps the raw markdown -- that is what the vault stores,
   * and it is what makes saving an untouched line a no-op rather than a
   * reformat of his sentence -- so the pictures go in a strip *under* the
   * box, wearing the composer tray's own `.attach-chip` chrome so an
   * attachment looks the same everywhere it appears. */
  function buildCaptureEditor(rawText) {
    var wrap = el("div", "capture-editor");
    var box = el("textarea", "capture-input");
    box.value = rawText || "";
    // Two rows is the composer's own starting height; `fit()` takes it
    // from there, so this is the floor rather than the size.
    box.rows = 2;
    wrap.appendChild(box);

    var tray = el("div", "attach-tray edit-tray");
    function renderTray() {
      tray.textContent = "";
      var found = [];
      /* `ATTACH_RE` is a module-level `/g` regex shared with the readers,
       * so its `lastIndex` is whatever the last user left behind. Reset
       * it, or the first chip strip drawn after somebody else's `exec`
       * silently starts halfway through the text. */
      ATTACH_RE.lastIndex = 0;
      var m;
      while ((m = ATTACH_RE.exec(box.value)) !== null) {
        found.push({ raw: m[0], isImage: m[1] === "!", name: m[2], url: m[3], at: m.index });
      }
      tray.hidden = found.length === 0;
      found.forEach(function (item) {
        var label = item.name || item.url;
        var chip = el("div", "attach-chip");
        if (item.isImage) {
          var thumb = el("img", "attach-thumb");
          thumb.src = item.url;
          // His filename, not "image" -- with four screenshots in a row
          // it is the only thing telling them apart to a screen reader.
          thumb.alt = label;
          chip.appendChild(thumb);
        } else {
          chip.appendChild(el("span", "attach-chip-name", "📎 " + label));
        }
        var remove = el("button", "attach-chip-remove", "✕");
        remove.type = "button";
        remove.title = "Remove " + label;
        remove.setAttribute("aria-label", "Remove " + label);
        remove.addEventListener("click", function () {
          /* Cut at the offset this chip was found at, not at the first
           * `indexOf` of its text: the same upload can legitimately be
           * linked twice in one capture, and a global or first-match
           * removal would take the wrong one. The offsets are re-derived
           * on every `input`, so they are never stale by more than the
           * event that would have redrawn them -- and the equality check
           * is what makes that a fact rather than an assumption. */
          var at = item.at;
          if (box.value.slice(at, at + item.raw.length) !== item.raw) {
            at = box.value.indexOf(item.raw);
          }
          if (at >= 0) {
            box.value = box.value.slice(0, at) + box.value.slice(at + item.raw.length); // not-prose: cuts one link construct out, keeps the whole rest
          }
          renderTray();
          fit();
        });
        chip.appendChild(remove);
        tray.appendChild(chip);
      });
    }
    wrap.appendChild(tray);

    function fit() {
      box.style.height = "auto";
      box.style.height = box.scrollHeight + "px";
    }
    box.addEventListener("input", function () { renderTray(); fit(); });
    renderTray();

    return {
      el: wrap,
      box: box,
      focus: function () {
        box.focus();
        // After it is in the document -- `scrollHeight` on a detached
        // node is 0, which would collapse the box to its padding.
        fit();
      },
    };
  }

  /* A capture's own controls, off the card and behind a long press.
   *
   * The owner, 2026-08-24: *"The new buttons for the messages to edit or
   * make idea or make issue should not be visible. Lets change it to when
   * i press and hold it it opens a modal with al the edit options. Do
   * this for issues, ideas and notes."*
   *
   * Five buttons per capture, on a phone, on a page that is otherwise his
   * own sentences -- that is what he is looking at when he says they
   * should not be visible. The gesture is one this page already teaches:
   * `HOLD_MS` on a board row opens that row's editor, so press-and-hold
   * to act on a thing is not a new idea here, only a second place it
   * applies, and the two now agree.
   *
   * It reuses the priority popup's overlay and backdrop rather than
   * building a second modal -- one node, one backdrop, one Escape
   * handler, and `dataset.openFor` already exists to record which thing
   * is showing in it. */
  var actionSheetHandlers = null;
  function closeActionSheet() {
    if (!actionSheetHandlers) return;
    document.removeEventListener("click", actionSheetHandlers.onDocClick, true);
    document.removeEventListener("keydown", actionSheetHandlers.onKeydown, true);
    actionSheetHandlers = null;
    /* Only if the overlay is still showing *us*. Three things share that
     * node now -- the rating popup, the filter modal and this -- and each
     * takes it by overwriting its contents without telling the last
     * holder. So a sheet whose overlay has since been taken over must
     * drop its handlers and touch nothing else, or it would empty a
     * rating popup somebody opened in the meantime. `openFor` is already
     * how the rating trigger recognises its own popup; this is the same
     * question asked from the other side. */
    if (prioMenuOverlay && prioMenuOverlay.dataset.openFor === "actions") {
      prioMenuOverlay.hidden = true;
      // Emptied as well as hidden: these buttons close over one capture's
      // index, and a stale set left in the shared overlay is a set of
      // controls pointing at a bullet that may since have moved.
      prioMenuOverlay.textContent = "";
      delete prioMenuOverlay.dataset.openFor;
      if (prioMenuBackdrop) prioMenuBackdrop.hidden = true;
    }
  }

  function openActionSheet(title, buttons, opts) {
    var options = opts || {};
    // The overlay is shared, so whatever else may be using it has to be
    // told it has lost it -- otherwise its Escape and outside-click
    // handlers stay registered against a node showing our buttons.
    closeFiltersModal();
    closeActionSheet();
    var overlay = getPrioMenuOverlay();
    overlay.textContent = "";
    overlay.removeAttribute("role");
    overlay.dataset.openFor = "actions";
    var head = el("div", "modal-head");
    head.appendChild(el("h2", "modal-title", title));
    var closeBtn = el("button", "modal-close", "×");
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "Close actions");
    closeBtn.addEventListener("click", closeActionSheet);
    head.appendChild(closeBtn);
    overlay.appendChild(head);
    var list = el("div", "action-sheet");
    buttons.forEach(function (btn) { list.appendChild(btn); });
    overlay.appendChild(list);
    prioMenuBackdrop.hidden = false;
    overlay.hidden = false;

    /* A hold opens this from `mousedown`/`touchstart`, so the release the
     * user has not made yet still becomes a `click` on the card
     * underneath -- which arrives at this document-level capture listener
     * before anything can stop it and closes the sheet the instant it
     * appears. Swallowing exactly one event is the narrow fix; the
     * keyboard route passes no flag, because there is no trailing click
     * to swallow there and eating the first real outside click would
     * leave the sheet feeling stuck. */
    var swallow = !!options.swallowNextClick;
    function onDocClick(e) {
      if (swallow) { swallow = false; return; }
      if (overlay.contains(e.target)) return;
      closeActionSheet();
    }
    function onKeydown(e) { if (e.key === "Escape") closeActionSheet(); }
    actionSheetHandlers = { onDocClick: onDocClick, onKeydown: onKeydown };
    document.addEventListener("click", onDocClick, true);
    document.addEventListener("keydown", onKeydown, true);
    if (buttons.length) buttons[0].focus();
  }

  /* Wire press-and-hold, and a keyboard equivalent, onto one card.
   *
   * `open` is called with `true` from the gesture and `false` from the
   * keyboard, which is the flag `openActionSheet` needs for the trailing
   * click. Written once because three surfaces now want it -- issues,
   * ideas and notes -- and three copies of a gesture is how they end up
   * disagreeing about how long a hold is. */
  function bindHoldMenu(node, open) {
    var holdTimer = null;
    function endHold() {
      if (holdTimer) { clearTimeout(holdTimer); holdTimer = null; }
    }
    function startHold(e) {
      /* A press that started on a control inside the card is that
       * control's, not the card's. The priority chip lives in the capture
       * body, and without this a slow tap on it would arm the rating
       * popup and the action sheet at once, then hand the overlay to
       * whichever fired last. */
      if (e && e.target && e.target !== node && e.target.closest
          && e.target.closest("button, a, textarea, input")) {
        return;
      }
      endHold();
      holdTimer = setTimeout(function () {
        holdTimer = null;
        open(true);
      }, HOLD_MS);
    }
    node.addEventListener("mousedown", startHold);
    node.addEventListener("touchstart", startHold);
    ["mouseup", "mouseleave", "touchend", "touchmove", "touchcancel"].forEach(
      function (name) { node.addEventListener(name, endHold); });
    node.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" && e.key !== " ") return;
      // Not while the editor this same menu opened is on the card -- the
      // box is a text field and a space in it is a space.
      if (e.target !== node) return;
      e.preventDefault();
      open(false);
    });
  }

  /* The capture card lives in `capture.js` (issue #233). Bound here, where
   * the block used to sit; everything it borrows is a hoisted function or
   * was set above this line, and its one caller, `renderBoardOwner`, runs
   * later. The guard is for a cached tab that has `app.js` from this build
   * and no `capture.js` yet. */
  var captureModule = window.novaCapture ? window.novaCapture({
    PRIORITY_SEP: PRIORITY_SEP,
    bindHoldMenu: bindHoldMenu,
    buildCaptureEditor: buildCaptureEditor,
    buildPrioPicker: buildPrioPicker,
    closeActionSheet: closeActionSheet,
    convertButtons: convertButtons,
    el: el,
    loadBoard: loadBoard,
    openActionSheet: openActionSheet,
    renderBlocks: renderBlocks,
  }) : {};
  var renderCapture = captureModule.renderCapture;

  function renderBoardOwner(board, payload) {
    var wrap = el("div", "board");
    /* One section. A capture a cycle has closed carries a `DONE (Cycle N):`
     * prefix (`nova_boards.split_capture_done`); Cycle 251 gave those their
     * own "Done, not yet cleared" section so they would stop claiming to be
     * work, and the owner asked for that section to go, capture 2026-08-20:
     * *"I do not like or see the point of the 'Done, not yet cleared' list
     * in issues and ideas. I do not use it and to me its just noise."*
     *
     * So a closed capture is not rendered at all. The reasoning for keeping
     * them visible was that the `DONE` marker is a cycle answering him and
     * the answer is worth reading once -- he has now said he does not read
     * it, which settles it. The bullet stays in the vault file either way;
     * nothing here deletes anything, and nothing yet prunes them from the
     * file (see `[roll-edvards-captures]`), so this hides a list that grows
     * rather than fixing why it grows.
     *
     * The index passed to `renderCapture` is the index into
     * `payload.captures`, not into the filtered list -- `/api/capture/edit`
     * addresses a bullet by its position in the file, so filtering for
     * display must not renumber it. */
    var captures = payload.captures || [];
    var open = [];
    captures.forEach(function (capture, index) {
      if (!capture.done) open.push({ capture: capture, index: index });
    });
    if (open.length) {
      var box = el("section", "captures");
      box.appendChild(el("h2", "captures-title", "Not boarded yet"));
      open.forEach(function (row) {
        box.appendChild(renderCapture(board, row.capture, row.index));
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

  /* Everything a row draws. A row whose sig is unchanged keeps the node
   * already on the page, so a search keystroke stops destroying the row
   * he had open (issue #233, step 9). Deliberately *not* including
   * `boardState.open`: the toggle opens and closes a row by hand without
   * re-rendering, so the node on screen already carries its own open
   * state, and signing on it would rebuild the one row whose contents --
   * a half-typed comment, a scrolled write-up -- are the thing worth
   * keeping. */
  function boardRowSig(item) {
    return [
      item.number, item.title, item.status, item.statusKey, item.priority,
      item.priorityKey, item.size, item.sizeKey, item.updated, item.where
    ].join("\u0000");
  }

  /* Draw `shown` into `boardRows` through `thread.js`, reusing the node
   * built for a row last time when its sig has not moved. The cache hangs
   * off the container rather than off the module, so a fresh `renderBoard`
   * -- which builds a new `board-rows` -- starts empty, and only the search
   * path, which keeps the container, reuses anything. Where Preact did not
   * load, this is the old rebuild exactly. */
  function drawBoardRows(board, shown, build, cacheKey) {
    var cache = boardRows.novaBoardCards || {};
    var built = {};
    var rows = shown.map(function (item) {
      var key = cacheKey + item.number;
      var sig = boardRowSig(item);
      var was = cache[key];
      var node = was && was.sig === sig ? was.node : build(board, item);
      built[key] = { node: node, sig: sig };
      return { node: node, key: key, sig: sig };
    });
    boardRows.novaBoardCards = built;
    if (!rows.length) {
      var text = boardState.query.trim()
        ? "Nothing matches “" + boardState.query.trim() + "”."
        : "Nothing here.";
      rows = [{ node: el("p", "empty", text), key: "empty", sig: text }];
    }
    if (window.novaThread) return window.novaThread.render(boardRows, rows);
    boardRows.textContent = "";
    rows.forEach(function (r) { boardRows.appendChild(r.node); });
  }

  function renderBoardRows(board, items) {
    drawBoardRows(board, visibleItems(items), renderBoardItem, "his:");
  }

  /* My own rows, cut by the search and by nothing else. `visibleItems` is
   * not reused: it applies the status filter and the toggles, which live
   * on the strip above *his* rows and my tab does not draw. Filtering by a
   * control the reader cannot see would hide rows for good. */
  function visibleNovaItems(items) {
    var query = boardState.query.trim().toLowerCase();
    if (!query) return items;
    var matched = boardState.matchedQuery === query && boardState.matches
      ? boardState.matches
      : [];
    return items.filter(function (i) {
      return (i.title || "").toLowerCase().indexOf(query) !== -1
        || matched.indexOf(i.number) !== -1;
    });
  }

  function renderNovaRows(board, items) {
    drawBoardRows(board, visibleNovaItems(items), renderNovaItem, "mine:");
  }

  /* Redraw only what a search changed. The owner, issues.md, 2026-08-15:
   * "When i use the search bar in Nova, my keyboard is closed on every
   * letter input so i have to open the keyboard each letter."
   *
   * `renderBoard` starts with `feed.textContent = ""`, so every keystroke
   * used to destroy the very input being typed into. Removing the focused
   * element dismisses the soft keyboard, and a `setTimeout(input.focus)`
   * cannot bring it back: a phone opens the keyboard for a focus inside a
   * user gesture, not one a task later. A desktop browser restored the
   * caret, which is why it shipped.
   *
   * A search changes which rows show and nothing else -- the chip counts
   * are computed against the status filter, not the query, and the sort
   * control does not read it -- so the rows are the only thing rebuilt,
   * and since step 9 of issue #233 a row that still matches is not even
   * that: `drawBoardRows` keeps its node. */
  function refreshBoardRows(board, payload) {
    if (!boardRows || !boardRows.isConnected) {
      renderBoard(board, payload);
      return;
    }
    if (boardState.tab === "nova") renderNovaRows(board, payload.novaItems || []);
    else renderBoardRows(board, payload.items || []);
  }

  /* The search box, the filter chips and the sort control, in that order
   * -- the owner's two asks (ideas.md #70 and #71) are one strip on the
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
    /* The project row, built from the board rather than from a list.
     * `nova_boards.board_projects` reads the names off the rows for the
     * same reason: typing a name into a `Project` cell is how a project
     * gets added, and a second list somewhere could only ever disagree
     * with the rows.
     *
     * Hidden while every row says the same thing, which is the state
     * both boards are in the day this ships. That is not a cap on the
     * control -- it is that a single chip reading "Nova (157)" filters
     * nothing and takes a row of screen on a 360px phone to say so. It
     * appears by itself the first time a second project exists. */
    var projects = [];
    items.forEach(function (i) {
      if (i.project && projects.indexOf(i.project) < 0) projects.push(i.project);
    });
    if (projects.length > 1) {
      projects.forEach(function (name) {
        var on = boardState.project === name;
        var count = items.filter(currentFilter().match).filter(function (i) {
          return i.project === name;
        }).length;
        var chip = el("button", "filter filter-project" + (on ? " on" : ""),
          name + " (" + count + ")");
        chip.type = "button";
        chip.setAttribute("aria-pressed", on ? "true" : "false");
        // Tapping the one that is already on clears it. There is no
        // separate "All" chip: it would be a fourth thing to explain and
        // the off state of every chip already means the same thing.
        chip.addEventListener("click", function () {
          boardState.project = on ? "" : name;
          renderBoard(board, payload);
        });
        chips.appendChild(chip);
      });
    }
    return chips;
  }

  /* The filter modal -- the owner, 2026-08-14: "make the filters into a
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

  /* The search box on its own, because both tabs now have one and they
   * are the same control. Extracted rather than copied: two things in
   * here were each learned once and would have to be relearned on a
   * second copy -- the clear button is hidden rather than removed so it
   * cannot move the caret's neighbour mid-edit, and the focus after a
   * clear happens synchronously inside the tap, which is the only way a
   * phone keeps the keyboard up. Everything else on the strip -- the
   * status chips, the toggles, the sort control -- is deliberately not
   * in here, because my tab does not draw them. */
  function renderSearchBox(board, payload) {
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
    return search;
  }

  function renderBoardControls(board, payload, items) {
    var bar = el("div", "board-controls");
    bar.appendChild(renderSearchBox(board, payload));

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
    // The dot on the closed filter button means "a cut is active that
    // you cannot see from here". A project pick is exactly that, so it
    // lights the same dot -- leaving it out would hide the one filter
    // capable of emptying the board with no sign of why.
    var anyToggleOn = TOGGLES.some(function (t) { return !!boardState.toggles[t.key]; })
      || !!boardState.project;
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
    var tab = boardState.tab;
    if (searchTimer) clearTimeout(searchTimer);
    if (!query) {
      boardState.matches = null;
      boardState.matchedQuery = null;
      return;
    }
    searchTimer = setTimeout(function () {
      // `mine=1` asks my board rather than his. The answer is a list of
      // row numbers and both boards number from 1, so the flag is what
      // makes the reply addressable at all -- and `tab` is captured above
      // rather than read here, for the same reason `board` is: switching
      // tabs mid-flight must not let his numbers land on my rows.
      fetch("/api/board?name=" + board + "&q=" + encodeURIComponent(query)
        + (tab === "nova" ? "&mine=1" : ""))
        .then(json)
        .then(function (result) {
          if (boardState.tab !== tab) return;
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

  /* My own boarded rows, above my note stream (issue #97). Deliberately
   * not `renderBoardItem`: that row carries a priority *picker* and a
   * tap that fetches the write-up from `/api/board?item=N`, and neither
   * belongs here. The rating on my own row is mine to set in the file
   * rather than his to correct on the page, and the write-up already
   * arrived with the payload as `novaDetails` -- so this is a read-only
   * row that expands from data the page is holding. */
  function renderNovaItem(board, item) {
    var row = el("article", "item item-" + item.statusKey);
    var head = el("div", "item-head");
    head.setAttribute("role", "button");
    head.setAttribute("tabindex", "0");
    head.setAttribute("aria-expanded", "false");

    var titleRow = el("div", "item-title-row");
    titleRow.appendChild(el("span", "item-number", "#" + item.number));
    titleRow.appendChild(el("span", "item-title", item.title));
    head.appendChild(titleRow);

    var metaRow = el("div", "item-meta-row");
    metaRow.appendChild(el("span", "chip chip-" + item.statusKey, item.status));
    if (item.priority) {
      metaRow.appendChild(el("span", "chip prio prio-" + item.priorityKey, item.priority));
    }
    // The size badge, milestone M2 of the picking redesign. A lettered
    // badge and deliberately not a coloured chip: a rating and a status
    // both step down in weight because more really is worse or further
    // along, and size has no better direction -- XL is not a worse row
    // than S, only a bigger one -- so spending colour here would assert a
    // judgement the field does not make. Drawn only when the row has one:
    // most rows are unestimated and an empty badge on every one of them is
    // noise, while the absence is itself readable as "nobody has sized
    // this".
    if (item.size) {
      metaRow.appendChild(el("span", "chip size size-" + item.sizeKey, item.size));
    }
    head.appendChild(metaRow);
    if (item.updated) head.appendChild(el("div", "item-updated", item.updated));
    row.appendChild(head);

    /* The body is filled and emptied in place rather than by redrawing
     * the tab. The first version of this called `renderBoard` on every
     * toggle, which tears down the very element that was just clicked --
     * so keyboard focus fell to <body> and the role="button" and Enter /
     * Space handling right above became decoration. Reviewer finding on
     * runner#354. His rows have never had this problem because they
     * mutate their own body; mine do it the same way now. */
    var body = el("div", "item-body");
    row.appendChild(body);
    var open = false;

    var key = board + ":mine:" + item.number;
    function fill() {
      body.textContent = "";
      var blocks = boardState.details[key];
      if (!blocks) {
        body.appendChild(el("p", "empty", "Loading…"));
        fetch("/api/board?name=" + board + "&item=" + item.number + "&mine=1")
          .then(json)
          .then(function (payload) {
            var one = (payload && payload.item) || {};
            boardState.details[key] = one.blocks || [];
            boardState.comments[key] = one.comments || [];
            if (open) fill();
          })
          .catch(function (err) {
            body.textContent = "";
            body.appendChild(el("p", "empty", "Could not load #" + item.number + ": " + err));
          });
        return;
      }
      // My own rows have no comment box -- he comments on his board, not
      // on mine -- but a row of mine can still carry notes a cycle wrote
      // under it, and they are the same shape, so they draw the same way.
      if (!blocks.length) {
        body.appendChild(el("p", "empty", "No write-up yet — only the board row."));
      } else {
        renderBlocks(body, blocks);
      }
      renderRowConversation(body, boardState.comments[key]);
    }

    function toggle() {
      open = !open;
      head.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) fill();
      else body.textContent = "";
    }
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        toggle();
      }
    });
    return row;
  }

  function renderBoardNova(board, payload) {
    var wrap = el("div", "board");
    var items = payload.novaItems || [];
    if (items.length) {
      var box = el("section", "nova-board");
      box.appendChild(el("h2", "captures-title", "On my board"));
      /* The same search his rows have had since ideas.md #71, over the
       * same two things: my row titles, which the page is holding, and
       * my write-ups, which it is not -- `board_page` windows
       * `novaDetails` away on every list request, so the write-up half
       * is answered by the server exactly as his is.
       *
       * It cuts the rows above and not the note stream below. The stream
       * is 660 bullets fetched a page at a time, so searching it means
       * searching what has not been fetched, which is a different piece
       * of work and not this one. */
      box.appendChild(renderSearchBox(board, payload));
      boardRows = el("div", "board-rows");
      renderNovaRows(board, items);
      box.appendChild(boardRows);
      wrap.appendChild(box);
      wrap.appendChild(el("h2", "captures-title", "Everything else I noticed"));
    }
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
    renderBoardStatus(board, payload);
    feed.textContent = "";

    var titles = boardTitles(board);
    var tabs = el("div", "tabs");
    [
      { key: "edvard", label: "Your " + titles.page.toLowerCase() },
      { key: "nova", label: titles.mine },
    ].forEach(function (tab) {
      var button = el("button", "tab" + (boardState.tab === tab.key ? " on" : ""), tab.label);
      button.type = "button";
      button.setAttribute("aria-pressed", boardState.tab === tab.key ? "true" : "false");
      button.addEventListener("click", function () {
        if (boardState.tab === tab.key) return;
        boardState.tab = tab.key;
        // Same reasoning as switching board in `loadBoard`, and now the
        // same consequence: `matches` is a list of row numbers answered
        // for one tab, and both tabs number from 1, so carrying it over
        // would apply my #3 to his #3. The box is cleared with it rather
        // than left standing over rows it never searched.
        boardState.query = "";
        boardState.matches = null;
        boardState.matchedQuery = null;
        renderBoard(board, payload);
      });
      tabs.appendChild(button);
    });
    feed.appendChild(tabs);
    feed.appendChild(
      boardState.tab === "nova"
        ? renderBoardNova(board, payload)
        : renderBoardOwner(board, payload)
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
      boardState.project = "";
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

  /* The costs page, the retrospective page and the ECharts layer under both
   * live in `charts.js` -- 40 KB of it, moved out on 2026-09-18 because this
   * file is what issue #233 is about. It is called here rather than left to
   * run on its own script tag so the four names below are bound at the exact
   * point in this file's body where they used to be defined, which is before
   * the boot at the bottom draws the first route. The guard is for a cached
   * tab that has `app.js` from this build and no `charts.js` yet; without it
   * the whole app would fail to boot over one missing page. */
  var closeFullChart, fmtStamp, loadCosts, loadRetro;
  if (window.novaCharts) {
    var chartsPage = window.novaCharts({
      el: el,
      feed: feed,
      statusEl: statusEl,
      fetchPage: fetchPage,
      markNav: markNav,
      route: route,
      savedCopyLine: savedCopyLine,
      stopPolling: stopPolling,
      wordmark: wordmark,
    });
    closeFullChart = chartsPage.closeFullChart;
    fmtStamp = chartsPage.fmtStamp;
    loadCosts = chartsPage.loadCosts;
    loadRetro = chartsPage.loadRetro;
  } else {
    closeFullChart = function () {};
    fmtStamp = function () { return ""; };
    loadCosts = function () {};
    loadRetro = function () {};
  }

  /* The `/plan` page lives in `plan.js` (issue #233). Everything it needs
   * from this file is handed over by name here; the guard is for a cached tab
   * that has `app.js` from this build and no `plan.js` yet. */
  var loadPlan;
  if (window.novaPlan) {
    loadPlan = window.novaPlan({
      el: el,
      feed: feed,
      fetchPage: fetchPage,
      json: json,
      markNav: markNav,
      renderBlocks: renderBlocks,
      renderSpans: renderSpans,
      route: route,
      savedCopyLine: savedCopyLine,
      statusEl: statusEl,
      stopPolling: stopPolling,
      wordmark: wordmark,
    }).loadPlan;
  } else {
    loadPlan = function () {};
  }

  /* The Pool page and the project page live in `project.js` (issue #233).
   *
   * Same seam as `chat-dock.js`, `charts.js` and `diag.js`: everything the
   * two pages need from this file is handed over by name here, and the two
   * names the router still calls come back. A name missing from this list
   * is a `ReferenceError` on first use rather than a page that half draws,
   * which is why it is a list somebody has to add to on purpose.
   */
  var loadPool, loadProject;
  if (window.novaProject) {
    var projectPages = window.novaProject({
      OWNER_RECORD: OWNER_RECORD,
      el: el,
      feed: feed,
      fetchPage: fetchPage,
      json: json,
      load: load,
      markNav: markNav,
      renderRowConversation: renderRowConversation,
      route: route,
      statusEl: statusEl,
      stopPolling: stopPolling,
      wordmark: wordmark,
    });
    loadPool = projectPages.loadPool;
    loadProject = projectPages.loadProject;
  } else {
    loadPool = function () {};
    loadProject = function () {};
  }

  /* The Notes page lives in `notes.js` (issue #233).
   *
   * The guard is for a cached tab that has `app.js` from this build and no
   * `notes.js` yet; without it the whole app would fail to boot over one
   * missing page. */
  var loadNotes;
  if (window.novaNotes) {
    var notesPage = window.novaNotes({
      OWNER_LABEL: OWNER_LABEL,
      bindHoldMenu: bindHoldMenu,
      buildCaptureEditor: buildCaptureEditor,
      captureHome: captureHome,
      closeActionSheet: closeActionSheet,
      convertButtons: convertButtons,
      el: el,
      feed: feed,
      fetchPage: fetchPage,
      json: json,
      load: load,
      loadWhenScrolledTo: loadWhenScrolledTo,
      markNav: markNav,
      openActionSheet: openActionSheet,
      renderBlocks: renderBlocks,
      route: route,
      savedCopyLine: savedCopyLine,
      statusEl: statusEl,
      stopPolling: stopPolling,
      wordmark: wordmark,
    });
    loadNotes = notesPage.loadNotes;
  } else {
    loadNotes = function () {};
  }

  /* The ask thread's helpers live in `ask.js` (issue #233). Bound here, where
   * the block used to sit. The guard is for a cached tab that has `app.js`
   * from this build and no `ask.js` yet. */
  var askModule = window.novaAsk ? window.novaAsk({
    OWNER_RECORD: OWNER_RECORD,
    // Call-time wrappers: `askMessage` and `askPending` live in `bubble.js`,
    // which is bound further down because it reads from this module.
    askMessage: function () { return askMessage.apply(null, arguments); },
    askPending: function () { return askPending.apply(null, arguments); },
    el: el,
  }) : {};
  var ASK_POLL_MAX = askModule.ASK_POLL_MAX;
  var ASK_POLL_MS = askModule.ASK_POLL_MS;
  var STICK_SLOP_PX = askModule.STICK_SLOP_PX;
  var askCopyButton = askModule.askCopyButton;
  var askPaintNote = askModule.askPaintNote;
  var askPaintSent = askModule.askPaintSent;
  var askRetryButton = askModule.askRetryButton;
  var mergePendingSends = askModule.mergePendingSends;
  var pingAskWatching = askModule.pingAskWatching;
  var pingConvWatching = askModule.pingConvWatching;

  /* The step sheet lives in `steps.js` (issue #233). It is bound here, where
   * the block used to sit, because code further down reads its constants
   * while this file loads. The guard is for a cached tab that has `app.js`
   * from this build and no `steps.js` yet. */
  var stepsModule = window.novaSteps ? window.novaSteps({
    appendRichText: appendRichText,
    el: el,
    transitionMs: transitionMs,
  }) : {};
  var CAPTURE_SHEET_OPEN_VH = stepsModule.CAPTURE_SHEET_OPEN_VH;
  var STEP_SHEET_DISMISS_VH = stepsModule.STEP_SHEET_DISMISS_VH;
  var STEP_SHEET_MAX_VH = stepsModule.STEP_SHEET_MAX_VH;
  var STEP_SHEET_MIN_VH = stepsModule.STEP_SHEET_MIN_VH;
  var STEP_SHEET_OPEN_VH = stepsModule.STEP_SHEET_OPEN_VH;
  var dragSheet = stepsModule.dragSheet;
  var openStepSheet = stepsModule.openStepSheet;
  var refreshStepSheet = stepsModule.refreshStepSheet;
  var stepMessageKey = stepsModule.stepMessageKey;
  var stepsLabel = stepsModule.stepsLabel;
  var stepsLine = stepsModule.stepsLine;

  /* The chat bubble renderer lives in `bubble.js` (issue #233). Bound here,
   * where the block used to sit, after `ask.js` and `steps.js`, which it
   * reads from. The guard is for a cached tab that has `app.js` from this
   * build and no `bubble.js` yet. */
  var bubbleModule = window.novaBubble ? window.novaBubble({
    OWNER_RECORD: OWNER_RECORD,
    appendRichText: appendRichText,
    askCopyButton: askCopyButton,
    askRetryButton: askRetryButton,
    el: el,
    openMessageActions: openMessageActions,
    stepMessageKey: stepMessageKey,
    stepsLine: stepsLine,
  }) : {};
  var PENDING_CLOCK_AFTER_SECONDS = bubbleModule.PENDING_CLOCK_AFTER_SECONDS;
  var askElapsed = bubbleModule.askElapsed;
  var askMessage = bubbleModule.askMessage;
  var askOrbit = bubbleModule.askOrbit;
  var askPending = bubbleModule.askPending;
  var askPendingSeconds = bubbleModule.askPendingSeconds;
  var askQuip = bubbleModule.askQuip;
  var chatTime = bubbleModule.chatTime;

  /* The drawer those actions open in.
   *
   * A sheet per message would be a hundred hidden dialogs in a long thread
   * and only one can ever be open, so the sheets here are reused and
   * appended to <body> -- the same reasoning as the tool drawer's.
   *
   * One drawer implementation, instantiated twice.
   *
   * It was a single sheet and a set of module-level `msgSheet*` variables
   * until 2026-09-08, when his ask made a second one necessary: *"Make the
   * project picker be another modal on top of the other modal. They should
   * both be drawers like what we did for the chat."* The capture sheet
   * holds the type, the project and the importance; the project list is
   * long and belongs in its own drawer over the top of it, not inlined
   * into a sheet he then has to scroll past to reach the rating.
   *
   * A factory rather than a second copy of forty lines: the two differ by
   * a z-index and nothing else, and a hand-written twin is where the drag,
   * the slide and the backdrop quietly drift apart. Each instance owns its
   * own element, its own backdrop and its own hide timer, so opening the
   * upper one cannot close the lower one -- which is the whole point of
   * stacking them.
   *
   * They borrow the tool drawer's look through the shared rule list in the
   * stylesheet and their drag through `dragSheet`, so there is still one
   * drawer in this app wearing several hats rather than several drawers.
   */
  function makeActionSheet(opts) {
    var extra = (opts && opts.className) || "";
    /* The four numbers the drag works in, in `SHEET_UNIT`. They default to
     * what the message-actions drawer has always used -- two buttons do
     * not want half a screen -- and the two capture drawers pass the tool
     * sheet's instead, because a capture sheet carries three groups and a
     * project list carries a row per project.
     *
     * `open` and the drag spec MUST read the same `openVh`: `current()`
     * falls back to it when the node has no height, so a second copy that
     * drifted would put back the jump this is here to stop. */
    var openVh = (opts && opts.openVh) || 30;
    var minVh = (opts && opts.minVh) || 20;
    var maxVh = (opts && opts.maxVh) || 60;
    var dismissVh = (opts && opts.dismissVh) || 12;
    var sheet = null;
    var backdrop = null;
    var body = null;
    var titleEl = null;
    var hideTimer = null;
    var dragger = null;

    function build() {
      if (sheet) return sheet;
      backdrop = el("div", "msg-sheet-backdrop" + (extra ? " " + extra + "-backdrop" : ""));
      backdrop.hidden = true;
      backdrop.addEventListener("click", close);
      document.body.appendChild(backdrop);

      sheet = el("div", "msg-sheet" + (extra ? " " + extra : ""));
      sheet.hidden = true;
      sheet.setAttribute("role", "dialog");
      sheet.setAttribute("aria-modal", "true");
      sheet.setAttribute("aria-label", "Message actions");

      var grip = el("div", "msg-sheet-grip");
      grip.setAttribute("aria-hidden", "true");
      sheet.appendChild(grip);

      var head = el("div", "msg-sheet-head");
      var titles = el("div", "msg-sheet-titles");
      titleEl = el("h2", "msg-sheet-title", "Message");
      titles.appendChild(titleEl);
      head.appendChild(titles);
      var closeBtn = el("button", "msg-sheet-close", "\u2715");
      closeBtn.type = "button";
      closeBtn.title = "Close";
      closeBtn.setAttribute("aria-label", "Close");
      closeBtn.addEventListener("click", close);
      head.appendChild(closeBtn);
      sheet.appendChild(head);

      body = el("div", "msg-sheet-body");
      sheet.appendChild(body);
      document.body.appendChild(sheet);

      dragger = dragSheet([grip, head], {
        node: function () { return sheet; },
        openVh: openVh,
        minVh: minVh,
        maxVh: maxVh,
        dismissVh: dismissVh,
        onDismiss: close
      });
      return sheet;
    }

    /* `opts.closeOnPick` is false for a sheet that holds more than one
     * decision. The default is true and is the older rule: a message action
     * is finished the moment it is taken, so the drawer gets out of the
     * way. The capture sheet is not like that -- it carries the type, the
     * project and the importance of the line he is about to file, and
     * closing on the first tap would make him open it three times. */
    function open(actions, title, openOpts) {
      var closeOnPick = !(openOpts && openOpts.closeOnPick === false);
      /* Per-open, because one instance serves two contents: the same
       * drawer holds a message's two actions and the capture box's three
       * groups, and half a screen is right for exactly one of those. The
       * drag's floor, ceiling and dismiss point stay the instance's -- he
       * can always pull either one taller. */
      var height = (openOpts && openOpts.openVh) || openVh;
      build();
      titleEl.textContent = title || "Message";
      sheet.setAttribute("aria-label", title || "Message actions");
      body.textContent = "";
      actions.forEach(function (button) {
        // The buttons keep their own classes and their own handlers; only
        // the box around them is new.
        if (closeOnPick) {
          button.addEventListener("click", function () { close(); });
        }
        body.appendChild(button);
      });
      if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
      /* An explicit height, the way `openStepSheet` does it -- NOT
       * `style.height = ""`, which is what this was.
       *
       * His report, 2026-09-08: *"The capture box drawer modal is very
       * buggy when i try to drag it up and down. It jumps around on the
       * screen ... The project list modal also just flies off screen."*
       * Both are the same missing line. Left with no height the sheet is
       * as tall as its contents, and a list of thirteen projects is taller
       * than the phone -- so a drawer anchored to the bottom grew straight
       * off the top, grip, title and all (his screenshot: the list filling
       * the screen with no drawer around it).
       *
       * The jumping is the same fact seen through `dragSheet`. `current()`
       * reads the height off the node and falls back to `openVh` when
       * there is none, so the first pointermove computed its delta from 30
       * while the sheet was rendering at whatever its content measured --
       * the sheet snapped to the number the drag believed before it
       * started following his finger.
       *
       * The message-actions sheet survived this for a day because two
       * buttons happen to be shorter than a screen. That is a fixture, not
       * a design. */
      if (dragger) dragger.setHeight(height);
      sheet.classList.add("msg-sheet--entering");
      backdrop.classList.add("msg-sheet-backdrop--entering");
      backdrop.hidden = false;
      sheet.hidden = false;
      void sheet.offsetHeight;
      sheet.classList.remove("msg-sheet--entering");
      backdrop.classList.remove("msg-sheet-backdrop--entering");
    }

    function close() {
      if (!sheet || sheet.hidden) return;
      sheet.classList.add("msg-sheet--entering");
      backdrop.classList.add("msg-sheet-backdrop--entering");
      function hide() {
        hideTimer = null;
        if (sheet.classList.contains("msg-sheet--entering")) {
          sheet.hidden = true;
          backdrop.hidden = true;
        }
      }
      var wait = transitionMs(sheet);
      if (hideTimer) clearTimeout(hideTimer);
      if (wait) hideTimer = setTimeout(hide, wait + 20);
      else hide();
    }

    return { open: open, close: close, node: function () { return sheet; } };
  }

  /* The lower drawer: message actions, the capture sheet, the type list.
   * Every caller that existed before there were two of these. */
  var actionSheet = makeActionSheet({
    // Opens small -- two buttons -- but with the tool sheet's room to be
    // dragged, since the capture box opens this same drawer with three
    // groups in it and passes its own opening height.
    openVh: 30,
    minVh: STEP_SHEET_MIN_VH,
    maxVh: STEP_SHEET_MAX_VH,
    dismissVh: STEP_SHEET_DISMISS_VH
  });
  /* The upper one, and the only thing that opens over another drawer. Its
   * own backdrop dims the sheet underneath, which is what makes the stack
   * read as a stack rather than as one sheet that changed its mind. */
  var stackedSheet = makeActionSheet({
    className: "msg-sheet--stacked",
    openVh: STEP_SHEET_OPEN_VH,
    minVh: STEP_SHEET_MIN_VH,
    maxVh: STEP_SHEET_MAX_VH,
    dismissVh: STEP_SHEET_DISMISS_VH
  });

  function openMessageActions(actions, title, opts) {
    actionSheet.open(actions, title, opts);
  }

  /* The visible model picker, on the thread itself.
   *
   * His issue #143 lists *"a visible model picker"* among the controls the
   * chat is missing. `nova_conversations.set_model` has existed since idea
   * #95 and the only thing that ever called it is the row editor behind a
   * press-and-hold in the switcher -- so from the thread he is reading,
   * which model is answering him was neither visible nor changeable. This is
   * the same write, on the surface he is actually looking at.
   *
   * `(metered)` is on the label for `rowEditor`'s reason and it is a money
   * reason rather than a cosmetic one: a metered model spends the prepaid
   * balance per token, and a picker that hides which ones do would let him
   * spend it by reaching for the top of a list.
   *
   * A model the catalog does not list still gets an option, selected, so
   * opening the picker can never silently repoint a thread at something else
   * because Agora's catalog moved on.
   */
  function modelOption(value, label) {
    var node = document.createElement("option");
    node.value = value;
    node.textContent = label;
    return node;
  }

  /* A message that says its piece and leaves.
   *
   * His report, 2026-09-07, with a screenshot: a failed model switch wrote
   * "could not switch: ..." into a span inside the composer row, which
   * pushed Send off the edge of a 360px screen -- the error took away the
   * button he needed. Anything that can be one line of unpredictable length
   * does not belong in a row that also holds controls, so it goes over the
   * top of the page instead and clears itself after four seconds. */
  /* How long this node's own transition actually is, in ms.
   *
   * A close has to wait for the slide before `hidden` lands, because
   * `display: none` cancels a transition outright -- but where there is no
   * transition to wait for there is nothing to wait *on*, and the wait
   * becomes a quarter-second of a panel sitting on screen after he closed
   * it. That is a phone set to reduce motion (the sheet rules above are
   * switched off for it) and it is jsdom, which runs no transitions at all.
   * Both get the synchronous close they should have had. */
  function transitionMs(node) {
    try {
      var raw = window.getComputedStyle(node).transitionDuration || "";
      var longest = 0;
      raw.split(",").forEach(function (part) {
        part = part.trim();
        var n = parseFloat(part);
        if (isNaN(n)) return;
        longest = Math.max(longest, part.indexOf("ms") > -1 ? n : n * 1000);
      });
      return longest;
    } catch (err) {
      return 0;
    }
  }

  var TOAST_MS = 4000;
  var toastTimer = null;
  function toast(text, isError) {
    if (!text) return;
    var host = document.getElementById("nova-toast");
    if (!host) {
      host = el("div", "toast");
      host.id = "nova-toast";
      // Announced, not just drawn: this is the only place some failures
      // are ever reported.
      host.setAttribute("role", "status");
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    host.textContent = text;
    host.classList.toggle("toast--error", !!isError);
    host.classList.add("toast--on");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastTimer = null;
      host.classList.remove("toast--on");
    }, TOAST_MS);
  }

  /* The composer's model picker lives in `models.js` (issue #233). Bound here,
   * where the block used to sit. The guard is for a cached tab that has
   * `app.js` from this build and no `models.js` yet. */
  var modelsModule = window.novaModels ? window.novaModels({
    el: el,
    fetchPage: fetchPage,
    localStore: localStore,
    modelOption: modelOption,
    toast: toast,
  }) : {};
  var paintModelPicker = modelsModule.paintModelPicker;

  /* The ask thread renderer lives in `askthread.js` (issue #233). Bound here,
   * where the block used to sit, after `bubble.js` and every other name it
   * borrows. The guard is for a cached tab that has `app.js` from this build
   * and no `askthread.js` yet. */
  var askThreadModule = window.novaAskThread ? window.novaAskThread({
    OWNER_RECORD: OWNER_RECORD,
    PENDING_CLOCK_AFTER_SECONDS: PENDING_CLOCK_AFTER_SECONDS,
    appendRichText: appendRichText,
    askCopyButton: askCopyButton,
    askElapsed: askElapsed,
    askMessage: askMessage,
    askOrbit: askOrbit,
    askPending: askPending,
    askPendingSeconds: askPendingSeconds,
    askQuip: askQuip,
    askRetryButton: askRetryButton,
    chatTime: chatTime,
    el: el,
    openMessageActions: openMessageActions,
    openStepSheet: openStepSheet,
    refreshStepSheet: refreshStepSheet,
    stepMessageKey: stepMessageKey,
    stepsLabel: stepsLabel,
  }) : {};
  var renderAskThread = askThreadModule.renderAskThread;

  /* The Conversations page is gone -- his ask, 2026-09-07: *"I only use
   * that chat modal for the conversations, never the /chat page. So
   * actually, lets cut the /ask page or whatever the path is for the page
   * with the conversations and only keep the modal."*
   *
   * `/conversation/<id>` still resolves, because notifications already
   * delivered to his phone point at it and a dead link in a notification is
   * worse than a redundant route. It opens the dock now (see the router)
   * rather than a second, differently-shaped rendering of the same thread.
   * `renderConvThread`, `openConversation`, `pollConv` and the rest went
   * with the page; the dock's own versions were always the ones he used. */



  /* The diagnostics page and the settings page live in `diag.js` -- 23 KB of
   * it, moved out on 2026-09-18 because this file is what issue #233 is
   * about. It is called here rather than left to run on its own script tag
   * so the two names below are bound at the exact point in this file's body
   * where they used to be defined, which is before the boot at the bottom
   * draws the first route. The guard is for a cached tab that has `app.js`
   * from this build and no `diag.js` yet; without it the whole app would
   * fail to boot over one missing page. */
  var renderDiag, renderSettings;
  if (window.novaDiag) {
    var diagPage = window.novaDiag({
      PRIORITIES: PRIORITIES,
      el: el,
      feed: feed,
      getPrioMenuOverlay: getPrioMenuOverlay,
      markNav: markNav,
      menuOpen: menuOpen,
      navEl: navEl,
      setMenu: setMenu,
      statusEl: statusEl,
      stopPolling: stopPolling,
      wordmark: wordmark,
    });
    renderDiag = diagPage.renderDiag;
    renderSettings = diagPage.renderSettings;
  } else {
    renderDiag = function () {};
    renderSettings = function () {};
  }

  /* Put the capture composer back above the feed.
   *
   * The Notes page moves that one section *into* the feed so the box sits
   * under the conversation (`renderNotes`, and the owner's ask that the
   * conversation be above the input box). Every renderer's first act is
   * `feed.textContent = ""`, so a navigation away from Notes that left it
   * there would delete the only composer in the document -- along with
   * the handlers `captureBox()` bound to it at startup, which nothing
   * re-binds. Hence: home first, then render. Doing it here rather than
   * in each renderer means a page added later cannot forget.
   */
  function captureHome() {
    var capture = document.getElementById("capture");
    if (capture && capture.parentNode !== feed.parentNode) {
      feed.parentNode.insertBefore(capture, feed);
    }
  }

  /* Which route `load` last painted a placeholder for.
   *
   * The placeholder is for *navigation* only. `load()` is also how the page
   * refreshes itself in place -- after a board edit, after a comment is
   * posted, after the heartbeat page acts -- and flashing "Loading…" over a
   * page the owner is already standing on would be a new flicker traded for
   * a fixed one. Comparing the route against the last painted one tells the
   * two apart with no new call site to keep in sync. */
  var paintedRoute = null;

  /** Everything in a route that decides which page is on screen. */
  function routeKey(here) {
    return [
      here.view,
      here.board || "",
      here.project || "",
      here.cycle === null || here.cycle === undefined ? "" : here.cycle,
      here.conversationId || "",
      here.asks ? "asks" : "",
    ].join("|");
  }

  /* The one frame between the tap and the payload.
   *
   * The name comes from the nav tab `markNav` has just highlighted rather
   * than from a table of its own: it is the word the owner tapped, so the
   * two can never disagree, and a page added to the drawer needs nothing
   * added here. A route with no tab of its own (a single cycle, one
   * project) highlights its parent tab, which is still the honest answer to
   * "what is loading". */
  function showLoading() {
    var on = navEl ? navEl.querySelector(".nav-tab.on") : null;
    var name = on ? on.textContent.trim() : "";
    feed.textContent = "";
    feed.appendChild(el("p", "empty", name ? "Loading " + name + "…" : "Loading…"));
  }

  function load() {
    // The overlay is fixed to the viewport and the feed under it is about
    // to be replaced, so a navigation that left it open would strand a
    // chart of the old page on top of the new one -- and the figure it
    // points at is gone, so nothing could close it.
    closeFullChart();
    /* And the action sheet, for a sharper version of the same reason. Its
     * buttons close over one board, one index and one capture's text, so a
     * sheet stranded over the next page is a live Delete pointing at a row
     * that is no longer on screen -- and `loadBoard` would early-return on
     * the repaint, because the URL no longer matches, leaving the failure
     * written into a status node `renderNotes` has already destroyed.
     *
     * An in-app link is safe by accident today: the delegated click
     * handler runs after the sheet's own document-capture listener has
     * closed it. `popstate` is not, and the phone back gesture is
     * `popstate`. Found reviewing the merged diff. */
    closeActionSheet();
    captureHome();
    stopScrollWatch();
    var here = route(window.location.pathname);
    /* The capture box belongs to the landing page now.
     *
     * His capture, 2026-09-13: *"remove the input components for ideas and
     * issues on all other pages than the homepage."* It used to ride every
     * page -- `captureHome()` above exists to put it back above the feed
     * wherever a page had moved it -- which meant the board pages carried two
     * ways in: the box at the top and the row editors below it.
     *
     * Hidden rather than removed from the document: `captureHome()` and the
     * composer's own handlers hold references to `#capture`, and a node that
     * exists and is hidden keeps every one of them true. */
    var captureBox = document.getElementById("capture");
    if (captureBox) {
      if (here.view === "home") captureBox.removeAttribute("hidden");
      else captureBox.setAttribute("hidden", "");
    }
    /* The unread-reply badge belongs to the journal and nowhere else.
     *
     * It used to be fetched and painted on every page -- cycle 474's reading
     * of his 2026-08-25 capture, *"I want to have a status the Nova header if
     * i have unread Journal comments"*. He reversed that on 2026-09-13:
     * *"remove the status pills related to journals like '21 new replies' or
     * other from all other pages than the Journal page."* So the badge is a
     * journal-page feature again, and every other view clears the node on the
     * way in rather than leaving whatever the last page painted standing over
     * a page it says nothing about.
     *
     * The ask pill ("N waiting on you") needed no change: it is drawn inside
     * `renderStatus`, which only the journal view calls. */
    if (here.view !== "journal" && mailEl) {
      mailEl.textContent = "";
      mailEl.setAttribute("hidden", "");
    }
    /* Answer the tap now, before anything is fetched.
     *
     * the owner, capture 2026-09-05, rated Immediately: *"The Nova app is
     * very slow on my phone when i navigate between pages. I have to click
     * multiple times on the sidebar buttons before it actually switch to
     * that page. I want it to be instant and also show the content
     * instantly."*
     *
     * The second sentence is the bug and the first sentence is what it
     * looks like. Every branch below fetches and paints nothing until the
     * response lands, so between the tap and the payload the screen is the
     * *previous* page, unchanged, with the previous tab still highlighted.
     * There was no signal at all that the tap had been received, so the
     * only reasonable thing to do is tap again.
     *
     * Measured in Chromium at his own 360x697 against the live pod, from
     * inside the cluster (`tools.poke_page`'s browser, 2026-09-05): tapping
     * Issues from the journal changed nothing on screen for 291ms, and
     * tapping Ideas from Issues for 705ms. That is the floor -- his phone
     * adds the tailnet round trip and a phone's own CPU to payloads that
     * are 75KB for Issues and 111KB for Ideas.
     *
     * The journal branch already called `markNav` before its fetch, and in
     * the same run its highlight moved in 52ms against 3400ms for its
     * content. So this is not a new mechanism; it is the one branch that
     * had it, moved up to cover all of them. */
    markNav();
    var key = routeKey(here);
    if (key !== paintedRoute) {
      paintedRoute = key;
      showLoading();
    }
    if (here.view === "board") {
      loadBoard(here.board);
      return;
    }
    if (here.view === "home") {
      loadHome();
      return;
    }
    if (here.view === "notes") {
      loadNotes();
      return;
    }
    if (here.view === "pool") {
      loadPool();
      return;
    }
    if (here.view === "projects" || here.view === "project") {
      loadProject(here.project);
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
    if (here.view === "plan") {
      loadPlan();
      return;
    }
    if (here.view === "conversations") {
      /* The URL still exists -- notifications already delivered to his
       * phone point at it -- but it is a way into the dock now rather than
       * a page of its own. The address bar is put back to `/` so a reload,
       * or a back-button, does not reopen the panel over whatever he
       * navigated to afterwards. */
      if (typeof window.novaOpenChat === "function") {
        window.novaOpenChat(here.conversationId);
        /* `/journal` and not `/`, since idea #274 moved the feed there. The
         * address bar has to match what is actually drawn under the dock,
         * and the fall-through below draws the feed -- pointing it at `/`
         * would make the guard in that branch (`view !== "journal"`) true
         * and leave the dock standing over an empty page. */
        try { history.replaceState(null, "", "/journal"); } catch (err) { /* no history */ }
        // Deliberately no `return`: the address bar now says `/journal`, so
        // this falls through to the journal below and the feed loads behind
        // the open dock. Returning here would leave the page empty under it.
      }
    }
    if (here.view === "heartbeats") {
      loadHeartbeats();
      return;
    }
    if (here.view === "catalog") {
      loadCatalog();
      return;
    }
    if (here.view === "alerts") {
      loadAlerts();
      return;
    }
    // No `loadDiag` -- this is the one view with no payload behind it, so
    // there is nothing to fetch and nothing that can fail on the way.
    if (here.view === "diag") {
      renderDiag();
      return;
    }
    // Same shape as `/diag`: nothing on this page comes from the server, so
    // there is no payload to fetch and nothing that can fail on the way.
    if (here.view === "settings") {
      renderSettings();
      return;
    }
    if (here.view === "galaxy") {
      loadGalaxy();
      return;
    }
    // `markNav` used to be here, on the journal branch alone. It is at the
    // top of this function now, so every branch gets it.
    fetchAll()
      .then(function (results) {
        // Same guard, other direction: a board fetch started before a tap
        // on Journal must not land after this one.
        if (route(window.location.pathname).view !== "journal") return;
        setChatAnswered(results[3]);
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

  /* the owner, issues.md 2026-08-10: "Nova takes a long time to load when i
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
  /* The chat-answered map the feed on screen was built from -- see the
   * poll's `changed` below. */
  var renderedChat = null;
  var pollTimer = null;
  /* Whether a poll's fetch is outstanding. Read only by `resume` below --
   * the timer cannot overlap itself, because it is only ever rescheduled
   * once the previous round has settled. */
  var polling = false;

  function typing() {
    /* A keystroke in the search box whose request has not gone out yet.
     * The 200ms debounce exists so a word is searched once rather than
     * seven times, and the 30-second poll runs straight through it: it
     * asks `journalUrl()`, which reads the box, so a timer landing between
     * "ingr" and "ingress" fetches the results for "ingr" and renders
     * them -- the debounce defeated by an unrelated timer, and the feed
     * repainted around a word he had not finished.
     *
     * The pending timer is the signal rather than the box's contents: a
     * search that has already been asked for and answered is a perfectly
     * good thing to poll, and blocking on any text at all would freeze the
     * feed for as long as the box was non-empty. */
    if (journalSearchTimer !== null) return true;
    var boxes = document.querySelectorAll("textarea");
    for (var i = 0; i < boxes.length; i++) {
      if (boxes[i].value.trim()) return true;
    }
    return false;
  }

  /* `resumed` is true when this poll is the app being opened rather than
   * the background timer coming round. The difference is the `typing()`
   * deferral below, and it is the whole of the owner's report that the
   * page shows an old time when he opens it.
   *
   * `typing()` looks at every textarea on the page, and every journal card
   * carries a comment drawer -- so one half-typed reply, in a drawer that
   * is closed and off screen, defers every poll for the life of the tab.
   * It is in-memory (`drafts`), so a reload clears it and nothing on the
   * page ever says why. That deferral is right for a timer firing while he
   * is mid-sentence and wrong for the moment he opens the app: he is
   * plainly not typing then, and a render no longer loses the text anyway
   * -- `drafts` restores it into the rebuilt drawer, and an open drawer
   * stays open.
   *
   * I could not reproduce his exact tab, so this is the class of failure
   * rather than a confirmed single cause; the other half of the fix is the
   * event list below. */
  function poll(resumed) {
    // The poll is the journal's. On a board page it would fetch the feed
    // and render it straight over the list -- the same "never interrupt"
    // rule the typing check below exists for, one level up.
    if (route(window.location.pathname).view !== "journal") return schedulePoll();
    if (document.hidden) return schedulePoll();
    if (typing() && !resumed) return schedulePoll();
    polling = true;
    fetchAll()
      .then(function (results) {
        var journal = results[0];
        var comments = JSON.stringify(results[2]);
        // An answer he typed into a cycle's thread changes nothing in the
        // journal or the comments payload, so without this the badge would
        // stay up until some other thing moved.
        //
        // Compared as the normalised map rather than as the raw payload:
        // a failed read and an empty answer are both "nothing is answered
        // in chat", and stringifying the two payloads would call that a
        // change on every poll that failed.
        setChatAnswered(results[3]);
        var chat = JSON.stringify(chatAnsweredCycles);
        // Normalised the same way `render` stores it. A payload with no
        // `version` at all -- an older server, or the tailnet serving the
        // last build's response to this build's app.js -- would otherwise
        // compare `undefined` against `null` and count as changed on every
        // single poll, throwing away every open drawer twice a minute.
        var version = (journal && journal.version) || null;
        var replayedNow = !!(journal && journal.status && journal.status.replayed);
        var changed = version !== renderedVersion || comments !== renderedComments
          || chat !== renderedChat || replayedNow !== renderedReplayed;
        // Re-checked after the fetch as well as before it: a request takes
        // long enough for him to have started typing during one.
        // A poll that came back is the only thing that clears the header's
        // error state, and it clears it by rendering the answer it just got
        // -- below, or on the next change. Reset here rather than inside the
        // `changed` branch: an unchanged payload is still a reachable
        // server, and that is the case that would otherwise stay red
        // forever once the loop went quiet.
        if (pollFailures >= POLL_FAILURES_BEFORE_STALE) {
          // Given the comments this poll just fetched, not the ones from
          // whenever the page last re-rendered: coming back online is
          // exactly when his reply is the news, and re-drawing the header
          // with a stale answer set would leave the ask pill up.
          //
          // `results[2]` and not `comments`, which is that payload already
          // serialised for the change comparison -- a string has no
          // `byCycle`, so reading it there would silently hand the header
          // an empty answer set and put the pill back up on every ask.
          renderStatus(journal.status || {}, results[2] ? (results[2].byCycle || {}) : null);
        }
        pollFailures = 0;
        if (changed && (resumed || !typing())) {
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
       * one of those would be the same flash-and-retract the owner reported in
       * the first place. Two consecutive misses is 30 seconds of a server
       * that is genuinely not answering, which is the thing worth showing
       * and is not something a handover between cells produces. */
      .catch(function (err) {
        pollFailures += 1;
        if (pollFailures >= POLL_FAILURES_BEFORE_STALE) {
          renderStatusUnreachable(fetchFailureDetail(err));
        }
      })
      // Both arms, not just the resolved one: a throw inside the `catch`
      // above would otherwise skip `schedulePoll`, and `polling` is now
      // what gates every resume -- so the page would stop catching up
      // permanently, with nothing on screen saying why. Before the resume
      // work that was only a lost timer.
      .then(schedulePoll, schedulePoll);
  }

  function schedulePoll() {
    polling = false;
    if (pollTimer) clearTimeout(pollTimer);
    // `setTimeout(poll, ...)` hands the timer id to `poll` as its first
    // argument in some runtimes, which would read as `resumed`. Wrapped so
    // a scheduled poll is always the ordinary kind.
    pollTimer = setTimeout(function () { poll(); }, POLL_MS);
  }

  /* Four ways an app comes back, and this file listened for one of them.
   *
   * `visibilitychange` is the phone case and it is the one that was here.
   * It is not the only one: a page restored from the back/forward cache
   * fires `pageshow` with `persisted` set and need never have gone hidden,
   * a window that regains focus without a visibility transition fires only
   * `focus`, and a phone that comes back on a network fires `online` while
   * already visible -- in that last case the timer is running and the next
   * catch-up is up to 30 seconds away, which is exactly the wait he is
   * describing. Each of these is one line and none of them costs anything
   * when the page is already current: the fetch is conditional and a 304
   * carries no body.
   *
   * They overlap on purpose -- opening the app fires two of them -- and
   * they arrive in separate tasks, so `resume` skips while a poll is
   * already in flight rather than trying to debounce on a timer. Two
   * concurrent polls are not merely wasteful: they render in completion
   * order, so the older answer can land last and put the stale header
   * back. */
  /* `wasAway` is what licenses skipping the `typing()` deferral, and only
   * two of the four events carry it. Coming back to a tab that was hidden,
   * or restoring one out of the back/forward cache, both mean he was not
   * at the keyboard. `focus` fires on an ordinary window switch and
   * `online` on a wifi blip, either of which can land while he is
   * mid-sentence in a drawer he is looking at -- so those two ask for a
   * poll and still defer to the box he is typing in. Reviewer finding on
   * runner#332; my own comment there had reasoned about the backgrounded
   * tab and then wired all four through it. */
  function resume(wasAway) {
    if (document.hidden || polling) return;
    // The armed timer is the other half of the in-flight guard: without
    // this it can fire during a slow resumed fetch and start a second one,
    // which is the race the guard is here to stop. `schedulePoll` at the
    // end of this round re-arms it.
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = null;
    poll(!!wasAway);
  }

  /* The fifth thing that asks for a poll, and the only one not driven by
   * him: `sw.js` telling the page that the copy it painted from the cache
   * has been overtaken. A global for the same reason `novaThreadUpdated` is
   * one -- the worker's message listener is registered outside this
   * closure. */
  window.novaApiUpdated = function () { resume(false); };

  document.addEventListener("visibilitychange", function () { resume(true); });
  window.addEventListener("pageshow", function (event) {
    if (event && event.persisted) resume(true);
  });
  window.addEventListener("focus", function () { resume(false); });
  window.addEventListener("online", function () { resume(false); });

  /* The capture box (item 6). A type button, a project button, priority,
   * attach, and a Submit hard against the right edge.
   *
   * **It was one button per target until 2026-09-08**, and that was the
   * right shape while there were three: a target button *was* the submit,
   * which is one tap fewer on a phone, and the Note button cost one line
   * of HTML rather than a redesign. His ask, with a screenshot of the box:
   * *"The issues, ideas, notes and project buttons are merged into one
   * button and opens a modal from the bottom with the different options
   * (idea, note, issue and the new project) and i can select there. I
   * actually want a new submit button that is all the way to the right.
   * The priority button and attach button should stay like they are."*
   *
   * A fourth target is what breaks the old shape: four destination buttons
   * plus priority plus attach do not fit one row at 390px, and the project
   * selector he also asked for is a fifth control. So the two *choices*
   * collapse into two buttons that open sheets, and the act of filing gets
   * its own button.
   *
   * The one thing that had to go with it: pressing a type must no longer
   * submit. Two submit paths that can disagree about the selected type is
   * a capture filed as the wrong kind, which is exactly the failure the
   * Enter key used to cause.
   *
   * Nothing here still names the targets: the sheet rows come from the
   * shipped HTML and `send` uses whatever `data-target` the selected one
   * carries. */
  (function captureBox() {
    var form = document.getElementById("capture-form");
    if (!form) return;
    var textEl = document.getElementById("capture-text");
    var captureStatus = document.getElementById("capture-status");
    var buttons = Array.prototype.slice.call(form.querySelectorAll(".capture-btn"));
    var typeBtn = document.getElementById("capture-type");
    var sendBtn = document.getElementById("capture-send");
    var NO_PROJECT = "No project";
    /* Last-used, not none. He files three issues about the same thing in a
     * row, so re-picking the project every time is a tax on the common
     * case; and unlike the rating, a wrong project is a cell a cycle
     * fixes rather than a claim about urgency. It resets to the last
     * choice and not to nothing after a send, for the same reason. */
    var currentTarget = buttons.length ? buttons[0].getAttribute("data-target") : "issues";
    var currentProject = "";

    /* the owner, issues.md 2026-08-14: "i want that aswell both when i input
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
      ariaLabel: "Importance",
      caption: "Importance the row inherits when this capture is boarded.",
      triggerClass: "capture-prio",
      triggerId: "capture-prio",
      onPick: function () {},
    });
    /* Appended last, so it renders at the far right of the button row,
     * on the same line as Issue/Idea/Note (the owner, 2026-08-14). issues.md
     * 2026-08-14 split this into its own row above the buttons because at
     * 390px the select was 136px wide -- "🔴 Immediately" set its
     * intrinsic width -- which left room for exactly one of the three
     * buttons on the first line. That measurement no longer holds: the
     * control is a fixed 44px circle now, not a word, so it rejoins the
     * group it was split out of. See `.capture-submit` in style.css. */

    /* The same attach button the comment drawer gets, on the box that
     * files an issue, an idea or a note -- which is the rest of the owner's
     * list, *"next to a comment, issue, note or idea"*.
     *
     * **After the three targets and before the picker**, which is not
     * where I first put it. I prepended it, on the argument that Issue /
     * Idea / Note are three *destinations* and a fourth control among them
     * would read as a fourth place to file -- so it belonged before the
     * choice, which is also when it is used. A browser test caught that
     * immediately, and it was pinning something the owner asked for: *"The
     * issue, idea, note and priority dropdown are now just scrambled"*,
     * and the row was rebuilt so the three targets come first and the
     * picker sits at the right edge. Prepending broke the first half.
     *
     * This position keeps both. The picker is still the last child and the
     * three targets are still the first three; the attach button takes the
     * one slot between them that neither rule claims. The hidden <input>
     * goes on the form rather than in this group, so it does not count as
     * a child of a row whose child count is itself pinned.
     *
     * One thing this does not yet do: the bullet it writes is rendered on
     * the board as the literal `![…](/api/upload/…)` text, because a
     * capture goes through the server's markdown span parser and that
     * parser has no image span. The bytes are stored and I can read them,
     * which is the half he asked for; the picture showing up on his own
     * board is filed separately. */
    var captureAttach = window.novaAttach.build({
      // All three destinations, not just one: whichever he taps mid-upload
      // files the text without the image. Same race as the comment drawer.
      onBusy: function (isBusy) {
        // The Submit button, not the type rows: those live in a sheet now
        // and choosing a type mid-upload is harmless. Filing is the thing
        // that would race the upload.
        setBusy(isBusy);
      },
      onStatus: setStatus,
    });
    var submitRow = document.querySelector(".capture-submit");
    /* The picker and its label park in the same hidden host the type
     * buttons ship in, and the sheet moves all three out of it. They have
     * to be IN the document from load: `#capture-prio` is the trigger
     * `/diag` opens to prove the health check leaves an open picker alone,
     * and a control that only exists while a drawer is open is a control
     * nothing else can reach. */
    var typesHost = document.getElementById("capture-types");
    var prioLabel = document.querySelector(".capture-prio-label");
    if (typesHost) {
      if (prioLabel) typesHost.appendChild(prioLabel);
      typesHost.appendChild(prioPicker.el);
    }
    form.appendChild(captureAttach.input);
    // Directly under the box he typed in, above the row of controls --
    // the thumbnails belong to the sentence, not to the buttons.
    textEl.parentNode.insertBefore(captureAttach.tray, textEl.nextSibling);
    /* The attach `+` immediately before Send, and nothing else in this row.
     * His screenshot, 2026-09-08: five controls in it and the leftmost one
     * cut off the side of a 390px screen. The type, the project and the
     * importance are three *decisions* and they moved into one sheet
     * together; what stays on the row is the one that opens it, the one
     * that adds a picture, and the one that files the line.
     *
     * `prioPicker.el` is deliberately NOT inserted anywhere. The picker
     * object is still what holds the value `send` reads -- only its trigger
     * is gone, replaced by the rows the sheet draws. */
    submitRow.insertBefore(captureAttach.button, sendBtn);


    /* the owner, issues.md 2026-08-09: "the input box for the Nova pwa is too
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

    /* "Keep this as one item". The owner, via Sokrates, issues.md #123: a
     * thirteen-paragraph paste about the NAS filed as thirteen separate
     * unboarded captures, because `clean_capture_text` makes every line
     * its own bullet -- *"a long paste into the capture box has no way to
     * signal 'this is one issue' short of avoiding blank lines entirely."*
     *
     * It is shown only while the box holds more than one non-blank line.
     * That is not tidiness: the control has no meaning on a one-line
     * capture, and this box is opened on a phone dozens of times a day to
     * type one line, so a permanently visible checkbox would cost every
     * one of those a row of height and a decision to ignore. Counting
     * non-blank lines is the same question `clean_capture_text` asks, so
     * the checkbox appears exactly when the split would actually happen.
     *
     * The attachment tray is deliberately not counted. An image already
     * folds onto the sentence beside it, so a sentence plus a screenshot
     * is one bullet either way and offering the toggle there would be
     * offering a choice that changes nothing. */
    var oneRow = document.getElementById("capture-one");
    var oneInput = document.getElementById("capture-one-input");
    function fitOne() {
      var lines = textEl.value.split("\n").filter(function (line) {
        return line.trim() !== "";
      });
      var many = lines.length > 1;
      if (oneRow) oneRow.hidden = !many;
      // Unchecking on the way out matters: he clears the box, types one
      // line the next hour, and the hidden checkbox would still be on.
      if (!many && oneInput) oneInput.checked = false;
    }

    function setStatus(text, isError) {
      captureStatus.textContent = text;
      captureStatus.className = isError ? "capture-status is-error" : "capture-status";
    }

    /* Submit is inert on an empty box, and that is a real state rather
     * than a cosmetic one: the old design had no submit at all, so
     * "nothing to file" could only be discovered by tapping a
     * destination. `count()` is why an image with no sentence still
     * enables it -- a screenshot on its own is a capture worth filing,
     * the same guard `send` has always carried. */
    var busy = false;
    function setBusy(isBusy) {
      busy = isBusy;
      fitSend();
    }
    function fitSend() {
      if (!sendBtn) return;
      var has = textEl.value.trim() !== "" || captureAttach.count() > 0;
      sendBtn.disabled = busy || !has;
    }

    function setTarget(target) {
      var picked = null;
      buttons.forEach(function (b) {
        if (b.getAttribute("data-target") === target) picked = b;
      });
      if (!picked) return;
      currentTarget = target;
      if (typeBtn) typeBtn.textContent = picked.textContent;
    }

    /* The project has no button of its own on the row any more, so this
     * only remembers it -- the sheet is where it is read back, and `send`
     * is what does something with it. */
    function setProject(name) {
      currentProject = name || "";
    }

    function send(target) {
      var text = textEl.value.trim();
      // A screenshot with no sentence under it is still a capture worth
      // filing -- see the same guard in the journal drawer's `submit`.
      if (!text && !captureAttach.count()) {
        textEl.focus();
        return;
      }
      var body = [text, captureAttach.markdown()].filter(Boolean).join("\n\n");
      setBusy(true);
      setStatus("saving…", false);
      fetch("/api/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target: target,
          text: body,
          priority: prioPicker.getValue(),
          project: currentProject,
          oneItem: !!(oneInput && oneInput.checked),
        }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          textEl.value = "";
          captureAttach.clear();
          prioPicker.setValue("");
          fit();
          fitOne();
          setStatus("saved to " + target, false);
          // The capture may be the top bullet of a file the feed shows.
          load();
        })
        .catch(function (err) { setStatus(String(err.message || err), true); })
        .then(function () { setBusy(false); });
    }

    /* Choosing a type updates the button and leaves the sheet open. It does
     * not submit -- that is the whole point of the rework, and the reason
     * the old handler on these very buttons had to go. */
    // Set by the sheet while it is built, so a type tap refreshes the marks
    // in the other two groups' company. Registered once here rather than
    // per open, which would stack a listener every time he opened it.
    var refreshMarks = null;
    buttons.forEach(function (button) {
      button.addEventListener("click", function () {
        setTarget(button.getAttribute("data-target"));
        if (refreshMarks) refreshMarks();
      });
    });

    /* One sheet for all three choices -- his ask, 2026-09-08, after the
     * row they were spread across ran off the side of his phone.
     *
     * It is three ROWS, each opening a drawer of its own over this one --
     * his follow-up the same morning: *"Make the types (issue, idea...)
     * also open as a drawer modal like the project list ... make the
     * priority a drawer modal like the rest."* The project list went first
     * because it is unbounded, and the reason generalises: an option list
     * inlined here pushes the groups below it off a drawer that opens
     * around half a screen, so the field he set two taps ago scrolls out
     * of sight. Three rows that each say what they are set to fit in any
     * drawer and read as a summary of the capture he is about to file.
     *
     * Nothing closes this sheet on a tap. The upper drawer does close on
     * one, because it holds a single decision and this is where he comes
     * back to. */
    function heading(text) {
      return el("p", "capture-sheet-head", text);
    }

    function optionRow(label, isPicked, onPick) {
      var row = el("button", "capture-btn", label);
      row.type = "button";
      row.setAttribute("aria-pressed", isPicked ? "true" : "false");
      row.addEventListener("click", onPick);
      return row;
    }

    /* A row on the capture sheet: the value it is currently set to, and a
     * drawer behind it. `data-field` is what the browser tests reach for --
     * three rows that differ only by their text are three rows nothing can
     * tell apart. */
    function fieldRow(field, value, open) {
      var row = optionRow(value, false, open);
      row.removeAttribute("aria-pressed");
      row.setAttribute("aria-haspopup", "dialog");
      row.setAttribute("data-field", field);
      return row;
    }

    function targetLabel() {
      var picked = null;
      buttons.forEach(function (b) {
        if (b.getAttribute("data-target") === currentTarget) picked = b;
      });
      return picked ? picked.textContent : currentTarget;
    }

    function prioLabelText() {
      return prioPicker.getValue() || "Unrated";
    }

    function openCaptureOptions() {
      /* Built fresh each open, because the project list is whatever the
       * board holds right now and a cycle can have added one since the page
       * loaded. `loadProjects` is the same cached index the row editor's
       * picker uses, so this costs one request per session. */
      loadProjects().then(function (names) {
        var rows = [];
        var typeRow = fieldRow("type", targetLabel(), function () {
          /* The real buttons out of the shipped HTML, moved into the upper
           * drawer -- so there is one set of type buttons in this document,
           * not two that can drift from `CAPTURE_TARGETS`. They carry their
           * own handler, registered once, which is what sets the target. */
          buttons.forEach(function (b) {
            b.setAttribute("aria-pressed",
              b.getAttribute("data-target") === currentTarget ? "true" : "false");
          });
          stackedSheet.open(buttons, "Type");
        });
        var projectRow = fieldRow("project", currentProject || NO_PROJECT, function () {
          openProjectSheet(names || []);
        });
        var prioRow = fieldRow("priority", prioLabelText(), function () {
          openPrioritySheet();
        });

        rows.push(heading("Type"), typeRow);
        rows.push(heading("Project"), projectRow);
        rows.push(heading("Importance"), prioRow);

        /* Re-read after every pick in the upper drawer. The sheet stays
         * open underneath it, so a row still showing the previous value
         * would be the only thing on screen and it would be wrong. */
        refreshMarks = function () {
          typeRow.textContent = targetLabel();
          projectRow.textContent = currentProject || NO_PROJECT;
          prioRow.textContent = prioLabelText();
        };
        openMessageActions(rows, "Capture details",
          { closeOnPick: false, openVh: CAPTURE_SHEET_OPEN_VH });
      });
    }

    /* The upper drawer, three times over. All three close on a pick,
     * unlike the sheet underneath: each holds a single decision, and the
     * sheet it drops back to is where the other two are made.
     *
     * Alphabetical, his ask 2026-09-08. The board's own order is a
     * priority ranking, which is the right order on the board and the
     * wrong one here: this is a list he scans for a name he already has in
     * mind, and the only order that helps is the one his eye can
     * binary-search. `localeCompare` and not `<`, because "Ålesund" sorting
     * after "Zulu" is a Norwegian alphabet answering in ASCII. `No project`
     * is prepended after the sort, so it stays the first row rather than
     * filing itself under N. */
    function openProjectSheet(names) {
      var sorted = (names || []).slice().sort(function (a, b) {
        return String(a).localeCompare(String(b));
      });
      var rows = [""].concat(sorted).map(function (name) {
        return optionRow(name || NO_PROJECT, name === currentProject, function () {
          setProject(name);
          if (refreshMarks) refreshMarks();
        });
      });
      stackedSheet.open(rows, "Project");
    }

    /* The rating, as a drawer instead of the `.prio-menu` popup it opened
     * before.
     *
     * `prioPicker` still owns the value: `send` reads it, the reset after a
     * successful file writes it, and `PRIORITIES` is the vocabulary both
     * this and `.prio-menu` spell out, so there is one list of ratings in
     * this app. What its trigger no longer does is appear -- the row on the
     * capture sheet shows the rating now. The element stays parked in the
     * hidden `#capture-types` host, and that is a wart I am flagging rather
     * than leaving quiet: three tests reach for `#capture-prio` as a real,
     * openable picker (including `/diag`'s guard that the health check
     * leaves an open one alone), and repointing those at the board-capture
     * picker is a change of its own rather than a line in this one. */
    function openPrioritySheet() {
      var rows = PRIORITIES.map(function (label) {
        return optionRow(label || "\u2013 Unrated", label === prioPicker.getValue(), function () {
          prioPicker.setValue(label);
          if (refreshMarks) refreshMarks();
        });
      });
      stackedSheet.open(rows, "Importance");
    }

    if (typeBtn) typeBtn.addEventListener("click", openCaptureOptions);
    if (sendBtn) {
      sendBtn.addEventListener("click", function () { send(currentTarget); });
    }
    form.addEventListener("submit", function (event) { event.preventDefault(); });
    /* Enter is a newline. The owner, issues.md #90: *"When i press enter on my
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
        // The selected type, now that there is one. It used to have to
        // guess, and guessed `issues` for a box with three buttons.
        send(currentTarget);
      }
    });
    textEl.addEventListener("input", fit);
    textEl.addEventListener("input", fitOne);
    textEl.addEventListener("input", fitSend);
    setTarget(currentTarget);
    setProject("");
    fitSend();
    fit();
    // Both on load, not just `fit`: a browser restores a textarea's value
    // across a refresh, so the box can already hold a paste before he has
    // typed a character and fired an `input` event.
    fitOne();
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

  /* "A new version is ready" -- his ask, 2026-09-07, pointing at Marcus,
   * which has carried this since its own deploys started going unnoticed.
   *
   * A deploy is invisible to an app that is already open: `sw.js` calls
   * `skipWaiting` and `clients.claim`, so the new worker takes over, but
   * this page goes on running the `app.js` it loaded. `controllerchange` is
   * the moment of that swap.
   *
   * `hadController` is what keeps a first visit silent -- the very first
   * worker claiming the page fires the same event, and announcing "a new
   * version" to someone who just opened the app for the first time is a
   * banner that means nothing. It is set after the first event rather than
   * only read, because a second deploy in the same sitting IS a real update
   * and has to announce itself. */
  function watchForUpdate(sw, onUpdate) {
    if (!sw || typeof sw.addEventListener !== "function") return;
    var hadController = !!sw.controller;
    sw.addEventListener("controllerchange", function () {
      if (hadController) onUpdate();
      hadController = true;
    });
  }

  /* The browser only re-checks `sw.js` on a navigation, and an installed PWA
   * that is left open and switched back to never navigates -- which is
   * exactly how he uses this. Asking on every return to the foreground is
   * what makes the banner appear without him reloading first, which would
   * rather defeat it. */
  function recheckOnVisible(registration) {
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState !== "visible") return;
      try {
        Promise.resolve(registration.update()).catch(function () {});
      } catch (err) { /* a browser that throws synchronously */ }
    });
  }

  function showUpdateBanner() {
    var host = document.getElementById("update-banner");
    if (host) host.hidden = false;
  }

  var updateReload = document.getElementById("update-reload");
  if (updateReload) {
    updateReload.addEventListener("click", function () { window.location.reload(); });
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then(function (registration) {
      subscribeToPush(registration);
      if (registration) recheckOnVisible(registration);
      return registration;
    }).catch(function () {});
    watchForUpdate(navigator.serviceWorker, showUpdateBanner);
    /* The worker retracting a thread it served him out of its prefetch cache.
     *
     * `sw.js` parks the conversation a push notification is about, then hands
     * that parked copy straight back on the tap so the message is on screen
     * with no round trip. That is only honest if something corrects it when
     * the parked copy is old -- a banner tapped an hour later, a reply that
     * finished after the push -- so the worker refetches behind the answer it
     * gave and posts this when the two differ. It says nothing when they
     * match, which is the ordinary case, so there is no flicker on the fast
     * path this exists to make fast.
     *
     * Guarded on the thread being the one still open, the same way `pollConv`
     * is: he can back out and open another one while the revalidation is in
     * flight, and a late answer must not repaint the thread he is reading now
     * with the messages of the one he left.
     */
    /* The worker retracting an API payload it served from last time's copy.
     *
     * On a reopen the page has no etag in memory, so `sw.js` answers from
     * its cache and asks the server behind that answer -- conditionally, so
     * the usual reply is an empty 304 and nothing is repainted. This fires
     * only when the fresh copy actually differs, which is the reopen that
     * lands after a cycle wrote something.
     *
     * `resume(false)` rather than a fetch of its own: it already refuses to
     * start a second poll while one is in flight, re-arms the timer, and
     * defers to a drawer he is typing in. It is also the journal's poll, so
     * a board page ignores this -- a board is a record and its own load
     * already marks a saved copy.
     */
    navigator.serviceWorker.addEventListener("message", function (event) {
      var msg = event.data || {};
      if (msg.type === "nova-api-updated") {
        if (window.novaApiUpdated) window.novaApiUpdated();
        return;
      }
      if (msg.type !== "nova-thread-updated" || !msg.conversationId) return;
      // The dock decides whether it is showing that thread; this no longer
      // knows, because the page that used to is gone.
      if (window.novaThreadUpdated) window.novaThreadUpdated(msg.conversationId);
    });
  }

  /* The Beats, Catalog and Alerts pages live in `beats.js` (issue #233).
   *
   * Bound here, next to the chat dock, rather than at the point in this
   * file where the three pages used to be defined -- which is where every
   * earlier split put its call. They read `POLL_MS`, and `POLL_MS` is
   * declared with the board-polling constants further down this file, so a
   * bind at the old position would hand the module `undefined` and the
   * Beats page would poll on `setTimeout(fn, undefined)` -- every 0ms,
   * forever. The router only calls these three at boot, which is the two
   * lines below this block, so binding late is safe and binding early is
   * not.
   *
   * The guard is for a cached tab that has `app.js` from this build and no
   * `beats.js` yet; without it the whole app would fail to boot over three
   * missing pages. */
  var loadHeartbeats, loadCatalog, loadAlerts;
  if (window.novaBeats) {
    var beatsPages = window.novaBeats({
      ASK_POLL_MAX: ASK_POLL_MAX,
      ASK_POLL_MS: ASK_POLL_MS,
      POLL_MS: POLL_MS,
      el: el,
      feed: feed,
      fetchPage: fetchPage,
      fmtStamp: fmtStamp,
      livePolls: livePolls,
      markNav: markNav,
      route: route,
      statusEl: statusEl,
      stopPolling: stopPolling,
      wordmark: wordmark,
    });
    loadHeartbeats = beatsPages.loadHeartbeats;
    loadCatalog = beatsPages.loadCatalog;
    loadAlerts = beatsPages.loadAlerts;
  } else {
    loadHeartbeats = function () {};
    loadCatalog = function () {};
    loadAlerts = function () {};
  }

  /* The home page and the galaxy live in `home.js` (issue #233). Bound here
   * rather than where they used to sit for the same reason as the Beats pages
   * above: they read `POLL_MS`, which is assigned a few hundred lines up from
   * here and after their old position. The guard is for a cached tab that has
   * `app.js` from this build and no `home.js` yet. */
  var loadHome, loadGalaxy;
  if (window.novaHome) {
    var homePages = window.novaHome({
      POLL_MS: POLL_MS,
      el: el,
      feed: feed,
      fetchPage: fetchPage,
      homeRecapFold: homeRecapFold,
      livePolls: livePolls,
      markNav: markNav,
      renderRecap: renderRecap,
      route: route,
      statusEl: statusEl,
      stopPolling: stopPolling,
      wordmark: wordmark,
    });
    loadHome = homePages.loadHome;
    loadGalaxy = homePages.loadGalaxy;
  } else {
    loadHome = function () {};
    loadGalaxy = function () {};
  }

  /* The chat dock lives in `chat-dock.js` -- 97 KB of it, moved out on
   * 2026-09-17 because this file is what issue #233 is about. It is called
   * here rather than left to run on its own script tag so it still runs at
   * this exact point in this file's body: `novaOpenChat` and
   * `novaThreadUpdated` are registered by it, and the boot below draws the
   * first route. The guard is for a cached tab that has `app.js` from this
   * build and no `chat-dock.js` yet. */
  if (window.novaChatDock) {
    window.novaChatDock({
      el: el,
      fetchPage: fetchPage,
      localStore: localStore,
      toast: toast,
      transitionMs: transitionMs,
      dragSheet: dragSheet,
      makeActionSheet: makeActionSheet,
      paintModelPicker: paintModelPicker,
      renderAskThread: renderAskThread,
      askPaintSent: askPaintSent,
      askPaintNote: askPaintNote,
      mergePendingSends: mergePendingSends,
      pingAskWatching: pingAskWatching,
      pingConvWatching: pingConvWatching,
      HOLD_MS: HOLD_MS,
      STICK_SLOP_PX: STICK_SLOP_PX,
      ASK_POLL_MS: ASK_POLL_MS,
      ASK_POLL_MAX: ASK_POLL_MAX,
      STEP_SHEET_MIN_VH: STEP_SHEET_MIN_VH,
      STEP_SHEET_MAX_VH: STEP_SHEET_MAX_VH,
      STEP_SHEET_DISMISS_VH: STEP_SHEET_DISMISS_VH,
    });
  }

  load();
  schedulePoll();
})();

/* Register this origin for push. Agora keeps the VAPID keypair, the
 * subscription store and the sender; `/api/push/key` and
 * `/api/push/subscribe` proxy to it. This exists because until
 * 2026-08-28 the Agora PWA was the only thing that could create a
 * subscription, so the owner's notifications depended on an app he had
 * said he would never open again (issues.md #119).
 *
 * Everything here fails quietly. A browser with no push support, a
 * declined permission prompt and an Agora with no VAPID keys are all
 * ordinary states, not errors worth a message on his screen -- the site
 * works identically without a subscription.
 *
 * `Notification.permission` is read before requesting so a reload does
 * not re-prompt; the browser would answer from its own record anyway,
 * but "default" is the only state where a prompt is the right thing.
 */
function subscribeToPush(registration) {
  if (!registration || !registration.pushManager) return;
  if (typeof Notification === "undefined") return;
  var permission = Notification.permission;
  var ask = permission === "default"
    ? Notification.requestPermission()
    : Promise.resolve(permission);
  ask.then(function (granted) {
    if (granted !== "granted") return;
    return fetch("/api/push/key")
      .then(function (res) { return res.ok ? res.json() : null; })
      .then(function (body) {
        if (!body || !body.publicKey) return;
        return registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(body.publicKey),
        });
      })
      .then(function (subscription) {
        if (!subscription) return;
        return fetch("/api/push/subscribe", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(subscription.toJSON()),
        });
      });
  }).catch(function () {});
}

/* The VAPID public key arrives base64url-encoded and `subscribe` wants
 * the raw bytes. Same conversion Agora's own app.js does. */
function urlBase64ToUint8Array(base64String) {
  var padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  var base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  var raw = atob(base64);
  var output = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) output[i] = raw.charCodeAt(i);
  return output;
}

/* Last statement on purpose: the opens reporter in index.html reads it, and
 * it is only reached when every line above parsed and ran without throwing
 * (nova_app_opens). */
window.novaBooted = true;
