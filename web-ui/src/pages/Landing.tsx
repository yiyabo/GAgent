import React from 'react';
import { Button, Card, Col, Layout, Row, Space, Typography } from 'antd';
import {
  ExperimentOutlined,
  MessageOutlined,
  ProjectOutlined,
  SafetyOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuthStore } from '@store/auth';

const { Title, Text, Paragraph } = Typography;

const FullPageLoading = () => (
  <div
    style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'var(--bg-primary)',
    }}
  >
    <div
      style={{
        width: 32,
        height: 32,
        border: '3px solid var(--border-color)',
        borderTopColor: 'var(--primary-color)',
        borderRadius: '50%',
        animation: 'spin 0.8s linear infinite',
      }}
    />
    <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
  </div>
);

const Landing: React.FC = () => {
  const navigate = useNavigate();
  const { initialized, authenticated } = useAuthStore();

  if (!initialized) {
    return <FullPageLoading />;
  }

  if (authenticated) {
    return <Navigate to="/chat" replace />;
  }

  const features = [
    {
      icon: <MessageOutlined style={{ fontSize: 28, color: 'var(--primary-color)' }} />,
      title: '自然语言交互',
      description:
        '用自然语言描述分析需求，系统将自动拆解为可执行计划，无需编写复杂命令。',
    },
    {
      icon: <ProjectOutlined style={{ fontSize: 28, color: 'var(--primary-color)' }} />,
      title: '智能任务编排',
      description:
        '基于 DAG 的任务调度引擎，自动管理依赖关系、并行执行与结果汇聚。',
    },
    {
      icon: <ExperimentOutlined style={{ fontSize: 28, color: 'var(--primary-color)' }} />,
      title: '噬菌体基因组分析',
      description:
        '集成 PhageScope 等专业工具，支持注释、分类、系统发育与功能分析。',
    },
    {
      icon: <SafetyOutlined style={{ fontSize: 28, color: 'var(--primary-color)' }} />,
      title: '安全可控',
      description:
        '严格的权限隔离与审计追踪，确保数据安全与实验结果的可追溯性。',
    },
  ];

  return (
    <div style={{ minHeight: '100vh', background: 'var(--bg-primary)' }}>
      <Layout.Header
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          height: 64,
          padding: '0 48px',
          background: 'var(--bg-primary)',
          borderBottom: '1px solid var(--border-color)',
          position: 'sticky',
          top: 0,
          zIndex: 100,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ThunderboltOutlined style={{ fontSize: 20, color: 'var(--primary-color)' }} />
          <Text strong style={{ fontSize: 16, color: 'var(--text-primary)' }}>
            Phage-Agent
          </Text>
        </div>
        <Space size={12}>
          <Button type="text" onClick={() => navigate('/login')}>
            登录
          </Button>
          <Button type="primary" onClick={() => navigate('/register')}>
            注册
          </Button>
        </Space>
      </Layout.Header>

      <div
        style={{
          padding: '120px 24px 80px',
          textAlign: 'center',
          background:
            'radial-gradient(circle at 50% 0%, rgba(201,100,66,0.10), transparent 60%), var(--bg-primary)',
        }}
      >
        <Space direction="vertical" size={24} style={{ maxWidth: 720, margin: '0 auto' }}>
          <Title
            level={1}
            style={{
              margin: 0,
              fontSize: 'clamp(36px, 5vw, 52px)',
              fontWeight: 600,
              lineHeight: 1.2,
              color: 'var(--text-primary)',
            }}
          >
            Phage-Agent
          </Title>
          <Paragraph
            style={{
              margin: 0,
              fontSize: 'clamp(16px, 2vw, 20px)',
              color: 'var(--text-secondary)',
              lineHeight: 1.6,
            }}
          >
            AI 驱动的噬菌体基因组分析与任务编排平台
            <br />
            让自然语言请求自动转化为计划、工具调用与流式对话响应
          </Paragraph>
          <Space size={16} style={{ marginTop: 16 }}>
            <Button
              type="primary"
              size="large"
              onClick={() => navigate('/login')}
              style={{
                height: 48,
                padding: '0 36px',
                fontSize: 16,
                borderRadius: 'var(--radius-md)',
              }}
            >
              开始使用
            </Button>
            <Button
              size="large"
              onClick={() => navigate('/register')}
              style={{
                height: 48,
                padding: '0 36px',
                fontSize: 16,
                borderRadius: 'var(--radius-md)',
              }}
            >
              免费注册
            </Button>
          </Space>
        </Space>
      </div>

      <div style={{ padding: '60px 24px 80px', maxWidth: 1120, margin: '0 auto' }}>
        <Title
          level={3}
          style={{
            textAlign: 'center',
            marginBottom: 48,
            fontWeight: 600,
            color: 'var(--text-primary)',
          }}
        >
          核心能力
        </Title>
        <Row gutter={[24, 24]}>
          {features.map((f) => (
            <Col xs={24} sm={12} lg={12} key={f.title}>
              <Card
                bordered={false}
                style={{
                  height: '100%',
                  borderRadius: 'var(--radius-lg)',
                  background: 'var(--bg-secondary)',
                  boxShadow: 'var(--shadow-sm)',
                  transition: 'all 0.22s cubic-bezier(0.34, 1.56, 0.64, 1)',
                }}
                onMouseEnter={(e) => {
                  const el = e.currentTarget as HTMLDivElement;
                  el.style.transform = 'translateY(-4px)';
                  el.style.boxShadow = 'var(--shadow-lg)';
                }}
                onMouseLeave={(e) => {
                  const el = e.currentTarget as HTMLDivElement;
                  el.style.transform = 'translateY(0)';
                  el.style.boxShadow = 'var(--shadow-sm)';
                }}
              >
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <div>{f.icon}</div>
                  <Text strong style={{ fontSize: 16, color: 'var(--text-primary)' }}>
                    {f.title}
                  </Text>
                  <Text type="secondary" style={{ lineHeight: 1.7 }}>
                    {f.description}
                  </Text>
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
      </div>

      <div
        style={{
          padding: '80px 24px',
          textAlign: 'center',
          background:
            'radial-gradient(circle at 50% 100%, rgba(201,100,66,0.08), transparent 60%), var(--bg-tertiary)',
        }}
      >
        <Space direction="vertical" size={24} style={{ maxWidth: 560, margin: '0 auto' }}>
          <Title level={3} style={{ margin: 0, fontWeight: 600, color: 'var(--text-primary)' }}>
            准备好开始了吗？
          </Title>
          <Paragraph style={{ margin: 0, color: 'var(--text-secondary)', fontSize: 16 }}>
            立即注册，体验 AI 驱动的噬菌体基因组分析与任务编排。
          </Paragraph>
          <Space size={16}>
            <Button
              type="primary"
              size="large"
              onClick={() => navigate('/login')}
              style={{
                height: 48,
                padding: '0 36px',
                fontSize: 16,
                borderRadius: 'var(--radius-md)',
              }}
            >
              开始使用
            </Button>
            <Button
              size="large"
              onClick={() => navigate('/register')}
              style={{
                height: 48,
                padding: '0 36px',
                fontSize: 16,
                borderRadius: 'var(--radius-md)',
              }}
            >
              免费注册
            </Button>
          </Space>
        </Space>
      </div>

      <div
        style={{
          padding: '24px',
          textAlign: 'center',
          borderTop: '1px solid var(--border-color)',
          background: 'var(--bg-primary)',
        }}
      >
        <Text type="secondary" style={{ fontSize: 13 }}>
          © {new Date().getFullYear()} Phage-Agent. All rights reserved.
        </Text>
      </div>
    </div>
  );
};

export default Landing;
