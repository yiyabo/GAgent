import React from 'react';
import {
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
}

export const Dependencies: React.FC<DependenciesProps> = ({ dependencies, onDependencyClick }) => {
  if (!dependencies || dependencies.length === 0) {
    return <Text type="secondary">No dependencies</Text>;
  }
  return (
    <Space wrap size={6}>
      {dependencies.map((dep) => (
        <Button
          key={dep}
          size="small"
          type="link"
          onClick={() => onDependencyClick(dep)}
        >
          Task #{dep}
        </Button>
      ))}
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
}

interface TaskDrawerContentProps {
  activeTask: PlanTaskNode;
  handleDependencyClick: (depId: number) => void;
  recentToolResults: ToolResultPayload[];
  resultLoading: boolean;
  taskResult: PlanResultItem | undefined;
  cachedResult: PlanResultItem | undefined;
}

export const TaskDrawerContent: React.FC<TaskDrawerContentProps> = ({
  activeTask,
  handleDependencyClick,
  recentToolResults,
  resultLoading,
  taskResult,
  cachedResult,
}) => {
  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <section>
        <Title level={5}>Basic Information</Title>
        <Descriptions column={1} bordered size="small">
          <Descriptions.Item label="Task Type">
            {activeTask.task_type ?? 'Unknown'}
          </Descriptions.Item>
          <Descriptions.Item label="Status">
            <Tag color={statusColorMap[activeTask.status ?? 'pending'] ?? 'default'}>
              {statusLabelMap[activeTask.status ?? 'pending'] ??
                activeTask.status ??
                'Unknown'}
            </Tag>
          </Descriptions.Item>
          {activeTask.parent_id ? (
            <Descriptions.Item label="Parent Task">
              <Button
                type="link"
                size="small"
                onClick={() => handleDependencyClick(activeTask.parent_id!)}
              >
                Task #{activeTask.parent_id}
              </Button>
            </Descriptions.Item>
          ) : (
            <Descriptions.Item label="Parent Task">None</Descriptions.Item>
          )}
          <Descriptions.Item label="Depth">
            {activeTask.depth ?? 0}
          </Descriptions.Item>
          <Descriptions.Item label="Created At">
            {activeTask.created_at ? new Date(activeTask.created_at).toLocaleString() : 'Unknown'}
          </Descriptions.Item>
          <Descriptions.Item label="Updated At">
            {activeTask.updated_at ? new Date(activeTask.updated_at).toLocaleString() : 'Unknown'}
          </Descriptions.Item>
        </Descriptions>
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

      <section>
        <Title level={5}>Metadata</Title>
        {activeTask.metadata && Object.keys(activeTask.metadata).length > 0 ? (
          <Paragraph
            code
            copyable
            style={{ maxHeight: 200, overflow: 'auto' }}
          >
            {JSON.stringify(activeTask.metadata, null, 2)}
          </Paragraph>
        ) : (
          <Text type="secondary">No metadata available</Text>
        )}
      </section>

      <section>
        <Title level={5}>Execution Result</Title>
        <ExecutionResult
          resultLoading={resultLoading}
          taskResult={taskResult}
          cachedResult={cachedResult}
        />
      </section>
    </Space>
  );
};

export const ExecutionResult: React.FC<ExecutionResultProps> = ({ resultLoading, taskResult, cachedResult }) => {
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

  return (
    <Space direction="vertical" size="small" style={{ width: '100%' }}>
      {result.status && (
        <Tag color={statusColorMap[result.status] ?? 'default'}>
          {statusLabelMap[result.status] ?? result.status}
        </Tag>
      )}
      {result.content && (
        <Paragraph style={{ whiteSpace: 'pre-wrap' }} copyable>
          {result.content}
        </Paragraph>
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
