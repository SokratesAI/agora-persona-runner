/* The rich-text renderer (issue #233).
 *
 * The fourteenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js`, `ask.js` and
 * `models.js`: the code that turns a comment, a journal entry or a board
 * write-up into paragraphs, lists, links, attachments and code blocks, plus
 * the scroll watcher that loads a long page as it comes into view.
 *
 * It borrows three names and hands five back. `ATTACH_RE` and `attachNode`
 * are defined just above the spot the block used to sit in `app.js`, so it
 * is bound there, in place.
 */
(function () {
  "use strict";

  window.novaRichText = function (shared) {
    var ATTACH_RE = shared.ATTACH_RE;
    var attachNode = shared.attachNode;
    var el = shared.el;

    /* Mermaid diagrams live in `mermaid.js`, loaded before this file.
     * Moved there whole in issue #233's step 10: `appendRichText` below is
     * the only place in this file that ever asked for one. */

    function appendRichText(container, paraClass, text) {
      window.novaMermaid.split(text).forEach(function (part) {
        if (part.type === "mermaid") {
          container.appendChild(window.novaMermaid.node(part.code));
          return;
        }
        appendPlainText(container, paraClass, part.text);
      });
    }

    /* Markdown in a message, his ask 2026-09-13: *"Please implement that Nova
     * can render markdown in the chat, because i can not see that table
     * properly, just lines and dashes."*
     *
     * A small renderer rather than a library: the shell is three static files
     * served off a phone connection, and the subset that actually appears in
     * these threads is tables, lists, headings, code, quotes and the four inline
     * marks. Nothing here ever touches `innerHTML` -- every node is built and
     * every string lands as a text node, so a message is still incapable of
     * injecting markup (`tests/browser/app.test.mjs` pins that).
     *
     * Attachments keep their old path: `ATTACH_RE` runs on the text of every
     * inline run, so a picture inside a list item or a table cell still draws. */
    function appendPlainText(container, paraClass, text) {
      var lines = String(text || "").split("\n");
      var i = 0;

      function isTableRule(line) {
        return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
      }
      function cellsOf(line) {
        var row = line.trim().replace(/^\|/, "").replace(/\|$/, "");
        return row.split("|").map(function (cell) { return cell.trim(); });
      }

      while (i < lines.length) {
        var line = lines[i];

        if (!line.trim()) { i += 1; continue; }

        // Fenced code. The fence language is kept out of the text on purpose:
        // mermaid is handled one layer up, in `appendRichText`.
        var fence = /^\s*```(.*)$/.exec(line);
        if (fence) {
          var code = [];
          var closed = false;
          var scan = i + 1;
          while (scan < lines.length) {
            if (/^\s*```/.test(lines[scan])) { closed = true; break; }
            code.push(lines[scan]);
            scan += 1;
          }
          /* An unfinished fence is a message still being typed, not a code
           * block: `mermaid.js`'s splitter already declines to draw one as a
           * diagram, and drawing it as code here would be the same guess in a
           * different coat. It stays the characters he typed. */
          if (!closed) {
            var typed = el("p", paraClass);
            appendInlineText(typed, lines.slice(i).join("\n"));
            container.appendChild(typed);
            i = lines.length;
            continue;
          }
          i = scan + 1;
          var pre = el("pre");
          pre.appendChild(el("code", null, code.join("\n")));
          container.appendChild(pre);
          continue;
        }

        // A table: a pipe row followed by a dashes row. Wrapped in a scroller,
        // because a five-column table does not fit a phone and a table that
        // widens the page breaks every other block on it.
        if (line.indexOf("|") !== -1 && i + 1 < lines.length && isTableRule(lines[i + 1])) {
          var head = cellsOf(line);
          i += 2;
          var table = el("table", "md-table");
          var thead = el("thead");
          var headRow = el("tr");
          head.forEach(function (cell) {
            var th = el("th");
            appendInlineText(th, cell);
            headRow.appendChild(th);
          });
          thead.appendChild(headRow);
          table.appendChild(thead);
          var tbody = el("tbody");
          while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim()) {
            var tr = el("tr");
            cellsOf(lines[i]).forEach(function (cell) {
              var td = el("td");
              appendInlineText(td, cell);
              tr.appendChild(td);
            });
            tbody.appendChild(tr);
            i += 1;
          }
          table.appendChild(tbody);
          var scroller = el("div", "md-table-scroll");
          scroller.appendChild(table);
          container.appendChild(scroller);
          continue;
        }

        var heading = /^(#{1,4})\s+(.*)$/.exec(line);
        if (heading) {
          // h4 at the shallowest: these sit inside a bubble under the page's own
          // h1, and a message must not outrank the page it is drawn on.
          var level = Math.min(6, 3 + heading[1].length);
          var h = el("h" + level, "md-heading");
          appendInlineText(h, heading[2]);
          container.appendChild(h);
          i += 1;
          continue;
        }

        if (/^\s*([-*_])\s*\1\s*\1[\s-*_]*$/.test(line)) {
          container.appendChild(el("hr", "md-rule"));
          i += 1;
          continue;
        }

        if (/^\s*>\s?/.test(line)) {
          var quoted = [];
          while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
            quoted.push(lines[i].replace(/^\s*>\s?/, ""));
            i += 1;
          }
          var quote = el("blockquote", "md-quote");
          appendPlainText(quote, paraClass, quoted.join("\n"));
          container.appendChild(quote);
          continue;
        }

        var bullet = /^\s*[-*+]\s+(.*)$/.exec(line);
        var numbered = /^\s*\d+[.)]\s+(.*)$/.exec(line);
        if (bullet || numbered) {
          var ordered = !!numbered;
          var list = el(ordered ? "ol" : "ul", "md-list");
          while (i < lines.length) {
            var item = ordered
              ? /^\s*\d+[.)]\s+(.*)$/.exec(lines[i])
              : /^\s*[-*+]\s+(.*)$/.exec(lines[i]);
            if (!item) break;
            var li = el("li");
            appendInlineText(li, item[1]);
            i += 1;
            // A wrapped line belongs to the item above it, not to a new one.
            while (i < lines.length && lines[i].trim()
                   && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])
                   && !/^\s*(#{1,4}\s|>|```)/.test(lines[i])) {
              li.appendChild(document.createTextNode(" "));
              appendInlineText(li, lines[i].trim());
              i += 1;
            }
            list.appendChild(li);
          }
          container.appendChild(list);
          continue;
        }

        // A paragraph runs until a blank line or the start of another block.
        var para = [];
        while (i < lines.length && lines[i].trim()
               && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])
               && !/^\s*(#{1,4}\s|>|```)/.test(lines[i])
               && !(lines[i].indexOf("|") !== -1 && i + 1 < lines.length && isTableRule(lines[i + 1]))) {
          para.push(lines[i]);
          i += 1;
        }
        var node = el("p", paraClass);
        appendInlineText(node, para.join("\n"));
        container.appendChild(node);
      }
    }

    /* One run of inline text: attachments first (they were here before
     * markdown and their syntax is ours, not CommonMark's), then the four
     * marks that actually appear -- code, bold, italic and links. */
    function appendInlineText(node, text) {
      var body = String(text || "");
      var last = 0;
      var match;
      ATTACH_RE.lastIndex = 0;
      while ((match = ATTACH_RE.exec(body)) !== null) {
        var before = body.slice(last, match.index);
        if (before) appendInlineMarks(node, before);
        node.appendChild(attachNode(match[3], match[2], !!match[1]));
        last = match.index + match[0].length;
      }
      var rest = body.slice(last);
      if (rest) appendInlineMarks(node, rest);
    }

    //: `code`, **bold**, *italic*, _italic_ and [label](href), in one pass so
    //: the first match wins rather than the first rule.
    var INLINE_RE = /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)|(_[^_\n]+_)|(\[[^\]\n]+\]\([^)\s]+\))/;

    function appendInlineMarks(node, text) {
      var rest = String(text || "");
      while (rest) {
        var hit = INLINE_RE.exec(rest);
        if (!hit) { node.appendChild(document.createTextNode(rest)); return; }
        if (hit.index) node.appendChild(document.createTextNode(rest.slice(0, hit.index)));  // not-prose: the run before a mark, not a clamp
        var token = hit[0];
        if (token.charAt(0) === "`") {
          node.appendChild(el("code", null, token.slice(1, -1)));
        } else if (token.slice(0, 2) === "**") {  // not-prose: reading the mark
          node.appendChild(el("strong", null, token.slice(2, -2)));
        } else if (token.charAt(0) === "*" || token.charAt(0) === "_") {
          node.appendChild(el("em", null, token.slice(1, -1)));
        } else {
          var split = token.indexOf("](");
          var link = el("a", "md-link", token.slice(1, split));
          var href = token.slice(split + 2, -1);
          /* Only http(s) and same-site paths become links. A `javascript:` href
           * is the one way a message could still run something, and it is
           * refused here rather than anywhere downstream. */
          if (/^https?:\/\//.test(href) || href.charAt(0) === "/") {
            link.setAttribute("href", href);
            if (href.charAt(0) !== "/") link.setAttribute("rel", "noopener noreferrer");
            node.appendChild(link);
          } else {
            node.appendChild(document.createTextNode(token));
          }
        }
        rest = rest.slice(hit.index + token.length);
      }
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

    return {
      appendRichText: appendRichText,
      loadWhenScrolledTo: loadWhenScrolledTo,
      renderBlocks: renderBlocks,
      renderSpans: renderSpans,
      stopScrollWatch: stopScrollWatch,
    };
  };
})();
