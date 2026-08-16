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
 * `digest` and `comments` override those two responses. The Needs Edvard
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
async function loadSite(path = "/", { failComments = false, commentsStatus = 200, journalStatus = 200, boardStatus = 200, costsStatus = 200, retroStatus = 200, digestStatus = 200, unparsable = false, replayed = false, digest, comments, install, journal, board, costs, retro } = {}) {
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

/* Edvard, on the comments board at cycle 81: "i do not like the double entry
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
/* Edvard, issues #86, on the feed rather than on `/cycle/N`. The two
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

  test("a cycle with no digest line keeps it, since nothing else labels the card", async () => {
    const solo = soloCycle();
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const title = "The only sentence on this card that was written as a title";
    journal.entries.find((e) => e.cycle === solo.cycle).title = title;
    const window = await loadSite("/", {
      journal: () => journal,
      digest: withoutDigestLine(solo.cycle),
    });
    assert.equal(cardFor(window, solo.cycle).querySelector(".entry-title").textContent, title);
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
    // markup, and it is the line Edvard called hard to read.
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
  test("the card takes its outcome from the last part that has one", async () => {
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
    assert.match(meta.textContent, /#86/);
    assert.match(meta.textContent, /merged/);
    // The stamp still belongs to the earliest part: that is when it began.
    assert.match(meta.textContent, new RegExp(parts[1].time));
  });

  /* Edvard's issues.md #59, the three small pickings on the journal card.
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
    assert.match(meta.textContent, /merged/);
    assert.ok(!/no-op/.test(meta.textContent), meta.textContent);
    // The page reads off the same function, so it must agree.
    const page = await loadSite("/cycle/57", { journal: () => journal });
    const pageMeta = page.document.querySelector(".entry-meta:not(.entry-meta-part)");
    assert.match(pageMeta.textContent, /#89/);
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
    assert.match(meta.textContent, /shipped/);
  });

  test("a part of the card that reached a different answer keeps its own row", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    const parts = journal.entries.filter((e) => e.cycle === 57);
    parts[1].outcome = "no-op";
    parts[1].pr = "none (status note)";
    parts[1].prSpans = [{ kind: "text", text: "none (status note)" }];
    parts[0].outcome = "merged";
    const w = await loadSite("/", { journal: () => journal });
    const card = cards(w)[0];
    assert.match(card.querySelector(".entry-meta").textContent, /merged/);
    const own = card.querySelector(".entry-meta-part");
    assert.ok(own, "the disagreeing part must keep a row of its own");
    assert.match(own.textContent, /no-op/);
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

describe("a journal card names the board item it worked on", () => {
  /* Edvard, ideas.md #68: "Journal cards in Nova should mark the issue or
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
  /* Edvard, issues.md #85: "Some of them are implemented and some of them
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
     * renders (Edvard, 2026-08-14: "the priority button should be the
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
  /* Edvard, comments board 2026-08-16: "the solution i want is to remove the
   * 'needs Edvard' block entirely. If you need something from me, it should
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
    assert.match(ask.textContent, /Needs Edvard/);
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

  test("a card with no ask keeps its drawer shut", async () => {
    const window = await loadSite();
    const card = window.document.querySelector(".entry");
    assert.equal(card.querySelector(".entry-ask"), null);
    assert.equal(card.classList.contains("is-commenting"), false);
  });
});

/* Edvard, in issue #59: "its not the link thats the problem, its the single
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

  /* Tabs, which is what Edvard asked for three times (issues.md #59, and
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
   * this asserts on a literal rather than on "a title element exists". */
  test("a one-part cycle's title is shown, since it has no subheading to live in", async () => {
    const journal = JSON.parse(JSON.stringify(payload.journal));
    // No digest line for this cycle: that is the case where the title is the
    // only label the card has. The digest-line case is the test below, and
    // this one asserted whichever solo cycle came first until #86 split them.
    const solo = journal.entries.find(
      (e) => e.cycle !== null
        && journal.entries.filter((o) => o.cycle === e.cycle).length === 1
    );
    solo.title = "The heartbeat was never late; the clock on the card was invented";
    const digest = JSON.parse(JSON.stringify(payload.digest));
    digest.lines = digest.lines.filter((l) => l.cycle !== solo.cycle);
    const window = await loadSite("/cycle/" + solo.cycle, { journal: () => journal, digest });
    assert.equal(cards(window)[0].querySelector(".entry-title").textContent, solo.title);
  });

  /* Edvard, issues #86: "Journal cards like cycle 209 seems to have two
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
    /* Edvard, ideas.md 2026-08-10: "Move the Journal chat bubble icon to the
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
    /* Edvard, issues.md 2026-08-10: "they should be below each other on the
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

/* Replying to Needs Edvard (2026-08-10). Edvard: "the 'needs Edvard' is
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

  /* Edvard, issues.md 2026-08-11: "The Nova site closes all drawers on what
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
   * hour and comments change whenever Edvard types, so almost every poll
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
 * Edvard, issues.md #71: "Make it more lazy load when i scroll down instead
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
 * Edvard: "I need more visualisations in the Nova app. Create more pages
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
    /* Edvard, issues.md #66: "should have a separator line or something
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

  test("Delete sends the capture's own text, not its position", async () => {
    /* The whole point of the design: a cycle boarding a bullet above this
     * one shifts every index, and an index-addressed delete would then
     * remove a different capture. */
    const window = await loadSite("/issues");
    window.confirm = () => true;
    const item = window.document.querySelector(".capture-item");
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Delete")[0]);
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
    click(window, [...items[1].querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Delete")[0]);
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(window.posted[0].body.index, 1,
      "both rows sent the same address, so the wrong capture would be deleted");
    assert.equal(window.posted[0].body.original, "fix this");
  });

  test("Delete asks first, and sends nothing when the answer is no", async () => {
    const window = await loadSite("/issues");
    window.confirm = () => false;
    const item = window.document.querySelector(".capture-item");
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Delete")[0]);
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
     * because what Edvard edits is what the vault stores. */
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
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Edit")[0]);

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

  test("saving an emptied field is not a delete", async () => {
    /* Deleting has a button and that button asks. Clearing the box has to
     * do nothing at all, or the confirm is one backspace away from being
     * bypassed. */
    const window = await loadSite("/issues");
    const item = window.document.querySelector(".capture-item");
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Edit")[0]);
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
    click(window, [...item.querySelectorAll(".capture-act")].filter(
      (b) => b.textContent === "Delete")[0]);
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

/* The sidebar. Edvard, issues.md 2026-08-11: "Move the Journal, issues &
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
    assert.deepEqual(hrefs, ["/", "/issues", "/ideas", "/costs", "/retro"]);

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

/* Edvard, issues.md #83: "Make the header for issues and ideas bold". The
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
  const plot = (window) => window.document.querySelector(".chart-svg");

  test("it plots the cycles and the quota, not the journal", async () => {
    const window = await loadSite("/costs");
    const charts = window.document.querySelectorAll(".chart");
    assert.equal(charts.length, 2);
    // One bar per cycle in the ledger, and the fixture has three. The
    // hover overlay is a `rect` too, hence the exclusion -- counting it
    // would let a chart that drew two bars pass as three.
    assert.equal(charts[0].querySelectorAll("rect:not(.chart-overlay)").length, 3);
    // Two series, two paths.
    assert.equal(charts[1].querySelectorAll("path").length, 2);
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
    const paths = [...plot(window).ownerDocument.querySelectorAll(".chart")[1].querySelectorAll("path")];
    const withHole = paths.map((p) => (p.getAttribute("d").match(/M/g) || []).length);
    assert.deepEqual(withHole, [1, 1], "the fixture's readings are contiguous");

    const holed = {
      ...payload.costs,
      quota: [
        [1786227966684, 27.0, null, 2.0, null],
        [1786420000000, null, null, 44.0, 0.58],
        [1786450678872, 78.0, 0.944, 51.0, 0.615],
      ],
    };
    const broken = await loadSite("/costs", { costs: holed });
    const fiveHour = broken.document.querySelectorAll(".chart")[1].querySelectorAll("path")[0];
    assert.equal((fiveHour.getAttribute("d").match(/M/g) || []).length, 2,
      "the five-hour line drew through a missing reading");
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
    assert.equal(window.document.querySelectorAll("rect:not(.chart-overlay)").length, 0);
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

/* A cycle that ran and wrote nothing, marked where it happened -- the
 * display half of Edvard's #72. He found cycles 127 and 128 himself by
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
   * has to be told which numbers count. Edvard's own notes carry no cycle
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
   * behind the cycle actually running -- which is what Edvard reported as
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

  /* The state Edvard is looking at almost every time he opens the app: a
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
  test("a stalled loop is never also reported as running", async () => {
    const window = await loadSite("/", {
      journal: () => withStatus({ running: true, stalled: true, silentIntervals: 4 }),
    });
    assert.deepEqual(live(window), []);
    assert.ok(warn(window).some((t) => t === "no entry for 4 hours"));
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

describe("a loop that has gone quiet says so in the header", () => {
  const warn = (window) =>
    [...window.document.querySelectorAll("#status .badge-warn")]
      .map((n) => n.textContent);

  /* `/no entry for/` and not `/no entry/`: the fixture journal really does
   * hole at cycle 56, so once the header started naming holes the looser
   * pattern matched "cycle 56 wrote no entry" and this test failed on a
   * badge it was never about. Each of the two badges is asserted by its
   * own wording. */
  test("no stall badge while the loop is running to time", async () => {
    const live = JSON.parse(JSON.stringify(payload.journal));
    live.status.stalled = false;
    live.status.silentIntervals = 1;
    const window = await loadSite("/", { journal: () => live });
    assert.deepEqual(warn(window).filter((t) => /no entry for/.test(t)), []);
  });

  /* The test above was called "nothing is said while the loop is healthy"
   * and did not check that, which the reviewer on runner#195 caught. Every
   * fixture in this file carries the hole at cycle 56, so narrowing that
   * assertion to the stall badge left *no* test anywhere asserting the
   * header is completely quiet — the state Edvard is looking at almost
   * every time he opens the app. Renaming the old one to what it actually
   * proves and adding this is the honest split; a fixture has to be
   * cleared, not a pattern narrowed, to claim silence. */
  test("a healthy loop with no holes says nothing at all", async () => {
    const live = JSON.parse(JSON.stringify(payload.journal));
    live.status.stalled = false;
    live.status.silentIntervals = 1;
    live.status.recentMissingCycles = [];
    const window = await loadSite("/", { journal: () => live });
    assert.deepEqual(warn(window), []);
  });

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
   * something: it is the page Edvard rates rows on, so an unmarked stale
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

  test("a stall is named with how long it has been", async () => {
    const quiet = JSON.parse(JSON.stringify(payload.journal));
    quiet.status.stalled = true;
    quiet.status.silentIntervals = 4;
    const window = await loadSite("/", { journal: () => quiet });
    assert.ok(warn(window).some((t) => t === "no entry for 4 hours"));
  });

  test("one hour is not pluralised", async () => {
    const quiet = JSON.parse(JSON.stringify(payload.journal));
    quiet.status.stalled = true;
    quiet.status.silentIntervals = 1;
    const window = await loadSite("/", { journal: () => quiet });
    assert.ok(warn(window).some((t) => t === "no entry for 1 hour"));
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

  /* Edvard, comments board 2026-08-14: "Should be displayed if the return
   * fetch came in with missing journals." The clock and the evidence are
   * separate badges because they catch separate failures -- see the
   * comment beside this in app.js. */

  test("a cycle that woke and wrote nothing is named", async () => {
    const holed = JSON.parse(JSON.stringify(payload.journal));
    holed.status.stalled = false;
    holed.status.recentMissingCycles = [128];
    const window = await loadSite("/", { journal: () => holed });
    assert.ok(warn(window).some((t) => t === "cycle 128 wrote no entry"));
  });

  test("more than one hole is counted rather than listed", async () => {
    const holed = JSON.parse(JSON.stringify(payload.journal));
    holed.status.stalled = false;
    holed.status.recentMissingCycles = [127, 128];
    const window = await loadSite("/", { journal: () => holed });
    assert.ok(warn(window).some((t) => t === "2 cycles wrote no entry"));
  });

  test("no recent hole says nothing, and an old one is not recent", async () => {
    /* The server decides the window; this asserts the client renders that
     * decision rather than reaching for the full `missingCycles` list,
     * which is history and never shrinks. */
    const holed = JSON.parse(JSON.stringify(payload.journal));
    holed.status.stalled = false;
    holed.status.missingCycles = [8, 52, 134];
    holed.status.recentMissingCycles = [];
    const window = await loadSite("/", { journal: () => holed });
    assert.deepEqual(warn(window).filter((t) => /wrote no entry/.test(t)), []);
  });

  test("a stall and a hole are both shown, not one instead of the other",
    async () => {
      const both = JSON.parse(JSON.stringify(payload.journal));
      both.status.stalled = true;
      both.status.silentIntervals = 3;
      both.status.recentMissingCycles = [204];
      const window = await loadSite("/", { journal: () => both });
      const shown = warn(window);
      assert.ok(shown.some((t) => t === "no entry for 3 hours"));
      assert.ok(shown.some((t) => t === "cycle 204 wrote no entry"));
    });

  test("a payload with no recentMissingCycles at all is not an error",
    async () => {
      /* The tailnet can serve the last build's app.js against this
       * build's server, or the other way round. An absent key must read
       * as "no holes", not throw and take the header down with it. */
      const old = JSON.parse(JSON.stringify(payload.journal));
      old.status.stalled = false;
      delete old.status.recentMissingCycles;
      const window = await loadSite("/", { journal: () => old });
      assert.deepEqual(warn(window).filter((t) => /wrote no entry/.test(t)), []);
      assert.ok(window.document.querySelector("#status .status-line"));
    });
});

/* An HTTP error is not a network error, and the page could not tell them
 * apart. `fetch` rejects only when the request never completed, so every
 * 500 and 502 arrived here as a resolved response whose JSON body happened
 * to parse -- and the page rendered it. Four written "Could not load ..."
 * messages sat in this file's `.catch` blocks, unreachable, for as long as
 * they have existed. Cycles 163 and 164 fixed the server side of this twice;
 * this is the browser side, and it is the half Edvard actually sees. */
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

/* The retrospective page (Edvard, issues.md 2026-08-13).
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

  test("one line per score, drawn with a dot at every real retro", async () => {
    const window = await loadSite("/retro", { retro: twoRetros });
    const paths = [...window.document.querySelectorAll(".chart-svg path")];
    assert.equal(paths.length, 3, "three scores, three lines");
    // Two retros, so every path is a single segment: one M and one L. A
    // path that swallowed the second point would still be a <path>.
    paths.forEach((p) => {
      assert.equal((p.getAttribute("d").match(/[ML]/g) || []).join(""), "ML");
    });
    assert.equal(window.document.querySelectorAll(".chart-svg circle").length, 6);
  });

  test("a higher score sits higher on the axis", async () => {
    // The one assertion that catches an inverted y, which every other test
    // on this page passes happily.
    const window = await loadSite("/retro", { retro: twoRetros });
    const feeling = [...window.document.querySelectorAll(".chart-svg path")][2];
    const [, y1, , y2] = feeling.getAttribute("d").match(/-?[\d.]+/g).map(Number);
    assert.ok(y2 < y1, `8/10 must be drawn above 6/10, got ${y1} then ${y2}`);
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
    const dots = [...window.document.querySelectorAll(".chart-svg circle")];
    assert.equal(dots.length, 3);
    dots.forEach((dot) => {
      assert.ok(Number.isFinite(Number(dot.getAttribute("cx"))), "x must be a number");
    });
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

  test("the hover label names a day, never an invented time of day", async () => {
    // The ledger stores dates and the payload converts them to midnight
    // UTC, so the shared tooltip's default stamp would print "14 Aug,
    // 02:00" in Oslo -- a real-looking time that corresponds to nothing.
    // jsdom lays nothing out, so the overlay's box has to be supplied for
    // the handler to get past its own zero-width guard.
    const window = await loadSite("/retro", { retro: twoRetros });
    const svg = window.document.querySelector(".chart-svg");
    svg.getBoundingClientRect = () => ({ left: 0, width: 360, top: 0, height: 168 });
    const overlay = window.document.querySelector(".chart-overlay");
    const move = new window.Event("pointermove", { bubbles: true });
    move.clientX = 359;
    overlay.dispatchEvent(move);
    const label = window.document.querySelector(".chart-tip-when").textContent;
    assert.doesNotMatch(label, /\d{1,2}:\d{2}/, `a time of day was invented: ${label}`);
    assert.match(label, /14/, `the newest retro's day is missing: ${label}`);
  });

  test("a 502 on the retro page reaches the retro page's own message", async () => {
    const window = await loadSite("/retro", { retroStatus: 502 });
    assert.match(feedText(window), /Could not load the retrospectives/);
  });
});

/* The capture row's layout. Edvard, issues.md 2026-08-14: *"Ui is ugly for
 * the priority rating. The issue, idea, note and priority dropdown are now
 * just scrambled after the addition of the priority dropdown."* That was
 * fixed the same day by giving the picker its own row above the buttons,
 * while it still showed a rating's word and grew to 136px wide.
 *
 * Edvard, 2026-08-14, later: once the picker shrank to a fixed 44px glyph
 * he asked for it back on the button row, at the far right. jsdom lays
 * nothing out, so none of these can see a wrap on a real phone -- that is
 * measured in Chromium at 390px, and the fix is CSS. What is real code,
 * and what these pin, is the structure the CSS depends on: the picker is
 * the last child of the same group the three targets are in, appended
 * after them in app.js rather than inserted, so it always renders at the
 * right edge of the row rather than somewhere the flex order does not
 * expect. */
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
    assert.equal(kids.length, 4, "the priority picker is not in the button group");
    assert.equal(
      kids[3] && kids[3].id, "capture-prio",
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
   * row's picker went back to `.chip.prio` (Edvard, 2026-08-14) and has
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

/* Edvard, issues.md #90: "When i press enter on my keyboard, it
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

/* Edvard, issues.md #91: "All unboarded issues and ideas should have the
 * priority status icon shown (as they do when its chosen) in the left top
 * corner, but pressing it should open the modal like it does sin the issue
 * cards." */
describe("rating a capture that is not boarded yet", () => {
  const rated = {
    text: "🟠 make the picker work here too",
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
    assert.equal(posted.body.text, "🔴 " + payload.board.captures[0].body);
  });

  test("re-rating an already-rated capture swaps the glyph rather than stacking a second one", async () => {
    // `capture.body` is the server's glyph-stripped text, and using
    // `capture.text` here instead would send "⚪ 🟠 make the picker...".
    const window = await loadSite("/issues", withCapture(rated));
    const trigger = window.document.querySelector(".capture-item .chip.prio");
    assert.equal(trigger.textContent, "🟠 High");
    assert.equal(trigger.className, "chip prio prio-high");
    click(window, trigger);
    click(window, [...window.document.querySelectorAll(".prio-option")]
      .find((o) => o.textContent === "⚪ Low"));
    await new Promise((r) => window.setTimeout(r, 0));
    const posted = window.posted.find((p) => p.url === "/api/capture/edit");
    assert.equal(posted.body.text, "⚪ make the picker work here too");
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
    const glyphOnly = { text: "🟠", body: "", priority: "🟠 High", priorityKey: "high", blocks: [] };
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
  test("the composer's picker opens with full words and closes to a bare glyph after a pick", async () => {
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
    assert.equal(trigger.textContent, "🟠", "the closed trigger should be back to a bare glyph");
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
    // Edvard, 2026-08-14: "on issues and ideas the priority button should
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
    // Edvard, 2026-08-14: "i liked the old issue priority status better...
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

/* Edvard, ideas.md #71: "Ability to search through issues or ideas. Also
 * filter the list based on different parameters like date, this week,
 * priority etc." and #70: "Lets me sort issues and ideas ... make sure
 * its both upwards and downwards option ... a button with a
 * upwards/downwards facing arrow to click and have it turn". */
describe("searching, filtering and sorting a board", () => {
  const rows = (window) =>
    [...window.document.querySelectorAll(".item-number")].map((n) => n.textContent);
  /* The filter and toggle buttons this suite reaches for all moved into
   * the filter modal (Edvard, 2026-08-14: "make the filters into a
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

  /* Edvard, issues.md 2026-08-15: "When i use the search bar in Nova, my
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

/* Edvard, comments board 2026-08-14, on the stall badge: "Or a display
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

/* Holding a boarded card -- Edvard's issue #84.
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
  /** A whole press: down, the second Edvard asked for, then up and the
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
 * Edvard: *"Lets me have the same comment conversation on ideas, notes and
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
