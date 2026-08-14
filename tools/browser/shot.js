const { chromium } = require('playwright-core');
const base = process.env.NOVA_SITE || 'http://nova-site:8083';
// Width comes from see_page.py, which defaults it to a phone. Height is
// generous rather than accurate: a screenshot is for looking at, and the
// only dimension that changes a layout is the one the flexbox wraps on.
const width = parseInt(process.env.NOVA_WIDTH || '390', 10);
const pages = process.argv.slice(2);
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  for (const p of pages) {
    const ctx = await browser.newContext({ viewport: { width, height: 1400 }, deviceScaleFactor: width < 700 ? 2 : 1 });
    const page = await ctx.newPage();
    const errs = [];
    page.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
    page.on('pageerror', e => errs.push('pageerror: ' + e.message));
    const resp = await page.goto(base + p, { waitUntil: 'networkidle', timeout: 30000 });
    const name = (p === '/' ? 'root' : p.replace(/\W+/g, '_').replace(/^_|_$/g, ''));
    await page.screenshot({ path: `shots/${name}-${width}.png`, fullPage: false });
    const text = (await page.locator('body').innerText()).trim();
    console.log(JSON.stringify({ path: p, width, status: resp.status(), textLen: text.length, head: text.slice(0, 160).replace(/\s+/g, ' '), consoleErrors: errs }));
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
