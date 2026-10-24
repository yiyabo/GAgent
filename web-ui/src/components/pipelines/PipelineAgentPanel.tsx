import React, { useCallback, useEffect, useRef, useState } from 'react';
import { App, Button, Input, Popconfirm, Spin, Tag, Typography } from 'antd';
import { CheckCircleFilled, CloseCircleFilled, DeleteOutlined, SendOutlined, UndoOutlined } from '@ant-design/icons';
import { pipelinesApi } from '@api/pipelines';
import type { AgentEvent, AgentMessage, PipelineGraph, PipelineRun, ValidationResult } from '@api/pipelines';
import { MarkdownRenderer } from '@components/chat/MarkdownRenderer';
import { useT } from '@/i18n/useT';

const { Text } = Typography;

const POLL_MS = 2500;

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

interface LiveStep {
  summary: string;
  ok: boolean;
}

type Saved = Promise<boolean>;

interface Props {
  pipelineId: string;
  /** The node the user has selected, if any: the assistant is told which one "this node" is. */
  selectedNode: { id: string; name: string } | null;
  /** Text to put in the input (used by "ask about this node"); a new nonce sets it again. */
  prefill: { text: string; nonce: number } | null;
  /** Called before a message is sent so unsaved canvas edits reach the server first. */
  beforeSend: () => Saved;
  onGraph: (graph: PipelineGraph, validation: ValidationResult) => void;
  onBusyChange: (busy: boolean) => void;
  onRunStarted: (run: PipelineRun) => void;
}

/** The assistant beside the canvas: a conversation that can also build and change the pipeline. */
const PipelineAgentPanel: React.FC<Props> = ({ pipelineId, selectedNode, prefill, beforeSend, onGraph, onBusyChange, onRunStarted }) => {
  const t = useT();
  const { message } = App.useApp();
  const [messages, setMessages] = useState<AgentMessage[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [serverBusy, setServerBusy] = useState(false);
  const [liveText, setLiveText] = useState('');
  const [liveSteps, setLiveSteps] = useState<LiveStep[]>([]);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [includeSelection, setIncludeSelection] = useState(true);
  const listRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<{ focus: () => void } | null>(null);
  const abort = useRef<AbortController | null>(null);
  const callbacks = useRef({ onGraph, onBusyChange });
  callbacks.current = { onGraph, onBusyChange };

  const working = sending || serverBusy;
  useEffect(() => {
    callbacks.current.onBusyChange(working);
  }, [working]);

  const load = useCallback(async () => {
    try {
      const data = await pipelinesApi.agentMessages(pipelineId);
      setMessages(data.messages);
      setServerBusy(data.busy);
      setLoaded(true);
      return data;
    } catch (error) {
      message.error(t('pipelines.agent.loadFailed', { error: errorText(error) }));
      setLoaded(true);
      return null;
    }
  }, [pipelineId, message, t]);

  useEffect(() => {
    void load();
    return () => abort.current?.abort();
  }, [load]);

  // A turn started earlier (another tab, a page reload) is still running on the server: wait for it, then show its result.
  useEffect(() => {
    if (!serverBusy || sending) return undefined;
    const timer = window.setInterval(async () => {
      const data = await load();
      if (data && !data.busy) {
        try {
          const detail = await pipelinesApi.detail(pipelineId);
          callbacks.current.onGraph(detail.draft, detail.validation);
        } catch {
          // the canvas keeps what it has
        }
      }
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [serverBusy, sending, load, pipelineId]);

  useEffect(() => {
    const list = listRef.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [messages, liveText, liveSteps, pendingUser]);

  useEffect(() => {
    if (!prefill) return;
    setInput(prefill.text);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }, [prefill]);

  useEffect(() => {
    setIncludeSelection(true);
  }, [selectedNode?.id]);

  const send = async (text: string) => {
    const content = text.trim();
    if (!content || working) return;
    if (!(await beforeSend())) return;
    setInput('');
    setSending(true);
    setPendingUser(content);
    setLiveText('');
    setLiveSteps([]);
    const controller = new AbortController();
    abort.current = controller;
    const handle = (event: AgentEvent) => {
      if (event.type === 'delta') setLiveText((current) => current + event.text);
      else if (event.type === 'tool') setLiveSteps((current) => [...current, { summary: event.summary, ok: event.ok }]);
      else if (event.type === 'graph') callbacks.current.onGraph(event.graph, event.validation);
      else if (event.type === 'error') message.error(t('pipelines.agent.sendFailed', { error: event.message }));
    };
    try {
      await pipelinesApi.sendToAgent(pipelineId, content, includeSelection && selectedNode ? selectedNode.id : null, handle, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) {
        message.error(t('pipelines.agent.sendFailed', { error: errorText(error) }));
        setInput(content);
      }
    } finally {
      if (!controller.signal.aborted) {
        await load();
        setSending(false);
        setPendingUser(null);
        setLiveText('');
        setLiveSteps([]);
      }
    }
  };

  const clear = async () => {
    try {
      await pipelinesApi.clearAgent(pipelineId);
      setMessages([]);
    } catch (error) {
      message.error(errorText(error));
    }
  };

  const undo = async (target: AgentMessage) => {
    try {
      const restored = await pipelinesApi.undoAgent(pipelineId, target.id);
      callbacks.current.onGraph(restored.draft, restored.validation);
      await load();
    } catch (error) {
      message.error(t('pipelines.agent.undoFailed', { error: errorText(error) }));
    }
  };

  const answerRun = async (target: AgentMessage, decision: 'run' | 'dismiss') => {
    try {
      const answer = await pipelinesApi.answerRunRequest(pipelineId, target.id, decision);
      await load();
      if (answer.run) onRunStarted(answer.run);
    } catch (error) {
      message.error(t('pipelines.agent.runFailed', { error: errorText(error) }));
    }
  };

  const lastAssistant = [...messages].reverse().find((item) => item.role === 'assistant');
  const lastUndoable = [...messages].reverse().find((item) => item.meta?.can_undo);

  const renderAssistant = (item: AgentMessage) => {
    const actions = item.meta?.actions || [];
    const request = item.meta?.run_request;
    return (
      <div key={item.id} className="pl-agent-msg pl-agent-assistant">
        <MarkdownRenderer content={item.content} />
        {actions.length ? (
          <details className="pl-agent-steps">
            <summary>{t('pipelines.agent.steps', { count: actions.length })}</summary>
            <ul>
              {actions.map((action, index) => (
                <li key={`${action.name}-${index}`} className={action.ok ? '' : 'pl-agent-step-failed'}>
                  {action.ok ? <CheckCircleFilled /> : <CloseCircleFilled />} {action.summary}
                  {action.ok ? '' : ` — ${action.error || t('pipelines.agent.stepFailed')}`}
                </li>
              ))}
            </ul>
          </details>
        ) : null}
        {item.meta?.undone ? <Tag>{t('pipelines.agent.undone')}</Tag> : null}
        {lastUndoable?.id === item.id && !working ? (
          <Popconfirm
            title={t('pipelines.agent.undoConfirm')}
            okText={t('pipelines.agent.undo')}
            cancelText={t('common.cancel')}
            onConfirm={() => void undo(item)}
          >
            <Button size="small" icon={<UndoOutlined />}>
              {t('pipelines.agent.undo')}
            </Button>
          </Popconfirm>
        ) : null}
        {request ? (
          request.state === 'pending' && lastAssistant?.id === item.id ? (
            <div className="pl-agent-run-card" role="group" aria-label={t('pipelines.agent.runRequest')}>
              <Text strong>{t('pipelines.agent.runRequest')}</Text>
              <div>{request.reason}</div>
              <div className="pl-agent-run-actions">
                <Button type="primary" size="small" disabled={working} onClick={() => void answerRun(item, 'run')}>
                  {t('pipelines.agent.runApprove')}
                </Button>
                <Button size="small" disabled={working} onClick={() => void answerRun(item, 'dismiss')}>
                  {t('pipelines.agent.runDismiss')}
                </Button>
              </div>
            </div>
          ) : (
            <Tag color={request.state === 'approved' ? 'success' : 'default'}>
              {request.state === 'approved' ? t('pipelines.agent.runStarted') : t('pipelines.agent.runDismissed')}
            </Tag>
          )
        ) : null}
      </div>
    );
  };

  const suggestions = [t('pipelines.agent.suggest1'), t('pipelines.agent.suggest2'), t('pipelines.agent.suggest3')];

  return (
    <div className="pl-agent">
      <div className="pl-agent-head">
        <Text strong>{t('pipelines.agent.title')}</Text>
        <Popconfirm
          title={t('pipelines.agent.clearConfirm')}
          okText={t('pipelines.agent.clear')}
          cancelText={t('common.cancel')}
          onConfirm={() => void clear()}
          disabled={!messages.length || working}
        >
          <Button type="text" size="small" icon={<DeleteOutlined />} disabled={!messages.length || working} aria-label={t('pipelines.agent.clear')} />
        </Popconfirm>
      </div>
      <div className="pl-agent-list" ref={listRef} aria-live="polite">
        {!loaded ? (
          <Spin />
        ) : (
          <>
            {!messages.length && !pendingUser ? (
              <div className="pl-agent-empty">
                <Text type="secondary">{t('pipelines.agent.intro')}</Text>
                <div className="pl-agent-suggest">
                  {suggestions.map((text) => (
                    <button key={text} type="button" className="pl-agent-chip" disabled={working} onClick={() => void send(text)}>
                      {text}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            {messages.map((item) =>
              item.role === 'user' ? (
                <div key={item.id} className="pl-agent-msg pl-agent-user">
                  {item.content}
                </div>
              ) : (
                renderAssistant(item)
              ),
            )}
            {pendingUser ? <div className="pl-agent-msg pl-agent-user">{pendingUser}</div> : null}
            {sending ? (
              <div className="pl-agent-msg pl-agent-assistant pl-agent-live">
                {liveText ? <MarkdownRenderer content={liveText} /> : null}
                {liveSteps.length ? (
                  <ul className="pl-agent-live-steps">
                    {liveSteps.slice(-6).map((step, index) => (
                      <li key={`${step.summary}-${index}`} className={step.ok ? '' : 'pl-agent-step-failed'}>
                        {step.ok ? <CheckCircleFilled /> : <CloseCircleFilled />} {step.summary}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <Text type="secondary">
                  <Spin size="small" /> {t('pipelines.agent.working')}
                </Text>
              </div>
            ) : serverBusy ? (
              <Text type="secondary">
                <Spin size="small" /> {t('pipelines.agent.stillWorking')}
              </Text>
            ) : null}
          </>
        )}
      </div>
      <div className="pl-agent-compose">
        {selectedNode && includeSelection ? (
          <Tag closable onClose={() => setIncludeSelection(false)} closeIcon aria-label={t('pipelines.agent.dropNode')}>
            {t('pipelines.agent.aboutNode', { name: selectedNode.name })}
          </Tag>
        ) : null}
        <Input.TextArea
          ref={inputRef as never}
          value={input}
          autoSize={{ minRows: 2, maxRows: 6 }}
          maxLength={8000}
          placeholder={t('pipelines.agent.placeholder')}
          disabled={working}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void send(input);
            }
          }}
        />
        <Button type="primary" icon={<SendOutlined />} loading={sending} disabled={!input.trim() || working} onClick={() => void send(input)}>
          {t('pipelines.agent.send')}
        </Button>
      </div>
    </div>
  );
};

export default PipelineAgentPanel;
