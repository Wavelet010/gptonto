const { chromium } = require('@playwright/test');

async function debug() {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const page = await browser.newPage();

  page.on('console', msg => {
    console.log(`[${msg.type()}] ${msg.text()}`);
  });

  await page.goto('http://localhost:9621/webui', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForTimeout(3000);

  // Take screenshot
  await page.screenshot({ path: '/tmp/debug-page.png', fullPage: false });
  console.log('Screenshot saved');

  // Find all buttons
  const buttons = await page.locator('button').all();
  console.log(`Found ${buttons.length} buttons:`);
  for (const btn of buttons) {
    const text = await btn.textContent();
    console.log(`  - "${text}"`);
  }

  // Try clicking first button with any text containing upload/上传
  for (const btn of buttons) {
    const text = (await btn.textContent()) || '';
    if (text.includes('Upload') || text.includes('上传')) {
      console.log(`\nClicking button: "${text}"`);
      await btn.click();
      break;
    }
  }

  await page.waitForTimeout(2000);
  await page.screenshot({ path: '/tmp/debug-after-click.png', fullPage: false });

  // Get page HTML around the dialog area
  const dialogHtml = await page.locator('[role="dialog"]').count();
  console.log(`\nDialog role count: ${dialogHtml}`);

  // Check for skip-graph-upload
  const cbCount = await page.locator('#skip-graph-upload').count();
  console.log(`skip-graph-upload count: ${cbCount}`);

  // Get all input elements
  const inputs = await page.locator('input').all();
  console.log(`Input elements: ${inputs.length}`);
  for (const input of inputs) {
    const id = await input.getAttribute('id');
    const type = await input.getAttribute('type');
    console.log(`  input id="${id}" type="${type}"`);
  }

  await browser.close();
}

debug().catch(console.error);
