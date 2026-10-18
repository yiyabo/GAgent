import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it } from 'vitest';

import AppHeader from './AppHeader';

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
    fireEvent.click(screen.getByRole('link', { name: 'GAgent 首页' }));
    expect(screen.getByTestId('where').textContent).toBe('/chat');
  });
});
