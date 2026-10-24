import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { Page, Route } from '@playwright/test';

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), 'fixtures');
const fixture = (name: string): string => readFileSync(join(FIXTURES, `${name}.json`), 'utf8');

/** `METHOD /path` (query ignored) -> fixture file. Responses were captured from a real backend, then scrubbed. */
const TABLE: Record<string, string> = {
  'GET /auth/me': 'auth-me',
  'GET /chat/sessions': 'sessions',
  'GET /chat/history/session_fixture_1': 'history',
  'GET /chat/sessions/session_fixture_1/runs': 'runs',
  'GET /health': 'health',
  'GET /health/llm': 'health-llm',
  'GET /plans': 'plans',
  'GET /plans/1/tree': 'plan-tree',
  'GET /plans/1/results': 'plan-results',
  'GET /plans/1/execution/summary': 'plan-summary',
  'GET /jobs/board': 'jobs-board',
  'GET /extensions/mcp': 'ext-mcp',
  'GET /extensions/skills': 'ext-skills',
  'GET /mcp/memory/stats': 'memory-stats',
  'POST /mcp/query_memory': 'query-memory',
  'GET /upload/list': 'upload-list',
  'GET /pipelines/status': 'pipelines-status',
  'GET /pipelines/node-types': 'pipelines-catalog',
  'GET /pipelines/items': 'pipelines-list',
  'GET /pipelines/pl_fixture': 'pipelines-detail',
  'GET /pipelines/pl_fixture/files': 'pipelines-files',
  'GET /pipelines/pl_fixture/runs': 'pipelines-runs',
  'GET /pipelines/runs/run_fixture': 'pipelines-run',
};

export interface MockReport {
  /** API calls the page made that no fixture covers. They get a 404, and the list is printed so a new endpoint is noticed. */
  unmocked: string[];
}

/**
 * Answers every fetch/xhr the app makes from fixtures, so the visual smoke needs no backend, no
 * network and no model. Page navigations pass through to the preview server untouched.
 */
export async function mockApi(page: Page): Promise<MockReport> {
  const report: MockReport = { unmocked: [] };
  await page.route('**/*', async (route: Route) => {
    const request = route.request();
    if (!['fetch', 'xhr'].includes(request.resourceType())) return route.continue();
    const url = new URL(request.url());
    const key = `${request.method()} ${url.pathname}`;
    const name = TABLE[key];
    if (name) return route.fulfill({ status: 200, contentType: 'application/json', body: fixture(name) });
    if (!report.unmocked.includes(key)) report.unmocked.push(key);
    return route.fulfill({ status: 404, contentType: 'application/json', body: '{"detail":"no fixture"}' });
  });
  return report;
}
