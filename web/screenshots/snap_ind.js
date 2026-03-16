const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2 });
  
  await page.goto('http://localhost:3120/', { waitUntil: 'domcontentloaded', timeout: 20000 });
  await page.waitForTimeout(4000);
  
  // Click 603163 row to open drawer
  const row = page.locator('text=603163').first();
  if (await row.count() > 0) {
    await row.click();
    await page.waitForTimeout(5000);
    await page.screenshot({ path: 'screenshots/indicator_drawer.png' });
    console.log('Drawer screenshot saved');
  } else {
    // Try watching page
    await page.goto('http://localhost:3120/watching', { waitUntil: 'domcontentloaded', timeout: 20000 });
    await page.waitForTimeout(4000);
    const row2 = page.locator('text=603929').first();
    if (await row2.count() > 0) {
      await row2.click();
      await page.waitForTimeout(5000);
      await page.screenshot({ path: 'screenshots/indicator_drawer.png' });
      console.log('Watching drawer screenshot saved');
    } else {
      await page.screenshot({ path: 'screenshots/indicator_page.png' });
      console.log('No stock found, page screenshot saved');
    }
  }
  
  await browser.close();
})();
