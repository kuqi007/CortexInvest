/**
 * E2E test: drawer delete button
 * 1. Pick first visible A-share watching stock
 * 2. Note its config, delete via drawer
 * 3. Verify removed from page + API
 * 4. Re-add it (cleanup)
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR  = new URL(".", import.meta.url).pathname;

let pass = 0, fail = 0;
const ok = (t) => { console.log(`  [PASS] ${t}`); pass++; };
const ng = (t, d) => { console.log(`  [FAIL] ${t}${d ? ": " + d : ""}`); fail++; };

const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
page.on("console", (m) => { if (m.type() === "error") console.log("  [JS ERR]", m.text().slice(0, 120)); });
await page.route("**/*.googleapis.com/**", (r) => r.abort());
await page.route("**/*.gstatic.com/**",   (r) => r.abort());

async function shot(name) {
  try { await page.screenshot({ path: `${DIR}/${name}`, timeout: 8000 }); }
  catch { console.log(`  [WARN] screenshot ${name} skipped`); }
}

// ─── Setup: pick a visible watching stock ─────────────────
console.log("\n═══ Setup: find watching stock ═══");
const metricsResp = await page.request.get(`${BASE}/api/metrics`);
const metricsData = await metricsResp.json();
const candidate = (metricsData.services || []).find(
  s => s.type !== "holding" && !String(s.id).startsWith("HK") && !s.hidden
);
if (!candidate) {
  console.log("  [SKIP] no A-share watching stock found — skip test");
  await browser.close();
  process.exit(0);
}
const testCode = candidate.id;
const testName = candidate.name;
console.log(`  Using: ${testCode} (${testName})`);
ok(`Found watching stock: ${testCode}`);

// Save original config for cleanup
const configResp = await page.request.get(`${BASE}/api/config`);
const configData = await configResp.json();
const origEntry = (configData.watchlist || {})[testCode] || {};
console.log(`  Original entry: ${JSON.stringify(origEntry)}`);

// ─── 1. Navigate to watching page ─────────────────────────
console.log("\n═══ 1. Watching page ═══");
await page.goto(`${BASE}/watching?tab=A`, { waitUntil: "domcontentloaded", timeout: 15000 });
await page.waitForTimeout(2000);
await shot("delete_01_watching.png");

const rowVisible = await page.evaluate((code) =>
  Array.from(document.querySelectorAll("span")).some(s => s.textContent?.trim() === code),
testCode);
rowVisible ? ok(`${testCode} visible on page`) : ng(`${testCode} not visible`);

// ─── 2. Open drawer ────────────────────────────────────────
console.log("\n═══ 2. Open drawer ═══");
const codeSpan = page.locator("span").filter({ hasText: new RegExp(`^${testCode}\\s*$`) }).first();
await codeSpan.click();
await page.waitForTimeout(800);
await shot("delete_02_drawer.png");

// Check header contains stock code
const headerText = await page.evaluate(() =>
  document.querySelector("div[style*='position: sticky'] span")?.textContent?.trim() || ""
);
headerText.includes(testCode) ? ok(`Drawer header: ${headerText.slice(0, 40)}`) : ng("Drawer header wrong", headerText);

// Check delete button
const deleteBtn = page.locator("button").filter({ hasText: "删除" }).first();
(await deleteBtn.count()) > 0 ? ok("Delete button present") : ng("Delete button not found");

// ─── 3. Click delete + confirm ─────────────────────────────
console.log("\n═══ 3. Delete ═══");
let dialogMsg = "";
page.once("dialog", async (dialog) => {
  dialogMsg = dialog.message();
  console.log(`  [DIALOG] ${dialogMsg.slice(0, 100)}`);
  await dialog.accept();
});

await deleteBtn.click();
await page.waitForTimeout(1500);
await shot("delete_03_after.png");

// Check dialog had stock info
(dialogMsg.includes(testCode) || dialogMsg.includes(testName) || dialogMsg.length > 5)
  ? ok(`Confirm shown: "${dialogMsg.slice(0, 60)}"`)
  : ng("No confirm dialog appeared");

// ─── 4. Verify removed ────────────────────────────────────
console.log("\n═══ 4. Verify removed ═══");

// Drawer should be closed
const drawerClosed = await page.evaluate(() =>
  !Array.from(document.querySelectorAll("div"))
    .some(d => d.style?.position === "fixed" && parseInt(d.style?.zIndex) > 0)
);
drawerClosed ? ok("Drawer closed after delete") : ng("Drawer still visible");

// Stock gone from page
const stockGone = await page.evaluate((code) =>
  !Array.from(document.querySelectorAll("span")).some(s => s.textContent?.trim() === code),
testCode);
stockGone ? ok(`${testCode} removed from page`) : ng(`${testCode} still showing`);

// Verify via API
const verifyResp = await page.request.get(`${BASE}/api/config`);
const verifyData = await verifyResp.json();
const stillIn = Object.keys(verifyData.watchlist || {}).includes(testCode);
!stillIn ? ok(`${testCode} removed from /api/config`) : ng(`${testCode} still in config`);

// ─── 5. Cleanup: restore the stock ────────────────────────
console.log("\n═══ 5. Cleanup: restore stock ═══");
const restoreData = { name: testName };
if (origEntry.type === "holding") {
  restoreData.type = "holding";
  if (origEntry.cost != null) restoreData.cost = origEntry.cost;
  if (origEntry.shares != null) restoreData.shares = origEntry.shares;
}
const restoreResp = await page.request.post(`${BASE}/api/config`, {
  data: { action: "add", code: testCode, data: restoreData },
});
const restoreJson = await restoreResp.json();
restoreJson.success ? ok(`Restored ${testCode}: ${restoreJson.message}`) : ng("Restore failed", restoreJson.message);

await browser.close();
console.log(`\n${"═".repeat(50)}`);
console.log(`DONE: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) process.exit(1);
