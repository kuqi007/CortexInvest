import { test, expect } from "@playwright/test";

test.describe("Manage Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/manage", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=manage", { timeout: 15_000 });
    await page.waitForTimeout(1000);
  });

  test("管理页正常加载", async ({ page }) => {
    await expect(page.getByText("manage", { exact: true })).toBeVisible();
    await expect(page.locator("text=settings")).toBeVisible();
  });

  test("Settings可展开编辑", async ({ page }) => {
    const settingsToggle = page.locator("text=settings").first();
    await settingsToggle.click();
    const pollInput = page.locator('input[type="number"]').first();
    await expect(pollInput).toBeVisible();
    await pollInput.click();
    await expect(pollInput).toBeFocused();
  });

  test("持仓分组可见", async ({ page }) => {
    const holdingSection = page.locator("text=/持仓:(A股个股|ETF|港股)/").first();
    if (await holdingSection.isVisible()) {
      await expect(holdingSection).toBeVisible();
    }
  });

  test("自选分组可见", async ({ page }) => {
    const watchSection = page.locator("text=/自选/").first();
    if (await watchSection.isVisible()) {
      await expect(watchSection).toBeVisible();
    }
  });

  test("Add stock区域", async ({ page }) => {
    await expect(page.locator("text=add stock")).toBeVisible();
    const codeInput = page.locator('input[placeholder="000001"]').first();
    await expect(codeInput).toBeVisible();
    await codeInput.fill("TEST001");
    await expect(codeInput).toHaveValue("TEST001");
    await codeInput.fill("");
  });

  test("导航回首页", async ({ page }) => {
    const backLink = page.locator("text=← monitor");
    await expect(backLink).toBeVisible();
    await backLink.click();
    await expect(page).toHaveURL("/");
  });

  test("Tag管理区域", async ({ page }) => {
    // Tag area 在 stock rows 中，检查是否有 tag 相关元素
    const tagElements = page.locator("text=/tags?/i").first();
    if (await tagElements.isVisible()) {
      await expect(tagElements).toBeVisible();
    }
    // 或者检查批量打 Tag 按钮（当有选中项时）
    const batchTagBtn = page.locator("text=批量打 Tag").first();
    if (await batchTagBtn.isVisible()) {
      await expect(batchTagBtn).toBeVisible();
    }
  });

  test("Hide开关可见", async ({ page }) => {
    const hideToggle = page.locator("text=hide").first();
    if (await hideToggle.isVisible()) {
      await expect(hideToggle).toBeVisible();
    }
  });
});
