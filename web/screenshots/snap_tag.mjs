import { chromium } from "playwright";
const DIR = new URL(".", import.meta.url).pathname;
const b = await chromium.launch({ headless: true });
const p = await (await b.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
await p.route("**/*.googleapis.com/**", r => r.abort());
await p.route("**/*.gstatic.com/**", r => r.abort());
await p.goto("http://localhost:3120/manage", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(2500);
const areas = p.locator('span[title="Click to edit tags"]');
console.log("tag areas:", await areas.count());
await areas.first().click();
await p.waitForTimeout(600);
await p.screenshot({ path: `${DIR}/tag_ux_open.png` });
// also screenshot the drawer tag section
await p.goto("http://localhost:3120", { waitUntil: "domcontentloaded", timeout: 20000 });
await p.waitForTimeout(2000);
// click a stock row to open drawer
const codeSpan = p.locator("span").filter({ hasText: /^\d{6}\s*$/ }).first();
await codeSpan.click();
await p.waitForTimeout(800);
await p.screenshot({ path: `${DIR}/tag_ux_drawer.png` });
await b.close();
console.log("done");
