import React from 'react';
import { Tag } from 'antd';
import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  ExclamationCircleOutlined,
  InfoCircleOutlined,
} from '@ant-design/icons';
import type {
  McpIsolation,
  McpState,
  SkillFindingSeverity,
  SkillScanner,
} from '@api/extensions';

/** Reason why the enable/disable switch is inactive, or null when it is usable. */
export function mcpSwitchBlockReason(args: {
  canManage: boolean;
  configured: boolean;
  state: McpState;
  unavailableReason?: string | null;
}): string | null {
  if (!args.canManage) return '仅管理员可以启用或停用';
  if (!args.configured) return '服务端未配置 MCP 挂载文件，暂时无法启用或停用';
  if (args.state === 'unavailable') {
    return args.unavailableReason || '当前环境无法启用该服务器';
  }
  return null;
}

export function skillSwitchBlockReason(canManage: boolean): string | null {
  return canManage ? null : '仅管理员可以启用或停用';
}

const ISOLATION_META: Record<McpIsolation, { color: string; label: string }> = {
  container: { color: 'green', label: '容器隔离' },
  host: { color: 'orange', label: '本机进程 — 未隔离' },
  remote: { color: 'blue', label: '远程服务' },
};

export const IsolationBadge: React.FC<{ isolation: McpIsolation | string }> = ({ isolation }) => {
  const meta = ISOLATION_META[isolation as McpIsolation] ?? { color: 'default', label: String(isolation) };
  return (
    <Tag color={meta.color} role="note" aria-label={`隔离方式：${meta.label}`} style={{ marginInlineEnd: 0 }}>
      {meta.label}
    </Tag>
  );
};

export const VerificationBadge: React.FC<{ status: string }> = ({ status }) => {
  const verified = status === 'verified';
  return (
    <Tag
      color={verified ? 'green' : 'default'}
      icon={verified ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
      role="note"
      aria-label={verified ? '验证状态：已验证' : '验证状态：未验证'}
      style={{ marginInlineEnd: 0 }}
    >
      {verified ? '已验证' : '未验证'}
    </Tag>
  );
};

/** Honest marker: a missing human sign-off must never be hidden behind "verified". */
export const SignoffBadge: React.FC<{ humanSignoff: boolean }> = ({ humanSignoff }) => (
  <Tag
    color={humanSignoff ? 'cyan' : 'gold'}
    role="note"
    aria-label={humanSignoff ? '人工确认：已完成' : '人工确认：尚未完成'}
    style={{ marginInlineEnd: 0 }}
  >
    {humanSignoff ? '人工已确认' : '待人工确认'}
  </Tag>
);

const SCANNER_META: Record<SkillScanner, { color: string; label: string }> = {
  passed: { color: 'green', label: '扫描通过' },
  warnings: { color: 'orange', label: '有警告' },
  blocked: { color: 'red', label: '已拦截' },
  'n/a': { color: 'default', label: '未扫描' },
};

export const ScannerBadge: React.FC<{ scanner: SkillScanner | string }> = ({ scanner }) => {
  const meta = SCANNER_META[scanner as SkillScanner] ?? { color: 'default', label: String(scanner) };
  return (
    <Tag color={meta.color} role="note" aria-label={`扫描结果：${meta.label}`} style={{ marginInlineEnd: 0 }}>
      {meta.label}
    </Tag>
  );
};

export const SeverityIcon: React.FC<{ severity: SkillFindingSeverity | string }> = ({ severity }) => {
  if (severity === 'block') {
    return <CloseCircleOutlined style={{ color: '#ff4d4f' }} aria-label="严重程度：拦截" role="img" />;
  }
  if (severity === 'warn') {
    return <ExclamationCircleOutlined style={{ color: '#faad14' }} aria-label="严重程度：警告" role="img" />;
  }
  return <InfoCircleOutlined style={{ color: 'var(--color-action)' }} aria-label="严重程度：提示" role="img" />;
};

/** Only http(s) links are rendered as anchors; anything else stays plain text. */
export function safeHref(value: string | null | undefined): string | null {
  if (!value) return null;
  return /^https?:\/\//i.test(value.trim()) ? value.trim() : null;
}

export const ExternalLink: React.FC<{ href?: string | null; children?: React.ReactNode }> = ({ href, children }) => {
  const safe = safeHref(href);
  const text = children ?? href ?? '';
  if (!safe) return <span>{text}</span>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer">
      {text}
    </a>
  );
};

export function shortCommit(commit?: string | null): string {
  if (!commit) return '';
  return commit.length > 12 ? commit.slice(0, 10) : commit;
}
