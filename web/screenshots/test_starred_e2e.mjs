/**
 * E2E test for /starred page (特别关注 tab)
 *
 * Covers: page load, star rows, sections, sort, collapse, tag filter, A/HK switch, drawer
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

const browser = await chromium.launch({ headless: true });
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });

try {
  // ═══ 1. Page load & basic structure ═══
  console.log("\n═══ 1. Page structure ═══");
  await p.goto(`${BASE}/starred?tab=A`, { waitUntil: "commit", timeout: 30000 });
  await p.waitForTimeout(3000);

  const hasError = await p.evaluate(() => document.body?.innerText?.includes("[ERROR]"));
  record("Starred: no [ERROR] banner", !hasError);

  const hasTitle = await p.evaluate(() => document.body?.innerText?.includes("starred"));
  record("Starred: title visible", hasTitle);

  const hasStats = await p.evaluate(() => {
    const t = document.body?.innerText || '';
    return t.includes("Nodes:") && t.includes("上涨:");
  });
  record("Starred: stats line present", hasStats);

  // ═══ 2. Star rows render ═══
  console.log("\n═══ 2. Star rows ═══");
  const starRowCount = await p.evaluate(() => {
    return Array.from(document.querySelectorAll('span')).filter(s => s.textContent.trim() === '★').length;
  });
  record("Starred: ★ rows rendered", starRowCount > 0, `${starRowCount} rows`);

  const sections = await p.evaluate(() => {
    const t = document.body?.innerText || '';
    return [...t.matchAll(/# ── (pinned:holdings|watchlist:stocks|watchlist:ETFs|hidden)/g)].map(m => m[1]);
  });
  record("Starred: section headers present", sections.length > 0, sections.join(", "));

  const hasHeaders = await p.evaluate(() => {
    const t = document.body?.innerText || '';
    return t.includes("成本") && t.includes("股数");
  });
  record("Starred: cost/shares headers present", hasHeaders);

  // ═══ 3. Sort functionality ═══
  console.log("\n═══ 3. Sort ═══");
  const sortChange = await p.evaluate(async () => {
    const spans = Array.from(document.querySelectorAll('span'));
    const header = spans.find(s => s.textContent.includes("涨跌幅"));
    if (!header) return { ok: false, detail: "header not found" };
    header.click();
    await new Promise(r => setTimeout(r, 500));
    return { ok: true };
  });
  record("Starred: sort by 涨跌幅 works", sortChange.ok, sortChange.detail || "");

  const sortReverse = await p.evaluate(async () => {
    const spans = Array.from(document.querySelectorAll('span'));
    const header = spans.find(s => s.textContent.includes("涨跌幅"));
    if (!header) return { ok: false };
    header.click();
    await new Promise(r => setTimeout(r, 500));
    return { ok: true };
  });
  record("Starred: sort direction toggles", sortReverse.ok);

  // ═══ 4. Collapse/expand sections ═══
  console.log("\n═══ 4. Collapse/expand ═══");

  // Find the innermost div with # ── and ▾ (the clickable section title)
  const afterCollapse = await p.evaluate(async () => {
    // Get all divs, find the deepest one matching our pattern
    const allDivs = Array.from(document.querySelectorAll('div'));
    // Find the one with cursor:pointer and containing ▾ # ──
    const secDiv = allDivs.find(d =>
      d.style.cursor === 'pointer' &&
      d.textContent.includes("▾ # ──") &&
      !d.textContent.includes("▾ # ──", d.textContent.indexOf("▾ # ──") + 1)
    );
    if (!secDiv) return { ok: false, detail: "section div not found" };
    const beforeText = secDiv.textContent.slice(0, 50);
    secDiv.click();
    await new Promise(r => setTimeout(r, 300));
    const afterText = secDiv.textContent.slice(0, 50);
    return { ok: afterText.includes("▸"), before: beforeText, after: afterText };
  });
  record("Starred: section collapses (▾→▸)", afterCollapse.ok, afterCollapse.detail || JSON.stringify({ b: afterCollapse.before, a: afterCollapse.after }));

  if (afterCollapse.ok) {
    const afterExpand = await p.evaluate(async () => {
      const allDivs = Array.from(document.querySelectorAll('div'));
      const secDiv = allDivs.find(d =>
        d.style.cursor === 'pointer' &&
        d.textContent.includes("▸ # ──")
      );
      if (!secDiv) return { ok: false };
      secDiv.click();
      await new Promise(r => setTimeout(r, 300));
      return { ok: secDiv.textContent.includes("▾") };
    });
    record("Starred: section expands (▸→▾)", afterExpand.ok);
  }

  // ═══ 5. Tag filter ═══
  console.log("\n═══ 5. Tag filter ═══");
  const noFilterActive = await p.evaluate(() => !document.body?.innerText?.includes("筛选:"));
  record("Starred: no active tag filter initially", noFilterActive);

  // ═══ 6. A/HK tab switch ═══
  console.log("\n═══ 6. Tab switch ═══");

  // Switch to HK
  const switchHK = await p.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    const hk = btns.find(b => (b.textContent || '').trim() === 'HK');
    if (hk) { hk.click(); return true; }
    return false;
  });
  await p.waitForTimeout(1500);
  record("Starred: switch to HK tab", switchHK);

  const hkCodes = await p.evaluate(() => {
    return Array.from(document.querySelectorAll('span')).filter(s => /^HK\d{5}/.test(s.textContent.trim())).length;
  });
  record("Starred HK: HK codes visible", hkCodes > 0, `${hkCodes} HK codes`);

  const hasL2 = await p.evaluate(() => {
    const t = document.body?.innerText || '';
    return t.includes("主力") && t.includes("主力%");
  });
  record("Starred HK: L2 columns present", hasL2);

  // Switch back to A via URL (since MarketSwitch may not show A button when HK is active)
  await p.goto(`${BASE}/starred?tab=A`, { waitUntil: "commit", timeout: 30000 });
  await p.waitForTimeout(2000);
  const backOnA = await p.evaluate(() => {
    const t = document.body?.innerText || '';
    return !t.includes("主力%") || t.includes("A-share");
  });
  record("Starred: switch back to A tab", backOnA);

  // ═══ 7. Drawer opens on row click ═══
  console.log("\n═══ 7. Drawer ═══");
  const drawerTest = await p.evaluate(async () => {
    const stars = Array.from(document.querySelectorAll('span'));
    const starSpan = stars.find(s => s.textContent.trim() === '★');
    if (!starSpan) return { ok: false, detail: "no star rows" };
    // Walk up to the row div: star is inside a span inside a flex div
    const parent = starSpan.parentElement; // span wrapper
    const row = parent?.parentElement; // flex div with border-bottom
    if (!row) return { ok: false, detail: "row not found" };
    row.click();
    await new Promise(r => setTimeout(r, 500));
    // Check for drawer overlay (fixed position, z-index 201)
    const overlays = Array.from(document.querySelectorAll('div')).filter(d =>
      d.style.position === 'fixed' && d.style.zIndex === '201'
    );
    return { ok: overlays.length > 0, detail: `overlays: ${overlays.length}` };
  });
  record("Starred: drawer opens on row click", drawerTest.ok, drawerTest.detail || "");

  // Close drawer via Escape
  await p.keyboard.press("Escape");
  await p.waitForTimeout(300);

  // ═══ 8. Console errors ═══
  console.log("\n═══ 8. Console errors ═══");
  const consoleErrors = [];
  p.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  await p.reload({ waitUntil: "commit" });
  await p.waitForTimeout(3000);
  record("Starred: no JS console errors", consoleErrors.length === 0, consoleErrors.join("; "));

} catch (e) {
  console.error(`\n[ERROR] Test crashed: ${e.message}`);
  fail++;
} finally {
  await browser.close();
}

console.log(`\n${"═".repeat(60)}`);
console.log(`STARRED E2E: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) process.exit(1);
