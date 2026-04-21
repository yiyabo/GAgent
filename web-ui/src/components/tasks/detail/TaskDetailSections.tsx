import React from 'react';
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';
import ToolResultCard from '@components/chat/ToolResultCard';
import type { DependencyPlanResponse, PlanResultItem, PlanTaskNode, ToolResultPayload } from '@/types';
import { statusColorMap, statusLabelMap } from './constants';
import { getVerificationView } from './verification';

const { Paragraph, Text, Title } = Typography;

// Clipboard fallback for non-HTTPS environments.
export function fallbackCopyToClipboard(text: string): boolean {
  const textArea = document.createElement('textarea');
  textArea.value = text;
  textArea.style.position = 'fixed';
  textArea.style.left = '-9999px';
  textArea.style.top = '-9999px';
  document.body.appendChild(textArea);
  textArea.focus();
  textArea.select();
  try {
    document.execCommand('copy');
    document.body.removeChild(textArea);
    return true;
  } catch (err) {
    console.error('Fallback copy failed:', err);
    document.body.removeChild(textArea);
    return false;
  }
}

export async function copyJsonToClipboard(
  value: unknown,
  successMessage: string,
  messageFn: { success: (msg: string) => void; error: (msg: string) => void },
) {
  try {
    const text = JSON.stringify(value, null, 2);
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      messageFn.success(successMessage);
    } else {
      if (fallbackCopyToClipboard(text)) {
        messageFn.success(successMessage);
      } else {
        messageFn.error('Copy failed, please copy manually');
      }
    }
  } catch (error) {
    console.warn('Copy failed', error);
    const text = JSON.stringify(value, null, 2);
    if (fallbackCopyToClipboard(text)) {
      messageFn.success(successMessage);
    } else {
      messageFn.error('Copy failed, please copy manually');
    }
  }
}

export function resolveTaskName(
  taskId: number,
  selectedTaskId: number | null,
  activeTask: PlanTaskNode | null,
  taskMap: Map<number, PlanTaskNode>,
  dependencyPlan: DependencyPlanResponse | null,
): string {
  if (taskId === selectedTaskId && activeTask?.name) {
    return activeTask.name;
  }
  const fromMap = taskMap.get(taskId)?.name;
  if (fromMap) {
    return fromMap;
  }
  const fromPlan =
    dependencyPlan?.missing_dependencies?.find((d) => d.id === taskId)?.name ??
    dependencyPlan?.running_dependencies?.find((d) => d.id === taskId)?.name;
  return fromPlan || `Task #${taskId}`;
}

export function resolveTaskStatus(
  taskId: number,
  taskMap: Map<number, PlanTaskNode>,
  dependencyPlan: DependencyPlanResponse | null,
): string {
  const fromMap = taskMap.get(taskId)?.status;
  if (fromMap) {
    return fromMap;
  }
  const fromPlan =
    dependencyPlan?.missing_dependencies?.find((d) => d.id === taskId)?.status ??
    dependencyPlan?.running_dependencies?.find((d) => d.id === taskId)?.status;
  return fromPlan || 'pending';
}

interface DependenciesProps {
  dependencies: number[] | undefined;
  onDependencyClick: (depId: number) => void;
  taskMap?: Map<number, PlanTaskNode>;
}

export const Dependencies: React.FC<DependenciesProps> = ({ dependencies, onDependencyClick, taskMap }) => {
  if (!dependencies || dependencies.length === 0) {
    return <Text type="secondary">No dependencies</Text>;
  }
  return (
    <Space wrap size={6}>
      {dependencies.map((dep) => {
        const depTask = taskMap?.get(dep);
        const depStatus = depTask?.effective_status ?? depTask?.status;
        return (
          <Button
            key={dep}
            size="small"
            type="link"
            onClick={() => onDependencyClick(dep)}
            style={{ padding: '0 4px', height: 'auto' }}
          >
            Task #{dep}
            {depStatus && (
              <Tag
                color={statusColorMap[depStatus] ?? 'default'}
                style={{ marginLeft: 4, fontSize: 11, lineHeight: '16px', padding: '0 4px' }}
              >
                {statusLabelMap[depStatus] ?? depStatus}
              </Tag>
            )}
          </Button>
        );
      })}
    </Space>
  );
};

interface ContextSectionsProps {
  sections: any[] | undefined;
}

export const ContextSections: React.FC<ContextSectionsProps> = ({ sections }) => {
  if (!Array.isArray(sections) || sections.length === 0) {
    return null;
  }
  const items = sections.map((section, index) => {
    const title =
      typeof section?.title === 'string' && section.title.trim().length > 0
        ? section.title
        : `Section ${index + 1}`;
    const content =
      typeof section?.content === 'string'
        ? section.content
        : JSON.stringify(section, null, 2);
    return {
      key: String(index),
      label: title,
      children: <Paragraph style={{ whiteSpace: 'pre-wrap' }}>{content}</Paragraph>,
    };
  });
  return <Collapse size="small" bordered={false} items={items} />;
};

interface ExecutionResultProps {
  resultLoading: boolean;
  taskResult: PlanResultItem | undefined;
  cachedResult: PlanResultItem | undefined;
  onReverify?: (() => void) | null;
  onManualAccept?: (() => void) | null;
  verifyLoading?: boolean;
  manualAcceptLoading?: boolean;
  canVerify?: boolean;
  canManualAccept?: boolean;
}

interface TaskDrawerContentProps {
  activeTask: PlanTaskNode;
  handleDependencyClick: (depId: number) => void;
  recentToolResults: ToolResultPayload[];
  resultLoading: boolean;
  taskResult: PlanResultItem | undefined;
  cachedResult: PlanResultItem | undefined;
  onReverify?: (() => void) | null;
  onManualAccept?: (() => void) | null;
  verifyLoading?: boolean;
  manualAcceptLoading?: boolean;
  canVerify?: boolean;
  canManualAccept?: boolean;
  taskMap?: Map<number, PlanTaskNode>;
}

export const TaskDrawerContent: React.FC<TaskDrawerContentProps> = ({
  activeTask,
  handleDependencyClick,
  recentToolResults,
  resultLoading,
  taskResult,
  cachedResult,
  onReverify,
  onManualAccept,
  verifyLoading = false,
  manualAcceptLoading = false,
  canVerify = false,
  canManualAccept = false,
  taskMap,
}) => {
  const effectiveStatus = activeTask.effective_status ?? activeTask.status ?? 'pending';
  const isBlocked = effectiveStatus === 'blocked';
  const hasTimestamps = Boolean(activeTask.created_at) || Boolean(activeTask.updated_at);

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <section>
        <Title level={5}>Status</Title>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Space wrap size={8}>
            <Tag color={statusColorMap[effectiveStatus] ?? 'default'}>
              {statusLabelMap[effectiveStatus] ?? effectiveStatus}
            </Tag>
            {activeTask.parent_id != null && (
              <Text type="secondary">
                Parent:{' '}
                <Button
                  type="link"
                  size="small"
                  style={{ padding: 0, height: 'auto' }}
                  onClick={() => handleDependencyClick(activeTask.parent_id!)}
                >
                  Task #{activeTask.parent_id}
                </Button>
              </Text>
            )}
          </Space>
          {isBlocked && activeTask.status_reason && (
            <div
              style={{
                background: '#fff7e6',
                border: '1px solid #ffd591',
                borderRadius: 6,
                padding: '8px 12px',
                fontSize: 13,
              }}
            >
              <Text type="warning" style={{ fontWeight: 500 }}>⏳ Blocked: </Text>
              <Text>{activeTask.status_reason}</Text>
            </div>
          )}
          {isBlocked && !activeTask.status_reason && activeTask.incomplete_dependencies && activeTask.incomplete_dependencies.length > 0 && (
            <div
              style={{
                background: '#fff7e6',
                border: '1px solid #ffd591',
                borderRadius: 6,
                padding: '8px 12px',
                fontSize: 13,
              }}
            >
              <Text type="warning" style={{ fontWeight: 500 }}>⏳ Waiting for: </Text>
              {activeTask.incomplete_dependencies.map((depId, idx) => (
                <React.Fragment key={depId}>
                  {idx > 0 && ', '}
                  <Button
                    type="link"
                    size="small"
                    style={{ padding: 0, height: 'auto', fontSize: 13 }}
                    onClick={() => handleDependencyClick(depId)}
                  >
                    Task #{depId}
                  </Button>
                </React.Fragment>
              ))}
            </div>
          )}
        </Space>
      </section>

      <section>
        <Title level={5}>Task Content</Title>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <div>
            <Text type="secondary">Instruction</Text>
            <Paragraph
              style={{ whiteSpace: 'pre-wrap' }}
              copyable
              ellipsis={{ rows: 6, expandable: true, symbol: 'Expand' }}
            >
              {activeTask.instruction || 'No description available'}
            </Paragraph>
          </div>
          <div>
            <Text type="secondary">Dependencies</Text>
            <Dependencies
              dependencies={activeTask.dependencies}
              onDependencyClick={handleDependencyClick}
              taskMap={taskMap}
            />
          </div>
        </Space>
      </section>

      <section>
        <Title level={5}>Context</Title>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {activeTask.context_combined ? (
            <Paragraph
              style={{ whiteSpace: 'pre-wrap' }}
              copyable
              ellipsis={{ rows: 6, expandable: true, symbol: 'Expand' }}
            >
              {activeTask.context_combined}
            </Paragraph>
          ) : (
            <Text type="secondary">No context summary available</Text>
          )}
          <ContextSections sections={activeTask.context_sections} />
          {activeTask.context_meta && Object.keys(activeTask.context_meta).length > 0 && (
            <Paragraph
              code
              copyable
              style={{ maxHeight: 200, overflow: 'auto' }}
            >
              {JSON.stringify(activeTask.context_meta, null, 2)}
            </Paragraph>
          )}
        </Space>
      </section>

      {recentToolResults.length > 0 && (
        <section>
          <Title level={5}>Recent Tool Summaries</Title>
          <Space direction="vertical" size="small" style={{ width: '100%' }}>
            {recentToolResults.map((result, index) => (
              <ToolResultCard
                key={`${result.name ?? 'tool'}_${index}`}
                payload={result}
                defaultOpen={index === 0}
              />
            ))}
          </Space>
        </section>
      )}

      <Collapse
        size="small"
        bordered={false}
        items={[
          ...(activeTask.metadata && Object.keys(activeTask.metadata).length > 0
            ? [{
                key: 'metadata',
                label: 'Metadata',
                children: (
                  <Paragraph
                    code
                    copyable
                    style={{ maxHeight: 200, overflow: 'auto' }}
                  >
                    {JSON.stringify(activeTask.metadata, null, 2)}
                  </Paragraph>
                ),
              }]
            : []),
          ...(hasTimestamps
            ? [{
                key: 'timestamps',
                label: 'Details',
                children: (
                  <Descriptions column={1} size="small">
                    <Descriptions.Item label="Type">{activeTask.task_type ?? 'Unknown'}</Descriptions.Item>
                    <Descriptions.Item label="Depth">{activeTask.depth ?? 0}</Descriptions.Item>
                    {activeTask.created_at && (
                      <Descriptions.Item label="Created">{new Date(activeTask.created_at).toLocaleString()}</Descriptions.Item>
                    )}
                    {activeTask.updated_at && (
                      <Descriptions.Item label="Updated">{new Date(activeTask.updated_at).toLocaleString()}</Descriptions.Item>
                    )}
                  </Descriptions>
                ),
              }]
            : []),
        ]}
      />

      <section>
        <Title level={5}>Execution Result</Title>
        <ExecutionResult
          resultLoading={resultLoading}
          taskResult={taskResult}
          cachedResult={cachedResult}
          onReverify={onReverify}
          onManualAccept={onManualAccept}
          verifyLoading={verifyLoading}
          manualAcceptLoading={manualAcceptLoading}
          canVerify={canVerify}
          canManualAccept={canManualAccept}
        />
      </section>
    </Space>
  );
};

export const ExecutionResult: React.FC<ExecutionResultProps> = ({
  resultLoading,
  taskResult,
  cachedResult,
  onReverify,
  onManualAccept,
  verifyLoading = false,
  manualAcceptLoading = false,
  canVerify = false,
  canManualAccept = false,
}) => {
  if (resultLoading && !taskResult && !cachedResult) {
    return (
      <div style={{ padding: '12px 0' }}>
        <Spin tip="Loading execution result..." />
      </div>
    );
  }

  const result = taskResult ?? cachedResult;
  if (!result) {
    return <Text type="secondary">No execution result available</Text>;
  }
  const verification = getVerificationView(result);
  const executionStatus = String(
    result.metadata?.execution_status ?? result.status ?? ''
  )
    .trim()
    .toLowerCase();
  const failureKind = String(result.metadata?.failure_kind ?? '')
    .trim()
    .toLowerCase();
  const executionCompleted = executionStatus === 'completed' || executionStatus === 'done' || executionStatus === 'success';
  const artifactVerification =
    result.metadata && typeof result.metadata.artifact_verification === 'object'
      ? (result.metadata.artifact_verification as Record<string, any>)
      : null;
  const producedFiles = Array.isArray(artifactVerification?.actual_outputs)
    ? (artifactVerification?.actual_outputs as unknown[])
        .map((item) => String(item ?? '').trim())
        .filter((item) => item.length > 0)
    : [];
  const expectedFiles = Array.isArray(artifactVerification?.expected_deliverables)
    ? (artifactVerification?.expected_deliverables as unknown[])
        .map((item) => String(item ?? '').trim())
        .filter((item) => item.length > 0)
    : [];
  const manualAcceptance =
    result.metadata && typeof result.metadata.manual_acceptance === 'object'
      ? (result.metadata.manual_acceptance as Record<string, any>)
      : null;
  const manualAccepted = Boolean(
    manualAcceptance && (
      String(manualAcceptance.status ?? '').trim().toLowerCase() === 'accepted' ||
      manualAcceptance.accepted === true
    )
  );

  return (
    <Space direction="vertical" size="small" style={{ width: '100%' }}>
      <Space wrap>
        {result.status && (
          <Tag color={statusColorMap[result.status] ?? 'default'}>
            {statusLabelMap[result.status] ?? result.status}
          </Tag>
        )}
        {verification.status && verification.label && (
          <Tag color={verification.color}>{verification.label}</Tag>
        )}
        {manualAccepted && <Tag color="blue">Manually accepted</Tag>}
        {canVerify && onReverify && (
          <Button size="small" onClick={onReverify} loading={verifyLoading}>
            Re-verify
          </Button>
        )}
        {canManualAccept && onManualAccept && (
          <Button size="small" onClick={onManualAccept} loading={manualAcceptLoading}>
            Accept manually
          </Button>
        )}
      </Space>
      {verification.status && (
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="Verification checks">
            {verification.checksPassed}/{verification.checksTotal}
          </Descriptions.Item>
          <Descriptions.Item label="Blocking">
            {verification.blocking ? 'yes' : 'no'}
          </Descriptions.Item>
          <Descriptions.Item label="Generated checks">
            {verification.generated ? 'yes' : 'no'}
          </Descriptions.Item>
        </Descriptions>
      )}
      {executionCompleted && verification.status === 'failed' && (
        <Alert
          type="warning"
          showIcon
          message="Execution completed, but verification failed"
          description={
            failureKind === 'contract_mismatch'
              ? 'The task produced files, but they did not match the expected deliverable contract, so the task remains failed until the outputs are corrected or accepted manually.'
              : 'The task produced output, but deterministic verification did not pass, so the task remains failed until the result is corrected or accepted manually.'
          }
        />
      )}
      {result.content && (
        <Paragraph style={{ whiteSpace: 'pre-wrap' }} copyable>
          {result.content}
        </Paragraph>
      )}
      {producedFiles.length > 0 && (
        <Collapse
          size="small"
          items={[
            {
              key: 'produced-files',
              label: `Produced files (${producedFiles.length})`,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {producedFiles.map((item) => (
                    <Paragraph key={item} copyable style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                      {item}
                    </Paragraph>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
      {expectedFiles.length > 0 && verification.status === 'failed' && (
        <Collapse
          size="small"
          items={[
            {
              key: 'expected-files',
              label: `Expected deliverables (${expectedFiles.length})`,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {expectedFiles.map((item) => (
                    <Paragraph key={item} copyable style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                      {item}
                    </Paragraph>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
      {verification.failures.length > 0 && (
        <Collapse
          size="small"
          items={[
            {
              key: 'verification-failures',
              label: `Verification failures (${verification.failures.length})`,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {verification.failures.map((failure, idx) => {
                    const parts = [
                      typeof failure.type === 'string' ? failure.type : 'check',
                      typeof failure.path === 'string' ? failure.path : null,
                      typeof failure.message === 'string' ? failure.message : null,
                    ].filter(Boolean);
                    return (
                      <Paragraph key={idx} style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                        {parts.join(' | ')}
                      </Paragraph>
                    );
                  })}
                </Space>
              ),
            },
          ]}
        />
      )}
      {verification.artifactPaths.length > 0 && (
        <Collapse
          size="small"
          items={[
            {
              key: 'verification-evidence',
              label: `Verification evidence (${verification.artifactPaths.length})`,
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  {verification.artifactPaths.map((item) => (
                    <Paragraph key={item} copyable style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                      {item}
                    </Paragraph>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
      {Array.isArray(result.notes) && result.notes.length > 0 && (
        <Collapse
          size="small"
          items={[
            {
              key: 'notes',
              label: `Notes (${result.notes.length})`,
              children: (
                <Space direction="vertical">
                  {result.notes.map((note, idx) => (
                    <Paragraph key={idx} style={{ whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                      {note}
                    </Paragraph>
                  ))}
                </Space>
              ),
            },
          ]}
        />
      )}
      {result.metadata && Object.keys(result.metadata).length > 0 && (
        <Paragraph
          code
          copyable
          style={{ maxHeight: 200, overflow: 'auto' }}
        >
          {JSON.stringify(result.metadata, null, 2)}
        </Paragraph>
      )}
    </Space>
  );
};
