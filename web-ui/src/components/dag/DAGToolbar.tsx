import React from 'react';
import {
  FullscreenExitOutlined,
  ExpandOutlined,
  ReloadOutlined,
  DownloadOutlined,
  AimOutlined,
} from '@ant-design/icons';
import { useT } from '@/i18n/useT';

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
}) => {
  const t = useT();
  return (
  <div className="dag-toolbar">
  <button className="dag-toolbar-btn" onClick={onFitView} title={t('dag.fit')} aria-label={t('dag.fit')}>
  <ExpandOutlined />
  </button>
  <button className="dag-toolbar-btn" onClick={onResetView} title={t('dag.reset')} aria-label={t('dag.reset')}>
  <AimOutlined />
  </button>
  <div className="dag-toolbar-divider" />
  <button className="dag-toolbar-btn" onClick={onRefresh} title={t('common.refresh')} aria-label={t('common.refresh')}>
  <ReloadOutlined />
  </button>
  <button className="dag-toolbar-btn" onClick={onExport} title={t('dag.exportImage')} aria-label={t('dag.exportImage')}>
  <DownloadOutlined />
  </button>
  <div className="dag-toolbar-divider" />
  <button className="dag-toolbar-btn" onClick={onExitFullscreen} title={t('dag.exitFullscreen')} aria-label={t('dag.exitFullscreen')}>
  <FullscreenExitOutlined />
  </button>
  </div>
  );
};
