import React from 'react';
import { App, Layout, Button, Badge, Tooltip, Space, Typography, Modal, Form, Input } from 'antd';
import {
  BellOutlined,
  LogoutOutlined,
  KeyOutlined,
  SettingOutlined,
  UnorderedListOutlined,
} from '@ant-design/icons';
import { useSystemStore } from '@store/system';
import { useLayoutStore } from '@store/layout';
import { useAuthStore } from '@store/auth';
import { useLocation, useNavigate } from 'react-router-dom';

const { Header } = Layout;
const { Text } = Typography;

const AppHeader: React.FC = () => {
  const { message } = App.useApp();
  const { apiConnected } = useSystemStore();
  const { chatListVisible, toggleChatList } = useLayoutStore();
  const { user, logout, changePassword, loading } = useAuthStore();
  const location = useLocation();
  const navigate = useNavigate();
  const isChatRoute = location.pathname.startsWith('/chat');
  const canManageLocalSession = user?.auth_source === 'session';
  const [form] = Form.useForm();
  const [changePasswordOpen, setChangePasswordOpen] = React.useState(false);

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  const handleChangePassword = async () => {
    try {
      const values = await form.validateFields();
      await changePassword(values.current_password, values.new_password);
      setChangePasswordOpen(false);
      form.resetFields();
      message.success('Password updated.');
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      message.error(error?.message || 'Failed to update password.');
    }
  };

  return (
    <>
      <Header className="app-header">
        <div className="app-logo">
          <span style={{ fontWeight: 500, fontSize: 14, color: 'var(--text-primary)' }}>AI Task Orchestration</span>
        </div>

        <div className="app-header-actions">
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

          <Space size="small">
            <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {user?.email || 'anonymous'}
            </Text>
            {canManageLocalSession ? (
              <Tooltip title="Change password">
                <Button
                  type="text"
                  icon={<KeyOutlined />}
                  style={{
                    color: 'var(--text-secondary)',
                    height: 32,
                    width: 32,
                    padding: 0,
                  }}
                  onClick={() => setChangePasswordOpen(true)}
                />
              </Tooltip>
            ) : null}
            {canManageLocalSession ? (
              <Tooltip title="Sign out">
                <Button
                  type="text"
                  icon={<LogoutOutlined />}
                  style={{
                    color: 'var(--text-secondary)',
                    height: 32,
                    width: 32,
                    padding: 0,
                  }}
                  onClick={() => void handleLogout()}
                />
              </Tooltip>
            ) : null}
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
      <Modal
        title="Change Password"
        open={changePasswordOpen && canManageLocalSession}
        onCancel={() => {
          setChangePasswordOpen(false);
          form.resetFields();
        }}
        onOk={() => void handleChangePassword()}
        confirmLoading={loading}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="current_password"
            label="Current Password"
            rules={[{ required: true, message: 'Current password is required' }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label="New Password"
            rules={[
              { required: true, message: 'New password is required' },
              { min: 8, message: 'At least 8 characters' },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label="Confirm New Password"
            dependencies={['new_password']}
            rules={[
              { required: true, message: 'Please confirm new password' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || value === getFieldValue('new_password')) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error('Passwords do not match'));
                },
              }),
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
};

export default AppHeader;
