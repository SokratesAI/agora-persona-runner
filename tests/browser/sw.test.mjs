/* Behavioural tests for the real agora_runner/nova_public/sw.js.
 *
 * A service worker is not a DOM, so these do not open a jsdom window like
 * app.test.mjs does. They run the actual file in a `node:vm` sandbox with
 * the handful of globals a worker gets -- `self`, `caches`, `fetch`,
 * `setTimeout` -- and then fire real events at the handlers it registered.
 *
 * The point of the sandbox is the clock. `NETWORK_TIMEOUT_MS` is eight
 * seconds, and a test that actually waited eight seconds for each case
 * would be a test nobody runs. `setTimeout` resolves out of the sandbox
 * global, so the fake below hands the pending callback back and the test
 * fires it when it wants to -- which also means these assert the timeout
 * *fires*, not merely that a number appears in the source.
 */
import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import vm from "node:vm";

const here = dirname(fileURLToPath(import.meta.url));
const swPath = join(here, "..", "..", "agora_runner", "nova_public", "sw.js");
const source = readFileSync(swPath, "utf8");

/* The two cache names, read out of the worker rather than restated here.
 *
 * `CACHE` is bumped whenever a shell file changes -- that bump is what
 * evicts the old markup, and it is expected to happen. A suite holding the
 * literal `nova-v1` turned every one of those into three red tests with
 * nothing wrong behind them (2026-09-07), which is the shape of a test
 * asserting a value instead of a rule. */
function constFromSource(name) {
  const found = source.match(new RegExp(`var ${name} = "?([^";]+)"?;`));
  if (!found) throw new Error(`sw.js no longer declares ${name}`);
  return found[1];
}
const SHELL_CACHE = constFromSource("CACHE");
const PUSH_CACHE = constFromSource("PUSH_CACHE");

/* One sandbox per test. The worker keeps module-level state (`CACHE`, the
 * registered handlers) and sharing it across tests would let one test's
 * cache answer another's request. */
function loadWorker() {
  const handlers = {};
  /* Named caches, because the worker owns two of them now and they have
   * opposite reading rules -- `nova-v1` is a fallback the network gets to
   * beat, `nova-push-v1` is served ahead of the network exactly once. A fake
   * that collapsed them into one map would let a test's prefetch answer a
   * request the real worker would have sent to the network. */
  const stores = new Map();         // cache name -> Map(request key -> Response)
  const puts = [];
  const timers = [];                // pending setTimeout callbacks
  const posted = [];                // messages sent to page clients
  let cleared = 0;
  let deletedCaches = [];
  let respondToFetch = null;        // set per test

  function store(name) {
    if (!stores.has(name)) stores.set(name, new Map());
    return stores.get(name);
  }

  const client = { visibilityState: "hidden", postMessage(msg) { posted.push(msg); } };
  const shown = [];

  const self = {
    location: { origin: "https://nova.example" },
    addEventListener(name, fn) { handlers[name] = fn; },
    skipWaiting() { return Promise.resolve(); },
    clients: { claim: () => Promise.resolve(), matchAll: () => Promise.resolve([client]) },
    registration: {
      showNotification(title, options) { shown.push({ title, options }); return Promise.resolve(); },
    },
  };

  function cacheApi(name) {
    return {
      addAll: () => Promise.resolve(),
      put(request, response) {
        puts.push(key(request));
        store(name).set(key(request), response);
        return Promise.resolve();
      },
      match: (request) => Promise.resolve(store(name).get(key(request))),
      delete(request) { return Promise.resolve(store(name).delete(key(request))); },
    };
  }

  let cachesBroken = false;
  const caches = {
    open: (name) => (cachesBroken
      ? Promise.reject(new TypeError("caches is not available"))
      : Promise.resolve(cacheApi(name))),
    keys: () => Promise.resolve([...stores.keys()]),
    delete: (name) => { deletedCaches.push(name); return Promise.resolve(true); },
    match: (request) => Promise.resolve(store(SHELL_CACHE).get(key(request))),
  };

  const sandbox = {
    self,
    caches,
    // Two arguments, because the worker's conditional revalidation passes
    // an init with `If-None-Match` in it and a fake that dropped it would
    // let an unconditional refetch pass a test about conditional ones.
    fetch: (request, init) => respondToFetch(request, init),
    setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length - 1; },
    clearTimeout() { cleared += 1; },
    Promise, Response, Headers, Blob, URL, console,
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "sw.js" });

  return {
    handlers,
    cache: store(SHELL_CACHE),
    pushCache: store(PUSH_CACHE),
    puts,
    timers,
    posted,
    shown,
    client,
    seedCacheNames(...names) { names.forEach((n) => store(n)); },
    // The worker's own URL builder, so a test can compare it with the
    // page's rather than restating either.
    threadUrl: (id) => sandbox.threadUrl(id),
    breakCaches() { cachesBroken = true; },
    deletedCaches: () => deletedCaches,
    clearedCount: () => cleared,
    network(fn) { respondToFetch = fn; },
    fireTimer(i = 0) { timers[i].fn(); },
  };
}

/* `caches.match` is keyed on the request in the real API; here the key is
 * the absolute URL. The worker passes a bare `"/"` for the navigation
 * fallback and a Request-alike everywhere else, and the real Cache API
 * resolves the string against the origin -- so this has to as well, or a
 * deep link opened offline misses a shell that is sitting right there. */
function key(request) {
  return new URL(typeof request === "string" ? request : request.url, "https://nova.example").href;
}

function req(url, extra = {}) {
  return { method: "GET", url, mode: "same-origin", ...extra };
}

/* Fire the fetch handler and hand back whatever it passed to respondWith.
 *
 * `waitUntil` is real here rather than a no-op: the stale-while-revalidate
 * path parks its conditional refetch on it, and a test that awaited only
 * the response would finish before the revalidation had run. `settled()`
 * on the returned handle is how a test waits for that half. */
function fetchEvent(worker, request) {
  let answered = null;
  const extended = [];
  worker.handlers.fetch({
    request,
    respondWith(p) { answered = p; },
    waitUntil(p) { extended.push(p); },
  });
  if (answered) answered.settled = () => Promise.all(extended);
  return answered;
}

/* A promise that has not settled after the microtask queue drains.
 * `setImmediate` runs after promise jobs, so anything that was going to
 * resolve without a timer or a network response already has. */
function pending(promise) {
  let settled = false;
  promise.then(() => { settled = true; }, () => { settled = true; });
  return new Promise((resolve) => setImmediate(() => resolve(!settled)));
}

describe("the service worker bounds how long the network gets", () => {
  test("a stalled fetch is answered from the cache instead of hanging forever", async () => {
    /* The failure the owner reported: `fetch` has no timeout, so a
     * connection that opens and then stalls neither resolves nor rejects.
     * Before this, the promise below never settled at all -- and because
     * every route goes through this one handler, a stalled fetch for the
     * shell hangs the whole app open, not just the request that stalled. */
    const worker = loadWorker();
    worker.cache.set("https://nova.example/app.js", new Response("cached body", { status: 200 }));
    worker.network(() => new Promise(() => {}));   // opens, never answers

    const answered = fetchEvent(worker, req("https://nova.example/app.js"));
    assert.equal(await pending(answered), true, "nothing should answer before the timer fires");

    worker.fireTimer();
    const response = await answered;
    assert.equal(await response.text(), "cached body");
    assert.equal(response.headers.get("X-Nova-Replayed"), "1",
      "the page has to be able to tell a replay from a live answer");
  });

  test("the wait is eight seconds, above every load anyone has observed", async () => {
    /* Not a spelling check on a constant: the delay asserted here is the
     * one the handler actually asked the clock for. The floor matters more
     * than the exact number -- `/api/conversations` measures ~1s inside the
     * cluster and the slowest whole load he has reported is 5-6 seconds, so
     * a timeout at or under that would turn an ordinary slow load into a
     * silently stale one, which is worse than the hang it replaced. */
    const worker = loadWorker();
    worker.network(() => new Promise(() => {}));
    fetchEvent(worker, req("https://nova.example/api/journal"));
    // An /api route now looks in the cache before it reaches `networkFirst`,
    // so the timer is armed a microtask later than it used to be. A cold
    // cache still ends up in exactly the same place.
    await drain();

    assert.equal(worker.timers.length, 1);
    assert.equal(worker.timers[0].ms, 8000);
    assert.ok(worker.timers[0].ms > 6000,
      "must sit above the slowest load he has actually reported");
  });

  test("a timeout with nothing cached waits for the network rather than erroring", async () => {
    /* The clause that keeps this from being a regression. A cold first
     * load has an empty cache, and rejecting on the timeout would replace
     * a slow load with a browser error page. So the timeout only ever
     * shortens a wait the cache can already answer. */
    const worker = loadWorker();
    let land;
    worker.network(() => new Promise((resolve) => { land = resolve; }));

    const answered = fetchEvent(worker, req("https://nova.example/api/journal"));
    await drain();                                   // the cache lookup, which misses
    worker.fireTimer();
    assert.equal(await pending(answered), true,
      "an empty cache is not an answer -- the network is still all there is");

    land(new Response("late but real", { status: 200 }));
    const response = await answered;
    assert.equal(await response.text(), "late but real");
    assert.equal(response.headers.get("X-Nova-Replayed"), null);
  });

  test("a response that beats the timer is served live and the timer is cleared", async () => {
    /* The ordinary case, which is every load: nothing about the timeout may
     * be observable when the network answers. A timer left running would
     * hold the worker awake for eight seconds after every single request.
     *
     * On the shell rather than on `/api/journal`, which is where this was
     * written, because an /api GET carrying no `If-None-Match` is now
     * answered from the cache first by design -- see the reopen block at the
     * bottom of this file, which asserts the same rule for the /api request
     * that does carry one. Moved rather than deleted: the shell is still
     * network-first and this is still the case that proves it. */
    const worker = loadWorker();
    worker.cache.set("https://nova.example/app.js", new Response("stale", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("live", { status: 200 })));

    const response = await fetchEvent(worker, req("https://nova.example/app.js"));
    assert.equal(await response.text(), "live");
    assert.equal(response.headers.get("X-Nova-Replayed"), null);
    assert.equal(worker.clearedCount(), 1, "the pending timer must be cleared");
  });

  test("a fetch that stalls still fills the cache when it finally lands", async () => {
    /* Why the losing fetch is not aborted. It is on its way to the cache
     * either way, so a response that arrives at nine seconds is what the
     * next load reads instantly instead of paying for again. */
    const worker = loadWorker();
    worker.cache.set("https://nova.example/app.js", new Response("cached body", { status: 200 }));
    let land;
    worker.network(() => new Promise((resolve) => { land = resolve; }));

    const answered = fetchEvent(worker, req("https://nova.example/app.js"));
    worker.fireTimer();
    assert.equal(await (await answered).text(), "cached body");

    land(new Response("arrived late", { status: 200 }));
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(worker.puts, ["https://nova.example/app.js"]);
    assert.equal(await worker.cache.get("https://nova.example/app.js").text(), "arrived late");
  });

  test("a refused connection still falls back to the cache, as it always did", async () => {
    /* The old behaviour, which the race must not have eaten: a rejected
     * fetch is answered from the cache without waiting for the timer. */
    const worker = loadWorker();
    worker.cache.set("https://nova.example/", new Response("the shell", { status: 200 }));
    worker.network(() => Promise.reject(new TypeError("Failed to fetch")));

    const response = await fetchEvent(worker, req("https://nova.example/cycle/49", { mode: "navigate" }));
    assert.equal(await response.text(), "the shell",
      "a deep link opened offline falls back to the shell, not to its own URL");
    assert.equal(response.headers.get("X-Nova-Replayed"), "1");
  });
});

/* Fire the push handler and hand back whatever it passed to waitUntil. */
function pushEvent(worker, payload) {
  let held = null;
  worker.handlers.push({
    data: { json: () => payload, text: () => JSON.stringify(payload) },
    waitUntil(p) { held = p; },
  });
  return held;
}

/* Let every already-resolved promise job run. */
function drain() {
  return new Promise((resolve) => setImmediate(resolve));
}

/* Built the way the page builds it rather than typed out here.
 *
 * This was a literal, and that is how the bug it exists to catch survived:
 * `&limit=` was added to app.js's fetch when paging shipped, the worker was
 * never updated, and this test happily went on pinning the worker to the
 * URL it already used. A test that restates the value it is checking cannot
 * fail when the two sides drift -- it only fails when someone edits the
 * test. Read from app.js, it fails on the drift itself. */
const PAGE_STEP = Number(
  (readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "..",
                     "agora_runner", "nova_public", "app.js"), "utf8")
    .match(/var PAGE_STEP = (\d+);/) || [])[1]);
const THREAD = "https://nova.example/api/conversations/thread?id=c-1"
  + "&limit=" + PAGE_STEP;

describe("a push notification brings its own conversation with it", () => {
  test("the thread is parked under the exact URL app.js will ask for", async () => {
    /* His report, idea #224: the tap costs 5-6 seconds because the app
     * starts from nothing. The id is in the payload, so the thread can be
     * on the device before he finishes reading the banner.
     *
     * The key is the whole assertion. A cache is keyed on the URL, so a
     * query string built even slightly differently here than in `app.js`
     * is a miss that looks exactly like a cold cache -- nothing logs it
     * and the feature silently does nothing forever. */
    const worker = loadWorker();
    worker.network(() => Promise.resolve(new Response("{\"messages\":[]}", { status: 200 })));

    await pushEvent(worker, { title: "Nova", body: "cycle 1089", conversationId: "c-1" });

    assert.deepEqual([...worker.pushCache.keys()], [THREAD]);
    assert.equal(worker.cache.size, 0, "the prefetch must not land in the network-first cache");
  });

  test("the banner is not held behind the fetch", async () => {
    /* The one thing a prefetch may never cost. `waitUntil` keeps the worker
     * alive until the fetch lands, which is what makes the parking reliable,
     * but the notification itself goes out first -- on a dead tailnet link
     * ordering them the other way holds the whole banner behind a request
     * that is never going to answer. The tap is the deadline, not the
     * banner: the thread endpoint measures 0.17-0.40s and nobody sees a
     * notification and taps it faster than that. */
    const worker = loadWorker();
    let land;
    worker.network(() => new Promise((resolve) => { land = resolve; }));

    const held = pushEvent(worker, { title: "Nova", body: "hi", conversationId: "c-1" });
    await drain();

    assert.equal(worker.shown.length, 1, "the notification must already be showing");
    assert.equal(await pending(held), true, "and the worker must still be waiting on the prefetch");

    land(new Response("{}", { status: 200 }));
    await held;
    assert.deepEqual([...worker.pushCache.keys()], [THREAD]);
  });

  test("a prefetch that fails does not take the notification with it", async () => {
    /* Every way this can fail leaves the app exactly as it was -- one
     * ordinary network-first load. Rejecting instead would trade a slow
     * open for no notification at all, which is the failure the feature
     * exists to avoid, made permanent. */
    const worker = loadWorker();
    worker.network(() => Promise.reject(new TypeError("Failed to fetch")));

    await pushEvent(worker, { title: "Nova", body: "hi", conversationId: "c-1" });

    assert.equal(worker.shown.length, 1);
    assert.equal(worker.pushCache.size, 0);
  });

  test("a push with no conversationId still notifies and parks nothing", async () => {
    const worker = loadWorker();
    let asked = 0;
    worker.network(() => { asked += 1; return Promise.resolve(new Response("{}", { status: 200 })); });

    await pushEvent(worker, { title: "Nova", body: "hi" });

    assert.equal(worker.shown.length, 1);
    assert.equal(asked, 0, "there is no id to fetch a thread for -- do not guess one");
    assert.equal(worker.pushCache.size, 0);
  });

  test("nothing is fetched or shown while a Nova tab is already visible", async () => {
    /* The rule that was already here, kept: the page is showing him the
     * thing. A prefetch would be work for a tap that cannot happen. */
    const worker = loadWorker();
    worker.client.visibilityState = "visible";
    let asked = 0;
    worker.network(() => { asked += 1; return Promise.resolve(new Response("{}", { status: 200 })); });

    await pushEvent(worker, { title: "Nova", body: "hi", conversationId: "c-1" });

    assert.equal(worker.shown.length, 0);
    assert.equal(asked, 0);
  });
});

describe("the tap after a push is answered without a round trip", () => {
  test("the parked thread answers immediately and says it was prefetched", async () => {
    const worker = loadWorker();
    worker.pushCache.set(THREAD, new Response("parked body", { status: 200 }));
    worker.network(() => new Promise(() => {}));   // the network never answers

    const response = await fetchEvent(worker, req(THREAD));

    assert.equal(await response.text(), "parked body");
    assert.equal(response.headers.get("X-Nova-Prefetched"), "1");
    assert.equal(worker.timers.length, 0, "networkFirst must not have run at all");
  });

  test("it is used once -- the next load of the same thread goes to the network", async () => {
    /* Single use rather than an expiry, because an expiry would be a
     * number nobody measured. The entry exists because a notification was
     * shown and not yet tapped; the read is the tap. */
    const worker = loadWorker();
    worker.pushCache.set(THREAD, new Response("parked body", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("live body", { status: 200 })));

    assert.equal(await (await fetchEvent(worker, req(THREAD))).text(), "parked body");
    await drain();
    const second = await fetchEvent(worker, req(THREAD));
    assert.equal(await second.text(), "live body");
    assert.equal(second.headers.get("X-Nova-Prefetched"), null);
  });

  test("no other route can ever be answered from the push cache", async () => {
    /* The cache-first rule is gated on the path, not on the lookup coming
     * back empty. An empty push cache would give the same answer today and
     * would stop giving it the moment anything else parked a key there. */
    const worker = loadWorker();
    worker.pushCache.set("https://nova.example/api/journal", new Response("parked", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("live", { status: 200 })));

    const response = await fetchEvent(worker, req("https://nova.example/api/journal"));
    assert.equal(await response.text(), "live");
  });

  test("a thread with nothing parked behaves exactly as it did before", async () => {
    const worker = loadWorker();
    worker.cache.set(THREAD, new Response("stale", { status: 200 }));
    worker.network(() => new Promise(() => {}));

    const answered = fetchEvent(worker, req(THREAD));
    assert.equal(await pending(answered), true);
    worker.fireTimer();
    const response = await answered;
    assert.equal(await response.text(), "stale");
    assert.equal(response.headers.get("X-Nova-Replayed"), "1");
  });
});

describe("stale bytes served from a prefetch are retracted", () => {
  test("a body that moved since the push tells the page to repaint", async () => {
    /* Serving stale bytes is only honest if something corrects them. The
     * banner tapped an hour later, or a reply that finished after the push,
     * is exactly the case the single-use rule cannot see -- so the worker
     * refetches behind the answer it gave and posts when the two differ. */
    const worker = loadWorker();
    worker.pushCache.set(THREAD, new Response("thinking…", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("the whole answer", { status: 200 })));

    assert.equal(await (await fetchEvent(worker, req(THREAD))).text(), "thinking…");
    await drain();
    await drain();

    assert.equal(worker.posted.length, 1);
    assert.equal(worker.posted[0].type, "nova-thread-updated");
    assert.equal(worker.posted[0].conversationId, "c-1");
    assert.equal(await worker.cache.get(THREAD).text(), "the whole answer",
      "and the fresh copy lands in the network-first cache");
  });

  test("an unchanged body says nothing, so the fast path never flickers", async () => {
    const worker = loadWorker();
    worker.pushCache.set(THREAD, new Response("same body", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("same body", { status: 200 })));

    await fetchEvent(worker, req(THREAD));
    await drain();
    await drain();

    assert.deepEqual(worker.posted, []);
  });

  test("a revalidation that cannot reach the network is not reported", async () => {
    /* The messages on screen are real. A network that is down does not make
     * them wrong, and the page's own poll is still running behind this. */
    const worker = loadWorker();
    worker.pushCache.set(THREAD, new Response("parked body", { status: 200 }));
    worker.network(() => Promise.reject(new TypeError("Failed to fetch")));

    assert.equal(await (await fetchEvent(worker, req(THREAD))).text(), "parked body");
    await drain();
    await drain();

    assert.deepEqual(worker.posted, []);
  });
});

describe("activating a new worker keeps the parked thread", () => {
  test("the push cache survives the swap and every other cache does not", async () => {
    /* A prefetch parked by a push that arrived while an update was
     * installing must survive the swap, or every deploy throws away the one
     * entry that makes the next notification open instantly. */
    const worker = loadWorker();
    worker.seedCacheNames(SHELL_CACHE, PUSH_CACHE, "nova-v0");

    let held = null;
    worker.handlers.activate({ waitUntil(p) { held = p; } });
    await held;

    assert.deepEqual(worker.deletedCaches(), ["nova-v0"]);
  });
});

describe("a browser without a usable Cache API still loads a thread", () => {
  test("a rejected caches.open falls through to the network instead of erroring", async () => {
    /* This branch sits in front of the one route a push notification lands
     * on, and `respondWith` renders a browser error page for a promise that
     * rejects. Safari's private mode has historically refused `caches.open`
     * outright, so the fallback is the ordinary load rather than nothing. */
    const worker = loadWorker();
    worker.breakCaches();
    worker.network(() => Promise.resolve(new Response("live body", { status: 200 })));

    const response = await fetchEvent(worker, req(THREAD));
    assert.equal(await response.text(), "live body");
  });
});


/* The prefetch is keyed on a URL, so the two halves have to build the same
 * one. They did not: `&limit=` was added to the page's fetch when paging
 * shipped and the worker was never updated, so every push parked its
 * prefetch under a URL the page never asked for. A guaranteed miss, on the
 * one path whose whole job is to make a notification open instantly, and
 * silent -- a miss reads exactly like a cold cache.
 *
 * Both numbers are read out of the real files rather than restated here,
 * which is the point: this fails when either side moves, not when this test
 * gets out of date. */
describe("the worker prefetches the URL the page actually asks for", () => {
  const appSource = readFileSync(
    join(dirname(fileURLToPath(import.meta.url)), "..", "..",
         "agora_runner", "nova_public", "app.js"), "utf8");

  test("the worker's page size is the page's own PAGE_STEP", () => {
    const workerLimit = Number(constFromSource("THREAD_PAGE_LIMIT"));
    const pageStep = Number((appSource.match(/var PAGE_STEP = (\d+);/) || [])[1]);
    assert.ok(pageStep, "app.js no longer declares PAGE_STEP");
    assert.equal(workerLimit, pageStep,
      "the worker prefetches a page size the dock never asks for");
  });

  test("the two build byte-identical thread URLs", () => {
    /* The worker's builder, run for real; the page's, reproduced from the
     * one line in app.js that writes it. A cache key is the whole string,
     * so this compares the whole string. */
    const worker = loadWorker();
    const id = "c-7 &weird";
    const fromWorker = worker.threadUrl(id);
    const pageStep = Number((appSource.match(/var PAGE_STEP = (\d+);/) || [])[1]);
    const fromPage = "/api/conversations/thread?id=" + encodeURIComponent(id)
      + "&limit=" + pageStep;
    assert.equal(fromWorker, fromPage);
  });
});

/* The reopen he reported on 2026-09-08: *"Nova re-downloads everything every
 * time I reopen the app, which is slow and drains my mobile roaming."*
 *
 * These pin both halves of the fix and, more importantly, the boundary
 * between them: the cold page gets the cache and a conditional refetch, and
 * the warm 30-second poll gets exactly what it got before. */
describe("a reopen is answered from the cache and confirmed with an etag", () => {
  const DIGEST = "https://nova.example/api/digest";

  function cached(worker, body, etag) {
    const headers = etag ? { ETag: etag } : {};
    worker.cache.set(DIGEST, new Response(body, { status: 200, headers }));
  }

  test("the page paints from the cache without waiting for the network", async () => {
    /* The measurement behind this: `/api/digest` is 2.85 MB and a reopen
     * refetched all of it before the page could draw anything. The network
     * below never answers, so a response arriving at all is proof the page
     * did not wait on it. */
    const worker = loadWorker();
    cached(worker, "last time's digest", 'W/"abc"');
    worker.network(() => new Promise(() => {}));

    const response = await fetchEvent(worker, req(DIGEST));
    assert.equal(await response.text(), "last time's digest");
  });

  test("the served copy is stamped stale, and not as an outage", async () => {
    /* `X-Nova-Replayed` is what `renderStatusUnreachable` is built on. A
     * reopen on a perfectly good link must not paint "can't reach Nova". */
    const worker = loadWorker();
    cached(worker, "body", 'W/"abc"');
    worker.network(() => new Promise(() => {}));

    const response = await fetchEvent(worker, req(DIGEST));
    assert.equal(response.headers.get("X-Nova-Stale"), "1");
    assert.equal(response.headers.get("X-Nova-Replayed"), null,
      "a revalidating cache hit is not an unreachable server");
  });

  test("the refetch behind it carries the cached etag", async () => {
    /* This is the byte saving. Without the header the server answers with
     * the whole 2.85 MB again; with it, an empty 304. The page cannot send
     * it on a reopen because `lastPayload` is memory and memory is gone. */
    const worker = loadWorker();
    cached(worker, "body", 'W/"abc"');
    const asked = [];
    worker.network((url, init) => {
      asked.push({ url, init });
      return Promise.resolve(new Response(null, { status: 304 }));
    });

    const answering = fetchEvent(worker, req(DIGEST));
    await answering;
    await answering.settled();

    assert.equal(asked.length, 1);
    assert.equal(asked[0].url, DIGEST);
    assert.equal(asked[0].init.headers["If-None-Match"], 'W/"abc"');
    assert.equal(asked[0].init.cache, "no-store",
      "the browser's own HTTP cache must not answer this instead of the server");
  });

  test("a 304 says nothing to the page, so a reopen never flickers", async () => {
    const worker = loadWorker();
    cached(worker, "body", 'W/"abc"');
    worker.network(() => Promise.resolve(new Response(null, { status: 304 })));

    const answering = fetchEvent(worker, req(DIGEST));
    await answering;
    await answering.settled();

    assert.deepEqual(worker.posted, []);
  });

  test("a copy that moved is replaced and the page is told to poll", async () => {
    const worker = loadWorker();
    cached(worker, "yesterday", 'W/"abc"');
    worker.network(() => Promise.resolve(
      new Response("today", { status: 200, headers: { ETag: 'W/"def"' } })));

    const answering = fetchEvent(worker, req(DIGEST));
    assert.equal(await (await answering).text(), "yesterday");
    await answering.settled();
    await drain();

    assert.equal(worker.posted.length, 1);
    assert.equal(worker.posted[0].type, "nova-api-updated");
    assert.equal(worker.posted[0].url, DIGEST);
    assert.equal(await worker.cache.get(DIGEST).text(), "today");
  });

  test("a poll that already carries its own etag is left on the network", async () => {
    /* The boundary. `fetchVersioned` sends `If-None-Match` out of memory
     * every 30 seconds while the tab is visible, asking whether the payload
     * it is holding moved. Answering that from the cache would answer a
     * question it did not ask and delay the one it did. */
    const worker = loadWorker();
    cached(worker, "cached body", 'W/"abc"');
    worker.network(() => Promise.resolve(new Response("live body", { status: 200 })));

    const withEtag = req(DIGEST, {
      headers: { get: (name) => (name === "If-None-Match" ? 'W/"abc"' : null) },
    });
    const response = await fetchEvent(worker, withEtag);

    assert.equal(await response.text(), "live body");
    assert.equal(response.headers.get("X-Nova-Stale"), null);
  });

  test("a cold cache is network-first exactly as it was", async () => {
    const worker = loadWorker();
    worker.network(() => Promise.resolve(new Response("first load", { status: 200 })));

    const response = await fetchEvent(worker, req(DIGEST));
    assert.equal(await response.text(), "first load");
    assert.equal(response.headers.get("X-Nova-Stale"), null);
  });

  test("a revalidation that cannot reach the server still reports", async () => {
    /* The offline reopen, and the one case where answering from the cache
     * silently would be dishonest rather than merely early. The response
     * went out without `X-Nova-Replayed` because nothing had failed yet, so
     * the page has to be sent back to ask -- that second request carries the
     * etag, takes the network-first path, and gets stamped as a replay. */
    const worker = loadWorker();
    cached(worker, "body", 'W/"abc"');
    worker.network(() => Promise.reject(new TypeError("Failed to fetch")));

    const answering = fetchEvent(worker, req(DIGEST));
    assert.equal(await (await answering).text(), "body");
    await answering.settled();
    await drain();

    assert.equal(worker.posted.length, 1);
    assert.equal(worker.posted[0].type, "nova-api-updated");
    assert.deepEqual(worker.puts, [], "nothing was written over the copy on screen");
  });

  test("a comments read is left on the network -- a path is not a caller", async () => {
    /* `/api/comments` is polled by `fetchAll` exactly like the three that
     * are on the list, and is fetched unconditionally from three other
     * places: `refreshMail`, the reply drawer's 8s wait, and the refetch
     * that runs the moment he posts a comment. None of those ever sends an
     * etag, so none of them would leave this path again -- and the last one
     * would repaint the drawer from the snapshot taken before his comment
     * existed. `fetchPage` guards on `X-Nova-Replayed`, which the cache-first
     * path does not set, so the guard would be dead. */
    const worker = loadWorker();
    const comments = "https://nova.example/api/comments";
    worker.cache.set(comments, new Response("comments before his post", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("comments including his post", { status: 200 })));

    const response = await fetchEvent(worker, req(comments));
    assert.equal(await response.text(), "comments including his post");
    assert.equal(response.headers.get("X-Nova-Stale"), null);
  });

  test("a board is left on the network, so an offline reopen still says so", async () => {
    /* Why this is a list of four paths and not the `/api/` prefix. A board
     * is fetched once per visit and never polled, so nothing would come back
     * to correct a cached copy -- and `fetchPage` draws "can't reach Nova"
     * off the `X-Nova-Replayed` stamp that only the network-first path sets.
     * Serving this from the cache buys a few kilobytes by painting a saved
     * copy as live, which is the failure the whole file is written against. */
    const worker = loadWorker();
    const board = "https://nova.example/api/board?name=issues";
    worker.cache.set(board, new Response("cached board", { status: 200 }));
    worker.network(() => Promise.reject(new TypeError("Failed to fetch")));

    const response = await fetchEvent(worker, req(board));
    assert.equal(await response.text(), "cached board");
    assert.equal(response.headers.get("X-Nova-Replayed"), "1");
    assert.equal(response.headers.get("X-Nova-Stale"), null);
  });

  test("the shell is untouched -- this is scoped to /api", async () => {
    const worker = loadWorker();
    worker.cache.set("https://nova.example/app.js", new Response("cached app.js", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("live app.js", { status: 200 })));

    const response = await fetchEvent(worker, req("https://nova.example/app.js"));
    assert.equal(await response.text(), "live app.js",
      "network-first is what keeps a rebuilt shell from pinning itself");
  });
});
