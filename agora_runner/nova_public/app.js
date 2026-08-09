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
  var needsEl = document.getElementById("needs");

  /** `/cycle/49` -> 49. Anything else -> null (show the whole feed). */
  function routedCycle(pathname) {
    var match = /^\/cycle\/(\d+)\/?$/.exec(pathname || "");
    return match ? parseInt(match[1], 10) : null;
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function renderSpans(parent, spans) {
    (spans || []).forEach(function (span) {
      if (span.kind === "code") parent.appendChild(el("code", null, span.text));
      else if (span.kind === "strong") parent.appendChild(el("strong", null, span.text));
      else parent.appendChild(document.createTextNode(span.text));
    });
  }

  /** Blocks -> nodes. Consecutive `li` blocks are gathered into one list;
   * the server emits them flat because a bullet run is just adjacency. */
  function renderBlocks(parent, blocks) {
    var list = null;
    (blocks || []).forEach(function (block) {
      if (block.type === "li") {
        if (!list) {
          list = el("ul");
          parent.appendChild(list);
        }
        var item = el("li");
        renderSpans(item, block.spans);
        list.appendChild(item);
        return;
      }
      list = null;
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

  function renderStatus(status) {
    statusEl.textContent = "";
    statusEl.appendChild(el("h1", "wordmark", "Nova"));

    var parts = [];
    if (status.cycle !== null && status.cycle !== undefined) parts.push("Cycle " + status.cycle);
    if (status.runningDays) parts.push("running " + status.runningDays + " days");
    if (status.lastWokeTime) parts.push("last woke " + status.lastWokeTime);
    else if (status.lastWokeDate) parts.push("last woke " + status.lastWokeDate);
    statusEl.appendChild(el("p", "status-line", parts.join(" · ")));

    if (status.lastOutcome) {
      var line = el("p", "status-sub");
      line.appendChild(el("span", outcomeClass(status.lastOutcome), status.lastOutcome));
      if (status.lastPr) line.appendChild(el("span", "status-pr", status.lastPr));
      if (status.lastOutcomeDetail) {
        line.appendChild(el("span", "status-pr", status.lastOutcomeDetail));
      }
      statusEl.appendChild(line);
    }
  }

  function renderNeeds(digest) {
    // Item 3: pinned when it has something, completely absent when it
    // doesn't -- not a box saying "Nothing".
    if (!digest || !digest.hasNeedsEdvard) {
      needsEl.hidden = true;
      return;
    }
    var body = needsEl.querySelector(".needs-body");
    body.textContent = "";
    renderBlocks(body, digest.needsEdvardBlocks);
    needsEl.hidden = false;
  }

  var nextBodyId = 0;

  /** The first paragraph of the entry, for cycles the digest never got a
   * line for -- 8 of the 57 live entries, plus every future cycle until
   * its digest line is written. A collapsed card with no summary at all
   * would be a row of dates. */
  function firstParagraph(blocks) {
    var found = (blocks || []).filter(function (block) { return block.type === "p"; })[0];
    if (!found) return "";
    return (found.spans || []).map(function (span) { return span.text; }).join("");
  }

  function renderEntry(entry, digestLine, expanded) {
    var card = el("article", "entry");
    if (entry.cycle !== null && entry.cycle !== undefined) card.id = "cycle-" + entry.cycle;

    var bodyId = "entry-body-" + nextBodyId++;

    // The whole header row is the tap target, per Edvard's "expanded/closed
    // with a tap" -- a chevron alone is a ~20px target on a phone.
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

    var stamp = [entry.date, entry.time].filter(Boolean).join(" ");
    if (stamp) toggle.appendChild(el("time", "stamp", stamp + " Oslo"));
    if (entry.outcome) {
      toggle.appendChild(el("span", outcomeClass(entry.outcome), entry.outcome));
    }
    if (entry.pr) toggle.appendChild(el("span", "pr", entry.pr));
    // The qualifier five entries carry ("stuck — CI outage, merged nothing")
    // goes beside the pill, not inside it. Nothing is dropped.
    if (entry.outcomeDetail) toggle.appendChild(el("span", "outcome-detail", entry.outcomeDetail));
    toggle.appendChild(el("span", "chevron", "▾"));

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

    if (entry.title && entry.cycle !== null && entry.cycle !== undefined) {
      card.appendChild(el("p", "entry-title", entry.title));
    }

    // Collapsed, this is the 2-3 line description. It is clamped in CSS
    // rather than cut here on purpose: the full text stays in the DOM and
    // in the page's find-in-page, and expanding is a class change rather
    // than a re-render. Nothing is ever truncated away.
    var summaryText = digestLine ? digestLine.text : firstParagraph(entry.blocks);
    if (summaryText) card.appendChild(el("p", "entry-summary", summaryText));

    var body = el("div", "entry-body");
    body.id = bodyId;
    renderBlocks(body, entry.blocks);
    card.appendChild(body);

    function setExpanded(open) {
      card.className = open ? "entry is-expanded" : "entry is-collapsed";
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }
    setExpanded(!!expanded);
    toggle.addEventListener("click", function () {
      setExpanded(toggle.getAttribute("aria-expanded") !== "true");
    });
    return card;
  }

  function render(journal, digest) {
    renderStatus(journal.status || {});
    renderNeeds(digest);

    var byCycle = {};
    ((digest && digest.lines) || []).forEach(function (line) {
      byCycle[line.cycle] = line;
    });

    var wanted = routedCycle(window.location.pathname);
    var entries = journal.entries || [];
    if (wanted !== null) {
      entries = entries.filter(function (entry) {
        return entry.cycle === wanted;
      });
    }

    feed.textContent = "";
    if (wanted !== null) {
      var back = el("a", "back", "← all cycles");
      back.href = "/";
      feed.appendChild(back);
      if (!entries.length) feed.appendChild(el("p", "empty", "No entry for cycle " + wanted + "."));
    }
    // A single cycle you navigated to deliberately opens expanded; there is
    // nothing to scan past on that page, which is the only reason to collapse.
    entries.forEach(function (entry) {
      feed.appendChild(renderEntry(entry, byCycle[entry.cycle], wanted !== null));
    });
  }

  function load() {
    Promise.all([
      fetch("/api/journal").then(function (r) { return r.json(); }),
      fetch("/api/digest").then(function (r) { return r.json(); }).catch(function () { return null; }),
    ])
      .then(function (results) { render(results[0], results[1]); })
      .catch(function (err) {
        feed.textContent = "";
        feed.appendChild(el("p", "empty", "Could not load the journal: " + err));
      });
  }

  /* The capture box (item 6). Two buttons rather than a target toggle plus
   * a submit: it is one tap fewer on a phone, which is the whole point of
   * the feature. The text is only cleared once the server confirms the
   * write -- a failed capture that wiped the box would lose the thought it
   * exists to catch. */
  (function captureBox() {
    var form = document.getElementById("capture-form");
    if (!form) return;
    var textEl = document.getElementById("capture-text");
    var captureStatus = document.getElementById("capture-status");
    var buttons = Array.prototype.slice.call(form.querySelectorAll(".capture-btn"));

    /* Edvard, issues.md 2026-08-09: "the input box for the Nova pwa is too
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

    function setStatus(text, isError) {
      captureStatus.textContent = text;
      captureStatus.className = isError ? "capture-status is-error" : "capture-status";
    }

    function send(target) {
      var text = textEl.value.trim();
      if (!text) {
        textEl.focus();
        return;
      }
      buttons.forEach(function (b) { b.disabled = true; });
      setStatus("saving…", false);
      fetch("/api/capture", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target: target, text: text }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          textEl.value = "";
          fit();
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
    // Enter sends as an issue; Shift+Enter keeps the newline, since several
    // lines become several bullets server-side.
    textEl.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && !event.shiftKey) {
        event.preventDefault();
        send("issues");
      }
    });
    textEl.addEventListener("input", fit);
    fit();
  })();

  // Back/forward between /cycle/N and / without a round trip.
  window.addEventListener("popstate", load);
  document.addEventListener("click", function (event) {
    var anchor = event.target.closest && event.target.closest("a[href^='/']");
    if (!anchor || event.metaKey || event.ctrlKey || event.shiftKey) return;
    event.preventDefault();
    history.pushState(null, "", anchor.getAttribute("href"));
    load();
    window.scrollTo(0, 0);
  });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(function () {});
  }

  load();
})();
