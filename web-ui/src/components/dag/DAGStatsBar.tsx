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
  <span></span>
  </div>
  {stats.pending > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot pending" />
  <span>{stats.pending} </span>
  </div>
  )}
  {stats.running > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot running" />
  <span>{stats.running} medium</span>
  </div>
  )}
  {stats.completed > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot completed" />
  <span>{stats.completed} completed</span>
  </div>
  )}
  {stats.failed > 0 && (
  <div className="dag-stats-item">
  <span className="dag-stats-dot failed" />
  <span>{stats.failed} failed</span>
  </div>
  )}
  </div>
);
