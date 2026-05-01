import { expect, test, type Page } from "@playwright/test";

type DashboardRoute = {
  path: string;
  key: "holdings" | "starred" | "watching";
  summaryTokens: string[];
  sectionPatterns: RegExp[];
  emptyPattern: RegExp;
  sortKey: string;
};

const routes: DashboardRoute[] = [
  {
    path: "/?tab=A",
    key: "holdings",
    summaryTokens: ["节点:", "成交额:", "平均涨跌:"],
    sectionPatterns: [/置顶 \(\d+\)/, /持仓:(股票|ETF) \(\d+\)/, /隐藏 \(\d+\)/],
    emptyPattern: /当前市场无持仓/,
    sortKey: "change",
  },
  {
    path: "/starred?tab=A",
    key: "starred",
    summaryTokens: ["节点:", "涨:", "跌:"],
    sectionPatterns: [/置顶 \(\d+\)/, /持仓 \(\d+\)/, /自选:(股票|ETF) \(\d+\)/, /隐藏 \(\d+\)/],
    emptyPattern: /暂无特别关注的股票/,
    sortKey: "change",
  },
  {
    path: "/watching?tab=A",
    key: "watching",
    summaryTokens: ["节点:", "可见:", "隐藏:"],
    sectionPatterns: [/置顶 \(\d+\)/, /自选:(股票|ETF) \(\d+\)/, /隐藏 \(\d+\)/],
    emptyPattern: /当前市场无自选/,
    sortKey: "chgAmt",
  },
];

async function openRoute(page: Page, route: DashboardRoute) {
  await page.goto(route.path, { waitUntil: "domcontentloaded" });
  await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
}

test.describe("Dashboard route consistency", () => {
  for (const route of routes) {
    test(`${route.key}: loading, trust bar, summary, sections, and sorting`, async ({ page }) => {
      let delayedFirstMetrics = false;
      await page.route("**/api/metrics", async (requestRoute) => {
        if (!delayedFirstMetrics) {
          delayedFirstMetrics = true;
          await new Promise((resolve) => setTimeout(resolve, 800));
        }
        await requestRoute.continue();
      });

      await page.goto(route.path, { waitUntil: "domcontentloaded" });
      await expect(page.locator("text=正在加载数据...").first()).toBeVisible();
      await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });

      const body = page.locator("body");
      await expect(body).toContainText(`· ${route.key}`);
      await expect(body).toContainText("源 SQLite");
      await expect(body).toContainText("快照");
      await expect(body).toContainText("告警视图");
      await expect(body).toContainText(/refresh #\d+/);

      for (const token of route.summaryTokens) {
        await expect(body).toContainText(token);
      }

      let hasSection = false;
      for (const pattern of route.sectionPatterns) {
        const section = page.locator(`text=${pattern}`).first();
        if (await section.isVisible()) {
          hasSection = true;
          await expect(section).toContainText(/# ──/);
        }
      }
      if (!hasSection) {
        await expect(body).toContainText(route.emptyPattern);
      }

      const header = page.locator(`[data-sort-key="${route.sortKey}"]`).first();
      if (!(await header.isVisible())) {
        test.skip(true, `${route.key} has no visible sortable rows in current E2E data`);
      }
      await header.click();
      await expect(header).toContainText(/[▲▼]/);
      await header.click();
      await expect(header).toContainText(/[▲▼]/);
    });
  }

  test("routes remain independently accessible after A-share tab selection", async ({ page }) => {
    for (const route of routes) {
      await openRoute(page, route);
      await expect(page.locator("body")).toContainText(`· ${route.key}`);
      await expect(page.locator("body")).toContainText("● A股");
    }
  });
});
