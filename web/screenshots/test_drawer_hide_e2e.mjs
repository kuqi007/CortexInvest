/**
 * E2E test: StockDrawer — Hide/Unhide button
 * Tests: open drawer on a holding stock, click hide, verify API call,
 *        click again to unhide, verify API call.
 *
 * Run: cd web && node screenshots/test_drawer_hide_e2e.mjs
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";

const results = [];
const ok = (t, detail = "") => {
  results.push({ name: t, pass: true });
  console.log(`  [PASS] ${t}${detail ? " -- " + detail : ""}`);
};
const ng = (t, detail = "") => {
  results.push({ name: t, pass: false });
  console.log(`  [FAIL] ${t}${detail ? " -- " + detail : ""}`);
};
const check = (cond, t, detail = "") => (cond ? ok(t, detail) : ng(t, detail));

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

  const configCalls = [];
  page.on("response", async (resp) => {
    if (resp.url().includes("/api/config") && resp.request().method() === "POST") {
      try {
        const body = await resp.json();
        const reqBody = JSON.parse(resp.request().postData() || '{}');
        configCalls.push({ reqBody, resBody: body });
      } catch { /* ignore */ }
    }
  });

  page.on("dialog", async (dialog) => {
    console.log(`    [dialog] ${dialog.type()}: "${dialog.message().slice(0, 80)}"`);
    await dialog.accept();
  });

  try {
    // ══════════════════════════════════════════════════
    // 1. Open Holdings page and click a holding row
    // ══════════════════════════════════════════════════
    console.log("\n--- 1. Open Holdings → Click Row ---");
    // Use HK tab
    await page.goto(`${BASE}/?tab=HK`, { waitUntil: "load", timeout: 30000 });
    await page.waitForTimeout(5000);

    // Verify data loaded — check for HK stock codes (span with HK0...)
    const hkCodeCount = await page.evaluate(() =>
      Array.from(document.querySelectorAll('span')).filter(s => /^HK\d+$/.test(s.textContent.trim())).length
    );
    check(hkCodeCount > 0, "HK holding rows rendered", `${hkCodeCount} rows`);

    // Find first holding row — div with borderBottom + whiteSpace:pre + HK code
    // Use evaluate+click since Playwright locator.click() can miss flex containers
    const clickedRow = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      for (const d of divs) {
        const s = d.style;
        if (s && s.borderBottom && s.borderBottom.includes('1px') && s.whiteSpace === 'pre') {
          const t = d.textContent || '';
          if (/HK\d+/.test(t)) {
            d.click();
            return true;
          }
        }
      }
      return false;
    });
    check(clickedRow, "Clicked a HK holding row");
    await page.waitForTimeout(1200);

    // ══════════════════════════════════════════════════
    // 2. Drawer open — verify it has content
    // ══════════════════════════════════════════════════
    console.log("\n--- 2. Drawer Open ---");
    const drawerText = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      // z=201 is the drawer, z=200 is the backdrop
      const drawer = divs.find(d => d.style && d.style.position === 'fixed' && d.style.zIndex === '201');
      return drawer ? drawer.textContent : "";
    });
    check(drawerText.length > 50, "StockDrawer opened with content", `len=${drawerText.length}`);
    check(drawerText.includes("基本信息"), "Drawer has 基本信息 section");

    // ══════════════════════════════════════════════════
    // 3. Find Hide/Unhide button and click it
    // ══════════════════════════════════════════════════
    console.log("\n--- 3. Click Hide/Unhide ---");
    configCalls.length = 0;

    // The hide button is a <button> with text 隐藏 or 取消隐藏
    const hideBtn = page.locator('button').filter({ hasText: "隐藏" });
    const hideCount = await hideBtn.count();
    check(hideCount > 0, "Hide/Unhide button found in drawer");

    // Determine current state
    const drawerBtns = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      return btns.map(b => b.textContent.trim());
    });
    const isCurrentlyHidden = drawerBtns.includes("取消隐藏");
    const btnLabel = isCurrentlyHidden ? "取消隐藏" : "隐藏";
    console.log(`    Current state: ${isCurrentlyHidden ? "已隐藏" : "未隐藏"} → will click "${btnLabel}"`);

    // Click the hide/unhide button
    await hideBtn.first().click();
    await page.waitForTimeout(1200);

    // Verify API call
    check(configCalls.length > 0, "API /api/config called after toggle");
    if (configCalls.length > 0) {
      const call = configCalls[0];
      const action = call.reqBody?.action;
      const data = call.reqBody?.data;
      check(action === "update", "API action=update", `got: ${action}`);
      check(data?.hidden !== undefined, "API data has hidden field", `got: ${JSON.stringify(data)}`);
    }

    // Verify button text changed
    const drawerBtnsAfter = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      return btns.map(b => b.textContent.trim());
    });
    const hasHideAfter = drawerBtnsAfter.includes("隐藏");
    const hasUnhideAfter = drawerBtnsAfter.includes("取消隐藏");
    const textChanged = isCurrentlyHidden ? hasHideAfter : hasUnhideAfter;
    check(textChanged, `Button toggled — was "${btnLabel}" → now "${hasHideAfter ? "隐藏" : "取消隐藏"}"`);

    // ══════════════════════════════════════════════════
    // 4. Click again to restore original state
    // ══════════════════════════════════════════════════
    console.log("\n--- 4. Click again to restore ---");
    configCalls.length = 0;

    // Click via evaluate to avoid locator timeout
    const secondBtnText = isCurrentlyHidden ? "隐藏" : "取消隐藏";
    await page.evaluate((text) => {
      const btns = Array.from(document.querySelectorAll('button'));
      const btn = btns.find(b => b.textContent.trim() === text);
      if (btn) btn.click();
    }, secondBtnText);
    await page.waitForTimeout(1200);

    check(configCalls.length > 0, "Second API call made");
    if (configCalls.length > 0) {
      const call = configCalls[0];
      check(call.reqBody?.data?.hidden !== undefined, "Second API has hidden field");
    }

    // ══════════════════════════════════════════════════
    // 5. Watching page — no hide button in drawer
    // ══════════════════════════════════════════════════
    console.log("\n--- 5. Watching page — no hide button ---");
    await page.goto(`${BASE}/watching?tab=HK`, { waitUntil: "load", timeout: 30000 });
    await page.waitForTimeout(5000);

    // Click a watching row (DEV badge)
    const clickedWatchRow = await page.evaluate(() => {
      const divs = Array.from(document.querySelectorAll('div'));
      for (const d of divs) {
        const s = d.style;
        if (s && s.borderBottom && s.borderBottom.includes('1px') && s.whiteSpace === 'pre') {
          const t = d.textContent || '';
          if (t.includes('DEV') && /HK\d+/.test(t)) {
            d.click();
            return true;
          }
        }
      }
      return false;
    });

    if (clickedWatchRow) {
      await page.waitForTimeout(800);
      const watchDrawerBtns = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        return btns.map(b => b.textContent.trim());
      });
      const noHideInWatch = !watchDrawerBtns.includes("隐藏") && !watchDrawerBtns.includes("取消隐藏");
      check(noHideInWatch, "Watching drawer: no hide/unhide button");
    } else {
      ok("No watching stock found (ok if empty)");
    }

    // ══════════════════════════════════════════════════
    // Summary
    // ══════════════════════════════════════════════════
    console.log(`\n════════════════════════════════════════════════════`);
    const passed = results.filter((r) => r.pass).length;
    const failed = results.filter((r) => !r.pass).length;
    console.log(`DRAWER HIDE E2E: ${passed} PASS | ${failed} FAIL`);
    if (failed > 0) {
      results.filter((r) => !r.pass).forEach((r) => console.log(`  FAIL: ${r.name}`));
    }
    console.log(`════════════════════════════════════════════════════`);

  } finally {
    await browser.close();
  }
}

main().catch((e) => {
  console.error("Test error:", e.message);
  process.exit(1);
});
