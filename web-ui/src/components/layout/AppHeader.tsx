import React from 'react';
import { Layout, Button, Badge, Tooltip, Space, Typography } from 'antd';
import {
  RobotOutlined,
  ApiOutlined,
  DatabaseOutlined,
  BellOutlined,
  SettingOutlined,
  MessageOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
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
  const { appSiderVisible, chatListVisible, toggleAppSider, toggleChatList } = useLayoutStore();
  const location = useLocation();
  const isChatRoute = location.pathname.startsWith('/chat');

  return (
    <Header className="app-header">
      <div className="app-logo">
        <RobotOutlined className="logo-icon" />
        <span>AI 智能任务编排系统</span>
      </div>
      
      <div className="app-header-actions">
        {/* 系统状态指示器 */}
        <Space size="large">
          <Tooltip title={`API连接: ${apiConnected ? '已连接' : '断开'}`}>
            <div className="system-status">
              <ApiOutlined style={{ marginRight: 4 }} />
              <div className={`status-indicator ${apiConnected ? '' : 'disconnected'}`} />
              <Text style={{ color: 'white', fontSize: 12 }}>
                {apiConnected ? 'API已连接' : 'API断开'}
              </Text>
            </div>
          </Tooltip>

          <Tooltip title={`数据库: ${systemStatus.database_status}`}>
            <div className="system-status">
              <DatabaseOutlined style={{ marginRight: 4 }} />
              <div className={`status-indicator ${
                systemStatus.database_status === 'connected' ? '' : 
                systemStatus.database_status === 'error' ? 'disconnected' : 'warning'
              }`} />
              <Text style={{ color: 'white', fontSize: 12 }}>
                数据库{systemStatus.database_status === 'connected' ? '正常' : '异常'}
              </Text>
            </div>
          </Tooltip>

          <Tooltip title="活跃任务">
            <div className="system-status">
              <Text style={{ color: 'white', fontSize: 12 }}>
                活跃任务: {systemStatus.active_tasks}
              </Text>
            </div>
          </Tooltip>

          <Tooltip title="API调用/分钟">
            <div className="system-status">
              <Text style={{ color: 'white', fontSize: 12 }}>
                API: {systemStatus.system_load.api_calls_per_minute}/min
              </Text>
            </div>
          </Tooltip>
        </Space>

        {/* 操作按钮 */}
        <Space>
          <Tooltip title={appSiderVisible ? '隐藏主导航' : '显示主导航'}>
            <Button
              type="text"
              icon={appSiderVisible ? <MenuFoldOutlined /> : <MenuUnfoldOutlined />}
              style={{ color: 'white' }}
              onClick={toggleAppSider}
            />
          </Tooltip>

          {isChatRoute && !chatListVisible && (
            <Tooltip title="显示对话列表">
              <Button
                type="text"
                icon={<UnorderedListOutlined />}
                style={{ color: 'white' }}
                onClick={toggleChatList}
              />
            </Tooltip>
          )}

          <Tooltip title="通知">
            <Badge count={0} size="small">
              <Button 
                type="text" 
                icon={<BellOutlined />} 
                style={{ color: 'white' }}
              />
            </Badge>
          </Tooltip>

          <Tooltip title={chatPanelVisible ? '隐藏聊天面板' : '显示聊天面板'}>
            <Button 
              type="text" 
              icon={<MessageOutlined />} 
              style={{ color: 'white' }}
              onClick={toggleChatPanel}
            />
          </Tooltip>

          <Tooltip title="系统设置">
            <Button 
              type="text" 
              icon={<SettingOutlined />} 
              style={{ color: 'white' }}
            />
          </Tooltip>
        </Space>
      </div>
    </Header>
  );
};

export default AppHeader;
