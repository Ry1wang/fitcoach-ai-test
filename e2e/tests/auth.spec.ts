/**
 * Layer 5 — Auth flow
 *
 * Covers: login page rendering, successful login, and logout.
 */
import { test, expect } from '@playwright/test';
import { TEST_USER, login } from './helpers';

test.describe('Auth', () => {
  test('login page renders required fields', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('input[placeholder="请输入邮箱"]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]:has-text("登录")')).toBeVisible();
  });

  test('wrong password shows error, not crash', async ({ page }) => {
    await page.goto('/');
    await page.fill('input[placeholder="请输入邮箱"]', TEST_USER.email);
    await page.fill('input[type="password"]', 'WrongPassword!');
    await page.click('button[type="submit"]:has-text("登录")');
    await page.waitForTimeout(2_000);
    // Must NOT enter the main app — 文档管理 section only appears after login
    await expect(page.locator('h2:has-text("文档管理")')).not.toBeVisible({ timeout: 3_000 });
    // An error message should be shown
    await expect(page.locator('text=Invalid credentials')).toBeVisible({ timeout: 5_000 });
    // Login form must still be present
    await expect(page.locator('button[type="submit"]:has-text("登录")')).toBeVisible();
  });

  test('successful login lands on main app', async ({ page }) => {
    await login(page);
    await expect(page.locator('h1:has-text("FitCoach AI")')).toBeVisible();
    await expect(page.locator('h2:has-text("文档管理")')).toBeVisible();
    await expect(page.locator('textarea[placeholder="输入你的问题..."]')).toBeVisible();
  });

  test('logout returns to login page', async ({ page }) => {
    await login(page);
    await page.click('button:has-text("退出登录")');
    await expect(page.locator('input[placeholder="请输入邮箱"]')).toBeVisible({ timeout: 10_000 });
  });
});
