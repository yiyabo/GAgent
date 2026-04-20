import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for the Frontend E2E Suite.
 *
 * Environment variables:
 *
 *   BASE_URL        – The frontend dev server URL.
 *                     Default: http://localhost:3001
 *
 *   API_BASE_URL    – The backend API URL. Stored in process.env so test
 *                     specs and page objects can read it at runtime.
 *                     Default: http://localhost:9000
 *
 *   E2E_LLM_MODE   – Controls whether the backend uses real or mocked LLM
 *                     responses during E2E tests. Set to "mock" in CI to
 *                     avoid requiring LLM API keys for frontend tests.
 *                     Values: "real" | "mock"
 *                     Default: "real"
 *
 *   CI              – Set automatically by most CI providers. When truthy,
 *                     enables one retry on failure.
 */

// Make API_BASE_URL and E2E_LLM_MODE available to test specs via process.env
process.env.API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:9000';
process.env.E2E_LLM_MODE = process.env.E2E_LLM_MODE || 'real';

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:3001',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  /* Start the frontend dev server before running tests */
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3001',
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
});
