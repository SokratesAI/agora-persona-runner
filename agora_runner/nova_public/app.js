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
  var mailEl = document.getElementById("mail");
  var changedEl = document.getElementById("changed");

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
    if (path === "/pool") return { view: "pool", cycle: null, board: null };
    if (path === "/costs") return { view: "costs", cycle: null, board: null };
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

  function markNav() {
    var here = route(window.location.pathname);
    // Hidden on `/asks` for the reason a deep link hides it: the URL is
    // already a filter, and a second one narrowing it would answer with
    // the newest matches across the whole archive rather than within the
    // asks -- `journal_page` treats `q` and `asks` as separate windows.
    setJournalSearchVisible(here.view === "journal" && here.cycle === null && !here.asks);
    // Every view but the journal is named after its own path, so the two
    // single-page views need no branch of their own -- which is what a
    // third one turning the chain into a nested ternary made worth doing.
    var want = here.view === "board"
      ? "/" + here.board
      // Both project views highlight the one nav tab there is. A tab per
      // project would be a nav that grows every time he files a row.
      : here.view === "project" || here.view === "projects" ? "/projects"
      : here.view === "journal" ? "/" : "/" + here.view;
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

  /* Which of my replies the owner has already seen, keyed by cycle number.
   *
   * the owner, capture 2026-08-25: *"I want to have a status the Nova header
   * if i have unread Journal comments. Journals should also show if i have
   * some unread by highlightong the comment button somehow, maybe with the
   * amount of unread messages."*
   *
   * Unread means *my* replies and never his own comments. He wrote those, so
   * counting them would raise a badge the moment he sends something and clear
   * it only when he re-opens his own message -- a notification that he has
   * spoken. The value stored per card is the newest reply stamp he has seen
   * on it, so anything written after that is unread and opening the card
   * clears the whole card at once.
   *
   * It lives in `localStorage` because it is the only state on this site that
   * is about *him* rather than about the loop: the server has no session, no
   * idea which device is his, and `comments.md` has nowhere to put a per-
   * reader mark. The trade is that the badge is per-device, which is honest
   * -- it is his phone that has or has not seen the reply.
   *
   * Every access is guarded and the page must keep working without it. A
   * browser with storage disabled throws on the *property*, not just on the
   * call, so the `try` has to wrap the lookup itself.
   */
  var READ_REPLIES_KEY = "nova.repliesRead.v1";

  /* `null` means nothing has ever been stored, and it is deliberately a
   * different state from `{}`, which means seeded and every card caught up.
   * Only the first of those may seed, and only `null` suppresses the badge
   * outright -- which is what a browser with no storage gets. */
  var repliesRead = null;
  var repliesReadLoaded = false;

  /* The unread replies he has opened in the header, captured at the tap.
   *
   * Deliberately not persisted: this is "the panel is on screen right now",
   * which is exactly the kind of state a reload should clear. The marks it
   * writes are the durable half and they live in `localStorage`. */
  var unreadOpen = null;

  /* Which cards have already auto-opened their ask drawer, so it happens
   * once per device rather than once per page load.
   *
   * the owner, unboarded capture 2026-08-25: *"Small bug. Journal comments seem
   * to expand themselves when i refresh the page even though i just closed
   * them."*
   *
   * The auto-open below (`asked.length && !fold.askSeen`) is the only thing
   * on this page that opens a drawer he did not tap, and its "once" was
   * kept in `folds`, which is a plain object rebuilt on every load. So
   * "once" meant once per page load: he closed the drawer, refreshed, and
   * the same card opened it again -- for as long as that ask stays on the
   * feed, which is the newest twenty entries. The comment beside it claimed
   * closing "stays closed through the five-minute poll", and that was the
   * whole of what it covered.
   *
   * Same store and the same trade as the read marks above: per-device,
   * because the server has no session and no idea which browser is his. A
   * browser with no storage falls back to the old once-per-load behaviour
   * rather than to a drawer that reopens on every render -- the in-memory
   * map is the primary and localStorage is only the durable copy. */
  var ASK_OPENED_KEY = "nova.askOpened.v1";
  var askOpened = null;

  function localStore() {
    try {
      return window.localStorage;
    } catch (err) {
      return null;
    }
  }

  function loadRepliesRead() {
    if (repliesReadLoaded) return repliesRead;
    repliesReadLoaded = true;
    var store = localStore();
    if (!store) return repliesRead;
    try {
      var raw = store.getItem(READ_REPLIES_KEY);
      if (raw === null || raw === undefined) return repliesRead;
      var parsed = JSON.parse(raw);
      // A corrupt value reads as never-stored rather than as an error: the
      // next seed overwrites it and the badge starts again from today.
      if (parsed && typeof parsed === "object") repliesRead = parsed;
    } catch (err) { /* unreadable: treat as never stored */ }
    return repliesRead;
  }

  function saveRepliesRead() {
    var store = localStore();
    if (!store || !repliesRead) return;
    try {
      store.setItem(READ_REPLIES_KEY, JSON.stringify(repliesRead));
    } catch (err) { /* quota or refused: the badge degrades, the page does not */ }
  }

  /* `{}` rather than `null` on an unreadable store: unlike the read marks
   * there is no seeding decision to protect here, and "nothing recorded" and
   * "cannot record" want the same answer -- open it this once. */
  function loadAskOpened() {
    if (askOpened) return askOpened;
    askOpened = {};
    var store = localStore();
    if (!store) return askOpened;
    try {
      var raw = store.getItem(ASK_OPENED_KEY);
      if (raw === null || raw === undefined) return askOpened;
      var parsed = JSON.parse(raw);
      // A corrupt value reads as never-stored, same as the read marks.
      if (parsed && typeof parsed === "object") askOpened = parsed;
    } catch (err) { /* unreadable: treat as never stored */ }
    return askOpened;
  }

  /* `key` is null for an entry with no cycle number -- there is exactly one
   * and nothing else can address it either, so it keeps the old
   * once-per-render behaviour rather than being given an invented key that
   * two untitled entries would share. */
  function askAlreadyOpened(key) {
    if (key === null) return false;
    return !!loadAskOpened()[key];
  }

  function markAskOpened(key) {
    if (key === null) return;
    var opened = loadAskOpened();
    opened[key] = true;
    var store = localStore();
    if (!store) return;
    try {
      store.setItem(ASK_OPENED_KEY, JSON.stringify(opened));
    } catch (err) { /* quota or refused: it reopens next load, nothing breaks */ }
  }

  /* Every reply on one card's thread, with the stamp it is ordered by.
   *
   * A reply carrying no stamp of its own inherits its comment's, exactly as
   * `paint` does. The two have to agree: if they did not, the badge would be
   * counting a reply that the thread draws somewhere else. */
  function repliesOf(items) {
    var out = [];
    (items || []).forEach(function (comment) {
      var replies = comment.replies;
      if (!(replies && replies.length) && comment.reply) {
        replies = [{ stamp: comment.replyStamp, text: comment.reply }];
      }
      (replies || []).forEach(function (answer) {
        out.push({
          stamp: (answer && answer.stamp) || comment.stamp || "",
          text: (answer && answer.text) || "",
          asked: comment.text || "",
        });
      });
    });
    return out;
  }

  function replyStamps(items) {
    return repliesOf(items).map(function (answer) { return answer.stamp; });
  }

  function newestReplyStamp(items) {
    var stamps = replyStamps(items);
    var newest = "";
    for (var i = 0; i < stamps.length; i++) {
      if (stamps[i] > newest) newest = stamps[i];
    }
    return newest;
  }

  function unreadOn(cycle, items) {
    var seen = loadRepliesRead();
    if (!seen) return [];
    var mark = seen[String(cycle)] || "";
    return repliesOf(items).filter(function (answer) { return answer.stamp > mark; });
  }

  function unreadReplies(cycle, items) {
    return unreadOn(cycle, items).length;
  }

  /* The first payload writes today's newest stamp for every card and shows
   * nothing unread.
   *
   * This is the part worth being careful about. On the first load after this
   * ships there are three hundred-odd replies in the archive and no record of
   * which he has read, and "300 unread" is not something I know -- it is the
   * absence of a measurement, printed as one. A badge that large on day one
   * also teaches him to ignore the badge, which costs the feature. So the
   * count starts from replies written after this existed. */
  function seedRepliesRead(byCycle) {
    if (loadRepliesRead()) return;
    if (!localStore()) return;
    repliesRead = {};
    Object.keys(byCycle || {}).forEach(function (cycle) {
      var newest = newestReplyStamp(byCycle[cycle]);
      if (newest) repliesRead[cycle] = newest;
    });
    saveRepliesRead();
  }

  function markRepliesRead(cycle, items) {
    var seen = loadRepliesRead();
    if (!seen) return false;
    var newest = newestReplyStamp(items);
    if (!newest) return false;
    var key = String(cycle);
    if ((seen[key] || "") >= newest) return false;
    seen[key] = newest;
    saveRepliesRead();
    return true;
  }

  /* How many unread replies there are and which card holds the oldest.
   *
   * Oldest rather than newest, for the reason `oldestOpenAsk` picks the
   * oldest ask: the newest card is the one at the top of the feed that he
   * will see anyway, and the one worth pointing at is the one about to
   * scroll out of the twenty-entry window. */
  function unreadSummary(byCycle) {
    var count = 0;
    var cards = 0;
    var oldest = null;
    var items = [];
    Object.keys(byCycle || {}).forEach(function (key) {
      var unread = unreadOn(key, byCycle[key]);
      if (!unread.length) return;
      count += unread.length;
      cards += 1;
      var cycle = parseInt(key, 10);
      if (!isNaN(cycle) && (oldest === null || cycle < oldest)) oldest = cycle;
      unread.forEach(function (answer) {
        items.push({ cycle: cycle, stamp: answer.stamp, text: answer.text, asked: answer.asked });
      });
    });
    /* Newest first: the panel is a mailbox, and the reply he is most likely
     * to be looking for is the one that just arrived. The *badge* still names
     * the oldest card, because that one is about to scroll out of the feed. */
    items.sort(function (a, b) { return a.stamp < b.stamp ? 1 : a.stamp > b.stamp ? -1 : 0; });
    return { count: count, cards: cards, cycle: oldest, items: items };
  }

  /* "3 cycles since you last looked · 2 PRs merged", and nothing at all
   * when there is nothing new.
   *
   * the owner, ideas board #115 (approved 08-25): *"You are asleep for nine
   * cycles at a time and the app opens on the newest journal card with no
   * sense of how much you missed. One line -- six cycles, two PRs merged,
   * one thing needs your input, one board row moved -- would let you decide
   * in two seconds whether to read or to close it."*
   *
   * Two of his four examples are deliberately not in the line, and both
   * omissions are about not saying the same thing twice on one screen. "One
   * thing needs your input" is already the `waiting on you` field in the
   * header directly above, and unread replies are already the `#mail` badge
   * directly below -- a second copy of either is the duplicate-outcome-pill
   * complaint (`issues.md` 2026-08-23) coming back through a different door.
   * "One board row moved" is the one I could not build here honestly: this
   * page fetches no board payload, and a row's *previous* status is not
   * stored anywhere on the device or the server, so there is nothing to
   * diff against. That is a real gap and it is written down rather than
   * guessed at.
   *
   * The mark lives in `localStorage` for the reason the read-reply marks do:
   * the server has no session and no idea which device is his, so "since
   * *you* last looked" can only be answered by the browser that looked. Per
   * device is the honest scope -- it is his phone that has or has not seen
   * cycle 540. */
  var LAST_SEEN_KEY = "nova.lastSeen.v1";

  /* The mark as it stood when this document loaded, held for the whole
   * session, and the reason this is a variable rather than a `getItem` at
   * paint time.
   *
   * `paintChanged` advances the stored mark the first time it draws, so a
   * second read of storage would answer "nothing new" -- and then tapping a
   * card and coming back, which is an in-page navigation and re-renders the
   * header, would silently drop the line he was reading. Capturing it once
   * means the line survives every navigation and every poll inside one open
   * of the app, and a genuinely fresh load is what resets it. A PWA resumed
   * from the background fires `pageshow`/`focus` and re-fetches without
   * reloading the document, so that path keeps the line too, which is the
   * behaviour I want: he backgrounded the app, he did not read it. */
  var lastSeenCycle = null;
  var lastSeenCaptured = false;

  /* The newest cycle he had been told about when he tapped the line away, or
   * null if he has not tapped it.
   *
   * A plain `dismissed = true` flag is what this was for one commit, and it
   * was wrong in the case the feature exists for: he leaves the app open on
   * his phone. The tap silenced `paintChanged` for the whole document
   * session, so the four cycles that ran while the tab sat there never
   * raised the line again -- the mark kept advancing underneath it and
   * nothing ever said so. Remembering *what* he dismissed instead of *that*
   * he dismissed means the line comes back the moment there is something in
   * it he has not already been shown. */
  var changedDismissedAt = null;

  function loadLastSeen() {
    var store = localStore();
    if (!store) return null;
    try {
      var raw = store.getItem(LAST_SEEN_KEY);
      var seen = parseInt(raw, 10);
      return isNaN(seen) ? null : seen;
    } catch (err) { return null; }
  }

  function saveLastSeen(cycle) {
    var store = localStore();
    if (!store) return;
    try {
      store.setItem(LAST_SEEN_KEY, String(cycle));
    } catch (err) { /* quota or refused: the line degrades, the page does not */ }
  }

  /* What to say, or "" for "say nothing".
   *
   * `entries` is the feed's window, not the whole journal, so the counts are
   * taken off the cards that are actually there and the line says `at least`
   * whenever the mark predates the oldest of them. That is the only way to
   * be exact in the common case -- he is a handful of cycles behind -- while
   * staying true after a week away, and it is why this counts cards rather
   * than subtracting cycle numbers: the loop has real holes in its numbering
   * (`status.missingCycles` exists because of them), so `540 - 534` is six
   * cycles only if all six ran.
   *
   * A PR counts when the footer names one *and* the outcome is `merged`.
   * `isRealPr` alone would count a `stuck` cycle that opened a PR nobody
   * took, which is the opposite of the reassurance the line is for. */
  function changedLine(newest, entries, seen) {
    if (seen === null || seen === undefined) return "";
    if (typeof newest !== "number" || newest <= seen) return "";
    var cycles = {};
    var mergedCycles = {};
    var oldest = null;
    (entries || []).forEach(function (entry) {
      var n = entry && entry.cycle;
      if (typeof n !== "number") return;
      if (oldest === null || n < oldest) oldest = n;
      if (n <= seen) return;
      cycles[n] = true;
      /* Keyed by cycle and not counted per entry: a cycle that writes an
       * addendum has two entries carrying the same `PR:` footer, and
       * counting rows would report one merge as two. */
      if (isRealPr(entry.pr) && /^merged$/i.test(String(entry.outcome || "").trim())) {
        mergedCycles[n] = true;
      }
    });
    var fresh = Object.keys(cycles).length;
    var merged = Object.keys(mergedCycles).length;
    if (!fresh) return "";
    /* The mark is older than the oldest card on the feed, so there are new
     * cycles this window cannot see and both counts are floors. */
    var partial = oldest !== null && oldest > seen + 1;
    var lead = partial ? "at least " : "";
    var line = lead + fresh + (fresh === 1 ? " cycle" : " cycles") + " since you last looked";
    if (merged) line += " · " + lead + merged + (merged === 1 ? " PR" : " PRs") + " merged";
    return line;
  }

  /* Draw it, and advance the mark.
   *
   * Only from the journal feed, and only on a live payload. A `/cycle/N`
   * permalink builds its status off the single entry it asked for, so
   * `status.cycle` there is whichever old cycle he deep-linked to -- writing
   * that as the mark would set it *backwards* and then claim a hundred
   * cycles were new on his next open. A replayed payload is suppressed for
   * the reason the badges beside it are: "this is what changed" is a claim
   * about now, made from bytes the service worker cached at some unknown
   * earlier time.
   *
   * The mark advances on the first paint rather than on a tap, because the
   * common case is that he opens the app, reads the line, and closes it
   * without touching anything -- a mark that only moved on a tap would show
   * him the same "6 cycles" forever. The tap is a dismissal, not the
   * acknowledgement, and it dismisses the news he was shown rather than the
   * feature: see `changedDismissedAt`. */
  function hideChanged() {
    if (!changedEl) return;
    changedEl.textContent = "";
    changedEl.setAttribute("hidden", "");
  }

  function paintChanged(status, entries) {
    if (!changedEl) return;
    if (routedCycle(window.location.pathname) !== null) return;
    /* A search answers with whichever cycles matched, from anywhere in the
     * archive, so the entries it returns are not "the newest N" and counting
     * them would report the shape of his query rather than what he missed. */
    if (journalQuery.trim()) return;
    if (!status || status.replayed) return;
    var newest = status.cycle;
    if (typeof newest !== "number") return;
    if (!lastSeenCaptured) {
      lastSeenCaptured = true;
      lastSeenCycle = loadLastSeen();
    }
    /* Never backwards: a search result or a short window can hand this a
     * `status.cycle` below the mark, and lowering it would invent news. */
    if (lastSeenCycle === null || newest > lastSeenCycle) saveLastSeen(newest);
    changedEl.textContent = "";
    var stale = changedDismissedAt !== null && newest <= changedDismissedAt;
    var text = stale ? "" : changedLine(newest, entries, lastSeenCycle);
    if (!text) {
      changedEl.setAttribute("hidden", "");
      return;
    }
    var btn = el("button", "changed-line", text);
    btn.type = "button";
    btn.title = "Dismiss";
    btn.addEventListener("click", function () {
      changedDismissedAt = newest;
      changedEl.textContent = "";
      changedEl.setAttribute("hidden", "");
    });
    changedEl.appendChild(btn);
    changedEl.removeAttribute("hidden");
  }

  /* The unread-reply badge and the panel it opens, in their own node outside
   * `statusEl` so they survive a page that is not the journal.
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
    if (!mailEl) return;
    mailEl.textContent = "";
    paintMailInto(replayed);
    if (mailEl.childNodes.length) mailEl.removeAttribute("hidden");
    else mailEl.setAttribute("hidden", "");
  }

  /* Comments for a page that does not fetch them.
   *
   * Only the journal view calls `fetchAll`, so `haveComments` was false on
   * every other page and `paintMail` had nothing to count. One uncached read
   * per navigation, tolerated the same way `fetchAll` tolerates its own: the
   * badge is not worth an error on a page it is not about. */
  function refreshMail() {
    return fetch("/api/comments")
      .then(json)
      .then(function (data) {
        if (!data) return;
        lastCommentsByCycle = data.byCycle || {};
        haveComments = true;
        seedRepliesRead(lastCommentsByCycle);
        paintMail(false);
      })
      .catch(function () { /* leave whatever the last paint put there */ });
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
    if (!replayed && haveComments) {
      var unread = unreadSummary(lastCommentsByCycle);
      if (unread.count) {
        /* The badge is a button now, not a link to a card.
         *
         * the owner, `issues.md` 2026-08-26: *"The reply status that tells me
         * that i have missed replies does not work as designed. Maybe it works
         * technically, but its not user friendly. We need a better way to show
         * me the message or drop it as it just noise now. I have read the
         * replies, but the status still shows that i have not read them."* He
         * screenshotted it reading **7 new replies · oldest on cycle 456**.
         *
         * Both halves of that are the same defect: the only thing that marks a
         * reply read is tapping open that one card's drawer, so a badge over
         * seven cards was seven separate errands, and cycle 456 was seventeen
         * cards down a twenty-card feed. Tapping the badge scrolled the feed to
         * a *collapsed* card and marked nothing. So he did read the replies --
         * in the drawers, in the chat dock, wherever -- and the count stood,
         * exactly as he says, because none of those paths is the one tap the
         * mark is wired to.
         *
         * He offered two fixes and I took the first: show him the message. The
         * badge opens the replies themselves, in the header, newest first, and
         * opening it is what marks them read. One tap, no hunting, and the text
         * is on screen rather than a number pointing at where the text lives.
         *
         * `unreadOpen` holds the items captured at the tap rather than
         * recomputing them, because the tap marks them read: recomputing would
         * find nothing unread and draw an empty panel over the message he just
         * asked to see. */
        var mail = el("p", "status-sub");
        var open = el("button", "badge badge-unread status-unread-open",
          unread.count + (unread.count === 1 ? " new reply" : " new replies"));
        open.type = "button";
        /* No `aria-expanded`: the badge does not survive being pressed. The
         * tap marks everything read, so the next render draws the panel and
         * no badge at all, and a control that is gone cannot be expanded. */
        open.addEventListener("click", function () {
          unreadOpen = unread.items;
          Object.keys(lastCommentsByCycle || {}).forEach(function (key) {
            markRepliesRead(key, lastCommentsByCycle[key]);
          });
          paintMail(replayed);
          /* The cards' own chips are derived from the same marks, and there is
           * no held journal payload to re-render the feed from. Clearing them
           * in place is the honest edit rather than a shortcut: they are now
           * read, and leaving them lit for up to a poll would be the badge
           * insisting on a reply that is open on his screen -- the thing this
           * whole feature keeps getting wrong. */
          if (feed) {
            Array.prototype.forEach.call(feed.querySelectorAll(".comment-unread"),
              function (chip) { chip.remove(); });
            Array.prototype.forEach.call(feed.querySelectorAll(".comment-toggle.has-unread"),
              function (button) { button.classList.remove("has-unread"); });
          }
        });
        mail.appendChild(open);
        mail.appendChild(el("span", "status-pr", unread.cards === 1
          ? "cycle " + unread.cycle
          : "oldest on cycle " + unread.cycle));
        mailEl.appendChild(mail);
      }
    }
    /* The replies themselves, which is the half of his ask the badge never
     * had. Drawn after `subs` so it sits under the status line it came from,
     * and outside the `!replayed` guard above because by the time this runs
     * the items are already in hand -- a cached payload cannot make a message
     * he asked to read disappear. */
    if (unreadOpen && unreadOpen.length) {
      var panel = el("div", "unread-panel");
      var head = el("p", "unread-panel-head");
      head.appendChild(el("span", "unread-panel-count",
        unreadOpen.length + (unreadOpen.length === 1 ? " reply" : " replies")));
      var shut = el("button", "unread-panel-close", "Close");
      shut.type = "button";
      shut.addEventListener("click", function () {
        unreadOpen = null;
        paintMail(replayed);
      });
      head.appendChild(shut);
      panel.appendChild(head);
      unreadOpen.forEach(function (answer) {
        var row = el("a", "unread-reply");
        row.href = "/cycle/" + answer.cycle;
        var meta = el("p", "unread-reply-meta");
        meta.appendChild(el("span", "status-pr", "cycle " + answer.cycle));
        if (answer.stamp) meta.appendChild(el("span", "status-pr", answer.stamp));
        row.appendChild(meta);
        /* His own comment first, dimmed, because a reply read outside its
         * thread has lost the question it answers -- "yes, that is the same
         * bug" is not a message on its own. One line of it: this is a mailbox,
         * not the thread, and the thread is one tap away on the link. */
        if (answer.asked) row.appendChild(el("p", "unread-reply-asked", answer.asked));
        row.appendChild(el("p", "unread-reply-text", answer.text || "(no text)"));
        panel.appendChild(row);
      });
      mailEl.appendChild(panel);
    }
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  /* An attach button for any composer on this site.
   *
   * the owner, comments board 2026-08-21: *"How do i send a screenshot?"* He
   * could see a layout bug on his Galaxy S25 that no renderer in this loop
   * can reproduce, and the only channel between us was text. Cycle 299 had
   * to answer "you can't" and ask him to describe the pixels instead.
   *
   * The upload happens on *pick*, not on send. Two reasons, and the second
   * is the one that decided it: a 3MB POST from a phone takes long enough
   * that doing it inside send() would make the send button look hung, and
   * an attachment that is already stored can be shown back to him before he
   * commits to anything.
   *
   * **Where it is shown back changed in Cycle 377, and that is this ask.**
   * The owner, ideas board 2026-08-24: *"Lets me preview a miniatyr version
   * of the uploaded images in the Nova app instead of the text that shows up
   * in the input box. Also let me upload multiple (at once) and cross them
   * out if i want to not send them after upload."* Until now the markdown
   * line was appended into the textarea, which made three things awkward at
   * once: he could not see what he had picked, a second pick pushed his own
   * sentence further up a box he is reading on a 360px phone, and "delete
   * it" meant selecting a 45-character URL by hand.
   *
   * So the attachments live in a tray beside the box instead — one chip per
   * file, a thumbnail for a picture and its name for anything else, each
   * with an ✕. The markdown is composed at send time by `markdown()` rather
   * than typed into the box. That is a real trade and it is worth naming:
   * he loses the ability to edit the alt text or move the line around inside
   * his sentence, and he gains seeing it and being able to drop it. He asked
   * for the second one.
   *
   * `FileReader.readAsDataURL` rather than an ArrayBuffer walk: the server
   * accepts a `data:` URL as-is (`store_upload` splits on the comma), so
   * this is one call with no manual base64 in JavaScript. */
  function buildAttach(opts) {
    opts = opts || {};
    var input = el("input", "attach-input");
    input.type = "file";
    // The second half of his ask. One `change` now carries a list, and the
    // uploads run one after another rather than all at once: a phone
    // picking four screenshots would otherwise open four simultaneous
    // multi-megabyte POSTs, and the status line could only honestly
    // describe one of them at a time anyway.
    input.multiple = true;
    // No `accept` at all. It was `image/*`, and on Android that is not a
    // filter over a file browser -- it is what makes the picker open
    // Google Photos with no way out. The owner, 2026-08-21: "It seems i only
    // can upload images. Or atleas the ui forces only my Google photos to
    // open and i have no option to upload files." The server resolves and
    // bounds the type, and answers with a sentence he can read, so an
    // allowlist here only ever hid his own files from him.
    input.hidden = true;

    var button = el("button", "attach-btn " + (opts.buttonClass || ""), "📎");
    button.type = "button";
    button.title = "Attach a file";
    button.setAttribute("aria-label", "Attach a file");

    function status(text, isError) {
      if (opts.onStatus) opts.onStatus(text, isError);
    }

    /* Whether an upload is in flight, and the composer's own send controls
     * follow it.
     *
     * Without this the two controls race, and the race loses the picture
     * silently: `submit()` reads the tray synchronously, so tapping Comment
     * while the POST is still going sends the text *without* the
     * attachment -- and then the upload resolves and pushes a chip into a
     * tray `submit()` has already cleared, so the image reappears as an
     * orphaned draft attached to nothing. He gets a comment with no
     * screenshot in it and no sign that anything went wrong.
     *
     * Disabling send is the fix rather than queueing the upload, because
     * the upload is the slow part and "wait for it" is the honest thing to
     * show. `status` already says "uploading …" while it runs. */
    function busy(isBusy) {
      button.disabled = isBusy;
      if (opts.onBusy) opts.onBusy(isBusy);
    }

    button.addEventListener("click", function () { input.click(); });

    /* What has been uploaded and not yet sent: `{name, url, isImage}` each.
     *
     * This is the composer's state now, not the textarea's, which is the
     * whole shape of the change. `onChange` is how a composer that has a
     * draft store keeps it across a re-render — the journal drawer is
     * rebuilt on every poll, and an attachment that survived only in this
     * closure would disappear from under him while he was still typing. */
    var pending = [];
    var tray = el("div", "attach-tray");

    function markdownFor(item) {
      // `![…]` only for something that renders as a picture. The server
      // decides that, not `file.type` -- Android reports `""` for plenty
      // of files and the extension lookup happens server side. A `![pdf]`
      // here would paint a broken image icon.
      return (item.isImage ? "!" : "") + "[" + item.name + "](" + item.url + ")";
    }

    function render() {
      tray.textContent = "";
      tray.hidden = pending.length === 0;
      pending.forEach(function (item, index) {
        var chip = el("div", "attach-chip");
        if (item.isImage) {
          var thumb = el("img", "attach-thumb");
          thumb.src = item.url;
          // The filename, not "image": with four screenshots in the tray
          // the alt text is the only thing that tells them apart to a
          // screen reader, and it is what he named them.
          thumb.alt = item.name;
          chip.appendChild(thumb);
        } else {
          chip.appendChild(el("span", "attach-chip-name", "📎 " + item.name));
        }
        /* "cross them out if i want to not send them after upload."
         *
         * It drops the attachment from this send only. The bytes stay on
         * the server, because `store_upload` already wrote them and there
         * is no delete endpoint -- and inventing one to make an ✕ feel
         * complete would be a second, destructive feature he did not ask
         * for. An orphaned upload costs disk and nothing else. */
        var remove = el("button", "attach-chip-remove", "✕");
        remove.type = "button";
        remove.title = "Remove " + item.name;
        remove.setAttribute("aria-label", "Remove " + item.name);
        remove.addEventListener("click", function () {
          pending.splice(index, 1);
          render();
          changed();
        });
        chip.appendChild(remove);
        tray.appendChild(chip);
      });
    }

    function changed() {
      if (opts.onChange) opts.onChange(pending.slice());
    }

    /* Whether this composer is still the one on screen.
     *
     * The journal drawer is thrown away and rebuilt whole on a render, and
     * the upload chain below is not cancelled when that happens -- it keeps
     * running inside the dead closure, with its own `pending` array, its own
     * detached tray and a `status` element nobody can see. Reviewer finding,
     * Cycle 377, and the worst of the three consequences is the one I would
     * not have predicted: `clear()` on a successful send deletes the draft,
     * and then the dead chain's next completed upload calls `changed()` and
     * *resurrects* it, so the next time he opens that drawer a picture he
     * already sent is sitting there looking unsent.
     *
     * So a completed upload that has nowhere visible to go is treated as a
     * failed one. It is reported, not swallowed -- the status line it writes
     * to is detached, which is exactly why the count in the batch summary
     * matters -- and the live drawer's tray keeps showing only what it will
     * actually send. That is the property worth protecting: an under-count
     * he can see beats a silent over-count he cannot.
     *
     * `isConnected` and not a generation counter, because the question this
     * has to answer is literally "is my tray on the page", and a counter
     * would be a second thing that has to be kept in step with the DOM. The
     * tray is in the document from the moment the composer is built, so this
     * is false only after a rebuild has orphaned it. */
    function live() {
      return tray.isConnected !== false;
    }

    render();

    /* One file, from picked to sitting in the tray. Rejects rather than
     * reporting, so the loop below can count how many of a batch failed
     * and say so once instead of overwriting the status line per file. */
    function upload(file) {
      return new Promise(function (resolve, reject) {
        var reader = new FileReader();
        reader.onerror = function () { reject(new Error("could not read " + file.name)); };
        reader.onload = function () {
          fetch("/api/upload", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              contentType: file.type,
              data: String(reader.result || ""),
            }),
          })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (result) {
              if (!result || !result.ok) {
                throw new Error((result && (result.message || result.error)) || "upload failed");
              }
              if (!live()) {
                throw new Error(file.name + " finished after the page moved on");
              }
              pending.push({
                name: file.name || "file",
                url: result.url,
                isImage: result.isImage !== false,
              });
              render();
              changed();
              resolve();
            })
            .catch(reject);
        };
        reader.readAsDataURL(file);
      });
    }

    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) return;
      busy(true);
      var attached = 0;
      var lastError = "";
      files
        .reduce(function (chain, file, index) {
          return chain.then(function () {
            status(
              files.length > 1
                ? "uploading " + (index + 1) + " of " + files.length + " — " + file.name + "…"
                : "uploading " + file.name + "…",
              false,
            );
            return upload(file).then(
              function () { attached += 1; },
              // One bad file in a batch of four must not throw away the
              // three good ones, so a rejection is recorded and the chain
              // continues. The last failure is the one reported: a status
              // line is one sentence and the most recent is the one he
              // can still act on by picking that file again.
              function (err) { lastError = String((err && (err.message || err)) || "upload failed"); },
            );
          });
        }, Promise.resolve())
        .then(function () {
          busy(false);
          // Cleared so picking the *same* file twice still fires
          // `change` -- otherwise a failed upload cannot be retried
          // without choosing a different image first.
          input.value = "";
          if (lastError) {
            status(attached ? lastError + " (" + attached + " attached)" : lastError, true);
          } else {
            status(attached === 1 ? "attached" : "attached " + attached + " files", false);
          }
        });
    });

    return {
      button: button,
      input: input,
      tray: tray,
      /** How many attachments are waiting. A composer with no typed text
       *  but a full tray still has something to send. */
      count: function () { return pending.length; },
      /** The markdown for everything in the tray, joined by `separator`.
       *  Board comments may not contain a line break, so that caller
       *  passes a space; the others take the default blank line. */
      markdown: function (separator) {
        return pending
          .map(markdownFor)
          .join(separator === undefined ? "\n\n" : separator);
      },
      /** Emptied only once the server has confirmed the send, the same
       *  rule the text boxes already follow. */
      clear: function () { pending = []; render(); changed(); },
      /** Repopulate from a draft store after a re-render. Deliberately
       *  silent -- `onChange` reports what the reader did, and replaying
       *  it here would write the draft back over itself. */
      restore: function (list) { pending = (list || []).slice(); render(); },
    };
  }

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

  /* Mermaid diagrams in a chat message.
   *
   * the owner, issues.md 2026-08-25: *"Make the new chat be able to display
   * mermaid charts, images and also be able to upload files like all other
   * input fields in the Nova app."* The other two halves shipped in #379.
   *
   * Vendored at `/vendor/mermaid.min.js` (11.17.2, MIT) for the same reason
   * ECharts is: a CDN script tag is a diagram that goes blank the moment the
   * phone is off the tailnet, which is the failure the service worker exists
   * to prevent. It is 3.5 MB -- three and a half times the chart library --
   * so unlike ECharts it is deliberately *not* in `sw.js`'s install-time
   * precache, and it is fetched only when a ```mermaid block actually turns
   * up in a message. Nobody pays for it until somebody draws a diagram.
   */
  var MERMAID_SRC = "/vendor/mermaid.min.js";
  var mermaidLoading = null;
  var mermaidSeq = 0;

  function ensureMermaid() {
    if (mermaidLoading) return mermaidLoading;
    mermaidLoading = new Promise(function (resolve, reject) {
      if (window.mermaid) return resolve(window.mermaid);
      var tag = document.createElement("script");
      tag.src = MERMAID_SRC;
      tag.async = true;
      tag.onload = function () {
        if (window.mermaid) resolve(window.mermaid);
        else reject(new Error("mermaid loaded but did not register"));
      };
      tag.onerror = function () { reject(new Error("could not load " + MERMAID_SRC)); };
      document.head.appendChild(tag);
    }).then(function (mermaid) {
      // `startOnLoad` would have it sweep the document for `.mermaid`
      // elements on its own, which is a second renderer racing the one
      // below. `strict` runs every label through mermaid's own DOMPurify,
      // and `suppressErrorRendering` stops it painting its red "syntax
      // error" bomb into the page when it cannot parse -- the fallback
      // here is the code block he typed, which says more.
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        suppressErrorRendering: true,
        // Dark, because this app is. `--bg` is `#12131a` and there is no
        // light mode to fall back to -- no `prefers-color-scheme` rule
        // anywhere in `style.css` -- so mermaid's default theme puts a
        // white card in the middle of a dark message bubble. I only found
        // that by driving the deployed page in a real browser and looking
        // at the screenshot; the diagram was correct and it was the one
        // bright rectangle on the page.
        theme: "dark",
      });
      return mermaid;
    });
    // A failed load must not poison every later diagram: drop the memo so
    // the next message retries. Offline once is not offline forever.
    mermaidLoading.catch(function () { mermaidLoading = null; });
    return mermaidLoading;
  }

  /** Split a message into plain-text runs and fenced ```mermaid blocks.
   *
   * A line scanner rather than a regex because the block is the only
   * multi-line construct this reader knows about and the boundaries have to
   * be exact: `appendRichText` splits paragraphs on blank lines, and a
   * flowchart with a blank line in it would otherwise be torn in half
   * before anything got to look at it.
   *
   * An unterminated fence stays plain text. That is the honest reading --
   * a half-typed message is not a diagram -- and it is also what keeps the
   * fallback path from swallowing the rest of the message.
   *
   * The blank line either side of a fence belongs to the fence and is
   * dropped with it. `.ask-text` is `pre-wrap`, so a newline left on the
   * end of the run before a diagram is a visible empty line above it, and
   * my first version of this shipped one at both ends. Only the lines
   * touching a block are trimmed: a message with no diagram in it comes
   * back exactly as it went in, which is what every other message is. */
  function splitMermaidBlocks(text) {
    var lines = String(text || "").split("\n");
    var parts = [];
    var plain = [];
    var index = 0;

    function flush(nextToBlock) {
      if (nextToBlock) {
        while (plain.length && !plain[plain.length - 1].trim()) plain.pop();
      }
      if (plain.length) {
        parts.push({ type: "text", text: plain.join("\n") });
        plain = [];
      }
    }

    while (index < lines.length) {
      if (/^[ \t]{0,3}(?:```|~~~)[ \t]*mermaid[ \t]*$/.test(lines[index])) {
        var code = [];
        var scan = index + 1;
        var closed = false;
        while (scan < lines.length) {
          if (/^[ \t]{0,3}(?:```|~~~)[ \t]*$/.test(lines[scan])) { closed = true; break; }
          code.push(lines[scan]);
          scan += 1;
        }
        if (closed) {
          flush(true);
          parts.push({ type: "mermaid", code: code.join("\n") });
          index = scan + 1;
          while (index < lines.length && !lines[index].trim()) index += 1;
          continue;
        }
      }
      plain.push(lines[index]);
      index += 1;
    }
    flush(parts.length > 0);
    return parts;
  }

  /** One ```mermaid block -> a figure that starts as the code he typed and
   * becomes a diagram once the library is down and the parse succeeds.
   *
   * The code block is what is on screen first, on purpose. It is what he
   * saw before this existed, it is readable on its own, and it is the right
   * thing to be left looking at when the download fails on a dead link or
   * the diagram does not parse. A spinner or a blank box would both be a
   * worse answer to the same two failures.
   *
   * `mermaid.render` hands back an SVG *string*, and the first line of this
   * file forbids innerHTML. `DOMParser` is not a way around that rule, it
   * is a stronger version of it: it parses into an inert document with
   * scripting disabled, so a `<script>` in that string cannot run even
   * after import -- and the string itself has already been through
   * mermaid's DOMPurify under `securityLevel: "strict"`. The invariant the
   * header is protecting is that text off the wire cannot become markup;
   * that still holds. */
  /* Every diagram this session has already drawn, keyed by its source.
   *
   * This is not a speed optimisation, it is the fix for a visible fault my
   * reviewer found on the first version. `renderAskThread` empties the
   * thread and rebuilds every message from scratch, and `pollConv` calls it
   * every four seconds for up to four minutes while an answer is on its
   * way. Text and pictures survive that -- a picture is the same URL and
   * comes straight back out of the browser's cache -- but a diagram is
   * built asynchronously, so every rebuild would drop each already-drawn
   * diagram back to its code block and redraw it a moment later. Every four
   * seconds, for as long as he waits for an answer.
   *
   * Keyed on the source rather than on a message id because that is what
   * decides whether two figures are the same picture, and it is what makes
   * the second draw a synchronous one -- the cached SVG goes in during the
   * same rebuild, so there is no frame where the diagram is missing. It
   * holds one entry per distinct diagram in a session and is dropped when
   * the tab is; a cap would be a number I invented. */
  var mermaidDrawn = Object.create(null);

  /** The parsed <svg> for one render, or null if the string was not one.
   *  A parse error document is itself an element called `parsererror`, so
   *  this is the shape that has to be checked rather than trusted --
   *  importing it would put the browser's error text on the page where the
   *  diagram goes. */
  function mermaidSvg(markup) {
    var parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
    var svg = parsed.documentElement;
    if (!svg || String(svg.nodeName).toLowerCase() !== "svg") return null;
    // Mermaid sizes the SVG for the width it measured, which is not the
    // width of a phone. Let CSS own the box.
    svg.removeAttribute("width");
    svg.removeAttribute("height");
    return svg;
  }

  function mermaidNode(code) {
    var figure = el("figure", "mermaid-figure");

    function draw(markup) {
      var svg = mermaidSvg(markup);
      if (!svg) return false;
      figure.textContent = "";
      figure.appendChild(document.importNode(svg, true));
      figure.className = "mermaid-figure mermaid-drawn";
      return true;
    }

    if (mermaidDrawn[code] && draw(mermaidDrawn[code])) return figure;

    var source = el("pre", "mermaid-source");
    source.appendChild(el("code", null, code));
    figure.appendChild(source);

    ensureMermaid().then(function (mermaid) {
      mermaidSeq += 1;
      return mermaid.render("mermaid-svg-" + mermaidSeq, code);
    }).then(function (result) {
      // Remembered only once it has actually parsed into an <svg>: caching
      // a string that does not is a permanent wrong answer for that
      // diagram, since nothing ever re-renders it.
      if (draw(result.svg)) mermaidDrawn[code] = result.svg;
    }).catch(function () {
      /* Offline, or a diagram mermaid cannot parse. The code block stays,
       * which is exactly what was there before. */
    });
    return figure;
  }

  function appendRichText(container, paraClass, text) {
    splitMermaidBlocks(text).forEach(function (part) {
      if (part.type === "mermaid") {
        container.appendChild(mermaidNode(part.code));
        return;
      }
      appendPlainText(container, paraClass, part.text);
    });
  }

  function appendPlainText(container, paraClass, text) {
    String(text || "").split(/\n{2,}/).forEach(function (para) {
      if (!para.trim()) return;
      var node = el("p", paraClass);
      var last = 0;
      var match;
      ATTACH_RE.lastIndex = 0;
      while ((match = ATTACH_RE.exec(para)) !== null) {
        var before = para.slice(last, match.index);
        if (before) node.appendChild(document.createTextNode(before));
        node.appendChild(attachNode(match[3], match[2], !!match[1]));
        last = match.index + match[0].length;
      }
      var rest = para.slice(last);
      if (rest) node.appendChild(document.createTextNode(rest));
      container.appendChild(node);
    });
  }

  /* Make a pager fire when it is scrolled to, instead of when it is tapped.
   *
   * the owner, issues.md #71: "Make it more lazy load when i scroll down
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
   * cards is already ~1400px against a phone's ~850px, so the owner's own
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

  /* Drop the live watcher, because a page that is being left has no pager.
   *
   * `attached` is disconnected when a *new* pager takes its place, which
   * is enough while every pager sits at the bottom of the feed: the feed
   * is emptied on the way out, the node stops intersecting, nothing
   * fires. The notes conversation put one at the *top* instead, and that
   * turned the same arrangement into the owner's bug report of
   * 2026-08-24. The link handler's own `window.scrollTo(0, 0)` scrolls
   * the pager into view on the way out, so the watcher fires, clicks a
   * button belonging to a page that is no longer on screen, and repaints
   * the notes conversation over whatever was arriving.
   *
   * `load()` calls this before it renders anything, so the rule is the
   * same shape as `captureHome()` beside it: a page added later cannot
   * forget, because leaving is handled once rather than per renderer.
   */
  function stopScrollWatch() {
    if (attached) attached.disconnect();
    attached = null;
  }

  function renderSpans(parent, spans) {
    (spans || []).forEach(function (span) {
      if (span.kind === "code") parent.appendChild(el("code", null, span.text));
      else if (span.kind === "strong") parent.appendChild(el("strong", null, span.text));
      else if (span.kind === "attach") {
        // A file this site uploaded on his behalf. `render_inline` has
        // already checked the path starts `/api/upload/`, which is the
        // whole safety rule -- the href is never parsed out of the text
        // here, same as `link` below.
        parent.appendChild(attachNode(span.url, span.text, span.isImage));
      } else if (span.kind === "link") {
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
    var listTag = null;
    (blocks || []).forEach(function (block) {
      if (block.type === "li" || block.type === "oli") {
        var tag = block.type === "oli" ? "ol" : "ul";
        // A run of bullets that turns into a run of numbers is two lists,
        // not one: reusing the open element would put numbered items
        // inside a `<ul>` and lose the numbering the author meant.
        if (!list || listTag !== tag) {
          list = el(tag);
          listTag = tag;
          parent.appendChild(list);
        }
        var item = el("li");
        renderSpans(item, block.spans);
        list.appendChild(item);
        return;
      }
      list = null;
      listTag = null;
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
      var answers = commentsByCycle[String(asks[i].cycle)];
      if (answers && answers.length) continue;
      open.push(asks[i]);
    }
    return open;
  }

  /* The oldest of them, or null. Kept as its own name because the pill has
   * always named exactly one card and still does; `openAsks` is what the
   * count and the panel beside it read. */
  function oldestOpenAsk(status, commentsByCycle) {
    var open = openAsks(status, commentsByCycle);
    return open.length ? open[open.length - 1] : null;
  }

  /* "since 08-16", and the day count when it has been more than one.
   *
   * The wait is computed here, off the reader's own clock, and not on the
   * server: this payload is cached and warmed, so a number of days baked
   * into it would freeze at build time -- the same reason `build_status`
   * refuses to consult a clock at all. */
  function askWaitLabel(ask, now) {
    var stamp = (ask.date || "").slice(5);
    var label = stamp ? "since " + stamp : "";
    var start = Date.parse((ask.date || "") + "T" + (ask.time || "00:00") + ":00");
    if (!isNaN(start)) {
      var days = Math.floor(((now || Date.now()) - start) / 86400000);
      if (days >= 1) label += " · " + days + (days === 1 ? " day" : " days");
    }
    return label;
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
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
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

    /* An ask with no answer, pointing at the card it lives on.
     *
     * Suppressed on a replayed payload for the same reason the badges
     * below are: "he has not replied" is a claim about now, and a cached
     * comments payload cannot support it -- the failure mode is telling
     * him he owes an answer he gave an hour ago.
     *
     * A link and not a button: it is the same `/cycle/N` permalink every
     * card carries, so it survives a right-click, a share and the back
     * button, and it lands on the page where the reply box is. */
    if (!replayed && haveComments) {
      var stillOpen = openAsks(status, lastCommentsByCycle);
      var open = stillOpen.length ? stillOpen[stillOpen.length - 1] : null;
      if (open) {
        /* The pill used to be the only link in here and it was an `<a>`
         * inside a `<p>`; now the whole field is the link, so the pill
         * goes back to being a `<span>` — an `<a>` nested in an `<a>` is
         * not valid HTML and the parser unnests it, which would leave the
         * badge outside the field it belongs to. */
        var waiting = statusField(open.cycle);
        waiting.appendChild(el("span", "badge badge-ask", "waiting on you"));
        waiting.appendChild(el("span", "status-pr", "cycle " + open.cycle));
        var wait = askWaitLabel(open);
        if (wait) waiting.appendChild(el("span", "status-pr", wait));
        subs.appendChild(waiting);
      }

      /* Every *other* open ask, which the field above cannot carry.
       *
       * the owner, ideas board #182: *"I'm not able to read all the 'waiting
       * on you' and all the answers for the journals."* `status.asks`
       * carries every card that raised an ask, and the field above renders
       * one of them -- the oldest -- so a second and a third open question
       * were visible only by scrolling the feed and recognising a card.
       *
       * **This used to be a button that expanded a panel of its own, and
       * he asked for that gone**, capture 2026-09-01: *"Drop the current
       * 'needs me' functionality (the yellow 'N WAITING ON YOU'
       * button/list) ... and replace it with a simple filter that just
       * lists the journal entries that need his input. No fancy
       * list/carousel behavior, just a plain filtered view."* The panel
       * was a second rendering of cards the feed already knows how to
       * draw: it showed a cycle number and a wait, and nothing else, so
       * reading the question still meant tapping through to the card. So
       * this is now a link to `/asks`, which is the feed with one
       * predicate on it, and the whole expand/collapse mechanism is
       * deleted rather than restyled.
       *
       * Drawn only when there is more than one, unchanged: with a single
       * open ask the field above already points at the one card, and a
       * filtered view of one is the card. */
      if (stillOpen.length > 1) {
        var more = statusField(null);
        var all = el("a", "badge badge-ask status-asks-open",
          stillOpen.length + " waiting on you");
        all.href = "/asks";
        more.appendChild(all);
        subs.appendChild(more);
      }
    }


    /* The newest written entry's PR, so the cycle it references is
     * `status.cycle` — the same number the line above it prints.
     *
     * This field used to lead with the outcome pill. The owner, `issues.md`
     * 2026-08-23: "Drop the Outcome pill from the top-of-page header too,
     * not just the card view — it's the same ugly all-caps duplicate of the
     * blue summary line, shown twice on the same screen." Both copies were
     * on the feed at once: this field, and the newest card directly under
     * it. So the pill and its qualifier are gone from here as well, and
     * what the field is for — jump to what the last cycle shipped — is the
     * PR reference, which is why that is what survives.
     *
     * `isRealPr` because the footer is mandatory: a cycle with nothing to
     * show still writes `PR: none`, and a header field reading "none" is
     * the noise this ask is about wearing a shorter word. It is the same
     * predicate `settledPart` uses, so the header and the card agree about
     * what counts as a PR.
     *
     * The paragraph above describes #300 and is kept as the reason the
     * *qualifier* is still gone; its sentence about the pill itself was
     * overtaken on 2026-08-24 and the block below is what holds now. */
    /* ...and it is back, narrowed. The owner, `issues.md` 2026-08-24: "i miss
     * the status fields. Please bring them back." Same rule as the card:
     * `shortOutcome` only, so the field can hold a badge and can never hold
     * the clause that got it cut. The field draws when either half has
     * something to say -- a cycle that shipped nothing still has a status
     * worth one word, and that is the case he is asking about. */
    var headerOutcome = shortOutcome(status.lastOutcome);
    if (headerOutcome || isRealPr(status.lastPr)) {
      var line = statusField(status.cycle);
      if (headerOutcome) {
        line.appendChild(el("span", outcomeClass(headerOutcome), headerOutcome));
      }
      if (isRealPr(status.lastPr)) {
        line.appendChild(el("span", "status-pr", status.lastPr));
      }
      subs.appendChild(line);
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
      var live = statusField(null);
      live.appendChild(el("span", "badge badge-live", "cycle running"));
      live.appendChild(el("span", "status-pr", "its entry arrives when it finishes"));
      subs.appendChild(live);
    }

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
    var attach = buildAttach({
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
        // The text is the owner's own prose and the server sends it as plain
        // text, so each blank-line-separated paragraph becomes its own <p>.
        // Nothing here interprets it as markdown *except* the two things
        // `appendRichText` knows about: the attach line this site writes on
        // his behalf, and a fenced ```mermaid block. The second one arrived
        // for the chat and reaches here because the reader is shared, which
        // is deliberate -- a diagram that drew in one thread and printed as
        // source in another would read as a bug in whichever he saw second.
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
    var askToggle = null;
    var setAskOpen = null;
    if (asked.length) {
      /* the owner, ideas.md 2026-08-16 22:14: "When my reply answers the yellow
       * 'needs the owner' block on an entry, minimize it instead of leaving it
       * full-size -- and let the owner minimize it himself too. Don't delete it,
       * just collapse it."
       *
       * Two halves, and the second is what makes the first safe to guess at.
       *
       * "My reply answers it" is not something this page can read. What it
       * can read is whether he has said anything on this card at all, and
       * that is the whole mechanism the ask relies on -- the ask is raised
       * on the card, the card's drawer is opened for it, and his answer goes
       * in that drawer. So: a card he has commented on has been answered.
       * That proxy is wrong sometimes (a comment can be about something
       * else), which is exactly why he also asked for the manual control --
       * a wrong guess costs one tap, not an unread question.
       *
       * Collapsed keeps the label and the control. "It should not be
       * deleted, but be minimised" -- so the yellow row stays on the card
       * saying an ask lives here, and only its prose folds away. Hiding the
       * row itself would be the deletion he ruled out, and would put this
       * back where idea #56 was: a question with nowhere visible to answer. */
      var answered = !!(comments && comments.length);
      var ask = el("div", "entry-ask");
      var askHead = el("div", "entry-ask-head");
      /* the owner, unboarded capture 2026-08-21: "Change the 'needs the owner' to
       * 'needs input'." The label is what he reads; the marker inside the
       * entry text still parses both spellings, because the archive's asks
       * are written and never edited. */
      askHead.appendChild(el("p", "entry-ask-label", "Needs input"));
      askToggle = el("button", "entry-ask-toggle");
      askToggle.type = "button";
      askHead.appendChild(askToggle);
      ask.appendChild(askHead);
      var askBodies = el("div", "entry-ask-bodies");
      askBodies.id = "ask-" + (entry.cycle === null || entry.cycle === undefined
        ? Math.random().toString(36).slice(2) : entry.cycle);
      asked.forEach(function (part) {
        var askBody = el("p", "entry-ask-body");
        renderSpans(askBody, part.askSpans);
        askBodies.appendChild(askBody);
      });
      ask.appendChild(askBodies);
      askToggle.setAttribute("aria-controls", askBodies.id);

      setAskOpen = function (open) {
        askBodies.hidden = !open;
        ask.classList.toggle("is-collapsed", !open);
        askToggle.setAttribute("aria-expanded", open ? "true" : "false");
        askToggle.textContent = open ? "Minimize" : "Show";
        askToggle.setAttribute("aria-label",
          (open ? "Minimize" : "Show") + " what this cycle needs from you");
      };
      /* Tri-state on purpose. `null` means he has not touched this card's
       * ask, so the answered-guess decides; once he has, his choice wins and
       * a background poll rebuilding the card does not overrule it. Storing
       * a plain boolean would make "he opened it" and "it was never
       * collapsed" the same value, and the next poll would re-collapse it
       * under him. */
      setAskOpen(fold.ask === null || fold.ask === undefined ? !answered : !fold.ask);
      card.appendChild(ask);
    }

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
      /* His own minimize, and it does not touch the card. Same reason the
       * chat bubble returns early: folding the ask is a decision about the
       * ask, not a request to read or close the cycle behind it. `fold.ask`
       * is written here rather than inside `setAskOpen`, so the first paint
       * -- which is a guess, not his choice -- leaves the tri-state alone. */
      if (setAskOpen && event.target.closest(".entry-ask-toggle")) {
        var wantOpen = askToggle.getAttribute("aria-expanded") !== "true";
        fold.ask = !wantOpen;
        setAskOpen(wantOpen);
        return;
      }
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

  function render(journal, digest, comments) {
    /* Drop an answer to a query he has already typed past.
     *
     * `load()` guards against having navigated to a different *view* and
     * against nothing else, so two searches in flight together are
     * resolved in whatever order the server finishes them -- and it is a
     * threading server, where the broader, older query is the one doing
     * more work, so finishing last is the ordinary case rather than the
     * unlucky one. Without this the feed silently reverts to the results
     * for the shorter word, with a count line to match, and nothing
     * anywhere says it happened.
     *
     * The board search has carried the same guard since it shipped
     * (`result.query !== query` in `runBoardSearch`) and I did not copy
     * it, because the count line reads `journal.query` and I mistook
     * *displaying* the answered query for *checking* it. Those are
     * different things and only one of them protects anything.
     *
     * Before `stopPolling`, deliberately: a stale answer must cost
     * nothing, and bailing out below that line would leave the tab with
     * no poll timer at all. Every path that changes `journalQuery` also
     * starts a fresh `load`, so dropping this one never leaves the page
     * waiting on an answer that will not come. */
    var live = journalQuery.trim().toLowerCase() || null;
    if (routedCycle(window.location.pathname) === null
        && (journal.query || null) !== live) return;
    stopPolling();
    markNav();
    // What the page is now showing, so the poll below can tell "nothing
    // changed" from "changed while he was typing".
    renderedVersion = (journal && journal.version) || null;
    renderedComments = JSON.stringify(comments);
    var commentsByCycle = (comments && comments.byCycle) || {};
    var commentsByEntry = (comments && comments.byEntry) || {};

    // `null` and not the empty object when the fetch itself failed: "no
    // comments" and "no answer about the comments" are different, and only
    // the first one licenses the header to say he owes a reply.
    renderStatus(journal.status || {}, comments ? commentsByCycle : null);
    paintChanged(journal.status || {}, journal.entries || []);

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
        var answers = commentsByCycle[String(entry.cycle)];
        return !(answers && answers.length);
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
    if (filtered) {
      var backAll = el("a", "back", "← all entries");
      backAll.href = "/";
      feed.appendChild(backAll);
      feed.appendChild(el("p", "empty", entries.length === 0
        ? "Nothing is waiting on you."
        : entries.length === 1
          ? "1 entry is waiting on you."
          : entries.length + " entries are waiting on you."));
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
    /* The hole in the record, marked where it happened (#72). The owner found
     * cycles 127 and 128 himself, by noticing the numbers on this feed jump
     * from 126 to 129 -- so the gap is put back exactly where he was
     * already looking, rather than summarised in a counter at the top.
     *
     * The server decides what counts as missing; this only decides where
     * to put it. That matters because a window is a contiguous slice of
     * the corpus but an unnumbered entry is not: filling in every number
     * between two cards from the client's own arithmetic would invent gaps
     * for the owner's own notes, which have no cycle number to be missing.
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
      feed.appendChild(renderEntry(parts, byCycle[cycle], thread));
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
    /* `!filtered`: `/asks` sends no window, so there is nothing more to
     * fetch -- and `total` is the number of asks the server found while
     * `entries` is the ones he has not answered, so the two differ by
     * exactly the answered ones and the pager would otherwise be drawn
     * permanently, offering to load entries that are already here. */
    if (wanted === null && !filtered && typeof total === "number" && entries.length < total) {
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

  function buildJournalSearch() {
    var box = el("section", "journal-search");
    box.id = "journal-search";
    var row = el("div", "journal-search-row");
    var input = document.createElement("input");
    input.type = "search";
    input.className = "journal-search-input";
    input.placeholder = "Search the journal";
    input.setAttribute("aria-label", "Search the journal");
    row.appendChild(input);

    // Built whether or not there is anything to clear and hidden rather
    // than absent, for the reason the board's clear button carries:
    // removing it on the last keystroke moves the caret's own neighbour
    // out from under his thumb mid-edit.
    var clear = el("button", "journal-search-clear", "×");
    clear.type = "button";
    clear.hidden = true;
    clear.setAttribute("aria-label", "Clear the search");
    clear.addEventListener("click", function () {
      journalQuery = "";
      input.value = "";
      clear.hidden = true;
      if (journalSearchTimer) clearTimeout(journalSearchTimer);
      windowSize = PAGE;
      load();
      // Synchronous, inside the tap, so the keyboard stays up.
      input.focus();
    });
    row.appendChild(clear);
    box.appendChild(row);

    // `role="status"` so the count is announced when it changes rather
    // than only being visible -- the result of a search is a number, and
    // the number is the whole answer when it is zero.
    var count = el("p", "journal-search-count");
    count.setAttribute("role", "status");
    count.hidden = true;
    box.appendChild(count);

    input.addEventListener("input", function () {
      journalQuery = input.value;
      clear.hidden = !input.value;
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
    var url = "/api/journal?limit=" + windowSize;
    var q = journalQuery.trim();
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
    // copy: `renderPriorityPicker` writes back to the object it was
    // handed, and a copy would take that write with it.
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
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
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

  /* The rating cell of one boarded row, as something the owner can change --
   * the row's own priority indicator in `.item-meta-row`, not a second
   * control hidden inside the write-up (the owner, 2026-08-14: "on issues and
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
      who.appendChild(el("span", "note-msg-name", message.author || "Nova"));
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

  /* One row of the owner's board. Closed it is the number, the title and a
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
    // needed to sit inside it, level with the status chip (the owner,
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

    // Every rating on both boards was set by a cycle, not by the owner
    // (issues.md capture, 2026-08-14). A finished row keeps the original
    // cycle-171 read-only chip if it has a rating and nothing if it does
    // not -- unrated getting no chip at all, rather than a grey "none"
    // one, is what tells the owner which open rows still want a rating; a
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

    // Below the status/priority line rather than beside it (the owner,
    // 2026-08-14: "the date should be placed below them").
    if (item.updated) head.appendChild(el("span", "item-updated", item.updated));

    row.appendChild(head);

    var body = el("div", "item-body");
    if (boardState.open !== item.number) body.hidden = true;
    row.appendChild(body);

    /* the owner, capture 2026-08-22: *"I can't delete, edit or upload a file
     * to a boarded issues. I wanted to delete issue #4 but i'm not able
     * to."* #4 is an ordinary open row, so nothing about that row made it
     * read-only -- the only way into the editor was the one-second hold,
     * an invisible gesture with no label anywhere on the page.
     *
     * **I did not try to work out whether the hold also fails on his S25,
     * and that is the point of fixing it this way.** A phone gesture is
     * not measurable from in here (three cycles have already guessed at
     * one), so the repair is chosen to land whichever theory is true: if
     * the hold breaks on his device the button reaches the editor anyway,
     * and if it works and he simply never knew it existed, the button
     * says so. The hold stays -- it costs nothing and he asked for it.
     */
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
          body: JSON.stringify({ target: board, number: item.number, text: body, author: "Edvard" }),
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
      var attach = buildAttach({
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

  function renderBoardEdvard(board, payload) {
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

  /* My own rows, cut by the search and by nothing else. `visibleItems`
   * is not reused here on purpose: it applies the status filter and the
   * toggles, and those live on the strip above *his* rows, which my tab
   * does not draw. Filtering by a control the reader cannot see would
   * hide rows with no way to get them back. */
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
    boardRows.textContent = "";
    var shown = visibleNovaItems(items);
    if (!shown.length) {
      boardRows.appendChild(el(
        "p", "empty",
        boardState.query.trim() ? "Nothing matches “" + boardState.query.trim() + "”."
          : "Nothing here."
      ));
    }
    shown.forEach(function (item) { boardRows.appendChild(renderNovaItem(board, item)); });
  }

  /* Redraw only what a search changed. The owner, issues.md, 2026-08-15:
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
      { key: "edvard", label: "Edvard's " + titles.page.toLowerCase() },
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

  /* ---- The costs page (issues.md #57, page 2) --------------------------
   *
   * the owner, 2026-08-08: "I want you to figure out the optimal method of
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
   * Every mark used to be built by hand with createElementNS. As of
   * 2026-08-20 the drawing belongs to Apache ECharts and this file only
   * describes what to draw -- see the chart layer below for why.
   */

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

  /* ---- Charts, on a real charting library --------------------------------
   *
   * the owner, 2026-08-20, on the hand-rolled version this replaces: "there
   * are still quite the amount of bugs and better ux improvements. Can we
   * just use a third party library for this? We do not have to reinvent
   * the wheel here. ... The zoom works, but it does not give me any more
   * granulation in the graph, it just makes the graph bars larger. I want
   * actual graph zoom as in expanding the values on the x/y axis and
   * showing more granularity. Also the hover effect when i press the graph
   * only works for a split second. I should be able to select stuff, move
   * around."
   *
   * He is describing the exact limit of what the old code could do. It
   * zoomed by putting a CSS `transform: scale()` on the rendered SVG,
   * which magnifies the picture and cannot add a tick to an axis: the
   * bars got fatter and the two date labels stayed the same two dates.
   * Real zoom means re-deriving the scales and redrawing, and doing that
   * with a crosshair, a sticky tooltip, pinch, drag-pan and rubber-band
   * selection on top is a charting library. So: Apache ECharts 5.5.1,
   * vendored at `/vendor/echarts.min.js` (Apache-2.0), and about 400
   * lines of hand-written SVG deleted.
   *
   * Vendored rather than a CDN because the app is served over a tailnet
   * and is meant to work on a dead link -- a CDN script tag is a chart
   * page that goes blank the moment the phone is off the internet, which
   * is the failure the service worker exists to prevent. It is 1.0 MB, so
   * it is loaded lazily on the first chart rather than in the shell, and
   * cached by the worker on first use.
   */
  var ECHARTS_SRC = "/vendor/echarts.min.js";
  var echartsLoading = null;

  function ensureECharts() {
    if (window.echarts) return Promise.resolve(window.echarts);
    if (echartsLoading) return echartsLoading;
    echartsLoading = new Promise(function (resolve, reject) {
      var tag = document.createElement("script");
      tag.src = ECHARTS_SRC;
      tag.async = true;
      tag.onload = function () {
        if (window.echarts) resolve(window.echarts);
        else reject(new Error("echarts loaded but did not register"));
      };
      tag.onerror = function () { reject(new Error("could not load " + ECHARTS_SRC)); };
      document.head.appendChild(tag);
    });
    // A failed load must not poison every later chart: drop the memo so
    // the next page visit retries. Offline once is not offline forever.
    echartsLoading.catch(function () { echartsLoading = null; });
    return echartsLoading;
  }

  /* A finger is not a mouse, and on these charts the difference decides
   * whether the page can scroll.
   *
   * ECharts' `inside` dataZoom pans on drag, and its drag handler calls
   * preventDefault on the event it was handed -- which on a phone is the
   * touchmove. Two full-width charts stacked down the costs page therefore
   * become a wall: a finger that lands on a chart pans the chart, and the
   * page underneath does not move. Nova is read on a phone first, so that
   * is the common case, not the edge one.
   *
   * On a coarse pointer the finger belongs to the page, and the chart is
   * moved with the slider under it instead -- a control that cannot be hit
   * by accident. Pinch-to-zoom is untouched: ECharts registers its pinch
   * handler on the zoom branch of the roam controller and gates only the
   * drag on `moveOnMouseMove`, so turning pan off leaves zooming alone. On
   * a mouse, drag-to-pan stays exactly as it was.
   */
  var COARSE_POINTER = (function () {
    try {
      return !!(window.matchMedia && window.matchMedia("(pointer: coarse)").matches);
    } catch (err) {
      // A browser that cannot answer the question is treated as a mouse:
      // that is the behaviour this app already shipped.
      return false;
    }
  })();

  /* One chart's frame: the caption, the box ECharts mounts into, and the
   * full-screen button. Everything inside the box -- axes, grid, marks,
   * crosshair, tooltip, zoom, pan, selection -- belongs to the library
   * now, which is the point of the change.
   */
  function chartFrame(title, subtitle) {
    var figure = el("figure", "chart");
    figure.appendChild(el("figcaption", "chart-title", title));
    if (subtitle) figure.appendChild(el("p", "chart-sub", subtitle));
    var plot = el("div", "chart-plot");
    figure.appendChild(plot);
    var chart = { figure: figure, plot: plot, title: title };

    var tools = el("div", "chart-tools");
    // Said in words, not left to an icon. The library's own toolbox
    // glyphs sit inside the plot and are unlabelled; this is the one
    // control that changes the page rather than the picture.
    var full = el("button", "chart-tool chart-tool-full", "Full screen");
    full.type = "button";
    full.setAttribute("aria-label", "Full screen: " + title);
    full.title = full.getAttribute("aria-label");
    full.addEventListener("click", function () {
      setChartFullscreen(chart, !figure.classList.contains("chart-full"));
    });
    tools.appendChild(el(
      "span", "chart-tools-hint",
      COARSE_POINTER
        ? "Pinch to zoom · drag the bar below to pan · tap a point to pin the readout"
        : "Scroll to zoom · drag to pan · click a point to pin the readout"
    ));
    tools.appendChild(full);
    figure.appendChild(tools);
    return chart;
  }

  /* Shared option scaffolding.
   *
   * The three things the owner asked for, each named where it is set:
   *  - granularity: `dataZoom` re-scales the axis and ECharts re-derives
   *    its ticks, so zooming in genuinely turns "14 Aug — 20 Aug" into
   *    hours. `filterMode: "none"` keeps the marks outside the window
   *    drawn rather than dropped, so panning does not blank a line.
   *  - a readout that stays: `triggerOn: "mousemove|click"` means a tap
   *    on a phone pins the tooltip instead of showing it for the length
   *    of the touch, which is the "split second" he is describing.
   *  - selection and moving around: `toolbox.dataZoom` is rubber-band
   *    select-to-zoom, `type: "inside"` is pinch and drag-pan, and
   *    `restore` puts it all back.
   */
  var CHART_FONT = 11;

  function baseOption(opts) {
    // See COARSE_POINTER: drag-to-pan on a touchscreen eats the page's
    // scroll, so on a phone the slider does the panning.
    var dragPans = !COARSE_POINTER;
    var yZoom = opts.zoomY === false ? [] : [
      { type: "inside", yAxisIndex: 0, filterMode: "none",
        zoomOnMouseWheel: "shift", moveOnMouseMove: dragPans },
    ];
    return {
      animation: false,
      backgroundColor: "transparent",
      textStyle: { color: AXIS_INK, fontSize: CHART_FONT },
      grid: { left: 44, right: 12, top: 12, bottom: 56, containLabel: false },
      tooltip: {
        trigger: "axis",
        triggerOn: "mousemove|click",
        confine: true,
        axisPointer: { type: "cross", label: { show: false },
                       crossStyle: { color: AXIS_INK }, lineStyle: { color: AXIS_INK } },
        backgroundColor: "rgba(16,18,26,0.94)",
        borderColor: GRID,
        textStyle: { color: "#e6e8f0", fontSize: CHART_FONT + 1 },
        formatter: opts.tooltip,
      },
      toolbox: {
        right: 8, top: 2, itemSize: 13,
        iconStyle: { borderColor: AXIS_INK },
        emphasis: { iconStyle: { borderColor: "#e6e8f0" } },
        feature: {
          dataZoom: { yAxisIndex: "none", title: { zoom: "Select an area to zoom", back: "Undo zoom" } },
          restore: { title: "Reset" },
        },
      },
      dataZoom: [
        { type: "inside", xAxisIndex: 0, filterMode: "none", moveOnMouseMove: dragPans },
        {
          type: "slider", xAxisIndex: 0, filterMode: "none",
          height: 22, bottom: 8,
          borderColor: GRID, fillerColor: "rgba(93,134,221,0.16)",
          handleStyle: { color: SERIES_A, borderColor: SERIES_A },
          moveHandleStyle: { color: GRID },
          dataBackground: { lineStyle: { color: AXIS_INK }, areaStyle: { color: GRID } },
          textStyle: { color: AXIS_INK, fontSize: CHART_FONT - 1 },
        },
      ].concat(yZoom),
      xAxis: {
        type: "time",
        min: opts.from, max: opts.to,
        axisLine: { lineStyle: { color: GRID } },
        axisTick: { lineStyle: { color: GRID } },
        axisLabel: { color: AXIS_INK, hideOverlap: true },
        splitLine: { show: false },
      },
      yAxis: {
        type: "value",
        min: opts.min, max: opts.max,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: AXIS_INK, formatter: opts.yLabel },
        splitLine: { lineStyle: { color: GRID } },
      },
      series: opts.series,
    };
  }

  /* Mount a built option into a frame once the library and the layout are
   * both ready.
   *
   * `init` needs a box with a real size, and the figure is not in the
   * document at the moment the render function returns it -- the caller
   * appends it afterwards. So this waits a frame and checks: an element
   * that never lands (the reader navigated away mid-load) is dropped
   * rather than initialised into a zero-width canvas.
   */
  var liveCharts = [];

  /* Forget the charts whose element has left the document, and give the
   * library back the canvas.
   *
   * Every ECharts instance holds a canvas and its own zrender event
   * handlers, and this is one page that swaps its whole view on
   * navigation -- so each visit to the costs page left two more instances
   * alive behind a detached element, with a window resize as the only
   * thing that ever pruned them. On a phone that is a tab left open all
   * day and never resized. Pruning at mount too bounds it at the charts
   * actually on screen.
   */
  function pruneCharts() {
    liveCharts = liveCharts.filter(function (chart) {
      if (chart.plot.isConnected) return true;
      chart.instance.dispose();
      return false;
    });
  }

  function mountEChart(chart, option) {
    /* Hung on the figure synchronously, before anything async starts, and
     * it is the only reason the charts are testable at all. ECharts draws
     * to a canvas, and jsdom has no canvas -- so `tests/browser` cannot
     * assert on a mark the way it did against hand-written SVG. What it
     * can assert on is the description this app hands the library, which
     * is now the whole of what this app decides about a chart. Reading a
     * rect's height was never testing the app's judgement anyway; it was
     * testing arithmetic that has since been deleted. */
    chart.option = option;
    chart.figure.chartOption = option;
    ensureECharts().then(function (echarts) {
      return new Promise(function (resolve) {
        requestAnimationFrame(function () { resolve(echarts); });
      });
    }).then(function (echarts) {
      if (!chart.plot.isConnected) return;
      pruneCharts();
      var instance = echarts.init(chart.plot, null, { renderer: "canvas" });
      instance.setOption(option);
      chart.instance = instance;
      liveCharts.push(chart);
    }).catch(function (err) {
      // Never a blank box. A chart that cannot draw says so, in the space
      // it would have used.
      if (chart.plot.childNodes.length) return;
      chart.plot.appendChild(el("p", "empty", "Chart could not load: " + err.message));
    });
  }

  window.addEventListener("resize", function () {
    pruneCharts();
    liveCharts.forEach(function (chart) { chart.instance.resize(); });
  });

  /* Full screen, unchanged in spirit from the version the owner asked for --
   * the phone-sized figure gets the whole window, which in landscape is a
   * much bigger picture and in portrait at least stops the tiles and the
   * other charts competing for it.
   *
   * The one thing it must now do that it did not before: tell the chart
   * its box changed. ECharts sizes its canvas at `init` and does not
   * watch the element, so without the `resize` the overlay would open on
   * a phone-width picture stretched across the screen.
   */
  var openFullChart = null;

  function setChartFullscreen(chart, on) {
    if (on && openFullChart && openFullChart !== chart) {
      setChartFullscreen(openFullChart, false);
    }
    chart.figure.classList.toggle("chart-full", on);
    document.body.classList.toggle("has-full-chart", on);
    var button = chart.figure.querySelector(".chart-tool-full");
    if (button) {
      button.textContent = on ? "Close" : "Full screen";
      // Two charts on the costs page means two buttons reading "Full
      // screen", and a screen reader announcing them identically is the
      // same failure as a bare priority glyph: the control does not say
      // what it acts on.
      button.setAttribute(
        "aria-label",
        (on ? "Close full screen: " : "Full screen: ") + (chart.title || "")
      );
      button.title = button.getAttribute("aria-label");
    }
    openFullChart = on ? chart : null;
    if (chart.instance) requestAnimationFrame(function () { chart.instance.resize(); });
  }

  function closeFullChart() {
    if (openFullChart) setChartFullscreen(openFullChart, false);
  }

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") closeFullChart();
  });

  /* A tooltip body, in the shape all three charts want: a stamp, then one
   * row per series with its own swatch. ECharts hands the formatter the
   * params for every series under the pointer; `rows` maps those to the
   * label and value this particular chart wants to print.
   */
  function tipHtml(when, rows) {
    var html = '<div class="chart-tip-when">' + escapeHtml(when) + "</div>";
    rows.forEach(function (row) {
      html += '<div class="chart-tip-row">'
        + '<span class="chart-tip-swatch" style="background:' + row.color + '"></span>'
        + '<span class="chart-tip-label">' + escapeHtml(row.label) + "</span>"
        + '<span class="chart-tip-value">' + escapeHtml(row.value) + "</span>"
        + "</div>";
    });
    return html;
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
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
      chart.plot.appendChild(el("p", "empty", "No cycles in the ledger yet."));
      return chart.figure;
    }
    // [when, minutes, turns, ?, weighted] -- the library wants [x, y] and
    // carries the rest through untouched for the tooltip.
    var data = rows.map(function (row) {
      return { value: [row[0], row[4]], minutes: row[1], turns: row[2] };
    });
    mountEChart(chart, baseOption({
      from: domain.from, to: domain.to,
      min: 0, max: null,
      yLabel: fmtTokens,
      series: [{
        type: "bar",
        name: "Weighted",
        data: data,
        itemStyle: { color: SERIES_A },
        // A bar per cycle placed at the moment it ran, not evenly spaced:
        // the loop has been idle for days at a stretch and run fourteen
        // cycles in one, and the gaps are the finding. On a time axis the
        // library sizes bars from the *smallest* gap in the data, which
        // used to be arithmetic this file did by hand; a minimum keeps a
        // lone cycle in a quiet week visible rather than sub-pixel.
        barMinWidth: 1,
        barMaxWidth: 14,
        large: true,
      }],
      tooltip: function (params) {
        var point = params[0];
        if (!point) return "";
        var extra = point.data || {};
        return tipHtml(fmtStamp(point.value[0]), [
          { color: SERIES_A, label: "Weighted", value: fmtTokens(point.value[1]) },
          { color: "transparent", label: "Ran for", value: extra.minutes + " min" },
          { color: "transparent", label: "Turns", value: String(extra.turns) },
        ]);
      },
    }));
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
      chart.plot.appendChild(el("p", "empty", "No quota readings yet."));
      return chart.figure;
    }
    var series = [
      { index: 1, color: SERIES_A, label: "5-hour window" },
      { index: 3, color: SERIES_B, label: "7-day window" },
    ].map(function (spec) {
      return {
        type: "line",
        name: spec.label,
        // A reading that predates this field is a hole, not a zero, and
        // `connectNulls: false` is what stops the line dropping to the
        // axis and back -- which would read as the quota emptying.
        connectNulls: false,
        showSymbol: false,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2, color: spec.color },
        itemStyle: { color: spec.color },
        data: rows.map(function (row) {
          var value = row[spec.index];
          return [row[0], value === null || value === undefined ? null : value];
        }),
      };
    });
    mountEChart(chart, baseOption({
      from: domain.from, to: domain.to,
      min: 0, max: 100,
      yLabel: function (v) { return v + "%"; },
      series: series,
      tooltip: function (params) {
        if (!params.length) return "";
        return tipHtml(fmtStamp(params[0].value[0]), params.map(function (point) {
          return {
            color: point.color,
            label: point.seriesName,
            value: point.value[1] === null ? "—" : point.value[1] + "%",
          };
        }));
      },
    }));

    // Two series, so a legend is not optional -- identity must not rest on
    // colour alone.
    var legend = el("div", "chart-legend");
    [
      { color: SERIES_A, label: "5-hour window" },
      { color: SERIES_B, label: "7-day window" },
    ].forEach(function (spec) {
      var key = el("span", "legend-key");
      var swatch = el("span", "legend-swatch");
      swatch.style.background = spec.color;
      key.appendChild(swatch);
      key.appendChild(el("span", "legend-label", spec.label));
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

  /* What the cycles delegated, added up off the rows.
   *
   * A cycle's `weighted` column is what that session was charged and
   * deliberately excludes the subagents it spawned -- see `_subagent` in
   * `nova_costs.py`. So every tile and chart on this page understated a
   * delegating cycle until these columns existed, on the one page the
   * question "where does the money go" is asked on.
   *
   * Two things this deliberately does not do. It does not read
   * `summary.subagent_weighted`, which is computed from the transcripts on
   * disk now and counts orphans, so it disagrees with the rows the chart
   * plots. And it does not divide by the all-time total: rows older than
   * 2026-08-19 carry no attribution at all and arrive as `null`, so the
   * denominator is the parent spend of the rows that were actually
   * measured. Dividing by everything would report a share of a period
   * nobody was counting in, which is a smaller number and a false one.
   */
  function delegatedSpend(payload) {
    var rows = payload.cycles || [];
    var tokens = 0, parent = 0, counted = 0, from = null;
    rows.forEach(function (r) {
      if (r[6] === null || r[6] === undefined) return;
      if (from === null) from = r[0];
      counted += 1;
      tokens += r[6];
      parent += r[4] || 0;
    });
    if (!counted) return null;
    return {
      tokens: tokens,
      from: from,
      share: parent + tokens > 0 ? (100 * tokens) / (parent + tokens) : 0,
    };
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
    var delegated = delegatedSpend(payload);
    if (delegated) {
      row.appendChild(statTile(
        "Delegated",
        fmtTokens(delegated.tokens),
        delegated.share.toFixed(1) + "% of spend since " + fmtDay(delegated.from)
      ));
    }
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
   * the owner: "Rate yourself on a scale from 1 to 10 on how you feel its
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
      chart.plot.appendChild(el("p", "empty", "No retrospectives yet."));
      return chart.figure;
    }
    var range = payload.range || [1, 10];
    var lo = range[0];
    var hi = range[1];
    var from = rows[0].at;
    var to = rows[rows.length - 1].at;
    // One retro is a single moment, so the domain has no width. Give it a
    // week either side, which is what the axis would show once the second
    // retro lands.
    if (to === from) {
      from -= 3.5 * 24 * 3600 * 1000;
      to += 3.5 * 24 * 3600 * 1000;
    }
    mountEChart(chart, baseOption({
      from: from, to: to,
      min: lo, max: hi,
      // Five retros are five observations however wide the window is, so
      // the y axis is not something to zoom into -- it is a 1-to-10 scale
      // with ten possible values.
      zoomY: false,
      yLabel: function (v) { return String(v); },
      series: series.map(function (line) {
        return {
          type: "line",
          name: line.label,
          // Same rule as the quota chart: a missing score is a hole, not
          // a zero, and a line drawn down to the axis and back would read
          // as a week that went catastrophically.
          connectNulls: false,
          // A dot per retro as well as the line. With one retro there is
          // no line to see at all, and with five there are still only five
          // real observations -- marking them stops the eye reading the
          // segments between as data.
          showSymbol: true,
          symbol: "circle",
          symbolSize: line.width * 2.5,
          lineStyle: { width: line.width, color: line.color },
          itemStyle: { color: line.color },
          data: rows.map(function (row) {
            var value = (row.scores || {})[line.key];
            return [row.at, typeof value === "number" ? value : null];
          }),
        };
      }),
      tooltip: function (params) {
        if (!params.length) return "";
        return tipHtml(fmtDay(params[0].value[0]), params.map(function (point) {
          return {
            color: point.color,
            label: point.seriesName,
            value: point.value[1] === null ? "—" : point.value[1] + "/" + hi,
          };
        }));
      },
    }));

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

  /* The one screen, drawn first (ideas.md #120).
   *
   * He asked for "one screen -- what shipped, what broke, what is still
   * stuck, and the one thing you would want to change", because a
   * chat-style report had read better to him than the journal did,
   * twice. So this is four short labelled paragraphs and nothing else:
   * no scores, no chart, no cycle numbers. Everything that answers "is
   * the loop getting better" is below it and is a different question.
   *
   * It is drawn from the newest retro that *has* a summary rather than
   * from the newest retro, because the three retros written before #120
   * have none, and skipping to the last real one is the difference
   * between an empty card and no card. Returns null when no retro has
   * written one yet -- the first is due the next time the retro runs. */
  function renderWeekCard(payload) {
    var rows = payload.retros || [];
    var row = null;
    for (var i = rows.length - 1; i >= 0; i--) {
      if (rows[i].week) { row = rows[i]; break; }
    }
    if (!row) return null;

    var card = el("article", "week-card");
    var head = el("header", "week-head");
    head.appendChild(el("h2", "week-title", "This week"));
    head.appendChild(el("p", "week-date", row.date));
    card.appendChild(head);
    (payload.weekKeys || []).forEach(function (part) {
      var text = row.week[part.key];
      if (!text) return;
      card.appendChild(el("h3", "week-sub", part.label));
      card.appendChild(el("p", "week-text", text));
    });
    return card;
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
    var week = renderWeekCard(payload);
    if (week) feed.appendChild(week);
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
   * on this page with a figure worth drawing. */
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
    if (item.board) card.appendChild(el("p", "rank-board", item.board));
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
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
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
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
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
  function renderProjectColumn(board, column) {
    var wrap = el("div", "project-column");
    wrap.appendChild(el("h3", "project-column-head",
      column.label + " · " + column.items.length));
    var list = el("ul", "project-rows");
    for (var i = 0; i < column.items.length; i++) {
      var item = column.items[i];
      var row = el("li", "project-row");
      row.appendChild(el("span", "project-row-num", "#" + item.number));
      row.appendChild(el("span", "project-row-title", item.title));
      // Only when there is one. A `## Done` row carries no priority cell
      // at all, so an unconditional chip would draw an empty coloured pill
      // on every finished row.
      if (item.priority) {
        row.appendChild(el("span", "chip prio prio-" + item.priorityKey, item.priority));
      }
      list.appendChild(row);
    }
    wrap.appendChild(list);
    return wrap;
  }

  function renderProjectIndex(payload) {
    var here = route(window.location.pathname);
    var wrap = el("div", "project-index");
    var projects = (payload && payload.projects) || [];
    var rated = (payload && payload.projectPriority) || {};
    for (var i = 0; i < projects.length; i++) {
      var name = projects[i];
      var link = el("a", "project-pill", name);
      link.setAttribute("href", "/project/" + encodeURIComponent(name));
      if (here.project && here.project.toLowerCase() === name.toLowerCase()) {
        link.className = "project-pill on";
      }
      // The server has already put the highest-rated project first; the
      // chip is what makes that order readable rather than mysterious.
      // Only when there is one -- every project is unrated today, so an
      // unconditional chip would draw an empty pill on all of them, the
      // same call `renderProjectColumn` makes for a `## Done` row.
      var rating = rated[name.toLowerCase()];
      if (rating && rating.priority) {
        link.appendChild(el(
          "span", "chip prio prio-" + rating.priorityKey, rating.priority));
      }
      wrap.appendChild(link);
    }
    return wrap;
  }

  /* The rating of one project, as something the owner can change --
   * his capture, 2026-09-01: *"Each project should also be able to be
   * assigned a priority, making one project and its tasks more important
   * than others."*
   *
   * `buildPrioPicker` verbatim, the same control the board rows use, so a
   * project's rating and a row's rating are picked the same way and read
   * the same colour. It saves on change with no Save button, for the
   * reason `renderPriorityPicker` gives: the only action it can take is
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
    if (summary.priorities && summary.priorities.length) {
      var chips = el("div", "project-summary-prios");
      summary.priorities.forEach(function (entry) {
        chips.appendChild(el("span", "project-summary-prio prio-" + (entry.key || "none"),
          entry.label + " · " + entry.count));
      });
      box.appendChild(chips);
    }
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
      // Word beside the symbol, never the symbol alone: `item.priority` is
      // already "🟠 High" off the board cell. An unrated row gets no chip
      // rather than an empty coloured pill.
      if (item.priority) {
        li.appendChild(el("span", "chip prio prio-" + item.priorityKey, item.priority));
      }
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

  function renderProjectPriority(name, payload) {
    var row = el("div", "project-prio");
    row.appendChild(el("span", "project-prio-label", "Project priority"));
    var note = el("span", "project-prio-note", "");
    var rated = ((payload && payload.projectPriority) || {})[name.toLowerCase()];
    // `.el` -- `buildPrioPicker` answers `{el, getValue, setValue}`, not a
    // node. It is a chip that opens the shared `.prio-menu` overlay, the
    // same control a board row's rating uses, not a `<select>`.
    row.appendChild(buildPrioPicker({
      current: (rated && rated.priority) || "",
      ariaLabel: "Priority of the " + name + " project",
      chipStyle: true,
      onPick: function (chosen) {
        note.textContent = "Saving…";
        return fetch("/api/project/priority", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ project: name, priority: chosen })
        })
          .then(json)
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && result.message) || "failed");
            note.textContent = "";
            // Reload rather than patching `payload` in place: the index
            // above is *sorted* by this value, so the visible consequence
            // of the pick is a reorder, and patching the one field would
            // leave the pills in the old order with a new chip on one.
            load();
          })
          .catch(function (err) {
            note.textContent = "Could not save: " + err;
            throw err;
          });
      },
    }).el);
    row.appendChild(note);
    return row;
  }

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
    // Its own class rather than `project-board-head`: that one means "a
    // board section is here", and a test that counts board sections was
    // already reading it.
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
          // Refetched rather than appended locally, for the same reason the
          // board row drops its cached write-up: the page must show what the
          // file actually holds, not what this tab believes it sent.
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
    // The box goes above the thread and the newest reply goes to the top of
    // it. The owner, idea #166: *"The input field is at the top and the
    // comments are below it with the newest reply at the top (basicly
    // inverted for everything else we have)."* Inverted is the word -- the
    // notes page and every row thread read oldest-first, and he wants this
    // one the other way round, because a project conversation is something
    // he checks rather than something he reads through.
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

  /* Which part of a project is on screen -- idea #166.
   *
   * The owner: *"Maybe the best is to have \"tabs\"/buttons that say issues,
   * ideas, conversation. When one of them is pressed the relevant items are
   * displayed and the others are hidden."* He described tabs and I answered
   * on the row suggesting chips that hide rather than switch; he did not
   * press for either, so this builds what he asked for. `All` is the fourth
   * one and it is the default, because the page he has today is the
   * combined view and a tab strip that can only take things away is a
   * regression for anyone who liked it.
   *
   * Held in a variable rather than in the URL. A tab is a view of a page,
   * not a page -- and `loadProject` refetches after every comment, so this
   * has to survive a re-render either way. The cost is that a tab is not
   * linkable, which is worth one line here if he ever asks for it.
   */
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
        // Redrawn from the payload already in hand rather than refetched:
        // switching tab shows him rows the page is holding, and a round
        // trip would blank them first.
        redraw();
      });
      row.appendChild(chip);
    });
    return row;
  }

  function renderProject(payload) {
    stopPolling();
    markNav();
    var name = (payload && payload.name) || "";
    var asked = (payload && payload.asked) || "";
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    statusEl.appendChild(el("p", "status-line",
      name ? name : "Projects"));
    feed.textContent = "";
    feed.appendChild(renderProjectIndex(payload));

    if (!asked) {
      feed.appendChild(el("p", "empty", "Pick a project."));
      return;
    }
    // Asked for a name no row carries. Said plainly rather than 404'd:
    // he types the project into a board cell, so a name with nothing
    // under it is a project he has not filed anything to yet, which is
    // not the same thing as a broken link.
    if (!name) {
      feed.appendChild(el("p", "empty",
        "Nothing is filed under “" + asked + "” yet."));
      return;
    }
    feed.appendChild(renderProjectPriority(name, payload));
    var summary = renderProjectSummary(payload);
    if (summary) feed.appendChild(summary);
    // Under the bar and above the tabs: the bar says how far along the
    // project is, this says what to do about it, and both belong before he
    // has chosen which board to look at.
    var backlog = renderProjectBacklog(payload);
    if (backlog) feed.appendChild(backlog);
    var tabs = projectTabs(payload);
    var tab = projectTabState(name, tabs);
    var tabRow = renderProjectTabs(tabs, function () { renderProject(payload); });
    if (tabRow) feed.appendChild(tabRow);
    // Built before the boards so the jump under the pills can point at it,
    // appended after them so the reading order does not change.
    var thread = renderProjectThread(name, payload);
    // Only in the combined view. On the conversation tab the thread is the
    // whole page, so a button that scrolls to it has nowhere to go.
    if (tab === "all") feed.appendChild(projectThreadJump(thread, payload));
    var boards = (payload && payload.boards) || {};
    var drew = false;
    var names = { issues: "Issues", ideas: "Ideas" };
    var order = ["issues", "ideas"];
    for (var b = 0; b < order.length; b++) {
      var key = order[b];
      if (tab !== "all" && tab !== key) continue;
      var board = boards[key];
      if (!board || !board.total) continue;
      drew = true;
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
      feed.appendChild(section);
    }
    // Only when he is looking at rows. On the conversation tab there are no
    // rows on screen by his own choice, and saying nothing is filed would
    // be the page arguing with the button he just pressed.
    if (!drew && tab !== "conversation") {
      feed.appendChild(el("p", "empty",
        "Nothing is filed under “" + name + "” yet."));
    }
    // Below the boards on purpose: the rows are what the project *is* and
    // the conversation is what has been said about them, which is the same
    // order a board row puts its write-up above its thread.
    if (tab === "all" || tab === "conversation") feed.appendChild(thread);
  }

  /* The jump from the top of a project page to its conversation.
   *
   * The owner, comments board 2026-08-28, on the first time he opened this
   * page: *"I see that the comment box is at the bottom making me scroll
   * all the way down. Not great ui."* The order below it is still right --
   * the rows are what the project is -- so the fix is a way down, not a
   * reshuffle. This sits under the project pills, where he already is when
   * the page paints.
   *
   * Its count is the reason it is a button rather than an anchor with a
   * label: "Conversation · 3" says there is something down there to read,
   * and "Conversation" alone says the box is empty and he is the first to
   * say anything. Focusing the box after the scroll is the point -- landing
   * next to it and still having to tap it is the same complaint one step
   * smaller. */
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

  function renderNoteMessage(note) {
    var msg = el("article", "note-msg note-msg-mine" + (note.waiting ? " note-msg-waiting" : ""));
    var who = el("p", "note-msg-who");
    who.appendChild(el("span", "note-msg-name", "Edvard"));
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
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
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
    feed.textContent = "";
    if (!notes.length) {
      feed.appendChild(el("p", "empty", "No notes yet. Type below and tap Note."));
      moveCaptureInto(feed);
      return;
    }
    if (notesShown > notes.length) notesShown = notes.length;
    var window_ = notes.slice(notes.length - notesShown);
    var thread = el("div", "note-thread");
    if (notesShown < notes.length) {
      /* The scroll-up handle. A button as well as a scroll trigger, on
       * purpose: an IntersectionObserver that fires on its own is the
       * lazy load he asked for, and a tappable control is what still
       * works when it does not -- the same belt-and-braces the board
       * pager uses. */
      var older = el("button", "more note-older", "Load older notes");
      older.type = "button";
      older.addEventListener("click", showOlderNotes);
      thread.appendChild(older);
      watchForOlderNotes(older);
    } else {
      thread.appendChild(el("p", "note-start", "The beginning of our notes."));
    }
    window_.forEach(function (note) {
      renderNoteMessage(note).forEach(function (node) { thread.appendChild(node); });
    });
    feed.appendChild(thread);
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

  /* The Questions page.
   *
   * the owner, ideas.md 2026-08-19: "Make a questions page in Nova where i can
   * ask questions in a box and a Claude sonnet model answers me."
   *
   * The answer is not synchronous -- the question goes into an Agora
   * conversation and a Sonnet persona answers it on the runner's next poll
   * tick (`nova_ask` says why that is the whole mechanism). So this page
   * has one job the other pages do not: show that something is coming, and
   * keep looking until it arrives, without a refresh.
   *
   * `waiting` comes from the server rather than being re-derived here from
   * the last sender. The rule for "is an answer owed" lives in
   * `turns.decide_turn`, and a second copy of it in this file would be the
   * duplication this loop keeps filing against itself.
   */
  var ASK_POLL_MS = 4000;
  // Roughly four minutes. A CLI turn that has not answered by then has
  // failed rather than being slow, and a page that polls forever is a
  // phone battery with a question mark on it.
  var ASK_POLL_MAX = 60;

  /* How close to the bottom still counts as being at the bottom.
   *
   * Not an exact comparison, and the slop is not caution: the scroll offset
   * is fractional on a phone at a non-integer zoom while `scrollHeight` and
   * `clientHeight` are rounded integers, so `scrollHeight - clientHeight ===
   * scrollTop` is false on a thread that is visually at the bottom, and the
   * thread would then stop following the answer he is waiting for. 64px is
   * about one message line and its gap -- near enough that the newest
   * message is on his screen, far enough that one deliberate flick upwards
   * is not read as staying put.
   *
   * Module scope because both threads need it and they measure different
   * things: the dock scrolls inside its own panel, the `/conversation/<id>`
   * page scrolls the document. Same number, one definition. */
  var STICK_SLOP_PX = 64;

  /* The `/conversation/<id>` page's version of the dock's `atBottom`.
   *
   * `.ask-thread` carries no `overflow` or height in `style.css`, so on this
   * page it is not a scroll container at all -- the document is. Measuring
   * `thread.scrollTop` here would read 0 forever and answer "at the bottom"
   * for every position, which is why this is a separate pair of functions
   * rather than the dock's reused. */
  function pageScrollTop() {
    return window.pageYOffset || document.documentElement.scrollTop || 0;
  }

  function pageAtBottom() {
    var viewport = window.innerHeight || document.documentElement.clientHeight || 0;
    return document.documentElement.scrollHeight - viewport - pageScrollTop() <= STICK_SLOP_PX;
  }

  function scrollPageToBottom() {
    window.scrollTo(0, document.documentElement.scrollHeight);
  }

  /* "He is reading this here, so do not buzz his phone about it."
   *
   * His capture, `ideas.md` 2026-08-25: *"The new chat is just a wrapper
   * around agora ... now when i use the new chat i get alerted by agora
   * whenever a new message arrives. This is not a huge problem, but its not
   * high quality of a product."*
   *
   * This thread is an Agora conversation, and Agora's service worker already
   * refuses to notify while its own app is visible — `clients.matchAll()`
   * cannot see a tab on this origin, so it thought nobody was looking. This
   * says so out loud, and Agora withholds the push for 30 seconds per ping.
   *
   * Deliberately not a timer of its own. It rides the poll that is already
   * running, which is the exact window an answer can land in: nothing polls
   * unless an answer is owed, and nothing is pushed unless one arrives. A
   * separate interval would vouch for him during the hours he is asleep with
   * the tab open, which is when he most wants the phone to ring.
   *
   * `document.hidden` is one guard and it is not enough on its own, which my
   * reviewer caught: the dock keeps polling after he closes it, on purpose, so
   * the dot on the launcher can light. A closed panel on a visible tab is not
   * a thread on his screen, and vouching there would silence the push while
   * the only signal left is a dot nobody is looking at. So the caller passes
   * whether the thread is actually showing, and both have to be true.
   *
   * Fire-and-forget on the wire only. A failure here is not harmless in the
   * other direction — a stale or wrong vouch drops a notification he wanted —
   * which is why the guards are on this side and the TTL on Agora's is two and
   * a half polls rather than a round thirty seconds. There is no way to revoke
   * a ping, so the TTL is also the window a locked phone keeps vouching for. */
  function pingAskWatching(onScreen) {
    if (!onScreen || document.hidden) return;
    fetch("/api/ask/watching", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    }).catch(function () {});
  }

  /* The same vouch, for a thread that is not the ask thread.
   *
   * `pingAskWatching` above resolves its conversation server-side by tag, so
   * it can only ever speak for one thread. The dock grew a switcher and now
   * paints any conversation, and the poll tick therefore vouched only while
   * the ask thread was showing -- correctly, because vouching for the wrong
   * id drops a notification he wanted. The cost of that correctness is that
   * his complaint stayed live for every other thread: read a heartbeat's
   * conversation here and the other app still buzzes about a message already
   * on his screen. Naming the id is what fixes it.
   *
   * Same two guards, deliberately duplicated rather than shared: `onScreen`
   * is the caller's answer to "is this thread actually showing", which is
   * narrower than "the panel is polling", and `document.hidden` sends a
   * backgrounded phone back to being notified. */
  function pingConvWatching(onScreen, conversationId) {
    if (!onScreen || !conversationId || document.hidden) return;
    fetch("/api/conversations/watching", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: conversationId }),
    }).catch(function () {});
  }

  /* The body goes through `appendRichText` rather than straight into a text
   * node, so a picture he attached is a picture here instead of the literal
   * `![shot.png](/api/upload/…)` he would otherwise read back. That is the
   * same one reader the comment threads and the boards already use, which
   * is the point: an attachment must not look like a thumbnail in one place
   * and a URL in another.
   *
   * `.ask-text` keeps `white-space: pre-wrap`, and both that and
   * `overflow-wrap` inherit into the paragraphs, so a plain answer wraps
   * exactly as it did before. */
  /* `message.partial` is a passage written on the way to the answer rather
   * than the answer (issue #129) -- the server keeps them now, so a turn
   * that takes four minutes shows its paragraphs as they are written
   * instead of nothing at all. Drawn as an ordinary bubble on purpose: it
   * IS what was said, just not the last thing that will be. The class only
   * softens it, so the finished reply still reads as the finished reply. */
  /* Copy one message's text to the clipboard.
   *
   * Two paths on purpose, and neither is defensive padding. `navigator.clipboard`
   * exists only in a secure context, so it is there when he opens the site over
   * https on the tailnet and absent when anything reaches nova-site over plain
   * http inside the cluster -- and absent in jsdom, which is where the tests
   * below run. The textarea path is the one that has to work when the modern
   * API is missing, so it is the one under test; a button that silently does
   * nothing is worse than no button.
   *
   * What gets copied is `message.text` -- the source he or I actually wrote,
   * not the rendered node. `appendRichText` turns a picture into an `<img>`
   * and a mermaid block into a diagram, so reading the DOM back would hand him
   * a paste with the code fences and the image link gone. */
  function copyToClipboard(text) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(text);
    }
    var scratch = document.createElement("textarea");
    scratch.value = text;
    scratch.setAttribute("readonly", "readonly");
    scratch.style.position = "fixed";
    scratch.style.left = "-9999px";
    document.body.appendChild(scratch);
    scratch.select();
    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (err) {
      ok = false;
    }
    document.body.removeChild(scratch);
    return ok ? Promise.resolve() : Promise.reject(new Error("copy failed"));
  }

  /* The copy button on a message bubble.
   *
   * Issue #143 asks for six controls; this is the one that needs nothing from
   * the server, so it is the one that ships whole. Every chat surface on this
   * site renders through `askMessage` -- the dock, a conversation thread and
   * the journal card's ask -- so one button here appears on all three.
   *
   * It says what happened rather than assuming: "Copied" on success, "Press
   * ctrl+C" on failure, both reverting after a moment. A clipboard write can
   * be refused by permission policy and there is no way to ask first. */
  function askCopyButton(text) {
    var button = el("button", "ask-copy", "Copy");
    button.type = "button";
    button.title = "Copy this message";
    button.addEventListener("click", function () {
      copyToClipboard(text).then(function () {
        button.textContent = "Copied";
      }, function () {
        button.textContent = "Press ctrl+C";
      }).then(function () {
        setTimeout(function () { button.textContent = "Copy"; }, 1600);
      });
    });
    return button;
  }

  /* --- The work behind an answer, as one line and a drawer ---------------
   *
   * His capture, `issues.md` 2026-09-01: *"The streaming of the thoughts in
   * a conversation show up as multiple bubbles and i do not like that. It
   * would be better to replecate Claude mobile app patter where the thoughts
   * and tools are compressed to one clickable line that opens a modal drawer
   * from the bottom that can be dragged upwards, but contains the thoughts as
   * text, but if tools have been used it is showed as a list of clickable
   * lines and when clicked, the "input" and "output" of the tool is shown.
   * ... Please research on how Claude mobile does this and try to replecate
   * it exactly."*
   *
   * He sent four screenshots and they are the specification, so this follows
   * them rather than my own idea of the same thing:
   *
   * - the collapsed line is dim, inline in the flow above the prose it
   *   preceded, and carries a `›` -- it is not a bubble and not a button
   *   that looks like one;
   * - the sheet rises from the bottom with a grab handle, a centred title
   *   and an `✕` on the left;
   * - tapping a tool row pushes a second view *inside the same sheet* --
   *   back arrow, the tool's name, its status under it, then `Inputs` and
   *   `Output`. His WebFetch screenshot is exactly that.
   *
   * Everything in `steps` comes from the server (`nova_conversations`), and
   * the one thing it does not carry is a tool's output: capped at 20,000
   * characters each, forty to a window, so they are fetched one at a time
   * when he opens that row. */

  /* What the collapsed line says.
   *
   * Claude mobile names the tool when there is one thing to name ("Used
   * ToolSearch") and generalises when there is not ("Browsed the web").
   * Same rule: name it when the block ran exactly one distinct capability,
   * count otherwise, and fall back to the thinking when no tool ran at all.
   *
   * The count is of steps, not of the two halves of each call -- the server
   * already folded those. */
  function stepsLabel(steps) {
    var names = [];
    var thoughts = 0;
    steps.forEach(function (step) {
      if (step.kind === "tool") {
        if (names.indexOf(step.capability) === -1) names.push(step.capability);
      } else {
        thoughts += 1;
      }
    });
    var tools = steps.length - thoughts;
    if (!tools) return thoughts === 1 ? "Thought about it" : "Thought it through";
    if (names.length === 1) return "Used " + names[0];
    return "Used " + tools + " tools";
  }

  /* The one sheet, appended to <body> and reused, the way `.prio-menu` is.
   * A sheet per message would be a hundred hidden dialogs in a long thread,
   * and only one can ever be open. */
  var stepSheet = null;
  var stepSheetBackdrop = null;
  var stepSheetBody = null;
  var stepSheetTitle = null;
  var stepSheetSub = null;
  var stepSheetBack = null;
  var stepSheetClose = null;

  function buildStepSheet() {
    if (stepSheet) return stepSheet;
    stepSheetBackdrop = el("div", "step-backdrop");
    stepSheetBackdrop.hidden = true;
    stepSheetBackdrop.addEventListener("click", closeStepSheet);
    document.body.appendChild(stepSheetBackdrop);

    stepSheet = el("div", "step-sheet");
    stepSheet.hidden = true;
    stepSheet.setAttribute("role", "dialog");
    stepSheet.setAttribute("aria-modal", "true");

    /* The grab handle. It is a real control, not decoration: he asked for a
     * drawer "that can be dragged upwards", and `dragStepSheet` below is
     * what does it. */
    var grip = el("div", "step-grip");
    grip.setAttribute("aria-hidden", "true");
    stepSheet.appendChild(grip);

    var head = el("div", "step-head");
    stepSheetBack = el("button", "step-back", "‹");
    stepSheetBack.type = "button";
    stepSheetBack.title = "Back to the list";
    stepSheetBack.setAttribute("aria-label", "Back to the list");
    stepSheetBack.hidden = true;
    head.appendChild(stepSheetBack);
    stepSheetClose = el("button", "step-close", "✕");
    stepSheetClose.type = "button";
    stepSheetClose.title = "Close";
    stepSheetClose.setAttribute("aria-label", "Close");
    stepSheetClose.addEventListener("click", closeStepSheet);
    head.appendChild(stepSheetClose);
    var titles = el("div", "step-titles");
    stepSheetTitle = el("h2", "step-title", "");
    titles.appendChild(stepSheetTitle);
    stepSheetSub = el("div", "step-sub", "");
    stepSheetSub.hidden = true;
    titles.appendChild(stepSheetSub);
    head.appendChild(titles);
    stepSheet.appendChild(head);

    stepSheetBody = el("div", "step-body");
    stepSheet.appendChild(stepSheetBody);
    document.body.appendChild(stepSheet);
    dragStepSheet(grip);
    return stepSheet;
  }

  /* Drag the sheet taller.
   *
   * Pointer events rather than touch events: one code path covers his phone
   * and a mouse, and jsdom can drive it. The height is a percentage of the
   * viewport written onto the node, so nothing here has to know the CSS.
   *
   * Bounded at both ends deliberately. A sheet dragged past the top has
   * nowhere further to go and one dragged to nothing leaves a strip he
   * cannot grab again -- and a floor-only clamp is the shape that let a
   * previous cycle's mutation raise a cap to 8GiB with every test green. */
  // Where the sheet opens: enough to read a few steps without covering
  // the message it belongs to, which is the height his screenshot shows.
  var STEP_SHEET_OPEN_VH = 55;
  var STEP_SHEET_MIN_VH = 25;
  var STEP_SHEET_MAX_VH = 92;
  function dragStepSheet(grip) {
    var from = 0;
    var startVh = 0;
    function move(e) {
      var vh = window.innerHeight || 1;
      var next = startVh + ((from - e.clientY) / vh) * 100;
      setStepSheetHeight(next);
    }
    function end() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
    }
    grip.addEventListener("pointerdown", function (e) {
      from = e.clientY;
      startVh = currentStepSheetHeight();
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end);
      if (e.preventDefault) e.preventDefault();
    });
  }

  function currentStepSheetHeight() {
    var raw = parseFloat((stepSheet && stepSheet.style.height) || "");
    return isNaN(raw) ? STEP_SHEET_OPEN_VH : raw;
  }

  function setStepSheetHeight(vh) {
    var clamped = Math.max(STEP_SHEET_MIN_VH, Math.min(STEP_SHEET_MAX_VH, vh));
    stepSheet.style.height = clamped + "vh";
    return clamped;
  }


  function closeStepSheet() {
    if (!stepSheet || stepSheet.hidden) return;
    stepSheet.hidden = true;
    stepSheetBackdrop.hidden = true;
    document.removeEventListener("keydown", onStepSheetKey, true);
  }

  function onStepSheetKey(e) {
    if (e.key !== "Escape") return;
    // Escape from the detail view goes back to the list rather than closing
    // the sheet, which is what the back arrow beside it does -- one gesture,
    // two ways to make it.
    if (!stepSheetBack.hidden) stepSheetBack.click();
    else closeStepSheet();
  }

  /* One row in the drawer.
   *
   * A thought is text and is not clickable: it is the whole of what there is
   * to say, and a tap target that opens nothing is worse than none. A tool is
   * a button, because there is an input and an output behind it. */
  function stepRow(step, onOpen) {
    if (step.kind !== "tool") {
      var passage = el("div", "step-thought");
      appendRichText(passage, null, step.text || "");
      return passage;
    }
    var row = el("button", "step-tool");
    row.type = "button";
    row.appendChild(el("span", "step-tool-name", step.capability || "tool"));
    if (step.input) row.appendChild(el("span", "step-tool-arg", step.input));
    if (step.status && step.status !== "done") {
      row.appendChild(el("span", "step-tool-state step-tool-" + step.status,
        step.status === "failed" ? "failed" : "running"));
    }
    row.addEventListener("click", function () { onOpen(step); });
    return row;
  }

  /* The detail view: his WebFetch screenshot.
   *
   * The output is fetched here rather than carried in the thread, so this is
   * the one place in the drawer that can fail. It says which of the two
   * things went wrong -- a call the thread no longer holds (404) against a
   * fetch that did not land -- because "no output" and "I could not ask" mean
   * different things and only one of them is worth retrying. */
  function showStepDetail(conversationId, step, limit) {
    stepSheetBack.hidden = false;
    stepSheetTitle.textContent = step.capability || "tool";
    stepSheetSub.hidden = false;
    stepSheetSub.textContent = step.status === "failed" ? "Failed"
      : step.status === "running" ? "Running" : "Completed";
    stepSheetBody.textContent = "";
    var pending = el("p", "step-loading", "Loading…");
    stepSheetBody.appendChild(pending);

    function section(label, text) {
      stepSheetBody.appendChild(el("div", "step-section-label", label));
      stepSheetBody.appendChild(el("pre", "step-pre", text));
    }

    if (!step.id) {
      // A call Agora never gave a `toolUseId`. There is nothing to ask for,
      // and the arguments are already here.
      stepSheetBody.textContent = "";
      section("Inputs", step.input || "");
      stepSheetBody.appendChild(
        el("p", "step-note", "This call has no id, so its output cannot be fetched."));
      return;
    }
    /* `fetch` rather than `fetchPage`, and the status is why. A call Agora
     * no longer holds is a 404 and a site that did not answer is anything
     * else; `fetchPage` turns both into one rejected promise carrying a
     * message, and telling them apart by that string would be this file
     * agreeing with `nova_site.py` about a sentence. The status is the
     * contract. */
    function failed(note) {
      if (stepSheetBack.hidden) return;  // he went back while it was in flight
      stepSheetBody.textContent = "";
      section("Inputs", step.input || "");
      stepSheetBody.appendChild(el("p", "step-note", note));
    }
    /* `limit` is the window the thread rows came from, echoed back by the
     * server. He can page back through a long thread, so a step on screen
     * may be older than the default window -- asking without it reports a
     * call he is looking at as gone. */
    fetch("/api/conversations/step?id=" + encodeURIComponent(conversationId)
      + "&tool=" + encodeURIComponent(step.id)
      + (limit ? "&limit=" + encodeURIComponent(limit) : "")).then(function (r) {
      if (r.status === 404) {
        failed("This call is no longer in the thread, so its output is gone.");
        return null;
      }
      if (!r.ok) {
        failed("Could not reach the server for the output.");
        return null;
      }
      return r.json();
    }).then(function (found) {
      if (!found) return;
      if (stepSheetBack.hidden) return;
      stepSheetBody.textContent = "";
      section("Inputs", found.input || step.input || "");
      if (found.status === "running") {
        /* An empty Output block over a call still running reads as "this
         * printed nothing", which is a different and wrong fact. The sheet
         * does not follow the four-second poll -- it draws the steps it was
         * opened with -- so this says what is true and what to do about it
         * rather than pretending to be live. */
        stepSheetBody.appendChild(el("p", "step-note",
          "Still running. Close and open this again for the output."));
        return;
      }
      section("Output", found.output || "");
    }, function () {
      failed("Could not reach the server for the output.");
    });
  }

  function showStepList(conversationId, steps, label, limit) {
    stepSheetBack.hidden = true;
    stepSheetSub.hidden = true;
    stepSheetTitle.textContent = label;
    stepSheetBody.textContent = "";
    steps.forEach(function (step) {
      stepSheetBody.appendChild(stepRow(step, function (chosen) {
        showStepDetail(conversationId, chosen, limit);
      }));
    });
  }

  function openStepSheet(conversationId, steps, label, limit) {
    buildStepSheet();
    stepSheetBack.onclick = function () {
      showStepList(conversationId, steps, label, limit);
    };
    showStepList(conversationId, steps, label, limit);
    setStepSheetHeight(STEP_SHEET_OPEN_VH);
    stepSheetBackdrop.hidden = false;
    stepSheet.hidden = false;
    document.addEventListener("keydown", onStepSheetKey, true);
    stepSheetClose.focus();
  }

  /* The collapsed line itself. Returns null when there is nothing behind it,
   * so a message with no steps is unchanged. */
  function stepsLine(conversationId, steps, limit) {
    if (!steps || !steps.length) return null;
    var label = stepsLabel(steps);
    var line = el("button", "ask-steps");
    line.type = "button";
    line.appendChild(el("span", "ask-steps-label", label));
    line.appendChild(el("span", "ask-steps-chev", "›"));
    line.setAttribute("aria-label", label + " — open the details");
    line.addEventListener("click", function () {
      openStepSheet(conversationId, steps, label, limit);
    });
    return line;
  }

  /* `conversationId` is what the drawer's detail view asks the server with.
   * It is threaded through rather than read off a module variable because
   * three surfaces render this -- the dock, a conversation thread and the
   * journal card's ask -- and only one of them has a module variable to
   * read. */
  function askMessage(message, conversationId, limit) {
    var steps = stepsLine(conversationId, message.steps, limit);
    /* A row that is only the work behind an answer still being written.
     * It is not a bubble: nothing has been said yet, and drawing it as one
     * is the thing he asked me to stop doing. */
    if (message.stepsOnly) {
      var working = el("div", "ask-msg-steps");
      if (steps) working.appendChild(steps);
      return working;
    }
    var mine = message.sender === "Edvard";
    var row = el("div", "ask-msg " + (mine ? "ask-mine" : "ask-theirs")
      + (message.partial ? " ask-partial" : ""));
    row.appendChild(el("div", "ask-who", mine ? "You" : message.sender || "Nova Answers"));
    /* Above the prose, the way Claude mobile puts it above the paragraph the
     * tool call led to -- and the way it happened. */
    if (steps) row.appendChild(steps);
    var body = el("div", "ask-text");
    appendRichText(body, null, message.text);
    row.appendChild(body);
    /* No button on a message with nothing to copy -- an attachment-only line
     * has an empty `text`, and a Copy that yields an empty clipboard reads as
     * broken rather than as empty. */
    if (message.text) row.appendChild(askCopyButton(message.text));
    return row;
  }

  /* The pending bubble, which was the word "Thinking…" and nothing else.
   *
   * His capture, `issues.md` 2026-08-30 12:56: *"I asked Nova for a status
   * report, but it just says thinking for a long time. I need feedback. What
   * is it doing? Did it even recieve my messages? What tools does it use? We
   * have some of this in Agora, but not in Nova. I have no idea if it broke
   * or if its working, so i might wait forover for no response."*
   *
   * Three separate questions and the bubble now answers all three: the
   * elapsed time says it is alive, the tool line says what it is doing, and
   * the fallback line says the question landed even before the first tool
   * call. `progress` comes from `/api/ask` and is only ever present while the
   * turn is running (nova_ask.thread).
   *
   * The clock advances on the poll rather than on a timer of its own -- one
   * `setInterval` here would be a second thing `stopPolling` has to know
   * about, and the poll is every four seconds, so it moves visibly anyway.
   * The submit handler passes null: it paints the bubble before the first
   * poll, when the page genuinely knows nothing yet. */
  function askElapsed(askedAt) {
    if (!askedAt) return "";
    var started = Date.parse(askedAt);
    if (isNaN(started)) return "";
    var secs = Math.max(0, Math.round((Date.now() - started) / 1000));
    if (secs < 60) return secs + "s";
    return Math.floor(secs / 60) + "m " + (secs % 60) + "s";
  }

  function askPending(progress) {
    var row = el("div", "ask-msg ask-theirs ask-pending");
    var elapsed = askElapsed(progress && progress.askedAt);
    row.appendChild(el("div", "ask-pending-head",
      elapsed ? "Thinking… · " + elapsed : "Thinking…"));
    var latest = progress && progress.latest;
    if (latest) {
      var step = el("div", "ask-pending-step");
      step.appendChild(el("span", "ask-pending-tool", latest.capability));
      if (latest.detail) step.appendChild(el("span", "ask-pending-detail", latest.detail));
      row.appendChild(step);
      if (progress.steps > 1) {
        row.appendChild(el("div", "ask-pending-count", progress.steps + " steps so far"));
      }
    } else {
      row.appendChild(el("div", "ask-pending-step", "Your question is in. No tool calls yet."));
    }
    return row;
  }

  function renderAskThread(container, payload) {
    container.textContent = "";
    var messages = payload.messages || [];
    if (!messages.length) {
      container.appendChild(el("p", "empty", "Ask me anything. I answer here, in a minute or so."));
      return;
    }
    messages.forEach(function (message) {
      container.appendChild(askMessage(message, payload.conversationId, payload.limit));
    });
    if (payload.waiting) container.appendChild(askPending(payload.progress));
  }

  /* The Conversations page.
   *
   * His capture, `ideas.md` 2026-08-25: *"its basicly a chat app with
   * multiple conversations history and i can start new ones etc."* The
   * chat dock in the corner talks to one thread; this is every thread.
   *
   * Two views on one route rather than two routes, and `convOpenId` is
   * what says which. A conversation id in the URL would be a nicer link,
   * but it would also mean `PAGE_ROUTE_PREFIXES` grows a second member and
   * the server starts pattern-matching a uuid -- and nothing links at a
   * single thread yet, so that is machinery with no reader. If something
   * ever does link at one, this is the place it changes.
   */
  var convOpenId = null;
  var convOpenName = "";

  function renderConvThread(container, payload) {
    container.textContent = "";
    var messages = payload.messages || [];
    if (!messages.length) {
      container.appendChild(el("p", "empty", "Nothing said here yet."));
      return;
    }
    messages.forEach(function (message) {
      container.appendChild(askMessage(message, payload.conversationId, payload.limit));
    });
    if (payload.waiting) container.appendChild(el("div", "ask-msg ask-theirs ask-pending", "Thinking…"));
  }

  /* Poll one conversation for an answer that is still being written.
   *
   * The route guard lives in the `.then` and nowhere else. An earlier
   * version checked in the timer body too, and a mutation pass showed the
   * pair could not both be tested: removing either one alone left the
   * other covering it, so both mutations passed and the navigation test
   * pinned nothing. The `.then` is the copy that has to stay -- it is what
   * catches a fetch still in flight when the owner taps another tab.
   *
   * The extra guard here is `convOpenId` --
   * he can tap back to the list and open a different thread without the
   * route changing, and a poll from the old thread must not paint over
   * the new one. */
  function pollConv(container, conversationId, attempts) {
    if (attempts >= ASK_POLL_MAX) return;
    livePolls.push(setTimeout(function () {
      fetchPage("/api/conversations/thread?id=" + encodeURIComponent(conversationId))
        .then(function (payload) {
          if (route(window.location.pathname).view !== "conversations") return;
          if (convOpenId !== conversationId) return;
          /* Both measurements are taken **before** the repaint and neither
           * can be taken after it: `renderConvThread` empties the container,
           * which collapses the document, and the browser clamps the scroll
           * offset to the shorter page. So by the time the messages are back
           * he is somewhere near the top and there is nothing left to read.
           *
           * His capture, `issues.md` #140: *"Chat auto-scrolls to the bottom
           * every time a new message arrives even if I've scrolled up to
           * reread something."* Cycle 568 fixed that in the chat dock and
           * this page was never touched, where it is worse rather than the
           * same -- the dock jumped him to the newest message, this threw
           * him to the oldest one. */
          var follow = pageAtBottom();
          var was = pageScrollTop();
          renderConvThread(container, payload);
          if (follow) scrollPageToBottom();
          else window.scrollTo(0, was);
          if (payload.waiting) pollConv(container, conversationId, attempts + 1);
        })
        .catch(function () { pollConv(container, conversationId, attempts + 1); });
    }, ASK_POLL_MS));
  }

  function openConversation(id, name) {
    convOpenId = id;
    convOpenName = name || "";
    stopPolling();
    markNav();
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    statusEl.appendChild(el("p", "status-line", convOpenName));
    feed.textContent = "";

    var back = el("button", "conv-back", "← Beats");
    back.setAttribute("type", "button");
    back.addEventListener("click", function () {
      convOpenId = null;
      // The URL moves too, or a reload from here re-opens the thread he
      // just backed out of. Beats rather than a listing: the Chats page is
      // deleted (his capture, *"Delete the chats page entirely -- never use
      // it"*), and the heartbeat cards are the only place left in this app
      // that lists threads. `history.back()` is not the answer -- half the
      // ways in here are a cold load from a push notification, where there
      // is no back entry to return to.
      history.pushState(null, "", "/heartbeats");
      load();
    });
    feed.appendChild(back);

    var thread = el("div", "ask-thread");
    var form = el("form", "ask-form");
    var box = el("textarea", "ask-box");
    box.setAttribute("rows", "3");
    box.setAttribute("placeholder", "Say something…");
    box.setAttribute("aria-label", "Your message");
    var send = el("button", "ask-send", "Send");
    send.setAttribute("type", "submit");
    var status = el("p", "ask-status");
    var uploading = false;
    var sending = false;
    function syncSend() { send.disabled = uploading || sending; }
    var attach = buildAttach({
      onBusy: function (isBusy) { uploading = isBusy; syncSend(); },
      onStatus: function (text) { status.textContent = text; },
    });
    form.appendChild(box);
    form.appendChild(attach.tray);
    form.appendChild(attach.input);
    form.appendChild(attach.button);
    form.appendChild(send);
    form.appendChild(status);
    feed.appendChild(form);
    feed.appendChild(thread);

    thread.appendChild(el("p", "empty", "loading…"));
    fetchPage("/api/conversations/thread?id=" + encodeURIComponent(id))
      .then(function (payload) {
        if (convOpenId !== id) return;
        renderConvThread(thread, payload);
        /* The composer is above the thread on this page, so the newest
         * message is at the very bottom of the document. Half the ways in
         * here are a tap on a push notification about that message, and
         * without this the page opens on the oldest one instead. It is also
         * what makes the poll's `follow` branch reachable at all: land at
         * the top and every later answer is correctly held there. */
        scrollPageToBottom();
        if (payload.waiting) pollConv(thread, id, 0);
      })
      .catch(function (err) {
        if (convOpenId !== id) return;
        thread.textContent = "";
        thread.appendChild(el("p", "empty", "Could not load this conversation: " + err));
      });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = box.value.trim();
      if (!text && !attach.count()) return;
      var body = [text, attach.markdown()].filter(Boolean).join("\n\n");
      sending = true;
      syncSend();
      status.textContent = "sending…";
      fetch("/api/conversations/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: id, text: body }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          box.value = "";
          attach.clear();
          status.textContent = "";
          sending = false;
          syncSend();
          // Paint it immediately: the send is the one moment the page knows
          // something happened, and four seconds of a box that has gone
          // blank reads as a lost message.
          thread.appendChild(askMessage({ sender: "Edvard", text: body }));
          thread.appendChild(el("div", "ask-msg ask-theirs ask-pending", "Thinking…"));
          // Sending is him asking to be at the bottom, whatever he was
          // rereading a second ago -- the same call the dock makes, and
          // without it his own message lands below the fold of a long
          // thread and the answer to it lands further below that.
          scrollPageToBottom();
          pollConv(thread, id, 0);
        })
        .catch(function (err) {
          sending = false;
          syncSend();
          status.textContent = "could not send: " + err.message;
        });
    });
  }

  /* The thread's heading, filled in after its messages rather than before.
   *
   * Guarded on `convOpenId` and not on the URL because he can back out and
   * open a different thread while the listing is still in flight, and a late
   * answer must not relabel the one he is looking at now. `convOpenId` is
   * set synchronously by `openConversation`, so it is already correct by the
   * time any of these promises resolve.
   */
  function setConvName(id, name) {
    if (convOpenId !== id) return;
    convOpenName = name;
    var line = statusEl.querySelector(".status-line");
    if (line) line.textContent = name;
  }

  /* Open one thread straight from a URL, with no listing tapped first.
   *
   * The thread endpoint answers with messages and no name, and the name is
   * the line above them, so it is resolved from the listing. That lookup
   * used to be *in front of* the thread fetch -- `openConversation` was
   * called from inside the listing's `.then` -- so the message he tapped a
   * notification to read waited on a whole extra round trip for one string.
   *
   * Measured 2026-09-01 against the live site from inside the cluster, so
   * with no tailnet leg in the numbers: `/api/conversations` answered in
   * 0.62s, 0.84s and 1.19s over three reads, while `/`, `/app.js` and
   * `/style.css` each answered in 3-6ms and the thread itself in 0.17-0.40s.
   * On the one path a push notification opens, the name lookup was the
   * largest single thing on the critical path and every millisecond of it
   * was spent before the thread fetch was allowed to start.
   *
   * So both go out together and the heading fills in behind the messages.
   * A failed lookup still opens the thread, which was already true here and
   * is now true by construction rather than by a `catch` branch: a missing
   * label is worth far less than the message.
   */
  function openConversationById(id) {
    openConversation(id, "");
    fetchPage("/api/conversations")
      .then(function (payload) {
        var rows = payload.conversations || [];
        for (var i = 0; i < rows.length; i++) {
          if (rows[i].id === id) return setConvName(id, rows[i].name || "");
        }
      })
      .catch(function () { /* the thread is already on screen */ });
  }

  /* The Heartbeats page.
   *
   * His capture: *"Then Agora can just be purely for heartbeats."* Cycle 441
   * built the Chats half of that; this is the other half, and after it there
   * is nothing on Agora's own app he opens day to day.
   *
   * Read plus two writes. Switching one off and pressing run are the things
   * he does from his phone; creating, deleting and editing a schedule are
   * not, and they are the four that can quietly take this loop off the air,
   * so they stay on Agora's page -- see `nova_heartbeats.py`.
   */
  /* Which conversation drawers he has opened, by heartbeat id.
   *
   * `renderHeartbeats` wipes the feed and rebuilds every card, and it runs on
   * a poll -- every 30s idle, every 4s while a run is queued. A `details`
   * rebuilt from scratch is closed, so without this the drawer he just opened
   * snaps shut under his thumb within four seconds of pressing "Run now".
   * Reviewer caught it. Same shape as `boardState.open` on the goals board.
   */
  var hbOpenThreads = {};

  function hbStateLine(row) {
    if (row.running) return { text: "Running now", cls: "hb-state hb-state-running" };
    if (!row.enabled) return { text: "Off", cls: "hb-state hb-state-off" };
    if (row.forceRun) return { text: "On · run queued", cls: "hb-state hb-state-running" };
    return { text: "On", cls: "hb-state hb-state-on" };
  }

  /* Both writes answer with `{ok, message}` and neither returns the changed
   * row, so the page re-lists rather than patching what it drew. A local
   * flip would show "Off" for a PATCH that never landed. */
  function hbPost(path, body, button) {
    button.disabled = true;
    fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json().catch(function () { return {}; }); })
      .then(function (result) {
        if (!result.ok) window.alert(result.message || result.error || "that did not work");
        loadHeartbeats();
      })
      .catch(function (err) {
        button.disabled = false;
        window.alert("could not reach Nova: " + err);
      });
  }

  /* This page draws things that change without him touching anything -- a
   * run starting, a run finishing, `lastResult` going from nothing to an
   * answer -- and until now it drew them exactly once. Press "Run now" and
   * the row reads "On · run queued" until he reloads the app. My reviewer
   * found that on runner#387's own diff and I filed it instead of fixing
   * it; it has been the top line of the handoff for two cycles since.
   *
   * `pollConv`'s shape, with the reschedule at the end of the render rather
   * than in a loop of its own, so exactly one timer is ever alive: every
   * path that repaints this page goes through `renderHeartbeats`, whose own
   * `stopPolling()` clears the pending one first. Leaving the page clears it
   * too, for free -- that is what `stopPolling` is for and every other view
   * calls it on the way in.
   *
   * Two cadences, both of them numbers already in this file: `ASK_POLL_MS`
   * for the seconds after he presses "Run now", and `POLL_MS` otherwise,
   * which is the journal feed's idle rate.
   *
   * The fast one keys on `forceRun` alone, and my first version had
   * `running || forceRun` until I looked at what the live endpoint actually
   * answers. `running` is `lastResult === "running"`, and the Nova row is
   * running for roughly eighteen of every twenty minutes -- so keying on it
   * makes the fast rate the permanent rate, ~900 requests an hour per open
   * tab against Agora's list API, for a row that changes twice. `forceRun`
   * is the flag Agora sets between his tap and the runner picking the run
   * up: it is his own action, it lasts seconds, and it is the only state on
   * this page he is standing there watching. Once a run is under way the
   * next thing that changes is minutes off and 30 seconds is not late.
   *
   * The polling itself never stops while the page is open -- `pollConv` has
   * an attempt cap because it waits for a single answer and is finished when
   * it arrives, and this waits for nothing in particular. **The fast phase**
   * is capped, and that number is measured rather than cautious. `run_now`'s
   * own docstring in `nova_heartbeats.py` says pressing it during a cycle
   * means the run happens when that cycle ends, "which can be most of an
   * hour later" -- so `forceRun` is not the few-seconds flag I first took it
   * for, and `/api/heartbeats` is uncached and makes two upstream Agora
   * calls per request. An uncapped fast phase is therefore up to ~1,800
   * upstream calls an hour from one open tab, for a row that changes once.
   * After `ASK_POLL_MAX` fast ticks -- four minutes, the same bound the Ask
   * page uses -- it drops to the idle rate and stays there. Four minutes is
   * long enough to see a press get picked up; an hour is a leak.
   */
  var hbFastTicks = 0;

  function scheduleHeartbeatsPoll(rows) {
    var queued = rows.some(function (r) { return r.forceRun; });
    var fast = queued && hbFastTicks < ASK_POLL_MAX;
    // Reset only when nothing is queued -- not merely when this tick came out
    // slow. My own first version reset on the slow tick, which meant the
    // counter went 60 fast, one slow, 60 fast, for ever: a cap that read as a
    // cap and bounded nothing. The test named for the four minutes caught it.
    hbFastTicks = queued ? hbFastTicks + 1 : 0;
    livePolls.push(setTimeout(loadHeartbeats, fast ? ASK_POLL_MS : POLL_MS));
  }

  function renderHeartbeats(payload) {
    stopPolling();
    markNav();
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    var rows = payload.heartbeats || [];
    var on = rows.filter(function (r) { return r.enabled; }).length;
    statusEl.appendChild(el("p", "status-line",
      rows.length + (rows.length === 1 ? " heartbeat" : " heartbeats") + " · " + on + " on"));
    feed.textContent = "";
    // Before the empty-list return, not after it: a first heartbeat created
    // from Agora's side should appear here without a reload as well.
    scheduleHeartbeatsPoll(rows);

    if (!rows.length) {
      feed.appendChild(el("p", "empty", "No heartbeats yet."));
      return;
    }

    var list = el("div", "hb-list");
    rows.forEach(function (row) {
      var card = el("div", "hb-row" + (row.enabled ? "" : " hb-off"));
      card.appendChild(el("div", "hb-name", row.name));
      var meta = [row.personaName, row.schedule].filter(Boolean).join(" · ");
      if (meta) card.appendChild(el("div", "hb-meta", meta));

      var state = hbStateLine(row);
      card.appendChild(el("div", state.cls, state.text));

      // `fmtStamp` takes milliseconds; an unparseable timestamp yields NaN,
      // which renders as "Invalid Date", so the line is dropped instead.
      // A heartbeat that has never run says so rather than saying nothing:
      // a missing line and "never ran" look identical and are not.
      var when = row.lastRunAt ? Date.parse(row.lastRunAt) : NaN;
      var lastLine = isNaN(when) ? "Never run" : "Last run " + fmtStamp(when);
      if (row.lastResult && !isNaN(when)) lastLine += " · " + row.lastResult;
      card.appendChild(el("div", "hb-when", lastLine));

      var actions = el("div", "hb-actions");
      var toggle = el("button", "hb-btn", row.enabled ? "Turn off" : "Turn on");
      toggle.setAttribute("type", "button");
      toggle.addEventListener("click", function () {
        hbPost("/api/heartbeats/enabled",
          { heartbeatId: row.id, enabled: !row.enabled }, toggle);
      });
      actions.appendChild(toggle);

      var run = el("button", "hb-btn", "Run now");
      run.setAttribute("type", "button");
      run.addEventListener("click", function () {
        hbPost("/api/heartbeats/run", { heartbeatId: row.id }, run);
      });
      actions.appendChild(run);

      if (row.conversationId) {
        var open = el("button", "hb-btn", "Open thread");
        open.setAttribute("type", "button");
        open.addEventListener("click", function () {
          // The URL has to move first. `openConversation` renders into the
          // same feed either way, but its poller guards on
          // `route(location.pathname).view === "conversations"`, so opened
          // from `/heartbeats` the thread would paint once and never
          // refresh. `/conversations` was that URL until the Chats page was
          // deleted; `/conversation/<id>` is the one that survives a reload.
          history.pushState(null, "", "/conversation/" + encodeURIComponent(row.conversationId));
          openConversation(row.conversationId, row.name);
        });
        actions.appendChild(open);
      }
      card.appendChild(actions);

      // His capture: *"The heartbeat conversations should rather somehow be
      // listed in the beats page as they belong there. Somehow underneath
      // their relative heartbeat and as a dropdown drawer so they are not
      // shown unless i want to see them."* Closed by default, same `details`
      // shape as the Task fold above it, and the count is in the summary so
      // he can see there are twelve without opening it.
      var threads = row.conversations || [];
      if (threads.length) {
        var box = el("details", "hb-threads");
        if (hbOpenThreads[row.id]) box.setAttribute("open", "");
        box.addEventListener("toggle", function () {
          hbOpenThreads[row.id] = box.open;
        });
        box.appendChild(el("summary", "",
          threads.length + (threads.length === 1 ? " conversation" : " conversations")));
        var tlist = el("div", "hb-thread-list");
        threads.forEach(function (conv) {
          var btn = el("button", "hb-thread", "");
          btn.setAttribute("type", "button");
          // A thread `_with_current` synthesised carries no name -- Agora has
          // one, this page has not fetched it. "Current thread" is the label
          // for the row and deliberately not the name passed to
          // `openConversation` below, which falls back to the heartbeat's own
          // name: "Current thread" as a page header says nothing about which
          // heartbeat it belongs to, which is the one thing this fixes.
          btn.appendChild(el("span", "hb-thread-name", conv.name || "Current thread"));
          var tw = conv.updatedAt ? Date.parse(conv.updatedAt) : NaN;
          if (!isNaN(tw)) btn.appendChild(el("span", "hb-thread-when", fmtStamp(tw)));
          btn.addEventListener("click", function () {
            // Same reason as "Open thread" above: the poller guards on the
            // URL saying `conversations`, so the URL moves before the render.
            history.pushState(null, "", "/conversation/" + encodeURIComponent(conv.id));
            openConversation(conv.id, conv.name || row.name);
          });
          tlist.appendChild(btn);
        });
        box.appendChild(tlist);
        card.appendChild(box);
      }

      if (row.task) {
        var task = el("details", "hb-task");
        var summary = el("summary", "", "Task");
        task.appendChild(summary);
        task.appendChild(el("pre", "", row.task));
        card.appendChild(task);
      }
      list.appendChild(card);
    });
    feed.appendChild(list);
  }

  function loadHeartbeats() {
    fetchPage("/api/heartbeats")
      .then(function (payload) {
        if (route(window.location.pathname).view !== "heartbeats") return;
        renderHeartbeats(payload);
      })
      .catch(function (err) {
        // The route guard belongs on this path too, and it belongs here now
        // rather than as a tidy-up: before this page polled, a failed fetch
        // could only be the one he triggered by opening it. Now a timer can
        // have a request in flight when he taps away, and without the guard
        // its failure paints "Could not load your heartbeats" over whatever
        // page he actually opened.
        if (route(window.location.pathname).view !== "heartbeats") return;
        // `stopPolling()` first, exactly as `renderHeartbeats` does, and this
        // line is the reviewer's finding on this PR rather than symmetry for
        // its own sake. `loadHeartbeats` is also called from `hbPost`, and
        // `button.disabled` guards only the button that was tapped -- so
        // "Turn off" on one row and "Run now" on another put two requests in
        // flight. If the success lands first it schedules a timer; a failure
        // landing after it used to append a second one without clearing the
        // first, and only a success ever clears. Two timers, then three.
        stopPolling();
        markNav();
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load your heartbeats: " + err));
        // And keep trying. Without this, one dropped request on a phone
        // leaves the page frozen for good, which is the bug this cycle is
        // fixing wearing a different hat.
        scheduleHeartbeatsPoll([]);
      });
  }

  /* The service catalog -- every workload running in the cluster, what
   * ordered it, and whether it is up.
   *
   * Step 2 of the IDP roadmap. `tools.catalog` reads the cluster and
   * writes `nova/catalog.md`; until this page existed the only way to
   * read it was to open Obsidian on a laptop. Nothing here talks to
   * Kubernetes: the server parses that one vault document, for the
   * reason written at the top of `nova_catalog.py`.
   *
   * No polling. The file changes when a cycle runs the tool, which is
   * once an hour at the most, so a timer here would be 900 requests a
   * day to re-read a document that had not moved -- and the page says
   * out loud when it was last regenerated, which is the honest answer to
   * "is this current" rather than a refresh that hides the question.
   */
  function catalogCard(row) {
    var card = el("div", "cat-row" + (row.status === "off" ? " cat-dim" : ""));
    var head = el("div", "cat-head");
    if (row.url) {
      var link = el("a", "cat-name", row.name);
      link.href = row.url;
      link.rel = "noopener";
      head.appendChild(link);
    } else {
      head.appendChild(el("span", "cat-name", row.name));
    }
    // The word, never a bare colour -- his ask about the priority symbols,
    // and "off" and "down" are the pair that most needs it: one is a
    // deliberate scale-to-zero and the other is something broken.
    var label = row.status === "up" ? "Up"
      : row.status === "off" ? "Off on purpose"
      : row.status === "down" ? "DOWN" : "Unknown";
    head.appendChild(el("span", "cat-state cat-state-" + row.status, label));
    card.appendChild(head);
    card.appendChild(el("div", "cat-meta", row.host || row.namespace));
    var ordered = row.claim
      ? "Source repo ordered by " + row.claim
      : "Nothing here was ordered by a claim";
    card.appendChild(el("div", "cat-meta", ordered));
    if (row.deployedBy) {
      card.appendChild(el("div", "cat-meta", "Deployed by " + row.deployedBy));
    }
    // The join that makes this a catalog rather than an inventory: the row
    // says what a thing is, where it runs, and where it is written up. A
    // service with no page says so rather than showing nothing, because the
    // gap is the useful half -- it is what somebody would go and write.
    if (row.docs) {
      var doc = el("a", "cat-meta cat-docs", "Read the docs");
      doc.href = row.docs;
      doc.rel = "noopener";
      card.appendChild(doc);
    } else if (row.docs === null) {
      card.appendChild(el("div", "cat-meta cat-nodocs", "No docs page"));
    }
    return card;
  }

  function renderCatalog(payload) {
    stopPolling();
    markNav();
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    var rows = payload.services || [];
    var parts = [rows.length + (rows.length === 1 ? " service" : " services")];
    if (payload.down) parts.push(payload.down + " down");
    if (payload.off) parts.push(payload.off + " off on purpose");
    statusEl.appendChild(el("p", "status-line", parts.join(" · ")));
    feed.textContent = "";

    if (payload.missing) {
      feed.appendChild(el("p", "empty",
        "No catalog has been written yet. A cycle builds it with tools.catalog."));
      return;
    }

    if (payload.headline) {
      var lead = el("div", "cat-headline" + (payload.incomplete ? " cat-warn" : ""));
      lead.appendChild(el("p", "cat-lead", payload.headline));
      if (payload.detail) lead.appendChild(el("p", "cat-detail", payload.detail));
      (payload.unreadable || []).forEach(function (u) {
        lead.appendChild(el("p", "cat-detail", u));
      });
      if (payload.docsHeadline) {
        lead.appendChild(el("p", "cat-lead", payload.docsHeadline));
        if (payload.docsDetail) lead.appendChild(el("p", "cat-detail", payload.docsDetail));
      }
      feed.appendChild(lead);
    }

    // Grouped by namespace, in the order the file lists them, because the
    // tool already sorts by namespace and re-sorting here would be a
    // second opinion about an order that has one owner.
    var list = el("div", "cat-list");
    var current = null;
    rows.forEach(function (row) {
      if (row.namespace !== current) {
        current = row.namespace;
        list.appendChild(el("h2", "cat-ns", current));
      }
      list.appendChild(catalogCard(row));
    });
    feed.appendChild(list);

    if ((payload.doors || []).length) {
      feed.appendChild(el("h2", "cat-ns", "Doors nothing in the catalog accounts for"));
      var doors = el("div", "cat-list");
      payload.doors.forEach(function (door) {
        doors.appendChild(el("div", "cat-row cat-dim", door));
      });
      feed.appendChild(doors);
    }

    var when = payload.regenerated
      ? "Read from the cluster " + payload.regenerated
        + (payload.cycle ? " by cycle " + payload.cycle : "")
      : "This catalog carries no timestamp.";
    feed.appendChild(el("p", "cat-when", when));
  }

  function loadCatalog() {
    fetchPage("/api/catalog")
      .then(function (payload) {
        if (route(window.location.pathname).view !== "catalog") return;
        renderCatalog(payload);
      })
      .catch(function (err) {
        // The same route guard as the heartbeats page, for the same
        // reason: a fetch in flight when he taps away must not paint its
        // failure over the page he actually opened.
        if (route(window.location.pathname).view !== "catalog") return;
        stopPolling();
        markNav();
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the catalog: " + err));
      });
  }

  /* `/diag` -- what the owner's own device reports about itself.
   *
   * Three cycles running have now shipped a fix for a rendering fault on a
   * phone none of them could look at. Cycle 299 attributed a missing
   * hamburger to an iPhone notch and shipped `env(safe-area-inset-top)`;
   * the cycle after it shipped `translateZ(0)` on a Chromium
   * compositor-bug theory; and then a capture landed saying he is on an
   * Android Galaxy S25 and a Windows desktop, so the first of those
   * targeted a platform he does not own. None of that was careless -- the
   * loop simply has no instrument pointed at his hardware. Cycle 303 drove
   * headless Chromium over the live site at six widths from 320 to 412 CSS
   * px with S25 metrics and a Samsung user agent: the button held
   * [x, 26, 40, 40] at every one of them and the priority popup centred
   * inside the viewport at every one of them. Reproducing nothing is the
   * measurement, and it says the variable is on his device, not in a width.
   *
   * So this page is guess number four's replacement rather than guess
   * number four. It fetches nothing -- every value on it is read from the
   * browser that is drawing it -- and `Send this to Nova` files the lot as
   * one note, which is a file step 1a already opens on every wake. The
   * one-line join is not cosmetic: `nova_capture.clean_capture_text` turns
   * each newline into its own bullet, so a multi-line report would land as
   * fourteen separate notes. */

  /** Resolved `env(safe-area-inset-*)`, in `top right bottom left` order.
   *
   * Read off a throwaway fixed element rather than `--shell-top`, because a
   * custom property computes to its unresolved token (`max(1.6rem, ...)`)
   * in every engine -- the padding it feeds is the only place the number
   * exists.
   *
   * The support test is `CSS.supports` rather than a look at the numbers,
   * and the reviewer is why. Reading the probe alone cannot answer the
   * question this row exists for: an engine that does not understand
   * `env()` drops the declaration and leaves the initial `0px`, which is
   * byte-identical to an engine that understands it perfectly and has no
   * notch to report. So "0px 0px 0px 0px" would have been printed in both
   * cases, and the case that matters -- the one that would explain Cycle
   * 299's fix doing nothing on his phone -- was unreachable. That is a
   * negative result guaranteed in advance, on the page built to stop
   * exactly that. */
  function safeAreaInsets() {
    var supported = !!(window.CSS && window.CSS.supports
      && window.CSS.supports("padding-top", "env(safe-area-inset-top, 0px)"));
    if (!supported) return "env() unsupported by this browser";
    var probe = document.createElement("div");
    probe.style.position = "fixed";
    probe.style.visibility = "hidden";
    probe.style.pointerEvents = "none";
    probe.style.top = "0";
    probe.style.left = "0";
    probe.style.paddingTop = "env(safe-area-inset-top, 0px)";
    probe.style.paddingRight = "env(safe-area-inset-right, 0px)";
    probe.style.paddingBottom = "env(safe-area-inset-bottom, 0px)";
    probe.style.paddingLeft = "env(safe-area-inset-left, 0px)";
    document.body.appendChild(probe);
    var cs = window.getComputedStyle(probe);
    var sides = [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft];
    document.body.removeChild(probe);
    if (sides.some(function (v) { return !v; })) return "unsupported";
    return sides.join(" ");
  }

  /** The hamburger's live box and every property that could hide it.
   *
   * Sampled twice -- once on paint and once three seconds later -- because
   * what he reported is "I see it 1 sec when I open or refresh the app and
   * then it vanishes", and a single reading cannot tell a button that was
   * never drawn from one that was drawn and then lost. */
  function menuBtnReport() {
    var node = document.getElementById("menu-btn");
    if (!node) return "not in the DOM";
    var box = node.getBoundingClientRect();
    var cs = window.getComputedStyle(node);
    return "at " + Math.round(box.left) + "," + Math.round(box.top)
      + " sized " + Math.round(box.width) + "x" + Math.round(box.height)
      + ", visibility " + cs.visibility
      + ", opacity " + cs.opacity
      + ", display " + cs.display
      + ", z-index " + cs.zIndex
      + ", transform " + cs.transform;
  }

  /** A node's live box, judged against the viewport it is drawn in.
   *
   * `menuBtnReport` above answers "is the button there"; this answers "is
   * this thing where it should be", which is the other half of the S25
   * report and the half no instrument in this loop has ever measured. It
   * names the overflow per edge rather than printing a bare rect, because
   * "out of place" is a direction and a distance, and a rect leaves the
   * reader to subtract.
   *
   * The visual-viewport clause is the reading I would actually bet on.
   * Both dropdowns are `position: fixed`, which pins them to the *layout*
   * viewport — so a pinch-zoom, or Android's keyboard, or a URL bar that
   * has not settled, moves what he sees without moving anything CSS knows
   * about. Headless Chromium has a visual viewport identical to its
   * layout viewport at every width, which is exactly why Cycle 303 could
   * drive six widths and reproduce nothing. */
  function boxReport(node) {
    if (!node) return "not in the DOM";
    var box = node.getBoundingClientRect();
    var cs = window.getComputedStyle(node);
    var vw = document.documentElement.clientWidth;
    var vh = document.documentElement.clientHeight;
    var over = [];
    if (box.left < -0.5) over.push("left by " + Math.round(-box.left));
    if (box.top < -0.5) over.push("top by " + Math.round(-box.top));
    if (box.right > vw + 0.5) over.push("right by " + Math.round(box.right - vw));
    if (box.bottom > vh + 0.5) over.push("bottom by " + Math.round(box.bottom - vh));
    var out = "at " + Math.round(box.left) + "," + Math.round(box.top)
      + " sized " + Math.round(box.width) + "x" + Math.round(box.height)
      + " in a " + vw + "x" + vh + " viewport"
      + ", display " + cs.display
      + ", visibility " + cs.visibility
      + ", transform " + cs.transform
      + (over.length ? ", OUTSIDE VIEWPORT: " + over.join(" and ") : ", fully inside");
    var vv = window.visualViewport;
    if (vv) {
      // What he is looking at, versus what CSS positioned. Equal on every
      // desktop and on headless Chromium; the gap is the whole point.
      var seenX = Math.round((box.left + box.width / 2) - (vv.offsetLeft + vv.width / 2));
      var seenY = Math.round((box.top + box.height / 2) - (vv.offsetTop + vv.height / 2));
      out += ", centre offset from visual viewport " + seenX + "," + seenY
        + " (scale " + vv.scale + ")";
    }
    return out;
  }

  /** Open the drawer, measure it once the slide has finished, put it back.
   *
   * Measuring it closed would report the parked box — `translateX(100%)`,
   * off the right edge — which reads as a spectacular fault every time
   * and is simply the drawer being shut. So this opens the real element
   * rather than a copy, waits out the 220ms transform transition with a
   * margin, and restores the exact state it found. The brief slide is
   * visible and the lede says so; a measurement he cannot see happening
   * is not obviously better than one he can. */
  function measureDrawer(write) {
    var was = menuOpen();
    setMenu(true);
    window.setTimeout(function () {
      /* `finally`, because this one mutates app-wide singletons rather
       * than a node the next navigation throws away. A throw between the
       * open and the restore leaves the drawer out, the scrim dimming the
       * whole app and `body.nav-open` holding the scroll lock, on every
       * page, until he reloads -- a page-wide lockup caused by the
       * diagnostic page, which is a strictly worse outcome than the
       * missing reading. Reviewer's finding.
       *
       * The state is reported beside the box, not assumed from the label.
       * Every number here is meaningless if the drawer was shut when it
       * was taken, and nothing else in the line would say so -- the parked
       * box is a perfectly ordinary-looking rect off the right edge. This
       * is the same discipline as the `CSS.supports` check on the
       * safe-area row: a reading that cannot distinguish its own
       * precondition is not evidence. */
      try {
        write(boxReport(navEl) + ", drawer was "
          + (menuOpen() ? "open" : "SHUT — this is the parked box, not the drawn one"));
      } finally {
        setMenu(was);
      }
    }, 350);
  }

  /** Same, for the centred priority popup.
   *
   * Populated with the real options before measuring: an empty `.prio-menu`
   * is a 17px-tall box, and its height against `max-height: 70vh` is one of
   * the few ways this thing could genuinely land wrong. The overlay is
   * shared with every picker on the page, so this hands it back emptied and
   * hidden — `openMenu` rebuilds the list from scratch on every open, so a
   * cleared overlay is the state it already expects. */
  function measurePrioMenu(write) {
    var menu = getPrioMenuOverlay();
    /* If a real picker got there first, leave it alone and say so.
     *
     * The capture box sits on this page too, so he can tap its priority
     * button inside the ~650ms before this runs. Without this guard the
     * measurement would empty the overlay under a picker he had just
     * opened, repopulate it with dead options carrying no click handlers,
     * then hide the whole thing -- his popup vanishing on its own, the
     * trigger still reading `aria-expanded="true"`, and no way to tell
     * from the note that it happened. A skipped reading he can retake by
     * reloading is worth more than a reading taken by breaking the page
     * under him. */
    if (!menu.hidden) {
      write("skipped — a priority picker was already open; reload the page to measure it");
      return;
    }
    menu.textContent = "";
    PRIORITIES.forEach(function (label) {
      var item = el("button", "prio-option", label || "– Unrated");
      item.type = "button";
      item.setAttribute("role", "option");
      menu.appendChild(item);
    });
    menu.hidden = false;
    try {
      write(boxReport(menu) + ", popup was " + (menu.hidden ? "HIDDEN — not the drawn box" : "open")
        + ", " + menu.children.length + " options");
    } finally {
      // Same reason as the drawer: a throw here would strand this overlay
      // centred over every page of the app until he reloads.
      menu.hidden = true;
      menu.textContent = "";
    }
  }

  function displayMode() {
    if (!window.matchMedia) return "matchMedia unsupported";
    var modes = ["standalone", "fullscreen", "minimal-ui", "browser"];
    for (var i = 0; i < modes.length; i += 1) {
      if (window.matchMedia("(display-mode: " + modes[i] + ")").matches) return modes[i];
    }
    return "unknown";
  }

  /** Every reading, as `[label, value]` pairs. One place, so the table on
   * screen and the note that gets sent can never drift apart. */
  function diagRows() {
    var doc = document.documentElement;
    var docStyle = window.getComputedStyle(doc);
    var header = document.getElementById("status");
    var vv = window.visualViewport;
    return [
      ["User agent", navigator.userAgent],
      ["Display mode", displayMode()],
      ["Window", window.innerWidth + " x " + window.innerHeight + " CSS px"],
      ["Device pixel ratio", String(window.devicePixelRatio)],
      ["Screen", (window.screen ? window.screen.width + " x " + window.screen.height : "unknown")
        + (window.screen && window.screen.orientation ? ", " + window.screen.orientation.type : "")],
      ["Visual viewport", vv
        ? Math.round(vv.width) + " x " + Math.round(vv.height)
          + ", offset top " + Math.round(vv.offsetTop) + ", scale " + vv.scale
        : "unsupported"],
      ["Safe-area insets", safeAreaInsets() + " (top right bottom left)"],
      ["Header top padding", header ? window.getComputedStyle(header).paddingTop : "no header"],
      ["Root font size", docStyle.fontSize],
      ["Colour scheme", window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark" : "light"],
      ["Hamburger, on paint", menuBtnReport()],
    ];
  }

  function renderDiag() {
    stopPolling();
    markNav();
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));
    statusEl.appendChild(el("p", "status-line", "What this device reports about itself"));
    feed.textContent = "";

    var card = el("div", "plan-card");
    card.appendChild(el("p", "diag-lede",
      "Nothing on this page comes from the server — every line is measured in the browser "
      + "showing it. If the app looks wrong on your phone, open this page there and tap Send. "
      + "The next cycle then reads what your device actually did, instead of guessing at it. "
      + "The side menu will slide in and out once on its own — that is this page measuring "
      + "where your phone actually puts it."));

    var list = el("dl", "diag-list");
    var rows = diagRows();
    rows.forEach(function (row) {
      list.appendChild(el("dt", "diag-key", row[0]));
      list.appendChild(el("dd", "diag-value", row[1]));
    });
    /* Two readings that cannot be taken yet, so they are rows now and
     * values later. The width one is the reviewer's catch and it was the
     * serious finding on this diff: `scrollWidth > clientWidth` was being
     * read inside `diagRows()`, while `feed` was still empty -- so it
     * measured the shell and never the 120-character monospace user-agent
     * string that is the one thing on this page capable of pushing it
     * wider. It would have reported "no sideways scroll" while scrolling
     * sideways, which is this page introducing the exact class of fault it
     * exists to diagnose, and reporting itself clean while doing it. */
    var extras = [
      ["Page vs viewport width", el("dd", "diag-value", "measuring…")],
      ["Hamburger, 3s later", el("dd", "diag-value", "measuring…")],
      /* The two he actually reported and nothing has ever measured. His
       * capture says "the dropdowns are out of place on his S25"; the
       * device report he sent on 2026-08-21 closed the hamburger half of
       * that — 40x40 at 304,26, visible, no sideways scroll — and carried
       * not one number about either dropdown, because this page did not
       * ask for any. Three blind fixes were shipped before the page
       * existed and the page then measured the symptom that was already
       * fine. */
      ["Menu drawer, opened", el("dd", "diag-value", "measuring…")],
      ["Priority popup, opened", el("dd", "diag-value", "measuring…")],
    ];
    extras.forEach(function (extra) {
      list.appendChild(el("dt", "diag-key", extra[0]));
      list.appendChild(extra[1]);
    });
    card.appendChild(list);

    var status = el("p", "capture-status");
    status.setAttribute("role", "status");
    var send = el("button", "capture-btn", "Send this to Nova");
    send.type = "button";
    send.id = "diag-send";
    send.addEventListener("click", function () {
      // Labels come from the same two lists the table was built from, so
      // what is sent and what is on screen cannot use different words for
      // the same reading.
      var parts = rows.map(function (row) { return row[0] + ": " + row[1]; })
        .concat(extras.map(function (extra) { return extra[0] + ": " + extra[1].textContent; }));
      send.disabled = true;
      status.textContent = "sending…";
      status.className = "capture-status";
      fetch("/api/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target: "notes",
          text: "[device report] " + parts.join(" | "),
          priority: "",
        }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) {
            throw new Error((result && (result.message || result.error)) || "failed");
          }
          status.textContent = "sent — the next cycle reads it as a note";
        })
        .catch(function (err) {
          status.textContent = String(err.message || err);
          status.className = "capture-status is-error";
          send.disabled = false;
        });
    });

    var actions = el("div", "diag-actions");
    actions.appendChild(status);
    actions.appendChild(send);
    card.appendChild(actions);
    feed.appendChild(card);

    /* Only now is there a page to measure. Reading `scrollWidth` forces
     * layout, so this is a real measurement of the document as rendered
     * rather than of an empty feed -- no frame to wait for. */
    var doc = document.documentElement;
    var wide = doc.scrollWidth > doc.clientWidth;
    extras[0][1].textContent = doc.scrollWidth + " vs " + doc.clientWidth
      + (wide ? " — SCROLLS SIDEWAYS" : " — no sideways scroll");

    // Writes into a node the next navigation will have discarded, which is
    // harmless -- the alternative is a timer to cancel and a handle to
    // carry, for a value nobody reads once the page is gone.
    window.setTimeout(function () { extras[1][1].textContent = menuBtnReport(); }, 3000);

    /* Both dropdowns, in sequence rather than at once, and both finished
     * well before the 3s hamburger sample above -- opening the drawer puts
     * `.open` on the button, so an overlapping measurement would report a
     * hamburger in a state he never put it in. */
    measureDrawer(function (value) {
      extras[2][1].textContent = value;
      /* After the drawer's 220ms slide back out, so the two are never on
       * screen together. The first version of this comment said the pause
       * was to get `body.nav-open`'s `overflow: hidden` out of the way,
       * and the reviewer was right that it is not: `setMenu` drops that
       * class synchronously, and only the CSS transform is delayed. */
      window.setTimeout(function () {
        measurePrioMenu(function (v) { extras[3][1].textContent = v; });
      }, 300);
    });
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
    /* The journal view gets its comments from `fetchAll` and paints the badge
     * out of `renderStatus`. Every other view has to ask for them, or the
     * badge he asked for in the header is a journal-page feature wearing a
     * header's clothes. */
    if (here.view !== "journal") refreshMail();
    /* Every internal link on this site is a `pushState`, not a page load
     * (see the delegated click handler at the bottom of this file), so a
     * `#changed` line painted on the journal would otherwise still be
     * sitting over the Issues board after one tap. `render` repaints it on
     * the way back in. */
    hideChanged();
    if (here.view === "board") {
      loadBoard(here.board);
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
      openConversationById(here.conversationId);
      return;
    }
    if (here.view === "heartbeats") {
      loadHeartbeats();
      return;
    }
    if (here.view === "catalog") {
      loadCatalog();
      return;
    }
    // No `loadDiag` -- this is the one view with no payload behind it, so
    // there is nothing to fetch and nothing that can fail on the way.
    if (here.view === "diag") {
      renderDiag();
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

  document.addEventListener("visibilitychange", function () { resume(true); });
  window.addEventListener("pageshow", function (event) {
    if (event && event.persisted) resume(true);
  });
  window.addEventListener("focus", function () { resume(false); });
  window.addEventListener("online", function () { resume(false); });

  /* The capture box (item 6). One button per target rather than a target
   * toggle plus a submit: it is one tap fewer on a phone, which is the
   * whole point of the feature, and it is why a third target cost one
   * button rather than a redesign. The text is only cleared once the
   * server confirms the write -- a failed capture that wiped the box would
   * lose the thought it exists to catch.
   *
   * Nothing here names the targets: `send` takes whatever `data-target`
   * the button carries and the server rejects anything not in
   * CAPTURE_TARGETS, so the Note button (the owner, issues.md 2026-08-12)
   * needed no change in this file at all. */
  (function captureBox() {
    var form = document.getElementById("capture-form");
    if (!form) return;
    var textEl = document.getElementById("capture-text");
    var captureStatus = document.getElementById("capture-status");
    var buttons = Array.prototype.slice.call(form.querySelectorAll(".capture-btn"));

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
      ariaLabel: "Priority",
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
    document.querySelector(".capture-submit").appendChild(prioPicker.el);

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
    var captureAttach = buildAttach({
      // All three destinations, not just one: whichever he taps mid-upload
      // files the text without the image. Same race as the comment drawer.
      onBusy: function (isBusy) {
        buttons.forEach(function (b) { b.disabled = isBusy; });
      },
      onStatus: setStatus,
    });
    var submitRow = document.querySelector(".capture-submit");
    form.appendChild(captureAttach.input);
    // Directly under the box he typed in, above the row of destinations --
    // the thumbnails belong to the sentence, not to the buttons.
    textEl.parentNode.insertBefore(captureAttach.tray, textEl.nextSibling);
    submitRow.insertBefore(captureAttach.button, prioPicker.el);


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

    function send(target) {
      var text = textEl.value.trim();
      // A screenshot with no sentence under it is still a capture worth
      // filing -- see the same guard in the journal drawer's `submit`.
      if (!text && !captureAttach.count()) {
        textEl.focus();
        return;
      }
      var body = [text, captureAttach.markdown()].filter(Boolean).join("\n\n");
      buttons.forEach(function (b) { b.disabled = true; });
      setStatus("saving…", false);
      fetch("/api/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target: target,
          text: body,
          priority: prioPicker.getValue(),
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
        .then(function () { buttons.forEach(function (b) { b.disabled = false; }); });
    }

    buttons.forEach(function (button) {
      button.addEventListener("click", function () { send(button.getAttribute("data-target")); });
    });
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
        send(buttons.length ? buttons[0].getAttribute("data-target") : "issues");
      }
    });
    textEl.addEventListener("input", fit);
    textEl.addEventListener("input", fitOne);
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

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").then(subscribeToPush).catch(function () {});
  }

  /* The chat dock -- the owner's capture on `ideas.md`, 2026-08-25, rated
   * High: *"A great idea is to have a chat-bot that covers in the bottom
   * right of my page in Nova that i can talk to about anything. Not sure if
   * it should be the same as a commentator on a journal or just a long
   * running session. I see this as maybe a replacement for 'merge' agora
   * into Nova and also the 'ask' page as this gives me a quick way to ask
   * questions or discuss ideas and implementations."*
   *
   * It is the `/ask` thread, reachable from every page instead of only from
   * one tab. Same endpoint, same Agora conversation, same answers -- so a
   * question typed here appears on `/ask` and the other way round, and
   * nothing had to be added to the server at all. `/ask` stays where it is:
   * he said *maybe* a replacement, and deleting a page on a maybe is the
   * one direction that is not one sentence to reverse.
   *
   * The one thing this may not do is put its timer in `livePolls`.
   * `stopPolling()` runs on every navigation and clears that array, so a
   * dock polling out of it goes silent the moment he taps a tab while
   * waiting -- which is the exact minute the answer lands in. It keeps a
   * single handle of its own and clears it before every reschedule, so
   * there is never more than one dock poller alive.
   */
  (function () {
    var btn = document.getElementById("chat-btn");
    var dock = document.getElementById("chat-dock");
    if (!btn || !dock) return;
    var closeBtn = document.getElementById("chat-close");
    var thread = document.getElementById("chat-thread");
    var form = document.getElementById("chat-form");
    var box = document.getElementById("chat-box");
    var send = document.getElementById("chat-send");
    var status = document.getElementById("chat-status");
    var dot = document.getElementById("chat-dot");

    var pollHandle = null;
    var loaded = false;
    var lastCount = 0;
    var isOpen = false;
    /* One-shot: the *next* paint goes to the newest message whatever the
     * scroll position says, and the paint that uses it clears it. Opening the
     * dock and sending a question both set it, because in both of those he
     * has just asked to be at the bottom and the position on screen has not
     * caught up yet. Every paint after that decides from where he actually
     * is. It must not latch -- a version of this that stayed true once set
     * followed the thread down forever, which is the bug it exists to fix. */
    var stickToBottom = true;

    /* Scrolling back through a thread.
     *
     * His capture, `issues.md` 2026-08-31, with a screenshot of this dock:
     * *"I can only see the latest messages in the chat. I can't scroll
     * upwards and see the earlier messages."* Both endpoints answer with the
     * newest `MAX_THREAD` messages and there was no way to ask for the ones
     * before them, so a thread longer than a page had a hard floor -- 79 of
     * the 720 conversations in the store are longer than that.
     *
     * The page grows rather than paging in chunks he has to stitch: reaching
     * the top asks for one `PAGE_STEP` more of the same thread, so the
     * payload is always "the newest N" and nothing has to merge two fetches.
     * `hasMore` comes from the server and is the only thing that stops it.
     * `pendingAnchor` is the scroll height measured before an older page is
     * painted -- the difference after it is exactly how far down the message
     * he was reading has moved. */
    var PAGE_STEP = 40;
    var pageLimit = PAGE_STEP;
    var hasMore = false;
    var loadingOlder = false;
    var pendingAnchor = null;

    var menuBtn = document.getElementById("chat-menu");
    var listEl = document.getElementById("chat-list");
    var titleEl = document.getElementById("chat-title");

    /* Which thread the dock is showing.
     *
     * His capture, `ideas.md` 2026-08-26: *"Add multi-conversation support
     * to the chat modal: hamburger button top-left opens a list of previous
     * conversations, switchable, modal remembers last-opened conversation.
     * Once done, delete the /ask page entirely as dead code."* The dock
     * talked to exactly one thread -- the `/ask` conversation -- and the
     * only place every other thread was reachable was the Conversations
     * page, which is the page he wants to stop needing.
     *
     * Two shapes behind one panel. `kind: "ask"` is `/api/ask`, which has
     * no id because the server finds its one tagged conversation itself;
     * `kind: "conv"` is `/api/conversations/thread?id=`. The two payloads
     * are already identical -- `{messages, waiting}` -- so only the URLs
     * differ, and nothing had to be added to the server.
     *
     * `sourceToken` is `convOpenId`'s job on the Conversations page: a
     * fetch for the thread he just left must not paint over the one he
     * just opened. A counter rather than an id because "ask" has none. */
    var source = { kind: "ask", id: null, name: "Ask Nova" };
    var sourceToken = 0;
    var recalled = false;

    /* Same store and the same trade as the read marks at the top of this
     * file: per-device, because the server has no session and no idea which
     * browser is his. A browser with storage disabled simply opens on the
     * ask thread every time, which is where the dock opened before this. */
    var CHAT_SOURCE_KEY = "nova.chatSource.v1";

    function rememberSource() {
      var store = localStore();
      if (!store) return;
      try {
        store.setItem(CHAT_SOURCE_KEY, JSON.stringify(source));
      } catch (err) { /* full or disabled: the dock still works */ }
    }

    function recallSource() {
      var store = localStore();
      if (!store) return;
      try {
        var raw = store.getItem(CHAT_SOURCE_KEY);
        if (!raw) return;
        var parsed = JSON.parse(raw);
        // A conversation needs an id to be fetchable at all, so a stored
        // `conv` without one is corrupt and reads as never-stored rather
        // than as a thread that 404s on every open.
        if (!parsed || parsed.kind !== "conv" || !parsed.id) return;
        source = { kind: "conv", id: parsed.id, name: parsed.name || "Conversation",
                   untitled: parsed.untitled === true };
      } catch (err) { /* unreadable: stay on the ask thread */ }
    }

    function threadUrl() {
      if (source.kind === "conv") {
        return "/api/conversations/thread?id=" + encodeURIComponent(source.id)
          + "&limit=" + pageLimit;
      }
      return "/api/ask?limit=" + pageLimit;
    }

    /* The composer grows with what he types. His capture, issues.md
     * 2026-08-25: *"make the input field start at one line, then when the
     * content is two lines it gets tall enough to fit two lines and
     * dynamicly scales up to 10 lines tall which is the cap."*
     *
     * Measure rather than assume: `line-height` computes to a pixel value
     * in every browser that has one set, and this one is set in the
     * stylesheet, but `getComputedStyle` returns the string "normal" when
     * it is not -- so a missing stylesheet must not make the cap NaN and
     * blank the box. `1.4 * font-size` is the same ratio the rule uses.
     *
     * The height is set from `scrollHeight`, which under `box-sizing:
     * border-box` measures content plus padding and never the border, so
     * the two border widths are added back. Setting `height` to "auto"
     * first is what lets the box *shrink* again when he deletes a line:
     * `scrollHeight` can never report less than the current height. */
    var CHAT_BOX_MAX_ROWS = 10;

    function growChatBox() {
      if (!box) return;
      var css = window.getComputedStyle(box);
      // Only a px value is a line height I can multiply. A browser resolves
      // `line-height: 1.4` to px here, but the keyword `normal` and a bare
      // ratio both come back unresolved in some engines, and `parseFloat`
      // would read "1.4" as 1.4 pixels and cap the box at fourteen.
      var line = /px$/.test(css.lineHeight) ? parseFloat(css.lineHeight) : NaN;
      if (!(line > 0)) line = parseFloat(css.fontSize) * 1.4;
      if (!(line > 0)) return;
      var frame =
        (parseFloat(css.paddingTop) || 0) +
        (parseFloat(css.paddingBottom) || 0) +
        (parseFloat(css.borderTopWidth) || 0) +
        (parseFloat(css.borderBottomWidth) || 0);
      var cap = line * CHAT_BOX_MAX_ROWS + frame;
      box.style.height = "auto";
      var wanted = box.scrollHeight +
        (parseFloat(css.borderTopWidth) || 0) +
        (parseFloat(css.borderBottomWidth) || 0);
      var next = Math.min(wanted, cap);
      box.style.height = next + "px";
      // Past the cap it has to scroll, and only past the cap -- a gutter on
      // a one-line box is the thing `overflow-y: hidden` is avoiding.
      box.style.overflowY = wanted > cap ? "auto" : "hidden";
    }
    /* The paperclip, his capture on `issues.md` 2026-08-25: *"Make the new
     * chat be able to display mermaid charts, images and also be able to
     * upload files like all other input fields in the Nova app."* This is
     * the same `buildAttach` the comment drawer, the board composer and the
     * capture box already use -- a tray of chips under the box rather than
     * markdown typed into it -- so there is still exactly one thing on this
     * site that knows how to upload a file.
     *
     * Send is disabled from two independent directions -- an upload in
     * flight and a question in flight -- and they overlap. Two writers of
     * `send.disabled` would race: an upload finishing while the question is
     * still out re-enables the button, and the next tap sends the same text
     * twice. So both set a flag and one function reads them. */
    var uploading = false;
    var sending = false;

    function syncSend() { send.disabled = uploading || sending; }

    var attach = buildAttach({
      onBusy: function (isBusy) { uploading = isBusy; syncSend(); },
      onStatus: function (text) { status.textContent = text; },
    });
    box.parentNode.insertBefore(attach.tray, box.nextSibling);
    form.appendChild(attach.input);
    send.parentNode.insertBefore(attach.button, send);

    function setDot(on) {
      if (on) dot.removeAttribute("hidden");
      else dot.setAttribute("hidden", "");
      btn.classList.toggle("chat-btn-unread", !!on);
    }

    /* `STICK_SLOP_PX` is at module scope -- the conversation page needs the
     * same number and measures it against the document instead. */
    function atBottom(node) {
      return node.scrollHeight - node.clientHeight - node.scrollTop <= STICK_SLOP_PX;
    }

    function paint(payload) {
      var messages = (payload && payload.messages) || [];
      /* An answer that arrives while the dock is shut puts a dot on the
       * launcher. Without it a closed dock is where his answer goes to
       * die: the reply takes about a minute, and a minute is long enough
       * to navigate away and forget it was coming.
       *
       * `loaded` is the first-paint guard, and it is here because my
       * reviewer disproved the comment that used to sit in its place. I had
       * deleted it after a mutation check showed no test could see it, and
       * wrote that it could not fire: opening sets `isOpen` before the read,
       * so a shut dock must have been read already. That reasoning assumes
       * the read finishes before he can act again. Open the dock and close
       * it before the fetch lands -- one tap on a slow phone -- and the
       * first paint of the session runs shut, with `lastCount` still 0, so
       * a thread he has read a hundred times lights the dot. A mutation
       * check only ever tests the mutation I thought of. */
      if (!isOpen && loaded && messages.length > lastCount) setDot(true);
      lastCount = messages.length;
      /* Both of these are read **before** the repaint and neither can be read
       * after it: `renderAskThread` empties the container, and emptying it
       * puts `scrollTop` back to 0. So a version of this that asked where he
       * was after painting would see every thread pinned to the top. */
      // `hasMore` absent means an older server or the empty-thread reply;
      // both are honestly "nothing more to fetch", so the default is false.
      hasMore = !!(payload && payload.hasMore);
      var follow = stickToBottom || atBottom(thread);
      var was = thread.scrollTop;
      var grewFrom = pendingAnchor;
      pendingAnchor = null;
      renderAskThread(thread, payload);
      loaded = true;
      /* The repaint is what moved him, so his place has to be put back either
       * way -- to the newest message when he was already on it, and to the
       * message he was rereading when he was not.
       *
       * His capture on `issues.md`, 2026-08-30: *"Chat auto-scrolls to the
       * bottom every time a new message arrives even if I've scrolled up to
       * reread something -- annoying, should only stick to bottom if I was
       * already near it."* Making the jump conditional on its own would have
       * been worse than the bug: without the `was` branch, a poll landing
       * while he read an old message would have thrown him to the *top* of
       * the thread instead of the bottom. */
      if (isOpen) {
        if (grewFrom !== null) {
          /* An older page has just been prepended. Neither branch below is
           * right for it: the bottom would throw away the scroll back he
           * just asked for, and `was` would leave him at the top of messages
           * he has not read yet. What keeps him on the message he was
           * looking at is the growth -- everything added went in above him. */
          thread.scrollTop = thread.scrollHeight - grewFrom;
        } else {
          thread.scrollTop = follow ? thread.scrollHeight : was;
        }
        stickToBottom = false;
      }
    }

    /* The thread he last read, kept on the device so opening the dock paints
     * before the network answers.
     *
     * His `issues.md` #111, 2026-08-27: *"It takes 4-5 seconds to load the
     * conversation when i open the chat bubble. Please do this background
     * loading when i open the Nova app or use some local storage for it."*
     * Measured from inside the cluster on 2026-08-28, five reads each:
     * `/api/ask` answered in 0.52s-2.04s and `/api/conversations/thread` in
     * 0.03s-0.08s. So the wait is the ask thread and the round trip to it,
     * and his phone reaches that over Tailscale on top -- the dock really
     * did open onto "loading…" and sit there.
     *
     * **One entry per thread, and no cap on how many.** I built the
     * single-entry version first and its own test caught why it does not
     * work: opening the dock reads the remembered thread and would overwrite
     * the entry, so every thread he switches to in the switcher is the slow
     * one again. The size is measured rather than feared -- the live `/api/ask`
     * body is 7,785 bytes and a conversation thread 3,043, against 40
     * conversations in the store on 2026-08-28, so caching every thread he
     * has is around 300KB of a 5MB origin budget. `setItem` is wrapped for
     * `rememberSource`'s reason anyway: a full or disabled store must leave
     * the dock working, not throw out of the paint.
     *
     * **The cached body is painted and never acted on.** `waiting` is what
     * starts a poll, and a stale `true` would poll a thread that was
     * answered an hour ago; `loadThread` fires immediately after this and
     * the network answer is what governs. */
    var CHAT_THREADS_KEY = "nova.chatThreads.v1";

    function sourceKey() {
      return source.kind === "conv" ? "conv:" + source.id : "ask";
    }

    function readThreads() {
      var store = localStore();
      if (!store) return null;
      try {
        var parsed = JSON.parse(store.getItem(CHAT_THREADS_KEY) || "null");
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch (err) {
        // Corrupt: read as empty, not as absent. `null` is reserved for "there
        // is no store", and returning it here would make `cacheThread` skip
        // the write -- so one unparseable value would kill the cache for good
        // instead of being overwritten by the next thread that loads.
        return {};
      }
    }

    function cacheThread(payload) {
      var store = localStore();
      if (!store) return;
      /* Only the newest page is worth keeping. The cache exists to paint the
       * dock before the network answers (`issues.md` #111), and a 500-message
       * thread he happened to scroll back through would be the thing every
       * later open pays to parse -- and `localStorage` is shared by every
       * thread in `held`, so one long one can push the rest out. */
      if (pageLimit > PAGE_STEP) return;
      // Not null: `readThreads` only returns null when there is no store, and
      // that is the line above.
      var held = readThreads();
      held[sourceKey()] = payload;
      try {
        store.setItem(CHAT_THREADS_KEY, JSON.stringify(held));
      } catch (err) { /* full or disabled: the dock still works */ }
    }

    /* True when it painted. An empty cached thread returns false rather than
     * painting nothing, so the "loading…" line still appears for a thread
     * that genuinely has no messages yet -- painting zero messages over the
     * placeholder would show him a blank panel with no sign a fetch was
     * running. */
    function paintCached() {
      var held = readThreads();
      if (!held) return false;
      var body = held[sourceKey()];
      if (!body || !(body.messages || []).length) return false;
      paint(body);
      return true;
    }

    function stopChatPoll() {
      if (pollHandle !== null) {
        clearTimeout(pollHandle);
        pollHandle = null;
      }
    }

    function pollChat(attempts) {
      stopChatPoll();
      if (attempts >= ASK_POLL_MAX) return;
      var token = sourceToken;
      pollHandle = setTimeout(function () {
        pollHandle = null;
        // Vouch for whichever thread is painted, not for the ask thread
        // regardless. Presence is per conversation on Agora's side and always
        // was; until `/api/conversations/watching` existed this could only
        // name the tagged one, so a switched-to thread got no vouch at all
        // and buzzed his phone about a message he was watching arrive.
        pingAskWatching(isOpen && source.kind === "ask");
        pingConvWatching(isOpen && source.kind === "conv", source.id);
        fetchPage(threadUrl())
          .then(function (payload) {
            if (token !== sourceToken) return;
            paint(payload);
            cacheThread(payload);
            if (payload.waiting) pollChat(attempts + 1);
          })
          // A failed poll is not a failed answer, same as `pollConv`.
          .catch(function () {
            if (token !== sourceToken) return;
            pollChat(attempts + 1);
          });
      }, ASK_POLL_MS);
    }

    /* One more page of the same thread, asked for by reaching the top.
     *
     * The guard is `loadingOlder` rather than a debounce on the scroll event:
     * a fetch takes long enough that a phone flicking at the top would send
     * several, and each answer repaints, so the second one would land on a
     * thread the first had already moved under him. */
    function loadOlder() {
      if (loadingOlder || !hasMore || !isOpen) return;
      loadingOlder = true;
      pageLimit += PAGE_STEP;
      /* Measured before the fetch and handed to `paint` only in the `.then`.
       * Setting `pendingAnchor` here instead would give it to whichever paint
       * happens next -- and while he is waiting for a reply the four-second
       * poll is repainting the same thread, so an ordinary poll landing
       * first would consume the anchor against unchanged content, compute a
       * growth of zero, and drop him at the top of the thread. That is the
       * exact moment this feature is for. */
      var anchor = thread.scrollHeight;
      var token = sourceToken;
      fetchPage(threadUrl())
        .then(function (payload) {
          if (token !== sourceToken) return;
          pendingAnchor = anchor;
          paint(payload);
        })
        .catch(function () {
          // Put the page size back: a failed fetch has painted nothing, so
          // leaving it raised would make the next poll silently ask for a
          // page he never got, and the retry on the next flick impossible.
          if (token !== sourceToken) return;
          pageLimit -= PAGE_STEP;
        })
        .then(function () { loadingOlder = false; });
    }

    /* How close to the top counts as asking for more. Bigger than
     * `STICK_SLOP_PX`'s job -- this one wants to fire slightly *before* he
     * hits the ceiling, so the older messages are already arriving by the
     * time he gets there rather than after a visible stop. */
    var OLDER_TRIGGER_PX = 96;

    thread.addEventListener("scroll", function () {
      if (thread.scrollTop <= OLDER_TRIGGER_PX) loadOlder();
    });

    function loadThread() {
      var token = sourceToken;
      return fetchPage(threadUrl())
        .then(function (payload) {
          if (token !== sourceToken) return;
          paint(payload);
          cacheThread(payload);
          if (payload.waiting) pollChat(0);
        })
        .catch(function (err) {
          if (token !== sourceToken) return;
          // Only paint an error over an empty dock. Overwriting a thread he
          // can already read with "could not load" would be the wrong
          // report -- the messages on screen are still true.
          if (!loaded) {
            thread.textContent = "";
            thread.appendChild(el("p", "empty", "Could not load the thread: " + err));
          }
        });
    }

    /* The switcher.
     *
     * `Ask Nova` is pinned at the top as its own row rather than left to
     * arrive in the listing: the `/ask` thread *is* in `/api/conversations`
     * -- it is a normal Agora conversation carrying the `nova-ask` tag --
     * but it is reachable through an endpoint that needs no id, and letting
     * both rows exist would give one thread two identities and two unread
     * counts. So it is filtered out below and pinned here.
     *
     * The list replaces the thread inside the panel instead of floating
     * over it, so the composer cannot be typed into while it is up and
     * there is only ever one scrolling region.
     */
    function setList(next) {
      var on = !!next;
      if (!listEl || !menuBtn) return;
      dock.classList.toggle("list-open", on);
      menuBtn.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) listEl.removeAttribute("hidden");
      else listEl.setAttribute("hidden", "");
      if (on) loadList();
    }

    function listRow(label, meta, current, onPick) {
      var row = el("button", "chat-list-row" + (current ? " current" : ""));
      row.setAttribute("type", "button");
      row.appendChild(el("div", "chat-list-name", label));
      if (meta) row.appendChild(el("div", "chat-list-meta", meta));
      row.addEventListener("click", onPick);
      return row;
    }

    /* One write against a conversation, resolved or thrown.
     *
     * The four routes answer `{ok, message}` and a refusal he can fix
     * carries a 400, so a rejected promise here always has a sentence in it
     * that is worth showing him -- which is why nothing on this path
     * swallows. */
    /* `chatWrite` throws away everything but `result`, which is right for
     * the five writes that answer with one string. `/api/conversations/new`
     * answers with an id *and* the name it settled on, so this one hands the
     * whole object back. `chatWrite` is written in terms of it rather than
     * beside it -- one place that decides what a failed write looks like. */
    function chatWriteFull(path, body) {
      return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) {
            throw new Error((result && (result.message || result.error)) || "failed");
          }
          return result;
        });
    }

    function chatWrite(path, body) {
      return chatWriteFull(path, body).then(function (answer) {
        return answer.result;
      });
    }

    /* The edit options a held row turns into.
     *
     * His capture, `issues.md` 2026-08-27, rated 🔴 Immediately: *"I need
     * the chat bubble to be able to start ned conversations, delete them,
     * change name, organize like move to a folder. Editing by pressing the
     * conversation for 1 sec and it gives me edit options."*
     *
     * The gesture is `HOLD_MS`, the same second his board rows use, because
     * it is the same instruction -- issue #84's *"If i hold the card for
     * more than 1 second i get into edit mode"*. Reusing the number rather
     * than picking one means a future change to how long a hold is stays
     * one edit.
     *
     * The panel replaces the row in place instead of opening a modal, for
     * the board editor's reason: the dock is a small panel on a phone and a
     * second layer over it would cover the list he is choosing from.
     *
     * **Delete asks, and it is the only one that does.** Rename and move
     * are both undoable by doing them again; a deleted conversation is
     * gone, and Agora unbinds any heartbeat pointing at it on the way out,
     * so there is nothing to put back.
     */
    function rowEditor(row, folders, models, done) {
      var wrap = el("div", "chat-row-edit");

      var name = document.createElement("input");
      name.type = "text";
      name.className = "chat-row-edit-name";
      name.value = row.name;
      name.setAttribute("aria-label", "Conversation name");
      wrap.appendChild(name);

      var pick = document.createElement("select");
      pick.className = "chat-row-edit-folder";
      pick.setAttribute("aria-label", "Folder");
      function option(value, label, selected) {
        var o = document.createElement("option");
        o.value = value;
        o.textContent = label;
        if (selected) o.selected = true;
        return o;
      }
      pick.appendChild(option("", "No folder", !row.folderId));
      folders.forEach(function (f) {
        pick.appendChild(option(f.id, f.name, f.id === row.folderId));
      });
      // A folder he has not made yet. Chosen from the same control as the
      // ones that exist, because "move it into a new folder called X" is
      // one intention and making him create the folder first would be two.
      pick.appendChild(option("+new", "New folder…", false));
      wrap.appendChild(pick);

      /* Which model answers in this thread.
       *
       * Idea #95 opens with it: *"It is hard to change model for a
       * conversation because that means changing the model for all other
       * conversations that personas is in."* Agora moved `model` off the
       * persona and onto the conversation on 08-21, so the write has worked
       * for six days -- from Agora's own app, which is not the one he opens.
       * This is that fix arriving at the door he uses.
       *
       * A model the catalog does not list still gets an option, selected,
       * so the picker can never silently repoint a thread at something else
       * just because Agora's catalog moved on. Same reason the folder list
       * starts with the one the row is already in.
       *
       * `(metered)` is on the label for `newChatForm`'s reason and it is a
       * money reason, not a cosmetic one: a metered model spends the prepaid
       * balance per token, and a picker that hides which ones do would let
       * him spend it by picking the top of a list.
       */
      var modelPick = document.createElement("select");
      // Two classes: the first is the folder select's styling, the second is
      // this control's own hook. Sharing only the styling class would make
      // `querySelector(".chat-row-edit-folder")` ambiguous in the panel.
      modelPick.className = "chat-row-edit-folder chat-row-edit-model";
      modelPick.setAttribute("aria-label", "Model");
      var listed = false;
      models = models || [];
      models.forEach(function (m) {
        if (m.id === row.model) listed = true;
        modelPick.appendChild(option(
          m.id, m.label + (m.metered ? " (metered)" : ""), false));
      });
      if (row.model && !listed) {
        modelPick.insertBefore(option(row.model, row.model, false), modelPick.firstChild);
      }
      if (!row.model) {
        modelPick.insertBefore(option("", "Model (unset)", false), modelPick.firstChild);
      }
      // Set after the options exist rather than with `selected` on each one.
      // A select auto-selects its first option, and the reset algorithm then
      // keeps only the *last* option marked selected -- so an option marked
      // at creation and inserted at the front loses to the auto-selected one
      // behind it, and the picker opens on a model the thread is not on.
      // Caught by the browser test on a conversation whose model the catalog
      // no longer lists; the same bug made an untouched save post a change.
      modelPick.value = row.model || "";
      // No catalog, no picker. `_model_rows` returns `[]` when Agora cannot
      // say what it accepts, and a select holding only the model the thread
      // already has is a control that cannot change anything.
      if (models.length) wrap.appendChild(modelPick);

      var newFolder = document.createElement("input");
      newFolder.type = "text";
      newFolder.className = "chat-row-edit-name";
      newFolder.placeholder = "New folder name";
      newFolder.setAttribute("aria-label", "New folder name");
      newFolder.hidden = true;
      wrap.appendChild(newFolder);
      pick.addEventListener("change", function () {
        newFolder.hidden = pick.value !== "+new";
        if (!newFolder.hidden) newFolder.focus();
      });

      var note = el("p", "chat-row-edit-note", "");
      var foot = el("div", "chat-row-edit-foot");
      var save = el("button", "chat-row-edit-save", "Save");
      save.setAttribute("type", "button");
      var cancel = el("button", "chat-row-edit-cancel", "Cancel");
      cancel.setAttribute("type", "button");
      var drop = el("button", "chat-row-edit-delete", "Delete");
      drop.setAttribute("type", "button");
      foot.appendChild(save);
      foot.appendChild(cancel);
      foot.appendChild(drop);
      wrap.appendChild(foot);
      wrap.appendChild(note);

      function busy(on) {
        save.disabled = on;
        drop.disabled = on;
        cancel.disabled = on;
      }

      cancel.addEventListener("click", function () { done(false); });

      save.addEventListener("click", function () {
        var wanted = name.value.trim();
        if (!wanted) {
          note.textContent = "A conversation needs a name.";
          return;
        }
        busy(true);
        note.textContent = "saving…";
        // The folder is resolved first because creating it can fail, and a
        // rename that landed beside a failed move would leave the row half
        // changed with no way to tell which half.
        var folder = Promise.resolve(pick.value === "+new"
          ? chatWrite("/api/conversations/folder", { name: newFolder.value.trim() })
          : pick.value);
        folder
          .then(function (folderId) {
            var work = [];
            if (wanted !== row.name) {
              work.push(chatWrite("/api/conversations/rename",
                { id: row.id, name: wanted }));
            }
            if ((folderId || "") !== (row.folderId || "")) {
              work.push(chatWrite("/api/conversations/move",
                { id: row.id, folderId: folderId || "" }));
            }
            // Only when it moved. Re-sending the model it already has would
            // audit a change that did not happen on every rename.
            if (modelPick.value && modelPick.value !== (row.model || "")) {
              work.push(chatWrite("/api/conversations/model",
                { id: row.id, model: modelPick.value }));
            }
            return Promise.all(work).then(function () { return wanted; });
          })
          .then(function (saved) {
            // The dock title is the one copy of this name outside the list,
            // so a rename of the thread he is reading has to move it too.
            if (source.kind === "conv" && source.id === row.id) {
              source.name = saved;
              titleEl.textContent = saved;
              rememberSource();
            }
            done(true);
          })
          .catch(function (err) {
            busy(false);
            note.textContent = "Could not save: " + err.message;
          });
      });

      drop.addEventListener("click", function () {
        if (!window.confirm("Delete \u201c" + row.name + "\u201d? This cannot be undone.")) return;
        busy(true);
        note.textContent = "deleting…";
        chatWrite("/api/conversations/delete", { id: row.id })
          .then(function () {
            // He may have been reading the thread he just deleted. Falling
            // back to the ask thread is the only source that is guaranteed
            // to still exist -- `nova_ask` finds it by tag and creates it
            // if it is missing.
            if (source.kind === "conv" && source.id === row.id) {
              switchTo({ kind: "ask", id: null, name: "Ask Nova" });
            }
            done(true);
          })
          .catch(function (err) {
            busy(false);
            note.textContent = "Could not delete: " + err.message;
          });
      });

      return wrap;
    }

    /* Press-and-hold on a conversation row. Same shape as the board's, and
     * the comment there is the one that explains it: the timer is cleared on
     * every way a press can end, not just on let-go, because a pending
     * `setTimeout` that fires into a detached node has already broken this
     * suite once. `held` suppresses the click the browser sends afterwards,
     * so a hold does not also switch threads. */
    function holdToEdit(node, open) {
      var timer = null;
      var held = false;
      function end() {
        if (timer) { clearTimeout(timer); timer = null; }
      }
      function start() {
        end();
        held = false;
        timer = setTimeout(function () {
          timer = null;
          held = true;
          open();
        }, HOLD_MS);
      }
      ["mousedown", "touchstart"].forEach(function (name) {
        node.addEventListener(name, start);
      });
      ["mouseup", "mouseleave", "touchend", "touchcancel", "touchmove", "scroll"]
        .forEach(function (name) { node.addEventListener(name, end); });
      node.addEventListener("click", function (event) {
        if (!held) return;
        held = false;
        event.preventDefault();
        event.stopPropagation();
      }, true);
    }

    function switchTo(next) {
      // Tapping the row he is already reading closes the list and leaves
      // the thread alone -- reloading it would blank a painted thread and
      // scroll him back to the bottom for nothing.
      var same = next.kind === source.kind &&
        (next.kind !== "conv" || next.id === source.id);
      setList(false);
      if (same) return;
      source = next;
      sourceToken += 1;
      rememberSource();
      stopChatPoll();
      // A fresh thread has its own message count, and `loaded` is what stops
      // the first paint of it lighting the unread dot on a thread he is
      // looking at right now.
      loaded = false;
      lastCount = 0;
      // A new thread opens on its newest page, whatever he had scrolled back
      // to on the last one.
      pageLimit = PAGE_STEP;
      hasMore = false;
      loadingOlder = false;
      pendingAnchor = null;
      titleEl.textContent = source.name;
      thread.textContent = "";
      // Only the thread the cache actually holds paints instantly; every
      // other row in the switcher still shows the placeholder.
      if (!paintCached()) thread.appendChild(el("p", "empty", "loading…"));
      loadThread();
    }

    /* A fold in the switcher, same `<details>` shape as the sidebar groups.
     *
     * His capture, `issues.md` 2026-08-26, two minutes apart: *"the list of
     * conversations are just filled with heartbeats"*, then *"I ment not
     * just filled with heartbeats, other converssations aswell but the
     * beats take up a lot of space and i have to scroll past them. Maybe
     * folders are the right solutions here aswell."*
     *
     * Measured against the live store the same morning: 40 non-archived
     * conversations, **30 of them cycle threads**. So the switcher opened
     * on 39 rows of which three quarters were a heartbeat he has never
     * needed to reopen, and the nine threads he might want were below them.
     *
     * The count goes in the summary because a shut fold otherwise hides how
     * much it is hiding, which is the failure the sidebar folds do not have
     * (there, the group name tells you what is inside). Nothing is dropped
     * -- every thread is still one tap away, which is the difference
     * between a fold and a filter. */
    function listFold(title, count, open) {
      var fold = el("details", "chat-list-fold");
      if (open) fold.setAttribute("open", "");
      var summary = el("summary", "chat-list-group");
      summary.appendChild(el("span", "chat-list-group-name", title));
      summary.appendChild(el("span", "chat-list-group-count", String(count)));
      fold.appendChild(summary);
      // The rows live in a `<div>` rather than directly in the `<details>`
      // for `.nav-fold-body`'s reason: overriding `display` on a `<details>`
      // replaces the box its closed-state hiding is built on.
      fold.appendChild(el("div", "chat-list-fold-body"));
      return fold;
    }

    /* The form behind "New conversation".
     *
     * A name and nothing else. Every new thread is with Nova -- the owner,
     * issues board #119 on 2026-08-29: *"drop the Agora multi-persona chat
     * picker from the Nova app entirely, the app should be Nova only, no
     * Claude/Opus/Gemini/Haiku/Study buddy tabs inside it"*. Threads he
     * already has with those personas still open from the list; what is
     * gone is starting another one.
     */
    function newForm(done) {
      var wrap = el("div", "chat-row-edit");
      var name = document.createElement("input");
      name.type = "text";
      name.className = "chat-row-edit-name";
      name.placeholder = "Optional \u2014 named after your first message";
      name.setAttribute("aria-label", "Conversation name");
      wrap.appendChild(name);

      var note = el("p", "chat-row-edit-note", "");
      var foot = el("div", "chat-row-edit-foot");
      var save = el("button", "chat-row-edit-save", "Start");
      save.setAttribute("type", "button");
      var cancel = el("button", "chat-row-edit-cancel", "Cancel");
      cancel.setAttribute("type", "button");
      foot.appendChild(save);
      foot.appendChild(cancel);
      wrap.appendChild(foot);
      wrap.appendChild(note);

      cancel.addEventListener("click", function () { done(false); });
      name.focus();

      save.addEventListener("click", function () {
        // Blank is an answer, not a missing field. His capture, issues.md
        // #139: *"I have to type a conversation title before I can even
        // start it, but I don't always know what it'll be about."* The
        // server names it `New chat` and the first message renames it.
        var wanted = name.value.trim();
        save.disabled = true;
        cancel.disabled = true;
        note.textContent = "starting…";
        chatWriteFull("/api/conversations/new", { name: wanted })
          .then(function (answer) {
            var id = answer.result;
            // A create that answers 200 without an id is the one failure
            // this form cannot recover from silently: `switchTo` would open
            // `?id=undefined`, which 404s, and the composer under it would
            // refuse every message with "conversationId and text must be
            // strings". That is exactly what he photographed on 2026-08-27,
            // because the route answered under `conversationId` while every
            // other chat write answers under `result`. Say so instead --
            // the conversation itself was created either way, so the list
            // is where he can still reach it.
            if (typeof id !== "string" || !id) {
              throw new Error("it was created but the server did not say which one — open it from the list");
            }
            // Straight into the thread he just made: he started it to say
            // something, and leaving him on the list would make him find it.
            // The name the store holds, not the box he typed into: those
            // differ by exactly the case this whole change is about, and a
            // header reading "" while the switcher reads "New chat" is the
            // two-copies-of-one-rule bug wearing a title bar.
            switchTo({ kind: "conv", id: id, name: answer.name || wanted,
                       untitled: !wanted });
            done(false);
          })
          .catch(function (err) {
            save.disabled = false;
            cancel.disabled = false;
            note.textContent = "Could not start it: " + err.message;
          });
      });
      return wrap;
    }

    /* The switcher's last answer, kept so re-opening it is not a blank wait.
     *
     * Issue #141: *"loads slowly every single time it's opened, not just on
     * cold start -- worth investigating whether it's re-fetching/re-querying
     * everything instead of caching."* It is not re-querying more than once:
     * `/api/conversations` is one fetch per open and always was. What it is
     * is slow -- measured Cycle 764 against the live store, that fetch takes
     * 0.4s-1.3s, because Agora's own `/conversations` answers 1.33MB for 782
     * rows and 871KB of that is a `personality` string per row that nothing
     * on this page reads. The list showed the word "loading…" for all of it,
     * every single open, including the ninth one in a row.
     *
     * So the last payload is kept and painted immediately on the next open,
     * and the fetch still runs unconditionally behind it. **Nothing is
     * suppressed and no answer is delayed** -- the same bytes arrive at the
     * same moment and repaint the list. That is why this is not the caching
     * `nova_site` refuses on purpose for this route: a cached *response*
     * would let a reply he is waiting for go unreported, and this cannot,
     * because it never skips a request.
     *
     * Anything that just changed the list passes `fresh`, so a rename, a
     * delete or a new thread never repaints the row he acted on in its old
     * form.
     */
    var listCache = null;
    var listToken = 0;

    function loadList(fresh) {
      if (fresh) listCache = null;
      // Open, shut, open leaves two fetches in flight, and without this the
      // older one could land second and become the cache -- which used to
      // cost one wrong repaint and would now cost every later open too.
      var token = ++listToken;
      if (listCache) {
        renderList(listCache);
      } else {
        renderShell();
        listEl.appendChild(el("p", "empty", "loading…"));
      }
      fetchPage("/api/conversations")
        .then(function (payload) {
          if (token !== listToken) return;
          listCache = payload;
          // He can hold a row and open its editor while the refresh is in
          // flight now, which was impossible when the list was empty until
          // the fetch landed. Repainting would throw away what he is typing;
          // the cache is already updated, so the next open shows it.
          if (listEl.querySelector(".chat-row-edit")) return;
          renderList(payload);
        })
        .catch(function (err) {
          if (token !== listToken) return;
          // A failed refresh over a list already on screen leaves that list
          // up. The rows he can see are the last ones that were true, and
          // replacing them with an error message is a worse answer than a
          // slightly old list.
          if (listCache) return;
          renderShell();
          listEl.appendChild(
            el("p", "empty", "Could not load your conversations: " + err));
        });
    }

    /* The two rows that are there whatever the listing says. */
    function renderShell() {
      listEl.textContent = "";
      // The label is the button's own text, not a `.chat-list-name` child:
      // that class means "a conversation is called this", and anything
      // reading the list -- a stylesheet, a test, a future feature counting
      // threads -- would be right to treat a node carrying it as a thread.
      var start = el("button", "chat-list-new", "+ New conversation");
      start.setAttribute("type", "button");
      start.addEventListener("click", function () {
        if (listEl.querySelector(".chat-row-edit")) return;
        var form = newForm(function (changed) {
          if (changed) loadList(true);
          else if (form.parentNode) listEl.replaceChild(start, form);
        });
        listEl.replaceChild(form, start);
      });
      listEl.appendChild(start);
      listEl.appendChild(listRow("Ask Nova", "", source.kind === "ask", function () {
        switchTo({ kind: "ask", id: null, name: "Ask Nova" });
      }));
    }

    function renderList(payload) {
      renderShell();
      var folders = payload.folders || [];
      var models = payload.models || [];
      var rows = (payload.conversations || []).filter(function (row) {
        return (row.tags || []).indexOf("nova-ask") === -1;
      });
      if (!rows.length) {
        listEl.appendChild(el("p", "empty", "No other conversations yet."));
        return;
      }
      // `cycleThread` is `nova_conversations.conversations()`'s own flag
      // for an `evolve-cycle:` tag. Reading the tag here as well would
      // be a second copy of that rule in a second language.
      var beats = rows.filter(function (row) { return !!row.cycleThread; });
      var loose = rows.filter(function (row) { return !row.cycleThread; });

      function fill(fold, group) {
        var body = fold.lastChild;
        group.forEach(function (row) {
          var meta = [row.personaName, row.model].filter(Boolean).join(" · ");
          var node = listRow(
            row.name, meta,
            source.kind === "conv" && source.id === row.id,
            function () {
              switchTo({ kind: "conv", id: row.id, name: row.name });
            });
          holdToEdit(node, function () {
            if (listEl.querySelector(".chat-row-edit")) return;
            var editor = rowEditor(row, folders, models, function (changed) {
              if (changed) loadList(true);
              else if (editor.parentNode) editor.parentNode.replaceChild(node, editor);
            });
            body.replaceChild(editor, node);
          });
          body.appendChild(node);
        });
        listEl.appendChild(fold);
      }

      /* His folders, each as its own fold.
       *
       * His capture, `issues.md` 2026-08-27: *"organize like move to a
       * folder"*. Agora has carried `folderId` on a conversation all
       * along and the switcher grouped by the `evolve-cycle:` tag
       * instead, so a folder he made was invisible here -- the rows in
       * it fell into "Conversations" with everything else.
       *
       * A folder that holds nothing is deliberately still drawn. An
       * empty fold is how he can see the folder exists, and dropping it
       * would make a folder disappear the moment its last conversation
       * moved out -- with no way to move anything back into it. */
      var filed = {};
      folders.forEach(function (folder) {
        filed[folder.id] = loose.filter(function (row) {
          return row.folderId === folder.id;
        });
      });
      var top = loose.filter(function (row) {
        return !row.folderId || !filed[row.folderId];
      });

      folders.forEach(function (folder) {
        var group = filed[folder.id];
        var current = source.kind === "conv" && group.some(function (row) {
          return row.id === source.id;
        });
        fill(listFold(folder.name, group.length, current), group);
      });
      // Open on the threads he starts, shut on the ones the loop starts
      // for itself -- and open a fold anyway when it holds the thread he
      // is reading, so the switcher never opens without the current row
      // on screen.
      if (top.length) fill(listFold("Conversations", top.length, true), top);
      if (beats.length) {
        var current = source.kind === "conv" && beats.some(function (row) {
          return row.id === source.id;
        });
        fill(listFold("Heartbeats", beats.length, current), beats);
      }
    }

    function setOpen(next) {
      isOpen = !!next;
      btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      dock.setAttribute("aria-hidden", isOpen ? "false" : "true");
      if (isOpen) dock.removeAttribute("hidden");
      else dock.setAttribute("hidden", "");
      dock.classList.toggle("open", isOpen);
      btn.classList.toggle("open", isOpen);
      // The full-screen sheet has to hide the hamburger, and CSS cannot
      // reach it from here -- `.menu-btn` is an earlier sibling. The class
      // goes on `body` so the media query can do it; on a wide screen the
      // dock is a small panel and the rule does not apply.
      document.body.classList.toggle("chat-open", isOpen);
      // Closing deliberately leaves the poll running: the question is still
      // being answered and the dot is how he finds out it landed. The
      // switcher does collapse, so re-opening lands on the thread rather
      // than on a list he left up three pages ago.
      if (!isOpen) {
        setList(false);
        return;
      }
      setDot(false);
      /* Opening always lands on the newest message. A shut dock is
       * `display: none`, where `scrollTop` reads 0 in a real browser, so
       * without this the first paint after an open would find him "at the
       * top" and hold him there -- which is the bug this is meant to fix,
       * pointing the other way. */
      stickToBottom = true;
      /* Read the remembered thread once per page load, on the first open
       * rather than at construction: `localStorage` is one synchronous
       * read and there is no reason to spend it on a dock he never taps.
       * After that `source` is the live value and the store is only its
       * durable copy. */
      if (!recalled) {
        recalled = true;
        recallSource();
        titleEl.textContent = source.name;
      }
      /* Opening the dock deliberately does not focus the box. It used to,
       * and his capture on `issues.md`, 2026-08-25: *"The new chat bubble
       * autoselects the input box which makes my mobile keyboard open up
       * and push everything up immediately. It should push everything up,
       * but not when i just want to read a new message."*
       *
       * The dot on the launcher means an answer has landed, so the common
       * reason to open this is to read, not to type -- and on a phone the
       * focus took half the screen for a keyboard he had not asked for,
       * over the message he opened it to see. Tapping the box still opens
       * the keyboard, which is the half he says is right. The `/ask` page
       * has never focused its box on render; this makes the dock agree
       * with it. */
      // After the panel is on screen, never before: `scrollHeight` on a
      // `display: none` element is 0, and a box measured shut collapses to
      // its padding.
      growChatBox();
      /* Paint what he last read before the fetch, then let the fetch paint
       * over it. `loaded` is the guard: once the dock has been painted this
       * page load the DOM is already ahead of the cache, and repainting from
       * it would put an older thread back on screen. */
      if (!loaded) paintCached();
      loadThread();
    }

    if (box) box.addEventListener("input", growChatBox);
    btn.addEventListener("click", function () { setOpen(!isOpen); });
    if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
    if (menuBtn) {
      menuBtn.addEventListener("click", function () {
        setList(!dock.classList.contains("list-open"));
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !isOpen) return;
      // Escape backs out one level at a time: the switcher first, the dock
      // only once the switcher is down. Closing the whole panel from an
      // open list would lose the thread he was trying to get back to.
      if (dock.classList.contains("list-open")) setList(false);
      else setOpen(false);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = box.value.trim();
      // A tray with a picture in it and nothing typed is still a real
      // message -- he sends the screenshot and asks about it next.
      if (!text && !attach.count()) return;
      var body = [text, attach.markdown()].filter(Boolean).join("\n\n");
      sending = true;
      syncSend();
      status.textContent = "sending…";
      // Two endpoints, one composer. `/api/ask` finds its own conversation
      // from the `nova-ask` tag and takes no id; every other thread is
      // addressed by one.
      var conv = source.kind === "conv";
      var token = sourceToken;
      // Everything `autotitle` needs, read before the send rather than after
      // it: he can switch threads while the request is in flight, and a
      // title derived here must land on the thread he typed it into.
      //
      // Two conditions, neither of them the safety check -- the server
      // refuses to rename anything not still called `New chat`, and that
      // check lives there so the placeholder is spelled in one file. These
      // only keep the app from asking when the answer is already known.
      // `untitled` is set by the form when he starts a thread without
      // naming it and rides along in the stored source, so a thread he
      // named is never asked about; "nothing painted yet" is how the dock
      // knows this is the opening message rather than the fortieth.
      var titleFor = conv && source.untitled && !thread.querySelector(".ask-msg")
        ? { id: source.id, name: source.name, text: text }
        : null;
      fetch(conv ? "/api/conversations/send" : "/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          conv ? { conversationId: source.id, text: body } : { text: body }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          box.value = "";
          // A sent ten-line question leaves a ten-line empty box behind
          // unless the height is recomputed; `input` does not fire on a
          // programmatic assignment.
          growChatBox();
          // Emptied only now, the same rule the box follows: a tray cleared
          // on the tap loses the picture when the send is refused.
          attach.clear();
          status.textContent = "";
          sending = false;
          syncSend();
          // The send landed, but he may have switched threads while it was
          // in flight -- painting his message into the thread he moved to
          // would put it under the wrong name, and polling would then be
          // polling the new thread on the old one's schedule.
          if (token !== sourceToken) return;
          // Paint his question straight away rather than waiting a poll for
          // the server to echo it, for `pollConv`'s reason: a box that has
          // gone blank with nothing to show for it reads as a lost message.
          thread.appendChild(askMessage({ sender: "Edvard", text: body }));
          thread.appendChild(el("div", "ask-msg ask-theirs ask-pending", "Thinking…"));
          // Sending is him asking to be at the bottom, whatever he was
          // rereading a second ago -- so the answer to this question lands on
          // his screen rather than below it.
          stickToBottom = true;
          thread.scrollTop = thread.scrollHeight;
          // His own message is not an unread answer.
          lastCount += 1;
          pollChat(0);
        })
        .then(function () {
          if (!titleFor) return;
          return chatWrite("/api/conversations/autotitle", titleFor)
            .then(function (named) {
              if (typeof named !== "string" || !named) return;
              // The thread was named; only the screen still on it moves.
              if (token !== sourceToken || source.id !== titleFor.id) return;
              source.name = named;
              source.untitled = false;
              titleEl.textContent = named;
              rememberSource();
              if (dock.classList.contains("list-open")) loadList(true);
            })
            // A thread that keeps the placeholder is a worse name, not a
            // failed send -- his message is already posted and the error
            // line under the box would say otherwise.
            .catch(function () { });
        })
        .catch(function (err) {
          sending = false;
          syncSend();
          status.textContent = "could not send: " + err.message;
        });
    });
  })();

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
