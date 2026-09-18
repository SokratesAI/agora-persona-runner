/* Which of my replies the owner has read, and which cards have opened their
 * ask drawer (issue #233).
 *
 * The nineteenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js`,
 * `models.js`, `richtext.js`, `bubble.js`, `askthread.js`, `rowedit.js` and
 * `cycle.js`: the read marks in `localStorage` (`nova.repliesRead.v1`,
 * `nova.askOpened.v1`), the counts built on them (`unreadOn`,
 * `unreadReplies`, `unreadSummary`), and `localStore` itself.
 *
 * It borrows nothing and hands eight functions back. The two stores and
 * their loaded flags are reassigned in here and read nowhere else, so they
 * stay private to this file rather than being handed over as values that
 * would go stale.
 */
(function () {
  "use strict";

  window.novaReplies = function () {
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
     * Oldest rather than newest, for the reason the ask pill names the
     * oldest ask: the newest card is the one at the top of the feed that he
     * will see anyway, and the one worth pointing at is the one about to
     * scroll out of the twenty-entry window. */
    function unreadSummary(byCycle) {
      var count = 0;
      var cards = 0;
      var oldest = null;
      var items = [];
      var cycles = [];
      Object.keys(byCycle || {}).forEach(function (key) {
        var unread = unreadOn(key, byCycle[key]);
        if (!unread.length) return;
        count += unread.length;
        cards += 1;
        var cycle = parseInt(key, 10);
        if (!isNaN(cycle)) cycles.push(cycle);
        if (!isNaN(cycle) && (oldest === null || cycle < oldest)) oldest = cycle;
        unread.forEach(function (answer) {
          items.push({ cycle: cycle, stamp: answer.stamp, text: answer.text, asked: answer.asked });
        });
      });
      /* Newest first: the panel is a mailbox, and the reply he is most likely
       * to be looking for is the one that just arrived. The *badge* still names
       * the oldest card, because that one is about to scroll out of the feed. */
      items.sort(function (a, b) { return a.stamp < b.stamp ? 1 : a.stamp > b.stamp ? -1 : 0; });
      /* Newest card first, which is the order `/replies` renders in anyway --
       * sent so the URL is stable between two fetches that found the same
       * cards, rather than varying with `Object.keys` order and turning every
       * poll into a fresh etag. */
      cycles.sort(function (a, b) { return b - a; });
      return { count: count, cards: cards, cycle: oldest, items: items, cycles: cycles };
    }

    return {
      askAlreadyOpened: askAlreadyOpened,
      localStore: localStore,
      markAskOpened: markAskOpened,
      markRepliesRead: markRepliesRead,
      seedRepliesRead: seedRepliesRead,
      unreadOn: unreadOn,
      unreadReplies: unreadReplies,
      unreadSummary: unreadSummary,
    };
  };
})();
