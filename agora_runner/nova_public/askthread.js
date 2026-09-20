/* The ask thread renderer (issue #233).
 *
 * The sixteenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js`,
 * `models.js`, `richtext.js` and `bubble.js`: `renderAskThread`, which paints
 * a whole conversation thread (the chat dock and a journal card's ask), with
 * the loop that pairs each answer with the question above it, the "lost
 * turn" note drawn when an answer never came, and the check for whether the
 * newest steps are still live. It also publishes `window.novaChat`, the
 * helpers `message.js` and `thread.js` draw a bubble with.
 *
 * It borrows seventeen names and hands one back. Every borrowed name is bound
 * above the spot the block used to sit in `app.js` (the newest are the
 * `bubble.js` ones), so it is bound there, in place.
 */
(function () {
  "use strict";

  window.novaAskThread = function (shared) {
    var OWNER_RECORD = shared.OWNER_RECORD;
    var PENDING_CLOCK_AFTER_SECONDS = shared.PENDING_CLOCK_AFTER_SECONDS;
    var appendRichText = shared.appendRichText;
    var askCopyButton = shared.askCopyButton;
    var askElapsed = shared.askElapsed;
    var askMessage = shared.askMessage;
    var askOrbit = shared.askOrbit;
    var askPending = shared.askPending;
    var askPendingSeconds = shared.askPendingSeconds;
    var askQuip = shared.askQuip;
    var askRetryButton = shared.askRetryButton;
    var chatTime = shared.chatTime;
    var el = shared.el;
    var openMessageActions = shared.openMessageActions;
    var openStepSheet = shared.openStepSheet;
    var refreshStepSheet = shared.refreshStepSheet;
    var stepMessageKey = shared.stepMessageKey;
    var stepsLabel = shared.stepsLabel;

    /* One loop for both threads. It carries the last thing he said forward so
     * every answer knows the question it came from -- the messages arrive as a
     * flat list with no reply-to on them, so position is the only link there is,
     * and reading it here is what keeps `askMessage` from needing the list. */
    function askPaintThread(put, payload, afterSend) {
      var asked = "", askedId = "", messages = payload.messages || [], newest = -1;
      // Only the newest finished message can still be answered with a tap:
      // anything said after a question -- his reply included -- has moved on.
      messages.forEach(function (message, i) { if (!message.partial) newest = i; });
      messages.forEach(function (message, i) {
        var retry = { question: asked, questionId: askedId, afterSend: afterSend };
        var answerable = i === newest && message.sender !== OWNER_RECORD;
        put(window.novaMessage && window.novaThread ? { message: message, conversationId: payload.conversationId,
          limit: payload.limit, retry: retry, answerable: answerable } : askMessage(message, payload.conversationId,
          payload.limit, retry), (message.id || message.createdAt) + message.sender,
          JSON.stringify([message, asked, askedId, payload.conversationId, payload.limit, answerable]));
        // Only his lines become the question to re-ask, and the update happens
        // after the row is built: an answer re-asks what was said *above* it,
        // and two answers in a row both point at the same question.
        if (message.sender === OWNER_RECORD && message.text) {
          asked = message.text;
          // The id of that same copy, so "Ask again" can take it back out
          // after the new one lands instead of leaving the thread with two.
          askedId = message.id ? String(message.id) : "";
        }
      });
      /* Held for `askLost`, which draws after this loop and needs the same
       * question the `⋯` menu's "Ask again" would send. One source, so the two
       * cannot disagree about what gets re-asked. */
      lastAskedQuestion = asked;
      lastAskedQuestionId = askedId;
      // Last, and outside the loop: the sheet is one node on <body> rather
      // than something inside a message, so it is repainted once against the
      // whole payload and not once per row.
      refreshStepSheet(payload);
    }

    // message.js draws a bubble with these; thread.js (Preact) keeps unchanged rows.
    window.novaChat = { owner: OWNER_RECORD, el: el, chatTime: chatTime, stepsLabel: stepsLabel,
      openStepSheet: openStepSheet, stepMessageKey: stepMessageKey, appendRichText: appendRichText,
      askCopyButton: askCopyButton, askRetryButton: askRetryButton, openMessageActions: openMessageActions,
      askPendingSeconds: askPendingSeconds, askElapsed: askElapsed, askOrbit: askOrbit,
      askQuip: askQuip, pendingClockAfter: PENDING_CLOCK_AFTER_SECONDS };
    function renderAskThread(container, payload, afterSend) {
      var rows = [], messages = payload.messages || [];
      function put(node, key, sig) { rows.push({ node: node, key: key, sig: sig }); }
      if (!messages.length) {
        put(el("p", "empty", "Ask me anything. I answer here, in a minute or so."), "empty", "");
      } else {
        askPaintThread(put, payload, afterSend);
        if (payload.waiting || tailIsWorking(messages)) {
          var lost = lostTurn(payload, messages);
          // As props, message.js draws the tail and keeps its nodes across polls.
          put(window.novaMessage && window.novaThread ? { tail: lost ? "lost" : "pending",
            progress: payload.progress, conversationId: payload.conversationId, quietSeconds: lost,
            question: lastAskedQuestion, questionId: lastAskedQuestionId,
            afterSend: afterSend } : lost ? askLost(payload.conversationId,
            lost, afterSend) : askPending(payload.progress), "tail", NaN);
        }
      }
      if (window.novaThread) return window.novaThread.render(container, rows);
      container.textContent = "";
      rows.forEach(function (r) { container.appendChild(r.node); });
    }

    /* How long a turn may go with nothing arriving before the page stops
     * claiming it is running.
     *
     * Ten minutes and not one, because a turn legitimately goes quiet: a long
     * Bash call, a subagent, a model thinking before it reaches for anything.
     * The bound is on SILENCE, not on the turn -- a turn narrating steps every
     * few seconds can run its full 45 minutes and never trip this. */
    var LOST_TURN_AFTER_SECONDS = 600;

    // The newest thing he said in the thread being painted; see `askPaintThread`.
    var lastAskedQuestion = "";
    var lastAskedQuestionId = "";

    /* Has this turn gone silent long enough to call it lost?
     *
     * His report, 2026-09-08: *"I sent you a message, got a warning on my
     * phone and you never responded and was just loading forever. Your pod
     * restarted but now you are up again."*
     *
     * The cause was a bridge rollout -- `strategy: Recreate` on a
     * ReadWriteOnce volume, so the old pod is taken down before the new one
     * starts and there is a real window with no bridge at all. That window is
     * structural and cannot be designed away here.
     *
     * What can be fixed here is the lie. The loader spins on `waiting`, and
     * `waiting` is a claim about the last message being his -- which stays
     * true forever when the turn was never picked up. So the page told him
     * something was happening for as long as he left it open. This is the
     * bound at which it stops saying that.
     *
     * Returns the seconds of silence, or 0 for "still working". Measured from
     * the newest thing in the thread rather than from his message: a turn that
     * ran for twenty minutes and then died has been silent for however long it
     * has been silent, not twenty minutes. */
    function lostTurn(payload, messages) {
      var newest = Date.parse((payload.progress && payload.progress.askedAt) || "");
      (messages || []).forEach(function (message) {
        var at = Date.parse(message.createdAt || "");
        if (!isNaN(at) && (isNaN(newest) || at > newest)) newest = at;
        (message.steps || []).forEach(function (step) {
          var stepAt = Date.parse(step.endedAt || step.at || "");
          if (!isNaN(stepAt) && (isNaN(newest) || stepAt > newest)) newest = stepAt;
        });
      });
      // No usable stamp anywhere is not evidence of silence. An older payload
      // carries none, and calling a live turn lost is the same lie pointing
      // the other way.
      if (isNaN(newest)) return 0;
      var quiet = Math.round((Date.now() - newest) / 1000);
      return quiet >= LOST_TURN_AFTER_SECONDS ? quiet : 0;
    }

    /* What the loader becomes when the turn stopped answering.
     *
     * It says the one thing he needs -- nothing is coming -- and offers the
     * one action that helps. `askRetryButton` is the same control the `⋯`
     * menu carries, so re-asking is one implementation rather than two that
     * can disagree about what gets sent. */
    function askLost(conversationId, quietSeconds, afterSend) {
      var row = el("div", "ask-msg ask-theirs ask-stopped-row");
      row.appendChild(el("div", "ask-stopped",
        "No answer came back. Nothing has arrived for "
        + Math.round(quietSeconds / 60) + " minutes, so the turn was lost."));
      var asked = lastAskedQuestion;
      if (conversationId && asked) {
        row.appendChild(askRetryButton(conversationId, asked, afterSend, lastAskedQuestionId));
      }
      return row;
    }

    /* How long a steps-only tail may go without a new step and still count as
     * a turn in flight. Generous on purpose: a single tool call can run for
     * minutes (a test suite, a CI wait), and a loader that gave up on a turn
     * that was merely thinking would be a worse lie than the one this fixes. */
    var TAIL_WORKING_WITHIN_SECONDS = 300;

    /* Is the bottom of the thread a block of work with nothing said after it?
     *
     * His question, 2026-09-08, with a screenshot of a reply followed by a
     * bare "Used 10 tools" and no loader: *"You seem to be working on
     * something, but after sending a message? There is no spinner, just some
     * tool usage that are displayed below your output. Not sure if this is by
     * design or a bug?"*
     *
     * Half of each. The row itself is deliberate -- `visible_rows` emits a
     * steps-only row for narration with no message after it, which is what
     * stops mid-turn work being drawn as a second chat bubble. The missing
     * loader is not. `payload.waiting` is computed from SETTLED messages
     * only, deliberately: it drives the poll, and a passage arriving mid-turn
     * must not read as "answered" and stop it. But the page used the same
     * flag to decide whether to draw the loader, so a thread visibly making
     * tool calls under a finished reply reported nothing at all.
     *
     * Bounded by the newest step's own timestamp, because the other case this
     * row exists for is a cycle that narrated for an hour and died. A loader
     * spinning forever over that would be the same class of untruth. Steps
     * have carried `at` since 2026-09-07, so the question is answerable here.
     */
    function tailIsWorking(messages) {
      var last = messages[messages.length - 1];
      if (!last || !last.stepsOnly) return false;
      var steps = last.steps || [];
      var newest = 0;
      steps.forEach(function (step) {
        var at = Date.parse(step.endedAt || step.at || "");
        if (!isNaN(at) && at > newest) newest = at;
      });
      // No usable stamp is not evidence of staleness -- an older payload
      // carries none at all, and the block is still the newest thing here.
      if (!newest) return true;
      return (Date.now() - newest) / 1000 <= TAIL_WORKING_WITHIN_SECONDS;
    }

    return {
      renderAskThread: renderAskThread,
    };
  };
})();
