/* The diagnostics page and the settings page (issue #233, step 13).
 *
 * The fifth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js` and `charts.js`. The two pages travel
 * together because they are the same page to the owner -- `/diag` reports
 * what his device says about itself and `/settings` is the one control
 * that changes it, the light/dark preference, and `renderSettings` is the
 * only other caller of the theme helpers `/diag` reads.
 *
 * What it needs from `app.js` arrives as one argument, the same seam
 * `chat-dock.js` and `charts.js` use and for the same reason: the list is
 * something somebody has to add to on purpose, and a name missing from it
 * is a `ReferenceError` on the first line that uses it rather than a page
 * that half draws. `app.js` calls this at the point in its own body where
 * the diagnostics page used to be defined, and takes back the two names
 * the router still reaches, `renderDiag` and `renderSettings`.
 *
 * Nothing here is called at boot. The theme the app starts in is applied
 * by the inline script in `index.html`, deliberately, so the first paint
 * is already the right colour; `applyTheme` in this file only keeps that
 * in step after the owner presses one of the three buttons.
 */
(function () {
  "use strict";

  window.novaDiag = function (shared) {
    var PRIORITIES = shared.PRIORITIES;
    var el = shared.el;
    var feed = shared.feed;
    var getPrioMenuOverlay = shared.getPrioMenuOverlay;
    var markNav = shared.markNav;
    var menuOpen = shared.menuOpen;
    var navEl = shared.navEl;
    var setMenu = shared.setMenu;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* `/diag` -- what the owner's own device reports about itself.
     *
     * Three cycles running have now shipped a fix for a rendering fault on a
     * phone none of them could look at. Cycle 299 attributed a missing
     * hamburger to an iPhone notch and shipped `env(safe-area-inset-top)`;
     * the cycle after it shipped `translateZ(0)` on a Chromium
     * compositor-bug theory; and then a capture landed saying he is on an
     * Android Galaxy S25 and a Windows desktop, so the first of those
     * targeted a platform he does not own. None of that was careless -- the
     * loop simply has no instrument pointed at his hardware. Cycle 303 drove
     * headless Chromium over the live site at six widths from 320 to 412 CSS
     * px with S25 metrics and a Samsung user agent: the button held
     * [x, 26, 40, 40] at every one of them and the priority popup centred
     * inside the viewport at every one of them. Reproducing nothing is the
     * measurement, and it says the variable is on his device, not in a width.
     *
     * So this page is guess number four's replacement rather than guess
     * number four. It fetches nothing -- every value on it is read from the
     * browser that is drawing it -- and `Send this to Nova` files the lot as
     * one note, which is a file step 1a already opens on every wake. The
     * one-line join is not cosmetic: `nova_capture.clean_capture_text` turns
     * each newline into its own bullet, so a multi-line report would land as
     * fourteen separate notes. */

    /** Resolved `env(safe-area-inset-*)`, in `top right bottom left` order.
     *
     * Read off a throwaway fixed element rather than `--shell-top`, because a
     * custom property computes to its unresolved token (`max(1.6rem, ...)`)
     * in every engine -- the padding it feeds is the only place the number
     * exists.
     *
     * The support test is `CSS.supports` rather than a look at the numbers,
     * and the reviewer is why. Reading the probe alone cannot answer the
     * question this row exists for: an engine that does not understand
     * `env()` drops the declaration and leaves the initial `0px`, which is
     * byte-identical to an engine that understands it perfectly and has no
     * notch to report. So "0px 0px 0px 0px" would have been printed in both
     * cases, and the case that matters -- the one that would explain Cycle
     * 299's fix doing nothing on his phone -- was unreachable. That is a
     * negative result guaranteed in advance, on the page built to stop
     * exactly that. */
    function safeAreaInsets() {
      var supported = !!(window.CSS && window.CSS.supports
        && window.CSS.supports("padding-top", "env(safe-area-inset-top, 0px)"));
      if (!supported) return "env() unsupported by this browser";
      var probe = document.createElement("div");
      probe.style.position = "fixed";
      probe.style.visibility = "hidden";
      probe.style.pointerEvents = "none";
      probe.style.top = "0";
      probe.style.left = "0";
      probe.style.paddingTop = "env(safe-area-inset-top, 0px)";
      probe.style.paddingRight = "env(safe-area-inset-right, 0px)";
      probe.style.paddingBottom = "env(safe-area-inset-bottom, 0px)";
      probe.style.paddingLeft = "env(safe-area-inset-left, 0px)";
      document.body.appendChild(probe);
      var cs = window.getComputedStyle(probe);
      var sides = [cs.paddingTop, cs.paddingRight, cs.paddingBottom, cs.paddingLeft];
      document.body.removeChild(probe);
      if (sides.some(function (v) { return !v; })) return "unsupported";
      return sides.join(" ");
    }

    /** The hamburger's live box and every property that could hide it.
     *
     * Sampled twice -- once on paint and once three seconds later -- because
     * what he reported is "I see it 1 sec when I open or refresh the app and
     * then it vanishes", and a single reading cannot tell a button that was
     * never drawn from one that was drawn and then lost. */
    function menuBtnReport() {
      var node = document.getElementById("menu-btn");
      if (!node) return "not in the DOM";
      var box = node.getBoundingClientRect();
      var cs = window.getComputedStyle(node);
      return "at " + Math.round(box.left) + "," + Math.round(box.top)
        + " sized " + Math.round(box.width) + "x" + Math.round(box.height)
        + ", visibility " + cs.visibility
        + ", opacity " + cs.opacity
        + ", display " + cs.display
        + ", z-index " + cs.zIndex
        + ", transform " + cs.transform;
    }

    /** A node's live box, judged against the viewport it is drawn in.
     *
     * `menuBtnReport` above answers "is the button there"; this answers "is
     * this thing where it should be", which is the other half of the S25
     * report and the half no instrument in this loop has ever measured. It
     * names the overflow per edge rather than printing a bare rect, because
     * "out of place" is a direction and a distance, and a rect leaves the
     * reader to subtract.
     *
     * The visual-viewport clause is the reading I would actually bet on.
     * Both dropdowns are `position: fixed`, which pins them to the *layout*
     * viewport — so a pinch-zoom, or Android's keyboard, or a URL bar that
     * has not settled, moves what he sees without moving anything CSS knows
     * about. Headless Chromium has a visual viewport identical to its
     * layout viewport at every width, which is exactly why Cycle 303 could
     * drive six widths and reproduce nothing. */
    function boxReport(node) {
      if (!node) return "not in the DOM";
      var box = node.getBoundingClientRect();
      var cs = window.getComputedStyle(node);
      var vw = document.documentElement.clientWidth;
      var vh = document.documentElement.clientHeight;
      var over = [];
      if (box.left < -0.5) over.push("left by " + Math.round(-box.left));
      if (box.top < -0.5) over.push("top by " + Math.round(-box.top));
      if (box.right > vw + 0.5) over.push("right by " + Math.round(box.right - vw));
      if (box.bottom > vh + 0.5) over.push("bottom by " + Math.round(box.bottom - vh));
      var out = "at " + Math.round(box.left) + "," + Math.round(box.top)
        + " sized " + Math.round(box.width) + "x" + Math.round(box.height)
        + " in a " + vw + "x" + vh + " viewport"
        + ", display " + cs.display
        + ", visibility " + cs.visibility
        + ", transform " + cs.transform
        + (over.length ? ", OUTSIDE VIEWPORT: " + over.join(" and ") : ", fully inside");
      var vv = window.visualViewport;
      if (vv) {
        // What he is looking at, versus what CSS positioned. Equal on every
        // desktop and on headless Chromium; the gap is the whole point.
        var seenX = Math.round((box.left + box.width / 2) - (vv.offsetLeft + vv.width / 2));
        var seenY = Math.round((box.top + box.height / 2) - (vv.offsetTop + vv.height / 2));
        out += ", centre offset from visual viewport " + seenX + "," + seenY
          + " (scale " + vv.scale + ")";
      }
      return out;
    }

    /** Open the drawer, measure it once the slide has finished, put it back.
     *
     * Measuring it closed would report the parked box — `translateX(100%)`,
     * off the right edge — which reads as a spectacular fault every time
     * and is simply the drawer being shut. So this opens the real element
     * rather than a copy, waits out the 220ms transform transition with a
     * margin, and restores the exact state it found. The brief slide is
     * visible and the lede says so; a measurement he cannot see happening
     * is not obviously better than one he can. */
    function measureDrawer(write) {
      var was = menuOpen();
      setMenu(true);
      window.setTimeout(function () {
        /* `finally`, because this one mutates app-wide singletons rather
         * than a node the next navigation throws away. A throw between the
         * open and the restore leaves the drawer out, the scrim dimming the
         * whole app and `body.nav-open` holding the scroll lock, on every
         * page, until he reloads -- a page-wide lockup caused by the
         * diagnostic page, which is a strictly worse outcome than the
         * missing reading. Reviewer's finding.
         *
         * The state is reported beside the box, not assumed from the label.
         * Every number here is meaningless if the drawer was shut when it
         * was taken, and nothing else in the line would say so -- the parked
         * box is a perfectly ordinary-looking rect off the right edge. This
         * is the same discipline as the `CSS.supports` check on the
         * safe-area row: a reading that cannot distinguish its own
         * precondition is not evidence. */
        try {
          write(boxReport(navEl) + ", drawer was "
            + (menuOpen() ? "open" : "SHUT — this is the parked box, not the drawn one"));
        } finally {
          setMenu(was);
        }
      }, 350);
    }

    /** Same, for the centred priority popup.
     *
     * Populated with the real options before measuring: an empty `.prio-menu`
     * is a 17px-tall box, and its height against `max-height: 70vh` is one of
     * the few ways this thing could genuinely land wrong. The overlay is
     * shared with every picker on the page, so this hands it back emptied and
     * hidden — `openMenu` rebuilds the list from scratch on every open, so a
     * cleared overlay is the state it already expects. */
    function measurePrioMenu(write) {
      var menu = getPrioMenuOverlay();
      /* If a real picker got there first, leave it alone and say so.
       *
       * The capture box sits on this page too, so he can tap its priority
       * button inside the ~650ms before this runs. Without this guard the
       * measurement would empty the overlay under a picker he had just
       * opened, repopulate it with dead options carrying no click handlers,
       * then hide the whole thing -- his popup vanishing on its own, the
       * trigger still reading `aria-expanded="true"`, and no way to tell
       * from the note that it happened. A skipped reading he can retake by
       * reloading is worth more than a reading taken by breaking the page
       * under him. */
      if (!menu.hidden) {
        write("skipped — a priority picker was already open; reload the page to measure it");
        return;
      }
      menu.textContent = "";
      PRIORITIES.forEach(function (label) {
        var item = el("button", "prio-option", label || "– Unrated");
        item.type = "button";
        item.setAttribute("role", "option");
        menu.appendChild(item);
      });
      menu.hidden = false;
      try {
        write(boxReport(menu) + ", popup was " + (menu.hidden ? "HIDDEN — not the drawn box" : "open")
          + ", " + menu.children.length + " options");
      } finally {
        // Same reason as the drawer: a throw here would strand this overlay
        // centred over every page of the app until he reloads.
        menu.hidden = true;
        menu.textContent = "";
      }
    }

    function displayMode() {
      if (!window.matchMedia) return "matchMedia unsupported";
      var modes = ["standalone", "fullscreen", "minimal-ui", "browser"];
      for (var i = 0; i < modes.length; i += 1) {
        if (window.matchMedia("(display-mode: " + modes[i] + ")").matches) return modes[i];
      }
      return "unknown";
    }

    /** Every reading, as `[label, value]` pairs. One place, so the table on
     * screen and the note that gets sent can never drift apart. */
    function diagRows() {
      var doc = document.documentElement;
      var docStyle = window.getComputedStyle(doc);
      var header = document.getElementById("status");
      var vv = window.visualViewport;
      return [
        ["User agent", navigator.userAgent],
        ["Display mode", displayMode()],
        ["Window", window.innerWidth + " x " + window.innerHeight + " CSS px"],
        ["Device pixel ratio", String(window.devicePixelRatio)],
        ["Screen", (window.screen ? window.screen.width + " x " + window.screen.height : "unknown")
          + (window.screen && window.screen.orientation ? ", " + window.screen.orientation.type : "")],
        ["Visual viewport", vv
          ? Math.round(vv.width) + " x " + Math.round(vv.height)
            + ", offset top " + Math.round(vv.offsetTop) + ", scale " + vv.scale
          : "unsupported"],
        ["Safe-area insets", safeAreaInsets() + " (top right bottom left)"],
        ["Header top padding", header ? window.getComputedStyle(header).paddingTop : "no header"],
        ["Root font size", docStyle.fontSize],
        ["Colour scheme", window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark" : "light"],
        ["Hamburger, on paint", menuBtnReport()],
      ];
    }

    /* The theme preference this browser holds: "light", "dark" or "system".
     * Stored per device rather than per account on purpose -- he reads Nova on
     * a phone in bed and on a desktop in daylight, and those two want different
     * answers. */
    function themePreference() {
      try {
        var stored = localStorage.getItem("nova-theme");
        if (stored === "light" || stored === "dark") return stored;
      } catch (e) { /* private mode: fall through to the device */ }
      return "system";
    }

    /** What "system" resolves to right now. */
    function deviceTheme() {
      var query = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
      return query && query.matches ? "light" : "dark";
    }

    /* Paint one preference. `data-theme` is only ever the effective palette --
     * see the boot script in index.html for why the preference and the palette
     * are two different values.
     *
     * The `theme-color` meta goes with it, because on Android the browser
     * paints the status bar from it: leaving it at the dark value puts a black
     * strip above a white page. */
    function applyTheme(preference) {
      var effective = preference === "system" ? deviceTheme() : preference;
      document.documentElement.setAttribute("data-theme", effective);
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", paletteById(palettePreference())[effective]);
      return effective;
    }

    /* Colour palettes, issue #136. The second axis beside light/dark: each
     * one answers both, in style.css under `:root[data-palette=...]`. `dot`
     * is the accent drawn on the button, and `dark`/`light` are the page
     * colours for the status-bar `theme-color`. The ids must match the boot
     * script in index.html. */
    var PALETTES = [
      { id: "nova", label: "Nova", dot: "#7aa2f7", dark: "#12131a", light: "#f4f6fb" },
      { id: "aurora", label: "Aurora", dot: "#5fd4b0", dark: "#0f1716", light: "#f2f8f6" },
      { id: "ember", label: "Ember", dot: "#f2a65a", dark: "#1a1411", light: "#fbf6f2" },
      { id: "nebula", label: "Nebula", dot: "#d68cf0", dark: "#16121c", light: "#f8f4fb" },
      { id: "graphite", label: "Graphite", dot: "#c9ccd6", dark: "#141414", light: "#f5f5f5" },
    ];

    function paletteById(id) {
      for (var i = 0; i < PALETTES.length; i++) {
        if (PALETTES[i].id === id) return PALETTES[i];
      }
      return PALETTES[0];
    }

    /** The stored palette id, or "nova" for none or anything unknown. */
    function palettePreference() {
      try { return paletteById(localStorage.getItem("nova-palette")).id; } catch (e) { return "nova"; }
    }

    /* `nova` removes the attribute rather than setting it, so the default is
     * the plain `:root` blocks and nothing else. */
    function applyPalette(id) {
      var root = document.documentElement;
      if (id === "nova") root.removeAttribute("data-palette");
      else root.setAttribute("data-palette", id);
      applyTheme(themePreference());
    }

    /* A phone that flips to dark at sunset has to flip the open tab with it,
     * but only while he is letting the device decide. Registered once, at load,
     * rather than on the settings page: the page he is on when the sun goes
     * down is usually not this one. */
    (function watchDeviceTheme() {
      var query = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)");
      if (!query) return;
      var onChange = function () {
        if (themePreference() === "system") applyTheme("system");
      };
      if (query.addEventListener) query.addEventListener("change", onChange);
      else if (query.addListener) query.addListener(onChange);
    })();

    /** `/settings` -- the app's own settings, as opposed to one conversation's.
     *
     * His capture, 2026-09-13: *"Add a new page for settings for Nova and place
     * it in the sidebar. I want to be able to toggle between light and dark
     * mode, but it should also just follow the device standard."*
     *
     * Three buttons rather than a two-way switch, because "follow the device"
     * is a third state and not the off position of the toggle: a switch can
     * say light or dark and has nowhere to put "whatever the phone says". */
    function renderSettings() {
      stopPolling();
      markNav();
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el("p", "status-line", "How this app looks on this device"));
      feed.textContent = "";

      var card = el("div", "plan-card");
      card.appendChild(el("h2", "settings-heading", "Appearance"));
      card.appendChild(el("p", "settings-note",
        "Saved on this device only, so your phone and your desktop can disagree."));

      var group = el("div", "settings-choice");
      group.setAttribute("role", "radiogroup");
      group.setAttribute("aria-label", "Theme");
      var current = themePreference();
      var buttons = [];
      [["system", "Device"], ["light", "Light"], ["dark", "Dark"]].forEach(function (choice) {
        var button = el("button", "settings-option", choice[1]);
        button.type = "button";
        button.setAttribute("role", "radio");
        button.dataset.theme = choice[0];
        button.setAttribute("aria-checked", choice[0] === current ? "true" : "false");
        button.addEventListener("click", function () {
          try { localStorage.setItem("nova-theme", choice[0]); } catch (e) { /* private mode */ }
          applyTheme(choice[0]);
          buttons.forEach(function (other) {
            other.setAttribute("aria-checked", other === button ? "true" : "false");
          });
          followsDevice.textContent = deviceLine(choice[0]);
        });
        buttons.push(button);
        group.appendChild(button);
      });
      card.appendChild(group);

      var followsDevice = el("p", "settings-note settings-device", deviceLine(current));
      card.appendChild(followsDevice);

      card.appendChild(el("h2", "settings-heading", "Colours"));
      var palettes = el("div", "settings-choice settings-palettes");
      palettes.setAttribute("role", "radiogroup");
      palettes.setAttribute("aria-label", "Colour palette");
      var currentPalette = palettePreference();
      var paletteButtons = [];
      PALETTES.forEach(function (palette) {
        var button = el("button", "palette-option");
        button.type = "button";
        button.setAttribute("role", "radio");
        button.dataset.palette = palette.id;
        button.setAttribute("aria-checked", palette.id === currentPalette ? "true" : "false");
        var dot = el("span", "palette-dot");
        dot.style.background = palette.dot;
        button.appendChild(dot);
        button.appendChild(document.createTextNode(palette.label));
        button.addEventListener("click", function () {
          try { localStorage.setItem("nova-palette", palette.id); } catch (e) { /* private mode */ }
          applyPalette(palette.id);
          paletteButtons.forEach(function (other) {
            other.setAttribute("aria-checked", other === button ? "true" : "false");
          });
        });
        paletteButtons.push(button);
        palettes.appendChild(button);
      });
      card.appendChild(palettes);
      feed.appendChild(card);
    }

    /** The line under the buttons: what the choice means right now. */
    function deviceLine(preference) {
      if (preference !== "system") {
        return "Always " + preference + ", whatever this device is set to.";
      }
      return "Following this device, which is currently "
        + (deviceTheme() === "light" ? "light" : "dark") + ".";
    }

    function renderDiag() {
      stopPolling();
      markNav();
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      statusEl.appendChild(el("p", "status-line", "What this device reports about itself"));
      feed.textContent = "";

      var card = el("div", "plan-card");
      card.appendChild(el("p", "diag-lede",
        "Nothing on this page comes from the server — every line is measured in the browser "
        + "showing it. If the app looks wrong on your phone, open this page there and tap Send. "
        + "The next cycle then reads what your device actually did, instead of guessing at it. "
        + "The side menu will slide in and out once on its own — that is this page measuring "
        + "where your phone actually puts it."));

      var list = el("dl", "diag-list");
      var rows = diagRows();
      rows.forEach(function (row) {
        list.appendChild(el("dt", "diag-key", row[0]));
        list.appendChild(el("dd", "diag-value", row[1]));
      });
      /* Two readings that cannot be taken yet, so they are rows now and
       * values later. The width one is the reviewer's catch and it was the
       * serious finding on this diff: `scrollWidth > clientWidth` was being
       * read inside `diagRows()`, while `feed` was still empty -- so it
       * measured the shell and never the 120-character monospace user-agent
       * string that is the one thing on this page capable of pushing it
       * wider. It would have reported "no sideways scroll" while scrolling
       * sideways, which is this page introducing the exact class of fault it
       * exists to diagnose, and reporting itself clean while doing it. */
      var extras = [
        ["Page vs viewport width", el("dd", "diag-value", "measuring…")],
        ["Hamburger, 3s later", el("dd", "diag-value", "measuring…")],
        /* The two he actually reported and nothing has ever measured. His
         * capture says "the dropdowns are out of place on his S25"; the
         * device report he sent on 2026-08-21 closed the hamburger half of
         * that — 40x40 at 304,26, visible, no sideways scroll — and carried
         * not one number about either dropdown, because this page did not
         * ask for any. Three blind fixes were shipped before the page
         * existed and the page then measured the symptom that was already
         * fine. */
        ["Menu drawer, opened", el("dd", "diag-value", "measuring…")],
        ["Priority popup, opened", el("dd", "diag-value", "measuring…")],
      ];
      extras.forEach(function (extra) {
        list.appendChild(el("dt", "diag-key", extra[0]));
        list.appendChild(extra[1]);
      });
      card.appendChild(list);

      var status = el("p", "capture-status");
      status.setAttribute("role", "status");
      var send = el("button", "capture-btn", "Send this to Nova");
      send.type = "button";
      send.id = "diag-send";
      send.addEventListener("click", function () {
        // Labels come from the same two lists the table was built from, so
        // what is sent and what is on screen cannot use different words for
        // the same reading.
        var parts = rows.map(function (row) { return row[0] + ": " + row[1]; })
          .concat(extras.map(function (extra) { return extra[0] + ": " + extra[1].textContent; }));
        send.disabled = true;
        status.textContent = "sending…";
        status.className = "capture-status";
        fetch("/api/capture", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            target: "notes",
            text: "[device report] " + parts.join(" | "),
            priority: "",
          }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            status.textContent = "sent — the next cycle reads it as a note";
          })
          .catch(function (err) {
            status.textContent = String(err.message || err);
            status.className = "capture-status is-error";
            send.disabled = false;
          });
      });

      var actions = el("div", "diag-actions");
      actions.appendChild(status);
      actions.appendChild(send);
      card.appendChild(actions);
      feed.appendChild(card);

      /* Only now is there a page to measure. Reading `scrollWidth` forces
       * layout, so this is a real measurement of the document as rendered
       * rather than of an empty feed -- no frame to wait for. */
      var doc = document.documentElement;
      var wide = doc.scrollWidth > doc.clientWidth;
      extras[0][1].textContent = doc.scrollWidth + " vs " + doc.clientWidth
        + (wide ? " — SCROLLS SIDEWAYS" : " — no sideways scroll");

      // Writes into a node the next navigation will have discarded, which is
      // harmless -- the alternative is a timer to cancel and a handle to
      // carry, for a value nobody reads once the page is gone.
      window.setTimeout(function () { extras[1][1].textContent = menuBtnReport(); }, 3000);

      /* Both dropdowns, in sequence rather than at once, and both finished
       * well before the 3s hamburger sample above -- opening the drawer puts
       * `.open` on the button, so an overlapping measurement would report a
       * hamburger in a state he never put it in. */
      measureDrawer(function (value) {
        extras[2][1].textContent = value;
        /* After the drawer's 220ms slide back out, so the two are never on
         * screen together. The first version of this comment said the pause
         * was to get `body.nav-open`'s `overflow: hidden` out of the way,
         * and the reviewer was right that it is not: `setMenu` drops that
         * class synchronously, and only the CSS transform is delayed. */
        window.setTimeout(function () {
          measurePrioMenu(function (v) { extras[3][1].textContent = v; });
        }, 300);
      });
    }
    return {
      renderDiag: renderDiag,
      renderSettings: renderSettings,
    };
  };
})();
