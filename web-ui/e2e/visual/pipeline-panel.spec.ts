import { expect, test, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { mockApi } from './mockApi';
import { scanPage } from './checks';

const runFixture = JSON.parse(readFileSync(new URL('./fixtures/pipelines-run.json', import.meta.url), 'utf8'));

async function openRun(page: Page, mode = 'light') {
  await mockApi(page);
  const run = {
    ...runFixture, status: 'succeeded',
    graph: {
      schema: 1,
      nodes: {
        lambda: runFixture.graph.nodes.lambda,
        report: { ...runFixture.graph.nodes.report, inputs: { evidence: [{ node: 'lambda', port: 'genome' }] } },
      },
      layout: { lambda: { x: 0, y: 0 }, report: { x: 300, y: 0 } },
    },
    nodes: {
      lambda: { ...runFixture.nodes.lambda, status: 'cached', outputs: { genome: { name: 'genome.fna', kind: 'file' } } },
      report: { ...runFixture.nodes.report, status: 'succeeded', outputs: { report: { name: 'report.md', kind: 'file' } } },
    },
  };
  await page.route('**/pipelines/runs/run_fixture', (route) => route.fulfill({ json: run }));
  await page.route('**/nodes/report/outputs/report/preview', (route) => route.fulfill({
    json: { kind: 'text', name: 'report.md', text: '# Completed report\n\n| Result | Value |\n| --- | --- |\n| Genome | Ready |\n\n```text\n' + 'long_path/'.repeat(30) + '\n```' },
  }));
  await page.addInitScript((value) => {
    localStorage.setItem('gagent.theme', value);
    localStorage.setItem('pipeline-panel-width', '480');
  }, mode);
  await page.goto('/chat');
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => {
    history.pushState({}, '', '/pipelines?id=pl_fixture&run=run_fixture');
    dispatchEvent(new PopStateEvent('popstate'));
  });
  await expect(page.locator('.pl-resizer')).toBeVisible();
  await expect(page.locator('.pl-node')).toHaveCount(2);
}

async function dragDivider(page: Page, delta: number) {
  const separator = page.getByRole('separator');
  const stacked = await separator.getAttribute('aria-orientation') === 'horizontal';
  const box = (await separator.boundingBox())!;
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.move(x + (stacked ? 0 : -delta), y + (stacked ? delta : 0), { steps: 8 });
  await page.mouse.up();
}

test('a 700px work area remains draggable, remembers size and preserves the graph', async ({ page }) => {
  await openRun(page);
  await page.locator('.pl-editor').evaluate((element) => { element.style.width = '702px'; element.style.flex = '1 1 auto'; });
  const separator = page.getByRole('separator');
  await expect(separator).toHaveAttribute('aria-orientation', 'vertical');
  const nodes = await page.locator('.react-flow__node').evaluateAll((items) => items.map((item) => (item as HTMLElement).style.transform));
  await separator.focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowRight');
  const before = Number(await separator.getAttribute('aria-valuenow'));
  await dragDivider(page, 40);
  await expect.poll(async () => Number(await separator.getAttribute('aria-valuenow'))).toBeGreaterThan(before + 30);
  const chosen = Number(await separator.getAttribute('aria-valuenow'));
  await page.locator('.pl-editor').evaluate((element) => { element.style.width = ''; });
  await expect.poll(async () => Number(await separator.getAttribute('aria-valuenow'))).toBe(chosen);
  expect(await page.locator('.react-flow__node').evaluateAll((items) => items.map((item) => (item as HTMLElement).style.transform))).toEqual(nodes);
  await page.getByRole('button', { name: /Collapse panel|收起侧栏/ }).click();
  await expect(page.locator('.pl-right')).toBeHidden();
  await page.getByRole('button', { name: /Open details|打开详情/ }).click();
  await expect(separator).toHaveAttribute('aria-valuenow', String(chosen));
});

for (const width of [600, 390]) {
  test(`stacked layout at ${width}px supports vertical drag and keeps the composer visible`, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await openRun(page);
    const separator = page.getByRole('separator');
    await expect(separator).toHaveAttribute('aria-orientation', 'horizontal');
    const before = Number(await separator.getAttribute('aria-valuenow'));
    await dragDivider(page, -40);
    await expect.poll(async () => Number(await separator.getAttribute('aria-valuenow'))).toBeLessThan(before - 30);
    await page.locator('.ant-segmented-item', { hasText: /Assistant|助手/ }).click();
    await expect(page.locator('.pl-agent-compose textarea')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await expect(page.locator('.pl-work-area')).not.toHaveClass(/pl-resizing/);
  });
}

for (const mode of ['light', 'dark'] as const) {
  test(`${mode}: report port is green and its inline preview fits expanded reading`, async ({ page }) => {
    await openRun(page, mode);
    const report = page.locator('.react-flow__node[data-id="report"]');
    await expect(report.locator('.pl-handle.source')).toHaveClass(/pl-handle-ready/);
    await expect(report.locator('.pl-filetype')).toContainText(['*', 'MD']);
    await expect(report.locator('.pl-handle.source')).toHaveCSS('background-color', mode === 'light' ? 'rgb(43, 110, 74)' : 'rgb(143, 209, 179)');
    await report.click();
    await expect(page.getByRole('heading', { name: 'Completed report' })).toBeVisible();
    await expect(page.getByRole('dialog')).toHaveCount(0);
    await page.getByRole('button', { name: /Expand reading|展开阅读/ }).click();
    await expect(page.locator('.pl-center')).toBeHidden();
    expect(await page.locator('.pl-right-body').first().evaluate((element) => element.scrollWidth <= element.clientWidth)).toBe(true);
    expect((await scanPage(page, mode)).map((finding) => `${finding.kind}: ${finding.detail}`)).toEqual([]);
    await page.getByRole('button', { name: /Return to canvas|返回画布/ }).click();
    await expect(page.locator('.pl-center')).toBeVisible();
  });
}
