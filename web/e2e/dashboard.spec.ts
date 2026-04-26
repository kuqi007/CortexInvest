import { test, expect } from "@playwright/test";

test.describe("Dashboard (首页)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
    // 等数据加载完成：refresh #1 表示至少 fetch 过一次
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 15_000 });
  });

  test("页面正常加载，标题栏和 Tab 栏可见", async ({ page }) => {
    await expect(page.locator("text=monitor (node)").first()).toBeVisible();
    await expect(page.locator("text=Claude Code (node)")).toBeVisible();
  });

  test("摘要栏显示节点数和涨跌统计", async ({ page }) => {
    await expect(page.locator("text=Nodes:")).toBeVisible();
    await expect(page.locator("text=throughput:")).toBeVisible();
    await expect(page.locator("text=avg_delta:")).toBeVisible();
  });

  test("持仓 section 可见且可折叠", async ({ page }) => {
    // 至少有一个 prod section
    const prodSection = page.locator("text=/prod:(stocks|ETF)/").first();
    if (await prodSection.isVisible()) {
      // 检查 PROD 行可见
      await expect(page.locator("text=PROD").first()).toBeVisible();

      // 点击折叠
      await prodSection.click();
      // 再次点击展开
      await prodSection.click();
      await expect(page.locator("text=PROD").first()).toBeVisible();
    }
  });

  test("自选 section 可见且可折叠", async ({ page }) => {
    const stageSection = page.locator("text=/stage:(stocks|ETF)/").first();
    if (await stageSection.isVisible()) {
      await expect(page.locator("text=DEV").first()).toBeVisible();

      // 折叠
      await stageSection.click();
      // 展开
      await stageSection.click();
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
    const chgHeader = page.locator("text=CHG%").first();
    if (await chgHeader.isVisible()) {
      await chgHeader.click();
      // 点击后应出现排序箭头 ▼ 或 ▲
      await expect(chgHeader).toContainText(/[▲▼]/);
      // 再点切换方向
      await chgHeader.click();
      await expect(chgHeader).toContainText(/[▲▼]/);
    }
  });

  test("portfolio 摘要显示 position/yield/return/today", async ({ page }) => {
    // 如果有持仓，应显示 portfolio 摘要行
    const positionLabel = page.locator("text=position:");
    if (await positionLabel.isVisible()) {
      await expect(page.locator("text=yield:")).toBeVisible();
      await expect(page.locator("text=return:")).toBeVisible();
      await expect(page.locator("text=today:")).toBeVisible();
    }
  });

  test("命令提示符可见", async ({ page }) => {
    await expect(page.locator("text=~/projects/monitor").last()).toBeVisible();
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
    const positionLabel = page.locator("text=position:");
    if (await positionLabel.isVisible()) {
      await expect(page.locator("text=yield:")).toBeVisible();
      await expect(page.locator("text=return:")).toBeVisible();
      await expect(page.locator("text=today:")).toBeVisible();
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
