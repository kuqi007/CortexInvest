import { chromium } from "playwright";
const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
await page.route("**/*.googleapis.com/**", r => r.abort());
await page.route("**/*.gstatic.com/**", r => r.abort());
await page.goto("http://localhost:3120/alerts", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);

await page.screenshot({ path: "screenshots/alerts_grouped_full.png" });
await page.screenshot({ path: "screenshots/alerts_top.png", clip: { x: 0, y: 0, width: 1600, height: 450 } });
await page.screenshot({ path: "screenshots/alerts_bottom.png", clip: { x: 0, y: 400, width: 1600, height: 500 } });

// Expand a row with multiple signals
const rows = page.locator("div[style*='cursor: pointer']");
const count = await rows.count();
if (count > 4) {
  await rows.nth(4).click();
  await page.waitForTimeout(300);
}
await page.screenshot({ path: "screenshots/alerts_expanded.png", clip: { x: 0, y: 60, width: 1600, height: 500 } });

console.log("done");
await browser.close();
