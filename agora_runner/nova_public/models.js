/* The composer's model picker (issue #233).
 *
 * The thirteenth piece of `app.js` moved out whole, after `mermaid.js`,
 * `attach.js`, `chat-dock.js`, `charts.js`, `diag.js`, `project.js`,
 * `beats.js`, `notes.js`, `home.js`, `plan.js`, `steps.js` and `ask.js`:
 * the model catalog kept between opens, the per-thread choice, the pill
 * that sizes itself to its label, and the change handler that posts it.
 *
 * It borrows five function declarations and hands one back.
 * `paintModelPicker` is only called after `app.js` has finished loading,
 * so it is bound where the block used to sit.
 */
(function () {
  "use strict";

  window.novaModels = function (shared) {
    var el = shared.el;
    var fetchPage = shared.fetchPage;
    var localStore = shared.localStore;
    var modelOption = shared.modelOption;
    var toast = shared.toast;

    /* The catalog, kept between opens.
     *
     * His report, 2026-09-07: *"it takes some time for it to appear almost as
     * it must load every time... The button should be displayed when i open
     * the model like the rest of the other buttons."* The picker was hidden
     * until `/api/conversations/model` answered, so on every open the composer
     * drew `+` and Send immediately and the pill arrived a beat later -- one
     * row assembling itself in two steps.
     *
     * The catalog is the same list for every thread and changes about never,
     * so it survives a reload in `localStorage`; which model a given thread is
     * on is per-thread and is remembered for the session only. Both are drawn
     * straight away and then corrected by the fetch that was happening
     * anyway -- so a stale cache costs a redraw, never a wrong write: the
     * change handler still posts to the server and still puts the control back
     * if the server refuses.
     */
    var MODEL_CATALOG_KEY = "nova.modelCatalog.v1";
    var modelCatalog = null;
    var modelChoices = {};

    function loadModelCatalog() {
      if (modelCatalog) return modelCatalog;
      var store = localStore();
      if (!store) return null;
      try {
        var raw = store.getItem(MODEL_CATALOG_KEY);
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        if (parsed && parsed.length) modelCatalog = parsed;
      } catch (err) { /* unreadable cache is no cache */ }
      return modelCatalog;
    }

    function saveModelCatalog(models) {
      if (!models || !models.length) return;
      modelCatalog = models;
      var store = localStore();
      if (!store) return;
      try {
        store.setItem(MODEL_CATALOG_KEY, JSON.stringify(models));
      } catch (err) { /* full or disabled: the picker still works */ }
    }

    /* Options for one catalog, selected on `current`. Returns whether the
     * model the thread is on was in the list -- the caller adds it if not, so
     * the picker can never silently repoint a thread at something else. */
    function fillModelOptions(pick, models, current) {
      pick.textContent = "";
      var listed = false;
      (models || []).forEach(function (m) {
        if (m.id === current) listed = true;
        pick.appendChild(modelOption(
          m.id, modelLabel(m.label) + (m.metered ? " (metered)" : "")));
      });
      if (current && !listed) {
        pick.insertBefore(modelOption(current, modelLabel(current)), pick.firstChild);
      }
      if (!current) {
        pick.insertBefore(modelOption("", "Model"), pick.firstChild);
      }
      pick.value = current || "";
      fitModelPick(pick);
      return listed;
    }

    /* "(CLI)" says which lane the model runs on, which is a thing about this
     * loop's plumbing and not a thing about the model. It cost about a fifth
     * of the pill's width to say it. */
    function modelLabel(label) {
      return String(label || "").replace(/\s*\(CLI\)\s*/i, " ").trim();
    }

    /* The pill is as wide as the name it is showing, not as wide as the
     * longest name in the list -- which is what a <select> does by default,
     * and what he reported as "still very wide". Measured in a canvas rather
     * than with a hidden node so nothing is added to the layout to size the
     * thing that is in the layout. */
    var modelFitCanvas = null;
    function fitModelPick(pick) {
      if (!pick || !pick.options || !pick.options.length) return;
      var chosen = pick.options[pick.selectedIndex];
      if (!chosen) return;
      try {
        if (!modelFitCanvas) modelFitCanvas = document.createElement("canvas");
        var ctx = modelFitCanvas.getContext("2d");
        if (!ctx) return;
        var style = window.getComputedStyle(pick);
        ctx.font = style.fontWeight + " " + style.fontSize + " " + style.fontFamily;
        var text = ctx.measureText(chosen.text || "").width;
        // The measured text, plus the padding the rule gives it. No arrow to
        // leave room for -- that came off in the same pass.
        var pad = parseFloat(style.paddingLeft || 0) + parseFloat(style.paddingRight || 0);
        pick.style.width = Math.ceil(text + pad + 2) + "px";
      } catch (err) { /* no canvas: the select keeps its default width */ }
    }

    function modelPicker(conversationId) {
      var wrap = el("div", "model-pick-bar");
      /* Hidden until the answer lands, and it stays hidden on every path that
       * does not produce one. A picker drawn empty and filled in a second
       * later is a control that reads as "no model" for that second, on the
       * one question this is meant to answer. */
      wrap.hidden = true;
      var pick = document.createElement("select");
      pick.className = "model-pick";
      pick.setAttribute("aria-label", "Model");
      var note = el("span", "model-pick-note");
      wrap.appendChild(pick);
      wrap.appendChild(note);
      if (!conversationId) return wrap;
      var current = "";
      /* On screen now if anything is known, rather than after a round trip.
       * `modelChoices` is what this thread was last seen on; the catalog is
       * shared. Both are replaced by the answer below when it lands. */
      var cachedModels = loadModelCatalog();
      if (cachedModels && cachedModels.length) {
        current = modelChoices[conversationId] || "";
        fillModelOptions(pick, cachedModels, current);
        wrap.hidden = false;
      }
      fetchPage("/api/conversations/model?id=" + encodeURIComponent(conversationId))
        .then(function (payload) {
          var models = payload.models || [];
          /* `found` is the server's answer to "does Agora still hold this
           * thread", and it is not the same question as "has it a model".
           * Both leave `model` empty and they mean opposite things -- see
           * `nova_conversations.model_choice`. No catalog, no picker either:
           * a select holding only the model the thread already has cannot
           * change anything. */
          // A thread Agora no longer holds hides the picker again rather
          // than leaving the cached guess on screen: `found` false and "no
          // model" are different answers, and only one of them is a picker.
          if (!payload.found || !models.length) {
            wrap.hidden = true;
            return;
          }
          current = payload.model || "";
          saveModelCatalog(models);
          modelChoices[conversationId] = current;
          // Options rebuilt from the server's list, and the value set after
          // they exist rather than with `selected` on each one -- `rowEditor`
          // carries the measurement, and the failure is a picker that opens
          // on a model the thread is not on.
          fillModelOptions(pick, models, current);
          wrap.hidden = false;
        })
        .catch(function () { /* no picker; the messages are what he came for */ });

      pick.addEventListener("change", function () {
        var wanted = pick.value;
        if (!wanted || wanted === current) return;
        pick.disabled = true;
        toast("switching model…");
        fetch("/api/conversations/model", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: conversationId, model: wanted }),
        })
          .then(function (r) { return r.json().catch(function () { return {}; }); })
          .then(function (result) {
            if (!result || !result.ok) {
              throw new Error((result && (result.message || result.error)) || "failed");
            }
            current = wanted;
            modelChoices[conversationId] = wanted;
            fitModelPick(pick);
            toast("switched to " + (pick.options[pick.selectedIndex] || {}).text);
          })
          .catch(function (err) {
            /* Put the control back on the model the thread is actually on.
             * Leaving it showing the one that did not take is the failure this
             * app keeps filing against itself -- a page reporting a write that
             * never happened. */
            pick.value = current;
            fitModelPick(pick);
            toast("could not switch: " + err.message, true);
          })
          .then(function () { pick.disabled = false; });
      });
      return wrap;
    }

    /* Draw a fresh picker into a host node, replacing whatever was there.
     *
     * Both surfaces reuse one host across threads -- the dock switches
     * conversations inside the same panel, and the page is rebuilt per open --
     * so the old thread's picker has to go before the new one's fetch lands,
     * or a slow answer for the thread he left paints over the one he is on.
     */
    function paintModelPicker(host, conversationId) {
      if (!host) return;
      host.textContent = "";
      host.appendChild(modelPicker(conversationId));
    }

    return {
      paintModelPicker: paintModelPicker,
    };
  };
})();
