import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { planTreeApi } from '@api/planTree';
import { planArtifactsApi } from '@api/planArtifacts';
import { artifactVersionsApi } from '@api/artifactVersions';
import { artifactsApi } from '@api/artifacts';
import { PlanTaskArtifacts } from './PlanTaskArtifacts';
import { artifactFile, collectPlanArtifacts } from './model';

vi.mock('@api/planArtifacts', () => ({ planArtifactsApi: { list: vi.fn() } }));
vi.mock('@api/planTree', () => ({ planTreeApi: { getPlanResults: vi.fn() } }));
vi.mock('@api/artifactVersions', () => ({ artifactVersionsApi: { list: vi.fn() } }));
vi.mock('@api/artifacts', async () => {
  const actual = await vi.importActual<typeof import('@api/artifacts')>('@api/artifacts');
  return { ...actual, artifactsApi: { listSessionArtifacts: vi.fn() } };
});
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


const entry = (task: number, name: string, session = 's1') => ({ alias:name,
  producer_task_id:task, path:`/app/runtime/${session}/artifacts/plan_1/${name}`, session_id:session });
const page = (items = [entry(1,'one.csv')], extra = {}) => ({ plan_id:1, session_id:'s1',
  schema_version:1, total:items.length, items, next_cursor:null, snapshot:'first', ...extra });

describe('PlanTaskArtifacts lightweight listing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page());
    vi.mocked(artifactVersionsApi.list).mockResolvedValue(emptyVersions);
  });
  it('uses only the lightweight API and closes preview on task switch', async () => {
    vi.mocked(planArtifactsApi.list).mockImplementation(async (_plan, task) => page([entry(task!,task===1?'one.csv':'two.csv')]));
    const view=mount();
    fireEvent.click(await screen.findByRole('button',{name:'one.csv'}));
    expect(screen.getByRole('dialog')).toHaveTextContent('s1:artifacts/plan_1/one.csv');
    expect(planTreeApi.getPlanResults).not.toHaveBeenCalled();
    expect(artifactVersionsApi.list).not.toHaveBeenCalled();
    view.rerender(<PlanTaskArtifacts planId={1} sessionId="s1" taskId={2} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(await screen.findByText('two.csv')).toBeInTheDocument();
  });
  it('shows unknown quantity on failure instead of an empty or zero inventory', async () => {
    vi.mocked(planArtifactsApi.list).mockRejectedValue(new Error('timeout'));
    mount({planId:1,sessionId:'s1',taskId:null});
    expect(await screen.findByText('数量未知')).toBeInTheDocument();
    expect(screen.getByText('任务产物记录加载失败')).toBeInTheDocument();
    expect(screen.queryByText('此计划尚未登记产物')).not.toBeInTheDocument();
    expect(screen.queryByText('0')).not.toBeInTheDocument();
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page());
    fireEvent.click(screen.getByRole('button',{name:/重\s*试/}));
    expect(await screen.findByText('one.csv')).toBeInTheDocument();
  });
  it('shows zero only after a successful empty listing', async () => {
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page([]));
    mount({planId:1,sessionId:'s1',taskId:null});
    expect(await screen.findByText('暂无已登记或声明的任务输出')).toBeInTheDocument();
    expect(screen.getByText('对应 0 个文件')).toBeInTheDocument();
  });
  it('shows only declared task outputs when no formal publication exists', async () => {
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page([], { declared_outputs: [{
      alias: 'declared:2:results/evidence.md', producer_task_id: 2, session_id: 's1',
      path: '/app/runtime/s1/raw_files/task_1/task_2/results/evidence.md',
      origin: 'declared_output', output_status: 'accepted', validated: true,
    }] }));
    mount({ planId: 1, sessionId: 's1', taskId: 2, tasks: [
      { id: 1, parent_id: null, task_type: 'root', status: 'running' },
      { id: 2, parent_id: 1, path: '/1/2', task_type: 'atomic', status: 'completed' },
    ] } as any);
    expect(await screen.findByRole('button', { name: 'evidence.md' })).toBeEnabled();
    expect(screen.getByText('对应 1 个文件')).toBeInTheDocument();
    expect(screen.getByText('执行时文件验收通过')).toBeInTheDocument();
    expect(screen.queryByText('任务工作文件')).not.toBeInTheDocument();
    expect(artifactsApi.listSessionArtifacts).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'evidence.md' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('s1:raw_files/task_1/task_2/results/evidence.md');
  });
  it('marks files from a failed task as partial outputs', async () => {
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page([], { declared_outputs: [{
      alias: 'declared:4:results/sections/01.md', producer_task_id: 4, session_id: 's1',
      path: '/app/runtime/s1/raw_files/task_1/task_4/results/sections/01.md',
      origin: 'declared_output', output_status: 'partial', validated: false,
      required_count: 5, matched_count: 1,
    }] }));
    mount({ planId: 1, sessionId: 's1', taskId: 4 });
    expect(await screen.findByText('任务未完成 · 1/5')).toBeInTheDocument();
    expect(screen.getByText('对应 1 个文件')).toBeInTheDocument();
  });
  it('retains first-page files if loading another page fails', async () => {
    vi.mocked(planArtifactsApi.list).mockImplementation(async (_p,_t,cursor) => {
      if(cursor) throw new Error('changed');
      return page([entry(1,'one.csv')],{total:2,next_cursor:'next'});
    });
    mount();fireEvent.click(await screen.findByText('加载更多产物'));
    expect(await screen.findByText('已加载 1')).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'one.csv'})).toBeEnabled();
  });
  it('pages without losing producer ownership', async () => {
    vi.mocked(planArtifactsApi.list).mockImplementation(async (_p,_t,cursor) => cursor
      ? page([entry(2,'two.csv')],{total:2}) : page([entry(1,'one.csv')],{total:2,next_cursor:'next'}));
    mount({planId:1,sessionId:'s1',taskId:null});
    fireEvent.click(await screen.findByText('加载更多产物'));
    expect(await screen.findByText('two.csv')).toBeInTheDocument();
    expect(screen.getByText('one.csv')).toBeInTheDocument();
    expect(screen.getByText('任务 #2')).toBeInTheDocument();
  });
  it('does not mix late responses from another plan/session', async () => {
    let finish:(value:any)=>void=()=>{};
    vi.mocked(planArtifactsApi.list).mockImplementation(plan => plan===1 ? new Promise(resolve=>{finish=resolve;})
      : Promise.resolve(page([entry(1,'new.csv','s2')],{plan_id:2,session_id:'s2'})));
    const view=mount();await waitFor(()=>expect(planArtifactsApi.list).toHaveBeenCalled());
    view.rerender(<PlanTaskArtifacts planId={2} sessionId="s2" taskId={1} />);
    expect(await screen.findByText('new.csv')).toBeInTheDocument();
    finish(page());await waitFor(()=>expect(screen.queryByText('one.csv')).not.toBeInTheDocument());
  });
  it('rejects a mismatched response plan', async () => {
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page([entry(1,'foreign.csv')],{plan_id:2}));
    mount();expect(await screen.findByText('数量未知')).toBeInTheDocument();
    expect(screen.queryByText('foreign.csv')).not.toBeInTheDocument();
  });
  it('does not describe recovered files as content-verified', async () => {
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page([{...entry(1,'old.csv'),content_status:'unchecked',storage_verified:true,validated:false}] as any));
    mount();expect(await screen.findByText('文件已核对 · 内容待验收')).toBeInTheDocument();
    expect(screen.queryByText('已通过声明检查')).not.toBeInTheDocument();
  });
  it('fetches version history only when requested', async () => {
    const current={...entry(1,'one.csv'),artifact_version_id:'v1',validated:true,created_at:100,origin:'execution'};
    vi.mocked(planArtifactsApi.list).mockResolvedValue(page([current]));
    vi.mocked(artifactVersionsApi.list).mockResolvedValue({...emptyVersions,schema_version:2,current_artifacts:{'one.csv':current},versions:[current]} as any);
    const view=mount();fireEvent.click(await screen.findByRole('button',{name:'one.csv 版本历史'}));
    await waitFor(()=>expect(artifactVersionsApi.list).toHaveBeenCalledWith(1,'one.csv',0));
    view.rerender(<PlanTaskArtifacts planId={1} sessionId="s1" taskId={2} />);
    expect(screen.queryByText('one.csv · 版本历史')).not.toBeInTheDocument();
  });
});
