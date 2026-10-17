import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
const mocked = vi.hoisted(() => ({send:vi.fn().mockResolvedValue(undefined)}));
vi.mock('@store/chat', () => ({useChatStore:{getState:() => ({sendMessage:mocked.send})}}));
import { SessionArtifactImage } from '../SessionArtifactImage';
afterEach(() => {cleanup(); vi.clearAllMocks();});

describe('replaced chat figure', () => {
  it('refreshes the displayed source and submits one fine tune request for the real filename', async () => {
    const old = '/api/artifacts/sessions/s1/file?path=results%2FAB_imaging_inflammation.png&v=old';
    const current = old.replace('v=old', 'v=english');
    const {rerender} = render(<SessionArtifactImage url={old} alt="AB_imaging_inflammation.png" />);
    expect(screen.getAllByAltText('AB_imaging_inflammation.png')[0]).toHaveAttribute('src', old);
    rerender(<SessionArtifactImage url={current} alt="AB_imaging_inflammation.png" />);
    expect(screen.getAllByAltText('AB_imaging_inflammation.png')[0]).toHaveAttribute('src', current);
    const dispatch = vi.spyOn(window, 'dispatchEvent');
    fireEvent.click(screen.getByRole('button', {name:/微调此图/}));
    fireEvent.change(await screen.findByPlaceholderText(/例如：将X轴字体调大/), {target:{value:'注释改成英文'}});
    fireEvent.click(screen.getByRole('button', {name:/发送微调指令/}));
    await waitFor(() => expect(mocked.send).toHaveBeenCalledTimes(1));
    const prompt = mocked.send.mock.calls[0][0];
    expect(prompt).toContain('针对目标图表 `AB_imaging_inflammation.png`');
    expect(prompt).toContain('原图文件: `results/AB_imaging_inflammation.png`');
    expect(prompt).toContain('覆盖更新 `results/AB_imaging_inflammation.png`');
    expect(prompt).not.toContain('results/file');
    expect(dispatch.mock.calls.some(([event]) => event.type === 'phage:send-chat-prompt')).toBe(false);
    dispatch.mockRestore();
  });
});
