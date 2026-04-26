import { test, expect } from "@playwright/test";

test("daily page shows close briefing card", async ({ page }) => {
  await page.goto("/daily");
  await page.waitForLoadState("networkidle");

  // Check close briefing card is visible
  const closeCard = page.locator("text=收盘简报").first();
  await expect(closeCard).toBeVisible();

  // Click to expand and verify page remains stable
  await closeCard.click();
  await expect(closeCard).toBeVisible();
});
