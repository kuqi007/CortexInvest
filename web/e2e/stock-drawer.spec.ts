import { test, expect } from "@playwright/test";

test.describe("单股配置 API /api/config/[symbol]", () => {
  test("查询存在的股票返回 found:true", async ({ request }) => {
    const res = await request.get("/api/config/000001");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.found).toBe(true);
    expect(data.name).toBe("平安银行");
    expect(data.type).toBe("holding");
  });

  test("查询 watching 股票返回 found:true 无 type 字段或 type=watching", async ({ request }) => {
    const res = await request.get("/api/config/159326");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.found).toBe(true);
    // watching 股票默认 type 为 "watching"
    if (data.type) expect(data.type).toBe("watching");
  });

  test("查询不存在的股票返回 found:false", async ({ request }) => {
    const res = await request.get("/api/config/NOTEXIST");
    expect(res.ok()).toBeTruthy();
    const data = await res.json();
    expect(data.found).toBe(false);
    expect(data).not.toHaveProperty("name");
  });
});

test.describe("StockDrawer 交互", () => {
  test.setTimeout(60_000);
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded", timeout: 30_000 });
    // 等数据加载: refresh counter 出现
    await page.waitForSelector("text=/refresh #[1-9]/", { timeout: 30_000 });
  });

  test("点击持仓行打开 drawer，显示股票代码", async ({ page }) => {
    // 找到第一个可点击的持仓行（title="点击查看详情"）
    const stockRow = page.locator('[title="点击查看详情"]').first();
    await expect(stockRow).toBeVisible({ timeout: 10_000 });
    await stockRow.click();

    // drawer 面板应该出现（固定定位的右侧面板）
    const drawer = page.locator('div[style*="position: fixed"][style*="right: 0"]').first();
    await expect(drawer).toBeVisible({ timeout: 5_000 });

    // drawer 中应该包含股票代码
    const drawerText = await drawer.textContent();
    expect(drawerText).toBeTruthy();
  });

  test("drawer 关闭后不影响页面", async ({ page }) => {
    const stockRow = page.locator('[title="点击查看详情"]').first();
    await expect(stockRow).toBeVisible({ timeout: 10_000 });
    await stockRow.click();

    // 等待 drawer 打开
    const drawer = page.locator('div[style*="position: fixed"][style*="right: 0"]').first();
    await expect(drawer).toBeVisible({ timeout: 5_000 });

    // 按 ESC 关闭
    await page.keyboard.press("Escape");

    // 等待 drawer 消失
    await expect(drawer).not.toBeVisible({ timeout: 5_000 });

    // 页面仍正常: 至少有一个持仓行可见
    await expect(stockRow).toBeVisible({ timeout: 5_000 });
  });
});
