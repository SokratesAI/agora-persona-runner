/* The ask thread's helpers (issue #233, step 20).
 *
 * The twelfth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js` and `steps.js`: the polling
 * constants, the sent-but-not-yet-echoed rows, the copy and retry buttons,
 * the "he is watching" pings and the one-line thread note.
 *
 * It borrows four names -- one constant and three function declarations --
 * and hands ten back. `app.js` binds it at the place the block used to sit;
 * every use of what it hands back loads after that point.
 */
(function () {
  "use strict";

  window.novaAsk = function (shared) {
    var OWNER_RECORD = shared.OWNER_RECORD;
    var askMessage = shared.askMessage;
    var askPending = shared.askPending;
    var el = shared.el;

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

    /* "Ask again" -- issue #143's regenerate, as far as this thread can honestly
     * go. Agora's conversations are append-only: there is no route that replaces
     * a message, so a button labelled "Regenerate" would promise a swap that
     * cannot happen. This sends the question that produced the answer again, and
     * the new answer lands at the bottom beside the old one, which is what the
     * data model actually supports. The label says that rather than hiding it.
     *
     * It posts to `/api/conversations/send` from both surfaces, including the
     * ask thread -- that route takes a conversation id and `nova_ask.thread`
     * hands one out, so the dock does not need `/api/ask`'s find-by-tag.
     *
     * `afterSend` is the surface's own repaint, because the two of them differ:
     * the dock owns `pollChat` and a `lastCount` that must not read his own
     * question as an unread answer, and the conversation page owns `pollConv`.
     * A button that sent and painted nothing would look like it had failed. */
    function askRetryButton(conversationId, question, afterSend) {
      var button = el("button", "ask-retry", "Ask again");
      button.type = "button";
      button.title = "Send this question again";
      button.addEventListener("click", function () {
        if (button.disabled) return;
        // Disabled for the whole flight, not just re-labelled: a second tap
        // while the first is in the air is two turns spent on one question.
        button.disabled = true;
        button.textContent = "sending…";
        fetch("/api/conversations/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversationId: conversationId, text: question }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
            button.textContent = "Asked again";
            if (afterSend) afterSend(question);
          })
          .catch(function () {
            // Re-enabled on failure, and only on failure: nothing was sent, so
            // trying again is the right move. A success stays spent.
            button.disabled = false;
            button.textContent = "could not send";
            setTimeout(function () { button.textContent = "Ask again"; }, 2400);
          });
      });
      return button;
    }

    /* Keep a message he just sent on screen until the server has it.
     *
     * Issue #233's spike (agora-persona-runner#1200) measured why a sent
     * message can vanish: the send paints his bubble locally, the next poll
     * repaints the thread from the server, and a poll that lands before the
     * server has stored the message draws a thread without it. Keyed diffing
     * does not fix that in any framework -- the control run lost it under
     * Preact and Svelte too -- so the merge lives in the data, here, and the
     * Preact thread will carry it unchanged.
     *
     * `pending` is `[{text, sentAt}]` for one thread. A pending send is
     * settled by a message of his with the same text stamped no earlier than
     * two minutes before it was sent (phone and server clocks disagree), and
     * each server message settles at most one send, so saying "ok" twice keeps
     * both. One the server never shows is dropped after ten minutes rather than
     * haunting the thread forever. Returns the messages to draw, whether any
     * send is still unconfirmed, and the pending list to keep. */
    var PENDING_SEND_SKEW_MS = 120000;
    var PENDING_SEND_EXPIRES_MS = 600000;

    function mergePendingSends(messages, pending, now) {
      var used = {};
      var keep = [];
      (pending || []).forEach(function (send) {
        if (now - send.sentAt > PENDING_SEND_EXPIRES_MS) return;
        var match = -1;
        (messages || []).forEach(function (message, i) {
          if (match !== -1 || used[i] || message.sender !== OWNER_RECORD) return;
          if ((message.text || "").trim() !== send.text.trim()) return;
          var at = Date.parse(message.createdAt || "");
          if (!isNaN(at) && at < send.sentAt - PENDING_SEND_SKEW_MS) return;
          match = i;
        });
        if (match === -1) keep.push(send);
        else used[match] = true;
      });
      var drawn = (messages || []).concat(keep.map(function (send) {
        return { sender: OWNER_RECORD, text: send.text,
          createdAt: new Date(send.sentAt).toISOString() };
      }));
      return { messages: drawn, unconfirmed: keep.length > 0, pending: keep };
    }

    /* What both surfaces do the moment a message goes out: show it, and show
     * that something is coming. Without this the thread sits unchanged for up
     * to a poll interval and the tap reads as having done nothing. */
    function askPaintSent(container, text, sentAt) {
      var sent = { sender: OWNER_RECORD, text: text,
        createdAt: new Date(sentAt || Date.now()).toISOString() };
      // Keyed as the next poll's merged send will be, so it updates this row.
      if (window.novaMessage && window.novaThread) {
        return window.novaThread.append(container, [
          { node: { message: sent }, key: sent.createdAt + sent.sender, sig: "sent" },
          { node: { tail: "pending", progress: null }, key: "tail", sig: NaN }]);
      }
      container.appendChild(askMessage(sent));
      // The same loader the poll's own bubble draws, rather than a second way
      // of saying the same thing -- this is the one he sees first, in the
      // moment between the tap and the first poll.
      container.appendChild(askPending(null));
    }

    /* A thread that is one line: "loading…", or why it could not load. */
    function askPaintNote(container, text) {
      var line = el("p", "empty", text);
      if (window.novaThread) return window.novaThread.render(container, [{ node: line, key: "note", sig: text }]);
      container.textContent = "";
      container.appendChild(line);
    }

    return {
      ASK_POLL_MAX: ASK_POLL_MAX,
      ASK_POLL_MS: ASK_POLL_MS,
      STICK_SLOP_PX: STICK_SLOP_PX,
      askCopyButton: askCopyButton,
      askPaintNote: askPaintNote,
      askPaintSent: askPaintSent,
      askRetryButton: askRetryButton,
      mergePendingSends: mergePendingSends,
      pingAskWatching: pingAskWatching,
      pingConvWatching: pingConvWatching,
    };
  };

  /* "Edit" -- issue #143's edit-and-resubmit, as far as this thread can
   * honestly go. Same reason "Ask again" is not called Regenerate: Agora's
   * conversations are append-only, so nothing can replace the message he
   * sent. This puts its text back in the chat box to change and send, and
   * the edited question lands at the bottom as a new one.
   *
   * Outside the factory, and read off `window` at render time, so neither
   * `bubble.js` nor `message.js` needs `app.js` to pass it along -- its
   * byte count is a goal of its own. The sheet closes itself on the tap.
   * It replaces whatever is in the box: he picked Edit on this message. */
  window.novaEditButton = function (text) {
    var box = document.getElementById("chat-box");
    if (!box || !text) return null;
    var button = document.createElement("button");
    button.className = "ask-edit";
    button.type = "button";
    button.textContent = "Edit";
    button.title = "Put this message back in the box to change and send";
    button.addEventListener("click", function () {
      box.value = text;
      // `input` is what grows the box to fit what is now in it.
      box.dispatchEvent(new Event("input", { bubbles: true }));
      box.focus();
      box.setSelectionRange(text.length, text.length);
    });
    return button;
  };

  /* Thumbs up / thumbs down -- issue #143's last control. The rating goes to
   * `/api/chat/rate`, which keeps one row per answer in the vault
   * (nova_chat_ratings), so a later cycle can read what he thought of what
   * Nova said. The sheet closes on the tap, so what he chose is shown the
   * next time he opens it: this device remembers it, and the chosen thumb
   * reads "(yours)". Tapping it again clears the rating. A save that fails
   * is forgotten here too, so the drawer never claims a rating the vault
   * does not have. Two buttons; empty without an answer id to rate. */
  var RATED_KEY = "nova-chat-rated";
  function ratedMap() {
    try { return JSON.parse(localStorage.getItem(RATED_KEY) || "{}") || {}; } catch (e) { return {}; }
  }
  function setRated(key, rating) {
    var map = ratedMap();
    if (rating) map[key] = rating; else delete map[key];
    try { localStorage.setItem(RATED_KEY, JSON.stringify(map)); } catch (e) { /* private mode */ }
  }
  window.novaRateButtons = function (conversationId, message) {
    if (!conversationId || !message || !message.id || !message.text) return [];
    var key = conversationId + "|" + message.id;
    var current = ratedMap()[key] || null;
    return [["up", "\uD83D\uDC4D Good answer"], ["down", "\uD83D\uDC4E Bad answer"]].map(function (pair) {
      var rating = pair[0], picked = current === rating;
      var button = document.createElement("button");
      button.className = "ask-rate ask-rate-" + rating + (picked ? " ask-rate-picked" : "");
      button.type = "button";
      button.textContent = pair[1] + (picked ? " (yours)" : "");
      button.title = picked ? "Take this rating back" : "Rate this answer";
      button.addEventListener("click", function () {
        var next = picked ? null : rating;
        setRated(key, next);
        fetch("/api/chat/rate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversationId: conversationId, messageId: String(message.id),
                                 rating: next, text: String(message.text).slice(0, 300) }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) { if (!result || !result.ok) throw new Error("not saved"); })
          .catch(function () { setRated(key, current); });
      });
      return button;
    });
  };
  /* "Delete" -- issue #138's last missing control but search. One message
   * out of the thread, for good: Agora splices out that message and nothing
   * after it (`/api/conversations/message/delete`). It asks first, because
   * nothing brings it back, and the question says the one side effect: with
   * the answer under his newest question gone, that question is unanswered
   * again and gets answered again. The thread's own poll redraws it without
   * the message; a refusal is said out loud rather than swallowed. */
  window.novaDeleteButton = function (conversationId, message) {
    if (!conversationId || !message || !message.id) return null;
    var button = document.createElement("button");
    button.className = "ask-delete";
    button.type = "button";
    button.textContent = "Delete";
    button.title = "Delete this message from the conversation";
    button.addEventListener("click", function () {
      if (!window.confirm("Delete this message? It cannot be undone. "
          + "If it is the answer to your newest question, that question gets answered again.")) return;
      fetch("/api/conversations/message/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: conversationId, messageId: String(message.id) }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) window.alert((result && result.message) || "Could not delete the message.");
        })
        .catch(function () { window.alert("Could not delete the message."); });
    });
    return button;
  };
})();
