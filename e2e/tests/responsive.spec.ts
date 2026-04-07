/**
 * Layer 5 — Responsive layout
 *
 * Verifies that the key UI elements remain visible and functional at the two
 * most common viewport breakpoints used by FitCoach users.
 *
 * NOTE: Only Chromium with two viewport sizes is tested in Phase 5.
 * Firefox / WebKit coverage can be added to playwright.config.ts once this
 * suite is stable (see TestPlan §5 Layer 5 "Known browser coverage gap").
 */
import { test, expect } from '@playwright/test';
import { login } from './helpers';

const VIEWPORTS = [
  { name: 'desktop (1280×800)',  width: 1280, height: 800 },
  { name: 'mobile  (375×812)',   width: 375,  height: 812 },
];

for (const vp of VIEWPORTS) {
  test.describe(`Responsive — ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test('login page is usable', async ({ page }) => {
      await page.goto('/');
      await expect(page.locator('input[placeholder="请输入邮箱"]')).toBeVisible();
      await expect(page.locator('input[type="password"]')).toBeVisible();
      await expect(page.locator('button[type="submit"]:has-text("登录")')).toBeVisible();
    });

    test('main app layout loads without overflow', async ({ page }) => {
      await login(page);
      // Core landmarks must be visible at this viewport
      await expect(page.locator('h1:has-text("FitCoach AI")')).toBeVisible();
      await expect(page.locator('textarea[placeholder="输入你的问题..."]')).toBeVisible();

      // No horizontal scrollbar: body width should not exceed viewport width
      const hasHorizontalScroll = await page.evaluate(() => {
        return document.documentElement.scrollWidth > document.documentElement.clientWidth;
      });
      expect(hasHorizontalScroll).toBe(false);
    });

    test('send button is reachable without scrolling', async ({ page }) => {
      // KNOWN: mobile (375px) layout overflows — sidebar + chat area together
      // exceed the viewport width, pushing the send button off-screen.
      // See KNOWN_ISSUES.md §ISSUE-004.  Marked fixme until the frontend adds
      // a responsive breakpoint for the sidebar.
      if (vp.width <= 375) test.fixme(true, 'Mobile layout overflow — see KNOWN_ISSUES.md §ISSUE-004');

      await login(page);
      const sendBtn = page.locator('button:has-text("发送")');
      await expect(sendBtn).toBeVisible();
      const box = await sendBtn.boundingBox();
      expect(box).not.toBeNull();
      expect(box!.x + box!.width).toBeLessThanOrEqual(vp.width + 5);  // 5px tolerance
    });
  });
}
