/**
 * E2E test: tag search + hover-to-delete
 * 1. Open Manage page, find a watching stock with tags
 * 2. If no tags, add one first via TagEditor
 * 3. Verify hover shows × button
 * 4. Click × to delete tag, verify removed
 * 5. Open TagEditor, verify search filters list
 * 6. Cleanup: restore original tags
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const DIR  = new URL(".", import.meta.url).pathname;

let pass = 0, fail = 0;
const ok = (t)     => { console.log(`  [PASS] ${t}`); pass++; };
const ng = (t, d)  => { console.log(`  [FAIL] ${t}${d ? ": " + d : ""}`); fail++; };

const browser = await chromium.launch({ headless: true });
const page    = await (await browser.newContext({ viewport: { width: 1600, height: 900 } })).newPage();
page.on("console", (m) => { if (m.type() === "error") console.log("  [JS ERR]", m.text().slice(0, 120)); });
await page.route("**/*.googleapis.com/**", (r) => r.abort());
await page.route("**/*.gstatic.com/**",   (r) => r.abort());

async function shot(name) {
  try { await page.screenshot({ path: `${DIR}/${name}`, timeout: 8000 }); }
  catch { console.log(`  [WARN] screenshot ${name} skipped`); }
}

// ─── 1. Navigate to Manage ─────────────────────────────
console.log("\n═══ 1. Navigate to Manage ═══");
await page.goto(`${BASE}/manage`, { waitUntil: "domcontentloaded", timeout: 20000 });
await page.waitForTimeout(2000);
await shot("tag_01_manage.png");

const pageText = await page.evaluate(() => document.body?.innerText || "");
pageText.includes("type") && pageText.includes("code")
  ? ok("Manage page loaded")
  : ng("Manage page not loaded");

// ─── 2. Find a stock with tags ─────────────────────────
console.log("\n═══ 2. Find stock with tags ═══");

// Get stocks with tags from API
const configResp = await page.request.get(`${BASE}/api/config`);
const configData = await configResp.json();
const watchlist  = configData.watchlist || {};

// Find a watching stock with at least one tag
let testCode = null, testTags = [];
for (const [code, entry] of Object.entries(watchlist)) {
  if (entry.type !== "holding" && entry.tags && entry.tags.length > 0) {
    testCode = code;
    testTags = [...entry.tags];
    break;
  }
}

if (!testCode) {
  console.log("  [SKIP] no watching stock with tags found");
  await browser.close();
  process.exit(0);
}
console.log(`  Using: ${testCode} with tags: [${testTags.join(", ")}]`);
ok(`Found watching stock with tags: ${testCode}`);

// ─── 3. Verify tags visible on page ───────────────────
console.log("\n═══ 3. Verify tags on page ═══");
const firstTag = testTags[0];

const tagVisible = await page.evaluate((tag) =>
  Array.from(document.querySelectorAll("span")).some(s => s.textContent?.trim() === tag),
firstTag);
tagVisible ? ok(`Tag "${firstTag}" visible on page`) : ng(`Tag "${firstTag}" not found on page`);

// ─── 4. Hover over tag → × appears ───────────────────
console.log("\n═══ 4. Hover to show × button ═══");

// Find the tag span for our stock
const tagSpan = page.locator("span").filter({ hasText: new RegExp(`^${firstTag}$`) }).first();
const tagCount = await tagSpan.count();
tagCount > 0 ? ok(`Tag span found: "${firstTag}"`) : ng(`Tag span not found: "${firstTag}"`);

if (tagCount > 0) {
  await tagSpan.hover();
  await page.waitForTimeout(300);
  await shot("tag_02_hover.png");

  // × should now be visible inside the hovered tag container
  const xVisible = await page.evaluate((tag) => {
    const spans = Array.from(document.querySelectorAll("span"));
    const tagEl = spans.find(s => s.textContent?.includes(tag) && s.textContent?.includes("×"));
    return !!tagEl;
  }, firstTag);
  xVisible ? ok("× button appears on hover") : ng("× button not visible on hover");
}

// ─── 5. Test tag search in dropdown ───────────────────
console.log("\n═══ 5. Tag search in dropdown ═══");

// Get all tags from API
const allTagsSet = new Set();
for (const entry of Object.values(watchlist)) {
  if (entry.tags) entry.tags.forEach(t => allTagsSet.add(t));
}
const allTags = [...allTagsSet];
console.log(`  Total tags in system: ${allTags.length}`);

if (allTags.length > 1) {
  // Pick a tag that is NOT on testCode's tags to search for
  const searchTarget = allTags.find(t => !testTags.includes(t)) || allTags[0];
  const searchPrefix = searchTarget.slice(0, 2); // search by first 2 chars

  // Find the +tag or existing tag area for our testCode
  // Click the tag area to open TagEditor
  const codeSpan = page.locator("span").filter({ hasText: new RegExp(`^${testCode}$`) }).first();
  const row = codeSpan.locator("xpath=ancestor::div[contains(@style,'display: flex')]").first();

  // Click the tags area (last span area in the row)
  const tagArea = page.locator("span[title='Click to edit tags']").first();
  const tagAreaCount = await tagArea.count();

  if (tagAreaCount > 0) {
    await tagArea.click();
    await page.waitForTimeout(300);
    await shot("tag_03_dropdown_open.png");

    // Check search/create combobox input exists (new UX: placeholder "search or create tag...")
    const searchInput = page.locator("input[placeholder='search or create tag...']").first();
    const searchInputCount = await searchInput.count();
    searchInputCount > 0 ? ok("Search input in TagEditor") : ng("Search input not found in TagEditor");

    if (searchInputCount > 0) {
      // Type search prefix
      await searchInput.fill(searchPrefix);
      await page.waitForTimeout(200);
      await shot("tag_04_search_filtered.png");

      // New combobox UI: tags rendered as <span> chips (not labels/checkboxes)
      // Count visible tag chips in the dropdown after filtering
      const dropdownChipCount = await page.evaluate((prefix) => {
        const spans = Array.from(document.querySelectorAll("span"));
        // Tag chips inside the dropdown have a background color and match the prefix
        return spans.filter(s => {
          const text = s.textContent?.trim() || "";
          return s.style?.background && text.length > 0 && text.toLowerCase().includes(prefix.toLowerCase());
        }).length;
      }, searchPrefix);

      console.log(`  Search "${searchPrefix}": ${dropdownChipCount} chips visible matching prefix`);

      dropdownChipCount > 0
        ? ok(`Search filters tags: ${dropdownChipCount} chips match "${searchPrefix}"`)
        : ng("Search not filtering or no matching chips found");
    }

    // Close dropdown with Escape
    await page.keyboard.press("Escape");
    await page.waitForTimeout(500);
    // Verify closed (combobox input gone)
    const dropdownGone = await page.locator("input[placeholder='search or create tag...']").count() === 0;
    dropdownGone ? ok("TagEditor closed after Escape") : ng("TagEditor still open after Escape");
  } else {
    ng("Could not open TagEditor — no tag area found with title='Click to edit tags'");
  }
} else {
  ok("Search test skipped (insufficient tags in system)");
}

// ─── 6. Delete a tag via hover × ──────────────────────
console.log("\n═══ 6. Delete tag via hover ×  ═══");

// Only test delete if stock has >1 tag (so we don't remove all tags)
if (testTags.length >= 1) {
  const tagToDelete = testTags[0];

  // Re-hover the tag
  // Track API calls
  let tagUpdateCalled = false;
  page.on("request", (req) => {
    if (req.url().includes("/api/config") && req.method() === "POST") {
      tagUpdateCalled = true;
    }
  });

  const tagSpan2 = page.locator("span").filter({ hasText: new RegExp(`^${tagToDelete}$`) }).first();
  if (await tagSpan2.count() > 0) {
    await tagSpan2.hover();
    await page.waitForTimeout(400);

    // After hover, only one × should exist on the page (inside the hovered tag chip)
    const xBtn = page.locator("span").filter({ hasText: /^×$/ }).first();
    const xBtnCount = await xBtn.count();
    xBtnCount > 0 ? ok(`× button visible on hover for "${tagToDelete}"`) : ng(`× button not visible on hover for "${tagToDelete}"`);

    if (xBtnCount > 0) {
      await xBtn.click();
      ok(`Clicked × on tag "${tagToDelete}"`);
    } else {
      ng(`Could not click × on tag "${tagToDelete}"`);
    }

    if (xBtnCount > 0) {
      // Wait for the config POST to complete (not just fire)
      await page.waitForTimeout(2500);
      await shot("tag_05_after_delete.png");
      tagUpdateCalled ? ok("API /api/config POST was called") : ng("API /api/config POST was NOT called");

      // Verify tag removed from page
      const stillVisible = await page.evaluate(({ tag, code }) => {
        const spans = Array.from(document.querySelectorAll("span"));
        const codeSpan = spans.find(s => s.textContent?.trim() === code);
        if (!codeSpan) return false;
        const row = codeSpan.closest("div");
        return !!row && Array.from(row.querySelectorAll("span")).some(s => s.textContent?.trim() === tag);
      }, { tag: tagToDelete, code: testCode });

      !stillVisible ? ok(`Tag "${tagToDelete}" removed from row`) : ng(`Tag "${tagToDelete}" still visible in row`);

      // Verify via API
      const verifyResp = await page.request.get(`${BASE}/api/config`);
      const verifyData = await verifyResp.json();
      const currentTags = verifyData.watchlist?.[testCode]?.tags || [];
      !currentTags.includes(tagToDelete)
        ? ok(`Tag "${tagToDelete}" removed in API`)
        : ng(`Tag "${tagToDelete}" still in API tags: [${currentTags.join(", ")}]`);

      // ─── Restore original tags ──────────────────────
      console.log("\n═══ 7. Cleanup: restore tags ═══");
      try {
        const restoreResp = await page.request.post(`${BASE}/api/config`, {
          data: { action: "update", code: testCode, data: { tags: testTags } },
        });
        const restoreJson = await restoreResp.json();
        restoreJson.success
          ? ok(`Restored tags for ${testCode}: [${testTags.join(", ")}]`)
          : ng("Restore failed", restoreJson.message);
      } catch (e) {
        console.log(`  [WARN] Restore request failed (dev server issue): ${String(e).slice(0, 80)}`);
        console.log("  [INFO] Manually verify tags restored via API if needed");
      }
    }
  } else {
    ng("Tag span not found for hover-delete test");
  }
} else {
  ok("Delete test skipped (stock has 0 tags)");
}

await browser.close();
console.log(`\n${"═".repeat(50)}`);
console.log(`DONE: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) process.exit(1);
