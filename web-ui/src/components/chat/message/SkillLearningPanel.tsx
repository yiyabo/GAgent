import React, {useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,Input,Modal,Space,Tag,message} from 'antd';
import {BookOutlined} from '@ant-design/icons';
import {skillLearningApi,type LearnedSkill,type LearningRunInfo} from '@api/skillLearning';
import {useChatStore} from '@store/chat';
const labels:Record<string,string>={candidate:'候选 · 未独立验证',trial:'已试用',stable:'稳定复用',suspended:'需修订',disabled:'已停用'};

export default function SkillLearningPanel({runId,sessionId,status}:{runId?:string|null;sessionId?:string|null;status?:string}) {
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false);
  const [info,setInfo]=useState<LearningRunInfo|null>(null),[skills,setSkills]=useState<LearnedSkill[]>([]);
  const [active,setActive]=useState<LearnedSkill|null>(null),[editing,setEditing]=useState(false);
  const [draft,setDraft]=useState<LearnedSkill['draft']|null>(null),[trialQuery,setTrialQuery]=useState(''),[error,setError]=useState('');
  const pending=useRef(false),generation=useRef(0);
  const trial=useChatStore(s=>s.trialLearnedSkill);
  const terminal=['completed','failed','cancelled'].includes(status??'');
  const refresh=async()=>{
    if(!runId||!sessionId)return;
    const seq=++generation.current;
    const [r,list]=await Promise.all([skillLearningApi.getRun(runId,sessionId),skillLearningApi.list(sessionId)]);
    if(seq!==generation.current)return;
    setInfo(r);setSkills(list.skills);setError('');
  };
  useEffect(()=>{
    generation.current++;setInfo(null);setActive(null);setSkills([]);setOpen(false);
    return()=>{generation.current++;};
  },[runId,sessionId]);
  useEffect(()=>{
    if(!open)return;
    void refresh().catch(e=>setError(e instanceof Error?e.message:'读取失败'));
    const timer=window.setInterval(()=>void refresh().catch(()=>{}),5000);
    return()=>{window.clearInterval(timer);generation.current++;};
  },[open,runId,sessionId]);
  const perform=async(fn:()=>Promise<unknown>)=>{
    if(pending.current)return;
    pending.current=true;setBusy(true);
    try{await fn();await refresh();}
    catch(e){message.error(e instanceof Error?e.message:'操作失败');}
    finally{pending.current=false;setBusy(false);}
  };
  if(!runId||!sessionId||!terminal)return null;
  return <>
    <Button type="text" size="small" icon={<BookOutlined/>} onClick={()=>setOpen(true)}>技能与反馈</Button>
    <Modal title="从这次任务学习" open={open} onCancel={()=>setOpen(false)} footer={null} width={760} destroyOnClose>
      {error&&<Alert type="error" message={error}/>}
      <Space wrap style={{marginBottom:12}}>
        <Button loading={busy} onClick={()=>void perform(()=>skillLearningApi.capture(runId,sessionId))}>保存为候选技能</Button>
        <Button disabled={busy} onClick={()=>void perform(()=>skillLearningApi.feedback(runId,sessionId,'useful'))}>这次结果有用</Button>
        <Button disabled={busy} onClick={()=>void perform(()=>skillLearningApi.feedback(runId,sessionId,'needs_work'))}>这次需要改进</Button>
      </Space>
      {info?.feedback&&<div style={{marginBottom:8}}>本轮反馈：{info.feedback.rating==='useful'?'有用':'需要改进'}</div>}
      {info?.job&&<Alert style={{marginBottom:12}} type={info.job.status==='failed'?'error':'info'}
        message={info.job.status==='completed'?'候选提炼完成':info.job.status==='skipped'?'本次没有足够证据提炼通用技能':info.job.status==='failed'?'提炼失败，可查看任务记录': '正在排队或提炼候选技能'}
        description={info.job.error_code==='hourly_budget'?'本小时学习额度已用完，稍后自动重试。':undefined}/>}
      <div style={{margin:'12px 0'}}>本项目技能：候选不会自动用于普通任务，稳定技能按相关性推荐。</div>
      {skills.map(skill=><Card key={skill.id} size="small" style={{marginBottom:8}} title={<Space><span>{skill.draft.name}</span><Tag>{labels[skill.state]}</Tag><span>v{skill.current_version}</span></Space>}>
        <div>{skill.draft.description}</div>
        <div style={{fontSize:12,color:'#777',margin:'6px 0'}}>来源任务：{skill.evidence.run_status}；已检查：{skill.evidence.validated_dimensions.join('、')||'尚无程序验收'}{skill.evidence.requires_human_review&&skill.review_status!=='accepted'?'；方法或内容还需要用户确认':skill.review_status==='accepted'?'；方法已由用户确认':''}</div>
        <Button size="small" onClick={()=>void perform(async()=>{const detail=await skillLearningApi.detail(skill.id,sessionId);setActive(detail);setDraft(detail.draft);setEditing(false);setTrialQuery('');})}>查看、修正或试用</Button>
      </Card>)}
      {!skills.length&&!error&&<div style={{color:'#777'}}>暂时没有候选技能。可以主动保存这次任务的方法。</div>}
      {active&&<Card title={`${active.draft.name} · v${active.current_version}`} style={{marginTop:12}}>
        <div>{active.draft.when_to_use}</div>
        <ol>{active.draft.steps.map((step,i)=><li key={i}>{step.instruction}</li>)}</ol>
        <div>验证方法：{active.draft.verification.join('；')}</div>
        <div>适用限制：{active.draft.limitations.join('；')}</div>
        <div>独立使用记录：{(active.usage??[]).map(item=>`${item.status==='passed'?'程序检查通过':item.status==='failed'?'检查失败':item.feedback_rating==='useful'?'用户认可，程序未完全覆盖':'尚未验证'} (${item.run_id})`).join('；')||'尚无'}</div>
        <Space wrap style={{margin:'12px 0'}}>
          <Button disabled={busy} onClick={()=>void perform(async()=>{await skillLearningApi.review(active,sessionId,'accept');setActive(await skillLearningApi.detail(active.id,sessionId));})}>确认方法适用</Button>
          <Button disabled={busy} onClick={()=>void perform(async()=>{await skillLearningApi.review(active,sessionId,'reject');setActive(await skillLearningApi.detail(active.id,sessionId));})}>方法需要修订</Button>
          <Button disabled={busy} onClick={()=>setEditing(v=>!v)}>修正此方法</Button>
          <Button disabled={busy} onClick={()=>void perform(async()=>{await skillLearningApi.review(active,sessionId,'disable');setActive(await skillLearningApi.detail(active.id,sessionId));})}>不再推荐</Button>
        </Space>
        {editing&&draft&&<div>
          <Input.TextArea value={draft.when_to_use} onChange={e=>setDraft({...draft,when_to_use:e.target.value})} aria-label="适用条件" autoSize={{minRows:2,maxRows:4}}/>
          {draft.steps.map((step,i)=><Input.TextArea key={i} value={step.instruction} aria-label={`步骤 ${i+1}`} style={{marginTop:8}}
            onChange={e=>setDraft({...draft,steps:draft.steps.map((item,j)=>j===i?{...item,instruction:e.target.value}:item)})} autoSize={{minRows:2,maxRows:4}}/>)}
          <Input.TextArea aria-label="验证方法" value={draft.verification.join('\n')} style={{marginTop:8}} onChange={e=>setDraft({...draft,verification:e.target.value.split('\n').filter(Boolean)})}/>
          <Button style={{margin:'8px 0'}} loading={busy} onClick={()=>void perform(async()=>{const updated=await skillLearningApi.edit(active,sessionId,draft);setActive(updated);setEditing(false);})}>保存新版本，重新验证</Button>
        </div>}
        <Alert type="info" showIcon message="使用另一份输入试用" description="在下面写新的任务，可通过聊天上传区选择新样例。会创建新的执行记录；旧结果重放不计作独立验证。"/>
        <Input.TextArea aria-label="新任务试用输入" value={trialQuery} onChange={e=>setTrialQuery(e.target.value)} style={{margin:'8px 0'}} autoSize={{minRows:3,maxRows:8}}/>
        <Button loading={busy} disabled={!trialQuery.trim()||['disabled','suspended'].includes(active.state)} onClick={()=>void perform(async()=>{await trial(active.id,sessionId,trialQuery.trim(),active.current_version);setOpen(false);})}>用新任务试用</Button>
      </Card>}
    </Modal>
  </>;
}
