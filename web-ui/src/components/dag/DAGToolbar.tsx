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
  <button className="dag-toolbar-btn" onClick={onFitView} title="">
  <ExpandOutlined />
  </button>
  <button className="dag-toolbar-btn" onClick={onResetView} title="">
  <AimOutlined />
  </button>
  <div className="dag-toolbar-divider" />
  <button className="dag-toolbar-btn" onClick={onRefresh} title="refresh">
  <ReloadOutlined />
  </button>
  <button className="dag-toolbar-btn" onClick={onExport} title="">
  <DownloadOutlined />
  </button>
  <div className="dag-toolbar-divider" />
  <button className="dag-toolbar-btn" onClick={onExitFullscreen} title="">
  <FullscreenExitOutlined />
  </button>
  </div>
);
