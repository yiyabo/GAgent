import React, { useEffect } from 'react';
import { Modal, Form, Input, Select, Tag, Space, message } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { memoryApi } from '@api/memory';
import { useMemoryStore } from '@store/memory';
import type { SaveMemoryRequest } from '@/types';

import { useT } from '@/i18n/useT';
import { memoryTypeLabel } from '@/i18n/memoryLabels';
const { TextArea } = Input;
const { Option } = Select;

interface SaveMemoryModalProps {
  open: boolean;
  onCancel: () => void;
}

const SaveMemoryModal: React.FC<SaveMemoryModalProps> = ({ open, onCancel }) => {
  const t = useT();
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const { addMemory } = useMemoryStore();

  const saveMutation = useMutation({
  mutationFn: (values: SaveMemoryRequest) => memoryApi.saveMemory(values),
  onSuccess: (newMemory) => {
  message.success(t('memory.saved'));
  addMemory(newMemory);
  queryClient.invalidateQueries(['memories']);
  queryClient.invalidateQueries(['memory-stats']);
  form.resetFields();
  onCancel();
  },
  onError: (error: any) => {
  message.error(t('memory.saveFailed', { message: error.message }));
  },
  });

  useEffect(() => {
  if (!open) {
  form.resetFields();
  }
  }, [open, form]);

  const handleSubmit = async () => {
  try {
  const values = await form.validateFields();

  const formattedValues: SaveMemoryRequest = {
  ...values,
  tags: values.tags || [],
  keywords: values.keywords || [],
  };

  saveMutation.mutate(formattedValues);
  } catch (error) {
  console.error('Failed to validate save form:', error);
  }
  };

  return (
  <Modal
  title={t('memory.form.title')}
  open={open}
  onOk={handleSubmit}
  onCancel={onCancel}
  confirmLoading={saveMutation.isPending}
  width={700}
  okText={t('common.save')}
  cancelText={t('common.cancel')}
  destroyOnClose
  >
  <Form
  form={form}
  layout="vertical"
  initialValues={{
  memory_type: 'experience',
  importance: 'medium',
  context: 'General',
  }}
  >
  {}
  <Form.Item
  label={t('memory.form.content')}
  name="content"
  rules={[
  { required: true, message: t('memory.form.contentRequired') },
  { min: 10, message: t('memory.form.contentMin') },
  { max: 5000, message: t('memory.form.contentMax') },
  ]}
  >
  <TextArea
  placeholder={t('memory.form.contentPlaceholder')}
  rows={6}
  showCount
  maxLength={5000}
  />
  </Form.Item>

  {}
  <Form.Item
  label={t('memory.form.type')}
  name="memory_type"
  rules={[{ required: true, message: t('memory.form.typeRequired') }]}
  >
  <Select placeholder={t('memory.form.typePlaceholder')}>
  <Option value="conversation">
  <Space>
  <Tag color="blue">conversation</Tag>
  <span>{memoryTypeLabel(t, 'conversation')}</span>
  </Space>
  </Option>
  <Option value="experience">
  <Space>
  <Tag color="green">experience</Tag>
  <span>{memoryTypeLabel(t, 'experience')}</span>
  </Space>
  </Option>
  <Option value="knowledge">
  <Space>
  <Tag color="purple">knowledge</Tag>
  <span>{memoryTypeLabel(t, 'knowledge')}</span>
  </Space>
  </Option>
  <Option value="context">
  <Space>
  <Tag color="orange">context</Tag>
  <span>{t('memory.form.typeContextSnapshot')}</span>
  </Space>
  </Option>
  </Select>
  </Form.Item>

  {}
  <Form.Item
  label={t('memory.form.importance')}
  name="importance"
  rules={[{ required: true, message: t('memory.form.importanceRequired') }]}
  >
  <Select placeholder={t('memory.form.importancePlaceholder')}>
  <Option value="critical">
  <Space>
  <Tag color="red">critical</Tag>
  <span>{t('memory.form.importanceCritical')}</span>
  </Space>
  </Option>
  <Option value="high">
  <Space>
  <Tag color="orange">high</Tag>
  <span>{t('memory.form.importanceHigh')}</span>
  </Space>
  </Option>
  <Option value="medium">
  <Space>
  <Tag color="blue">medium</Tag>
  <span>{t('memory.form.importanceMedium')}</span>
  </Space>
  </Option>
  <Option value="low">
  <Space>
  <Tag color="default">low</Tag>
  <span>{t('memory.form.importanceLow')}</span>
  </Space>
  </Option>
  <Option value="temporary">
  <Space>
  <Tag color="default">temporary</Tag>
  <span>{t('memory.form.importanceTemporary')}</span>
  </Space>
  </Option>
  </Select>
  </Form.Item>

  {}
  <Form.Item
  label={t('memory.form.tags')}
  name="tags"
  tooltip={t('memory.form.tagsTip')}
  >
  <Select
  mode="tags"
  placeholder={t('memory.form.tagsPlaceholder')}
  tokenSeparators={[',']}
  maxTagCount="responsive"
  style={{ width: '100%' }}
  />
  </Form.Item>

  {}
  <Form.Item
  label={t('memory.form.keywords')}
  name="keywords"
  tooltip={t('memory.form.keywordsTip')}
  >
  <Select
  mode="tags"
  placeholder={t('memory.form.keywordsPlaceholder')}
  tokenSeparators={[',']}
  maxTagCount="responsive"
  style={{ width: '100%' }}
  />
  </Form.Item>

  {}
  <Form.Item
  label={t('memory.form.context')}
  name="context"
  tooltip={t('memory.form.contextTip')}
  >
  <Input placeholder={t('memory.form.contextPlaceholder')} />
  </Form.Item>

  {}
  <Form.Item
  label={t('memory.form.taskId')}
  name="related_task_id"
  tooltip={t('memory.form.taskIdTip')}
  >
  <Input
  type="number"
  placeholder={t('memory.form.taskIdPlaceholder')}
  min={1}
  />
  </Form.Item>
  </Form>

  {}
  <div style={{
  marginTop: '16px',
  padding: '12px',
  background: 'var(--color-action-bg)',
  borderRadius: '6px',
  fontSize: '13px',
  color: 'var(--text-secondary)'
  }}>
  <div><strong>{t('memory.form.hint')}</strong></div>
  <ul style={{ marginTop: '8px', marginBottom: 0, paddingLeft: '20px' }}>
  <li>{t('memory.form.hint1')}</li>
  <li>{t('memory.form.hint2')}</li>
  <li>{t('memory.form.hint3')}</li>
  </ul>
  </div>
  </Modal>
  );
};

export default SaveMemoryModal;
