/* The step sheet (issue #233, step 19).
 *
 * The eleventh piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js` and `plan.js`: the collapsed "thought
 * for / used N tools" line under an answer and the bottom sheet it opens,
 * with its drag, its tool detail view and its sheet-height constants.
 *
 * It borrows three names, all function declarations, and hands eleven back.
 * `app.js` binds it at the exact place the block used to sit, because
 * `actionSheet` and `stackedSheet` read the sheet-height constants while
 * `app.js` is still loading, a few hundred lines further down.
 */
(function () {
  "use strict";

  window.novaSteps = function (shared) {
    var appendRichText = shared.appendRichText;
    var el = shared.el;
    var transitionMs = shared.transitionMs;

    /* --- The work behind an answer, as one line and a drawer ---------------
     *
     * His capture, `issues.md` 2026-09-01: *"The streaming of the thoughts in
     * a conversation show up as multiple bubbles and i do not like that. It
     * would be better to replecate Claude mobile app patter where the thoughts
     * and tools are compressed to one clickable line that opens a modal drawer
     * from the bottom that can be dragged upwards, but contains the thoughts as
     * text, but if tools have been used it is showed as a list of clickable
     * lines and when clicked, the "input" and "output" of the tool is shown.
     * ... Please research on how Claude mobile does this and try to replecate
     * it exactly."*
     *
     * He sent four screenshots and they are the specification, so this follows
     * them rather than my own idea of the same thing:
     *
     * - the collapsed line is dim, inline in the flow above the prose it
     *   preceded, and carries a `›` -- it is not a bubble and not a button
     *   that looks like one;
     * - the sheet rises from the bottom with a grab handle, a centred title
     *   and an `✕` on the left;
     * - tapping a tool row pushes a second view *inside the same sheet* --
     *   back arrow, the tool's name, its status under it, then `Inputs` and
     *   `Output`. His WebFetch screenshot is exactly that.
     *
     * Everything in `steps` comes from the server (`nova_conversations`), and
     * the one thing it does not carry is a tool's output: capped at 20,000
     * characters each, forty to a window, so they are fetched one at a time
     * when he opens that row. */

    /* What the collapsed line says.
     *
     * Claude mobile names the tool when there is one thing to name ("Used
     * ToolSearch") and generalises when there is not ("Browsed the web").
     * Same rule: name it when the block ran exactly one distinct capability,
     * count otherwise, and fall back to the thinking when no tool ran at all.
     *
     * The count is of steps, not of the two halves of each call -- the server
     * already folded those.
     *
     * **A block that holds prose says so, even when it also ran a tool**, and
     * that half is his issue of 2026-09-12: *"when one assistant turn contains
     * multiple text segments interleaved with tool calls (text, then a tool
     * call, then more text), only the LAST text segment reaches [the owner's]
     * client -- earlier segments are silently dropped."* They were not dropped.
     * I read the live thread (conversation ee039370, 18:25 Oslo): the 1,429
     * characters explaining idea #106 to him were posted, folded into this
     * block, and drawn behind a line that read **"Used nova_capture"** -- a
     * tool name, above the one-sentence closing bubble "Saved." Nothing on the
     * screen said there was a paragraph written to him inside it, so he had no
     * reason to tap, and from where he sat the answer had vanished.
     *
     * So the label leads with the writing when there is any. This does not move
     * prose back into bubbles -- his capture of 2026-09-01 asked for exactly
     * one collapsed line and I am not undoing it -- it makes the one line
     * honest about what is behind it. Same principle as pairing a priority
     * symbol with its word: if he has to open something to find out that I
     * said anything, I have not said it. */
    function stepsLabel(steps) {
      var names = [];
      var thoughts = 0;
      steps.forEach(function (step) {
        if (step.kind === "tool") {
          if (names.indexOf(step.capability) === -1) names.push(step.capability);
        } else {
          thoughts += 1;
        }
      });
      var tools = steps.length - thoughts;
      if (!tools) return thoughts === 1 ? "Thought about it" : "Thought it through";
      var used = names.length === 1 ? "Used " + names[0] : "Used " + tools + " tools";
      if (!thoughts) return used;
      var wrote = thoughts === 1 ? "Wrote a passage" : "Wrote " + thoughts + " passages";
      return wrote + " \u00b7 " + used;
    }

    /* The one sheet, appended to <body> and reused, the way `.prio-menu` is.
     * A sheet per message would be a hundred hidden dialogs in a long thread,
     * and only one can ever be open. */
    var stepSheet = null;
    var stepSheetBackdrop = null;
    var stepSheetBody = null;
    var stepSheetTitle = null;
    var stepSheetSub = null;
    var stepSheetBack = null;
    var stepSheetClose = null;

    /* What the open sheet is showing, so the four-second poll can keep it
     * current instead of it drawing whatever it was opened with.
     *
     * That staleness was the one limit left on his issue #168 after the drawer
     * itself shipped: a call that was still running when he opened it said
     * "close and open this again for the output", and steps that arrived while
     * he was reading never appeared at all. Both surfaces that draw a thread
     * repaint it whole every four seconds; this is what carries the sheet along
     * with them.
     *
     * `key` is the message the steps hang off. A real message has an Agora id;
     * the block for work with no answer under it yet has none, because the
     * server invents that row (`visible_rows`), so it gets a sentinel that no
     * id can collide with. */
    var STEP_SHEET_PENDING_KEY = "\u0000steps-only";
    var stepSheetOn = null;

    function stepMessageKey(message) {
      if (message.id) return String(message.id);
      return message.stepsOnly ? STEP_SHEET_PENDING_KEY : "";
    }

    /* Enough of a step to tell "nothing moved" from "something did", and
     * nothing more. A repaint that changed none of this must not redraw the
     * sheet: he would lose his place in a long list of tool rows every four
     * seconds, which is the same complaint as the thread scrolling under him
     * (issue #140). `status` is in here because that is the field that turns
     * a running call into a finished one. */
    /* What a step *is*, ignoring everything that legitimately changes under it:
     * a call is the same call once it finishes, and its arguments arrive in two
     * halves. So what it is and which one it is, and nothing else. */
    function stepIdentity(step) {
      return [step.kind, step.capability || "", step.id || "",
              step.text || ""].join("");
    }

    function stepSignature(steps) {
      return steps.map(function (step) {
        return [step.kind, step.capability || "", step.id || "",
                step.status || "", step.input || "", step.text || ""].join("");
      }).join("");
    }

    function buildStepSheet() {
      if (stepSheet) return stepSheet;
      stepSheetBackdrop = el("div", "step-backdrop");
      stepSheetBackdrop.hidden = true;
      stepSheetBackdrop.addEventListener("click", closeStepSheet);
      document.body.appendChild(stepSheetBackdrop);

      stepSheet = el("div", "step-sheet");
      stepSheet.hidden = true;
      stepSheet.setAttribute("role", "dialog");
      stepSheet.setAttribute("aria-modal", "true");

      /* The grab handle. It is a real control, not decoration: he asked for a
       * drawer "that can be dragged upwards", and `dragStepSheet` below is
       * what does it. */
      var grip = el("div", "step-grip");
      grip.setAttribute("aria-hidden", "true");
      stepSheet.appendChild(grip);

      var head = el("div", "step-head");
      stepSheetBack = el("button", "step-back", "‹");
      stepSheetBack.type = "button";
      stepSheetBack.title = "Back to the list";
      stepSheetBack.setAttribute("aria-label", "Back to the list");
      stepSheetBack.hidden = true;
      head.appendChild(stepSheetBack);
      var titles = el("div", "step-titles");
      stepSheetTitle = el("h2", "step-title", "");
      titles.appendChild(stepSheetTitle);
      stepSheetSub = el("div", "step-sub", "");
      stepSheetSub.hidden = true;
      titles.appendChild(stepSheetSub);
      head.appendChild(titles);
      /* Appended last so it sits on the right -- his ask, 2026-09-07, and the
       * side the chat dock's × has always been on. The two panels are the
       * same object on a phone and a control that swaps sides between them is
       * one he has to look for twice. */
      stepSheetClose = el("button", "step-close", "✕");
      stepSheetClose.type = "button";
      stepSheetClose.title = "Close";
      stepSheetClose.setAttribute("aria-label", "Close");
      stepSheetClose.addEventListener("click", closeStepSheet);
      head.appendChild(stepSheetClose);
      stepSheet.appendChild(head);

      stepSheetBody = el("div", "step-body");
      stepSheet.appendChild(stepSheetBody);
      document.body.appendChild(stepSheet);
      // The header drags too: the grip alone is a thin strip to aim at on a
      // phone, and every sheet he has used elsewhere drags by its whole top.
      dragStepSheet([grip, head]);
      return stepSheet;
    }

    /* Drag the sheet taller.
     *
     * Pointer events rather than touch events: one code path covers his phone
     * and a mouse, and jsdom can drive it. The height is a percentage of the
     * viewport written onto the node, so nothing here has to know the CSS.
     *
     * Bounded at both ends deliberately. A sheet dragged past the top has
     * nowhere further to go and one dragged to nothing leaves a strip he
     * cannot grab again -- and a floor-only clamp is the shape that let a
     * previous cycle's mutation raise a cap to 8GiB with every test green. */
    // Where the sheet opens: enough to read a few steps without covering
    // the message it belongs to, which is the height his screenshot shows.
    var STEP_SHEET_OPEN_VH = 55;
    var STEP_SHEET_MIN_VH = 25;
    var STEP_SHEET_MAX_VH = 92;
    // Dragged below this, the sheet closes rather than sitting there --
    // his ask, 2026-09-07: "Make it close when i drag it all the way
    // down/out of the screen at the bottom." It is under MIN on purpose:
    // MIN is where the sheet *rests* if he lets go early, and this is the
    // point past which he has clearly meant to throw it away.
    var STEP_SHEET_DISMISS_VH = 14;
    /* One drag, two drawers.
     *
     * The tool sheet and the chat dock are the same object on a phone -- a
     * panel pinned to the bottom whose height he sets with his thumb -- so
     * they share this rather than carrying two copies that drift. `spec`
     * names the four heights and what closing means; everything the two
     * differ on is in there and nothing else is.
     *
     *   node()      the element being resized, or null when it is not on
     *               screen. A function because the tool sheet builds its
     *               node lazily.
     *   openVh      what `current()` answers before anything has been set.
     *   minVh       where it rests if he lets go early.
     *   maxVh       the ceiling.
     *   dismissVh   drag below this and `onDismiss` runs instead. Under
     *               minVh on purpose: min is a resting place, this is the
     *               point past which he has clearly meant to throw it away.
     *   enabled()   optional; false means the handles do nothing, which is
     *               how the chat dock stays a fixed panel on a wide screen.
     */
    /* `dvh`, not `vh`, for anything sized against the screen here.
     *
     * His report, 2026-09-07: the chat drawer went full height in the browser
     * and stopped short of the top in the installed PWA. `vh` is the *largest*
     * viewport -- the one with the URL bar scrolled away -- so in a browser a
     * 92vh drawer is taller than the visible area and reads as full height,
     * while in a PWA (where the viewport already is the screen, and nothing is
     * hiding) the same 92% leaves a visible strip of page above it. `dvh`
     * tracks the viewport that is actually on screen, so one number means the
     * same thing in both.
     *
     * Feature-detected rather than assumed: an engine without `dvh` gets the
     * old behaviour instead of a height it cannot parse, which would leave the
     * drawer with no height at all. */
    var SHEET_UNIT = (function () {
      try {
        if (window.CSS && window.CSS.supports && window.CSS.supports("height", "1dvh")) {
          return "dvh";
        }
      } catch (err) { /* fall through */ }
      return "vh";
    }());

    /* What the capture sheet opens at, taller than the tool drawer's 55.
     * His screenshot, 2026-09-08: the Importance row was cut in half by the
     * bottom of the screen -- *"open a bit more by default as the buttons are
     * just slightly out of screen."* Its content is known and fixed (three
     * headings and three rows), unlike the tool drawer's, so this is a height
     * chosen to fit it rather than a compromise: 68 clears the third row with
     * room to see it is the last one, and still leaves the box he typed in
     * visible above the sheet. */
    var CAPTURE_SHEET_OPEN_VH = 68;

    function dragSheet(handles, spec) {
      var from = 0;
      var startVh = 0;
      var dragging = false;

      function current() {
        var node = spec.node();
        var raw = parseFloat((node && node.style.height) || "");
        return isNaN(raw) ? spec.openVh : raw;
      }

      function setHeight(vh, floorVh) {
        var node = spec.node();
        if (!node) return spec.openVh;
        var floor = floorVh === undefined ? spec.minVh : floorVh;
        var clamped = Math.max(floor, Math.min(spec.maxVh, vh));
        node.style.height = clamped + SHEET_UNIT;
        return clamped;
      }

      function move(e) {
        if (!dragging) return;
        var vh = window.innerHeight || 1;
        var next = startVh + ((from - e.clientY) / vh) * 100;
        if (next < spec.dismissVh) {
          // Thrown away rather than resized. `end` first, so the listeners
          // are gone before the panel is hidden and a stray move cannot
          // resize something he can no longer see.
          end();
          spec.onDismiss();
          return;
        }
        setHeight(next, spec.dismissVh);
      }

      function end() {
        dragging = false;
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", end);
        window.removeEventListener("pointercancel", end);
        // Let go short of the dismiss point and it springs back to the
        // resting floor, rather than staying at a height too small to read
        // or to grab again.
        var node = spec.node();
        if (node && !node.hidden && current() < spec.minVh) setHeight(spec.minVh);
      }

      function onDown(e) {
        if (spec.enabled && !spec.enabled()) return;
        // A tap on the close, back or menu button is a tap on that button,
        // not a grab of the panel behind it.
        if (e.target && e.target.closest && e.target.closest("button")) return;
        // One drag at a time -- the same guard `attachRowDrag` uses for the
        // project/milestone lists, so a second finger landing on the grip
        // cannot restart the gesture mid-drag.
        if (dragging) return;
        dragging = true;
        from = e.clientY;
        startVh = current();
        // Without capture the gesture is the phone's to take back the moment
        // it decides to -- see the comment on `pointercancel` below, and
        // `attachRowDrag` above, where the same call fixed the same class of
        // bug for the drag lists.
        var node = e.currentTarget;
        if (node && node.setPointerCapture && e.pointerId !== undefined) {
          try { node.setPointerCapture(e.pointerId); } catch (err) { /* not supported */ }
        }
        window.addEventListener("pointermove", move);
        window.addEventListener("pointerup", end);
        // The phone hands a drag to native scrolling/zooming as "cancelled"
        // rather than "up" whenever it decides the gesture is ambiguous --
        // measured on his own issue, "stuck halfway... can't slide up and
        // down". Without this, that cancellation left `move`/`end` listening
        // forever: `dragging` was never cleared, so the grip's own next
        // `pointerdown` was ignored by the guard above, and the panel read
        // as jammed at whatever height the aborted drag left it.
        window.addEventListener("pointercancel", end);
        if (e.preventDefault) e.preventDefault();
      }

      for (var i = 0; i < handles.length; i++) {
        handles[i].addEventListener("pointerdown", onDown);
      }
      return { setHeight: setHeight, current: current };
    }

    function dragStepSheet(handles) {
      dragSheet(handles, {
        node: function () { return stepSheet; },
        openVh: STEP_SHEET_OPEN_VH,
        minVh: STEP_SHEET_MIN_VH,
        maxVh: STEP_SHEET_MAX_VH,
        dismissVh: STEP_SHEET_DISMISS_VH,
        onDismiss: closeStepSheet
      });
    }

    function setStepSheetHeight(vh, floorVh) {
      var floor = floorVh === undefined ? STEP_SHEET_MIN_VH : floorVh;
      var clamped = Math.max(floor, Math.min(STEP_SHEET_MAX_VH, vh));
      stepSheet.style.height = clamped + SHEET_UNIT;
      return clamped;
    }



    var stepSheetHide = null;

    function closeStepSheet() {
      if (!stepSheet || stepSheet.hidden) return;
      /* Slid out rather than switched off. `hidden` is `display: none` and a
       * display change cancels a transition outright, so the attribute waits
       * for the animation -- the same deferral, and the same cancel-on-reopen
       * guard, as the chat dock's. */
      stepSheet.classList.add("step-sheet--entering");
      stepSheetBackdrop.classList.add("step-backdrop--entering");
      if (stepSheetHide) clearTimeout(stepSheetHide);
      function hideStepSheet() {
        stepSheetHide = null;
        // Guarded: he may have reopened it while this was waiting, and
        // `openStepSheet` takes the class off again.
        if (stepSheet.classList.contains("step-sheet--entering")) {
          stepSheet.hidden = true;
          stepSheetBackdrop.hidden = true;
        }
      }
      var stepWait = transitionMs(stepSheet);
      if (stepWait) stepSheetHide = setTimeout(hideStepSheet, stepWait + 20);
      else hideStepSheet();
      /* Deliberately not clearing `stepSheetOn` here. `refreshStepSheet` reads
       * `stepSheet.hidden` first and `openStepSheet` overwrites the whole
       * record, so a second guard would be one nothing can fail on -- I wrote
       * it, mutated it away, and all six tests stayed green. That is the same
       * pair of guards `pollConv` above has already been cut down to once. */
      document.removeEventListener("keydown", onStepSheetKey, true);
    }

    function onStepSheetKey(e) {
      if (e.key !== "Escape") return;
      // Escape from the detail view goes back to the list rather than closing
      // the sheet, which is what the back arrow beside it does -- one gesture,
      // two ways to make it.
      if (!stepSheetBack.hidden) stepSheetBack.click();
      else closeStepSheet();
    }

    /* One row in the drawer.
     *
     * A thought is text and is not clickable: it is the whole of what there is
     * to say, and a tap target that opens nothing is worse than none. A tool is
     * a button, because there is an input and an output behind it. */
    /* A step's clock time, or "" when the server could not date it.
     *
     * Seconds are in it deliberately: two calls in the same minute is the
     * ordinary case in a turn, and a list where six rows all read "14:22"
     * answers nothing about their order or their spacing. */
    function stepTime(at) {
      if (!at) return "";
      var ms = Date.parse(at);
      if (isNaN(ms)) return "";
      return new Date(ms).toLocaleTimeString(undefined, {
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    }

    function stepRow(step, onOpen) {
      if (step.kind !== "tool") {
        var passage = el("div", "step-thought");
        appendRichText(passage, null, step.text || "");
        return passage;
      }
      var row = el("button", "step-tool");
      row.type = "button";
      row.appendChild(el("span", "step-tool-name", step.capability || "tool"));
      if (step.input) row.appendChild(el("span", "step-tool-arg", step.input));
      if (step.status && step.status !== "done") {
        row.appendChild(el("span", "step-tool-state step-tool-" + step.status,
          step.status === "failed" ? "failed" : "running"));
      }
      /* When it ran -- his ask, 2026-09-07. Clock time only: every call in
       * one drawer happened within a turn, so the date would be the same on
       * every row and is what `fmtStamp` would spend half the width saying.
       * A step the server could not date draws nothing rather than a
       * plausible wrong time. */
      var at = stepTime(step.at);
      if (at) row.appendChild(el("span", "step-tool-at", at));
      row.addEventListener("click", function () { onOpen(step); });
      return row;
    }

    /* The detail view: his WebFetch screenshot.
     *
     * The output is fetched here rather than carried in the thread, so this is
     * the one place in the drawer that can fail. It says which of the two
     * things went wrong -- a call the thread no longer holds (404) against a
     * fetch that did not land -- because "no output" and "I could not ask" mean
     * different things and only one of them is worth retrying. */
    /* Which detail fetch is the current one.
     *
     * One tap used to mean one fetch, and `stepSheetBack.hidden` was enough to
     * catch the only race there was -- he goes back while it is in flight. The
     * refresh above issues a second one for the same step, so now two can be
     * out at once: the `running` answer from his tap and the `done` answer from
     * the poll that saw it finish. If they land in that order the drawer reverts
     * to "Still running" and stays there, because the signature has already
     * moved and nothing will refresh it again. */
    var stepDetailToken = 0;

    function showStepDetail(conversationId, step, limit) {
      if (stepSheetOn) stepSheetOn.detailId = step.id || "";
      stepDetailToken += 1;
      var token = stepDetailToken;
      stepSheetBack.hidden = false;
      stepSheetTitle.textContent = step.capability || "tool";
      stepSheetSub.hidden = false;
      stepSheetSub.textContent = step.status === "failed" ? "Failed"
        : step.status === "running" ? "Running" : "Completed";
      stepSheetBody.textContent = "";
      var pending = el("p", "step-loading", "Loading…");
      stepSheetBody.appendChild(pending);

      function section(label, text) {
        stepSheetBody.appendChild(el("div", "step-section-label", label));
        stepSheetBody.appendChild(el("pre", "step-pre", text));
      }

      if (!step.id) {
        // A call Agora never gave a `toolUseId`. There is nothing to ask for,
        // and the arguments are already here.
        stepSheetBody.textContent = "";
        section("Inputs", step.input || "");
        stepSheetBody.appendChild(
          el("p", "step-note", "This call has no id, so its output cannot be fetched."));
        return;
      }
      /* `fetch` rather than `fetchPage`, and the status is why. A call Agora
       * no longer holds is a 404 and a site that did not answer is anything
       * else; `fetchPage` turns both into one rejected promise carrying a
       * message, and telling them apart by that string would be this file
       * agreeing with `nova_site.py` about a sentence. The status is the
       * contract. */
      function failed(note) {
        if (stepSheetBack.hidden || token !== stepDetailToken) return;
        stepSheetBody.textContent = "";
        section("Inputs", step.input || "");
        stepSheetBody.appendChild(el("p", "step-note", note));
      }
      /* `limit` is the window the thread rows came from, echoed back by the
       * server. He can page back through a long thread, so a step on screen
       * may be older than the default window -- asking without it reports a
       * call he is looking at as gone. */
      fetch("/api/conversations/step?id=" + encodeURIComponent(conversationId)
        + "&tool=" + encodeURIComponent(step.id)
        + (limit ? "&limit=" + encodeURIComponent(limit) : "")).then(function (r) {
        if (r.status === 404) {
          failed("This call is no longer in the thread, so its output is gone.");
          return null;
        }
        if (!r.ok) {
          failed("Could not reach the server for the output.");
          return null;
        }
        return r.json();
      }).then(function (found) {
        if (!found) return;
        if (stepSheetBack.hidden || token !== stepDetailToken) return;
        stepSheetBody.textContent = "";
        section("Inputs", found.input || step.input || "");
        if (found.status === "running") {
          /* An empty Output block over a call still running reads as "this
           * printed nothing", which is a different and wrong fact.
           *
           * It used to say "close and open this again for the output", which
           * was true while the sheet drew only what it was opened with. It
           * follows the poll now, so that instruction would be busywork -- but
           * the replacement deliberately promises nothing either: the repaint
           * is what fills this in, and a thread the server has stopped calling
           * `waiting` is no longer being polled. So it states the fact and
           * stops there. */
          stepSheetBody.appendChild(el("p", "step-note", "Still running — no output yet."));
          return;
        }
        section("Output", found.output || "");
      }, function () {
        failed("Could not reach the server for the output.");
      });
    }

    function showStepList(conversationId, steps, label, limit) {
      stepSheetBack.hidden = true;
      stepSheetSub.hidden = true;
      stepSheetTitle.textContent = label;
      stepSheetBody.textContent = "";
      steps.forEach(function (step) {
        stepSheetBody.appendChild(stepRow(step, function (chosen) {
          showStepDetail(conversationId, chosen, limit);
        }));
      });
    }

    /* Draw the list and point the back arrow at *these* steps.
     *
     * The two go together: the arrow's handler closes over the array it was
     * given, so a refresh that repainted the list without rebinding it would
     * leave "back" returning to the version he opened with. */
    function paintStepList(conversationId, steps, limit) {
      var label = stepsLabel(steps);
      stepSheetBack.onclick = function () {
        showStepList(conversationId, steps, label, limit);
      };
      showStepList(conversationId, steps, label, limit);
    }

    /* Which steps the open sheet should now be showing, given a fresh payload.
     *
     * The one case worth spelling out is the handover. While a turn runs, its
     * work is a row of its own with no id; the moment the answer arrives the
     * server attaches those same steps to the answer message instead, and the
     * id-less row is gone. Matching on the key alone would go stale at exactly
     * the interesting moment, so a sheet opened on that row follows the steps
     * to the message that swallowed them -- which is the last message carrying
     * any, because `flush` hangs them on the message that comes after them.
     * Then it re-keys, so every later poll is an exact match again. */
    function stepsForOpenSheet(messages) {
      var i;
      for (i = 0; i < messages.length; i += 1) {
        if (stepMessageKey(messages[i]) !== stepSheetOn.key) continue;
        /* An exact key match is only trustworthy when the key identifies one
         * block, and the pending key does not: `stepMessageKey` hands the same
         * sentinel to EVERY steps-only row, because the server invents those
         * rows and they have no id to be told apart by.
         *
         * His report, 2026-09-08, with three screenshots: *"When i open the
         * tools drawer, it displayed an older tools run from earlier ... It
         * says that it has ran 21 tools, i open the drawer and still it says
         * 21 tools. Then suddenly after 4 seconds or so it switches to say 12
         * tools."* Two runs were narrating into one thread that morning, and
         * the thread is served from the worker's cache first (#898) so the
         * paint he opened on and the paint a round trip later were different
         * blocks. Both answered to the sentinel, so this loop handed the sheet
         * whichever came first and swapped the drawer under him.
         *
         * So the sentinel has to earn its match on content as well: the block
         * must still lead with the steps he opened. If it does not, fall
         * through to the search below, which looks for that block wherever it
         * ended up -- and if it is nowhere in this payload, `refreshStepSheet`
         * leaves the sheet exactly as it is rather than showing him somebody
         * else's work. */
        if (stepSheetOn.key === STEP_SHEET_PENDING_KEY
            && !openedStepsLeadWith(messages[i].steps)) break;
        return messages[i].steps || [];
      }
      if (stepSheetOn.key !== STEP_SHEET_PENDING_KEY) return null;
      /* The steps he is reading are the ones that moved, so the message that
       * swallowed them opens with them -- `flush` copies the whole pending list
       * on before anything else is appended. Matching that prefix rather than
       * taking "the last message with any steps" is what keeps this right when
       * two rounds land inside one four-second gap: he asks a follow-up the
       * moment the first answer appears, both settle before the next tick, and
       * the newest message is then a different turn's work entirely. */
      for (i = messages.length - 1; i >= 0; i -= 1) {
        if (openedStepsLeadWith(messages[i].steps)) {
          stepSheetOn.key = stepMessageKey(messages[i]);
          return messages[i].steps;
        }
      }
      return null;
    }

    function openedStepsLeadWith(steps) {
      var mine = stepSheetOn.opened;
      if (!steps || steps.length < mine.length) return false;
      for (var i = 0; i < mine.length; i += 1) {
        if (stepIdentity(steps[i]) !== mine[i]) return false;
      }
      return true;
    }

    /* Carry the open sheet forward onto a repaint. Called once per thread
     * paint, on both surfaces that draw one. */
    function refreshStepSheet(payload) {
      if (!stepSheet || stepSheet.hidden || !stepSheetOn) return;
      /* One sheet, three surfaces. The floating dock sits outside the routed
       * feed, so it can be open over a conversation page on a different thread,
       * and switching the dock's own thread repaints without closing the sheet.
       * Both land here. The pending row's key carries no conversation of its
       * own, so two threads each with a turn in flight collide on it every
       * time -- the thread is checked rather than inferred from the key. */
      if ((payload && payload.conversationId) !== stepSheetOn.conversationId) return;
      var steps = stepsForOpenSheet((payload && payload.messages) || []);
      // The block he is reading is not in this payload at all -- he has paged
      // back past it, or the window rolled. Leave the sheet exactly as it is:
      // wiping it would take the thing he opened off the screen.
      if (!steps) return;
      var signature = stepSignature(steps);
      if (signature === stepSheetOn.signature) return;
      stepSheetOn.signature = signature;
      var conversationId = stepSheetOn.conversationId;
      var limit = stepSheetOn.limit;
      if (!stepSheetBack.hidden && stepSheetOn.detailId) {
        for (var i = 0; i < steps.length; i += 1) {
          if (steps[i].id === stepSheetOn.detailId) {
            // Rebind first: `showStepDetail` leaves the arrow pointing at
            // whatever the last paint gave it.
            stepSheetBack.onclick = (function (fresh) {
              return function () { showStepList(conversationId, fresh, stepsLabel(fresh), limit); };
            }(steps));
            showStepDetail(conversationId, steps[i], limit);
            return;
          }
        }
        return;  // that call is gone from the block; keep showing what he opened
      }
      if (!stepSheetBack.hidden) return;  // a detail view with no id to re-find
      /* His place in the list survives the redraw. A tool row leaving the
       * viewport because two more arrived above it is the auto-scroll
       * complaint of issue #140 in a smaller box. */
      var at = stepSheetBody.scrollTop;
      paintStepList(conversationId, steps, limit);
      stepSheetBody.scrollTop = at;
    }

    function openStepSheet(conversationId, steps, limit, key) {
      buildStepSheet();
      stepSheetOn = { key: key, conversationId: conversationId, limit: limit,
                      detailId: "", signature: stepSignature(steps),
                      opened: steps.map(stepIdentity) };
      paintStepList(conversationId, steps, limit);
      setStepSheetHeight(STEP_SHEET_OPEN_VH);
      /* Unhidden already off-screen, then let back up on the next frame, so
       * the browser has a start value to animate *from*. Setting `hidden`
       * and the final position in the same frame is the shape that renders
       * as an instant appearance with the transition silently skipped. */
      if (stepSheetHide) { clearTimeout(stepSheetHide); stepSheetHide = null; }
      stepSheet.classList.add("step-sheet--entering");
      stepSheetBackdrop.classList.add("step-backdrop--entering");
      stepSheetBackdrop.hidden = false;
      stepSheet.hidden = false;
      // Reading a layout property is what forces the reflow; the value is
      // deliberately unused. Without it the two class changes coalesce into
      // one style recalculation and nothing moves.
      void stepSheet.offsetHeight;
      stepSheet.classList.remove("step-sheet--entering");
      stepSheetBackdrop.classList.remove("step-backdrop--entering");
      document.addEventListener("keydown", onStepSheetKey, true);
      stepSheetClose.focus();
    }

    /* The collapsed line itself. Returns null when there is nothing behind it,
     * so a message with no steps is unchanged. */
    function stepsLine(conversationId, steps, limit, key) {
      if (!steps || !steps.length) return null;
      var label = stepsLabel(steps);
      var line = el("button", "ask-steps");
      line.type = "button";
      line.appendChild(el("span", "ask-steps-label", label));
      line.appendChild(el("span", "ask-steps-chev", "›"));
      line.setAttribute("aria-label", label + " — open the details");
      line.addEventListener("click", function () {
        openStepSheet(conversationId, steps, limit, key);
      });
      return line;
    }

    return {
      CAPTURE_SHEET_OPEN_VH: CAPTURE_SHEET_OPEN_VH,
      STEP_SHEET_DISMISS_VH: STEP_SHEET_DISMISS_VH,
      STEP_SHEET_MAX_VH: STEP_SHEET_MAX_VH,
      STEP_SHEET_MIN_VH: STEP_SHEET_MIN_VH,
      STEP_SHEET_OPEN_VH: STEP_SHEET_OPEN_VH,
      dragSheet: dragSheet,
      openStepSheet: openStepSheet,
      refreshStepSheet: refreshStepSheet,
      stepMessageKey: stepMessageKey,
      stepsLabel: stepsLabel,
      stepsLine: stepsLine,
    };
  };
})();
