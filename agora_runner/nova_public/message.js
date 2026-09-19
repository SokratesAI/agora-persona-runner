/* One chat message, as a Preact component (issue #233, step 3; ADR 0010).
 *
 * Until this, `askMessage` in `app.js` built every bubble by hand and
 * `thread.js` only decided which of those hand-built nodes to keep. The
 * bubble itself is declared here now, and `thread.js` renders it directly.
 * `askMessage` stays in `app.js` for the two places that paint a bubble
 * straight into the thread (the optimistic send) and for a page where
 * Preact did not load.
 *
 * The helpers a bubble needs -- the step drawer, rich text, the copy and
 * re-ask buttons, the actions drawer -- still live in `app.js`, which hands
 * them over as `window.novaChat` once it has loaded. They are read at render
 * time, never at load, because this file loads first.
 *
 * What the bubble shows, and why, is unchanged:
 * - The clock time first, top left of the bubble, 24h -- his capture,
 *   `issues.md` 2026-09-16: *"A small 24h timestamp in the top left of each
 *   bubble."* An undated message gets no stamp rather than `--:--`.
 * - The steps line above the prose, the way it happened.
 * - Copy and "Ask again" open from a `⋯` at the bottom right -- his ask,
 *   2026-09-07, replacing a press-and-hold whose hit area he had to hunt
 *   for. No Copy on a message with no text (an empty clipboard reads as
 *   broken), no re-ask unless there is a finished answer with a question
 *   above it, and no button at all when neither applies.
 * - A row that is only the work behind an answer still being written is not
 *   a bubble: nothing has been said yet.
 */
(function () {
  "use strict";
  var P = window.htmPreact;
  if (!P || !P.html) return;
  var html = P.html;

  /* Rich text is built by `appendRichText`, which writes DOM, so this owns
   * an empty `.ask-text` and fills it after Preact has placed it. It redraws
   * only when the text changes. */
  function RichText(props) { P.Component.call(this, props); }
  RichText.prototype = Object.create(P.Component.prototype);
  RichText.prototype.constructor = RichText;
  RichText.prototype.shouldComponentUpdate = function (next) {
    return next.text !== this.props.text;
  };
  RichText.prototype.render = function () { return html`<div class="ask-text"></div>`; };
  function fill() {
    this.base.textContent = "";
    window.novaChat.appendRichText(this.base, null, this.props.text);
  }
  RichText.prototype.componentDidMount = fill;
  RichText.prototype.componentDidUpdate = fill;

  function Steps(props) {
    var d = window.novaChat, steps = props.message.steps;
    if (!steps || !steps.length) return null;
    var label = d.stepsLabel(steps);
    function open() {
      d.openStepSheet(props.conversationId, steps, props.limit, d.stepMessageKey(props.message));
    }
    return html`<button type="button" class="ask-steps" aria-label=${label + " — open the details"} onClick=${open}><span class="ask-steps-label">${label}</span><span class="ask-steps-chev">›</span></button>`;
  }

  function Message(props) {
    var d = window.novaChat, m = props.message, retry = props.retry;
    if (m.stepsOnly) return html`<div class="ask-msg-steps"><${Steps} ...${props} /></div>`;
    var mine = m.sender === d.owner;
    var when = d.chatTime(m.createdAt);
    var actions = [];
    if (m.text) actions.push(function () { return d.askCopyButton(m.text); });
    if (mine && m.text && window.novaEditButton && document.getElementById("chat-box")) {
      actions.push(function () { return window.novaEditButton(m.text); });
    }
    if (!mine && !m.partial && window.novaRateButtons) {
      // Built when the drawer opens, so each opening reads the current rating.
      [0, 1].forEach(function (i) { if (m.id && m.text && props.conversationId) actions.push(function () { return window.novaRateButtons(props.conversationId, m)[i]; }); });
    }
    if (!mine && !m.partial && props.conversationId && retry && retry.question) {
      actions.push(function () {
        return d.askRetryButton(props.conversationId, retry.question, retry.afterSend);
      });
    }
    if (!m.partial && m.id && props.conversationId && window.novaDeleteButton) {
      actions.push(function () { return window.novaDeleteButton(props.conversationId, m); });
    }
    function more() {
      d.openMessageActions(actions.map(function (make) { return make(); }));
    }
    var cls = "ask-msg " + (mine ? "ask-mine" : "ask-theirs") + (m.partial ? " ask-partial" : "");
    var options = !mine && m.options && m.options.length && props.conversationId ? m.options : null;
    var live = !!props.answerable;
    return html`<div class=${cls}><div class="ask-who">${when ? html`<span class="ask-when">${when}</span>` : null}<span class="ask-who-name">${mine ? "You" : m.sender || "Nova Answers"}</span></div><${Steps} ...${props} /><${RichText} text=${m.text} />${options ? html`<${Slot} sig=${JSON.stringify([props.conversationId, options, live])} make=${function () { return optionButtons(props.conversationId, options, live, retry && retry.afterSend); }} />` : null}${actions.length ? html`<button type="button" class="ask-more" title="Message actions" aria-label="Message actions" onClick=${more}>⋯</button>` : null}</div>`;
  }

  /* The answers a persona offered with its question, one button each (idea
   * #164, slice 2). A tap sends the label as his reply, exactly as if he had
   * typed it, so the persona needs nothing new to read it. Only the newest
   * message's buttons are live; once anything has been said after it they
   * stay on screen greyed, as a record of what was offered. Built by hand
   * inside a `Slot` so a tap's disabled state survives the four-second poll. */
  function optionButtons(conversationId, options, live, afterSend) {
    var el = window.novaChat.el, row = el("div", "ask-options" + (live ? "" : " ask-options-spent"));
    var buttons = options.map(function (label) {
      var button = el("button", "ask-option", label);
      button.type = "button";
      button.disabled = !live;
      button.addEventListener("click", function () {
        if (button.disabled) return;
        // The whole row goes quiet on the first tap: two answers to one
        // question are two turns, and the second contradicts the first.
        buttons.forEach(function (b) { b.disabled = true; });
        button.classList.add("ask-option-picked");
        fetch("/api/conversations/send", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversationId: conversationId, text: label }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
            if (afterSend) afterSend(label);
          })
          .catch(function () {
            // Nothing was sent, so the choice is his to make again.
            buttons.forEach(function (b) { b.disabled = false; });
            button.classList.remove("ask-option-picked");
          });
      });
      row.appendChild(button);
      return button;
    });
    return row;
  }

  /* A slot that app.js fills with one hand-built node, once, and keeps.
   * Redrawn only when `sig` changes: the orbit's animation and an "Ask again"
   * mid-send ("sending…", disabled) must survive the four-second poll, which
   * used to swap both for fresh nodes. */
  function Slot(props) { P.Component.call(this, props); }
  Slot.prototype = Object.create(P.Component.prototype);
  Slot.prototype.constructor = Slot;
  Slot.prototype.shouldComponentUpdate = function (next) { return next.sig !== this.props.sig; };
  Slot.prototype.render = function () { return html`<span class="thread-slot"></span>`; };
  function place() {
    this.base.textContent = "";
    this.base.appendChild(this.props.make());
  }
  Slot.prototype.componentDidMount = place;
  Slot.prototype.componentDidUpdate = place;

  /* The bottom of a thread whose turn is still owed (step 4 of #233): the
   * loader while it runs -- the clock after `pendingClockAfter` seconds, the
   * newest tool call once there is one, the orbit before that -- and, once
   * nothing has arrived for too long, the card saying the turn was lost with
   * the same "Ask again" the `⋯` menu carries. Same classes askPending and
   * askLost build by hand; those stay for a page where Preact did not load. */
  function Tail(props) {
    var d = window.novaChat, p = props.progress, latest = p && p.latest;
    if (props.tail === "lost") {
      var id = props.conversationId, q = props.question;
      return html`<div class="ask-msg ask-theirs ask-stopped-row"><div class="ask-stopped">${"No answer came back. Nothing has arrived for " + Math.round(props.quietSeconds / 60) + " minutes, so the turn was lost."}</div>${id && q ? html`<${Slot} sig=${id + "\n" + q} make=${function () { return d.askRetryButton(id, q, props.afterSend); }} />` : null}</div>`;
    }
    var secs = d.askPendingSeconds(p && p.askedAt);
    return html`<div class="ask-msg ask-theirs ask-pending">${secs !== null && secs >= d.pendingClockAfter ? html`<div class="ask-pending-head">${d.askElapsed(p.askedAt)}</div>` : null}${latest ? html`<div class="ask-pending-step"><span class="ask-pending-tool">${latest.capability}</span>${latest.detail ? html`<span class="ask-pending-detail">${latest.detail}</span>` : null}</div>` : html`<${Slot} sig="orbit" make=${d.askOrbit} />`}${latest && p.steps > 1 ? html`<div class="ask-pending-count">${p.steps + " steps so far"}</div>` : null}</div>`;
  }

  window.novaMessage = { Message: Message, Tail: Tail };
})();
