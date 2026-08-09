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
  window.scrollTo = () => {}; // jsdom has none, and the link handler calls it
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
/** A digest line as the card renders it -- spans joined, so the `**` the
 *  raw `text` field still carries is not compared against the DOM. */
const lineText = (line) => line.spans.map((s) => s.text).join("");
/** The headline a collapsed card shows for a digest line -- its first drawer.
 *  Distinct from `lineText`: the rest of the line is revealed on open, so
 *  comparing a card against the whole line would have been comparing it
 *  against text the card is not supposed to be showing. */
const lineBrief = (line) => line.briefSpans.map((s) => s.text).join("");
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
