import { beforeEach, describe, expect, it, vi } from 'vitest';
vi.mock('@store/auth', () => ({ useAuthStore: { getState: () => ({ projectId: null }) } }));
vi.mock('@store/tasks', () => ({ useTasksStore: { getState: () => ({}) } }));
vi.mock('@api/chat', () => ({ chatApi: {
  getResumeInfo: vi.fn(), resumeRun: vi.fn(), getHistory: vi.fn(), getActiveRun: vi.fn(), updateSession: vi.fn(), autotitleSession: vi.fn(),
} }));
vi.mock('../../chatUtils', async (load) => {
  const actual = await load<typeof import('../../chatUtils')>();
  return { ...actual, postChatRun: vi.fn(), streamRunEvents: vi.fn() };
});
import { chatApi } from '@api/chat';
import { postChatRun, streamRunEvents } from '../../chatUtils';
import { createMessageSlice } from './index';
import { createSessionSlice } from '../createSessionSlice';
import { createUISlice } from '../createUISlice';

function storeFixture() {
  const a = { id:'local-A',session_id:'server-A',title:'A',created_at:new Date(),updated_at:new Date(),messages:[] };
  const b = { ...a,id:'local-B',session_id:'server-B',title:'B',messages:[] };
  const store: any = {sessions:[a,b],currentSession:null,uploadedFiles:[],memoryEnabled:true,relevantMemories:[],
    syncUploadedFilesFromServer:vi.fn().mockResolvedValue(undefined),clearUploadedFiles:vi.fn()};
  const get = () => store;
  const set = (patch: any) => Object.assign(store,typeof patch === 'function' ? patch(store) : patch);
  Object.assign(store,createSessionSlice(set as any,get,{} as any),createMessageSlice(set as any,get,{} as any),createUISlice(set as any,get,{} as any));
  store.sessions=[a,b]; store.setCurrentSession(a);
  return {store,a,b};
}
const ready = {run_id:'parent',session_id:'server-A',status:'failed',can_resume:true,reason_code:'ready',reason:'ready',message:'Write the original report'};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(chatApi.getResumeInfo).mockResolvedValue(ready);
  vi.mocked(chatApi.resumeRun).mockResolvedValue({run_id:'child',session_id:'server-A',resume_from_run_id:'parent'});
  vi.mocked(chatApi.updateSession).mockResolvedValue({} as any);
  vi.mocked(chatApi.autotitleSession).mockResolvedValue({} as any);
  vi.mocked(streamRunEvents).mockImplementation(async function* () {
    yield {seq:1,event:{type:'final',payload:{response:'Continued result',actions:[],metadata:{status:'completed',session_id:'server-A',unified_stream:true}}} as any};
  });
});
describe('explicit durable continuation', () => {
  it('calls resume with source/session/idempotency, streams child and preserves draft', async () => {
    const {store} = storeFixture(); store.inputText='Unsent draft';
    await store.resumeChatRun('parent','server-A');
    expect(chatApi.getResumeInfo).toHaveBeenCalledWith('parent','server-A');
    expect(chatApi.resumeRun).toHaveBeenCalledWith('parent',expect.objectContaining({session_id:'server-A',client_message_id:expect.any(String),memory_enabled:true}));
    expect(postChatRun).not.toHaveBeenCalled();
    expect(streamRunEvents).toHaveBeenCalledWith('server-A','child',-1);
    expect(store.inputText).toBe('Unsent draft');
    expect(store.messages.find((m:any)=>m.type==='assistant').metadata).toMatchObject({chat_run_id:'child',resume_from_run_id:'parent',status:'completed'});
  });
  it('retains a cancelled child as failed UI status so continuation remains available', async () => {
    const {store}=storeFixture();
    vi.mocked(streamRunEvents).mockImplementation(async function* () {
      yield {seq:1,event:{type:'final',payload:{response:'Stopped',actions:[],metadata:{status:'cancelled',session_id:'server-A',unified_stream:true}}} as any};
    });
    await store.resumeChatRun('parent','server-A');
    expect(store.messages.find((m:any)=>m.type==='assistant').metadata).toMatchObject({status:'failed',runtime_failure_status:'cancelled',chat_run_id:'child'});
  });
  it('does not turn unsupported continuation into a new ordinary request', async () => {
    const {store} = storeFixture();
    vi.mocked(chatApi.getResumeInfo).mockResolvedValue({...ready,can_resume:false,reason:'检查点不存在'});
    await expect(store.resumeChatRun('parent','server-A')).rejects.toThrow('检查点不存在');
    expect(chatApi.resumeRun).not.toHaveBeenCalled(); expect(postChatRun).not.toHaveBeenCalled(); expect(store.messages).toHaveLength(0);
  });
  it('rejects a mismatched session and a session switch during preflight', async () => {
    const {store,b} = storeFixture();
    await expect(store.resumeChatRun('parent','server-B')).rejects.toThrow('原会话');
    let resolve!: (value: any)=>void;
    vi.mocked(chatApi.getResumeInfo).mockReturnValue(new Promise(r=>resolve=r));
    const pending=store.resumeChatRun('parent','server-A'); store.setCurrentSession(b); resolve(ready);
    await expect(pending).rejects.toThrow('会话已切换'); expect(chatApi.resumeRun).not.toHaveBeenCalled();
  });
  it('fences concurrent clicks before a second continuation can start', async () => {
    const {store} = storeFixture();
    let release!: ()=>void;
    const gate = new Promise<void>(r=>release=r);
    vi.mocked(streamRunEvents).mockImplementation(async function* () {
      await gate;
      yield {seq:1,event:{type:'final',payload:{response:'Done',actions:[],metadata:{status:'completed',unified_stream:true}}} as any};
    });
    const first = store.resumeChatRun('parent','server-A');
    const second = store.resumeChatRun('parent','server-A');
    await expect(second).rejects.toThrow('已有任务');
    expect(chatApi.resumeRun).toHaveBeenCalledTimes(1); release(); await first;
  });
  it('keeps the child outcome in A when navigation selects B during streaming', async () => {
    const {store,b}=storeFixture();
    let release!: ()=>void;
    const gate=new Promise<void>(r=>release=r);
    vi.mocked(streamRunEvents).mockImplementation(async function* () {
      await gate;
      yield {seq:1,event:{type:'final',payload:{response:'A continued',actions:[],metadata:{status:'completed',session_id:'server-A',unified_stream:true}}} as any};
    });
    const pending=store.resumeChatRun('parent','server-A');
    await vi.waitFor(()=>expect(chatApi.resumeRun).toHaveBeenCalledTimes(1));
    store.setCurrentSession(b);release();await pending;
    expect(store.currentSession.id).toBe('local-B'); expect(store.messages).toEqual([]);
    expect(store.sessions[0].messages.find((m:any)=>m.type==='assistant').metadata.chat_run_id).toBe('child');
  });
});
