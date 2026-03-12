const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
  });
  const page = await ctx.newPage();

  page.on('response', async (response) => {
    const url = response.url();
    if (/\.(js|css|png|svg|woff2|ico|jpg)(\?|$)/.test(url)) return;
    try {
      const ct = response.headers()['content-type'] || '';
      if (ct.includes('json') || ct.includes('text/plain')) {
        const body = await response.text();
        console.log('API:', url.substring(0, 150));
        console.log('  Status:', response.status(), 'CT:', ct.substring(0, 50));
        console.log('  Body:', body.substring(0, 1000));
        console.log('---');
      }
    } catch(e) {}
  });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(5000);

  await browser.close();
})();
