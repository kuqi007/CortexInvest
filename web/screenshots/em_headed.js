const { chromium } = require('playwright');

(async () => {
  // Use headed mode - sometimes headless is detected
  const browser = await chromium.launch({ 
    headless: false,
    args: ['--disable-blink-features=AutomationControlled']
  });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
  });
  const page = await ctx.newPage();
  
  // Remove automation flags
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
  });

  page.on('response', async (response) => {
    const url = response.url();
    if (/\.(js|css|png|svg|woff2|ico|jpg|ttf|map)(\?|$)/.test(url)) return;
    try {
      const ct = response.headers()['content-type'] || '';
      if (ct.includes('json')) {
        const body = await response.text();
        if (body.length > 100) {
          console.log('API:', url.substring(0, 180));
          console.log('  Body:', body.substring(0, 600));
          console.log('---');
        }
      }
    } catch(e) {}
  });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(12000);
  
  const allText = await page.evaluate(() => document.body.innerText);
  console.log('\n=== BODY TEXT ===');
  console.log(allText.substring(0, 3000) || '(empty)');
  
  const html = await page.evaluate(() => document.documentElement.outerHTML);
  console.log('\n=== HTML LENGTH ===', html.length);
  console.log(html.substring(0, 2000));
  
  await page.screenshot({ path: 'screenshots/eastmoney_report4.png', fullPage: true });
  console.log('\nScreenshot saved');
  await browser.close();
})();
