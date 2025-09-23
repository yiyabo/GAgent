import React from 'react';
import { Layout } from 'antd';
import ChatSidebar from './ChatSidebar';
import ChatMainArea from './ChatMainArea';
import DAGSidebar from './DAGSidebar';

const ChatLayout: React.FC = () => {
  return (
    <Layout style={{ height: '100vh', overflow: 'hidden' }}>
      {/* 左侧对话列表 */}
      <Layout.Sider 
        width={280} 
        style={{ 
          background: '#f8f9fa',
          borderRight: '1px solid #e5e7eb'
        }}
      >
        <ChatSidebar />
      </Layout.Sider>

      {/* 中间聊天主区域 */}
      <Layout.Content 
        style={{ 
          background: 'white',
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0 // 防止flex子元素溢出
        }}
      >
        <ChatMainArea />
      </Layout.Content>

      {/* 右侧DAG可视化 */}
      <Layout.Sider 
        width={400} 
        style={{ 
          background: 'white',
          borderLeft: '1px solid #e5e7eb'
        }}
        reverseArrow
      >
        <DAGSidebar />
      </Layout.Sider>
    </Layout>
  );
};

export default ChatLayout;
