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
    if (!mine && !m.partial && props.conversationId && retry && retry.question) {
      actions.push(function () {
        return d.askRetryButton(props.conversationId, retry.question, retry.afterSend);
      });
    }
    function more() {
      d.openMessageActions(actions.map(function (make) { return make(); }));
    }
    var cls = "ask-msg " + (mine ? "ask-mine" : "ask-theirs") + (m.partial ? " ask-partial" : "");
    return html`<div class=${cls}><div class="ask-who">${when ? html`<span class="ask-when">${when}</span>` : null}<span class="ask-who-name">${mine ? "You" : m.sender || "Nova Answers"}</span></div><${Steps} ...${props} /><${RichText} text=${m.text} />${actions.length ? html`<button type="button" class="ask-more" title="Message actions" aria-label="Message actions" onClick=${more}>⋯</button>` : null}</div>`;
  }

  window.novaMessage = { Message: Message };
})();
