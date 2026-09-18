/* The Beats, Catalog and Alerts pages (issue #233, step 15).
 *
 * The seventh piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js` and `project.js`.
 * The three pages travel together because they are one page's worth of
 * machinery: `/heartbeats` is the schedule list with its own poll,
 * `/catalog` is the model list those schedules pick from, and `/alerts`
 * is what the same backend says went wrong. Nothing else in `app.js`
 * reaches into them -- the router calls the three loaders and that is
 * the whole seam back.
 *
 * What it needs from `app.js` arrives as one argument, the same seam
 * every module before it uses and for the same reason: a name missing
 * from the list is a `ReferenceError` on the first line that uses it
 * rather than a page that half draws. `app.js` calls this at the point
 * in its own body where the Heartbeats page used to be defined, and
 * takes back the three loaders the router still reaches.
 */
(function () {
  "use strict";

  window.novaBeats = function (shared) {
    var ASK_POLL_MAX = shared.ASK_POLL_MAX;
    var ASK_POLL_MS = shared.ASK_POLL_MS;
    var POLL_MS = shared.POLL_MS;
    var el = shared.el;
    var feed = shared.feed;
    var fmtStamp = shared.fmtStamp;
    var fetchPage = shared.fetchPage;
    var livePolls = shared.livePolls;
    var markNav = shared.markNav;
    var route = shared.route;
    var statusEl = shared.statusEl;
    var stopPolling = shared.stopPolling;
    var wordmark = shared.wordmark;

    /* The Heartbeats page.
     *
     * His capture: *"Then Agora can just be purely for heartbeats."* Cycle 441
     * built the Chats half of that; this is the other half, and after it there
     * is nothing on Agora's own app he opens day to day.
     *
     * Read plus two writes. Switching one off and pressing run are the things
     * he does from his phone; creating, deleting and editing a schedule are
     * not, and they are the four that can quietly take this loop off the air,
     * so they stay on Agora's page -- see `nova_heartbeats.py`.
     */
    /* Which conversation drawers he has opened, by heartbeat id.
     *
     * `renderHeartbeats` wipes the feed and rebuilds every card, and it runs on
     * a poll -- every 30s idle, every 4s while a run is queued. A `details`
     * rebuilt from scratch is closed, so without this the drawer he just opened
     * snaps shut under his thumb within four seconds of pressing "Run now".
     * Reviewer caught it. Same shape as `boardState.open` on the goals board.
     */
    var hbOpenThreads = {};

    function hbStateLine(row) {
      if (row.running) return { text: "Running now", cls: "hb-state hb-state-running" };
      if (!row.enabled) return { text: "Off", cls: "hb-state hb-state-off" };
      if (row.forceRun) return { text: "On · run queued", cls: "hb-state hb-state-running" };
      return { text: "On", cls: "hb-state hb-state-on" };
    }

    /* Both writes answer with `{ok, message}` and neither returns the changed
     * row, so the page re-lists rather than patching what it drew. A local
     * flip would show "Off" for a PATCH that never landed. */
    function hbPost(path, body, button) {
      button.disabled = true;
      fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result.ok) window.alert(result.message || result.error || "that did not work");
          loadHeartbeats();
        })
        .catch(function (err) {
          button.disabled = false;
          window.alert("could not reach Nova: " + err);
        });
    }

    /* This page draws things that change without him touching anything -- a
     * run starting, a run finishing, `lastResult` going from nothing to an
     * answer -- and until now it drew them exactly once. Press "Run now" and
     * the row reads "On · run queued" until he reloads the app. My reviewer
     * found that on runner#387's own diff and I filed it instead of fixing
     * it; it has been the top line of the handoff for two cycles since.
     *
     * `pollConv`'s shape, with the reschedule at the end of the render rather
     * than in a loop of its own, so exactly one timer is ever alive: every
     * path that repaints this page goes through `renderHeartbeats`, whose own
     * `stopPolling()` clears the pending one first. Leaving the page clears it
     * too, for free -- that is what `stopPolling` is for and every other view
     * calls it on the way in.
     *
     * Two cadences, both of them numbers already in this file: `ASK_POLL_MS`
     * for the seconds after he presses "Run now", and `POLL_MS` otherwise,
     * which is the journal feed's idle rate.
     *
     * The fast one keys on `forceRun` alone, and my first version had
     * `running || forceRun` until I looked at what the live endpoint actually
     * answers. `running` is `lastResult === "running"`, and the Nova row is
     * running for roughly eighteen of every twenty minutes -- so keying on it
     * makes the fast rate the permanent rate, ~900 requests an hour per open
     * tab against Agora's list API, for a row that changes twice. `forceRun`
     * is the flag Agora sets between his tap and the runner picking the run
     * up: it is his own action, it lasts seconds, and it is the only state on
     * this page he is standing there watching. Once a run is under way the
     * next thing that changes is minutes off and 30 seconds is not late.
     *
     * The polling itself never stops while the page is open -- `pollConv` has
     * an attempt cap because it waits for a single answer and is finished when
     * it arrives, and this waits for nothing in particular. **The fast phase**
     * is capped, and that number is measured rather than cautious. `run_now`'s
     * own docstring in `nova_heartbeats.py` says pressing it during a cycle
     * means the run happens when that cycle ends, "which can be most of an
     * hour later" -- so `forceRun` is not the few-seconds flag I first took it
     * for, and `/api/heartbeats` is uncached and makes two upstream Agora
     * calls per request. An uncapped fast phase is therefore up to ~1,800
     * upstream calls an hour from one open tab, for a row that changes once.
     * After `ASK_POLL_MAX` fast ticks -- four minutes, the same bound the Ask
     * page uses -- it drops to the idle rate and stays there. Four minutes is
     * long enough to see a press get picked up; an hour is a leak.
     */
    var hbFastTicks = 0;

    function scheduleHeartbeatsPoll(rows) {
      var queued = rows.some(function (r) { return r.forceRun; });
      var fast = queued && hbFastTicks < ASK_POLL_MAX;
      // Reset only when nothing is queued -- not merely when this tick came out
      // slow. My own first version reset on the slow tick, which meant the
      // counter went 60 fast, one slow, 60 fast, for ever: a cap that read as a
      // cap and bounded nothing. The test named for the four minutes caught it.
      hbFastTicks = queued ? hbFastTicks + 1 : 0;
      livePolls.push(setTimeout(loadHeartbeats, fast ? ASK_POLL_MS : POLL_MS));
    }

    /* Issue #239: Marcus is the third agent he can stop from here. Cycles have
     * the Stop button and heartbeats the switch below; Marcus runs no heartbeat,
     * only coach turns in one conversation, so this card ends whichever of those
     * is running. It answers with what happened -- "nothing was running" is the
     * common answer and it is still the truth he pressed for. His 20:00
     * reminder is not a turn and keeps its own switch in the Marcus app.
     */
    // Its own classes, styled like a heartbeat row: the page's tests count
    // `.hb-row`s as heartbeats, and so would anything else reading the page.
    function marcusStopCard() {
      var card = el("div", "marcus-stop");
      card.appendChild(el("div", "marcus-stop-name", "Marcus"));
      card.appendChild(el("div", "marcus-stop-meta", "Coach turns · no heartbeat"));
      var actions = el("div", "marcus-stop-actions");
      var stop = el("button", "marcus-stop-btn", "Stop Marcus");
      stop.setAttribute("type", "button");
      stop.addEventListener("click", function () {
        stop.disabled = true;
        fetch("/api/marcus/stop", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            stop.disabled = false;
            if (!result.ok) {
              window.alert(result.message || result.error || "that did not work");
            } else {
              window.alert(result.message === "stopped"
                ? "Marcus stopped." : "Marcus had nothing running.");
            }
          })
          .catch(function (err) {
            stop.disabled = false;
            window.alert("could not reach Nova: " + err);
          });
      });
      actions.appendChild(stop);
      card.appendChild(actions);
      return card;
    }

    function renderHeartbeats(payload) {
      stopPolling();
      markNav();
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      var rows = payload.heartbeats || [];
      var on = rows.filter(function (r) { return r.enabled; }).length;
      statusEl.appendChild(el("p", "status-line",
        rows.length + (rows.length === 1 ? " heartbeat" : " heartbeats") + " · " + on + " on"));
      feed.textContent = "";
      // Before the empty-list return, not after it: a first heartbeat created
      // from Agora's side should appear here without a reload as well.
      scheduleHeartbeatsPoll(rows);
      feed.appendChild(marcusStopCard());

      if (!rows.length) {
        feed.appendChild(el("p", "empty", "No heartbeats yet."));
        return;
      }

      var list = el("div", "hb-list");
      rows.forEach(function (row) {
        var card = el("div", "hb-row" + (row.enabled ? "" : " hb-off"));
        card.appendChild(el("div", "hb-name", row.name));
        var meta = [row.personaName, row.schedule].filter(Boolean).join(" · ");
        if (meta) card.appendChild(el("div", "hb-meta", meta));

        var state = hbStateLine(row);
        card.appendChild(el("div", state.cls, state.text));

        // `fmtStamp` takes milliseconds; an unparseable timestamp yields NaN,
        // which renders as "Invalid Date", so the line is dropped instead.
        // A heartbeat that has never run says so rather than saying nothing:
        // a missing line and "never ran" look identical and are not.
        var when = row.lastRunAt ? Date.parse(row.lastRunAt) : NaN;
        var lastLine = isNaN(when) ? "Never run" : "Last run " + fmtStamp(when);
        if (row.lastResult && !isNaN(when)) lastLine += " · " + row.lastResult;
        card.appendChild(el("div", "hb-when", lastLine));

        var actions = el("div", "hb-actions");
        var toggle = el("button", "hb-btn", row.enabled ? "Turn off" : "Turn on");
        toggle.setAttribute("type", "button");
        toggle.addEventListener("click", function () {
          hbPost("/api/heartbeats/enabled",
            { heartbeatId: row.id, enabled: !row.enabled }, toggle);
        });
        actions.appendChild(toggle);

        var run = el("button", "hb-btn", "Run now");
        run.setAttribute("type", "button");
        run.addEventListener("click", function () {
          hbPost("/api/heartbeats/run", { heartbeatId: row.id }, run);
        });
        actions.appendChild(run);

        if (row.conversationId) {
          var open = el("button", "hb-btn", "Open thread");
          open.setAttribute("type", "button");
          open.addEventListener("click", function () {
            // Into the dock, which is the only thread view now (2026-09-07).
            // No `pushState`: the page he is on stays where it is and the
            // panel opens over it, which is what every other way into a
            // thread does.
            if (window.novaOpenChat) window.novaOpenChat(row.conversationId);
          });
          actions.appendChild(open);
        }
        card.appendChild(actions);

        // His capture: *"The heartbeat conversations should rather somehow be
        // listed in the beats page as they belong there. Somehow underneath
        // their relative heartbeat and as a dropdown drawer so they are not
        // shown unless i want to see them."* Closed by default, same `details`
        // shape as the Task fold above it, and the count is in the summary so
        // he can see there are twelve without opening it.
        var threads = row.conversations || [];
        if (threads.length) {
          var box = el("details", "hb-threads");
          if (hbOpenThreads[row.id]) box.setAttribute("open", "");
          box.addEventListener("toggle", function () {
            hbOpenThreads[row.id] = box.open;
          });
          box.appendChild(el("summary", "",
            threads.length + (threads.length === 1 ? " conversation" : " conversations")));
          var tlist = el("div", "hb-thread-list");
          threads.forEach(function (conv) {
            var btn = el("button", "hb-thread", "");
            btn.setAttribute("type", "button");
            // A thread `_with_current` synthesised carries no name -- Agora has
            // one, this page has not fetched it. "Current thread" is the label
            // for the row and deliberately not the name passed to
            // `openConversation` below, which falls back to the heartbeat's own
            // name: "Current thread" as a page header says nothing about which
            // heartbeat it belongs to, which is the one thing this fixes.
            btn.appendChild(el("span", "hb-thread-name", conv.name || "Current thread"));
            var tw = conv.updatedAt ? Date.parse(conv.updatedAt) : NaN;
            if (!isNaN(tw)) btn.appendChild(el("span", "hb-thread-when", fmtStamp(tw)));
            btn.addEventListener("click", function () {
              // Same as "Open thread" above: the dock, over this page.
              if (window.novaOpenChat) window.novaOpenChat(conv.id);
            });
            tlist.appendChild(btn);
          });
          box.appendChild(tlist);
          card.appendChild(box);
        }

        if (row.task) {
          var task = el("details", "hb-task");
          var summary = el("summary", "", "Task");
          task.appendChild(summary);
          task.appendChild(el("pre", "", row.task));
          card.appendChild(task);
        }
        list.appendChild(card);
      });
      feed.appendChild(list);
    }

    function loadHeartbeats() {
      fetchPage("/api/heartbeats")
        .then(function (payload) {
          if (route(window.location.pathname).view !== "heartbeats") return;
          renderHeartbeats(payload);
        })
        .catch(function (err) {
          // The route guard belongs on this path too, and it belongs here now
          // rather than as a tidy-up: before this page polled, a failed fetch
          // could only be the one he triggered by opening it. Now a timer can
          // have a request in flight when he taps away, and without the guard
          // its failure paints "Could not load your heartbeats" over whatever
          // page he actually opened.
          if (route(window.location.pathname).view !== "heartbeats") return;
          // `stopPolling()` first, exactly as `renderHeartbeats` does, and this
          // line is the reviewer's finding on this PR rather than symmetry for
          // its own sake. `loadHeartbeats` is also called from `hbPost`, and
          // `button.disabled` guards only the button that was tapped -- so
          // "Turn off" on one row and "Run now" on another put two requests in
          // flight. If the success lands first it schedules a timer; a failure
          // landing after it used to append a second one without clearing the
          // first, and only a success ever clears. Two timers, then three.
          stopPolling();
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load your heartbeats: " + err));
          // And keep trying. Without this, one dropped request on a phone
          // leaves the page frozen for good, which is the bug this cycle is
          // fixing wearing a different hat.
          scheduleHeartbeatsPoll([]);
        });
    }

    /* The service catalog -- every workload running in the cluster, what
     * ordered it, and whether it is up.
     *
     * Step 2 of the IDP roadmap. `tools.catalog` reads the cluster and
     * writes `nova/catalog.md`; until this page existed the only way to
     * read it was to open Obsidian on a laptop. Nothing here talks to
     * Kubernetes: the server parses that one vault document, for the
     * reason written at the top of `nova_catalog.py`.
     *
     * No polling. The file changes when a cycle runs the tool, which is
     * once an hour at the most, so a timer here would be 900 requests a
     * day to re-read a document that had not moved -- and the page says
     * out loud when it was last regenerated, which is the honest answer to
     * "is this current" rather than a refresh that hides the question.
     */
    function catalogCard(row) {
      var card = el("div", "cat-row" + (row.status === "off" ? " cat-dim" : ""));
      var head = el("div", "cat-head");
      if (row.url) {
        var link = el("a", "cat-name", row.name);
        link.href = row.url;
        link.rel = "noopener";
        head.appendChild(link);
      } else {
        head.appendChild(el("span", "cat-name", row.name));
      }
      // The word, never a bare colour -- his ask about the priority symbols,
      // and "off" and "down" are the pair that most needs it: one is a
      // deliberate scale-to-zero and the other is something broken.
      var label = row.status === "up" ? "Up"
        : row.status === "off" ? "Off on purpose"
        : row.status === "down" ? "DOWN" : "Unknown";
      head.appendChild(el("span", "cat-state cat-state-" + row.status, label));
      card.appendChild(head);
      card.appendChild(el("div", "cat-meta", row.host || row.namespace));
      var ordered = row.claim
        ? "Source repo ordered by " + row.claim
        : "Nothing here was ordered by a claim";
      card.appendChild(el("div", "cat-meta", ordered));
      if (row.deployedBy) {
        card.appendChild(el("div", "cat-meta", "Deployed by " + row.deployedBy));
      }
      // The join that makes this a catalog rather than an inventory: the row
      // says what a thing is, where it runs, and where it is written up. A
      // service with no page says so rather than showing nothing, because the
      // gap is the useful half -- it is what somebody would go and write.
      if (row.docs) {
        var doc = el("a", "cat-meta cat-docs", "Read the docs");
        doc.href = row.docs;
        doc.rel = "noopener";
        card.appendChild(doc);
      } else if (row.docs === null) {
        card.appendChild(el("div", "cat-meta cat-nodocs", "No docs page"));
      }
      return card;
    }

    function renderCatalog(payload) {
      stopPolling();
      markNav();
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      var rows = payload.services || [];
      var parts = [rows.length + (rows.length === 1 ? " service" : " services")];
      if (payload.down) parts.push(payload.down + " down");
      if (payload.off) parts.push(payload.off + " off on purpose");
      statusEl.appendChild(el("p", "status-line", parts.join(" · ")));
      feed.textContent = "";

      if (payload.missing) {
        feed.appendChild(el("p", "empty",
          "No catalog has been written yet. A cycle builds it with tools.catalog."));
        return;
      }

      if (payload.headline) {
        var lead = el("div", "cat-headline" + (payload.incomplete ? " cat-warn" : ""));
        lead.appendChild(el("p", "cat-lead", payload.headline));
        if (payload.detail) lead.appendChild(el("p", "cat-detail", payload.detail));
        (payload.unreadable || []).forEach(function (u) {
          lead.appendChild(el("p", "cat-detail", u));
        });
        if (payload.docsHeadline) {
          lead.appendChild(el("p", "cat-lead", payload.docsHeadline));
          if (payload.docsDetail) lead.appendChild(el("p", "cat-detail", payload.docsDetail));
        }
        feed.appendChild(lead);
      }

      // Grouped by namespace, in the order the file lists them, because the
      // tool already sorts by namespace and re-sorting here would be a
      // second opinion about an order that has one owner.
      var list = el("div", "cat-list");
      var current = null;
      rows.forEach(function (row) {
        if (row.namespace !== current) {
          current = row.namespace;
          list.appendChild(el("h2", "cat-ns", current));
        }
        list.appendChild(catalogCard(row));
      });
      feed.appendChild(list);

      if ((payload.doors || []).length) {
        feed.appendChild(el("h2", "cat-ns", "Doors nothing in the catalog accounts for"));
        var doors = el("div", "cat-list");
        payload.doors.forEach(function (door) {
          doors.appendChild(el("div", "cat-row cat-dim", door));
        });
        feed.appendChild(doors);
      }

      var when = payload.regenerated
        ? "Read from the cluster " + payload.regenerated
          + (payload.cycle ? " by cycle " + payload.cycle : "")
        : "This catalog carries no timestamp.";
      feed.appendChild(el("p", "cat-when", when));
    }

    /* `/alerts` -- what Prometheus is complaining about right now.
     *
     * The card colour is the whole interface here, so it carries the word
     * too: `personality.md` -- if a reader has to know a colour code to know
     * what I said, I have not said it.
     *
     * The empty state is the part that needed thought. "Nothing is firing"
     * and "I could not ask" produce the same empty list, so this never draws
     * the calm sentence without the rule count beside it, and `blind` gets a
     * warning of its own: Prometheus serves zero alerts just as happily when
     * its rules file failed to load. */
    function alertCard(alert, kind) {
      var card = el("div", "alert-card alert-" + kind);
      var head = el("div", "alert-head");
      head.appendChild(el("span", "alert-word alert-word-" + kind,
        kind === "firing" ? "\uD83D\uDD34 Firing" : "\uD83D\uDFE0 Pending"));
      head.appendChild(el("span", "alert-name", alert.name));
      if (alert.severity) head.appendChild(el("span", "alert-sev", alert.severity));
      card.appendChild(head);
      if (alert.text) card.appendChild(el("p", "alert-text", alert.text));
      if ((alert.where || []).length) {
        card.appendChild(el("p", "alert-where", alert.where.join(" \u00b7 ")));
      }
      if (alert.since) card.appendChild(el("p", "alert-since", "since " + alert.since));
      return card;
    }

    function renderAlerts(payload) {
      stopPolling();
      markNav();
      statusEl.textContent = "";
      statusEl.appendChild(wordmark());
      var firing = payload.firing || [];
      var pending = payload.pending || [];
      var parts = [firing.length + (firing.length === 1 ? " firing" : " firing")];
      if (pending.length) parts.push(pending.length + " pending");
      parts.push(payload.rules + (payload.rules === 1 ? " rule" : " rules"));
      statusEl.appendChild(el("p", "status-line", parts.join(" \u00b7 ")));
      feed.textContent = "";

      if (!payload.reachable) {
        feed.appendChild(el("p", "empty",
          "I could not reach Prometheus, so this page knows nothing right now: "
          + (payload.error || "no reason given")));
        return;
      }
      if (payload.blind) {
        feed.appendChild(el("p", "empty",
          "Prometheus answered but is evaluating no rules at all. That is not"
          + " quiet \u2014 an empty alert list looks exactly the same whether the"
          + " rules file loaded or silently failed to."));
        return;
      }
      if (!firing.length && !pending.length) {
        feed.appendChild(el("p", "empty",
          "Nothing is firing. Prometheus answered and is evaluating "
          + payload.rules + " rule" + (payload.rules === 1 ? "" : "s") + "."));
        return;
      }
      firing.forEach(function (a) { feed.appendChild(alertCard(a, "firing")); });
      pending.forEach(function (a) { feed.appendChild(alertCard(a, "pending")); });
    }

    function loadAlerts() {
      fetchPage("/api/alerts")
        .then(function (payload) {
          if (route(window.location.pathname).view !== "alerts") return;
          renderAlerts(payload);
        })
        .catch(function (err) {
          // Same route guard as the catalog page and for the same reason: a
          // fetch still in flight when he taps away must not paint its
          // failure over the page he actually opened.
          if (route(window.location.pathname).view !== "alerts") return;
          stopPolling();
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load alerts: " + err));
        });
    }

    function loadCatalog() {
      fetchPage("/api/catalog")
        .then(function (payload) {
          if (route(window.location.pathname).view !== "catalog") return;
          renderCatalog(payload);
        })
        .catch(function (err) {
          // The same route guard as the heartbeats page, for the same
          // reason: a fetch in flight when he taps away must not paint its
          // failure over the page he actually opened.
          if (route(window.location.pathname).view !== "catalog") return;
          stopPolling();
          markNav();
          feed.textContent = "";
          feed.appendChild(el("p", "empty", "Could not load the catalog: " + err));
        });
    }
    return {
      loadAlerts: loadAlerts,
      loadCatalog: loadCatalog,
      loadHeartbeats: loadHeartbeats,
    };
  };
})();
