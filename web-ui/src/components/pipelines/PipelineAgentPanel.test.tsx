import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentEvent, AgentMessage, PipelineRun } from '@api/pipelines';
import { parseAgentEvents, pipelinesApi } from '@api/pipelines';
import { useLanguageStore } from '@/i18n/language';
import PipelineAgentPanel from './PipelineAgentPanel';

vi.setConfig({ testTimeout: 30_000 });

vi.mock('@api/pipelines', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@api/pipelines')>();
  return {
    ...actual,
    pipelinesApi: {
      agentMessages: vi.fn(),
      sendToAgent: vi.fn(),
      clearAgent: vi.fn(),
      undoAgent: vi.fn(),
      answerRunRequest: vi.fn(),
      detail: vi.fn(),
    },
  };
});

const api = vi.mocked(pipelinesApi);

const assistant = (over: Partial<AgentMessage> = {}): AgentMessage => ({ id: 2, role: 'assistant', content: 'Built it.', meta: {}, ...over });

function renderPanel(over: Partial<React.ComponentProps<typeof PipelineAgentPanel>> = {}) {
  const props = {
    pipelineId: 'pl_1',
    selectedNode: null,
    prefill: null,
    beforeSend: vi.fn().mockResolvedValue(true),
    onGraph: vi.fn(),
    onBusyChange: vi.fn(),
    onRunStarted: vi.fn(),
    ...over,
  };
  render(
    <AntdApp>
      <PipelineAgentPanel {...props} />
    </AntdApp>,
  );
  return props;
}

beforeEach(() => {
  vi.clearAllMocks();
  useLanguageStore.setState({ language: 'en' });
  api.agentMessages.mockResolvedValue({ messages: [], busy: false });
});

afterEach(() => useLanguageStore.setState({ language: 'zh-CN' }));

describe('parseAgentEvents', () => {
  it('reads whole events, skips keepalives and keeps a trailing partial block', () => {
    const text = `data: {"type":"delta","text":"Hi"}\n\n: keepalive\n\ndata: {"type":"tool","name":"validate","summary":"checked","ok":true}\n\ndata: {"type":"del`;
    const { events, rest } = parseAgentEvents(text);
    expect(events.map((event) => event.type)).toEqual(['delta', 'tool']);
    expect(rest).toBe('data: {"type":"del');
  });
});

describe('the pipeline assistant panel', () => {
  it('offers starting questions on an empty conversation and sends one', async () => {
    api.sendToAgent.mockResolvedValue(undefined);
    const props = renderPanel();
    const suggestion = await screen.findByText('Which nodes do I need to compare two phage genomes?');
    fireEvent.click(suggestion);
    await waitFor(() => expect(api.sendToAgent).toHaveBeenCalled());
    expect(props.beforeSend).toHaveBeenCalled();
    expect(api.sendToAgent.mock.calls[0].slice(0, 3)).toEqual(['pl_1', 'Which nodes do I need to compare two phage genomes?', null]);
  });

  it('streams the answer, applies graph changes and shows the saved reply with its steps and undo', async () => {
    api.sendToAgent.mockImplementation(async (_id, _text, _node, onEvent) => {
      const events: AgentEvent[] = [
        { type: 'delta', text: 'Adding a genome node.' },
        { type: 'tool', name: 'add_node', summary: 'added lambda (ncbi.genome)', ok: true },
        { type: 'graph', graph: { schema: 1, nodes: { lambda: { type: 'ncbi.genome' } } }, validation: { ok: true, errors: [], warnings: [], order: [] } },
      ];
      events.forEach(onEvent);
    });
    const saved: AgentMessage[] = [
      { id: 1, role: 'user', content: 'add lambda', meta: {} },
      assistant({ content: 'Added the lambda genome.', meta: { can_undo: true, actions: [{ name: 'add_node', summary: 'added lambda (ncbi.genome)', ok: true }] } }),
    ];
    const props = renderPanel();
    const box = await screen.findByPlaceholderText(/Ask a question/);
    api.agentMessages.mockResolvedValue({ messages: saved, busy: false });
    fireEvent.change(box, { target: { value: 'add lambda' } });
    fireEvent.keyDown(box, { key: 'Enter' });

    await waitFor(() => expect(props.onGraph).toHaveBeenCalledTimes(1));
    expect(Object.keys(props.onGraph.mock.calls[0][0].nodes)).toEqual(['lambda']);
    expect(await screen.findByText('Added the lambda genome.')).toBeTruthy();
    expect(screen.getByText('Canvas steps (1)')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Undo these changes/ })).toBeTruthy();
    expect(props.onBusyChange).toHaveBeenCalledWith(true);
    await waitFor(() => expect(props.onBusyChange).toHaveBeenLastCalledWith(false));
  });

  it('tells the assistant which node is selected, until the user drops it', async () => {
    api.sendToAgent.mockResolvedValue(undefined);
    renderPanel({ selectedNode: { id: 'bp', name: 'Protein search (blastp)' } });
    const box = await screen.findByPlaceholderText(/Ask a question/);
    expect(screen.getByText('About the node: Protein search (blastp)')).toBeTruthy();
    fireEvent.change(box, { target: { value: 'what does this do?' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(api.sendToAgent).toHaveBeenCalled());
    expect(api.sendToAgent.mock.calls[0][2]).toBe('bp');
  });

  it('does not send on Shift+Enter or while a message is empty', async () => {
    renderPanel();
    const box = await screen.findByPlaceholderText(/Ask a question/);
    fireEvent.change(box, { target: { value: 'line one' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    fireEvent.change(box, { target: { value: '   ' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(api.sendToAgent).not.toHaveBeenCalled();
  });

  it('shows a run request as a card and starts the run only when the user approves', async () => {
    const run = { id: 'plr_1', version: 1, status: 'queued' } as PipelineRun;
    const request = assistant({ content: 'Ready.', meta: { run_request: { reason: 'Count the sequences.', state: 'pending' } } });
    api.agentMessages.mockResolvedValue({ messages: [{ id: 1, role: 'user', content: 'run it', meta: {} }, request], busy: false });
    api.answerRunRequest.mockResolvedValue({ state: 'approved', run });
    const props = renderPanel();
    expect(await screen.findByText('Count the sequences.')).toBeTruthy();
    expect(props.onRunStarted).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }));
    await waitFor(() => expect(props.onRunStarted).toHaveBeenCalledWith(run));
    expect(api.answerRunRequest).toHaveBeenCalledWith('pl_1', 2, 'run');
  });

  it('undoes a turn through the server and hands the restored graph back', async () => {
    const message = assistant({ meta: { can_undo: true } });
    api.agentMessages.mockResolvedValue({ messages: [message], busy: false });
    api.undoAgent.mockResolvedValue({ draft: { schema: 1, nodes: {} }, validation: { ok: true, errors: [], warnings: [], order: [] } });
    const props = renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /Undo these changes/ }));
    const confirm = await screen.findByText('Put the canvas back as it was before this answer? Edits you made since then are lost.');
    const okButton = confirm.closest('.ant-popover')?.querySelector('.ant-btn-primary') as HTMLElement;
    fireEvent.click(okButton);
    await waitFor(() => expect(api.undoAgent).toHaveBeenCalledWith('pl_1', 2));
    expect(props.onGraph).toHaveBeenCalled();
  });

  it('waits for a turn that is still running on the server and then refreshes the canvas', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      api.agentMessages.mockResolvedValueOnce({ messages: [], busy: true }).mockResolvedValue({ messages: [assistant()], busy: false });
      api.detail.mockResolvedValue({ draft: { schema: 1, nodes: { a: { type: 'seqkit.stats' } } }, validation: { ok: true, errors: [], warnings: [], order: [] } } as never);
      const props = renderPanel();
      expect(await screen.findByText('The assistant is still working on your last message…')).toBeTruthy();
      await vi.advanceTimersByTimeAsync(3000);
      await waitFor(() => expect(props.onGraph).toHaveBeenCalled());
      expect(await screen.findByText('Built it.')).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
