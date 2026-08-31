/* Scripted interactions against a running Nova site, in a real browser.
 *
 * `shot.js` loads a page and looks at it. That catches a page that did
 * not render; it cannot catch a control that stops working the moment
 * you touch it, which is the class of bug this app actually ships.
 *
 * Each probe below writes one JSON line: {probe, ok, detail, errors}.
 * `poke_page.py` reads those and decides. Keeping the verdict on the
 * Python side means a probe that throws is a failure rather than a
 * missing line nobody notices.
 */
const { chromium } = require('playwright-core');

const base = process.env.NOVA_SITE || 'http://nova-site:8083';
const width = parseInt(process.env.NOVA_WIDTH || '390', 10);

// A phone context, not a narrow desktop one. The keyboard bug of Cycle
// 211 only reproduces with touch: a desktop browser re-focuses an input
// that was replaced under the cursor, and a phone does not.
const PHONE = {
  viewport: { width, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
  userAgent:
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 ' +
    '(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1',
};

/* How long to wait between keystrokes in the search probe.
 *
 * It has to stay comfortably above the board search's debounce --
 * `setTimeout(..., 200)` in `app.js`, `runBoardSearch` -- because typing
 * faster than that lets every keystroke coalesce into a single render,
 * and a probe that never provokes a second render passes without having
 * tested anything.
 *
 * The coupling is to a constant in another file that nothing checks, so
 * the margin is deliberately wide rather than minimal: at 250 it was
 * 50ms, and a debounce raised to 300 during some future tuning would
 * silently blind this probe instead of failing loudly.
 */
const KEY_GAP_MS = 450;

function watch(page) {
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  return errors;
}

/* Take the *worker* offline, not just the page -- for real.
 *
 * Two emulated approaches were measured on 2026-08-15 (Cycle 217) and
 * both are dead ends, written down so nobody pays for them twice:
 *
 * - `context.setOffline(true)` only reaches requests made from the page.
 *   A service worker runs in its own target with its own network stack,
 *   so its `fetch(request)` still succeeds -- `sw.js` never enters its
 *   `catch`, never replays, never stamps. The probe then reports "no
 *   banner" whether or not the banner works, which is a negative result
 *   guaranteed in advance. Measured: controlled page, offline context,
 *   `X-Nova-Replayed` null and a live 200 body.
 * - `context.newCDPSession(worker)` would steer the worker's own target,
 *   and this Playwright rejects it: `page: expected Page or Frame`.
 *
 * So do not emulate. Put a plain TCP forwarder in front of the site,
 * point the browser at that, and close it. Every socket from every
 * target dies at once, which is what losing a tailnet actually is, and
 * it needs no cooperation from Playwright at all. It also has to be
 * 127.0.0.1 regardless: a service worker will not register on an
 * insecure origin, and localhost is the only one a browser trusts
 * without a security override that would itself weaken the test.
 */
const net = require('net');
const { URL } = require('url');

/* Every forwarder ever opened, so the runner can close them all.
 *
 * A listening server keeps node's event loop alive. If a probe throws
 * between `listen()` and `cut()` -- a selector that never appears, a
 * navigation timeout -- the process would sit there until the caller's
 * 600s subprocess timeout, and one broken probe would cost ten minutes
 * and look like a hang rather than a failure. `poke_page.py` treats a
 * throw as a failed probe precisely so the run can continue; leaking
 * the socket would undo that.
 */
const OPEN_FORWARDERS = [];

function forwarder(target) {
  const to = new URL(target);
  const sockets = new Set();
  const server = net.createServer((client) => {
    const upstream = net.connect(Number(to.port || 80), to.hostname);
    sockets.add(client);
    sockets.add(upstream);
    client.on('error', () => {});
    upstream.on('error', () => {});
    client.pipe(upstream);
    upstream.pipe(client);
  });
  const handle = {
    listen: () =>
      new Promise((resolve) => server.listen(0, '127.0.0.1', () => resolve(server.address().port))),
    // Closing the listener alone would leave established keep-alive
    // connections working, and the worker holds one. Destroy them too.
    cut: () =>
      new Promise((resolve) => {
        for (const s of sockets) s.destroy();
        server.close(() => resolve());
      }),
  };
  OPEN_FORWARDERS.push(handle);
  return handle;
}

/* Wait until the worker controlling this page is one we can steer.
 *
 * `serviceWorker.ready` resolving is not enough: an active registration
 * does not mean this page is controlled by it, and an uncontrolled page
 * never reaches the fetch handler at all -- its offline reads come out
 * of Chromium's own HTTP cache as an ordinary 200 with none of our
 * headers, which looks exactly like a broken worker.
 */
async function workerReady(page) {
  return page.evaluate(async () => {
    if (!navigator.serviceWorker) return { registered: false, controlled: false };
    const reg = await navigator.serviceWorker.ready.catch(() => null);
    return {
      registered: !!(reg && reg.active),
      controlled: !!navigator.serviceWorker.controller,
    };
  });
}

/* Typing into the board search must not close the keyboard.
 *
 * Cycle 211: the page rebuilt every row on each keystroke, which
 * destroyed the input being typed into, and a phone will not reopen the
 * keyboard for a focus that arrives after the tap. 267 green browser
 * tests were structurally blind to it -- they assert on the DOM after a
 * render, and this is a fact about the transition between two renders.
 *
 * So the two things measured here are the two things that were wrong:
 * how many blur events the input fired, and whether the node the user
 * is typing into is still the same object afterwards. Either one alone
 * can be satisfied by an implementation that still breaks the keyboard.
 */
async function probeSearchFocus(browser, path) {
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  const errors = watch(page);
  await page.goto(base + path, { waitUntil: 'networkidle', timeout: 30000 });

  const input = page.locator('.board-search-input');
  await input.waitFor({ state: 'visible', timeout: 15000 });
  await input.tap();

  // Tag the node and count blurs from inside the page. Both have to be
  // installed on the element itself: a listener on document would also
  // hear blurs from rows being replaced, which is not the question.
  await page.evaluate(() => {
    const box = document.querySelector('.board-search-input');
    window.__novaBlurs = 0;
    window.__novaTag = 'poke-' + Math.random();
    box.dataset.novaTag = window.__novaTag;
    box.addEventListener('blur', () => { window.__novaBlurs += 1; });
  });

  const typed = 'issue';
  for (const ch of typed) {
    await page.keyboard.type(ch);
    await page.waitForTimeout(KEY_GAP_MS);
  }
  await page.waitForTimeout(600);

  const detail = await page.evaluate(() => {
    const box = document.querySelector('.board-search-input');
    return {
      blurs: window.__novaBlurs,
      // The tag rides on the element. A new element has no tag, so this
      // is node identity and not a re-read of the same variable.
      sameNode: !!box && box.dataset.novaTag === window.__novaTag,
      stillFocused: document.activeElement === box,
      value: box ? box.value : null,
    };
  });
  await ctx.close();

  const ok =
    detail.blurs === 0 && detail.sameNode && detail.stillFocused && detail.value === typed;
  return { probe: 'search-focus' + path, ok, detail, errors };
}

/* The offline banner, under a real service worker.
 *
 * Nothing in this loop had ever run `sw.js` under an actual
 * registration -- jsdom has no workers -- so "a real phone reads
 * X-Nova-Replayed off a worker-built response" was reasoned, never
 * measured. The three steps are the ones a phone in a tunnel takes:
 * install the worker, let it cache a page, then lose the network.
 *
 * Registration needs a secure context and `--base` is plain http, so
 * the browser is pointed at the `forwarder` above rather than at `base`
 * directly -- localhost is trusted without a security override. If the
 * worker still does not register, this reports `registered: false` and
 * fails rather than quietly skipping.
 */
async function probeOfflineBanner(browser, path) {
  const fwd = forwarder(base);
  const port = await fwd.listen();
  const origin = 'http://127.0.0.1:' + port;
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  const errors = watch(page);

  await page.goto(origin + path, { waitUntil: 'networkidle', timeout: 30000 });
  const sw = await workerReady(page);
  if (!sw.registered) {
    await ctx.close();
    await fwd.cut();
    return { probe: 'offline-banner' + path, ok: false, detail: sw, errors };
  }

  // Warm the cache through the worker: the first load above may have
  // been served before the worker took control of the page.
  await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(500);

  await fwd.cut();
  await page.reload({ waitUntil: 'load', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1500);

  const text = await page.locator('body').innerText().catch(() => '');
  // Re-read control *here*, not from the `workerReady` above. That call
  // runs before the warm reload, and the warm reload exists precisely
  // because the first load is often not yet controlled -- so the early
  // value reads `false` on a perfectly healthy run and would send a
  // future cycle debugging the wrong end. `probeReplayHeader` reads it
  // live for the same reason.
  const controlled = await page
    .evaluate(() => !!navigator.serviceWorker.controller)
    .catch(() => null);
  const detail = {
    registered: true,
    controlled,
    textLen: text.trim().length,
    banner: text.includes('showing a saved copy'),
    // The head is what makes a failure diagnosable rather than just
    // red: "no banner" and "the app rendered an error page" look
    // identical without it.
    head: text.trim().slice(0, 200).replace(/\s+/g, ' '),
  };
  await ctx.close();

  // A blank page offline would also lack the banner, so both halves are
  // required: the app has to still be there *and* say it is stale.
  return {
    probe: 'offline-banner' + path,
    ok: detail.banner && detail.textLen > 200,
    detail,
    errors,
  };
}

/* Does `sw.js` itself stamp a replayed response, separately from whether
 * the app then says so?
 *
 * `offline-banner` above is an end-to-end check, and when it fails it
 * cannot say which end. This asks the worker directly: go offline, fetch
 * an API URL from inside the page, and report the status and the
 * `X-Nova-Replayed` header exactly as the app's `isReplayed` would read
 * them. Two probes that fail together mean the worker; this one green
 * and that one red means the app.
 */
async function probeReplayHeader(browser) {
  const fwd = forwarder(base);
  const port = await fwd.listen();
  const ctx = await browser.newContext(PHONE);
  const page = await ctx.newPage();
  const errors = watch(page);

  await page.goto('http://127.0.0.1:' + port + '/', { waitUntil: 'networkidle', timeout: 30000 });
  const sw = await workerReady(page);
  if (!sw.registered) {
    await ctx.close();
    await fwd.cut();
    return { probe: 'replay-header', ok: false, detail: sw, errors };
  }
  await page.reload({ waitUntil: 'networkidle', timeout: 30000 });
  // Warm the cache for the exact URL asked for below. The worker caches
  // per request, so a URL the app never fetched has nothing to replay.
  await page.evaluate(() => fetch('/api/board?name=issues').then((r) => r.text()));
  await page.waitForTimeout(500);

  await fwd.cut();
  const detail = await page.evaluate(async () => {
    try {
      const r = await fetch('/api/board?name=issues');
      return {
        registered: true,
        // An active registration is not the same as a *controlled*
        // page: an uncontrolled page never reaches the worker's fetch
        // handler at all, and its offline reads come from Chromium's
        // own HTTP cache -- 200, real body, none of our headers. That
        // is indistinguishable from a broken worker without this field.
        controlled: !!navigator.serviceWorker.controller,
        status: r.status,
        replayed: r.headers.get('X-Nova-Replayed'),
        type: r.type,
      };
    } catch (e) {
      return { registered: true, threw: String(e.message).slice(0, 200) };
    }
  });
  await ctx.close();

  return { probe: 'replay-header', ok: detail.status === 200 && detail.replayed === '1', detail, errors };
}

/* Every link in the menu drawer is reachable, on the phone that reported it.
 *
 * The owner, `issues.md` 2026-08-26: *"The sliding sidebar menu in Nova is
 * now so full with pages links that it starts to move out the bottom of
 * my screen."* The drawer is a `position: fixed` flex column pinned
 * `top: 0; bottom: 0`, so its box is exactly one viewport tall; the
 * links inside it are not. Without an `overflow-y` the surplus
 * simply hangs below the bottom edge, unreachable -- `body.nav-open`
 * stops the page behind from scrolling, and the drawer itself never
 * scrolled.
 *
 * The viewport is the owner's, not the file's default: 360x697 CSS px, off the
 * device report pasted into `notes.md`. That matters because the
 * probe's failure has to be *possible* -- at the 844px this file uses
 * elsewhere the links very nearly fit, and a probe that is green before
 * the fix measures nothing.
 *
 * The assertion is the user's sentence, not the implementation: scroll
 * the drawer as far down as it goes and the last link must be inside the
 * viewport. `scrollable` is reported beside it rather than asserted, so
 * a future layout that makes every link fit outright still passes. The
 * count is deliberately not written down here -- it was "thirteen" until
 * Cycle 461 grouped the menu, and a probe whose comment names a number
 * the page can change is a comment that goes stale on its own.
 */
async function probeNavReachable(browser, path) {
  const ctx = await browser.newContext({ ...PHONE, viewport: { width: 360, height: 697 } });
  const page = await ctx.newPage();
  const errors = watch(page);
  await page.goto(base + path, { waitUntil: 'networkidle', timeout: 30000 });

  await page.locator('.menu-btn').tap();
  await page.waitForTimeout(400); // the slide is 220ms

  const detail = await page.evaluate(() => {
    const nav = document.querySelector('.nav');
    const tabs = nav ? nav.querySelectorAll('.nav-tab') : [];
    const last = tabs.length ? tabs[tabs.length - 1] : null;
    if (!nav || !last) return { open: false };
    // Ask for the bottom before scrolling too, so the report says
    // whether this drawer needed the scroll at all.
    const before = last.getBoundingClientRect().bottom;
    nav.scrollTop = nav.scrollHeight;
    const after = last.getBoundingClientRect().bottom;
    return {
      open: nav.classList.contains('open'),
      tabs: tabs.length,
      viewport: window.innerHeight,
      scrollable: nav.scrollHeight > nav.clientHeight,
      lastBottomBeforeScroll: Math.round(before),
      lastBottomAfterScroll: Math.round(after),
      lastText: last.textContent.trim(),
    };
  });
  await ctx.close();

  const ok = !!detail.open && detail.tabs > 0 && detail.lastBottomAfterScroll <= detail.viewport;
  return { probe: 'nav-reachable' + path, ok, detail, errors };
}

/* The page behind the chat sheet does not scroll while the sheet is up.
 *
 * The owner, `issues.md` 2026-08-31: *"When i have the chat modal open, i
 * should not be able to scroll the page its hovering over. Currently i can
 * and its wierd."* The dock is `position: fixed`, so the document under it
 * is a perfectly ordinary scroller and every gesture that misses the
 * thread -- the head, the composer, a flick past the last message --
 * scrolls the journal behind. Closing the sheet then lands him somewhere
 * he never navigated to.
 *
 * **The gesture has to be a wheel, and `window.scrollTo` is the trap.**
 * `overflow: hidden` stops the *user* scrolling a box; it deliberately
 * goes on allowing script to scroll it, which is what makes
 * `scrollTo`-into-a-clipped-container work at all. So a probe built on
 * `scrollTo` reports the page moving whether or not the lock is there --
 * measured both ways on 2026-08-31 before this was written, and it read
 * FAIL against the fix as loudly as against the bug. `mouse.wheel` is a
 * real input event and is refused by the lock: 500px unlocked, 0px locked,
 * same page, same run.
 *
 * Both halves are asserted, because only the pair is evidence. With the
 * sheet shut the page **must** move, or the "it did not move" below is a
 * negative guaranteed in advance by a short page rather than by the fix.
 *
 * The viewport is the owner's own 360x697 from `notes.md`, which is also
 * the width that puts the dock in its full-screen `max-width: 30rem` form
 * -- the one he is describing. The context is deliberately *not* `PHONE`:
 * a wheel event is not dispatched into a touch-only context, and the thing
 * under test is the CSS, which does not know which input moved the page.
 */
async function wheelScroll(page, distance) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.mouse.move(180, 300);
  await page.mouse.wheel(0, distance);
  await page.waitForTimeout(300);
  return page.evaluate(() => Math.round(window.scrollY));
}

async function probeChatScrollLock(browser, path) {
  const ctx = await browser.newContext({ viewport: { width: 360, height: 697 } });
  const page = await ctx.newPage();
  const errors = watch(page);
  await page.goto(base + path, { waitUntil: 'networkidle', timeout: 30000 });

  // Closed: the page has to be scrollable, or nothing below means anything.
  const closed = await wheelScroll(page, 500);
  const height = await page.evaluate(() => Math.round(document.documentElement.scrollHeight));

  await page.locator('#chat-btn').click();
  await page.waitForTimeout(400);
  const open = await wheelScroll(page, 500);
  const marks = await page.evaluate(() => ({
    marked: document.body.classList.contains('chat-open'),
    dockOpen: !document.getElementById('chat-dock').hasAttribute('hidden'),
  }));
  await ctx.close();

  const detail = {
    pageScrollsWhenClosed: closed > 0,
    scrolledWhenClosed: closed,
    documentHeight: height,
    chatOpen: marks.dockOpen,
    bodyMarked: marks.marked,
    scrolledWhenOpen: open,
  };
  const ok = detail.pageScrollsWhenClosed && detail.chatOpen && detail.scrolledWhenOpen === 0;
  return { probe: 'chat-scroll-lock' + path, ok, detail, errors };
}

const PROBES = {
  'nav-reachable': (b) => probeNavReachable(b, '/'),
  'chat-scroll-lock': (b) => probeChatScrollLock(b, '/'),
  'search-focus': (b) => probeSearchFocus(b, '/issues'),
  'search-focus-ideas': (b) => probeSearchFocus(b, '/ideas'),
  'replay-header': (b) => probeReplayHeader(b),
  'offline-banner': (b) => probeOfflineBanner(b, '/'),
  'offline-banner-issues': (b) => probeOfflineBanner(b, '/issues'),
};

(async () => {
  const wanted = process.argv.slice(2);
  const names = wanted.length ? wanted : Object.keys(PROBES);
  const browser = await chromium.launch({
    headless: true,
    args: (process.env.NOVA_CHROME_ARGS || '').split(' ').filter(Boolean),
  });
  for (const name of names) {
    const run = PROBES[name];
    if (!run) {
      console.log(JSON.stringify({ probe: name, ok: false, detail: { unknown: true }, errors: [] }));
      continue;
    }
    try {
      const row = await run(browser);
      // Report under the name that was *asked for*, not one the probe
      // builds from its own path. `poke_page.py` matches the two to spot
      // a probe that never reported, and a probe that renames itself is
      // indistinguishable from one that vanished.
      console.log(JSON.stringify({ ...row, probe: name }));
    } catch (e) {
      // A probe that throws is a failed probe, not a missing one. The
      // silent-gap failure is the one this loop keeps paying for.
      console.log(
        JSON.stringify({ probe: name, ok: false, detail: { threw: String(e.message).slice(0, 300) }, errors: [] })
      );
    }
  }
  await browser.close();
  // A probe that threw may not have reached its own `cut()`.
  for (const fwd of OPEN_FORWARDERS) await fwd.cut().catch(() => {});
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
