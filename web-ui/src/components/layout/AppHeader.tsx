import React from 'react';
import { App, Layout, Button, Badge, Tooltip, Space, Typography, Modal, Form, Input, Tag, Dropdown } from 'antd';
import {
  BellOutlined,
  LogoutOutlined,
  KeyOutlined,
  SettingOutlined,
  UnorderedListOutlined,
  AppstoreOutlined,
  ExperimentOutlined,
  ApiOutlined,
  MessageOutlined,
  NodeIndexOutlined,
} from '@ant-design/icons';
import { useSystemStore } from '@store/system';
import { useLayoutStore } from '@store/layout';
import { useAuthStore } from '@store/auth';
import { useLocation, useNavigate } from 'react-router-dom';
import Wordmark from '@components/common/Wordmark';
import LanguageSwitch from '@components/common/LanguageSwitch';
import { useT } from '@/i18n/useT';
import { describeAuthError } from '@utils/authError';
import { usePipelinesEnabled } from '@hooks/usePipelines';

const { Header } = Layout;
const { Text } = Typography;

const AppHeader: React.FC = () => {
  const t = useT();
  const { message } = App.useApp();
  const { apiConnected } = useSystemStore();
  const { chatListVisible, toggleChatList, workspaceMode, setWorkspaceMode } = useLayoutStore();
  const { user, logout, changePassword, loading, projectLabel } = useAuthStore();
  const location = useLocation();
  const navigate = useNavigate();
  const isChatRoute = location.pathname.startsWith('/chat');
  const canManageLocalSession = user?.auth_source === 'session';
  const pipelinesEnabled = usePipelinesEnabled();
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
      message.success(t('header.passwordUpdated'));
    } catch (error: any) {
      if (error?.errorFields) {
        return;
      }
      message.error(describeAuthError(error, 'changePassword') || t('header.passwordUpdateFailed'));
    }
  };

  return (
    <>
      <Header className={`app-header ${isChatRoute && workspaceMode === 'plan' ? 'workspace-header' : ''}${location.pathname.startsWith('/pipelines') ? ' pipeline-header' : ''}`}>
        <div className="app-logo" role="link" aria-label={t('header.homeAria')} style={{ cursor: 'pointer' }} onClick={() => navigate('/chat')}>
          <ExperimentOutlined className="logo-icon" />
          <Wordmark />
        </div>

        <div className="app-header-actions">
          <Space size="small" style={{ marginRight: 16 }}>
            <Tooltip title={apiConnected ? t('header.connected') : t('header.disconnected')}>
              <div className="system-status" style={{ gap: 6 }}>
                <div className={`status-indicator ${apiConnected ? '' : 'disconnected'}`} />
                <Text style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                  {apiConnected ? t('header.ready') : t('header.offline')}
                </Text>
              </div>
            </Tooltip>
          </Space>

          <Space size="small">
            {isChatRoute && <Dropdown trigger={['click']} menu={{ selectedKeys: [workspaceMode], items: [
              { key: 'plan', label: t('header.layoutPlan') },
              { key: 'classic', label: t('header.layoutClassic') },
            ], onClick: ({ key }) => setWorkspaceMode(key as 'plan' | 'classic') }}>
              <Button type="text" icon={<AppstoreOutlined />} aria-label={t('header.switchLayoutAria')}>{t('header.layout')}</Button>
            </Dropdown>}
            {!isChatRoute && (
              <Tooltip title={t('header.backToChat')}>
                <Button type="text" icon={<MessageOutlined />} aria-label={t('header.chat')} style={{ color: 'var(--text-secondary)' }} onClick={() => navigate('/chat')}>
                  {t('header.chat')}
                </Button>
              </Tooltip>
            )}
            <Tooltip title={t('header.extensionsTip')}>
              <Button
                type="text"
                icon={<ApiOutlined />}
                aria-label={t('header.extensions')}
                style={{ color: location.pathname.startsWith('/extensions') ? 'var(--text-primary)' : 'var(--text-secondary)' }}
                onClick={() => navigate('/extensions')}
              >
                {t('header.extensions')}
              </Button>
            </Tooltip>
            {pipelinesEnabled ? (
              <Tooltip title={t('header.pipelinesTip')}>
                <Button
                  type="text"
                  icon={<NodeIndexOutlined />}
                  aria-label={t('header.pipelines')}
                  style={{ color: location.pathname.startsWith('/pipelines') ? 'var(--text-primary)' : 'var(--text-secondary)' }}
                  onClick={() => navigate('/pipelines')}
                >
                  {t('header.pipelines')}
                </Button>
              </Tooltip>
            ) : null}
            <LanguageSwitch />
            <Text className="workspace-account-email" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {user?.email || t('header.anonymous')}
            </Text>
            {projectLabel ? <Tag color="blue">{projectLabel}</Tag> : null}
            {canManageLocalSession ? (
              <Tooltip title={t('header.changePassword')}>
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
              <Tooltip title={t('header.signOut')}>
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
              <Tooltip title={t('header.chatList')}>
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

            <Tooltip title={t('header.notifications')}>
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

            <Tooltip title={t('header.settings')}>
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
        title={t('header.changePassword')}
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
            label={t('header.currentPassword')}
            rules={[{ required: true, message: t('header.currentPasswordRequired') }]}
          >
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item
            name="new_password"
            label={t('header.newPassword')}
            rules={[
              { required: true, message: t('header.newPasswordRequired') },
              { min: 8, message: t('header.passwordMin') },
            ]}
          >
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item
            name="confirm_password"
            label={t('header.confirmPassword')}
            dependencies={['new_password']}
            rules={[
              { required: true, message: t('header.confirmPasswordRequired') },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || value === getFieldValue('new_password')) {
                    return Promise.resolve();
                  }
                  return Promise.reject(new Error(t('header.passwordMismatch')));
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
