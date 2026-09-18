/* The home page and the galaxy (issue #233, step 17).
 *
 * The ninth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js` and `notes.js`. `/` is the landing page with its galaxy strip,
 * and `/galaxy` is the same picture full size; they share the canvas code,
 * so they move together. Nothing else in `app.js` reaches into either --
 * the router calls `loadHome` and `loadGalaxy`, and that is the whole seam
 * back.
 *
 * What it needs from `app.js` arrives as one argument, the same seam every
 * module before it uses. Two of the borrowed names are values that decide
 * where it may be bound: `POLL_MS` is assigned near the bottom of `app.js`,
 * so this is bound beside the Beats pages just before the boot rather than
 * where the code used to sit; and `livePolls` is an array this page pushes
 * its strip timer onto, which is only safe because `stopPolling` empties
 * that array in place instead of replacing it.
 */
(function () {
  "use strict";

  window.novaHome = function (shared) {
    var POLL_MS = shared.POLL_MS;
    var el = shared.el;
    var feed = shared.feed;
    var fetchPage = shared.fetchPage;
    var homeRecapFold = shared.homeRecapFold;
    var livePolls = shared.livePolls;
    var markNav = shared.markNav;
    var renderRecap = shared.renderRecap;
    var route = shared.route;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* `/galaxy` -- what my Claude sessions are doing, drawn.
     *
     * His idea, ideas.md 2026-09-03: *"I want a page in Nova that has a
     * vizualisation of what your Claude sessions are doing ... more space,
     * planets, astronauts, rocketships, stars etc. Following your super-nova
     * Galaxy theme. I want your o be really creative on this!"*
     *
     * One canvas and one list under it, and the list is not a fallback --
     * it is the same data said in words, for the same reason a priority
     * symbol on this site always carries its word: if a reader has to
     * decode a picture to know what I said, I have not said it. A body on
     * the canvas is one live claim; the ring it sits on is how long that
     * cycle has held it, so a session that has just started is close in and
     * one near its 45-minute cap is out at the edge and dimming.
     *
     * `requestAnimationFrame` and not a CSS animation, because the orbit
     * has to keep its phase across a repaint and the labels have to stay
     * upright while the bodies move. The loop stops itself the moment the
     * route leaves this page -- nothing here polls, so a tab left open on
     * another view costs nothing. */

    var galaxyFrame = null;

    function stopGalaxy() {
      if (galaxyFrame !== null) {
        window.cancelAnimationFrame(galaxyFrame);
        galaxyFrame = null;
      }
    }

    /* `/` -- the landing page (idea #274), step 2 of 3.
     *
     * The owner, 2026-09-08: *"Lets make a landing page instead! ... more
     * status updates, the 12 hour summary of what has happened, project
     * updates, links to journals than needs input and comments"*. The spec
     * is `projects/sokrates/projects/nova/landing-page.md`.
     *
     * **One fetch, and that is the constraint this page exists under.**
     * `/api/home` composes the recap, the project index, the ranking and the
     * open asks server-side, because he reads this on a phone and sometimes
     * roaming -- a dashboard that fans out to four endpoints re-creates the
     * ten-second chat load that took most of 2026-09-08 to fix. So nothing
     * below asks for a second payload, and nothing below re-derives a number
     * the payload already carries.
     *
     * The galaxy strip and the health line are step 3 and are deliberately
     * not here: the strip polls on its own clock, so it must never be what
     * this page waits for.
     */
    function loadHome() {
      markNav();
      fetchPage("/api/home")
        .then(function (payload) {
          if (route(window.location.pathname).view !== "home") return;
          renderHome(payload || {});
        })
        .catch(function (err) {
          if (route(window.location.pathname).view !== "home") return;
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the landing page: " + err));
        });
    }

    /** One project card: where it stands, and the one thing that is next in it. */
    function renderHomeProject(card) {
      var section = el("section", "home-project");
      var head = el("div", "home-project-head");
      var link = el("a", "home-project-name", card.name);
      link.setAttribute("href", "/project/" + encodeURIComponent(card.name));
      head.appendChild(link);
      if (card.priority) head.appendChild(el("span", "home-project-prio", card.priority));
      section.appendChild(head);
      /* "4 of 11 done" rather than a bare percentage: the percentage is in the
       * payload and is drawn as the bar's width, and a number he can check
       * against the project page is worth more than one he cannot. `dropped`
       * is named beside it when there is any, because it is out of the
       * denominator -- a project cannot reach 100% by abandoning rows, and
       * this is the only place that says so. */
      var total = (card.done || 0) + (card.open || 0);
      var countText = total
        ? card.done + " of " + total + " done"
        : "nothing boarded yet";
      if (card.dropped) countText += " · " + card.dropped + " dropped";
      section.appendChild(el("p", "home-project-count", countText));
      if (total) {
        var bar = el("div", "home-bar");
        var fill = el("div", "home-bar-fill");
        fill.style.width = (card.percentDone || 0) + "%";
        bar.appendChild(fill);
        /* The bar is decoration over a sentence that already says the
         * number, so it is hidden from a screen reader rather than given a
         * role that would read the same fact twice. */
        bar.setAttribute("aria-hidden", "true");
        section.appendChild(bar);
      }
      if (card.milestone) {
        section.appendChild(el("p", "home-project-milestone", card.milestone));
      }
      /* `next: null` is a project with no open row left, and it says so. An
       * empty task line would read as "I do not know", which is a different
       * thing and the card would be claiming it either way. */
      if (!card.next) {
        section.appendChild(el("p", "home-project-next home-project-clear",
          "No open rows."));
        return section;
      }
      var next = el("p", "home-project-next");
      next.appendChild(document.createTextNode("Next: "));
      /* Linked to the row itself, the same `/issues#<n>` target the project
       * page learned to build on cycle 1449 -- `applyBoardHash` opens the
       * named row expanded, so this lands on the row and not near it. */
      var row = el("a", "home-project-task", card.next.title);
      row.setAttribute("href", "/" + (card.next.board === "issue" ? "issues" : "ideas")
        + "#" + card.next.number);
      next.appendChild(row);
      section.appendChild(next);
      return section;
    }

    /* The health line on `/` -- idea #274, step 3b.
     *
     * His spec: *"One quiet line: cycle running, gaps in numbering, critical
     * alerts, quota burn. Silent when fine."* Silent is the default and this
     * returns `null` for it, so a healthy loop draws nothing at all rather
     * than a green tick -- *"an empty queue should look like calm, not like a
     * form"* is the same rule one block down, and it applies here too.
     *
     * `health_block` in `nova_home.py` has already judged everything that
     * cannot go stale in a cache. The one concern this side adds is the
     * clock-dependent one, and it is here rather than there on purpose:
     * `/api/home` is served stale-while-revalidate, so the body can be hours
     * old, and a `stalled` computed on the server would be frozen at "fine"
     * for exactly those hours. `lastWrittenAt` is absolute, this clock is
     * live, so the subtraction is correct however old the body is.
     */
    function healthConcerns(health, now) {
      var concerns = (health.concerns || []).slice();
      var written = health.lastWrittenAt ? Date.parse(health.lastWrittenAt) : NaN;
      var minutes = Number(health.cadenceMinutes);
      var grace = Number(health.stallGrace);
      /* `stallGrace` -- `cycle_health.STALL_GRACE_INTERVALS`, carried in the
       * payload rather than restated here -- is why this waits rather
       * than asking whether this interval has an entry yet: a cycle writes
       * its entry at the END of its run, so between waking and filing there
       * is a real window where the newest entry is the previous cycle's. A
       * check without the grace would cry stall every single interval. */
      if (!isNaN(written) && minutes > 0) {
        var intervals = Math.floor((now - written) / (minutes * 60000));
        if (grace > 0 && intervals >= grace) {
          concerns.push("nothing has been written for " + intervals
            + (intervals === 1 ? " interval" : " intervals"));
        }
      }
      return concerns;
    }

    function renderHealthLine(health, now) {
      /* `null` and `{}` are different answers and only the second is
       * reassurance: an absent block means nobody looked. Both draw nothing,
       * so this returns early on the first rather than letting an empty
       * object fall through the same path. */
      if (!health) return null;
      var concerns = healthConcerns(health, now === undefined ? Date.now() : now);
      if (!concerns.length) return null;
      var line = el("p", "status-line home-health");
      line.appendChild(el("strong", "home-health-label", "Health"));
      concerns.forEach(function (text) {
        line.appendChild(el("span", "home-health-item", text));
      });
      return line;
    }

    function renderHome(payload) {
      /* The strip below schedules itself onto `livePolls`, so this render has
       * to clear the pending one first -- otherwise a repaint of `/` leaves
       * two timers polling the same endpoint, and they double on every
       * repaint. It also clears whatever the view he arrived from left
       * behind, which every other view already does on the way in. */
      stopPolling();
      stopGalaxy();
      /* Every view but the journal paints its own header, and one that did not
       * would leave the page saying "loading…" for as long as he looked at it --
       * `statusEl` is set once per render and nothing else clears it. The
       * journal's header is the alive-and-running line built from its own
       * payload; this one is the page name plus the health line, which draws
       * nothing at all when there is nothing wrong. */
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      var line = el("p", "status-line");
      line.appendChild(el("strong", "status-page", "Home"));
      statusEl.appendChild(line);
      var health = renderHealthLine(payload.health);
      if (health) statusEl.appendChild(health);
      feed.textContent = "";

      /* What is running right now, at the top of the page.
       *
       * It sat under the projects until 2026-09-13, when he asked for it above
       * the twelve-hour summary: the strip is the only block here about this
       * minute, and everything below it is about a window that has already
       * closed. The canvas still arrives on its own later request -- see
       * `loadHomeStrip` -- so the slot is in the document from the first paint
       * and nothing jumps when it lands.
       */
      var active = payload.active || [];
      var live = el("section", "home-live");
      /* The strip's own slot, empty until `/api/galaxy` answers. Appended
       * here so the words below keep their place on the page whether or not
       * the canvas ever arrives -- a block that moved down when a second
       * request landed would make the page jump under his thumb. */
      live.appendChild(el("div", "home-strip"));
      if (payload.claimsReadable === false) {
        live.appendChild(el("p", "home-live-says",
          "I could not read the claims ledger, so this is not an idle loop — it is a blind one."));
      } else if (!active.length) {
        live.appendChild(el("p", "home-live-says", "No session is working right now."));
      } else {
        live.appendChild(el("p", "home-live-says", active.length === 1
          ? "1 session is working right now:"
          : active.length + " sessions are working right now:"));
        var running = el("ul", "home-live-list");
        active.forEach(function (entry) {
          running.appendChild(el("li", "home-live-item",
            "Cycle " + entry.cycle + (entry.title ? " — " + entry.title : "")));
        });
        live.appendChild(running);
      }
      var galaxy = el("a", "home-live-all", "The galaxy →");
      galaxy.setAttribute("href", "/galaxy");
      live.appendChild(galaxy);
      feed.appendChild(live);

      /* The recap first and open, which is the reversal `renderRecap`'s
       * `homeRecapFold` exists for. `renderRecap` returns null when there is
       * nothing in it, which is a cold journal rather than an error, so the
       * rest of the page still draws. */
      var recap = renderRecap(payload.recap || {}, homeRecapFold);
      if (recap) feed.appendChild(recap);

      /* The "N questions are waiting on you" block used to be here.
       *
       * Removed 2026-09-13 on his report: *"remove 'questions waiting for you'
       * on the homepage as it is listed that 83 questions are waiting for me,
       * which is not true."* It counted every ask any cycle has ever written
       * into a journal entry and never had answered, back to the first one, so
       * it grew monotonically and said nothing about today. The count is still
       * computed server-side (`payload.needsYou`) and still reachable at
       * `/asks`; what is gone is the claim on the landing page. Fixing what the
       * number means is its own job, and a wrong number is worse than none
       * while that job waits. */

      var projects = payload.projects || [];
      var section = el("section", "home-projects");
      section.appendChild(el("h2", "home-section-title", "Projects"));
      if (!projects.length) {
        section.appendChild(el("p", "empty", "No projects on the boards yet."));
      } else {
        projects.forEach(function (card) {
          section.appendChild(renderHomeProject(card));
        });
        var more = el("a", "home-projects-all", "All projects →");
        more.setAttribute("href", "/projects");
        section.appendChild(more);
      }
      feed.appendChild(section);


      var toFeed = el("a", "home-journal-all", "The journal →");
      toFeed.setAttribute("href", "/journal");
      feed.appendChild(toFeed);

      /* Last, and on its own request. See `loadHomeStrip`. */
      loadHomeStrip();
    }

    /* The galaxy strip on `/` -- idea #274, step 3.
     *
     * The spec's single-payload rule has exactly one exception and this is
     * it: *"live sessions are live by definition ... So it gets its own small
     * poll, renders **after** the rest, and falls back to the plain list the
     * galaxy page already carries under its picture. **It must never be what
     * the page waits for.**"*
     *
     * Three things follow, and each is a line of code rather than an
     * intention. It is called at the *end* of `renderHome`, so every block on the page --
     * the strip's own slot included -- is already on screen before this asks
     * for anything. Its canvas goes into a slot that is already in the
     * document, so a strip that never arrives leaves the page exactly as it
     * was. And a failed poll reschedules instead of drawing an error: the
     * words underneath already say what is running, so a red line here would
     * be the second thing on the page saying the same thing worse.
     *
     * **The words below the canvas are not repainted from this payload, and
     * that is deliberate.** `/api/home` carries each live cycle's board
     * *title* -- it joins the ledger against his board -- and `/api/galaxy`
     * carries the slug and the note instead. So the list is the richer half
     * and the canvas is the fresher one. A cycle that claimed a row since the
     * home payload was cached appears as a body with no line under it, which
     * is the right way round: the picture is the live thing, and the sentence
     * he can read is never wrong about a cycle it names. */
    function loadHomeStrip() {
      function again() {
        if (route(window.location.pathname).view !== "home") return;
        livePolls.push(setTimeout(loadHomeStrip, POLL_MS));
      }
      fetchPage("/api/galaxy")
        .then(function (payload) {
          if (route(window.location.pathname).view !== "home") return;
          renderHomeStrip(payload || {});
          again();
        })
        .catch(again);
    }

    function renderHomeStrip(payload) {
      var slot = document.querySelector(".home-strip");
      if (!slot) return;
      var active = payload.active || [];
      var recent = payload.recent || [];
      /* An unreadable ledger draws no picture at all. An empty canvas and a
       * blind one look identical, and the sentence under it already tells him
       * which he is looking at -- drawing a starfield over "I could not read
       * the claims ledger" would contradict it in the one language he cannot
       * check. */
      if (payload.readable === false) {
        slot.textContent = "";
        stopGalaxy();
        return;
      }
      /* One canvas, reused across polls rather than replaced: a new element
       * every thirty seconds would restart the orbit from phase zero, so the
       * bodies would jump. `drawGalaxy` cancels the previous frame loop
       * itself and picks the new claim list up on the next frame. */
      var canvas = slot.querySelector("canvas");
      if (!canvas) {
        canvas = document.createElement("canvas");
        canvas.className = "galaxy-canvas home-strip-canvas";
        canvas.setAttribute("role", "img");
        slot.appendChild(canvas);
      }
      /* A canvas is opaque to a screen reader, so the label is the picture
       * said in words -- and zero gets its own sentence rather than "0
       * sessions", because the strip is the one block on this page that is
       * normally empty and "0 sessions are working" reads like a fault. */
      canvas.setAttribute("aria-label", (!active.length
        ? "No session is working right now."
        : active.length === 1
          ? "1 session is working right now."
          : active.length + " sessions are working right now.")
        + " The same list is written out below.");
      drawGalaxy(canvas, active, recent, payload.ttlMinutes || 45,
                 "home", galaxyStripHeight);
    }

    function loadGalaxy() {
      markNav();
      fetchPage("/api/galaxy")
        .then(function (payload) {
          if (route(window.location.pathname).view !== "galaxy") return;
          renderGalaxy(payload || {});
        })
        .catch(function (err) {
          if (route(window.location.pathname).view !== "galaxy") return;
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the galaxy: " + err));
        });
    }

    /** A stable 0..1 from a slug, so one claim keeps its colour and its
     *  starting angle across every repaint and every reload. Hashing the
     *  slug rather than using the array index is what stops every body on
     *  the canvas jumping when one cycle releases a row. */
    function galaxySeed(text) {
      var h = 2166136261;
      var i;
      for (i = 0; i < (text || "").length; i += 1) {
        h ^= text.charCodeAt(i);
        h = (h * 16777619) >>> 0;
      }
      return (h >>> 8) / 16777216;
    }

    function galaxyAge(entry, ttl) {
      /* 0 at the moment of claiming, 1 at the TTL. `null` held minutes is a
       * row whose timestamp I could not read -- it draws at rest rather than
       * at either end, because both ends would be a guess printed as a fact. */
      if (entry.heldMinutes === null || entry.heldMinutes === undefined) return 0.5;
      return Math.max(0, Math.min(1, entry.heldMinutes / (ttl || 45)));
    }

    function renderGalaxy(payload) {
      stopGalaxy();
      feed.textContent = "";

      var active = payload.active || [];
      var recent = payload.recent || [];
      var ttl = payload.ttlMinutes || 45;

      var head = el("div", "galaxy-head");
      head.appendChild(el("h2", "galaxy-title", "The galaxy"));
      var says;
      if (payload.readable === false) {
        says = "I could not read the claims ledger, so this is not an empty galaxy — it is a blind one.";
      } else if (!active.length) {
        says = "No session is holding a row this minute. The cooled bodies below are what the last few finished.";
      } else if (active.length === 1) {
        says = "One session is working right now.";
      } else {
        says = active.length + " sessions are working right now.";
      }
      head.appendChild(el("p", "galaxy-says", says));
      feed.appendChild(head);

      var canvas = document.createElement("canvas");
      canvas.className = "galaxy-canvas";
      /* Labelled, because a canvas is opaque to a screen reader and the
       * list underneath is the accessible copy of the same facts. */
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", says + " The same list is written out below.");
      feed.appendChild(canvas);

      var list = el("div", "galaxy-list");
      function line(entry, live) {
        var row = el("div", "galaxy-row" + (live ? " is-live" : ""));
        var dot = el("span", "galaxy-dot");
        dot.style.background = galaxyColour(entry);
        row.appendChild(dot);
        var body = el("div", "galaxy-row-body");
        var held = entry.heldMinutes === null || entry.heldMinutes === undefined
          ? "age unknown"
          : Math.round(entry.heldMinutes) + " min";
        body.appendChild(el("div", "galaxy-row-head",
          "Cycle " + entry.cycle + " · " + (live ? "working" : (entry.state || "released")) + " · " + held));
        var said = entry.note || entry.outcome || entry.item;
        body.appendChild(el("div", "galaxy-row-note", said));
        row.appendChild(body);
        return row;
      }
      var i;
      for (i = 0; i < active.length; i += 1) list.appendChild(line(active[i], true));
      if (recent.length) {
        list.appendChild(el("h3", "galaxy-subhead", "Cooling"));
        for (i = 0; i < recent.length; i += 1) list.appendChild(line(recent[i], false));
      }
      feed.appendChild(list);

      drawGalaxy(canvas, active, recent, ttl, "galaxy", galaxyPageHeight);
    }

    /** The full page's canvas: most of a phone screen, which is what a page
     *  whose whole job is the picture should be. */
    function galaxyPageHeight(w) { return Math.max(240, Math.round(w * 0.72)); }

    /** The strip's canvas on `/`. Half the page's ratio and a lower floor,
     *  because on the landing page the picture is one block of six and the
     *  capture box has to stay reachable without scrolling. */
    function galaxyStripHeight(w) { return Math.max(132, Math.round(w * 0.34)); }

    /** A live body's hue, from its slug. Kept in one place because the dot
     *  in the list and the planet on the canvas have to agree -- two
     *  colour formulas for one body is the drift this repo keeps paying
     *  for, one page down. */
    function galaxyColour(entry) {
      var hue = Math.round(galaxySeed(entry.item) * 300);
      return "hsl(" + hue + ", 78%, 66%)";
    }

    /* `view` and `height` are parameters because the same drawing serves two
     * places now: the full page at `/galaxy` and the compact strip on `/`
     * (idea #274, step 3). The frame loop has to know which route it belongs
     * to -- a loop that checked for `"galaxy"` while drawing on the landing
     * page would cancel itself on its first frame, and one that checked for
     * nothing would keep animating a canvas he has navigated away from. */
    function drawGalaxy(canvas, active, recent, ttl, view, height) {
      /* Exactly one frame loop is ever alive. `renderGalaxy` clears the old
       * one on its way in, but the strip on `/` calls this again on every
       * poll, and a second `requestAnimationFrame` chain would overwrite
       * `galaxyFrame` and leave the first one running for ever -- one more
       * orbit of the same bodies every thirty seconds, on the page he leaves
       * open. */
      stopGalaxy();
      var ctx = canvas.getContext && canvas.getContext("2d");
      if (!ctx) return;

      /* Fixed stars, generated once from the slug set rather than per
       * frame: a starfield redrawn from Math.random() every frame is a
       * snowstorm, which is the failure this whole page is one tap away
       * from. */
      var stars = [];
      var s;
      for (s = 0; s < 90; s += 1) {
        stars.push({
          x: galaxySeed("star-x-" + s),
          y: galaxySeed("star-y-" + s),
          r: 0.4 + galaxySeed("star-r-" + s) * 1.3,
          tw: galaxySeed("star-t-" + s)
        });
      }

      var started = null;

      function frame(ts) {
        if (route(window.location.pathname).view !== view) { stopGalaxy(); return; }
        if (started === null) started = ts;
        var t = (ts - started) / 1000;

        var ratio = window.devicePixelRatio || 1;
        var w = canvas.clientWidth || 320;
        var h = height(w);
        if (canvas.width !== Math.round(w * ratio) || canvas.height !== Math.round(h * ratio)) {
          canvas.width = Math.round(w * ratio);
          canvas.height = Math.round(h * ratio);
          canvas.style.height = h + "px";
        }
        ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
        ctx.clearRect(0, 0, w, h);

        var cx = w / 2;
        var cy = h / 2;
        var maxR = Math.min(w, h) / 2 - 26;

        var sky = ctx.createRadialGradient(cx, cy, 0, cx, cy, Math.max(w, h) / 1.4);
        sky.addColorStop(0, "#150f2e");
        sky.addColorStop(1, "#05040d");
        ctx.fillStyle = sky;
        ctx.fillRect(0, 0, w, h);

        var i;
        for (i = 0; i < stars.length; i += 1) {
          var st = stars[i];
          var a = 0.25 + 0.45 * (0.5 + 0.5 * Math.sin(t * 0.8 + st.tw * 6.28));
          ctx.globalAlpha = a;
          ctx.fillStyle = "#cfd6ff";
          ctx.beginPath();
          ctx.arc(st.x * w, st.y * h, st.r, 0, 6.2832);
          ctx.fill();
        }
        ctx.globalAlpha = 1;

        // The supernova at the middle is me: it pulses whether or not any
        // session is holding a row, because the loop is running either way.
        var pulse = 16 + 3 * Math.sin(t * 1.6);
        var core = ctx.createRadialGradient(cx, cy, 0, cx, cy, pulse * 2.6);
        core.addColorStop(0, "rgba(255,255,255,0.95)");
        core.addColorStop(0.35, "rgba(180,160,255,0.65)");
        core.addColorStop(1, "rgba(120,90,255,0)");
        ctx.fillStyle = core;
        ctx.beginPath();
        ctx.arc(cx, cy, pulse * 2.6, 0, 6.2832);
        ctx.fill();

        // Cooled bodies first, so a live one is never painted under one.
        drawBodies(ctx, recent, cx, cy, maxR, t, ttl, false);
        drawBodies(ctx, active, cx, cy, maxR, t, ttl, true);

        galaxyFrame = window.requestAnimationFrame(frame);
      }
      galaxyFrame = window.requestAnimationFrame(frame);
    }

    function drawBodies(ctx, entries, cx, cy, maxR, t, ttl, live) {
      var i;
      for (i = 0; i < entries.length; i += 1) {
        var entry = entries[i];
        var seed = galaxySeed(entry.item);
        var age = galaxyAge(entry, ttl);
        // Live bodies ride out from a third of the radius to the edge as
        // the claim ages; released ones sit outside them, drifting off.
        var radius = live
          ? maxR * (0.34 + 0.56 * age)
          : maxR * (0.92 + 0.06 * ((i % 3) / 3));
        var speed = live ? 0.32 - 0.16 * age : 0.06;
        var angle = seed * 6.2832 + t * speed;
        var x = cx + Math.cos(angle) * radius;
        var y = cy + Math.sin(angle) * radius * 0.62;

        ctx.globalAlpha = live ? 0.28 : 0.1;
        ctx.strokeStyle = live ? galaxyColour(entry) : "#6b6f93";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.ellipse(cx, cy, radius, radius * 0.62, 0, 0, 6.2832);
        ctx.stroke();

        var size = live ? 7 - 2 * age : 3.4;
        ctx.globalAlpha = live ? 1 : 0.5;
        var glow = ctx.createRadialGradient(x, y, 0, x, y, size * 3.2);
        glow.addColorStop(0, live ? galaxyColour(entry) : "#8f95bd");
        glow.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = glow;
        ctx.beginPath();
        ctx.arc(x, y, size * 3.2, 0, 6.2832);
        ctx.fill();
        ctx.fillStyle = live ? galaxyColour(entry) : "#8f95bd";
        ctx.beginPath();
        ctx.arc(x, y, size, 0, 6.2832);
        ctx.fill();

        if (live) {
          ctx.globalAlpha = 0.92;
          ctx.fillStyle = "#e8eaff";
          ctx.font = "600 11px system-ui, sans-serif";
          ctx.textAlign = x < cx ? "right" : "left";
          ctx.fillText("Cycle " + entry.cycle, x + (x < cx ? -size - 5 : size + 5), y + 4);
        }
        ctx.globalAlpha = 1;
      }
    }

    return {
      loadGalaxy: loadGalaxy,
      loadHome: loadHome,
    };
  };
})();
