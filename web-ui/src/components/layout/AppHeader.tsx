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
        <span style={{ fontWeight: 500, fontSize: 14, color: 'var(--text-primary)' }}>AI Task Orchestration</span>
      </div>
      
      <div className="app-header-actions">
        {/* System status */}
        <Space size="small" style={{ marginRight: 16 }}>
          <Tooltip title={apiConnected ? 'Connected' : 'Disconnected'}>
            <div className="system-status" style={{ gap: 6 }}>
              <div className={`status-indicator ${apiConnected ? '' : 'disconnected'}`} />
              <Text style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                {apiConnected ? 'Ready' : 'Offline'}
              </Text>
            </div>
          </Tooltip>
        </Space>

        {/* Action buttons - minimalist style */}
        <Space size="small">
          {isChatRoute && !chatListVisible && (
            <Tooltip title="Chat List">
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

          <Tooltip title="Notifications">
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

          <Tooltip title="Settings">
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
