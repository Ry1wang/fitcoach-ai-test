import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,   // sequential — single live stack
  retries: 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: '../reports/e2e_html', open: 'never' }],
    ['json', { outputFile: '../reports/e2e_latest.json' }],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost',
    headless: true,
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
