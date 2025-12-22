import React from 'react';
import { Layout, Button, Badge, Tooltip, Space, Typography } from 'antd';
import {
  ApiOutlined,
  DatabaseOutlined,
  BellOutlined,
  SettingOutlined,
  MessageOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { useSystemStore } from '@store/system';
import { useChatStore } from '@store/chat';
import { useLayoutStore } from '@store/layout';
import { useLocation } from 'react-router-dom';

const { Header } = Layout;
const { Text } = Typography;

const AppHeader: React.FC = () => {
  const { systemStatus, apiConnected } = useSystemStore();
  const { toggleChatPanel, chatPanelVisible } = useChatStore();
  const { chatListVisible, toggleChatList } = useLayoutStore();
  const location = useLocation();
  const isChatRoute = location.pathname.startsWith('/chat');

  return (
    <Header className="app-header">
      <div className="app-logo">
        <span style={{ fontWeight: 500, fontSize: 14, color: 'var(--text-primary)' }}>AI 任务编排</span>
      </div>
      
      <div className="app-header-actions">
        {/* 系统状态 */}
        <Space size="small" style={{ marginRight: 16 }}>
          <Tooltip title={apiConnected ? '已连接' : '断开'}>
            <div className="system-status" style={{ gap: 6 }}>
              <div className={`status-indicator ${apiConnected ? '' : 'disconnected'}`} />
              <Text style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                {apiConnected ? '就绪' : '离线'}
              </Text>
            </div>
          </Tooltip>
        </Space>

        {/* 操作按钮 - 极简浅色 */}
        <Space size="small">
          {isChatRoute && !chatListVisible && (
            <Tooltip title="对话列表">
              <Button
                type="text"
                icon={<UnorderedListOutlined />}
                style={{
                  color: 'var(--text-secondary)',
                  height: 32,
                  width: 32,
                  padding: 0,
                }}
                onClick={toggleChatList}
              />
            </Tooltip>
          )}

          <Tooltip title="通知">
            <Badge count={0} size="small">
              <Button
                type="text"
                icon={<BellOutlined />}
                style={{
                  color: 'var(--text-secondary)',
                  height: 32,
                  width: 32,
                  padding: 0,
                }}
              />
            </Badge>
          </Tooltip>

          <Tooltip title="设置">
            <Button
              type="text"
              icon={<SettingOutlined />}
              style={{
                color: 'var(--text-secondary)',
                height: 32,
                width: 32,
                padding: 0,
              }}
            />
          </Tooltip>
        </Space>
      </div>
    </Header>
  );
};

export default AppHeader;
