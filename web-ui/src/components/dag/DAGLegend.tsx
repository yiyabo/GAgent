import React from 'react';
import { getCardColors } from './dag-constants';
import { useCanvasPalette } from '@/theme/canvasPalette';

export const DAGLegend: React.FC = () => {
  const palette = useCanvasPalette();
  return (
  <div className="dag-legend-panel dag-card-legend">
    <div className="dag-legend-title">Task Types</div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: getCardColors(palette, 'ROOT').bg,
        borderLeft: `3px solid ${getCardColors(palette, 'ROOT').accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>Root</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: getCardColors(palette, 'COMPOSITE').bg,
        borderLeft: `3px solid ${getCardColors(palette, 'COMPOSITE').accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>Composite</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: getCardColors(palette, 'ATOMIC').bg,
        borderLeft: `3px solid ${getCardColors(palette, 'ATOMIC').accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>Atomic</span>
    </div>
    
    <div className="dag-legend-divider" />
    <div className="dag-legend-title">Connections</div>
    <div className="dag-legend-item">
      <span className="dag-legend-line dag-legend-line-solid" />
      <span>Hierarchy</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-line dag-legend-line-dashed" />
      <span>Dependency <small style={{ opacity: 0.6 }}>(hover)</small></span>
    </div>
    
    <div className="dag-legend-hint">
      Scroll to zoom · Click to select
    </div>
  </div>
  );
};
