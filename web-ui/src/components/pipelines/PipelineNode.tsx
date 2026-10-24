import React, { memo } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import { ClockCircleOutlined, CloseCircleOutlined, WarningOutlined } from '@ant-design/icons';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { pick, type PipelineFlowNode } from './graphModel';
import { portTypeLabel, StatusTag } from './PipelineStatus';

const PipelineNodeView: React.FC<NodeProps<PipelineFlowNode>> = ({ data, selected }) => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  const { spec, node, run, issues, readyInputs = [], issueLevel = 'error' } = data;
  const status = run?.status;
  const classes = [
    'pl-node',
    `pl-kind-${spec.kind}`,
    status ? `pl-node-${status}` : '',
    issues.length && !status ? `pl-node-issues pl-node-issues-${issueLevel}` : '',
    selected ? 'pl-node-selected' : '',
  ].filter(Boolean);
  return (
    <div className={classes.join(' ')}>
      <div className="pl-node-head">
        <span className="pl-node-title" title={pick(spec.title, language)}>
          {pick(spec.title, language)}
        </span>
        {spec.slow ? <ClockCircleOutlined className="pl-node-slow" aria-label={t('pipelines.inspector.slow')} /> : null}
        {status ? <StatusTag status={status} /> : null}
      </div>
      {node.label ? <div className="pl-node-label">{node.label}</div> : null}
      <div className="pl-ports">
        {spec.inputs.map((port) => (
          <div key={`in-${port.name}`} className="pl-port pl-port-in">
            <Handle
              type="target"
              position={Position.Left}
              id={port.name}
              className={`pl-handle${readyInputs.includes(port.name) ? ' pl-handle-ready' : ''}`}
              title={`${pick(port.title, language)} · ${port.types.join(', ')}`}
            />
            <span className="pl-port-name">{pick(port.title, language)}</span>
            <span className="pl-filetype" title={port.types.join(', ')}>{port.types.map(portTypeLabel).join(' / ')}</span>
          </div>
        ))}
        {spec.outputs.map((port) => (
          <div key={`out-${port.name}`} className="pl-port pl-port-out">
            <span className="pl-port-name">{pick(port.title, language)}</span>
            <span className="pl-filetype" title={port.type}>{portTypeLabel(port.type)}</span>
            <Handle
              type="source" position={Position.Right} id={port.name}
              className={`pl-handle${run?.outputs?.[port.name] ? ' pl-handle-ready' : ''}`}
              title={`${pick(port.title, language)} · ${port.type} · ${t(run?.outputs?.[port.name] ? 'pipelines.panel.outputReady' : 'pipelines.panel.outputPending')}`}
            />
          </div>
        ))}
      </div>
      {issues.length && !status ? (
        <div className={`pl-node-issue pl-node-issue-${issueLevel}`}>
          {issueLevel === 'warning' ? <WarningOutlined /> : <CloseCircleOutlined />} <span>{issues[0]}</span>
        </div>
      ) : null}
    </div>
  );
};

export default memo(PipelineNodeView);
