/* The attach button for every composer on this site (issue #233, step 11).
 *
 * The second piece of `app.js` moved out whole, after `mermaid.js`. It is
 * the same test the diagrams passed: nothing in here reads any of
 * `app.js`'s state -- it talks to `POST /api/upload` and hands back an
 * object -- and the four composers that mount it (the chat, the comment
 * drawer, the board composer and the capture box) each reach it at exactly
 * one call. So the whole concern travels together and nothing at either end
 * has to know the other's internals.
 *
 * `el` comes off `window.novaChat` at call time, the way `mermaid.js` and
 * `message.js` already take their helpers -- one definition of it, in
 * `app.js`, rather than a second copy here.
 */
(function () {
  "use strict";

  /* An attach button for any composer on this site.
   *
   * the owner, comments board 2026-08-21: *"How do i send a screenshot?"* He
   * could see a layout bug on his Galaxy S25 that no renderer in this loop
   * can reproduce, and the only channel between us was text. Cycle 299 had
   * to answer "you can't" and ask him to describe the pixels instead.
   *
   * The upload happens on *pick*, not on send. Two reasons, and the second
   * is the one that decided it: a 3MB POST from a phone takes long enough
   * that doing it inside send() would make the send button look hung, and
   * an attachment that is already stored can be shown back to him before he
   * commits to anything.
   *
   * **Where it is shown back changed in Cycle 377, and that is this ask.**
   * The owner, ideas board 2026-08-24: *"Lets me preview a miniatyr version
   * of the uploaded images in the Nova app instead of the text that shows up
   * in the input box. Also let me upload multiple (at once) and cross them
   * out if i want to not send them after upload."* Until now the markdown
   * line was appended into the textarea, which made three things awkward at
   * once: he could not see what he had picked, a second pick pushed his own
   * sentence further up a box he is reading on a 360px phone, and "delete
   * it" meant selecting a 45-character URL by hand.
   *
   * So the attachments live in a tray beside the box instead — one chip per
   * file, a thumbnail for a picture and its name for anything else, each
   * with an ✕. The markdown is composed at send time by `markdown()` rather
   * than typed into the box. That is a real trade and it is worth naming:
   * he loses the ability to edit the alt text or move the line around inside
   * his sentence, and he gains seeing it and being able to drop it. He asked
   * for the second one.
   *
   * `FileReader.readAsDataURL` rather than an ArrayBuffer walk: the server
   * accepts a `data:` URL as-is (`store_upload` splits on the comma), so
   * this is one call with no manual base64 in JavaScript. */
  function buildAttach(opts) {
    var el = window.novaChat.el;
    opts = opts || {};
    var input = el("input", "attach-input");
    input.type = "file";
    // The second half of his ask. One `change` now carries a list, and the
    // uploads run one after another rather than all at once: a phone
    // picking four screenshots would otherwise open four simultaneous
    // multi-megabyte POSTs, and the status line could only honestly
    // describe one of them at a time anyway.
    input.multiple = true;
    // No `accept` at all. It was `image/*`, and on Android that is not a
    // filter over a file browser -- it is what makes the picker open
    // Google Photos with no way out. The owner, 2026-08-21: "It seems i only
    // can upload images. Or atleas the ui forces only my Google photos to
    // open and i have no option to upload files." The server resolves and
    // bounds the type, and answers with a sentence he can read, so an
    // allowlist here only ever hid his own files from him.
    input.hidden = true;

    // `+` rather than a paperclip: the glyph Claude's composer uses for the
    // same control, and the one thing in this row that is not an emoji
    // rendering at a different weight to its neighbours.
    var button = el("button", "attach-btn " + (opts.buttonClass || ""), "+");
    button.type = "button";
    button.title = "Attach a file";
    button.setAttribute("aria-label", "Attach a file");

    function status(text, isError) {
      if (opts.onStatus) opts.onStatus(text, isError);
    }

    /* Whether an upload is in flight, and the composer's own send controls
     * follow it.
     *
     * Without this the two controls race, and the race loses the picture
     * silently: `submit()` reads the tray synchronously, so tapping Comment
     * while the POST is still going sends the text *without* the
     * attachment -- and then the upload resolves and pushes a chip into a
     * tray `submit()` has already cleared, so the image reappears as an
     * orphaned draft attached to nothing. He gets a comment with no
     * screenshot in it and no sign that anything went wrong.
     *
     * Disabling send is the fix rather than queueing the upload, because
     * the upload is the slow part and "wait for it" is the honest thing to
     * show. `status` already says "uploading …" while it runs. */
    function busy(isBusy) {
      button.disabled = isBusy;
      if (opts.onBusy) opts.onBusy(isBusy);
    }

    button.addEventListener("click", function () { input.click(); });

    /* What has been uploaded and not yet sent: `{name, url, isImage}` each.
     *
     * This is the composer's state now, not the textarea's, which is the
     * whole shape of the change. `onChange` is how a composer that has a
     * draft store keeps it across a re-render — the journal drawer is
     * rebuilt on every poll, and an attachment that survived only in this
     * closure would disappear from under him while he was still typing. */
    var pending = [];
    var tray = el("div", "attach-tray");

    function markdownFor(item) {
      // `![…]` only for something that renders as a picture. The server
      // decides that, not `file.type` -- Android reports `""` for plenty
      // of files and the extension lookup happens server side. A `![pdf]`
      // here would paint a broken image icon.
      return (item.isImage ? "!" : "") + "[" + item.name + "](" + item.url + ")";
    }

    function render() {
      tray.textContent = "";
      tray.hidden = pending.length === 0;
      pending.forEach(function (item, index) {
        var chip = el("div", "attach-chip");
        if (item.isImage) {
          var thumb = el("img", "attach-thumb");
          thumb.src = item.url;
          // The filename, not "image": with four screenshots in the tray
          // the alt text is the only thing that tells them apart to a
          // screen reader, and it is what he named them.
          thumb.alt = item.name;
          chip.appendChild(thumb);
        } else {
          chip.appendChild(el("span", "attach-chip-name", "📎 " + item.name));
        }
        /* "cross them out if i want to not send them after upload."
         *
         * It drops the attachment from this send only. The bytes stay on
         * the server, because `store_upload` already wrote them and there
         * is no delete endpoint -- and inventing one to make an ✕ feel
         * complete would be a second, destructive feature he did not ask
         * for. An orphaned upload costs disk and nothing else. */
        var remove = el("button", "attach-chip-remove", "✕");
        remove.type = "button";
        remove.title = "Remove " + item.name;
        remove.setAttribute("aria-label", "Remove " + item.name);
        remove.addEventListener("click", function () {
          pending.splice(index, 1);
          render();
          changed();
        });
        chip.appendChild(remove);
        tray.appendChild(chip);
      });
    }

    function changed() {
      if (opts.onChange) opts.onChange(pending.slice());
    }

    /* Whether this composer is still the one on screen.
     *
     * The journal drawer is thrown away and rebuilt whole on a render, and
     * the upload chain below is not cancelled when that happens -- it keeps
     * running inside the dead closure, with its own `pending` array, its own
     * detached tray and a `status` element nobody can see. Reviewer finding,
     * Cycle 377, and the worst of the three consequences is the one I would
     * not have predicted: `clear()` on a successful send deletes the draft,
     * and then the dead chain's next completed upload calls `changed()` and
     * *resurrects* it, so the next time he opens that drawer a picture he
     * already sent is sitting there looking unsent.
     *
     * So a completed upload that has nowhere visible to go is treated as a
     * failed one. It is reported, not swallowed -- the status line it writes
     * to is detached, which is exactly why the count in the batch summary
     * matters -- and the live drawer's tray keeps showing only what it will
     * actually send. That is the property worth protecting: an under-count
     * he can see beats a silent over-count he cannot.
     *
     * `isConnected` and not a generation counter, because the question this
     * has to answer is literally "is my tray on the page", and a counter
     * would be a second thing that has to be kept in step with the DOM. The
     * tray is in the document from the moment the composer is built, so this
     * is false only after a rebuild has orphaned it. */
    function live() {
      return tray.isConnected !== false;
    }

    render();

    /* One file, from picked to sitting in the tray. Rejects rather than
     * reporting, so the loop below can count how many of a batch failed
     * and say so once instead of overwriting the status line per file. */
    function upload(file) {
      return new Promise(function (resolve, reject) {
        var reader = new FileReader();
        reader.onerror = function () { reject(new Error("could not read " + file.name)); };
        reader.onload = function () {
          fetch("/api/upload", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              filename: file.name,
              contentType: file.type,
              data: String(reader.result || ""),
            }),
          })
            .then(function (r) { return r.json().catch(function () { return {}; }); })
            .then(function (result) {
              if (!result || !result.ok) {
                throw new Error((result && (result.message || result.error)) || "upload failed");
              }
              if (!live()) {
                throw new Error(file.name + " finished after the page moved on");
              }
              pending.push({
                name: file.name || "file",
                url: result.url,
                isImage: result.isImage !== false,
              });
              render();
              changed();
              resolve();
            })
            .catch(reject);
        };
        reader.readAsDataURL(file);
      });
    }

    input.addEventListener("change", function () {
      var files = Array.prototype.slice.call(input.files || []);
      if (!files.length) return;
      busy(true);
      var attached = 0;
      var lastError = "";
      files
        .reduce(function (chain, file, index) {
          return chain.then(function () {
            status(
              files.length > 1
                ? "uploading " + (index + 1) + " of " + files.length + " — " + file.name + "…"
                : "uploading " + file.name + "…",
              false,
            );
            return upload(file).then(
              function () { attached += 1; },
              // One bad file in a batch of four must not throw away the
              // three good ones, so a rejection is recorded and the chain
              // continues. The last failure is the one reported: a status
              // line is one sentence and the most recent is the one he
              // can still act on by picking that file again.
              function (err) { lastError = String((err && (err.message || err)) || "upload failed"); },
            );
          });
        }, Promise.resolve())
        .then(function () {
          busy(false);
          // Cleared so picking the *same* file twice still fires
          // `change` -- otherwise a failed upload cannot be retried
          // without choosing a different image first.
          input.value = "";
          if (lastError) {
            status(attached ? lastError + " (" + attached + " attached)" : lastError, true);
          } else {
            /* Nothing on success. His ask, 2026-09-08: *"when i attach an
             * image there is a text 'attached'. This is unnecessary as i
             * can see that the image is uploaded."* The tray below the box
             * already shows the thumbnail -- the word was a second, worse
             * copy of what the picture says. A FAILURE still speaks, above:
             * that is the case with nothing on screen to see. */
            status("", false);
          }
        });
    });

    return {
      button: button,
      input: input,
      tray: tray,
      /** How many attachments are waiting. A composer with no typed text
       *  but a full tray still has something to send. */
      count: function () { return pending.length; },
      /** The markdown for everything in the tray, joined by `separator`.
       *  Board comments may not contain a line break, so that caller
       *  passes a space; the others take the default blank line. */
      markdown: function (separator) {
        return pending
          .map(markdownFor)
          .join(separator === undefined ? "\n\n" : separator);
      },
      /** Emptied only once the server has confirmed the send, the same
       *  rule the text boxes already follow. */
      clear: function () { pending = []; render(); changed(); },
      /** Repopulate from a draft store after a re-render. Deliberately
       *  silent -- `onChange` reports what the reader did, and replaying
       *  it here would write the draft back over itself. */
      restore: function (list) { pending = (list || []).slice(); render(); },
    };
  }

  window.novaAttach = { build: buildAttach };
})();
