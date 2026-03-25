import React from 'react';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@store/auth';

const { Title, Text } = Typography;

const Login: React.FC = () => {
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
      setError(err?.message || 'Login failed. Please check your credentials.');
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 24,
        background:
          'radial-gradient(circle at 20% 20%, rgba(201,100,66,0.12), transparent 50%), var(--bg-primary)',
      }}
    >
      <Card
        style={{ width: '100%', maxWidth: 420, borderRadius: 12 }}
        bodyStyle={{ padding: 28 }}
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <div>
            <Title level={3} style={{ margin: 0 }}>
              Sign In
            </Title>
            <Text type="secondary">Use your email and password to continue.</Text>
          </div>
          {error ? <Alert type="error" message={error} showIcon /> : null}
          <Form layout="vertical" onFinish={onFinish}>
            <Form.Item
              name="email"
              label="Email"
              rules={[
                { required: true, message: 'Email is required' },
                { type: 'email', message: 'Invalid email' },
              ]}
            >
              <Input autoComplete="email" placeholder="you@example.com" />
            </Form.Item>
            <Form.Item
              name="password"
              label="Password"
              rules={[{ required: true, message: 'Password is required' }]}
            >
              <Input.Password autoComplete="current-password" placeholder="Your password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              Sign In
            </Button>
          </Form>
          <Text type="secondary">
            No account yet? <Link to="/register">Create one</Link>
          </Text>
        </Space>
      </Card>
    </div>
  );
};

export default Login;
