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
    <button className="dag-toolbar-btn" onClick={onFitView} title="缩放适应">
      <ExpandOutlined />
    </button>
    <button className="dag-toolbar-btn" onClick={onResetView} title="重置视角">
      <AimOutlined />
    </button>
    <div className="dag-toolbar-divider" />
    <button className="dag-toolbar-btn" onClick={onRefresh} title="刷新">
      <ReloadOutlined />
    </button>
    <button className="dag-toolbar-btn" onClick={onExport} title="导出图片">
      <DownloadOutlined />
    </button>
    <div className="dag-toolbar-divider" />
    <button className="dag-toolbar-btn" onClick={onExitFullscreen} title="退出全屏">
      <FullscreenExitOutlined />
    </button>
  </div>
);
