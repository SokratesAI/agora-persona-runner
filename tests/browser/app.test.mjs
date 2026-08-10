/* Behavioural tests for the real agora_runner/nova_public/app.js.
 *
 * Why these exist. Every previous change to this file was verified by
 * reading it, or by asserting that a substring appeared in its source --
 * which cannot tell the difference between code that is present and code
 * that works. Cycle 56 did run jsdom checks against it and reported
 * nineteen of them in the journal, but ran them out of a scratch directory
 * and committed none, so the verification died with the session and the
 * next cycle to touch the file (this one) had nothing. That is the same
 * shape as a PR body citing evidence that was never written down: the
 * record claimed a check that no longer exists.
 *
 * So they run the actual index.html and the actual app.js in a DOM,
 * against a payload generated from the real server functions
 * (tests/browser/regen.py), and click on things.
 */
import { test, before, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = join(here, "..", "..", "agora_runner", "nova_public");
const payload = JSON.parse(readFileSync(join(here, "fixtures", "payload.json"), "utf8"));

/** Load the site at `path` with fetch stubbed to serve the fixture.
 *
 * `failComments` rejects only `/api/comments`, which is how the "a broken
 * endpoint costs the bubbles, not the feed" test reaches the catch that
 * app.js's Promise.all relies on.
 *
 * `digest` and `comments` override those two responses. The Needs Edvard
 * tests need both: the live fixture's digest asks for nothing (so the
 * section is hidden), and its comments carry no reply to it.
 *
 * `install` runs against the window just before app.js is evaluated, for a
 * test that has to be in place before the page's first render -- the reply
 * poll schedules its first timer there. */
async function loadSite(path = "/", { failComments = false, digest, comments, install } = {}) {
  const html = readFileSync(join(publicDir, "index.html"), "utf8");
  const dom = new JSDOM(html, {
    url: "https://nova.example" + path,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  /* Every POST is recorded and answered, so a test can assert what the
   * page actually sent rather than only what it then displayed -- the
   * difference between checking the wire and checking the DOM. */
  window.posted = [];
  window.postReply = { ok: true, message: "ok" };
  window.fetch = (url, init) => {
    if (init && init.method === "POST") {
      window.posted.push({ url, headers: init.headers, body: JSON.parse(init.body) });
      return Promise.resolve({ json: () => Promise.resolve(window.postReply) });
    }
    if (url.includes("/api/comments")) {
      return failComments
        ? Promise.reject(new Error("comments are down"))
        : Promise.resolve({ json: () => Promise.resolve(comments || payload.comments) });
    }
    const body = url.includes("/api/digest") ? (digest || payload.digest) : payload.journal;
    return Promise.resolve({ json: () => Promise.resolve(body) });
  };
  window.scrollTo = () => {}; // jsdom has none, and the link handler calls it
  /* Kept before `install` can replace it: a test that takes setTimeout over
   * to step a poll would otherwise also take over the drain below, and this
   * function would never return. */
  const realTimeout = window.setTimeout.bind(window);
  if (install) install(window);
  window.eval(readFileSync(join(publicDir, "app.js"), "utf8"));
  // app.js renders from three resolved promises; let the microtasks drain.
  await new Promise((resolve) => realTimeout(resolve, 0));
  return window;
}

/** A real click, the way a browser dispatches one (bubbling to the card). */
function click(window, node) {
  node.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
}

const cards = (window) => [...window.document.querySelectorAll(".entry")];
/** A digest line as the card renders it -- spans joined, so the `**` the
 *  raw `text` field still carries is not compared against the DOM. */
const lineText = (line) => line.spans.map((s) => s.text).join("");
/** The headline a collapsed card shows for a digest line -- its first drawer.
 *  Distinct from `lineText`: the rest of the line is revealed on open, so
 *  comparing a card against the whole line would have been comparing it
 *  against text the card is not supposed to be showing. */
const lineBrief = (line) => line.briefSpans.map((s) => s.text).join("");
const expanded = (card) => card.classList.contains("is-expanded");

/** The comments payload with one cycle's newest comment awaiting a reply. */
function withPending(cycle) {
  const copy = JSON.parse(JSON.stringify(payload.comments));
  copy.byCycle[String(cycle)][0].replyPending = true;
  return copy;
}

/** Take `window.setTimeout` over so a poll can be stepped rather than waited
 *  out. jsdom runs real timers, and the reply poll is on an 8-second cycle;
 *  a test that slept through two of them would be a 16-second test. The real
 *  timer is kept for draining microtasks. */
function captureTimers(window) {
  const real = window.setTimeout.bind(window);
  const scheduled = new Map();
  let nextId = 1;
  window.setTimeout = (fn) => {
    const id = nextId++;
    scheduled.set(id, fn);
    return id;
  };
  /* Modelled rather than stubbed out: whether a discarded poll is actually
   * cancelled is one of the things under test, and a no-op clearTimeout
   * would make that unobservable. */
  window.clearTimeout = (id) => scheduled.delete(id);
  return {
    get queued() {
      return [...scheduled.values()];
    },
    /** Run everything scheduled so far, then let the fetches settle. */
    async fire() {
      const due = [...scheduled.entries()];
      due.forEach(([id, fn]) => {
        scheduled.delete(id);
        fn();
      });
      for (let i = 0; i < 5; i += 1) {
        await new Promise((resolve) => real(resolve, 0));
      }
      return due.length;
    },
  };
}

describe("cards expand and collapse", () => {
  let window;
  before(async () => {
    window = await loadSite();
  });

  test("every entry in the payload gets a card", () => {
    assert.equal(cards(window).length, payload.journal.entries.length);
  });

  test("cards start collapsed on the feed", () => {
    assert.ok(cards(window).every((card) => !expanded(card)));
  });

  test("a click anywhere on the card expands it, not just the header", () => {
    // `.entry-body` used to be in this list -- it was the furthest thing from
    // the header that is still the card. It is deliberately not any more:
    // Edvard asked for the full journal to close when its own text is
    // clicked, so inside the body a click means "shut this drawer" rather
    // than "collapse the card". That is pinned below instead.
    for (const selector of [".entry-brief", ".entry-meta", ".entry-toggle"]) {
      const card = cards(window)[0];
      const target = card.querySelector(selector);
      assert.ok(target, "expected " + selector + " to exist");
      const before = expanded(card);
      click(window, target);
      assert.equal(expanded(card), !before, "clicking " + selector + " did not toggle");
      click(window, target); // put it back
      assert.equal(expanded(card), before);
    }
  });

  test("clicking the card again collapses it", () => {
    const card = cards(window)[0];
    click(window, card);
    assert.ok(expanded(card));
    click(window, card);
    assert.ok(!expanded(card));
  });

  test("one card's click does not touch its neighbours", () => {
    const [first, second] = cards(window);
    click(window, first);
    assert.ok(expanded(first));
    assert.ok(!expanded(second));
    click(window, first);
  });

  test("the toggle keeps aria-expanded in step with the card", () => {
    const card = cards(window)[0];
    const toggle = card.querySelector(".entry-toggle");
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
    click(window, card);
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    click(window, card);
    assert.equal(toggle.getAttribute("aria-expanded"), "false");
  });

  test("the toggle controls the body it actually owns", () => {
    for (const card of cards(window)) {
      const controlled = card.querySelector(".entry-toggle").getAttribute("aria-controls");
      assert.equal(card.querySelector(".entry-body").id, controlled);
    }
  });

  test("selecting text inside a card does not collapse it", () => {
    const card = cards(window)[0];
    click(window, card);
    assert.ok(expanded(card));
    const paragraph = card.querySelector(".entry-body p");
    const range = window.document.createRange();
    range.selectNodeContents(paragraph);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    click(window, paragraph);
    assert.ok(expanded(card), "the drag-select's click collapsed the card");
    window.getSelection().removeAllRanges();
    click(window, card);
  });
});

describe("two entries for one cycle are two different cards", () => {
  let window;
  before(async () => {
    window = await loadSite();
  });

  test("the fixture really does hold two entries for cycle 57", () => {
    const fifty7 = payload.journal.entries.filter((e) => e.cycle === 57);
    assert.equal(fifty7.length, 2);
  });

  test("their summaries are not identical", () => {
    // Edvard, issues.md 2026-08-09: "Why are the journals for cycles 55-57
    // listed twice in the Nova app?" They were not duplicates; both cards
    // rendered the same digest line as their summary.
    const [first, second] = cards(window);
    const summary = (card) => card.querySelector(".entry-brief").textContent;
    assert.notEqual(summary(first), summary(second));
  });

  test("the digest line goes to the cycle's own run, not to its addendum", () => {
    const line = payload.digest.lines.find((l) => l.cycle === 57);
    const [addendum, run] = cards(window);
    assert.equal(run.querySelector(".entry-brief").textContent, lineBrief(line));
    assert.notEqual(addendum.querySelector(".entry-brief").textContent, lineBrief(line));
    // And the remainder is the drawer inside it, on that card only.
    assert.equal(run.querySelector(".entry-digest").textContent.trim(),
      line.restSpans.map((s) => s.text).join("").trim());
    assert.equal(addendum.querySelector(".entry-digest"), null);
    assert.ok(lineBrief(line).length < lineText(line).length,
      "the fixture must actually split, or this test cannot fail");
  });

  test("the addendum summarises itself from its own first paragraph", () => {
    const addendum = cards(window)[0];
    const brief = payload.journal.entries[0].briefSpans.map((s) => s.text).join("");
    const opening = payload.journal.entries[0].blocks.find((b) => b.type === "p");
    const text = opening.spans.map((s) => s.text).join("");
    assert.equal(addendum.querySelector(".entry-brief").textContent, brief);
    assert.ok(text.startsWith(brief), "the brief is the front of that paragraph");
    assert.ok(brief.length < text.length, "and shorter than it, or nothing is being tested");
  });

  test("the digest summary renders its bold instead of showing asterisks", () => {
    // The digest line was the only text on the page rendering its own
    // markup, and it is the line Edvard called hard to read.
    const run = cards(window)[1];
    const summary = run.querySelector(".entry-brief");
    assert.equal(summary.querySelectorAll("strong").length, 1);
    assert.equal(
      summary.querySelector("strong").textContent,
      "The digest line for cycle 57, which opens with a bolded headline sentence "
        + "the way every live one does."
    );
    assert.ok(!summary.textContent.includes("**"), summary.textContent);
  });

  test("no summary anywhere in the feed leaks a markdown asterisk", () => {
    for (const card of cards(window)) {
      const summary = card.querySelector(".entry-brief");
      if (summary) assert.ok(!summary.textContent.includes("**"), summary.textContent);
    }
  });

  test("the addendum is still labelled as one", () => {
    assert.equal(cards(window)[0].querySelector(".entry-title").textContent, "verification");
  });

  test("no element id is used twice", () => {
    const ids = [...window.document.querySelectorAll("[id]")].map((n) => n.id);
    assert.equal(new Set(ids).size, ids.length, "duplicate id: " + ids.join(", "));
  });

  test("the cycle anchor exists exactly once for a cycle with two entries", () => {
    assert.equal(window.document.querySelectorAll("#cycle-57").length, 1);
  });
});

describe("PR references are links", () => {
  let window;
  before(async () => {
    window = await loadSite();
  });

  test("each reference in a multi-repo footer is its own link", () => {
    const card = cards(window).find((c) => c.textContent.includes("runner#58"));
    const links = [...card.querySelectorAll(".pr .pr-link")];
    assert.deepEqual(
      links.map((a) => a.textContent),
      ["runner#58", "runner-config#6", "platform-config#490"]
    );
    assert.deepEqual(
      links.map((a) => a.getAttribute("href")),
      [
        "https://github.com/SokratesAI/agora-persona-runner/pull/58",
        "https://github.com/SokratesAI/agora-persona-runner-config/pull/6",
        "https://github.com/SokratesAI/platform-config/pull/490",
      ]
    );
  });

  test("the separators between references survive as text", () => {
    const card = cards(window).find((c) => c.textContent.includes("runner#58"));
    assert.equal(
      card.querySelector(".pr").textContent,
      "runner#58, runner-config#6, platform-config#490"
    );
  });

  test("a footer with no reference in it makes no link", () => {
    const card = cards(window).find((c) => c.querySelector(".pr").textContent === "none");
    assert.equal(card.querySelectorAll(".pr a").length, 0);
  });

  test("links open away from the PWA without handing it the opener", () => {
    for (const link of window.document.querySelectorAll(".pr-link")) {
      assert.equal(link.getAttribute("target"), "_blank");
      assert.match(link.getAttribute("rel"), /noopener/);
    }
  });

  test("clicking a PR link does not also toggle the card", () => {
    const card = cards(window).find((c) => c.querySelector(".pr-link"));
    const before = expanded(card);
    click(window, card.querySelector(".pr-link"));
    assert.equal(expanded(card), before);
  });

  test("clicking the permalink does not also toggle the card", () => {
    const card = cards(window).find((c) => c.querySelector(".entry-permalink"));
    const before = expanded(card);
    click(window, card.querySelector(".entry-permalink"));
    assert.equal(expanded(card), before);
  });
});

describe("the Needs Edvard box", () => {
  test("stays hidden when the section says a bolded nothing", async () => {
    // The live digest has said `**Nothing.**` since the box shipped, and the
    // emptiness test compared the section literally, so the box had never
    // once been hidden. Edvard, issues.md 2026-08-09.
    assert.equal(payload.digest.hasNeedsEdvard, false);
    const window = await loadSite();
    assert.ok(window.document.getElementById("needs").hidden);
  });

  test("appears when the section actually asks for something", async () => {
    const html = readFileSync(join(publicDir, "index.html"), "utf8");
    const dom = new JSDOM(html, { url: "https://nova.example/", runScripts: "outside-only" });
    const { window } = dom;
    const asking = {
      ...payload.digest,
      hasNeedsEdvard: true,
      needsEdvardBlocks: [{ type: "p", spans: [{ kind: "text", text: "Decide about the node." }] }],
    };
    window.fetch = (url) =>
      Promise.resolve({
        json: () => Promise.resolve(url.includes("/api/digest") ? asking : payload.journal),
      });
    window.eval(readFileSync(join(publicDir, "app.js"), "utf8"));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    const needs = window.document.getElementById("needs");
    assert.ok(!needs.hidden);
    assert.match(needs.textContent, /Decide about the node/);
  });
});

describe("a deep-linked cycle", () => {
  test("shows both of that cycle's entries, expanded", async () => {
    const window = await loadSite("/cycle/57");
    const shown = cards(window);
    assert.equal(shown.length, 2);
    assert.ok(shown.every(expanded));
  });

  test("still gives the digest line to only one of them", async () => {
    const window = await loadSite("/cycle/57");
    const brief = lineBrief(payload.digest.lines.find((l) => l.cycle === 57));
    const summaries = cards(window).map((c) => c.querySelector(".entry-brief").textContent);
    assert.equal(summaries.filter((s) => s === brief).length, 1);
  });
});

describe("the vault cannot inject markup", () => {
  test("a script tag in an entry stays text and creates no node", async () => {
    const hostile = JSON.parse(JSON.stringify(payload.journal));
    hostile.entries[0].blocks = [
      {
        type: "p",
        spans: [{ kind: "text", text: "<script>window.pwned = true;</script><img onerror=1>" }],
      },
    ];
    const html = readFileSync(join(publicDir, "index.html"), "utf8");
    const { window } = new JSDOM(html, { url: "https://nova.example/", runScripts: "outside-only" });
    window.fetch = (url) =>
      Promise.resolve({
        json: () => Promise.resolve(url.includes("/api/digest") ? payload.digest : hostile),
      });
    window.eval(readFileSync(join(publicDir, "app.js"), "utf8"));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    assert.equal(window.pwned, undefined);
    assert.equal(window.document.querySelectorAll("script:not([src])").length, 0);
    assert.equal(window.document.querySelectorAll("img").length, 0);
    assert.match(cards(window)[0].textContent, /<script>/);
  });
});

/* Edvard, issues.md 2026-08-09: "when a journey card is opened, the Digest is
 * revealed. Below that, a 'read the full journal' button to expand the full
 * journal. If the full journal text is clicked or the button, the full journal
 * is closed again. So its a drawer within a drawer."
 *
 * Three levels, and each transition is a separate assertion below, because the
 * bug this shape invites is one click doing two things -- a tap on the inner
 * button also collapsing the outer card, since both listeners see it. */
describe("a drawer within a drawer", () => {
  let window;
  before(async () => {
    window = await loadSite();
  });

  const reading = (card) => card.classList.contains("is-reading");
  const journalButton = (card) => card.querySelector(".journal-toggle");
  const firstCard = () => cards(window)[0];

  test("a collapsed card shows only the brief", () => {
    const card = firstCard();
    assert.ok(card.querySelector(".entry-brief"), "the brief is always present");
    assert.ok(!expanded(card));
    assert.ok(!reading(card));
  });

  test("opening the card reveals the rest of the digest and the button", () => {
    const card = firstCard();
    click(window, card.querySelector(".entry-brief"));
    assert.ok(expanded(card));
    assert.ok(journalButton(card), "the button exists");
    assert.ok(!reading(card), "the journal itself stays shut at this level");
  });

  test("the button opens the full journal without collapsing the card", () => {
    const card = firstCard();
    click(window, journalButton(card));
    assert.ok(reading(card), "the journal opened");
    assert.ok(expanded(card), "and the card it sits inside did not collapse");
  });

  test("the button says what it will do next", () => {
    const card = firstCard();
    assert.equal(journalButton(card).textContent, "Close the full journal");
  });

  test("clicking the journal text closes the journal, not the card", () => {
    const card = firstCard();
    assert.ok(reading(card));
    click(window, card.querySelector(".entry-body"));
    assert.ok(!reading(card), "the journal closed");
    assert.ok(expanded(card), "the card stayed open");
    assert.equal(journalButton(card).textContent, "Read the full journal");
  });

  test("the button closes it again too", () => {
    const card = firstCard();
    click(window, journalButton(card));
    assert.ok(reading(card));
    click(window, journalButton(card));
    assert.ok(!reading(card));
    assert.ok(expanded(card));
  });

  test("collapsing the card shuts the drawer inside it", async () => {
    // Otherwise reopening a card drops you back into the middle of a 115-line
    // entry you had already closed.
    const card = firstCard();
    click(window, journalButton(card));
    assert.ok(reading(card));
    click(window, card.querySelector(".entry-brief"));
    assert.ok(!expanded(card));
    click(window, card.querySelector(".entry-brief"));
    assert.ok(expanded(card));
    assert.ok(!reading(card), "reopening the card does not reopen the journal");
  });
});

describe("a payload cached before the brief existed", () => {
  /* sw.js is network-first and caches /api responses, so opening the app with
   * the tailnet down after this deploy pairs the new app.js with the last
   * payload the old build served. The card must still say something, and must
   * degrade to what it showed before rather than to a worse thing. */
  test("still renders a brief, clamped, from the unsplit text", async () => {
    const stale = JSON.parse(JSON.stringify(payload));
    for (const line of stale.digest.lines) { delete line.briefSpans; delete line.restSpans; }
    for (const entry of stale.journal.entries) delete entry.briefSpans;

    const html = readFileSync(join(publicDir, "index.html"), "utf8");
    const dom = new JSDOM(html, { url: "https://nova.example/", runScripts: "outside-only", pretendToBeVisual: true });
    const { window } = dom;
    window.fetch = (url) => Promise.resolve({
      json: () => Promise.resolve(url.includes("/api/digest") ? stale.digest : stale.journal),
    });
    window.scrollTo = () => {};
    window.eval(readFileSync(join(publicDir, "app.js"), "utf8"));
    await new Promise((resolve) => window.setTimeout(resolve, 0));

    const shown = [...window.document.querySelectorAll(".entry")];
    assert.ok(shown.length);
    for (const card of shown) {
      const brief = card.querySelector(".entry-brief");
      assert.ok(brief && brief.textContent.trim(), "a stale payload still gets a summary");
      assert.ok(brief.classList.contains("is-unsplit"), "and it is marked for clamping");
    }
    // The digest drawer has nothing to show, so the card opens onto the button.
    assert.equal(window.document.querySelector(".entry-digest"), null);
    assert.ok(shown.every((c) => c.querySelector(".journal-toggle")));
  });
});

/* The comment drawer (ideas.md #44): "add a button with a chat bubble icon
 * that opens a multiline text input so that i can add a comment more
 * directly towards your cycles."
 *
 * The fixture is built for this: cycle 57 has two entries and two comments
 * (one already acknowledged), cycle 55 has one comment spanning two
 * paragraphs, and the fourth entry has no cycle number at all. Those are
 * the four cases the rules below actually turn on, and none of them is
 * hypothetical -- six live cycles have a second entry.
 */
describe("commenting on a cycle", () => {
  let window;
  const cardFor = (w, cycle) =>
    cards(w).find((c) => c.querySelector("h2").textContent === "Cycle " + cycle);
  const bubble = (card) => card.querySelector(".comment-toggle");
  const drawerOpen = (card) => card.classList.contains("is-commenting");

  before(async () => {
    window = await loadSite();
  });

  test("a numbered cycle gets a chat bubble", () => {
    assert.ok(bubble(cardFor(window, 57)));
    assert.ok(bubble(cardFor(window, 55)));
  });

  test("an entry with no cycle number gets none", () => {
    // There is nothing to key a comment to, so a box there would swallow it.
    const orphan = cards(window).find((c) => !/^Cycle /.test(c.querySelector("h2").textContent));
    assert.ok(orphan, "the fixture must contain an entry with no cycle");
    assert.equal(bubble(orphan), null);
  });

  test("a cycle with two entries has exactly one bubble", () => {
    // Two would be two places to look for the same conversation.
    const both = cards(window).filter((c) => c.querySelector("h2").textContent === "Cycle 57");
    assert.equal(both.length, 2, "the fixture must contain a cycle with an addendum");
    assert.equal(both.filter((c) => bubble(c)).length, 1);
  });

  test("the bubble carries the number of comments on that cycle", () => {
    assert.equal(bubble(cardFor(window, 57)).textContent, "💬 2");
    assert.equal(bubble(cardFor(window, 55)).textContent, "💬 1");
  });

  test("the drawer starts shut", () => {
    assert.ok(!drawerOpen(cardFor(window, 57)));
  });

  test("tapping the bubble opens the drawer without expanding the card", () => {
    // He asked to comment on a cycle, not to read one first.
    const card = cardFor(window, 55);
    click(window, bubble(card));
    assert.ok(drawerOpen(card));
    assert.ok(!expanded(card), "the card itself must stay collapsed");
    click(window, bubble(card));
    assert.ok(!drawerOpen(card));
  });

  test("existing comments are shown in the server's order, with the read ones marked", () => {
    // Oldest at the top, newest just above the box he types in: "the
    // conversation goes downwards" (Edvard, 2026-08-10). The order is the
    // server's -- `comments_by_cycle` sorts by stamp across both sections --
    // and this file renders it as given rather than sorting again.
    const card = cardFor(window, 57);
    const shown = [...card.querySelectorAll(".comment")];
    assert.deepEqual(
      shown.map((c) => c.querySelector(".comment-body").textContent),
      payload.comments.byCycle["57"].map((c) => c.text.split("\n\n")[0]),
    );
    // The older of these two is the one a cycle already retired, so "read"
    // is above "unread" here -- which is the case reversing would get wrong.
    assert.ok(shown[0].classList.contains("is-acknowledged"));
    assert.ok(!shown[1].classList.contains("is-acknowledged"));
  });

  test("a comment's paragraph breaks survive to the page", () => {
    // A comment is prose. Joining its paragraphs would be rewriting him.
    // Scoped to the direct children, because Nova's reply lives inside the
    // comment it answers and its paragraphs are not his.
    const card = cardFor(window, 55);
    const paragraphs = [...card.querySelectorAll(".comment > .comment-body")].map((p) => p.textContent);
    assert.deepEqual(paragraphs, payload.comments.byCycle["55"][0].text.split("\n\n"));
  });

  /* Answering a comment on the card (2026-08-10): "A good idea is to have
   * the session that created the Journal instantly reply to my comments on
   * the Journal! That would be so cool, to have a conversation with
   * comments on the Journal entry."
   *
   * The fixture gives cycle 55's comment a reply and cycle 57's none. */

  test("Nova's reply is shown under the comment it answers", () => {
    const card = cardFor(window, 55);
    const reply = card.querySelector(".comment .comment-reply");
    assert.ok(reply, "the reply is rendered");
    assert.equal(
      reply.querySelector(".comment-body").textContent,
      payload.comments.byCycle["55"][0].reply,
    );
    assert.equal(reply.querySelector(".comment-stamp").textContent, "2026-08-09 13:12");
  });

  test("a comment with no reply gets no empty reply block", () => {
    const card = cardFor(window, 57);
    assert.equal(card.querySelector(".comment-reply"), null);
    assert.equal(card.querySelector(".comment-waiting"), null);
  });

  test("a reply still coming says so, because it can be a cycle away", async () => {
    /* The bridge runs one CLI call at a time and a Nova cycle can hold it
     * for the better part of an hour. Silence for that long reads as
     * broken, so the wait is stated. */
    /* Timers captured even though this test never steps one: a pending
     * comment schedules a poll that reschedules itself, and a real one left
     * running in a window nobody closes keeps node's event loop alive after
     * the last assertion. */
    const w = await loadSite("/", {
      comments: withPending(57),
      install: captureTimers,
    });
    const card = cardFor(w, 57);
    assert.equal(card.querySelector(".comment-waiting").textContent, "Nova is replying…");
  });

  test("the page polls until the reply lands, then lets go", async () => {
    /* The poll is the only thing that turns "replying…" into the reply
     * without him reloading, and the only thing that stops. Both halves are
     * asserted here: a poll that never fired and a poll that never stopped
     * would each pass a test that only checked the other. */
    let timers;
    const w = await loadSite("/", {
      comments: withPending(57),
      install: (win) => { timers = captureTimers(win); },
    });
    const card = cardFor(w, 57);
    assert.ok(card.querySelector(".comment-waiting"), "waiting to begin with");

    const answered = JSON.parse(JSON.stringify(payload.comments));
    answered.byCycle["57"][0].reply = "Thanks — here is what I found.";
    answered.byCycle["57"][0].replyPending = false;
    let served = 0;
    w.fetch = (url) => {
      if (String(url).includes("/api/comments")) {
        served += 1;
        return Promise.resolve({ json: () => Promise.resolve(served === 1 ? withPending(57) : answered) });
      }
      return Promise.resolve({ json: () => Promise.resolve({}) });
    };

    await timers.fire();
    assert.equal(served, 1, "it re-fetched on its own");
    assert.ok(card.querySelector(".comment-waiting"), "still waiting, so still polling");

    await timers.fire();
    assert.equal(served, 2);
    assert.equal(card.querySelector(".comment-waiting"), null);
    assert.equal(
      card.querySelector(".comment-reply .comment-body").textContent,
      "Thanks — here is what I found.",
    );
    assert.equal(timers.queued.length, 0, "nothing left scheduled once the reply is in");
  });

  test("navigating while a reply is coming does not leave two pollers", async () => {
    /* A render throws every drawer away and builds new ones. The discarded
     * drawer's poll has to go with it, or every tap he makes while waiting
     * adds another poller hitting his own site for as long as the reply
     * takes -- and a reply can take the length of a cycle. */
    let timers;
    const w = await loadSite("/", {
      comments: withPending(57),
      install: (win) => { timers = captureTimers(win); },
    });
    assert.equal(timers.queued.length, 1, "one poll for the one pending reply");

    const link = w.document.querySelector("a[href^='/cycle/']");
    assert.ok(link, "the fixture has a per-cycle link to navigate with");
    click(w, link);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(timers.queued.length, 1, "still one, not one per navigation");
  });

  test("typing in the box does not collapse the card out from under it", () => {
    const card = cardFor(window, 57);
    click(window, bubble(card));
    const wasExpanded = expanded(card);
    click(window, card.querySelector(".comment-text"));
    assert.equal(expanded(card), wasExpanded);
    assert.ok(drawerOpen(card), "and the drawer stays open");
  });

  test("Comment posts the cycle it belongs to, as JSON", async () => {
    const card = cardFor(window, 55);
    click(window, bubble(card));
    card.querySelector(".comment-text").value = "  do more research  ";
    click(window, card.querySelector(".comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));

    const sent = window.posted.at(-1);
    assert.equal(sent.url, "/api/comment");
    assert.equal(sent.headers["Content-Type"], "application/json");
    assert.deepEqual(sent.body, { cycle: 55, text: "do more research" });
  });

  test("the box clears only once the server confirms the write", async () => {
    const card = cardFor(window, 57);
    click(window, bubble(card));
    const box = card.querySelector(".comment-text");

    window.postReply = { ok: false, message: "409 conflict" };
    box.value = "a thought worth keeping";
    click(window, card.querySelector(".comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(box.value, "a thought worth keeping", "a failed write must not eat the text");
    assert.match(card.querySelector(".comment-status").textContent, /409/);
    assert.ok(card.querySelector(".comment-status").classList.contains("is-error"));

    window.postReply = { ok: true, message: "ok" };
    click(window, card.querySelector(".comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(box.value, "");
  });

  test("Enter is a newline here, not a send", async () => {
    /* The capture box sends on Enter; this box must not. He asked for "a
     * multiline text input" and his own example is two sentences long, so
     * Enter meaning "send" would put a modifier key between him and every
     * paragraph break -- on a phone keyboard he does not have one. Pinned
     * because the two boxes look alike enough that making them "consistent"
     * is an obvious-looking change that would break this one. */
    const card = cardFor(window, 55);
    click(window, bubble(card));
    const box = card.querySelector(".comment-text");
    box.value = "first line";
    const before = window.posted.length;
    box.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }),
    );
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(window.posted.length, before, "Enter posted the comment");
  });

  test("an empty comment is never sent", async () => {
    const card = cardFor(window, 55);
    click(window, bubble(card));
    card.querySelector(".comment-text").value = "   \n  ";
    const before = window.posted.length;
    click(window, card.querySelector(".comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(window.posted.length, before);
  });

  test("a comments endpoint that fails costs the bubbles, not the feed", async () => {
    const w = await loadSite("/", { failComments: true });
    assert.equal(cards(w).length, payload.journal.entries.length);
    assert.equal(bubble(cardFor(w, 57)).textContent, "💬");
  });
});

/* Replying to Needs Edvard (2026-08-10). Edvard: "the 'needs Edvard' is
 * still missing a comment block, so its hard for me to answer it. [...]
 * Where did you intend me to answer it? [...] I want a reply button on it."
 *
 * The section had been asking him a question for eight cycles with nowhere
 * to type an answer. These tests pin the two things that makes it fixed:
 * the box is reachable without a click, and what it sends names the block
 * rather than a cycle. */
describe("replying to Needs Edvard", () => {
  const asking = {
    ...payload.digest,
    hasNeedsEdvard: true,
    needsEdvardBlocks: [{ type: "p", spans: [{ kind: "text", text: "Decide about idea #56." }] }],
  };
  const withReply = {
    byCycle: payload.comments.byCycle,
    needs: [{ cycle: null, stamp: "2026-08-10 08:20", text: "go ahead and do it", acknowledged: false }],
  };
  const needsEl = (window) => window.document.getElementById("needs");
  const box = (window) => needsEl(window).querySelector(".comment-text");

  test("the box is there without a click, unlike a card's", async () => {
    /* The one deliberate difference from a journal card. A fold is what
     * hid this for eight cycles, and the section only exists at all when
     * something is being asked -- so there is no state where an open box
     * is noise. */
    const window = await loadSite("/", { digest: asking });
    assert.ok(!needsEl(window).hidden);
    assert.ok(box(window), "the answer field is in the DOM with no interaction");
    assert.ok(needsEl(window).querySelector(".comment-send"));
  });

  test("no box at all when nothing is being asked", async () => {
    // The section is hidden; a reply field inside a hidden section would be
    // a box he can never reach, which is the bug this feature is fixing.
    const window = await loadSite();
    assert.ok(needsEl(window).hidden);
  });

  test("the reply posts the block, not a cycle", async () => {
    const window = await loadSite("/", { digest: asking });
    box(window).value = "  go ahead and do it  ";
    click(window, needsEl(window).querySelector(".comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));

    const sent = window.posted.at(-1);
    assert.equal(sent.url, "/api/comment");
    assert.deepEqual(sent.body, { target: "needs", text: "go ahead and do it" });
    assert.ok(!("cycle" in sent.body), "a cycle would file his answer on a random card");
  });

  test("answers already given are shown, so a saved one is tellable from a lost one", async () => {
    const window = await loadSite("/", { digest: asking, comments: withReply });
    assert.match(needsEl(window).textContent, /go ahead and do it/);
    assert.equal(needsEl(window).querySelectorAll(".comment").length, 1);
  });

  test("a needs reply never paints onto a journal card", async () => {
    const window = await loadSite("/", { digest: asking, comments: withReply });
    const feed = window.document.getElementById("feed");
    assert.ok(!feed.textContent.includes("go ahead and do it"));
  });

  test("a failed write keeps what he typed", async () => {
    const window = await loadSite("/", { digest: asking });
    window.postReply = { ok: false, message: "409 conflict" };
    box(window).value = "go ahead and do it";
    click(window, needsEl(window).querySelector(".comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(box(window).value, "go ahead and do it");
    assert.match(needsEl(window).querySelector(".comment-status").textContent, /409/);
  });

  test("the question and the answer box are still one section", async () => {
    // Reading the ask and answering it should not be two places to look.
    const window = await loadSite("/", { digest: asking });
    assert.match(needsEl(window).textContent, /Decide about idea #56/);
    assert.ok(needsEl(window).contains(box(window)));
  });
});

describe("the Needs Edvard reply box survives a re-render", () => {
  /* `load()` runs again on popstate and after a capture, so renderNeeds is
   * not a once-per-page-load function. Without clearing the old drawer the
   * section would grow a second answer box on every navigation -- two
   * places to type one answer, and only one of them holding what he wrote. */
  test("navigating back does not leave two boxes", async () => {
    const asking = {
      ...payload.digest,
      hasNeedsEdvard: true,
      needsEdvardBlocks: [{ type: "p", spans: [{ kind: "text", text: "Decide about idea #56." }] }],
    };
    const window = await loadSite("/", { digest: asking });
    const needs = window.document.getElementById("needs");
    assert.equal(needs.querySelectorAll(".comment-text").length, 1);

    window.dispatchEvent(new window.PopStateEvent("popstate"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(needs.querySelectorAll(".comment-text").length, 1, "a second box appeared");
    assert.equal(needs.querySelectorAll(".comment-drawer").length, 1);
  });
});
