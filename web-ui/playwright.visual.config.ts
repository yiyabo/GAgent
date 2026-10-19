import { defineConfig, devices } from '@playwright/test';

/**
 * Visual smoke config. Separate from playwright.config.ts, which drives the full-stack suite and
 * needs a live backend. This one serves the production build and answers every API call from
 * fixtures (e2e/visual/mockApi.ts), so it runs anywhere with only Node and a browser.
 *
 *   npm run test:visual
 */
export default defineConfig({
  testDir: './e2e/visual',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  workers: 2,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    // Same-origin API base so fetches reach page.route(), exactly as the gateway build does in production.
    command: 'npm run build && npx vite preview --port 4173 --host 127.0.0.1 --strictPort',
    env: { VITE_API_BASE_URL: '', VITE_WS_BASE_URL: '' },
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
