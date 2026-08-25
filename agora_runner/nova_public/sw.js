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
        return key === CACHE ? null : caches.delete(key);
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

  event.respondWith(
    fetch(request).then(function (response) {
      if (response && response.ok) {
        var copy = response.clone();
        caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
      }
      return response;
    }).catch(function () {
      return caches.match(fallback).then(replayed);
    })
  );
});

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
