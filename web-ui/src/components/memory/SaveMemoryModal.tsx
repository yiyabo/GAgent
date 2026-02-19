import React, { useEffect } from 'react';
import { Modal, Form, Input, Select, Tag, Space, message } from 'antd';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { memoryApi } from '@api/memory';
import { useMemoryStore } from '@store/memory';
import type { SaveMemoryRequest } from '@/types';

const { TextArea } = Input;
const { Option } = Select;

interface SaveMemoryModalProps {
  open: boolean;
  onCancel: () => void;
}

const SaveMemoryModal: React.FC<SaveMemoryModalProps> = ({ open, onCancel }) => {
  const [form] = Form.useForm();
  const queryClient = useQueryClient();
  const { addMemory } = useMemoryStore();

  const saveMutation = useMutation({
  mutationFn: (values: SaveMemoryRequest) => memoryApi.saveMemory(values),
  onSuccess: (newMemory) => {
  message.success('Memory saved successfully.');
  addMemory(newMemory);
  queryClient.invalidateQueries(['memories']);
  queryClient.invalidateQueries(['memory-stats']);
  form.resetFields();
  onCancel();
  },
  onError: (error: any) => {
  message.error(`Failed to save memory: ${error.message}`);
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
  title="Save Memory"
  open={open}
  onOk={handleSubmit}
  onCancel={onCancel}
  confirmLoading={saveMutation.isPending}
  width={700}
  okText="Save"
  cancelText="Cancel"
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
  label="Memory Content"
  name="content"
  rules={[
  { required: true, message: 'Please enter memory content.' },
  { min: 10, message: 'Memory content must be at least 10 characters.' },
  { max: 5000, message: 'Memory content cannot exceed 5000 characters.' },
  ]}
  >
  <TextArea
  placeholder="Enter memory content to save..."
  rows={6}
  showCount
  maxLength={5000}
  />
  </Form.Item>

  {}
  <Form.Item
  label="Memory Type"
  name="memory_type"
  rules={[{ required: true, message: 'Please select a memory type.' }]}
  >
  <Select placeholder="Select memory type">
  <Option value="conversation">
  <Space>
  <Tag color="blue">conversation</Tag>
  <span>Conversation</span>
  </Space>
  </Option>
  <Option value="experience">
  <Space>
  <Tag color="green">experience</Tag>
  <span>Experience</span>
  </Space>
  </Option>
  <Option value="knowledge">
  <Space>
  <Tag color="purple">knowledge</Tag>
  <span>Knowledge</span>
  </Space>
  </Option>
  <Option value="context">
  <Space>
  <Tag color="orange">context</Tag>
  <span>Context Snapshot</span>
  </Space>
  </Option>
  </Select>
  </Form.Item>

  {}
  <Form.Item
  label="Importance"
  name="importance"
  rules={[{ required: true, message: 'Please select an importance level.' }]}
  >
  <Select placeholder="Select importance">
  <Option value="critical">
  <Space>
  <Tag color="red">critical</Tag>
  <span>Critical long-term value</span>
  </Space>
  </Option>
  <Option value="high">
  <Space>
  <Tag color="orange">high</Tag>
  <span>High value</span>
  </Space>
  </Option>
  <Option value="medium">
  <Space>
  <Tag color="blue">medium</Tag>
  <span>Medium value</span>
  </Space>
  </Option>
  <Option value="low">
  <Space>
  <Tag color="default">low</Tag>
  <span>Low value</span>
  </Space>
  </Option>
  <Option value="temporary">
  <Space>
  <Tag color="default">temporary</Tag>
  <span>Temporary reference</span>
  </Space>
  </Option>
  </Select>
  </Form.Item>

  {}
  <Form.Item
  label="Tags"
  name="tags"
  tooltip="Enter tags; press Enter to add."
  >
  <Select
  mode="tags"
  placeholder="Enter tags..."
  tokenSeparators={[',']}
  maxTagCount="responsive"
  style={{ width: '100%' }}
  />
  </Form.Item>

  {}
  <Form.Item
  label="Keywords"
  name="keywords"
  tooltip="Enter searchable keywords."
  >
  <Select
  mode="tags"
  placeholder="Enter keywords..."
  tokenSeparators={[',']}
  maxTagCount="responsive"
  style={{ width: '100%' }}
  />
  </Form.Item>

  {}
  <Form.Item
  label="Context"
  name="context"
  tooltip="Optional context or domain for this memory."
  >
  <Input placeholder="e.g., project, issue, experiment" />
  </Form.Item>

  {}
  <Form.Item
  label="Task ID"
  name="related_task_id"
  tooltip="Optional: link this memory to a related task ID."
  >
  <Input
  type="number"
  placeholder="Enter task ID (optional)"
  min={1}
  />
  </Form.Item>
  </Form>

  {}
  <div style={{
  marginTop: '16px',
  padding: '12px',
  background: '#f0f5ff',
  borderRadius: '6px',
  fontSize: '13px',
  color: '#666'
  }}>
  <div><strong>Hint:</strong></div>
  <ul style={{ marginTop: '8px', marginBottom: 0, paddingLeft: '20px' }}>
  <li>Write specific content that is easy to search later.</li>
  <li>Add tags and keywords to improve retrieval quality.</li>
  <li>Choose an importance level based on long-term value.</li>
  </ul>
  </div>
  </Modal>
  );
};

export default SaveMemoryModal;
