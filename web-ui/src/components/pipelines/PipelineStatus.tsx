import React from 'react';
import { CheckCircleOutlined, ClockCircleOutlined, CloseCircleOutlined, InfoCircleOutlined, PauseCircleOutlined, WarningOutlined } from '@ant-design/icons';
import type { TranslationKey } from '@/i18n/keys';
import { useT } from '@/i18n/useT';

export const STATUS_KEYS: Record<string, TranslationKey> = {
  queued: 'pipelines.status.queued', running: 'pipelines.status.running',
  succeeded: 'pipelines.status.succeeded', failed: 'pipelines.status.failed',
  cancelled: 'pipelines.status.cancelled', pending: 'pipelines.status.pending',
  cached: 'pipelines.status.cached', skipped: 'pipelines.status.skipped',
  warning: 'pipelines.panel.warning',
};

export const statusTone = (status?: string | null) =>
  status === 'succeeded' || status === 'cached' ? 'success' : status === 'failed' ? 'error'
    : status === 'running' ? 'running' : status === 'warning' ? 'warning'
      : status === 'queued' || status === 'pending' ? 'pending' : 'neutral';

export const StatusTag: React.FC<{ status?: string | null }> = ({ status }) => {
  const t = useT();
  if (!status) return null;
  const tone = statusTone(status);
  const Icon = tone === 'success' ? CheckCircleOutlined : tone === 'error' ? CloseCircleOutlined
    : tone === 'warning' ? WarningOutlined : status === 'cancelled' || status === 'skipped' ? PauseCircleOutlined : ClockCircleOutlined;
  return <span className={`pl-status pl-status-${tone}`}><Icon />{t(STATUS_KEYS[status] || 'pipelines.status.pending')}</span>;
};

export const StatusLegend: React.FC = () => {
  const t = useT();
  return (
    <details className="pl-legend">
      <summary><InfoCircleOutlined /> {t('pipelines.panel.legend')}</summary>
      <div className="pl-legend-items">
        {['succeeded', 'failed', 'warning', 'running', 'pending', 'cancelled', 'skipped'].map((status) => <StatusTag key={status} status={status} />)}
        <span className="pl-selection-label">{t('pipelines.panel.selected')}</span>
      </div>
      <p>{t('pipelines.panel.statusMeaning')}</p>
    </details>
  );
};

export const portTypeLabel = (type: string): string => ({
  fasta_nt: 'FASTA·nt', fasta_aa: 'FASTA·aa', gff: 'GFF', table: 'TSV', report: 'MD',
  directory: 'DIR', blast_db_nt: 'BLAST·nt', blast_db_aa: 'BLAST·aa', hmm: 'HMM', any: '*',
}[type] || type);
