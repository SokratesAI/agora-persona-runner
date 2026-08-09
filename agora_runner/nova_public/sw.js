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
var SHELL = ["/", "/app.js", "/style.css", "/icon.svg", "/manifest.webmanifest"];

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

  // /cycle/49 is served by the same shell as / -- fall back to the shell
  // rather than the URL, or a deep link opened offline misses the cache.
  var fallback = url.pathname.indexOf("/cycle/") === 0 ? "/" : request;

  event.respondWith(
    fetch(request).then(function (response) {
      if (response && response.ok) {
        var copy = response.clone();
        caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
      }
      return response;
    }).catch(function () {
      return caches.match(fallback);
    })
  );
});
