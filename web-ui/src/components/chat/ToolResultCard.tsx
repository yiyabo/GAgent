import React, { useMemo, useState } from 'react';
import { Alert, Button, Collapse, List, Space, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { ToolResultItem, ToolResultPayload } from '@/types';
import { useChatStore } from '@store/chat';
import { collectArtifactImagePathsFromResult, resolveArtifactImageSrc } from '@/utils/artifactImageUrl';
import { SessionArtifactImage } from './SessionArtifactImage';

const { Paragraph, Text } = Typography;

interface ToolResultCardProps {
  payload: ToolResultPayload;
  defaultOpen?: boolean;
  sessionId?: string | null;
}

const ToolResultCard: React.FC<ToolResultCardProps> = ({ payload, defaultOpen = false, sessionId }) => {
  const [introVisible, setIntroVisible] = useState(true);
  const [retryLoading, setRetryLoading] = useState(false);

  const sendMessage = useChatStore((state) => state.sendMessage);

  const {
    toolName,
    introMessage,
    header,
    query,
    searchItems,
    triples,
    responseText,
    promptText,
    errorText,
    success,
    executionMeta,
    providerLabel,
    fallbackLabel,
    metadata,
    subgraph,
    isWebSearch,
  } = useMemo(() => {
    const toolValue = typeof payload.name === 'string' && payload.name ? payload.name : 'tool';
    const isWeb = toolValue === 'web_search';
    const isGraph = toolValue === 'graph_rag';

    const providerValue =
      isWeb && typeof payload.result?.provider === 'string'
        ? payload.result.provider
        : isWeb && typeof payload.parameters?.provider === 'string'
          ? payload.parameters.provider
          : undefined;
    const fallbackValue =
      isWeb && typeof payload.result?.fallback_from === 'string'
        ? payload.result.fallback_from
        : undefined;
    const labelMap: Record<string, string> = {
      builtin: 'Built-in Search',
      perplexity: 'Perplexity',
    };
    const providerLabelText = providerValue ? labelMap[providerValue] ?? providerValue : undefined;
    const fallbackLabelText = fallbackValue ? labelMap[fallbackValue] ?? fallbackValue : undefined;

    const successState = payload.result?.success !== false;
    let headerText =
      payload.summary ??
      (successState ? 'Tool execution completed' : 'Tool execution failed. Please try again later.');
    let intro = 'Tool call completed';
    if (isWeb) {
      headerText =
        payload.summary ??
        (successState ? 'Web search completed' : 'Web search failed. Please try again later.');
      intro = 'Web search has been called to fetch real-time information';
    } else if (isGraph) {
      headerText =
        payload.summary ??
        (successState ? 'Knowledge graph retrieval completed' : 'Knowledge graph retrieval failed');
      intro = 'Phage knowledge graph has been queried';
    }
    const normalizedQuery =
      payload.result?.query ??
      (typeof payload.parameters?.query === 'string' ? payload.parameters.query : undefined);
    const resultItems =
      isWeb && Array.isArray(payload.result?.results) && payload.result?.results?.length
        ? payload.result?.results.filter(Boolean)
        : [];
    const response =
      typeof payload.result?.response === 'string' && payload.result.response.trim().length > 0
        ? payload.result.response
        : typeof payload.result?.answer === 'string' && payload.result.answer.trim().length > 0
          ? payload.result.answer
          : undefined;
    const error =
      typeof payload.result?.error === 'string' && payload.result.error.trim().length > 0
        ? payload.result.error
        : undefined;
    const executionBackend =
      typeof payload.result?.execution_backend === 'string' && payload.result.execution_backend.trim().length > 0
        ? payload.result.execution_backend.trim()
        : undefined;
    const executionLane =
      typeof payload.result?.execution_lane === 'string' && payload.result.execution_lane.trim().length > 0
        ? payload.result.execution_lane.trim()
        : undefined;
    const executionLaneReason =
      typeof payload.result?.execution_lane_reason === 'string' &&
      payload.result.execution_lane_reason.trim().length > 0
        ? payload.result.execution_lane_reason.trim()
        : undefined;
    const workspaceDir =
      typeof payload.result?.workspace_dir === 'string' && payload.result.workspace_dir.trim().length > 0
        ? payload.result.workspace_dir.trim()
        : typeof payload.result?.work_dir === 'string' && payload.result.work_dir.trim().length > 0
          ? payload.result.work_dir.trim()
          : undefined;
    const codeFile =
      typeof payload.result?.code_file === 'string' && payload.result.code_file.trim().length > 0
        ? payload.result.code_file.trim()
        : undefined;
    const codeDirectory =
      typeof payload.result?.code_directory === 'string' && payload.result.code_directory.trim().length > 0
        ? payload.result.code_directory.trim()
        : undefined;

    return {
      toolName: toolValue,
      introMessage: intro,
      header: headerText,
      query: normalizedQuery,
      searchItems: resultItems,
      responseText: response,
      promptText: isGraph && typeof payload.result?.prompt === 'string' ? payload.result.prompt : undefined,
      errorText: error,
      success: successState,
      executionMeta:
        executionBackend || executionLane || executionLaneReason || workspaceDir || codeDirectory || codeFile
          ? {
              executionBackend,
              executionLane,
              executionLaneReason,
              workspaceDir,
              codeDirectory,
              codeFile,
            }
          : undefined,
      providerLabel: providerLabelText,
      fallbackLabel: fallbackLabelText,
      metadata: isGraph && payload.result?.metadata && typeof payload.result.metadata === 'object'
        ? payload.result.metadata
        : undefined,
      triples: isGraph && Array.isArray(payload.result?.triples) ? payload.result.triples : [],
      subgraph: isGraph && payload.result?.subgraph && typeof payload.result.subgraph === 'object'
        ? payload.result.subgraph
        : undefined,
      isWebSearch: isWeb,
    };
  }, [payload]);

  const artifactPreviewUrls = useMemo(() => {
    const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
    if (!sid) return [];
    const paths = collectArtifactImagePathsFromResult(
      payload.result as Record<string, any> | null | undefined,
    );
    return paths
      .map((p) => resolveArtifactImageSrc(p, sid))
      .filter((url) => /^https?:\/\//i.test(url));
  }, [payload.result, sessionId]);

  const handleRetry = async () => {
    if (!isWebSearch) {
      return;
    }
    if (!query) {
      return;
    }
    try {
      setRetryLoading(true);
      const prompt = `Please call the web_search tool again, query "${query}", and summarize the latest results.`;
      await sendMessage(prompt, { tool_retry: true, retry_query: query });
    } finally {
      setRetryLoading(false);
    }
  };

  const handleCollapseChange = (keys: string | string[]) => {
    if (keys && (Array.isArray(keys) ? keys.length > 0 : true)) {
      setIntroVisible(false);
    }
  };

  const collapseItems = [
    {
      key: 'result',
      label: (
        <Space>
          <Tag color={success ? (toolName === 'graph_rag' ? 'purple' : 'green') : 'red'}>
            {toolName}
          </Tag>
          {providerLabel && <Tag color="blue">{providerLabel}</Tag>}
          <Text>{header}</Text>
        </Space>
      ),
      children: (
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          {providerLabel && (
            <Text type="secondary">
              Source provider: {providerLabel}
              {fallbackLabel ? ` (fallback: ${fallbackLabel})` : ''}
            </Text>
          )}
          {query && (
            <Text type="secondary">
              Query: <Text code>{query}</Text>
            </Text>
          )}
          {executionMeta && (
            <Space direction="vertical" size={0}>
              {(executionMeta.executionBackend || executionMeta.executionLane) && (
                <Text type="secondary">
                  Execution:
                  {executionMeta.executionBackend ? ` ${executionMeta.executionBackend}` : ''}
                  {executionMeta.executionLane ? ` (${executionMeta.executionLane})` : ''}
                </Text>
              )}
              {executionMeta.executionLaneReason && (
                <Text type="secondary">Reason: {executionMeta.executionLaneReason}</Text>
              )}
              {executionMeta.workspaceDir && (
                <Text type="secondary">
                  Workspace: <Text code>{executionMeta.workspaceDir}</Text>
                </Text>
              )}
              {executionMeta.codeDirectory && (
                <Text type="secondary">
                  Code dir: <Text code>{executionMeta.codeDirectory}</Text>
                </Text>
              )}
              {executionMeta.codeFile && (
                <Text type="secondary">
                  Code file: <Text code>{executionMeta.codeFile}</Text>
                </Text>
              )}
            </Space>
          )}
          {responseText && (
            <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap' }}>
              {responseText}
            </Paragraph>
          )}
          {isWebSearch && success && responseText && searchItems.length === 0 && (
            <Alert
              type="warning"
              showIcon
              message="No parseable source links returned"
              description="The tool payload has no structured results list (URLs), so the text cannot be checked against independent sources item by item. For time-sensitive or factual claims, treat with caution or run another search."
            />
          )}
          {promptText && (
            <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap' }}>
              {promptText}
            </Paragraph>
          )}
          {metadata && (
            <Text type="secondary">
              Triple count: {metadata.triple_count ?? '-'}, hops: {metadata.hops ?? '-'}
            </Text>
          )}
          {searchItems.length > 0 && (
            <List<ToolResultItem>
              size="small"
              dataSource={searchItems}
              renderItem={(item, index) => (
                <List.Item key={`${item?.url ?? index}`}>
                  <Space direction="vertical" size={2}>
                    {item?.title && (
                      <Text strong>
                        {item.url ? (
                          <a href={item.url} target="_blank" rel="noopener noreferrer">
                            {item.title}
                          </a>
                        ) : (
                          item.title
                        )}
                      </Text>
                    )}
                    {item?.source && (
                      <Text type="secondary">Source: {item.source}</Text>
                    )}
                    {item?.snippet && (
                      <Text style={{ whiteSpace: 'pre-wrap' }}>{item.snippet}</Text>
                    )}
                  </Space>
                </List.Item>
              )}
            />
          )}
          {triples && triples.length > 0 && (
            <List<Record<string, any>>
              size="small"
              dataSource={triples}
              renderItem={(item, index) => (
                <List.Item key={index}>
                  <Space direction="vertical" size={2}>
                    <Text strong>
                      {item.entity1} --[{item.relation}]→ {item.entity2}
                    </Text>
                    <Text type="secondary">
                      Type: {item.entity1_type ?? 'unknown'} → {item.entity2_type ?? 'unknown'}
                    </Text>
                    {item.pdf_name && (
                      <Text type="secondary">Source PDF: {item.pdf_name}</Text>
                    )}
                  </Space>
                </List.Item>
              )}
            />
          )}
          {subgraph && (
            <Alert
              type="info"
              showIcon
              message="Knowledge-graph subgraph returned. You can analyze it further in graph view."
            />
          )}
          {artifactPreviewUrls.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 6 }}>
                Artifacts (preview)
              </Text>
              <Space wrap size={8}>
                {artifactPreviewUrls.map((url, idx) => (
                  <a key={`${url}_${idx}`} href={url} target="_blank" rel="noopener noreferrer">
                    <SessionArtifactImage
                      url={url}
                      alt=""
                      imageStyle={{
                        maxWidth: 200,
                        maxHeight: 160,
                        objectFit: 'contain',
                        borderRadius: 6,
                        border: '1px solid var(--border-color, #e8e8e8)',
                        display: 'block',
                      }}
                    />
                  </a>
                ))}
              </Space>
            </div>
          )}
          {!success && (
            <Alert
              type="error"
              showIcon
              message={errorText ?? 'Search failed. Please try again later.'}
            />
          )}
          {!success && query && isWebSearch && (
            <Button
              type="link"
              icon={<ReloadOutlined />}
              onClick={handleRetry}
              loading={retryLoading}
              style={{ paddingLeft: 0 }}
            >
              Retry search
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div className="tool-result-card">
      {introVisible && (
        <Alert
          showIcon
          type="info"
          message={introMessage}
          style={{ marginBottom: 8 }}
        />
      )}
      <Collapse
        size="small"
        defaultActiveKey={defaultOpen ? ['result'] : []}
        onChange={handleCollapseChange}
        ghost
        items={collapseItems}
      />
    </div>
  );
};

export default ToolResultCard;
