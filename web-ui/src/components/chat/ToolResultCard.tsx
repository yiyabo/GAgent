import React, { useMemo, useState } from 'react';
import { Alert, Button, Collapse, List, Space, Tag, Typography } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import { ToolResultItem, ToolResultPayload } from '@/types';
import { useChatStore } from '@store/chat';

const { Paragraph, Text } = Typography;

interface ToolResultCardProps {
  payload: ToolResultPayload;
  defaultOpen?: boolean;
}

const ToolResultCard: React.FC<ToolResultCardProps> = ({ payload, defaultOpen = false }) => {
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
      builtin: '内置搜索',
      perplexity: 'Perplexity',
    };
    const providerLabelText = providerValue ? labelMap[providerValue] ?? providerValue : undefined;
    const fallbackLabelText = fallbackValue ? labelMap[fallbackValue] ?? fallbackValue : undefined;

    const successState = payload.result?.success !== false;
    let headerText =
      payload.summary ??
      (successState ? '工具执行完成' : '工具执行失败，稍后重试');
    let intro = '工具调用完成';
    if (isWeb) {
      headerText =
        payload.summary ??
        (successState ? '网络搜索已完成' : '网络搜索失败，稍后重试');
      intro = '已调用 Web 搜索获取实时资料';
    } else if (isGraph) {
      headerText =
        payload.summary ??
        (successState ? '知识图谱检索完成' : '知识图谱检索失败');
      intro = '已查询噬菌体知识图谱';
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

  const handleRetry = async () => {
    if (!isWebSearch) {
      return;
    }
    if (!query) {
      return;
    }
    try {
      setRetryLoading(true);
      const prompt = `请再次调用 web_search 工具，查询 "${query}"，并总结最新结果。`;
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
              使用来源：{providerLabel}
              {fallbackLabel ? `（由 ${fallbackLabel} 兜底）` : ''}
            </Text>
          )}
          {query && (
            <Text type="secondary">
              查询语句：<Text code>{query}</Text>
            </Text>
          )}
          {responseText && (
            <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap' }}>
              {responseText}
            </Paragraph>
          )}
          {promptText && (
            <Paragraph style={{ marginBottom: 8, whiteSpace: 'pre-wrap' }}>
              {promptText}
            </Paragraph>
          )}
          {metadata && (
            <Text type="secondary">
              三元组数量：{metadata.triple_count ?? '-'}，扩展层数：{metadata.hops ?? '-'}
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
                      <Text type="secondary">来源：{item.source}</Text>
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
                      类型: {item.entity1_type ?? '未知'} → {item.entity2_type ?? '未知'}
                    </Text>
                    {item.pdf_name && (
                      <Text type="secondary">来源 PDF: {item.pdf_name}</Text>
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
              message="返回了知识图谱子图，可在图谱视图中进一步分析。"
            />
          )}
          {!success && (
            <Alert
              type="error"
              showIcon
              message={errorText ?? '搜索失败，请稍后重试。'}
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
              重试搜索
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
