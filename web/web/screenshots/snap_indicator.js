const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 2 });
  
  await page.goto('http://localhost:3120/manage', { waitUntil: 'networkidle', timeout: 15000 });
  await page.waitForTimeout(2000);
  
  // Find 603929 row and click it to open drawer
  const row = page.locator('text=603929').first();
  if (await row.count() > 0) {
    await row.click();
    await page.waitForTimeout(3000); // Wait for drawer + indicator API
    await page.screenshot({ path: 'screenshots/indicator_drawer_603929.png' });
    console.log('603929 drawer screenshot saved');
  } else {
    console.log('603929 not found, trying 603163');
    const row2 = page.locator('text=603163').first();
    if (await row2.count() > 0) {
      await row2.click();
      await page.waitForTimeout(3000);
      await page.screenshot({ path: 'screenshots/indicator_drawer_603163.png' });
      console.log('603163 drawer screenshot saved');
    } else {
      console.log('Neither stock found on manage page');
      await page.screenshot({ path: 'screenshots/indicator_manage_page.png' });
    }
  }
  
  await browser.close();
})();
