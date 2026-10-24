import React, { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ClockCircleOutlined, WarningOutlined } from '@ant-design/icons';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import type { TranslationKey } from '@/i18n/keys';
import { pick, type PipelineFlowNode } from './graphModel';

export const STATUS_KEYS: Record<string, TranslationKey> = {
  queued: 'pipelines.status.queued',
  running: 'pipelines.status.running',
  succeeded: 'pipelines.status.succeeded',
  failed: 'pipelines.status.failed',
  cancelled: 'pipelines.status.cancelled',
  pending: 'pipelines.status.pending',
  cached: 'pipelines.status.cached',
  skipped: 'pipelines.status.skipped',
};

const PipelineNodeView: React.FC<NodeProps<PipelineFlowNode>> = ({ data, selected }) => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  const { spec, node, run, issues } = data;
  const status = run?.status;
  const classes = [
    'pl-node',
    `pl-kind-${spec.kind}`,
    status ? `pl-node-${status}` : '',
    issues.length && !status ? 'pl-node-issues' : '',
    selected ? 'pl-node-selected' : '',
  ].filter(Boolean);
  return (
    <div className={classes.join(' ')}>
      <div className="pl-node-head">
        <span className="pl-node-title" title={pick(spec.title, language)}>
          {pick(spec.title, language)}
        </span>
        {spec.slow ? <ClockCircleOutlined className="pl-node-slow" aria-label={t('pipelines.inspector.slow')} /> : null}
        {status ? <span className={`pl-pill pl-pill-${status}`}>{t(STATUS_KEYS[status] || 'pipelines.status.pending')}</span> : null}
      </div>
      {node.label ? <div className="pl-node-label">{node.label}</div> : null}
      <div className="pl-ports">
        {spec.inputs.map((port) => (
          <div key={`in-${port.name}`} className="pl-port pl-port-in">
            <Handle
              type="target"
              position={Position.Left}
              id={port.name}
              className={`pl-handle pl-type-${port.types[0]}`}
            />
            <span className="pl-port-name">{pick(port.title, language)}</span>
          </div>
        ))}
        {spec.outputs.map((port) => (
          <div key={`out-${port.name}`} className="pl-port pl-port-out">
            <span className="pl-port-name">{pick(port.title, language)}</span>
            <Handle type="source" position={Position.Right} id={port.name} className={`pl-handle pl-type-${port.type}`} />
          </div>
        ))}
      </div>
      {issues.length && !status ? (
        <div className="pl-node-issue">
          <WarningOutlined /> <span>{issues[0]}</span>
        </div>
      ) : null}
    </div>
  );
};

export default memo(PipelineNodeView);
