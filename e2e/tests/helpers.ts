import { Page } from '@playwright/test';

export const TEST_USER = {
  email: 'test_runner@example.com',
  password: 'TestPassword123!',
};

/**
 * Log in as the test user.  Registers the account first if it does not exist.
 *
 * Uses a wait-then-check pattern: after clicking login, wait for the page to
 * settle before deciding whether the login succeeded or the account is missing.
 * This avoids a race condition where `isVisible` fires while the page is still
 * mid-navigation and the login-page buttons are still briefly in the DOM.
 */
export async function login(page: Page): Promise<void> {
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  await page.fill('input[placeholder="请输入邮箱"]', TEST_USER.email);
  await page.fill('input[type="password"]', TEST_USER.password);
  await page.click('button[type="submit"]:has-text("登录")');

  // Wait until one of: main app loaded OR an error visible OR timeout
  await Promise.race([
    page.waitForSelector('h2:has-text("文档管理")', { timeout: 10_000 }),
    page.waitForSelector('text=Invalid credentials',  { timeout: 10_000 }),
    page.waitForSelector('text=not found',             { timeout: 10_000 }),
  ]).catch(() => { /* ignore — fallthrough to the isLoggedIn check */ });

  const isLoggedIn = await page.locator('h2:has-text("文档管理")').isVisible().catch(() => false);
  if (isLoggedIn) return;

  // Account may not exist yet — try registering
  const registerBtn = page.locator('button:has-text("去注册")');
  if (!await registerBtn.isVisible({ timeout: 2_000 }).catch(() => false)) {
    // Unknown state — surface the page content for debugging
    throw new Error(`Login failed and no register button found. Page URL: ${page.url()}`);
  }
  await registerBtn.click();
  await page.waitForLoadState('networkidle');

  await page.fill('input[placeholder="请输入邮箱"]', TEST_USER.email);
  await page.fill('input[type="password"]', TEST_USER.password);
  const confirmInput = page.locator('input[placeholder*="确认"]');
  if (await confirmInput.isVisible({ timeout: 1_000 }).catch(() => false)) {
    await confirmInput.fill(TEST_USER.password);
  }
  await page.click('button[type="submit"]');
  await page.waitForLoadState('networkidle');

  // After registration the app may auto-login or redirect to login
  const loggedInAfterReg = await page.locator('h2:has-text("文档管理")').isVisible({ timeout: 5_000 }).catch(() => false);
  if (!loggedInAfterReg) {
    await page.fill('input[placeholder="请输入邮箱"]', TEST_USER.email);
    await page.fill('input[type="password"]', TEST_USER.password);
    await page.click('button[type="submit"]:has-text("登录")');
  }

  await page.waitForSelector('h2:has-text("文档管理")', { timeout: 15_000 });
}
