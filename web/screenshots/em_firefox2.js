const { firefox } = require('playwright');

(async () => {
  const browser = await firefox.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'domcontentloaded', timeout: 30000 });
  
  // Wait for the root div to have children
  try {
    await page.waitForSelector('#root > *', { timeout: 20000 });
    console.log('Root has children!');
  } catch(e) {
    console.log('Root still empty after 20s');
  }
  
  await page.waitForTimeout(5000);
  
  const text = await page.evaluate(() => document.body.innerText);
  console.log('\n=== TEXT ===');
  console.log(text.substring(0, 5000) || '(empty)');
  
  const childCount = await page.evaluate(() => document.getElementById('root')?.children.length || 0);
  console.log('\nRoot children:', childCount);
  
  await page.screenshot({ path: 'screenshots/eastmoney_firefox.png', fullPage: true });
  console.log('Screenshot saved');
  await browser.close();
})();
