const { firefox } = require('playwright');

(async () => {
  const browser = await firefox.launch({ headless: false });
  const page = await browser.newPage({ 
    viewport: { width: 1400, height: 1000 },
    deviceScaleFactor: 2
  });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#root > *', { timeout: 20000 });
  await page.waitForTimeout(10000);

  // Scroll down to load all content
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log('Height:', height);

  // Take cropped sections of the report content area
  // First, top section
  await page.screenshot({ path: 'screenshots/em_hi_top.png', clip: { x: 0, y: 50, width: 1400, height: 500 } });
  console.log('Top saved');

  // Middle section
  await page.screenshot({ path: 'screenshots/em_hi_mid.png', clip: { x: 0, y: 450, width: 1400, height: 500 } });
  console.log('Mid saved');

  // Bottom section  
  await page.screenshot({ path: 'screenshots/em_hi_bot.png', clip: { x: 0, y: 700, width: 1400, height: 300 } });
  console.log('Bot saved');

  await browser.close();
})();
