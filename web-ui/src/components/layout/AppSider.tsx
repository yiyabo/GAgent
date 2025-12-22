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
    label: '控制台',
    path: '/dashboard',
  },
  {
    key: 'chat',
    icon: <MessageOutlined />,
    label: 'AI对话',
    path: '/chat',
  },
  {
    key: 'tasks',
    icon: <NodeIndexOutlined />,
    label: '任务管理',
    path: '/tasks',
  },
  {
    key: 'plans',
    icon: <ProjectOutlined />,
    label: '计划管理',
    path: '/plans',
  },
  {
    key: 'memory',
    icon: <DatabaseOutlined />,
    label: '记忆管理',
    path: '/memory',
  },
  {
    key: 'analytics',
    icon: <BarChartOutlined />,
    label: '分析统计',
    path: '/analytics',
  },
  {
    key: 'tools',
    icon: <ToolOutlined />,
    label: '工具箱',
    path: '/tools',
  },
  {
    key: 'templates',
    icon: <BookOutlined />,
    label: '模板库',
    path: '/templates',
  },
  {
    key: 'system',
    icon: <SettingOutlined />,
    label: '系统设置',
    path: '/system',
  },
];

const AppSider: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { appSiderVisible, toggleAppSider } = useLayoutStore();

  // 根据当前路径确定选中的菜单项
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
            <Tooltip title="隐藏主导航" placement="right">
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
          <Tooltip title="展开主导航" placement="right">
            <Button
              type="text"
              size="small"
              icon={<MenuUnfoldOutlined />}
              className="sider-collapse-button sider-handle-vertical"
              onClick={toggleAppSider}
            >
              <span className="sider-handle-text">主导航</span>
            </Button>
          </Tooltip>
        </div>
      )}
    </Sider>
  );
};

export default AppSider;
