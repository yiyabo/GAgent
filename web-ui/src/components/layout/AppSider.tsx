import React from 'react';
import { Layout, Menu, Button, Tooltip } from 'antd';
import { useNavigate, useLocation } from 'react-router-dom';
import {
  DashboardOutlined,
  NodeIndexOutlined,
  ProjectOutlined,
  SettingOutlined,
  BarChartOutlined,
  ToolOutlined,
  BookOutlined,
  DatabaseOutlined,
  MessageOutlined,
  MenuUnfoldOutlined,
  MenuFoldOutlined,
} from '@ant-design/icons';
import { useLayoutStore } from '@store/layout';

const { Sider } = Layout;

interface MenuItem {
  key: string;
  icon: React.ReactNode;
  label: string;
  path: string;
}

const menuItems: MenuItem[] = [
  {
    key: 'dashboard',
    icon: <DashboardOutlined />,
    label: 'Dashboard',
    path: '/dashboard',
  },
  {
    key: 'chat',
    icon: <MessageOutlined />,
    label: 'AI Chat',
    path: '/chat',
  },
  {
    key: 'tasks',
    icon: <NodeIndexOutlined />,
    label: 'Task Management',
    path: '/tasks',
  },
  {
    key: 'plans',
    icon: <ProjectOutlined />,
    label: 'Plan Management',
    path: '/plans',
  },
  {
    key: 'memory',
    icon: <DatabaseOutlined />,
    label: 'Memory',
    path: '/memory',
  },
  {
    key: 'analytics',
    icon: <BarChartOutlined />,
    label: 'Analytics',
    path: '/analytics',
  },
  {
    key: 'tools',
    icon: <ToolOutlined />,
    label: 'Tools',
    path: '/tools',
  },
  {
    key: 'templates',
    icon: <BookOutlined />,
    label: 'Templates',
    path: '/templates',
  },
  {
    key: 'system',
    icon: <SettingOutlined />,
    label: 'Settings',
    path: '/system',
  },
];

const AppSider: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { appSiderVisible, toggleAppSider } = useLayoutStore();

  // Determine selected menu item based on current path
  const selectedKeys = [location.pathname.slice(1) || 'dashboard'];

  const handleMenuClick = (item: { key: string }) => {
    const menuItem = menuItems.find(m => m.key === item.key);
    if (menuItem) {
      navigate(menuItem.path);
    }
  };

  return (
    <Sider
      width={200}
      collapsed={!appSiderVisible}
      collapsedWidth={24}
      trigger={null}
      className="app-sider"
      style={{ overflow: 'hidden' }}
    >
      {appSiderVisible ? (
        <>
          <div className="sider-header">
            <Tooltip title="Hide navigation" placement="right">
              <Button
                type="text"
                size="small"
                icon={<MenuFoldOutlined />}
                className="sider-collapse-button"
                onClick={toggleAppSider}
              />
            </Tooltip>
          </div>
          <Menu
            mode="inline"
            selectedKeys={selectedKeys}
            className="sider-menu"
            theme="dark"
            onClick={handleMenuClick}
            items={menuItems.map(item => ({
              key: item.key,
              icon: item.icon,
              label: item.label,
            }))}
          />
        </>
      ) : (
        <div className="sider-collapsed">
          <Tooltip title="Expand navigation" placement="right">
            <Button
              type="text"
              size="small"
              icon={<MenuUnfoldOutlined />}
              className="sider-collapse-button sider-handle-vertical"
              onClick={toggleAppSider}
            >
              <span className="sider-handle-text">Menu</span>
            </Button>
          </Tooltip>
        </div>
      )}
    </Sider>
  );
};

export default AppSider;
