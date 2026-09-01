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

/* One sandbox per test. The worker keeps module-level state (`CACHE`, the
 * registered handlers) and sharing it across tests would let one test's
 * cache answer another's request. */
function loadWorker() {
  const handlers = {};
  const cache = new Map();          // request key -> Response
  const puts = [];
  const timers = [];                // pending setTimeout callbacks
  let cleared = 0;
  let respondToFetch = null;        // set per test

  const self = {
    location: { origin: "https://nova.example" },
    addEventListener(name, fn) { handlers[name] = fn; },
    skipWaiting() { return Promise.resolve(); },
    clients: { claim: () => Promise.resolve(), matchAll: () => Promise.resolve([]) },
    registration: { showNotification: () => Promise.resolve() },
  };

  const caches = {
    open: () => Promise.resolve({
      addAll: () => Promise.resolve(),
      put(request, response) { puts.push(key(request)); cache.set(key(request), response); },
    }),
    keys: () => Promise.resolve([]),
    delete: () => Promise.resolve(true),
    match: (request) => Promise.resolve(cache.get(key(request))),
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
    cache,
    puts,
    timers,
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
