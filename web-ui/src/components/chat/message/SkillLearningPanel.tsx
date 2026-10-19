import React, {useEffect,useRef,useState} from 'react';
import {Alert,Button,Card,Input,Modal,Space,Tag,message} from 'antd';
import {BookOutlined} from '@ant-design/icons';
import {skillLearningApi,type LearnedSkill,type LearningRunInfo} from '@api/skillLearning';
import {useChatStore} from '@store/chat';
import {useT} from '@/i18n/useT';
import type {TranslationKey} from '@/i18n/keys';
const STATE_LABELS:Record<string,TranslationKey>={candidate:'skills.candidate',trial:'skills.trial',stable:'skills.stable',suspended:'skills.suspended',disabled:'skills.disabled'};

export default function SkillLearningPanel({runId,sessionId,status}:{runId?:string|null;sessionId?:string|null;status?:string}) {
  const t=useT();
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
    void refresh().catch(e=>setError(e instanceof Error?e.message:t('skills.readFailed')));
    const timer=window.setInterval(()=>void refresh().catch(()=>{}),5000);
    return()=>{window.clearInterval(timer);generation.current++;};
  },[open,runId,sessionId]);
  const perform=async(fn:()=>Promise<unknown>)=>{
    if(pending.current)return;
    pending.current=true;setBusy(true);
    try{await fn();await refresh();}
    catch(e){message.error(e instanceof Error?e.message:t('skills.actionFailed'));}
    finally{pending.current=false;setBusy(false);}
  };
  if(!runId||!sessionId||!terminal)return null;
  return <>
    <Button type="text" size="small" icon={<BookOutlined/>} onClick={()=>setOpen(true)}>{t('skills.open')}</Button>
    <Modal title={t('skills.modalTitle')} open={open} onCancel={()=>setOpen(false)} footer={null} width={760} destroyOnClose>
      {error&&<Alert type="error" message={error}/>}
      <Space wrap style={{marginBottom:12}}>
        <Button loading={busy} onClick={()=>void perform(()=>skillLearningApi.capture(runId,sessionId))}>{t('skills.capture')}</Button>
        <Button disabled={busy} onClick={()=>void perform(()=>skillLearningApi.feedback(runId,sessionId,'useful'))}>{t('skills.useful')}</Button>
        <Button disabled={busy} onClick={()=>void perform(()=>skillLearningApi.feedback(runId,sessionId,'needs_work'))}>{t('skills.needsWork')}</Button>
      </Space>
      {info?.feedback&&<div style={{marginBottom:8}}>{t('skills.feedbackLine',{rating:info.feedback.rating==='useful'?t('skills.ratingUseful'):t('skills.ratingNeedsWork')})}</div>}
      {info?.job&&<Alert style={{marginBottom:12}} type={info.job.status==='failed'?'error':'info'}
        message={info.job.status==='completed'?t('skills.jobCompleted'):info.job.status==='skipped'?t('skills.jobSkipped'):info.job.status==='failed'?t('skills.jobFailed'):t('skills.jobQueued')}
        description={info.job.error_code==='hourly_budget'?t('skills.hourlyBudget'):undefined}/>}
      <div style={{margin:'12px 0'}}>{t('skills.scope')}</div>
      <Input.Search placeholder={t('skills.searchPlaceholder')} onSearch={query=>void perform(async()=>{const data=await skillLearningApi.list(sessionId,query);setSkills(query?data.recommended_skills??data.skills:data.skills);})} style={{marginBottom:12}}/>
      {skills.map(skill=><Card key={skill.id} size="small" style={{marginBottom:8}} title={<Space><span>{skill.draft.name}</span><Tag>{STATE_LABELS[skill.state]?t(STATE_LABELS[skill.state]):skill.state}</Tag><span>v{skill.current_version}</span></Space>}>
        <div>{skill.draft.description}</div>
        <div style={{fontSize:12,color:'var(--text-secondary)',margin:'6px 0'}}>{t(skill.evidence.requires_human_review&&skill.review_status!=='accepted'?'skills.evidenceNeedsReview':skill.review_status==='accepted'?'skills.evidenceAccepted':'skills.evidence',{status:skill.evidence.run_status,dims:skill.evidence.validated_dimensions.join(t('common.itemSeparator'))||t('skills.noChecks')})}</div>
        <Button size="small" onClick={()=>void perform(async()=>{const detail=await skillLearningApi.detail(skill.id,sessionId);setActive(detail);setDraft(detail.draft);setEditing(false);setTrialQuery('');})}>{t('skills.view')}</Button>
      </Card>)}
      {!skills.length&&!error&&<div style={{color:'var(--text-secondary)'}}>{t('skills.empty')}</div>}
      {active&&<Card title={`${active.draft.name} · v${active.current_version}`} style={{marginTop:12}}>
        <div>{active.draft.when_to_use}</div>
        <ol>{active.draft.steps.map((step,i)=><li key={i}>{step.instruction}</li>)}</ol>
        <div>{t('skills.verification',{list:active.draft.verification.join(t('common.listSeparator'))})}</div>
        <div>{t('skills.limitations',{list:active.draft.limitations.join(t('common.listSeparator'))})}</div>
        {active.version_stats&&<p>{t('skills.stats',{exposures:active.version_stats.exposures,body:active.version_stats.body_deliveries,passed:active.version_stats.independent_passed_materials,tokens:active.version_stats.co_used_run_tokens!=null?t('skills.tokensUsed',{n:active.version_stats.co_used_run_tokens}):t('skills.tokensUnknown')})}</p>}
        {!!active.similar_skills?.length&&<p>{t('skills.similar',{list:active.similar_skills.map(item=>`${item.name} v${item.version}`).join(t('common.itemSeparator'))})}</p>}
        <div>{t('skills.usage',{list:(active.usage??[]).map(item=>`${item.status==='passed'?t('skills.usagePassed'):item.status==='failed'?t('skills.usageFailed'):item.feedback_rating==='useful'?t('skills.usageApproved'):t('skills.usageUnverified')} (${item.run_id})`).join(t('common.listSeparator'))||t('skills.usageNone')})}</div>
        <Space wrap style={{margin:'12px 0'}}>
          <Button disabled={busy} onClick={()=>void perform(async()=>{await skillLearningApi.review(active,sessionId,'accept');setActive(await skillLearningApi.detail(active.id,sessionId));})}>{t('skills.accept')}</Button>
          <Button disabled={busy} onClick={()=>void perform(async()=>{await skillLearningApi.review(active,sessionId,'reject');setActive(await skillLearningApi.detail(active.id,sessionId));})}>{t('skills.reject')}</Button>
          <Button disabled={busy} onClick={()=>setEditing(v=>!v)}>{t('skills.edit')}</Button>
          <Button disabled={busy} onClick={()=>void perform(async()=>{await skillLearningApi.review(active,sessionId,'disable');setActive(await skillLearningApi.detail(active.id,sessionId));})}>{t('skills.disable')}</Button>
        </Space>
        {editing&&draft&&<div>
          <Input.TextArea value={draft.when_to_use} onChange={e=>setDraft({...draft,when_to_use:e.target.value})} aria-label={t('skills.whenToUse')} autoSize={{minRows:2,maxRows:4}}/>
          {draft.steps.map((step,i)=><Input.TextArea key={i} value={step.instruction} aria-label={t('skills.step',{n:i+1})} style={{marginTop:8}}
            onChange={e=>setDraft({...draft,steps:draft.steps.map((item,j)=>j===i?{...item,instruction:e.target.value}:item)})} autoSize={{minRows:2,maxRows:4}}/>)}
          <Input.TextArea aria-label={t('skills.verificationLabel')} value={draft.verification.join('\n')} style={{marginTop:8}} onChange={e=>setDraft({...draft,verification:e.target.value.split('\n').filter(Boolean)})}/>
          <Button style={{margin:'8px 0'}} loading={busy} onClick={()=>void perform(async()=>{const updated=await skillLearningApi.edit(active,sessionId,draft);setActive(updated);setEditing(false);})}>{t('skills.saveVersion')}</Button>
        </div>}
        <Alert type="info" showIcon message={t('skills.trialAlertTitle')} description={t('skills.trialAlertBody')}/>
        <Input.TextArea aria-label={t('skills.trialInput')} value={trialQuery} onChange={e=>setTrialQuery(e.target.value)} style={{margin:'8px 0'}} autoSize={{minRows:3,maxRows:8}}/>
        <Button loading={busy} disabled={!trialQuery.trim()||['disabled','suspended'].includes(active.state)} onClick={()=>void perform(async()=>{await trial(active.id,sessionId,trialQuery.trim(),active.current_version);setOpen(false);})}>{t('skills.trialRun')}</Button>
      </Card>}
    </Modal>
  </>;
}
