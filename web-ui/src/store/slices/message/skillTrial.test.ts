import {beforeEach,describe,it,expect,vi} from 'vitest';
vi.mock('@store/auth',()=>({useAuthStore:{getState:()=>({projectId:null})}}));
vi.mock('@store/tasks',()=>({useTasksStore:{getState:()=>({})}}));
vi.mock('@api/skillLearning',()=>({skillLearningApi:{detail:vi.fn()}}));
import {skillLearningApi} from '@api/skillLearning';
import {createMessageSlice} from './index';
function fixture(){
 const s:any={id:'local',session_id:'s'};const b:any={id:'b',session_id:'b'};
 const state:any={currentSession:s,processingSessionIds:new Set()};
 const get=()=>state;const set=(p:any)=>Object.assign(state,typeof p==='function'?p(state):p);
 Object.assign(state,createMessageSlice(set as any,get,{} as any));
 state.sendMessage=vi.fn().mockResolvedValue(undefined);return {state,b};
}
beforeEach(()=>{vi.clearAllMocks();vi.mocked(skillLearningApi.detail).mockResolvedValue({id:'skill',state:'candidate',current_version:1} as any);});
describe('explicit learned skill trials',()=>{
 it('creates a fresh request with exact skill version and supplied sample',async()=>{
  const {state}=fixture();await state.trialLearnedSkill('skill','s','新样例',1);
  expect(state.sendMessage).toHaveBeenCalledWith('新样例',{learned_skill_ids:['skill'],learned_skill_versions:{skill:1},skill_trial:true});
 });
 it('rejects disabled and stale versions',async()=>{
  const {state}=fixture();vi.mocked(skillLearningApi.detail).mockResolvedValue({state:'disabled',current_version:1} as any);
  await expect(state.trialLearnedSkill('skill','s','样例',1)).rejects.toThrow('停用');
  vi.mocked(skillLearningApi.detail).mockResolvedValue({state:'candidate',current_version:2} as any);
  await expect(state.trialLearnedSkill('skill','s','样例',1)).rejects.toThrow('版本已更新');expect(state.sendMessage).not.toHaveBeenCalled();
 });
 it('rejects a session switch during preflight',async()=>{
  const {state,b}=fixture();let resolve!:(x:any)=>void;
  vi.mocked(skillLearningApi.detail).mockReturnValue(new Promise(r=>resolve=r));
  const pending=state.trialLearnedSkill('skill','s','样例',1);state.currentSession=b;resolve({state:'candidate',current_version:1});
  await expect(pending).rejects.toThrow('会话已切换');expect(state.sendMessage).not.toHaveBeenCalled();
 });
 it('does not dispatch while another run is active',async()=>{
  const {state}=fixture();state.processingSessionIds.add('s');await expect(state.trialLearnedSkill('skill','s','样例',1)).rejects.toThrow('已有任务');
  expect(skillLearningApi.detail).not.toHaveBeenCalled();
 });
});
