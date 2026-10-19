import React, { Component, ErrorInfo, ReactNode } from 'react';
import { Result, Button } from 'antd';
import { CloseCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { translate } from '@/i18n/useT';

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
 * React error boundary component.
 * Catches runtime rendering errors and prevents full-page UI crashes.
 */
const IS_DEV = import.meta.env.DEV;

const isModuleImportError = (error: Error | null): boolean => {
  const message = error?.message.toLowerCase() ?? '';
  return (
    message.includes('importing a module script failed') ||
    message.includes('failed to fetch dynamically imported module') ||
    message.includes('loading chunk')
  );
};

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
  return {
  hasError: true,
  error,
  errorInfo: null,
  };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
  console.error('❌ ErrorBoundary caught an error:', error, errorInfo);

  this.setState({
  error,
  errorInfo,
  });

  // logErrorToService(error, errorInfo);
  }

  handleReset = () => {
  if (isModuleImportError(this.state.error)) {
  window.location.reload();
  return;
  }

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
  const moduleImportError = isModuleImportError(this.state.error);
  if (this.props.fallback) {
  return this.props.fallback;
  }

  return (
  <div style={{ padding: '48px 24px', maxWidth: '800px', margin: '0 auto' }}>
  <Result
  status="error"
  icon={<CloseCircleOutlined style={{ color: 'var(--status-error-fg)' }} />}
  title={translate('errorBoundary.crashed')}
  subTitle={moduleImportError ? translate('errorBoundary.moduleLoadFailed') : translate('errorBoundary.renderFailed')}
  extra={[
  <Button type="primary" key="reset" onClick={this.handleReset}>
  {moduleImportError ? translate('errorBoundary.reloadPage') : translate('errorBoundary.resetComponent')}
  </Button>,
  <Button key="reload" icon={<ReloadOutlined />} onClick={this.handleReload}>
  {translate('errorBoundary.reloadPage')}
  </Button>,
  ]}
  >
  {}
  {IS_DEV && this.state.error && (
  <div
  style={{
  textAlign: 'left',
  background: 'var(--surface-sunken)',
  padding: '16px',
  borderRadius: '4px',
  marginTop: '24px',
  }}
  >
  <h4 style={{ color: 'var(--status-error-fg)', marginBottom: '12px' }}>{translate('errorBoundary.devDetails')}</h4>
  <div
  style={{
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--status-error-fg)',
  marginBottom: '8px',
  }}
  >
  <strong>{translate('errorBoundary.errorLabel')}</strong> {this.state.error.message}
  </div>
  {this.state.error.stack && (
  <pre
  style={{
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-secondary)',
  background: 'var(--surface-card)',
  padding: '12px',
  borderRadius: '4px',
  overflow: 'auto',
  maxHeight: '300px',
  border: '1px solid var(--border-default)',
  }}
  >
  {this.state.error.stack}
  </pre>
  )}
  {this.state.errorInfo && (
  <details style={{ marginTop: '12px' }}>
  <summary style={{ cursor: 'pointer', color: 'var(--color-action)', fontWeight: 'bold' }}>
  {translate('errorBoundary.componentStack')}
  </summary>
  <pre
  style={{
  fontSize: '12px',
  fontFamily: 'var(--font-mono)',
  color: 'var(--text-secondary)',
  background: 'var(--surface-card)',
  padding: '12px',
  borderRadius: '4px',
  overflow: 'auto',
  maxHeight: '300px',
  border: '1px solid var(--border-default)',
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
