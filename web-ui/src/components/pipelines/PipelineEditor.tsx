import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { App, Button, Input, Popconfirm, Spin, Tag, Tooltip, Typography } from 'antd';
import { ArrowLeftOutlined, CaretRightOutlined, DeleteOutlined, LockOutlined, PartitionOutlined } from '@ant-design/icons';
import { useQueryClient } from '@tanstack/react-query';
import { PipelinesApiError, pipelinesApi } from '@api/pipelines';
import type { NodeCatalog, NodeTypeSpec, PipelineDetail, PipelineFile, PipelineGraph, ValidationResult } from '@api/pipelines';
import { isActiveRun, usePipelineFiles, usePipelineRun, usePipelineRuns } from '@hooks/usePipelines';
import { useLanguageStore } from '@/i18n/language';
import type { TranslationKey } from '@/i18n/keys';
import { useT } from '@/i18n/useT';
import NodeInspector from './NodeInspector';
import NodePalette from './NodePalette';
import PipelineCanvas, { type ConnectionProblem, type PipelineFlowInstance } from './PipelineCanvas';
import PipelineSidePanel from './PipelineSidePanel';
import RunPanel from './RunPanel';
import { addNode, autoLayout, freeSpot, normalizeGraph, pick, removeNode, specIndex, type GraphUpdate } from './graphModel';
import { issuesByNode, shownIssues } from './issues';

const { Paragraph } = Typography;

const SAVE_DELAY_MS = 800;
const NEW_NODE_SIZE = { width: 230, height: 110 };

const CONNECTION_KEYS: Record<ConnectionProblem, TranslationKey> = {
  self: 'pipelines.connection.self',
  type: 'pipelines.connection.type',
  report: 'pipelines.connection.report',
  loop: 'pipelines.connection.loop',
  unknown: 'pipelines.connection.unknown',
};

type SaveState = 'saved' | 'pending' | 'saving' | 'error';

const SAVE_KEYS: Record<SaveState, TranslationKey> = {
  saved: 'pipelines.saved',
  pending: 'pipelines.unsaved',
  saving: 'pipelines.saving',
  error: 'pipelines.notSaved',
};

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

interface Props {
  catalog: NodeCatalog;
  pipeline: PipelineDetail;
  runId: string | null;
  onOpenRun: (runId: string | null) => void;
  onClose: () => void;
}

/**
 * One pipeline: the canvas, the palette and the side panel, or one of its runs. Every edit is saved on
 * its own shortly after it is made; a draft does not have to be valid to be saved.
 */
const PipelineEditor: React.FC<Props> = ({ catalog, pipeline, runId, onOpenRun, onClose }) => {
  const t = useT();
  const { message } = App.useApp();
  const language = useLanguageStore((state) => state.language);
  const queryClient = useQueryClient();
  const specs = useMemo(() => specIndex(catalog), [catalog]);

  const [graph, setGraph] = useState<PipelineGraph>(() => normalizeGraph(pipeline.draft));
  const [name, setName] = useState(pipeline.name);
  const [description, setDescription] = useState(pipeline.description || '');
  const [validation, setValidation] = useState<ValidationResult>(pipeline.validation);
  const [latestVersion, setLatestVersion] = useState(pipeline.latest_version);
  const [selected, setSelected] = useState<string | null>(null);
  const [runSelected, setRunSelected] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [busy, setBusy] = useState<'run' | 'freeze' | 'cancel' | 'delete' | null>(null);

  // What the next save sends. Updated synchronously, so a save never sends an older edit.
  const latest = useRef({ graph, name, description });
  const savedName = useRef(pipeline.name);
  const editSeq = useRef(0);
  const savedSeq = useRef(0);
  const inFlight = useRef<Promise<unknown> | null>(null);
  const timer = useRef<number>();
  const flowRef = useRef<PipelineFlowInstance | null>(null);
  const centerRef = useRef<HTMLDivElement>(null);

  const files = usePipelineFiles(pipeline.id);
  const runs = usePipelineRuns(pipeline.id);
  const run = usePipelineRun(runId);
  const viewingRun = Boolean(runId);

  const save = useCallback(async (): Promise<boolean> => {
    window.clearTimeout(timer.current);
    while (inFlight.current) await inFlight.current;
    if (editSeq.current === savedSeq.current) return true;
    const seq = editSeq.current;
    const { graph: draft, name: title, description: text } = latest.current;
    setSaveState('saving');
    const request = pipelinesApi.save(pipeline.id, { name: title.trim() || undefined, description: text, graph: draft });
    inFlight.current = request.catch(() => undefined);
    try {
      const saved = await request;
      savedSeq.current = Math.max(savedSeq.current, seq);
      savedName.current = saved.name;
      if (editSeq.current === seq) setValidation(saved.validation);
      setSaveState(editSeq.current === savedSeq.current ? 'saved' : 'pending');
      queryClient.setQueryData(['pipelines', 'detail', pipeline.id], saved);
      void queryClient.invalidateQueries({ queryKey: ['pipelines', 'list'] });
      return true;
    } catch (error) {
      setSaveState('error');
      message.error(t('pipelines.saveFailed', { error: errorText(error) }));
      return false;
    } finally {
      inFlight.current = null;
    }
  }, [pipeline.id, queryClient, message, t]);

  const saveRef = useRef(save);
  saveRef.current = save;

  useEffect(
    () => () => {
      // Leaving the editor (another pipeline, another page) still keeps the last edit.
      window.clearTimeout(timer.current);
      if (editSeq.current !== savedSeq.current) void saveRef.current();
    },
    [],
  );

  useEffect(() => {
    if (saveState === 'saved') return undefined;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [saveState]);

  const schedule = useCallback(() => {
    editSeq.current += 1;
    setSaveState('pending');
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void saveRef.current(), SAVE_DELAY_MS);
  }, []);

  const changeGraph = useCallback(
    (update: GraphUpdate) => {
      const next = update(latest.current.graph);
      if (next === latest.current.graph) return;
      latest.current = { ...latest.current, graph: next };
      setGraph(next);
      schedule();
    },
    [schedule],
  );

  const changeName = (value: string) => {
    setName(value);
    latest.current = { ...latest.current, name: value };
    schedule();
  };

  const changeDescription = (value: string) => {
    setDescription(value);
    latest.current = { ...latest.current, description: value };
    schedule();
  };

  const add = useCallback(
    (spec: NodeTypeSpec) => {
      const box = centerRef.current?.getBoundingClientRect();
      const flow = flowRef.current;
      const center =
        flow && box && box.width
          ? flow.screenToFlowPosition({ x: box.left + box.width / 2, y: box.top + box.height / 2 })
          : { x: 40 + NEW_NODE_SIZE.width / 2, y: 40 + NEW_NODE_SIZE.height / 2 };
      const start = { x: center.x - NEW_NODE_SIZE.width / 2, y: center.y - NEW_NODE_SIZE.height / 2 };
      let created = '';
      changeGraph((current) => {
        const result = addNode(current, spec, freeSpot(current, start));
        created = result.id;
        return result.graph;
      });
      setSelected(created);
    },
    [changeGraph],
  );

  const tidy = () => {
    changeGraph((current) => ({ ...current, layout: autoLayout(current) }));
    window.setTimeout(() => void flowRef.current?.fitView({ padding: 0.15, maxZoom: 1, duration: 300 }), 60);
  };

  const upload = useCallback(
    async (file: File, onProgress: (percent: number) => void): Promise<PipelineFile | null> => {
      try {
        const saved = await pipelinesApi.upload(pipeline.id, file, onProgress);
        message.success(t('pipelines.inspector.uploaded', { name: saved.name }));
        void queryClient.invalidateQueries({ queryKey: ['pipelines', 'files', pipeline.id] });
        return saved;
      } catch (error) {
        message.error(t('pipelines.inspector.uploadFailed', { error: errorText(error) }));
        return null;
      }
    },
    [pipeline.id, message, t, queryClient],
  );

  const refreshAfterRunChange = () => {
    void queryClient.invalidateQueries({ queryKey: ['pipelines', 'runs', pipeline.id] });
    void queryClient.invalidateQueries({ queryKey: ['pipelines', 'list'] });
  };

  const freeze = async () => {
    setBusy('freeze');
    try {
      if (!(await save())) return;
      const frozen = await pipelinesApi.freeze(pipeline.id);
      message.success(
        frozen.version === latestVersion
          ? t('pipelines.frozenUnchanged', { version: frozen.version })
          : t('pipelines.frozen', { version: frozen.version }),
      );
      setLatestVersion(frozen.version);
      void queryClient.invalidateQueries({ queryKey: ['pipelines', 'list'] });
    } catch (error) {
      if (error instanceof PipelinesApiError && error.validation) setValidation(error.validation);
      message.error(t('pipelines.freezeFailed', { error: errorText(error) }));
    } finally {
      setBusy(null);
    }
  };

  const startRun = async () => {
    setBusy('run');
    try {
      if (!(await save())) return;
      const started = await pipelinesApi.startRun(pipeline.id);
      setLatestVersion(started.version);
      queryClient.setQueryData(['pipelines', 'run', started.id], started);
      refreshAfterRunChange();
      message.success(t('pipelines.runStarted'));
      setRunSelected(null);
      onOpenRun(started.id);
    } catch (error) {
      if (error instanceof PipelinesApiError && error.validation) setValidation(error.validation);
      message.error(t('pipelines.runFailed', { error: errorText(error) }));
    } finally {
      setBusy(null);
    }
  };

  const cancelRun = async () => {
    if (!runId) return;
    setBusy('cancel');
    try {
      queryClient.setQueryData(['pipelines', 'run', runId], await pipelinesApi.cancelRun(runId));
    } catch (error) {
      message.error(t('pipelines.cancelFailed', { error: errorText(error) }));
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy('delete');
    try {
      if (!(await save())) return;
      await pipelinesApi.remove(pipeline.id);
      queryClient.removeQueries({ queryKey: ['pipelines', 'detail', pipeline.id] });
      void queryClient.invalidateQueries({ queryKey: ['pipelines', 'list'] });
      onClose();
    } catch (error) {
      message.error(t('pipelines.deleteFailed', { error: errorText(error) }));
    } finally {
      setBusy(null);
    }
  };

  const runStatus = run.data?.status;
  useEffect(() => {
    if (runStatus && !isActiveRun(runStatus)) refreshAfterRunChange();
  }, [runStatus]);

  const issues = useMemo(
    () => shownIssues(t, validation, graph, specs, language).filter((issue) => !issue.node || graph.nodes[issue.node]),
    [t, validation, graph, specs, language],
  );
  const nodeIssues = useMemo(() => issuesByNode(issues), [issues]);
  const nodeName = useCallback(
    (id: string) => {
      const node = graph.nodes[id];
      const spec = specs.get(node?.type || '');
      const title = spec ? pick(spec.title, language) : id;
      return node?.label ? `${title} · ${node.label}` : title;
    },
    [graph, specs, language],
  );
  const runGraph = useMemo(() => (run.data?.graph ? normalizeGraph(run.data.graph) : null), [run.data?.graph]);
  const selectedNode = selected && graph.nodes[selected] ? selected : null;
  const errorsCount = validation.errors.length;

  const side = () => {
    if (viewingRun) {
      if (run.data) {
        return (
          <RunPanel
            catalog={catalog}
            run={run.data}
            selectedNode={runSelected}
            onBack={() => onOpenRun(null)}
            onCancel={() => void cancelRun()}
            cancelling={busy === 'cancel'}
          />
        );
      }
      return run.error ? (
        <div className="pl-inspector">
          <Button size="small" icon={<ArrowLeftOutlined />} onClick={() => onOpenRun(null)}>
            {t('pipelines.run_view.backToEdit')}
          </Button>
          <Paragraph type="danger" style={{ marginTop: 12 }}>
            {t('pipelines.run_view.loadFailed', { error: errorText(run.error) })}
          </Paragraph>
        </div>
      ) : (
        <Spin />
      );
    }
    if (selectedNode) {
      return (
        <NodeInspector
          catalog={catalog}
          graph={graph}
          nodeId={selectedNode}
          files={files.data || []}
          issues={nodeIssues.get(selectedNode) || []}
          onChange={changeGraph}
          onRemove={(id) => {
            changeGraph((current) => removeNode(current, id));
            setSelected(null);
          }}
          onUpload={upload}
        />
      );
    }
    return (
      <PipelineSidePanel
        description={description}
        onDescriptionChange={changeDescription}
        issues={issues}
        nodeName={nodeName}
        onSelectNode={setSelected}
        runs={runs.data}
        onOpenRun={(id) => {
          setRunSelected(null);
          onOpenRun(id);
        }}
      />
    );
  };

  return (
    <div className="pl-editor">
      <div className="pl-toolbar">
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={onClose}>
          {t('pipelines.allPipelines')}
        </Button>
        <Input
          className="pl-name"
          value={name}
          maxLength={120}
          aria-label={t('pipelines.inspector.name')}
          onChange={(event) => changeName(event.target.value)}
          onBlur={() => {
            if (!name.trim()) {
              setName(savedName.current);
              latest.current = { ...latest.current, name: savedName.current };
            }
          }}
        />
        <span className={`pl-save pl-save-${saveState}`} role="status">
          {t(SAVE_KEYS[saveState])}
          {saveState === 'error' ? (
            <Button type="link" size="small" onClick={() => void save()}>
              {t('common.retry')}
            </Button>
          ) : null}
        </span>
        <Tag>{latestVersion ? t('pipelines.version', { version: latestVersion }) : t('pipelines.notFrozen')}</Tag>
        {errorsCount ? (
          <Tag color="error">{t('pipelines.problems', { count: errorsCount })}</Tag>
        ) : (
          <Tag color="success">{t('pipelines.ready')}</Tag>
        )}
        {validation.warnings.length ? <Tag color="warning">{t('pipelines.warnings', { count: validation.warnings.length })}</Tag> : null}
        <span className="pl-toolbar-spacer" />
        {!viewingRun ? (
          <Tooltip title={t('pipelines.tidyTip')}>
            <Button icon={<PartitionOutlined />} onClick={tidy} disabled={!Object.keys(graph.nodes).length}>
              {t('pipelines.tidy')}
            </Button>
          </Tooltip>
        ) : null}
        <Tooltip title={errorsCount ? t('pipelines.fixFirst') : t('pipelines.freezeTip')}>
          <Button icon={<LockOutlined />} loading={busy === 'freeze'} disabled={Boolean(errorsCount) || busy !== null} onClick={() => void freeze()}>
            {t('pipelines.freeze')}
          </Button>
        </Tooltip>
        <Tooltip title={errorsCount ? t('pipelines.fixFirst') : t('pipelines.runTip')}>
          <Button
            type="primary"
            icon={<CaretRightOutlined />}
            loading={busy === 'run'}
            disabled={Boolean(errorsCount) || busy !== null}
            onClick={() => void startRun()}
          >
            {t('pipelines.run')}
          </Button>
        </Tooltip>
        <Popconfirm
          title={t('pipelines.deleteConfirm')}
          okText={t('pipelines.deleteOk')}
          cancelText={t('common.cancel')}
          okButtonProps={{ danger: true }}
          onConfirm={() => void remove()}
        >
          <Tooltip title={t('pipelines.deletePipeline')}>
            <Button danger icon={<DeleteOutlined />} loading={busy === 'delete'} aria-label={t('pipelines.deletePipeline')} />
          </Tooltip>
        </Popconfirm>
      </div>
      <div className="pl-body">
        {!viewingRun ? (
          <aside className="pl-left">
            <NodePalette catalog={catalog} onAdd={add} />
          </aside>
        ) : null}
        <div className="pl-center" ref={centerRef}>
          {viewingRun ? (
            runGraph ? (
              <PipelineCanvas
                key={`run-${runId}`}
                catalog={catalog}
                graph={runGraph}
                readOnly
                runNodes={run.data?.nodes}
                selectedNode={runSelected}
                onSelectNode={setRunSelected}
                onInit={(instance) => {
                  flowRef.current = instance;
                }}
              />
            ) : (
              <div className="pl-center-state">
                <Spin />
              </div>
            )
          ) : (
            <PipelineCanvas
              key="draft"
              catalog={catalog}
              graph={graph}
              issues={nodeIssues}
              selectedNode={selectedNode}
              onSelectNode={setSelected}
              onChange={changeGraph}
              onRejectConnection={(problem) => void message.warning(t(CONNECTION_KEYS[problem]))}
              onInit={(instance) => {
                flowRef.current = instance;
              }}
            />
          )}
        </div>
        <aside className="pl-right">{side()}</aside>
      </div>
    </div>
  );
};

export default PipelineEditor;
