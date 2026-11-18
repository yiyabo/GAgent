import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { MemoryEntry } from '@/types';

vi.mock('@api/memory', () => ({
  memoryApi: {
    listMemories: vi.fn(),
    updateMemory: vi.fn(),
    deleteMemory: vi.fn(),
    saveMemory: vi.fn(),
  },
}));

import { memoryApi } from '@api/memory';
import { useLanguageStore } from '@/i18n/language';
import MemoryGovernanceDrawer from './MemoryGovernanceDrawer';

const api = vi.mocked(memoryApi);

const entry = (overrides: Partial<MemoryEntry>): MemoryEntry => ({
  id: 'm1',
  content: '用户偏好用 MAFFT 做多序列比对',
  memory_type: 'knowledge',
  importance: 'high',
  keywords: ['MAFFT'],
  tags: ['phylo'],
  context: 'General',
  created_at: '2026-11-17T10:00:00Z',
  last_accessed: null,
  retrieval_count: 3,
  embedding_generated: true,
  ...overrides,
});

const FIRST = entry({});
const SECOND = entry({ id: 'm2', content: '实验数据存放在 /data3', memory_type: 'context', importance: 'low', tags: [], retrieval_count: 0 });

const page = (items: MemoryEntry[], total = items.length) => ({ items, total, limit: 50, offset: 0 });

const cards = () => screen.queryAllByTestId('memory-gov-card');

/** Open an antd Select by its aria-label and pick the option with this label. */
async function pick(selectLabel: string, optionLabel: string) {
  const combobox = screen.getByRole('combobox', { name: selectLabel });
  fireEvent.mouseDown(combobox);
  const option = await waitFor(() => {
    const found = document.querySelector<HTMLElement>(`.ant-select-item-option[title="${optionLabel}"]`);
    if (!found) throw new Error(`option ${optionLabel} not rendered`);
    return found;
  });
  fireEvent.click(option);
}

beforeEach(() => {
  vi.clearAllMocks();
  api.listMemories.mockResolvedValue(page([FIRST, SECOND]));
  api.deleteMemory.mockResolvedValue({ success: true, deleted_id: 'm1' });
  api.saveMemory.mockResolvedValue({} as never);
});

afterEach(cleanup);

describe('memory governance drawer', () => {
  it('does not fetch while closed', () => {
    render(<MemoryGovernanceDrawer open={false} onClose={() => {}} />);
    expect(api.listMemories).not.toHaveBeenCalled();
  });

  it('lists memories with their type, importance, recall count and the total', async () => {
    api.listMemories.mockResolvedValue(page([FIRST, SECOND], 2));
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);

    await screen.findByText('用户偏好用 MAFFT 做多序列比对');
    expect(api.listMemories).toHaveBeenCalledWith({ search: '', memory_type: undefined, importance: undefined, limit: 50, offset: 0 });
    expect(cards()).toHaveLength(2);

    const first = within(cards()[0]);
    expect(first.getByText('知识')).toBeTruthy();
    expect(first.getByText('高')).toBeTruthy();
    expect(first.getByText('phylo')).toBeTruthy();
    expect(first.getByText('已召回 3 次')).toBeTruthy();
    expect(within(cards()[1]).getByText('已召回 0 次')).toBeTruthy();
    expect(document.querySelector('.memory-gov-total')?.textContent).toBe('2');
    expect(screen.getByTitle('共 2 条记忆')).toBeTruthy();
  });

  it('offers more when the backend holds more than one page', async () => {
    api.listMemories
      .mockResolvedValueOnce(page([FIRST], 2))
      .mockResolvedValueOnce({ items: [SECOND], total: 2, limit: 50, offset: 1 });
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);

    fireEvent.click(await screen.findByText('加载更多'));
    await screen.findByText('实验数据存放在 /data3');
    expect(api.listMemories).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 1 }));
    expect(cards()).toHaveLength(2);
    expect(screen.queryByText('加载更多')).toBeNull();
  });

  it('sends the type, importance and search filters to the backend', async () => {
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);
    await screen.findByText('用户偏好用 MAFFT 做多序列比对');

    api.listMemories.mockResolvedValue(page([FIRST]));
    await pick('全部类型', '知识');
    await waitFor(() => expect(api.listMemories).toHaveBeenLastCalledWith(expect.objectContaining({ memory_type: 'knowledge' })));

    await pick('全部重要性', '高');
    await waitFor(() =>
      expect(api.listMemories).toHaveBeenLastCalledWith(expect.objectContaining({ memory_type: 'knowledge', importance: 'high' })),
    );

    api.listMemories.mockResolvedValue(page([]));
    fireEvent.change(screen.getByRole('searchbox', { name: '搜索记忆内容' }), { target: { value: 'MAFFT' } });
    await waitFor(() =>
      expect(api.listMemories).toHaveBeenLastCalledWith(
        expect.objectContaining({ search: 'MAFFT', memory_type: 'knowledge', importance: 'high' }),
      ),
    );
    await screen.findByText('没有符合筛选条件的记忆');
  });

  it('edits a memory and sends only the fields that changed', async () => {
    const edited = { ...FIRST, content: '用户偏好用 MAFFT --auto 做比对', importance: 'critical' as const };
    api.updateMemory.mockResolvedValue({ success: true, item: edited });
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);
    await screen.findByText('用户偏好用 MAFFT 做多序列比对');

    fireEvent.click(within(cards()[0]).getByRole('button', { name: '编辑' }));
    fireEvent.change(screen.getByLabelText('编辑记忆内容'), { target: { value: '  用户偏好用 MAFFT --auto 做比对 ' } });
    await pick('重要性', '关键');
    fireEvent.click(within(cards()[0]).getByRole('button', { name: /保\s*存/ }));

    await waitFor(() =>
      expect(api.updateMemory).toHaveBeenCalledWith('m1', { content: '用户偏好用 MAFFT --auto 做比对', importance: 'critical' }),
    );
    await screen.findByText('用户偏好用 MAFFT --auto 做比对');
    expect(within(cards()[0]).getByText('关键')).toBeTruthy();
    expect(screen.queryByLabelText('编辑记忆内容')).toBeNull();
  });

  it('closes the editor without a request when nothing changed', async () => {
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);
    await screen.findByText('用户偏好用 MAFFT 做多序列比对');

    fireEvent.click(within(cards()[0]).getByRole('button', { name: '编辑' }));
    fireEvent.click(within(cards()[0]).getByRole('button', { name: /保\s*存/ }));

    await waitFor(() => expect(screen.queryByLabelText('编辑记忆内容')).toBeNull());
    expect(api.updateMemory).not.toHaveBeenCalled();
  });

  it('deletes a memory after confirmation and lowers the total', async () => {
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);
    await screen.findByText('用户偏好用 MAFFT 做多序列比对');

    fireEvent.click(within(cards()[0]).getByRole('button', { name: '删除' }));
    await screen.findByText('删除这条记忆？此操作无法撤销。');
    const confirm = document.querySelector<HTMLElement>('.ant-popconfirm-buttons .ant-btn-primary');
    expect(confirm).not.toBeNull();
    fireEvent.click(confirm!);

    await waitFor(() => expect(api.deleteMemory).toHaveBeenCalledWith('m1'));
    await waitFor(() => expect(screen.queryByText('用户偏好用 MAFFT 做多序列比对')).toBeNull());
    expect(cards()).toHaveLength(1);
    // The badge animates between the old and new number, so wait for its title to settle on the new total.
    await screen.findByTitle('共 1 条记忆');
  });

  it('shows the empty state when there are no memories', async () => {
    api.listMemories.mockResolvedValue(page([]));
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);

    await screen.findByText('还没有记忆。可以手动添加，或开启记忆后聊天。');
    expect(cards()).toHaveLength(0);
    expect(document.querySelector('.memory-gov-total')?.textContent).toBe('0');
  });

  it('shows a loading state, then an error with a retry that refetches', async () => {
    let fail: (error: Error) => void = () => {};
    api.listMemories.mockReturnValueOnce(new Promise((_, reject) => { fail = reject; }));
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);

    expect(screen.getByTestId('memory-gov-loading')).toBeTruthy();
    fail(new Error('backend down'));
    await screen.findByText('加载记忆失败：backend down');

    fireEvent.click(screen.getByRole('button', { name: /重\s*试/ }));
    await screen.findByText('用户偏好用 MAFFT 做多序列比对');
    expect(api.listMemories).toHaveBeenCalledTimes(2);
  });

  it('adds a memory through saveMemory and reloads the list', async () => {
    api.listMemories.mockResolvedValueOnce(page([])).mockResolvedValueOnce(page([FIRST]));
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);
    await screen.findByText('还没有记忆。可以手动添加，或开启记忆后聊天。');

    fireEvent.click(screen.getByRole('button', { name: /添加记忆/ }));
    fireEvent.change(screen.getByLabelText('记忆内容'), { target: { value: '用户偏好用 MAFFT 做多序列比对' } });
    await pick('记忆类型', '经验');
    fireEvent.click(screen.getByRole('button', { name: /保\s*存/ }));

    await waitFor(() =>
      expect(api.saveMemory).toHaveBeenCalledWith({
        content: '用户偏好用 MAFFT 做多序列比对',
        memory_type: 'experience',
        importance: 'medium',
        tags: [],
        keywords: [],
      }),
    );
    await waitFor(() => expect(cards()).toHaveLength(1));
    expect(api.listMemories).toHaveBeenCalledTimes(2);
    expect(screen.queryByLabelText('记忆内容')).toBeNull();
  });
});

describe('memory governance drawer in English', () => {
  beforeEach(() => useLanguageStore.getState().setLanguage('en'));
  afterEach(() => useLanguageStore.getState().setLanguage('zh-CN'));

  it('words the recall count for one and for many', async () => {
    api.listMemories.mockResolvedValue(page([entry({ retrieval_count: 1 }), entry({ id: 'm2', retrieval_count: 5 })]));
    render(<MemoryGovernanceDrawer open onClose={() => {}} />);

    await screen.findByText('Recalled once');
    expect(screen.getByText('Recalled 5 times')).toBeTruthy();
    expect(screen.getByText('Memory management')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Add Memory/ })).toBeTruthy();
  });
});
