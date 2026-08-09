// Standalone Playwright test for skip_graph checkbox in Upload dialog
const { chromium } = require('@playwright/test');

const BASE_URL = 'http://localhost:9621/webui';

async function run() {
  console.log('=== Phase 2: Playwright UI Test for skip_graph checkbox ===\n');

  const browser = await chromium.launch({
    args: ['--no-sandbox', '--disable-setuid-sandbox'],
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  const page = await context.newPage();

  let passed = 0;
  let failed = 0;

  function check(condition, description) {
    if (condition) {
      console.log(`  PASS: ${description}`);
      passed++;
    } else {
      console.error(`  FAIL: ${description}`);
      failed++;
    }
  }

  async function openDialog(page) {
    const uploadBtn = page.locator('button', { hasText: /上传|Upload/ }).first();
    await uploadBtn.waitFor({ state: 'visible', timeout: 15000 });
    await uploadBtn.click();
    await page.waitForSelector('#skip-graph-upload', { timeout: 5000 });
  }

  async function closeDialog(page) {
    await page.keyboard.press('Escape');
    await page.waitForSelector('#skip-graph-upload', { state: 'detached', timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(500);
  }

  try {
    // --- Navigation ---
    console.log('1. Navigating to WebUI...');
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    // Wait for the upload button to be present
    await page.waitForSelector('button', { hasText: /上传|Upload/ }, { timeout: 15000 });
    console.log('   Page loaded successfully.\n');

    // =============================================
    // Test 1: Checkbox visible and unchecked by default
    // =============================================
    console.log('2. Open dialog, verify checkbox');
    await openDialog(page);
    console.log('   Dialog opened.');

    const checkbox = page.locator('#skip-graph-upload');
    check(await checkbox.isVisible(), 'Checkbox is visible in dialog');
    check(!(await checkbox.isChecked()), 'Checkbox is unchecked by default');

    const label = page.locator('label[for="skip-graph-upload"]');
    check(await label.isVisible(), 'Label is visible');
    const labelText = await label.textContent();
    console.log(`   Label text: "${labelText}"`);
    console.log();

    // =============================================
    // Test 2: Toggle checkbox on and off
    // =============================================
    console.log('3. Toggle checkbox');
    await checkbox.check();
    check(await checkbox.isChecked(), 'Checkbox is checked after explicit check');
    await checkbox.uncheck();
    check(!(await checkbox.isChecked()), 'Checkbox is unchecked after explicit uncheck');
    console.log();

    // =============================================
    // Test 3: Check, close dialog, reopen, verify reset
    // =============================================
    console.log('4. Verify state reset on close/reopen');
    await checkbox.check();
    check(await checkbox.isChecked(), 'Checkbox checked before close');

    await closeDialog(page);
    console.log('   Dialog closed.');

    await openDialog(page);
    console.log('   Dialog reopened.');

    const checkboxAfterReopen = page.locator('#skip-graph-upload');
    check(!(await checkboxAfterReopen.isChecked()), 'Checkbox is unchecked after reopen (state should be reset)');
    console.log();

    // =============================================
    // Test 4: Screenshot with checkbox checked
    // =============================================
    console.log('5. Take screenshot with checkbox visible and checked...');
    await checkboxAfterReopen.check();
    check(await checkboxAfterReopen.isChecked(), 'Checkbox checked for screenshot');
    await page.screenshot({ path: '/tmp/skip-graph-dialog.png', fullPage: false });
    console.log('   Screenshot saved to /tmp/skip-graph-dialog.png\n');

    // =============================================
    // Summary
    // =============================================
    console.log('=== Results ===');
    console.log(`Passed: ${passed}`);
    console.log(`Failed: ${failed}`);

    if (failed > 0) {
      console.error('\nSome tests FAILED.');
      process.exit(1);
    } else {
      console.log('\nAll tests PASSED.');
    }
  } catch (err) {
    console.error('\nTest error:', err.message);
    await page.screenshot({ path: '/tmp/skip-graph-error.png', fullPage: false }).catch(() => {});
    process.exit(1);
  } finally {
    await browser.close();
  }
}

run();
