import React, { useMemo, useState } from 'react';
import { Input, Empty } from 'antd';
import { CheckOutlined, CloseOutlined, LoadingOutlined, SearchOutlined, LockOutlined, RightOutlined, DownOutlined } from '@ant-design/icons';
import type { PlanTaskNode } from '@/types';
import { buildTaskOutline, statusLabel, taskStatus } from './model';

export const TaskStatusIcon: React.FC<{ status: string }> = ({ status }) => (
  <span className={`pw-status-icon pw-status-${status}`} aria-hidden="true">
    {['completed', 'succeeded'].includes(status) ? <CheckOutlined />
      : ['running', 'in_progress'].includes(status) ? <LoadingOutlined />
      : ['failed', 'error'].includes(status) ? <CloseOutlined />
      : status === 'blocked' ? <LockOutlined /> : <span className="pw-status-ring" />}
  </span>
);

interface Props { tasks: PlanTaskNode[]; selectedTaskId: number | null; onSelect: (id: number) => void }
const TaskOutline: React.FC<Props> = ({ tasks, selectedTaskId, onSelect }) => {
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const rows = useMemo(() => buildTaskOutline(tasks), [tasks]);
  const visible = useMemo(() => {
    const result = [] as typeof rows;
    let hiddenBelow: number | null = null;
    for (const row of rows) {
      if (query.trim()) {
        if (`${row.task.name} ${row.task.id}`.toLowerCase().includes(query.trim().toLowerCase())) result.push(row);
        continue;
      }
      if (hiddenBelow != null && row.depth > hiddenBelow) continue;
      hiddenBelow = null;
      result.push(row);
      if (collapsed.has(row.task.id)) hiddenBelow = row.depth;
    }
    return result;
  }, [rows, collapsed, query]);
  return (
    <aside className="pw-outline" aria-label="计划任务列表">
      <div className="pw-outline-heading"><span>任务</span><span className="pw-muted">{tasks.length}</span></div>
      <Input className="pw-task-search" size="small" prefix={<SearchOutlined />} value={query}
        placeholder="查找任务" aria-label="查找任务" allowClear onChange={(event) => setQuery(event.target.value)} />
      <div className="pw-task-list">
        {visible.map(({ task, depth, hasChildren }) => {
          const status = taskStatus(task);
          return <div key={task.id} className={`pw-task-row ${selectedTaskId === task.id ? 'is-selected' : ''}`}
            style={{ '--task-depth': Math.min(depth, 5) } as React.CSSProperties}>
            {hasChildren ? <button className="pw-tree-toggle" aria-label={`${collapsed.has(task.id) ? '展开' : '折叠'} ${task.name}`}
              onClick={() => setCollapsed((old) => { const next = new Set(old); next.has(task.id) ? next.delete(task.id) : next.add(task.id); return next; })}>
              {collapsed.has(task.id) ? <RightOutlined /> : <DownOutlined />}
            </button> : <span className="pw-tree-toggle-spacer" />}
            <button className="pw-task-select" aria-current={selectedTaskId === task.id ? 'step' : undefined}
              onClick={() => onSelect(task.id)} title={task.name}>
              <TaskStatusIcon status={status} />
              <span className="pw-task-copy"><span className="pw-task-name">{task.short_name || task.name}</span>
                <span className="pw-task-meta">#{task.id} · {statusLabel(status)}{task.freshness === 'stale' ? ' · 待更新' : ''}</span>
              </span>
            </button>
          </div>;
        })}
        {!visible.length && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的任务" />}
      </div>
    </aside>
  );
};
export default TaskOutline;
