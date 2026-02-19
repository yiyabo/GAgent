import React from 'react';

interface DAGStatsBarProps {
  stats: {
    total: number;
    pending: number;
    running: number;
    completed: number;
    failed: number;
  };
}

export const DAGStatsBar: React.FC<DAGStatsBarProps> = ({ stats }) => (
  <div className="dag-stats-bar">
    <div className="dag-stats-item">
      <span className="dag-stats-count">{stats.total}</span>
      <span>总计</span>
    </div>
    {stats.pending > 0 && (
      <div className="dag-stats-item">
        <span className="dag-stats-dot pending" />
        <span>{stats.pending} 待处理</span>
      </div>
    )}
    {stats.running > 0 && (
      <div className="dag-stats-item">
        <span className="dag-stats-dot running" />
        <span>{stats.running} 运行中</span>
      </div>
    )}
    {stats.completed > 0 && (
      <div className="dag-stats-item">
        <span className="dag-stats-dot completed" />
        <span>{stats.completed} 已完成</span>
      </div>
    )}
    {stats.failed > 0 && (
      <div className="dag-stats-item">
        <span className="dag-stats-dot failed" />
        <span>{stats.failed} 失败</span>
      </div>
    )}
  </div>
);
