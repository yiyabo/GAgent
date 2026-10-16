import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
const mocked = vi.hoisted(() => ({
  apply: vi.fn(), load: vi.fn(), error: vi.fn(),
  session: { id:'a', titleSource:'user', settings:{ response_style:'general', response_length:'auto' } },
}));
vi.mock('@store/chat', () => {
  const state = () => ({ currentSession:mocked.session, responseDefaults:null, responsePreferenceSaving:new Set(),
    loadResponseDefaults:mocked.load, updateResponsePreferences:mocked.apply });
  const useChatStore = (selector:any) => selector(state());
  useChatStore.getState = state;
  return { useChatStore };
});
vi.mock('@store/auth', () => {
  const user = {user_id:'alice'};
  return {useAuthStore:(selector:any) => selector({authenticated:true,user})};
});
vi.mock('antd', async () => { const actual = await vi.importActual<any>('antd'); return {...actual, message:{error:mocked.error}}; });
import ResponseStyleSelector from './ResponseStyleSelector';
beforeEach(() => { vi.clearAllMocks(); mocked.load.mockResolvedValue(undefined); mocked.apply.mockResolvedValue(undefined); });
afterEach(cleanup);
describe('reply style selector', () => {
  it('applies a complete preference once with the explicit default option', async () => {
    render(<ResponseStyleSelector />);
    fireEvent.click(screen.getByRole('button', {name:'回复风格：通用助手，篇幅：自动'}));
    await screen.findByRole('dialog', {name:'回复偏好'});
    fireEvent.click(screen.getByRole('radio', {name:/^科研顾问/}));
    fireEvent.click(screen.getByRole('radio', {name:'简洁'}));
    fireEvent.click(screen.getByRole('checkbox', {name:'同时设为新对话默认'}));
    fireEvent.click(screen.getByRole('button', {name:/应用到此对话/}));
    await waitFor(() => expect(mocked.apply).toHaveBeenCalledWith('a', {response_style:'research_advisor',response_length:'concise'}, true));
    expect(mocked.apply).toHaveBeenCalledTimes(1);
  });
  it('Escape closes without applying and returns focus to the trigger', async () => {
    render(<ResponseStyleSelector />);
    const trigger = screen.getByRole('button', {name:'回复风格：通用助手，篇幅：自动'});
    fireEvent.click(trigger);
    const dialog = await screen.findByRole('dialog', {name:'回复偏好'});
    fireEvent.click(screen.getByRole('radio', {name:/^资深开发/}));
    fireEvent.keyDown(dialog, {key:'Escape'});
    await waitFor(() => expect(trigger.getAttribute('aria-expanded')).toBe('false'));
    expect(document.activeElement).toBe(trigger); expect(mocked.apply).not.toHaveBeenCalled();
  });
});
