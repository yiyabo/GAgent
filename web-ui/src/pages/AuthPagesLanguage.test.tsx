import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { useLanguageStore } from '@/i18n/language';

vi.mock('@store/auth', () => ({
  useAuthStore: () => ({ login: vi.fn(), register: vi.fn(), loading: false, initialized: true, authenticated: false }),
}));

import Login from './Login';
import Register from './Register';

afterEach(() => { cleanup(); useLanguageStore.getState().setLanguage('zh-CN'); });

describe.each([['Login', Login], ['Register', Register]] as const)('%s page', (_name, Page) => {
  it('lets a signed-out visitor switch the language, and the page follows', async () => {
    useLanguageStore.getState().setLanguage('en');
    const { container } = render(<MemoryRouter><Page /></MemoryRouter>);
    expect(container.textContent).not.toMatch(/[\u4e00-\u9fff]/);
    fireEvent.click(screen.getByRole('button', { name: 'Language' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: '中文' }));
    await vi.waitFor(() => expect(container.textContent).toMatch(/[\u4e00-\u9fff]/));
    expect(useLanguageStore.getState().language).toBe('zh-CN');
  });
});
