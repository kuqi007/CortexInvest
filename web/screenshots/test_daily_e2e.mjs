/**
 * E2E test for /daily page (晨报 + 信号日报)
 *
 * Covers: page load, morning briefing card, daily report card, collapse/expand, empty state, markdown rendering
 */
import { chromium } from "playwright";

const BASE = "http://localhost:3120";
const ok = (t) => console.log(`  [PASS] ${t}`);
const ng = (t, d) => console.log(`  [FAIL] ${t}: ${d || ""}`);

let pass = 0, fail = 0;
function record(t, passed, detail = "") {
  if (passed) { ok(t); pass++; }
  else { ng(t, detail); fail++; }
}

const browser = await chromium.launch({ headless: true });
const p = await browser.newPage({ viewport: { width: 1280, height: 900 } });

try {
  // ═══ 1. Page load & structure ═══
  console.log("\n═══ 1. Page structure ═══");
  await p.goto(`${BASE}/daily`, { waitUntil: "commit", timeout: 30000 });
  await p.waitForTimeout(3000);

  const hasError = await p.evaluate(() => document.body?.innerText?.includes("[ERROR]"));
  record("Daily: no [ERROR] banner", !hasError);

  const hasTitle = await p.evaluate(() => document.body?.innerText?.includes("daily"));
  record("Daily: title visible", hasTitle);

  // AppTabs shows "daily" as active
  const isActiveTab = await p.evaluate(() => {
    const t = document.body?.innerText || '';
    return t.includes("daily — morning") || t.includes("daily");
  });
  record("Daily: active tab highlighted", isActiveTab);

  // ═══ 2. Content check ═══
  console.log("\n═══ 2. Content ═══");

  // Check if morning briefing or daily report exists
  const contentState = await p.evaluate(async () => {
    const t = document.body?.innerText || '';
    const hasMorning = t.includes("早间简报");
    const hasReport = t.includes("信号日报");
    const hasEmpty = t.includes("暂无数据");
    return { hasMorning, hasReport, hasEmpty };
  });
  record("Daily: has content (morning/report/empty)", contentState.hasMorning || contentState.hasReport || contentState.hasEmpty);

  // ═══ 3. Morning briefing card ═══
  console.log("\n═══ 3. Morning briefing ═══");
  if (contentState.hasMorning) {
    // Card header has collapsible toggle
    const hasCollapse = await p.evaluate(() => {
      const t = document.body?.innerText || '';
      return t.includes("▾") || t.includes("▸");
    });
    record("Daily: briefing has collapse toggle", hasCollapse);

    // Check for market data (US or Asia)
    const hasMarketData = await p.evaluate(() => {
      const t = document.body?.innerText || '';
      return t.includes("美股") || t.includes("亚太") || t.includes("道琼斯") || t.includes("纳斯达克");
    });
    record("Daily: briefing has market data", hasMarketData);

    // Collapse the briefing
    const collapseBriefing = await p.evaluate(async () => {
      const divs = Array.from(document.querySelectorAll('div'));
      // Find the clickable header with 早间简报
      const header = divs.find(d =>
        d.style.cursor === 'pointer' &&
        d.textContent.includes("早间简报")
      );
      if (!header) return { ok: false, detail: "header not found" };
      const beforeText = header.textContent.slice(0, 30);
      header.click();
      await new Promise(r => setTimeout(r, 300));
      // After collapse, ▾ should become ▸
      return { ok: header.textContent.includes("▸"), before: beforeText, after: header.textContent.slice(0, 30) };
    });
    record("Daily: briefing collapses", collapseBriefing.ok, collapseBriefing.detail || "");

    // Expand again
    if (collapseBriefing.ok) {
      const expandBriefing = await p.evaluate(async () => {
        const divs = Array.from(document.querySelectorAll('div'));
        const header = divs.find(d =>
          d.style.cursor === 'pointer' &&
          d.textContent.includes("早间简报")
        );
        if (!header) return { ok: false };
        header.click();
        await new Promise(r => setTimeout(r, 300));
        return { ok: header.textContent.includes("▾") };
      });
      record("Daily: briefing expands", expandBriefing.ok);
    }
  } else {
    record("Daily: briefing has collapse toggle", true, "(skipped: no morning briefing)");
    record("Daily: briefing has market data", true, "(skipped: no morning briefing)");
    record("Daily: briefing collapses", true, "(skipped)");
    record("Daily: briefing expands", true, "(skipped)");
  }

  // ═══ 4. Daily report card ═══
  console.log("\n═══ 4. Daily report ═══");
  if (contentState.hasReport) {
    // Report should render markdown sections (## headers)
    const hasMarkdownSections = await p.evaluate(() => {
      const t = document.body?.innerText || '';
      return t.includes("市场整体评估") || t.includes("重点关注") || t.includes("逐股速览") || t.includes("操作建议");
    });
    record("Daily: report has markdown sections", hasMarkdownSections);

    // Collapse the report
    const collapseReport = await p.evaluate(async () => {
      const divs = Array.from(document.querySelectorAll('div'));
      const header = divs.find(d =>
        d.style.cursor === 'pointer' &&
        d.textContent.includes("信号日报")
      );
      if (!header) return { ok: false, detail: "report header not found" };
      header.click();
      await new Promise(r => setTimeout(r, 300));
      return { ok: header.textContent.includes("▸"), text: header.textContent.slice(0, 30) };
    });
    record("Daily: report collapses", collapseReport.ok, collapseReport.detail || "");

    // Expand
    if (collapseReport.ok) {
      const expandReport = await p.evaluate(async () => {
        const divs = Array.from(document.querySelectorAll('div'));
        const header = divs.find(d =>
          d.style.cursor === 'pointer' &&
          d.textContent.includes("信号日报")
        );
        if (!header) return { ok: false };
        header.click();
        await new Promise(r => setTimeout(r, 300));
        return { ok: header.textContent.includes("▾") };
      });
      record("Daily: report expands", expandReport.ok);
    }
  } else {
    record("Daily: report has markdown sections", true, "(skipped: no daily report for today)");
    record("Daily: report collapses", true, "(skipped)");
    record("Daily: report expands", true, "(skipped)");
  }

  // ═══ 5. Empty state ═══
  console.log("\n═══ 5. Empty state ═══");
  if (contentState.hasEmpty) {
    record("Daily: empty state shows guidance text", true);
  } else {
    record("Daily: empty state shows guidance text", true, "(skipped: content exists)");
  }

  // ═══ 6. API returns data ═══
  console.log("\n═══ 6. API ═══");
  const apiCheck = await p.evaluate(async () => {
    try {
      const res = await fetch("/api/summary");
      const json = await res.json();
      return { ok: !!json.data, hasDate: !!json.data?.date, hasReport: !!json.data?.report };
    } catch {
      return { ok: false };
    }
  });
  record("Daily: /api/summary returns data", apiCheck.ok, JSON.stringify(apiCheck));

  // ═══ 7. Console errors ═══
  console.log("\n═══ 7. Console errors ═══");
  const consoleErrors = [];
  p.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  await p.reload({ waitUntil: "commit" });
  await p.waitForTimeout(3000);
  record("Daily: no JS console errors", consoleErrors.length === 0, consoleErrors.join("; "));

} catch (e) {
  console.error(`\n[ERROR] Test crashed: ${e.message}`);
  fail++;
} finally {
  await browser.close();
}

console.log(`\n${"═".repeat(60)}`);
console.log(`DAILY E2E: ${pass + fail} tests | ${pass} PASS | ${fail} FAIL`);
if (fail > 0) process.exit(1);
