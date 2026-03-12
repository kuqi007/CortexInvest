/**
 * E2E test: alias field, [d] dip badge, star optimistic toggle
 * Uses HK02722 (重庆机电) as test subject
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR  = new URL(".", import.meta.url).pathname;

let pass = 0, fail = 0;
const ok = (t) => { console.log(`  [PASS] ${t}`); pass++; };
const ng = (t, d) => { console.log(`  [FAIL] ${t}${d ? ": " + d : ""}`); fail++; };

const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
page.on("console", (m) => { if (m.type() === "error") console.log("  [JS ERR]", m.text().slice(0, 100)); });
await page.route("**/*.googleapis.com/**", (r) => r.abort());
await page.route("**/*.gstatic.com/**",   (r) => r.abort());

async function shot(name) {
  try { await page.screenshot({ path: `${DIR}/${name}`, timeout: 8000 }); }
  catch { console.log(`  [WARN] screenshot ${name} skipped`); }
}

// Track /api/config POST responses
const apiLog = [];
page.on("response", async (resp) => {
  if (resp.url().includes("/api/config") && resp.request().method() === "POST") {
    try { apiLog.push({ ok: resp.ok(), ...(await resp.json()) }); } catch { /* ignore */ }
  }
});

// ─── helpers ───────────────────────────────────────────────
async function goto(path) {
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(1200);
}

async function openDrawer() {
  const row = page.locator("div[style*='white-space: pre']").filter({ hasText: "HK02722" }).first();
  await row.click();
  await page.waitForTimeout(600);
}

async function closeDrawer() {
  await page.keyboard.press("Escape");
  await page.waitForTimeout(300);
}

// EditableCell: span[title='Click to edit'] whose parentElement text contains label
async function editCell(label, value) {
  const cells = page.locator("span[title='Click to edit']");
  const count = await cells.count();
  for (let i = 0; i < count; i++) {
    const ctx = await cells.nth(i).evaluate(el => el.parentElement?.textContent || "");
    if (ctx.includes(label)) {
      await cells.nth(i).click();
      const inp = page.locator("input:focus");
      await inp.fill(value);
      await page.keyboard.press("Enter");
      await page.waitForTimeout(900);
      return true;
    }
  }
  return false;
}

// ─── 1. Initial state ──────────────────────────────────────
console.log("\n═══ 1. Load Holdings HK tab ═══");
await goto("/?tab=HK");
await shot("alias_01_initial.png");

const rowText = await page.evaluate(() => {
  const divs = Array.from(document.querySelectorAll("div[style*='white-space: pre']"));
  return divs.find(d => d.textContent?.includes("HK02722"))?.textContent?.trim().slice(0, 60) || "";
});
rowText.includes("HK02722") ? ok(`HK02722 row found`) : ng("HK02722 row not found");

// ─── 2. Set alias ──────────────────────────────────────────
console.log("\n═══ 2. Alias ═══");
await openDrawer();
await shot("alias_02_drawer_open.png");

// Drawer header
const headerText = await page.evaluate(() =>
  document.querySelector("div[style*='position: sticky'] span")?.textContent?.trim() || ""
);
headerText.includes("HK02722") ? ok(`Drawer header: ${headerText.slice(0, 30)}`) : ng("Drawer header wrong", headerText);

// Set alias
const edited = await editCell("别名", "2X重庆机电");
edited ? ok("别名 cell edited") : ng("别名 cell not found");

const lastSave = apiLog[apiLog.length - 1];
lastSave?.ok ? ok(`API save OK: ${lastSave.message}`) : ng("API save failed", JSON.stringify(lastSave));

// Reload and verify row shows alias
await closeDrawer();
await goto("/?tab=HK");
await shot("alias_03_after_alias.png");

const rowAfter = await page.evaluate(() => {
  const divs = Array.from(document.querySelectorAll("div[style*='white-space: pre']"));
  return divs.find(d => d.textContent?.includes("HK02722"))?.textContent?.trim().slice(0, 80) || "";
});
// alias "2X重庆机电" sliced to 6 chars → "2X重庆机电" (all 6)
const aliasInRow = rowAfter.includes("2X重庆") || rowAfter.includes("2X重");
aliasInRow ? ok(`Alias shown in row: ${rowAfter.slice(0, 60)}`) : ng("Alias not in row", rowAfter.slice(0, 60));

// ─── 3. [d] dip badge ──────────────────────────────────────
console.log("\n═══ 3. Dip badge [d] ═══");
await openDrawer();

// Toggle dip_buy ON
const dipToggle = page.locator("span[style*='inline-flex']").filter({ hasText: "dip 回调监控" }).first();
const dipFound = await dipToggle.count() > 0;
dipFound ? ok("Dip toggle found in drawer") : ng("Dip toggle not found");

if (dipFound) {
  await dipToggle.click();
  await page.waitForTimeout(900);
  const dipSave = apiLog[apiLog.length - 1];
  dipSave?.ok ? ok(`Dip_buy toggled ON: ${dipSave.message}`) : ng("Dip_buy toggle failed", JSON.stringify(dipSave));
}

await closeDrawer();
await goto("/?tab=HK");
await shot("alias_04_dip_badge.png");

// Check [d] badge in indicator column
const hasDipBadge = await page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("div[style*='white-space: pre']"));
  const row = rows.find(r => r.textContent?.includes("HK02722"));
  if (!row) return false;
  // First span child is the indicator column (inline-flex)
  const indicators = row.querySelector("span[style*='inline-flex']");
  return indicators?.textContent?.includes("d") ?? false;
});
hasDipBadge ? ok("[d] badge visible in row indicator column") : ng("[d] badge not found in row");

// ─── 4. Star optimistic toggle ─────────────────────────────
console.log("\n═══ 4. Star optimistic toggle ═══");
await openDrawer();
await shot("alias_05_drawer_for_star.png");

// Get initial star state
const starText0 = await page.locator("button").filter({ hasText: /星标/ }).first().textContent();
const wasStarred = starText0?.includes("已星标") ?? false;
ok(`Initial star state: ${wasStarred ? "already starred" : "not starred"}`);

// Click star — verify optimistic update (no reload)
const starBtn = page.locator("button").filter({ hasText: /星标/ }).first();
await starBtn.click();
await page.waitForTimeout(300); // optimistic: immediate, no API wait

const starText1 = await page.locator("button").filter({ hasText: /星标/ }).first().textContent();
const toggled = wasStarred
  ? !starText1?.includes("已星标")
  : starText1?.includes("已星标");
toggled
  ? ok(`Optimistic toggle: "${starText0?.trim()}" → "${starText1?.trim()}"`)
  : ng("Star did not toggle optimistically", `before="${starText0?.trim()}" after="${starText1?.trim()}"`);

// Click again to restore
await starBtn.click();
await page.waitForTimeout(300);

const starText2 = await page.locator("button").filter({ hasText: /星标/ }).first().textContent();
const restored = wasStarred
  ? starText2?.includes("已星标")
  : !starText2?.includes("已星标");
restored ? ok(`Star restored to original: "${starText2?.trim()}"`) : ng("Star not restored", starText2?.trim());

// Wait for both API calls to complete
await page.waitForTimeout(600);
const recentApiOk = apiLog.slice(-3).filter(r => r.ok).length >= 1;
recentApiOk ? ok("Star API calls OK") : ng("Star API calls failed", JSON.stringify(apiLog.slice(-3)));

await shot("alias_06_star_toggle.png");

// ─── 5. Cleanup ────────────────────────────────────────────
console.log("\n═══ 5. Cleanup ═══");

// Clear alias
const clearedAlias = await editCell("别名", "");
clearedAlias ? ok("Alias cleared") : ng("Could not clear alias cell");

const clearSave = apiLog[apiLog.length - 1];
clearSave?.ok ? ok(`Alias clear API OK: ${clearSave.message}`) : ng("Alias clear API failed", JSON.stringify(clearSave));

// Turn off dip_buy
const dipToggle2 = page.locator("span[style*='inline-flex']").filter({ hasText: "dip 回调监控" }).first();
if (await dipToggle2.count() > 0) {
  await dipToggle2.click();
  await page.waitForTimeout(900);
  ok("Dip_buy turned off");
} else {
  ng("Dip toggle not found for cleanup");
}

await closeDrawer();
await goto("/?tab=HK");
await shot("alias_07_cleanup.png");

// Verify alias gone + [d] badge gone
const rowFinal = await page.evaluate(() => {
  const divs = Array.from(document.querySelectorAll("div[style*='white-space: pre']"));
  return divs.find(d => d.textContent?.includes("HK02722"))?.textContent?.trim().slice(0, 80) || "";
});
!rowFinal.includes("2X重") ? ok("Alias removed from row") : ng("Alias still showing", rowFinal.slice(0, 60));

const dipBadgeGone = await page.evaluate(() => {
  const rows = Array.from(document.querySelectorAll("div[style*='white-space: pre']"));
  const row = rows.find(r => r.textContent?.includes("HK02722"));
  const indicators = row?.querySelector("span[style*='inline-flex']");
  return !(indicators?.textContent?.includes("d") ?? false);
});
dipBadgeGone ? ok("[d] badge removed") : ng("[d] badge still visible");

// ─── Summary ───────────────────────────────────────────────
await browser.close();
console.log(`\n${"═".repeat(50)}`);
console.log(`DONE: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) process.exit(1);
