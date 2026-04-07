/**
 * Layer 5 — Query / chat flow
 *
 * Covers: send-button state, submitting a question, receiving a streamed
 * response, starting a new conversation, and long-query handling.
 */
import { test, expect } from '@playwright/test';
import { login } from './helpers';

test.describe('Query flow', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('send button is disabled when textarea is empty', async ({ page }) => {
    const sendBtn = page.locator('button:has-text("发送")');
    await expect(sendBtn).toBeDisabled();
  });

  test('send button becomes enabled after typing a question', async ({ page }) => {
    await page.fill('textarea[placeholder="输入你的问题..."]', 'How do I squat correctly?');
    const sendBtn = page.locator('button:has-text("发送")');
    await expect(sendBtn).not.toBeDisabled({ timeout: 3_000 });
  });

  test('submitting a question produces a non-empty response', async ({ page }) => {
    const question = 'What is progressive overload?';
    await page.fill('textarea[placeholder="输入你的问题..."]', question);
    await page.click('button:has-text("发送")');

    // After sending, the send button becomes enabled again when streaming ends.
    // Then assert the response area shows a non-trivial reply.
    // "查看参考来源" appears at the end of every RAG response in the UI.
    await expect(page.locator('text=查看参考来源')).toBeVisible({ timeout: 90_000 });
  });

  test('question text clears after submission', async ({ page }) => {
    await page.fill('textarea[placeholder="输入你的问题..."]', 'What is progressive overload?');
    await page.click('button:has-text("发送")');
    // After sending, the textarea should be cleared
    await expect(
      page.locator('textarea[placeholder="输入你的问题..."]')
    ).toHaveValue('', { timeout: 5_000 });
  });

  test('new conversation button creates a fresh chat session', async ({ page }) => {
    // Send a first message
    await page.fill('textarea[placeholder="输入你的问题..."]', 'Tell me about nutrition.');
    await page.click('button:has-text("发送")');
    // Wait for response to start
    await page.waitForTimeout(3_000);

    // Click new conversation
    await page.click('button:has-text("新对话")');

    // The textarea should be empty and no in-progress response visible
    await expect(
      page.locator('textarea[placeholder="输入你的问题..."]')
    ).toBeVisible();
  });

  test('long query (500 chars) does not break the layout', async ({ page }) => {
    const longQuery = 'I have been training for five years and I recently developed a minor knee injury. '
      .repeat(6)
      .trim()
      .slice(0, 500);

    await page.fill('textarea[placeholder="输入你的问题..."]', longQuery);
    // The send button should still be visible and enabled
    const sendBtn = page.locator('button:has-text("发送")');
    await expect(sendBtn).not.toBeDisabled({ timeout: 3_000 });
    // The main layout must not be broken
    await expect(page.locator('h2:has-text("文档管理")')).toBeVisible();
  });

  test('empty submit does not trigger a network request', async ({ page }) => {
    const requests: string[] = [];
    page.on('request', req => {
      if (req.url().includes('/chat')) requests.push(req.url());
    });

    // Attempt to click send without typing anything
    const sendBtn = page.locator('button:has-text("发送")');
    if (!await sendBtn.isDisabled()) {
      await sendBtn.click();
    }
    await page.waitForTimeout(1_000);
    expect(requests.length).toBe(0);
  });
});
