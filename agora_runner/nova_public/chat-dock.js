/* The chat dock -- the owner's capture on `ideas.md`, 2026-08-25, rated
 * High: *"A great idea is to have a chat-bot that covers in the bottom
 * right of my page in Nova that i can talk to about anything. Not sure if
 * it should be the same as a commentator on a journal or just a long
 * running session. I see this as maybe a replacement for 'merge' agora
 * into Nova and also the 'ask' page as this gives me a quick way to ask
 * questions or discuss ideas and implementations."*
 *
 * It is the `/ask` thread, reachable from every page instead of only from
 * one tab. Same endpoint, same Agora conversation, same answers -- so a
 * question typed here appears on `/ask` and the other way round, and
 * nothing had to be added to the server at all. `/ask` stays where it is:
 * he said *maybe* a replacement, and deleting a page on a maybe is the
 * one direction that is not one sentence to reverse.
 *
 * The one thing this may not do is put its timer in `livePolls`.
 * `stopPolling()` runs on every navigation and clears that array, so a
 * dock polling out of it goes silent the moment he taps a tab while
 * waiting -- which is the exact minute the answer lands in. It keeps a
 * single handle of its own and clears it before every reschedule, so
 * there is never more than one dock poller alive.
 */
(function () {
  "use strict";

  /* What this needs from `app.js` and does not own. It arrives as one
   * argument rather than through `window` so the seam is a list somebody
   * has to add to on purpose, and a name missing from it is a
   * `ReferenceError` on the first line that uses it rather than a dock
   * that half works. `app.js` calls this at exactly the point in its own
   * body where the dock used to be defined, so the dock still registers
   * `novaOpenChat` and `novaThreadUpdated` before the first route draws.
   *
   * Every one of these is shared with something else on the page -- the
   * `/ask` page, the board rows, the comment drawer -- which is why they
   * stayed in `app.js` rather than coming along. */
  window.novaChatDock = function (shared) {
    var el = shared.el;
    var fetchPage = shared.fetchPage;
    var localStore = shared.localStore;
    var toast = shared.toast;
    var transitionMs = shared.transitionMs;
    var dragSheet = shared.dragSheet;
    var makeActionSheet = shared.makeActionSheet;
    var paintModelPicker = shared.paintModelPicker;
    var renderAskThread = shared.renderAskThread;
    var askPaintSent = shared.askPaintSent;
    var askPaintNote = shared.askPaintNote;
    var mergePendingSends = shared.mergePendingSends;
    var pingAskWatching = shared.pingAskWatching;
    var pingConvWatching = shared.pingConvWatching;
    var HOLD_MS = shared.HOLD_MS;
    var STICK_SLOP_PX = shared.STICK_SLOP_PX;
    var ASK_POLL_MS = shared.ASK_POLL_MS;
    var ASK_POLL_MAX = shared.ASK_POLL_MAX;
    var STEP_SHEET_MIN_VH = shared.STEP_SHEET_MIN_VH;
    var STEP_SHEET_MAX_VH = shared.STEP_SHEET_MAX_VH;
    var STEP_SHEET_DISMISS_VH = shared.STEP_SHEET_DISMISS_VH;

    var btn = document.getElementById("chat-btn");
    var dock = document.getElementById("chat-dock");
    if (!btn || !dock) return;
    var closeBtn = document.getElementById("chat-close");
    var thread = document.getElementById("chat-thread");
    var form = document.getElementById("chat-form");
    var box = document.getElementById("chat-box");
    var send = document.getElementById("chat-send");
    var status = document.getElementById("chat-status");
    var dot = document.getElementById("chat-dot");

    var pollHandle = null;
    var loaded = false;
    var lastCount = 0;
    // Sends the server has not shown back yet, per thread; see `mergePendingSends`.
    var pendingSends = {};
    var isOpen = false;
    /* One-shot: the *next* paint goes to the newest message whatever the
     * scroll position says, and the paint that uses it clears it. Opening the
     * dock and sending a question both set it, because in both of those he
     * has just asked to be at the bottom and the position on screen has not
     * caught up yet. Every paint after that decides from where he actually
     * is. It must not latch -- a version of this that stayed true once set
     * followed the thread down forever, which is the bug it exists to fix. */
    var stickToBottom = true;

    /* Scrolling back through a thread.
     *
     * His capture, `issues.md` 2026-08-31, with a screenshot of this dock:
     * *"I can only see the latest messages in the chat. I can't scroll
     * upwards and see the earlier messages."* Both endpoints answer with the
     * newest `MAX_THREAD` messages and there was no way to ask for the ones
     * before them, so a thread longer than a page had a hard floor -- 79 of
     * the 720 conversations in the store are longer than that.
     *
     * The page grows rather than paging in chunks he has to stitch: reaching
     * the top asks for one `PAGE_STEP` more of the same thread, so the
     * payload is always "the newest N" and nothing has to merge two fetches.
     * `hasMore` comes from the server and is the only thing that stops it.
     * `pendingAnchor` is the scroll height measured before an older page is
     * painted -- the difference after it is exactly how far down the message
     * he was reading has moved. */
    var PAGE_STEP = 40;
    var pageLimit = PAGE_STEP;
    var hasMore = false;
    var loadingOlder = false;
    var pendingAnchor = null;

    var menuBtn = document.getElementById("chat-menu");
    var listEl = document.getElementById("chat-list");
    var titleEl = document.getElementById("chat-title");
    var modelHost = document.getElementById("chat-model-host");

    /* Which thread the dock is showing.
     *
     * His capture, `ideas.md` 2026-08-26: *"Add multi-conversation support
     * to the chat modal: hamburger button top-left opens a list of previous
     * conversations, switchable, modal remembers last-opened conversation.
     * Once done, delete the /ask page entirely as dead code."* The dock
     * talked to exactly one thread -- the `/ask` conversation -- and the
     * only place every other thread was reachable was the Conversations
     * page, which is the page he wants to stop needing.
     *
     * Two shapes behind one panel. `kind: "ask"` is `/api/ask`, which has
     * no id because the server finds its one tagged conversation itself;
     * `kind: "conv"` is `/api/conversations/thread?id=`. The two payloads
     * are already identical -- `{messages, waiting}` -- so only the URLs
     * differ, and nothing had to be added to the server.
     *
     * `sourceToken` is `convOpenId`'s job on the Conversations page: a
     * fetch for the thread he just left must not paint over the one he
     * just opened. A counter rather than an id because "ask" has none. */
    var source = { kind: "ask", id: null, name: "Ask Nova" };
    var sourceToken = 0;
    var recalled = false;

    /* Same store and the same trade as the read marks at the top of this
     * file: per-device, because the server has no session and no idea which
     * browser is his. A browser with storage disabled simply opens on the
     * ask thread every time, which is where the dock opened before this. */
    var CHAT_SOURCE_KEY = "nova.chatSource.v1";

    function rememberSource() {
      var store = localStore();
      if (!store) return;
      try {
        store.setItem(CHAT_SOURCE_KEY, JSON.stringify(source));
      } catch (err) { /* full or disabled: the dock still works */ }
    }

    function recallSource() {
      var store = localStore();
      if (!store) return;
      try {
        var raw = store.getItem(CHAT_SOURCE_KEY);
        if (!raw) return;
        var parsed = JSON.parse(raw);
        // A conversation needs an id to be fetchable at all, so a stored
        // `conv` without one is corrupt and reads as never-stored rather
        // than as a thread that 404s on every open.
        if (!parsed || parsed.kind !== "conv" || !parsed.id) return;
        source = { kind: "conv", id: parsed.id, name: parsed.name || "Conversation",
                   untitled: parsed.untitled === true };
      } catch (err) { /* unreadable: stay on the ask thread */ }
    }

    function threadUrl() {
      if (source.kind === "conv") {
        return "/api/conversations/thread?id=" + encodeURIComponent(source.id)
          + "&limit=" + pageLimit;
      }
      return "/api/ask?limit=" + pageLimit;
    }

    /* The composer grows with what he types. His capture, issues.md
     * 2026-08-25: *"make the input field start at one line, then when the
     * content is two lines it gets tall enough to fit two lines and
     * dynamicly scales up to 10 lines tall which is the cap."*
     *
     * Measure rather than assume: `line-height` computes to a pixel value
     * in every browser that has one set, and this one is set in the
     * stylesheet, but `getComputedStyle` returns the string "normal" when
     * it is not -- so a missing stylesheet must not make the cap NaN and
     * blank the box. `1.4 * font-size` is the same ratio the rule uses.
     *
     * The height is set from `scrollHeight`, which under `box-sizing:
     * border-box` measures content plus padding and never the border, so
     * the two border widths are added back. Setting `height` to "auto"
     * first is what lets the box *shrink* again when he deletes a line:
     * `scrollHeight` can never report less than the current height. */
    var CHAT_BOX_MAX_ROWS = 10;

    function growChatBox() {
      if (!box) return;
      var css = window.getComputedStyle(box);
      // Only a px value is a line height I can multiply. A browser resolves
      // `line-height: 1.4` to px here, but the keyword `normal` and a bare
      // ratio both come back unresolved in some engines, and `parseFloat`
      // would read "1.4" as 1.4 pixels and cap the box at fourteen.
      var line = /px$/.test(css.lineHeight) ? parseFloat(css.lineHeight) : NaN;
      if (!(line > 0)) line = parseFloat(css.fontSize) * 1.4;
      if (!(line > 0)) return;
      var frame =
        (parseFloat(css.paddingTop) || 0) +
        (parseFloat(css.paddingBottom) || 0) +
        (parseFloat(css.borderTopWidth) || 0) +
        (parseFloat(css.borderBottomWidth) || 0);
      var cap = line * CHAT_BOX_MAX_ROWS + frame;
      box.style.height = "auto";
      var wanted = box.scrollHeight +
        (parseFloat(css.borderTopWidth) || 0) +
        (parseFloat(css.borderBottomWidth) || 0);
      var next = Math.min(wanted, cap);
      box.style.height = next + "px";
      // Past the cap it has to scroll, and only past the cap -- a gutter on
      // a one-line box is the thing `overflow-y: hidden` is avoiding.
      box.style.overflowY = wanted > cap ? "auto" : "hidden";
    }
    /* The paperclip, his capture on `issues.md` 2026-08-25: *"Make the new
     * chat be able to display mermaid charts, images and also be able to
     * upload files like all other input fields in the Nova app."* This is
     * the same `buildAttach` the comment drawer, the board composer and the
     * capture box already use -- a tray of chips under the box rather than
     * markdown typed into it -- so there is still exactly one thing on this
     * site that knows how to upload a file.
     *
     * Send is disabled from two independent directions -- an upload in
     * flight and a question in flight -- and they overlap. Two writers of
     * `send.disabled` would race: an upload finishing while the question is
     * still out re-enables the button, and the next tap sends the same text
     * twice. So both set a flag and one function reads them. */
    var uploading = false;
    var sending = false;
    /* Whether Nova is answering right now. Read off `payload.waiting` at the
     * paint below rather than tracked here from the send: the server is the
     * only thing that knows a turn is still running, and a locally-held flag
     * would be wrong every time he reloads the page mid-turn or opens the
     * dock on a thread that was already busy. */
    var turnRunning = false;
    var stopping = false;
    /* Which thread the running turn belongs to, off the same payload. Not
     * `source.id`: the Ask Nova surface has no id of its own -- the server
     * finds its conversation from the `nova-ask` tag -- so reading the id
     * from the composer would leave the stop button dead on the one surface
     * he uses most. */
    var runningConversationId = "";

    /* One button, two jobs -- his ask, 2026-09-07: *"the send button should
     * become a filled square while you're running, so I can cancel the
     * current turn"*. A separate stop button beside Send would sit dead for
     * all but a few minutes of the day and take composer width forever.
     *
     * `type` is what actually switches the behaviour, not the click handler:
     * as a `submit` the form's own listener sends, as a `button` it does
     * nothing and the click handler below takes it. Leaving it a submit and
     * branching inside the handler would fire the form listener too.
     *
     * Enter still sends while a turn is running, deliberately -- he asked
     * for the stop so he could "give you more context and re-ask", and
     * taking away the one thing that already worked mid-turn would be a
     * strange way to grant that. */
    function syncSend() {
      var stop = turnRunning && !sending;
      send.classList.toggle("chat-send--stop", stop);
      send.type = stop ? "button" : "submit";
      send.textContent = stop ? "" : "Send";
      send.setAttribute("aria-label", stop ? "Stop" : "Send");
      send.disabled = stop ? stopping : (uploading || sending);
    }

    send.addEventListener("click", function (event) {
      // The send path is the form's `submit` listener; this only ever runs
      // for the stop shape, which is a plain button and submits nothing.
      if (send.type !== "button") return;
      event.preventDefault();
      if (stopping || !runningConversationId) return;
      stopping = true;
      syncSend();
      status.textContent = "stopping\u2026";
      fetch("/api/conversations/cancel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversationId: runningConversationId }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          stopping = false;
          turnRunning = false;
          syncSend();
          status.textContent = "";
          /* The word he asked for, in place of the loader. Painted here
           * rather than waited for: the next poll is up to four seconds
           * away, and a spinner still spinning after a stop that worked is
           * the thing he is trying to get rid of. The poll repaints over
           * this from the server's own answer, so if the turn did NOT stop
           * the loader comes back -- which is the truth. */
          var pending = thread.querySelector(".ask-pending");
          if (pending) {
            pending.textContent = "stopped";
            pending.classList.add("ask-stopped");
          }
        })
        .catch(function (err) {
          stopping = false;
          syncSend();
          status.textContent = "could not stop: " + err.message;
        });
    });

    var attach = window.novaAttach.build({
      onBusy: function (isBusy) { uploading = isBusy; syncSend(); },
      onStatus: function (text) { status.textContent = text; },
    });
    box.parentNode.insertBefore(attach.tray, box.nextSibling);
    form.appendChild(attach.input);

    /* The `+` drawer -- his ask, 2026-09-07, against Claude's "Add context"
     * sheet: the composer keeps `+` and the model pill, and everything else
     * moves behind the `+`.
     *
     * The three controls are MOVED here, not rebuilt: `attach.button` and
     * the mic already carries its own listeners and its own
     * state (`aria-pressed` while the mic is listening), and a second copy
     * would be a second set of both. Only their parent changes.
     *
     * It borrows `.step-sheet` and friends wholesale rather than growing a
     * third set of drawer styles -- same shape, same grip, same slide, and
     * `dragSheet` is already the one drag both other drawers use. */
    var extrasBackdrop = el("div", "chat-extras-backdrop");
    extrasBackdrop.hidden = true;
    document.body.appendChild(extrasBackdrop);

    /* Its own classes, not the tool sheet's. Sharing `.step-sheet` made
     * `document.querySelector(".step-sheet")` find whichever was built
     * first -- which is this one, since the dock is set up before any tool
     * drawer opens. The look is still shared, from one rule list in the
     * stylesheet; only the identity is separate. */
    var extras = el("div", "chat-extras");
    extras.id = "chat-extras";
    extras.hidden = true;
    extras.setAttribute("role", "dialog");
    extras.setAttribute("aria-modal", "true");
    extras.setAttribute("aria-label", "Add context");

    var extrasGrip = el("div", "chat-extras-grip");
    extrasGrip.setAttribute("aria-hidden", "true");
    extras.appendChild(extrasGrip);

    var extrasHead = el("div", "chat-extras-head");
    var extrasTitles = el("div", "chat-extras-titles");
    extrasTitles.appendChild(el("h2", "chat-extras-title", "Add context"));
    extrasHead.appendChild(extrasTitles);
    var extrasClose = el("button", "chat-extras-close", "✕");
    extrasClose.type = "button";
    extrasClose.title = "Close";
    extrasClose.setAttribute("aria-label", "Close");
    extrasHead.appendChild(extrasClose);
    extras.appendChild(extrasHead);

    var extrasBody = el("div", "chat-extras-body extras-body");
    var extrasGrid = el("div", "extras-grid");
    extrasBody.appendChild(extrasGrid);
    extras.appendChild(extrasBody);
    document.body.appendChild(extras);

    /* A label under each glyph, the way Claude's Camera/Photos/Files read.
     * Appending a child leaves every listener on the button untouched. */
    function asTile(button, label) {
      if (!button) return;
      button.classList.add("extras-tile");
      button.appendChild(el("span", "extras-label", label));
      extrasGrid.appendChild(button);
    }
    asTile(attach.button, "Files");
    /* The model picker left this drawer on 2026-09-11 for the Settings
     * drawer behind `⋮` (below); read-aloud was removed outright then, and
     * the mic followed it on 2026-09-20. This one is "add something to what
     * I am about to send". */


    /* The Settings drawer behind `⋮` -- his ask, 2026-09-11: *"a three
     * stacked dot button on the left side that opens a 'settings' drawer and
     * move the 'choose model' and read out loud to be moved there... in
     * settings we can add a third thing which is 'generate title' that prompts
     * the haiku to re-geneate the title if i notice the conversation drifts
     * too much."*
     *
     * Built on `makeActionSheet`, the same drawer factory the message actions
     * and the capture sheet use, so it slides, drags and dismisses like every
     * other drawer here. Nothing closes it on a tap: it holds three settings,
     * and changing one of them should not take the model picker away.
     *
     * The model host is MOVED in, never rebuilt, for the reason the `+`
     * drawer gave: it carries its own state and handlers. */
    var settingsBtn = document.getElementById("chat-settings");
    var settingsSheet = makeActionSheet({
      className: "msg-sheet--settings",
      openVh: 40,
      minVh: STEP_SHEET_MIN_VH,
      maxVh: STEP_SHEET_MAX_VH,
      dismissVh: STEP_SHEET_DISMISS_VH
    });

    /* Tiles, three to a row -- his ask, 2026-09-11: *"make all of the
     * settings buttons designed like the square ones on the + drawer in a
     * matrix with 3 buttons on each row."* They ARE the `+` drawer's tiles:
     * the same `extras-grid` / `extras-tile` / `extras-label`, with the
     * stylesheet's selectors widened to this sheet, so the two drawers cannot
     * drift apart in looks.
     *
     * Rename was here for an hour and is gone at his ask; the list's own
     * editor still renames. */
    var settingsGrid = el("div", "extras-grid settings-grid");

    function settingsTile(tag, glyph, label) {
      var tile = el(tag, "extras-tile settings-tile");
      if (tag === "button") tile.type = "button";
      var mark = el("span", "chat-glyph", glyph);
      var text = el("span", "extras-label", label);
      tile.appendChild(mark);
      tile.appendChild(text);
      settingsGrid.appendChild(tile);
      return { tile: tile, glyph: mark, label: text };
    }

    /* Model. The picker's own <select> is laid invisibly over the whole
     * tile, so a tap opens the phone's native list -- the picker is MOVED in,
     * not rebuilt, so its catalog, its change handler and every test that
     * finds `.model-pick` keep working. The label reads the chosen name. */
    var modelTile = settingsTile("div", "🧠", "Model");
    modelTile.tile.classList.add("settings-model-tile");
    if (modelHost) modelTile.tile.appendChild(modelHost);
    function paintModelTile() {
      var pick = modelHost && modelHost.querySelector("select");
      var chosen = pick && pick.options && pick.options[pick.selectedIndex];
      modelTile.label.textContent = chosen && chosen.value ? chosen.textContent : "Model";
    }
    if (modelHost) modelHost.addEventListener("change", paintModelTile);

    /* Answer style: one tile that toggles, Brief by default (his call, the
     * same day). Brief is the absence of a tag server-side. */
    var styleTile = settingsTile("button", "📝", "Brief");
    var muteTile = settingsTile("button", "🔔", "Notifications");
    var titleTile = settingsTile("button", "✨", "Generate title");

    /* Theme and colours, which until issue #137 lived only on the `/settings`
     * page: the chat's small settings all sit behind `⋮` now. They are this
     * device's, not the conversation's, so they show in every thread. Each is
     * one tile that steps to the next choice, like Brief/Detailed. */
    var THEMES = [["system", "🌓", "Device"], ["light", "☀️", "Light"], ["dark", "🌙", "Dark"]];
    var themeTile = settingsTile("button", "🌓", "Device");
    var paletteTile = settingsTile("button", "🎨", "Nova");

    function paintAppearance() {
      var look = window.novaAppearance;
      themeTile.tile.hidden = paletteTile.tile.hidden = !look;
      if (!look) return;
      var theme = look.themePreference();
      THEMES.forEach(function (t) {
        if (t[0] !== theme) return;
        themeTile.glyph.textContent = t[1];
        themeTile.label.textContent = t[2];
      });
      var id = look.palettePreference();
      look.PALETTES.forEach(function (p) {
        if (p.id === id) paletteTile.label.textContent = p.label;
      });
    }

    themeTile.tile.addEventListener("click", function () {
      var look = window.novaAppearance;
      if (!look) return;
      var at = THEMES.map(function (t) { return t[0]; }).indexOf(look.themePreference());
      look.setTheme(THEMES[(at + 1) % THEMES.length][0]);
      paintAppearance();
    });

    paletteTile.tile.addEventListener("click", function () {
      var look = window.novaAppearance;
      if (!look) return;
      var ids = look.PALETTES.map(function (p) { return p.id; });
      look.setPalette(ids[(ids.indexOf(look.palettePreference()) + 1) % ids.length]);
      paintAppearance();
    });

    var styleNow = "brief";
    var mutedNow = false;

    function paintPrefs(p) {
      styleNow = p && p.style === "detailed" ? "detailed" : "brief";
      styleTile.label.textContent = styleNow === "detailed" ? "Detailed" : "Brief";
      mutedNow = !!(p && p.muted);
      muteTile.glyph.textContent = mutedNow ? "🔕" : "🔔";
      muteTile.label.textContent = mutedNow ? "Muted" : "Notifications";
      muteTile.tile.setAttribute("aria-pressed", mutedNow ? "true" : "false");
    }

    function convId() {
      return source && source.kind === "conv" ? source.id : "";
    }

    styleTile.tile.addEventListener("click", function () {
      var id = convId();
      if (!id) return;
      var next = styleNow === "brief" ? "detailed" : "brief";
      chatWrite("/api/conversations/style", { id: id, style: next })
        .then(function () { paintPrefs({ style: next, muted: mutedNow }); })
        .catch(function (err) { toast("could not save the style: " + err.message, true); });
    });

    muteTile.tile.addEventListener("click", function () {
      var id = convId();
      if (!id) return;
      var next = mutedNow ? "off" : "on";
      chatWrite("/api/conversations/mute", { id: id, muted: next })
        .then(function () { paintPrefs({ style: styleNow, muted: next === "on" }); })
        .catch(function (err) { toast("could not change notifications: " + err.message, true); });
    });

    titleTile.tile.addEventListener("click", function () {
      var id = convId();
      if (titleTile.tile.disabled || !id) return;
      titleTile.tile.disabled = true;
      titleTile.label.textContent = "Generating…";
      chatWrite("/api/conversations/retitle", { id: id })
        .then(function (named) {
          if (typeof named !== "string" || !named) throw new Error("no title came back");
          // He may have switched threads while Haiku thought about it.
          if (!source || source.id !== id) return;
          source.name = named;
          source.untitled = false;
          titleEl.textContent = named;
          rememberSource();
          if (dock.classList.contains("list-open")) loadList(true);
          toast("Title: " + named);
        })
        .catch(function (err) { toast("could not generate a title: " + err.message, true); })
        .then(function () {
          titleTile.tile.disabled = false;
          titleTile.label.textContent = "Generate title";
        });
    });

    /* Parked in the document, hidden, until the drawer first opens -- the
     * same pattern `#capture-types` uses. Left detached, the model picker
     * paints into a node nothing can see, and nothing else can reach it. */
    var settingsParking = el("div", "settings-parking");
    settingsParking.hidden = true;
    settingsParking.appendChild(settingsGrid);
    document.body.appendChild(settingsParking);

    if (settingsBtn) {
      settingsBtn.addEventListener("click", function () {
        var id = convId();
        // Style, notifications and the title belong to a real conversation;
        // the legacy ask thread has only the model.
        [styleTile, muteTile, titleTile].forEach(function (t) { t.tile.hidden = !id; });
        paintModelTile();
        paintAppearance();
        paintPrefs(null);
        if (id) {
          // Read fresh each open: a cycle, another device or Agora itself
          // may have changed the tags since.
          fetch("/api/conversations/prefs?id=" + encodeURIComponent(id))
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (p) { if (p && p.ok && convId() === id) paintPrefs(p); })
            .catch(function () { /* stays on the defaults it painted */ });
        }
        settingsSheet.open([settingsGrid], "Settings", { closeOnPick: false });
      });
    }

    var plusBtn = document.getElementById("chat-plus");
    var extrasHide = null;

    function setExtras(open) {
      if (extrasHide) { clearTimeout(extrasHide); extrasHide = null; }
      if (plusBtn) plusBtn.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {
        extras.style.height = "";
        extras.classList.add("chat-extras--entering");
        extrasBackdrop.classList.add("chat-extras-backdrop--entering");
        extrasBackdrop.hidden = false;
        extras.hidden = false;
        void extras.offsetHeight;
        extras.classList.remove("chat-extras--entering");
        extrasBackdrop.classList.remove("chat-extras-backdrop--entering");
        return;
      }
      extras.classList.add("chat-extras--entering");
      extrasBackdrop.classList.add("chat-extras-backdrop--entering");
      function hideExtras() {
        extrasHide = null;
        if (extras.classList.contains("chat-extras--entering")) {
          extras.hidden = true;
          extrasBackdrop.hidden = true;
        }
      }
      var extrasWait = transitionMs(extras);
      if (extrasWait) extrasHide = setTimeout(hideExtras, extrasWait + 20);
      else hideExtras();
    }

    if (plusBtn) {
      plusBtn.addEventListener("click", function () { setExtras(extras.hidden); });
    }
    extrasClose.addEventListener("click", function () { setExtras(false); });
    extrasBackdrop.addEventListener("click", function () { setExtras(false); });
    // Picking a file closes the sheet: the picker takes over the screen and
    // coming back to a drawer still sitting there is a second thing to shut.
    if (attach.button) {
      attach.button.addEventListener("click", function () { setExtras(false); });
    }
    dragSheet([extrasGrip, extrasHead], {
      node: function () { return extras; },
      openVh: 42,
      minVh: 24,
      maxVh: 80,
      dismissVh: 14,
      onDismiss: function () { setExtras(false); }
    });


    function setDot(on) {
      if (on) dot.removeAttribute("hidden");
      else dot.setAttribute("hidden", "");
      btn.classList.toggle("chat-btn-unread", !!on);
    }

    /* `STICK_SLOP_PX` is at module scope -- the conversation page needs the
     * same number and measures it against the document instead. */
    function atBottom(node) {
      return node.scrollHeight - node.clientHeight - node.scrollTop <= STICK_SLOP_PX;
    }

    function paint(payload) {
      var messages = (payload && payload.messages) || [];
      /* An answer that arrives while the dock is shut puts a dot on the
       * launcher. Without it a closed dock is where his answer goes to
       * die: the reply takes about a minute, and a minute is long enough
       * to navigate away and forget it was coming.
       *
       * `loaded` is the first-paint guard, and it is here because my
       * reviewer disproved the comment that used to sit in its place. I had
       * deleted it after a mutation check showed no test could see it, and
       * wrote that it could not fire: opening sets `isOpen` before the read,
       * so a shut dock must have been read already. That reasoning assumes
       * the read finishes before he can act again. Open the dock and close
       * it before the fetch lands -- one tap on a slow phone -- and the
       * first paint of the session runs shut, with `lastCount` still 0, so
       * a thread he has read a hundred times lights the dot. A mutation
       * check only ever tests the mutation I thought of. */
      /* His own sends the server has not echoed yet are drawn at the
       * bottom, and a thread holding one is still waiting on an answer. */
      var key = sourceKey();
      var merged = mergePendingSends(messages, pendingSends[key], Date.now());
      pendingSends[key] = merged.pending;
      if (merged.unconfirmed) {
        messages = merged.messages;
        payload = Object.assign({}, payload, { messages: messages, waiting: true });
      }
      if (!isOpen && loaded && messages.length > lastCount) setDot(true);
      lastCount = messages.length;
      /* Read **before** the repaint: a redraw puts `scrollTop` back to 0. */
      // `hasMore` absent means an older server or the empty-thread reply;
      // both are honestly "nothing more to fetch", so the default is false.
      hasMore = !!(payload && payload.hasMore);
      var follow = stickToBottom || atBottom(thread);
      var was = thread.scrollTop;
      var grewFrom = pendingAnchor;
      pendingAnchor = null;
      /* The composer's stop shape follows the same flag the loader does, so
       * the button and the spinner can never disagree about whether a turn
       * is running. */
      turnRunning = !!(payload && payload.waiting);
      runningConversationId = (payload && payload.conversationId)
        || (source.kind === "conv" ? source.id : "");
      syncSend();
      renderAskThread(thread, payload, function (text) {
        askPaintSent(thread, text);
        // Same three lines the composer runs, and for the same reasons:
        // asking is asking to be at the bottom, and his own question is not
        // an unread answer for the dot to light on.
        stickToBottom = true;
        thread.scrollTop = thread.scrollHeight;
        lastCount += 1;
        pollChat(0);
      });
      loaded = true;
      /* The repaint is what moved him, so his place has to be put back either
       * way -- to the newest message when he was already on it, and to the
       * message he was rereading when he was not.
       *
       * His capture on `issues.md`, 2026-08-30: *"Chat auto-scrolls to the
       * bottom every time a new message arrives even if I've scrolled up to
       * reread something -- annoying, should only stick to bottom if I was
       * already near it."* Making the jump conditional on its own would have
       * been worse than the bug: without the `was` branch, a poll landing
       * while he read an old message would have thrown him to the *top* of
       * the thread instead of the bottom. */
      if (isOpen) {
        if (grewFrom !== null) {
          /* An older page has just been prepended. Neither branch below is
           * right for it: the bottom would throw away the scroll back he
           * just asked for, and `was` would leave him at the top of messages
           * he has not read yet. What keeps him on the message he was
           * looking at is the growth -- everything added went in above him. */
          thread.scrollTop = thread.scrollHeight - grewFrom;
        } else {
          thread.scrollTop = follow ? thread.scrollHeight : was;
        }
        stickToBottom = false;
      }
    }

    /* The thread he last read, kept on the device so opening the dock paints
     * before the network answers.
     *
     * His `issues.md` #111, 2026-08-27: *"It takes 4-5 seconds to load the
     * conversation when i open the chat bubble. Please do this background
     * loading when i open the Nova app or use some local storage for it."*
     * Measured from inside the cluster on 2026-08-28, five reads each:
     * `/api/ask` answered in 0.52s-2.04s and `/api/conversations/thread` in
     * 0.03s-0.08s. So the wait is the ask thread and the round trip to it,
     * and his phone reaches that over Tailscale on top -- the dock really
     * did open onto "loading…" and sit there.
     *
     * **One entry per thread, and no cap on how many.** I built the
     * single-entry version first and its own test caught why it does not
     * work: opening the dock reads the remembered thread and would overwrite
     * the entry, so every thread he switches to in the switcher is the slow
     * one again. The size is measured rather than feared -- the live `/api/ask`
     * body is 7,785 bytes and a conversation thread 3,043, against 40
     * conversations in the store on 2026-08-28, so caching every thread he
     * has is around 300KB of a 5MB origin budget. `setItem` is wrapped for
     * `rememberSource`'s reason anyway: a full or disabled store must leave
     * the dock working, not throw out of the paint.
     *
     * **The cached body is painted and never acted on.** `waiting` is what
     * starts a poll, and a stale `true` would poll a thread that was
     * answered an hour ago; `loadThread` fires immediately after this and
     * the network answer is what governs. */
    var CHAT_THREADS_KEY = "nova.chatThreads.v1";

    function sourceKey() {
      return source.kind === "conv" ? "conv:" + source.id : "ask";
    }

    function readThreads() {
      var store = localStore();
      if (!store) return null;
      try {
        var parsed = JSON.parse(store.getItem(CHAT_THREADS_KEY) || "null");
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch (err) {
        // Corrupt: read as empty, not as absent. `null` is reserved for "there
        // is no store", and returning it here would make `cacheThread` skip
        // the write -- so one unparseable value would kill the cache for good
        // instead of being overwritten by the next thread that loads.
        return {};
      }
    }

    function cacheThread(payload) {
      var store = localStore();
      if (!store) return;
      /* Only the newest page is worth keeping. The cache exists to paint the
       * dock before the network answers (`issues.md` #111), and a 500-message
       * thread he happened to scroll back through would be the thing every
       * later open pays to parse -- and `localStorage` is shared by every
       * thread in `held`, so one long one can push the rest out. */
      if (pageLimit > PAGE_STEP) return;
      // Not null: `readThreads` only returns null when there is no store, and
      // that is the line above.
      var held = readThreads();
      held[sourceKey()] = payload;
      try {
        store.setItem(CHAT_THREADS_KEY, JSON.stringify(held));
      } catch (err) { /* full or disabled: the dock still works */ }
    }

    /* True when it painted. An empty cached thread returns false rather than
     * painting nothing, so the "loading…" line still appears for a thread
     * that genuinely has no messages yet -- painting zero messages over the
     * placeholder would show him a blank panel with no sign a fetch was
     * running. */
    function paintCached() {
      var held = readThreads();
      if (!held) return false;
      var body = held[sourceKey()];
      if (!body || !(body.messages || []).length) return false;
      paint(body);
      return true;
    }

    function stopChatPoll() {
      if (pollHandle !== null) {
        clearTimeout(pollHandle);
        pollHandle = null;
      }
    }

    function pollChat(attempts) {
      stopChatPoll();
      if (attempts >= ASK_POLL_MAX) return;
      var token = sourceToken;
      pollHandle = setTimeout(function () {
        pollHandle = null;
        // Vouch for whichever thread is painted, not for the ask thread
        // regardless. Presence is per conversation on Agora's side and always
        // was; until `/api/conversations/watching` existed this could only
        // name the tagged one, so a switched-to thread got no vouch at all
        // and buzzed his phone about a message he was watching arrive.
        pingAskWatching(isOpen && source.kind === "ask");
        pingConvWatching(isOpen && source.kind === "conv", source.id);
        fetchPage(threadUrl(), { poll: true })
          .then(function (payload) {
            if (token !== sourceToken) return;
            paint(payload);
            cacheThread(payload);
            // A send the server has not shown back yet keeps the poll going
            // too, or a thread whose tail is Nova's would stop confirming it.
            if (payload.waiting || (pendingSends[sourceKey()] || []).length) pollChat(attempts + 1);
          })
          // A failed poll is not a failed answer, same as `pollConv`.
          .catch(function () {
            if (token !== sourceToken) return;
            pollChat(attempts + 1);
          });
      }, ASK_POLL_MS);
    }

    /* One more page of the same thread, asked for by reaching the top.
     *
     * The guard is `loadingOlder` rather than a debounce on the scroll event:
     * a fetch takes long enough that a phone flicking at the top would send
     * several, and each answer repaints, so the second one would land on a
     * thread the first had already moved under him. */
    function loadOlder() {
      if (loadingOlder || !hasMore || !isOpen) return;
      loadingOlder = true;
      pageLimit += PAGE_STEP;
      /* Measured before the fetch and handed to `paint` only in the `.then`.
       * Setting `pendingAnchor` here instead would give it to whichever paint
       * happens next -- and while he is waiting for a reply the four-second
       * poll is repainting the same thread, so an ordinary poll landing
       * first would consume the anchor against unchanged content, compute a
       * growth of zero, and drop him at the top of the thread. That is the
       * exact moment this feature is for. */
      var anchor = thread.scrollHeight;
      var token = sourceToken;
      fetchPage(threadUrl(), { poll: true })
        .then(function (payload) {
          if (token !== sourceToken) return;
          pendingAnchor = anchor;
          paint(payload);
        })
        .catch(function () {
          // Put the page size back: a failed fetch has painted nothing, so
          // leaving it raised would make the next poll silently ask for a
          // page he never got, and the retry on the next flick impossible.
          if (token !== sourceToken) return;
          pageLimit -= PAGE_STEP;
        })
        .then(function () { loadingOlder = false; });
    }

    /* How close to the top counts as asking for more. Bigger than
     * `STICK_SLOP_PX`'s job -- this one wants to fire slightly *before* he
     * hits the ceiling, so the older messages are already arriving by the
     * time he gets there rather than after a visible stop. */
    var OLDER_TRIGGER_PX = 96;

    thread.addEventListener("scroll", function () {
      if (thread.scrollTop <= OLDER_TRIGGER_PX) loadOlder();
    });

    function loadThread() {
      var token = sourceToken;
      return fetchPage(threadUrl(), { poll: true })
        .then(function (payload) {
          if (token !== sourceToken) return;
          paint(payload);
          cacheThread(payload);
          /* Here and not in `switchTo`, because the Ask row has no id of its
           * own -- `nova_ask` finds its conversation by tag, and the thread
           * payload is the first thing on this side that knows which one it
           * is. `pollChat` deliberately does not do this: the model behind a
           * thread changes when he changes it, and re-fetching the listing
           * every four seconds to re-answer that would be the cost
           * `model_choice` is a separate route to avoid. */
          paintModelPicker(modelHost, payload.conversationId);
          if (payload.waiting) pollChat(0);
        })
        .catch(function (err) {
          if (token !== sourceToken) return;
          // Only paint an error over an empty dock. Overwriting a thread he
          // can already read with "could not load" would be the wrong
          // report -- the messages on screen are still true.
          if (!loaded) {
            askPaintNote(thread, "Could not load the thread: " + err);
          }
        });
    }

    /* The switcher.
     *
     * `Ask Nova` is pinned at the top as its own row rather than left to
     * arrive in the listing: the `/ask` thread *is* in `/api/conversations`
     * -- it is a normal Agora conversation carrying the `nova-ask` tag --
     * but it is reachable through an endpoint that needs no id, and letting
     * both rows exist would give one thread two identities and two unread
     * counts. So it is filtered out below and pinned here.
     *
     * The list replaces the thread inside the panel instead of floating
     * over it, so the composer cannot be typed into while it is up and
     * there is only ever one scrolling region.
     */
    function setList(next) {
      var on = !!next;
      if (!listEl || !menuBtn) return;
      // A query typed over one view means nothing in the other: a thread
      // name is not a message, so switching either way starts clean.
      clearSearch();
      findOpen = false;
      dock.classList.toggle("list-open", on);
      menuBtn.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) listEl.removeAttribute("hidden");
      else listEl.setAttribute("hidden", "");
      showSearch();
      if (on) loadList();
    }

    /* Search -- the last piece of issue #138: *"Chat is missing: search
     * (within a conversation and across the conversation list)"*.
     *
     * One box, two jobs, decided by what the panel is showing. With the
     * switcher open it is always there and filters the conversations by
     * name, persona and model; over a thread it is behind the 🔍 in the
     * header and filters that thread's messages by their text. Both hide
     * what does not match rather than highlighting, so what is left on a
     * phone screen is only the answer. Nothing is fetched: the list and
     * the thread are already in the page, so it filters as he types.
     *
     * The thread repaints itself on every poll and every streamed token,
     * so the filter is re-applied by an observer rather than once -- a
     * message that arrives while he is searching obeys the query too. */
    var searchEl = document.getElementById("chat-search");
    var findBtn = document.getElementById("chat-find");
    var findOpen = false;

    function searchQuery() {
      return searchEl ? searchEl.value.trim().toLowerCase() : "";
    }

    function textHas(text, q) {
      return (text || "").toLowerCase().indexOf(q) !== -1;
    }

    function filterList(q) {
      if (!listEl) return 0;
      var hits = 0;
      [].forEach.call(listEl.querySelectorAll(".chat-list-fold"), function (fold) {
        var inFold = 0;
        [].forEach.call(fold.querySelectorAll(".chat-list-row"), function (row) {
          var hit = !q || textHas(row.textContent, q);
          row.hidden = !hit;
          if (hit) inFold += 1;
        });
        hits += inFold;
        fold.hidden = !!q && !inFold;
        // A match inside a shut fold is opened to show it, and shut again
        // when the query goes, so searching never rearranges his folds.
        if (q && inFold && !fold.open) {
          fold.open = true;
          fold.setAttribute("data-search-opened", "");
        } else if (!q && fold.hasAttribute("data-search-opened")) {
          fold.open = false;
          fold.removeAttribute("data-search-opened");
        }
      });
      return hits;
    }

    function filterThread(q) {
      if (!thread) return 0;
      var hits = 0;
      [].forEach.call(thread.querySelectorAll(".ask-msg"), function (msg) {
        var body = msg.querySelector(".ask-text");
        var hit = !q || textHas((body || msg).textContent, q);
        if (msg.hidden !== !hit) msg.hidden = !hit;
        if (hit) hits += 1;
      });
      return hits;
    }

    function applySearch() {
      if (!searchEl) return;
      var q = searchQuery();
      var hits = dock.classList.contains("list-open") ? filterList(q) : filterThread(q);
      searchEl.classList.toggle("chat-search--none", !!q && !hits);
    }

    function clearSearch() {
      if (!searchEl) return;
      searchEl.value = "";
      searchEl.classList.remove("chat-search--none");
      filterList("");
      filterThread("");
    }

    function showSearch() {
      if (!searchEl) return;
      var listing = dock.classList.contains("list-open");
      searchEl.hidden = !(listing || findOpen);
      var label = listing ? "Search conversations" : "Search this chat";
      searchEl.placeholder = label;
      searchEl.setAttribute("aria-label", label);
      if (findBtn) findBtn.hidden = listing;
    }

    if (searchEl) {
      searchEl.addEventListener("input", applySearch);
      searchEl.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;
        event.stopPropagation();
        clearSearch();
        if (!dock.classList.contains("list-open")) {
          findOpen = false;
          showSearch();
        }
      });
    }
    if (findBtn) {
      findBtn.addEventListener("click", function () {
        findOpen = !findOpen;
        if (!findOpen) clearSearch();
        showSearch();
        if (findOpen && searchEl) searchEl.focus();
      });
    }
    if (thread && searchEl && window.MutationObserver) {
      new MutationObserver(function () {
        if (searchQuery() && !dock.classList.contains("list-open")) applySearch();
      }).observe(thread, { childList: true, subtree: true });
    }

    function listRow(label, meta, current, onPick) {
      var row = el("button", "chat-list-row" + (current ? " current" : ""));
      row.setAttribute("type", "button");
      row.appendChild(el("div", "chat-list-name", label));
      if (meta) row.appendChild(el("div", "chat-list-meta", meta));
      row.addEventListener("click", onPick);
      return row;
    }

    /* Swipe a row left to archive it -- his ask, 2026-09-07.
     *
     * `archived` is a flag Agora has always carried and the listing has
     * always filtered on; `POST /api/conversations/archive` is the write
     * that sets it, and it is deliberately a different route from `delete`.
     * A gesture this cheap to make by accident must not be able to reach an
     * irreversible write, which is why this archives and offers an undo
     * rather than asking him to confirm a delete.
     *
     * Left only. A row that follows a finger in both directions reads as a
     * carousel, and there is nothing to the right of it.
     */
    var SWIPE_ARM_PX = 12;
    var SWIPE_ARCHIVE_PX = 96;

    function swipeToArchive(row, conv) {
      var startX = 0;
      var startY = 0;
      var dx = 0;
      var sliding = false;
      var decided = false;

      function reset() {
        row.style.transition = "transform 160ms ease";
        row.style.transform = "";
        row.classList.remove("chat-list-row--sliding");
        setTimeout(function () { row.style.transition = ""; }, 180);
        sliding = false;
        decided = false;
        dx = 0;
      }

      function move(e) {
        if (!sliding) return;
        dx = e.clientX - startX;
        if (!decided) {
          // Vertical wins ties: this list scrolls, and a scroll that turns
          // into an archive because the finger drifted is the failure that
          // matters here.
          var dy = Math.abs(e.clientY - startY);
          if (Math.abs(dx) < SWIPE_ARM_PX && dy < SWIPE_ARM_PX) return;
          if (dy > Math.abs(dx)) { end(); return; }
          decided = true;
          row.classList.add("chat-list-row--sliding");
        }
        if (e.preventDefault) e.preventDefault();
        // Rightward travel is pinned at 0 rather than followed.
        row.style.transform = "translateX(" + Math.min(0, dx) + "px)";
        row.classList.toggle("chat-list-row--armed", dx <= -SWIPE_ARCHIVE_PX);
      }

      function end() {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        var far = dx <= -SWIPE_ARCHIVE_PX;
        row.classList.remove("chat-list-row--armed");
        if (!far) { reset(); return; }
        // Off the edge first, then the write: the row leaving is the
        // feedback, and waiting for a round trip to start it makes the
        // gesture feel like it did not take.
        row.style.transition = "transform 160ms ease, opacity 160ms ease";
        row.style.transform = "translateX(-110%)";
        row.style.opacity = "0";
        chatWrite("/api/conversations/archive", { id: conv.id })
          .then(function () {
            toast("Archived “" + conv.name + "”");
            loadList(true);
          })
          .catch(function (err) {
            // It is still there, so it comes back rather than vanishing on
            // a write that did not happen.
            row.style.opacity = "";
            reset();
            toast("could not archive: " + err.message, true);
          });
        sliding = false;
        decided = false;
      }

      row.addEventListener("pointerdown", function (e) {
        if (sliding) return;
        // A held row opens the editor; that gesture owns the row once it
        // fires, and this one must not also be running underneath it.
        if (row.classList.contains("chat-row-edit")) return;
        sliding = true;
        decided = false;
        dx = 0;
        startX = e.clientX;
        startY = e.clientY;
        if (row.setPointerCapture && e.pointerId !== undefined) {
          try { row.setPointerCapture(e.pointerId); } catch (err) { /* not supported */ }
        }
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", end);
        window.addEventListener("pointercancel", end);
      });

      // A row that travelled is not a row that was tapped.
      row.addEventListener("click", function (e) {
        if (Math.abs(dx) >= SWIPE_ARM_PX) {
          e.preventDefault();
          e.stopImmediatePropagation();
        }
      }, true);
    }

    /* One write against a conversation, resolved or thrown.
     *
     * The four routes answer `{ok, message}` and a refusal he can fix
     * carries a 400, so a rejected promise here always has a sentence in it
     * that is worth showing him -- which is why nothing on this path
     * swallows. */
    /* `chatWrite` throws away everything but `result`, which is right for
     * the five writes that answer with one string. `/api/conversations/new`
     * answers with an id *and* the name it settled on, so this one hands the
     * whole object back. `chatWrite` is written in terms of it rather than
     * beside it -- one place that decides what a failed write looks like. */
    function chatWriteFull(path, body) {
      return fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) {
            throw new Error((result && (result.message || result.error)) || "failed");
          }
          return result;
        });
    }

    function chatWrite(path, body) {
      return chatWriteFull(path, body).then(function (answer) {
        return answer.result;
      });
    }

    /* The edit options a held row turns into.
     *
     * His capture, `issues.md` 2026-08-27, rated 🔴 Immediately: *"I need
     * the chat bubble to be able to start ned conversations, delete them,
     * change name, organize like move to a folder. Editing by pressing the
     * conversation for 1 sec and it gives me edit options."*
     *
     * The gesture is `HOLD_MS`, the same second his board rows use, because
     * it is the same instruction -- issue #84's *"If i hold the card for
     * more than 1 second i get into edit mode"*. Reusing the number rather
     * than picking one means a future change to how long a hold is stays
     * one edit.
     *
     * The panel replaces the row in place instead of opening a modal, for
     * the board editor's reason: the dock is a small panel on a phone and a
     * second layer over it would cover the list he is choosing from.
     *
     * **Delete asks, and it is the only one that does.** Rename and move
     * are both undoable by doing them again; a deleted conversation is
     * gone, and Agora unbinds any heartbeat pointing at it on the way out,
     * so there is nothing to put back.
     */
    function rowEditor(row, folders, models, done) {
      var wrap = el("div", "chat-row-edit");

      var name = document.createElement("input");
      name.type = "text";
      name.className = "chat-row-edit-name";
      name.value = row.name;
      name.setAttribute("aria-label", "Conversation name");
      wrap.appendChild(name);

      var pick = document.createElement("select");
      pick.className = "chat-row-edit-folder";
      pick.setAttribute("aria-label", "Folder");
      function option(value, label, selected) {
        var o = document.createElement("option");
        o.value = value;
        o.textContent = label;
        if (selected) o.selected = true;
        return o;
      }
      pick.appendChild(option("", "No folder", !row.folderId));
      folders.forEach(function (f) {
        pick.appendChild(option(f.id, f.name, f.id === row.folderId));
      });
      // A folder he has not made yet. Chosen from the same control as the
      // ones that exist, because "move it into a new folder called X" is
      // one intention and making him create the folder first would be two.
      pick.appendChild(option("+new", "New folder…", false));
      wrap.appendChild(pick);

      /* Which model answers in this thread.
       *
       * Idea #95 opens with it: *"It is hard to change model for a
       * conversation because that means changing the model for all other
       * conversations that personas is in."* Agora moved `model` off the
       * persona and onto the conversation on 08-21, so the write has worked
       * for six days -- from Agora's own app, which is not the one he opens.
       * This is that fix arriving at the door he uses.
       *
       * A model the catalog does not list still gets an option, selected,
       * so the picker can never silently repoint a thread at something else
       * just because Agora's catalog moved on. Same reason the folder list
       * starts with the one the row is already in.
       *
       * `(metered)` is on the label for `newChatForm`'s reason and it is a
       * money reason, not a cosmetic one: a metered model spends the prepaid
       * balance per token, and a picker that hides which ones do would let
       * him spend it by picking the top of a list.
       */
      var modelPick = document.createElement("select");
      // Two classes: the first is the folder select's styling, the second is
      // this control's own hook. Sharing only the styling class would make
      // `querySelector(".chat-row-edit-folder")` ambiguous in the panel.
      modelPick.className = "chat-row-edit-folder chat-row-edit-model";
      modelPick.setAttribute("aria-label", "Model");
      var listed = false;
      models = models || [];
      models.forEach(function (m) {
        if (m.id === row.model) listed = true;
        modelPick.appendChild(option(
          m.id, m.label + (m.metered ? " (metered)" : ""), false));
      });
      if (row.model && !listed) {
        modelPick.insertBefore(option(row.model, row.model, false), modelPick.firstChild);
      }
      if (!row.model) {
        modelPick.insertBefore(option("", "Model (unset)", false), modelPick.firstChild);
      }
      // Set after the options exist rather than with `selected` on each one.
      // A select auto-selects its first option, and the reset algorithm then
      // keeps only the *last* option marked selected -- so an option marked
      // at creation and inserted at the front loses to the auto-selected one
      // behind it, and the picker opens on a model the thread is not on.
      // Caught by the browser test on a conversation whose model the catalog
      // no longer lists; the same bug made an untouched save post a change.
      modelPick.value = row.model || "";
      // No catalog, no picker. `_model_rows` returns `[]` when Agora cannot
      // say what it accepts, and a select holding only the model the thread
      // already has is a control that cannot change anything.
      if (models.length) wrap.appendChild(modelPick);

      var newFolder = document.createElement("input");
      newFolder.type = "text";
      newFolder.className = "chat-row-edit-name";
      newFolder.placeholder = "New folder name";
      newFolder.setAttribute("aria-label", "New folder name");
      newFolder.hidden = true;
      wrap.appendChild(newFolder);
      pick.addEventListener("change", function () {
        newFolder.hidden = pick.value !== "+new";
        if (!newFolder.hidden) newFolder.focus();
      });

      var note = el("p", "chat-row-edit-note", "");
      var foot = el("div", "chat-row-edit-foot");
      var save = el("button", "chat-row-edit-save", "Save");
      save.setAttribute("type", "button");
      var cancel = el("button", "chat-row-edit-cancel", "Cancel");
      cancel.setAttribute("type", "button");
      var drop = el("button", "chat-row-edit-delete", "Delete");
      drop.setAttribute("type", "button");
      /* Delete on the left, Save on the right -- his ask, 2026-09-07, and
       * the safer arrangement of the two: the destructive button moves off
       * the corner his thumb rests on, and Save lands where every other
       * primary action in this panel already is (Send, directly below it). */
      foot.appendChild(drop);
      foot.appendChild(cancel);
      foot.appendChild(save);
      wrap.appendChild(foot);
      wrap.appendChild(note);

      function busy(on) {
        save.disabled = on;
        drop.disabled = on;
        cancel.disabled = on;
      }

      cancel.addEventListener("click", function () { done(false); });

      save.addEventListener("click", function () {
        var wanted = name.value.trim();
        if (!wanted) {
          note.textContent = "A conversation needs a name.";
          return;
        }
        busy(true);
        note.textContent = "saving…";
        // The folder is resolved first because creating it can fail, and a
        // rename that landed beside a failed move would leave the row half
        // changed with no way to tell which half.
        var folder = Promise.resolve(pick.value === "+new"
          ? chatWrite("/api/conversations/folder", { name: newFolder.value.trim() })
          : pick.value);
        folder
          .then(function (folderId) {
            var work = [];
            if (wanted !== row.name) {
              work.push(chatWrite("/api/conversations/rename",
                { id: row.id, name: wanted }));
            }
            if ((folderId || "") !== (row.folderId || "")) {
              work.push(chatWrite("/api/conversations/move",
                { id: row.id, folderId: folderId || "" }));
            }
            // Only when it moved. Re-sending the model it already has would
            // audit a change that did not happen on every rename.
            if (modelPick.value && modelPick.value !== (row.model || "")) {
              work.push(chatWrite("/api/conversations/model",
                { id: row.id, model: modelPick.value }));
            }
            return Promise.all(work).then(function () { return wanted; });
          })
          .then(function (saved) {
            // The dock title is the one copy of this name outside the list,
            // so a rename of the thread he is reading has to move it too.
            if (source.kind === "conv" && source.id === row.id) {
              source.name = saved;
              titleEl.textContent = saved;
              rememberSource();
            }
            done(true);
          })
          .catch(function (err) {
            busy(false);
            note.textContent = "Could not save: " + err.message;
          });
      });

      drop.addEventListener("click", function () {
        if (!window.confirm("Delete \u201c" + row.name + "\u201d? This cannot be undone.")) return;
        busy(true);
        note.textContent = "deleting…";
        chatWrite("/api/conversations/delete", { id: row.id })
          .then(function () {
            // He may have been reading the thread he just deleted. Falling
            // back to the ask thread is the only source that is guaranteed
            // to still exist -- `nova_ask` finds it by tag and creates it
            // if it is missing.
            if (source.kind === "conv" && source.id === row.id) {
              switchTo({ kind: "ask", id: null, name: "Ask Nova" });
            }
            done(true);
          })
          .catch(function (err) {
            busy(false);
            note.textContent = "Could not delete: " + err.message;
          });
      });

      return wrap;
    }

    /* Press-and-hold on a conversation row. Same shape as the board's, and
     * the comment there is the one that explains it: the timer is cleared on
     * every way a press can end, not just on let-go, because a pending
     * `setTimeout` that fires into a detached node has already broken this
     * suite once. `held` suppresses the click the browser sends afterwards,
     * so a hold does not also switch threads. */
    function holdToEdit(node, open) {
      var timer = null;
      var held = false;
      function end() {
        if (timer) { clearTimeout(timer); timer = null; }
      }
      function start() {
        end();
        held = false;
        timer = setTimeout(function () {
          timer = null;
          held = true;
          open();
        }, HOLD_MS);
      }
      ["mousedown", "touchstart"].forEach(function (name) {
        node.addEventListener(name, start);
      });
      ["mouseup", "mouseleave", "touchend", "touchcancel", "touchmove", "scroll"]
        .forEach(function (name) { node.addEventListener(name, end); });
      node.addEventListener("click", function (event) {
        if (!held) return;
        held = false;
        event.preventDefault();
        event.stopPropagation();
      }, true);
    }

    /* What the header says while the listing that knows the real name is
     * still in flight. A uuid would be worse and an empty header reads as
     * broken, so it is briefly generic and then replaced. */
    var UNNAMED_THREAD = "Conversation";

    /* Each thread ranks by its newest message, sent or received -- his ask,
     * `issues.md` 2026-09-19: *"Sort the chats based on latest message
     * sent/received. Not based on last opened."*
     *
     * Until then the rank was the later of "he opened it" (kept per device in
     * localStorage) and "it moved", from his earlier ask of 2026-09-07. Opening
     * a thread to read it pushed it to the top without anything new in it, so
     * the order stopped saying where the conversation was. `updatedAt` is
     * Agora's `lastMessageAt` (`nova_conversations.conversations()`), which is
     * exactly the newest message either way, so it is the whole rank now.
     * A `nova.convOpened.v1` map left on a phone is simply no longer read.
     */
    function rowRank(row) {
      return Date.parse(row.updatedAt || "") || 0;
    }

    function switchTo(next) {
      // Tapping the row he is already reading closes the list and leaves
      // the thread alone -- reloading it would blank a painted thread and
      // scroll him back to the bottom for nothing.
      var same = next.kind === source.kind &&
        (next.kind !== "conv" || next.id === source.id);
      setList(false);
      if (same) return;
      source = next;
      sourceToken += 1;
      rememberSource();
      stopChatPoll();
      // A fresh thread has its own message count, and `loaded` is what stops
      // the first paint of it lighting the unread dot on a thread he is
      // looking at right now.
      loaded = false;
      lastCount = 0;
      // A new thread opens on its newest page, whatever he had scrolled back
      // to on the last one.
      pageLimit = PAGE_STEP;
      hasMore = false;
      loadingOlder = false;
      pendingAnchor = null;
      titleEl.textContent = source.name;
      /* And the picker goes with it, before anything is painted.
       *
       * `modelHost` is a singleton in `index.html`, not a node rebuilt per
       * thread, and the picker for the new thread only arrives when its own
       * `loadThread` fetch lands. Without this the previous thread's picker
       * stays on screen -- live, and closed over the previous thread's id --
       * over messages that are already the new thread's, because
       * `paintCached` repaints them instantly. Changing it in that window
       * repoints the thread he just left, at a model he was choosing for the
       * one he is looking at. Reviewer caught it. */
      paintModelPicker(modelHost, "");
      thread.textContent = "";
      // Only the thread the cache actually holds paints instantly; every
      // other row in the switcher still shows the placeholder.
      if (!paintCached()) askPaintNote(thread, "loading…");
      loadThread();
    }

    /* A fold in the switcher, same `<details>` shape as the sidebar groups.
     *
     * His capture, `issues.md` 2026-08-26, two minutes apart: *"the list of
     * conversations are just filled with heartbeats"*, then *"I ment not
     * just filled with heartbeats, other converssations aswell but the
     * beats take up a lot of space and i have to scroll past them. Maybe
     * folders are the right solutions here aswell."*
     *
     * Measured against the live store the same morning: 40 non-archived
     * conversations, **30 of them cycle threads**. So the switcher opened
     * on 39 rows of which three quarters were a heartbeat he has never
     * needed to reopen, and the nine threads he might want were below them.
     *
     * The count goes in the summary because a shut fold otherwise hides how
     * much it is hiding, which is the failure the sidebar folds do not have
     * (there, the group name tells you what is inside). Nothing is dropped
     * -- every thread is still one tap away, which is the difference
     * between a fold and a filter. */
    function listFold(title, count, open) {
      var fold = el("details", "chat-list-fold");
      if (open) fold.setAttribute("open", "");
      var summary = el("summary", "chat-list-group");
      summary.appendChild(el("span", "chat-list-group-name", title));
      summary.appendChild(el("span", "chat-list-group-count", String(count)));
      fold.appendChild(summary);
      // The rows live in a `<div>` rather than directly in the `<details>`
      // for `.nav-fold-body`'s reason: overriding `display` on a `<details>`
      // replaces the box its closed-state hiding is built on.
      fold.appendChild(el("div", "chat-list-fold-body"));
      return fold;
    }

    /* The switcher's last answer, kept so re-opening it is not a blank wait.
     *
     * Issue #141: *"loads slowly every single time it's opened, not just on
     * cold start -- worth investigating whether it's re-fetching/re-querying
     * everything instead of caching."* It is not re-querying more than once:
     * `/api/conversations` is one fetch per open and always was. What it is
     * is slow -- measured Cycle 764 against the live store, that fetch takes
     * 0.4s-1.3s, because Agora's own `/conversations` answers 1.33MB for 782
     * rows and 871KB of that is a `personality` string per row that nothing
     * on this page reads. The list showed the word "loading…" for all of it,
     * every single open, including the ninth one in a row.
     *
     * So the last payload is kept and painted immediately on the next open,
     * and the fetch still runs unconditionally behind it. **Nothing is
     * suppressed and no answer is delayed** -- the same bytes arrive at the
     * same moment and repaint the list. That is why this is not the caching
     * `nova_site` refuses on purpose for this route: a cached *response*
     * would let a reply he is waiting for go unreported, and this cannot,
     * because it never skips a request.
     *
     * Anything that just changed the list passes `fresh`, so a rename, a
     * delete or a new thread never repaints the row he acted on in its old
     * form.
     */
    var listCache = null;
    var listToken = 0;

    /* ...and the same payload survives a reload, which the in-memory cache
     * above could not.
     *
     * His report, 2026-09-07: *"When i open the list its loading the
     * conversations and i have to wait for some time, every time."* The
     * cache above already fixed the second and ninth open of one page load;
     * it did nothing for the first, and on a phone that is reopened all day
     * the first open is most of them. So the last answer goes to
     * `localStorage` and is painted before the fetch is even sent.
     *
     * The trade is unchanged and is the one the comment above argues: the
     * request still goes out every single time and nothing is suppressed, so
     * a reply he is waiting for cannot go unreported -- a stale list costs
     * one repaint a moment later.
     *
     * Capped, because this payload grows with his history and a
     * multi-megabyte synchronous write on every open would cost more than
     * the wait it removes. Over the cap it simply is not persisted; the
     * in-memory cache still works for the rest of that page load. */
    var LIST_CACHE_KEY = "nova.convList.v1";
    var LIST_CACHE_MAX = 400000;

    function loadCachedList() {
      var store = localStore();
      if (!store) return null;
      try {
        var raw = store.getItem(LIST_CACHE_KEY);
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        return (parsed && parsed.conversations) ? parsed : null;
      } catch (err) {
        return null;
      }
    }

    function saveCachedList(payload) {
      var store = localStore();
      if (!store || !payload) return;
      try {
        var raw = JSON.stringify(payload);
        if (raw.length > LIST_CACHE_MAX) {
          store.removeItem(LIST_CACHE_KEY);
          return;
        }
        store.setItem(LIST_CACHE_KEY, raw);
      } catch (err) { /* full or disabled: the list still loads */ }
    }

    function loadList(fresh) {
      if (fresh) listCache = null;
      // The stored copy is only ever read to fill an empty first open --
      // `fresh` means something just changed the list, and a cache written
      // before that change must not be drawn over the top of it.
      if (!listCache && !fresh) listCache = loadCachedList();
      // Open, shut, open leaves two fetches in flight; the older one must
      // not land second and become the cache.
      var token = ++listToken;
      // Under Preact a refresh keeps a drawn list up (issue #233, step 7).
      var drawn = fresh && window.novaThread && listEl.novaThreadRows
        && listEl.novaThreadRows.length > 1;
      if (listCache) {
        renderList(listCache);
      } else if (!drawn) {
        renderShell();
        listEl.appendChild(el("p", "empty", "loading…"));
      }
      fetchPage("/api/conversations")
        .then(function (payload) {
          if (token !== listToken) return;
          listCache = payload;
          saveCachedList(payload);
          /* Only ever replaces the placeholder, never a name already on
           * screen: a thread he is mid-rename on must not be relabelled by
           * a listing that predates the rename. */
          if (source.kind === "conv" && source.name === UNNAMED_THREAD) {
            var named = (payload.conversations || []).filter(function (row) {
              return row.id === source.id;
            })[0];
            if (named && named.name) {
              source.name = named.name;
              titleEl.textContent = named.name;
              rememberSource();
            }
          }
          // An editor he opened mid-refresh is not repainted away; the
          // cache is updated, so the next open shows it.
          if (listEl.querySelector(".chat-row-edit")) return;
          renderList(payload);
        })
        .catch(function (err) {
          if (token !== listToken) return;
          // A failed refresh over a list already on screen leaves that list
          // up. The rows he can see are the last ones that were true, and
          // replacing them with an error message is a worse answer than a
          // slightly old list.
          if (listCache) return;
          renderShell();
          listEl.appendChild(
            el("p", "empty", "Could not load your conversations: " + err));
        });
    }

    /* The two rows that are there whatever the listing says. */
    function renderShell() {
      listEl.textContent = "";
      listEl.novaThreadRows = null;
      // The label is the button's own text, not a `.chat-list-name` child:
      // that class means "a conversation is called this", and anything
      // reading the list -- a stylesheet, a test, a future feature counting
      // threads -- would be right to treat a node carrying it as a thread.
      // One tap on a floating `+` -- his ask, 2026-09-07.
      var start = el("button", "chat-list-fab", "+");
      start.setAttribute("type", "button");
      start.title = "New conversation";
      start.setAttribute("aria-label", "New conversation");
      start.addEventListener("click", function () {
        if (start.disabled) return;
        start.disabled = true;
        chatWriteFull("/api/conversations/new", { name: nextUntitledName() })
          .then(function (answer) {
            /* `result` IS the new id, a string, and `name` rides beside it
             * -- the contract `/api/conversations/new` has answered with
             * since 08-27 (#445), pinned server-side by
             * `test_chat_write_result_key.py`.
             *
             * His report, 2026-09-11: *"I'm not able to start new
             * converssations"*, with the toast "could not start: no
             * conversation came back". This read `answer.result.id` since
             * the 09-07 switcher rebuild (#863), and a string has no `.id`
             * -- so every tap created a conversation on the server and then
             * told him it had not. The browser test passed because its
             * fixture was written in the shape this code wanted rather than
             * the shape the server sends. */
            var id = typeof answer.result === "string" ? answer.result : "";
            if (!id) throw new Error("no conversation came back");
            // Straight into it: he tapped `+` to start talking, not to
            // watch a list redraw. `switchTo` shuts the switcher for us.
            switchTo({ kind: "conv", id: id, name: answer.name || UNTITLED_LABEL });
            listCache = null;
          })
          .catch(function (err) { toast("could not start: " + err.message, true); })
          .then(function () { start.disabled = false; });
      });
      listEl.appendChild(start);
      // No "Ask Nova" row (his ask, 09-07); `kind: "ask"` still opens.
    }

    /* "New chat", or the first free "New chat - N". Only the suffix: the
     * server defaults an empty name, and `autotitle` renames either
     * spelling. Best-effort: with no listing cached it sends the default. */
    var UNTITLED_LABEL = "New chat";

    function nextUntitledName() {
      var rows = (listCache && listCache.conversations) || [];
      var taken = {};
      rows.forEach(function (row) { taken[(row.name || "").trim()] = true; });
      if (!taken[UNTITLED_LABEL]) return "";
      for (var n = 2; n < 500; n += 1) {
        var candidate = UNTITLED_LABEL + " - " + n;
        if (!taken[candidate]) return candidate;
      }
      return "";
    }

    function renderList(payload) {
      var folders = payload.folders || [];
      var models = payload.models || [];
      var rows = (payload.conversations || []).filter(function (row) {
        return (row.tags || []).indexOf("nova-ask") === -1;
      });
      // Under Preact the folds go through `thread.js` (issue #233, step 7):
      // an unchanged fold keeps its node and the open or shut he gave it.
      var drawn = window.novaThread ? [] : null;
      if (drawn) {
        var fab = listEl.novaListFab;
        if (!fab) {
          renderShell();
          fab = listEl.novaListFab = listEl.firstChild;
        }
        drawn.push({ node: fab, key: "fab", sig: "fab" });
      } else {
        renderShell();
      }
      if (!rows.length) {
        var none = el("p", "empty", "No other conversations yet.");
        if (drawn) window.novaThread.render(listEl, drawn.concat([{ node: none, key: "empty", sig: NaN }]));
        else listEl.appendChild(none);
        return;
      }
      // `cycleThread` is `nova_conversations.conversations()`'s own flag
      // for an `evolve-cycle:` tag. Reading the tag here as well would
      // be a second copy of that rule in a second language.
      // Newest message first, inside every group. The
      // grouping itself is unchanged -- folders, loose threads and the
      // heartbeat folds are still the folds; this is the order within them.
      rows = rows.slice().sort(function (a, b) { return rowRank(b) - rowRank(a); });
      var beats = rows.filter(function (row) { return !!row.cycleThread; });
      var loose = rows.filter(function (row) { return !row.cycleThread; });

      function fill(fold, group) {
        var body = fold.lastChild;
        group.forEach(function (row) {
          var meta = [row.personaName, row.model].filter(Boolean).join(" · ");
          var node = listRow(
            row.name, meta,
            source.kind === "conv" && source.id === row.id,
            function () {
              switchTo({ kind: "conv", id: row.id, name: row.name });
            });
          swipeToArchive(node, row);
          holdToEdit(node, function () {
            if (listEl.querySelector(".chat-row-edit")) return;
            var editor = rowEditor(row, folders, models, function (changed) {
              if (changed) {
                // Out first, or the repaint is refused for an open editor.
                if (editor.parentNode) editor.parentNode.removeChild(editor);
                loadList(true);
              } else if (editor.parentNode) editor.parentNode.replaceChild(node, editor);
            });
            body.replaceChild(editor, node);
          });
          body.appendChild(node);
        });
        return fold;
      }

      /* His folders, each as its own fold.
       *
       * His capture, `issues.md` 2026-08-27: *"organize like move to a
       * folder"*. Agora has carried `folderId` on a conversation all
       * along and the switcher grouped by the `evolve-cycle:` tag
       * instead, so a folder he made was invisible here -- the rows in
       * it fell into "Conversations" with everything else.
       *
       * A folder that holds nothing is deliberately still drawn. An
       * empty fold is how he can see the folder exists, and dropping it
       * would make a folder disappear the moment its last conversation
       * moved out -- with no way to move anything back into it. */
      var filed = {};
      folders.forEach(function (folder) {
        filed[folder.id] = loose.filter(function (row) {
          return row.folderId === folder.id;
        });
      });
      var top = loose.filter(function (row) {
        return !row.folderId || !filed[row.folderId];
      });

      /* The folds themselves are ordered by what is in them -- his ask,
       * 2026-09-07: *"make also the folder with the latest messages be the
       * folder on top"*. A fold ranks as its newest row, on the same
       * newest-message rank the rows inside it are sorted by,
       * so the two orders cannot disagree with each other.
       *
       * `Conversations` and `Heartbeats` are ranked with the named folders
       * rather than pinned under them: a folder he last touched in August
       * sitting above the thread that answered a minute ago is the same
       * complaint one level up.
       *
       * An empty folder ranks 0 and sinks, but is still drawn -- that is
       * the rule the comment above states, and sorting must not quietly
       * turn "last" into "gone". */
      function foldRank(group) {
        return (group || []).reduce(function (best, row) {
          return Math.max(best, rowRank(row));
        }, 0);
      }
      function holdsCurrent(group) {
        return source.kind === "conv" && (group || []).some(function (row) {
          return row.id === source.id;
        });
      }

      var folds = folders.map(function (folder) {
        var group = filed[folder.id];
        return { name: folder.name, group: group, open: holdsCurrent(group) };
      });
      // Open on the threads he starts, shut on the ones the loop starts
      // for itself -- and open a fold anyway when it holds the thread he
      // is reading, so the switcher never opens without the current row
      // on screen.
      if (top.length) folds.push({ name: "Conversations", group: top, open: true });
      if (beats.length) {
        folds.push({ name: "Heartbeats", group: beats, open: holdsCurrent(beats) });
      }

      folds.sort(function (a, b) { return foldRank(b.group) - foldRank(a.group); });
      var here = source.kind === "conv" ? source.id : "";
      folds.forEach(function (fold) {
        if (!drawn) {
          listEl.appendChild(fill(listFold(fold.name, fold.group.length, fold.open), fold.group));
          return;
        }
        var sig = JSON.stringify([fold.open, here, folders, models, fold.group]);
        var kept = (listEl.novaThreadRows || []).filter(function (r) {
          return r.key === "fold:" + fold.name && r.sig === sig;
        })[0];
        drawn.push({ key: "fold:" + fold.name, sig: sig,
          node: kept ? kept.node : fill(listFold(fold.name, fold.group.length, fold.open), fold.group) });
      });
      if (drawn) window.novaThread.render(listEl, drawn);
      if (searchQuery()) applySearch();
    }

    /* The close animation has to finish before `hidden` lands, because
     * `[hidden]` is `display: none` and a display change cancels a
     * transition outright. So the attribute is deferred, and the handle is
     * kept so re-opening mid-close can cancel it -- without that, a quick
     * shut-then-open leaves a timer that hides a dock he has just
     * reopened. */
    var DOCK_ANIM_MS = 240;
    var pendingHide = null;

    /* The dock is a draggable drawer on a phone -- his ask, 2026-09-07:
     * "make the chat modal a draggable drawer like the tools modal". Same
     * `dragSheet` the tool sheet uses, so there is one drag in this file
     * and not two.
     *
     * `enabled` is the breakpoint: above 30rem the dock is a fixed panel
     * over its launcher (see `.chat-dock` in style.css) and dragging it
     * taller would resize something anchored to a corner. The query is
     * read per gesture rather than cached, because a phone that turns
     * sideways crosses it without reloading the page. */
    // 100, not 92: in the PWA the missing 8% was visible as a strip of page
    // above the drawer. His earlier capture on this panel -- "make the new
    // chat modal full height, atleast for mobile. It feels so small" -- is
    // the reason it opens at the ceiling at all.
    var DOCK_OPEN_VH = 100;
    var DOCK_MIN_VH = 30;
    var DOCK_DISMISS_VH = 16;
    function dockIsDrawer() {
      return !!(window.matchMedia
                && window.matchMedia("(max-width: 30rem)").matches);
    }
    var dockDrag = dragSheet(
      [document.getElementById("chat-grip"), dock.querySelector(".chat-head")]
        .filter(Boolean),
      {
        node: function () { return dock; },
        openVh: DOCK_OPEN_VH,
        minVh: DOCK_MIN_VH,
        maxVh: DOCK_OPEN_VH,
        dismissVh: DOCK_DISMISS_VH,
        enabled: dockIsDrawer,
        onDismiss: function () { setOpen(false); }
      });

    /* The one way in from outside this closure.
     *
     * A push notification's URL used to render the Conversations *page*;
     * his ask, 2026-09-07: *"when i click the Nova notification it opens the
     * /ask page or something, not the chat modal... I only use that chat
     * modal for the conversations."* So the router calls this instead, and
     * the tap lands in the panel he actually reads.
     *
     * The name is looked up from the listing when it is cached and falls
     * back to a placeholder the first paint replaces -- a title is worth
     * being briefly generic for, and guessing one from the id would put a
     * uuid in the header. */
    window.novaOpenChat = function (conversationId) {
      if (!conversationId) return;
      var known = ((listCache && listCache.conversations) || []).filter(
        function (row) { return row.id === conversationId; })[0];
      setOpen(true);
      switchTo({ kind: "conv", id: conversationId,
                 name: (known && known.name) || UNNAMED_THREAD });
      // Nothing is cached on a cold start -- which is exactly the case a
      // notification tap is -- so the listing is fetched for the name
      // alone. `loadList` caches it, so this costs the request the next
      // switcher open would have made anyway.
      if (!known) loadList();
    };

    /* Is an answer waiting for him right now, from before this page load?
     *
     * `paint` above lights the dot when a thread he is looking at grows,
     * which only works while the tab is open on that thread. His
     * `ideas.md` #182: *"The floating chat bubble in the Nova app will
     * then get highlighted whenever a response is in"* -- and the case
     * that mattered was the one this could not see, opening the app on a
     * phone hours later with an answer already sitting in a thread.
     *
     * One request per page load, not a poll: `/api/conversations/waiting`
     * costs Agora a full conversation listing plus a heartbeat listing,
     * and the thread he has open is already covered by `paint`.
     *
     * A failure leaves the dot alone. The dot means "there is something
     * here"; a fetch that did not answer is not a reason to say that, and
     * it is not a reason to clear one `paint` has already lit either. */
    function checkWaiting() {
      fetchPage("/api/conversations/waiting")
        .then(function (payload) {
          if (isOpen) return;
          if (payload && payload.count > 0) setDot(true);
        })
        .catch(function () { /* the dot stays exactly as it was */ });
    }
    checkWaiting();

    /* The service worker's retract, landing in the dock instead of the page.
     *
     * `sw.js` hands back a parked thread on a notification tap, then
     * refetches behind that answer and posts when the two differ. The page
     * that used to repaint on it is deleted; the dock is where the thread is
     * now, so it reloads if it is showing that conversation and ignores the
     * message otherwise. */
    window.novaThreadUpdated = function (conversationId) {
      if (!conversationId) return;
      if (source.kind !== "conv" || source.id !== conversationId) return;
      loadThread();
    };

    function setOpen(next) {
      isOpen = !!next;
      btn.setAttribute("aria-expanded", isOpen ? "true" : "false");
      dock.setAttribute("aria-hidden", isOpen ? "false" : "true");
      if (pendingHide) { clearTimeout(pendingHide); pendingHide = null; }
      if (isOpen) {
        // Unhidden already in its closed position, then released on the
        // next frame -- same reason the tool sheet does this: setting
        // `hidden` and the final position in one frame renders as an
        // instant appearance with the transition skipped.
        dock.classList.add("chat-dock--closed");
        dock.removeAttribute("hidden");
        // Back to full height on every open. Without this, a dock he
        // dragged shut at 16vh would reopen at 16vh -- the drag would have
        // quietly become a setting.
        if (dockIsDrawer()) dockDrag.setHeight(DOCK_OPEN_VH);
        else dock.style.height = "";
        void dock.offsetHeight;
        dock.classList.remove("chat-dock--closed");
      } else {
        dock.classList.add("chat-dock--closed");
        function hideDock() {
          pendingHide = null;
          // Guarded: `isOpen` is the live answer, and it may have flipped
          // back while this was waiting.
          if (!isOpen) dock.setAttribute("hidden", "");
        }
        var dockWait = Math.min(transitionMs(dock), DOCK_ANIM_MS);
        if (dockWait) pendingHide = setTimeout(hideDock, dockWait + 20);
        else hideDock();
      }
      dock.classList.toggle("open", isOpen);
      btn.classList.toggle("open", isOpen);
      // The full-screen sheet has to hide the hamburger, and CSS cannot
      // reach it from here -- `.menu-btn` is an earlier sibling. The class
      // goes on `body` so the media query can do it; on a wide screen the
      // dock is a small panel and the rule does not apply.
      document.body.classList.toggle("chat-open", isOpen);
      // Closing deliberately leaves the poll running: the question is still
      // being answered and the dot is how he finds out it landed. The
      // switcher does collapse, so re-opening lands on the thread rather
      // than on a list he left up three pages ago.
      if (!isOpen) {
        setList(false);
        return;
      }
      setDot(false);
      /* Opening always lands on the newest message. A shut dock is
       * `display: none`, where `scrollTop` reads 0 in a real browser, so
       * without this the first paint after an open would find him "at the
       * top" and hold him there -- which is the bug this is meant to fix,
       * pointing the other way. */
      stickToBottom = true;
      /* Read the remembered thread once per page load, on the first open
       * rather than at construction: `localStorage` is one synchronous
       * read and there is no reason to spend it on a dock he never taps.
       * After that `source` is the live value and the store is only its
       * durable copy. */
      if (!recalled) {
        recalled = true;
        recallSource();
        titleEl.textContent = source.name;
      }
      /* Opening the dock deliberately does not focus the box. It used to,
       * and his capture on `issues.md`, 2026-08-25: *"The new chat bubble
       * autoselects the input box which makes my mobile keyboard open up
       * and push everything up immediately. It should push everything up,
       * but not when i just want to read a new message."*
       *
       * The dot on the launcher means an answer has landed, so the common
       * reason to open this is to read, not to type -- and on a phone the
       * focus took half the screen for a keyboard he had not asked for,
       * over the message he opened it to see. Tapping the box still opens
       * the keyboard, which is the half he says is right. The `/ask` page
       * has never focused its box on render; this makes the dock agree
       * with it. */
      // After the panel is on screen, never before: `scrollHeight` on a
      // `display: none` element is 0, and a box measured shut collapses to
      // its padding.
      growChatBox();
      /* Paint what he last read before the fetch, then let the fetch paint
       * over it. `loaded` is the guard: once the dock has been painted this
       * page load the DOM is already ahead of the cache, and repainting from
       * it would put an older thread back on screen. */
      if (!loaded) paintCached();
      loadThread();
    }

    if (box) box.addEventListener("input", growChatBox);
    btn.addEventListener("click", function () { setOpen(!isOpen); });
    if (closeBtn) closeBtn.addEventListener("click", function () { setOpen(false); });
    if (menuBtn) {
      menuBtn.addEventListener("click", function () {
        setList(!dock.classList.contains("list-open"));
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape" || !isOpen) return;
      // Escape backs out one level at a time: the switcher first, the dock
      // only once the switcher is down. Closing the whole panel from an
      // open list would lose the thread he was trying to get back to.
      if (dock.classList.contains("list-open")) setList(false);
      else setOpen(false);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var text = box.value.trim();
      // A tray with a picture in it and nothing typed is still a real
      // message -- he sends the screenshot and asks about it next.
      if (!text && !attach.count()) return;
      var body = [text, attach.markdown()].filter(Boolean).join("\n\n");
      sending = true;
      syncSend();
      status.textContent = "sending…";
      // Two endpoints, one composer. `/api/ask` finds its own conversation
      // from the `nova-ask` tag and takes no id; every other thread is
      // addressed by one.
      var conv = source.kind === "conv";
      var token = sourceToken;
      // Everything `autotitle` needs, read before the send rather than after
      // it: he can switch threads while the request is in flight, and a
      // title derived here must land on the thread he typed it into.
      //
      // The safety checks are all on the server -- it refuses a name he
      // typed, and it refuses a name it derived unless the thread has spent
      // two messages off that topic. Those rules live there so they are
      // spelled once. The condition here is only structural: the opening
      // message can name an untitled thread, and a re-title is impossible
      // before his third message because the server wants two in a row.
      var mine = thread.querySelectorAll(".ask-msg.ask-mine");
      /* The first message only -- his call, 2026-09-11, *"Set once."* This
       * used to fire on every message after the second as well, so the
       * server could re-title a thread it thought had drifted; that is what
       * renamed his threads to his latest line. Drift is now his button, in
       * Settings. */
      var titleFor = conv && source.untitled
        ? { id: source.id, name: source.name, text: text,
            // Every message of his in the thread, oldest first, not this
            // one. The server reads two different things out of it: which
            // message the current title was derived from -- that is how it
            // knows it wrote the title rather than him, with no stored flag
            // -- and what he was talking about just before this. Truncating
            // at 200 cannot change the first: a derived title is cut from
            // the first sentence and clipped to 60 characters, so it never
            // reads past 200 anyway.
            recent: [].map.call(mine, function (row) {
              var body = row.querySelector(".ask-text");
              return (body ? body.textContent : "").slice(0, 200);  // not-prose: nothing is shown, it is what the server reads a title out of
            }) }
        : null;
      fetch(conv ? "/api/conversations/send" : "/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          conv ? { conversationId: source.id, text: body } : { text: body }),
      })
        .then(function (r) { return r.json().catch(function () { return {}; }); })
        .then(function (result) {
          if (!result || !result.ok) throw new Error((result && (result.message || result.error)) || "failed");
          box.value = "";
          // A sent ten-line question leaves a ten-line empty box behind
          // unless the height is recomputed; `input` does not fire on a
          // programmatic assignment.
          growChatBox();
          // Emptied only now, the same rule the box follows: a tray cleared
          // on the tap loses the picture when the send is refused.
          attach.clear();
          status.textContent = "";
          sending = false;
          syncSend();
          // The send landed, but he may have switched threads while it was
          // in flight -- painting his message into the thread he moved to
          // would put it under the wrong name, and polling would then be
          // polling the new thread on the old one's schedule.
          if (token !== sourceToken) return;
          var sentKey = sourceKey();
          var sentAt = Date.now();
          (pendingSends[sentKey] = pendingSends[sentKey] || []).push(
            { text: body, sentAt: sentAt });
          // Paint his question straight away rather than waiting a poll for
          // the server to echo it, for `pollConv`'s reason: a box that has
          // gone blank with nothing to show for it reads as a lost message.
          // Same painter as the re-ask; a hand copy here once lost the loader.
          askPaintSent(thread, body, sentAt);
          // Sending is him asking to be at the bottom, whatever he was
          // rereading a second ago -- so the answer to this question lands on
          // his screen rather than below it.
          stickToBottom = true;
          thread.scrollTop = thread.scrollHeight;
          // His own message is not an unread answer.
          lastCount += 1;
          pollChat(0);
        })
        .then(function () {
          if (!titleFor) return;
          return chatWrite("/api/conversations/autotitle", titleFor)
            .then(function (named) {
              if (typeof named !== "string" || !named) return;
              // The thread was named; only the screen still on it moves.
              if (token !== sourceToken || source.id !== titleFor.id) return;
              source.name = named;
              source.untitled = false;
              titleEl.textContent = named;
              rememberSource();
              if (dock.classList.contains("list-open")) loadList(true);
            })
            // A thread that keeps the placeholder is a worse name, not a
            // failed send -- his message is already posted and the error
            // line under the box would say otherwise.
            .catch(function () { });
        })
        .catch(function (err) {
          sending = false;
          syncSend();
          status.textContent = "could not send: " + err.message;
        });
    });
  };
})();
