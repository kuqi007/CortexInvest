/**
 * MarketSummaryBar E2E Test
 * Verifies A-share and HK tab MarketSummaryBar rendering
 *
 * Run: node web/screenshots/test_market_summary.mjs
 */

import { chromium } from "playwright";

const BASE = "http://127.0.0.1:3120";
const DIR = new URL(".", import.meta.url).pathname;

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`  [${ok ? "PASS" : "FAIL"}] ${name}${detail ? " -- " + detail : ""}`);
}

async function main() {
  const browser = await chromium.launch({ headless: true, args: ["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"] });
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push("PAGE_ERROR: " + err.message));

  try {
    // ================================================================
    // 1. A-share tab: MarketSummaryBar with SH/SZ/创业板/科创50/成交/AMO
    // ================================================================
    console.log("\n=== 1. A-share tab MarketSummaryBar ===");

    await page.goto(`${BASE}/?tab=A`, { waitUntil: "commit", timeout: 60000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
    await page.waitForTimeout(8000);
    await page.screenshot({ path: `${DIR}/market_summary_a.png`, fullPage: true });

    // 1a. MarketSummaryBar rendered
    const barText = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      for (const d of divs) {
        if (d.textContent.includes("SH ") && d.textContent.includes("SZ ")) return d.textContent;
      }
      return "";
    });
    record("1a: MarketSummaryBar visible (SH/SZ found)", barText.includes("SH ") && barText.includes("SZ "));

    // 1b. 创业板 present
    record("1b: 创业板 visible", barText.includes("创业板"));

    // 1c. 科创50 present
    record("1c: 科创50 visible", barText.includes("科创50"));

    // 1d. 成交 present
    record("1d: 成交 visible", barText.includes("成交"));

    // 1e. AMO present
    record("1e: AMO visible", barText.includes("AMO"));

    // 1f. Old vol: row gone (should not appear in the summary bar text)
    // Note: vol might still appear elsewhere, just not in the MarketSummaryBar
    record("1f: No duplicate SH/SZ in old inline format", !barText.includes("vol:"));

    // ================================================================
    // 2. HK tab: MarketSummaryBar with 恒生/恒生科技/成交(参考)
    // ================================================================
    console.log("\n=== 2. HK tab MarketSummaryBar ===");

    await page.goto(`${BASE}/?tab=HK`, { waitUntil: "commit", timeout: 60000 });
    await page.waitForLoadState("domcontentloaded", { timeout: 120000 });
    await page.waitForTimeout(8000);
    await page.screenshot({ path: `${DIR}/market_summary_hk.png`, fullPage: true });

    const hkText = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll("div"));
      for (const d of divs) {
        if (d.textContent.includes("恒生 ") && d.textContent.includes("恒生科技")) return d.textContent;
      }
      return "";
    });
    record("2a: MarketSummaryBar visible (恒生 found)", hkText.includes("恒生 "));

    // 2b. 恒生科技 present
    record("2b: 恒生科技 visible", hkText.includes("恒生科技"));

    // 2c. 成交(参考) present
    record("2c: 成交 visible with 参考 label", hkText.includes("成交") && hkText.includes("参考"));

    // 2d. AMO NOT present in HK bar (HK has no AMO)
    record("2d: AMO NOT in HK bar", !hkText.includes("AMO"));

    // 2e. FX row gone
    const hasFX = await page.evaluate(() =>
      Array.from(document.querySelectorAll("div")).some((d) => d.textContent.includes("FX:") && d.textContent.includes("HKD/CNY"))
    );
    record("2e: Old FX row removed", !hasFX);

    // ================================================================
    // 3. Summary
    // ================================================================
    console.log("\n=== 3. Summary ===");
    const passed = results.filter((r) => r.ok).length;
    const failed = results.filter((r) => !r.ok).length;
    console.log(`\n  Results: ${passed} passed, ${failed} failed`);

    if (consoleErrors.length > 0) {
      console.log(`\n  Console errors (${consoleErrors.length}):`);
      consoleErrors.slice(0, 5).forEach((e) => console.log(`    ${e}`));
    }

    await browser.close();
    process.exit(failed > 0 ? 1 : 0);
  } catch (e) {
    console.error("Test error:", e.message);
    await browser.close();
    process.exit(1);
  }
}

main();
