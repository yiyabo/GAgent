import React from 'react';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@store/auth';
import { useT } from '@/i18n/useT';
import LanguageSwitch from '@components/common/LanguageSwitch';
import { describeAuthError } from '@utils/authError';

const { Title, Text } = Typography;

const Register: React.FC = () => {
  const t = useT();
  const navigate = useNavigate();
  const { register, loading } = useAuthStore();
  const [error, setError] = React.useState<string | null>(null);

  const onFinish = async (values: {
    email: string;
    password: string;
    confirmPassword: string;
  }) => {
    setError(null);
    try {
      await register(values.email, values.password);
      navigate('/chat', { replace: true });
    } catch (err: any) {
      setError(describeAuthError(err, 'register') || t('auth.registerFailed'));
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
          'radial-gradient(circle at 80% 0%, color-mix(in srgb, var(--color-action) 8%, transparent), transparent 46%), var(--bg-primary)',
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
              {t('auth.createAccount')}
            </Title>
            <Text type="secondary">{t('auth.registerSubtitle')}</Text>
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
              rules={[
                { required: true, message: t('auth.passwordRequired') },
                { min: 8, message: t('header.passwordMin') },
              ]}
            >
              <Input.Password autoComplete="new-password" placeholder={t('header.passwordMin')} />
            </Form.Item>
            <Form.Item
              name="confirmPassword"
              label={t('auth.confirmPassword')}
              dependencies={['password']}
              rules={[
                { required: true, message: t('auth.confirmRequired') },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue('password') === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error(t('header.passwordMismatch')));
                  },
                }),
              ]}
            >
              <Input.Password autoComplete="new-password" placeholder={t('auth.repeatPassword')} />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              {t('auth.register')}
            </Button>
          </Form>
          <Text type="secondary">
            {t('auth.haveAccount')} <Link to="/login">{t('auth.signInLink')}</Link>
          </Text>
        </Space>
      </Card>
    </div>
  );
};

export default Register;
