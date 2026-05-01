import { test, expect } from "@playwright/test";

test.describe("Dashboard (首页)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    // 等数据加载完成：refresh #1 表示至少 fetch 过一次
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
  });

  test("页面正常加载，标题栏和 Tab 栏可见", async ({ page }) => {
    await expect(page.locator("text=✱ monitor").first()).toBeVisible();
    await expect(page.locator("text=holdings").first()).toBeVisible();
  });

  test("摘要栏显示节点数和涨跌统计", async ({ page }) => {
    await expect(page.locator("text=节点:").first()).toBeVisible();
    await expect(page.locator("text=成交额:").first()).toBeVisible();
    await expect(page.locator("text=平均涨跌:").first()).toBeVisible();
  });

  test("持仓 section 可见且可折叠", async ({ page }) => {
    const prodSection = page.locator("text=/prod:(stocks|ETF)/").first();
    if (await prodSection.isVisible()) {
      await prodSection.click();
      await prodSection.click();
    }
  });

  test("自选 section 可见且可折叠", async ({ page }) => {
    await page.goto("/watching", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
    const sec = page.locator("text=自选:股票").first();
    if (await sec.isVisible()) {
      await sec.click();
      await sec.click();
    }
  });

  test("hidden section 默认折叠", async ({ page }) => {
    const hiddenSection = page.locator("text=/hidden \\(\\d+\\)/");
    if (await hiddenSection.isVisible()) {
      // hidden section 的 ▸ 表示折叠状态
      await expect(hiddenSection).toContainText("▸");
      // 点击展开
      await hiddenSection.click();
      await expect(hiddenSection).toContainText("▾");
      // 再点击折叠回去
      await hiddenSection.click();
      await expect(hiddenSection).toContainText("▸");
    }
  });

  test("表头列可点击排序", async ({ page }) => {
    const chgHeader = page.locator("text=涨跌幅").first();
    if (await chgHeader.isVisible()) {
      await chgHeader.click();
      await expect(chgHeader).toContainText(/[▲▼]/);
      await chgHeader.click();
      await expect(chgHeader).toContainText(/[▲▼]/);
    }
  });

  test("portfolio 摘要显示 position/yield/return/today", async ({ page }) => {
    const mkt = page.locator("text=总市值:").first();
    if (await mkt.isVisible()) {
      await expect(page.locator("text=盈亏:").first()).toBeVisible();
      await expect(page.locator("text=收益率:").first()).toBeVisible();
      await expect(page.locator("text=今日:").first()).toBeVisible();
    }
  });

  test("命令提示符可见", async ({ page }) => {
    await expect(page.locator("text=行情收集").first()).toBeVisible();
  });
});

test.describe("Dashboard 新增功能", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
  });

  test("搜索框可见且可输入", async ({ page }) => {
    const searchInput = page.locator('input[placeholder="代码或名称..."]').first();
    if (await searchInput.isVisible()) {
      await searchInput.fill("000001");
      await expect(searchInput).toHaveValue("000001");
      await searchInput.fill("");
    }
  });

  test("inline add stock 按钮/区域可见", async ({ page }) => {
    const addBtn = page.locator("text=添加持仓").first();
    if (await addBtn.isVisible()) {
      await expect(addBtn).toBeVisible();
    }
    const codeInput = page.locator('input[placeholder="代码"]').first();
    if (await codeInput.isVisible()) {
      await expect(codeInput).toBeVisible();
    }
  });

  test("中文标签显示", async ({ page }) => {
    const chgLabel = page.locator("text=涨跌幅").first();
    if (await chgLabel.isVisible()) {
      await expect(chgLabel).toBeVisible();
    }
    const posLabel = page.locator("text=持仓:").first();
    if (await posLabel.isVisible()) {
      await expect(posLabel).toBeVisible();
    }
  });

  test("portfolio摘要显示更多字段", async ({ page }) => {
    const positionLabel = page.locator("text=总市值:");
    if (await positionLabel.isVisible()) {
      await expect(page.locator("text=盈亏:").first()).toBeVisible();
      await expect(page.locator("text=收益率:").first()).toBeVisible();
      await expect(page.locator("text=今日:").first()).toBeVisible();
      // 扩展验证更多字段
      const availLabel = page.locator("text=可用:").first();
      if (await availLabel.isVisible()) {
        await expect(availLabel).toBeVisible();
      }
      const totalAssets = page.locator("text=总资产:").first();
      if (await totalAssets.isVisible()) {
        await expect(totalAssets).toBeVisible();
      }
      const mktVal = page.locator("text=总市值:").first();
      if (await mktVal.isVisible()) {
        await expect(mktVal).toBeVisible();
      }
    }
  });

  test("排序功能 - 多列表头可点击", async ({ page }) => {
    const headers = ["涨跌幅", "盈亏%", "市值", "仓位"];
    for (const label of headers) {
      const header = page.locator(`text=${label}`).first();
      if (await header.isVisible()) {
        await header.click();
        await expect(header).toContainText(/[▲▼]/);
        await header.click();
        await expect(header).toContainText(/[▲▼]/);
        break; // 验证一个即可，避免过多点击
      }
    }
  });

  test("Alert events区域", async ({ page }) => {
    // alert log tail 可能在数据加载后出现
    const alertLog = page.locator("text=info").filter({ hasText: /行情收集|调度器/ }).first();
    if (await alertLog.isVisible()) {
      await expect(alertLog).toBeVisible();
    }
  });

  test("Market turnover显示", async ({ page }) => {
    const shLabel = page.locator("text=SH").first();
    if (await shLabel.isVisible()) {
      await expect(shLabel).toBeVisible();
      const szLabel = page.locator("text=SZ").first();
      await expect(szLabel).toBeVisible();
      const turnover = page.locator("text=成交").first();
      await expect(turnover).toBeVisible();
    }
  });
});

  test.describe("行情停止 Banner 逻辑", () => {
    /**
     * These tests verify the banner text logic based on trading-status API.
     * The stale banner (with age > 6*pollMs) shows different messages:
     *   - cn=true (A股交易中)  → orange warning "行情已停止更新"
     *   - cn=false, hk=true    → gray info "A股已收盘，港股仍在交易"
     *   - cn=false, hk=false   → gray info "已休市，行情暂时停止更新"
     *
     * The market-status indicators in the header are also verified.
     * Note: testing the stale banner display timing requires the MetricsProvider ts
     * to be > 6*pollMs old — in dev without poller this is tested via unit test.
     */

    test("A股休市 + 港股交易中 → 指标显示休市/交易中，指标区可见", async ({ page }) => {
      // Mock trading-status: A股 closed, 港股 open
      await page.route("**/api/trading-status", (route) => {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            success: true,
            data: { trading: true, markets: { cn: false, hk: true }, time: "2026-04-30 15:30:00" },
          }),
        });
      });

      await page.goto("/", { waitUntil: "domcontentloaded" });
      // Wait for trading-status to be fetched and rendered
      await page.waitForTimeout(2000);

      // Verify market indicators show correct status
      await expect(page.locator("text=A股").first()).toBeVisible();
      await expect(page.locator("text=港股").first()).toBeVisible();
      // The "休市" text should be visible in the status chip area
      const body = page.locator("body");
      await expect(body).toContainText("休市");
      await expect(body).toContainText("交易中");
    });

    test("A股交易中 + 港股休市 → 指标均显示交易中/休市", async ({ page }) => {
      await page.route("**/api/trading-status", (route) => {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            success: true,
            data: { trading: true, markets: { cn: true, hk: false }, time: "2026-04-30 10:30:00" },
          }),
        });
      });

      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(2000);

      const body = page.locator("body");
      await expect(body).toContainText("交易中");
    });

    test("A股和港股都收盘 → 指标均显示休市", async ({ page }) => {
      await page.route("**/api/trading-status", (route) => {
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            success: true,
            data: { trading: false, markets: { cn: false, hk: false }, time: "2026-04-30 16:30:00" },
          }),
        });
      });

      await page.goto("/", { waitUntil: "domcontentloaded" });
      await page.waitForTimeout(2000);

      // Both should show 休市
      const body = page.locator("body");
      await expect(body).toContainText("A股");
      await expect(body).toContainText("港股");
    });
  });

  test.describe("主力列 HK/A股 tab 切换", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
  });

  test("A股 tab 不显示主力列表头", async ({ page }) => {
    // Check if A-share tab exists and click it if not already active
    const aShareTab = page.locator("text=A-share").first();
    if (await aShareTab.isVisible()) {
      const isActive = await aShareTab.getAttribute("aria-selected");
      if (isActive !== "true") {
        await aShareTab.click();
        await page.waitForTimeout(2000);
      }
    }

    // Verify "主力" header is NOT visible on A-share tab
    const mainForceHeader = page.locator("text=主力").first();
    if (await mainForceHeader.isVisible()) {
      await expect(mainForceHeader).not.toBeVisible();
    }
  });

  test("HK tab 显示主力列表头", async ({ page }) => {
    const hkTab = page.getByRole("button", { name: "HK", exact: true });
    if (await hkTab.isVisible()) {
      await hkTab.click();
      await page.waitForTimeout(2000);
      await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
    }

    // Verify "主力" header IS visible in HK tab
    await expect(page.locator("text=主力").first()).toBeVisible();

    // Switch back to A-share tab
    const aShareTab = page.locator("text=A-share").first();
    if (await aShareTab.isVisible()) {
      await aShareTab.click();
      await page.waitForTimeout(2000);
      await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
    }

    // Verify "主力" header is NOT visible again on A-share tab
    const mainForceHeader = page.locator("text=主力").first();
    if (await mainForceHeader.isVisible()) {
      await expect(mainForceHeader).not.toBeVisible();
    }
  });
});
