import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@api/chat', () => ({
  chatApi: {
    updateSession: vi.fn().mockResolvedValue({}),
    getActionStatus: vi.fn(),
  },
}));

import { chatApi } from '@api/chat';
import { handleJobUpdate, handleProgressStatus, processFinalPayload } from './streamHandlers';

const buildContext = () => {
  const assistantMessage = {
    id: 'assistant-1',
    type: 'assistant',
    content: '',
    metadata: {},
  };
  const initialSession = {
    id: 'session-1',
    session_id: 'session-1',
    plan_id: null,
    plan_title: null,
    current_task_id: null,
    current_task_name: null,
    isUserNamed: true,
    messages: [assistantMessage],
  };
  const store: any = {
    messages: [assistantMessage],
    currentSession: initialSession,
    sessions: [initialSession],
    currentPlanId: null,
    currentPlanTitle: null,
    currentTaskId: null,
    currentTaskName: null,
    currentWorkflowId: null,
    updateMessage: (messageId: string, updates: Record<string, unknown>) => {
      store.messages = store.messages.map((message: any) =>
        message.id === messageId ? { ...message, ...updates } : message
      );
      if (store.currentSession) {
        store.currentSession = { ...store.currentSession, messages: store.messages };
        store.sessions = store.sessions.map((session: any) =>
          session.id === store.currentSession.id ? store.currentSession : session
        );
      }
    },
    loadChatHistory: vi.fn().mockResolvedValue(undefined),
    autotitleSession: vi.fn().mockResolvedValue(undefined),
    setActiveRunId: vi.fn(),
    setSessionProcessing: vi.fn(),
    setCurrentWorkflowId: vi.fn((workflowId: string | null) => {
      store.currentWorkflowId = workflowId;
    }),
  };
  const get = () => store;
  const set = (updater: any) => {
    const patch = typeof updater === 'function' ? updater(store) : updater;
    Object.assign(store, patch);
  };

  return {
    store,
    ctx: {
      get,
      set,
      assistantMessageId: 'assistant-1',
      mergedMetadata: {},
      currentSession: initialSession,
      state: {
        streamedContent: '',
        lastFlushedContent: '',
        flushHandle: null,
        thinkingDeltaFlushHandle: null,
        pendingThinkingDeltas: {},
        pendingThinkingDeltaStartedAt: {},
        finalPayload: null,
        jobFinalized: false,
        isBackgroundDispatch: false,
      },
      startActionStatusPolling: vi.fn(),
      flushAnalysisText: vi.fn(),
      scheduleFlush: vi.fn(),
    },
  };
};

describe('streamHandlers session sync', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the updated session when a job completion binds a plan', async () => {
    const { store, ctx } = buildContext();

    await handleJobUpdate(ctx as any, {
      payload: {
        status: 'succeeded',
        job_id: 'job-1',
        result: {
          bound_plan_id: 55,
          plan_title: 'Plan 55',
          steps: [],
        },
      },
    });

    expect(store.currentPlanId).toBe(55);
    expect(store.currentSession.plan_id).toBe(55);
    expect(store.currentSession.plan_title).toBe('Plan 55');
    expect(chatApi.updateSession).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ plan_id: 55, plan_title: 'Plan 55' }),
    );
  });

  it('keeps the updated session when the final payload carries a plan binding', async () => {
    const { store, ctx } = buildContext();
    ctx.state.finalPayload = {
      response: 'Plan ready',
      actions: [],
      metadata: {
        status: 'completed',
        plan_id: 77,
        plan_title: 'Plan 77',
      },
    };

    await processFinalPayload(ctx as any);

    expect(store.currentPlanId).toBe(77);
    expect(store.currentSession.plan_id).toBe(77);
    expect(store.currentSession.plan_title).toBe('Plan 77');
    expect(chatApi.updateSession).toHaveBeenCalledWith(
      'session-1',
      expect.objectContaining({ plan_id: 77, plan_title: 'Plan 77' }),
    );
  });

  it('hydrates artifact gallery from final payload metadata', async () => {
    const { store, ctx } = buildContext();
    ctx.state.finalPayload = {
      response: '这里是刚才那张图片。',
      actions: [],
      metadata: {
        status: 'completed',
        artifact_gallery: [
          {
            path: 'tool_outputs/run_1/figure.png',
            display_name: 'figure.png',
            source_tool: 'code_executor',
          },
        ],
      },
    };

    await processFinalPayload(ctx as any);

    expect((store.messages[0].metadata as any).artifact_gallery).toHaveLength(1);
    expect((store.messages[0].metadata as any).artifact_gallery[0]).toMatchObject({
      path: 'tool_outputs/run_1/figure.png',
      display_name: 'figure.png',
      source_tool: 'code_executor',
      mime_family: 'image',
    });
  });

  it('preserves structured plan downgrade metadata from final payload', async () => {
    const { store, ctx } = buildContext();
    ctx.state.finalPayload = {
      response: '这里是一段文本建议。',
      actions: [],
      metadata: {
        status: 'completed',
        plan_creation_state: 'text_only',
        plan_creation_message: '本轮只生成了文本建议，未创建结构化计划。',
      },
    };

    await processFinalPayload(ctx as any);

    expect((store.messages[0].metadata as any).plan_creation_state).toBe('text_only');
    expect((store.messages[0].metadata as any).plan_creation_message).toBe(
      '本轮只生成了文本建议，未创建结构化计划。',
    );
    expect(store.currentSession.plan_id).toBeNull();
  });

  it('clears stale compact progress current state when final payload completes', async () => {
    const { store, ctx } = buildContext();

    handleProgressStatus(ctx as any, {
      phase: 'gathering',
      label: '检索资料：flow matching biomolecular generation 2024 2025 2026',
      details: 'flow matching biomolecular generation 2024 2025 2026',
      tool: 'web_search',
      status: 'active',
      iteration: 1,
    });

    handleProgressStatus(ctx as any, {
      phase: 'synthesizing',
      label: '切换为保守总结',
      tool: 'web_search',
      status: 'failed',
      iteration: 1,
    });

    ctx.state.finalPayload = {
      response: 'final answer',
      actions: [],
      metadata: {
        status: 'completed',
      },
    };

    await processFinalPayload(ctx as any);

    const metadata: any = store.messages[0].metadata;
    expect(metadata.deep_think_progress.status).toBe('completed');
    expect(metadata.deep_think_progress.current_status).toBeNull();
    expect(metadata.deep_think_progress.current_tool).toBeNull();
    expect(metadata.deep_think_progress.current_label).toBeNull();
    expect(metadata.deep_think_progress.tool_items).toHaveLength(1);
    expect(metadata.deep_think_progress.tool_items[0]).toMatchObject({
      tool: 'web_search',
      status: 'failed',
    });
  });

  it('normalizes compact progress into tool items and dedupes retries', () => {
    const { store, ctx } = buildContext();

    handleProgressStatus(ctx as any, {
      phase: 'gathering',
      label: '检索资料：AnewSampling Learning the All-Atom Equilibrium Distribution biomolecular interactions',
      details: 'AnewSampling Learning the All-Atom Equilibrium Distribution biomolecular interactions database open source data availability',
      tool: 'web_search',
      status: 'active',
      iteration: 1,
    });

    handleProgressStatus(ctx as any, {
      phase: 'gathering',
      label: '检索失败，正在重试',
      tool: 'web_search',
      status: 'retrying',
      iteration: 1,
    });

    const metadata: any = store.messages[0].metadata;
    expect(metadata.thinking_visibility).toBe('progress');
    expect(metadata.deep_think_progress.current_tool).toBe('web_search');
    expect(metadata.deep_think_progress.current_status).toBe('retrying');
    expect(metadata.deep_think_progress.tool_items).toHaveLength(1);
    expect(metadata.deep_think_progress.tool_items[0]).toMatchObject({
      tool: 'web_search',
      status: 'retrying',
    });
    expect(metadata.deep_think_progress.tool_items[0].details).toContain('AnewSampling');
    expect(metadata.deep_think_progress.history).toHaveLength(2);
  });

  it('keeps generic progress labels out of expanded notes', () => {
    const { store, ctx } = buildContext();

    handleProgressStatus(ctx as any, {
      phase: 'analyzing',
      label: '处理当前步骤',
      status: 'active',
      iteration: 1,
    });

    handleProgressStatus(ctx as any, {
      phase: 'finalizing',
      label: '根据搜索结果，关于 **AnewSampling** 论文及其数据库的开源情况如下： --- ## 论文基本信息 | 项目 | 详情 |',
      status: 'active',
      iteration: 2,
    });

    const metadata: any = store.messages[0].metadata;
    expect(metadata.deep_think_progress.current_label).toContain('AnewSampling');
    expect(metadata.deep_think_progress.current_label.length).toBeLessThanOrEqual(72);
    expect(metadata.deep_think_progress.expanded_notes).toHaveLength(1);
    expect(metadata.deep_think_progress.expanded_notes[0]).toContain('论文基本信息');
  });
});
