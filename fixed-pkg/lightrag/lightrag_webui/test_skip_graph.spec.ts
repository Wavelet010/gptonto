import { test, expect } from '@playwright/test';

test.describe('Skip Graph checkbox in Upload dialog', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to WebUI and wait for it to load
    await page.goto('http://localhost:9621/webui');
    // Wait for the page to be fully loaded
    await page.waitForLoadState('networkidle');
    // Wait for the upload button to be visible
    await page.waitForSelector('button:has-text("上传")', { timeout: 10000 });
  });

  test('should display skip graph checkbox in upload dialog', async ({ page }) => {
    // Click the Upload button to open the dialog
    await page.click('button:has-text("上传")');

    // Wait for the dialog to appear
    await page.waitForSelector('id=skip-graph-upload', { timeout: 5000 });

    // Verify the checkbox is visible
    const checkbox = page.locator('id=skip-graph-upload');
    await expect(checkbox).toBeVisible();

    // Verify it's unchecked by default
    await expect(checkbox).not.toBeChecked();

    // Verify the label text is visible
    const label = page.locator('label[for="skip-graph-upload"]');
    await expect(label).toBeVisible();
    await expect(label).toHaveText(/跳过图谱生成|Skip graph/);
  });

  test('should toggle skip graph checkbox', async ({ page }) => {
    // Open the dialog
    await page.click('button:has-text("上传")');
    await page.waitForSelector('id=skip-graph-upload', { timeout: 5000 });

    const checkbox = page.locator('id=skip-graph-upload');

    // Click to check
    await checkbox.check();
    await expect(checkbox).toBeChecked();

    // Click to uncheck
    await checkbox.uncheck();
    await expect(checkbox).not.toBeChecked();
  });

  test('should reset skip graph state when dialog is closed and reopened', async ({ page }) => {
    // Open the dialog
    await page.click('button:has-text("上传")');
    await page.waitForSelector('id=skip-graph-upload', { timeout: 5000 });

    const checkbox = page.locator('id=skip-graph-upload');

    // Check the checkbox
    await checkbox.check();
    await expect(checkbox).toBeChecked();

    // Close the dialog by clicking outside (press Escape)
    await page.keyboard.press('Escape');

    // Wait for dialog to close
    await page.waitForSelector('id=skip-graph-upload', { state: 'detached', timeout: 5000 });

    // Reopen the dialog
    await page.click('button:has-text("上传")');
    await page.waitForSelector('id=skip-graph-upload', { timeout: 5000 });

    // Verify checkbox is reset to unchecked
    const checkboxAfterReopen = page.locator('id=skip-graph-upload');
    await expect(checkboxAfterReopen).not.toBeChecked();
  });

  test('should take screenshot of dialog with checkbox visible', async ({ page }) => {
    // Open the dialog
    await page.click('button:has-text("上传")');
    await page.waitForSelector('id=skip-graph-upload', { timeout: 5000 });

    // Check the checkbox
    const checkbox = page.locator('id=skip-graph-upload');
    await checkbox.check();
    await expect(checkbox).toBeChecked();

    // Take screenshot
    await page.screenshot({ path: '/tmp/upload-dialog-with-skip-graph.png', fullPage: true });
  });
});
