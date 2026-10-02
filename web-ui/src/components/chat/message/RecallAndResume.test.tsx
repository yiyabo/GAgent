import React from 'react';
import { act, fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
const { resume, info } = vi.hoisted(() => ({ resume:vi.fn(), info:vi.fn() }));
vi.mock('@store/chat', () => ({ useChatStore:(select:any)=>select({resumeChatRun:resume}) }));
vi.mock('antd', () => ({ message:{info}, Button:({children,loading,icon,...props}:any)=><button {...props}>{children}</button> }));
vi.mock('@ant-design/icons',()=>({PlayCircleOutlined:()=>null}));
import ResumeRunAction from './ResumeRunAction';
import RecallReferences from './RecallReferences';
beforeEach(()=>{vi.clearAllMocks();resume.mockResolvedValue(undefined);});
afterEach(cleanup);
describe('recall evidence and continuation controls',()=>{
  it('shows sources as historical evidence including failed status',()=>{
    render(<RecallReferences context={{memories:[{id:'note',scope:'project',content:'Saved note',created_at:'2026-10-01'}],history:[{message_id:19,session_id:'a',role:'assistant',status:'failed',content:'Historical claim',created_at:'2026-09-30'}]}}/>);
    expect(screen.getByText('参考记忆与历史（2 条）')).toBeTruthy();
    expect(screen.getByText(/消息 #19.*failed/)).toBeTruthy();
    expect(screen.getByText('Historical claim')).toBeTruthy();
  });
  it('only displays continuation for failed/cancelled durable runs',()=>{
    const {rerender}=render(<ResumeRunAction runId="parent" sessionId="a" status="completed"/>);
    expect(screen.queryByRole('button')).toBeNull();
    rerender(<ResumeRunAction runId="parent" sessionId="a" status="failed"/>);
    expect(screen.getByRole('button',{name:'继续此任务'})).toBeTruthy();
    rerender(<ResumeRunAction sessionId="a" status="failed"/>);
    expect(screen.queryByRole('button')).toBeNull();
  });
  it('dispatches once while pending and displays the explicit refusal reason',async()=>{
    let reject!: (error:Error)=>void;
    resume.mockReturnValue(new Promise((_,r)=>reject=r));
    render(<ResumeRunAction runId="parent" sessionId="a" status="cancelled"/>);
    const button=screen.getByRole('button');fireEvent.click(button);fireEvent.click(button);
    expect(resume).toHaveBeenCalledTimes(1);expect(resume).toHaveBeenCalledWith('parent','a');
    await act(async()=>reject(new Error('先核对写入结果')));
    expect(info).toHaveBeenCalledWith('先核对写入结果');
  });
});
