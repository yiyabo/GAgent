import React from 'react';
import { Layout, Menu } from 'antd';
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
} from '@ant-design/icons';

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

  // 根据当前路径确定选中的菜单项
  const selectedKeys = [location.pathname.slice(1) || 'dashboard'];

  const handleMenuClick = (item: { key: string }) => {
    const menuItem = menuItems.find(m => m.key === item.key);
    if (menuItem) {
      navigate(menuItem.path);
    }
  };

  return (
    <Sider width={200} className="app-sider">
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
    </Sider>
  );
};

export default AppSider;
