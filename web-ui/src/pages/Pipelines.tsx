import React, { useState } from 'react';
import { Alert, App, Button, Empty, Spin } from 'antd';
import { useQueryClient } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import { pipelinesApi } from '@api/pipelines';
import NewPipelineModal from '@components/pipelines/NewPipelineModal';
import PipelineEditor from '@components/pipelines/PipelineEditor';
import PipelineList from '@components/pipelines/PipelineList';
import '@components/pipelines/pipelines.css';
import { useNodeCatalog, usePipeline, usePipelineList, usePipelinesStatus } from '@hooks/usePipelines';
import { useT } from '@/i18n/useT';

const errorText = (error: unknown) => (error instanceof Error ? error.message : String(error));

/** The open pipeline and run live in the address (?id=…&run=…), so a reload or a shared link lands on them. */
const PipelinesWorkspace: React.FC = () => {
  const t = useT();
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  const [params, setParams] = useSearchParams();
  const id = params.get('id');
  const runId = params.get('run');
  const catalog = useNodeCatalog();
  const list = usePipelineList();
  const detail = usePipeline(id);
  const [newFrom, setNewFrom] = useState<{ template: string | null } | null>(null);
  const [creating, setCreating] = useState(false);

  const open = (pipelineId: string | null, run: string | null = null) => {
    const next: Record<string, string> = {};
    if (pipelineId) next.id = pipelineId;
    if (pipelineId && run) next.run = run;
    setParams(next);
  };

  const create = async (name: string, template: string | null) => {
    setCreating(true);
    try {
      const created = await pipelinesApi.create(name, template ? { template } : {});
      queryClient.setQueryData(['pipelines', 'detail', created.id], created);
      void queryClient.invalidateQueries({ queryKey: ['pipelines', 'list'] });
      setNewFrom(null);
      open(created.id);
    } catch (error) {
      message.error(t('pipelines.createFailed', { error: errorText(error) }));
    } finally {
      setCreating(false);
    }
  };

  if (catalog.isLoading) {
    return (
      <div className="pl-center-state">
        <Spin />
      </div>
    );
  }
  if (!catalog.data) {
    return <Alert type="error" showIcon message={t('pipelines.loadFailed', { error: errorText(catalog.error) })} />;
  }
  const templates = catalog.data.templates || [];

  let body: React.ReactNode;
  if (!id) {
    body = (
      <PipelineList
        pipelines={list.data}
        loading={list.isLoading}
        error={list.error}
        templates={templates}
        onOpen={(pipelineId) => open(pipelineId)}
        onNew={(template) => setNewFrom({ template })}
      />
    );
  } else if (detail.data && detail.data.id === id) {
    body = (
      <PipelineEditor
        key={id}
        catalog={catalog.data}
        pipeline={detail.data}
        runId={runId}
        onOpenRun={(run) => open(id, run)}
        onClose={() => open(null)}
      />
    );
  } else if (detail.error) {
    body = (
      <div className="pl-center-state">
        <Alert type="error" showIcon message={t('pipelines.openFailed', { error: errorText(detail.error) })} />
        <Button onClick={() => open(null)}>{t('pipelines.allPipelines')}</Button>
      </div>
    );
  } else {
    body = (
      <div className="pl-center-state">
        <Spin />
      </div>
    );
  }

  return (
    <>
      {body}
      <NewPipelineModal
        open={Boolean(newFrom)}
        templates={templates}
        initialTemplate={newFrom?.template ?? null}
        creating={creating}
        onCancel={() => setNewFrom(null)}
        onCreate={(name, template) => void create(name, template)}
      />
    </>
  );
};

const Pipelines: React.FC = () => {
  const t = useT();
  const enabled = usePipelinesStatus();
  return (
    <div className="pl-page">
      {enabled === null ? (
        <div className="pl-center-state">
          <Spin />
        </div>
      ) : enabled ? (
        <PipelinesWorkspace />
      ) : (
        <div className="pl-center-state">
          <Empty description={t('pipelines.disabled')} />
        </div>
      )}
    </div>
  );
};

export default Pipelines;
