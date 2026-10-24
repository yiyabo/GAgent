import React from 'react';
import { Divider, Input, Typography } from 'antd';
import { CloseCircleOutlined, WarningOutlined } from '@ant-design/icons';
import type { PipelineRun } from '@api/pipelines';
import { useT } from '@/i18n/useT';
import type { ShownIssue } from './issues';
import { StatusTag, formatServerTime } from './RunPanel';

const { Text, Paragraph } = Typography;

interface Props {
  description: string;
  onDescriptionChange: (value: string) => void;
  issues: ShownIssue[];
  nodeName: (id: string) => string;
  onSelectNode: (id: string) => void;
  runs: PipelineRun[] | undefined;
  onOpenRun: (runId: string) => void;
}

/** What the side panel shows while no node is selected: the pipeline itself, its problems and its runs. */
const PipelineSidePanel: React.FC<Props> = ({ description, onDescriptionChange, issues, nodeName, onSelectNode, runs, onOpenRun }) => {
  const t = useT();
  return (
    <div className="pl-inspector">
      <Text type="secondary">{t('pipelines.inspector.pipelineTitle')}</Text>
      <label className="pl-field-label" htmlFor="pl-description">
        {t('pipelines.inspector.description')}
      </label>
      <Input.TextArea
        id="pl-description"
        autoSize={{ minRows: 2, maxRows: 6 }}
        maxLength={2000}
        placeholder={t('pipelines.inspector.descriptionPlaceholder')}
        value={description}
        onChange={(event) => onDescriptionChange(event.target.value)}
      />

      <Divider style={{ margin: '12px 0' }} />
      <Text strong>{t('pipelines.inspector.problemsTitle')}</Text>
      {issues.length === 0 ? (
        <Paragraph type="secondary" className="pl-hint">
          {t('pipelines.inspector.noProblems')}
        </Paragraph>
      ) : (
        <ul className="pl-issue-list">
          {issues.map((issue, index) => (
            <li key={`${issue.node || ''}-${index}`} className={`pl-issue pl-issue-${issue.level}`}>
              {issue.level === 'error' ? <CloseCircleOutlined /> : <WarningOutlined />}
              <span>
                {issue.node ? (
                  <button type="button" className="pl-link" onClick={() => onSelectNode(issue.node as string)}>
                    {nodeName(issue.node)}
                  </button>
                ) : null}
                {issue.text}
              </span>
            </li>
          ))}
        </ul>
      )}

      <Divider style={{ margin: '12px 0' }} />
      <Text strong>{t('pipelines.inspector.runsTitle')}</Text>
      {!runs?.length ? (
        <Paragraph type="secondary" className="pl-hint">
          {t('pipelines.inspector.noRuns')}
        </Paragraph>
      ) : (
        <div className="pl-run-list">
          {runs.map((run) => (
            <button key={run.id} type="button" className="pl-run-row" onClick={() => onOpenRun(run.id)}>
              <StatusTag status={run.status} />
              <span>{t('pipelines.run_view.versionOf', { version: run.version })}</span>
              <span className="pl-run-time">{formatServerTime(run.created_at)}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default PipelineSidePanel;
