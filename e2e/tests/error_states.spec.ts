/**
 * Layer 5 — Error state rendering
 *
 * Covers: near-empty query, special-character query, maxlength enforcement,
 * and that the app stays functional after each edge case.
 */
import { test, expect } from '@playwright/test';
import { login } from './helpers';

test.describe('Error states', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('single-character query does not cause a 5xx error', async ({ page }) => {
    // Near-empty input: a single character — should either be blocked by the
    // UI or handled gracefully by the backend (no 500 / unhandled exception)
    const errors: number[] = [];
    page.on('response', resp => {
      if (resp.url().includes('/chat') && resp.status() >= 500) {
        errors.push(resp.status());
      }
    });

    await page.fill('textarea[placeholder="输入你的问题..."]', 'A');
    const sendBtn = page.locator('button:has-text("发送")');
    if (!await sendBtn.isDisabled()) {
      await sendBtn.click();
      await page.waitForTimeout(5_000);
    }

    expect(errors.length).toBe(0);
    // App layout must remain intact
    await expect(page.locator('textarea[placeholder="输入你的问题..."]')).toBeVisible();
  });

  test('textarea respects maxlength="2000"', async ({ page }) => {
    const over2000 = 'x'.repeat(2100);
    await page.fill('textarea[placeholder="输入你的问题..."]', over2000);
    const value = await page.inputValue('textarea[placeholder="输入你的问题..."]');
    expect(value.length).toBeLessThanOrEqual(2000);
  });

  test('special characters in query do not crash the UI', async ({ page }) => {
    const specialQuery = '<script>alert(1)</script> & "quotes" \' \\backslash';
    await page.fill('textarea[placeholder="输入你的问题..."]', specialQuery);
    const sendBtn = page.locator('button:has-text("发送")');
    if (!await sendBtn.isDisabled()) {
      await sendBtn.click();
      await page.waitForTimeout(5_000);
    }
    // No JS alert dialog should have appeared; the page must still be interactive
    await expect(page.locator('textarea[placeholder="输入你的问题..."]')).toBeVisible();
  });

  test('Chinese-English mixed query is accepted without error', async ({ page }) => {
    const mixedQuery = 'How many reps? 我应该做多少组深蹲? What about rest time?';
    await page.fill('textarea[placeholder="输入你的问题..."]', mixedQuery);
    const sendBtn = page.locator('button:has-text("发送")');
    await expect(sendBtn).not.toBeDisabled({ timeout: 3_000 });

    const errors: number[] = [];
    page.on('response', resp => {
      if (resp.url().includes('/chat') && resp.status() >= 500) errors.push(resp.status());
    });
    await sendBtn.click();
    await page.waitForTimeout(5_000);
    expect(errors.length).toBe(0);
  });

  test('app remains functional after a sequence of rapid interactions', async ({ page }) => {
    // Type and clear twice, then submit a valid question
    const textarea = page.locator('textarea[placeholder="输入你的问题..."]');
    await textarea.fill('first attempt');
    await textarea.clear();
    await textarea.fill('second attempt');
    await textarea.clear();
    await textarea.fill('What is protein synthesis?');

    await page.click('button:has-text("发送")');
    await page.waitForTimeout(3_000);

    // Layout still intact
    await expect(page.locator('h1:has-text("FitCoach AI")')).toBeVisible();
    await expect(page.locator('h2:has-text("文档管理")')).toBeVisible();
  });
});
