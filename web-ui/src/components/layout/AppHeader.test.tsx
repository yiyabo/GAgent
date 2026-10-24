import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useLanguageStore } from '@/i18n/language';

import AppHeader from './AppHeader';

// The header asks the server whether pipelines are on; answer here instead.
const pipelines = vi.hoisted(() => ({ enabled: false }));
vi.mock('@hooks/usePipelines', () => ({ usePipelinesEnabled: () => pipelines.enabled }));

const Where = () => <div data-testid="where">{useLocation().pathname}</div>;

function renderAt(path: string) {
  return render(
    <AntdApp>
      <MemoryRouter initialEntries={[path]}>
        <AppHeader />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </MemoryRouter>
    </AntdApp>,
  );
}

describe('AppHeader navigation between chat and extensions', () => {
  it('goes from the chat page to extensions', () => {
    renderAt('/chat');
    expect(screen.queryByRole('button', { name: '聊天' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '扩展' }));
    expect(screen.getByTestId('where').textContent).toBe('/extensions');
  });

  it('goes back to chat from the extensions page, by button or by the logo', () => {
    const { unmount } = renderAt('/extensions');
    fireEvent.click(screen.getByRole('button', { name: '聊天' }));
    expect(screen.getByTestId('where').textContent).toBe('/chat');
    unmount();

    renderAt('/extensions');
    fireEvent.click(screen.getByRole('link', { name: 'Phage-Agent 首页' }));
    expect(screen.getByTestId('where').textContent).toBe('/chat');
  });
});

describe('AppHeader pipelines entry', () => {
  afterEach(() => {
    pipelines.enabled = false;
  });

  it('is hidden while the server has pipelines switched off', () => {
    renderAt('/chat');
    expect(screen.queryByRole('button', { name: '流水线' })).toBeNull();
  });

  it('opens the pipelines page when the server has them on', () => {
    pipelines.enabled = true;
    renderAt('/chat');
    fireEvent.click(screen.getByRole('button', { name: '流水线' }));
    expect(screen.getByTestId('where').textContent).toBe('/pipelines');
  });
});

describe('AppHeader language', () => {
  afterEach(() => useLanguageStore.setState({ language: 'zh-CN' }));

  it('shows its labels in English, then in Chinese, when the language changes', async () => {
    useLanguageStore.setState({ language: 'en' });
    renderAt('/extensions');
    expect(screen.getByRole('button', { name: 'Extensions' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chat' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Phage-Agent home' })).toBeInTheDocument();

    useLanguageStore.getState().setLanguage('zh-CN');
    expect(await screen.findByRole('button', { name: '扩展' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '聊天' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Phage-Agent 首页' })).toBeInTheDocument();
  });

  it('writes <html lang> with the language', async () => {
    useLanguageStore.getState().setLanguage('en');
    expect(document.documentElement.getAttribute('lang')).toBe('en');
    useLanguageStore.getState().setLanguage('zh-CN');
    expect(document.documentElement.getAttribute('lang')).toBe('zh-CN');
  });
});
