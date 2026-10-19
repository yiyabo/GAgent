import React from 'react';
import { Alert,Button,Drawer,List,Space,Tag } from 'antd';
import { artifactVersionsApi,ArtifactVersions } from '@/api/artifactVersions';
import { buildArtifactFileUrl } from '@/api/artifacts';
import { useT } from '@/i18n/useT';
import { dateLocale } from '@/i18n/format';
export function ArtifactVersionPanel({planId,sessionId}:{planId?:number;sessionId:string|null}) {
  const t=useT();
  const [data,setData]=React.useState<ArtifactVersions>();
  const [open,setOpen]=React.useState(false);const [busy,setBusy]=React.useState(false);
  const [notice,setNotice]=React.useState('');const generation=React.useRef(0);const busyRef=React.useRef(false);const pending=React.useRef<{fingerprint:string;key:string}|null>(null);
  React.useEffect(()=>{const version=++generation.current;setData(undefined);setOpen(false);setNotice('');setBusy(false);busyRef.current=false;pending.current=null;
    if(planId&&sessionId)void artifactVersionsApi.list(planId).then(value=>{if(version===generation.current)setData(value);}).catch(()=>{});
    return()=>{generation.current++;};
  },[planId,sessionId]);
  if(!planId||!sessionId||!data||data.schema_version<2)return null;
  const update=async()=>{
    if(busyRef.current)return;busyRef.current=true;const version=generation.current;setBusy(true);setNotice('');
    try {
      const payload={expected_manifest_revision:data.manifest_revision};
      const preview=await artifactVersionsApi.preview(planId,payload);
      if(version!==generation.current)return;
      if(preview.blocked_task_ids.length)throw new Error(t('artifactVersions.blocked'));
      if(!preview.ordered_task_ids.length){setNotice(t('artifactVersions.upToDate'));return;}
      if(pending.current?.fingerprint!==preview.preview_fingerprint)pending.current={fingerprint:preview.preview_fingerprint,key:crypto.randomUUID()};
      await artifactVersionsApi.execute(planId,{...payload,preview_fingerprint:preview.preview_fingerprint,idempotency_key:pending.current.key});
      pending.current=null;
      if(version===generation.current)setNotice(t('artifactVersions.started'));
    } catch(error) {if(version===generation.current)setNotice(error instanceof Error?error.message:t('artifactVersions.failed'));}
    finally {if(version===generation.current){setBusy(false);busyRef.current=false;}}
  };
  return <>
    <Space style={{padding:'6px 12px'}}><Button size="small" onClick={()=>setOpen(true)}>{t('artifactVersions.history')}</Button><Button size="small" loading={busy} onClick={()=>void update()}>{t('artifactVersions.update')}</Button></Space>
    <Drawer title={t('artifactVersions.drawerTitle')} open={open} onClose={()=>setOpen(false)} width={480}>
      {notice&&<Alert message={notice} style={{marginBottom:12}}/>}
      <List dataSource={data.versions} renderItem={item=><List.Item key={item.artifact_version_id}>
        <div><Space><span>{item.alias}</span><Tag>{data.current_artifacts[item.alias]?.artifact_version_id===item.artifact_version_id?({fresh:t('artifactVersions.fresh'),stale:t('artifactVersions.stale'),unknown:t('artifactVersions.unknown'),reconciling:t('artifactVersions.reconciling')}[data.current_artifacts[item.alias]?.freshness||'unknown']):t('artifactVersions.older')}</Tag><Tag>{item.validated?t('artifactVersions.validated'):t('artifactVersions.unvalidated')}</Tag></Space>
          <p>{new Date(item.created_at*1000).toLocaleString(dateLocale())} · {item.producer_task_id?t('artifactVersions.task',{id:item.producer_task_id}):t('artifactVersions.imported')}</p>
          <p>{t('artifactVersions.method',{text:item.binding?.instruction||t('artifactVersions.methodMissing')})}</p>
          <p>{t('artifactVersions.inputs',{list:Object.keys(item.binding?.inputs||{}).join(t('common.itemSeparator'))||t('artifactVersions.inputsMissing')})}</p>
          <a href={buildArtifactFileUrl(sessionId,item.path)} target="_blank" rel="noreferrer">{t('artifactVersions.viewFile')}</a>
        </div>
      </List.Item>}/>
      {data.next_cursor!=null&&<Button onClick={()=>{const version=generation.current;void artifactVersionsApi.list(planId,undefined,data.next_cursor!).then(next=>{if(version===generation.current)setData({...next,versions:[...data.versions,...next.versions]});});}}>{t('artifactVersions.loadMore')}</Button>}
    </Drawer>
    {notice&&!open&&<Alert message={notice} style={{margin:'4px 12px'}}/>}
  </>;
}
