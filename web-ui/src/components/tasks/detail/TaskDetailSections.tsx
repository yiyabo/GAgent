import React from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Collapse,
  Descriptions,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from 'antd';
import { CopyOutlined, FileOutlined, LinkOutlined } from '@ant-design/icons';
import ToolResultCard from '@components/chat/ToolResultCard';
import { ArtifactPreviewModal } from '@components/layout/artifactPreview';
import { buildDeliverableFileUrl } from '@api/artifacts';
import { useChatStore } from '@store/chat';
import type { DependencyPlanResponse, PlanResultItem, PlanTaskNode, ToolResultPayload } from '@/types';
import type { TaskTokenUsageItem } from '@/api/stats';
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

const FAILURE_KIND_LABELS: Record<string, string> = {
  contract_mismatch: 'Deliverable contract mismatch',
  execution_failed: 'Execution failed',
  blocked_dependency: 'Blocked by dependency',
  verification_config_error: 'Verification configuration error',
};

export function humanizeFailureKind(kind: string): string {
  const normalized = kind.trim().toLowerCase();
  if (!normalized) {
    return '';
  }
  if (FAILURE_KIND_LABELS[normalized]) {
    return FAILURE_KIND_LABELS[normalized];
  }
  return normalized
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

const REPAIR_NOTE_KEYS = ['repair_message', 'fix_message', 'repair_note', 'message'];

export function resolveRepairNote(metadata: Record<string, any> | null | undefined): string | null {
  if (!metadata || typeof metadata !== 'object') {
    return null;
  }
  for (const key of REPAIR_NOTE_KEYS) {
    const value = metadata[key];
    if (typeof value === 'string' && value.trim().length > 0) {
      return value.trim();
    }
  }
  return null;
}

interface ResultTechnicalView {
  producedFiles: string[];
  expectedFiles: string[];
  verificationRaw: Record<string, any> | null;
  resultMetadata: Record<string, any> | null;
}

export function getResultTechnicalView(result: PlanResultItem | null | undefined): ResultTechnicalView {
  const metadata =
    result?.metadata && typeof result.metadata === 'object' ? result.metadata : null;
  const artifactVerification =
    metadata && typeof metadata.artifact_verification === 'object'
      ? (metadata.artifact_verification as Record<string, any>)
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
  const verificationRaw =
    metadata && typeof metadata.verification === 'object'
      ? (metadata.verification as Record<string, any>)
      : null;
  return {
    producedFiles,
    expectedFiles,
    verificationRaw,
    resultMetadata: metadata && Object.keys(metadata).length > 0 ? metadata : null,
  };
}

interface PublishedArtifactEntry {
  key: string;
  alias: string;
  fileName: string;
  pathTail: string;
  relativePath: string;
  extension: string;
  version?: string;
  href: string | null;
  rawPath: string | null;
}

export function collectPublishedArtifacts(
  result: PlanResultItem | null | undefined,
  sessionId: string | null | undefined,
): PublishedArtifactEntry[] {
  const metadata = result?.metadata;
  const published =
    metadata && typeof metadata.published_artifacts === 'object'
      ? (metadata.published_artifacts as Record<string, unknown>)
      : null;
  if (!published) {
    return [];
  }
  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  const entries: PublishedArtifactEntry[] = [];
  const seen = new Set<string>();
  for (const [key, value] of Object.entries(published)) {
    if (!value || typeof value !== 'object') {
      continue;
    }
    const item = value as Record<string, unknown>;
    const contractPath = key.startsWith('contract:') ? key.slice('contract:'.length).trim() : '';
    const alias = String(item.alias ?? '').trim() || (contractPath ? '' : key.trim());
    const displayPath = String(item.deliverable_path ?? item.path ?? item.source_path ?? '').trim();
    const segments = displayPath.split('/').filter(Boolean);
    const contractSegments = contractPath.split('/').filter(Boolean);
    const fileName =
      segments[segments.length - 1] ?? contractSegments[contractSegments.length - 1] ?? alias ?? 'artifact';
    const pathTail =
      segments.length > 1 ? segments.slice(-2).join('/') : displayPath || contractPath;
    const relativePath =
      String(item.deliverable_path ?? '').trim() ||
      contractPath ||
      (segments[segments.length - 1] ?? '');
    // Task-level published paths are absolute container paths under the session
    // dir (e.g. /app/runtime/<sid>/_scratch/...). Derive the session-relative
    // path so the preview can fall back to the raw artifacts endpoints.
    const absolutePath = String(item.path ?? '').trim();
    let rawPath: string | null = null;
    if (sid && absolutePath) {
      const marker = `/${sid}/`;
      const markerIndex = absolutePath.indexOf(marker);
      if (markerIndex >= 0) {
        const sessionRelative = absolutePath.slice(markerIndex + marker.length).trim();
        if (sessionRelative.length > 0) {
          rawPath = sessionRelative;
        }
      }
    }
    const version =
      typeof item.version === 'string' && item.version.trim().length > 0
        ? item.version.trim()
        : undefined;
    const href =
      sid && relativePath
        ? buildDeliverableFileUrl(sid, relativePath, version ? { version } : undefined)
        : null;
    const dotIndex = fileName.lastIndexOf('.');
    const extension = dotIndex > 0 ? fileName.slice(dotIndex + 1).toLowerCase() : '';
    const dedupeKey = href ?? `${key}::${displayPath}`;
    if (seen.has(dedupeKey)) {
      continue;
    }
    seen.add(dedupeKey);
    entries.push({ key, alias, fileName, pathTail, relativePath, extension, version, href, rawPath });
  }
  return entries;
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

interface PublishedArtifactsSectionProps {
  result: PlanResultItem | undefined;
}

export const PublishedArtifactsSection: React.FC<PublishedArtifactsSectionProps> = ({ result }) => {
  const sessionId = useChatStore(
    (state) => state.currentSession?.session_id ?? state.currentSession?.id ?? null
  );
  const entries = React.useMemo(
    () => collectPublishedArtifacts(result, sessionId),
    [result, sessionId]
  );
  const [previewEntry, setPreviewEntry] = React.useState<PublishedArtifactEntry | null>(null);
  if (entries.length === 0) {
    return null;
  }
  return (
    <section>
      <Title level={5}>Published Artifacts ({entries.length})</Title>
      <Space direction="vertical" size={8} style={{ width: '100%' }}>
        {entries.map((entry) => (
          <Card
            key={entry.key}
            size="small"
            hoverable={Boolean(entry.href)}
            onClick={entry.href ? () => setPreviewEntry(entry) : undefined}
            style={{ cursor: entry.href ? 'pointer' : 'default' }}
          >
            <Space direction="vertical" size={2} style={{ width: '100%' }}>
              <Space size={6} wrap>
                <FileOutlined />
                <Text strong>{entry.fileName}</Text>
                {entry.alias && entry.alias !== entry.fileName && <Tag>{entry.alias}</Tag>}
                {entry.href && (
                  <Tooltip title="Open in new tab">
                    <a
                      href={entry.href}
                      target="_blank"
                      rel="noreferrer"
                      aria-label={`Open ${entry.fileName} in new tab`}
                      onClick={(event) => event.stopPropagation()}
                    >
                      <LinkOutlined />
                    </a>
                  </Tooltip>
                )}
              </Space>
              {entry.pathTail && entry.pathTail !== entry.fileName && (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {entry.pathTail}
                </Text>
              )}
            </Space>
          </Card>
        ))}
      </Space>
      {previewEntry && (
        <ArtifactPreviewModal
          open
          onClose={() => setPreviewEntry(null)}
          sessionId={sessionId ?? null}
          name={previewEntry.fileName}
          path={previewEntry.relativePath}
          sourceType="deliverables"
          extension={previewEntry.extension}
          version={previewEntry.version}
          rawPath={previewEntry.rawPath}
        />
      )}
    </section>
  );
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
  taskTokenUsage?: TaskTokenUsageItem | null;
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
  taskTokenUsage,
}) => {
  const { message } = AntdApp.useApp();
  const effectiveStatus = activeTask.effective_status ?? activeTask.status ?? 'pending';
  const isBlocked = effectiveStatus === 'blocked';
  const hasTimestamps = Boolean(activeTask.created_at) || Boolean(activeTask.updated_at);
  const effectiveResult = taskResult ?? cachedResult;
  const resultTechnical = React.useMemo(
    () => getResultTechnicalView(effectiveResult),
    [effectiveResult]
  );

  const handleCopyDetails = () => {
    void copyJsonToClipboard(
      { task: activeTask, result: effectiveResult ?? null },
      'Task details copied',
      message
    );
  };

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

      <Collapse
        size="small"
        bordered={false}
        items={[
          {
            key: 'task-instruction',
            label: 'Task instruction',
            children: (
              <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }} copyable>
                {activeTask.instruction || 'No description available'}
              </Paragraph>
            ),
          },
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

      <PublishedArtifactsSection result={effectiveResult} />

      <Collapse
        size="small"
        bordered={false}
        items={[
          {
            key: 'technical-details',
            label: 'Technical Details',
            children: (
              <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                <div>
                  <Button size="small" icon={<CopyOutlined />} onClick={handleCopyDetails}>
                    Copy task JSON
                  </Button>
                </div>
                <div>
                  <Text type="secondary">Token Consumption</Text>
                  <Descriptions column={1} size="small" bordered style={{ marginTop: 4 }}>
                    <Descriptions.Item label="Total Tokens">
                      <Text strong>
                        {taskTokenUsage ? taskTokenUsage.total_tokens.toLocaleString() : '0'}
                      </Text>
                    </Descriptions.Item>
                  </Descriptions>
                </div>
                <div>
                  <Text type="secondary">Dependencies</Text>
                  <div style={{ marginTop: 4 }}>
                    <Dependencies
                      dependencies={activeTask.dependencies}
                      onDependencyClick={handleDependencyClick}
                      taskMap={taskMap}
                    />
                  </div>
                </div>
                <div>
                  <Text type="secondary">Context</Text>
                  <Space direction="vertical" size="small" style={{ width: '100%', marginTop: 4 }}>
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
                </div>
                {hasTimestamps && (
                  <div>
                    <Text type="secondary">Details</Text>
                    <Descriptions column={1} size="small" style={{ marginTop: 4 }}>
                      <Descriptions.Item label="Type">{activeTask.task_type ?? 'Unknown'}</Descriptions.Item>
                      <Descriptions.Item label="Depth">{activeTask.depth ?? 0}</Descriptions.Item>
                      {activeTask.created_at && (
                        <Descriptions.Item label="Created">{new Date(activeTask.created_at).toLocaleString()}</Descriptions.Item>
                      )}
                      {activeTask.updated_at && (
                        <Descriptions.Item label="Updated">{new Date(activeTask.updated_at).toLocaleString()}</Descriptions.Item>
                      )}
                    </Descriptions>
                  </div>
                )}
                {activeTask.metadata && Object.keys(activeTask.metadata).length > 0 && (
                  <div>
                    <Text type="secondary">Metadata</Text>
                    <Paragraph
                      code
                      copyable
                      style={{ maxHeight: 200, overflow: 'auto', marginTop: 4 }}
                    >
                      {JSON.stringify(activeTask.metadata, null, 2)}
                    </Paragraph>
                  </div>
                )}
                {recentToolResults.length > 0 && (
                  <div>
                    <Text type="secondary">
                      Recent Tool Summaries (session-level, not specific to this task)
                    </Text>
                    <Space direction="vertical" size="small" style={{ width: '100%', marginTop: 4 }}>
                      {recentToolResults.map((result, index) => (
                        <ToolResultCard
                          key={`${result.name ?? 'tool'}_${index}`}
                          payload={result}
                          defaultOpen={index === 0}
                        />
                      ))}
                    </Space>
                  </div>
                )}
                {resultTechnical.producedFiles.length > 0 && (
                  <div>
                    <Text type="secondary">Produced files ({resultTechnical.producedFiles.length})</Text>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                      {resultTechnical.producedFiles.map((item) => (
                        <li key={item}>
                          <Text copyable style={{ whiteSpace: 'pre-wrap' }}>{item}</Text>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {resultTechnical.expectedFiles.length > 0 && (
                  <div>
                    <Text type="secondary">Expected deliverables ({resultTechnical.expectedFiles.length})</Text>
                    <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                      {resultTechnical.expectedFiles.map((item) => (
                        <li key={item}>
                          <Text copyable style={{ whiteSpace: 'pre-wrap' }}>{item}</Text>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {resultTechnical.verificationRaw && (
                  <div>
                    <Text type="secondary">Verification raw data</Text>
                    <Paragraph
                      code
                      copyable
                      style={{ maxHeight: 200, overflow: 'auto', marginTop: 4 }}
                    >
                      {JSON.stringify(resultTechnical.verificationRaw, null, 2)}
                    </Paragraph>
                  </div>
                )}
                {resultTechnical.resultMetadata && (
                  <div>
                    <Text type="secondary">Result metadata</Text>
                    <Paragraph
                      code
                      copyable
                      style={{ maxHeight: 200, overflow: 'auto', marginTop: 4 }}
                    >
                      {JSON.stringify(resultTechnical.resultMetadata, null, 2)}
                    </Paragraph>
                  </div>
                )}
              </Space>
            ),
          },
        ]}
      />
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
  const executionFailed = executionStatus === 'failed' || executionStatus === 'error';
  const showFailureSummary =
    executionFailed || failureKind.length > 0 || verification.status === 'failed';
  const repairNote = resolveRepairNote(result.metadata);
  const artifactAuthority =
    result.metadata && typeof result.metadata.artifact_authority === 'object'
      ? (result.metadata.artifact_authority as Record<string, any>)
      : null;
  const publishedArtifacts =
    result.metadata && typeof result.metadata.published_artifacts === 'object'
      ? Object.values(result.metadata.published_artifacts as Record<string, unknown>)
          .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
      : [];
  const expectedPublishAliases = Array.isArray(artifactAuthority?.expected_publish_aliases)
    ? (artifactAuthority?.expected_publish_aliases as unknown[])
        .map((item) => String(item ?? '').trim())
        .filter((item) => item.length > 0)
    : [];
  const publishedArtifactLabels = publishedArtifacts
    .map((item) => {
      const alias = String(item.alias ?? '').trim();
      const path = String(item.path ?? item.source_path ?? '').trim();
      return alias || path;
    })
    .filter((item) => item.length > 0);
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
      {(showFailureSummary || Boolean(result.content)) && (
        <div>
          <Text type="secondary">Result summary</Text>
          {showFailureSummary && (
            <Space direction="vertical" size={6} style={{ width: '100%', marginTop: 4 }}>
              {failureKind && (
                <div>
                  <Tag color="red">{humanizeFailureKind(failureKind)}</Tag>
                </div>
              )}
              {verification.failures.length > 0 && (
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {verification.failures.map((failure, idx) => (
                    <li key={idx} style={{ marginBottom: 4 }}>
                      <Space size={6} wrap>
                        <Tag>
                          {typeof failure.type === 'string' && failure.type ? failure.type : 'check'}
                        </Tag>
                        {typeof failure.path === 'string' && failure.path && (
                          <Text code>{failure.path}</Text>
                        )}
                      </Space>
                      {typeof failure.message === 'string' && failure.message && (
                        <div>
                          <Text type="secondary" style={{ whiteSpace: 'pre-wrap' }}>
                            {failure.message}
                          </Text>
                        </div>
                      )}
                    </li>
                  ))}
                </ul>
              )}
              {repairNote && (
                <Text type="secondary" style={{ whiteSpace: 'pre-wrap' }}>
                  {repairNote}
                </Text>
              )}
            </Space>
          )}
          {result.content && (
            <Paragraph
              style={{ whiteSpace: 'pre-wrap', marginTop: 4, marginBottom: 0 }}
              copyable
              ellipsis={{ rows: 6, expandable: true, symbol: 'Expand' }}
            >
              {result.content}
            </Paragraph>
          )}
        </div>
      )}
      <Space wrap>
        {result.status && (
          <Tag color={statusColorMap[result.status] ?? 'default'}>
            {statusLabelMap[result.status] ?? result.status}
          </Tag>
        )}
        {verification.status && verification.label && (
          <Tag color={verification.color}>{verification.label}</Tag>
        )}
        {verification.status && verification.checksTotal > 0 && (
          <Tag color={verification.color}>
            {`${verification.checksPassed}/${verification.checksTotal} checks passed${verification.blocking ? ' · blocking' : ''}`}
          </Tag>
        )}
        {manualAccepted && <Tag color="blue">Manually accepted</Tag>}
        {executionCompleted && publishedArtifactLabels.length > 0 && (
          <Tag color="green">Published artifacts: {publishedArtifactLabels.length}</Tag>
        )}
        {executionCompleted && publishedArtifactLabels.length === 0 && (
          <Tag>No published artifact</Tag>
        )}
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
      {executionCompleted && verification.status !== 'failed' && publishedArtifactLabels.length === 0 && (
        <Alert
          type={expectedPublishAliases.length > 0 ? 'warning' : 'info'}
          showIcon
          message={
            expectedPublishAliases.length > 0
              ? 'Execution finished, but no canonical artifact was published'
              : 'Execution finished without a published artifact'
          }
          description={
            expectedPublishAliases.length > 0
              ? 'This task reported completion, but the plan artifact registry has no published output yet. Check the produced files or rerun after fixing the output location.'
              : 'This task may have produced only raw files or summary text. Completion and published artifacts are tracked separately.'
          }
        />
      )}
      {Array.isArray(result.notes) && result.notes.length > 0 && (
        <div>
          <Text type="secondary">Notes</Text>
          <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
            {result.notes.map((note, idx) => (
              <li key={idx}>
                <Text style={{ whiteSpace: 'pre-wrap' }}>{note}</Text>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Space>
  );
};
