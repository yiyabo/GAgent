import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  BulbOutlined,
  ToolOutlined,
  FileTextOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  LoadingOutlined,
  DownOutlined,
  UpOutlined,
} from '@ant-design/icons';
import {
  Card,
  Tag,
  Typography,
  Spin,
  Empty,
  Badge,
  Space,
} from 'antd';
import dayjs from 'dayjs';
import relativeTime from 'dayjs/plugin/relativeTime';
import { ENV } from '@/config/env';
import { planTreeApi } from '@api/planTree';
import type { JobLogEvent, BackgroundTaskItem } from '@/types';

dayjs.extend(relativeTime);

const { Text, Paragraph } = Typography;

interface AgentWorkPanelProps {
  sessionId: string | null;
}

type AgentSubType =
  | 'agent_thinking'
  | 'agent_tool_use'
  | 'agent_tool_result'
  | 'agent_text'
  | 'agent_result'
  | 'raw'
  | 'stdout'
  | 'stderr'
  | string;

interface ParsedEvent {
  id: string;
  timestamp: string;
  subType: AgentSubType;
  message: string;
  metadata: Record<string, any>;
}

interface ActiveJobInfo {
  jobId: string;
  jobType: string;
  taskName: string;
  startedAt: string | null;
  status: string;
}

const ACTIVE_STATUSES = new Set(['running', 'pending']);

const PREFERRED_JOB_TYPES = new Set(['plan_execute', 'code_executor']);

let _idCounter = 0;
const nextId = () => `evt_${++_idCounter}_${Date.now()}`;

const formatRelativeTime = (ts: string): string => {
  if (!ts) return '';
  const d = dayjs(ts);
  if (!d.isValid()) return '';
  const diffSec = Math.floor((Date.now() - d.valueOf()) / 1000);
  if (diffSec < 5) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  return d.fromNow();
};

const formatDuration = (startedAt?: string | null): string => {
  if (!startedAt) return '';
  const start = dayjs(startedAt);
  if (!start.isValid()) return '';
  const diffMs = Date.now() - start.valueOf();
  if (diffMs < 1000) return '<1s';
  if (diffMs < 60000) return `${Math.floor(diffMs / 1000)}s`;
  const minutes = Math.floor(diffMs / 60000);
  const seconds = Math.floor((diffMs % 60000) / 1000);
  return `${minutes}m${seconds.toString().padStart(2, '0')}s`;
};

const truncateText = (text: string, maxLen = 300): string => {
  if (!text || text.length <= maxLen) return text || '';
  return `${text.slice(0, maxLen)}...`;
};

const summarizeToolInput = (toolName: string, toolInput: any): string => {
  if (!toolInput || typeof toolInput !== 'object') {
    return `Running: ${toolName}`;
  }
  const keys = Object.keys(toolInput);
  if (keys.length === 0) {
    return `Running: ${toolName}`;
  }
  const firstKey = keys[0];
  const firstVal = toolInput[firstKey];
  if (typeof firstVal === 'string' && firstVal.trim()) {
    const short = firstVal.length > 60 ? `${firstVal.slice(0, 60)}...` : firstVal;
    return `Running: ${toolName} — ${firstKey}: ${short}`;
  }
  return `Running: ${toolName} (${keys.length} param${keys.length > 1 ? 's' : ''})`;
};

const getSubTypeIcon = (subType: AgentSubType, isError?: boolean) => {
  if (isError) return <CloseCircleOutlined />;
  switch (subType) {
    case 'agent_thinking':
      return <BulbOutlined />;
    case 'agent_tool_use':
      return <ToolOutlined />;
    case 'agent_tool_result':
      return isError ? <CloseCircleOutlined /> : <FileTextOutlined />;
    case 'agent_text':
      return <CodeOutlined />;
    case 'agent_result':
      return <CheckCircleOutlined />;
    default:
      return <FileTextOutlined />;
  }
};

const getSubTypeColor = (subType: AgentSubType, isError?: boolean): string => {
  if (isError) return 'red';
  switch (subType) {
    case 'agent_thinking':
      return 'blue';
    case 'agent_tool_use':
      return 'orange';
    case 'agent_tool_result':
      return 'green';
    case 'agent_text':
      return 'purple';
    case 'agent_result':
      return 'green';
    default:
      return 'default';
  }
};

const getSubTypeLabel = (subType: AgentSubType): string => {
  switch (subType) {
    case 'agent_thinking':
      return 'Thinking';
    case 'agent_tool_use':
      return 'Tool Use';
    case 'agent_tool_result':
      return 'Tool Result';
    case 'agent_text':
      return 'Text';
    case 'agent_result':
      return 'Result';
    case 'raw':
      return 'Raw';
    case 'stdout':
      return 'Stdout';
    case 'stderr':
      return 'Stderr';
    default:
      return subType;
  }
};

const parseStreamData = (raw: MessageEvent<any>): Record<string, any> | null => {
  try {
    const payload = JSON.parse(raw.data);
    if (!payload) return null;
    return payload;
  } catch {
    return null;
  }
};

const AgentWorkPanel: React.FC<AgentWorkPanelProps> = ({ sessionId }) => {
  const [activeJob, setActiveJob] = useState<ActiveJobInfo | null>(null);
  const [events, setEvents] = useState<ParsedEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [expandedDetails, setExpandedDetails] = useState<Set<string>>(new Set());
  const [rawExpanded, setRawExpanded] = useState(false);
  const [consoleExpanded, setConsoleExpanded] = useState(false);

  const scrollRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const [showScrollBtn, setShowScrollBtn] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const pollerRef = useRef<number | null>(null);
  const eventsRef = useRef<ParsedEvent[]>([]);
  const pendingEventsRef = useRef<ParsedEvent[]>([]);
  const rafRef = useRef<number | null>(null);

  eventsRef.current = events;

  const flushPendingEvents = useCallback(() => {
    rafRef.current = null;
    const pending = pendingEventsRef.current;
    if (pending.length === 0) return;
    pendingEventsRef.current = [];
    setEvents((prev) => {
      const next = [...prev, ...pending];
      if (next.length > 500) {
        return next.slice(-500);
      }
      return next;
    });
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(flushPendingEvents);
  }, [flushPendingEvents]);

  const findActiveJob = useCallback(async () => {
    if (!sessionId) return;
    try {
      const board = await planTreeApi.getBackgroundTaskBoard({
        limit: 50,
        session_id: sessionId,
        include_finished: false,
      });

      let candidate: BackgroundTaskItem | null = null;
      const allItems: BackgroundTaskItem[] = [];
      Object.values(board.groups || {}).forEach((group: any) => {
        if (group?.items) {
          allItems.push(...group.items);
        }
      });

      const activeItems = allItems.filter(
        (item) => item.session_id === sessionId && ACTIVE_STATUSES.has(item.status)
      );

      if (activeItems.length > 0) {
        candidate = activeItems.reduce((best: BackgroundTaskItem | null, item: BackgroundTaskItem) => {
          if (!best) return item;
          const bestPreferred = PREFERRED_JOB_TYPES.has(best.job_type);
          const itemPreferred = PREFERRED_JOB_TYPES.has(item.job_type);
          if (itemPreferred && !bestPreferred) return item;
          if (!itemPreferred && bestPreferred) return best;
          return item;
        }, null);
      }

      if (candidate) {
        setActiveJob({
          jobId: candidate.job_id,
          jobType: candidate.job_type,
          taskName: candidate.label || 'Untitled Task',
          startedAt: candidate.started_at || null,
          status: candidate.status,
        });
      } else {
        setActiveJob(null);
      }
    } catch (err) {
      console.error('Failed to fetch active job:', err);
    }
  }, [sessionId]);

  const closeStream = useCallback(() => {
    if (sourceRef.current) {
      sourceRef.current.close();
      sourceRef.current = null;
    }
    setIsStreaming(false);
  }, []);

  const stopPolling = useCallback(() => {
    if (pollerRef.current !== null) {
      window.clearTimeout(pollerRef.current);
      pollerRef.current = null;
    }
  }, []);

  const startPolling = useCallback(() => {
    if (pollerRef.current !== null) return;
    const tick = async () => {
      pollerRef.current = null;
      await findActiveJob();
      pollerRef.current = window.setTimeout(tick, 5000);
    };
    pollerRef.current = window.setTimeout(tick, 5000);
  }, [findActiveJob]);

  useEffect(() => {
    if (!sessionId) {
      setActiveJob(null);
      setEvents([]);
      closeStream();
      stopPolling();
      return;
    }

    setEvents([]);
    setIsLoading(true);
    findActiveJob().finally(() => setIsLoading(false));

    return () => {
      closeStream();
      stopPolling();
    };
  }, [sessionId, findActiveJob, closeStream, stopPolling]);

  useEffect(() => {
    if (!activeJob?.jobId) return;

    let cancelled = false;
    setEvents([]);
    setExpandedDetails(new Set());
    setRawExpanded(false);
    setConsoleExpanded(false);

    const streamUrl = `${ENV.API_BASE_URL}/jobs/${activeJob.jobId}/stream`;

    const init = async () => {
      try {
        const snapshot = await planTreeApi.getJobStatus(activeJob.jobId);
        if (cancelled) return;
        if (snapshot.logs && Array.isArray(snapshot.logs)) {
          const parsedLogs = snapshot.logs
            .filter((log: JobLogEvent) => {
              const st = String(log.metadata?.sub_type || '').trim().toLowerCase();
              return st && st !== 'raw' && st !== 'stdout' && st !== 'stderr';
            })
            .map((log: JobLogEvent) => ({
              id: nextId(),
              timestamp: log.timestamp || new Date().toISOString(),
              subType: String(log.metadata?.sub_type || '').trim().toLowerCase() as AgentSubType,
              message: log.message || '',
              metadata: log.metadata || {},
            }));
          setEvents(parsedLogs);
        }
      } catch (err) {
        if (cancelled) return;
        console.warn('Failed to load initial job snapshot:', err);
      }

      try {
        const source = new EventSource(streamUrl, { withCredentials: true });
        sourceRef.current = source;
        setIsStreaming(true);

        source.onmessage = (event) => {
          const parsed = parseStreamData(event);
          if (!parsed) return;

          if (parsed.type === 'snapshot' || parsed.type === 'heartbeat') {
            return;
          }

          const eventData = parsed.event || parsed;
          if (!eventData) return;

          const subType = String(eventData.metadata?.sub_type || '').trim().toLowerCase() as AgentSubType;
          if (!subType) return;

          const newEvent: ParsedEvent = {
            id: nextId(),
            timestamp: eventData.timestamp || new Date().toISOString(),
            subType,
            message: eventData.message || '',
            metadata: eventData.metadata || {},
          };

          pendingEventsRef.current.push(newEvent);
          scheduleFlush();
        };

        source.onerror = () => {
          if (cancelled) return;
          console.warn('SSE connection interrupted; switching to polling mode.');
          closeStream();
          startPolling();
        };
      } catch (err) {
        console.warn('SSE initialization failed; falling back to polling:', err);
        startPolling();
      }
    };

    init();

    return () => {
      cancelled = true;
      closeStream();
      stopPolling();
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      flushPendingEvents();
    };
  }, [activeJob?.jobId, closeStream, startPolling, stopPolling, flushPendingEvents]);

  useEffect(() => {
    if (scrollRef.current && autoScrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length]);

  const toggleDetail = useCallback((id: string) => {
    setExpandedDetails((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  const { mainEvents, rawEvents, consoleEvents } = useMemo(() => {
    const main: ParsedEvent[] = [];
    const raw: ParsedEvent[] = [];
    const con: ParsedEvent[] = [];

    events.forEach((evt) => {
      if (evt.subType === 'raw') {
        raw.push(evt);
      } else if (evt.subType === 'stdout' || evt.subType === 'stderr') {
        con.push(evt);
      } else {
        main.push(evt);
      }
    });

    return { mainEvents: main, rawEvents: raw, consoleEvents: con };
  }, [events]);

  const renderEventCard = (evt: ParsedEvent, index: number) => {
    const isError = evt.subType === 'agent_tool_result' && evt.metadata?.is_error === true;
    const color = getSubTypeColor(evt.subType, isError);
    const icon = getSubTypeIcon(evt.subType, isError);
    const isExpanded = expandedDetails.has(evt.id);

    let content = evt.message;
    let details: React.ReactNode = null;

    if (evt.subType === 'agent_tool_use') {
      const toolName = evt.metadata?.tool_name || 'unknown';
      const toolInput = evt.metadata?.tool_input;
      content = summarizeToolInput(toolName, toolInput);
      if (toolInput && typeof toolInput === 'object') {
        details = (
          <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {JSON.stringify(toolInput, null, 2)}
          </pre>
        );
      }
    } else if (evt.subType === 'agent_tool_result') {
      const resultText = evt.metadata?.result || evt.message || '';
      content = truncateText(resultText, 200);
      if (resultText.length > 200) {
        details = (
          <pre style={{ margin: 0, fontSize: 11, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {resultText}
          </pre>
        );
      }
    } else if (evt.subType === 'agent_result') {
      const usage = evt.metadata?.usage;
      content = evt.message || '';
      if (usage && typeof usage === 'object') {
        details = (
          <div style={{ fontSize: 11, color: '#666' }}>
            {Object.entries(usage).map(([key, val]) => (
              <div key={key}>
                {key}: {String(val)}
              </div>
            ))}
          </div>
        );
      }
    }

    const cardBorderColor = isError ? '#ff4d4f' : color === 'blue' ? '#1890ff' : color === 'orange' ? '#fa8c16' : color === 'green' ? '#52c41a' : color === 'purple' ? '#722ed1' : '#d9d9d9';
    const cardBg = evt.subType === 'agent_thinking' ? '#f0f5ff' : evt.subType === 'agent_result' ? '#f6ffed' : '#fff';

    return (
      <div
        key={evt.id}
        style={{
          display: 'flex',
          gap: 12,
          marginBottom: 16,
          position: 'relative',
        }}
      >
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            width: 32,
            flexShrink: 0,
          }}
        >
          <Badge
            count={icon}
            style={{
              backgroundColor: isError ? '#ff4d4f' : color === 'blue' ? '#1890ff' : color === 'orange' ? '#fa8c16' : color === 'green' ? '#52c41a' : color === 'purple' ? '#722ed1' : '#d9d9d9',
              color: '#fff',
              fontSize: 14,
              width: 28,
              height: 28,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: '50%',
            }}
          />
          {index < mainEvents.length - 1 && (
            <div
              style={{
                width: 2,
                flex: 1,
                backgroundColor: '#e8e8e8',
                marginTop: 4,
                minHeight: 20,
              }}
            />
          )}
        </div>
        <Card
          size="small"
          style={{
            flex: 1,
            borderLeft: `3px solid ${cardBorderColor}`,
            backgroundColor: cardBg,
            marginBottom: 0,
          }}
          styles={{ body: { padding: '12px 16px' } }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: 8,
            }}
          >
            <Space size={8}>
              <Tag color={color}>{getSubTypeLabel(evt.subType)}</Tag>
              {isError && <Tag color="red">Error</Tag>}
            </Space>
            <Text type="secondary" style={{ fontSize: 11 }}>
              {formatRelativeTime(evt.timestamp)}
            </Text>
          </div>
          <Paragraph style={{ marginBottom: 0, fontSize: 13, whiteSpace: 'pre-wrap' }}>
            {content}
          </Paragraph>
          {details && (
            <div style={{ marginTop: 8 }}>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 4,
                  cursor: 'pointer',
                  color: '#1890ff',
                  fontSize: 12,
                }}
                onClick={() => toggleDetail(evt.id)}
              >
                {isExpanded ? <UpOutlined /> : <DownOutlined />}
                <span>{isExpanded ? 'Hide details' : 'Show details'}</span>
              </div>
              {isExpanded && (
                <div
                  style={{
                    marginTop: 8,
                    padding: 8,
                    backgroundColor: '#f5f5f5',
                    borderRadius: 4,
                    maxHeight: 300,
                    overflow: 'auto',
                  }}
                >
                  {details}
                </div>
              )}
            </div>
          )}
        </Card>
      </div>
    );
  };

  const header = useMemo(() => {
    if (!activeJob) return null;

    const elapsed = formatDuration(activeJob.startedAt);
    const isRunning = ACTIVE_STATUSES.has(activeJob.status);

    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '12px 16px',
          backgroundColor: '#fafafa',
          borderBottom: '1px solid #f0f0f0',
        }}
      >
        <Space direction="vertical" size={2}>
          <Space size={8}>
            <Text strong style={{ fontSize: 14 }}>
              {activeJob.taskName || 'Agent Work'}
            </Text>
            <Tag color={isRunning ? 'blue' : 'default'}>
              {isRunning ? <LoadingOutlined spin /> : null}
              {activeJob.jobType}
            </Tag>
          </Space>
          <Text type="secondary" style={{ fontSize: 12 }}>
            Job: {activeJob.jobId.slice(0, 8)}...
          </Text>
        </Space>
        <Space direction="vertical" size={2} align="end">
          {elapsed && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              Elapsed: {elapsed}
            </Text>
          )}
          <Badge
            status={isStreaming ? 'processing' : 'default'}
            text={isStreaming ? 'Live' : 'Polling'}
          />
        </Space>
      </div>
    );
  }, [activeJob, isStreaming]);

  if (isLoading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '40px 0' }}>
        <Spin size="large" tip="Loading agent work..." />
      </div>
    );
  }

  if (!activeJob) {
    return (
      <div style={{ padding: '40px 16px' }}>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Space direction="vertical" size={4} style={{ textAlign: 'center' }}>
              <Text type="secondary">No active agent work</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>
                Start a task to see the agent in action
              </Text>
            </Space>
          }
        />
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {header}
      <div
        ref={scrollRef}
        onScroll={() => {
          const el = scrollRef.current;
          if (!el) return;
          const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
          autoScrollRef.current = atBottom;
          setShowScrollBtn(!atBottom);
        }}
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px 16px 24px',
          position: 'relative',
        }}
      >
        {mainEvents.length === 0 && !isLoading && (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={
              <Text type="secondary">Waiting for agent events...</Text>
            }
            style={{ marginTop: 40 }}
          />
        )}

        {mainEvents.map((evt, idx) => renderEventCard(evt, idx))}

        {rawEvents.length > 0 && (
          <div style={{ marginTop: 24 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                cursor: 'pointer',
                color: '#1890ff',
                fontSize: 13,
                marginBottom: 8,
              }}
              onClick={() => setRawExpanded((v) => !v)}
            >
              {rawExpanded ? <UpOutlined /> : <DownOutlined />}
              <span>Raw output ({rawEvents.length} events)</span>
            </div>
            {rawExpanded && (
              <div
                style={{
                  backgroundColor: '#1e1e1e',
                  color: '#d4d4d4',
                  padding: 12,
                  borderRadius: 4,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  maxHeight: 300,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {rawEvents.map((evt) => `${evt.timestamp}: ${evt.message}`).join('\n')}
              </div>
            )}
          </div>
        )}

        {consoleEvents.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 4,
                cursor: 'pointer',
                color: '#1890ff',
                fontSize: 13,
                marginBottom: 8,
              }}
              onClick={() => setConsoleExpanded((v) => !v)}
            >
              {consoleExpanded ? <UpOutlined /> : <DownOutlined />}
              <span>Console ({consoleEvents.length} lines)</span>
            </div>
            {consoleExpanded && (
              <div
                style={{
                  backgroundColor: '#1e1e1e',
                  color: '#d4d4d4',
                  padding: 12,
                  borderRadius: 4,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  maxHeight: 300,
                  overflow: 'auto',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                }}
              >
                {consoleEvents.map((evt) => `${evt.timestamp}: [${evt.subType.toUpperCase()}] ${evt.message}`).join('\n')}
              </div>
            )}
          </div>
        )}
        {showScrollBtn && (
          <div
            onClick={() => {
              if (scrollRef.current) {
                scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
                autoScrollRef.current = true;
                setShowScrollBtn(false);
              }
            }}
            style={{
              position: 'sticky',
              bottom: 8,
              alignSelf: 'center',
              background: 'var(--bg-secondary)',
              border: '1px solid var(--border-color)',
              borderRadius: 6,
              padding: '2px 12px',
              cursor: 'pointer',
              fontSize: 13,
              boxShadow: 'var(--shadow-sm)',
              zIndex: 10,
              width: 'fit-content',
              margin: '0 auto',
            }}
          >↓ 回到底部</div>
        )}
      </div>
    </div>
  );
};

export default React.memo(AgentWorkPanel);
