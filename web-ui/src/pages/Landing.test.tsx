import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { useLanguageStore } from '@/i18n/language';

const navigate = vi.hoisted(() => vi.fn());
vi.mock('react-router-dom', async () => ({ ...(await vi.importActual<any>('react-router-dom')), useNavigate: () => navigate }));
vi.mock('@store/auth', () => ({ useAuthStore: () => ({ initialized: true, authenticated: false }) }));

import Landing from './Landing';

afterEach(() => { cleanup(); useLanguageStore.getState().setLanguage('zh-CN'); });

const renderLanding = () => render(<MemoryRouter><Landing /></MemoryRouter>);

describe('landing page', () => {
  it('is Chinese under the Chinese interface', () => {
    renderLanding();
    expect(screen.getByText('核心能力')).toBeTruthy();
    expect(screen.getAllByText('开始使用').length).toBe(2);
    expect(screen.getByText('自然语言交互')).toBeTruthy();
  });

  it('is English, with no Chinese left, under the English interface; the buttons still navigate', () => {
    useLanguageStore.getState().setLanguage('en');
    const { container } = renderLanding();
    expect(screen.getByText('Core capabilities')).toBeTruthy();
    expect(screen.getByText('Natural-language interaction')).toBeTruthy();
    expect(screen.getByText(/Phage-Agent\. All rights reserved\./)).toBeTruthy();
    expect(container.textContent).not.toMatch(/[\u4e00-\u9fff]/);
    fireEvent.click(screen.getAllByText('Get started')[0]);
    expect(navigate).toHaveBeenCalledWith('/login');
    fireEvent.click(screen.getByText('Sign up'));
    expect(navigate).toHaveBeenCalledWith('/register');
  });

  it('has a language switch, so a signed-out visitor can change the language', async () => {
    useLanguageStore.getState().setLanguage('en');
    renderLanding();
    fireEvent.click(screen.getByRole('button', { name: 'Language' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: '中文' }));
    expect(await screen.findByText('核心能力')).toBeTruthy();
    expect(useLanguageStore.getState().language).toBe('zh-CN');
  });
});
