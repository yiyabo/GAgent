import React from 'react';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@store/auth';
import { useT } from '@/i18n/useT';
import LanguageSwitch from '@components/common/LanguageSwitch';

const { Title, Text } = Typography;

const Login: React.FC = () => {
  const t = useT();
  const navigate = useNavigate();
  const location = useLocation();
  const { login, loading } = useAuthStore();
  const [error, setError] = React.useState<string | null>(null);

  const nextPath =
    new URLSearchParams(location.search).get('next') || '/chat';

  const onFinish = async (values: { email: string; password: string }) => {
    setError(null);
    try {
      await login(values.email, values.password);
      navigate(nextPath, { replace: true });
    } catch (err: any) {
      setError(err?.message || t('auth.loginFailed'));
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        position: 'relative',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background:
          'radial-gradient(circle at 20% 20%, color-mix(in srgb, var(--color-action) 10%, transparent), transparent 50%), var(--bg-primary)',
      }}
    >
      <div style={{ position: 'absolute', top: 16, right: 24 }}>
        <LanguageSwitch />
      </div>
      <Card
        style={{ width: '100%', maxWidth: 420, borderRadius: 12 }}
        bodyStyle={{ padding: 28 }}
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <div>
            <Title level={3} style={{ margin: 0 }}>
              {t('auth.signIn')}
            </Title>
            <Text type="secondary">{t('auth.signInSubtitle')}</Text>
          </div>
          {error ? <Alert type="error" message={error} showIcon /> : null}
          <Form layout="vertical" onFinish={onFinish}>
            <Form.Item
              name="email"
              label={t('auth.email')}
              rules={[
                { required: true, message: t('auth.emailRequired') },
                { type: 'email', message: t('auth.emailInvalid') },
              ]}
            >
              <Input autoComplete="email" placeholder="you@example.com" />
            </Form.Item>
            <Form.Item
              name="password"
              label={t('auth.password')}
              rules={[{ required: true, message: t('auth.passwordRequired') }]}
            >
              <Input.Password autoComplete="current-password" placeholder={t('auth.passwordPlaceholder')} />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              {t('auth.signIn')}
            </Button>
          </Form>
          <Text type="secondary">
            {t('auth.noAccount')} <Link to="/register">{t('auth.createOne')}</Link>
          </Text>
        </Space>
      </Card>
    </div>
  );
};

export default Login;
