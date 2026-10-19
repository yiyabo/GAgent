import React from 'react';
import {
  FullscreenExitOutlined,
  ExpandOutlined,
  ReloadOutlined,
  DownloadOutlined,
  AimOutlined,
} from '@ant-design/icons';

interface DAGToolbarProps {
  onFitView: () => void;
  onResetView: () => void;
  onRefresh: () => void;
  onExport: () => void;
  onExitFullscreen: () => void;
}

export const DAGToolbar: React.FC<DAGToolbarProps> = ({
  onFitView,
  onResetView,
  onRefresh,
  onExport,
  onExitFullscreen,
}) => (
  <div className="dag-toolbar">
  <button className="dag-toolbar-btn" onClick={onFitView} title="Fit to view" aria-label="Fit to view">
  <ExpandOutlined />
  </button>
  <button className="dag-toolbar-btn" onClick={onResetView} title="Reset view" aria-label="Reset view">
  <AimOutlined />
  </button>
  <div className="dag-toolbar-divider" />
  <button className="dag-toolbar-btn" onClick={onRefresh} title="Refresh" aria-label="Refresh">
  <ReloadOutlined />
  </button>
  <button className="dag-toolbar-btn" onClick={onExport} title="Export image" aria-label="Export image">
  <DownloadOutlined />
  </button>
  <div className="dag-toolbar-divider" />
  <button className="dag-toolbar-btn" onClick={onExitFullscreen} title="Exit fullscreen" aria-label="Exit fullscreen">
  <FullscreenExitOutlined />
  </button>
  </div>
);
