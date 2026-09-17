/* Mermaid diagrams in a chat message (issue #233, step 10).
 *
 * The first piece of `app.js` moved out whole rather than converted. The
 * three steps before this one each paid for their new code by condensing
 * comments in the files they touched, and that seam is mined out -- the
 * ratchet on `app.js` had one byte of room left under it. Diagrams are the
 * right first thing to leave: `app.js` reaches this file at exactly one
 * place (`appendRichText`), nothing here reads any of `app.js`'s state, and
 * the library itself is already lazy, so the whole concern travels together.
 *
 * `el` comes off `window.novaChat` at call time, the way `message.js`
 * already takes its helpers -- one definition of it, in `app.js`, rather
 * than a second copy here.
 */
(function () {
  "use strict";

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
   * reviewer found on the first version. `renderAskThread` rebuilt every
   * message from scratch, and `pollConv` calls it
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
    var el = window.novaChat.el;
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

  window.novaMermaid = { split: splitMermaidBlocks, node: mermaidNode };
})();
