import { chromium } from "playwright";
const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
await page.route("**/*.googleapis.com/**", r => r.abort());
await page.route("**/*.gstatic.com/**", r => r.abort());
await page.goto("http://localhost:3120/alerts", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(3000);

// Top nav + summary bar
await page.screenshot({ path: "screenshots/alerts_top.png", clip: { x: 0, y: 0, width: 1600, height: 120 } });

// First ~15 rows of alert events
await page.screenshot({ path: "screenshots/alerts_rows.png", clip: { x: 0, y: 80, width: 1600, height: 500 } });

// Middle section
await page.screenshot({ path: "screenshots/alerts_mid.png", clip: { x: 0, y: 500, width: 1600, height: 400 } });

console.log("done");
await browser.close();
