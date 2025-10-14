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

  // 保存记忆的Mutation
  const saveMutation = useMutation({
    mutationFn: (values: SaveMemoryRequest) => memoryApi.saveMemory(values),
    onSuccess: (newMemory) => {
      message.success('✅ 记忆保存成功!');
      addMemory(newMemory);
      queryClient.invalidateQueries(['memories']);
      queryClient.invalidateQueries(['memory-stats']);
      form.resetFields();
      onCancel();
    },
    onError: (error: any) => {
      message.error(`❌ 保存失败: ${error.message}`);
    },
  });

  // Modal关闭时重置表单
  useEffect(() => {
    if (!open) {
      form.resetFields();
    }
  }, [open, form]);

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields();

      // 处理标签和关键词 (确保是数组)
      const formattedValues: SaveMemoryRequest = {
        ...values,
        tags: values.tags || [],
        keywords: values.keywords || [],
      };

      saveMutation.mutate(formattedValues);
    } catch (error) {
      console.error('表单验证失败:', error);
    }
  };

  return (
    <Modal
      title="💾 保存新记忆"
      open={open}
      onOk={handleSubmit}
      onCancel={onCancel}
      confirmLoading={saveMutation.isPending}
      width={700}
      okText="保存"
      cancelText="取消"
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
        {/* 记忆内容 */}
        <Form.Item
          label="记忆内容"
          name="content"
          rules={[
            { required: true, message: '请输入记忆内容' },
            { min: 10, message: '记忆内容至少10个字符' },
            { max: 5000, message: '记忆内容不能超过5000个字符' },
          ]}
        >
          <TextArea
            placeholder="输入要保存的记忆内容..."
            rows={6}
            showCount
            maxLength={5000}
          />
        </Form.Item>

        {/* 记忆类型 */}
        <Form.Item
          label="记忆类型"
          name="memory_type"
          rules={[{ required: true, message: '请选择记忆类型' }]}
        >
          <Select placeholder="选择记忆类型">
            <Option value="conversation">
              <Space>
                <Tag color="blue">对话</Tag>
                <span>重要的对话内容</span>
              </Space>
            </Option>
            <Option value="experience">
              <Space>
                <Tag color="green">经验</Tag>
                <span>操作经验和学习成果</span>
              </Space>
            </Option>
            <Option value="knowledge">
              <Space>
                <Tag color="purple">知识</Tag>
                <span>领域知识和概念</span>
              </Space>
            </Option>
            <Option value="context">
              <Space>
                <Tag color="orange">上下文</Tag>
                <span>环境和背景信息</span>
              </Space>
            </Option>
          </Select>
        </Form.Item>

        {/* 重要性级别 */}
        <Form.Item
          label="重要性级别"
          name="importance"
          rules={[{ required: true, message: '请选择重要性级别' }]}
        >
          <Select placeholder="选择重要性级别">
            <Option value="critical">
              <Space>
                <Tag color="red">关键</Tag>
                <span>永久保存</span>
              </Space>
            </Option>
            <Option value="high">
              <Space>
                <Tag color="orange">高</Tag>
                <span>长期保存</span>
              </Space>
            </Option>
            <Option value="medium">
              <Space>
                <Tag color="blue">中</Tag>
                <span>定期清理</span>
              </Space>
            </Option>
            <Option value="low">
              <Space>
                <Tag>低</Tag>
                <span>短期保存</span>
              </Space>
            </Option>
            <Option value="temporary">
              <Space>
                <Tag color="gray">临时</Tag>
                <span>自动清理</span>
              </Space>
            </Option>
          </Select>
        </Form.Item>

        {/* 标签 */}
        <Form.Item
          label="标签"
          name="tags"
          tooltip="输入标签后按回车添加,支持多个标签"
        >
          <Select
            mode="tags"
            placeholder="输入标签..."
            tokenSeparators={[',']}
            maxTagCount="responsive"
            style={{ width: '100%' }}
          />
        </Form.Item>

        {/* 关键词 */}
        <Form.Item
          label="关键词"
          name="keywords"
          tooltip="输入关键词后按回车添加,用于语义搜索"
        >
          <Select
            mode="tags"
            placeholder="输入关键词..."
            tokenSeparators={[',']}
            maxTagCount="responsive"
            style={{ width: '100%' }}
          />
        </Form.Item>

        {/* 上下文 */}
        <Form.Item
          label="上下文"
          name="context"
          tooltip="描述记忆产生的环境或场景"
        >
          <Input placeholder="例如: 项目开发、问题排查、学习笔记等" />
        </Form.Item>

        {/* 关联任务ID (可选) */}
        <Form.Item
          label="关联任务ID"
          name="related_task_id"
          tooltip="如果这条记忆与某个任务相关,可以填写任务ID"
        >
          <Input
            type="number"
            placeholder="输入任务ID (可选)"
            min={1}
          />
        </Form.Item>
      </Form>

      {/* 提示信息 */}
      <div style={{
        marginTop: '16px',
        padding: '12px',
        background: '#f0f5ff',
        borderRadius: '6px',
        fontSize: '13px',
        color: '#666'
      }}>
        <div>💡 <strong>提示:</strong></div>
        <ul style={{ marginTop: '8px', marginBottom: 0, paddingLeft: '20px' }}>
          <li>记忆内容会自动生成嵌入向量,用于语义搜索</li>
          <li>标签和关键词有助于快速检索和分类</li>
          <li>重要性级别决定记忆的保留时长</li>
        </ul>
      </div>
    </Modal>
  );
};

export default SaveMemoryModal;
