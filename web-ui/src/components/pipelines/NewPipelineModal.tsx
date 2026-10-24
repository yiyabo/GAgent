import React, { useEffect, useState } from 'react';
import { Input, Modal, Radio } from 'antd';
import type { PipelineTemplate } from '@api/pipelines';
import { useLanguageStore } from '@/i18n/language';
import { useT } from '@/i18n/useT';
import { pick } from './graphModel';

interface Props {
  open: boolean;
  templates: PipelineTemplate[];
  initialTemplate: string | null;
  creating: boolean;
  onCancel: () => void;
  onCreate: (name: string, template: string | null) => void;
}

const NewPipelineModal: React.FC<Props> = ({ open, templates, initialTemplate, creating, onCancel, onCreate }) => {
  const t = useT();
  const language = useLanguageStore((state) => state.language);
  const [name, setName] = useState('');
  const [template, setTemplate] = useState<string | null>(null);
  const [named, setNamed] = useState(false);

  const suggested = (id: string | null) => {
    const chosen = templates.find((item) => item.id === id);
    return chosen ? pick(chosen.title, language) : t('pipelines.defaultName');
  };

  useEffect(() => {
    if (!open) return;
    setTemplate(initialTemplate);
    setName(suggested(initialTemplate));
    setNamed(false);
  }, [open]);

  const choose = (id: string | null) => {
    setTemplate(id);
    // Follow the choice until the user types a name of their own.
    if (!named) setName(suggested(id));
  };

  const submit = () => {
    if (name.trim() && !creating) onCreate(name.trim(), template);
  };

  return (
    <Modal
      open={open}
      title={t('pipelines.newPipeline')}
      okText={t('pipelines.create')}
      cancelText={t('common.cancel')}
      confirmLoading={creating}
      okButtonProps={{ disabled: !name.trim() }}
      onCancel={onCancel}
      onOk={submit}
      destroyOnHidden
    >
      <label className="pl-field-label" htmlFor="pl-new-name">
        {t('pipelines.inspector.name')}
      </label>
      <Input
        id="pl-new-name"
        value={name}
        maxLength={120}
        placeholder={t('pipelines.newNamePlaceholder')}
        onChange={(event) => {
          setName(event.target.value);
          setNamed(true);
        }}
        onPressEnter={submit}
      />
      <div className="pl-field-label" style={{ marginTop: 14 }}>
        {t('pipelines.startFrom')}
      </div>
      <Radio.Group className="pl-start-options" value={template ?? ''} onChange={(event) => choose(event.target.value || null)}>
        <Radio value="">{t('pipelines.startBlank')}</Radio>
        {templates.map((item) => (
          <Radio key={item.id} value={item.id}>
            <span>{pick(item.title, language)}</span>
            <span className="pl-start-desc">{pick(item.description, language)}</span>
          </Radio>
        ))}
      </Radio.Group>
    </Modal>
  );
};

export default NewPipelineModal;
