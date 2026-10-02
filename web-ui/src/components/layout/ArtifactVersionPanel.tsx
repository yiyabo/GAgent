import React from 'react';
import { Alert,Button,Drawer,List,Space,Tag } from 'antd';
import { artifactVersionsApi,ArtifactVersions } from '@/api/artifactVersions';
import { buildArtifactFileUrl } from '@/api/artifacts';
export function ArtifactVersionPanel({planId,sessionId}:{planId?:number;sessionId:string|null}) {
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
      if(preview.blocked_task_ids.length)throw new Error('部分输入来源尚未确定，请先检查任务依赖。');
      if(!preview.ordered_task_ids.length){setNotice('当前结果无需更新。');return;}
      if(pending.current?.fingerprint!==preview.preview_fingerprint)pending.current={fingerprint:preview.preview_fingerprint,key:crypto.randomUUID()};
      await artifactVersionsApi.execute(planId,{...payload,preview_fingerprint:preview.preview_fingerprint,idempotency_key:pending.current.key});
      pending.current=null;
      if(version===generation.current)setNotice('更新已开始，可在任务列表查看进度。');
    } catch(error) {if(version===generation.current)setNotice(error instanceof Error?error.message:'更新失败，请刷新后重试。');}
    finally {if(version===generation.current){setBusy(false);busyRef.current=false;}}
  };
  return <>
    <Space style={{padding:'6px 12px'}}><Button size="small" onClick={()=>setOpen(true)}>历史与来源</Button><Button size="small" loading={busy} onClick={()=>void update()}>更新相关结果</Button></Space>
    <Drawer title="结果历史与来源" open={open} onClose={()=>setOpen(false)} width={480}>
      {notice&&<Alert message={notice} style={{marginBottom:12}}/>}
      <List dataSource={data.versions} renderItem={item=><List.Item key={item.artifact_version_id}>
        <div><Space><span>{item.alias}</span><Tag>{data.current_artifacts[item.alias]?.artifact_version_id===item.artifact_version_id?({fresh:'当前',stale:'待更新',unknown:'来源未追踪',reconciling:'同步中'}[data.current_artifacts[item.alias]?.freshness||'unknown']):'历史版本'}</Tag><Tag>{item.validated?'已通过声明检查':'尚未核验'}</Tag></Space>
          <p>{new Date(item.created_at*1000).toLocaleString()} · {item.producer_task_id?`任务 #${item.producer_task_id}`:'历史导入'}</p>
          <p>方法：{item.binding?.instruction||'历史方法未记录'}</p>
          <p>关联输入：{Object.keys(item.binding?.inputs||{}).join('、')||'输入来源尚未追踪'}</p>
          <a href={buildArtifactFileUrl(sessionId,item.path)} target="_blank" rel="noreferrer">查看此版本文件</a>
        </div>
      </List.Item>}/>
      {data.next_cursor!=null&&<Button onClick={()=>{const version=generation.current;void artifactVersionsApi.list(planId,undefined,data.next_cursor!).then(next=>{if(version===generation.current)setData({...next,versions:[...data.versions,...next.versions]});});}}>加载更多版本</Button>}
    </Drawer>
    {notice&&!open&&<Alert message={notice} style={{margin:'4px 12px'}}/>}
  </>;
}
