import React, { useState } from 'react';
import { Button, Divider, Input, InputNumber, Progress, Select, Space, Tag, Typography, Upload } from 'antd';
import { DeleteOutlined, MessageOutlined, UploadOutlined } from '@ant-design/icons';
import type { NodeCatalog, ParamSpec, PipelineFile, PipelineGraph } from '@api/pipelines';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { pick, specIndex, updateNode, type GraphUpdate } from './graphModel';

const { Text, Paragraph } = Typography;

type UploadResult = Promise<PipelineFile | null>;

interface Props {
  catalog: NodeCatalog;
  graph: PipelineGraph;
  nodeId: string;
  files: PipelineFile[];
  issues: string[];
  onChange: (update: GraphUpdate) => void;
  onRemove: (nodeId: string) => void;
  onUpload: (file: File, onProgress: (percent: number) => void) => UploadResult;
  onAskAgent: (nodeId: string) => void;
}

const NodeInspector: React.FC<Props> = ({ catalog, graph, nodeId, files, issues, onChange, onRemove, onUpload, onAskAgent }) => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  const [uploading, setUploading] = useState<number | null>(null);
  const node = graph.nodes[nodeId];
  const spec = specIndex(catalog).get(node?.type || '');
  if (!node || !spec) return null;
  const typeTitle = (id: string) => pick(catalog.data_types.find((item) => item.id === id)?.title, language) || id;

  const setParam = (name: string, value: unknown) =>
    onChange((current) => {
      const params = { ...(current.nodes[nodeId]?.params || {}) };
      if (value === null || value === undefined || value === '') delete params[name];
      else params[name] = value;
      return updateNode(current, nodeId, { params });
    });

  const limitsOf = (param: ParamSpec): string => {
    if (param.kind !== 'integer' && param.kind !== 'number') return '';
    const parts: string[] = [];
    if (param.default !== null && param.default !== undefined) parts.push(t('pipelines.inspector.defaultValue', { value: String(param.default) }));
    if (param.minimum !== undefined && param.maximum !== undefined) {
      parts.push(t('pipelines.inspector.range', { min: String(param.minimum), max: String(param.maximum) }));
    }
    return parts.join(' · ');
  };

  const control = (param: ParamSpec) => {
    const value = node.params?.[param.name];
    if (param.kind === 'path') {
      return (
        <Space direction="vertical" style={{ width: '100%' }}>
          <Select
            allowClear
            style={{ width: '100%' }}
            placeholder={t('pipelines.inspector.chooseFile')}
            value={typeof value === 'string' && value ? value : undefined}
            options={files.map((file) => ({ value: file.path, label: file.name }))}
            onChange={(next) => setParam(param.name, next)}
          />
          <Upload
            showUploadList={false}
            accept=".fasta,.fa,.fna,.faa,.ffn,.fas,.fsa,.hmm"
            disabled={uploading !== null}
            customRequest={async ({ file, onSuccess, onError }) => {
              setUploading(0);
              const saved = await onUpload(file as File, setUploading).finally(() => setUploading(null));
              if (saved) {
                setParam(param.name, saved.path);
                onSuccess?.(saved);
              } else {
                onError?.(new Error('upload'));
              }
            }}
          >
            <Button size="small" icon={<UploadOutlined />}>
              {t('pipelines.inspector.upload')}
            </Button>
          </Upload>
          {uploading !== null ? <Progress percent={uploading} size="small" /> : null}
          <Text type="secondary" className="pl-hint">
            {t('pipelines.inspector.uploadHint')}
          </Text>
        </Space>
      );
    }
    if (param.kind === 'enum') {
      return (
        <Select
          style={{ width: '100%' }}
          value={typeof value === 'string' ? value : (param.default as string | undefined)}
          options={(param.choices || []).map((choice) => ({ value: choice, label: choice }))}
          onChange={(next) => setParam(param.name, next)}
        />
      );
    }
    if (param.kind === 'integer' || param.kind === 'number') {
      return (
        <InputNumber
          style={{ width: '100%' }}
          value={typeof value === 'number' ? value : (param.default as number | undefined)}
          min={param.minimum}
          max={param.maximum}
          step={param.kind === 'integer' || (param.maximum ?? 1) > 10 ? 1 : 0.05}
          precision={param.kind === 'integer' ? 0 : undefined}
          onChange={(next) => setParam(param.name, next)}
        />
      );
    }
    if (param.kind === 'text') {
      return (
        <Input.TextArea
          autoSize={{ minRows: 3, maxRows: 10 }}
          maxLength={param.max_length}
          value={typeof value === 'string' ? value : ''}
          onChange={(event) => setParam(param.name, event.target.value)}
        />
      );
    }
    return (
      <Input
        maxLength={param.max_length}
        value={typeof value === 'string' ? value : ''}
        onChange={(event) => setParam(param.name, event.target.value)}
      />
    );
  };

  return (
    <div className="pl-inspector">
      <Text type="secondary">{t('pipelines.inspector.nodeTitle')}</Text>
      <Typography.Title level={5} style={{ margin: '2px 0 4px' }}>
        {pick(spec.title, language)}
      </Typography.Title>
      <Paragraph type="secondary" className="pl-hint">
        {pick(spec.description, language)}
      </Paragraph>
      {spec.tool ? (
        <Text type="secondary" className="pl-hint">
          {t('pipelines.inspector.tool')}: <code>{`${spec.tool} ${spec.operation}`}</code>
        </Text>
      ) : null}
      {spec.slow ? <Tag className="pl-tag-slow">{t('pipelines.inspector.slow')}</Tag> : null}
      <Button size="small" icon={<MessageOutlined />} onClick={() => onAskAgent(nodeId)} style={{ alignSelf: 'flex-start', marginTop: 6 }}>
        {t('pipelines.agent.askAbout')}
      </Button>
      {issues.length ? (
        <div className="pl-issue-box">
          {issues.map((issue) => (
            <div key={issue}>{issue}</div>
          ))}
        </div>
      ) : null}

      <Divider style={{ margin: '10px 0' }} />
      <label className="pl-field-label" htmlFor={`pl-label-${nodeId}`}>
        {t('pipelines.inspector.label')}
      </label>
      <Input
        id={`pl-label-${nodeId}`}
        maxLength={80}
        placeholder={t('pipelines.inspector.labelPlaceholder')}
        value={node.label || ''}
        onChange={(event) => {
          const label = event.target.value || undefined;
          onChange((current) => updateNode(current, nodeId, { label }));
        }}
      />

      <Divider style={{ margin: '10px 0' }} />
      <Text strong>{t('pipelines.inspector.params')}</Text>
      {spec.params.length === 0 ? (
        <Paragraph type="secondary" className="pl-hint">
          {t('pipelines.inspector.noParams')}
        </Paragraph>
      ) : (
        spec.params.map((param) => (
          <div key={param.name} className="pl-field">
            <span className="pl-field-label">{pick(param.title, language)}</span>
            {control(param)}
            {param.help && param.kind !== 'path' ? (
              <Text type="secondary" className="pl-hint">
                {pick(param.help, language)}
              </Text>
            ) : null}
            {limitsOf(param) ? (
              <Text type="secondary" className="pl-hint">
                {limitsOf(param)}
              </Text>
            ) : null}
          </div>
        ))
      )}

      <Divider style={{ margin: '10px 0' }} />
      {spec.inputs.length ? (
        <>
          <Text strong>{t('pipelines.inspector.inputs')}</Text>
          {spec.inputs.map((port) => (
            <div key={port.name} className="pl-port-row">
              <span>{pick(port.title, language)}</span>
              <Text type="secondary">
                {t('pipelines.inspector.accepts', {
                  types: port.types.map((type) => (type === catalog.any_type ? '*' : typeTitle(type))).join(' / '),
                })}
              </Text>
            </div>
          ))}
        </>
      ) : null}
      <Text strong>{t('pipelines.inspector.outputs')}</Text>
      {spec.outputs.map((port) => (
        <div key={port.name} className="pl-port-row">
          <span>{pick(port.title, language)}</span>
          <Text type="secondary">{typeTitle(port.type)}</Text>
        </div>
      ))}

      <Divider style={{ margin: '10px 0' }} />
      <Button danger size="small" icon={<DeleteOutlined />} onClick={() => onRemove(nodeId)}>
        {t('pipelines.inspector.removeNode')}
      </Button>
    </div>
  );
};

export default NodeInspector;
