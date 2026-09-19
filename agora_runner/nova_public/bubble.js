/* The chat bubble renderer (issue #233).
 *
 * The fifteenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js`,
 * `models.js` and `richtext.js`: `askMessage`, which draws one message in
 * the dock, a conversation thread or a journal card's ask, and `askPending`,
 * the loader under a question that has no answer yet, with the clock and
 * the orbit it draws.
 *
 * It borrows eight names and hands seven back. It is bound in place, after
 * `ask.js` and `steps.js`, because it reads helpers from both. `ask.js` in
 * turn reads `askMessage` and `askPending`, so `app.js` hands it call-time
 * wrappers rather than the values, which do not exist yet when it is bound.
 */
(function () {
  "use strict";

  window.novaBubble = function (shared) {
    var OWNER_RECORD = shared.OWNER_RECORD;
    var appendRichText = shared.appendRichText;
    var askCopyButton = shared.askCopyButton;
    var askRetryButton = shared.askRetryButton;
    var el = shared.el;
    var openMessageActions = shared.openMessageActions;
    var stepMessageKey = shared.stepMessageKey;
    var stepsLine = shared.stepsLine;

    /* A message's clock time as `HH:MM`, or "" when the server did not date it.
     *
     * 24-hour explicitly rather than by locale: he asked for 24h, and
     * `toLocaleTimeString` with no `hourCycle` answers whatever the phone's
     * locale happens to say, which is a guess that is right until it is not.
     * No seconds -- `stepTime` in the step drawer carries them because six
     * tool calls inside one minute is the ordinary case there, and two chat
     * messages inside one minute is not.
     *
     * An undated message gets no stamp at all rather than a placeholder: the
     * folded `stepsOnly` rows carry an empty `createdAt` by construction, and
     * a bubble reading `--:--` says a clock is broken when nothing is. */
    function chatTime(at) {
      if (!at) return "";
      var ms = Date.parse(at);
      if (isNaN(ms)) return "";
      return new Date(ms).toLocaleTimeString(undefined, {
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      });
    }

    /* `conversationId` is what the drawer's detail view asks the server with.
     * It is threaded through rather than read off a module variable because
     * three surfaces render this -- the dock, a conversation thread and the
     * journal card's ask -- and only one of them has a module variable to
     * read. */
    function askMessage(message, conversationId, limit, retry) {
      var steps = stepsLine(conversationId, message.steps, limit, stepMessageKey(message));
      /* A row that is only the work behind an answer still being written.
       * It is not a bubble: nothing has been said yet, and drawing it as one
       * is the thing he asked me to stop doing. */
      if (message.stepsOnly) {
        var working = el("div", "ask-msg-steps");
        if (steps) working.appendChild(steps);
        return working;
      }
      var mine = message.sender === OWNER_RECORD;
      var row = el("div", "ask-msg " + (mine ? "ask-mine" : "ask-theirs")
        + (message.partial ? " ask-partial" : ""));
      var who = el("div", "ask-who");
      // Why each piece is where it is: message.js, which draws this in the thread.
      var when = chatTime(message.createdAt);
      if (when) who.appendChild(el("span", "ask-when", when));
      who.appendChild(el("span", "ask-who-name",
        mine ? "You" : message.sender || "Nova Answers"));
      row.appendChild(who);
      /* Above the prose, the way Claude mobile puts it above the paragraph the
       * tool call led to -- and the way it happened. */
      if (steps) row.appendChild(steps);
      var body = el("div", "ask-text");
      appendRichText(body, null, message.text);
      row.appendChild(body);
      var actions = [];
      if (message.text) actions.push(function () { return askCopyButton(message.text); });
      if (mine && message.text && window.novaEditButton && document.getElementById("chat-box")) {
        actions.push(function () { return window.novaEditButton(message.text); });
      }
      if (!mine && !message.partial && window.novaRateButtons) {
        // Built when the drawer opens, so each opening reads the current rating.
        [0, 1].forEach(function (i) { if (message.id && message.text && conversationId) actions.push(function () { return window.novaRateButtons(conversationId, message)[i]; }); });
      }
      if (!mine && !message.partial && conversationId && retry && retry.question) {
        actions.push(function () {
          return askRetryButton(conversationId, retry.question, retry.afterSend);
        });
      }
      if (!message.partial && message.id && conversationId && window.novaDeleteButton) {
        actions.push(function () { return window.novaDeleteButton(conversationId, message); });
      }
      if (actions.length) {
        var more = el("button", "ask-more", "\u22EF");
        more.type = "button";
        more.title = "Message actions";
        more.setAttribute("aria-label", "Message actions");
        more.addEventListener("click", function () {
          openMessageActions(actions.map(function (make) { return make(); }));
        });
        row.appendChild(more);
      }
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

    /* How long a turn runs before the bubble puts a number on it.
     *
     * His ask, 2026-09-07: *"the 'thinking' text can also go. Only the loading
     * css can be there and if it has run for more than 20 seconds maybe the
     * counter can show."*
     *
     * The word "Thinking…" said what the loader now says by existing. The
     * clock is different: it is there because of his issue #143 -- *"I have no
     * idea if it broke or if its working, so i might wait forever for no
     * response"* -- so it is not deleted, it is held back until the wait is
     * long enough to be the thing he is actually asking about. Under twenty
     * seconds an answer is simply on its way; past it, a number is the
     * difference between waiting and wondering. */
    var PENDING_CLOCK_AFTER_SECONDS = 20;

    function askPendingSeconds(askedAt) {
      if (!askedAt) return null;
      var started = Date.parse(askedAt);
      if (isNaN(started)) return null;
      return Math.max(0, Math.round((Date.now() - started) / 1000));
    }

    /* The loader, and the reason it is built here rather than left to CSS
     * alone.
     *
     * His report, 2026-09-07: *"I want the planets to circle continuously
     * around, not stopping midway and restarting at the top as they do now."*
     * The keyframes were never the problem -- `0deg -> 360deg` linear is
     * already seamless. The element's lifetime was: `renderAskThread` clears
     * the thread and rebuilds every row on each poll, `ASK_POLL_MS` is 4000,
     * and an orbit of 7s or 11s therefore never finished one. Every four
     * seconds the node was thrown away and a new one started at the top,
     * which is exactly the restart he was watching.
     *
     * So the phase is computed rather than owned by the element: a negative
     * `animation-delay` starts a freshly built body at the angle it would
     * have been at had it been turning since `ORBIT_EPOCH`. Two nodes built
     * four seconds apart are then at the same angle to the millisecond, and
     * there is no frame at which anything jumps -- the animation is
     * continuous across a node that is not.
     *
     * The durations live here rather than in the stylesheet because the delay
     * has to be taken modulo the period, and one of the two has to be the
     * source. `animation-name` and the timing function stay in CSS, so the
     * reduced-motion block's `animation: none` still wins -- inline duration
     * and delay cannot reintroduce a name it has switched off.
     *
     * Inner faster than outer, at his ask, which is also the way real orbits
     * go: a shorter radius is a shorter year. Same direction for both, for
     * the same reason -- the two periods are unrelated enough that they drift
     * apart on their own without being sent opposite ways. */
    var ORBIT_EPOCH = Date.now();
    var ORBIT_OUTER_SECONDS = 12;
    var ORBIT_INNER_SECONDS = 4.5;
    var ORBIT_PULSE_SECONDS = 3.4;

    function orbitPhase(node, seconds) {
      node.style.animationDuration = seconds + "s";
      // Modulo the period, so the delay stays small however long the tab has
      // been open -- a delay of minus four hours is legal and works, but it
      // is not a number anything reading this element should have to hold.
      var elapsed = ((Date.now() - ORBIT_EPOCH) / 1000) % seconds;
      node.style.animationDelay = "-" + elapsed.toFixed(3) + "s";
      return node;
    }

    function askOrbit() {
      var orbit = el("div", "ask-orbit");
      orbit.setAttribute("aria-hidden", "true");
      orbit.appendChild(orbitPhase(el("span", "ask-orbit-core"), ORBIT_PULSE_SECONDS));
      orbit.appendChild(orbitPhase(
        el("span", "ask-orbit-body ask-orbit-body-a"), ORBIT_OUTER_SECONDS));
      orbit.appendChild(orbitPhase(
        el("span", "ask-orbit-body ask-orbit-body-b"), ORBIT_INNER_SECONDS));
      return orbit;
    }

    /* A line in Nova's own voice beside the orbit (issue #142).
     *
     * His capture, 2026-08-30: *"The 'thinking' box while Nova works is
     * static and feels dead -- wanted a dynamic, Nova-personality
     * acknowledgement (supernova/cosmos theme, humor, made-up words, e.g.
     * 'Expanding...' or 'Got it, give me a parsec to work it out')"*. The
     * static word "Thinking…" went on 09-07; this is the living line that
     * replaces it, not the dead one coming back.
     *
     * The first few seconds say the question landed, which is the half of
     * the wait he asked about in #143; after that the line changes every
     * `QUIP_SECONDS`. It is chosen from the clock rather than at random,
     * because the bubble is rebuilt on every four-second poll and a random
     * pick would flicker to a new line on each one. The question's own
     * `askedAt` seeds it, so two questions do not recite the same order. */
    var QUIP_ACK_SECONDS = 6;
    var QUIP_SECONDS = 7;
    var QUIP_ACKS = [
      "Got it — give me a parsec to work it out.",
      "Heard you across the void. On it.",
      "Caught it. Plotting a course…",
      "Received. Spinning up the stardrive…",
    ];
    var QUIPS = [
      "Expanding…",
      "Supernovating…",
      "Consulting the nebula…",
      "Gathering stardust…",
      "Folding a little spacetime…",
      "Cosmoodling…",
      "Negotiating with gravity…",
      "Aligning the planets…",
      "Stellarifying the details…",
      "Listening to the pulsars…",
      "Orbiting the question…",
      "Brewing a small galaxy…",
      "Counting moons, carrying the one…",
      "Starweaving…",
    ];

    function askQuip(askedAt) {
      var seed = 0;
      for (var i = 0; askedAt && i < askedAt.length; i++) seed += askedAt.charCodeAt(i);
      var secs = askPendingSeconds(askedAt);
      if (secs === null || secs < QUIP_ACK_SECONDS) return QUIP_ACKS[seed % QUIP_ACKS.length];
      return QUIPS[(seed + Math.floor((secs - QUIP_ACK_SECONDS) / QUIP_SECONDS)) % QUIPS.length];
    }

    function askPending(progress) {
      var row = el("div", "ask-msg ask-theirs ask-pending");
      var secs = askPendingSeconds(progress && progress.askedAt);
      if (secs !== null && secs >= PENDING_CLOCK_AFTER_SECONDS) {
        row.appendChild(el("div", "ask-pending-head",
          askElapsed(progress.askedAt)));
      }
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
        /* Before the first tool call there is nothing true to say about what
         * it is doing, and the sentence that used to sit here -- "Your
         * question is in. No tool calls yet." -- spent two lines saying that.
         * His ask, 2026-09-07: a loader instead, in the galaxy's language.
         *
         * The same thing `/galaxy` draws, in miniature: a violet-white core
         * that pulses because the loop is running either way, and two bodies
         * orbiting it. CSS rather than a second canvas -- this sits in a
         * message bubble, it repaints on a four-second poll, and a canvas per
         * pending turn would be an animation frame loop with no off switch. */
        row.appendChild(askOrbit());
        row.appendChild(el("div", "ask-pending-quip", askQuip(progress && progress.askedAt)));
      }
      return row;
    }

    return {
      PENDING_CLOCK_AFTER_SECONDS: PENDING_CLOCK_AFTER_SECONDS,
      askElapsed: askElapsed,
      askMessage: askMessage,
      askOrbit: askOrbit,
      askPending: askPending,
      askPendingSeconds: askPendingSeconds,
      askQuip: askQuip,
      chatTime: chatTime,
    };
  };
})();
