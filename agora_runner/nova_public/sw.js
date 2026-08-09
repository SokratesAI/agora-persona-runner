/* Enough service worker to make this installable and survive a dead
 * tailnet link, and no more.
 *
 * Network-first for /api, so an open tab never shows a stale journal
 * while the network is fine -- the cache is a fallback, not a layer.
 * Cache-first for the shell, which changes only when the image is
 * rebuilt. CACHE is version-suffixed and old versions are dropped on
 * activate, so a shell change actually reaches an installed copy.
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

  if (url.pathname.indexOf("/api/") === 0) {
    event.respondWith(
      fetch(request).then(function (response) {
        var copy = response.clone();
        caches.open(CACHE).then(function (cache) { cache.put(request, copy); });
        return response;
      }).catch(function () {
        return caches.match(request);
      })
    );
    return;
  }

  // /cycle/49 is served by the same shell as / -- match the shell rather
  // than the URL, or a deep link opened offline misses the cache.
  var shellRequest = url.pathname.indexOf("/cycle/") === 0 ? "/" : request;
  event.respondWith(
    caches.match(shellRequest).then(function (hit) {
      return hit || fetch(request);
    })
  );
});
