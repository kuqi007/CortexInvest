import { test, expect } from "@playwright/test";

test("daily page shows close briefing card", async ({ page }) => {
  await page.goto("http://localhost:3120/daily");
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(2000);

  // Screenshot for visual verification
  await page.screenshot({ path: "/tmp/daily-page-test.png", fullPage: true });

  // Check close briefing card is visible
  const closeCard = page.locator("text=收盘简报").first;
  await expect(closeCard).toBeVisible();

  // Check it has time content
  const cardText = await closeCard.innerText();
  console.log("Close card header:", cardText);
  expect(cardText).toMatch(/收盘简报/);

  // Click to expand and check content
  await closeCard.click();
  await page.waitForTimeout(500);

  const body = page.locator("body");
  const bodyText = await body.innerText();
  expect(bodyText).toContain("持仓盈亏");
});
