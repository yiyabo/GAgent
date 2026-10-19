import React from 'react';
import { getCardColors } from './dag-constants';
import { useCanvasPalette } from '@/theme/canvasPalette';

import { useT } from '@/i18n/useT';
export const DAGLegend: React.FC = () => {
  const palette = useCanvasPalette();
  const t = useT();
  return (
  <div className="dag-legend-panel dag-card-legend">
    <div className="dag-legend-title">{t('dag.taskTypes')}</div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: getCardColors(palette, 'ROOT').bg,
        borderLeft: `3px solid ${getCardColors(palette, 'ROOT').accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>{t('dag.root')}</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: getCardColors(palette, 'COMPOSITE').bg,
        borderLeft: `3px solid ${getCardColors(palette, 'COMPOSITE').accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>{t('dag.composite')}</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: getCardColors(palette, 'ATOMIC').bg,
        borderLeft: `3px solid ${getCardColors(palette, 'ATOMIC').accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>{t('dag.atomic')}</span>
    </div>
    
    <div className="dag-legend-divider" />
    <div className="dag-legend-title">{t('dag.connections')}</div>
    <div className="dag-legend-item">
      <span className="dag-legend-line dag-legend-line-solid" />
      <span>{t('dag.hierarchy')}</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-line dag-legend-line-dashed" />
      <span>{t('dag.dependency')} <small style={{ opacity: 0.6 }}>{t('dag.hover')}</small></span>
    </div>
    
    <div className="dag-legend-hint">
      {t('dag.zoomHint')}
    </div>
  </div>
  );
};
