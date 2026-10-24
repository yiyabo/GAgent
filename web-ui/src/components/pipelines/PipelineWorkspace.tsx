import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Segmented, Tooltip } from 'antd';
import { FullscreenExitOutlined, FullscreenOutlined, MenuFoldOutlined, MenuUnfoldOutlined } from '@ant-design/icons';
import { useT } from '@/i18n/useT';

const WIDTH_KEY = 'pipeline-panel-width';
const DEFAULT_WIDTH = 480;
const DEFAULT_HEIGHT = 200;
const clamp = (value: number, min: number, max: number) => Math.round(Math.min(max, Math.max(min, value)));

interface Props {
  children: React.ReactNode;
  palette?: React.ReactNode;
  details: React.ReactNode;
  assistant: React.ReactNode;
  tab: 'details' | 'agent';
  onTabChange: (tab: 'details' | 'agent') => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  locked: boolean;
}

/** Resizing changes the viewport, never the saved node positions or the mounted conversation. */
const PipelineWorkspace: React.FC<Props> = ({ children, palette, details, assistant, tab, onTabChange, open, onOpenChange, locked }) => {
  const t = useT();
  const area = useRef<HTMLDivElement>(null);
  const handle = useRef<HTMLDivElement>(null);
  const reopen = useRef<HTMLButtonElement>(null);
  const collapse = useRef<HTMLButtonElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  const [preferredWidth, setPreferredWidth] = useState(() => {
    try {
      const stored = Number(localStorage.getItem(WIDTH_KEY));
      if (Number.isFinite(stored) && stored >= 320) return stored;
    } catch { /* Storage may be unavailable. */ }
    return DEFAULT_WIDTH;
  });
  const [preferredHeight, setPreferredHeight] = useState(DEFAULT_HEIGHT);
  const [expanded, setExpanded] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ id: number; x: number; y: number; size: number; stacked: boolean } | null>(null);
  const stacked = box.width <= 680 && box.width > 0;
  const min = stacked ? Math.min(100, Math.floor(box.height * 0.25)) : 320;
  const max = stacked
    ? Math.max(min, Math.floor(Math.min(box.height * 0.65, box.height - 216)))
    : Math.max(min, Math.floor(Math.min(box.width * 0.7, box.width - Math.min(360, box.width * 0.3) - 12)));
  const size = clamp(stacked ? preferredHeight : preferredWidth, min, max);

  useLayoutEffect(() => {
    const element = area.current;
    if (!element) return undefined;
    const measure = () => setBox({ width: element.clientWidth, height: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const finish = () => {
    const current = drag.current;
    drag.current = null;
    setDragging(false);
    if (current && handle.current?.hasPointerCapture(current.id)) handle.current.releasePointerCapture(current.id);
  };
  useEffect(() => {
    finish();
  }, [stacked, open, expanded]);
  useEffect(() => {
    window.addEventListener('blur', finish);
    return () => window.removeEventListener('blur', finish);
  }, []);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { setExpanded(false); setPaletteOpen(false); }
    };
    window.addEventListener('keydown', escape);
    return () => window.removeEventListener('keydown', escape);
  }, []);

  const resize = (value: number) => {
    const next = clamp(value, min, max);
    if (stacked) setPreferredHeight(next);
    else {
      setPreferredWidth(next);
      try { localStorage.setItem(WIDTH_KEY, String(next)); } catch { /* Keep the size for this visit. */ }
    }
  };
  const reset = () => resize(stacked ? DEFAULT_HEIGHT : DEFAULT_WIDTH);
  const changeTab = (value: string | number) => { onTabChange(value as Props['tab']); onOpenChange(true); };
  const expandLabel = t(expanded ? 'pipelines.panel.restore' : 'pipelines.panel.expand');
  const resizeLabel = t(stacked ? 'pipelines.panel.resizeHeight' : 'pipelines.panel.resizeWidth');

  return (
    <div className={`pl-body${expanded && open ? ' pl-reading' : ''}${paletteOpen ? ' pl-palette-open' : ''}`}>
      {palette ? (
        <>
          <Button size="small" className="pl-palette-toggle" onClick={() => setPaletteOpen(!paletteOpen)} aria-expanded={paletteOpen}>
            {t('pipelines.panel.nodes')}
          </Button>
          <aside className="pl-left" onClick={(event) => {
            if ((event.target as HTMLElement).closest('.pl-palette-item')) setPaletteOpen(false);
          }}>{palette}</aside>
        </>
      ) : null}
      <div
        ref={area}
        className={`pl-work-area${stacked ? ' pl-stacked' : ''}${expanded && open ? ' pl-expanded' : ''}${!open ? ' pl-collapsed' : ''}${dragging ? ' pl-resizing' : ''}`}
        style={{ '--pl-panel-width': `${size}px`, '--pl-canvas-height': `${size}px` } as React.CSSProperties}
      >
        {children}
        <div
          ref={handle}
          className="pl-resizer"
          role="separator"
          tabIndex={0}
          aria-label={resizeLabel}
          aria-orientation={stacked ? 'horizontal' : 'vertical'}
          aria-controls="pl-result-panel"
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuenow={size}
          title={resizeLabel}
          onDoubleClick={reset}
          onKeyDown={(event) => {
            const keys = stacked ? ['ArrowUp', 'ArrowDown'] : ['ArrowLeft', 'ArrowRight'];
            if (![...keys, 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            if (event.key === 'Home') reset();
            else resize(event.key === 'End' ? max : size + (event.key === 'ArrowLeft' || event.key === 'ArrowDown' ? 24 : -24));
          }}
          onPointerDown={(event) => {
            if (event.button !== 0 || drag.current) return;
            event.preventDefault();
            drag.current = { id: event.pointerId, x: event.clientX, y: event.clientY, size, stacked };
            event.currentTarget.setPointerCapture(event.pointerId);
            event.currentTarget.focus({ preventScroll: true });
            setDragging(true);
          }}
          onPointerMove={(event) => {
            const start = drag.current;
            if (!start || start.id !== event.pointerId || start.stacked !== stacked) return;
            resize(start.size + (stacked ? event.clientY - start.y : start.x - event.clientX));
          }}
          onPointerUp={(event) => { if (drag.current?.id === event.pointerId) finish(); }}
          onPointerCancel={(event) => { if (drag.current?.id === event.pointerId) finish(); }}
          onLostPointerCapture={finish}
        />
        <aside className="pl-right" id="pl-result-panel" aria-label={t('pipelines.panel.title')}>
          <div className="pl-panel-toolbar">
            <Segmented block size="small" value={tab} onChange={changeTab} options={[
              { value: 'details', label: t('pipelines.tab.details') },
              { value: 'agent', label: t('pipelines.tab.agent') },
            ]} />
            <Tooltip title={expandLabel}>
              <Button size="small" icon={expanded ? <FullscreenExitOutlined /> : <FullscreenOutlined />} aria-label={expandLabel} onClick={() => { setExpanded(!expanded); setPaletteOpen(false); }} />
            </Tooltip>
            <Tooltip title={t('pipelines.panel.collapse')}>
              <Button ref={collapse} size="small" icon={<MenuFoldOutlined />} aria-label={t('pipelines.panel.collapse')} onClick={() => {
                onOpenChange(false);
                setExpanded(false);
                window.requestAnimationFrame(() => reopen.current?.focus());
              }} />
            </Tooltip>
          </div>
          <div className={`pl-right-body${tab === 'agent' ? ' pl-hidden' : ''}${locked ? ' pl-locked' : ''}`}>{details}</div>
          <div className={`pl-right-body pl-right-agent-body${tab === 'agent' ? '' : ' pl-hidden'}`}>{assistant}</div>
          <footer className="pl-panel-footer">
            <span>{t(stacked ? 'pipelines.panel.dragHeight' : 'pipelines.panel.dragWidth')}</span>
            <span>{expanded ? t('pipelines.panel.reading') : t('pipelines.panel.pixels', { size })}</span>
            <Button type="text" size="small" onClick={reset}>{t('pipelines.panel.reset')}</Button>
          </footer>
        </aside>
        {!open ? (
          <Button ref={reopen} className="pl-panel-reopen" size="small" icon={<MenuUnfoldOutlined />} onClick={() => {
            onOpenChange(true);
            window.requestAnimationFrame(() => collapse.current?.focus());
          }}>{t('pipelines.panel.open')}</Button>
        ) : null}
      </div>
    </div>
  );
};

export default PipelineWorkspace;
