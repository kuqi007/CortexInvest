const { firefox } = require('playwright');

(async () => {
  const browser = await firefox.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#root > *', { timeout: 20000 });
  await page.waitForTimeout(8000);

  // Get full page height
  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  console.log('Page height:', height);

  // Take full page screenshot at high resolution
  await page.screenshot({ path: 'screenshots/em_full_page.png', fullPage: true });
  console.log('Full page screenshot saved');

  // Also scroll and take sectioned screenshots for readability
  const sections = Math.ceil(height / 900);
  for (let i = 0; i < Math.min(sections, 10); i++) {
    await page.evaluate((y) => window.scrollTo(0, y), i * 900);
    await page.waitForTimeout(1000);
    await page.screenshot({ path: `screenshots/em_section_${i}.png` });
    console.log(`Section ${i} saved`);
  }

  // Extract all text content
  const text = await page.evaluate(() => document.body.innerText);
  console.log('\n=== FULL TEXT ===');
  console.log(text);
  console.log('=== END ===');

  await browser.close();
})();
