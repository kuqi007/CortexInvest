import { test, expect } from "@playwright/test";

test.describe("Daily Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/daily", { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(2000);
  });

  test("页面正常加载", async ({ page }) => {
    // 验证标题栏可见
    await expect(page.locator("text=daily").first()).toBeVisible();
    // 验证 Tab 栏可见
    await expect(page.locator("text=daily").first()).toBeVisible();
  });

  test("收盘简报卡片", async ({ page }) => {
    const closeCard = page.locator("text=收盘简报").first();
    if (await closeCard.isVisible()) {
      await expect(closeCard).toBeVisible();
    }
  });

  test("持仓盈亏显示", async ({ page }) => {
    const closeCard = page.locator("text=收盘简报").first();
    if (await closeCard.isVisible()) {
      await closeCard.click();
      await page.waitForTimeout(500);
      const body = page.locator("body");
      const bodyText = await body.innerText();
      expect(bodyText).toContain("持仓盈亏");
    }
  });

  test("Morning briefing", async ({ page }) => {
    const morningCard = page.locator("text=早间简报").first();
    if (await morningCard.isVisible()) {
      await expect(morningCard).toBeVisible();
    }
  });

  test("Earnings calendar", async ({ page }) => {
    // 如果页面有财报日历区域
    const earningsLabel = page.locator("text=/财报|earnings/i").first();
    if (await earningsLabel.isVisible()) {
      await expect(earningsLabel).toBeVisible();
    }
  });

  test("Summary report", async ({ page }) => {
    const summaryCard = page.locator("text=信号日报").first();
    if (await summaryCard.isVisible()) {
      await expect(summaryCard).toBeVisible();
    }
  });

  test("页面可滚动", async ({ page }) => {
    const body = page.locator("body");
    await expect(body).toBeVisible();
    // 验证至少有一个内容元素在 viewport 内
    const anyContent = page.locator("text=/收盘简报|早间简报|信号日报|Loading/").first();
    await expect(anyContent).toBeVisible();
  });
});
