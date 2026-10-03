import React, { useEffect, useRef, useState } from 'react';
import { Button, Drawer, Segmented, Tooltip } from 'antd';
import { MenuUnfoldOutlined, MessageOutlined, PartitionOutlined } from '@ant-design/icons';
import { useLayoutStore } from '@store/layout';
import { useChatStore } from '@store/chat';
import ChatSidebar from './ChatSidebar';
import ChatMainArea from './ChatMainArea';
import DAGSidebar from './DAGSidebar';
import TaskDetailDrawer from '@components/tasks/TaskDetailDrawer';
import { workspaceGeometry } from './workspaceGeometry';
import './ResearchWorkspace.css';

const ResearchWorkspace: React.FC = () => {
  const currentPlanId = useChatStore((state) => state.currentPlanId);
  const drawerScope = useChatStore((state) => `${state.currentSession?.session_id ?? state.currentSession?.id ?? 'none'}:${state.currentPlanId ?? 'none'}`);
  const { chatListVisible, workspacePlanWidth, setWorkspacePlanWidth, dagSidebarFullscreen,
    toggleChatList, setDagSidebarFullscreen } = useLayoutStore();
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(() => typeof window === 'undefined' ? 1440 : window.innerWidth);
  const [pane, setPane] = useState<'chat' | 'plan'>('chat');
  const [navOpen, setNavOpen] = useState(false);
  const drag = useRef(false);
  const frame = useRef<number>();
  const geometry = workspaceGeometry(width, chatListVisible, workspacePlanWidth ?? (currentPlanId == null ? 420 : null));

  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const restore = () => {
      if (!drag.current) return;
      drag.current = false;
      document.body.style.removeProperty('cursor');
      document.body.style.removeProperty('user-select');
    };
    const move = (event: PointerEvent) => {
      if (!drag.current || !container.current) return;
      const next = container.current.getBoundingClientRect().right - event.clientX;
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => setWorkspacePlanWidth(Math.min(geometry.maxPlan, Math.max(420, next))));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', restore);
    window.addEventListener('pointercancel', restore);
    window.addEventListener('blur', restore);
    return () => {
      restore();
      if (frame.current) cancelAnimationFrame(frame.current);
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', restore);
      window.removeEventListener('pointercancel', restore);
      window.removeEventListener('blur', restore);
    };
  }, [geometry.maxPlan, setWorkspacePlanWidth]);

  const compactPlan = pane === 'plan' || dagSidebarFullscreen;
  return (
    <div className={`research-workspace ${geometry.compact ? 'is-compact' : ''}`} ref={container}>
      {geometry.compact && <div className="workspace-mobile-toolbar">
        <Button type="text" aria-label="打开会话列表" icon={<MenuUnfoldOutlined />} onClick={() => setNavOpen(true)} />
        <Segmented value={compactPlan ? 'plan' : 'chat'} options={[
          { value: 'chat', label: '研究对话', icon: <MessageOutlined /> },
          { value: 'plan', label: '计划工作区', icon: <PartitionOutlined /> },
        ]} onChange={(value) => { setPane(value as 'chat' | 'plan'); setDagSidebarFullscreen(false); }} />
      </div>}
      <div className="workspace-columns">
        {geometry.navWidth > 0 && <aside className="workspace-navigation" style={{ width: geometry.navWidth }} aria-label="会话导航"><ChatSidebar /></aside>}
        {!geometry.compact && !chatListVisible && <Tooltip title="展开会话列表"><Button className="workspace-nav-reopen" type="text" aria-label="展开会话列表" icon={<MenuUnfoldOutlined />} onClick={toggleChatList} /></Tooltip>}
        <section className="workspace-conversation" aria-label="研究对话" hidden={geometry.compact ? compactPlan : dagSidebarFullscreen}>
          <ChatMainArea />
        </section>
        {!geometry.compact && !dagSidebarFullscreen && <div className="workspace-divider" role="separator" tabIndex={0}
          aria-label="调整对话与计划宽度" aria-orientation="vertical" aria-valuemin={420} aria-valuemax={Math.round(geometry.maxPlan)} aria-valuenow={Math.round(geometry.planWidth)}
          onPointerDown={(event) => { event.preventDefault(); drag.current = true; document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'; }}
          onDoubleClick={() => setWorkspacePlanWidth((width - geometry.navWidth) * 0.65)}
          onKeyDown={(event) => { if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); setWorkspacePlanWidth(Math.min(geometry.maxPlan, Math.max(420, geometry.planWidth + (event.key === 'ArrowLeft' ? 32 : -32)))); } }}
        ><span /></div>}
        <section className="workspace-plan" aria-label="计划工作区" hidden={geometry.compact && !compactPlan}
          style={{ width: geometry.compact || dagSidebarFullscreen ? undefined : geometry.planWidth, flex: geometry.compact || dagSidebarFullscreen ? 1 : undefined }}>
          <DAGSidebar />
        </section>
      </div>
      <Drawer title="研究会话" placement="left" width="min(320px, 90vw)" open={geometry.compact && navOpen} onClose={() => setNavOpen(false)} bodyStyle={{ padding: 0 }}>
        <ChatSidebar onCollapse={() => setNavOpen(false)} onSessionSelected={() => { setNavOpen(false); setPane('chat'); setDagSidebarFullscreen(false); }} />
      </Drawer>
      <TaskDetailDrawer key={drawerScope} />
    </div>
  );
};

export default ResearchWorkspace;
