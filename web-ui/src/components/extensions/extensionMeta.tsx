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
import { translate, useT } from '@/i18n/useT';
import type { TranslationKey } from '@/i18n/keys';

/** Reason why the enable/disable switch is inactive, or null when it is usable. */
export function mcpSwitchBlockReason(args: {
  canManage: boolean;
  configured: boolean;
  state: McpState;
  unavailableReason?: string | null;
}): string | null {
  if (!args.canManage) return translate('extensions.adminOnly');
  if (!args.configured) return translate('extensions.mcpNotConfigured');
  if (args.state === 'unavailable') {
    return args.unavailableReason || translate('extensions.mcpCannotEnable');
  }
  return null;
}

export function skillSwitchBlockReason(canManage: boolean): string | null {
  return canManage ? null : translate('extensions.adminOnly');
}

const ISOLATION_META: Record<McpIsolation, { color: string; label: TranslationKey }> = {
  container: { color: 'green', label: 'extensions.isolationContainer' },
  host: { color: 'orange', label: 'extensions.isolationHost' },
  remote: { color: 'blue', label: 'extensions.isolationRemote' },
};

export const IsolationBadge: React.FC<{ isolation: McpIsolation | string }> = ({ isolation }) => {
  const t = useT();
  const known = ISOLATION_META[isolation as McpIsolation];
  const color = known?.color ?? 'default';
  const label = known ? t(known.label) : String(isolation);
  return (
    <Tag color={color} role="note" aria-label={t('extensions.isolationAria', { label })} style={{ marginInlineEnd: 0 }}>
      {label}
    </Tag>
  );
};

export const VerificationBadge: React.FC<{ status: string }> = ({ status }) => {
  const t = useT();
  const verified = status === 'verified';
  return (
    <Tag
      color={verified ? 'green' : 'default'}
      icon={verified ? <CheckCircleOutlined /> : <ExclamationCircleOutlined />}
      role="note"
      aria-label={verified ? t('extensions.verifiedAria') : t('extensions.unverifiedAria')}
      style={{ marginInlineEnd: 0 }}
    >
      {verified ? t('extensions.verified') : t('extensions.unverified')}
    </Tag>
  );
};

/** Honest marker: a missing human sign-off must never be hidden behind "verified". */
export const SignoffBadge: React.FC<{ humanSignoff: boolean }> = ({ humanSignoff }) => {
  const t = useT();
  return (
    <Tag
      color={humanSignoff ? 'cyan' : 'gold'}
      role="note"
      aria-label={humanSignoff ? t('extensions.signedOffAria') : t('extensions.signoffPendingAria')}
      style={{ marginInlineEnd: 0 }}
    >
      {humanSignoff ? t('extensions.signedOff') : t('extensions.signoffPending')}
    </Tag>
  );
};

const SCANNER_META: Record<SkillScanner, { color: string; label: TranslationKey }> = {
  passed: { color: 'green', label: 'extensions.scanPassed' },
  warnings: { color: 'orange', label: 'extensions.scanWarnings' },
  blocked: { color: 'red', label: 'extensions.scanBlocked' },
  'n/a': { color: 'default', label: 'extensions.scanNone' },
};

export const ScannerBadge: React.FC<{ scanner: SkillScanner | string }> = ({ scanner }) => {
  const t = useT();
  const known = SCANNER_META[scanner as SkillScanner];
  const label = known ? t(known.label) : String(scanner);
  return (
    <Tag color={known?.color ?? 'default'} role="note" aria-label={t('extensions.scanAria', { label })} style={{ marginInlineEnd: 0 }}>
      {label}
    </Tag>
  );
};

export const SeverityIcon: React.FC<{ severity: SkillFindingSeverity | string }> = ({ severity }) => {
  const t = useT();
  if (severity === 'block') {
    return <CloseCircleOutlined style={{ color: 'var(--status-error-fg)' }} aria-label={t('extensions.severityBlock')} role="img" />;
  }
  if (severity === 'warn') {
    return <ExclamationCircleOutlined style={{ color: 'var(--status-warning-fg)' }} aria-label={t('extensions.severityWarn')} role="img" />;
  }
  return <InfoCircleOutlined style={{ color: 'var(--color-action)' }} aria-label={t('extensions.severityInfo')} role="img" />;
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
