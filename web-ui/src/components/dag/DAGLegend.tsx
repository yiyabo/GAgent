import React from 'react';
import { CARD_COLORS } from './dag-constants';

export const DAGLegend: React.FC = () => (
  <div className="dag-legend-panel dag-card-legend">
    <div className="dag-legend-title">Task Types</div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: CARD_COLORS.ROOT.bg,
        borderLeft: `3px solid ${CARD_COLORS.ROOT.accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>Root</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: CARD_COLORS.COMPOSITE.bg,
        borderLeft: `3px solid ${CARD_COLORS.COMPOSITE.accent}`
      }} />
      <span style={{ fontStyle: 'italic' }}>Composite</span>
    </div>
    <div className="dag-legend-item">
      <span className="dag-legend-card" style={{ 
        background: CARD_COLORS.ATOMIC.bg,
        borderLeft: `3px solid ${CARD_COLORS.ATOMIC.accent}`
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
