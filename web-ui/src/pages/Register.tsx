import React from 'react';
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd';
import { Link, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@store/auth';

const { Title, Text } = Typography;

const Register: React.FC = () => {
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
      setError(err?.message || 'Registration failed.');
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
          'radial-gradient(circle at 80% 0%, rgba(201,100,66,0.10), transparent 46%), var(--bg-primary)',
      }}
    >
      <Card
        style={{ width: '100%', maxWidth: 420, borderRadius: 12 }}
        bodyStyle={{ padding: 28 }}
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <div>
            <Title level={3} style={{ margin: 0 }}>
              Create Account
            </Title>
            <Text type="secondary">Email + password login for internal testing.</Text>
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
              rules={[
                { required: true, message: 'Password is required' },
                { min: 8, message: 'At least 8 characters' },
              ]}
            >
              <Input.Password autoComplete="new-password" placeholder="At least 8 characters" />
            </Form.Item>
            <Form.Item
              name="confirmPassword"
              label="Confirm Password"
              dependencies={['password']}
              rules={[
                { required: true, message: 'Please confirm password' },
                ({ getFieldValue }) => ({
                  validator(_, value) {
                    if (!value || getFieldValue('password') === value) {
                      return Promise.resolve();
                    }
                    return Promise.reject(new Error('Passwords do not match'));
                  },
                }),
              ]}
            >
              <Input.Password autoComplete="new-password" placeholder="Repeat password" />
            </Form.Item>
            <Button type="primary" htmlType="submit" block loading={loading}>
              Register
            </Button>
          </Form>
          <Text type="secondary">
            Already have an account? <Link to="/login">Sign in</Link>
          </Text>
        </Space>
      </Card>
    </div>
  );
};

export default Register;
