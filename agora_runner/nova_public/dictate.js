/* Dictation, shared by every box he writes to me in.
 *
 * His `ideas.md` #221: *"I do have a goal of being able to talk to you
 * instead of writing text like this."* The chat dock has had a mic since
 * Cycle 1090 and it was the only one in the app: the two comment boxes --
 * the drawer on a journal card, and the composer under a board item --
 * are where he writes the longest prose he sends me, and both were typing
 * only. This module is that mic, lifted out of `chat-dock.js` unchanged
 * rather than copied, so there is one recogniser to fix when it is wrong.
 *
 * There is no server, no model and no per-token cost -- `SpeechRecognition`
 * is the phone's own, and on iOS it is still only under the `webkit`
 * prefix. A caller reveals its button only when `supported()` is true: a
 * control that cannot do anything is worse than no control.
 */
(function () {
  "use strict";

  function Recognition() {
    return window.SpeechRecognition || window.webkitSpeechRecognition;
  }

  function supported() {
    return !!Recognition();
  }

  function speechLang() {
    return document.documentElement.lang
      || (window.navigator && window.navigator.language)
      || "en-US";
  }

  /* `button` toggles listening; `onText` receives each finished phrase;
   * `onStatus` is how a blocked microphone reaches him. Returns `{ stop }`
   * -- a caller whose box can leave the screen (a dock that shuts, a
   * drawer that closes) calls it, so the phone is never listening with no
   * pressed button in view. `stop` is a no-op on a browser with no
   * recogniser, so no caller has to branch. */
  function wire(opts) {
    var Rec = Recognition();
    var button = opts && opts.button;
    var onText = (opts && opts.onText) || function () {};
    var onStatus = (opts && opts.onStatus) || function () {};
    if (!button || !Rec) return { stop: function () {} };

    var listening = null;
    // He wants the mic on: set by his tap, cleared by his next tap or a
    // refused microphone. `listening` is only the recogniser running now.
    var wanted = false;

    function sync() {
      button.setAttribute("aria-pressed", wanted && listening ? "true" : "false");
    }

    function startListening() {
      var rec = new Rec();
      rec.lang = speechLang();
      rec.interimResults = false;
      rec.continuous = false;
      rec.onresult = function (event) {
        var said = "";
        var results = event.results || [];
        for (var i = event.resultIndex || 0; i < results.length; i += 1) {
          said += results[i][0].transcript;
        }
        said = said.trim();
        if (!said) return;
        /* Dictation lands in the box and does not send. A recogniser that
         * mishears has to be correctable before it goes out, and the box
         * is where correcting already happens -- sending on silence would
         * make every mishearing a message he cannot take back. */
        onText(said);
      };
      rec.onerror = function (event) {
        var code = event && event.error;
        /* A pause is not a failure. `no-speech` and `aborted` are what a
         * browser reports when he stops talking for a few seconds, and in
         * a meeting that is every other sentence -- the recogniser ends
         * and `onend` below starts the next one. Anything else ends the
         * session -- a blocked microphone, no network, an unsupported
         * language -- because restarting into it is a loop that fails
         * every time and never hears anything. */
        if (code === "no-speech" || code === "aborted") return;
        wanted = false;
        onStatus((code === "not-allowed" || code === "service-not-allowed"
          || code === "audio-capture") ? "the microphone is blocked" : "didn't catch that");
      };
      rec.onend = function () {
        if (listening !== rec) return;
        listening = null;
        /* A comment drawer is thrown away and rebuilt on every poll, so
         * the button this recogniser belongs to can leave the document
         * while it is still listening. Restarting into a detached button
         * would leave the phone listening with nothing pressed on screen
         * to say so -- which is the one property `stop` exists to keep. */
        if (button.isConnected === false) {
          wanted = false;
          return;
        }
        /* This recogniser runs with `continuous = false`, so the browser
         * ends it at the first pause. The mic stays on by starting a fresh
         * one until he taps it off: describing a demo in a meeting is
         * several sentences with gaps between them, and one tap per
         * sentence is not speaking instead of typing (ideas.md #140, part
         * of #134). */
        if (wanted) {
          startListening();
          return;
        }
        sync();
      };
      listening = rec;
      sync();
      try {
        rec.start();
      } catch (err) {
        // `start()` on an already-running recogniser throws; treat it as
        // not listening rather than leaving the button stuck pressed, and
        // stop wanting it so a restart cannot throw in a loop.
        listening = null;
        wanted = false;
        sync();
      }
    }

    function stop() {
      if (!wanted) return;
      wanted = false;
      if (listening) listening.stop();
      else sync();
    }

    /* A mic that stays on must not outlive the screen that shows it:
     * leaving the app turns it off. A caller that can be torn down while
     * the page stays put (a drawer, a re-rendered panel) also calls `stop`
     * itself -- this listener only covers the whole tab going away. */
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "hidden") stop();
    });

    button.removeAttribute("hidden");
    button.addEventListener("click", function () {
      if (wanted) {
        stop();
        return;
      }
      wanted = true;
      onStatus("");
      startListening();
    });

    return { stop: stop };
  }

  /* One mic button, built the same everywhere: the glyph the chat dock
   * already uses, hidden until `wire` reveals it. `make` is the caller's
   * element helper (`el`) so the button carries the page's own classes. */
  function button(el, className) {
    var node = el("button", className + " nova-mic");
    node.type = "button";
    node.title = "Speak instead of typing";
    node.setAttribute("aria-label", "Speak instead of typing");
    node.setAttribute("aria-pressed", "false");
    node.setAttribute("hidden", "");
    var glyph = el("span", "chat-glyph", "🎤");
    glyph.setAttribute("aria-hidden", "true");
    node.appendChild(glyph);
    return node;
  }

  /* Append dictated words to a textarea the way the chat dock does: one
   * space between phrases, never a newline (a board comment may not
   * contain one at all), and leave the caret where he can keep typing. */
  function appendTo(box) {
    return function (said) {
      box.value = box.value ? box.value.replace(/\s*$/, "") + " " + said : said;
      box.dispatchEvent(new Event("input", { bubbles: true }));
    };
  }

  window.novaDictation = {
    supported: supported,
    wire: wire,
    button: button,
    appendTo: appendTo,
  };
})();
