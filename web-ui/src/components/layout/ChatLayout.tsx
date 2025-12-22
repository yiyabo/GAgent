import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Layout } from 'antd';
import ChatSidebar from './ChatSidebar';
import ChatMainArea from './ChatMainArea';
import DAGSidebar from './DAGSidebar';
import TaskDetailDrawer from '@components/tasks/TaskDetailDrawer';
import { useLayoutStore } from '@store/layout';
import { Button, Tooltip } from 'antd';
import { MenuUnfoldOutlined } from '@ant-design/icons';

const ChatLayout: React.FC = () => {
  const {
    chatListVisible,
    chatListWidth,
    setChatListWidth,
    dagSidebarWidth,
    setDagSidebarWidth,
    dagSidebarFullscreen,
    toggleChatList,
  } = useLayoutStore();
  const containerRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<'left' | 'right' | null>(null);
  const [containerWidth, setContainerWidth] = useState(0);

  const chatSidebarStyle = useMemo(
    () => ({
      background: '#f8f9fa',
      borderRight: chatListVisible ? '1px solid #e5e7eb' : 'none',
    }),
    [chatListVisible]
  );

  useEffect(() => {
    if (!containerRef.current) return undefined;
    const observer = new ResizeObserver((entries) => {
      if (!entries.length) return;
      setContainerWidth(entries[0].contentRect.width);
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      if (!dragRef.current || !containerRef.current) {
        return;
      }
      const rect = containerRef.current.getBoundingClientRect();
      if (dragRef.current === 'left') {
        const nextWidth = Math.min(Math.max(event.clientX - rect.left, 220), 520);
        setChatListWidth(nextWidth);
      }
      if (dragRef.current === 'right') {
        const maxWidth = Math.max(rect.width - (chatListVisible ? chatListWidth : 0) - 240, 360);
        const nextWidth = Math.min(Math.max(rect.right - event.clientX, 320), maxWidth);
        setDagSidebarWidth(nextWidth);
      }
    };

    const handleMouseUp = () => {
      if (dragRef.current) {
        dragRef.current = null;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };

    window.addEventListener('mousemove', handleMouseMove);
    window.addEventListener('mouseup', handleMouseUp);
    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      window.removeEventListener('mouseup', handleMouseUp);
    };
  }, [chatListVisible, chatListWidth, setChatListWidth, setDagSidebarWidth]);

  const handleDragStart = (type: 'left' | 'right') => (event: React.MouseEvent) => {
    event.preventDefault();
    dragRef.current = type;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  };

  const leftWidth = chatListVisible ? chatListWidth : 0;
  const calculatedDagWidth =
    dagSidebarFullscreen && containerWidth
      ? Math.max(containerWidth - leftWidth, 360)
      : dagSidebarWidth;

  return (
    <>
      <div
        ref={containerRef}
        style={{
          height: 'calc(100vh - 64px)', // 减去 header 高度
          overflow: 'hidden',
          margin: '-24px', // 抵消外层 Content 的 padding
        }}
      >
        <Layout style={{ height: '100%', overflow: 'hidden' }}>
        {/* 左侧对话列表 */}
        <Layout.Sider 
          width={chatListWidth}
          collapsed={!chatListVisible}
          collapsedWidth={24}
          trigger={null}
          style={chatSidebarStyle}
        >
          {chatListVisible ? (
            <ChatSidebar />
          ) : (
            <div className="chatlist-collapsed">
              <Tooltip title="展开对话列表" placement="right">
                <Button
                  type="text"
                  size="small"
                  icon={<MenuUnfoldOutlined />}
                  className="chatlist-collapse-button chatlist-handle-vertical"
                  onClick={toggleChatList}
                >
                  <span className="chatlist-handle-text">对话列表</span>
                </Button>
              </Tooltip>
            </div>
          )}
        </Layout.Sider>

        {chatListVisible && (
          <div
            className="layout-resizer"
            onMouseDown={handleDragStart('left')}
          />
        )}

        {/* 中间聊天主区域 */}
        <Layout.Content 
          style={{ 
            background: 'white',
            display: dagSidebarFullscreen ? 'none' : 'flex',
            flexDirection: 'column',
            minWidth: 0 // 防止flex子元素溢出
          }}
        >
          <ChatMainArea />
        </Layout.Content>

        {!dagSidebarFullscreen && (
          <div
            className="layout-resizer"
            onMouseDown={handleDragStart('right')}
          />
        )}

        {/* 右侧DAG可视化 */}
        <Layout.Sider 
          width={calculatedDagWidth}
          style={{ 
            background: 'white',
            borderLeft: '1px solid #e5e7eb'
          }}
          reverseArrow
        >
          <DAGSidebar />
        </Layout.Sider>
      </Layout>
      </div>
      <TaskDetailDrawer />
    </>
  );
};

export default ChatLayout;
