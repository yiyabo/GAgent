import React, { Component, ErrorInfo, ReactNode } from 'react';
import { Result, Button } from 'antd';
import { CloseCircleOutlined, ReloadOutlined } from '@ant-design/icons';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

/**
 * React错误边界组件
 * 捕获子组件树中的JavaScript错误,记录错误,并显示备用UI
 */
const IS_DEV = import.meta.env.DEV;

class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
  }

  static getDerivedStateFromError(error: Error): State {
    // 更新state以在下一次渲染时显示备用UI
    return {
      hasError: true,
      error,
      errorInfo: null,
    };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // 记录错误详情
    console.error('❌ ErrorBoundary caught an error:', error, errorInfo);

    this.setState({
      error,
      errorInfo,
    });

    // TODO: 可以在这里将错误发送到错误报告服务
    // logErrorToService(error, errorInfo);
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  handleReload = () => {
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      // 如果提供了自定义fallback UI,使用它
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // 默认错误UI
      return (
        <div style={{ padding: '48px 24px', maxWidth: '800px', margin: '0 auto' }}>
          <Result
            status="error"
            icon={<CloseCircleOutlined style={{ color: '#ff4d4f' }} />}
            title="组件渲染失败"
            subTitle="抱歉,组件在渲染时遇到了错误。请尝试刷新页面或联系技术支持。"
            extra={[
              <Button type="primary" key="reset" onClick={this.handleReset}>
                重置组件
              </Button>,
              <Button key="reload" icon={<ReloadOutlined />} onClick={this.handleReload}>
                刷新页面
              </Button>,
            ]}
          >
            {/* 开发环境下显示错误详情 */}
            {IS_DEV && this.state.error && (
              <div
                style={{
                  textAlign: 'left',
                  background: '#fafafa',
                  padding: '16px',
                  borderRadius: '4px',
                  marginTop: '24px',
                }}
              >
                <h4 style={{ color: '#ff4d4f', marginBottom: '12px' }}>错误详情 (仅开发环境显示)</h4>
                <div
                  style={{
                    fontSize: '12px',
                    fontFamily: 'monospace',
                    color: '#d32f2f',
                    marginBottom: '8px',
                  }}
                >
                  <strong>错误信息:</strong> {this.state.error.message}
                </div>
                {this.state.error.stack && (
                  <pre
                    style={{
                      fontSize: '11px',
                      fontFamily: 'monospace',
                      color: '#666',
                      background: '#fff',
                      padding: '12px',
                      borderRadius: '4px',
                      overflow: 'auto',
                      maxHeight: '300px',
                      border: '1px solid #d9d9d9',
                    }}
                  >
                    {this.state.error.stack}
                  </pre>
                )}
                {this.state.errorInfo && (
                  <details style={{ marginTop: '12px' }}>
                    <summary style={{ cursor: 'pointer', color: '#1890ff', fontWeight: 'bold' }}>
                      组件堆栈信息
                    </summary>
                    <pre
                      style={{
                        fontSize: '11px',
                        fontFamily: 'monospace',
                        color: '#666',
                        background: '#fff',
                        padding: '12px',
                        borderRadius: '4px',
                        overflow: 'auto',
                        maxHeight: '300px',
                        border: '1px solid #d9d9d9',
                        marginTop: '8px',
                      }}
                    >
                      {this.state.errorInfo.componentStack}
                    </pre>
                  </details>
                )}
              </div>
            )}
          </Result>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
