/**
 * E2E test for pin (置顶) feature — full coverage
 *
 * Covers: drawer toggle, API persistence, 置顶 section visibility,
 * multi-pin ordering, watching/holdings/starred page coverage
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}: ${d || ""}`);

let pass = 0, fail = 0;
function record(t, passed, detail = "") {
  if (passed) { ok(t); pass++; }
  else { ng(t, detail); fail++; }
}

async function getConfig(page) {
  return page.evaluate(async () => {
    const r = await fetch("/api/config");
    return r.json();
  });
}

async function apiPin(page, code, value) {
  return page.evaluate(async ({ code, value }) => {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "pin", code, value }),
    });
    return await r.json();
  }, { code, value });
}

async function openDrawerForCode(page, code) {
  return page.evaluate(async (code) => {
    const spans = Array.from(document.querySelectorAll('span'));
    const codeSpan = spans.find(s => s.textContent.trim().startsWith(code));
    if (!codeSpan) return { ok: false, detail: `code ${code} not found in DOM` };
    const row = codeSpan.closest('div[style*="border-bottom"]') || codeSpan.parentElement?.parentElement;
    if (!row) return { ok: false, detail: "row not found" };
    row.click();
    await new Promise(r => setTimeout(r, 800));
    const overlays = Array.from(document.querySelectorAll('div')).filter(d =>
      d.style.position === 'fixed' && d.style.zIndex === '201'
    );
    return { ok: overlays.length > 0, detail: `overlays: ${overlays.length}` };
  }, code);
}

const browser = await chromium.launch({ headless: true });
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });

let testCode1 = null;
let testCode2 = null;

try {
  // ═══ 0. Setup ═══
  console.log("\n═══ 0. Setup ═══");
  await p.goto(`${BASE}/watching?tab=A`, { waitUntil: "commit", timeout: 30000 });
  await p.waitForTimeout(3000);

  const config = await getConfig(p);
  const watching = config.watching || {};
  const codes = Object.keys(watching).filter(c => !c.startsWith("HK") && !c.startsWith("KR"));
  testCode1 = codes[0] || null;
  testCode2 = codes[1] || null;

  record("Found test stock 1", !!testCode1, testCode1 || "none");
  record("Found test stock 2", !!testCode2, testCode2 || "none");

  if (testCode1) await apiPin(p, testCode1, false);
  if (testCode2) await apiPin(p, testCode2, false);

  // ═══ 1. Drawer: pin toggle ═══
  console.log("\n═══ 1. Drawer pin toggle ═══");
  if (testCode1) {
    await p.goto(`${BASE}/watching?tab=A`, { waitUntil: "commit", timeout: 30000 });
    await p.waitForTimeout(3000);

    const drawerOpened = await openDrawerForCode(p, testCode1);
    record("Drawer opens", drawerOpened.ok, drawerOpened.detail || "");

    if (drawerOpened.ok) {
      const hasPinToggle = await p.evaluate(() => {
        return Array.from(document.querySelectorAll('span')).some(s => s.textContent?.includes("置顶") || s.textContent?.includes("已置顶"));
      });
      record("Pin toggle visible in drawer", hasPinToggle);

      const initialText = await p.evaluate(() => {
        const pin = Array.from(document.querySelectorAll('span')).find(s => s.textContent?.includes("置顶") || s.textContent?.includes("已置顶"));
        return pin ? pin.textContent.trim() : "";
      });
      record("Initial state is '置顶' (unpinned)", initialText === "置顶", `got: ${initialText}`);

      // Pin via drawer
      const pinClicked = await p.evaluate(async () => {
        const pin = Array.from(document.querySelectorAll('span')).find(s => s.textContent?.trim() === "置顶");
        if (!pin) return { ok: false, detail: "置顶 not found" };
        pin.click();
        await new Promise(r => setTimeout(r, 1500));
        return { ok: true };
      });
      record("Pin clicked", pinClicked.ok, pinClicked.detail || "");

      await p.waitForTimeout(500);
      const afterPin = await p.evaluate(() => {
        const pin = Array.from(document.querySelectorAll('span')).find(s => s.textContent?.includes("置顶") || s.textContent?.includes("已置顶"));
        return pin ? pin.textContent.trim() : "";
      });
      record("State → '已置顶'", afterPin === "已置顶", `got: ${afterPin}`);

      const cfg1 = await getConfig(p);
      const e1 = cfg1.watching?.[testCode1] || cfg1.holdings?.[testCode1];
      record("API: pin_order > 0", typeof e1?.pin_order === 'number' && e1.pin_order > 0, `pin_order=${e1?.pin_order}`);

      // Unpin via drawer
      const unpinClicked = await p.evaluate(async () => {
        const pin = Array.from(document.querySelectorAll('span')).find(s => s.textContent?.trim() === "已置顶");
        if (!pin) return { ok: false };
        pin.click();
        await new Promise(r => setTimeout(r, 1500));
        return { ok: true };
      });
      record("Unpin clicked", unpinClicked.ok);

      await p.waitForTimeout(500);
      const afterUnpin = await p.evaluate(() => {
        const pin = Array.from(document.querySelectorAll('span')).find(s => s.textContent?.includes("置顶") || s.textContent?.includes("已置顶"));
        return pin ? pin.textContent.trim() : "";
      });
      record("State → '置顶'", afterUnpin === "置顶", `got: ${afterUnpin}`);

      const cfg2 = await getConfig(p);
      const e2 = cfg2.watching?.[testCode1] || cfg2.holdings?.[testCode1];
      record("API: pin_order=0", e2?.pin_order === 0 || e2?.pin_order === undefined, `pin_order=${e2?.pin_order}`);

      await p.keyboard.press("Escape");
      await p.waitForTimeout(300);
    }
  }

  // ═══ 2. Watching page: 置顶 section ═══
  console.log("\n═══ 2. Watching page 置顶 section ═══");
  if (testCode1) {
    await apiPin(p, testCode1, true);

    await p.goto(`${BASE}/watching?tab=A`, { waitUntil: "commit", timeout: 30000 });
    await p.waitForTimeout(3000);

    const txt = await p.evaluate(() => document.body?.innerText || '');
    record("Watching: pinned section visible", txt.includes("pinned"));
    record("Watching: pinned stock in page", txt.includes(testCode1));
  }

  // ═══ 3. Holdings page ═══
  console.log("\n═══ 3. Holdings page ═══");
  if (testCode1) {
    await p.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 30000 });
    await p.waitForTimeout(3000);
    const txt = await p.evaluate(() => document.body?.innerText || '');
    record("Holdings page loads without error", !txt.includes("[ERROR]"));
  }

  // ═══ 4. Starred page: 置顶 section ═══
  console.log("\n═══ 4. Starred page 置顶 section ═══");
  if (testCode1) {
    await p.evaluate(async (code) => {
      await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "update", code, data: { star: true } }) });
    }, testCode1);

    await p.goto(`${BASE}/starred?tab=A`, { waitUntil: "commit", timeout: 30000 });
    await p.waitForTimeout(3000);

    const txt = await p.evaluate(() => document.body?.innerText || '');
    record("Starred: '置顶' section visible", txt.includes("置顶"));
    record("Starred: pinned stock in page", txt.includes(testCode1));

    const sectionOrder = await p.evaluate(() => {
      const t = document.body?.innerText || '';
      const names = ["置顶", "holdings", "watchlist:stocks", "watchlist:ETFs"];
      return names.filter(n => t.includes(n));
    });
    record("Starred: 置顶 is first section", sectionOrder[0] === "置顶", `order: ${sectionOrder.join(", ")}`);

    // Restore star
    await p.evaluate(async (code) => {
      await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "update", code, data: { star: false } }) });
    }, testCode1);
  }

  // ═══ 5. Multi-pin ordering ═══
  console.log("\n═══ 5. Multi-pin ordering ═══");
  if (testCode1 && testCode2) {
    await apiPin(p, testCode1, false);
    await apiPin(p, testCode2, false);

    await apiPin(p, testCode1, true);
    await apiPin(p, testCode2, true);

    const cfg = await getConfig(p);
    const o1 = cfg.watching?.[testCode1]?.pin_order ?? cfg.holdings?.[testCode1]?.pin_order;
    const o2 = cfg.watching?.[testCode2]?.pin_order ?? cfg.holdings?.[testCode2]?.pin_order;
    record("Both pin_order > 0", o1 > 0 && o2 > 0, `o1=${o1} o2=${o2}`);
    record("Second pinned has higher pin_order", o2 > o1, `o1=${o1} o2=${o2}`);

    // Star both and verify DOM order on starred page
    await p.evaluate(async ({ c1, c2 }) => {
      for (const c of [c1, c2]) {
        await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "update", code: c, data: { star: true } }) });
      }
    }, { c1: testCode1, c2: testCode2 });

    await p.goto(`${BASE}/starred?tab=A`, { waitUntil: "commit", timeout: 30000 });
    await p.waitForTimeout(3000);

    const domOrder = await p.evaluate(({ c1, c2 }) => {
      const spans = Array.from(document.querySelectorAll('span'));
      const p1 = spans.findIndex(s => s.textContent.trim().startsWith(c1));
      const p2 = spans.findIndex(s => s.textContent.trim().startsWith(c2));
      return { p1, p2 };
    }, { c1: testCode1, c2: testCode2 });
    record("Starred: higher pin_order appears first in DOM", domOrder.p2 < domOrder.p1, `pos1=${domOrder.p1} pos2=${domOrder.p2}`);

    // Restore
    await p.evaluate(async ({ c1, c2 }) => {
      for (const c of [c1, c2]) {
        await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "update", code: c, data: { star: false } }) });
        await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pin", code: c, value: false }) });
      }
    }, { c1: testCode1, c2: testCode2 });
  }

  // ═══ 6. API edge cases ═══
  console.log("\n═══ 6. API edge cases ═══");
  if (testCode1) {
    const badPin = await apiPin(p, "999999", true);
    record("Pin non-existent code → error", badPin.success === false);

    const noCode = await p.evaluate(async () => {
      const r = await fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "pin", value: true }) });
      return await r.json();
    });
    record("Pin without code → error", noCode.success === false);
  }

  // ═══ 7. Console errors ═══
  console.log("\n═══ 7. Console errors ═══");
  const consoleErrors = [];
  p.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  await p.goto(`${BASE}/watching?tab=A`, { waitUntil: "commit", timeout: 30000 });
  await p.waitForTimeout(3000);
  record("No JS console errors", consoleErrors.length === 0, consoleErrors.join("; "));

} catch (e) {
  console.error(`\n[ERROR] Test crashed: ${e.message}`);
  console.error(e.stack);
  fail++;
} finally {
  for (const c of [testCode1, testCode2].filter(Boolean)) {
    try { await apiPin(p, c, false); } catch {}
  }
  await browser.close();
}

console.log(`\n${"═".repeat(60)}`);
console.log(`PIN E2E: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) process.exit(1);
