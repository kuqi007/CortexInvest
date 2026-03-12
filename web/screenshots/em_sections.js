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

  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(3000);
  
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  
  // Take 7 sections of 750px each
  for (let i = 0; i < 7; i++) {
    const y = i * 700;
    if (y >= height) break;
    const h = Math.min(750, height - y);
    await page.screenshot({ path: `screenshots/em_p${i}.png`, clip: { x: 50, y, width: 1300, height: h } });
    console.log(`Section ${i} saved (y=${y}, h=${h})`);
  }
  
  await browser.close();
})();
