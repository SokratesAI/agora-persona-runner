/* Enough service worker to make this installable and survive a dead
 * tailnet link, and no more.
 *
 * Network-first for everything, cache as the fallback -- not a layer in
 * front of the network. The shell gets the same treatment as /api even
 * though it changes far less often, because the obvious alternative is
 * a trap: cache-first for the shell only reaches an installed copy
 * again if the cache name changes, and `CACHE` below is a constant in a
 * file that is rebuilt without editing it. A cache-first shell keyed on
 * a hardcoded version is a PWA that pins itself to the first build it
 * ever saw. Network-first costs one conditional request on a tailnet
 * and cannot do that.
 *
 * The install-time precache is still worth having: it is what makes a
 * genuinely offline first load work.
 */
var CACHE = "nova-v1";
/* Where a push handler parks the thread its notification is about.
 *
 * A second cache rather than a corner of the first one, because the two
 * have opposite reading rules. Everything in `CACHE` is a fallback the
 * network gets to beat; an entry in here is served *ahead* of the
 * network, once, and then deleted. Keeping them apart is what stops the
 * cache-first rule leaking onto any other route -- `servePrefetched`
 * below can only ever answer for a key that a push put here.
 */
var PUSH_CACHE = "nova-push-v1";
/* The vendored charting library is in here for the same reason it is
 * vendored at all: the costs page has to draw on a dead tailnet link.
 * app.js loads it lazily on the first chart, so the fetch handler below
 * only ever caches it after a visit made *while online* -- which makes
 * the costs page the one page whose first offline load is a blank box.
 * It is 1.0 MB and the install pays for it once.
 */
/* `/vendor/mermaid.min.js` is deliberately NOT in this list, and that is a
 * different answer to the same question the paragraph above settles for
 * ECharts. It is 3.5 MB against ECharts' 1.0, and a chart page is a page
 * the owner opens; a mermaid diagram only exists if a message happens to
 * carry one, which most do not. Precaching it would put three and a half
 * megabytes on every install for a feature many installs never reach. The
 * fetch handler below caches it on first use like anything else, so a
 * diagram he has already seen still draws offline, and one he has not
 * falls back to the code block `app.js` leaves on screen.
 */
var SHELL = ["/", "/app.js", "/style.css", "/icon.svg", "/manifest.webmanifest",
             "/vendor/echarts.min.js"];

self.addEventListener("install", function (event) {
  event.waitUntil(
    caches.open(CACHE).then(function (cache) {
      return cache.addAll(SHELL);
    }).then(function () {
      return self.skipWaiting();
    })
  );
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (key) {
        // PUSH_CACHE is spared as well as CACHE. It is the only other
        // cache this worker owns, and a prefetch parked by a push that
        // arrived while an update was installing must survive the swap --
        // otherwise every deploy throws away the one entry that makes the
        // next notification open instantly.
        return key === CACHE || key === PUSH_CACHE ? null : caches.delete(key);
      }));
    }).then(function () {
      return self.clients.claim();
    })
  );
});

self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.method !== "GET") return;
  var url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // /cycle/49, /issues and /ideas are all served by the same shell as / --
  // fall back to the shell rather than the URL, or a deep link opened
  // offline misses the cache. Keyed on the request being a navigation
  // rather than on a list of paths, so the next page added to app.js's
  // router cannot forget to add itself here.
  var fallback = request.mode === "navigate" && url.pathname !== "/" ? "/" : request;

  /* The one route a push can have already answered.
   *
   * Gated on the path rather than on the cache lookup coming back empty,
   * so the cache-first rule below is off every other route by
   * construction. An empty `PUSH_CACHE` would give the same answer today
   * and would stop giving it the moment anything else parked a key there.
   */
  if (url.pathname === THREAD_PATH) {
    event.respondWith(servePrefetched(request, url).catch(function () {
      // The Cache API is not guaranteed to be there. Safari's private mode
      // has historically rejected `caches.open` outright, and this branch
      // sits in front of the one route a push notification lands on -- a
      // rejection here would reach `respondWith` and turn his tap into a
      // browser error page. `networkFirst` survives the same thing because
      // its own cache reads are all on failure paths.
      return undefined;
    }).then(function (hit) {
      return hit || networkFirst(request, fallback);
    }));
    return;
  }

  event.respondWith(networkFirst(request, fallback));
});

/* The thread endpoint, spelled once.
 *
 * `app.js` builds this URL to fetch a conversation and the `push` handler
 * below builds the same one to prefetch it. They have to agree exactly --
 * a cache is keyed on the URL, so a query string in the other order is a
 * miss that looks like a cold cache and nothing anywhere reports it.
 */
var THREAD_PATH = "/api/conversations/thread";

function threadUrl(conversationId) {
  return THREAD_PATH + "?id=" + encodeURIComponent(conversationId);
}

/* How long the network gets before the cache answers instead.
 *
 * `fetch` has no timeout of its own and never has. A refused connection
 * rejects and the `.catch` below has always handled that; a connection
 * that *opens and then stalls* -- a phone on a dying tailnet link, a
 * relay that accepted the SYN and went away -- never rejects and never
 * resolves, so the promise handed to `respondWith` hangs, and the tab
 * hangs with it. Every route goes through here, so a stalled fetch for
 * the shell hangs the whole app open, not just the request that stalled.
 * That is the failure the owner asked to fix before anything is built on
 * top of this handler, and it is the same one Marcus's worker had.
 *
 * The number is a ceiling on a slow load, not a guess at a fast one, so
 * it is set from the slowest thing this app is known to do rather than
 * from what feels responsive. Measured from inside the cluster on
 * 2026-09-01: the shell 3-6ms, `/app.js` 6ms, `/api/journal` 6-11ms, and
 * `/api/conversations` 795-978ms, which is the slow one because it
 * proxies Agora. Add the tailnet leg to his phone, which I cannot
 * measure from here, and the slowest whole load he has actually reported
 * is the five to six seconds it took a push notification to open. Eight
 * seconds therefore sits above every load anyone has observed and an
 * order of magnitude above every server-side measurement.
 *
 * Setting it lower is the real risk in both directions: too low turns an
 * ordinary slow load into a silently stale one, which is worse than the
 * hang, because a hang is visible and stale data is not.
 */
var NETWORK_TIMEOUT_MS = 8000;

/* Network first, cache second, and the network again if the cache is empty.
 *
 * The last clause is the one worth stating. On a timeout with nothing
 * cached there is no answer to give, and rejecting here would turn a slow
 * first load into a browser error page -- strictly worse than the wait it
 * replaced. So the timeout only ever *shortens* the wait for a request the
 * cache can already answer; for anything else it costs nothing and changes
 * nothing.
 *
 * The losing fetch is deliberately not aborted. It is still filling the
 * cache on its way past, so a response that arrives at nine seconds is
 * what the next load reads instantly instead of paying for again.
 *
 * A stall and a refusal are therefore *not* the same event here, which is
 * the one thing the race must keep straight. A refused connection is a
 * final answer -- there is nothing left in flight to wait for -- so on a
 * cache miss it resolves to `undefined` exactly as it did before this,
 * and that undefined is what the page's own "can't reach Nova" path is
 * built on. A stall on a cache miss has a live request still running and
 * waits for it.
 */
function networkFirst(request, fallback) {
  var network = fetch(request).then(function (response) {
    if (response && response.ok) {
      var copy = response.clone();
      // Detached on purpose -- the response is already on its way to the
      // page and must not wait on the write. That makes the `.catch`
      // load-bearing rather than defensive: a browser whose Cache API is
      // unavailable would otherwise raise an unhandled rejection inside
      // the worker on every successful request.
      caches.open(CACHE)
        .then(function (cache) { cache.put(request, copy); })
        .catch(function () { /* nothing cached is not a failed request */ });
    }
    return response;
  });

  var timer = null;
  var stalled = new Promise(function (resolve) {
    timer = setTimeout(function () { resolve(TOO_SLOW); }, NETWORK_TIMEOUT_MS);
  });

  return Promise.race([
    network.catch(function () { return REFUSED; }),
    stalled,
  ]).then(function (result) {
    if (timer !== null) clearTimeout(timer);
    if (result !== REFUSED && result !== TOO_SLOW) return result;
    return caches.match(fallback).then(function (hit) {
      if (hit) return replayed(hit);
      return result === TOO_SLOW ? network : replayed(hit);
    });
  });
}

/* Answer a thread request from what the push already fetched, once.
 *
 * This is the only cache-first read in the file and it is deliberately
 * the narrowest one that can exist: it answers exactly one path, only
 * for a key a `push` handler put there, and it deletes that key as it
 * reads it. A second load of the same thread goes back down the ordinary
 * network-first path, so nothing here can pin the app to an old body.
 *
 * Single use rather than an expiry, because an expiry would be a number
 * I would have to invent. The entry exists because a notification was
 * shown and has not been tapped yet; the read *is* the tap. What that
 * leaves open is a tap hours after the banner, where the parked body is
 * genuinely old -- and that is what `revalidate` below covers, by
 * telling the page when the fresh copy differs so it can repaint. Serving
 * stale bytes is only safe if something retracts them, and the whole
 * point of the file's own `X-Nova-Replayed` comment is that stale data
 * is worse than slow data precisely because it is invisible.
 *
 * A missing entry resolves to `undefined` and the caller falls through to
 * `networkFirst`, which is every request except the first one after a
 * push.
 */
function servePrefetched(request, url) {
  return caches.open(PUSH_CACHE).then(function (cache) {
    return cache.match(request).then(function (hit) {
      if (!hit) return undefined;
      cache.delete(request);
      return hit.text().then(function (body) {
        revalidate(request, url, body);
        var headers = new Headers(hit.headers);
        headers.set("X-Nova-Prefetched", "1");
        return new Response(body, {
          status: hit.status,
          statusText: hit.statusText,
          headers: headers,
        });
      });
    });
  });
}

/* Fetch the thread again behind the answer we just gave, and say so if it
 * moved.
 *
 * Deliberately silent when the bodies match, which is the ordinary case --
 * he taps a banner seconds after it appears and nothing has changed since
 * the prefetch. Repainting then would be a flicker with no new information
 * in it. The comparison is on the whole body rather than on a message
 * count or an etag, because the thing that changes between the prefetch
 * and the tap is usually a reply finishing, which rewrites a message
 * already in the list rather than appending one.
 *
 * A failed revalidation is not reported. The page is already showing real
 * messages; a network that is down does not make them wrong, and the
 * app's own poll is still running behind this.
 */
function revalidate(request, url, served) {
  var conversationId = url.searchParams.get("id");
  return fetch(request).then(function (response) {
    if (!response || !response.ok) return undefined;
    var copy = response.clone();
    caches.open(CACHE)
      .then(function (cache) { cache.put(request, copy); })
      .catch(function () { /* see the same write in `networkFirst` */ });
    return response.text().then(function (fresh) {
      if (fresh === served) return undefined;
      return self.clients.matchAll({ type: "window" }).then(function (clients) {
        clients.forEach(function (client) {
          client.postMessage({ type: "nova-thread-updated", conversationId: conversationId });
        });
      });
    });
  }).catch(function () { return undefined; });
}

/* Sentinels rather than `null` or `undefined`, because a cache miss
 * resolves to `undefined` and this must not mistake one for the other.
 * Two of them because a stall and a refusal end differently: see the
 * comment above `networkFirst`. */
var TOO_SLOW = { novaStalled: true };
var REFUSED = { novaRefused: true };

/* Say, in the response itself, that these bytes came out of the cache.
 *
 * Without this the page cannot tell the two apart, because this handler
 * answers a dead network with a perfectly ordinary `200`. That is the
 * whole point of network-first -- and it also quietly defeats
 * `renderStatusUnreachable`, which exists precisely so the app never
 * asserts an unconfirmed status as current. The fetch did not fail from
 * where the page is standing, so the honest path never runs and the page
 * renders hours-old data as live.
 *
 * For the stall badge that is not merely untidy, it is issue #81. The
 * journal etag folds in `silentIntervals`, so during a real stall the
 * payload sitting in this cache is one that says `stalled: true`. A phone
 * resuming from sleep polls before the tailnet is up, this handler replays
 * that body, the badge appears -- and the next poll, once the network is
 * back, retracts it. A badge that flashes and disappears, with nothing
 * wrong on the server and nothing to find in its logs.
 *
 * A header rather than a rewritten body: the body is 184KB of JSON and
 * this must not parse it. The response is rebuilt rather than mutated
 * because `Headers` on a cached `Response` is immutable. Same-origin, so
 * the page can read a custom header off it -- the note in `app.js` about
 * unreachable headers is about the ETag on a raw cache hit, which is a
 * different thing from one we construct here.
 */
function replayed(hit) {
  if (!hit) return hit;
  var headers = new Headers(hit.headers);
  headers.set("X-Nova-Replayed", "1");
  return hit.blob().then(function (body) {
    return new Response(body, {
      status: hit.status,
      statusText: hit.statusText,
      headers: headers,
    });
  });
}

/* Push. Until 2026-08-28 the Agora PWA was the only thing in either repo
 * that could register a subscription, so every notification the owner got
 * -- including every cycle reply -- was delivered to a subscription his
 * Agora app created, while he had said he would never open Agora again
 * (issues.md #119). Agora still owns the VAPID keypair and does the
 * sending; this file only has to render what arrives at Nova's origin.
 *
 * No notification while a Nova tab is visible: the page is already showing
 * him the thing, and a system banner on top of that is noise rather than a
 * missed-message alert. That is the same call Agora's own worker makes.
 */
self.addEventListener("push", function (event) {
  var data = { title: "Nova", body: "" };
  try {
    if (event.data) data = event.data.json();
  } catch (err) {
    data = { title: "Nova", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then(function (clients) {
      var open = clients.some(function (c) { return c.visibilityState === "visible"; });
      if (open) return undefined;
      /* The notification and the prefetch go out together, and the banner
       * is never made to wait for the fetch.
       *
       * Idea #224 asks for the thread to be cached "before the notification
       * even shows", and the goal behind that -- a warm cache by the time
       * he taps -- is what this does. What it does not do is put a network
       * request in front of the banner. The tap is the deadline, not the
       * banner: measured from inside the cluster on 2026-09-01 the thread
       * endpoint answers in 0.17-0.40s, and nobody sees a notification and
       * taps it faster than that. Ordering them the other way would trade a
       * guaranteed delay on every notification for a saving on none of
       * them, and on a dead tailnet link it would hold the whole banner
       * behind a fetch that is never going to land.
       *
       * `Promise.all` rather than a bare call, so `waitUntil` keeps the
       * worker alive until the prefetch has actually been written.
       */
      return Promise.all([
        self.registration.showNotification(data.title || "Nova", {
          body: data.body || "",
          icon: "/icon.svg",
          data: { conversationId: data.conversationId || null },
        }),
        prefetchThread(data.conversationId),
      ]);
    })
  );
});

/* Park the conversation this notification is about, so the tap costs nothing.
 *
 * His report, idea #224: *"Notification tap-to-open takes 5-6 seconds to
 * show the actual message, not instant like Messenger."* Cycle 766 took the
 * round trip for the heading off that path and #226 named this as the piece
 * that removes the rest: the id is already in the payload, so the thread can
 * be on the device before he has finished reading the banner.
 *
 * Errors are swallowed on purpose. A prefetch is an optimisation and every
 * way it can fail -- no id in the payload, a refused connection, a 500 --
 * leaves the app exactly as it was, which is one ordinary network-first
 * load. Rejecting here would instead take the notification down with it,
 * turning a slow open into no notification at all.
 */
function prefetchThread(conversationId) {
  if (!conversationId) return Promise.resolve(undefined);
  var url = threadUrl(conversationId);
  return fetch(url).then(function (response) {
    if (!response || !response.ok) return undefined;
    return caches.open(PUSH_CACHE).then(function (cache) {
      return cache.put(url, response.clone());
    });
  }).catch(function () { return undefined; });
}

/* Where a tap lands.
 *
 * This used to focus the first open Nova window and navigate nowhere, so
 * the notification opened whatever page that window was already showing.
 * The owner reported it on 2026-08-30: *"I quickly red it, clicked it and
 * it opened Nova but to the issues page. I therefore lost the context of
 * the message."* He had a board page open, so that is what focusing gave
 * him, and the text he had half-read was gone with the banner.
 *
 * The id to land on was already in the payload -- Agora sends
 * `conversationId` with every push and the `push` handler above has been
 * storing it on the notification the whole time. Nothing read it.
 *
 * `navigate` rather than `focus` alone, because focusing an existing tab
 * is what loses the destination. It is only defined on a client this
 * worker controls, and `includeUncontrolled` is deliberately still on:
 * a window it cannot navigate is still better focused than ignored,
 * which is what the fallbacks below are for.
 */
self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var data = event.notification.data || {};
  var target = data.conversationId
    ? "/conversation/" + encodeURIComponent(data.conversationId)
    : "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (clients) {
      for (var i = 0; i < clients.length; i++) {
        var client = clients[i];
        if (typeof client.navigate === "function") {
          return client.navigate(target).then(function (moved) {
            return (moved || client).focus();
          }).catch(function () {
            return client.focus();
          });
        }
        if ("focus" in client) return client.focus();
      }
      return self.clients.openWindow(target);
    })
  );
});
