import { chromium } from 'playwright';
const BASE = 'http://localhost:3120';
const DIR = new URL('.', import.meta.url).pathname;

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' -- ' + detail : ''}`);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();

  try {
    // ── 1. Navigate to /alerts ──
    // NOTE: use 'commit' (not 'networkidle') — alerts page polls every 30s, networkidle never settles
    console.log('\n=== Alerts Page Tests ===\n');
    await page.goto(`${BASE}/alerts`, { waitUntil: 'commit', timeout: 30000 });
    await page.waitForTimeout(3000);

    // Screenshot may fail in isolated runs due to JetBrains Mono font loading from Google Fonts
    // Run test_full_checkup.mjs for reliable screenshots
    try {
      await page.screenshot({ path: `${DIR}/alerts_viewport.png`, timeout: 15000 });
      console.log('  Screenshot: alerts_viewport.png');
    } catch (e) {
      console.log(`  Screenshot: skipped (font timeout - run test_full_checkup.mjs for screenshots)`);
    }

    // ── 2. Check no ERROR banner ──
    const errorBanner = await page.evaluate(() => {
      const els = Array.from(document.querySelectorAll('div'));
      return els.some(el => el.textContent.includes('[ERROR]'));
    });
    record('No [ERROR] banner', !errorBanner);

    // ── 3. Nav links present ──
    const navLinks = await page.evaluate(() => {
      const anchors = Array.from(document.querySelectorAll('a'));
      return anchors.map(a => ({ href: a.getAttribute('href'), text: a.textContent.trim() }));
    });
    const hasMonitorLink = navLinks.some(l => l.href === '/');
    const hasSimLink = navLinks.some(l => l.href === '/sim');
    const hasManageLink = navLinks.some(l => l.href === '/manage');
    record('Nav links present', hasMonitorLink && hasSimLink && hasManageLink,
      `monitor:${hasMonitorLink} sim:${hasSimLink} manage:${hasManageLink}`);

    // ── 4. API returns events ──
    const apiCheck = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const events = data.alertEvents || [];
      return {
        totalEvents: events.length,
        hasEvents: events.length > 0,
        servicesCount: (data.services || []).length,
      };
    });
    record('API returns events', apiCheck.hasEvents, `${apiCheck.totalEvents} events, ${apiCheck.servicesCount} services`);

    // ── 5. Events have correct time format ──
    const timeFormat = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const events = data.alertEvents || [];
      if (events.length === 0) return { ok: true, detail: 'no events' };
      const badTime = events.filter(e => !/^\d{2}:\d{2}:\d{2}$/.test(e.time || ''));
      return { ok: badTime.length === 0, detail: `${events.length} events, ${badTime.length} bad time format` };
    });
    record('Event time format correct', timeFormat.ok, timeFormat.detail);

    // ── 6. Events have required fields ──
    const eventFields = await page.evaluate(async () => {
      const res = await fetch('/api/metrics');
      const data = await res.json();
      const events = data.alertEvents || [];
      if (events.length === 0) return { ok: true, detail: 'no events' };
      const missing = events.filter(e => !e.time || !e.level || !e.kind);
      return { ok: missing.length === 0, detail: `${missing.length} events missing time/level/kind` };
    });
    record('Events have required fields', eventFields.ok, eventFields.detail);

    // ── 7. Scroll area present ──
    const scrollable = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      return divs.some(d => (d.style.overflow === 'auto' || d.style.overflowY === 'auto'));
    });
    record('Scroll area present', scrollable);

    // ── 8. /alerts route loads ──
    const routeCheck = await page.evaluate(async () => {
      const res = await fetch('/alerts');
      return { ok: res.status === 200, status: res.status };
    });
    record('/alerts route loads', routeCheck.ok, `status ${routeCheck.status}`);

  } finally {
    await browser.close();
  }

  // Summary
  const fail = results.filter(r => !r.ok).length;
  console.log(`\nTotal: ${results.length} | Pass: ${results.length - fail} | Fail: ${fail}`);
  if (fail) process.exit(1);
}

main().catch(e => { console.error(e); process.exit(2); });
