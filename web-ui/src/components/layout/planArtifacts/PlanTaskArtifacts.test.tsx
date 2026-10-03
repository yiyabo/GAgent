import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { planTreeApi } from '@api/planTree';
import { artifactVersionsApi } from '@api/artifactVersions';
import { PlanTaskArtifacts } from './PlanTaskArtifacts';
import { artifactFile, collectPlanArtifacts } from './model';

vi.mock('@api/planTree', () => ({ planTreeApi: { getPlanResults: vi.fn() } }));
vi.mock('@api/artifactVersions', () => ({ artifactVersionsApi: { list: vi.fn() } }));
vi.mock('../artifactPreview', () => ({ ArtifactPreviewModal: (props: any) => <div role="dialog">{props.sessionId}:{props.path}</div> }));

const published = (taskId: number, name: string, sid = 's1', planId = 1) => ({
  task_id: taskId,
  metadata: { published_artifacts: { [name]: {
    alias: name, path: `/app/runtime/${sid}/artifacts/plan_${planId}/${name}`, producer_task_id: taskId,
  } } },
});
const emptyVersions = { schema_version: 1, manifest_revision: 0, current_artifacts: {}, versions: [], next_cursor: null };
function mount(props = { planId: 1, sessionId: 's1', taskId: 1 }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, cacheTime: 0 } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return render(<PlanTaskArtifacts {...props} />, { wrapper });
}

describe('plan artifact ownership', () => {
  it('does not guess a current-session download for a different session path', () => {
    const file = artifactFile('report', { path: '/app/runtime/old/report.csv', deliverable_path: 'report.csv' }, 1, 'new');
    expect(file.path).toBeNull();
    expect(file.name).toBe('report.csv');
  });

  it('uses an explicitly bound source session and keeps immutable paths', () => {
    const file = artifactFile('report', { path: '/app/runtime/old/artifacts/plan_1/version_blobs/hash/report.csv', binding: { session_id: 'old' } }, 1, 'new');
    expect(file.sessionId).toBe('old');
    expect(file.path).toBe('artifacts/plan_1/version_blobs/hash/report.csv');
    expect(file.sourceType).toBe('raw');
  });

  it('does not resolve a bare relative receipt against the viewing chat session', () => {
    expect(artifactFile('report', { path: 'artifacts/plan_1/report.csv' }, 1, 'viewing-chat').path).toBeNull();
    expect(artifactFile('report', { deliverable_path: 'docs/report.csv' }, 1, 'viewing-chat').path).toBeNull();
    expect(artifactFile('report', { path: 'artifacts/plan_1/report.csv' }, 1, 'bound-store', true).path).toBe('artifacts/plan_1/report.csv');
  });

  it('can resolve a relative receipt when its own plan manifest proves the session', () => {
    const result = { task_id: 1, metadata: {
      artifact_manifest_path: '/app/runtime/actual-store/artifacts/plan_1/artifacts_manifest.json',
      published_artifacts: { report: { path: 'artifacts/plan_1/report.csv' } },
    } };
    expect(collectPlanArtifacts(1, [result], undefined, 'actual-store')[0].path).toBe('artifacts/plan_1/report.csv');
    expect(collectPlanArtifacts(1, [result], undefined, 'viewing-chat')[0].path).toBeNull();
    expect(collectPlanArtifacts(2, [result], undefined, 'actual-store')[0].path).toBeNull();
  });

  it('only accepts the published task owner and the requested plan', () => {
    const valid = published(1, 'one.csv');
    const invalidTask = published(2, 'wrong-task.csv');
    invalidTask.metadata.published_artifacts['wrong-task.csv'].producer_task_id = 1;
    const invalidPlan = { task_id: 1, metadata: { published_artifacts: { wrong: { path: 'wrong.csv', producer_plan_id: 2 } } } };
    expect(collectPlanArtifacts(1, [valid, invalidTask, invalidPlan], undefined, 's1').map(file => file.name)).toEqual(['one.csv']);
  });

  it('lets the manifest reassign current aliases without duplicating old task receipts', () => {
    const versions = { ...emptyVersions, current_artifacts: { 'one.csv': { path: 'new.csv', producer_task_id: 2 } } } as any;
    const files = collectPlanArtifacts(1, [published(1, 'one.csv')], versions, 's1');
    expect(files).toHaveLength(1);
    expect(files[0].taskId).toBe(2);
  });
});

describe('PlanTaskArtifacts', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(planTreeApi.getPlanResults).mockResolvedValue({ plan_id: 1, total: 2, items: [published(1, 'one.csv'), published(2, 'two.csv')] });
    vi.mocked(artifactVersionsApi.list).mockResolvedValue(emptyVersions);
  });

  it('shows only selected task outputs and closes its preview when switching tasks', async () => {
    const view = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'one.csv' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('s1:artifacts/plan_1/one.csv');
    expect(screen.queryByText('two.csv')).not.toBeInTheDocument();
    view.rerender(<PlanTaskArtifacts planId={1} sessionId="s1" taskId={2} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(await screen.findByText('two.csv')).toBeInTheDocument();
    expect(screen.queryByText('one.csv')).not.toBeInTheDocument();
  });

  it('does not show a late response after switching session and plan with the same task id', async () => {
    let finish: (value: any) => void = () => {};
    vi.mocked(planTreeApi.getPlanResults).mockImplementation(plan => plan === 1
      ? new Promise(resolve => { finish = resolve; })
      : Promise.resolve({ plan_id: 2, total: 1, items: [published(1, 'new.csv', 's2', 2)] }));
    const view = mount();
    await waitFor(() => expect(planTreeApi.getPlanResults).toHaveBeenCalledWith(1));
    view.rerender(<PlanTaskArtifacts planId={2} sessionId="s2" taskId={1} />);
    expect(await screen.findByText('new.csv')).toBeInTheDocument();
    finish({ plan_id: 1, total: 1, items: [published(1, 'old.csv')] });
    await waitFor(() => expect(screen.queryByText('old.csv')).not.toBeInTheDocument());
    expect(screen.getByRole('link', { name: '下载 new.csv' })).toHaveAttribute('href', expect.stringContaining('/sessions/s2/'));
  });

  it('groups plan outputs by task and leaves missing ownership unassigned', async () => {
    vi.mocked(artifactVersionsApi.list).mockResolvedValue({ ...emptyVersions, current_artifacts: { unknown: { path: 'unassigned.csv' } } } as any);
    mount({ planId: 1, sessionId: 's1', taskId: null } as any);
    expect(await screen.findByText('one.csv')).toBeInTheDocument();
    expect(await screen.findByText('two.csv')).toBeInTheDocument();
    expect(screen.getByText('任务 #1')).toBeInTheDocument();
    expect(screen.getByText('任务 #2')).toBeInTheDocument();
    expect(screen.getByText('任务归属未记录')).toBeInTheDocument();
  });

  it('shows a partial failure and keeps task receipts usable', async () => {
    vi.mocked(artifactVersionsApi.list).mockRejectedValue(new Error('service down'));
    mount();
    expect(await screen.findByText('版本与来源暂时不可用')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'one.csv' })).toBeEnabled();
  });

  it('does not treat foreign plan response as this plan outputs', async () => {
    vi.mocked(planTreeApi.getPlanResults).mockResolvedValue({ plan_id: 2, total: 1, items: [published(1, 'foreign.csv')] });
    mount();
    expect(await screen.findByText('任务产物记录加载失败')).toBeInTheDocument();
    expect(screen.queryByText('foreign.csv')).not.toBeInTheDocument();
  });

  it('loads alias history and clears it on selection change', async () => {
    const current = { alias: 'one.csv', path: '/app/runtime/s1/artifacts/plan_1/version_blobs/new/one.csv', artifact_version_id: 'new', producer_task_id: 1, validated: true, created_at: 100, origin: 'execution', freshness: 'fresh' as const };
    const older = { ...current, path: '/app/runtime/s1/artifacts/plan_1/version_blobs/old/one.csv', artifact_version_id: 'old', created_at: 50 };
    vi.mocked(artifactVersionsApi.list).mockResolvedValue({ schema_version: 2, manifest_revision: 2, current_artifacts: { 'one.csv': current }, versions: [current, older], next_cursor: null });
    const view = mount();
    fireEvent.click(await screen.findByRole('button', { name: 'one.csv 版本历史' }));
    expect(await screen.findByText('one.csv · 版本历史')).toBeInTheDocument();
    await waitFor(() => expect(artifactVersionsApi.list).toHaveBeenCalledWith(1, 'one.csv', 0));
    expect(await screen.findByText('历史版本')).toBeInTheDocument();
    view.rerender(<PlanTaskArtifacts planId={1} sessionId="s1" taskId={2} />);
    expect(screen.queryByText('one.csv · 版本历史')).not.toBeInTheDocument();
  });

  it('refreshes artifact freshness when task status stays completed but inputs become stale', async () => {
    const current = { alias: 'one.csv', path: '/app/runtime/s1/artifacts/plan_1/one.csv', artifact_version_id: 'v1', producer_task_id: 1, validated: true, created_at: 100, origin: 'execution', freshness: 'fresh' as const };
    vi.mocked(artifactVersionsApi.list).mockResolvedValue({ ...emptyVersions, schema_version: 2, current_artifacts: { 'one.csv': current }, versions: [current] });
    const task = { id: 1, name: 'Task', status: 'completed' as const, freshness: 'fresh' as const };
    const view = mount({ planId: 1, sessionId: 's1', taskId: 1, tasks: [task] } as any);
    expect(await screen.findByText('当前')).toBeInTheDocument();
    vi.mocked(artifactVersionsApi.list).mockResolvedValue({ ...emptyVersions, schema_version: 2, current_artifacts: { 'one.csv': { ...current, freshness: 'stale' } }, versions: [current] });
    view.rerender(<PlanTaskArtifacts planId={1} sessionId="s1" taskId={1} tasks={[{ ...task, freshness: 'stale', stale_reasons: ['input_version_changed:data'] }]} />);
    expect(await screen.findByText('待更新')).toBeInTheDocument();
  });

  it('uses the history response current pointer after the alias was republished', async () => {
    const old = { alias: 'report', path: '/app/runtime/s1/artifacts/plan_1/old.csv', artifact_version_id: 'v1', producer_task_id: 1, validated: true, created_at: 100, origin: 'execution' };
    const newer = { ...old, path: '/app/runtime/s1/artifacts/plan_1/new.csv', artifact_version_id: 'v2', created_at: 200 };
    vi.mocked(artifactVersionsApi.list).mockImplementation(async (_id, alias) => alias
      ? { ...emptyVersions, schema_version: 2, current_artifacts: { report: { ...newer, freshness: 'fresh' } }, versions: [newer, old] }
      : { ...emptyVersions, schema_version: 2, current_artifacts: { report: { ...old, freshness: 'fresh' } }, versions: [old] });
    mount();
    fireEvent.click(await screen.findByRole('button', { name: 'old.csv 版本历史' }));
    const newButton = await screen.findByRole('button', { name: 'new.csv' });
    expect(newButton.closest('.plan-artifact-row')).toHaveTextContent('当前');
    expect(newButton.closest('.plan-artifact-row')).not.toHaveTextContent('历史版本');
    expect(screen.getByText('历史版本').closest('.plan-artifact-row')).toHaveTextContent('old.csv');
  });
});
