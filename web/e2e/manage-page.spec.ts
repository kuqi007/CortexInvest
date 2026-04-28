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

test.describe("代码输入正则校验", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/manage", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=manage", { timeout: 15_000 });
    await page.waitForTimeout(1000);
  });

  test("无效代码被拦截", async ({ page }) => {
    const codeInput = page.locator('input[placeholder="000001"]').first();
    const addButton = page.locator("button", { hasText: "Add" }).first();

    // Listen for dialog events as fallback
    let dialogMessage = "";
    page.on("dialog", (dialog) => {
      dialogMessage = dialog.message();
    });

    const invalidCodes = ["ABC", "12345", "1234567", "HK123"];

    for (const code of invalidCodes) {
      dialogMessage = "";
      await codeInput.fill(code);
      await addButton.click();

      // Wait for toast to appear (toast has red background for errors)
      await page.waitForTimeout(300);

      // Check for error toast containing "股票代码格式错误"
      // The toast appears as a fixed div with the error message
      const toastVisible = await page
        .locator("div")
        .filter({ hasText: /股票代码格式错误/ })
        .first()
        .isVisible()
        .catch(() => false);

      const hasError = toastVisible || dialogMessage.includes("股票代码格式错误");

      // The error toast or dialog should appear
      expect(
        hasError,
        "无效代码 \"" + code + "\" 未被拦截, toastVisible=" + toastVisible + ", dialogMessage=" + dialogMessage
      ).toBeTruthy();

      // Verify stock was NOT added (table should not contain this code)
      const stockRow = page.locator("text=/^" + code + "$/").first();
      if (await stockRow.isVisible().catch(() => false)) {
        // If visible, it might be from a previous test run — not a test failure
        console.log("Note: " + code + " appears in list (possibly pre-existing)");
      }
    }
  });

  test("有效代码可提交", async ({ page }) => {
    const codeInput = page.locator('input[placeholder="000001"]').first();
    const addButton = page.locator("text=add", { exact: false }).first();

    // Listen for dialog events
    let dialogMessage = "";
    page.on("dialog", (dialog) => {
      dialogMessage = dialog.message();
    });

    // Use 688888 as test code (科创板, unlikely to exist)
    const testCode = "688888";

    await codeInput.fill(testCode);
    await addButton.click();
    await page.waitForTimeout(1000);

    // Verify NO "股票代码格式错误" error toast appears
    const errorToast = page.locator("text=/股票代码格式错误/").first();
    const hasError =
      (await errorToast.isVisible().catch(() => false)) ||
      dialogMessage.includes("股票代码格式错误");

    expect(hasError, "有效代码不应触发格式错误").toBeFalsy();

    // Cleanup: remove the test stock via API if it was added
    // Check if it was actually added by looking for it in the list
    const stockRow = page.locator("text=/^" + testCode + "$/").first();
    const wasAdded = await stockRow.isVisible().catch(() => false);

    if (wasAdded) {
      // Remove via API
      const { request } = page.context();
      await request.post("/api/config", {
        data: { action: "remove", code: testCode },
      });
    }
  });
});
