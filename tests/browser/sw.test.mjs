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
  const found = source.match(new RegExp(`var ${name} = "([^"]+)"`));
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
    fetch: (request) => respondToFetch(request),
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

/* Fire the fetch handler and hand back whatever it passed to respondWith. */
function fetchEvent(worker, request) {
  let answered = null;
  worker.handlers.fetch({ request, respondWith(p) { answered = p; } });
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
     * hold the worker awake for eight seconds after every single request. */
    const worker = loadWorker();
    worker.cache.set("https://nova.example/api/journal", new Response("stale", { status: 200 }));
    worker.network(() => Promise.resolve(new Response("live", { status: 200 })));

    const response = await fetchEvent(worker, req("https://nova.example/api/journal"));
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

const THREAD = "https://nova.example/api/conversations/thread?id=c-1";

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
