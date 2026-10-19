import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useLanguageStore } from '@/i18n/language';

const mocked = vi.hoisted(() => ({
  user: { user_id: 'alice' },
  session: { id: 'a', titleSource: 'user', settings: { response_style: 'research_advisor', response_length: 'concise' } },
  files: [{ file_id: 'f1', original_name: 'reads.fasta', file_name: 'reads.fasta', file_size: '1 KB', source: 'server' }],
}));
vi.mock('@store/chat', () => {
  const state = () => ({
    currentSession: mocked.session, responseDefaults: null, responsePreferenceSaving: new Set(),
    loadResponseDefaults: vi.fn().mockResolvedValue(undefined), updateResponsePreferences: vi.fn(),
    uploadedFiles: mocked.files, removeUploadedFile: vi.fn(), syncUploadedFilesFromServer: vi.fn().mockResolvedValue(undefined),
  });
  const useChatStore: any = (selector: any) => selector(state());
  useChatStore.getState = state;
  return { useChatStore };
});
vi.mock('@store/auth', () => ({
  useAuthStore: Object.assign((selector: any) => selector({ authenticated: true, user: mocked.user }), {
    getState: () => ({ projectId: null }),
  }),
}));

import ResponseStyleSelector from './ResponseStyleSelector';
import UploadedFilesList from './UploadedFilesList';
import RecallReferences from './message/RecallReferences';
import { FigureFineTuneModal } from './FigureFineTuneModal';

const CJK = /[\u4e00-\u9fff]/;

beforeEach(() => useLanguageStore.getState().setLanguage('en'));
afterEach(() => { cleanup(); useLanguageStore.getState().setLanguage('zh-CN'); });

describe('chat parts in English', () => {
  it('the reply style selector, its dialog and its aria labels', async () => {
    render(<ResponseStyleSelector />);
    const trigger = screen.getByRole('button', { name: 'Reply style: Research advisor, length: Concise' });
    expect(trigger.textContent).toContain('Research advisor');
    fireEvent.click(trigger);
    const dialog = await screen.findByRole('dialog', { name: 'Reply preferences' });
    expect(dialog.textContent).not.toMatch(CJK);
    expect(screen.getByRole('radio', { name: /^Product manager/ })).toBeTruthy();
    expect(screen.getByRole('checkbox', { name: 'Also make this the default for new chats' })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Apply to this chat/ })).toBeTruthy();
  });

  it('the attachment list', () => {
    const { container } = render(<UploadedFilesList />);
    expect(container.textContent).toContain('Files: 1');
    expect(container.textContent).not.toMatch(CJK);
  });

  it('recalled memory and history', () => {
    const { container } = render(<RecallReferences context={{
      memories: [{ id: 'n1', scope: 'user', content: 'Saved', created_at: '2026-10-01' }],
      history: [{ message_id: 19, session_id: 'a', role: 'assistant', status: 'failed', content: 'Claim', created_at: '2026-09-30' }],
    }} />);
    expect(screen.getByText('Referenced memory and history (2)')).toBeTruthy();
    expect(container.textContent).toContain('Memory · User · n1');
    expect(container.textContent).toContain('History · a · Message #19 · assistant · failed');
    expect(container.textContent).not.toMatch(CJK);
    expect(render(<RecallReferences context={{ unavailable: true }} />).container.textContent)
      .toBe('Memory recall is temporarily unavailable. This turn did not use any earlier material.');
  });

  it('the fine-tune dialog and the request sent to the agent are English, with the prefix the backend prompt names', async () => {
    const { useChatStore } = await import('@store/chat');
    const send = vi.fn().mockResolvedValue(undefined);
    (useChatStore as any).getState = () => ({ sendMessage: send, inputText: '', setInputText: vi.fn() });
    render(<FigureFineTuneModal visible onClose={() => {}} imageUrl="/a/b.png" imageAlt="fig.png" imagePath="results/fig.png" />);
    expect(await screen.findByText('Fine-tune figure — fig.png')).toBeTruthy();
    fireEvent.click(screen.getByText('🎨 Unify Nature colours'));
    fireEvent.click(screen.getByRole('button', { name: /Send fine-tune request/ }));
    await vi.waitFor(() => expect(send).toHaveBeenCalled());
    const sent = send.mock.calls[0][0] as string;
    expect(sent.startsWith('[Figure fine-tune] Target figure `')).toBe(true);
    expect(sent).toContain('- Source file: `results/fig.png`');
    expect(sent).toContain('Nature palette');
    expect(sent).not.toMatch(CJK);
  });

  it('the Chinese request is exactly the text the backend prompt was written for', async () => {
    useLanguageStore.getState().setLanguage('zh-CN');
    const { useChatStore } = await import('@store/chat');
    const send = vi.fn().mockResolvedValue(undefined);
    (useChatStore as any).getState = () => ({ sendMessage: send, inputText: '', setInputText: vi.fn() });
    render(<FigureFineTuneModal visible onClose={() => {}} imageUrl="/a/b.png" imageAlt="fig.png" imagePath="results/fig.png" />);
    fireEvent.change(await screen.findByPlaceholderText(/例如：将X轴字体调大/), { target: { value: '注释改成英文' } });
    fireEvent.click(screen.getByRole('button', { name: /发送微调指令/ }));
    await vi.waitFor(() => expect(send).toHaveBeenCalled());
    expect(send.mock.calls[0][0]).toBe(
      '【图表局部微调】针对目标图表 `fig.png` (fig.png):\n' +
      '- 原图文件: `results/fig.png`\n' +
      '- 修改具体要求: 注释改成英文\n' +
      '- 规范要求: 请在保持全文风格一致（统一字体字号、Nature配色、纯白背景、无多余边框）的前提下进行定向修改，并覆盖更新 `results/fig.png`（输出 PNG, 可编辑矢量 SVG, 出版级 PDF）。',
    );
  });
});
