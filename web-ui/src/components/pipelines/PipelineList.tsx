import React from 'react';
import { Alert, Button, Empty, Spin, Tag, Typography } from 'antd';
import { PlusOutlined } from '@ant-design/icons';
import type { PipelineSummary, PipelineTemplate } from '@api/pipelines';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { pick } from './graphModel';
import { StatusTag, formatServerTime } from './RunPanel';

const { Title, Paragraph } = Typography;

interface Props {
  pipelines: PipelineSummary[] | undefined;
  loading: boolean;
  error: unknown;
  templates: PipelineTemplate[];
  onOpen: (id: string) => void;
  onNew: (template: string | null) => void;
}

/** The page without a pipeline open: the user's pipelines and the templates to start from. */
const PipelineList: React.FC<Props> = ({ pipelines, loading, error, templates, onOpen, onNew }) => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  return (
    <div className="pl-landing">
      <div className="pl-landing-head">
        <div>
          <Title level={3} style={{ margin: 0 }}>
            {t('pipelines.title')}
          </Title>
          <Paragraph type="secondary" style={{ margin: '4px 0 0' }}>
            {t('pipelines.subtitle')}
          </Paragraph>
        </div>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => onNew(null)}>
          {t('pipelines.newPipeline')}
        </Button>
      </div>

      <section className="pl-section">
        <Title level={5}>{t('pipelines.listTitle')}</Title>
        {loading ? (
          <Spin />
        ) : error ? (
          <Alert type="error" showIcon message={t('pipelines.loadFailed', { error: error instanceof Error ? error.message : String(error) })} />
        ) : !pipelines?.length ? (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('pipelines.empty')} />
        ) : (
          <div className="pl-cards">
            {pipelines.map((pipeline) => (
              <button key={pipeline.id} type="button" className="pl-card" onClick={() => onOpen(pipeline.id)}>
                <span className="pl-card-title">{pipeline.name}</span>
                {pipeline.description ? <span className="pl-card-desc">{pipeline.description}</span> : null}
                <span className="pl-card-meta">
                  <Tag>{pipeline.latest_version ? t('pipelines.version', { version: pipeline.latest_version }) : t('pipelines.notFrozen')}</Tag>
                  <StatusTag status={pipeline.last_run_status} />
                  <span>{t('pipelines.updatedAt', { time: formatServerTime(pipeline.updated_at) })}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      {templates.length ? (
        <section className="pl-section">
          <Title level={5}>{t('pipelines.templatesTitle')}</Title>
          <div className="pl-cards">
            {templates.map((template) => (
              <div key={template.id} className="pl-card pl-card-template">
                <span className="pl-card-title">{pick(template.title, language)}</span>
                <span className="pl-card-desc">{pick(template.description, language)}</span>
                <span className="pl-card-meta">
                  <span>{t('pipelines.nodeCount', { count: template.node_count })}</span>
                  <Button size="small" onClick={() => onNew(template.id)}>
                    {t('pipelines.useTemplate')}
                  </Button>
                </span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
};

export default PipelineList;
