import React from 'react';
import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest';
import {render,screen,fireEvent,waitFor,cleanup} from '@testing-library/react';
const {trial}=vi.hoisted(()=>({trial:vi.fn()}));
vi.mock('@store/chat',()=>({useChatStore:(select:any)=>select({trialLearnedSkill:trial})}));
vi.mock('@api/skillLearning',()=>({skillLearningApi:{getRun:vi.fn(),capture:vi.fn(),feedback:vi.fn(),list:vi.fn(),detail:vi.fn(),review:vi.fn(),edit:vi.fn()}}));
import {skillLearningApi} from '@api/skillLearning';
import SkillLearningPanel from './SkillLearningPanel';
const skill:any={id:'x',current_version:1,public_name:'learned:x:v1',state:'candidate',review_status:'pending',
 draft:{name:'json-report',description:'整理 JSON 数据',domain:'routine',when_to_use:'需要整理 JSON 时',inputs:['数据'],steps:[{instruction:'生成数据并检查',evidence_ids:['step-1']}],verification:['检查 JSON 字段'],limitations:['不证明科学正确'],pitfalls:[],keywords:['JSON']},
 evidence:{source_outputs_verified:true,run_status:'succeeded',validated_dimensions:['output_contract'],requires_human_review:true},usage:[]};
beforeEach(()=>{
 vi.clearAllMocks();trial.mockResolvedValue(undefined);
 vi.mocked(skillLearningApi.getRun).mockResolvedValue({run_id:'r',session_id:'s',run_status:'succeeded',skills:[skill]} as any);
 vi.mocked(skillLearningApi.list).mockResolvedValue({skills:[skill]});vi.mocked(skillLearningApi.detail).mockResolvedValue(skill);
 vi.mocked(skillLearningApi.capture).mockResolvedValue({} as any);vi.mocked(skillLearningApi.feedback).mockResolvedValue({} as any);
});
afterEach(cleanup);
describe('skill learning message panel',()=>{
 it('does not offer learning for an unfinished run',()=>{
  render(<SkillLearningPanel runId="r" sessionId="s" status="running"/>);expect(screen.queryByText('技能与反馈')).toBeNull();
 });
 it('shows candidate scope and sends feedback against the originating run',async()=>{
  render(<SkillLearningPanel runId="r" sessionId="s" status="completed"/>);
  fireEvent.click(screen.getByText('技能与反馈'));
  await screen.findByText('候选 · 未独立验证');expect(screen.getByText(/方法或内容还需要用户确认/)).toBeTruthy();
  fireEvent.click(screen.getByText('这次需要改进'));
  await waitFor(()=>expect(skillLearningApi.feedback).toHaveBeenCalledWith('r','s','needs_work'));
 });
 it('provides the new sample and pinned version rather than replaying the source request',async()=>{
  render(<SkillLearningPanel runId="r" sessionId="s" status="completed"/>);
  fireEvent.click(screen.getByText('技能与反馈'));
  fireEvent.click(await screen.findByText('查看、修正或试用'));
  await screen.findByLabelText('新任务试用输入');
  fireEvent.change(screen.getByLabelText('新任务试用输入'),{target:{value:'用新数据生成 JSON'}});
  fireEvent.click(screen.getByText('用新任务试用'));
  await waitFor(()=>expect(trial).toHaveBeenCalledWith('x','s','用新数据生成 JSON',1));
 });
});
