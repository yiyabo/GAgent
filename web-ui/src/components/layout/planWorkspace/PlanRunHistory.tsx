import React, { useState } from 'react';
import { Alert, Button, Empty } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';
import JobLogPanel from '@components/chat/JobLogPanel';
import type { BackgroundTaskItem } from '@/types';
import { displayTime, isActiveJob, statusLabel } from './model';
import { TaskStatusIcon } from './TaskOutline';

interface Props { jobs: BackgroundTaskItem[]; planId: number; loading?: boolean; error?: string | null; onBackground: () => void }
const PlanRunHistory: React.FC<Props> = ({ jobs, planId, loading, error, onBackground }) => {
  const [picked, setPicked] = useState<string | null>(null);
  const selected = jobs.find((job) => job.job_id === picked) ?? jobs.find(isActiveJob) ?? jobs[0];
  return <div className="pw-history">
    <div className="pw-section-heading"><h3>运行记录</h3><Button size="small" onClick={onBackground}>后台任务面板</Button></div>
    <p className="pw-muted pw-small">显示当前计划最近的运行。展开记录可查看执行步骤、工具调用与完整日志。</p>
    {error && <Alert type="warning" showIcon message="运行记录暂时无法刷新" description={error} />}
    {!jobs.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? '正在读取运行记录…' : '该计划暂无运行记录'} /> : <>
      <div className="pw-run-list">
        {jobs.map((job) => <button key={job.job_id} className={`pw-run-row ${selected?.job_id === job.job_id ? 'is-selected' : ''}`}
          aria-pressed={selected?.job_id === job.job_id} onClick={() => setPicked(job.job_id)}>
          <TaskStatusIcon status={job.status} /><span className="pw-run-copy"><strong>{job.label || `运行 ${job.job_id.slice(0, 8)}`}</strong>
            <span>{statusLabel(job.status)}{job.execution_paused ? ' · 已暂停' : ''} · {displayTime(job.started_at ?? job.created_at)}</span>
          </span><ClockCircleOutlined />
        </button>)}
      </div>
      {selected && <JobLogPanel key={selected.job_id} jobId={selected.job_id} planId={planId}
        jobType={selected.job_type} targetTaskName={selected.label} />}
    </>}
  </div>;
};
export default PlanRunHistory;
