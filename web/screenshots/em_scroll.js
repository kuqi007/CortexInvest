const { firefox } = require('playwright');

(async () => {
  const browser = await firefox.launch({ headless: false });
  const page = await browser.newPage({ 
    viewport: { width: 1400, height: 5000 },
    deviceScaleFactor: 2
  });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#root > *', { timeout: 20000 });
  await page.waitForTimeout(10000);

  // Scroll to bottom to trigger lazy loading
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(3000);
  
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log('Full height:', height);

  await page.screenshot({ path: 'screenshots/em_complete.png', fullPage: true });
  console.log('Complete screenshot saved');
  
  await browser.close();
})();
