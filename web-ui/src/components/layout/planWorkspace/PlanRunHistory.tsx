import React, { useState } from 'react';
import { Alert, Button, Empty } from 'antd';
import { ClockCircleOutlined } from '@ant-design/icons';
import JobLogPanel from '@components/chat/JobLogPanel';
import type { BackgroundTaskItem } from '@/types';
import { displayTime, isActiveJob } from './model';
import { useT } from '@/i18n/useT';
import { planStatusLabel } from '@/i18n/planStatusLabels';
import { TaskStatusIcon } from './TaskOutline';

interface Props { jobs: BackgroundTaskItem[]; planId: number; loading?: boolean; error?: string | null; onBackground: () => void }
const PlanRunHistory: React.FC<Props> = ({ jobs, planId, loading, error, onBackground }) => {
  const t = useT();
  const [picked, setPicked] = useState<string | null>(null);
  const selected = jobs.find((job) => job.job_id === picked) ?? jobs.find(isActiveJob) ?? jobs[0];
  return <div className="pw-history">
    <div className="pw-section-heading"><h3>{t('planWorkspace.historyTitle')}</h3><Button size="small" onClick={onBackground}>{t('planWorkspace.backgroundPanel')}</Button></div>
    <p className="pw-muted pw-small">{t('planWorkspace.historyHint')}</p>
    {error && <Alert type="warning" showIcon message={t('planWorkspace.historyStale')} description={error} />}
    {!jobs.length ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={loading ? t('planWorkspace.readingHistory') : t('planWorkspace.noHistory')} /> : <>
      <div className="pw-run-list">
        {jobs.map((job) => <button key={job.job_id} className={`pw-run-row ${selected?.job_id === job.job_id ? 'is-selected' : ''}`}
          aria-pressed={selected?.job_id === job.job_id} onClick={() => setPicked(job.job_id)}>
          <TaskStatusIcon status={job.status} /><span className="pw-run-copy"><strong>{job.label || t('planWorkspace.runFallback', { id: job.job_id.slice(0, 8) })}</strong>
            <span>{planStatusLabel(t, job.status)}{job.execution_paused ? ` · ${planStatusLabel(t, 'paused')}` : ''} · {displayTime(job.started_at ?? job.created_at)}</span>
          </span><ClockCircleOutlined />
        </button>)}
      </div>
      {selected && <JobLogPanel compact key={selected.job_id} jobId={selected.job_id} planId={planId}
        jobType={selected.job_type} targetTaskName={selected.label} />}
    </>}
  </div>;
};
export default PlanRunHistory;
