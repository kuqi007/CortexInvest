const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    userAgent: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
  });
  const page = await ctx.newPage();

  const apiData = [];
  page.on('response', async (response) => {
    const url = response.url();
    if (/\.(js|css|png|svg|woff2|ico|jpg|ttf)(\?|$)/.test(url)) return;
    try {
      const ct = response.headers()['content-type'] || '';
      if (ct.includes('json')) {
        const body = await response.text();
        apiData.push({ url: url.substring(0, 200), body: body.substring(0, 2000) });
      }
    } catch(e) {}
  });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'networkidle', timeout: 30000 });
  
  // Wait longer for SPA to render
  await page.waitForTimeout(10000);
  
  // Check for any late-loaded content
  const allText = await page.evaluate(() => document.body.innerText);
  console.log('=== PAGE TEXT ===');
  console.log(allText.substring(0, 3000));
  console.log('=== END TEXT ===');
  
  // Check if there's a shadow DOM or iframe
  const iframeCount = await page.evaluate(() => document.querySelectorAll('iframe').length);
  console.log('Iframes:', iframeCount);
  
  const divCount = await page.evaluate(() => document.querySelectorAll('div').length);
  console.log('Divs:', divCount);
  
  // Print all API calls
  console.log('\n=== ALL API CALLS ===');
  for (const d of apiData) {
    console.log('URL:', d.url);
    console.log('Body:', d.body.substring(0, 500));
    console.log('---');
  }
  
  await page.screenshot({ path: 'screenshots/eastmoney_report3.png', fullPage: true });
  await browser.close();
})();
