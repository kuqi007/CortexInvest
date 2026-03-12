import { chromium } from "playwright";
const browser = await chromium.launch({ headless: true });
const page = await (await browser.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
await page.route("**/*.googleapis.com/**", r => r.abort());
await page.route("**/*.gstatic.com/**", r => r.abort());
await page.goto("http://localhost:3120/manage", { waitUntil: "domcontentloaded", timeout: 30000 });
await page.waitForTimeout(2000);

const tagArea = page.locator("span[title='Click to edit tags']").first();
await tagArea.click();
await page.waitForTimeout(400);

const editor = page.locator("input[placeholder='search or create tag...']");
const box = await editor.boundingBox();
if (box) {
  await page.screenshot({
    path: "screenshots/tag_zoom.png",
    clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 60), width: 340, height: 320 }
  });
  console.log("done");
} else {
  await page.screenshot({ path: "screenshots/tag_zoom.png" });
  console.log("full screenshot");
}
await browser.close();
