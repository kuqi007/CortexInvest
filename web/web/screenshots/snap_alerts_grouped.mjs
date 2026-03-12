import { chromium } from "playwright";
const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
await page.route("**/*.googleapis.com/**", r => r.abort());
await page.route("**/*.gstatic.com/**", r => r.abort());
await page.goto("http://localhost:3120/alerts", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);

// Full page grouped view
await page.screenshot({ path: "screenshots/alerts_grouped_full.png" });

// Top section zoom
await page.screenshot({ path: "screenshots/alerts_grouped_top.png", clip: { x: 0, y: 0, width: 1600, height: 500 } });

// Click first stock to expand
const firstRow = page.locator("span").filter({ hasText: /^\u25b8$/ }).first();
if (await firstRow.count() > 0) {
  await firstRow.click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: "screenshots/alerts_grouped_expanded.png", clip: { x: 0, y: 30, width: 1600, height: 500 } });
}

console.log("done");
await browser.close();
