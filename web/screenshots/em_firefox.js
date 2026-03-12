const { firefox } = require('playwright');

(async () => {
  const browser = await firefox.launch({ headless: false });
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  page.on('response', async (response) => {
    const url = response.url();
    if (/\.(js|css|png|svg|woff2|ico|jpg|ttf|map)(\?|$)/.test(url)) return;
    try {
      const ct = response.headers()['content-type'] || '';
      if (ct.includes('json')) {
        const body = await response.text();
        if (body.length > 200 && !url.includes('getFeedbackReason') && !url.includes('getGlobalConfig') && !url.includes('qsNotice')) {
          console.log('API:', url.substring(0, 180));
          console.log('  Body:', body.substring(0, 1500));
          console.log('---');
        }
      }
    } catch(e) {}
  });

  await page.goto('https://ai.eastmoney.com/share/?scene=virtualReport&productType=dfcf&reportId=355979349226580710&appfenxiang=1', { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(15000);
  
  const text = await page.evaluate(() => document.body.innerText);
  console.log('\n=== TEXT ===');
  console.log(text.substring(0, 5000) || '(empty)');
  
  await page.screenshot({ path: 'screenshots/eastmoney_firefox.png', fullPage: true });
  console.log('\nScreenshot saved');
  await browser.close();
})();
