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
import { test, before, describe, after } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const publicDir = join(here, "..", "..", "agora_runner", "nova_public");
const payload = JSON.parse(readFileSync(join(here, "fixtures", "payload.json"), "utf8"));

/* Every window this file opens, closed once the whole file is done.
 *
 * jsdom runs real timers on the Node event loop, and app.js now schedules a
 * poll that reschedules itself forever. Leave one window open and `node
 * --test` never exits -- it ran for 33 minutes on this suite before anyone
 * noticed it was not slow, it was hung.
 *
 * This was an `afterEach` for exactly one push, and that was wrong twice over
 * (cycle 92). Five suites open one window in `before` and share it across
 * their tests; an `afterEach` closed it after the first of them and left the
 * rest asserting against a window with no `document` -- 45 failures. And it
 * did not even fix the hang, because two tests below built their own `JSDOM`
 * without registering it, so `node --test` still never exited. Hence
 * `openWindow` as the single door: a raw `new JSDOM` in this file is a leak,
 * and the last test in this file is what says so. */
const openWindows = [];
after(() => {
  for (const window of openWindows.splice(0)) window.close();
});

/** The only place this file may construct a window. See `openWindows`. */
function openWindow(html, options) {
  const dom = new JSDOM(html, options);
  openWindows.push(dom.window);
  return dom;
}

/** A stub response carrying the fields `app.js` actually reads off a real one.
 *
 *  `ok` is the one that matters and every double in this file used to omit
 *  it. `fetch` resolves on a 500 or a 502 as readily as on a 200, so `ok`
 *  is the only thing separating a page's success path from its error path
 *  -- and a double without it can express "the network died" and "the
 *  server answered" and nothing in between, which is exactly the case that
 *  broke on the live site. Every test here was therefore passing through a
 *  branch a real browser cannot reach.
 */
function res(body, status = 200) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
  });
}

/** A proxy's 502 page: a real status, and a body that is not JSON at all, so
 *  reading it rejects. This is the shape the site sees when something in
 *  front of the server fails rather than the server itself, and it is the
 *  reason the helper under test reads the body with two callbacks instead
 *  of one. */
function unreadableBody(status) {
  return Promise.resolve({
    ok: false,
    status,
    json: () => Promise.reject(new SyntaxError("Unexpected token < in JSON")),
  });
}

/** The shape `sw.js` hands back when it answers a dead network out of its
 *  own cache: an ordinary, entirely successful `200` whose only tell is the
 *  header it stamps on the way past.
 *
 *  Every other double in this file omits `headers`, which is why this branch
 *  went untested for as long as it did -- without one, a test can say "the
 *  network answered" and "the network died" and nothing in between, and the
 *  gap between those two is exactly where issue #81 lived. */
function replayedRes(body) {
  return Promise.resolve({
    ok: true,
    status: 200,
    headers: { get: (name) => (name === "X-Nova-Replayed" ? "1" : null) },
    json: () => Promise.resolve(body),
  });
}

/** The other real shape: a 304 is not `ok` and carries no body at all, so a
 *  page that checks `ok` before checking for 304 turns every successful
 *  conditional poll into an error. Kept as its own helper rather than a
 *  status argument to `res`, because the empty body is the whole point. */
function notModified() {
  return Promise.resolve({
    ok: false,
    status: 304,
    json: () => Promise.reject(new Error("a 304 carries no body")),
  });
}

/** Load the site at `path` with fetch stubbed to serve the fixture.
 *
 * `failComments` rejects only `/api/comments`, which is how the "a broken
 * endpoint costs the bubbles, not the feed" test reaches the catch that
 * app.js's Promise.all relies on.
 *
 * `digest` and `comments` override those two responses. The Needs the owner
 * tests need both: the live fixture's digest asks for nothing (so the
 * section is hidden), and its comments carry no reply to it.
 *
 * `install` runs against the window just before app.js is evaluated, for a
 * test that has to be in place before the page's first render -- the reply
 * poll schedules its first timer there.
 *
 * `journal` is a function of the requested URL rather than a fixed body,
 * which is what the pagination tests need: the whole point of a window is
 * that the answer depends on the query string. */
async function loadSite(path = "/", { failComments = false, commentsStatus = 200, journalStatus = 200, boardStatus = 200, costsStatus = 200, retroStatus = 200, planStatus = 200, notesStatus = 200, digestStatus = 200, askStatus = 200, unparsable = false, replayed = false, digest, comments, install, journal, board, costs, retro, plan, notes, ask } = {}) {
  const html = readFileSync(join(publicDir, "index.html"), "utf8");
  const dom = openWindow(html, {
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
  /* Which routes the worker answers out of its cache.
   *
   * `replayed: true` means the journal, which is all it could mean when the
   * journal was the only route that read the stamp. An array of path
   * fragments replays any of the others, and the two are kept apart on
   * purpose: a test that replays `/api/board` must leave the journal live,
   * or it cannot tell a board that marked itself from a board that inherited
   * a mark off the header. */
  const replayPaths = replayed === true ? ["/api/journal"] : (replayed || []);
  const serve = (url) => {
    if (url.includes("/api/comments")) {
      return failComments
        ? Promise.reject(new Error("comments are down"))
        : res(comments || payload.comments, commentsStatus);
    }
    if (url.includes("/api/costs")) {
      return res(costs || payload.costs, costsStatus);
    }
    if (url.includes("/api/retro")) {
      // No fixture default: the retro ledger is empty until the first
      // Friday, so "nothing supplied" has to mean the empty page rather
      // than a body no live server has ever sent.
      return res(retro || { scoreKeys: [], range: [1, 10], retros: [] }, retroStatus);
    }
    if (url.includes("/api/plan")) {
      // No fixture default, for the reason `/api/retro` has none: both
      // documents are written by a cycle and a fresh vault has neither,
      // so "nothing supplied" has to mean the empty page rather than a
      // body no live server has ever sent.
      return res(plan || { documents: [] }, planStatus);
    }
    if (url.includes("/api/notes")) {
      // No fixture default, for the reason `/api/retro` and `/api/plan`
      // have none: a vault where the owner has never left a note really is
      // empty, and "nothing supplied" must mean that rather than a body
      // no live server has ever sent.
      return res(notes || { notes: [], waitingTotal: 0, readTotal: 0 }, notesStatus);
    }
    if (url.includes("/api/ask")) {
      // A function, because the point of most of these tests is that the
      // answer *changes* between polls -- a fixed body could never show
      // an answer arriving. No default fixture, for the reason retro and
      // plan have none: an unused questions page really is empty.
      const body = typeof ask === "function" ? ask(url) : ask;
      // A fixture may hand back a rejected promise to model the server
      // being unreachable -- passed straight through, because `res` would
      // wrap the promise itself as the response body.
      if (body && typeof body.then === "function") return body;
      return res(body || { conversationId: null, messages: [], waiting: false }, askStatus);
    }
    if (url.includes("/api/board")) {
      // Two shapes off one route, told apart the way the server tells
      // them apart: `item=` is a tap on a row, anything else is the list.
      const asked = board ? board(url) : null;
      const body = asked || (url.includes("item=") ? payload.boardItem : payload.board);
      return res(body, boardStatus);
    }
    if (url.includes("/api/digest")) {
      // A function for the same reason `journal` is one: the digest takes
      // a window too now, so its answer depends on the query string.
      const body = typeof digest === "function" ? digest(url) : digest || payload.digest;
      return res(body, digestStatus);
    }
    const body = journal ? journal(url) : payload.journal;
    if (unparsable) return unreadableBody(journalStatus);
    return res(body, journalStatus);
  };
  /* The stamp goes on whatever `serve` already decided to answer, rather
   * than each branch growing its own replayed variant. That is also what the
   * worker does -- it rebuilds a response it found in the cache and knows
   * nothing about which route produced it. */
  const stamped = (answer) => answer.then((r) => ({
    ok: r.ok,
    status: r.status,
    headers: { get: (name) => (name === "X-Nova-Replayed" ? "1" : null) },
    json: r.json,
  }));
  window.fetch = (url, init) => {
    if (init && init.method === "POST") {
      window.posted.push({ url, headers: init.headers, body: JSON.parse(init.body) });
      return res(window.postReply);
    }
    const answer = serve(url);
    return replayPaths.some((p) => url.includes(p)) ? stamped(answer) : answer;
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
 *  raw `text` field still carries is not compared against the DOM. Both
 *  drawers, since that is the whole line: the server stopped sending a
 *  third copy of it when `/api/digest` learned to send one window. */
const lineText = (line) =>
  [...line.briefSpans, ...line.restSpans].map((s) => s.text).join("");
/** The headline a collapsed card shows for a digest line -- its first drawer.
 *  Distinct from `lineText`: the rest of the line is revealed on open, so
 *  comparing a card against the whole line would have been comparing it
 *  against text the card is not supposed to be showing. */
const lineBrief = (line) => line.briefSpans.map((s) => s.text).join("");
const expanded = (card) => card.classList.contains("is-expanded");

/** `install` hook that puts the real style.css into the document.
 *
 *  index.html links the stylesheet, and jsdom fetches no external resource,
 *  so without this every `display` in this file is a jsdom default rather
 *  than anything the site would show. Inlined as a `<style>` because that is
 *  the one shape jsdom's cascade does implement. */
function withStyle(window) {
  const style = window.document.createElement("style");
  style.textContent = readFileSync(join(publicDir, "style.css"), "utf8");
  window.document.head.appendChild(style);
}

/** The comments payload with one cycle's newest comment awaiting a reply. */
function withPending(cycle) {
  const copy = JSON.parse(JSON.stringify(payload.comments));
  copy.byCycle[String(cycle)][0].replyPending = true;
  return copy;
}

/** app.js's page-wide entry poll interval. Held apart from the comment-drawer
 *  polls `captureTimers` was written for: `fire()` would otherwise re-render
 *  the whole page underneath a test that is stepping one drawer, and every
 *  existing `queued.length` assertion would be counting a poll it never meant
 *  to count. */
const PAGE_POLL_MS = 30000;

/** Take `window.setTimeout` over so a poll can be stepped rather than waited
 *  out. jsdom runs real timers, and the reply poll is on an 8-second cycle;
 *  a test that slept through two of them would be a 16-second test. The real
 *  timer is kept for draining microtasks. */
function captureTimers(window) {
  const real = window.setTimeout.bind(window);
  const scheduled = new Map();
  let nextId = 1;
  window.setTimeout = (fn, ms) => {
    const id = nextId++;
    scheduled.set(id, { fn, ms });
    return id;
  };
  /* Modelled rather than stubbed out: whether a discarded poll is actually
   * cancelled is one of the things under test, and a no-op clearTimeout
   * would make that unobservable. */
  window.clearTimeout = (id) => scheduled.delete(id);
  const drain = async () => {
    for (let i = 0; i < 5; i += 1) {
      await new Promise((resolve) => real(resolve, 0));
    }
  };
  const pending = (isPagePoll) =>
    [...scheduled.entries()].filter(([, e]) => (e.ms === PAGE_POLL_MS) === isPagePoll);
  const fireThese = async (isPagePoll) => {
    const due = pending(isPagePoll);
    due.forEach(([id, e]) => {
      scheduled.delete(id);
      e.fn();
    });
    await drain();
    return due.length;
  };
  return {
    get queued() {
      return pending(false).map(([, e]) => e.fn);
    },
    get queuedPagePolls() {
      return pending(true).map(([, e]) => e.fn);
    },
    /** Run every drawer poll scheduled so far, then let the fetches settle. */
    async fire() {
      return fireThese(false);
    },
    /** The same, for the page-wide entry poll only. */
    async firePagePoll() {
      return fireThese(true);
    },
  };
}

describe("cards expand and collapse", () => {
  let window;
  before(async () => {
    window = await loadSite();
  });

  test("every cycle in the payload gets one card, not every entry", () => {
    const cycles = new Set(payload.journal.entries.map((e) => e.cycle));
    assert.equal(cards(window).length, cycles.size);
    assert.ok(cycles.size < payload.journal.entries.length,
      "the fixture must hold a cycle with two entries, or this pins nothing");
  });

  test("cards start collapsed on the feed", () => {
    assert.ok(cards(window).every((card) => !expanded(card)));
  });

  test("a click anywhere on the card expands it, not just the header", () => {
    // `.entry-body` used to be in this list -- it was the furthest thing from
    // the header that is still the card. It is deliberately not any more:
    // The owner asked for the full journal to close when its own text is
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

  test("the toggle controls the drawer it actually owns", () => {
    for (const card of cards(window)) {
      const controlled = card.querySelector(".entry-toggle").getAttribute("aria-controls");
      assert.equal(card.querySelector(".entry-parts").id, controlled);
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

/* the owner, on the comments board at cycle 81: "i do not like the double entry
 * Journal cards. If a double entry is necessary like for cycle 81, have it be
 * combined into one card that has tabs or something similar. Its confusing
 * that its two separate cards."
 *
 * Cycle 105 answered that on `/cycle/N` and left the feed drawing two. This
 * block used to be called "two entries for one cycle are two different cards"
 * and pinned the old behaviour in place: a digest line handed to the earlier
 * card, an addendum summarising itself so the two would not look identical,
 * an anchor id and a comment bubble owned by whichever card came first. All
 * of that machinery existed only because there were two cards. */
/* the owner, issues #86, on the feed rather than on `/cycle/N`. The two
 * surfaces draw a card from the same rules and #199 made that deliberate,
 * so the rule is asserted on both -- it lives in two functions. */
describe("a feed card carries one title, not two", () => {
  function soloCycle() {
    return payload.journal.entries.find(
      (e) => e.cycle !== null
        && payload.journal.entries.filter((o) => o.cycle === e.cycle).length === 1
    );
  }

  function cardFor(window, cycle) {
    return cards(window).find((c) => c.querySelector("h2").textContent === "Cycle " + cycle);
  }

  /* Every solo cycle in the fixture has a digest line, so the second case is
   * built by dropping one rather than found -- which is the live shape for
   * the 55 entries older than the twelve lines the digest keeps. */
  function withoutDigestLine(cycle) {
    const digest = JSON.parse(JSON.stringify(payload.digest));
    digest.lines = digest.lines.filter((l) => l.cycle !== cycle);
    return digest;
  }

  test("a cycle with a digest line drops its heading title", async () => {
    const solo = soloCycle();
    assert.ok(payload.digest.lines.some((l) => l.cycle === solo.cycle));
    // The title is written in rather than taken from the fixture: that
    // entry's own title is empty, so without this the assertion below holds
    // whether or not the code under test exists. Caught by mutation.
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.find((e) => e.cycle === solo.cycle).title =
      "A second title saying what the digest line already says";
    const window = await loadSite("/", { journal: () => journal });
    const card = cardFor(window, solo.cycle);
    assert.equal(card.querySelector(".entry-title"), null);
    assert.equal(card.querySelector(".entry-brief").textContent,
      lineBrief(payload.digest.lines.find((l) => l.cycle === solo.cycle)));
  });

  /* the owner, comments board 2026-08-22: "Sometimes there are two titles and
   * they repeat eachoter with different words ... I like the one with the
   * colored backline", then "The one line summary can be cut." The card he
   * photographed had no digest line, so #86's rule did not cover it and the
   * heading title sat above the entry's own brief, saying the same thing. */
  test("a cycle briefed from its own prose drops its heading title too", async () => {
    const solo = soloCycle();
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const entry = journal.entries.find((e) => e.cycle === solo.cycle);
    entry.title = "A heading repeating the entry's own opening sentence";
    entry.briefSpans = [{ kind: "text", text: "The brief the entry wrote for itself." }];
    const window = await loadSite("/", {
      journal: () => journal,
      digest: withoutDigestLine(solo.cycle),
    });
    const card = cardFor(window, solo.cycle);
    assert.equal(card.querySelector(".entry-title"), null);
    // Not vacuous: the label that replaced it is still drawn.
    assert.equal(card.querySelector(".entry-brief").textContent,
      "The brief the entry wrote for itself.");
  });

  /* Nothing to label the card with: no digest line, no brief of its own,
   * and no prose for the `is-unsplit` fallback to brief from either. That
   * fallback fills the same slot, so it counts as a label -- a card that
   * falls back to it and *also* draws the title is the bug again. */
  test("a card with no brief at all keeps its title, since nothing else labels it", async () => {
    const solo = soloCycle();
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const title = "The only sentence on this card that was written as a title";
    const entry = journal.entries.find((e) => e.cycle === solo.cycle);
    entry.title = title;
    entry.briefSpans = [];
    entry.blocks = [];
    const window = await loadSite("/", {
      journal: () => journal,
      digest: withoutDigestLine(solo.cycle),
    });
    const card = cardFor(window, solo.cycle);
    assert.equal(card.querySelector(".entry-title").textContent, title);
    assert.equal(card.querySelector(".entry-brief"), null);
  });

  /* The stale-payload path: sw.js can pair this app.js with a payload that
   * has no `briefSpans` at all, and the card then briefs from the digest
   * line's raw text. That is still a label, so the title stays out. */
  test("a card briefed by the is-unsplit fallback drops its title too", async () => {
    const solo = soloCycle();
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const entry = journal.entries.find((e) => e.cycle === solo.cycle);
    entry.title = "A heading beside a fallback brief saying the same thing";
    entry.briefSpans = [];
    const digest = JSON.parse(JSON.stringify(payload.digest));
    const line = digest.lines.find((l) => l.cycle === solo.cycle);
    delete line.briefSpans;
    line.text = "The whole digest line, unsplit, because the payload is old.";
    const window = await loadSite("/", { journal: () => journal, digest });
    const card = cardFor(window, solo.cycle);
    assert.equal(card.querySelector(".entry-title"), null);
    assert.equal(card.querySelector(".entry-brief.is-unsplit").textContent,
      "The whole digest line, unsplit, because the payload is old.");
  });
});

describe("two entries for one cycle are one card", () => {
  let window;
  before(async () => {
    window = await loadSite();
  });

  test("the fixture really does hold two entries for cycle 57", () => {
    const fifty7 = payload.journal.entries.filter((e) => e.cycle === 57);
    assert.equal(fifty7.length, 2);
  });

  test("cycle 57 draws exactly one card", () => {
    const headings = cards(window).map((card) => card.querySelector("h2").textContent);
    assert.equal(headings.filter((h) => h === "Cycle 57").length, 1);
  });

  test("the one card carries the cycle's digest line, whole", () => {
    const line = payload.digest.lines.find((l) => l.cycle === 57);
    const card = cards(window)[0];
    assert.equal(card.querySelector(".entry-brief").textContent, lineBrief(line));
    assert.equal(card.querySelector(".entry-digest").textContent.trim(),
      line.restSpans.map((s) => s.text).join("").trim());
    assert.ok(lineBrief(line).length < lineText(line).length,
      "the fixture must actually split, or this test cannot fail");
  });

  test("both entries' prose is inside that one card's drawer", () => {
    const bodies = cards(window)[0].querySelectorAll(".entry-parts .entry-body");
    assert.equal(bodies.length, 2);
    const text = cards(window)[0].querySelector(".entry-parts").textContent;
    for (const entry of payload.journal.entries.filter((e) => e.cycle === 57)) {
      const opening = entry.blocks.find((b) => b.type === "p");
      assert.ok(text.includes(opening.spans.map((s) => s.text).join("")),
        "a part's own prose is missing from the card that replaced its card");
    }
  });

  test("the parts read oldest-first and are labelled and dated", () => {
    const parts = [...cards(window)[0].querySelectorAll(".entry-part-tab")]
      .map((h) => h.textContent);
    assert.equal(parts.length, 2);
    // The wire is newest-first, so entries[1] is the earlier of the two.
    const [addendum, run] = payload.journal.entries.filter((e) => e.cycle === 57);
    // No " Oslo" suffix any more (issues.md #59) -- the date and time
    // themselves still have to be the last thing on the subheading, which
    // is what stops "remove Oslo" being satisfied by removing the stamp.
    assert.ok(parts[0].endsWith(run.date + " " + run.time), parts[0]);
    assert.ok(parts[1].endsWith(addendum.date + " " + addendum.time), parts[1]);
    assert.ok(!/Oslo/.test(parts[0] + parts[1]), parts[0] + " | " + parts[1]);
    assert.ok(parts[1].startsWith("Verification"), parts[1]);
  });

  test("the button says how many entries are behind it", () => {
    assert.equal(cards(window)[0].querySelector(".journal-toggle").textContent,
      "Read the full journal (2 entries)");
    const single = cards(window).find(
      (card) => card.querySelectorAll(".entry-parts .entry-body").length === 1);
    assert.ok(single, "the fixture must also hold a one-entry cycle");
    assert.equal(single.querySelector(".journal-toggle").textContent, "Read the full journal");
  });

  test("the digest summary renders its bold instead of showing asterisks", () => {
    // The digest line was the only text on the page rendering its own
    // markup, and it is the line the owner called hard to read.
    const summary = cards(window)[0].querySelector(".entry-brief");
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

  test("no element id is used twice", () => {
    const ids = [...window.document.querySelectorAll("[id]")].map((n) => n.id);
    assert.equal(new Set(ids).size, ids.length, "duplicate id: " + ids.join(", "));
  });

  test("the cycle anchor exists exactly once for a cycle with two entries", () => {
    assert.equal(window.document.querySelectorAll("#cycle-57").length, 1);
  });

  /* The same two cases the page has, on the card, because one card per cycle
   * gives the card the same problem the page had: one meta row for two parts
   * that may not agree. The fixture's only two-entry cycle carries identical
   * fields on both entries, so a mutation from `settled` back to the earliest
   * part passes every other test in this file. */
  test("the card takes its PR from the last part that has one", async () => {
    // Cycle 102 on the live pod: the base entry carries no PR and no outcome,
    // the addendum carries `#86 / merged`. Reading the earliest part shows a
    // cycle that merged a PR as having done nothing.
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[1].pr = "";
    parts[1].prSpans = [];
    parts[1].outcome = "";
    parts[0].pr = "#86";
    parts[0].prSpans = [{ kind: "text", text: "#86" }];
    parts[0].outcome = "merged";
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta");
    // The PR, not the outcome: the card stopped drawing the outcome pill
    // (see "the feed card carries no outcome pill" below), so the PR badge
    // is what pins `settledPart` here now. It is the same field read off the
    // same part, so a mutation back to the earliest part still fails.
    assert.match(meta.textContent, /#86/);
    // The stamp still belongs to the earliest part: that is when it began.
    assert.match(meta.textContent, new RegExp(parts[1].time));
  });

  /* the owner's issues.md #59, the three small pickings on the journal card.
   * Each of these asserts an absence, so each one also proves the selector
   * matches something when the thing is present -- Cycle 202 shipped a
   * `.prio-picker` assertion against markup that never rendered that class
   * and it passed under its own mutation. Here the positive control is the
   * same fixture with the field put back. */
  test("no chevron on a feed card, and the selector would have found one", async () => {
    const w = await loadSite();
    const card = cards(w)[0];
    assert.equal(card.querySelector(".chevron"), null);
    // The control: `.chevron` is a selector jsdom resolves, so a span
    // carrying that class in this very card is found. Without this, the
    // assertion above passes for a misspelled class name too.
    const proof = w.document.createElement("span");
    proof.className = "chevron";
    card.appendChild(proof);
    assert.ok(card.querySelector(".chevron"));
    proof.remove();
  });

  test("the timestamp no longer says Oslo, but still says the time", async () => {
    const w = await loadSite();
    const stamp = cards(w)[0].querySelector(".stamp");
    assert.ok(stamp, "there is a stamp to make a claim about");
    assert.ok(!/Oslo/.test(stamp.textContent), stamp.textContent);
    // The half that stops "remove Oslo" from being satisfied by removing
    // the whole stamp: a real HH:MM has to survive.
    assert.match(stamp.textContent, /\d{4}-\d{2}-\d{2} \d{2}:\d{2}/);
  });

  test("a cycle's runtime shows when the server sent one, and not when it did not", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => { e.runtimeSeconds = 962; });
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(meta.textContent, /ran 16 min/);

    // Absent, not null: a cycle the join could not resolve gets no key at
    // all, and the card must print nothing rather than "ran 0 min".
    const quiet = JSON.parse(JSON.stringify(payload.journal));
    quiet.entries.forEach((e) => { delete e.runtimeSeconds; });
    const w2 = await loadSite("/", { journal: () => quiet });
    const meta2 = cards(w2)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.equal(meta2.querySelector(".runtime"), null);
    assert.ok(!/\bran\b/.test(meta2.textContent), meta2.textContent);
  });

  /* Cycle 105 on the live pod, and cycle 6: the footer is mandatory, so a
   * part with nothing of its own to report still files `PR: none | Outcome:
   * no-op`. Reading that as the cycle's answer announces a cycle that merged
   * a PR as a no-op. Both real; neither was in the fixture. */
  test("a later part that reports nothing does not overrule a merged PR", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[1].pr = "#89";                       // the earlier entry: the work
    parts[1].prSpans = [{ kind: "text", text: "#89" }];
    parts[1].outcome = "merged";
    parts[0].pr = "none";                      // the addendum: nothing to add
    parts[0].prSpans = [{ kind: "text", text: "none" }];
    parts[0].outcome = "no-op";
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(meta.textContent, /#89/);
    // The addendum's `none` is the thing that must not win. The card no
    // longer prints the outcome word, so this is the assertion carrying the
    // claim -- reading the earliest part would print `none` here.
    assert.ok(!/none/.test(meta.textContent), meta.textContent);
    // The page reads off the same function, so it must agree -- and it does
    // still print the outcome, which is where `merged` is checked.
    const page = await loadSite("/cycle/57", { journal: () => journal });
    const pageMeta = page.document.querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(pageMeta.textContent, /#89/);
    assert.match(pageMeta.textContent, /merged/);
    assert.ok(!/no-op/.test(pageMeta.textContent), pageMeta.textContent);
  });

  test("a qualified `none` is still a none", async () => {
    // Cycle 6's third entry writes `none (status note)`, so this cannot be
    // an equality test against the string "none".
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[1].pr = "#32";
    parts[1].prSpans = [{ kind: "text", text: "#32" }];
    parts[1].outcome = "shipped";
    parts[0].pr = "none (status note)";
    parts[0].prSpans = [{ kind: "text", text: "none (status note)" }];
    parts[0].outcome = "no-op";
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(meta.textContent, /#32/);
    assert.ok(!/status note/.test(meta.textContent), meta.textContent);
  });

  test("a part of the card that reached a different answer keeps its own row", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[1].outcome = "no-op";
    parts[1].pr = "none (status note)";
    parts[1].prSpans = [{ kind: "text", text: "none (status note)" }];
    parts[0].outcome = "merged";
    parts[0].pr = "#91";
    parts[0].prSpans = [{ kind: "text", text: "#91" }];
    const w = await loadSite("/", { journal: () => journal });
    const card = cards(w)[0];
    assert.match(card.querySelector(".entry-meta").textContent, /#91/);
    const own = card.querySelector(".entry-meta-part");
    assert.ok(own, "the disagreeing part must keep a row of its own");
    // Inside the drawer the outcome survives, which is the whole reason the
    // row exists -- the part's answer differs and nothing else says so.
    assert.match(own.textContent, /no-op/);
  });

  /* the owner, comments board 2026-08-23, on cycle 340's card: "What is this new
   * grey title? ... This is ugly and seems like information i do not need or
   * want", then "Sure. Cut it" to dropping the pill from the card. His card
   * had a free-text Outcome 84 characters long, uppercased by the stylesheet,
   * sitting where a one-word badge goes.
   *
   * Each half is a control for the other: the same fixture, the same
   * selector, present on `/cycle/<n>` and absent on the feed. Without the
   * page half this passes for a misspelled selector, which is how Cycle 202
   * shipped an assertion against markup that never rendered. */
  test("a free-text outcome draws no pill on the feed card, and /cycle/<n> still does", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts.forEach((e) => {
      e.outcome = "prompt.md wired to tools.backlog_brief; capture inbox cleared";
      e.outcomeDetail = "";
    });

    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.ok(!/backlog_brief/.test(meta.textContent), meta.textContent);
    assert.equal(meta.querySelector(".badge"), null);

    const page = await loadSite("/cycle/57", { journal: () => journal });
    const pageMeta = page.document.querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(pageMeta.textContent, /backlog_brief/);
    assert.ok(pageMeta.querySelector(".badge"), "the page keeps the pill");
  });

  /* The other half of the rule, and the one the owner asked for back on
   * 2026-08-24: "i miss the status fields. Please bring them back." A word
   * from the closed vocabulary is a badge, so it goes back on the card; the
   * clause above is what stays cut. Of 411 outcomes in the live journal, 404
   * are one of these seven words, so this is the case that decides whether
   * he sees a status at all when he scrolls the feed.
   *
   * The free-text test above is this one's control: same selector, same
   * fixture shape, opposite verdict, so neither can pass by accident. */
  test("a one-word outcome is back on the feed card", async () => {
    for (const [word, cls] of [["merged", "badge-good"], ["stuck", "badge-warn"], ["report", null]]) {
      const journal = JSON.parse(JSON.stringify(payload.journal));
      journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
        e.outcome = word;
        e.outcomeDetail = "";
      });
      const w = await loadSite("/", { journal: () => journal });
      const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
      const badge = meta.querySelector(".badge");
      assert.ok(badge, `no pill for ${word}: ${meta.textContent}`);
      assert.equal(badge.textContent, word);
      if (cls) assert.ok(badge.classList.contains(cls), `${word} should be ${cls}`);
    }
  });

  /* `Outcome: none` draws nothing, on the card or in the header. The
   * vocabulary held `none` for one cycle on the theory that it was a word
   * the footer offers; the archive has zero of them in 414 entries, and
   * `isRealPr` exists precisely to keep the word "none" off the header --
   * so admitting it as a badge would have restored #300's complaint through
   * the other field. The header half of this is in the status-fields suite
   * below, where `withStatus` is in scope. This is the test the narrowing
   * rests on: it fails the
   * moment `shortOutcome` gets permissive again, which the two "free text
   * draws nothing" tests beside it cannot see, because free text was
   * refused before this change as well. */
  test("an outcome of none is not a status word on the card", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.outcome = "none";
      e.outcomeDetail = "";
    });
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.equal(meta.querySelector(".badge"), null, meta.textContent);
    // The control: the same fixture with a real status word does draw one,
    // so this is not asserting against a selector that never matches.
    const journal2 = JSON.parse(JSON.stringify(payload.journal));
    journal2.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.outcome = "research";
      e.outcomeDetail = "";
    });
    const w2 = await loadSite("/", { journal: () => journal2 });
    const meta2 = cards(w2)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.equal(meta2.querySelector(".badge").textContent, "research");
  });

  /* The qualifier stays off the card even when the word beside it is back --
   * it is the prose half, and prose in a badge row is what #300 cut. */
  test("a one-word outcome brings back the word, not its qualifier", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.outcome = "stuck";
      e.outcomeDetail = "CI outage, merged nothing";
    });
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.ok(meta.querySelector(".badge"), "the word is drawn");
    assert.equal(meta.querySelector(".outcome-detail"), null);
  });

  /* Cycle 340's own card is `PR: none | Outcome: <a whole clause>`. Cutting
   * the pill and keeping the `none` leaves the card saying one dim word that
   * answers a question nothing else on it asks. On the page, where the pill
   * survives, `none` is still the object of a sentence -- so this is the
   * feed's rule, not a rule about the string. */
  test("a card whose cycle shipped no PR says nothing, but the page still says none", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.pr = "none";
      e.prSpans = [{ kind: "text", text: "none" }];
      e.outcome = "no-op";
      e.outcomeDetail = "";
    });
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.equal(meta.querySelector(".pr"), null);

    const page = await loadSite("/cycle/57", { journal: () => journal });
    const pageMeta = page.document.querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(pageMeta.textContent, /none/);
    assert.ok(pageMeta.querySelector(".pr"), "the page keeps the badge");
  });

  /* The control for the rule above: a real reference is still drawn on the
   * card. Without this, deleting the PR badge from the feed entirely would
   * satisfy the test above. */
  test("a card whose cycle shipped a real PR still names it", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.pr = "runner#300";
      e.prSpans = [{ kind: "text", text: "runner#300" }];
    });
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(meta.textContent, /runner#300/);
  });

  /* The qualifier is not a separate opinion, it is the tail of the pill's
   * sentence -- five entries read "stuck — CI outage, merged nothing". Cutting
   * the pill and leaving that behind puts a bare subordinate clause on the
   * card, which is the same complaint with fewer words. */
  test("the qualifier goes with the pill it qualifies", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.outcome = "stuck";
      e.outcomeDetail = "CI outage, merged nothing";
    });
    const w = await loadSite("/", { journal: () => journal });
    const meta = cards(w)[0].querySelector(".entry-meta:not(.entry-meta-part)");
    assert.equal(meta.querySelector(".outcome-detail"), null);

    const page = await loadSite("/cycle/57", { journal: () => journal });
    const pageMeta = page.document.querySelector(".entry-meta:not(.entry-meta-part)");
    assert.ok(pageMeta.querySelector(".outcome-detail"), "the page keeps it");
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

  /* This used to read off a feed card. The feed no longer draws the badge
   * for a `none` at all -- see "a card whose cycle shipped no PR says
   * nothing" -- so the claim moves to `/cycle/<n>`, which still draws it.
   * The claim itself is unchanged: a footer that names no reference must
   * not linkify the word standing in for one. */
  test("a footer with no reference in it makes no link", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => {
      e.pr = "none";
      e.prSpans = [{ kind: "text", text: "none" }];
    });
    const page = await loadSite("/cycle/57", { journal: () => journal });
    const pr = page.document.querySelector(".entry-meta .pr");
    assert.ok(pr, "the page draws the badge, so there is something to check");
    assert.equal(pr.textContent, "none");
    assert.equal(pr.querySelectorAll("a").length, 0);
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

describe("a journal card names the board item it worked on", () => {
  /* the owner, ideas.md #68: "Journal cards in Nova should mark the issue or
   * idea number they worked on like they do with the prs. With links."
   *
   * No live entry carries the field yet -- it starts from the next one
   * written, because backfilling 197 entries would be inventing a record
   * rather than keeping one -- so the payload here is the real one with the
   * field added to its first card. */
  const withBoard = (board, spans) => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries[0].board = board;
    journal.entries[0].boardSpans = spans;
    return journal;
  };

  test("the reference renders as a link into the app, not out to GitHub", async () => {
    const window = await loadSite("/", {
      journal: () => withBoard("idea #68", [
        { kind: "link", text: "idea #68", url: "/ideas#68" },
      ]),
    });
    const link = cards(window)[0].querySelector(".board .board-link");
    assert.equal(link.textContent, "idea #68");
    assert.equal(link.getAttribute("href"), "/ideas#68");
    // The half that would be wrong by default: `renderSpans` sends every
    // link to a new tab, which for one of our own pages means leaving the
    // PWA to arrive back inside it.
    assert.equal(link.getAttribute("target"), null);
  });

  test("the text around the references survives, and a card without the field shows nothing", async () => {
    const window = await loadSite("/", {
      journal: () => withBoard("issue #71 and idea #62", [
        { kind: "link", text: "issue #71", url: "/issues#71" },
        { kind: "text", text: " and " },
        { kind: "link", text: "idea #62", url: "/ideas#62" },
      ]),
    });
    assert.equal(cards(window)[0].querySelector(".board").textContent, "issue #71 and idea #62");
    assert.equal(cards(window)[1].querySelector(".board"), null);
  });

  test("a payload from an older build has no board and renders no badge", async () => {
    const window = await loadSite();
    assert.equal(window.document.querySelectorAll(".board").length, 0);
  });
});

describe("an outdated row leaves Open without claiming it shipped", () => {
  /* the owner, issues.md #85: "Some of them are implemented and some of them
   * are outdated. We need to clean it up. Maybe we need a new status called
   * 'outdated', so i can go through them and delete them myself." A cycle
   * writes the status; he deletes the row. So the two things that have to
   * hold are that it leaves Open — otherwise the pile he asked to shrink
   * does not shrink — and that it does not land in Done, which would read
   * as shipped. */
  const outdatedBoard = () => {
    const board = JSON.parse(JSON.stringify(payload.board));
    board.items[1].status = "⚫ Outdated";
    board.items[1].statusKey = "outdated";
    // `item=` is a tap on one row and answers with a different shape, so
    // it falls through to the fixture's own reply rather than the list.
    return (url) => (url.includes("item=") ? null : board);
  };
  const rows = (window) =>
    [...window.document.querySelectorAll(".item")].map((r) => r.id);
  const filterLabels = (window) =>
    [...window.document.querySelectorAll(".filter")].map((chip) => chip.textContent);

  test("Open drops it, and Done does not pick it up", async () => {
    const window = await loadSite("/issues", { board: outdatedBoard() });
    const number = payload.board.items[1].number;
    assert.ok(!rows(window).includes("item-" + number),
      "an outdated row was still listed under Open: " + rows(window).join(","));
    click(window, window.document.querySelector(".board-filter-btn"));
    const done = [...window.document.querySelectorAll(".filter")]
      .filter((chip) => chip.textContent.startsWith("Done"))[0];
    click(window, done);
    assert.ok(!rows(window).includes("item-" + number),
      "an outdated row was counted as shipped under Done");
  });

  test("its own filter is the list he goes through", async () => {
    const window = await loadSite("/issues", { board: outdatedBoard() });
    const number = payload.board.items[1].number;
    click(window, window.document.querySelector(".board-filter-btn"));
    const chip = [...window.document.querySelectorAll(".filter")]
      .filter((c) => c.textContent.startsWith("Outdated"))[0];
    assert.ok(chip, "there is no Outdated filter to go through: " + filterLabels(window).join(","));
    click(window, chip);
    assert.deepEqual(rows(window), ["item-" + number]);
  });

  test("the tally counts it as neither open nor done", async () => {
    const window = await loadSite("/issues", { board: outdatedBoard() });
    const line = window.document.querySelector(".status-line").textContent;
    /* Asserted as the whole string rather than as three separate regexes.
     * The first version checked that the line did not say `3 open`, and
     * neither the old tally nor the new one can ever produce that number
     * -- the old one said `2 open` because it derived `done` independently
     * -- so it was true in both states and pinned nothing. The fixture has
     * 4 rows, 2 already done, 1 turned outdated here, which leaves 1 open;
     * the old code said `2 open, 2 done` for the same payload. */
    assert.match(line, /1 open, 2 done, 1 outdated, /,
      "the tally does not split the three buckets: " + line);
  });

  test("it gets no rating picker, because the server would refuse one", async () => {
    /* `set_row_priority` refuses a closed row, and outdated is closed. A
     * picker drawn here would be a control whose only outcome is a
     * rejection — the same reason a done row has never had one. The
     * picker lives in the row's header now, not a body only an open row
     * renders (the owner, 2026-08-14: "the priority button should be the
     * priority tag instead"), so there is no need to open the row to
     * check for it -- and payload.board.items[1] is unrated in the
     * fixture, so a non-editable row shows no chip at all rather than a
     * read-only one. */
    const number = payload.board.items[1].number;
    const window = await loadSite("/issues#" + number, { board: outdatedBoard() });
    const row = window.document.getElementById("item-" + number);
    assert.ok(row, "the row the URL named was filtered off the page");
    assert.equal(row.querySelector(".item-meta-row > .chip.prio"), null,
      "an outdated row was offered a rating the server will not write");
    // The selector is the one the picker actually renders, not a guess:
    // an open row in the same payload must still have it, or this test
    // would pass against a picker that had simply been renamed.
    const openRow = window.document.getElementById(
      "item-" + payload.board.items[0].number);
    const trigger = openRow.querySelector(".item-meta-row > .chip.prio");
    assert.ok(trigger, "the selector matches nothing at all, so the assertion above is vacuous");
    assert.equal(trigger.tagName, "BUTTON", "an editable row's chip should be a clickable trigger");
  });
});

describe("a board link opens the row it names", () => {
  test("/issues#57 opens that row rather than only landing on the page", async () => {
    const window = await loadSite("/issues#57");
    const row = window.document.getElementById("item-57");
    assert.ok(row, "the row the URL named is not on the page");
    assert.equal(row.querySelector(".item-head").getAttribute("aria-expanded"), "true");
    assert.equal(row.querySelector(".item-body").hidden, false);
  });

  test("a done item still opens, because a URL beats the default filter", async () => {
    /* The failure this exists for: the board opens on `Open`, and an item a
     * journal entry worked on is usually already done -- so the link would
     * land on the right page showing everything except the thing it pointed
     * at, with nothing saying why. */
    const window = await loadSite("/issues#51");
    const row = window.document.getElementById("item-51");
    assert.ok(row, "a done item the URL named was filtered off the page");
    assert.equal(row.querySelector(".item-head").getAttribute("aria-expanded"), "true");
    click(window, window.document.querySelector(".board-filter-btn"));
    const on = [...window.document.querySelectorAll(".filter.on")].map((c) => c.textContent);
    assert.ok(on.some((label) => label.startsWith("All")), "the filter did not give way: " + on);
  });

  test("a number that is on no row changes nothing", async () => {
    const window = await loadSite("/issues#9999");
    assert.equal(window.document.querySelectorAll(".item-head[aria-expanded='true']").length, 0);
    click(window, window.document.querySelector(".board-filter-btn"));
    const on = [...window.document.querySelectorAll(".filter.on")].map((c) => c.textContent);
    assert.ok(on.some((label) => label.startsWith("Open")), "a stale number moved the filter");
  });

  test("no hash leaves every row closed", async () => {
    const window = await loadSite("/issues");
    assert.equal(window.document.querySelectorAll(".item-head[aria-expanded='true']").length, 0);
  });

  test("tapping a filter afterwards does not drag the row back open", async () => {
    /* The hash is consumed once per navigation, not on every render. Read
     * on each render, closing the row and pressing a chip would reopen it,
     * which is a page that argues with the person using it. */
    const window = await loadSite("/issues#57");
    click(window, window.document.getElementById("item-57").querySelector(".item-head"));
    assert.equal(
      window.document.getElementById("item-57").querySelector(".item-head")
        .getAttribute("aria-expanded"),
      "false",
    );
    click(window, window.document.querySelector(".board-filter-btn"));
    const all = [...window.document.querySelectorAll(".filter")]
      .filter((chip) => chip.textContent.startsWith("All"))[0];
    click(window, all);
    assert.equal(
      window.document.getElementById("item-57").querySelector(".item-head")
        .getAttribute("aria-expanded"),
      "false",
      "the hash reopened a row the reader had closed",
    );
  });
});

describe("an ask lives on the card that raised it", () => {
  /* the owner, comments board 2026-08-16: "the solution i want is to remove the
   * 'needs the owner' block entirely. If you need something from me, it should
   * be added in the Journal card somehow and i'll answer in the comment of a
   * journal card. [...] add a new yellow block below the title or somehow
   * higlight your issue so that i see it." */

  /** A journal payload whose newest entry carries an ask. */
  function asking(ask) {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const entry = journal.entries[0];
    entry.ask = ask;
    entry.askSpans = [{ kind: "text", text: ask }];
    return journal;
  }

  test("the separate block is gone from the page entirely", async () => {
    const window = await loadSite();
    assert.equal(window.document.getElementById("needs"), null);
    assert.equal(window.document.querySelector(".needs-done"), null);
  });

  test("an ask renders as its own block on its own card", async () => {
    const window = await loadSite("/", { journal: () => asking("Decide about the node.") });
    const ask = window.document.querySelector(".entry-ask");
    assert.ok(ask, "the card should carry the ask");
    assert.match(ask.textContent, /Needs input/);
    assert.match(ask.textContent, /Decide about the node/);
  });

  test("the ask sits above the brief, which is where he asked for it", async () => {
    const window = await loadSite("/", { journal: () => asking("Decide about the node.") });
    const card = window.document.querySelector(".entry-ask").closest(".entry");
    const kids = Array.from(card.children);
    const askAt = kids.findIndex((n) => n.classList.contains("entry-ask"));
    const briefAt = kids.findIndex((n) => n.classList.contains("entry-brief"));
    assert.ok(askAt > -1 && briefAt > -1, "both should be on the card");
    assert.ok(askAt < briefAt, "the ask goes below the title and above the brief");
  });

  test("a card with an ask opens its own comment drawer", async () => {
    /* The answer box is the whole point -- idea #56 sat unanswered for eight
     * cycles because the block asked a question and gave him nowhere to type.
     * A card's drawer is shut by default, so an ask that did not open it
     * would reintroduce exactly that. */
    const window = await loadSite("/", { journal: () => asking("Decide about the node.") });
    const card = window.document.querySelector(".entry-ask").closest(".entry");
    assert.ok(card.classList.contains("is-commenting"));
  });

  test("a two-part cycle shows both of its asks, not just the first", async () => {
    /* Reviewer finding, Cycle 247. The server cuts each part's ask out of
     * that part's own prose, so an ask the card declines to render is gone
     * from the page with no trace. */
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const cycle = journal.entries[0].cycle;
    const twin = JSON.parse(JSON.stringify(journal.entries[0]));
    twin.ask = "second ask";
    twin.askSpans = [{ kind: "text", text: "second ask" }];
    journal.entries[0].ask = "first ask";
    journal.entries[0].askSpans = [{ kind: "text", text: "first ask" }];
    twin.cycle = cycle;
    journal.entries.splice(1, 0, twin);
    const window = await loadSite("/", { journal: () => journal });
    const ask = window.document.querySelector(".entry-ask");
    assert.match(ask.textContent, /first ask/);
    assert.match(ask.textContent, /second ask/);
    assert.equal(ask.querySelectorAll(".entry-ask-label").length, 1);
  });

  /* the owner, ideas.md 2026-08-16 22:14: "When my reply answers the yellow
   * 'needs the owner' block on an entry, minimize it instead of leaving it
   * full-size -- and let the owner minimize it himself too. Don't delete it,
   * just collapse it." */

  /** The cycle the fixture's newest entry belongs to. */
  const newestCycle = payload.journal.entries[0].cycle;

  /* The fixture's newest entry is cycle 57, and cycle 57 already carries two
   * comments -- so "an answered ask starts minimized" passes against the
   * plain fixture whether or not the code reads the comments at all. Both
   * halves therefore build their own comments payload: `silent` is the one
   * that makes the open case capable of failing. */
  /** A comments payload where he has replied on the newest entry's card. */
  function repliedOnNewest() {
    const copy = JSON.parse(JSON.stringify(payload.comments));
    copy.byCycle[String(newestCycle)] = [
      { stamp: "2026-08-16 22:14", text: "Do it.", acknowledged: false },
    ];
    return copy;
  }

  /** The same, with nothing said on the newest entry's card. */
  function silentOnNewest() {
    const copy = JSON.parse(JSON.stringify(payload.comments));
    delete copy.byCycle[String(newestCycle)];
    return copy;
  }

  test("an unanswered ask is open, with a control to minimize it", async () => {
    const window = await loadSite("/", {
      journal: () => asking("Decide about the node."),
      comments: silentOnNewest(),
    });
    const ask = window.document.querySelector(".entry-ask");
    const toggle = ask.querySelector(".entry-ask-toggle");
    assert.ok(toggle, "the ask should carry its own control");
    assert.equal(toggle.getAttribute("aria-expanded"), "true");
    assert.equal(ask.querySelector(".entry-ask-bodies").hidden, false);
    assert.equal(ask.classList.contains("is-collapsed"), false);
  });

  test("an ask on a card he has replied to starts minimized", async () => {
    const window = await loadSite("/", {
      journal: () => asking("Decide about the node."),
      comments: repliedOnNewest(),
    });
    const ask = window.document.querySelector(".entry-ask");
    assert.equal(ask.querySelector(".entry-ask-bodies").hidden, true);
    assert.equal(ask.querySelector(".entry-ask-toggle").getAttribute("aria-expanded"), "false");
    assert.ok(ask.classList.contains("is-collapsed"));
  });

  test("minimized still says an ask is there, rather than deleting it", async () => {
    /* "It should not be deleted, but be minimised." The label is what makes
     * a collapsed ask findable at all -- hide the row and the card looks
     * like every card with nothing to answer.
     *
     * Reviewer finding, Cycle 249. This test used to assert `ask.hidden ===
     * false` and that the label read "Needs the owner", which nothing in the
     * feature can move: `setAskOpen` never touches `ask.hidden`, and the
     * label was rendered unconditionally before the change too. It passed
     * with the whole feature reverted. What it has to assert is the
     * *contrast* -- the prose is gone and the row is not -- because that is
     * the only thing that distinguishes minimised from deleted. */
    const window = await loadSite("/", {
      journal: () => asking("Decide about the node."),
      comments: repliedOnNewest(),
    });
    const ask = window.document.querySelector(".entry-ask");
    assert.ok(ask.classList.contains("is-collapsed"), "this case should be collapsed at all");
    assert.equal(ask.querySelector(".entry-ask-bodies").hidden, true, "the prose should be folded");
    assert.equal(ask.querySelector(".entry-ask-label").hidden, false);
    assert.equal(ask.querySelector(".entry-ask-toggle").hidden, false);
    assert.match(ask.textContent, /Needs input/, "the row still names itself");
    assert.doesNotMatch(
      ask.querySelector(".entry-ask-head").textContent,
      /Decide about the node/,
      "the question itself should not be in the row that stays",
    );
  });

  test("he can minimize an unanswered ask himself, and open it again", async () => {
    const window = await loadSite("/", {
      journal: () => asking("Decide about the node."),
      comments: silentOnNewest(),
    });
    const ask = window.document.querySelector(".entry-ask");
    const toggle = ask.querySelector(".entry-ask-toggle");
    toggle.click();
    assert.equal(ask.querySelector(".entry-ask-bodies").hidden, true, "his tap should fold it");
    toggle.click();
    assert.equal(ask.querySelector(".entry-ask-bodies").hidden, false, "and unfold it again");
  });

  test("minimizing the ask does not open or close the card behind it", async () => {
    /* The card toggles on any tap that is not claimed by a control, so a
     * missing branch in that one listener would expand the whole cycle
     * every time he folded its ask. */
    const window = await loadSite("/", {
      journal: () => asking("Decide about the node."),
      comments: silentOnNewest(),
    });
    const card = window.document.querySelector(".entry-ask").closest(".entry");
    const before = card.classList.contains("is-expanded");
    card.querySelector(".entry-ask-toggle").click();
    assert.equal(card.classList.contains("is-expanded"), before);
  });

  test("a poll does not re-collapse an ask he has opened", async () => {
    /* The failure a plain boolean would give: he opens an answered ask, a
     * background poll rebuilds the feed, and the guess overrules his tap.
     * A real poll, not a hand call of the renderer -- the version has to
     * move or the page correctly leaves itself alone and this proves
     * nothing. */
    let timers;
    const comments = repliedOnNewest();
    const window = await loadSite("/", {
      journal: () => asking("Decide about the node."),
      comments,
      install: (win) => { timers = captureTimers(win); },
    });
    window.document.querySelector(".entry-ask-toggle").click();
    assert.equal(window.document.querySelector(".entry-ask-bodies").hidden, false);

    const card = window.document.querySelector(".entry-ask").closest(".entry");
    const grown = asking("Decide about the node.");
    grown.version = 'W/"ask-poll"';
    window.fetch = (url) =>
      res(
        String(url).includes("/api/digest") ? payload.digest
          : String(url).includes("/api/comments") ? comments
            : grown,
      );
    await timers.firePagePoll();
    assert.ok(!window.document.contains(card), "the old card is still here, so the feed was not actually rebuilt and this proves nothing");
    assert.equal(
      window.document.querySelector(".entry-ask-bodies").hidden,
      false,
      "his choice should survive the re-render",
    );
  });

  test("the ask's minimize control meets the 44px touch minimum", () => {
    /* Reviewer finding, Cycle 249, and it was reading my own comment back at
     * me: I wrote "44px of tap height comes from the padding" above a rule
     * whose padding and font size compute to about 29px. Fifteen rules in
     * this file set the floor explicitly. Pinned the way the capture box's
     * picker is pinned, because a comment claiming a number is exactly what
     * failed here. */
    const css = readFileSync(join(publicDir, "style.css"), "utf8");
    const { window } = openWindow("<style>" + css + "</style>");
    const rules = [...window.document.styleSheets[0].cssRules];
    const sized = rules.find(
      (r) => r.selectorText === ".entry-ask-toggle" && /min-height/.test(r.style.cssText),
    );
    assert.ok(sized, "no .entry-ask-toggle rule sets a min-height");
    assert.match(sized.style.cssText, /min-height:\s*44px/);
  });

  test("a card with no ask keeps its drawer shut", async () => {
    const window = await loadSite();
    const card = window.document.querySelector(".entry");
    assert.equal(card.querySelector(".entry-ask"), null);
    assert.equal(card.classList.contains("is-commenting"), false);
  });
});

/* the owner, in issue #59: "its not the link thats the problem, its the single
 * view that is bad ui... does not make sense, is hard to understand and
 * wasteful", and on the comments board: "If a double entry is necessary like
 * for cycle 81, have it be combined into one card".
 *
 * Cycle 57 is the fixture's two-entry cycle, so every one of these is the
 * double-card case as well as the single-view one. */
describe("a deep-linked cycle is one page, not a feed card", () => {
  test("a cycle with two entries draws one article, not two", async () => {
    const window = await loadSite("/cycle/57");
    assert.equal(payload.journal.entries.filter((e) => e.cycle === 57).length, 2,
      "the fixture must contain a cycle with an addendum");
    assert.equal(cards(window).length, 1);
  });

  test("the heading appears once and is the page's own h1", async () => {
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    assert.equal(page.querySelectorAll(".entry-head h2").length, 1);
    assert.equal(page.querySelector(".entry-head h2").textContent, "Cycle 57");
  });

  test("the journal text is readable without pressing anything", async () => {
    /* The bug this pins, and the reason the stylesheet is inlined for it.
     *
     * The old page reused the feed card, and `setExpanded` re-derived the
     * journal drawer from the drawer's own `aria-expanded` -- `false` on a
     * card built one line earlier -- so the entry you named in the URL
     * arrived shut behind "Read the full journal". Whether it is shut is a
     * CSS fact (`.entry .entry-body { display: none }`), and jsdom loads no
     * external stylesheet, so every previous test in this file asserting a
     * `display` would have read a div's default `block` and passed either
     * way. `withStyle` puts the real style.css in the document, which makes
     * this the one rule in that file with a test behind it. */
    const window = await loadSite("/cycle/57", { install: withStyle });
    const page = cards(window)[0];
    const bodies = [...page.querySelectorAll(".entry-body")];
    assert.equal(bodies.length, 2, "one body per part of the cycle");
    assert.deepEqual(bodies.map((b) => window.getComputedStyle(b).display),
      ["block", "block"]);
  });

  test("the same stylesheet still hides a feed card's journal until asked", async () => {
    /* The other half of the mutation: `withStyle` has to be able to see a
     * hidden drawer, or the test above proves nothing about the CSS.
     *
     * The drawer is `.entry-parts` rather than `.entry-body`, because a
     * multi-part card holds several bodies and one of them cannot be what
     * opens and shuts. jsdom's `getComputedStyle` does not walk ancestors,
     * so this has to name the element the rule actually hides. */
    const window = await loadSite("/", { install: withStyle });
    const drawer = cards(window)[0].querySelector(".entry-parts");
    assert.equal(window.getComputedStyle(drawer).display, "none");
  });

  // `withStyle` because the last assertion is a `display`, and without the
  // real stylesheet jsdom answers `block` for a div whether the collapse
  // rules exist or not -- the assertion would have named a behaviour it
  // could not see. The reviewer caught that; it was true as written.
  test("nothing on the page collapses it, and there is no journal toggle", async () => {
    const window = await loadSite("/cycle/57", { install: withStyle });
    const page = cards(window)[0];
    assert.equal(page.querySelector(".journal-toggle"), null);
    // The `.chevron` assertion that used to sit here was deleted with the
    // element (issues.md #59). It said "the page has no chevron" at a
    // point where the feed still did, which made it a real contrast;
    // once nothing renders one it passes whatever the page does, and a
    // test that cannot fail is worse than no test. The feed-side check
    // below is where the removal is actually pinned.
    assert.equal(page.querySelector(".entry-toggle"), null);
    const body = page.querySelector(".entry-body");
    body.dispatchEvent(new window.Event("click", { bubbles: true }));
    assert.equal(window.getComputedStyle(body).display, "block");
  });

  test("no permalink pointing at the page you are already on", async () => {
    const window = await loadSite("/cycle/57");
    assert.equal(cards(window)[0].querySelector(".entry-permalink"), null);
  });

  test("the digest line and the meta row are drawn once, not per entry", async () => {
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    const brief = lineBrief(payload.digest.lines.find((l) => l.cycle === 57));
    const briefs = [...page.querySelectorAll(".entry-brief")].map((b) => b.textContent);
    assert.deepEqual(briefs, [brief]);
    // `:not(.entry-meta-part)` because a disagreeing part's own row shares
    // the `entry-meta` class for its styling; the fixture's parts agree, so
    // this is one either way and the qualifier is what makes it say so.
    assert.equal(page.querySelectorAll(".entry-meta:not(.entry-meta-part)").length, 1);
  });

  /* Cycle 102 on the live pod: the base entry carries no PR and no outcome,
   * the addendum carries `#86 / merged`. Taking all four fields from the
   * earliest part -- which is what the first version of this page did --
   * renders a cycle that merged a PR as having done nothing. The fixture's
   * only two-entry cycle has identical fields on both entries, so this
   * shape has to be built by hand or it is never exercised. */
  test("the header takes its outcome from the last part that has one", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    // Newest-first on the wire: [0] is the addendum, [1] the base entry.
    parts[1].pr = "";
    parts[1].prSpans = [];
    parts[1].outcome = "";
    parts[0].pr = "#86";
    parts[0].prSpans = [{ kind: "text", text: "#86" }];
    parts[0].outcome = "merged";
    const window = await loadSite("/cycle/57", { journal: () => journal });
    const meta = cards(window)[0].querySelector(".entry-meta");
    assert.match(meta.textContent, /#86/);
    assert.match(meta.textContent, /merged/);
    // The stamp still belongs to the earliest part: that is when it began.
    assert.match(meta.textContent, new RegExp(parts[1].time));
  });

  test("a part that reached a different answer keeps its own row", async () => {
    /* Cycle 6 wrote three entries with three different PR/outcome pairs. A
     * single header can only be one of them, so the others must survive
     * somewhere or drawing the header once silently loses them. */
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[1].outcome = "no-op";
    parts[1].pr = "none (status note)";
    parts[1].prSpans = [{ kind: "text", text: "none (status note)" }];
    parts[0].outcome = "merged";
    const window = await loadSite("/cycle/57", { journal: () => journal });
    const page = cards(window)[0];
    assert.match(page.querySelector(".entry-meta").textContent, /merged/);
    const own = page.querySelector(".entry-meta-part");
    assert.ok(own, "the disagreeing part must keep a row of its own");
    assert.match(own.textContent, /no-op/);
    assert.match(own.textContent, /none \(status note\)/);
  });

  test("parts that agree with the header stay silent", async () => {
    // The fixture's two entries carry identical pr/outcome, which is the
    // common shape and the one that must not produce a second row.
    const window = await loadSite("/cycle/57");
    assert.equal(cards(window)[0].querySelectorAll(".entry-meta-part").length, 0);
  });

  test("a long prose part label is not uppercased", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[0].title = "I watched it land, and caught myself re-filing a bug Edvard had already reported";
    const window = await loadSite("/cycle/57", { journal: () => journal, install: withStyle });
    const heading = [...cards(window)[0].querySelectorAll(".entry-part-tab")][1];
    /* jsdom reports an unset property as "", not as its initial value, so
     * this asserts the property is not uppercase rather than that it equals
     * "none" -- the mutation this must catch is someone adding uppercase
     * back, and both "" and "none" are correct answers to that. */
    assert.notEqual(window.getComputedStyle(heading).textTransform, "uppercase");
  });

  test("the parts read oldest first and each says when it was written", async () => {
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    const both = payload.journal.entries.filter((e) => e.cycle === 57);
    const headings = [...page.querySelectorAll(".entry-part-tab")].map((h) => h.textContent);
    assert.equal(headings.length, 2, "two parts, two subheadings");
    // `both` is newest-first off the wire; the page reverses it.
    assert.ok(headings[0].includes(both.at(-1).time), headings[0]);
    assert.ok(headings[1].includes(both[0].time), headings[1]);
  });

  /* The real titles, read off the live pod at 10:13 Oslo on 2026-08-11 --
   * every multi-entry cycle in the corpus. The fixture's are tidier than
   * these, which is exactly the fixture-simpler-than-reality trap, so the
   * messy ones are pinned here against the shipped renderer rather than
   * against a copy of the rule. */
  test("a part's heading survives the fourteen shapes real cycles have used", async () => {
    const window = await loadSite("/cycle/57");
    const both = payload.journal.entries.filter((e) => e.cycle === 57);
    const real = [
      ["(addendum)", "Addendum"],
      ["addendum", "Addendum"],
      ["verification", "Verification"],
      ["postscript", "Postscript"],
      ["· addendum (2026-08-11 05:24)", "Addendum"],
      // These two strip to nothing, so they take the positional fallback --
      // "Addendum" here because this loop writes the *later* part. The
      // first-position half of that rule is the test below.
      ["(2026-08-11 05:09)", "Addendum"],
      ["", "Addendum"],
      ["The question box had no answer field", "The question box had no answer field"],
    ];
    for (const [title, want] of real) {
      const journal = JSON.parse(JSON.stringify(payload.journal));
      const parts = journal.entries.filter((e) => e.cycle === 57);
      // Newest-first on the wire, so index 0 is the *later* part. Titles
      // that fall back are checked in both positions below.
      parts[0].title = title;
      const w = await loadSite("/cycle/57", { journal: () => journal });
      const heading = w.document.querySelectorAll(".entry-part-tab")[1].textContent;
      assert.ok(heading.startsWith(want + " · "),
        `title ${JSON.stringify(title)} rendered ${JSON.stringify(heading)}, wanted ${want}`);
      assert.ok(!/\(\d{4}-\d{2}-\d{2}/.test(heading),
        `heading printed a date twice: ${heading}`);
    }
    assert.equal(both.length, 2);
  });

  test("the first part of an untitled cycle is not called an addendum", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    journal.entries.filter((e) => e.cycle === 57).forEach((e) => { e.title = ""; });
    const window = await loadSite("/cycle/57", { journal: () => journal });
    const headings = [...window.document.querySelectorAll(".entry-part-tab")].map((h) => h.textContent);
    assert.ok(headings[0].startsWith("The cycle · "), headings[0]);
    assert.ok(headings[1].startsWith("Addendum · "), headings[1]);
  });

  /* Tabs, which is what the owner asked for three times (issues.md #59, and
   * the comments board at cycle 81) and did not get twice. The tests below
   * pin the behaviour that makes a tab acceptable rather than the fact that
   * one exists: exactly one part visible, all of them still in the DOM, the
   * cycle's settled answer never inside a panel, and a keyboard that can
   * reach the half that is not on top. */
  test("only the selected part is showing, and the other is still in the DOM", async () => {
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    const panels = [...page.querySelectorAll(".entry-part-panel")];
    assert.equal(panels.length, 2, "two parts, two panels");
    assert.equal(panels.filter((p) => !p.hidden).length, 1, "exactly one panel is showing");
    assert.equal(panels[0].hidden, false, "the first part is the one open");
    /* Hidden, not absent. Find-in-page and a copy-all have to reach the
     * half that is not on top -- that is the whole reason two earlier
     * cycles argued against tabs, and it is only true if the prose stays
     * rendered. */
    const later = payload.journal.entries.filter((e) => e.cycle === 57)[0];
    assert.ok(panels[1].textContent.length > 0, "the shut panel still holds its prose");
    assert.ok(later.blocks.length, "the fixture's later part must have prose to hide");
  });

  test("tapping the second tab swaps which part is showing", async () => {
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    const tabs = [...page.querySelectorAll(".entry-part-tab")];
    const panels = [...page.querySelectorAll(".entry-part-panel")];
    click(window, tabs[1]);
    assert.equal(panels[0].hidden, true);
    assert.equal(panels[1].hidden, false);
    assert.equal(tabs[1].getAttribute("aria-selected"), "true");
    assert.equal(tabs[0].getAttribute("aria-selected"), "false");
    // Back again, because a tab that only goes one way is a disclosure.
    click(window, tabs[0]);
    assert.equal(panels[0].hidden, false);
    assert.equal(panels[1].hidden, true);
  });

  test("the cycle's settled outcome sits outside the tabs", async () => {
    /* This is the objection the two previous cycles raised against tabs --
     * that a tab hides the addendum, which is usually where the cycle says
     * how it actually ended. It is answered by where the outcome is drawn,
     * not by argument, so it needs a test: the meta row is a sibling of the
     * tab strip, so it is readable whichever tab is open. */
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    /* Assert the panels exist first. Without this the test passes with the
     * tabs deleted entirely -- `closest(".entry-part-panel")` on a class
     * nothing renders is null, which is the same answer as "correctly
     * outside them". The reviewer caught that; it was vacuous as written. */
    assert.ok(page.querySelectorAll(".entry-part-panel").length > 1,
      "this cycle must actually be drawn with tab panels");
    const outcome = page.querySelector(".entry-meta .badge");
    assert.ok(outcome, "the cycle draws a settled outcome");
    assert.equal(outcome.closest(".entry-part-panel"), null,
      "the settled outcome must not be inside a tab panel");
  });

  test("arrow keys move between the parts, and only one tab is a tab stop", async () => {
    const window = await loadSite("/cycle/57");
    const page = cards(window)[0];
    const tabs = [...page.querySelectorAll(".entry-part-tab")];
    assert.deepEqual(tabs.map((t) => t.tabIndex), [0, -1], "roving tabindex");
    tabs[0].dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
    );
    assert.equal(tabs[1].getAttribute("aria-selected"), "true");
    assert.deepEqual(tabs.map((t) => t.tabIndex), [-1, 0]);
    // Wraps, so End/Home are not the only way back.
    tabs[1].dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true }),
    );
    assert.equal(tabs[0].getAttribute("aria-selected"), "true");
    /* ArrowLeft, Home and End were all implemented and none were pressed --
     * with two tabs, ArrowRight and ArrowLeft reach the same target from
     * either side, so a swapped comparison would have shipped silently. */
    tabs[0].dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true }),
    );
    assert.equal(tabs[1].getAttribute("aria-selected"), "true", "ArrowLeft wraps backwards");
    tabs[1].dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Home", bubbles: true, cancelable: true }),
    );
    assert.equal(tabs[0].getAttribute("aria-selected"), "true", "Home goes to the first part");
    tabs[0].dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "End", bubbles: true, cancelable: true }),
    );
    assert.equal(tabs[1].getAttribute("aria-selected"), "true", "End goes to the last part");
  });


  test("a single-part cycle gets no tab strip", async () => {
    const solo = payload.journal.entries.find(
      (e) => e.cycle !== null && payload.journal.entries.filter((o) => o.cycle === e.cycle).length === 1
    );
    assert.ok(solo, "the fixture must contain a cycle with exactly one entry");
    const window = await loadSite("/cycle/" + solo.cycle);
    const page = cards(window)[0];
    assert.equal(page.querySelectorAll("[role='tablist']").length, 0);
    assert.equal(page.querySelectorAll(".entry-part-panel").length, 0);
    // ...and its prose is still on the page, which is the thing the strip
    // must not have taken with it.
    assert.ok(page.querySelector(".entry-body").textContent.trim().length > 0);
  });

  test("one comment bubble for the cycle, not one per entry", async () => {
    const window = await loadSite("/cycle/57");
    assert.equal(cards(window)[0].querySelectorAll(".comment-toggle").length, 1);
  });

  /* The page sets its own className wholesale, so it could easily drop the
   * class the drawer's CSS keys on. Asserted through `getComputedStyle`
   * against the real stylesheet rather than by looking for the class, which
   * is the difference between "the attribute is set" and "he can see it". */
  test("the comment drawer really opens on the page", async () => {
    const window = await loadSite("/cycle/57", { install: withStyle });
    const page = cards(window)[0];
    const drawer = page.querySelector(".comment-drawer");
    assert.equal(window.getComputedStyle(drawer).display, "none");
    click(window, page.querySelector(".comment-toggle"));
    assert.equal(window.getComputedStyle(drawer).display, "block");
    click(window, page.querySelector(".comment-toggle"));
    assert.equal(window.getComputedStyle(drawer).display, "none");
  });

  /* 26 single-entry cycles carry a real title. The first version of this
   * page rendered titles only as part subheadings, and a single part gets
   * none, so all 26 lost theirs -- invisible in the fixture, which is why
   * this asserts on a literal rather than on "a title element exists".
   *
   * This is now the only case that draws one: no digest line *and* no
   * brief of the entry's own, so the title is the card's only label. */
  test("a one-part cycle's title is shown when the card has no brief at all", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const solo = journal.entries.find(
      (e) => e.cycle !== null
        && journal.entries.filter((o) => o.cycle === e.cycle).length === 1
    );
    solo.title = "The heartbeat was never late; the clock on the card was invented";
    solo.briefSpans = [];
    const digest = JSON.parse(JSON.stringify(payload.digest));
    digest.lines = digest.lines.filter((l) => l.cycle !== solo.cycle);
    const window = await loadSite("/cycle/" + solo.cycle, { journal: () => journal, digest });
    assert.equal(cards(window)[0].querySelector(".entry-title").textContent, solo.title);
  });

  /* the owner, comments board 2026-08-22, on a screenshot of cycle 329's card:
   * "Sometimes there are two titles and they repeat eachoter with different
   * words. See image. I like the one with the colored backline", then "The
   * one line summary can be cut."
   *
   * The card he photographed had no digest line, so #86's rule did not
   * apply and the heading title was drawn above the entry's own brief --
   * which opens by restating the heading. The coloured backline is
   * `.entry-brief`, so that is the one that stays. */
  test("a cycle briefed from its own prose shows that and not its heading title", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const solo = journal.entries.find(
      (e) => e.cycle !== null
        && journal.entries.filter((o) => o.cycle === e.cycle).length === 1
        && (e.briefSpans || []).length
    );
    assert.ok(solo, "the fixture must contain a solo cycle briefed from its own prose");
    solo.title = "A heading saying the same thing the entry's first sentence says";
    // Written in, not read back out of the fixture: an assertion computed
    // from `solo.briefSpans` compares the render to its own input, so a
    // mutation moves both sides together. Two spans of different kinds
    // because every brief in the fixture is a single plain-text span, and
    // real ones carry `strong` and `code`.
    solo.briefSpans = [
      { kind: "text", text: "The brief the entry wrote for itself, in " },
      { kind: "code", text: "app.js" },
    ];
    const digest = JSON.parse(JSON.stringify(payload.digest));
    digest.lines = digest.lines.filter((l) => l.cycle !== solo.cycle);
    const window = await loadSite("/cycle/" + solo.cycle, { journal: () => journal, digest });
    const card = cards(window)[0];
    assert.equal(card.querySelector(".entry-title"), null);
    // Not vacuous: the brief the title was competing with is still drawn,
    // so this cannot pass by the card having lost both labels.
    assert.equal(card.querySelector(".entry-brief").textContent.trim(),
      "The brief the entry wrote for itself, in app.js");
  });

  /* the owner, issues #86: "Journal cards like cycle 209 seems to have two
   * titles. Only one is enough." The digest line is the one he reads. */
  test("a cycle with a digest line shows that and not its heading title too", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const solo = journal.entries.find(
      (e) => e.cycle !== null
        && journal.entries.filter((o) => o.cycle === e.cycle).length === 1
        && payload.digest.lines.some((l) => l.cycle === e.cycle)
    );
    assert.ok(solo, "the fixture must contain a solo cycle that has a digest line");
    solo.title = "A second title saying the same thing the digest line says";
    const window = await loadSite("/cycle/" + solo.cycle, { journal: () => journal });
    const card = cards(window)[0];
    assert.equal(card.querySelector(".entry-title"), null);
    // Not vacuous: the brief the title was competing with is still drawn, so
    // this cannot pass by the card having lost both.
    assert.equal(
      card.querySelector(".entry-brief").textContent.trim(),
      lineBrief(payload.digest.lines.find((l) => l.cycle === solo.cycle))
    );
  });

  test("a title that is only its own timestamp renders as nothing", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const solo = journal.entries.find(
      (e) => e.cycle !== null && journal.entries.filter((o) => o.cycle === e.cycle).length === 1
    );
    solo.title = "(2026-08-11 02:00)";
    const window = await loadSite("/cycle/" + solo.cycle, { journal: () => journal });
    assert.equal(cards(window)[0].querySelector(".entry-title"), null);
  });

  test("a single-entry cycle gets no part subheadings at all", async () => {
    const solo = payload.journal.entries.find(
      (e) => e.cycle !== null && payload.journal.entries.filter((o) => o.cycle === e.cycle).length === 1
    );
    assert.ok(solo, "the fixture must contain a cycle with exactly one entry");
    const window = await loadSite("/cycle/" + solo.cycle);
    assert.equal(cards(window)[0].querySelectorAll(".entry-part-tab").length, 0);
  });

  test("the feed draws the same cycle as one card, on the same rules", async () => {
    const window = await loadSite("/");
    const own = cards(window).filter((c) => c.querySelector("h2").textContent === "Cycle 57");
    assert.equal(own.length, 1);
    // Same subheadings, same order, same source function as the page above.
    const page = await loadSite("/cycle/57");
    assert.deepEqual(
      [...own[0].querySelectorAll(".entry-part-tab")].map((h) => h.textContent),
      [...cards(page)[0].querySelectorAll(".entry-part-tab")].map((h) => h.textContent)
    );
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
    const { window } = openWindow(html, { url: "https://nova.example/", runScripts: "outside-only" });
    window.fetch = (url) =>
      res(url.includes("/api/digest") ? payload.digest : hostile);
    window.eval(readFileSync(join(publicDir, "app.js"), "utf8"));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    assert.equal(window.pwned, undefined);
    assert.equal(window.document.querySelectorAll("script:not([src])").length, 0);
    assert.equal(window.document.querySelectorAll("img").length, 0);
    assert.match(cards(window)[0].textContent, /<script>/);
  });
});

/* the owner, issues.md 2026-08-09: "when a journey card is opened, the Digest is
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

  test("tapping a part tab does not collapse the card it is inside", async () => {
    /* The whole card is a tap target -- "i want to click anywhere on it to
     * expand/close it" -- so a tab's click bubbles up to that listener and
     * shuts the drawer the tab lives in. The part you asked for appears and
     * vanishes in the same frame.
     *
     * Every assertion about which panel is `hidden` passes while this is
     * broken, because the panel is right up until the card closes over it.
     * A real browser found this; jsdom agreed with me. So this test asserts
     * the card is still open, which is the thing that was actually wrong. */
    /* Its own window: this test has to leave a card expanded to press a tab
     * inside it, and the rest of this suite shares one window and asserts on
     * a collapsed one. */
    const own = await loadSite("/");
    const card = cards(own).find((c) => c.querySelectorAll(".entry-part-tab").length > 1);
    assert.ok(card, "the fixture must have a multi-part cycle in the feed");
    click(own, card.querySelector(".entry-toggle"));
    click(own, journalButton(card));
    assert.ok(expanded(card) && reading(card), "the drawer is open before the tab is tapped");
    const tabs = [...card.querySelectorAll(".entry-part-tab")];
    click(own, tabs[1]);
    assert.ok(expanded(card), "the card stayed expanded");
    assert.ok(reading(card), "the journal drawer stayed open");
    assert.equal(tabs[1].getAttribute("aria-selected"), "true", "and the tab actually switched");
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
    // The first card is cycle 57, which wrote two entries, so closing has to
    // put back that card's own label rather than the generic one.
    assert.equal(journalButton(card).textContent, "Read the full journal (2 entries)");
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
    const dom = openWindow(html, { url: "https://nova.example/", runScripts: "outside-only", pretendToBeVisual: true });
    const { window } = dom;
    window.fetch = (url) =>
      res(url.includes("/api/digest") ? stale.digest : stale.journal);
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

  test("the bubble sits in the card's foot, below the drawer's opener, not in the head", () => {
    /* the owner, ideas.md 2026-08-10: "Move the Journal chat bubble icon to the
     * bottom right of the Journal cards." Two separate things have to hold,
     * because each breaks on its own:
     *   - it is out of `.entry-head` and inside `.entry-foot` (the move), and
     *   - the foot is before the drawer in document order, so tapping it
     *     opens the drawer *underneath* it. renderComments appends the drawer
     *     to the same container, so building the foot after the call passes
     *     the first assertion and fails this one. */
    const card = cardFor(window, 55);
    const b = bubble(card);
    assert.equal(b.parentNode.className, "entry-foot");
    assert.equal(card.querySelector(".entry-head .comment-toggle"), null);

    const kids = Array.from(card.children);
    const foot = kids.indexOf(b.parentNode);
    const drawer = kids.indexOf(card.querySelector(".comment-drawer"));
    assert.ok(foot > -1 && drawer > -1, "both the foot and the drawer are children of the card");
    assert.ok(foot < drawer, "the drawer opens below the bubble, not above it");
  });

  test("the foot does not sit flush against the brief on a collapsed card", () => {
    /* The reviewer caught this and nothing here could have: every other test
     * in this file asserts class names and DOM order, so the whole stylesheet
     * is untested and a layout regression ships green.
     *
     * The specific trap: `.entry.is-collapsed .entry-brief` zeroes its own
     * bottom margin, which was correct when the brief was the last visible
     * element on a collapsed card -- the digest, the journal toggle and the
     * body are all `display: none` there. The foot is now below it and is
     * deliberately not in those hide rules, so with no margin of its own the
     * brief's last line and the bubble touch on all ~57 collapsed cards.
     *
     * This is the real cascade, not a re-statement of it: jsdom resolves the
     * shipped style.css, so deleting `margin-top` from `.entry-foot` fails
     * this test. */
    const css = readFileSync(join(publicDir, "style.css"), "utf8");
    const { window: w } = openWindow(
      "<html><head><style>" + css + "</style></head><body>" +
        '<article class="entry is-collapsed">' +
        '<p class="entry-brief">brief</p>' +
        '<div class="entry-foot"><button class="comment-toggle">💬</button></div>' +
        "</article></body></html>"
    );
    const brief = w.document.querySelector(".entry-brief");
    const foot = w.document.querySelector(".entry-foot");
    assert.equal(
      w.getComputedStyle(brief).marginBottom,
      "0px",
      "the rule this guards against must still be there, or the test is guarding nothing"
    );
    const gap = w.getComputedStyle(foot).marginTop;
    assert.ok(gap && gap !== "0px" && gap !== "", "the foot needs a top margin of its own, got " + JSON.stringify(gap));
    assert.equal(w.getComputedStyle(foot).justifyContent, "flex-end", "bottom *right*");
  });

  test("an entry with no cycle number gets none", () => {
    // There is nothing to key a comment to, so a box there would swallow it.
    const orphan = cards(window).find((c) => !/^Cycle /.test(c.querySelector("h2").textContent));
    assert.ok(orphan, "the fixture must contain an entry with no cycle");
    assert.equal(bubble(orphan), null);
  });

  test("a cycle with two entries has exactly one bubble", () => {
    // Two would be two places to look for the same conversation. It used to
    // take an ownership rule across two cards to get this right; now the
    // cycle has one card, so the guarantee is structural.
    const both = cards(window).filter((c) => c.querySelector("h2").textContent === "Cycle 57");
    assert.equal(both.length, 1, "one card per cycle");
    assert.equal(payload.journal.entries.filter((e) => e.cycle === 57).length, 2,
      "the fixture must contain a cycle with an addendum");
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
    // conversation goes downwards" (the owner, 2026-08-10). The order is the
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
    // Nova's reply is a `.comment` too now (a sibling, same indentation),
    // so it is excluded by class rather than by nesting -- its paragraphs
    // are not his.
    const card = cardFor(window, 55);
    const paragraphs = [...card.querySelectorAll(".comment:not(.comment-reply) > .comment-body")].map((p) => p.textContent);
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
    const reply = card.querySelector(".comment-reply");
    assert.ok(reply, "the reply is rendered");
    /* the owner, issues.md 2026-08-10: "they should be below each other on the
     * same indentation. So the comments alternates between blue and green
     * downwards." So the reply is the comment's next sibling in the list,
     * not a child of it, and it carries `.comment` for the same box. */
    const answered = card.querySelector(".comment:not(.comment-reply)");
    assert.equal(answered.nextElementSibling, reply);
    assert.ok(reply.classList.contains("comment"));
    assert.equal(reply.parentElement, answered.parentElement);
    assert.equal(
      reply.querySelector(".comment-body").textContent,
      payload.comments.byCycle["55"][0].reply,
    );
    assert.equal(reply.querySelector(".comment-stamp").textContent, "2026-08-09 13:12");
  });

  test("a cycle's answer is its own purple bubble, not text inside the blue one", async () => {
    /* the owner, ideas board 2026-08-21: *"Give Nova cycle comments a purple
     * background/border in the app (mine is green, commentator is blue,
     * Nova should be purple)"*. Two different things answer him under one
     * name -- the instant reply worker, and an hourly cycle -- and until
     * now they wore the same blue. Worse, a cycle's answer is a second
     * `#### Nova` block in `comments.md`, and the app used to paint its
     * heading as literal text in the middle of the first bubble; his
     * screenshot the same day is of exactly that. */
    const copy = JSON.parse(JSON.stringify(payload.comments));
    const comment = copy.byCycle["55"][0];
    comment.replies = [
      { author: "commentator", stamp: "2026-08-09 13:12", text: comment.reply },
      { author: "cycle", stamp: "2026-08-09 14:20", text: "Cycle 56: boarded it." },
    ];
    const w = await loadSite("/", { comments: copy });
    const card = cardFor(w, 55);
    const replies = [...card.querySelectorAll(".comment-reply")];
    assert.equal(replies.length, 2, "one bubble per block, not one bubble with a heading in it");
    assert.ok(!replies[0].classList.contains("comment-reply-cycle"));
    assert.ok(replies[1].classList.contains("comment-reply-cycle"));
    assert.equal(replies[1].querySelector(".comment-body").textContent, "Cycle 56: boarded it.");
    assert.equal(replies[1].querySelector(".comment-who").textContent, "Nova · cycle");
    assert.equal(replies[1].querySelector(".comment-stamp").textContent, "2026-08-09 14:20");
    // Both sit beside his comment, not inside it -- the alternating-downwards
    // shape he asked for in issues.md 2026-08-10 still holds with two.
    const his = card.querySelector(".comment:not(.comment-reply)");
    assert.equal(his.nextElementSibling, replies[0]);
    assert.equal(replies[0].nextElementSibling, replies[1]);
  });

  test("a reply written after his next comment is painted after it, not beside its question", async () => {
    /* the owner, issues.md 2026-08-23: *"Comment thread ordering bug: a Nova
     * cycle reply posted at 14:01 rendered between two of my comments
     * timestamped 13:31 and 13:40 instead of after both — thread isn't
     * sorting strictly by time."*
     *
     * This is his case exactly. The reply is stored inside the comment it
     * answers, so painting in storage order put 14:01 at 13:31's position. */
    const copy = JSON.parse(JSON.stringify(payload.comments));
    copy.byCycle["55"] = [
      {
        cycle: 55, stamp: "2026-08-23 13:31", text: "First question.",
        reply: "", replyStamp: "",
        replies: [{ author: "cycle", stamp: "2026-08-23 14:01", text: "Answered an hour later." }],
        acknowledged: false, replyPending: false, replyWaiting: false,
        replyWaitingSeconds: 0, replyFailed: false,
      },
      {
        cycle: 55, stamp: "2026-08-23 13:40", text: "Second question, nine minutes later.",
        reply: "", replyStamp: "", replies: [],
        acknowledged: false, replyPending: false, replyWaiting: false,
        replyWaitingSeconds: 0, replyFailed: false,
      },
    ];
    const card = cardFor(await loadSite("/", { comments: copy }), 55);
    assert.deepEqual(
      [...card.querySelectorAll(".comment .comment-stamp")].map((s) => s.textContent),
      ["2026-08-23 13:31", "2026-08-23 13:40", "2026-08-23 14:01"],
    );
  });

  test("a reply carrying no stamp stays under its question rather than jumping to the top", async () => {
    /* `replyStamp` comes off a `#### Nova · <stamp>` heading and the payload
     * can carry it empty. Sorting that on `""` would put the answer above
     * every comment in the thread, which is the same bug pointing the other
     * way, so a stampless reply inherits the stamp of what it answers. */
    const copy = JSON.parse(JSON.stringify(payload.comments));
    copy.byCycle["55"] = [
      {
        cycle: 55, stamp: "2026-08-23 13:31", text: "Earlier question.",
        reply: "", replyStamp: "", replies: [],
        acknowledged: false, replyPending: false, replyWaiting: false,
        replyWaitingSeconds: 0, replyFailed: false,
      },
      {
        cycle: 55, stamp: "2026-08-23 13:40", text: "Later question.",
        reply: "", replyStamp: "",
        replies: [{ author: "cycle", stamp: "", text: "An answer with no time on it." }],
        acknowledged: false, replyPending: false, replyWaiting: false,
        replyWaitingSeconds: 0, replyFailed: false,
      },
    ];
    const card = cardFor(await loadSite("/", { comments: copy }), 55);
    assert.deepEqual(
      [...card.querySelectorAll(".comment .comment-body")].map((b) => b.textContent),
      ["Earlier question.", "Later question.", "An answer with no time on it."],
    );
  });

  test("a status line stays under the comment it is about, wherever that sits", async () => {
    /* The waiting lines carry no time of their own, so they take their
     * comment's stamp. Without that they would sort to `""` and land at the
     * top of the thread, attached to nothing -- which is worse than the bug
     * above, because the line only says *which* comment by sitting under it. */
    const copy = JSON.parse(JSON.stringify(payload.comments));
    copy.byCycle["55"] = [
      {
        cycle: 55, stamp: "2026-08-23 13:31", text: "Old, already answered.",
        reply: "", replyStamp: "",
        replies: [{ author: "cycle", stamp: "2026-08-23 13:35", text: "Done." }],
        acknowledged: false, replyPending: false, replyWaiting: false,
        replyWaitingSeconds: 0, replyFailed: false,
      },
      {
        cycle: 55, stamp: "2026-08-23 13:40", text: "Newer, still waiting.",
        reply: "", replyStamp: "", replies: [],
        acknowledged: false, replyPending: true, replyWaiting: false,
        replyWaitingSeconds: 0, replyFailed: false,
      },
    ];
    const card = cardFor(await loadSite("/", { comments: copy }), 55);
    const waiting = card.querySelector(".comment-waiting");
    assert.ok(waiting, "the pending line is rendered");
    assert.equal(
      waiting.previousElementSibling.querySelector(".comment-body").textContent,
      "Newer, still waiting.",
    );
  });

  test("an old cached app.js payload with only `reply` still paints one bubble", async () => {
    // `replies` is new. A browser holding yesterday's app.js against today's
    // server is not a case worth breaking, and the fallback is two lines.
    const copy = JSON.parse(JSON.stringify(payload.comments));
    delete copy.byCycle["55"][0].replies;
    const card = cardFor(await loadSite("/", { comments: copy }), 55);
    assert.equal(card.querySelectorAll(".comment-reply").length, 1);
    assert.equal(card.querySelector(".comment-waiting"), null);
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

  test("a long wait reports how long it has been and blames nothing", async () => {
    /* The line here used to read "Queued behind a running cycle", which is a
     * cause the server cannot see: a reply takes the bridge's parallel lane
     * except in the 15-minute window before an OAuth refresh, so the stated
     * reason was usually false. What is left is the elapsed time, which is
     * also the thing that separates a slow answer from a stuck one. */
    const waiting = withPending(57);
    waiting.byCycle["57"][0].replyWaiting = true;
    waiting.byCycle["57"][0].replyWaitingSeconds = 185;
    const w = await loadSite("/", { comments: waiting, install: captureTimers });
    const text = cardFor(w, 57).querySelector(".comment-waiting").textContent;
    assert.match(text, /Still working on this — 3 minutes so far\./);
    assert.doesNotMatch(text, /[Qq]ueued/);
  });

  test("a wait with no elapsed time still reads as a sentence", async () => {
    /* An older server, or a payload that lost the field, must not put
     * "NaN minutes" in front of him -- that is worse than the fixed
     * sentence this replaced. */
    const waiting = withPending(57);
    waiting.byCycle["57"][0].replyWaiting = true;
    delete waiting.byCycle["57"][0].replyWaitingSeconds;
    const w = await loadSite("/", { comments: waiting, install: captureTimers });
    const text = cardFor(w, 57).querySelector(".comment-waiting").textContent;
    assert.match(text, /Still working on this — a moment so far\./);
    assert.doesNotMatch(text, /NaN|undefined/);
  });

  test("a null elapsed time reads as a moment, not as zero seconds", async () => {
    /* Number(null) is 0, so a coercing guard lets a value the server never
     * means to send render as a confident "0 seconds". The same hole passes
     * [] and "" as zero and true as one. */
    const waiting = withPending(57);
    waiting.byCycle["57"][0].replyWaiting = true;
    waiting.byCycle["57"][0].replyWaitingSeconds = null;
    const w = await loadSite("/", { comments: waiting, install: captureTimers });
    const text = cardFor(w, 57).querySelector(".comment-waiting").textContent;
    assert.match(text, /Still working on this — a moment so far\./);
    assert.doesNotMatch(text, /0 seconds/);
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
        return res(served === 1 ? withPending(57) : answered);
      }
      return res({});
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

  test("a server error mid-wait keeps the drawer waiting instead of giving up", async () => {
    /* The drawer's `.catch` is the keep-waiting path, and a 500 used to
     * walk straight past it: the error body parsed, `pick` found no reply
     * in it, and the drawer stopped as though it had been told the reply
     * was not coming. He would have been left looking at a comment that
     * had quietly given up on itself. */
    let timers;
    const w = await loadSite("/", {
      comments: withPending(57),
      install: (win) => { timers = captureTimers(win); },
    });
    const card = cardFor(w, 57);
    w.fetch = (url) => (String(url).includes("/api/comments")
      ? res({ error: "the comments file is unreadable" }, 502)
      : res({}));

    await timers.fire();
    assert.ok(card.querySelector(".comment-waiting"), "still waiting after a 502");
    assert.ok(timers.queued.length > 0, "and still scheduled to try again");
  });

  test("a saved copy mid-wait keeps the drawer waiting instead of giving up", async () => {
    /* The 500 above is the loud version of this. This is the quiet one, and
     * it is worse: the worker answers a dead network out of its cache with an
     * ordinary 200 that parses cleanly, so nothing throws and the `.catch`
     * below it never runs. The cached body predates the reply, `pick` finds
     * no pending comment in it, and `paint` reads that as "the wait is over"
     * -- `watch(false)`, poll cancelled, permanently. He is left on a drawer
     * that has quietly stopped asking, with no error and nothing to retry. */
    let timers;
    const w = await loadSite("/", {
      comments: withPending(57),
      install: (win) => { timers = captureTimers(win); },
    });
    const card = cardFor(w, 57);
    assert.ok(card.querySelector(".comment-waiting"), "waiting to begin with");

    /* The cache holds what the page saw before the comment was ever posted:
     * no pending reply anywhere in it. That is the payload that used to end
     * the wait. */
    w.fetch = (url) => (String(url).includes("/api/comments")
      ? replayedRes(payload.comments)
      : res({}));

    await timers.fire();
    assert.ok(card.querySelector(".comment-waiting"), "still waiting after a saved copy");
    assert.ok(timers.queued.length > 0, "and still scheduled to try again");
  });

  test("a saved copy does not paint over a reply that is already on screen", async () => {
    /* Not repainted at all, rather than repainted and re-watched. A saved
     * copy is strictly older than what the drawer was drawn from, so acting
     * on one can only take information away -- here, a reply that had
     * already landed. */
    const answered = JSON.parse(JSON.stringify(payload.comments));
    answered.byCycle["57"][0].reply = "Thanks — here is what I found.";
    answered.byCycle["57"][0].replyPending = true;

    let timers;
    const w = await loadSite("/", {
      comments: answered,
      install: (win) => { timers = captureTimers(win); },
    });
    const card = cardFor(w, 57);
    assert.equal(
      card.querySelector(".comment-reply .comment-body").textContent,
      "Thanks — here is what I found.",
      "the reply is on screen before the poll runs",
    );

    w.fetch = (url) => (String(url).includes("/api/comments")
      ? replayedRes(payload.comments)
      : res({}));

    await timers.fire();
    assert.ok(
      card.querySelector(".comment-reply .comment-body"),
      "the reply survived a replayed poll",
    );
  });

  test("a saved copy after posting does not blank out the comment he just sent", async () => {
    /* The refetch after a successful write is the second door into the same
     * bug, and the one he would actually notice: the server has confirmed
     * the comment, the status line says "saved", and a cached list from
     * before it existed would repaint the drawer without it. A comment that
     * vanishes under the word "saved" is one he sends again. */
    let timers;
    const w = await loadSite("/", {
      install: (win) => { timers = captureTimers(win); },
    });
    const card = cardFor(w, 57);
    click(w, bubble(card));
    const before = card.querySelectorAll(".comment").length;
    assert.ok(before > 0, "the fixture drew some comments to begin with");

    const box = card.querySelector(".comment-text");
    box.value = "One more thing.";
    /* The POST still succeeds -- `loadSite` answers it with `postReply` --
     * and only the refetch behind it is served out of the cache, which is
     * exactly the split that makes this dangerous. */
    const live = w.fetch;
    w.fetch = (url, init) => {
      if (init && init.method === "POST") return live(url, init);
      if (String(url).includes("/api/comments")) return replayedRes({ byCycle: {}, needs: [] });
      return live(url, init);
    };
    click(w, card.querySelector(".comment-send"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(
      card.querySelectorAll(".comment").length,
      before,
      "the drawer still shows what it showed, not an empty cached list",
    );
    assert.equal(w.posted.length, 1, "and the comment really was sent");
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
    assert.equal(cards(w).length, new Set(payload.journal.entries.map((e) => e.cycle)).size);
    assert.equal(bubble(cardFor(w, 57)).textContent, "💬");
  });
});

/* Replying to Needs the owner (2026-08-10). The owner: "the 'needs the owner' is
 * still missing a comment block, so its hard for me to answer it. [...]
 * Where did you intend me to answer it? [...] I want a reply button on it."
 *
 * The section had been asking him a question for eight cycles with nowhere
 * to type an answer. These tests pin the two things that makes it fixed:
 * the box is reachable without a click, and what it sends names the block
 * rather than a cycle. */
describe("the page notices new entries on its own", () => {
  /** The site, with its timers under the test's control. */
  async function pollable(options = {}) {
    let timers;
    const window = await loadSite("/", {
      ...options,
      install: (win) => { timers = captureTimers(win); },
    });
    return { window, timers };
  }

  /** Serve `journal` from here on, leaving the other two endpoints alone. */
  function serve(window, journal) {
    window.fetch = (url) =>
      res(
        String(url).includes("/api/digest") ? payload.digest
          : String(url).includes("/api/comments") ? payload.comments
            : journal,
      );
  }

  /** The fixture with one more entry at the top, and a new server version. */
  function grown(version) {
    const copy = JSON.parse(JSON.stringify(payload.journal));
    const first = JSON.parse(JSON.stringify(copy.entries[0]));
    first.cycle = 999;
    copy.entries.unshift(first);
    copy.version = version;
    return copy;
  }

  test("it polls on its own, and keeps polling", async () => {
    const { timers } = await pollable();
    assert.equal(timers.queuedPagePolls.length, 1, "one poll scheduled at load");
    await timers.firePagePoll();
    assert.equal(timers.queuedPagePolls.length, 1, "and another after it ran");
  });

  test("a new entry lands on the page without a refresh", async () => {
    const { window, timers } = await pollable();
    const before = cards(window).length;
    serve(window, grown('W/"newer"'));

    await timers.firePagePoll();
    assert.equal(cards(window).length, before + 1, "the new card is on the page");
  });

  /* The half that is easy to get wrong, because a page that re-renders when
   * nothing changed still looks correct in a screenshot. It is not: a render
   * builds every card new, so an open drawer closes and the position he was
   * reading at moves. Twice a minute. */
  test("an unchanged payload leaves the page alone", async () => {
    const { window, timers } = await pollable();
    const card = cards(window)[0];
    assert.ok(window.document.contains(card));

    await timers.firePagePoll();
    assert.ok(window.document.contains(card), "the page was rebuilt for no reason");
  });

  /* And the same when the server does not send a version at all -- the
   * fixture above is exactly that payload, so this asserts the comparison is
   * normalised rather than that the fixture happens to match. */
  test("a payload with no version is not mistaken for a changed one", async () => {
    const { window, timers } = await pollable();
    assert.equal(payload.journal.version, undefined, "the fixture predates versions");
    const card = cards(window)[0];

    await timers.firePagePoll();
    await timers.firePagePoll();
    assert.ok(window.document.contains(card), "an absent version read as a change");
  });

  /* the owner, issues.md 2026-08-11: "The Nova site closes all drawers on what
   * seems like every 30 sec or so. Is this a refresh bug?"
   *
   * The two tests above cover the half where the page rebuilt for nothing.
   * This is the half that stays true even when the rebuild is earned: a new
   * entry arrives every hour, and the card he had open closed with it. The
   * card object itself is replaced -- that is what a render does -- so these
   * assert on the card at that position being open, not on the old node. */
  test("a poll keeps the part tab he opened, not just the card", async () => {
    /* `fold` carried expanded/journal/comments through a rebuild and did not
     * carry the open tab, so a routine poll put him back on part one while
     * the card and drawer correctly stayed open. The reviewer found that; my
     * own first test for it fired a poll that served an unchanged journal,
     * so nothing rebuilt and the test passed with the fix reverted. Hence
     * `grown()` here, and the length assertion below: this only means
     * something if a real render happened. */
    const { window, timers } = await pollable();
    const before = cards(window).length;
    const card = cards(window).find((c) => c.querySelectorAll(".entry-part-tab").length > 1);
    assert.ok(card, "the fixture must have a multi-part cycle in the feed");
    const cycle = card.id;
    click(window, card.querySelector(".entry-toggle"));
    click(window, card.querySelector(".journal-toggle"));
    click(window, [...card.querySelectorAll(".entry-part-tab")][1]);
    serve(window, grown('W/"newer-tab"'));

    await timers.firePagePoll();
    assert.equal(cards(window).length, before + 1, "the new card landed, so this is a real rebuild");
    const after = cards(window).find((c) => c.id === cycle);
    assert.ok(after && after !== card, "the card was rebuilt, not reused");
    const tabs = [...after.querySelectorAll(".entry-part-tab")];
    assert.equal(tabs[1].getAttribute("aria-selected"), "true", "still on the part he opened");
    assert.equal([...after.querySelectorAll(".entry-part-panel")].findIndex((n) => !n.hidden), 1);
  });

  test("a card he had opened is still open after a new entry lands", async () => {
    const { window, timers } = await pollable();
    const before = cards(window).length;
    click(window, cards(window)[0]);
    assert.ok(expanded(cards(window)[0]), "the tap opened it");
    serve(window, grown('W/"newer"'));

    await timers.firePagePoll();
    const after = cards(window);
    assert.equal(after.length, before + 1, "the new card landed, so this is a real rebuild");
    assert.ok(expanded(after[1]), "his card closed when the new entry arrived");
    assert.ok(!expanded(after[0]), "and the new one opened itself");
  });

  test("the drawer inside the card is restored with it", async () => {
    const { window, timers } = await pollable();
    const card = cards(window)[0];
    click(window, card);
    click(window, card.querySelector(".journal-toggle"));
    assert.ok(card.classList.contains("is-reading"), "the journal drawer opened");
    serve(window, grown('W/"newer"'));

    await timers.firePagePoll();
    const restored = cards(window)[1];
    assert.ok(expanded(restored), "the card came back collapsed");
    assert.ok(restored.classList.contains("is-reading"), "the drawer inside it came back shut");
  });

  test("the card that was open is the one that reopens, not the one in its place", async () => {
    /* Keyed by cycle rather than by position. A new entry pushes every card
     * down by one, so an index-keyed store hands the open state to whichever
     * cycle inherited the slot -- which is precisely the moment this runs. */
    const { window, timers } = await pollable();
    const opened = cards(window)[0].id;
    click(window, cards(window)[0]);
    serve(window, grown('W/"newer"'));

    await timers.firePagePoll();
    const open = cards(window).filter(expanded).map((card) => card.id);
    assert.deepEqual(open, [opened], "the open card moved to a different cycle");
  });

  test("it does not throw away what he is typing", async () => {
    const { window, timers } = await pollable();
    const card = cards(window)[0];
    const box = window.document.querySelector("textarea");
    assert.ok(box, "the page has somewhere to type");
    box.value = "half a sentence";
    serve(window, grown('W/"newer"'));

    await timers.firePagePoll();
    assert.ok(window.document.contains(card), "his card was rebuilt while he typed");
    assert.equal(box.value, "half a sentence");

    box.value = "";
    await timers.firePagePoll();
    assert.ok(!window.document.contains(card), "the deferred update never arrived");
  });

  /* the owner, issues.md 2026-08-24, with a screenshot of the header reading
   * "Cycle 374 · last woke 17:50" at 19:25, two entries behind: "Nova app
   * does not auto refresh/sync when i open it up. Look at the time in the
   * top left at compare it to the latest run cycle."
   *
   * Two things could hold a poll off forever, and these pin both. */
  /** Let the fetches a dispatched event started settle. `firePagePoll` does
   *  this internally; an event-driven poll has no timer to fire. Node's own
   *  `setTimeout`, not the window's -- `captureTimers` has taken that one. */
  const settle = async () => {
    for (let i = 0; i < 5; i += 1) await new Promise((r) => setTimeout(r, 0));
  };

  test("opening the app catches up even with a half-typed comment left in a drawer", async () => {
    /* Every card carries a comment drawer, so `typing()` sees a textarea per
     * entry on the page. One unsent reply -- in a drawer that is closed and
     * scrolled away -- deferred every poll for the life of the tab, and the
     * store behind it is in memory, so a reload cleared it and nothing on
     * the page ever said why. Opening the app is not typing. */
    const { window, timers } = await pollable();
    const card = cards(window)[0];
    const box = window.document.querySelector("textarea");
    box.value = "half a sentence";
    box.dispatchEvent(new window.Event("input"));
    serve(window, grown('W/"reopened"'));

    await timers.firePagePoll();
    assert.ok(window.document.contains(card), "the background timer should still hold off while he types");

    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await settle();
    assert.ok(!window.document.contains(card), "opening the app left the feed on the old entries");
    assert.equal(
      window.document.querySelector("textarea").value,
      "half a sentence",
      "the rebuild lost what he had typed",
    );
  });

  test("a page restored from the back/forward cache catches up too", async () => {
    /* A restore fires `pageshow` with `persisted` and need never have gone
     * hidden, so the visibility handler alone does not see it. */
    const { window } = await pollable();
    const before = cards(window).length;
    serve(window, grown('W/"restored"'));

    const event = new window.Event("pageshow");
    Object.defineProperty(event, "persisted", { value: true });
    window.dispatchEvent(event);
    await settle();
    assert.equal(cards(window).length, before + 1, "a restored page kept showing what it had");
  });

  /** Count journal requests while serving a newer feed. */
  function counting(window) {
    const count = { n: 0 };
    window.fetch = (url) => {
      if (String(url).includes("/api/journal")) count.n += 1;
      return res(
        String(url).includes("/api/digest") ? payload.digest
          : String(url).includes("/api/comments") ? payload.comments
            : grown('W/"counted"'),
      );
    };
    return count;
  }

  test("a window regaining focus asks, on its own", async () => {
    /* Asserted alone rather than alongside `visibilitychange`: with both
     * dispatched, the pre-existing visibility handler answers for one fetch
     * and the assertion holds with the focus listener absent entirely. The
     * reviewer caught that -- my first version of this test passed against a
     * full revert. */
    const { window } = await pollable();
    const count = counting(window);
    window.dispatchEvent(new window.Event("focus"));
    await settle();
    assert.equal(count.n, 1, "focus alone did not ask the server anything");
  });

  test("two ways of coming back at once still make one request", async () => {
    /* Opening the app fires more than one of these, in separate tasks. Two
     * polls in flight render in completion order, so the older answer can
     * land last and put the stale header back. */
    const { window } = await pollable();
    const count = counting(window);
    window.document.dispatchEvent(new window.Event("visibilitychange"));
    window.dispatchEvent(new window.Event("focus"));
    await settle();
    assert.equal(count.n, 1, "both events fetched, so they can race each other");
  });

  test("a window regaining focus does not rebuild the box he is typing in", async () => {
    /* `focus` fires on an ordinary window switch and `online` on a wifi
     * blip, neither of which says he was ever away from the keyboard. Only
     * the two events that mean the tab was gone skip the typing deferral. */
    const { window } = await pollable();
    const card = cards(window)[0];
    const box = window.document.querySelector("textarea");
    box.value = "mid sentence";
    box.dispatchEvent(new window.Event("input"));
    serve(window, grown('W/"focused"'));

    window.dispatchEvent(new window.Event("focus"));
    await settle();
    assert.ok(window.document.contains(card), "focus rebuilt the feed while he was typing");

    window.dispatchEvent(new window.Event("online"));
    await settle();
    assert.ok(window.document.contains(card), "online rebuilt the feed while he was typing");

    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await settle();
    assert.ok(!window.document.contains(card), "coming back to a hidden tab should still catch up");
  });

  test("a resumed poll cancels the timer that was already armed", async () => {
    /* Otherwise the background timer fires during a slow resumed fetch and
     * starts a second one -- the race the in-flight guard exists to stop,
     * on the one path it did not cover. */
    const { window, timers } = await pollable();
    assert.equal(timers.queuedPagePolls.length, 1, "one poll armed at load");
    let resolveJournal;
    window.fetch = (url) => {
      if (String(url).includes("/api/digest")) return res(payload.digest);
      if (String(url).includes("/api/comments")) return res(payload.comments);
      return new Promise((r) => { resolveJournal = () => r(res(grown('W/"slow"'))); });
    };
    window.document.dispatchEvent(new window.Event("visibilitychange"));
    await settle();
    assert.equal(
      timers.queuedPagePolls.length,
      0,
      "the armed timer survived the resume, so it can fire into the fetch still in flight",
    );
    resolveJournal();
    await settle();
    assert.equal(timers.queuedPagePolls.length, 1, "the round did not re-arm the timer");
  });
});

/* The other half of the same complaint: "Nova takes a long time to load."
 *
 * The server has answered `If-None-Match` with a 304 since #77 and the page
 * never sent one, so every poll re-downloaded the whole journal to find out
 * it had not changed. Measured against the live pod on 2026-08-11: 227,520
 * gzipped bytes per poll, every 30 seconds, on his phone.
 *
 * These stub a fetch that behaves the way nova_site actually does, rather
 * than one that always answers 200 -- a client that mishandles a 304 blanks
 * the feed, and a stub that never sends one cannot see that happen. */
describe("a poll asks whether anything changed, not for the whole journal", () => {
  async function pollable() {
    let timers;
    const window = await loadSite("/", { install: (win) => { timers = captureTimers(win); } });
    return { window, timers };
  }

  /** The fixture with a version on it. The recorded one predates versions,
   *  and a payload with no version is the case where there is nothing to
   *  send -- covered by the suite above, not this one. */
  const versioned = (base, version) =>
    Object.assign(JSON.parse(JSON.stringify(base)), { version });

  /** nova_site's actual behaviour: a matching `If-None-Match` gets an empty
   *  304, anything else gets the body. Returns the request log.
   *
   *  The 304's `json()` rejects rather than resolving to something empty,
   *  because that is what a real one does -- there is no body to parse. A
   *  client that reads the status wrongly fails here loudly instead of
   *  quietly rendering `undefined`. */
  function server(window, journal, digest, comments) {
    const sent = [];
    window.fetch = (url, init) => {
      const path = String(url);
      const asked = (init && init.headers && init.headers["If-None-Match"]) || null;
      sent.push({ path, asked });
      if (path.includes("/api/comments")) {
        return res(comments || payload.comments);
      }
      const body = path.includes("/api/digest") ? digest : journal;
      if (asked === body.version) {
        return notModified();
      }
      return res(body);
    };
    return sent;
  }

  const asked = (sent, endpoint) => sent.find((r) => r.path.includes(endpoint));

  test("it sends back the version the server last gave it", async () => {
    const { window, timers } = await pollable();
    const sent = server(window, versioned(payload.journal, 'W/"j1"'), versioned(payload.digest, 'W/"d1"'));

    await timers.firePagePoll(); // learns the versions; nothing to send yet
    assert.equal(asked(sent, "/api/journal").asked, null, "it invented a version it was never given");

    sent.length = 0;
    await timers.firePagePoll();
    assert.equal(asked(sent, "/api/journal").asked, 'W/"j1"');
    assert.equal(asked(sent, "/api/digest").asked, 'W/"d1"');
  });

  /* The endpoint that is uncached and unversioned on purpose, because it
   * changes underneath itself while a reply is being written. Asking it
   * conditionally would be asking it to answer 304 to a question it has no
   * etag for. */
  test("it does not ask the comments endpoint conditionally", async () => {
    const { window, timers } = await pollable();
    const sent = server(window, versioned(payload.journal, 'W/"j1"'), versioned(payload.digest, 'W/"d1"'));

    await timers.firePagePoll();
    sent.length = 0;
    await timers.firePagePoll();
    assert.equal(asked(sent, "/api/comments").asked, null);
    /* Paired with the journal on the same poll on purpose. On its own the
     * assertion above is true of the client that predates this change too,
     * so it would pin nothing -- it is only evidence of a deliberate split
     * if the other endpoint on the same poll did send one. */
    assert.equal(asked(sent, "/api/journal").asked, 'W/"j1"', "nothing was conditional at all");
  });

  /* The failure this whole suite exists to catch. A 304 has no body, so a
   * page that tries to render one shows nothing at all -- and it would only
   * do it on the second poll, thirty seconds after a load that looked fine. */
  test("a 304 leaves the page exactly as it was", async () => {
    const { window, timers } = await pollable();
    server(window, versioned(payload.journal, 'W/"j1"'), versioned(payload.digest, 'W/"d1"'));

    await timers.firePagePoll();
    const card = cards(window)[0];
    const count = cards(window).length;
    assert.ok(count > 0, "nothing was on the page before the 304 to begin with");

    await timers.firePagePoll();
    assert.equal(cards(window).length, count, "the feed emptied on a 304");
    assert.ok(window.document.contains(card), "the page was rebuilt for an empty response");
  });

  /* And the remembered payload must not become the page. A client that
   * answers its own poll from memory forever is worse than one that
   * re-downloads: it never shows a new entry again. */
  test("a new entry still arrives after a run of 304s", async () => {
    const { window, timers } = await pollable();
    server(window, versioned(payload.journal, 'W/"j1"'), versioned(payload.digest, 'W/"d1"'));
    await timers.firePagePoll();
    const before = cards(window).length;
    await timers.firePagePoll();
    await timers.firePagePoll();

    server(window, grownWithVersion('W/"j2"'), versioned(payload.digest, 'W/"d1"'));
    await timers.firePagePoll();
    assert.equal(cards(window).length, before + 1, "the page stopped noticing new entries");
  });

  /* The test that tells "handled the 304" apart from "threw on the 304".
   *
   * Both leave the feed intact, because a failed poll is caught and the
   * previous page is the right thing to keep -- so every assertion above
   * passes either way. What only the first one does is finish the poll: a
   * rejected journal fetch takes the whole `Promise.all` down with it, and
   * the comment that arrived alongside the unchanged journal never renders.
   *
   * That is the live case, not a contrived one. The journal changes once an
   * hour and comments change whenever the owner types, so almost every poll
   * that has anything to show is exactly this one. */
  test("a new comment renders on a poll where the journal did not change", async () => {
    const { window, timers } = await pollable();
    server(window, versioned(payload.journal, 'W/"j1"'), versioned(payload.digest, 'W/"d1"'));
    await timers.firePagePoll();
    const before = window.document.querySelectorAll(".comment-body").length;

    const grownComments = JSON.parse(JSON.stringify(payload.comments));
    const cycle = Object.keys(grownComments.byCycle)[0];
    grownComments.byCycle[cycle].unshift({
      cycle: Number(cycle),
      stamp: "2026-08-11 05:00",
      text: "a comment that arrived while the journal stood still",
      reply: "",
      replyStamp: "",
      acknowledged: false,
      replyPending: false,
      replyWaiting: false,
      replyFailed: false,
    });
    // Same versions, so both versioned endpoints answer 304 on this poll.
    server(window, versioned(payload.journal, 'W/"j1"'), versioned(payload.digest, 'W/"d1"'), grownComments);

    await timers.firePagePoll();
    assert.equal(
      window.document.querySelectorAll(".comment-body").length,
      before + 1,
      "a 304 on the journal swallowed a comment that had arrived",
    );
  });
});

/** The fixture with one more entry at the top and a new version. The suite
 *  above has its own copy scoped to itself; this one is shared. */
function grownWithVersion(version) {
  const copy = JSON.parse(JSON.stringify(payload.journal));
  const first = JSON.parse(JSON.stringify(copy.entries[0]));
  first.cycle = 999;
  copy.entries.unshift(first);
  copy.version = version;
  return copy;
}

/* The suite this file cannot run on itself.
 *
 * A window that outlives the run keeps `node --test` alive forever, and the
 * symptom is a CI job that sits at 33 minutes rather than a red test -- there
 * is no assertion that fires, because every test has already passed. So the
 * check is on the source instead: one door in, and `openWindow` is it. */
describe("no window escapes the registry", () => {
  const source = readFileSync(join(here, "app.test.mjs"), "utf8");

  test("only openWindow constructs a JSDOM", () => {
    const constructions = source.match(/new JSDOM\(/g) || [];
    assert.equal(
      constructions.length,
      1,
      "a raw `new JSDOM` was added -- its window is never closed, and the " +
        "whole suite will hang after the last test passes. Use openWindow.",
    );
  });

  test("the registry is emptied after the file, not after each test", () => {
    /* Five suites below open one window in `before` and share it. An
     * `afterEach` closed it after the first test in each and cost 45
     * failures; this is the line that would have to change again. */
    assert.match(source, /\nafter\(\(\) => \{\n\s+for \(const window of openWindows\.splice\(0\)\)/);
  });
});

/* The cold load. #84 made the poll conditional and left the first load
 * downloading every entry ever written -- 109 of them, 187,148 gzipped
 * bytes off the live pod on 2026-08-11, one more an hour. The page asks
 * for a window now.
 *
 * The stub below is the real server contract, not a convenience: `total` is
 * the whole corpus and `entries` is the slice, so a test can tell "the
 * pager knows there is more" from "the pager can count the cards". */
describe("the feed loads a window rather than the whole journal", () => {
  const all = payload.journal.entries;

  /** A server that honours `?limit=` over a corpus of `size` entries. */
  function paged(size) {
    const corpus = [];
    for (let i = 0; i < size; i += 1) {
      // Distinct cycle numbers, newest first, so a card can be identified.
      corpus.push({ ...JSON.parse(JSON.stringify(all[2])), cycle: size - i });
    }
    const asked = [];
    const serve = (url) => {
      asked.push(url);
      const limit = Number(new URL(url, "https://nova.example").searchParams.get("limit"));
      return {
        entries: corpus.slice(0, limit || corpus.length),
        status: payload.journal.status,
        total: corpus.length,
        version: 'W/"' + (limit || "all") + '"',
      };
    };
    return { serve, asked };
  }

  test("a cold load asks for twenty entries, not all of them", async () => {
    const server = paged(50);
    await loadSite("/", { journal: server.serve });
    assert.match(server.asked[0], /\/api\/journal\?limit=20$/);
  });

  test("it renders the window it was given and offers the rest", async () => {
    const server = paged(50);
    const window = await loadSite("/", { journal: server.serve });
    assert.equal(cards(window).length, 20);
    assert.ok(window.document.querySelector("button.more"), "no way to reach the older entries");
  });

  test("showing older entries widens the window and adds cards", async () => {
    const server = paged(50);
    const window = await loadSite("/", { journal: server.serve });
    click(window, window.document.querySelector("button.more"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.match(server.asked[server.asked.length - 1], /limit=40$/);
    assert.equal(cards(window).length, 40);
  });

  test("the pager disappears once the whole journal is on screen", async () => {
    const server = paged(25);
    const window = await loadSite("/", { journal: server.serve });
    click(window, window.document.querySelector("button.more"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(cards(window).length, 25);
    assert.equal(window.document.querySelector("button.more"), null);
  });

  test("a server that sends no total offers no pager at all", async () => {
    /* The fixture is the pre-pagination payload: entries and status, no
     * `total`. A page that guessed from `entries.length` would show a
     * pager that could never do anything. */
    const window = await loadSite("/");
    assert.equal(window.document.querySelector("button.more"), null);
  });

  test("a deep link asks for its own cycle instead of a window", async () => {
    const server = paged(50);
    await loadSite("/cycle/7", { journal: server.serve });
    assert.match(server.asked[0], /\/api\/journal\?cycle=7$/);
  });

  /** The digest URLs the page asked for, in order. */
  function digestSpy() {
    const asked = [];
    const serve = (url) => {
      asked.push(url);
      return payload.digest;
    };
    return { serve, asked };
  }

  test("the digest is asked for the same window as the feed", async () => {
    /* 266KB of the digest's 271KB is its summary lines, one per cycle and
     * one more an hour -- the same shape the journal had before it learned
     * to send a window. The page has to ask for the window, or the server
     * hands back all of them and the cold load is no smaller. */
    const server = paged(50);
    const spy = digestSpy();
    await loadSite("/", { journal: server.serve, digest: spy.serve });
    assert.match(spy.asked[0], /\/api\/digest\?limit=20$/);
  });

  test("showing older entries widens the digest with the feed", async () => {
    /* They have to move together. A feed of forty against summaries for
     * twenty is twenty cards that lost their headline on the way past the
     * boundary. */
    const server = paged(50);
    const spy = digestSpy();
    const window = await loadSite("/", { journal: server.serve, digest: spy.serve });
    click(window, window.document.querySelector("button.more"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.match(spy.asked[spy.asked.length - 1], /\/api\/digest\?limit=40$/);
  });

  test("a deep link asks the digest for its own cycle too", async () => {
    const server = paged(50);
    const spy = digestSpy();
    await loadSite("/cycle/7", { journal: server.serve, digest: spy.serve });
    assert.match(spy.asked[0], /\/api\/digest\?cycle=7$/);
  });

  test("a poll asks for the window that is on screen, not the first page", async () => {
    const server = paged(50);
    let timers;
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { timers = captureTimers(w); },
    });
    click(window, window.document.querySelector("button.more"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    server.asked.length = 0;
    await timers.firePagePoll();
    assert.ok(server.asked.length, "the poll never ran");
    assert.ok(
      server.asked.every((url) => /limit=40$/.test(url)),
      "a poll narrowed the feed back to the first page: " + server.asked.join(", "),
    );
  });
});

/* Scrolling to the end of the feed loads the next window on its own.
 *
 * the owner, issues.md #71: "Make it more lazy load when i scroll down instead
 * of a button i press."
 *
 * jsdom has no IntersectionObserver at all, which is why every test above
 * still clicks and still passes -- app.js finds none and leaves the button
 * exactly as it was. That is the fallback working, and it means these tests
 * have to install one. The stub is deliberately dumb: it records what it was
 * asked to watch and fires only when a test says so, because the thing under
 * test is what app.js does with an intersection, not when a real browser
 * decides to report one. */
describe("the pager fires on scroll, not only on a press", () => {
  /** Install a fake IntersectionObserver and hand back the control surface.
   *
   * `initial` models the half of the spec that is easiest to forget: an
   * observer delivers an observation as soon as `observe()` is called, if
   * the target already meets the condition. A stub that only fires when a
   * test says so cannot see any of the behaviour that follows from that,
   * and the first version of this file had exactly that blind spot. */
  function observed(window, { initial = false } = {}) {
    const watching = [];
    let disconnects = 0;
    window.IntersectionObserver = class {
      constructor(callback, options) {
        this.callback = callback;
        this.options = options;
      }
      observe(node) {
        watching.push({ node, observer: this });
        if (initial) this.callback([{ isIntersecting: true, target: node }], this);
      }
      disconnect() {
        disconnects += 1;
        for (let i = watching.length - 1; i >= 0; i -= 1) {
          if (watching[i].observer === this) watching.splice(i, 1);
        }
      }
    };
    return {
      watching,
      get disconnects() { return disconnects; },
      /** Report the newest watched node as having scrolled into view. */
      scrollTo() {
        const last = watching[watching.length - 1];
        last.observer.callback([{ isIntersecting: true, target: last.node }], last.observer);
      },
    };
  }

  /** The same paged server the suite above uses. */
  function paged(size) {
    const all = payload.journal.entries;
    const corpus = [];
    for (let i = 0; i < size; i += 1) {
      corpus.push({ ...JSON.parse(JSON.stringify(all[2])), cycle: size - i });
    }
    const asked = [];
    return {
      asked,
      serve(url) {
        asked.push(url);
        const limit = Number(new URL(url, "https://nova.example").searchParams.get("limit"));
        return {
          entries: corpus.slice(0, limit || corpus.length),
          status: payload.journal.status,
          total: corpus.length,
          version: 'W/"' + (limit || "all") + '"',
        };
      },
    };
  }

  test("reaching the end of the feed widens the window with no press", async () => {
    const server = paged(50);
    let spy;
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { spy = observed(w); },
    });
    assert.equal(spy.watching.length, 1, "the pager was never watched");
    assert.equal(spy.watching[0].node, window.document.querySelector("button.more"));

    spy.scrollTo();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.match(server.asked[server.asked.length - 1], /limit=40$/);
    assert.equal(cards(window).length, 40);
  });

  test("the observer is handed a margin, so it does not wait for the pager to be visible", async () => {
    /* A window that only begins loading once the reader is looking at the
     * end of the feed shows them the end of the feed. The margin is what
     * makes it feel like there was never a boundary.
     *
     * The name says what this checks and no more: it reads the option the
     * observer was constructed with. Whether a margin of that size actually
     * feels seamless is a judgement about a real phone, and no test in a
     * jsdom with no layout and no scrolling can make it. */
    const server = paged(50);
    let spy;
    await loadSite("/", { journal: server.serve, install: (w) => { spy = observed(w); } });
    const margin = spy.watching[0].observer.options.rootMargin;
    assert.match(margin, /(\d+)px/);
    assert.ok(Number(margin.match(/(\d+)px/)[1]) > 0, "no margin, so it fires too late: " + margin);
  });

  test("a second intersection before the fetch lands does not skip a window", async () => {
    /* A real observer can deliver a batch that was already queued when
     * `disconnect` landed. Two reports either side of one fetch must widen
     * by twenty, not forty -- the reader would otherwise scroll past a page
     * they never saw requested.
     *
     * What actually stops the second one is that the click handler disables
     * the button and a disabled button dispatches no click. That is worth a
     * test precisely because it is not a line anyone wrote here: it is a
     * platform rule the code is leaning on, and it is invisible in the
     * source. */
    const server = paged(90);
    let spy;
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { spy = observed(w); },
    });
    const before = spy.watching[0];
    before.observer.callback([{ isIntersecting: true, target: before.node }], before.observer);
    before.observer.callback([{ isIntersecting: true, target: before.node }], before.observer);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(cards(window).length, 40, "one node in view twice widened the window twice");
  });

  test("it ignores a report that the pager left the screen", async () => {
    const server = paged(50);
    let spy;
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { spy = observed(w); },
    });
    const seen = spy.watching[0];
    seen.observer.callback([{ isIntersecting: false, target: seen.node }], seen.observer);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(cards(window).length, 20, "scrolling away from the pager loaded more");
  });

  test("the old observer lets go of the node the re-render threw away", async () => {
    /* `render` rebuilds the feed from scratch, so the node the first
     * observer holds is detached the moment the new window arrives. Left
     * attached, every widening leaves another observer behind watching a
     * node that can never intersect again. */
    const server = paged(90);
    let spy;
    await loadSite("/", { journal: server.serve, install: (w) => { spy = observed(w); } });
    spy.scrollTo();
    await new Promise((resolve) => setTimeout(resolve, 0));
    /* Two, not one, and the second is the point of the test above this one:
     * firing disconnects it, and then the re-render's fresh attach
     * disconnects whatever it is superseding. Both paths have to hold or an
     * observer outlives the node it is watching. */
    assert.ok(spy.disconnects >= 1, "the fired observer stayed attached");
    assert.equal(spy.watching.length, 1, "more than one live observer on one pager");
  });

  test("the pager stops looking like a button once it drives itself", async () => {
    /* He asked for it to stop being something he presses. It stays a real
     * focusable button -- and it stays laid out, because an element that is
     * `display: none` never intersects and the whole thing would silently
     * never fire. */
    const server = paged(50);
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { observed(w); },
    });
    const pager = window.document.querySelector("button.more");
    assert.ok(pager.classList.contains("more-auto"), "still styled as a control");
    assert.doesNotMatch(pager.textContent, /^Show /, "still tells him to press it");
    assert.notEqual(window.getComputedStyle(pager).display, "none");
  });

  test("with no IntersectionObserver it is still the button it always was", async () => {
    /* Which is also what every other test in this file is relying on. */
    const server = paged(50);
    const window = await loadSite("/", { journal: server.serve });
    const pager = window.document.querySelector("button.more");
    assert.equal(pager.textContent, "Show older entries");
    assert.equal(pager.classList.contains("more-auto"), false);
    click(window, pager);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(cards(window).length, 40);
  });

  test("a pager already on screen at first paint fills the screen and stops", async () => {
    /* The spec delivers an observation on `observe()`, so on a viewport tall
     * enough to hold the whole first window the pager widens with no scroll.
     * That is intended -- it is the screen filling -- but it has to *stop*,
     * and this stub is the worst case for that: it reports every new pager
     * as visible, which is a viewport of infinite height. What must not
     * happen is a hang or a fetch that never ends. */
    const server = paged(50);
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { observed(w, { initial: true }); },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.ok(cards(window).length > 20, "an already-visible pager did nothing");
    assert.equal(cards(window).length, 50, "it stopped before the corpus was exhausted");
    assert.equal(window.document.querySelector("button.more"), null, "a pager with nothing left to fetch");
  });

  test("only one observer is ever live, however many times the feed re-renders", async () => {
    /* The leak the fired-observer disconnect does not cover: the 30-second
     * poll rebuilds the feed whenever an entry lands, and a reader who never
     * scrolled to the pager leaves an observer on a node that has just been
     * thrown away. Over a day on a phone that is hundreds of them. */
    const server = paged(50);
    let spy;
    const window = await loadSite("/", {
      journal: server.serve,
      install: (w) => { spy = observed(w); },
    });
    assert.equal(spy.watching.length, 1);
    const first = spy.watching[0].node;

    // Two more renders that are not the pager firing: a fresh load each time.
    window.dispatchEvent(new window.Event("popstate"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    window.dispatchEvent(new window.Event("popstate"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(spy.watching.length, 1, "an observer per render, all but one on a detached node");
    assert.notEqual(spy.watching[0].node, first, "the live observer is on the old node");
    assert.ok(spy.disconnects >= 2, "the superseded observers were never disconnected");
  });

  test("my notes pager scrolls itself too", async () => {
    /* Same helper, second caller. The journal is the one he asked about and
     * this is the other list in the app with a pager under it; leaving one
     * behind is how the two halves of a pair drift. */
    let spy;
    const window = await loadSite("/issues", { install: (w) => { spy = observed(w); } });
    const tab = [...window.document.querySelectorAll(".tab")]
      .filter((one) => /Nova's/.test(one.textContent))[0];
    click(window, tab);
    const pager = window.document.querySelector("button.more");
    assert.ok(pager, "no pager on the notes tab, so this test proves nothing");
    assert.ok(spy.watching.some((one) => one.node === pager), "the notes pager is not watched");
  });
});

/* A render throws every comment drawer away and builds a new one. `poll`
 * dodges that by refusing to run while there is text in a box; the pager
 * cannot, because the re-render is what the reader just asked for. So the
 * text has to outlive the node. */
describe("an unsent comment survives a re-render", () => {
  test("showing older entries does not throw away what was typed", async () => {
    const corpus = [];
    for (let i = 0; i < 50; i += 1) {
      corpus.push({ ...JSON.parse(JSON.stringify(payload.journal.entries[2])), cycle: 50 - i });
    }
    const serve = (url) => {
      const limit = Number(new URL(url, "https://nova.example").searchParams.get("limit"));
      return {
        entries: corpus.slice(0, limit || corpus.length),
        status: payload.journal.status,
        total: corpus.length,
        version: 'W/"' + limit + '"',
      };
    };
    const window = await loadSite("/", { journal: serve });

    const box = window.document.querySelector(".entry .comment-text");
    box.value = "half a thought";
    box.dispatchEvent(new window.Event("input", { bubbles: true }));

    click(window, window.document.querySelector("button.more"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(cards(window).length, 40, "the pager did not widen the window");
    const after = window.document.querySelector(".entry .comment-text");
    assert.notEqual(after, box, "the drawer was not actually rebuilt, so this proves nothing");
    assert.equal(after.value, "half a thought");
  });
});

/* The board pages -- Issues and Ideas (issues.md #57).
 *
 * the owner: "I need more visualisations in the Nova app. Create more pages
 * to contain more, such as issue list, idea list (separate pages)".
 *
 * These drive the real router, so what is being checked is what a cold
 * load of `/issues` actually renders, not that a function exists. */
const rows = (window) => [...window.document.querySelectorAll(".item")];
const rowNumbers = (window) =>
  rows(window).map((row) => row.querySelector(".item-number").textContent);

describe("the issues page", () => {
  test("a cold load of /issues renders his rows, not the journal", async () => {
    const window = await loadSite("/issues");
    assert.equal(cards(window).length, 0, "the journal feed rendered on a board page");
    assert.ok(rows(window).length > 0, "no board rows");
    const first = rows(window)[0];
    assert.equal(first.querySelector(".item-number").textContent, "#57");
    assert.equal(first.querySelector(".item-title").textContent, "More pages in the Nova app");
    assert.equal(first.querySelector(".chip").textContent, "🟡 In progress");
  });

  test("the page asks the board endpoint and never the journal", async () => {
    const asked = [];
    const window = await loadSite("/issues", {
      board: (url) => {
        asked.push(url);
        return url.includes("item=") ? payload.boardItem : payload.board;
      },
      journal: (url) => {
        asked.push(url);
        return payload.journal;
      },
    });
    assert.ok(asked.length > 0);
    assert.ok(
      asked.every((url) => url.includes("/api/board")),
      "a board page fetched the journal: " + asked.join(", "),
    );
    assert.ok(asked[0].includes("name=issues"));
    assert.ok(window.document.querySelector(".nav-tab[href='/issues']").classList.contains("on"));
  });

  test("the default filter hides done items and a chip brings them back", async () => {
    const window = await loadSite("/issues");
    const open = rowNumbers(window);
    assert.ok(open.includes("#57"), "an open item is missing from the default view");
    assert.ok(!open.includes("#51"), "a done item is in the default view");

    click(window, window.document.querySelector(".board-filter-btn"));
    const all = [...window.document.querySelectorAll(".filter")]
      .filter((chip) => chip.textContent.startsWith("All"))[0];
    click(window, all);
    assert.ok(rowNumbers(window).includes("#51"), "All did not reveal the done items");

    const done = [...window.document.querySelectorAll(".filter")]
      .filter((chip) => chip.textContent.startsWith("Done"))[0];
    click(window, done);
    assert.deepEqual(rowNumbers(window).sort(), ["#51", "#56"]);
  });

  test("the write-up is fetched on the tap that opens the row, not before", async () => {
    const asked = [];
    const window = await loadSite("/issues", {
      board: (url) => {
        asked.push(url);
        return url.includes("item=") ? payload.boardItem : payload.board;
      },
    });
    assert.ok(
      asked.every((url) => !url.includes("item=")),
      "a detail body was fetched before anything was opened: " + asked.join(", "),
    );
    const head = rows(window)[0].querySelector(".item-head");
    click(window, head);
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.ok(asked.some((url) => url.includes("item=57")), "the tap fetched nothing");
    assert.equal(head.getAttribute("aria-expanded"), "true");
    const body = rows(window)[0].querySelector(".item-body");
    assert.equal(body.hidden, false);
    assert.match(body.textContent, /Five pages, in the order I would build them\./);
  });

  test("opening a second row closes the first", async () => {
    const window = await loadSite("/issues");
    click(window, rows(window)[0].querySelector(".item-head"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    click(window, rows(window)[1].querySelector(".item-head"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(rows(window)[0].querySelector(".item-body").hidden, true);
    assert.equal(rows(window)[1].querySelector(".item-body").hidden, false);
  });

  test("his unboarded captures are shown rather than hidden until a cycle files them", async () => {
    const window = await loadSite("/issues");
    const box = window.document.querySelector(".captures");
    assert.ok(box, "the capture block is missing");
    assert.match(box.textContent, /Small pickings on Nova ui/);
  });

  test("each not-boarded capture is separated from the next", async () => {
    /* the owner, issues.md #66: "should have a separator line or something
     * that shows a clear separation of the not boarded issues." The block
     * already had a border; what ran together was one bullet against the
     * next. Pinned on the rule rather than on a class name being present,
     * because the class alone would still pass with no rule drawn. */
    const window = await loadSite("/issues");
    const items = [...window.document.querySelectorAll(".capture-item")];
    assert.ok(items.length >= 1, "no captures rendered");
    const sheet = readFileSync(join(publicDir, "style.css"), "utf8");
    assert.match(sheet, /\.capture-item\s*{[^}]*border-top:\s*1px/);
    assert.match(sheet, /\.capture-item:first-of-type\s*{[^}]*border-top:\s*0/);
  });


  /* Every control on a capture now lives behind a press-and-hold. The
   * owner, 2026-08-24: *"The new buttons for the messages to edit or make
   * idea or make issue should not be visible. Lets change it to when i
   * press and hold it it opens a modal with al the edit options."*
   *
   * So a test that wants Delete has to make the gesture first, and it is
   * driven as real events rather than by calling the handler -- the click
   * a browser sends after the release is the one thing that could close
   * the sheet the same instant it opened, and only the event sequence has
   * that in it. Save and Cancel are deliberately *not* found here: they
   * belong to the open editor on the card, not to the sheet. */
  const CAPTURE_HOLD_MS = 1030;
  const holdCapture = async (window, item) => {
    const body = item.querySelector(".capture-body");
    const fire = (type) => body.dispatchEvent(
      new window.MouseEvent(type, { bubbles: true, cancelable: true }));
    fire("mousedown");
    await new Promise((resolve) => setTimeout(resolve, CAPTURE_HOLD_MS));
    fire("mouseup");
    click(window, body);
    await new Promise((resolve) => setTimeout(resolve, 0));
    const sheet = window.document.querySelector(".action-sheet");
    assert.ok(sheet, "a one-second hold opened no action sheet");
    return sheet;
  };
  const sheetAct = (sheet, label) =>
    [...sheet.querySelectorAll(".capture-act")].filter((b) => b.textContent === label)[0];

  test("Delete sends the capture's own text, not its position", async () => {
    /* The whole point of the design: a cycle boarding a bullet above this
     * one shifts every index, and an index-addressed delete would then
     * remove a different capture. */
    const window = await loadSite("/issues");
    window.confirm = () => true;
    const item = window.document.querySelector(".capture-item");
    click(window, sheetAct(await holdCapture(window, item), "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.posted.length, 1);
    assert.equal(window.posted[0].url, "/api/capture/delete");
    assert.equal(window.posted[0].body.target, "issues");
    assert.equal(window.posted[0].body.original, payload.board.captures[0].text);
    assert.equal(window.posted[0].body.index, 0);
    assert.equal(window.posted[0].body.text, undefined, "a delete carried replacement text");
  });

  test("the second of two identical captures sends its own position", async () => {
    /* Review found this: matching on text alone rewrites whichever came
     * first and reports success. Two captures reading the same is the
     * only way the page can tell the server which one was tapped. */
    const same = { text: "fix this", blocks: [{ type: "p", spans: [{ kind: "text", text: "fix this" }] }] };
    const board = { ...payload.board, captures: [same, same] };
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("item=") ? payload.boardItem : board),
    });
    window.confirm = () => true;
    const items = [...window.document.querySelectorAll(".capture-item")];
    assert.equal(items.length, 2);
    click(window, sheetAct(await holdCapture(window, items[1]), "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.posted[0].body.index, 1,
      "both rows sent the same address, so the wrong capture would be deleted");
    assert.equal(window.posted[0].body.original, "fix this");
  });

  test("a capture a cycle closed is not shown, and the ones left keep their addresses", async () => {
    /* Cycle 251 gave closed captures their own "Done, not yet cleared"
     * section; The owner asked for that section to go, capture 2026-08-20:
     * *"I do not like or see the point of the 'Done, not yet cleared' list
     * in issues and ideas. I do not use it and to me its just noise."*
     *
     * Dropping them changes what the addressing risk looks like rather
     * than removing it. `/api/capture/edit` takes the bullet's position in
     * the *file*, so a filtered list whose index came from the filtered
     * array would address the wrong bullet -- and now silently, since the
     * row it would hit is no longer on screen to look wrong. */
    const blocks = (t) => [{ type: "p", spans: [{ kind: "text", text: t }] }];
    const board = {
      ...payload.board,
      /* The done one is **first** in the file, so the open one's file
       * index (1) and its index among the rendered rows (0) differ. With
       * it second both numbers are 0 and the assertion cannot fail. */
      captures: [
        { text: "DONE (Cycle 247): shipped it", done: "Cycle 247", body: "shipped it", blocks: blocks("shipped it") },
        { text: "the thing I typed", body: "the thing I typed", blocks: blocks("the thing I typed") },
      ],
    };
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("item=") ? payload.boardItem : board),
    });
    window.confirm = () => true;

    const titles = [...window.document.querySelectorAll(".captures-title")]
      .map((n) => n.textContent);
    assert.deepEqual(titles, ["Not boarded yet"]);
    const open = window.document.querySelector(".captures");
    assert.equal(open.querySelectorAll(".capture-item").length, 1);
    assert.match(open.textContent, /the thing I typed/);
    assert.doesNotMatch(open.textContent, /shipped it/);
    assert.equal(window.document.querySelector(".capture-done-chip"), null);

    // The open one is second in the file and must still say so.
    click(window, sheetAct(await holdCapture(window, open.querySelector(".capture-item")), "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted[0].body.index, 1,
      "filtering renumbered the rows and the page sent the wrong address");
    assert.equal(window.posted[0].body.original, "the thing I typed");
  });

  test("Delete asks first, and sends nothing when the answer is no", async () => {
    const window = await loadSite("/issues");
    window.confirm = () => false;
    const item = window.document.querySelector(".capture-item");
    click(window, sheetAct(await holdCapture(window, item), "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted.length, 0, "a declined confirm still deleted");
  });

  test("Edit opens the raw markdown and saves it against the original", async () => {
    /* The capture carries markdown, and that is the whole point of the
     * fixture. Filling the field from the rendered node instead of from
     * `text` is indistinguishable on a plain one-line capture -- which is
     * every capture in the live payload -- so a test written against one
     * of those passes whether the code is right or wrong. Here the two
     * genuinely differ: the field must hold the backticks and asterisks,
     * because what the owner edits is what the vault stores. */
    const raw = "the `/api/board` page is **slow**";
    const board = {
      ...payload.board,
      captures: [{
        text: raw,
        blocks: [{ type: "p", spans: [
          { kind: "text", text: "the " },
          { kind: "code", text: "/api/board" },
          { kind: "text", text: " page is " },
          { kind: "strong", text: "slow" },
        ] }],
      }],
    };
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("item=") ? payload.boardItem : board),
    });
    const item = window.document.querySelector(".capture-item");
    // The rendered paragraph, not all of `.capture-body`: the priority
    // trigger (issues.md #91) is a sibling inside that same element and
    // its label would otherwise be concatenated onto his text here. `p` is
    // what `renderBlocks` produced, which is the thing this line is about.
    assert.equal(item.querySelector(".capture-body p").textContent,
      "the /api/board page is slow", "the fixture does not render differently from its source");
    click(window, sheetAct(await holdCapture(window, item), "Edit"));

    const box = item.querySelector(".capture-input");
    assert.ok(box, "Edit did not open a field");
    assert.equal(box.value, raw,
      "the field was filled with rendered text rather than what the vault holds");
    box.value = "reworded on the phone";
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Save")[0]);
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.posted.length, 1);
    assert.equal(window.posted[0].url, "/api/capture/edit");
    assert.deepEqual(window.posted[0].body, {
      target: "issues",
      index: 0,
      original: raw,
      text: "reworded on the phone",
    });
  });

  test("an edit box shows its attachments as pictures, and ✕ takes one out", async () => {
    /* The owner, 2026-08-24: *"uploaded images just show like a url text,
     * it should show like the miniature images like when i upload them."*
     *
     * The textarea keeps the raw markdown -- that is what the vault
     * stores -- so the picture is a chip under the box, and removing it
     * has to cut that construct out of the text, which is the thing he
     * cannot do with a thumb on a 90-character URL. */
    const raw = "look at this ![shot.jpg](/api/upload/abc123.jpg) and this [notes.pdf](/api/upload/def.pdf)";
    const board = {
      ...payload.board,
      captures: [{ text: raw, blocks: [{ type: "p", spans: [{ kind: "text", text: raw }] }] }],
    };
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("item=") ? payload.boardItem : board),
    });
    const item = window.document.querySelector(".capture-item");
    click(window, sheetAct(await holdCapture(window, item), "Edit"));

    const chips = [...item.querySelectorAll(".edit-tray .attach-chip")];
    assert.equal(chips.length, 2, "the two attachments did not become chips");
    const thumb = chips[0].querySelector("img.attach-thumb");
    assert.ok(thumb, "the image attachment drew no thumbnail");
    assert.equal(thumb.getAttribute("src"), "/api/upload/abc123.jpg");
    assert.equal(thumb.getAttribute("alt"), "shot.jpg",
      "the alt text has to be his filename -- it is what tells two screenshots apart");
    // The non-image one is a name, not a broken picture.
    assert.equal(chips[1].querySelector("img"), null);
    assert.match(chips[1].textContent, /notes\.pdf/);

    click(window, chips[0].querySelector(".attach-chip-remove"));
    const box = item.querySelector(".capture-input");
    assert.equal(
      box.value,
      "look at this  and this [notes.pdf](/api/upload/def.pdf)",
      "✕ cut the wrong span out of his text",
    );
    assert.equal(item.querySelectorAll(".edit-tray .attach-chip").length, 1,
      "the strip did not redraw after a removal");
  });

  test("the second of two links to the same upload is the one ✕ removes", async () => {
    /* `indexOf` would have taken the first, which is a different sentence
     * of his. The chips are indistinguishable on screen, so nothing but
     * this test can tell the two implementations apart. */
    const raw = "first ![a.jpg](/api/upload/a.jpg) then ![a.jpg](/api/upload/a.jpg) end";
    const board = {
      ...payload.board,
      captures: [{ text: raw, blocks: [{ type: "p", spans: [{ kind: "text", text: raw }] }] }],
    };
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("item=") ? payload.boardItem : board),
    });
    const item = window.document.querySelector(".capture-item");
    click(window, sheetAct(await holdCapture(window, item), "Edit"));
    const chips = [...item.querySelectorAll(".edit-tray .attach-chip")];
    assert.equal(chips.length, 2);
    click(window, chips[1].querySelector(".attach-chip-remove"));
    assert.equal(
      item.querySelector(".capture-input").value,
      "first ![a.jpg](/api/upload/a.jpg) then  end",
    );
  });

  test("Escape closes the action sheet without acting on the capture", async () => {
    const window = await loadSite("/issues");
    const item = window.document.querySelector(".capture-item");
    assert.ok(await holdCapture(window, item));
    window.document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.equal(window.document.querySelector(".action-sheet"), null,
      "Escape left the sheet open");
    assert.equal(window.posted.length, 0, "closing the sheet wrote to the server");
  });

  test("saving an emptied field is not a delete", async () => {
    /* Deleting has a button and that button asks. Clearing the box has to
     * do nothing at all, or the confirm is one backspace away from being
     * bypassed. */
    const window = await loadSite("/issues");
    const item = window.document.querySelector(".capture-item");
    click(window, sheetAct(await holdCapture(window, item), "Edit"));
    item.querySelector(".capture-input").value = "   ";
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Save")[0]);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted.length, 0, "an emptied field deleted the capture");
  });

  test("a capture that moved under him says so instead of silently doing nothing", async () => {
    const window = await loadSite("/issues");
    window.confirm = () => true;
    window.postReply = { ok: false, message: "that capture is no longer in the list" };
    const item = window.document.querySelector(".capture-item");
    click(window, sheetAct(await holdCapture(window, item), "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.match(item.querySelector(".capture-item-status").textContent, /no longer/);
  });

  test("my own notes are a separate tab, newest first, with a pager", async () => {
    const window = await loadSite("/issues");
    const tab = [...window.document.querySelectorAll(".tab")]
      .filter((one) => /Nova's/.test(one.textContent))[0];
    click(window, tab);

    const notes = [...window.document.querySelectorAll(".note")];
    assert.equal(notes.length, 2, "the notes window was not the one the server sent");
    assert.match(notes[0].textContent, /vault_tool\.py get. does NOT truncate/);
    assert.equal(notes[0].querySelector(".note-cycle").getAttribute("href"), "/cycle/63");
    assert.ok(
      window.document.querySelector("button.more"),
      "3 notes exist and 2 were sent, so there should be a pager",
    );
    assert.equal(rows(window).length, 0, "his rows are still rendered on my tab");
  });

  test("the journal poll does not render over a board page", async () => {
    const server = { asked: [] };
    let timers;
    const window = await loadSite("/issues", {
      board: (url) => {
        server.asked.push(url);
        return url.includes("item=") ? payload.boardItem : payload.board;
      },
      journal: (url) => {
        server.asked.push(url);
        return payload.journal;
      },
      install: (w) => { timers = captureTimers(w); },
    });
    await timers.firePagePoll();

    assert.ok(
      server.asked.every((url) => url.includes("/api/board")),
      "the poll fetched the journal from a board page: " + server.asked.join(", "),
    );
    assert.ok(rows(window).length > 0, "the poll replaced the board with the feed");
  });

  test("tapping Ideas in the nav switches board without a reload", async () => {
    const asked = [];
    const window = await loadSite("/issues", {
      board: (url) => {
        asked.push(url);
        return url.includes("item=") ? payload.boardItem : payload.board;
      },
    });
    click(window, window.document.querySelector(".nav-tab[href='/ideas']"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.location.pathname, "/ideas");
    assert.ok(asked.some((url) => url.includes("name=ideas")), "asked: " + asked.join(", "));
    assert.ok(window.document.querySelector(".nav-tab[href='/ideas']").classList.contains("on"));
    assert.ok(!window.document.querySelector(".nav-tab[href='/issues']").classList.contains("on"));
  });

  test("the journal still loads at / with the nav in place", async () => {
    const window = await loadSite("/");
    assert.ok(cards(window).length > 0, "the feed stopped rendering");
    assert.equal(rows(window).length, 0);
    assert.ok(window.document.querySelector(".nav-tab[href='/']").classList.contains("on"));
  });
});

/* The sidebar. The owner, issues.md 2026-08-11: "Move the Journal, issues &
 * ideas tabs buttons to a sidebar that opens from a hamburger button that
 * is placed at the top right of the Nova page on the same horizontal line
 * as the Nova header. Add slide animations."
 *
 * jsdom applies no stylesheet here, so none of these can see the slide --
 * the animation is CSS and is verified by reading it, which these tests
 * deliberately do not claim to cover. What they do cover is the part that
 * is real code: whether the button, the scrim, the Escape key and a tap
 * on a link all agree about one boolean, and whether the drawer survives
 * the header re-render that happens on every poll. */
describe("the sidebar", () => {
  const btn = (window) => window.document.getElementById("menu-btn");
  const drawer = (window) => window.document.getElementById("nav");
  const scrim = (window) => window.document.getElementById("scrim");

  test("the nav starts closed and hidden from the tab order", async () => {
    const window = await loadSite("/");
    assert.ok(!drawer(window).classList.contains("open"));
    assert.equal(drawer(window).getAttribute("aria-hidden"), "true");
    assert.equal(btn(window).getAttribute("aria-expanded"), "false");
    assert.ok(!window.document.body.classList.contains("nav-open"));
  });

  test("the hamburger opens it, and opens it again after closing", async () => {
    const window = await loadSite("/");
    click(window, btn(window));
    assert.ok(drawer(window).classList.contains("open"));
    assert.ok(scrim(window).classList.contains("open"));
    assert.ok(btn(window).classList.contains("open"), "the bars never became a cross");
    assert.equal(drawer(window).getAttribute("aria-hidden"), "false");
    assert.equal(btn(window).getAttribute("aria-expanded"), "true");
    assert.equal(btn(window).getAttribute("aria-label"), "Close menu");
    assert.ok(window.document.body.classList.contains("nav-open"),
      "the page can still scroll under the open drawer");

    click(window, btn(window));
    assert.ok(!drawer(window).classList.contains("open"), "the button does not toggle back");
    assert.equal(btn(window).getAttribute("aria-label"), "Open menu");

    click(window, btn(window));
    assert.ok(drawer(window).classList.contains("open"), "it only opened once");
  });

  test("tapping the scrim closes it", async () => {
    const window = await loadSite("/");
    click(window, btn(window));
    click(window, scrim(window));
    assert.ok(!drawer(window).classList.contains("open"));
    assert.ok(!scrim(window).classList.contains("open"));
    assert.ok(!window.document.body.classList.contains("nav-open"));
  });

  test("Escape closes it", async () => {
    const window = await loadSite("/");
    click(window, btn(window));
    window.document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    assert.ok(!drawer(window).classList.contains("open"));
  });

  /* The links did not change when they moved into the drawer, so routing
   * is meant to be untouched -- but a drawer that stays open over the page
   * it just navigated to is the classic way to get this half right. */
  test("tapping a link routes and closes the drawer behind it", async () => {
    const window = await loadSite("/");
    click(window, btn(window));
    click(window, window.document.querySelector(".nav-tab[href='/issues']"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.location.pathname, "/issues");
    assert.ok(rows(window).length > 0, "the board did not render");
    assert.ok(!drawer(window).classList.contains("open"), "the drawer stayed open over the board");
    assert.ok(!window.document.body.classList.contains("nav-open"),
      "the body was left unable to scroll");
  });

  /* The header is cleared and rebuilt on every render, and the button now
   * lives in the page beside it. A blanket `textContent = ""` at the wrong
   * scope removes the button along with the status line, and the drawer
   * becomes unopenable -- on the journal from the first paint, and on a
   * board page from the moment the board's own header lands. */
  test("the hamburger survives a journal render", async () => {
    const window = await loadSite("/");
    assert.ok(window.document.querySelector(".status-line"), "the header never rendered");
    assert.ok(btn(window), "the render took the menu button with it");
    click(window, btn(window));
    assert.ok(drawer(window).classList.contains("open"));
  });

  test("the hamburger survives a board render", async () => {
    const window = await loadSite("/issues");
    assert.ok(window.document.querySelector(".status-line"), "the board header never rendered");
    assert.ok(btn(window), "the board render took the menu button with it");
    click(window, btn(window));
    assert.ok(drawer(window).classList.contains("open"));
  });

  /* The href list on its own passes with or without this change -- the
   * three anchors were already children of `#nav` before it, which is the
   * point of the move being as small as it is. So it is asserted together
   * with the thing the move did create: closed, the drawer is off-screen
   * and its links are out of the tab order, which is the whole difference
   * between a drawer and the row of tabs it replaced. `aria-hidden` is the
   * half of that a DOM test can see; the `visibility` half is CSS. */
  test("every section lives in the drawer, and is exposed only with it", async () => {
    const window = await loadSite("/");
    const hrefs = [...drawer(window).querySelectorAll(".nav-tab")].map((a) => a.getAttribute("href"));
    assert.deepEqual(hrefs, ["/", "/issues", "/ideas", "/notes", "/pool", "/costs", "/retro", "/plan", "/ask", "/diag"]);

    assert.equal(drawer(window).getAttribute("aria-hidden"), "true");
    click(window, btn(window));
    assert.equal(drawer(window).getAttribute("aria-hidden"), "false");
  });

  /* jsdom applies no stylesheet, so nothing above this can see the slide.
   * This is the one CSS claim that is worth a mechanical check rather than
   * a reading, because it is the one that was wrong: a media query adds no
   * specificity, so a reduced-motion rule listing only `.nav` loses to
   * `.nav.open` and suppresses the closing animation while leaving the
   * opening one at full length. Reviewer caught it; this stops it coming
   * back. Parsing the real sheet, not grepping it -- a dropped or
   * malformed rule fails here rather than reading as present. */
  test("reduced motion reaches the open states, not just the closed ones", () => {
    const css = readFileSync(join(publicDir, "style.css"), "utf8");
    const { window } = openWindow("<style>" + css + "</style>");
    const rules = [...window.document.styleSheets[0].cssRules];

    const reduced = rules.filter(
      (r) => r.media && /prefers-reduced-motion/.test(r.media.mediaText),
    );
    assert.equal(reduced.length, 1, "expected exactly one reduced-motion block");

    const targeted = new Set();
    for (const inner of reduced[0].cssRules) {
      for (const one of inner.selectorText.split(",")) targeted.add(one.trim());
    }
    // Both directions of both animated elements.
    for (const sel of [".nav", ".nav.open", ".scrim", ".scrim.open"]) {
      assert.ok(targeted.has(sel), `reduced motion does not cover ${sel}`);
    }
  });
});

/* Two taps in quick succession leave two fetches in flight. Before there
 * were three views to land on, whichever resolved last simply won. */
describe("navigating away mid-fetch", () => {
  test("a slow board response does not paint over the page you moved to", async () => {
    let release;
    const held = new Promise((resolve) => { release = resolve; });
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("name=ideas")
        ? { ...payload.board, items: [], notes: [], notesTotal: 0 }
        : payload.board),
    });
    // Hold the Issues response open, tap Ideas, then let Issues land last.
    const realFetch = window.fetch;
    window.fetch = (url, init) => (url.includes("name=issues")
      ? held.then(() => realFetch(url, init))
      : realFetch(url, init));

    click(window, window.document.querySelector(".nav-tab[href='/issues']"));
    click(window, window.document.querySelector(".nav-tab[href='/ideas']"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    release();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.location.pathname, "/ideas");
    assert.equal(rows(window).length, 0, "the stale Issues response painted over Ideas");
  });
});

/* the owner, issues.md #83: "Make the header for issues and ideas bold". The
 * whole line was one dim string, so the page you were on read as part of
 * the tally after it. Two assertions, because either alone passes on its
 * own: the name has to be in the bold element, and the counts have to have
 * stayed out of it. */
describe("the board header", () => {
  const head = (window) => window.document.querySelector(".status-line");

  test("the page name is bold and the counts are not", async () => {
    const window = await loadSite("/issues");
    const strong = head(window).querySelector("strong.status-page");
    assert.ok(strong, "the page name is not in a bold element");
    assert.equal(strong.textContent, "Issues");
    assert.ok(
      !/open|done|notes/.test(strong.textContent),
      "the tally was bolded along with the name",
    );
    assert.match(head(window).textContent, /^Issues — \d+ open, /);
  });

  test("the ideas page names itself too", async () => {
    const window = await loadSite("/ideas");
    assert.equal(
      window.document.querySelector(".status-line strong.status-page").textContent,
      "Ideas",
    );
  });
});

/* ---- The costs page (issues.md #57, page 2) ------------------------------
 *
 * Two charts of hand-written SVG, so what is worth pinning here is not
 * that they render -- it is the handful of decisions inside them that a
 * later change could quietly undo while leaving a chart on screen. Each
 * test below is one of those, and each one is a mistake this data would
 * actually produce: a hole read as a zero, a bad timestamp placed at the
 * epoch, identity carried by colour alone.
 */
describe("the costs page", () => {
  /* Since 2026-08-20 the drawing belongs to ECharts, which paints to a
   * canvas jsdom does not implement -- so there is no mark in the DOM to
   * count. `figure.chartOption` is the description this app hands the
   * library, set synchronously by `mountEChart`, and it is now the whole
   * of what this app decides about a chart. Asserting on it tests the
   * app's judgement; counting rects used to test arithmetic that has
   * since been deleted along with the rects. */
  const optionOf = (window, index = 0) =>
    window.document.querySelectorAll(".chart")[index].chartOption;

  test("it plots the cycles and the quota, not the journal", async () => {
    const window = await loadSite("/costs");
    const charts = window.document.querySelectorAll(".chart");
    assert.equal(charts.length, 2);
    // One bar per cycle in the ledger, and the fixture has three.
    const cycles = optionOf(window, 0);
    assert.equal(cycles.series.length, 1);
    assert.equal(cycles.series[0].type, "bar");
    assert.equal(cycles.series[0].data.length, 3);
    // Two series, two lines.
    const quota = optionOf(window, 1);
    assert.equal(quota.series.length, 2);
    quota.series.forEach((line) => assert.equal(line.type, "line"));
  });

  test("the nav marks Costs, and the journal poll does not paint over it", async () => {
    const window = await loadSite("/costs");
    const on = [...window.document.querySelectorAll(".nav-tab")]
      .filter((a) => a.getAttribute("aria-current") === "page")
      .map((a) => a.getAttribute("href"));
    assert.deepEqual(on, ["/costs"]);
    assert.equal(window.document.querySelector(".entry"), null);
  });

  test("a reading from before `pace` existed leaves a hole, not a drop to zero", async () => {
    /* The fixture's oldest quota row has no pace and, more to the point,
     * this is the shape the real ledger has for its first two days. Read
     * as zero the line would dive to the axis and back; the path has to
     * stop and start again instead. `M` twice in one `d` is exactly that
     * -- and a single `M` would be the bug. */
    const window = await loadSite("/costs");
    const holes = optionOf(window, 1).series.map(
      (line) => line.data.filter(([, y]) => y === null).length);
    assert.deepEqual([...holes], [0, 0], "the fixture's readings are contiguous");

    const holed = {
      ...payload.costs,
      quota: [
        [1786227966684, 27.0, null, 2.0, null],
        [1786420000000, null, null, 44.0, 0.58],
        [1786450678872, 78.0, 0.944, 51.0, 0.615],
      ],
    };
    const broken = await loadSite("/costs", { costs: holed });
    const fiveHour = optionOf(broken, 1).series[0];
    // A null y is what ECharts draws as a gap. A zero here would be the
    // bug -- the line would dive to the axis and back, reading as a quota
    // that emptied and refilled.
    assert.deepEqual([...fiveHour.data].map(([, y]) => y), [27.0, null, 78.0]);
    assert.equal(fiveHour.connectNulls, false,
      "connectNulls must stay off or the gap is drawn through");
  });

  test("two series get a legend, so identity is never colour alone", async () => {
    const window = await loadSite("/costs");
    const labels = [...window.document.querySelectorAll(".legend-label")].map((n) => n.textContent);
    assert.deepEqual(labels, ["5-hour window", "7-day window"]);
  });

  test("a vault with no ledger is an empty page, not a broken one", async () => {
    const window = await loadSite("/costs", {
      costs: {
        generatedAt: null, cycleColumns: [], quotaColumns: [],
        cycles: [], quota: [], summary: {}, weights: {},
      },
    });
    assert.equal(window.document.querySelectorAll(".chart").length, 2);
    // No option is built at all when there is nothing to draw, so the
    // empty state cannot be a chart that quietly drew zero marks.
    assert.equal(optionOf(window, 0), undefined);
    assert.equal(optionOf(window, 1), undefined);
    const empties = [...window.document.querySelectorAll(".empty")].map((n) => n.textContent);
    assert.deepEqual(empties, ["No cycles in the ledger yet.", "No quota readings yet."]);
  });

  test("a failed fetch says so rather than leaving the last page up", async () => {
    const window = await loadSite("/", { install: (w) => { w.__x = 1; } });
    window.fetch = () => Promise.reject(new Error("costs are down"));
    window.history.pushState(null, "", "/costs");
    click(window, [...window.document.querySelectorAll(".nav-tab")].find(
      (a) => a.getAttribute("href") === "/costs"));
    await new Promise((r) => setTimeout(r, 0));
    assert.match(window.document.querySelector(".empty").textContent, /Could not load the costs/);
  });
});

/* ---- Zoom, pan, selection and full screen on a chart ---------------------
 *
 * the owner, 2026-08-20, on the version this replaces: "The zoom works, but
 * it does not give me any more granulation in the graph, it just makes the
 * graph bars larger. I want actual graph zoom as in expanding the values
 * on the x/y axis and showing more granularity. Also the hover effect when
 * i press the graph only works for a split second. I should be able to
 * select stuff, move around."
 *
 * The old suite here had fifteen tests of a CSS `transform: scale()` and
 * its pinch arithmetic. That mechanism is gone -- it magnified a picture
 * and could not add a tick to an axis, which is exactly what he is
 * describing -- so those tests went with it rather than being ported.
 * Deleting a test whose subject no longer exists is not a loss of
 * coverage; keeping it green against a shim would have been.
 *
 * What is worth pinning now is the description this app hands ECharts,
 * because that description is the whole of what this app decides. Each
 * test below names the sentence of his it answers.
 */
describe("a chart can be zoomed, panned, selected and opened full screen", () => {
  const charts = (window) => [...window.document.querySelectorAll(".chart")];
  const option = (window, index = 0) => charts(window)[index].chartOption;

  test("every chart on the page gets a full-screen control, not just the first", async () => {
    const window = await loadSite("/costs");
    assert.equal(charts(window).length, 2);
    charts(window).forEach((figure) => {
      assert.ok(figure.querySelector(".chart-tool-full"), "no full-screen control");
    });
    // The two buttons must not announce identically -- the control has to
    // say what it acts on, which is the same rule as a bare priority glyph.
    const labels = charts(window).map(
      (f) => f.querySelector(".chart-tool-full").getAttribute("aria-label"));
    assert.deepEqual(labels, [
      "Full screen: What a cycle costs",
      "Full screen: How much quota is left",
    ]);
  });

  test('"expanding the values on the x/y axis": zoom re-scales the axis, not the picture', async () => {
    const window = await loadSite("/costs");
    const zooms = option(window).dataZoom;
    // Pinch and drag-pan on the axis itself...
    const inside = zooms.filter((z) => z.type === "inside");
    assert.ok(inside.some((z) => z.xAxisIndex === 0), "no x-axis zoom");
    assert.ok(inside.some((z) => z.yAxisIndex === 0), "no y-axis zoom");
    // ...and a slider under it, so there is a visible handle too.
    assert.ok(zooms.some((z) => z.type === "slider"), "no zoom slider");
    // The axis is a real time scale, which is what makes zooming in add
    // ticks. A category axis would give back the old behaviour.
    assert.equal(option(window).xAxis.type, "time");
    // Marks outside the window stay drawn rather than being dropped, so
    // panning never blanks a line.
    zooms.forEach((z) => assert.equal(z.filterMode, "none"));
  });

  test('"I should be able to select stuff": a rubber-band selection zooms', async () => {
    const window = await loadSite("/costs");
    const features = option(window).toolbox.feature;
    assert.ok(features.dataZoom, "no select-to-zoom");
    assert.ok(features.restore, "no way back to the whole picture");
  });

  test('"only works for a split second": a tap pins the readout', async () => {
    const window = await loadSite("/costs");
    const tip = option(window).tooltip;
    assert.equal(tip.trigger, "axis");
    // `mousemove` alone is hover-only, which on a touch screen lasts
    // exactly as long as the finger is down. `click` is what makes a tap
    // leave it up.
    assert.match(tip.triggerOn, /click/);
    assert.equal(tip.confine, true, "the readout may not spill off a phone");
  });

  test("the readout names its series and never leans on colour alone", async () => {
    const window = await loadSite("/costs");
    const html = option(window, 1).tooltip.formatter([
      { value: [1786450678872, 78], seriesName: "5-hour window", color: "#5d86dd" },
      { value: [1786450678872, null], seriesName: "7-day window", color: "#bd8b2f" },
    ]);
    assert.match(html, /5-hour window/);
    assert.match(html, /78%/);
    // A reading that is missing says so, rather than printing 0%.
    assert.match(html, /7-day window/);
    assert.match(html, /—/);
  });

  test("a value that could carry markup is escaped, not interpolated", async () => {
    // The formatter builds HTML, which nothing else in this file does.
    // That is the library's interface and not a choice, so the escaping
    // has to be the thing that is checked.
    const window = await loadSite("/costs");
    const html = option(window, 1).tooltip.formatter([
      { value: [1786450678872, 78], seriesName: "<img src=x onerror=1>", color: "#5d86dd" },
    ]);
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
  });

  test("full screen toggles the overlay and the button says how to leave", async () => {
    const window = await loadSite("/costs");
    const figure = charts(window)[0];
    const button = figure.querySelector(".chart-tool-full");
    click(window, button);
    assert.ok(figure.classList.contains("chart-full"));
    assert.ok(window.document.body.classList.contains("has-full-chart"));
    assert.equal(button.textContent, "Close");
    click(window, button);
    assert.ok(!figure.classList.contains("chart-full"));
    assert.ok(!window.document.body.classList.contains("has-full-chart"));
    assert.equal(button.textContent, "Full screen");
  });

  test("opening one chart full screen closes the other", async () => {
    const window = await loadSite("/costs");
    const [first, second] = charts(window);
    click(window, first.querySelector(".chart-tool-full"));
    click(window, second.querySelector(".chart-tool-full"));
    assert.ok(!first.classList.contains("chart-full"), "two overlays at once");
    assert.ok(second.classList.contains("chart-full"));
  });

  test("Escape leaves full screen, since a phone has a back reflex and a desktop has a key", async () => {
    const window = await loadSite("/costs");
    const figure = charts(window)[0];
    click(window, figure.querySelector(".chart-tool-full"));
    window.document.dispatchEvent(
      new window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    assert.ok(!figure.classList.contains("chart-full"));
  });

  test("the gestures are named in words, because nothing else says they exist", async () => {
    const window = await loadSite("/costs");
    const hint = charts(window)[0].querySelector(".chart-tools-hint").textContent;
    assert.match(hint, /zoom/i);
    assert.match(hint, /pan/i);
  });

  /* The costs page is two full-width charts stacked, and ECharts' pan
   * handler calls preventDefault on the event it is given -- a touchmove,
   * on a phone. Left at the library's default of on, a finger that lands
   * on a chart pans the chart and the page underneath does not scroll,
   * which is most of the page. So the app decides it per pointer rather
   * than inheriting it, and this pins the decision being made at all: an
   * `undefined` here passes every other test in this file and is the bug.
   */
  test("drag-to-pan is a mouse behaviour, so a finger can still scroll the page", async () => {
    const window = await loadSite("/costs");
    const inside = option(window).dataZoom.filter((z) => z.type === "inside");
    assert.ok(inside.length, "no inside zoom to decide about");
    inside.forEach((z) => assert.equal(
      typeof z.moveOnMouseMove, "boolean", "drag-pan left to the library's default"));
    // jsdom answers `(pointer: coarse)` with no, so this window is the
    // mouse case -- where drag-to-pan is on, and the hint says so.
    inside.forEach((z) => assert.equal(z.moveOnMouseMove, true));
    assert.match(
      charts(window)[0].querySelector(".chart-tools-hint").textContent, /drag to pan/i);
  });

});

/* A cycle that ran and wrote nothing, marked where it happened -- the
 * display half of the owner's #72. He found cycles 127 and 128 himself by
 * noticing the feed jump from 126 to 129, so the gap goes back exactly
 * where he was already looking. The committed fixture carries a real one:
 * cycles 57 and 55, with 56 missing, and an unnumbered entry of his own
 * below them. */
describe("a hole in the record is visible in the feed", () => {
  const gaps = (window) => [...window.document.querySelectorAll(".cycle-gap")];

  test("the missing cycle is named between the two cards that bracket it", async () => {
    const window = await loadSite("/");
    assert.equal(gaps(window).length, 1);
    assert.match(gaps(window)[0].textContent, /Cycle 56 ran and wrote no entry/);
  });

  test("it sits between the cards, not at the top of the feed", async () => {
    const window = await loadSite("/");
    const feed = window.document.getElementById("feed");
    const kids = [...feed.children];
    const gap = kids.findIndex((n) => n.classList.contains("cycle-gap"));
    // Card for 57 above it, card for 55 below it.
    assert.ok(gap > 0, "the gap rendered before the newer card");
    assert.ok(kids[gap - 1].classList.contains("entry"));
    assert.ok(kids[gap + 1].classList.contains("entry"));
  });

  test("a journal with no holes shows no marker at all", async () => {
    const clean = JSON.parse(JSON.stringify(payload.journal));
    clean.status.missingCycles = [];
    const window = await loadSite("/", { journal: () => clean });
    assert.equal(gaps(window).length, 0);
  });

  /* The client does its own arithmetic between two adjacent cards, so it
   * has to be told which numbers count. The owner's own notes carry no cycle
   * number and sit in the feed between numbered entries -- filling in
   * every number between two cards would invent a gap out of a note, and
   * the server is the only one that knows the difference. */
  test("it marks only what the server called missing", async () => {
    const lying = JSON.parse(JSON.stringify(payload.journal));
    lying.status.missingCycles = [];
    lying.entries = lying.entries.filter((e) => e.cycle !== 56);
    const window = await loadSite("/", { journal: () => lying });
    assert.equal(gaps(window).length, 0);
  });

  /* The feed is not sorted by cycle number. A card takes the position of
   * its cycle's newest part, so an addendum written after the next cycle
   * has already filed carries its whole card back up the page, above a
   * higher number. Reading the hole off the previous card then announced
   * it above a card that is newer than the hole -- which says the cycle in
   * that card never ran, while its own entry sits directly underneath. */
  const reordered = (cyclesInWireOrder, missingCycles) => {
    const base = payload.journal.entries.find((e) => typeof e.cycle === "number");
    const j = JSON.parse(JSON.stringify(payload.journal));
    j.entries = cyclesInWireOrder.map((cycle) => {
      const entry = JSON.parse(JSON.stringify(base));
      entry.cycle = cycle;
      return entry;
    });
    j.status.missingCycles = missingCycles;
    return j;
  };
  const positions = (window) =>
    [...window.document.getElementById("feed").children];

  test("an addendum out of order does not strand the hole above a newer card",
    async () => {
      // Cycle 54 wrote an addendum during 57, so its card sits above 56's.
      const window = await loadSite("/",
        { journal: () => reordered([57, 54, 56, 54], [55]) });
      assert.equal(gaps(window).length, 1);
      assert.match(gaps(window)[0].textContent, /Cycle 55 ran and wrote no entry/);
      const kids = positions(window);
      const gap = kids.findIndex((n) => n.classList.contains("cycle-gap"));
      const newer = kids.findIndex((n) => n.id === "cycle-56");
      assert.ok(newer >= 0, "cycle 56 has a card");
      assert.ok(gap > newer,
        "the hole was drawn above cycle 56, which is newer than the hole");
    });

  test("the hole is drawn once, under the oldest card newer than it",
    async () => {
      const window = await loadSite("/",
        { journal: () => reordered([59, 55, 58, 55], [56, 57]) });
      assert.equal(gaps(window).length, 1);
      assert.match(gaps(window)[0].textContent,
        /Cycles 56, 57 ran and wrote no entry/);
      const kids = positions(window);
      const gap = kids.findIndex((n) => n.classList.contains("cycle-gap"));
      assert.equal(kids[gap - 1].id, "cycle-58");
    });

  /* One addendum can leapfrog any number of newer cards, not just one --
   * the sequence number is simply the next one free when the addendum is
   * written. Anchoring on the numerically smallest card newer than the
   * hole would put the marker back above two cards that are newer than
   * it; the last such card in the feed is the one that holds. */
  test("a hole never lands above a card newer than it, however scrambled",
    async () => {
      const window = await loadSite("/",
        { journal: () => reordered([57, 62, 60, 50], [55]) });
      assert.equal(gaps(window).length, 1);
      const kids = positions(window);
      const gap = kids.findIndex((n) => n.classList.contains("cycle-gap"));
      for (const id of ["cycle-57", "cycle-62", "cycle-60"]) {
        const at = kids.findIndex((n) => n.id === id);
        assert.ok(at >= 0 && at < gap, id + " is newer than the hole and sits below it");
      }
      assert.equal(kids[gap + 1].id, "cycle-50");
    });

  test("a hole older than everything loaded is left for the older page",
    async () => {
      const window = await loadSite("/",
        { journal: () => reordered([57, 56], [55]) });
      assert.equal(gaps(window).length, 0);
    });

  test("a hole newer than everything loaded is not pinned to the top",
    async () => {
      const window = await loadSite("/",
        { journal: () => reordered([57, 56], [58]) });
      assert.equal(gaps(window).length, 0);
    });
});

/* The other half of issue #81, and the half that could silently do nothing.
 *
 * The page's check for a replayed payload is worth exactly as much as the
 * worker's stamp is real: if `sw.js` does not set the header, the client
 * flag never fires and the fix is a guard guarding nothing, with every
 * test above it still green. So the worker is run for real -- its source
 * evaluated against stubs for the worker globals -- and asked what the
 * page would actually receive. */
describe("the service worker says so when it answers from its cache", () => {
  function runFetchHandler({ networkFails, cached }) {
    const listeners = {};
    const workerSelf = {
      addEventListener: (name, fn) => { listeners[name] = fn; },
      location: { origin: "https://nova.example" },
      skipWaiting: () => {},
      clients: { claim: () => {} },
    };
    const cacheStub = {
      open: () => Promise.resolve({ addAll: () => Promise.resolve(), put: () => Promise.resolve() }),
      keys: () => Promise.resolve([]),
      match: () => Promise.resolve(cached),
    };
    const fetchStub = networkFails
      ? () => Promise.reject(new TypeError("Failed to fetch"))
      : () => Promise.resolve(new Response('{"live":true}', { status: 200 }));
    const source = readFileSync(join(publicDir, "sw.js"), "utf8");
    new Function("self", "caches", "fetch", "Headers", "Response", "URL", source)(
      workerSelf, cacheStub, fetchStub, Headers, Response, URL);

    let answered;
    listeners.fetch({
      request: { method: "GET", url: "https://nova.example/api/journal?limit=20", mode: "cors" },
      respondWith: (p) => { answered = p; },
    });
    return answered;
  }

  test("a cache hit is stamped, and still carries the body it cached", async () => {
    const response = await runFetchHandler({
      networkFails: true,
      cached: new Response('{"cached":true}', { status: 200 }),
    });
    assert.equal(response.headers.get("X-Nova-Replayed"), "1");
    assert.equal(response.status, 200);
    assert.equal(await response.text(), '{"cached":true}');
  });

  test("a live answer is not stamped", async () => {
    const response = await runFetchHandler({ networkFails: false, cached: null });
    assert.equal(response.headers.get("X-Nova-Replayed"), null);
    assert.equal(await response.text(), '{"live":true}');
  });

  /* Offline with nothing cached: the worker has nothing to hand back and
   * must say so by resolving to undefined, which is what turns into the
   * page's own "can't reach Nova" path. Stamping must not invent a
   * response out of a miss. */
  test("a cache miss stays a miss", async () => {
    assert.equal(await runFetchHandler({ networkFails: true, cached: undefined }), undefined);
  });
});

describe("a cycle that is running says so in the header", () => {
  /* The other half of #72. The header names the newest cycle that has
   * *written*, so for the first 20-45 minutes of every hour it names one
   * behind the cycle actually running -- which is what the owner reported as
   * a failure. The server decides; these assert the page renders that
   * decision and, more importantly, that it never renders it beside the
   * badge that contradicts it. */
  const live = (window) =>
    [...window.document.querySelectorAll("#status .badge-live")]
      .map((n) => n.textContent);
  const warn = (window) =>
    [...window.document.querySelectorAll("#status .badge-warn")]
      .map((n) => n.textContent);

  const withStatus = (extra) => {
    const copy = JSON.parse(JSON.stringify(payload.journal));
    Object.assign(copy.status, { recentMissingCycles: [] }, extra);
    return copy;
  };

  test("a cycle in flight is named on the page", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: false, silentIntervals: 0 }),
    });
    assert.deepEqual(live(window), ["cycle running"]);
  });

  /* The state the owner is looking at almost every time he opens the app: a
   * cycle finished, the next has not woken. The badge must be absent, not
   * merely worded differently -- a badge that is always up is a badge
   * nobody reads, which is the objection this whole header is built
   * around. */
  test("nothing is said between cycles", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: false, stalled: false, silentIntervals: 1 }),
    });
    assert.deepEqual(live(window), []);
    assert.deepEqual(warn(window), []);
  });

  /* The server already refuses to emit the pair, and this is the second
   * lock on the same door: if a future change ever lets both through, the
   * page would tell him the loop is working and has been dead for four
   * hours, in two lines a centimetre apart. */
  /* Its second assertion used to be `warn(...)` containing "no entry for 4
   * hours" — the stall badge, which doubled as this test's positive
   * control: it proved the stalled fixture had actually reached the page,
   * so the empty `live(...)` meant something. Removing that badge took the
   * control with it, and `assert.deepEqual(live(window), [])` alone would
   * pass against a page that never renders a live badge under any input.
   *
   * So the control is rebuilt from the badge he kept: the same fixture
   * with `stalled: false` must produce "cycle running". One test, both
   * directions, and it fails if either the flag stops suppressing or the
   * badge stops rendering. */
  test("a stalled loop is never also reported as running", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: true, silentIntervals: 4 }),
    });
    assert.deepEqual(live(window), []);

    const healthy = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: false, silentIntervals: 1 }),
    });
    assert.ok(live(healthy).some((t) => t === "cycle running"),
      "the control failed: this fixture cannot raise the live badge either, "
      + "so the assertion above proves nothing about `stalled`");
  });

  /* Same reason the stall badge is hidden on a replayed payload: "a cycle
   * is running" is a claim about right now, and a copy served out of the
   * service worker's cache after a failed fetch cannot make it. A phone
   * off the tailnet would otherwise be told a cycle was running for as
   * long as it stayed offline. */
  test("a saved copy does not claim a cycle is running", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: false, replayed: true }),
    });
    assert.deepEqual(live(window), []);
  });
});

describe("an ask nobody answered is named in the header", () => {
  /* An ask lives on the journal card that raised it, and the card scrolls
   * off the feed while the question stays open. #94's waited a day on card
   * 247 -- by then fourteen cards down -- while the board row it blocks sat
   * at the top of the owner's board and three cycles in a row skipped it. The
   * card was the right home for the ask; nothing was the home for "this one
   * is still waiting". */
  const pill = (window) => window.document.querySelector("#status .badge-ask");
  /* The href moved off the badge and onto the field around it when the
   * status fields became one horizontal, clickable list (the owner's capture,
   * 2026-08-22): the whole field is the link now, so the badge inside it
   * had to stop being one -- an `<a>` inside an `<a>` is unnested by the
   * parser. Same destination, bigger tap target. */
  const askLink = (window) => {
    const found = pill(window);
    return found && found.closest("a.status-sub");
  };
  const head = (window) => window.document.getElementById("status");

  const withAsks = (asks, extra) => {
    const copy = JSON.parse(JSON.stringify(payload.journal));
    Object.assign(copy.status, { recentMissingCycles: [], asks }, extra || {});
    return copy;
  };


  test("an unanswered ask links to the card it lives on", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([{ cycle: 247, date: "2026-08-16", time: "21:20" }]),
      comments: { byCycle: {}, needs: [] },
    });
    const found = pill(window);
    assert.ok(found, "expected a waiting-on-you pill");
    assert.equal(askLink(window).getAttribute("href"), "/cycle/247");
    assert.match(window.document.getElementById("status").textContent, /cycle 247/);
  });

  /* The longest wait is the one worth surfacing: a fresh ask is on a card
   * he can still see. Taking the first of the list instead would name an
   * ask -- plausible, wrong, and untestable by eye. */
  test("the oldest open ask wins", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([
        { cycle: 260, date: "2026-08-17", time: "10:00" },
        { cycle: 247, date: "2026-08-16", time: "21:20" },
      ]),
      comments: { byCycle: {}, needs: [] },
    });
    assert.equal(askLink(window).getAttribute("href"), "/cycle/247");
  });

  /* A comment on the card is the answer, so the pill has to move on to the
   * next one still waiting rather than staying on the one he just replied
   * to. This is the assertion that makes the feature capable of stopping. */
  test("a card he has replied to is not still waiting", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([
        { cycle: 260, date: "2026-08-17", time: "10:00" },
        { cycle: 247, date: "2026-08-16", time: "21:20" },
      ]),
      comments: {
        byCycle: { 247: [{ stamp: "2026-08-17 07:00", text: "answered" }] },
        needs: [],
      },
    });
    assert.equal(askLink(window).getAttribute("href"), "/cycle/260");
  });

  test("nothing is said when every ask has an answer", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([{ cycle: 247, date: "2026-08-16", time: "21:20" }]),
      comments: {
        byCycle: { 247: [{ stamp: "2026-08-17 07:00", text: "answered" }] },
        needs: [],
      },
    });
    assert.equal(pill(window), null);
  });

  test("a journal with no asks says nothing", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([]),
      comments: { byCycle: {}, needs: [] },
    });
    assert.equal(pill(window), null);
  });

  /* `/api/comments` is tolerated when it fails -- it resolves to null and
   * costs the bubbles, not the feed. The header must not read that as "he
   * has replied to nothing" and raise the pill on every open ask: it would
   * be a claim about what he has done, drawn from a payload that never
   * arrived, on the one screen he checks from his phone. */
  test("a comments fetch that failed is not read as no answers", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([{ cycle: 247, date: "2026-08-16", time: "21:20" }]),
      failComments: true,
    });
    assert.equal(pill(window), null);
  });

  /* The recovery path, and it is here because it is where the bug was.
   *
   * Once the page has been offline the poll re-draws the header on its own,
   * without a full re-render, and the only comments it holds at that moment
   * are the ones it just fetched. The first version of this read the poll's
   * `comments` local -- which is that payload already serialised for the
   * change comparison, so `.byCycle` was `undefined`, the header was handed
   * an empty answer set, and the pill went back up on an ask he had already
   * answered. A string is a perfectly good value to read a missing property
   * off, so nothing threw.
   *
   * **The obvious version of this test does not catch it, and I wrote that
   * one first.** Have him answer *during* the outage and the recovering poll
   * sees a changed payload, re-renders the whole page, and the correct
   * comments arrive through `render` a line later -- the bug is real and
   * invisible. So nothing changes here: the answer is already in the payload
   * before the outage, and the recovery draw is the only thing that touches
   * the header. */
  test("coming back online does not put the pill back on an answered ask", async () => {
    let timers;
    const window = await loadSite("/", {
      journal: () => withAsks([{ cycle: 247, date: "2026-08-16", time: "21:20" }]),
      comments: {
        byCycle: { 247: [{ stamp: "2026-08-17 07:00", text: "answered" }] },
        needs: [],
      },
      install: (w) => { timers = captureTimers(w); },
    });
    assert.equal(pill(window), null, "answered, so nothing is waiting");

    const good = window.fetch;
    window.fetch = () => Promise.reject(new Error("network down"));
    await timers.firePagePoll();
    await timers.firePagePoll();
    assert.match(head(window).textContent, /can't reach Nova/);

    window.fetch = good;
    await timers.firePagePoll();
    assert.doesNotMatch(head(window).textContent, /can't reach Nova/);
    assert.equal(pill(window), null);
  });

  /* Same rule as the running and stall badges: "he has not replied" is a
   * claim about now, and a payload replayed out of the service worker's
   * cache cannot support it. The failure it prevents is telling him he owes
   * an answer he gave an hour ago, on the one screen he checks from his
   * phone. */
  test("a saved copy does not claim he owes an answer", async () => {
    const window = await loadSite("/", {
      journal: () => withAsks([{ cycle: 247, date: "2026-08-16", time: "21:20" }],
        { replayed: true }),
      comments: { byCycle: {}, needs: [] },
    });
    assert.equal(pill(window), null);
  });
});

describe("a loop that has gone quiet says so in the header", () => {
  const warn = (window) =>
    [...window.document.querySelectorAll("#status .badge-warn")]
      .map((n) => n.textContent);

  /* Two tests stood here asserting the header is quiet when the loop is
   * healthy. They are gone rather than kept, because with the two
   * journal-health badges removed there is no state left in which the
   * header is *not* quiet, so both would pass against a page that had
   * never rendered a badge in its life. The invariant test below is the
   * one that survives: it asserts the same silence under the conditions
   * that used to break it. */

  /* Issue #81, the badge that appeared and retracted with nothing wrong on
   * the server. `sw.js` is network-first, so it answers a failed fetch out
   * of its cache -- as a `200`, which the page has no way to distinguish
   * from a live answer. The journal etag folds in `silentIntervals`, so the
   * body sitting in that cache during a real stall is one that says
   * `stalled: true`. A phone waking up polls before the tailnet is back,
   * gets that body replayed, and raises a badge about a stall that ended
   * hours ago; the next poll retracts it.
   *
   * The rule these two tests pin: a replayed payload may still show its
   * content, and may not make a claim about *now*. */
  test("a stall badge is not raised from a payload replayed out of the cache", async () => {
    const quiet = JSON.parse(JSON.stringify(payload.journal));
    quiet.status.stalled = true;
    quiet.status.silentIntervals = 4;
    const window = await loadSite("/", { journal: () => quiet, replayed: true });
    assert.deepEqual(warn(window).filter((t) => /no entry for/.test(t)), []);
  });

  test("a replayed payload says it is a saved copy rather than passing as current", async () => {
    const quiet = JSON.parse(JSON.stringify(payload.journal));
    quiet.status.stalled = true;
    quiet.status.silentIntervals = 4;
    const window = await loadSite("/", { journal: () => quiet, replayed: true });
    const header = window.document.querySelector("#status");
    assert.match(header.textContent, /showing a saved copy/);
    assert.match(header.textContent, /as of the last load/);
    // The feed is still drawn: an offline app that shows nothing is worse
    // than one that shows what it has, honestly labelled.
    assert.ok(window.document.querySelectorAll(".entry").length);
  });

  /* The regression my own first draft shipped, caught in review, and the
   * reason this test exists rather than a comment saying to be careful.
   *
   * The mark was written onto the parsed body, and that same object is what
   * `lastPayload` remembers and what the 304 branch hands back. So one blip
   * on a phone latched "can't reach Nova" onto the header for as long as the
   * etag held still -- which, while the loop is quiet, is up to an hour. The
   * banner meant to explain a flash had become a longer one.
   *
   * Recovery also cannot be seen in the version: identical bytes, identical
   * etag, and the only thing that moved is whether they are current. So the
   * poll compares the replay state as well, or it never re-renders to clear
   * it. */
  test("a live poll after a replayed one clears the saved-copy banner", async () => {
    const quiet = JSON.parse(JSON.stringify(payload.journal));
    quiet.status.stalled = false;
    quiet.status.recentMissingCycles = [];

    let replayNext = true;
    let polls = 0;
    let timers;
    const window = await loadSite("/", {
      install: (w) => {
        timers = captureTimers(w);
        w.fetch = (url, init) => {
          if (init && init.method === "POST") return res({ ok: true });
          if (url.includes("/api/comments")) return res(payload.comments);
          if (url.includes("/api/digest")) return res(payload.digest);
          polls += 1;
          if (replayNext) return replayedRes(quiet);
          // The network is back, and the server answers exactly what the
          // etag contract says it answers when nothing has changed.
          return notModified();
        };
      },
    });

    assert.match(window.document.querySelector("#status").textContent,
      /showing a saved copy/, "the first load was replayed and says so");

    replayNext = false;
    assert.equal(await timers.firePagePoll(), 1, "one page poll was scheduled");
    assert.doesNotMatch(window.document.querySelector("#status").textContent,
      /showing a saved copy/, "a reachable server clears it, 304 and all");
    assert.ok(polls >= 2, "the poll actually ran");
  });

  /* The other three surfaces the worker replays.
   *
   * The stamp has been on every same-origin GET since the worker learned to
   * set it, but only `fetchVersioned` read it -- so the journal marked itself
   * and the board, the costs page and the retro page went on rendering a
   * saved copy as fully current. The board is the one that actually costs
   * something: it is the page the owner rates rows on, so an unmarked stale
   * board invites a tap on a row that has already moved.
   *
   * Each of these loads only its own route replayed and leaves the journal
   * live, which is what makes them worth having: the page has to say this
   * about itself, not inherit it from a header the journal drew. */
  test("a replayed board says it is a saved copy", async () => {
    const window = await loadSite("/issues", { replayed: ["/api/board"] });
    const header = window.document.querySelector("#status");
    assert.match(header.textContent, /can't reach Nova/);
    assert.match(header.textContent, /showing a saved copy/);
    // Still drawn, same rule as the feed: what it has, honestly labelled.
    assert.ok(rows(window).length);
  });

  test("a replayed costs page says it is a saved copy", async () => {
    const window = await loadSite("/costs", { replayed: ["/api/costs"] });
    assert.match(window.document.querySelector("#status").textContent,
      /showing a saved copy/);
  });

  test("a replayed retro page says it is a saved copy", async () => {
    const window = await loadSite("/retro", { replayed: ["/api/retro"] });
    assert.match(window.document.querySelector("#status").textContent,
      /showing a saved copy/);
  });

  test("a live board is not marked", async () => {
    const window = await loadSite("/issues");
    assert.doesNotMatch(window.document.querySelector("#status").textContent,
      /saved copy/);
  });

  /* The board redraws itself constantly without going back to the network --
   * search, sort, tab, every row toggle -- all of them re-rendering from the
   * payload the page already holds. Carrying the mark on that payload rather
   * than in a render argument is what keeps it up across all of them, and a
   * phone that is still offline is still reading a saved copy after it sorts
   * a column. */
  test("the saved-copy mark survives a board re-render", async () => {
    const window = await loadSite("/issues", { replayed: ["/api/board"] });
    const before = rows(window);
    click(window, window.document.querySelector(".board-sort-dir"));
    assert.deepEqual(rows(window), before.slice().reverse(),
      "the board did not actually re-render");
    assert.match(window.document.querySelector("#status").textContent,
      /showing a saved copy/);
  });

  /* the owner asked for both journal-health badges to go, capture 2026-08-20:
   * *"I do not like he statuses on the top of Nova. The message 'cycle 265
   * wrote no entry' just stands there forever. Please remove all those
   * statuses as i do not want them."*
   *
   * This is the one test that replaces the five that used to assert those
   * badges appear. It hands the page the strongest possible case for
   * raising one -- the server reporting a stall *and* two recent holes,
   * on a live (not replayed) payload -- and requires the header to stay
   * quiet anyway. Before the badges were removed this failed on both
   * counts, which is what makes it worth having; an assertion that a
   * badge is absent under conditions that never produce one would pin
   * nothing at all. `badge-live` and `badge-error` are deliberately not
   * in scope: "cycle running" is the status he kept, and the can't-read
   * badge is a claim about this page rather than about the loop. */
  test("the header raises no journal-health badge, however bad the record",
    async () => {
      const bad = JSON.parse(JSON.stringify(payload.journal));
      bad.status.stalled = true;
      bad.status.silentIntervals = 4;
      bad.status.recentMissingCycles = [204, 205];
      const window = await loadSite("/", { journal: () => bad });
      assert.deepEqual(warn(window), []);
    });

  /* The server saying it cannot see the journal, rather than guessing
   * that the loop stopped. Asserted as an error badge and not a warning:
   * it is the same failure as "can't reach Nova" one hop further back,
   * and the header already spends `badge-warn` on two things that are
   * claims about the loop rather than about this process. */
  test("a record the site cannot refresh says so instead of crying stall", async () => {
    const frozen = JSON.parse(JSON.stringify(payload.journal));
    frozen.status.stalled = false;
    frozen.status.silentIntervals = 9;
    frozen.status.recentMissingCycles = [];
    frozen.status.recordStale = true;
    const window = await loadSite("/", { journal: () => frozen });
    const header = window.document.querySelector("#status");
    assert.deepEqual(warn(window), []);
    assert.ok([...header.querySelectorAll(".badge-error")]
      .some((n) => n.textContent === "can't read the journal"));
  });

  /* The other direction, so the assertion above cannot pass by matching
   * nothing: the selector has to be able to find the badge when the flag
   * is off too, and here it must not. */
  test("a readable record shows no can't-read badge", async () => {
    const live = JSON.parse(JSON.stringify(payload.journal));
    live.status.stalled = false;
    live.status.silentIntervals = 1;
    live.status.recentMissingCycles = [];
    live.status.recordStale = false;
    const window = await loadSite("/", { journal: () => live });
    assert.deepEqual([...window.document.querySelectorAll("#status .badge-error")]
      .map((n) => n.textContent), []);
  });

  /* Five tests stood here asserting the stall badge and the gap badge
   * render, the newest citing the owner's own 2026-08-14 ask for the second
   * one. He reversed that on 2026-08-20 and they are replaced by the
   * single invariant above rather than rewritten one by one -- there is
   * only one behaviour left to pin, and five tests asserting the absence
   * of five things would be five ways of measuring nothing. The server
   * still computes and serves `stalled`, `silentIntervals` and
   * `recentMissingCycles`; `test_journal_health_display.py` still covers
   * that, and it is where the remaining value in this feature lives. */
});

/* An HTTP error is not a network error, and the page could not tell them
 * apart. `fetch` rejects only when the request never completed, so every
 * 500 and 502 arrived here as a resolved response whose JSON body happened
 * to parse -- and the page rendered it. Four written "Could not load ..."
 * messages sat in this file's `.catch` blocks, unreachable, for as long as
 * they have existed. Cycles 163 and 164 fixed the server side of this twice;
 * this is the browser side, and it is the half the owner actually sees. */
describe("a server error is shown, not rendered as emptiness", () => {
  const feedText = (window) => window.document.getElementById("feed").textContent;

  test("a 502 on the journal says so instead of drawing an empty page", async () => {
    const window = await loadSite("/", { journalStatus: 502 });
    assert.match(feedText(window), /Could not load the journal/);
    assert.equal(window.document.querySelectorAll(".entry").length, 0);
  });

  test("the server's own message is preferred over the bare status", async () => {
    const window = await loadSite("/", {
      journalStatus: 500,
      journal: () => ({ error: "the journal folder is unreadable" }),
    });
    assert.match(feedText(window), /the journal folder is unreadable/);
  });

  test("a 502 on comments costs the bubbles, and says the bubbles are missing", async () => {
    const window = await loadSite("/", { commentsStatus: 502 });
    // The feed is the page: it must survive a comments failure.
    assert.ok(window.document.querySelectorAll(".entry").length > 0);
    assert.equal(window.document.querySelectorAll(".comment").length, 0);
    assert.match(feedText(window), /Comments could not be loaded/);
  });

  test("a healthy page says nothing about comments", async () => {
    const window = await loadSite("/");
    assert.doesNotMatch(feedText(window), /Could not load|could not be loaded/);
  });

  /* The board and the costs pages route through the same helper. Pinned
   * separately because they are separate call sites: reverting any one of
   * them back to a bare `r.json()` restores the bug on that page alone. */
  test("a status is shown when the body is not JSON at all", async () => {
    // A proxy in front of the server answers with an HTML error page, so
    // reading the body rejects too. There is no message to prefer, and the
    // page must still say something rather than fall through to a blank.
    const window = await loadSite("/", { journalStatus: 502, unparsable: true });
    assert.match(feedText(window), /Could not load the journal/);
    assert.match(feedText(window), /HTTP 502/);
  });

  test("a 502 on the digest costs the summaries, not the feed", async () => {
    // The digest is tolerated the same way comments are, and it was
    // tolerated before this change too -- so this pins that the new throw
    // still lands in that existing catch rather than escaping to the
    // page-level one and taking the whole feed with it.
    const window = await loadSite("/", { digestStatus: 502 });
    assert.ok(window.document.querySelectorAll(".entry").length > 0);
    assert.doesNotMatch(feedText(window), /Could not load the journal/);
  });

  test("a 502 on the board reaches the board's own message", async () => {
    const window = await loadSite("/issues", { boardStatus: 502 });
    assert.match(feedText(window), /Could not load the board/);
  });

  test("a 502 on the costs page reaches the costs page's own message", async () => {
    const window = await loadSite("/costs", { costsStatus: 502 });
    assert.match(feedText(window), /Could not load the costs/);
  });
});

/* The retrospective page (the owner, issues.md 2026-08-13).
 *
 * The chart is SVG built with createElementNS, so jsdom can be asked what
 * was actually drawn -- which is the half of this page that no Python test
 * reaches. The assertions are about marks and their positions rather than
 * about the presence of a <figure>: a chart that renders an empty box
 * passes every test that only counts elements. */
describe("the retrospective page", () => {
  // Scoped to this suite, the same way the error-message suite scopes its
  // own -- there is no file-level helper to reuse.
  const feedText = (window) => window.document.getElementById("feed").textContent;

  const twoRetros = {
    scoreKeys: [
      { key: "going", label: "How it's going" },
      { key: "effectiveness", label: "How effective" },
      { key: "feeling", label: "Overall feeling" },
    ],
    range: [1, 10],
    retros: [
      {
        at: Date.UTC(2026, 7, 7), date: "2026-08-07", cycle: 120,
        scores: { going: 5, effectiveness: 5, feeling: 6 },
        overall: "Finding its feet.", good: "Ships most cycles.", bad: "Re-derives too much.",
        changes: ["Read the pace number before picking."],
      },
      {
        at: Date.UTC(2026, 7, 14), date: "2026-08-14", cycle: 181,
        scores: { going: 7, effectiveness: 6, feeling: 8 },
        overall: "Steady, and finally measurable.", good: "The drift checks hold.",
        bad: "Still re-reads its own memory every hour.", changes: [],
      },
    ],
  };

  test("an empty ledger is a page that says so, not an error", async () => {
    const window = await loadSite("/retro");
    assert.match(feedText(window), /The first retrospective runs on a Friday morning/);
    assert.equal(window.document.querySelectorAll(".retro-card").length, 0);
    assert.doesNotMatch(feedText(window), /Could not load/);
  });

  test("one line per score, with a dot at every real retro", async () => {
    const window = await loadSite("/retro", { retro: twoRetros });
    const series = window.document.querySelector(".chart").chartOption.series;
    assert.equal(series.length, 3, "three scores, three lines");
    series.forEach((line) => {
      assert.equal(line.data.length, 2, "a retro was swallowed");
      // A dot per retro as well as the line: with one retro there is no
      // line to see at all, and with five there are still only five real
      // observations -- marking them stops the eye reading the segments
      // between as data.
      assert.equal(line.showSymbol, true);
    });
  });

  test("the axis runs the right way up, and the scale is the ledger's own", async () => {
    /* The old version of this test read a y pixel out of a path, because
     * this file drew the path. ECharts draws it now, so an inverted axis
     * is no longer something this app can get wrong by arithmetic -- it
     * would take `yAxis.inverse`, which is not set. What this app still
     * decides, and can still get wrong, is the range: hardcode 0-10 or
     * 1-100 and every score is drawn against the wrong scale. */
    const window = await loadSite("/retro", { retro: twoRetros });
    const chart = window.document.querySelector(".chart").chartOption;
    assert.equal(chart.yAxis.min, 1);
    assert.equal(chart.yAxis.max, 10);
    assert.ok(!chart.yAxis.inverse, "a higher score must sit higher");
    const feeling = chart.series[2];
    assert.deepEqual([...feeling.data].map(([, v]) => v), [6, 8]);
  });

  test("the legend names all three, so identity never rests on colour", async () => {
    const window = await loadSite("/retro", { retro: twoRetros });
    const labels = [...window.document.querySelectorAll(".legend-label")].map((n) => n.textContent);
    assert.deepEqual(labels, ["How it's going", "How effective", "Overall feeling"]);
  });

  test("a single retro still draws, rather than collapsing its own x-axis", async () => {
    // One retro is a domain with no width, so every x would be NaN and the
    // dots would vanish. This is the state the page ships in for a week.
    const window = await loadSite("/retro", {
      retro: { ...twoRetros, retros: [twoRetros.retros[0]] },
    });
    const chart = window.document.querySelector(".chart").chartOption;
    assert.equal(chart.series.length, 3);
    chart.series.forEach((line) => assert.equal(line.data.length, 1));
    // The domain is widened to a week either side rather than left at
    // zero width, which is where the NaN used to come from.
    assert.ok(chart.xAxis.max > chart.xAxis.min, "the x-axis collapsed to a point");
  });

  test("the cards read newest first and carry the prose, not just the scores", async () => {
    const window = await loadSite("/retro", { retro: twoRetros });
    const cards = [...window.document.querySelectorAll(".retro-card")];
    assert.equal(cards.length, 2);
    assert.match(cards[0].textContent, /2026-08-14/);
    assert.match(cards[0].textContent, /Steady, and finally measurable\./);
    assert.match(cards[0].textContent, /Still re-reads its own memory every hour\./);
    // The newest retro chose no changes, so its card must not sprout an
    // empty heading; the older one did, and must show it.
    assert.doesNotMatch(cards[0].textContent, /What I am changing/);
    assert.match(cards[1].textContent, /What I am changing/);
    assert.match(cards[1].textContent, /Read the pace number before picking\./);
  });

  test("the nav marks Retro as the current page", async () => {
    const window = await loadSite("/retro", { retro: twoRetros });
    const on = [...window.document.querySelectorAll(".nav-tab.on")].map((a) => a.getAttribute("href"));
    assert.deepEqual(on, ["/retro"]);
  });

  test("the readout names a day, never an invented time of day", async () => {
    // The ledger stores dates and the payload converts them to midnight
    // UTC, so the cost charts' stamp would print "14 Aug, 02:00" in Oslo
    // -- a real-looking time that corresponds to nothing. This page
    // overrides it, and the override is what is being pinned.
    const window = await loadSite("/retro", { retro: twoRetros });
    const chart = window.document.querySelector(".chart").chartOption;
    const at = chart.series[0].data[1][0];
    const label = chart.tooltip.formatter([
      { value: [at, 8], seriesName: "Overall feeling", color: "#8fd694" },
    ]);
    assert.doesNotMatch(label, /\d{1,2}:\d{2}/, `a time of day was invented: ${label}`);
    assert.match(label, /14/, `the newest retro's day is missing: ${label}`);
    // And the score reads as a score, not a bare number.
    assert.match(label, /8\/10/);
  });

  test("the retro chart gets the same treatment, since it lives in the same frame", async () => {
    const window = await loadSite("/retro", { retro: twoRetros });
    const figure = window.document.querySelector(".chart");
    assert.ok(figure, "the retro page rendered no chart at all");
    assert.ok(figure.querySelector(".chart-tool-full"), "no full-screen control");
    assert.ok(figure.chartOption.dataZoom.some((z) => z.type === "slider"));
    // The one deliberate difference: 1-to-10 is ten possible values, so
    // there is nothing to zoom into on the y axis.
    assert.ok(!figure.chartOption.dataZoom.some((z) => z.yAxisIndex === 0),
      "the score axis should not be zoomable");
  });

  test("a 502 on the retro page reaches the retro page's own message", async () => {
    const window = await loadSite("/retro", { retroStatus: 502 });
    assert.match(feedText(window), /Could not load the retrospectives/);
  });
});

/* The capture row's layout. The owner, issues.md 2026-08-14: *"Ui is ugly for
 * the priority rating. The issue, idea, note and priority dropdown are now
 * just scrambled after the addition of the priority dropdown."* That was
 * fixed the same day by giving the picker its own row above the buttons,
 * while it still showed a rating's word and grew to 136px wide.
 *
 * the owner, 2026-08-14, later: once the picker shrank to a fixed 44px glyph
 * he asked for it back on the button row, at the far right. jsdom lays
 * nothing out, so none of these can see a wrap on a real phone -- that is
 * measured in Chromium at 390px, and the fix is CSS. What is real code,
 * and what these pin, is the structure the CSS depends on: the picker is
 * the last child of the same group the three targets are in, appended
 * after them in app.js rather than inserted, so it always renders at the
 * right edge of the row rather than somewhere the flex order does not
 * expect. */
/* The attach button, in a DOM rather than in a substring.

 * The owner, comments board 2026-08-21: *"How do i send a screenshot?"*
 *
 * The Python side of this feature is pinned by tests that `open(app.js)`
 * and count substrings, and those cannot see placement. That is not a
 * hypothetical: the first version of this button was prepended to
 * `.capture-submit`, every one of those string assertions stayed green,
 * and the only thing that caught it was `the capture row does not
 * scramble` below -- a test written for something else entirely. So the
 * button gets asserted here, as a node, where being in the wrong place
 * is a thing a test can see. */
describe("the attach button is on the page, not just in the source", () => {
  test("the capture box has a paperclip and a file input", async () => {
    const window = await loadSite("/");
    const group = window.document.querySelector(".capture-submit");
    const attach = group.querySelector(".attach-btn");
    assert.ok(attach, "no attach button in the capture row");
    assert.equal(attach.type, "button", "a submit button would post the form");
    assert.ok(
      attach.getAttribute("aria-label"),
      "the button shows a glyph, so it needs a label to be announced",
    );
    // On the form and deliberately not in the group whose child count is
    // pinned -- a hidden input is not something anyone laid out.
    const input = window.document.querySelector("#capture-form .attach-input");
    assert.ok(input, "no file input reachable from the capture form");
    assert.equal(input.type, "file");
    // Was `image/*` until Cycle 309. The owner, 2026-08-21: "It seems i only
    // can upload images. Or atleas the ui forces only my Google photos to
    // open and i have no option to upload files." On Android that
    // attribute is not a filter over a file browser -- it is what opens
    // Google Photos with no way out -- so the picker takes anything and
    // the server resolves and bounds the type.
    assert.equal(input.accept, "", "an accept list traps the Android picker in Photos");
    assert.equal(group.querySelector(".attach-input"), null,
      "the hidden input is inside the pinned button group");
  });

  /* The insert side, which is what decides whether a `!` is written at all.
   * Cycle 309: the render tests above pin what happens to a line that
   * already exists, and would all stay green if `buildAttach` wrote `![...]`
   * for a PDF -- which is the bug they look like they cover.
   *
   * Cycle 377 moved *where* it is written. The markdown no longer lands in
   * the textarea; it lands in a tray as a chip and is composed at send
   * time. So these read the send payload rather than `box.value` -- which
   * is the stronger assertion of the two anyway, since the payload is what
   * the owner's board actually receives. */
  const CAPTURE_INPUT = "#capture-form .attach-input";
  const CAPTURE_TRAY = "#capture-form .attach-tray";

  /** Pick `files` (each `{name, type, isImage}`) on a composer's input, and
   *  wait for every one of them to land in the tray. */
  async function pick(window, inputSelector, traySelector, files) {
    const input = window.document.querySelector(inputSelector);
    const tray = window.document.querySelector(traySelector);
    const before = tray.querySelectorAll(".attach-chip").length;
    Object.defineProperty(input, "files", {
      configurable: true,
      value: files.map((f) => new window.File([new Uint8Array([1, 2, 3])], f.name, { type: f.type })),
    });
    // One response per file, in the order they are uploaded, so a batch can
    // be told apart chip by chip rather than all sharing one URL.
    let served = 0;
    window.fetch = () => {
      const file = files[Math.min(served, files.length - 1)];
      served += 1;
      return res({
        ok: true,
        name: "x" + served,
        url: "/api/upload/x" + served + "." + file.name.split(".").pop(),
        bytes: 3,
        isImage: file.isImage,
      });
    };
    input.dispatchEvent(new window.Event("change"));
    // `FileReader` resolves on a task and the POST on a microtask after it,
    // and the batch runs them one after another -- so allow per file.
    for (let i = 0; i < 40 * files.length; i++) {
      if (tray.querySelectorAll(".attach-chip").length >= before + files.length) break;
      await new Promise((r) => setTimeout(r, 5));
    }
    return tray;
  }

  /** What `send` would POST, by clicking a capture button with `fetch` stubbed. */
  async function captureBody(window) {
    let sent = null;
    window.fetch = (url, options) => {
      sent = JSON.parse(options.body);
      return res({ ok: true });
    };
    const status = window.document.querySelector(".capture-status");
    status.textContent = "";
    window.document.querySelector('#capture-form [data-target="issues"]').click();
    // Waiting on the *status line*, not on the tray, because one of the
    // tests below asserts what happened to the tray -- and a wait that
    // watches the thing it is about to assert can only ever pass.
    for (let i = 0; i < 40 && !/^saved to /.test(status.textContent); i++) {
      await new Promise((r) => setTimeout(r, 5));
    }
    return sent;
  }

  test("picking a picture puts a thumbnail in the tray, not text in the box", async () => {
    const window = await loadSite("/");
    const box = window.document.querySelector("#capture-form textarea");
    box.value = "look at this";
    const tray = await pick(window, CAPTURE_INPUT, CAPTURE_TRAY,
      [{ name: "shot.jpg", type: "image/jpeg", isImage: true }]);

    // The ask itself: "preview a miniatyr version of the uploaded images
    // ... instead of the text that shows up in the input box."
    const thumb = tray.querySelector("img.attach-thumb");
    assert.ok(thumb, "no thumbnail in the tray");
    assert.equal(thumb.getAttribute("src"), "/api/upload/x1.jpg");
    assert.equal(thumb.getAttribute("alt"), "shot.jpg",
      "four screenshots in a tray are told apart by their alt text alone");
    assert.equal(box.value, "look at this", "the markdown was written into the box after all");
    assert.equal(tray.hidden, false, "a tray with a chip in it is hidden");

    assert.equal((await captureBody(window)).text,
      "look at this\n\n![shot.jpg](/api/upload/x1.jpg)");
  });

  test("picking a file gets a named chip and a plain link, with no bang", async () => {
    const window = await loadSite("/");
    window.document.querySelector("#capture-form textarea").value = "";
    const tray = await pick(window, CAPTURE_INPUT, CAPTURE_TRAY,
      [{ name: "runner.log", type: "", isImage: false }]);
    assert.equal(tray.querySelector("img.attach-thumb"), null,
      "a log file has nothing to show and must not paint a broken image");
    assert.match(tray.querySelector(".attach-chip-name").textContent, /runner\.log/);

    // A screenshot with no sentence under it is still worth filing, which
    // is why this sends at all with an empty box.
    const body = await captureBody(window);
    assert.equal(body.text, "[runner.log](/api/upload/x1.log)");
    assert.equal(body.text.startsWith("!"), false,
      "a `!` here paints a broken-image icon for a file that has nothing to show");
  });

  test("several files can be picked at once, and each gets its own chip", async () => {
    const window = await loadSite("/");
    window.document.querySelector("#capture-form textarea").value = "";
    const tray = await pick(window, CAPTURE_INPUT, CAPTURE_TRAY, [
      { name: "one.jpg", type: "image/jpeg", isImage: true },
      { name: "two.jpg", type: "image/jpeg", isImage: true },
      { name: "three.png", type: "image/png", isImage: true },
    ]);
    assert.equal(tray.querySelectorAll(".attach-chip").length, 3,
      "picking three files did not produce three chips");
    assert.equal(
      window.document.querySelector(CAPTURE_INPUT).multiple, true,
      "the picker only lets him choose one file at a time",
    );
    assert.equal((await captureBody(window)).text,
      "![one.jpg](/api/upload/x1.jpg)\n\n![two.jpg](/api/upload/x2.jpg)\n\n![three.png](/api/upload/x3.png)");
  });

  test("crossing one out drops it from what gets sent, and keeps the rest", async () => {
    const window = await loadSite("/");
    window.document.querySelector("#capture-form textarea").value = "";
    const tray = await pick(window, CAPTURE_INPUT, CAPTURE_TRAY, [
      { name: "keep.jpg", type: "image/jpeg", isImage: true },
      { name: "drop.jpg", type: "image/jpeg", isImage: true },
    ]);
    const remove = tray.querySelectorAll(".attach-chip")[1].querySelector(".attach-chip-remove");
    assert.ok(remove, "no way to cross an attachment out");
    assert.equal(remove.getAttribute("aria-label"), "Remove drop.jpg",
      "the ✕ is a glyph, so it needs a label that names what it destroys");
    remove.click();

    assert.equal(tray.querySelectorAll(".attach-chip").length, 1);
    assert.equal((await captureBody(window)).text, "![keep.jpg](/api/upload/x1.jpg)");
  });

  test("an empty tray is hidden, and sending clears it", async () => {
    const window = await loadSite("/");
    const tray = window.document.querySelector(CAPTURE_TRAY);
    assert.ok(tray, "no tray on the capture form");
    assert.equal(tray.hidden, true, "an empty tray takes up room under the box");

    window.document.querySelector("#capture-form textarea").value = "";
    await pick(window, CAPTURE_INPUT, CAPTURE_TRAY,
      [{ name: "shot.jpg", type: "image/jpeg", isImage: true }]);
    await captureBody(window);
    // Cleared only on a confirmed write, the same rule the box follows: a
    // tray that emptied itself on a failure would lose the upload it exists
    // to hold on to.
    assert.equal(tray.querySelectorAll(".attach-chip").length, 0,
      "the picture stayed in the tray after it was sent, so the next capture carries it too");
    assert.equal(tray.hidden, true);
  });

  /* Reviewer finding, Cycle 377. The journal drawer is rebuilt whole on a
   * render and the upload chain is not cancelled with it, so a file that
   * finishes uploading into an orphaned composer used to push a chip, call
   * `onChange`, and write itself back into the draft store the *live*
   * drawer had already read -- including after a successful send had
   * deleted it, which resurrects an attachment he has already sent.
   *
   * Detaching the tray is the whole condition, so that is what this drives
   * directly rather than trying to reproduce a poll landing mid-batch. */
  test("an upload that lands after its composer is gone does not attach", async () => {
    const window = await loadSite("/");
    const tray = window.document.querySelector(CAPTURE_TRAY);
    window.document.querySelector("#capture-form textarea").value = "";
    await pick(window, CAPTURE_INPUT, CAPTURE_TRAY,
      [{ name: "before.jpg", type: "image/jpeg", isImage: true }]);
    assert.equal(tray.querySelectorAll(".attach-chip").length, 1);

    tray.remove();
    const input = window.document.querySelector(CAPTURE_INPUT);
    Object.defineProperty(input, "files", {
      configurable: true,
      value: [new window.File([new Uint8Array([1])], "after.jpg", { type: "image/jpeg" })],
    });
    window.fetch = () => res({ ok: true, name: "y", url: "/api/upload/y.jpg", bytes: 1, isImage: true });
    input.dispatchEvent(new window.Event("change"));
    // Long enough for the FileReader task and the POST microtask to land.
    for (let i = 0; i < 40; i++) await new Promise((r) => setTimeout(r, 5));

    assert.equal(tray.querySelectorAll(".attach-chip").length, 1,
      "an orphaned composer attached a picture nobody can see");
    assert.match(
      window.document.querySelector(".capture-status").textContent,
      /after the page moved on/,
      "the dropped upload was swallowed rather than reported",
    );
  });

  test("the comment drawer has one too", async () => {
    const window = await loadSite("/");
    const actions = window.document.querySelector(".comment-actions");
    assert.ok(actions, "no comment composer rendered at all");
    const attach = actions.querySelector(".attach-btn");
    assert.ok(attach, "no attach button in the comment composer");
    // Before Comment, so the primary action keeps the right edge it had.
    const kids = [...actions.children];
    assert.ok(
      kids.indexOf(attach) < kids.findIndex((el) => el.className.includes("comment-send")),
      "the attach button is after the send button",
    );
    // And its tray (Cycle 377), between the box and this row. The drawer is
    // the one composer that gets rebuilt on every poll, so "the node is on
    // the page" is worth asserting here even though the upload behaviour is
    // covered on the capture form.
    const drawer = actions.parentNode;
    const order = [...drawer.children].map((n) => n.className.split(" ")[0]);
    assert.ok(
      order.indexOf("attach-tray") > order.indexOf("comment-text")
        && order.indexOf("attach-tray") < order.indexOf("comment-actions"),
      `the tray should sit under the box and above the buttons, got ${order.join(",")}`,
    );
  });
});

/* Cycle 309. The owner, comments board 2026-08-21 21:09: "How about a file?
 * It seems i only can upload images. Or atleas the ui forces only my
 * Google photos to open and i have no option to upload files."
 *
 * There was no browser test over the render half at all before this --
 * `appendRichText` recognised one construct and nothing on this side
 * pinned it -- so these two cover the branch I added *and* the branch that
 * was already there, because a regex change is exactly the edit that can
 * fix the new case and silently break the old one. */
describe("an attachment renders as what it is", () => {
  const HASH = "89f92e607e3e8a3e85a40b40f4a07609";

  function commentSaying(text) {
    const copy = JSON.parse(JSON.stringify(payload.comments));
    copy.byCycle["57"] = [{
      cycle: 57, stamp: "2026-08-21 21:09", text,
      reply: "", replyStamp: "", replies: [], acknowledged: false,
      replyPending: false, replyWaiting: false, replyWaitingSeconds: 0,
      replyFailed: false,
    }];
    return copy;
  }

  test("an image is a thumbnail you can open", async () => {
    const window = await loadSite("/", {
      comments: commentSaying(`look at this ![shot.jpg](/api/upload/${HASH}.jpg)`),
    });
    const body = window.document.querySelector(".comment-body");
    const img = body.querySelector("img.attach-img");
    assert.ok(img, "an image attachment should still paint as an image");
    assert.equal(img.getAttribute("src"), `/api/upload/${HASH}.jpg`);
    assert.equal(img.alt, "shot.jpg");
    assert.equal(body.textContent.includes("/api/upload/"), false,
      "the markdown should be replaced, not printed beside the image");
  });

  test("a file is a named link, not a broken image", async () => {
    const window = await loadSite("/", {
      comments: commentSaying(`the log is [runner.log](/api/upload/${HASH}.log)`),
    });
    const body = window.document.querySelector(".comment-body");
    assert.equal(body.querySelector("img"), null,
      "a .log has nothing to show; an <img> here is a broken-image icon");
    const link = body.querySelector("a.attach-file");
    assert.ok(link, "a file attachment should paint as a link");
    assert.equal(link.getAttribute("href"), `/api/upload/${HASH}.log`);
    assert.ok(link.textContent.includes("runner.log"),
      "the link must carry his filename -- the URL is a 32-hex hash and says nothing");
  });

  test("a link he typed himself is still text, not an element", async () => {
    // The URL is required to start with `/api/upload/`, and dropping the
    // required `!` is the change that could have loosened that. A remote
    // link and a `javascript:` one must stay the characters he typed.
    const window = await loadSite("/", {
      comments: commentSaying("[x](https://example.com/api/upload/a.png) [y](javascript:alert(1))"),
    });
    const body = window.document.querySelector(".comment-body");
    assert.equal(body.querySelector("a"), null, "no anchor should be built");
    assert.equal(body.querySelector("img"), null);
    assert.ok(body.textContent.includes("javascript:alert(1)"),
      "it stays the text he typed");
  });
});

describe("the capture row does not scramble", () => {
  test("the priority picker joins the targets as the last item in the group", async () => {
    const window = await loadSite("/");
    const group = window.document.querySelector(".capture-submit");
    assert.ok(group, "the buttons are no longer grouped");
    const kids = [...group.children];
    assert.deepEqual(
      kids.slice(0, 3).map((el) => el.dataset.target),
      ["issues", "ideas", "notes"],
      "the button group does not hold the three targets first",
    );
    // Five since the attach button joined the row: the three targets, the
    // paperclip, the picker. The count is still asserted rather than
    // loosened, because the whole point of this test is that nothing gets
    // to appear in this row without someone deciding where it goes -- the
    // attach button was prepended first and this assertion is what caught
    // it putting the targets at 1, 2, 3.
    assert.equal(kids.length, 5, "the priority picker is not in the button group");
    assert.equal(
      kids[3] && kids[3].className.includes("attach-btn"), true,
      "the attach button is not between the targets and the picker",
    );
    assert.equal(
      kids[4] && kids[4].id, "capture-prio",
      "the picker is not the last (rightmost) item in the row",
    );
  });

  test("the picker keeps a label a screen reader and a reader can both find", async () => {
    const window = await loadSite("/");
    const picker = window.document.getElementById("capture-prio");
    const label = window.document.querySelector(".capture-prio-label");
    assert.ok(label, "the priority row lost its visible label");
    assert.equal(label.htmlFor, picker.id);
  });

  /* The one CSS claim worth a mechanical check: `.capture-actions` is
   * allowed to wrap and `.capture-submit` is what stops the wrap landing
   * between two buttons. A `flex-wrap: wrap` added to the group -- which
   * looks harmless and would be the obvious thing to add if a fourth
   * target ever made it tight -- reopens the exact bug. */
  test("the button group is not itself allowed to wrap", () => {
    const css = readFileSync(join(publicDir, "style.css"), "utf8");
    const { window } = openWindow("<style>" + css + "</style>");
    const rules = [...window.document.styleSheets[0].cssRules];
    const group = rules.find((r) => r.selectorText === ".capture-submit");
    const row = rules.find((r) => r.selectorText === ".capture-actions");
    assert.ok(group && row, "the capture row's rules are gone");
    assert.equal(group.style.display, "flex");
    // `cssText` rather than `.style.flexWrap`: jsdom's CSSOM parses the
    // declaration into the text but exposes no property for it, so a
    // property assertion here reads `undefined` whatever the sheet says
    // and would pass with the bug back.
    assert.match(row.style.cssText, /flex-wrap:\s*wrap/, "the row must still wrap");
    assert.doesNotMatch(group.style.cssText, /flex-wrap/, "the button group may wrap again");
  });

  /* Cycle 191's finding was that the two pickers sharing one CSS rule
   * could drift apart silently; that rule is gone now that the board
   * row's picker went back to `.chip.prio` (the owner, 2026-08-14) and has
   * nothing left in common with the capture box's circle to protect. This
   * pins the replacement invariant instead: `.capture-prio` is still held
   * to the 44px iOS touch minimum this stylesheet holds every button to,
   * on its own now rather than as half of a shared rule. */
  test("the capture box's picker still meets the 44px touch minimum on its own", () => {
    const css = readFileSync(join(publicDir, "style.css"), "utf8");
    const { window } = openWindow("<style>" + css + "</style>");
    const rules = [...window.document.styleSheets[0].cssRules];
    const sized = rules.find(
      (r) => r.selectorText === ".capture-prio" && /min-height/.test(r.style.cssText),
    );
    assert.ok(sized, "no .capture-prio rule sets a min-height any more");
    // `cssText`, not `.style.minHeight` -- jsdom parses the declaration
    // into the text and exposes no property for it, so a property
    // assertion reads `undefined` whatever the sheet says.
    assert.match(sized.style.cssText, /min-height:\s*44px/);
  });
});

/* the owner, issues.md #90: "When i press enter on my keyboard, it
 * automatically submits my input text as an issue in the Nova text input
 * field. Pressing enter should create a new line, not submit."
 *
 * These pin the *absence* of a handler, which is a shape worth being
 * careful about: `preventDefault` not being called is the only observable
 * difference between "Enter inserts a newline" and "Enter did nothing at
 * all", since jsdom does not type into a textarea for you. So both halves
 * are asserted -- nothing posted, and the event left un-cancelled so the
 * browser's own newline survives. */
describe("Enter in the capture box is a newline", () => {
  function press(window, el, key, mods) {
    const event = new window.KeyboardEvent("keydown", Object.assign(
      { key, bubbles: true, cancelable: true }, mods || {}));
    el.dispatchEvent(event);
    return event;
  }

  test("Enter files nothing and is left to insert a newline, with or without Shift", async () => {
    /* Both cases in one test on purpose. Written as two, the Shift+Enter
     * half passed under every mutation I could aim at this -- the old
     * code let Shift+Enter through too -- so it read as a second guard and
     * was pinning nothing. The bare-Enter case is the one that discriminates;
     * Shift is here to say the modifier is no longer load-bearing, which is
     * the actual bug: a soft keyboard has a return key and no reachable
     * Shift+Enter, so on the phone he captures from there was no escape. */
    for (const mods of [{}, { shiftKey: true }]) {
      const window = await loadSite("/issues");
      const box = window.document.getElementById("capture-text");
      box.value = "half a thought";
      const event = press(window, box, "Enter", mods);
      await new Promise((r) => window.setTimeout(r, 0));
      const how = JSON.stringify(mods);
      assert.equal(window.posted.length, 0, `${how}+Enter filed the half-written capture`);
      assert.equal(event.defaultPrevented, false,
        `${how}+Enter cancelled the newline, so it does nothing at all rather than breaking the line`);
      assert.equal(box.value, "half a thought", `${how}+Enter cleared the box as if it had sent`);
    }
  });

  test("Cmd/Ctrl+Enter still sends, to the leftmost button's target", async () => {
    for (const mods of [{ metaKey: true }, { ctrlKey: true }]) {
      const window = await loadSite("/issues");
      const box = window.document.getElementById("capture-text");
      box.value = "ship the thing";
      press(window, box, "Enter", mods);
      await new Promise((r) => window.setTimeout(r, 0));
      assert.equal(window.posted.length, 1, `no send on ${JSON.stringify(mods)}+Enter`);
      assert.equal(window.posted[0].url, "/api/capture");
      assert.equal(window.posted[0].body.text, "ship the thing");
      /* Both halves, because either alone is weaker than it looks. Review
       * on #223 flagged the first: comparing against the same selector the
       * production code reads mirrors the logic rather than pinning an
       * answer, so a mutation choosing the *last* button would move both
       * sides together if the real button order were ever reversed. The
       * literal alone would be wrong in the other direction -- the point of
       * this change is that the target is not hardcoded -- so the literal
       * pins today's answer and the second assertion pins why it is that. */
      assert.equal(window.posted[0].body.target, "issues");
      assert.equal(
        window.document.querySelector(".capture-btn").getAttribute("data-target"), "issues",
        "the leftmost capture button is no longer Issue, so the chord's target changed with it",
      );
    }
  });
});

/* the owner, issues.md #91: "All unboarded issues and ideas should have the
 * priority status icon shown (as they do when its chosen) in the left top
 * corner, but pressing it should open the modal like it does sin the issue
 * cards." */
describe("rating a capture that is not boarded yet", () => {
  const rated = {
    text: "🟠 High: make the picker work here too",
    body: "make the picker work here too",
    priority: "🟠 High",
    priorityKey: "high",
    blocks: [{ type: "p", spans: [{ kind: "text", text: "make the picker work here too" }] }],
  };
  const withCapture = (capture) => ({
    board: (url) => (url.includes("item=") ? payload.boardItem
      : { ...payload.board, captures: [capture] }),
  });

  test("an unrated capture still shows a trigger, so a first rating can be given", async () => {
    // The read-only chip it replaces was painted only when a rating
    // existed, which left the one case that needs the control most --
    // nothing rated yet -- with nothing to press.
    const window = await loadSite("/issues");
    const trigger = window.document.querySelector(".capture-item .chip.prio");
    assert.ok(trigger, "an unrated capture has no priority trigger");
    assert.equal(trigger.tagName, "BUTTON");
    assert.equal(trigger.textContent, "Unrated");
    assert.equal(trigger.className, "chip prio", "an unrated trigger must carry no colour class");
  });

  test("picking a rating rewrites the bullet's leading glyph through /api/capture/edit", async () => {
    const window = await loadSite("/issues", withCapture(payload.board.captures[0]));
    click(window, window.document.querySelector(".capture-item .chip.prio"));
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "🔴 Immediately"));
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/capture/edit");
    assert.ok(posted, "no write reached /api/capture/edit");
    assert.equal(posted.body.target, "issues");
    assert.equal(posted.body.index, 0);
    assert.equal(posted.body.original, payload.board.captures[0].text,
      "the edit did not carry the capture's own text as its address");
    assert.equal(posted.body.text, "🔴 Immediately: " + payload.board.captures[0].body);
  });

  test("re-rating an already-rated capture swaps the rating rather than stacking a second one", async () => {
    // `capture.body` is the server's glyph-stripped text, and using
    // `capture.text` here instead would send "⚪ Low: 🟠 High: make the picker...".
    const window = await loadSite("/issues", withCapture(rated));
    const trigger = window.document.querySelector(".capture-item .chip.prio");
    assert.equal(trigger.textContent, "🟠 High");
    assert.equal(trigger.className, "chip prio prio-high");
    click(window, trigger);
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "⚪ Low"));
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/capture/edit");
    assert.equal(posted.body.text, "⚪ Low: make the picker work here too");
  });

  test("clearing a rating leaves the text and does not send an empty edit", async () => {
    // `/api/capture/edit` answers a blank text with 400 "nothing to save"
    // and never deletes -- deletion is its own route. So the point here is
    // not safety, it is that an Unrated pick on a glyph-only bullet has no
    // request worth making.
    const window = await loadSite("/issues", withCapture(rated));
    click(window, window.document.querySelector(".capture-item .chip.prio"));
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "– Unrated"));
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/capture/edit");
    assert.equal(posted.body.text, "make the picker work here too");
  });

  test("a glyph-only capture sends nothing, rather than a request that can only 400", async () => {
    const glyphOnly = { text: "🟠 High:", body: "", priority: "🟠 High", priorityKey: "high", blocks: [] };
    const window = await loadSite("/issues", withCapture(glyphOnly));
    const trigger = window.document.querySelector(".capture-item .chip.prio");
    click(window, trigger);
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "– Unrated"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(window.posted.filter((p) => p.url === "/api/capture/edit").length, 0,
      "an edit the server can only refuse was sent anyway");
    assert.equal(trigger.textContent, "🟠 High", "the trigger kept a rating it never saved");
    assert.match(
      window.document.querySelector(".capture-item .capture-item-status").textContent,
      /nothing to rate/,
      "the local refusal said nothing on screen",
    );
  });

  test("a failed write reverts the trigger rather than showing an unsaved rating", async () => {
    const window = await loadSite("/issues", withCapture(rated));
    window.postReply = { ok: false, message: "conflict" };
    const trigger = window.document.querySelector(".capture-item .chip.prio");
    click(window, trigger);
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "⚪ Low"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(trigger.textContent, "🟠 High", "the trigger kept a rating the server refused");
    assert.match(
      window.document.querySelector(".capture-item .capture-item-status").textContent,
      /conflict/,
      "the refusal reverted the chip in silence, so he cannot tell it from a tap that never registered",
    );
  });

  test("each capture's trigger names the capture it belongs to", async () => {
    /* Not a behaviour test and deliberately not written as one. I first
     * wrote this as "two captures each open their own menu, not each
     * other's", on the theory that `openMenu` keys the shared popup on
     * `opts.ariaLabel` so identical labels would make the second tap close
     * the first's menu. It passed with every capture sharing one label,
     * because the document-level outside-click handler closes the menu
     * before the second trigger's handler reads `openFor` -- so it was a
     * test that could not fail, dressed as a guard. What unique labels
     * actually buy is a screen reader being able to tell a page of
     * "Priority, Unrated" triggers apart, so that is what this asserts. */
    const first = payload.board.captures[0];
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("item=") ? payload.boardItem
        : { ...payload.board, captures: [first, rated] }),
    });
    const labels = [...window.document.querySelectorAll(".capture-item .chip.prio")]
      .map((t) => t.getAttribute("aria-label"));
    assert.equal(labels.length, 2);
    assert.equal(new Set(labels).size, 2, "both triggers announce the same thing");
  });
});

/* `buildPrioPicker` in app.js, exercised through both surfaces it builds.
 * The bug that motivated these: closing the popup (`menu.hidden = true`)
 * silently did nothing, because `.prio-menu`'s own `display: flex` beat
 * the UA stylesheet's `[hidden] { display: none }` in the cascade. Every
 * structural test above kept passing throughout -- none of them opened
 * the thing and looked at what was left on screen after a pick. */
describe("the priority picker (buildPrioPicker)", () => {
  test("the composer's picker opens with glyph and word, and closes to the glyph alone", async () => {
    const window = await loadSite("/issues");
    const trigger = window.document.getElementById("capture-prio");
    assert.equal(trigger.textContent, "–", "the closed trigger should start on the dash");
    click(window, trigger);
    const menu = window.document.querySelector(".prio-menu");
    assert.ok(menu, "no menu was opened");
    assert.equal(menu.hidden, false, "the menu did not open");
    assert.deepEqual(
      [...menu.querySelectorAll(".prio-option")].map((o) => o.textContent),
      ["– Unrated", "⚪ Low", "🔵 Medium", "🟠 High", "🔴 Immediately"],
      "the open list must spell out each rating, unlike the closed button",
    );
    const high = [...menu.querySelectorAll(".prio-option")].find((o) => o.textContent === "🟠 High");
    click(window, high);
    // Glyph only, no word. The owner, 2026-08-22: the word made the closed
    // button wide enough to shove the row's other buttons out of place, and
    // "the button should just show the color". The word is not lost -- the
    // menu two assertions up spells out all five, and a board row's chip
    // still carries `🟠 High` in full. This assertion was left on the old
    // behaviour when that shipped, which is half of why `main` went red.
    assert.equal(trigger.textContent, "🟠", "the closed trigger should be the picked glyph alone");
    assert.equal(menu.hidden, true, "the menu did not close after a pick");
  });

  test("the picked priority rides along with the next capture, then resets", async () => {
    const window = await loadSite("/issues");
    click(window, window.document.getElementById("capture-prio"));
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "🔴 Immediately"));
    window.document.getElementById("capture-text").value = "ship the thing";
    click(window, window.document.querySelector('.capture-btn[data-target="issues"]'));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(window.posted.length, 1);
    assert.equal(window.posted[0].body.priority, "🔴 Immediately");
    assert.equal(
      window.document.getElementById("capture-prio").textContent, "–",
      "the picker did not reset after a send",
    );
  });

  test("picking a rating on a boarded row posts it and updates the chip", async () => {
    const window = await loadSite("/issues#57");
    const trigger = window.document.getElementById("item-57").querySelector(".item-meta-row > .chip.prio");
    assert.ok(trigger, "the open row has no priority trigger");
    assert.equal(trigger.textContent, "Unrated", "#57 is unrated in the fixture");
    assert.equal(trigger.className, "chip prio", "an unrated trigger must carry no prio-<key> colour class");
    click(window, trigger);
    const low = [...window.document.querySelectorAll(".prio-option")].find((o) => o.textContent === "⚪ Low");
    click(window, low);
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/board/priority");
    assert.ok(posted, "no write reached /api/board/priority");
    assert.equal(posted.body.number, 57);
    assert.equal(posted.body.priority, "⚪ Low");
    assert.equal(trigger.textContent, "⚪ Low", "the trigger did not adopt the full new label");
    assert.equal(trigger.className, "chip prio prio-low");
  });

  test("a failed write reverts the chip and reports the error, rather than keeping an unsaved choice", async () => {
    const window = await loadSite("/issues#57");
    window.postReply = { ok: false, message: "conflict" };
    const row = window.document.getElementById("item-57");
    const trigger = row.querySelector(".item-meta-row > .chip.prio");
    const before = trigger.textContent;
    click(window, trigger);
    const high = [...window.document.querySelectorAll(".prio-option")].find((o) => o.textContent === "🟠 High");
    click(window, high);
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(trigger.textContent, before, "the chip did not revert once the write failed");
    assert.match(row.querySelector(".item-prio-note").textContent, /Could not save/);
  });

  test("a board row's priority trigger is in the head, so a closed row still shows it", async () => {
    // the owner, 2026-08-14: "on issues and ideas the priority button should
    // be the priority tag instead, not a separate button" -- the old
    // picker lived in `.item-body`, which only exists once a row opens.
    const window = await loadSite("/issues");
    const row = window.document.getElementById("item-57");
    assert.equal(row.querySelector(".item-head").getAttribute("aria-expanded"), "false");
    const trigger = row.querySelector(".item-meta-row > .chip.prio");
    assert.ok(trigger, "the trigger is not beside the head, or the row waited to open first");
    assert.equal(trigger.tagName, "BUTTON");
  });

  test("a rated board row's trigger keeps the original cycle-171 chip look", async () => {
    // the owner, 2026-08-14: "i liked the old issue priority status better...
    // make it into a button that opens the modal, but the visual design is
    // not changed from the old design" -- same class, same full text, on
    // a <button> instead of a <span>.
    //
    // A fresh, explicit board rather than `payload.board.items.find(...)`:
    // the default fixture's items are shared, mutable state across every
    // test in this file (`loadSite` hands `payload.board` straight to
    // app.js with no clone when nothing overrides it), and the picking
    // test above mutates `item.priority` on the real object by reference.
    // Reading the fixture back out here would sometimes see that test's
    // leftovers instead of what this test itself set up.
    const window = await loadSite("/issues", {
      board: (url) => {
        if (url.includes("q=") || url.includes("item=")) return null;
        const board = JSON.parse(JSON.stringify(payload.board));
        const rated = board.items.find((i) => i.statusKey !== "done");
        rated.priority = "🔵 Medium";
        rated.priorityKey = "medium";
        return board;
      },
    });
    const rated = payload.board.items.find((i) => i.statusKey !== "done");
    const trigger = window.document.getElementById("item-" + rated.number)
      .querySelector(".item-meta-row > .chip.prio");
    assert.equal(trigger.textContent, "🔵 Medium");
    assert.equal(trigger.className, "chip prio prio-medium");
  });

  test("a done row shows a read-only chip instead of a picker", async () => {
    const window = await loadSite("/issues", {
      board: (url) => {
        if (url.includes("q=") || url.includes("item=")) return null;
        const board = JSON.parse(JSON.stringify(payload.board));
        const done = board.items.find((i) => i.statusKey === "done");
        done.priority = "🟠 High";
        done.priorityKey = "high";
        return board;
      },
    });
    const done = payload.board.items.find((i) => i.statusKey === "done");
    click(window, window.document.querySelector(".board-filter-btn"));
    click(window, [...window.document.querySelectorAll(".filter")].find((c) => c.textContent.startsWith("All")));
    const row = window.document.getElementById("item-" + done.number);
    const indicator = row.querySelector(".item-meta-row > .chip.prio");
    assert.ok(indicator, "no priority chip on the done row");
    assert.notEqual(indicator.tagName, "BUTTON", "a done row's priority chip must not be a clickable trigger");
    assert.equal(indicator.textContent, "🟠 High");
    assert.equal(indicator.className, "chip prio prio-high");
  });

  test("an unrated done row shows no chip at all -- the cycle-171 rule this design brought back", async () => {
    const window = await loadSite("/issues", {
      board: (url) => {
        if (url.includes("q=") || url.includes("item=")) return null;
        return payload.board;
      },
    });
    const done = payload.board.items.find((i) => i.statusKey === "done" && !i.priority);
    assert.ok(done, "fixture has no unrated done row to test against");
    click(window, window.document.querySelector(".board-filter-btn"));
    click(window, [...window.document.querySelectorAll(".filter")].find((c) => c.textContent.startsWith("All")));
    const row = window.document.getElementById("item-" + done.number);
    assert.equal(row.querySelector(".item-meta-row > .chip.prio"), null);
  });
});

/* the owner, ideas.md #71: "Ability to search through issues or ideas. Also
 * filter the list based on different parameters like date, this week,
 * priority etc." and #70: "Lets me sort issues and ideas ... make sure
 * its both upwards and downwards option ... a button with a
 * upwards/downwards facing arrow to click and have it turn". */
describe("searching, filtering and sorting a board", () => {
  const rows = (window) =>
    [...window.document.querySelectorAll(".item-number")].map((n) => n.textContent);
  /* The filter and toggle buttons this suite reaches for all moved into
   * the filter modal (the owner, 2026-08-14: "make the filters into a
   * modal... remove all the filter buttons"). `chip` opens it first if
   * it is not already, so every existing call site in this file keeps
   * working unchanged rather than needing its own "open the modal" step
   * added by hand. */
  const openFilters = (window) => {
    const btn = window.document.querySelector(".board-filter-btn");
    if (btn && btn.getAttribute("aria-expanded") !== "true") click(window, btn);
  };
  const chip = (window, prefix) => {
    openFilters(window);
    return [...window.document.querySelectorAll(".filter")]
      .filter((c) => c.textContent.startsWith(prefix))[0];
  };

  /* The write-up half of the search is debounced then fetched, so it
   * lands two turns of the event loop later. `captureTimers` is for the
   * poll intervals; this is a one-shot `setTimeout` on the real clock. */
  const settle = () => new Promise((r) => setTimeout(r, 260));

  const typeSearch = (window, text) => {
    const input = window.document.querySelector(".board-search-input");
    input.value = text;
    input.dispatchEvent(new window.Event("input"));
    return input;
  };

  test("typing narrows to title matches without waiting for the server", async () => {
    const window = await loadSite("/issues");
    typeSearch(window, "gemini");
    assert.deepEqual(rows(window), ["#58"]);
  });

  test("a write-up match the page cannot see arrives from the server", async () => {
    /* The whole reason the search is not purely client-side: "cache" is
     * in no row title, only in #57's detail body, which the list payload
     * deliberately never carries. */
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("q=") ? { query: "cache", matches: [57] } : null),
    });
    typeSearch(window, "cache");
    // Nothing matches on title, so the page is empty until the answer lands.
    assert.deepEqual(rows(window), []);
    await settle();
    assert.deepEqual(rows(window), ["#57"]);
  });

  test("a stale answer for an older query is thrown away", async () => {
    /* A reply for "cach" must never be shown as the result for "cache" --
     * the row it names may not match what is in the box any more. */
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("q=") ? { query: "cach", matches: [57] } : null),
    });
    typeSearch(window, "cache");
    await settle();
    assert.deepEqual(rows(window), []);
  });

  test("clearing the search puts every row back", async () => {
    const window = await loadSite("/issues");
    typeSearch(window, "gemini");
    click(window, window.document.querySelector(".board-search-clear"));
    assert.deepEqual(rows(window), ["#57", "#58"]);
  });

  /* the owner, issues.md 2026-08-15: "When i use the search bar in Nova, my
   * keyboard is closed on every letter input so i have to open the
   * keyboard each letter. This is very frustrating."
   *
   * A phone dismisses the soft keyboard when the focused element leaves
   * the document, and will not reopen it for a `.focus()` that arrives
   * outside the tap that caused it. So the fix is not "restore focus
   * better", it is "do not remove the input" -- which is what these
   * assert, by node identity. jsdom has no keyboard to watch, but it has
   * the thing the keyboard was reacting to. */
  test("typing does not replace the input under the caret", async () => {
    const window = await loadSite("/issues");
    const before = window.document.querySelector(".board-search-input");
    before.focus();
    typeSearch(window, "gemini");
    assert.equal(
      window.document.querySelector(".board-search-input"),
      before,
      "the search box was rebuilt mid-keystroke, which closes the keyboard",
    );
    assert.equal(window.document.activeElement, before, "focus left the search box");
    // And it still did its job.
    assert.deepEqual(rows(window), ["#58"]);
  });

  test("the server's answer does not replace the input either", async () => {
    /* The second half, and it fails independently of the first: this
     * lands ~200ms after the last keystroke, while he is still holding
     * the keyboard open over the box. */
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("q=") ? { query: "cache", matches: [57] } : null),
    });
    const before = window.document.querySelector(".board-search-input");
    before.focus();
    typeSearch(window, "cache");
    await settle();
    assert.deepEqual(rows(window), ["#57"], "the answer never landed, so this proves nothing");
    assert.equal(
      window.document.querySelector(".board-search-input"),
      before,
      "the search box was rebuilt when the write-up answer arrived",
    );
    assert.equal(window.document.activeElement, before, "focus left the search box");
  });

  test("the clear button is hidden rather than absent while the box is empty", async () => {
    /* Adding and removing it around the input is a DOM change next to the
     * caret on the first and last character typed. `hidden` is the same
     * thing to a reader and to a screen reader, and moves nothing. */
    const window = await loadSite("/issues");
    const clear = window.document.querySelector(".board-search-clear");
    assert.ok(clear, "there is no clear button to hide");
    assert.equal(clear.hidden, true);
    typeSearch(window, "gemini");
    assert.equal(window.document.querySelector(".board-search-clear"), clear);
    assert.equal(clear.hidden, false);
    typeSearch(window, "");
    assert.equal(clear.hidden, true);
  });

  test("the unrated chip finds the rows no one has rated", async () => {
    const window = await loadSite("/issues", {
      board: (url) => {
        if (url.includes("q=") || url.includes("item=")) return null;
        const board = JSON.parse(JSON.stringify(payload.board));
        board.items[0].priority = "🟠 High";
        board.items[0].priorityKey = "high";
        return board;
      },
    });
    click(window, chip(window, "Unrated"));
    assert.deepEqual(rows(window), ["#58"]);
  });

  test("the toggles and the status filter compose rather than replace", async () => {
    /* Deliberately NOT under `All`: `All` matches everything, so ANDing a
     * toggle onto it and running the toggle alone give the same answer,
     * and the test would pass under a `replace` implementation too. Under
     * `Open` they diverge -- #51 is done and carries a `where`, so it
     * matches the toggle and must still be excluded by the status filter. */
    const window = await loadSite("/issues");
    click(window, chip(window, "Nova worked on it"));
    assert.deepEqual(rows(window), ["#57"], "the status filter gave way to the toggle");
    click(window, chip(window, "All"));
    assert.deepEqual(rows(window).sort(), ["#51", "#57"]);
  });

  test("switching boards drops a search answered for the other one", async () => {
    /* `matches` is a list of row *numbers* the server answered for one
     * file. Carried across, #58-from-Issues silently filters whatever #58
     * happens to be on Ideas. */
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("q=") ? { query: "gemini", matches: [58] } : null),
    });
    typeSearch(window, "gemini");
    await settle();
    assert.deepEqual(rows(window), ["#58"]);
    const ideas = [...window.document.querySelectorAll(".nav a")]
      .filter((a) => a.getAttribute("href") === "/ideas")[0];
    click(window, ideas);
    await settle();
    assert.equal(window.document.querySelector(".board-search-input").value, "");
    assert.ok(rows(window).length > 1, "the other board's search still cut the list: " + rows(window));
  });

  test("the same word typed on the other board does not reuse the old answer", async () => {
    /* Clearing `query` alone is not enough and the obvious test cannot
     * see it: `matches` is only consulted when `matchedQuery` equals what
     * is in the box, so the stale list stays invisible until the same
     * word is typed again -- and then it filters the *other* file by row
     * numbers answered for this one, before its own fetch lands. */
    let asked = 0;
    const window = await loadSite("/issues", {
      board: (url) => {
        if (!url.includes("q=")) return null;
        asked += 1;
        // Only the first board's search ever answers, so anything the
        // second board shows can only have come from the stale list.
        return asked === 1 ? { query: "cache", matches: [57] } : { query: "never", matches: [] };
      },
    });
    // "cache" is in no row title, so every row it shows can only have
    // come from a server answer -- which is what makes the stale one
    // visible. A word that also matches a title (like "gemini") would
    // hide the bug behind a legitimate match, and this test caught that
    // about itself before it caught anything about the code.
    typeSearch(window, "cache");
    await settle();
    assert.deepEqual(rows(window), ["#57"]);
    const ideas = [...window.document.querySelectorAll(".nav a")]
      .filter((a) => a.getAttribute("href") === "/ideas")[0];
    click(window, ideas);
    await settle();
    typeSearch(window, "cache");
    assert.deepEqual(rows(window), [], "a stale answer filtered the board it was not asked about");
  });

  test("a search answered after you have navigated away does not repaint", async () => {
    /* Every other loader in app.js checks the URL is still current before
     * painting. A debounce plus a round trip is long enough to tap the
     * nav, and without the guard the old board lands on top of the new
     * page while the nav highlights the new one. */
    const window = await loadSite("/issues", {
      board: (url) => (url.includes("q=") ? { query: "gemini", matches: [58] } : null),
    });
    typeSearch(window, "gemini");
    // Navigate while the debounce is still pending -- no settle first.
    const journal = [...window.document.querySelectorAll(".nav a")]
      .filter((a) => a.getAttribute("href") === "/")[0];
    click(window, journal);
    await settle();
    assert.equal(
      window.document.querySelector(".board-search-input"),
      null,
      "the board repainted over the page navigated to",
    );
  });

  test("the arrow flips the order and says which way it points", async () => {
    const window = await loadSite("/issues");
    click(window, chip(window, "All"));
    const before = rows(window);
    const arrow = window.document.querySelector(".board-sort-dir");
    // Ascending to start: the board's own row order, which is what it
    // showed before #70 and must keep showing until something is tapped.
    assert.equal(arrow.getAttribute("aria-pressed"), "false");
    assert.ok(!arrow.className.includes("desc"), "the arrow started rotated");
    click(window, arrow);
    const after = window.document.querySelector(".board-sort-dir");
    assert.equal(after.getAttribute("aria-pressed"), "true");
    assert.ok(after.className.includes("desc"), "the arrow did not turn");
    assert.deepEqual(rows(window), before.slice().reverse());
    click(window, window.document.querySelector(".board-sort-dir"));
    assert.deepEqual(rows(window), before, "a second tap did not come back");
  });

  test("sorting by priority puts unrated last whichever way the arrow points", async () => {
    /* ideas.md #69 settled that unrated is the absence of a priority, not
     * a low one. Both directions, because "last in one and first in the
     * other" is exactly the bucket that decision refused. */
    const window = await loadSite("/issues", {
      board: (url) => {
        if (url.includes("q=") || url.includes("item=")) return null;
        const board = JSON.parse(JSON.stringify(payload.board));
        board.items[0].priority = "⚪ Low";
        board.items[0].priorityKey = "low";
        board.items[1].priority = "🔴 Immediately";
        board.items[1].priorityKey = "immediately";
        return board;
      },
    });
    click(window, chip(window, "All"));
    const select = window.document.querySelector(".board-sort-select");
    select.value = "priority";
    select.dispatchEvent(new window.Event("change"));
    // Ascending: Low, then Immediately, then the two nobody has rated.
    assert.deepEqual(rows(window), ["#57", "#58", "#56", "#51"]);
    click(window, window.document.querySelector(".board-sort-dir"));
    // Descending flips the rated rows and leaves the unrated ones last.
    assert.deepEqual(rows(window).slice(0, 2), ["#58", "#57"]);
    assert.deepEqual(rows(window).slice(2).sort(), ["#51", "#56"]);
  });

  test("a board dated MM-DD still has an age, which is what the date filters read", async () => {
    /* Every row on both live boards reads `08-14`, with no year. A parser
     * that wanted `YYYY-MM-DD` would match none of them and both date
     * filters would be silently empty with every test green. */
    const window = await loadSite("/issues");
    const week = chip(window, "This week");
    const stale = chip(window, "Untouched");
    const counted = (c) => Number(c.textContent.match(/\((\d+)\)/)[1]);
    assert.ok(
      counted(week) + counted(stale) > 0,
      "no open row had a readable date: " + week.textContent + " " + stale.textContent,
    );
  });

  test("the default order is the file's, which is not the number's", async () => {
    /* The board is `## Board` newest-first with `## Done` appended after
     * it, so #51 sits *below* #56 while being the lower number. Sorting by
     * the number looks identical on the default Open view -- 57, 58 either
     * way -- which is how the first version of this shipped: it reordered
     * the All view and every existing test still passed. */
    const window = await loadSite("/issues");
    click(window, chip(window, "All"));
    assert.deepEqual(rows(window), ["#57", "#58", "#56", "#51"]);
  });

  test("a search that matches nothing says what it was looking for", async () => {
    const window = await loadSite("/issues");
    typeSearch(window, "zzzznothing");
    assert.deepEqual(rows(window), []);
    assert.match(window.document.querySelector(".empty").textContent, /zzzznothing/);
  });
});

/* the owner, comments board 2026-08-14, on the stall badge: "Or a display
 * error if the fetch failed, also".
 *
 * The header is the part of the page that answers "is the loop alive", and
 * it was the part with no failure state at all. A cold load that failed sat
 * on "loading…"; a poll that failed left the last good line standing,
 * unmarked. Both of those are the reassuring answer given at the one moment
 * the page has no evidence for it. */
describe("the header says so when it cannot reach the server", () => {
  const header = (window) => window.document.getElementById("status");

  test("a failed cold load replaces 'loading…' with an error, not silence", async () => {
    const window = await loadSite("/", { journalStatus: 502 });
    assert.match(header(window).textContent, /can't reach Nova/);
    assert.doesNotMatch(header(window).textContent, /loading…/);
    assert.ok(header(window).querySelector(".badge-error"));
  });

  test("the server's own message reaches the header, not just the feed", async () => {
    const window = await loadSite("/", {
      journalStatus: 500,
      journal: () => ({ error: "the journal folder is unreadable" }),
    });
    assert.match(header(window).textContent, /the journal folder is unreadable/);
  });

  test("a healthy load says nothing about reachability", async () => {
    const window = await loadSite("/");
    assert.doesNotMatch(header(window).textContent, /can't reach Nova/);
    assert.equal(header(window).querySelector(".badge-error"), null);
  });

  /* The whole point of the threshold: one dropped request on a phone is not
   * an outage, and flashing the header red for it would be the same
   * flash-and-retract that produced this complaint. */
  test("one failed poll is tolerated; the second is reported", async () => {
    let timers;
    const window = await loadSite("/", { install: (w) => { timers = captureTimers(w); } });
    const good = window.fetch;
    window.fetch = () => Promise.reject(new Error("network down"));
    await timers.firePagePoll();
    assert.doesNotMatch(header(window).textContent, /can't reach Nova/);
    await timers.firePagePoll();
    assert.match(header(window).textContent, /can't reach Nova/);

    /* And it recovers: a poll that comes back clears the error even when
     * the payload has not changed, which is the case that would otherwise
     * stay red for good once the loop went quiet. */
    window.fetch = good;
    await timers.firePagePoll();
    assert.doesNotMatch(header(window).textContent, /can't reach Nova/);
    assert.match(header(window).textContent, /Cycle /);
  });

  test("the last known line is kept, marked as stale rather than current", async () => {
    let timers;
    const window = await loadSite("/", { install: (w) => { timers = captureTimers(w); } });
    const before = header(window).querySelector(".status-line").textContent;
    window.fetch = () => Promise.reject(new Error("network down"));
    await timers.firePagePoll();
    await timers.firePagePoll();
    const line = header(window).querySelector(".status-line");
    assert.ok(line.classList.contains("is-stale"));
    assert.match(line.textContent, /as of the last load/);
    assert.ok(line.textContent.startsWith(before));
  });
});

/* Holding a boarded card -- the owner's issue #84.
 *
 * *"I need to be able to edit and especially delete boarded ideas and
 * issues from the agora app. If i hold the card for more than 1 second i
 * get into edit mode and also have the option of deleting, save or cancel
 * the edit."*
 *
 * These drive the real gesture through real events rather than calling
 * the handler, because the whole feature is the difference between a tap
 * and a hold and only the event sequence has that in it. */
describe("holding a board row opens edit mode", () => {
  const HOLD = 1000;
  const press = (window, node, type) =>
    node.dispatchEvent(new window.MouseEvent(type, { bubbles: true, cancelable: true }));
  /** A whole press: down, the second the owner asked for, then up and the
   *  click a browser sends after it. */
  const hold = async (window, node, ms = HOLD + 30) => {
    press(window, node, "mousedown");
    await new Promise((resolve) => setTimeout(resolve, ms));
    press(window, node, "mouseup");
    click(window, node);
    await new Promise((resolve) => setTimeout(resolve, 0));
  };
  const head = (window, number) =>
    window.document.getElementById("item-" + number).querySelector(".item-head");
  const act = (row, label) =>
    [...row.querySelectorAll(".capture-act")].filter((b) => b.textContent === label)[0];

  test("a one-second hold turns the row into a box holding its own title", async () => {
    const window = await loadSite("/issues");
    await hold(window, head(window, 57));
    const row = window.document.getElementById("item-57");
    const box = row.querySelector(".item-edit-input");
    assert.ok(box, "no editor after a hold");
    assert.equal(box.value, "More pages in the Nova app");
    assert.ok(act(row, "Save") && act(row, "Cancel") && act(row, "Delete"),
      "edit mode is missing one of save/cancel/delete");
    // The head stays -- it is how he sees which row is in the box -- but
    // it stops being a toggle. Asserting `hidden` here is what the first
    // version did, and it passed in jsdom while the real browser showed
    // the row still visible and still tappable: `.item-head` sets
    // `display: flex`, which beats the `[hidden]` user-agent rule.
    assert.ok(row.classList.contains("is-editing"));
    click(window, row.querySelector(".item-head"));
    assert.equal(row.querySelector(".item-head").getAttribute("aria-expanded"), "false",
      "tapping the head while editing opened the write-up under the box");
  });

  test("the click that ends a hold does not also open the write-up", async () => {
    /* The failure this exists for: a browser sends `click` after
     * `mouseup`, so without the flag the row toggles open underneath the
     * editor that just appeared. */
    const window = await loadSite("/issues");
    await hold(window, head(window, 57));
    assert.equal(head(window, 57).getAttribute("aria-expanded"), "false");
  });

  test("an ordinary tap still opens the write-up and opens no editor", async () => {
    const window = await loadSite("/issues");
    const node = head(window, 57);
    press(window, node, "mousedown");
    press(window, node, "mouseup");
    click(window, node);
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(node.getAttribute("aria-expanded"), "true");
    assert.equal(window.document.querySelector(".item-edit"), null,
      "a tap opened edit mode");
  });

  test("letting go early cancels the hold, and leaves no timer behind", async () => {
    /* Both halves matter. A press that ends at 200ms must not open the
     * editor -- and the timer it started must be cleared, or it fires
     * into a detached closure long after this test has finished, which
     * this suite has already been broken by once. */
    const window = await loadSite("/issues");
    const node = head(window, 57);
    press(window, node, "mousedown");
    await new Promise((resolve) => setTimeout(resolve, 200));
    press(window, node, "mouseup");
    await new Promise((resolve) => setTimeout(resolve, HOLD + 100));
    assert.equal(window.document.querySelector(".item-edit"), null,
      "a short press opened edit mode later");
  });

  test("Save sends the row's number and the new title", async () => {
    const window = await loadSite("/issues");
    await hold(window, head(window, 57));
    const row = window.document.getElementById("item-57");
    row.querySelector(".item-edit-input").value = "A better title";
    click(window, act(row, "Save"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted.length, 1);
    assert.equal(window.posted[0].url, "/api/board/edit");
    assert.deepEqual(window.posted[0].body,
      { target: "issues", number: 57, title: "A better title" });
  });

  test("Save on an untouched title posts nothing at all", async () => {
    /* Opening the editor and thinking better of it is the common case,
     * and it must not rewrite his file with the bytes it already holds. */
    const window = await loadSite("/issues");
    await hold(window, head(window, 57));
    const row = window.document.getElementById("item-57");
    click(window, act(row, "Save"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted.length, 0);
  });

  test("Delete asks first, and sends the number when it is allowed to", async () => {
    const window = await loadSite("/issues");
    window.confirm = () => true;
    await hold(window, head(window, 57));
    const row = window.document.getElementById("item-57");
    click(window, act(row, "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted.length, 1);
    assert.equal(window.posted[0].url, "/api/board/delete");
    assert.deepEqual(window.posted[0].body, { target: "issues", number: 57 });
  });

  test("a declined confirm deletes nothing", async () => {
    const window = await loadSite("/issues");
    window.confirm = () => false;
    await hold(window, head(window, 57));
    const row = window.document.getElementById("item-57");
    click(window, act(row, "Delete"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(window.posted.length, 0, "a declined confirm still deleted");
  });

  test("a hold works on a touch screen too, which is where he asked for it", async () => {
    const window = await loadSite("/issues");
    const node = head(window, 57);
    node.dispatchEvent(new window.Event("touchstart", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, HOLD + 30));
    node.dispatchEvent(new window.Event("touchend", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.ok(window.document.querySelector(".item-edit-input"), "no editor after a touch hold");
  });

  test("a hold that turns into a scroll cancels, rather than editing", async () => {
    const window = await loadSite("/issues");
    const node = head(window, 57);
    node.dispatchEvent(new window.Event("touchstart", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, 200));
    node.dispatchEvent(new window.Event("touchmove", { bubbles: true }));
    await new Promise((resolve) => setTimeout(resolve, HOLD + 100));
    assert.equal(window.document.querySelector(".item-edit"), null,
      "scrolling past a row opened edit mode");
  });

  test("the row's own text is not selectable, so a phone hold reaches the app", async () => {
    /* jsdom applies no stylesheet, so this reads the sheet. A one-second
     * hold on text is the browser's own gesture -- iOS pops the callout
     * menu on top of the editor -- and turning it off is what makes the
     * gesture reach this code at all. */
    await loadSite("/issues");
    const sheet = readFileSync(join(publicDir, "style.css"), "utf8");
    assert.match(sheet, /\.item-head\s*{[^}]*user-select:\s*none/);
    assert.match(sheet, /\.item-head\s*{[^}]*-webkit-touch-callout:\s*none/);
  });
});

/* ---- Commenting on a boarded row (idea #64) -----------------------------
 *
 * the owner: *"Lets me have the same comment conversation on ideas, notes and
 * issues like the Journal. Add a comment button and let me leave comments
 * that discuss each idea."* Rated 🔴 Immediately and open since 08-12 --
 * skipped by every cycle since, which is what he filed.
 *
 * There is no thread widget to test, and that is the design: the comment
 * is appended to the row's own write-up, which the open row already
 * fetches and renders. So what is worth pinning is the composer, what it
 * posts, and the one thing a "saved" message can lie about -- whether the
 * body he is looking at afterwards actually contains his sentence.
 */
describe("commenting on a boarded row", () => {
  const composer = (window) =>
    window.document.getElementById("item-57").querySelector(".item-comment");

  test("an open row offers a comment box under its write-up", async () => {
    const window = await loadSite("/issues#57");
    const box = composer(window);
    assert.ok(box, "the open row has no comment composer");
    assert.ok(box.querySelector(".item-comment-box"), "no text box");
    assert.equal(box.querySelector(".item-comment-send").textContent, "Comment");
    // Under the write-up, not above it -- the conversation accumulates
    // below his statement of the problem, the same order the file uses.
    const body = window.document.getElementById("item-57").querySelector(".item-body");
    const kids = [...body.children];
    assert.equal(kids[kids.length - 1], box, "the composer is not last in the body");
  });

  test("Comment posts the row it belongs to, as JSON", async () => {
    const window = await loadSite("/issues#57");
    composer(window).querySelector(".item-comment-box").value = "  Still wrong on my phone.  ";
    click(window, composer(window).querySelector(".item-comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/board/comment");
    assert.ok(posted, "no write reached /api/board/comment");
    assert.deepEqual(posted.body, {
      target: "issues",
      number: 57,
      text: "Still wrong on my phone.",
    });
  });

  test("a pasted line break is folded rather than sent, because the server refuses one", async () => {
    /* `append_detail_note` ends a write-up at the next heading, so a note
     * carrying a break truncates the block and takes every later line of
     * his own text off the page. The server returns 400; folding here is
     * what stops a paste from two lines of a note becoming an error he
     * has to read and retype. */
    const window = await loadSite("/issues#57");
    composer(window).querySelector(".item-comment-box").value = "one\n  two\r\nthree";
    click(window, composer(window).querySelector(".item-comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/board/comment");
    assert.equal(posted.body.text, "one two three");
  });

  test("an empty box posts nothing at all", async () => {
    const window = await loadSite("/issues#57");
    composer(window).querySelector(".item-comment-box").value = "   ";
    click(window, composer(window).querySelector(".item-comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(window.posted.find((p) => p.url === "/api/board/comment"), undefined);
    assert.match(composer(window).querySelector(".item-comment-status").textContent, /Nothing/);
  });

  /* The third verb in *"I can't delete, edit or upload a file to a boarded
   * issues"* (Cycle 320). Cycle 318 did delete and edit and filed this. */
  test("the composer carries an attach button and its hidden input", async () => {
    const window = await loadSite("/issues#57");
    const foot = composer(window).querySelector(".item-comment-foot");
    const button = foot.querySelector(".attach-btn");
    assert.ok(button, "no way to attach a file to a board comment");
    assert.equal(button.getAttribute("aria-label"), "Attach a file");
    // Detached inputs never fire `change` in some engines, which is the
    // kind of bug that only shows up on his phone.
    assert.ok(foot.querySelector("input[type=file]"), "the file input is not in the document");
    // Before Comment, so the primary action keeps the right edge.
    const order = [...foot.children].map((n) => n.className.split(" ")[0]);
    assert.ok(
      order.indexOf("attach-btn") < order.indexOf("item-comment-send"),
      `attach should sit before Comment, got ${order.join(",")}`,
    );
    /* And the tray it fills (Cycle 377), between the box and that row. The
     * upload tests live on the capture form because that is where the
     * fixture lets a file be picked -- so this composer's only exposure to
     * the feature is whether the tray is on the page at all, which is the
     * "built, tested, and dead on his screen" failure this suite exists
     * for. */
    const wrap = composer(window);
    const kids = [...wrap.children].map((n) => n.className.split(" ")[0]);
    assert.ok(kids.includes("attach-tray"), `no tray in the board composer, got ${kids.join(",")}`);
    assert.ok(
      kids.indexOf("attach-tray") > kids.indexOf("item-comment-box")
        && kids.indexOf("attach-tray") < kids.indexOf("item-comment-foot"),
      `the tray should sit under the box and above the buttons, got ${kids.join(",")}`,
    );
  });

  test("an attachment in the write-up renders as a picture, not as its markdown", async () => {
    /* The half that makes the button worth having. A board comment is
     * appended to the row's own write-up, which arrives as server-parsed
     * spans -- so without an `attach` span the file he just sent renders
     * as the literal `![shot.png](/api/upload/…)` text. */
    const window = await loadSite("/issues#57", {
      board: (url) => {
        if (!url.includes("item=57")) return null;
        return {
          ...payload.boardItem,
          item: {
            ...payload.boardItem.item,
            blocks: [
              { type: "p", spans: [
                { kind: "text", text: "here: " },
                { kind: "attach", text: "shot.png", url: "/api/upload/ab12.png", isImage: true },
              ] },
              { type: "p", spans: [
                { kind: "attach", text: "notes.pdf", url: "/api/upload/cd34.pdf", isImage: false },
              ] },
            ],
          },
        };
      },
    });
    const body = window.document.getElementById("item-57").querySelector(".item-body");
    const img = body.querySelector("img.attach-img");
    assert.ok(img, "the attached picture did not render as an image");
    assert.equal(img.getAttribute("src"), "/api/upload/ab12.png");
    assert.equal(img.getAttribute("alt"), "shot.png");
    const file = body.querySelector("a.attach-file");
    assert.ok(file, "the attached file did not render as a chip");
    assert.match(file.textContent, /notes\.pdf/);
    assert.equal(file.getAttribute("href"), "/api/upload/cd34.pdf");
    // And nothing anywhere still shows the raw construct.
    assert.ok(!body.textContent.includes("/api/upload/ab12.png"));
  });

  test("a saved comment refetches the write-up rather than leaving the old one on screen", async () => {
    /* The failure this pins is the one that reads as success: the status
     * says saved, and the body above it is the copy from before the
     * comment, so his own sentence is missing from the thread he just
     * added it to. Cycle 218 shipped a fix for exactly that shape on the
     * journal drawer. */
    let reads = 0;
    const window = await loadSite("/issues#57", {
      board: (url) => {
        if (!url.includes("item=57")) return null;
        reads += 1;
        return payload.boardItem;
      },
    });
    assert.ok(reads >= 1, "the open row never fetched its write-up");
    const before = reads;
    composer(window).querySelector(".item-comment-box").value = "Why is this still open?";
    click(window, composer(window).querySelector(".item-comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.ok(reads > before, "the write-up was not re-read after the comment landed");
  });

  test("a refused comment says so and keeps the text, rather than swallowing it", async () => {
    const window = await loadSite("/issues#57");
    window.postReply = { ok: false, message: "#57 is not a row on issues" };
    composer(window).querySelector(".item-comment-box").value = "Lost?";
    click(window, composer(window).querySelector(".item-comment-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    const status = composer(window).querySelector(".item-comment-status");
    assert.match(status.textContent, /not a row/);
    assert.equal(status.className, "item-comment-status is-error");
    assert.equal(composer(window).querySelector(".item-comment-box").value, "Lost?",
      "the comment was cleared on a failure and he would have to retype it");
  });
});

/* The `/plan` page (issues.md #7). `roadmap.md` and `goals.md` are the two
 * documents written so the owner could argue with Nova's prioritisation, and
 * until now the only way to read either was to open Obsidian.
 *
 * The server tests already pin the payload. What only a rendered DOM can
 * answer is whether he sees any of it: this repo has shipped a feature
 * that was built, tested, merged and completely dead on his screen. */
describe("the notes page", () => {
  /* the owner, issues.md 2026-08-21: "I do not have a notes page that shows
   * any overview of the notes made."
   *
   * And `notes.md` 2026-08-24, which turned it into a conversation:
   * "alternating posts are green (mine) and purple (Nova cycle
   * response) ... the conversation above the input box ... ordered with
   * the latest note at the bottom ... it should not start at the top and
   * i have to scroll all the way down ... lazy loaded so when i scroll up
   * they load".
   *
   * Five asks, and each has a test below. The fixture is in the order the
   * server now sends -- oldest first, unanswered last -- because a page
   * that re-sorted what it was given would pass a test written the other
   * way round and still be wrong on the live payload. */
  const feedText = (window) => window.document.getElementById("feed").textContent;
  const settle = () => new Promise((r) => setTimeout(r, 260));
  /* A local copy of the journal suite's observer stub, which is scoped
   * inside its own `describe`. Deliberately the smaller half of it: this
   * block only needs to know *which node* got watched, not to replay an
   * initial observation, so `install` records and never fires. */
  const observerSpy = () => {
    const watching = [];
    return {
      watching,
      install(window) {
        window.IntersectionObserver = class {
          constructor(callback, options) { this.callback = callback; this.options = options; }
          observe(node) { watching.push({ node, observer: this }); }
          disconnect() {
            for (let i = watching.length - 1; i >= 0; i -= 1) {
              if (watching[i].observer === this) watching.splice(i, 1);
            }
          }
        };
      },
    };
  };
  const note = (text, opts = {}) => ({
    text,
    blocks: [{ kind: "p", spans: [{ text }] }],
    responses: (opts.responses || []).map((r) => ({
      cycle: r.cycle === undefined ? null : r.cycle,
      blocks: [{ kind: "p", spans: [{ text: r.text }] }],
    })),
    answered: !!(opts.responses || []).length,
    waiting: !!opts.waiting,
    // The capture-list position, or null on anything the edit, delete and
    // convert endpoints cannot address -- every note under `## Read`. It is
    // what the server sends and what decides whether controls are drawn.
    index: opts.index === undefined ? null : opts.index,
  });
  const twoNotes = {
    waitingTotal: 1,
    readTotal: 1,
    notesTotal: 2,
    notes: [
      note("Platform-config billing block is fine as-is.", {
        responses: [{ cycle: 258, text: "Read Cycle 258. Recorded it." }],
      }),
      note("Nobody has read this one.", { waiting: true, index: 0 }),
    ],
  };
  const manyNotes = (count) => {
    const notes = [];
    for (let i = 1; i <= count; i += 1) notes.push(note("Note number " + i));
    return { waitingTotal: 0, readTotal: count, notesTotal: count, notes };
  };

  test("a vault with no notes is a page that says so, not an error", async () => {
    const window = await loadSite("/notes");
    assert.match(feedText(window), /No notes yet/);
    assert.equal(window.document.querySelectorAll(".note-msg").length, 0);
    assert.doesNotMatch(feedText(window), /Could not load/);
  });

  test("his messages are green and a cycle's are purple, both named in words", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const messages = [...window.document.querySelectorAll(".note-msg")];
    // Three messages from two notes: his, the cycle's answer, then his
    // unanswered one.
    assert.deepEqual(
      messages.map((m) => m.className.includes("note-msg-nova")),
      [false, true, false],
    );
    assert.deepEqual(
      messages.map((m) => m.querySelector(".note-msg-name").textContent),
      ["Edvard", "Nova", "Edvard"],
    );
    assert.match(messages[1].querySelector(".note-msg-body").textContent, /Recorded it/);
    assert.equal(messages[1].querySelector(".note-msg-cycle").getAttribute("href"), "/cycle/258");
  });

  test("the transcript is drawn in the order the server sent, not re-sorted", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const bodies = [...window.document.querySelectorAll(".note-msg-body")]
      .map((b) => b.textContent);
    assert.deepEqual(bodies, [
      "Platform-config billing block is fine as-is.",
      "Read Cycle 258. Recorded it.",
      "Nobody has read this one.",
    ]);
  });

  test("a waiting note says Waiting in words, not only in colour", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const messages = [...window.document.querySelectorAll(".note-msg")];
    const waiting = messages[messages.length - 1];
    assert.match(waiting.querySelector(".badge").textContent, /Waiting/);
    assert.ok(waiting.classList.contains("note-msg-waiting"));
    // And the answered one carries no badge at all -- the purple reply
    // under it is what says a cycle got there.
    assert.equal(messages[0].querySelector(".badge"), null);
  });

  test("the waiting count is the headline, because it is the question", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    assert.match(window.document.querySelector(".status-line").textContent, /1 note waiting/);
  });

  test("the composer sits under the conversation, not above it", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const feed = window.document.getElementById("feed");
    const capture = window.document.getElementById("capture");
    assert.equal(capture.parentNode, feed, "the composer is not in the feed");
    assert.equal(feed.lastElementChild, capture, "the composer is not the last thing on the page");
    // The one composer the shell has, not a second copy that could drift
    // from the handlers `captureBox()` bound at startup.
    assert.equal(window.document.querySelectorAll("#capture-form").length, 1);
    assert.equal(window.document.querySelectorAll(".capture-btn").length, 3);
  });

  test("navigating away puts the composer back rather than deleting it", async () => {
    /* The destructive one. Every renderer's first act is to empty the
     * feed, so a composer left inside it is gone -- and nothing rebinds
     * `captureBox()`, so the box would be missing from every page until
     * a reload. */
    const window = await loadSite("/notes", { notes: twoNotes });
    window.document.querySelector('.nav-tab[href="/issues"]').click();
    await settle();
    const capture = window.document.getElementById("capture");
    assert.ok(capture, "the composer was destroyed by navigating away from Notes");
    assert.equal(capture.parentNode, window.document.getElementById("feed").parentNode);
    assert.equal(window.document.querySelectorAll(".capture-btn").length, 3);
  });

  test("it opens on the newest message instead of at the top", async () => {
    const scrolls = [];
    const window = await loadSite("/notes", {
      notes: twoNotes,
      install: (w) => { w.scrollTo = (x, y) => scrolls.push(y); },
    });
    assert.ok(scrolls.length, "nothing scrolled the page at all");
    // jsdom lays nothing out, so scrollHeight is 0 and the value cannot
    // be asserted -- what is checkable is that the page asked to go to
    // the bottom of the document rather than to a fixed offset.
    assert.equal(scrolls[scrolls.length - 1], window.document.documentElement.scrollHeight);
  });

  test("older messages are behind a pager that the scroll watcher fires", async () => {
    const spy = observerSpy();
    const window = await loadSite("/notes", {
      notes: manyNotes(30),
      install: (w) => { spy.install(w); w.scrollTo = () => {}; },
    });
    const shown = [...window.document.querySelectorAll(".note-msg-body")].map((b) => b.textContent);
    assert.equal(shown.length, 12, "the page did not open on a window of the newest messages");
    assert.equal(shown[shown.length - 1], "Note number 30");
    assert.equal(shown[0], "Note number 19");
    const pager = window.document.querySelector(".note-older");
    assert.ok(pager, "no way to reach the older messages");
    assert.ok(spy.watching.some((one) => one.node === pager), "the notes pager is not watched");
    pager.click();
    const wider = [...window.document.querySelectorAll(".note-msg-body")].map((b) => b.textContent);
    assert.equal(wider.length, 24);
    assert.equal(wider[0], "Note number 7");
    assert.equal(wider[wider.length - 1], "Note number 30", "revealing older ones lost the newest");
  });

  test("scrolling up does not destroy the composer", async () => {
    /* The bug the reviewer caught after this shipped, and the reason it
     * is a separate test from the navigation one above: `captureHome()`
     * in `load()` covers arriving and leaving, and `showOlderNotes`
     * re-renders *in place* with the composer already inside the feed.
     * `feed.textContent = ""` then detached the one composer the whole
     * app has -- from every page, until a reload -- on the central
     * interaction of this feature. */
    const spy = observerSpy();
    const window = await loadSite("/notes", {
      notes: manyNotes(30),
      install: (w) => { spy.install(w); w.scrollTo = () => {}; },
    });
    window.document.querySelector(".note-older").click();
    const capture = window.document.getElementById("capture");
    assert.ok(capture, "scrolling up destroyed the composer");
    assert.equal(capture.parentNode, window.document.getElementById("feed"));
    assert.equal(window.document.querySelectorAll(".capture-btn").length, 3);
    // And it is still the last thing on the page, under the messages it
    // just revealed.
    assert.equal(window.document.getElementById("feed").lastElementChild, capture);
  });

  test("coming back to the page opens on the newest window again", async () => {
    const spy = observerSpy();
    const window = await loadSite("/notes", {
      notes: manyNotes(30),
      install: (w) => { spy.install(w); w.scrollTo = () => {}; },
    });
    window.document.querySelector(".note-older").click();
    assert.equal(window.document.querySelectorAll(".note-msg-body").length, 24);
    window.document.querySelector('.nav-tab[href="/issues"]').click();
    await settle();
    window.document.querySelector('.nav-tab[href="/notes"]').click();
    await settle();
    assert.equal(
      window.document.querySelectorAll(".note-msg-body").length,
      12,
      "the page reopened on a stale, widened window",
    );
  });

  /* The owner, issues.md 2026-08-24, on the page as it shipped:
   *
   * "Navigating to it takes me to the bottom of the page, but when i
   * navigate then to another page i'm scrolled down on that page to and
   * also the input box for ideas and issues are now gone. I have to
   * refresh Nova to get it back, so lots of bugs there."
   *
   * One mechanism, both symptoms. The pager at the *top* of the
   * conversation is watched by an IntersectionObserver, and nothing
   * disconnected it on the way out. So leaving the page ran
   * `window.scrollTo(0, 0)` in the link handler, which put that pager
   * on screen, which fired the watcher, which clicked it, which
   * re-rendered the whole notes conversation into the page he had just
   * navigated to -- moving the app's one composer into the feed on the
   * way, where the arriving page's `feed.textContent = ""` then deleted
   * it -- and finished by scrolling him back down to keep his place in a
   * conversation that was no longer on screen.
   *
   * Both tests below fire the observer by hand rather than trusting a
   * spy that only records. The three notes tests above this one all use
   * a stub that never fires, which is exactly why this survived them. */
  const firablePager = (spy, window) => {
    const pager = window.document.querySelector(".note-older");
    assert.ok(pager, "no pager on the notes page, so this test proves nothing");
    const watch = spy.watching.filter((one) => one.node === pager)[0];
    assert.ok(watch, "the notes pager is not watched, so this test proves nothing");
    return { pager, watch };
  };

  test("leaving the page stops the pager watching for a scroll", async () => {
    const spy = observerSpy();
    const window = await loadSite("/notes", {
      notes: manyNotes(30),
      install: (w) => { spy.install(w); w.scrollTo = () => {}; },
    });
    firablePager(spy, window);
    window.document.querySelector('.nav-tab[href="/issues"]').click();
    await settle();
    /* Matched on the class, not on the node captured before the
     * navigation. Against the unfixed code that identity check passes,
     * and it passes *because the bug fires*: leaving the page repaints
     * the conversation, which builds a second pager and disconnects the
     * first, so the node this test was holding is legitimately no longer
     * watched. The assertion was satisfied by the defect it was written
     * to catch -- the rubric's item 13, arrived at from an unexpected
     * direction. Any live notes pager is the honest question. */
    assert.equal(
      spy.watching.filter((one) => one.node.className.includes("note-older")).length,
      0,
      "a notes pager is still being watched from another page",
    );
  });

  test("a pager that fires after the page is gone changes nothing", async () => {
    const scrolls = [];
    const spy = observerSpy();
    const window = await loadSite("/notes", {
      notes: manyNotes(30),
      install: (w) => { spy.install(w); w.scrollTo = (x, y) => scrolls.push(y); },
    });
    const { pager, watch } = firablePager(spy, window);
    window.document.querySelector('.nav-tab[href="/issues"]').click();
    await settle();
    scrolls.length = 0;
    // What the browser does when `scrollTo(0, 0)` brings a pager that
    // nobody disconnected into view. The node is detached by now, so this
    // is the only way to reach that code path from a test.
    watch.observer.callback([{ isIntersecting: true, target: pager }]);
    await settle();
    const feed = window.document.getElementById("feed");
    assert.equal(
      feed.querySelectorAll(".note-msg").length,
      0,
      "the notes conversation was repainted over another page",
    );
    const capture = window.document.getElementById("capture");
    assert.ok(capture, "the composer was destroyed from every page");
    assert.equal(capture.parentNode, feed.parentNode, "the composer is inside the feed on a board page");
    assert.equal(window.document.querySelectorAll(".capture-btn").length, 3);
    assert.deepEqual(scrolls, [], "the notes page scrolled a page it was no longer on");
  });

  test("the top of the conversation says so instead of offering a pager", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    assert.equal(window.document.querySelector(".note-older"), null);
    assert.match(window.document.querySelector(".note-start").textContent, /beginning/i);
  });

  test("a note moved to Read with no reply is not drawn as answered", async () => {
    const window = await loadSite("/notes", {
      notes: {
        waitingTotal: 0,
        readTotal: 1,
        notesTotal: 1,
        notes: [note("Moved and never written up.")],
      },
    });
    assert.match(feedText(window), /no reply written/i);
    assert.equal(window.document.querySelectorAll(".note-msg-cycle").length, 0);
  });

  /* Edit, delete and convert on the notes page.
   *
   * The owner, 2026-08-24: *"The note i sent regarding the rebuilding the
   * notes page was sent as a note, but its actually an idea, but i have no
   * way of changing it or editing it. So we need crude operations for
   * notes, but also the possibility to change issues/ideas/notes into one
   * of the other."* */
  /* A note's controls moved into the same long-press sheet the two boards
   * use -- the owner asked for it on all three surfaces at once ("Do this
   * for issues, ideas and notes"), so the gesture is driven here the same
   * way, as real events including the click a browser sends on release.
   *
   * `noteActs` still reads the message itself, and its meaning has
   * inverted on purpose: it is now what proves a note has *no* controls,
   * and `holdNote` returning null is the other half of that. */
  const noteActs = (window, i = 0) =>
    [...window.document.querySelectorAll(".note-msg")[i].querySelectorAll(".capture-act")];
  const holdNote = async (window, i = 0) => {
    const body = window.document.querySelectorAll(".note-msg")[i]
      .querySelector(".note-msg-body");
    const fire = (type) => body.dispatchEvent(
      new window.MouseEvent(type, { bubbles: true, cancelable: true }));
    fire("mousedown");
    await new Promise((resolve) => setTimeout(resolve, 1030));
    fire("mouseup");
    click(window, body);
    await settle();
    return window.document.querySelector(".action-sheet");
  };
  const actNamed = (sheet, name) =>
    [...sheet.querySelectorAll(".capture-act")].filter((b) => b.textContent === name)[0];
  /* Save and Cancel are the exception: they belong to the editor that
   * replaced the button row on the card, and holding again to find them
   * would reopen the sheet over the box being typed into. */
  const saveOn = (window, i = 0) =>
    noteActs(window, i).filter((b) => b.textContent === "Save")[0];

  test("a waiting note carries Edit, Delete and the two conversions", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const waitingIndex = [...window.document.querySelectorAll(".note-msg")]
      .findIndex((m) => m.className.includes("note-msg-waiting"));
    // Nothing on the card before the gesture -- that is the ask.
    assert.equal(noteActs(window, waitingIndex).length, 0,
      "the controls are on the note before it has been held");
    const sheet = await holdNote(window, waitingIndex);
    assert.ok(sheet, "a one-second hold on a waiting note opened no action sheet");
    assert.deepEqual(
      [...sheet.querySelectorAll(".capture-act")].map((b) => b.textContent),
      ["Edit", "Make issue", "Make idea", "Delete"],
      "Delete must stay last -- it is the destructive one",
    );
  });

  test("a note a cycle has already answered has no controls at all", async () => {
    /* Rewriting it would leave the reply underneath answering text that is
     * gone, and the edit/delete/convert endpoints cannot address it in any case: the server
     * sends `index: null` for everything under `## Read`. */
    const window = await loadSite("/notes", { notes: twoNotes });
    const answered = [...window.document.querySelectorAll(".note-msg")]
      .filter((m) => !m.className.includes("note-msg-waiting"))
      .filter((m) => !m.className.includes("note-msg-nova"));
    assert.ok(answered.length, "the fixture has no read note, so this proves nothing");
    answered.forEach((m) =>
      assert.equal(m.querySelectorAll(".capture-act").length, 0));
    /* And holding it opens nothing either. Since the controls left the
     * card, counting them there would now pass on a note that *does* have
     * them -- so the gesture is the assertion that still has teeth. */
    const answeredIndex = [...window.document.querySelectorAll(".note-msg")]
      .indexOf(answered[0]);
    assert.equal(await holdNote(window, answeredIndex), null,
      "an answered note opened an action sheet");
  });

  test("a waiting note with no index gets no controls either", async () => {
    /* The two parsers disagreed. Drawing nothing is the answer; drawing a
     * Delete would hand the server an index pointing at a different line
     * of his file. */
    const window = await loadSite("/notes", {
      notes: {
        waitingTotal: 1,
        readTotal: 0,
        notesTotal: 1,
        notes: [note("Nobody has read this one.", { waiting: true })],
      },
    });
    assert.equal(window.document.querySelectorAll(".capture-act").length, 0);
    assert.equal(await holdNote(window, 0),
      null, "a note the server could not address opened an action sheet");
  });

  test("Make idea posts the note's own address, and the target board", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const waitingIndex = [...window.document.querySelectorAll(".note-msg")]
      .findIndex((m) => m.className.includes("note-msg-waiting"));
    click(window, actNamed(await holdNote(window, waitingIndex), "Make idea"));
    await settle();
    const posted = window.posted.find((p) => p.url === "/api/capture/convert");
    assert.ok(posted, "no write reached /api/capture/convert");
    assert.equal(posted.body.from, "notes");
    assert.equal(posted.body.to, "ideas");
    assert.equal(posted.body.index, 0);
    assert.equal(posted.body.original, "Nobody has read this one.");
  });

  test("a second tap on Make idea cannot fire a second conversion", async () => {
    /* The destination write is unconditional, so a double tap on a slow
     * connection lands a second copy in the target file and the removal
     * then fails because the first tap already took the line. The first
     * version of `convertButtons` disabled only the caller's Edit and
     * Delete and left its own buttons live for the whole fetch. */
    const window = await loadSite("/notes", { notes: twoNotes });
    const waitingIndex = [...window.document.querySelectorAll(".note-msg")]
      .findIndex((m) => m.className.includes("note-msg-waiting"));
    const btn = actNamed(await holdNote(window, waitingIndex), "Make idea");
    click(window, btn);
    assert.equal(btn.disabled, true, "the button stayed live during its own fetch");
    click(window, btn);
    await settle();
    assert.equal(
      window.posted.filter((p) => p.url === "/api/capture/convert").length,
      1,
      "a double tap converted twice",
    );
  });

  test("Delete on a note asks first and never fires when refused", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const waitingIndex = [...window.document.querySelectorAll(".note-msg")]
      .findIndex((m) => m.className.includes("note-msg-waiting"));
    window.confirm = () => false;
    click(window, actNamed(await holdNote(window, waitingIndex), "Delete"));
    await settle();
    assert.equal(window.posted.filter((p) => p.url.includes("delete")).length, 0);
    window.confirm = () => true;
    click(window, actNamed(await holdNote(window, waitingIndex), "Delete"));
    await settle();
    const posted = window.posted.find((p) => p.url === "/api/capture/delete");
    assert.ok(posted, "a confirmed delete never reached the server");
    assert.equal(posted.body.target, "notes");
    assert.equal(posted.body.index, 0);
  });

  test("Edit opens the raw text of the note, not the rendered blocks", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const waitingIndex = [...window.document.querySelectorAll(".note-msg")]
      .findIndex((m) => m.className.includes("note-msg-waiting"));
    click(window, actNamed(await holdNote(window, waitingIndex), "Edit"));
    const box = window.document.querySelector(".note-acts .capture-input");
    assert.ok(box, "Edit opened no box");
    assert.equal(box.value, "Nobody has read this one.");
    box.value = "   ";
    click(window, saveOn(window, waitingIndex));
    await settle();
    assert.equal(
      window.posted.filter((p) => p.url === "/api/capture/edit").length,
      0,
      "emptying the box is not how a note is deleted",
    );
    box.value = "Actually an idea.";
    click(window, saveOn(window, waitingIndex));
    await settle();
    const posted = window.posted.find((p) => p.url === "/api/capture/edit");
    assert.ok(posted, "no write reached /api/capture/edit");
    assert.equal(posted.body.target, "notes");
    assert.equal(posted.body.text, "Actually an idea.");
    assert.equal(posted.body.original, "Nobody has read this one.");
  });

  test("a failed fetch says so instead of leaving the last page up", async () => {
    const window = await loadSite("/notes", { notesStatus: 502 });
    assert.match(feedText(window), /Could not load the notes/);
  });

  test("the nav marks Notes, and only Notes", async () => {
    const window = await loadSite("/notes", { notes: twoNotes });
    const on = [...window.document.querySelectorAll(".nav-tab")]
      .filter((tab) => tab.classList.contains("on"))
      .map((tab) => tab.getAttribute("href"));
    assert.deepEqual(on, ["/notes"]);
  });
});

describe("the plan page", () => {
  const twoDocuments = {
    documents: [
      {
        key: "roadmap", label: "Roadmap", title: "Roadmap",
        updated: "2026-08-16", missing: false,
        sections: [
          { level: 0, heading: null, blocks: [
            { type: "p", spans: [{ kind: "text", text: "Written by Nova, Cycle 226." }] },
          ] },
          { level: 2, heading: "The five I would do next, in order", blocks: [
            { type: "p", spans: [
              { kind: "strong", text: "1. Get CI back." },
              { kind: "text", text: " Not my work — yours." },
            ] },
            { type: "li", spans: [{ kind: "text", text: "One item" }] },
          ] },
        ],
      },
      {
        key: "goals", label: "Goals", title: "Goals",
        updated: "2026-08-17", missing: false,
        sections: [
          { level: 2, heading: "Weekly review", blocks: [] },
          { level: 3, heading: "2026-08-17 — week of 08-16", blocks: [
            { type: "quote", spans: [{ kind: "text", text: "What moved." }] },
          ] },
        ],
      },
    ],
  };

  test("both documents paint, with their headings at their own depth", async () => {
    const window = await loadSite("/plan", { plan: twoDocuments });
    const cards = window.document.querySelectorAll(".plan-card");
    assert.equal(cards.length, 2);
    assert.deepEqual(
      [...window.document.querySelectorAll(".plan-title")].map((h) => h.textContent),
      ["Roadmap", "Goals"]
    );
    // `##` is an h3 and `###` an h4, because the card's own title is the
    // h2. A section heading rendered larger than the document it is inside
    // is the shape this asserts against.
    assert.equal(window.document.querySelectorAll("h3.plan-heading").length, 2);
    assert.equal(window.document.querySelectorAll("h4.plan-heading").length, 1);
    assert.match(window.document.querySelector(".plan-updated").textContent, /2026-08-16/);
  });

  test("the standfirst above the first heading is rendered, not dropped", async () => {
    const window = await loadSite("/plan", { plan: twoDocuments });
    assert.match(window.document.querySelector(".plan-card").textContent,
      /Written by Nova, Cycle 226\./);
  });

  test("bullets and quotes render as bullets and quotes", async () => {
    const window = await loadSite("/plan", { plan: twoDocuments });
    assert.equal(window.document.querySelectorAll(".plan-section li").length, 1);
    assert.equal(window.document.querySelectorAll(".plan-section blockquote").length, 1);
    assert.equal(window.document.querySelector(".plan-section strong").textContent,
      "1. Get CI back.");
  });

  test("a numbered list renders as an ol, and a bullet run beside it stays a ul", async () => {
    // The reviewer's finding on this page: `goals.md`'s G5 is a real
    // three-item numbered list, and everything here rendered it as one
    // run-on paragraph. The mixed run is asserted because the two kinds
    // share one open element in `renderBlocks`, so a fix that only
    // handled `oli` would have put numbered items inside the `ul`.
    const window = await loadSite("/plan", { plan: { documents: [{
      key: "goals", label: "Goals", title: "Goals", updated: "", missing: false,
      sections: [{ level: 2, heading: "G5", blocks: [
        { type: "li", spans: [{ kind: "text", text: "A bullet" }] },
        { type: "oli", spans: [{ kind: "text", text: "Share of spend" }] },
        { type: "oli", spans: [{ kind: "text", text: "Median weighted tokens" }] },
      ] }],
    }] } });
    const section = window.document.querySelector(".plan-section");
    assert.equal(section.querySelectorAll("ul").length, 1);
    assert.equal(section.querySelectorAll("ol").length, 1);
    assert.equal(section.querySelector("ul").children.length, 1);
    assert.equal(section.querySelector("ol").children.length, 2);
    assert.equal(section.querySelector("ol").children[0].textContent, "Share of spend");
  });

  test("the nav tab marks itself, so he can tell which page he is on", async () => {
    const window = await loadSite("/plan", { plan: twoDocuments });
    const tab = window.document.querySelector(".nav-tab[href='/plan']");
    assert.ok(tab.classList.contains("on"));
    assert.equal(tab.getAttribute("aria-current"), "page");
  });

  test("a document that does not exist yet says so instead of vanishing", async () => {
    const window = await loadSite("/plan", { plan: { documents: [
      twoDocuments.documents[0],
      { key: "goals", label: "Goals", title: "Goals", updated: "", missing: true, sections: [] },
    ] } });
    assert.equal(window.document.querySelectorAll(".plan-card").length, 2,
      "the missing document was dropped rather than shown as missing");
    assert.match(window.document.querySelectorAll(".plan-card")[1].textContent,
      /Not written yet/);
  });

  test("a failed fetch says so rather than leaving the page blank", async () => {
    const window = await loadSite("/plan", { planStatus: 502 });
    assert.match(window.document.querySelector("#feed").textContent, /Could not load the plan/);
  });

  /* The goals scoreboard (issue #96). The owner: "It is just a huge wall of
   * text. I hate that ... i understand visuals much faster."
   *
   * What only the DOM can answer here is whether the numbers are readable
   * without decoding a colour, and whether the bar can quietly lie. */
  const scored = (rows) => ({
    documents: [{
      key: "goals", label: "Goals", title: "Goals", updated: "2026-08-20",
      missing: false, scoreboard: rows,
      sections: [{ level: 2, heading: "The slate", blocks: [
        { type: "p", spans: [{ kind: "text", text: "The reasoning." }] },
      ] }],
    }],
  });

  const G1 = {
    name: "G1 — The loop works on what you asked for",
    measure: "Merged PRs per board row closed",
    now: "2.8", target: "2.0", unit: "PRs per closed row",
    direction: "down", nowValue: 2.8, targetValue: 2.0, onTarget: false,
  };

  test("the scoreboard paints above the prose, not below it", async () => {
    const window = await loadSite("/plan", { plan: scored([G1]) });
    const card = window.document.querySelector(".plan-card");
    // By class token, not by the whole `className`: a folded section
    // carries `plan-section plan-fold`, and an exact-string match here
    // silently became `indexOf(...) === -1` when the fold shipped.
    const kids = [...card.children].map((n) => [...n.classList]);
    const at = (name) => kids.findIndex((c) => c.includes(name));
    assert.ok(at("goal-board") < at("plan-section") && at("plan-section") !== -1,
      "the answer goes above the argument for it: " + JSON.stringify(kids));
    assert.equal(window.document.querySelectorAll(".goal-row").length, 1);
  });

  test("every value is printed as text, so nothing is carried by colour alone", async () => {
    const window = await loadSite("/plan", { plan: scored([G1]) });
    const row = window.document.querySelector(".goal-row");
    assert.match(row.textContent, /G1 — The loop works on what you asked for/);
    assert.match(row.textContent, /Merged PRs per board row closed/);
    assert.match(row.textContent, /2\.8 PRs per closed row/);
    assert.match(row.textContent, /target 2\.0/);
    // The verdict is a word. The class is the second encoding of it.
    assert.equal(row.querySelector(".goal-verdict").textContent, "Off target");
    assert.ok(row.querySelector(".goal-verdict").classList.contains("off"));
  });

  test("the bar puts now and target on one shared scale", async () => {
    const window = await loadSite("/plan", { plan: scored([G1]) });
    const row = window.document.querySelector(".goal-row");
    // now 2.8 is the larger of the two, so it fills the track and the
    // target tick sits at 2.0/2.8 — the gap between them is the message.
    assert.equal(row.querySelector(".goal-fill").style.width, "100%");
    assert.match(row.querySelector(".goal-tick").style.left, /^71\.4285/);
  });

  test("a goal whose number is still a sentence gets a row and no bar", async () => {
    const vague = { ...G1, now: "about 2.8", nowValue: null, onTarget: null };
    const window = await loadSite("/plan", { plan: scored([vague]) });
    const row = window.document.querySelector(".goal-row");
    assert.match(row.textContent, /about 2\.8/);
    assert.equal(row.querySelector(".goal-track"), null,
      "a bar drawn from a sentence is the failure this block exists to avoid");
    assert.equal(row.querySelector(".goal-verdict"), null,
      "no number means no verdict, rather than a guessed one");
  });

  test("a goal with no target says so instead of leaving a blank", async () => {
    const open = { ...G1, target: "", targetValue: null, onTarget: null };
    const window = await loadSite("/plan", { plan: scored([open]) });
    assert.match(window.document.querySelector(".goal-row").textContent, /no target set/);
  });

  /* The weekly history line (idea #38): "once a week come back to the goals
   * and see how much work has been done towards them ... show some history
   * in some charts". Until this, `now:` was overwritten every Monday, so the
   * page could never show a direction. */

  test("a goal's past readings draw a line and are also printed as words", async () => {
    const withHistory = { ...G1, history: [
      { date: "2026-08-16", cycle: 229, value: 2.8 },
      { date: "2026-08-17", cycle: 257, value: 2.5 },
    ] };
    const window = await loadSite("/plan", { plan: scored([withHistory]) });
    const row = window.document.querySelector(".goal-row");
    assert.equal(row.querySelectorAll(".goal-spark-line").length, 1);
    assert.equal(row.querySelectorAll(".goal-spark-dot").length, 2);
    // The shape is the summary; the words are the record. A reader who has
    // to squint at a 34px line has not been told anything.
    assert.match(row.querySelector(".goal-history-text").textContent,
      /08-16 2\.8 PRs per closed row.*08-17 2\.5 PRs per closed row/);
  });

  test("the line spans the full range, so a small real move is visible", async () => {
    const withHistory = { ...G1, history: [
      { date: "2026-08-16", cycle: 229, value: 2.8 },
      { date: "2026-08-17", cycle: 257, value: 2.5 },
    ] };
    const window = await loadSite("/plan", { plan: scored([withHistory]) });
    const points = window.document.querySelector(".goal-spark-line").getAttribute("points");
    const ys = points.split(" ").map((pair) => Number(pair.split(",")[1]));
    // 2.8 is the high value so it sits at the top of the box, 2.5 at the
    // bottom. Anchoring the scale at zero instead would draw both of these
    // as the same flat line, which is the chart lying by omission.
    assert.equal(ys[0], 3);
    assert.equal(ys[1], 31);
  });

  test("one reading is a dot, not a line, and not a hidden row", async () => {
    const once = { ...G1, history: [{ date: "2026-08-17", cycle: 257, value: 2.5 }] };
    const window = await loadSite("/plan", { plan: scored([once]) });
    const row = window.document.querySelector(".goal-row");
    assert.equal(row.querySelector(".goal-spark-line"), null);
    assert.equal(row.querySelectorAll(".goal-spark-dot").length, 1);
    assert.match(row.querySelector(".goal-history-text").textContent, /08-17 2\.5/);
  });

  test("a flat series sits on the middle line rather than dividing by zero", async () => {
    const flat = { ...G1, history: [
      { date: "2026-08-16", cycle: 229, value: 4 },
      { date: "2026-08-17", cycle: 257, value: 4 },
    ] };
    const window = await loadSite("/plan", { plan: scored([flat]) });
    const points = window.document.querySelector(".goal-spark-line").getAttribute("points");
    assert.ok(points.split(" ").every((pair) => Number(pair.split(",")[1]) === 17), points);
  });

  test("a goal with no history yet keeps its row and grows no empty chart", async () => {
    const window = await loadSite("/plan", { plan: scored([{ ...G1, history: [] }]) });
    const row = window.document.querySelector(".goal-row");
    assert.match(row.textContent, /2\.8 PRs per closed row/);
    assert.equal(row.querySelector(".goal-history"), null);
  });

  test("a document with no scoreboard renders exactly as it did before", async () => {
    const window = await loadSite("/plan", { plan: twoDocuments });
    assert.equal(window.document.querySelector(".goal-board"), null);
    assert.equal(window.document.querySelectorAll(".plan-card").length, 2);
  });

  /* The roadmap's ranked strip (issue #96, design item 2). The DOM is the
   * only place that can answer whether the status reaches him as a word
   * rather than as a coloured circle he cannot tell apart. */
  const ranked = (rows, done) => ({
    documents: [{
      key: "roadmap", label: "Roadmap", title: "Roadmap", updated: "2026-08-21",
      missing: false, scoreboard: [], ranked: rows, rankedDone: done,
      sections: [{ level: 2, heading: "The five I would do next, in order", blocks: [
        { type: "p", spans: [{ kind: "text", text: "The argument." }] },
      ] }],
    }],
  });

  const R1 = {
    rank: "1", title: "Get CI back",
    claim: "Not my work — yours, and it is two minutes.",
    board: "idea #73", statusSymbol: "\u{1F7E1}", statusLabel: "In progress",
  };

  test("the strip paints above the argument for it", async () => {
    const window = await loadSite("/plan", { plan: ranked([R1]) });
    const kids = [...window.document.querySelector(".plan-card").children].map((n) => [...n.classList]);
    const at = (name) => kids.findIndex((c) => c.includes(name));
    assert.ok(at("rank-strip") < at("plan-section") && at("plan-section") !== -1,
      "the answer goes above the argument: " + JSON.stringify(kids));
    assert.equal(window.document.querySelectorAll(".rank-card").length, 1);
  });

  test("the status chip carries the word, not just the symbol", async () => {
    const window = await loadSite("/plan", { plan: ranked([R1]) });
    const card = window.document.querySelector(".rank-card");
    assert.equal(card.querySelector(".rank-chip").textContent, "\u{1F7E1} In progress");
    assert.match(card.textContent, /Get CI back/);
    assert.match(card.textContent, /two minutes/);
    assert.match(card.textContent, /idea #73/);
    assert.equal(card.querySelector(".rank-num").textContent, "1");
  });

  test("a status the server did not recognise gets no chip rather than a guessed one", async () => {
    const unknown = { ...R1, statusSymbol: "", statusLabel: "" };
    const window = await loadSite("/plan", { plan: ranked([unknown]) });
    assert.equal(window.document.querySelector(".rank-chip"), null);
    assert.match(window.document.querySelector(".rank-card").textContent, /Get CI back/);
  });

  test("a document with no ranked strip renders exactly as it did before", async () => {
    const window = await loadSite("/plan", { plan: twoDocuments });
    assert.equal(window.document.querySelector(".rank-strip"), null);
    assert.equal(window.document.querySelectorAll(".plan-card").length, 2);
  });

  /* The two lists (2026-08-25). The heading is a claim about every card
   * under it, and on that morning three of the five cards under "What I
   * would do next, in order" were finished. Only the DOM can say whether
   * a reader can tell which list a card is in. */
  const DONE3 = {
    rank: "3", title: "Fix my vault write path",
    claim: "It was garbage collection, not a write bug.",
    board: "idea #61", statusSymbol: "✅", statusLabel: "Done",
  };

  test("a finished card sits under its own heading, not under the one saying it is next", async () => {
    const window = await loadSite("/plan", { plan: ranked([R1], [DONE3]) });
    const lists = window.document.querySelectorAll(".rank-list");
    assert.equal(lists.length, 2);
    assert.match(lists[0].textContent, /Get CI back/);
    assert.doesNotMatch(lists[0].textContent, /vault write path/,
      "a done card under 'what I would do next' is the whole bug");
    assert.match(lists[1].textContent, /vault write path/);
    assert.ok(lists[1].classList.contains("rank-done-list"));
    const titles = [...window.document.querySelectorAll(".rank-strip-title")].map((h) => h.textContent);
    assert.deepEqual(titles, ["What I would do next, in order", "Already finished"]);
    // The rank number travels with the card. The file numbers an item once
    // and never renumbers, so a finished 3 has to still read as 3.
    assert.equal(lists[1].querySelector(".rank-num").textContent, "3");
  });

  test("a roadmap whose every item is finished says so instead of promising five next steps", async () => {
    const window = await loadSite("/plan", { plan: ranked([], [DONE3]) });
    assert.match(window.document.querySelector(".rank-strip").textContent,
      /Nothing on this list is still open/);
    assert.equal(window.document.querySelectorAll(".rank-list").length, 1,
      "no empty list under the heading that says what is next");
    assert.match(window.document.querySelector(".rank-done-list").textContent,
      /vault write path/);
  });

  test("with nothing finished the strip is exactly what it was", async () => {
    const window = await loadSite("/plan", { plan: ranked([R1], []) });
    assert.equal(window.document.querySelectorAll(".rank-list").length, 1);
    assert.equal(window.document.querySelector(".rank-done-title"), null);
    assert.match(window.document.querySelector(".rank-strip-note").textContent,
      /The argument for each one is below/);
  });
});

/* The per-section fold (issue #96, design items 4 and 5).
 *
 * The DOM is the only place that can answer whether the prose is actually
 * behind a control and whether the heading survived going into a summary.
 * The server decides `open`; these assert the client honours it. */
describe("the plan page folds its prose", () => {
  const folded = (sections) => ({
    documents: [{
      key: "goals", label: "Goals", title: "Goals", updated: "2026-08-21",
      missing: false, scoreboard: [], ranked: [], sections,
    }],
  });
  const prose = [{ type: "p", spans: [{ kind: "text", text: "The reasoning." }] }];

  test("a headed section becomes a details, closed, with its heading in the summary", async () => {
    const window = await loadSite("/plan", {
      plan: folded([{ level: 2, heading: "The slate", blocks: prose, open: false }]),
    });
    const fold = window.document.querySelector(".plan-fold");
    assert.equal(fold.tagName, "DETAILS");
    assert.equal(fold.open, false);
    // The heading is *inside* the summary, not replaced by it -- the scan
    // down the left edge has to read the same headings it always did.
    assert.equal(fold.querySelector("summary h3.plan-heading").textContent, "The slate");
    assert.match(fold.querySelector(".plan-fold-body").textContent, /The reasoning\./);
  });

  test("the server's open flag is what opens a section", async () => {
    const window = await loadSite("/plan", {
      plan: folded([
        { level: 3, heading: "2026-08-17 — newest", blocks: prose, open: true },
        { level: 3, heading: "2026-08-16 — older", blocks: prose, open: false },
      ]),
    });
    const folds = [...window.document.querySelectorAll(".plan-fold")];
    assert.deepEqual(folds.map((f) => f.open), [true, false]);
    assert.equal(folds[0].querySelector("summary h4.plan-heading").textContent,
      "2026-08-17 — newest");
  });

  test("the standfirst is not a fold at all", async () => {
    const window = await loadSite("/plan", {
      plan: folded([{ level: 0, heading: null, blocks: prose, open: true }]),
    });
    assert.equal(window.document.querySelector(".plan-fold"), null);
    assert.match(window.document.querySelector(".plan-section").textContent, /The reasoning\./);
  });

  test("a closed parent does not swallow the open review beneath it", async () => {
    // The real shape of `goals.md`, and the one the other fixtures here
    // miss: `## Weekly review` has prose of its own, so it is a *non-empty*
    // closed fold sitting directly above an open one. Reviewer finding on
    // #269. What makes this work is that sections are flat siblings rather
    // than nested by level -- so this asserts the sibling relationship
    // directly, because a later cycle nesting them would hide the newest
    // review with every other test still green.
    const window = await loadSite("/plan", {
      plan: folded([
        { level: 2, heading: "Weekly review", open: false,
          blocks: [{ type: "p", spans: [{ kind: "text", text: "Appended once a week." }] }] },
        { level: 3, heading: "2026-08-17 — the newest", open: true, blocks: prose },
        { level: 3, heading: "2026-08-16 — the older", open: false, blocks: prose },
      ]),
    });
    const folds = [...window.document.querySelectorAll(".plan-fold")];
    assert.deepEqual(folds.map((f) => f.open), [false, true, false]);
    const [parent, newest] = folds;
    assert.equal(parent.contains(newest), false, "the open review must not be inside the closed parent");
    assert.equal(newest.parentNode, parent.parentNode, "they are siblings");
    assert.match(newest.querySelector(".plan-fold-body").textContent, /The reasoning\./);
  });

  test("a heading with an empty body renders plainly rather than as a control that lies", async () => {
    const window = await loadSite("/plan", {
      plan: folded([{ level: 2, heading: "Nothing under here", blocks: [], open: false }]),
    });
    assert.equal(window.document.querySelector(".plan-fold"), null);
    assert.equal(window.document.querySelector("h3.plan-heading").textContent,
      "Nothing under here");
  });
});

/* The Questions page.
 *
 * the owner, ideas.md 2026-08-19: "Make a questions page in Nova where i can
 * ask questions in a box and a Claude sonnet model answers me."
 *
 * The behaviour worth pinning is the one this page has and no other page
 * does: the answer does not come back with the request. It arrives a poll
 * later, from a persona the runner speaks for, so "the question was sent"
 * and "the answer showed up" are two separate things and both can fail on
 * their own.
 */
describe("the questions page", () => {
  test("an unused page offers the box and says so", async () => {
    const window = await loadSite("/ask");
    assert.ok(window.document.querySelector(".ask-box"), "no question box");
    assert.ok(window.document.querySelector(".ask-send"), "no send button");
    assert.match(window.document.querySelector(".ask-thread .empty").textContent, /Ask me anything/);
  });

  test("an existing thread renders question and answer, his own marked as his", async () => {
    const window = await loadSite("/ask", {
      ask: {
        conversationId: "c-ask",
        waiting: false,
        messages: [
          { id: "1", sender: "Edvard", text: "how many pods?" },
          { id: "2", sender: "Nova Answers", text: "Seven." },
        ],
      },
    });
    const rows = [...window.document.querySelectorAll(".ask-msg")];
    assert.deepEqual(rows.map((r) => r.querySelector(".ask-text").textContent),
      ["how many pods?", "Seven."]);
    assert.ok(rows[0].classList.contains("ask-mine"));
    assert.ok(rows[1].classList.contains("ask-theirs"));
    assert.equal(rows[1].querySelector(".ask-who").textContent, "Nova Answers");
  });

  test("asking posts the text and paints the question before any poll", async () => {
    const window = await loadSite("/ask");
    const box = window.document.querySelector(".ask-box");
    box.value = "  why is the loop slow?  ";
    window.document.querySelector(".ask-form").dispatchEvent(new window.Event("submit"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.deepEqual(window.posted.map((p) => [p.url, p.body]),
      [["/api/ask", { text: "why is the loop slow?" }]]);
    assert.equal(box.value, "", "the box should clear once the question is away");
    const texts = [...window.document.querySelectorAll(".ask-msg")].map((r) => r.textContent);
    assert.match(texts[0], /why is the loop slow\?/);
    // Without this the page goes quiet for four seconds after a send, which
    // is what a lost message looks like.
    assert.ok(window.document.querySelector(".ask-pending"), "nothing says an answer is coming");
  });

  test("a refused question keeps the text and says why", async () => {
    const window = await loadSite("/ask");
    window.postReply = { ok: false, message: "that is longer than 4000 characters" };
    const box = window.document.querySelector(".ask-box");
    box.value = "a very long question";
    window.document.querySelector(".ask-form").dispatchEvent(new window.Event("submit"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.match(window.document.querySelector(".ask-status").textContent, /longer than 4000/);
    assert.equal(box.value, "a very long question", "his text was thrown away on a failure");
    assert.equal(window.document.querySelector(".ask-send").disabled, false,
      "the button stayed disabled, so he cannot retry");
  });

  test("the answer arrives on a poll, and the polling then stops", async () => {
    let turn = 0;
    let timers;
    const window = await loadSite("/ask", {
      install: (win) => { timers = captureTimers(win); },
      ask: () => {
        turn += 1;
        return turn === 1
          ? { conversationId: "c", waiting: true, messages: [{ id: "1", sender: "Edvard", text: "q" }] }
          : {
            conversationId: "c",
            waiting: false,
            messages: [
              { id: "1", sender: "Edvard", text: "q" },
              { id: "2", sender: "Nova Answers", text: "Seven." },
            ],
          };
      },
    });
    assert.ok(window.document.querySelector(".ask-pending"), "a thread owed an answer should say so");
    assert.equal(timers.queued.length, 1, "nothing is polling for the answer");

    await timers.fire();
    assert.deepEqual([...window.document.querySelectorAll(".ask-text")].map((n) => n.textContent),
      ["q", "Seven."]);
    assert.equal(window.document.querySelector(".ask-pending"), null);
    // The half that costs a phone battery rather than a wrong answer.
    assert.equal(timers.queued.length, 0, "still polling after the answer landed");
  });

  test("a failed poll keeps waiting instead of painting an error over a live question", async () => {
    let timers;
    let fail = false;
    const window = await loadSite("/ask", {
      install: (win) => { timers = captureTimers(win); },
      ask: () => {
        // Rejected, not thrown: `serve` is called synchronously inside
        // `window.fetch`, so a throw escapes past the page's own `.catch`
        // and fails the test rather than exercising it.
        if (fail) return Promise.reject(new Error("nova is down"));
        return { conversationId: "c", waiting: true, messages: [{ id: "1", sender: "Edvard", text: "q" }] };
      },
    });
    fail = true;
    await timers.fire();
    assert.match(window.document.querySelector(".ask-msg").textContent, /q/,
      "the question was replaced by an error");
    assert.equal(timers.queued.length, 1, "gave up after one failed poll");
  });

  test("navigating away stops the poll", async () => {
    let timers;
    const window = await loadSite("/ask", {
      install: (win) => { timers = captureTimers(win); },
      ask: { conversationId: "c", waiting: true, messages: [{ id: "1", sender: "Edvard", text: "q" }] },
    });
    assert.equal(timers.queued.length, 1);
    window.history.pushState({}, "", "/costs");
    await timers.fire();
    assert.equal(timers.queued.length, 0, "a poll survived the navigation");
  });
});

describe("the device page", () => {
  /* `/diag` exists because three cycles in a row shipped a fix for a
   * rendering fault on a phone none of them could look at -- an iPhone
   * safe-area fix for a man on a Galaxy S25, then a Chromium compositor
   * workaround on a theory. Headless Chromium at six widths from 320 to
   * 412 CSS px reproduced neither symptom, so the variable is his device.
   *
   * These tests are deliberately about the *contract with the next cycle*
   * -- a report reaches `notes.md`, as one bullet, carrying the readings
   * that were on screen -- and not about the values, which are whatever
   * the browser running the test happens to be. Asserting a number here
   * would pin jsdom, which is exactly the renderer whose agreement proves
   * nothing. */

  test("the page renders without asking the server for anything", async () => {
    const window = await loadSite("/diag");
    assert.equal(window.posted.length, 0, "the device page fetched something it should not have");
    const keys = [...window.document.querySelectorAll(".diag-key")].map((k) => k.textContent);
    assert.ok(keys.includes("User agent"), `no user agent row, got ${JSON.stringify(keys)}`);
    assert.ok(keys.includes("Safe-area insets"), "no safe-area row -- the reading Cycle 299 guessed at");
    assert.ok(keys.includes("Display mode"), "no display-mode row");
    assert.ok(keys.includes("Hamburger, on paint"), "no first hamburger reading");
    assert.ok(keys.includes("Hamburger, 3s later"), "no second hamburger reading");
    assert.ok(keys.includes("Menu drawer, opened"), "no drawer reading -- the symptom he actually reported");
    assert.ok(keys.includes("Priority popup, opened"), "no popup reading -- the other dropdown he reported");
  });

  /* The dropdown readings, and the one thing about them that is not
   * obvious: both elements are invisible until something opens them, so a
   * reading taken without opening one measures the parked box -- the
   * drawer sits at `translateX(100%)` off the right edge, which would
   * report a spectacular fault on every device forever and mean only that
   * the drawer was shut. That is a positive result guaranteed in advance,
   * the failure this page was built to stop being committed by the page
   * itself.
   *
   * jsdom's `getBoundingClientRect` is all zeros, so the numbers here pin
   * nothing and are deliberately not asserted. The first draft of this
   * test leaned on computed `visibility` instead, on the theory that
   * `.nav` is `visibility: hidden` until `.nav.open` -- and the mutation
   * that deleted `setMenu(true)` altogether **passed it**. jsdom does not
   * carry that rule through to `getComputedStyle`, so the reading said
   * `visible` whether or not anything had opened, which is a positive
   * result guaranteed in advance on the diff that exists to stop them.
   *
   * So each reading now names its own precondition -- "drawer was open",
   * "popup was open" -- read off DOM state rather than the cascade, which
   * is both what these assertions can honestly pin and the thing I would
   * want to know first when reading the note he sends back. */
  test("each dropdown is measured while it is open, not while it is parked", async () => {
    const window = await loadSite("/diag");
    const reading = (label) => [...window.document.querySelectorAll(".diag-key")]
      .find((k) => k.textContent === label).nextElementSibling.textContent;
    assert.match(reading("Menu drawer, opened"), /measuring/, "the drawer was measured before it could be opened");

    await new Promise((r) => window.setTimeout(r, 1200));

    assert.match(reading("Menu drawer, opened"), /drawer was open/,
      `the drawer was measured while shut: ${reading("Menu drawer, opened")}`);
    assert.match(reading("Priority popup, opened"), /popup was open/,
      `the popup was measured while hidden: ${reading("Priority popup, opened")}`);
    /* And the cascade reading kept as a second, weaker check on the popup,
     * because it does work there: jsdom answers `block` where his browser
     * answers `flex`, but the UA stylesheet's `[hidden] { display: none }`
     * is honoured, so `none` is the one value a measurement taken while
     * open cannot produce. Mutation-checked. */
    assert.doesNotMatch(reading("Priority popup, opened"), /display none/,
      `the popup computed as display:none, so it was not drawn: ${reading("Priority popup, opened")}`);
    /* An empty popup is a 17px box whose height says nothing about the
     * real one, and `max-height: 70vh` against the real height is one of
     * the few ways this thing could genuinely land wrong on a short
     * screen. */
    assert.match(reading("Priority popup, opened"), /5 options/,
      "the popup was measured empty, so its height is not the height he sees");
  });

  /* The one assertion that pins the diagnostic itself rather than the
   * plumbing around it, and it needed a fake box to exist: jsdom hands
   * back a 0x0 rect in a 0x0 viewport for everything, so every reading is
   * trivially "fully inside" and a broken overflow test would never say
   * otherwise. Mutation-checked -- disabling the right-edge branch in
   * `boxReport` left every other test in this file green, which is how
   * this one came to be written. */
  test("a dropdown hanging off the screen is reported as hanging off the screen", async () => {
    const window = await loadSite("/diag");
    const doc = window.document.documentElement;
    Object.defineProperty(doc, "clientWidth", { value: 360, configurable: true });
    Object.defineProperty(doc, "clientHeight", { value: 697, configurable: true });
    // An S25-shaped drawer that starts 40px past the right edge -- the
    // exact shape of "the dropdowns are out of place" he reported.
    window.document.getElementById("nav").getBoundingClientRect = () => ({
      left: 128, top: 0, right: 400, bottom: 697, width: 272, height: 697,
    });
    await new Promise((r) => window.setTimeout(r, 600));

    const drawer = [...window.document.querySelectorAll(".diag-key")]
      .find((k) => k.textContent === "Menu drawer, opened").nextElementSibling.textContent;
    assert.match(drawer, /OUTSIDE VIEWPORT: right by 40/,
      `a drawer 40px off the right edge did not report as off the edge: ${drawer}`);
    assert.match(drawer, /in a 360x697 viewport/, "the viewport it was judged against is not in the reading");
  });

  /* The overlay is one shared node reused by every priority picker on the
   * site, and the capture box carrying one of them is on this page too --
   * so he can open a real picker inside the ~650ms before the measurement
   * runs. Emptying it under him would make his popup vanish on its own
   * while its trigger still said `aria-expanded="true"`, and nothing in
   * the note would record that it had happened. */
  test("a real picker already open is left alone, and the reading says so", async () => {
    const window = await loadSite("/diag");
    /* Opened through the real trigger rather than by setting `hidden`
     * directly. The overlay does not exist until something asks for it --
     * `getPrioMenuOverlay` creates it lazily -- so a test that reached for
     * `.prio-menu` at load time got null, and faking the open state would
     * not have exercised the picker's own bookkeeping (`dataset.openFor`,
     * `aria-expanded`, the document-level dismiss handler) that this guard
     * exists to protect. */
    click(window, window.document.getElementById("capture-prio"));
    const popup = window.document.querySelector(".prio-menu");
    assert.ok(popup && !popup.hidden, "tapping the capture box's priority button did not open the picker");
    await new Promise((r) => window.setTimeout(r, 1200));

    const reading = [...window.document.querySelectorAll(".diag-key")]
      .find((k) => k.textContent === "Priority popup, opened").nextElementSibling.textContent;
    assert.match(reading, /skipped/, `the measurement ran over an open picker: ${reading}`);
    assert.ok(!popup.hidden, "the picker he opened was closed by the measurement");
    assert.equal(popup.children.length, 5, "the picker he opened had its options replaced under him");
    assert.equal(window.document.getElementById("capture-prio").getAttribute("aria-expanded"), "true",
      "the trigger was left claiming a popup that is no longer open");
  });

  /* Both elements are shared with the rest of the app -- one drawer, one
   * popup overlay reused by every picker on the page. A measurement that
   * left either open would hand him a page with the menu stuck out and no
   * memory of having opened it. */
  test("measuring the dropdowns puts the page back exactly as it was", async () => {
    const window = await loadSite("/diag");
    await new Promise((r) => window.setTimeout(r, 1200));
    const nav = window.document.getElementById("nav");
    assert.ok(!nav.classList.contains("open"), "the drawer was left open after measuring");
    assert.equal(nav.getAttribute("aria-hidden"), "true", "the drawer was left exposed to a screen reader");
    assert.ok(!window.document.body.classList.contains("nav-open"), "the page was left unable to scroll");
    assert.ok(!window.document.getElementById("menu-btn").classList.contains("open"),
      "the hamburger was left in its open state");
    const popup = window.document.querySelector(".prio-menu");
    assert.ok(popup.hidden, "the shared priority popup was left on screen");
    assert.equal(popup.children.length, 0,
      "the shared popup kept the options this page put in it, so the next real picker opens showing them");
  });

  /* The two readings are the point of the page, not decoration: what he
   * reported is "I see it 1 sec ... and then it vanishes", and one sample
   * cannot tell a button that was never drawn from one that was drawn and
   * lost. A single-sample page would have looked complete and answered the
   * wrong question. */
  /* The button is *changed* between the two samples, and that is the whole
   * test. The reviewer caught the first version asserting only that the
   * placeholder was replaced by something matching /visibility/ -- which a
   * mutation reusing the on-paint string verbatim would have survived,
   * because jsdom's button never moves on its own. A test that cannot tell
   * a fresh reading from a cached one cannot pin the one claim this page
   * makes. */
  test("the second hamburger reading is a fresh measurement, not the first one again", async () => {
    const window = await loadSite("/diag");
    /* By label, not by index. The first draft of this test read `.at(-1)`
     * and `.at(-2)`, and adding one row above them silently repointed the
     * second one at a different reading entirely. */
    const reading = (label) => [...window.document.querySelectorAll(".diag-key")]
      .find((k) => k.textContent === label).nextElementSibling.textContent;
    const later = () => reading("Hamburger, 3s later");
    const onPaint = reading("Hamburger, on paint");
    assert.match(later(), /measuring/, "the later reading was not pending on paint");

    window.document.getElementById("menu-btn").style.visibility = "hidden";
    await new Promise((r) => window.setTimeout(r, 3100));

    assert.match(later(), /visibility hidden/, "the 3s sample did not see the button change");
    assert.match(onPaint, /visibility visible/, "the on-paint sample was overwritten, so there is only one reading");
  });

  test("Send files the readings as one note, not one bullet per line", async () => {
    const window = await loadSite("/diag");
    click(window, window.document.getElementById("diag-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    assert.equal(window.posted.length, 1, "Send reached no endpoint");
    assert.equal(window.posted[0].url, "/api/capture");
    assert.equal(window.posted[0].body.target, "notes", "a device report is a note, not a board row");
    const text = window.posted[0].body.text;
    /* `nova_capture.clean_capture_text` makes a bullet out of every newline, so
     * a multi-line body would land in `notes.md` as twenty separate notes
     * and read as twenty things waiting for a cycle. */
    assert.ok(!text.includes("\n"), "the report has newlines, so it would land as many notes");
    assert.match(text, /^\[device report\] /);
    assert.match(text, /User agent: /);
    assert.match(text, /Hamburger, 3s later: /, "the second sample is missing from what was sent");
    assert.match(text, /Page vs viewport width: /, "the width reading is missing from what was sent");
  });

  test("what is sent is what is on screen", async () => {
    const window = await loadSite("/diag");
    const onScreen = [...window.document.querySelectorAll(".diag-value")].map((v) => v.textContent);
    click(window, window.document.getElementById("diag-send"));
    await new Promise((r) => window.setTimeout(r, 0));
    const text = window.posted[0].body.text;
    for (const value of onScreen) {
      assert.ok(text.includes(value), `a reading on screen never reached the note: ${value}`);
    }
  });

  test("a refused send says so and stays sendable", async () => {
    const window = await loadSite("/diag");
    window.postReply = { ok: false, message: "the vault said no" };
    const send = window.document.getElementById("diag-send");
    click(window, send);
    await new Promise((r) => window.setTimeout(r, 0));
    const status = window.document.querySelector(".diag-actions .capture-status");
    assert.match(status.textContent, /the vault said no/, `status did not carry the server's reason: ${status.textContent}`);
    assert.equal(send.disabled, false, "a failed send left the only button on the page dead");
  });
});

/* the owner, capture 2026-08-22: "I can't delete, edit or upload a file to a
 * boarded issues. I wanted to delete issue #4 but i'm not able to."
 *
 * #4 was an ordinary open row, so nothing had made it read-only -- the only
 * way into the editor was a one-second hold with no label anywhere saying so.
 * These pin the visible way in, which is the half of the repair that does not
 * depend on guessing what his phone does with a hold. */
describe("an opened board row offers a visible way into the editor", () => {
  test("the button is there and opens the same editor the hold does", async () => {
    const window = await loadSite("/issues#57");
    const row = window.document.getElementById("item-57");
    const button = row.querySelector(".item-actions button");
    assert.ok(button, "an opened row has no visible edit control at all");
    assert.equal(button.textContent, "Edit / Delete");
    assert.equal(row.querySelector(".item-edit"), null, "the editor is open before it was asked for");
    click(window, button);
    const editor = row.querySelector(".item-edit");
    assert.ok(editor, "the button did not open the editor");
    assert.ok(editor.querySelector(".item-edit-input"), "no title box in the editor");
    const labels = [...editor.querySelectorAll("button")].map((b) => b.textContent);
    assert.deepEqual(labels, ["Save", "Cancel", "Delete"]);
  });

  test("a closed row shows no edit control", async () => {
    const window = await loadSite("/issues");
    assert.equal(window.document.querySelectorAll(".item-actions").length, 0);
  });

  test("pressing it twice does not stack two editors", async () => {
    const window = await loadSite("/issues#57");
    const row = window.document.getElementById("item-57");
    click(window, row.querySelector(".item-actions button"));
    click(window, row.querySelector(".item-actions button"));
    assert.equal(row.querySelectorAll(".item-edit").length, 1);
  });
});

describe("the status fields are one horizontal list, and they link down to the card", () => {
  /* the owner, capture 2026-08-22: *"The status fields at the top, we are
   * keeping them. Please have them shown horisontal listed, not vertical.
   * Also clicking them navigates me down to the Journal it references."*
   *
   * Two asks and two halves here. The layout half cannot be asserted from
   * jsdom -- it has no layout engine, so `getBoundingClientRect` is all
   * zeroes and "are these side by side" is unanswerable. What *is*
   * answerable, and is the thing that actually broke, is the DOM the CSS
   * needs: every field in one `.status-subs` container rather than
   * appended straight to the header as sibling `<p>`s. Assert that, and
   * assert the stylesheet lays that container out as a wrapping row, which
   * together are the whole mechanism.
   *
   * The click half is real behaviour and is tested as behaviour. */
  const withStatus = (extra) => {
    const copy = JSON.parse(JSON.stringify(payload.journal));
    Object.assign(copy.status, { recentMissingCycles: [] }, extra || {});
    return copy;
  };
  const fields = (window) =>
    [...window.document.querySelectorAll("#status .status-subs > .status-sub")];

  test("every status field sits in one row container, not loose in the header", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: false }),
      comments: { byCycle: {}, needs: [] },
    });
    const rows = window.document.querySelectorAll("#status .status-subs");
    assert.equal(rows.length, 1, "expected exactly one status field row");
    assert.ok(fields(window).length >= 2,
      "the control failed: this fixture renders fewer than two fields, so "
      + "nothing here could tell a row from a column");
    /* The failure this pins: a field appended to `#status` directly is
     * outside the flex row and stacks under it however the CSS reads. */
    assert.equal(
      window.document.querySelectorAll("#status > .status-sub").length, 0,
      "a status field is still a direct child of the header");
  });

  test("the stylesheet lays that container out across, and wraps it", async () => {
    const css = readFileSync(join(publicDir, "style.css"), "utf8");
    const rule = css.slice(css.indexOf(".status-subs {"));
    const block = rule.slice(0, rule.indexOf("}"));
    assert.match(block, /display:\s*flex/);
    assert.match(block, /flex-wrap:\s*wrap/);
  });

  test("the field naming the last PR scrolls the feed to that cycle", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ cycle: 57, lastOutcome: "merged", lastPr: "#289" }),
      comments: { byCycle: {}, needs: [] },
    });
    // Matched on the PR rather than the outcome: this test is about the
    // field being a working link, and the field holds both halves.
    const badge = [...window.document.querySelectorAll("#status .status-subs .status-sub")]
      .find((f) => /#289/.test(f.textContent));
    assert.ok(badge, "expected a PR field in the header");
    const field = badge.closest ? (badge.closest("a.status-sub") || badge) : badge;
    assert.equal(field.tagName, "A", "the outcome field is not clickable");
    assert.equal(field.getAttribute("href"), "/cycle/57");

    /* The card is on this page, so the click must stay on this page and
     * scroll rather than follow the href. Both halves are asserted: a
     * `preventDefault` with no scroll would be a link that does nothing. */
    const card = window.document.getElementById("cycle-57");
    assert.ok(card, "the control failed: cycle 57 has no card in this feed, "
      + "so the in-page branch could not have been taken either way");
    let scrolled = false;
    card.scrollIntoView = () => { scrolled = true; };
    const ev = new window.MouseEvent("click", { bubbles: true, cancelable: true });
    field.dispatchEvent(ev);
    assert.ok(scrolled, "clicking the field did not scroll to the card");
    assert.ok(ev.defaultPrevented, "the click also followed the permalink");
  });

  test("a field that references no cycle is not a link", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: false }),
      comments: { byCycle: {}, needs: [] },
    });
    const running = fields(window).find((f) => /cycle running/.test(f.textContent));
    assert.ok(running, "expected the running field");
    assert.equal(running.tagName, "P");
  });

  /* the owner, `issues.md` 2026-08-23: "Drop the Outcome pill from the
   * top-of-page header too, not just the card view — it's the same ugly
   * all-caps duplicate of the blue summary line, shown twice on the same
   * screen." Both copies were on the feed at once, which is what "twice on
   * the same screen" names: this header field, and the newest card below it.
   *
   * The control against a selector that matches nothing is the PR: the same
   * field, same fixture, one child present and the other absent. */
  test("a free-text outcome draws no header pill, and the PR is still named", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({
        cycle: 57,
        lastOutcome: "prompt.md wired to tools.backlog_brief; inbox cleared",
        lastOutcomeDetail: "CI outage, merged nothing",
        lastPr: "#289",
      }),
      comments: { byCycle: {}, needs: [] },
    });
    const subs = window.document.querySelector("#status .status-subs");
    assert.ok(!/backlog_brief/.test(subs.textContent), subs.textContent);
    assert.ok(!/merged nothing/.test(subs.textContent), subs.textContent);
    assert.match(subs.textContent, /#289/);
  });

  /* The footer is mandatory, so a cycle with nothing to show still writes
   * `PR: none`, and the field must not become the word "none" linking to a
   * cycle -- that is the noise #300 removed and it stays removed.
   *
   * What changed on 2026-08-24 is the other half. The owner: "i miss the status
   * fields. Please bring them back", written while a run of cycles was dying
   * without shipping anything. So a cycle whose PR is `none` now gets a
   * field again -- carrying its one-word status, never the `none`. */
  test("a last cycle with no PR still gets a status word, and never says none", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ cycle: 57, lastOutcome: "no-op", lastPr: "none" }),
      comments: { byCycle: {}, needs: [] },
    });
    const subs = window.document.querySelector("#status .status-subs");
    assert.ok(subs, "the field carrying the status word was not drawn");
    assert.ok(!/none/.test(subs.textContent), subs.textContent);
    assert.match(subs.textContent, /no-op/);
    // The control: the same fixture with a real reference draws both halves.
    const w2 = await loadSite("/", {
      journal: () => withStatus({ cycle: 57, lastOutcome: "no-op", lastPr: "runner#289" }),
      comments: { byCycle: {}, needs: [] },
    });
    const subs2 = w2.document.querySelector("#status .status-subs").textContent;
    assert.match(subs2, /runner#289/);
    assert.match(subs2, /no-op/);
  });

  /* `Outcome: none` is not a status word either, and this is the half that
   * would have hurt: `isRealPr` keeps the word "none" out of this field, so
   * a `none` admitted by `shortOutcome` would have walked back in beside it
   * as a badge. Fails the moment the vocabulary gets permissive again. */
  test("an outcome of none draws no header field", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ cycle: 57, lastOutcome: "none", lastPr: "none" }),
      comments: { byCycle: {}, needs: [] },
    });
    const subs = window.document.querySelector("#status .status-subs");
    assert.ok(!subs || !/none/.test(subs.textContent), subs && subs.textContent);
    // The control: a real status word on the same fixture does draw one.
    const w2 = await loadSite("/", {
      journal: () => withStatus({ cycle: 57, lastOutcome: "research", lastPr: "none" }),
      comments: { byCycle: {}, needs: [] },
    });
    assert.match(w2.document.querySelector("#status .status-subs").textContent, /research/);
  });

  /* And the case that has no field to draw at all: a free-text outcome is
   * refused by `shortOutcome` and the PR is `none`, so neither half has
   * anything a badge can hold. This is cycle 340's exact shape -- the card
   * he complained about -- and it must still render nothing. */
  test("a free-text outcome with no PR still gets no header field", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({
        cycle: 57,
        lastOutcome: "goal review written, 8 board rows reprioritised",
        lastPr: "none",
      }),
      comments: { byCycle: {}, needs: [] },
    });
    const subs = window.document.querySelector("#status .status-subs");
    assert.ok(!subs || !/reprioritised|none/.test(subs.textContent), subs && subs.textContent);
  });
});

/* My own board rows got a search of their own (Cycle 408). His rows have
 * had one since ideas.md #71 -- "Ability to search through issues or
 * ideas" -- and the Nova tab shipped with no box at all, so the two
 * halves of one page answered the same question differently. */
describe("searching my own board rows", () => {
  const MINE = {
    ...payload.board,
    novaItems: [
      {
        number: 1,
        title: "Dead newspaper feeds",
        status: "\u{1F7E1} In progress",
        statusKey: "progress",
        priority: "\u{1F7E0} High",
        priorityKey: "high",
        updated: "08-25",
      },
      {
        number: 2,
        title: "A title that says nothing useful",
        status: "⚪ Backlog",
        statusKey: "backlog",
        updated: "08-25",
      },
    ],
  };
  const rows = (window) =>
    [...window.document.querySelectorAll(".nova-board .item-number")].map((n) => n.textContent);
  const novaTab = (window) =>
    [...window.document.querySelectorAll(".tabs .tab")].find((b) => b.textContent.startsWith("Nova"));
  const searchBox = (window) => window.document.querySelector(".nova-board .board-search-input");
  const typeSearch = (window, text) => {
    const input = searchBox(window);
    input.value = text;
    input.dispatchEvent(new window.Event("input"));
    return input;
  };
  const settle = () => new Promise((r) => setTimeout(r, 260));

  test("my tab has a search box, over my rows", async () => {
    const window = await loadSite("/issues", { board: (url) => (url.includes("q=") ? null : MINE) });
    click(window, novaTab(window));
    assert.ok(searchBox(window), "the Nova tab should have a search box");
    assert.deepEqual(rows(window), ["#1", "#2"]);
  });

  test("typing narrows my rows on the title without waiting for the server", async () => {
    const window = await loadSite("/issues", { board: (url) => (url.includes("q=") ? null : MINE) });
    click(window, novaTab(window));
    typeSearch(window, "newspaper");
    assert.deepEqual(rows(window), ["#1"]);
  });

  test("a write-up match asks the server for my board, not his", async () => {
    /* The half the page cannot do itself: `board_page` windows
     * `novaDetails` away on every list request, so my write-ups are not
     * on the page any more than his are. `mine=1` is what makes the
     * answer addressable -- both boards number from 1. */
    const asked = [];
    const window = await loadSite("/issues", {
      board: (url) => {
        if (!url.includes("q=")) return MINE;
        asked.push(url);
        return url.includes("mine=1") ? { query: "asknature", matches: [2] } : { query: "asknature", matches: [] };
      },
    });
    click(window, novaTab(window));
    typeSearch(window, "asknature");
    // No title holds it, so the tab is empty until the answer lands.
    assert.deepEqual(rows(window), []);
    await settle();
    assert.equal(asked.length, 1);
    assert.ok(asked[0].includes("mine=1"), "the Nova tab's search should carry mine=1");
    assert.deepEqual(rows(window), ["#2"]);
  });

  test("switching tabs clears the query rather than carrying his numbers over", async () => {
    /* `matches` is a list of row numbers answered for one tab and both
     * tabs number from 1, so carrying it across would apply his #1 to
     * mine. */
    const window = await loadSite("/issues", { board: (url) => (url.includes("q=") ? null : MINE) });
    click(window, novaTab(window));
    typeSearch(window, "newspaper");
    assert.deepEqual(rows(window), ["#1"]);
    const his = [...window.document.querySelectorAll(".tabs .tab")].find((b) => !b.textContent.startsWith("Nova"));
    click(window, his);
    assert.equal(window.document.querySelector(".board-search-input").value, "");
    click(window, novaTab(window));
    assert.equal(searchBox(window).value, "");
    assert.deepEqual(rows(window), ["#1", "#2"]);
  });
});

describe("searching the journal", () => {
  const all = payload.journal.entries;

  /* A server that honours `?q=` the way `nova_site.journal_page` does:
   * matches ignore the window, `total` is the number of matches, and the
   * query comes back with the answer. Written against that contract
   * rather than against a fixed answer, so a change to the real one that
   * this page could not survive shows up here as a failing test rather
   * than as a fixture that no longer describes anything.
   */
  function searchable() {
    const corpus = [];
    for (let i = 0; i < 30; i += 1) {
      corpus.push({
        ...JSON.parse(JSON.stringify(all[2])),
        cycle: 30 - i,
        // Three of the thirty carry the word, and all three sit past the
        // twenty-entry window on purpose: a filter over what was already
        // on screen would find none of them.
        title: "Cycle " + (30 - i) + (i >= 22 && i <= 24 ? " — the ingress" : " — a quiet one"),
      });
    }
    const asked = [];
    const serve = (url) => {
      asked.push(url);
      const params = new URL(url, "https://nova.example").searchParams;
      const q = (params.get("q") || "").trim().toLowerCase();
      const limit = Number(params.get("limit")) || corpus.length;
      if (!q) {
        return {
          entries: corpus.slice(0, limit),
          status: payload.journal.status,
          total: corpus.length,
          version: 'W/"plain-' + limit + '"',
        };
      }
      const matched = corpus.filter((entry) => entry.title.toLowerCase().includes(q));
      return {
        entries: matched.slice(0, limit),
        status: payload.journal.status,
        total: matched.length,
        query: q,
        version: 'W/"q-' + q + '-' + limit + '"',
      };
    };
    return { serve, asked };
  }

  /** Type into the box and wait past the 200ms debounce. */
  async function search(window, text) {
    const box = window.document.querySelector(".journal-search-input");
    box.value = text;
    box.dispatchEvent(new window.Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 260));
    return box;
  }

  test("the journal feed carries a search box", async () => {
    const window = await loadSite("/", { journal: searchable().serve });
    const box = window.document.querySelector(".journal-search-input");
    assert.ok(box, "no way to search the journal");
    assert.equal(box.getAttribute("aria-label"), "Search the journal");
  });

  test("it is not on the board pages, and not on a deep-linked cycle", async () => {
    /* One box, on the page it searches. The boards have their own and a
     * deep link asks the server for a single entry by number, so there is
     * no window there for a query to narrow. */
    const board = await loadSite("/issues");
    const onBoard = board.document.querySelector("#journal-search");
    assert.ok(!onBoard || onBoard.hidden, "the journal search box followed the reader onto the board");

    const deep = await loadSite("/cycle/7", { journal: searchable().serve });
    const onDeep = deep.document.querySelector("#journal-search");
    assert.ok(!onDeep || onDeep.hidden, "a single-entry page offered a search over one entry");
  });

  test("typing asks the server and shows what came back", async () => {
    const server = searchable();
    const window = await loadSite("/", { journal: server.serve });
    assert.equal(cards(window).length, 20, "the plain feed should be one window");

    await search(window, "ingress");
    assert.match(server.asked[server.asked.length - 1], /q=ingress/);
    // Three matches, all of them past the first window -- so this is the
    // whole journal being searched and not the page being filtered.
    assert.equal(cards(window).length, 3);
    assert.equal(window.document.querySelector("button.more"), null);
  });

  test("the count names the query the answer was built from", async () => {
    const window = await loadSite("/", { journal: searchable().serve });
    await search(window, "ingress");
    const count = window.document.querySelector(".journal-search-count");
    assert.ok(!count.hidden);
    assert.match(count.textContent, /3 entries mention/);
    assert.match(count.textContent, /ingress/);
  });

  test("a search nothing matches says so rather than looking empty", async () => {
    /* A feed that simply went blank is the one outcome that reads as a
     * broken page rather than as an answer. */
    const window = await loadSite("/", { journal: searchable().serve });
    await search(window, "kubernetes");
    assert.equal(cards(window).length, 0);
    const count = window.document.querySelector(".journal-search-count");
    assert.ok(!count.hidden);
    assert.match(count.textContent, /No entry mentions/);
  });

  test("clearing the box brings the feed back", async () => {
    const server = searchable();
    const window = await loadSite("/", { journal: server.serve });
    await search(window, "ingress");
    click(window, window.document.querySelector(".journal-search-clear"));
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(cards(window).length, 20);
    assert.ok(!/q=/.test(server.asked[server.asked.length - 1]), "still searching after a clear");
    assert.ok(window.document.querySelector(".journal-search-count").hidden);
  });

  test("a repaint of the feed does not take the box away mid-word", async () => {
    /* The reason the box is outside `<main id="feed">`. `render` empties
     * that element on every paint -- the 30-second poll, a new entry
     * arriving, a tap on the pager -- so a box inside it would lose the
     * caret and the keyboard on the first poll after he started typing.
     * Asserting the node survives is not enough: it has to be the same
     * node, still holding what he typed. */
    const server = searchable();
    const window = await loadSite("/", { journal: server.serve });
    const box = await search(window, "ingress");
    assert.ok(window.document.contains(box), "the search box was rebuilt out from under the caret");
    assert.equal(box.value, "ingress");
    assert.equal(window.document.querySelectorAll(".journal-search-input").length, 1);
  });

  test("no digest is asked for while a search is running", async () => {
    /* `/api/digest?limit=N` resolves its window out of the newest N
     * cycles, so its summaries belong to the feed's window and not to
     * whatever the search matched. Asked for anyway, the page would hand
     * cycle 30's summary to a card from cycle 6. */
    const server = searchable();
    const digest = { asked: [], serve: null };
    digest.serve = (url) => { digest.asked.push(url); return payload.digest; };
    const window = await loadSite("/", { journal: server.serve, digest: digest.serve });
    const before = digest.asked.length;
    await search(window, "ingress");
    assert.equal(digest.asked.length, before, "the digest was fetched for a window the search is not in");
  });
});

describe("the journal search box sits between the composer and the feed", () => {
  test("the capture composer stays at the top and the box sits under it", async () => {
    /* `captureHome` moves the composer back above the feed on every
     * `load`, and this box is inserted immediately above the feed once.
     * The order that produces is composer, search, feed -- so the search
     * is next to the thing it searches, and the box he types captures
     * into has not moved from where it has always been. Pinned because
     * nothing else would notice the two swapping: both are above the
     * feed either way and both still work. */
    const window = await loadSite("/");
    const feed = window.document.getElementById("feed");
    const box = window.document.getElementById("journal-search");
    const capture = window.document.getElementById("capture");
    assert.equal(box.nextElementSibling, feed, "the search box is not directly above the feed");
    assert.equal(capture.nextElementSibling, box, "the composer and the search box swapped places");
  });
});

describe("a journal search answer that arrived too late", () => {
  const all = payload.journal.entries;

  /* Thirty entries. Three say "ingress"; three more say "ingredient", so
   * "ingr" matches six and "ingress" matches three.
   *
   * That gap is the whole fixture, and the first version of this file did
   * not have it: every title said "ingress", so the prefix he typed on the
   * way there matched exactly the same rows, and a test named for a stale
   * answer replacing a fresh one could not tell the two apart. It passed
   * with the guard deliberately removed. A fixture in which the wrong
   * answer and the right answer are identical proves nothing about which
   * one is on screen. */
  function corpus() {
    const out = [];
    for (let i = 0; i < 30; i += 1) {
      const word = i >= 22 && i <= 24 ? " — the ingress"
        : i >= 19 && i <= 21 ? " — the ingredient"
          : " — a quiet one";
      out.push({
        ...JSON.parse(JSON.stringify(all[2])),
        cycle: 30 - i,
        title: "Cycle " + (30 - i) + word,
      });
    }
    return out;
  }

  /** What the real endpoint answers for `q`, per `nova_site.journal_page`. */
  function answer(rows, q, limit) {
    const needle = (q || "").trim().toLowerCase();
    if (!needle) {
      return {
        entries: rows.slice(0, limit), status: payload.journal.status,
        total: rows.length, version: 'W/"plain"',
      };
    }
    const matched = rows.filter((e) => e.title.toLowerCase().includes(needle));
    return {
      entries: matched.slice(0, limit), status: payload.journal.status,
      total: matched.length, query: needle, version: 'W/"q-' + needle + '"',
    };
  }

  async function type(window, text) {
    const box = window.document.querySelector(".journal-search-input");
    box.value = text;
    box.dispatchEvent(new window.Event("input"));
    await new Promise((resolve) => setTimeout(resolve, 260));
  }

  test("the results for a word he typed past never replace the ones he asked for", async () => {
    /* Two searches in flight together are resolved in whatever order the
     * server finishes them, and `nova_site` is a threading server where
     * the broader, older query does more work -- so finishing last is the
     * ordinary case. Without a guard the feed silently reverts to the
     * shorter word's results, with a count line agreeing, and nothing
     * anywhere says it happened.
     *
     * `window.fetch` is replaced rather than `loadSite`'s server used,
     * because the ordering *is* the thing under test and the fixture
     * server answers synchronously by construction.
     */
    const rows = corpus();
    const window = await loadSite("/", { journal: (url) => answer(rows, null, 20) });

    const held = [];
    window.fetch = (url) => {
      const s = String(url);
      if (s.includes("/api/comments")) return res(payload.comments);
      if (s.includes("/api/digest")) return res(payload.digest);
      const params = new URL(s, "https://nova.example").searchParams;
      const q = params.get("q") || "";
      const body = answer(rows, q, Number(params.get("limit")) || 20);
      if (q !== "ingr") return res(body);
      // Held open until after the newer search has already rendered.
      return new Promise((resolve) => {
        held.push(() => resolve({ ok: true, status: 200, json: () => Promise.resolve(body) }));
      });
    };

    await type(window, "ingr");
    assert.equal(held.length, 1, "the first search never went out");
    await type(window, "ingress");
    assert.equal(cards(window).length, 3, "the newer search did not render");

    held.forEach((release) => release());
    await new Promise((resolve) => setTimeout(resolve, 20));

    // Six is what "ingr" matches and three is what "ingress" matches, so
    // the count of cards is the assertion: the label reads the box and
    // would say "ingress" either way, which is a thing this test cannot
    // use and the reason it asserts on the feed instead.
    assert.equal(cards(window).length, 3, "the stale answer repainted the feed with its own six rows");
    assert.match(
      window.document.querySelector(".journal-search-count").textContent,
      /^3 entries/,
      "the count reverted to the total for the word he had already typed past",
    );
  });

  test("the background poll does not fire a half-typed word", async () => {
    /* The 200ms debounce exists so a word is searched once rather than
     * seven times, and the 30-second poll reads the box directly through
     * `journalUrl` -- so a timer landing between "ingr" and "ingress"
     * would fetch and render the results for "ingr", the debounce
     * defeated by an unrelated timer.
     *
     * `captureTimers` holds every timeout, so the debounce is queued and
     * deliberately not fired here: this is exactly the window the poll
     * must stay out of.
     */
    const rows = corpus();
    let timers;
    const window = await loadSite("/", {
      journal: (url) => {
        const params = new URL(String(url), "https://nova.example").searchParams;
        return answer(rows, params.get("q"), Number(params.get("limit")) || 20);
      },
      install: (win) => { timers = captureTimers(win); },
    });
    assert.equal(cards(window).length, 20);

    const box = window.document.querySelector(".journal-search-input");
    box.value = "ingr";
    box.dispatchEvent(new window.Event("input"));

    await timers.firePagePoll();
    assert.equal(cards(window).length, 20, "the poll searched a word he had not finished typing");
    assert.ok(
      window.document.querySelector(".journal-search-count").hidden,
      "a count line appeared for a search he had not asked for yet",
    );
  });

  test("the count line keeps his capitalisation, not the server's", async () => {
    /* The query comes back lower-cased because that is what it was
     * matched with. A line built from it tells him `TAILSCALE` found
     * "tailscale", which reads like the box corrected him. */
    const rows = corpus();
    const window = await loadSite("/", {
      journal: (url) => {
        const params = new URL(String(url), "https://nova.example").searchParams;
        return answer(rows, params.get("q"), Number(params.get("limit")) || 20);
      },
    });
    await type(window, "Ingress");
    const count = window.document.querySelector(".journal-search-count");
    assert.match(count.textContent, /Ingress/);
    assert.ok(!/ingress/.test(count.textContent.replace(/Ingress/g, "")), "showed the normalised copy too");
  });
});

/* the owner, capture 2026-08-25: *"I want to have a status the Nova header if i
 * have unread Journal comments. Journals should also show if i have some unread
 * by highlightong the comment button somehow, maybe with the amount of unread
 * messages."*
 *
 * Two surfaces, one definition of unread: a reply *I* wrote that he has not
 * opened. The read marks live in `localStorage`, which is why every test here
 * either seeds it through `install` or deliberately leaves it empty -- jsdom
 * gives each window its own store, so "empty" is the honest first-load case.
 */

/** `install` hook that pre-seeds the read marks app.js keeps per card. */
function withRepliesRead(marks) {
  return (window) => {
    window.localStorage.setItem("nova.repliesRead.v1", JSON.stringify(marks));
  };
}

const unreadBadge = (window) => window.document.querySelector(".badge-unread");
const unreadChip = (card) => card.querySelector(".comment-unread");

describe("unread replies are counted on the card and in the header", () => {
  /* Re-declared rather than shared: the two helpers of the same name live
   * inside "commenting on a cycle"'s describe block and are not in scope
   * here. */
  const cardFor = (w, cycle) =>
    cards(w).find((c) => c.querySelector("h2").textContent === "Cycle " + cycle);
  const bubble = (card) => card.querySelector(".comment-toggle");

  test("a first load claims nothing unread and seeds the marks instead", async () => {
    /* The one that decides whether this feature is usable. There are three
     * hundred-odd replies in the archive and no record of which he has read,
     * so counting them all would print the absence of a measurement as a
     * measurement -- and a badge reading "300" on day one teaches him to
     * ignore the badge. */
    const window = await loadSite("/");
    assert.equal(unreadBadge(window), null, "a fresh device claimed unread replies it cannot know about");
    assert.equal(bubble(cardFor(window, 55)).textContent, "💬 1");
    assert.equal(unreadChip(cardFor(window, 55)), null);
    const stored = JSON.parse(window.localStorage.getItem("nova.repliesRead.v1"));
    assert.equal(stored["55"], "2026-08-09 13:12", "the seed did not record the reply that was on screen");
  });

  test("a reply written after his last read shows on the card and in the header", async () => {
    const window = await loadSite("/", { install: withRepliesRead({ "55": "2026-08-09 13:00" }) });
    const card = cardFor(window, 55);
    assert.ok(bubble(card).classList.contains("has-unread"), "the 💬 button is not highlighted");
    assert.equal(unreadChip(card).textContent, "1");
    // The comment count survives beside it: a card he has caught up on must
    // not look empty, which replacing the number would do.
    assert.match(bubble(card).textContent, /^💬 1/);
    const badge = unreadBadge(window);
    assert.equal(badge.textContent, "1 new reply");
    assert.equal(badge.closest("a").getAttribute("href"), "/cycle/55");
  });

  test("opening the drawer clears the card chip and the header badge together", async () => {
    /* Both, in one tap. The header clearing a poll later would insist on a
     * reply that is open on his screen. */
    const window = await loadSite("/", { install: withRepliesRead({ "55": "2026-08-09 13:00" }) });
    const card = cardFor(window, 55);
    click(window, bubble(card));
    assert.equal(unreadChip(card), null, "the chip survived him reading the reply");
    assert.equal(bubble(card).classList.contains("has-unread"), false);
    assert.equal(unreadBadge(window), null, "the header still claims an unread reply he just opened");
    assert.equal(
      JSON.parse(window.localStorage.getItem("nova.repliesRead.v1"))["55"],
      "2026-08-09 13:12",
      "the read mark did not move to the reply he opened",
    );
  });

  test("the header names the oldest card when more than one is unread", async () => {
    const comments = JSON.parse(JSON.stringify(payload.comments));
    comments.byCycle["57"][1].replies = [
      { author: "commentator", stamp: "2026-08-09 16:30", text: "on it" },
    ];
    const window = await loadSite("/", {
      comments,
      install: withRepliesRead({ "55": "2026-08-09 13:00", "57": "2026-08-09 16:00" }),
    });
    const badge = unreadBadge(window);
    assert.equal(badge.textContent, "2 new replies");
    assert.equal(badge.closest("a").getAttribute("href"), "/cycle/55");
    assert.match(badge.parentElement.textContent, /oldest on cycle 55/);
  });

  test("his own comments never count as unread", async () => {
    /* A comment he typed is not a notification that he typed it. Cycle 57
     * holds two of his comments and no reply at all, so with every card
     * marked unread-from-the-beginning it must still draw no chip -- while
     * cycle 55, which holds one real reply, does. That pairing is the test:
     * an empty seed makes both cards eligible and only one of them counts. */
    const window = await loadSite("/", { install: withRepliesRead({}) });
    assert.equal(unreadChip(cardFor(window, 57)), null, "his own comments were counted as unread");
    assert.equal(unreadChip(cardFor(window, 55)).textContent, "1");
    assert.equal(unreadBadge(window).textContent, "1 new reply");
  });

  test("a replayed page draws no badge", async () => {
    /* The header already says it is showing a saved copy, and "you have
     * something new" is a claim about right now. Same rule, and the same
     * `status.replayed` flag, that the "waiting on you" pill follows. */
    const window = await loadSite("/", {
      replayed: true,
      install: withRepliesRead({ "55": "2026-08-09 13:00" }),
    });
    assert.equal(unreadBadge(window), null, "the header counted unread replies out of a saved copy");
  });

  test("a browser with storage disabled still renders, without a badge", async () => {
    /* Safari in private mode throws on the `localStorage` property itself,
     * not on the call, so the guard has to wrap the lookup. Without it this
     * page would not paint at all. */
    const window = await loadSite("/", {
      install: (w) => {
        Object.defineProperty(w, "localStorage", {
          configurable: true,
          get() { throw new Error("storage is disabled"); },
        });
      },
    });
    assert.equal(cards(window).length > 0, true, "the feed did not render without localStorage");
    assert.equal(unreadBadge(window), null);
    assert.equal(unreadChip(cardFor(window, 55)), null);
  });

  test("the deep-linked card counts and clears the same way the feed card does", async () => {
    /* `/cycle/N` builds its own card through a second `setCommentsOpen`, and
     * the two are separate code paths that have drifted before. */
    const window = await loadSite("/cycle/55", { install: withRepliesRead({ "55": "2026-08-09 13:00" }) });
    const card = window.document.querySelector(".entry.is-page");
    assert.equal(unreadChip(card).textContent, "1");
    click(window, bubble(card));
    assert.equal(unreadChip(card), null, "the deep-linked card kept its chip after he read the reply");
  });
});
