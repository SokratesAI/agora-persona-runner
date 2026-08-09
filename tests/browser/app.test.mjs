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

/** Load the site at `path` with fetch stubbed to serve the fixture. */
async function loadSite(path = "/") {
  const html = readFileSync(join(publicDir, "index.html"), "utf8");
  const dom = new JSDOM(html, {
    url: "https://nova.example" + path,
    runScripts: "outside-only",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  window.fetch = (url) =>
    Promise.resolve({
      json: () =>
        Promise.resolve(url.includes("/api/digest") ? payload.digest : payload.journal),
    });
  window.eval(readFileSync(join(publicDir, "app.js"), "utf8"));
  // app.js renders from two resolved promises; let the microtasks drain.
  await new Promise((resolve) => window.setTimeout(resolve, 0));
  return window;
}

/** A real click, the way a browser dispatches one (bubbling to the card). */
function click(window, node) {
  node.dispatchEvent(new window.MouseEvent("click", { bubbles: true, cancelable: true }));
}

const cards = (window) => [...window.document.querySelectorAll(".entry")];
const expanded = (card) => card.classList.contains("is-expanded");

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
    // The body is the furthest thing from the header that is still the card,
    // and before this change it was the one place clicking did nothing.
    for (const selector of [".entry-body", ".entry-summary", ".entry-meta", ".entry-toggle"]) {
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
    const summary = (card) => card.querySelector(".entry-summary").textContent;
    assert.notEqual(summary(first), summary(second));
  });

  test("the digest line goes to the cycle's own run, not to its addendum", () => {
    const line = payload.digest.lines.find((l) => l.cycle === 57).text;
    const [addendum, run] = cards(window);
    assert.equal(run.querySelector(".entry-summary").textContent, line);
    assert.notEqual(addendum.querySelector(".entry-summary").textContent, line);
  });

  test("the addendum summarises itself from its own first paragraph", () => {
    const addendum = cards(window)[0];
    const opening = payload.journal.entries[0].blocks.find((b) => b.type === "p");
    const text = opening.spans.map((s) => s.text).join("");
    assert.equal(addendum.querySelector(".entry-summary").textContent, text);
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
    const line = payload.digest.lines.find((l) => l.cycle === 57).text;
    const summaries = cards(window).map((c) => c.querySelector(".entry-summary").textContent);
    assert.equal(summaries.filter((s) => s === line).length, 1);
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
