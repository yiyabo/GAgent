import React from 'react';
import {
  Typography,
  Space,
  Drawer,
  Tag,
  Button,
} from 'antd';
import type { ToolResultPayload } from '@/types';
import ToolResultCard from '../ToolResultCard';

const { Text } = Typography;

interface ActionSummaryProps {
  items: Array<Record<string, any>>;
}

const ActionSummary: React.FC<ActionSummaryProps> = ({ items }) => {
  if (!items.length) return null;
  return (
    <div style={{ marginTop: 10 }}>
      <Space direction="vertical" size={4} style={{ width: '100%' }}>
        <Text strong style={{ color: 'var(--text-primary)', fontSize: 12 }}>动作摘要</Text>
        <div>
          {items.map((item, index) => {
            const order = typeof item.order === 'number' ? item.order : index + 1;
            const success = item.success;
            const icon = success === true ? '✅' : success === false ? '⚠️' : '⏳';
            const kind = typeof item.kind === 'string' ? item.kind : 'action';
            const name = typeof item.name === 'string' && item.name ? `/${item.name}` : '';
            const messageText =
              typeof item.message === 'string' && item.message.trim().length > 0
                ? ` - ${item.message}`
                : '';
            return (
              <div key={`${order}_${kind}_${name}`} style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 2 }}>
                <Text>
                  {icon} 步骤 {order}: {kind}
                  {name}
                  {messageText}
                </Text>
              </div>
            );
          })}
        </div>
      </Space>
    </div>
  );
};

interface ToolStatusBarProps {
  toolResults: ToolResultPayload[];
  isPendingAction: boolean;
  unifiedStream: boolean;
  onOpenDrawer: () => void;
}

export const ToolStatusBar: React.FC<ToolStatusBarProps> = ({
  toolResults,
  isPendingAction,
  unifiedStream,
  onOpenDrawer,
}) => {
  if (isPendingAction) return null;
  if (unifiedStream) return null;
  if (!toolResults.length) return null;

  const successCount = toolResults.filter((item) => item.result?.success !== false).length;
  const failCount = toolResults.length - successCount;
  const statusTag = failCount > 0 ? (
    <Tag color="red">部分失败</Tag>
  ) : (
    <Tag color="green">全部成功</Tag>
  );

  const toolTags = toolResults.slice(0, 3).map((item, index) => (
    <Tag key={`${item.name ?? 'tool'}_${index}`} color="blue">
      {item.name ?? 'tool'}
    </Tag>
  ));

  return (
    <div
      style={{
        marginTop: 10,
        padding: '8px 12px',
        borderRadius: 'var(--radius-sm)',
        background: 'var(--bg-tertiary)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        flexWrap: 'wrap',
        fontSize: 11,
      }}
    >
      <Space size={6} wrap align="center">
        <Text strong style={{ fontSize: 11 }}>工具</Text>
        {statusTag}
        <Space size={[4, 4]} wrap>
          {toolTags}
          {toolResults.length > 3 && (
            <Text type="secondary">+{toolResults.length - 3} 更多</Text>
          )}
        </Space>
        <Text type="secondary">
          {failCount > 0 ? `失败 ${failCount} · 成功 ${successCount}` : `成功 ${successCount}`}
        </Text>
      </Space>
      <Button type="link" size="small" onClick={onOpenDrawer} style={{ padding: 0 }}>
        查看调用流程
      </Button>
    </div>
  );
};

interface ToolResultDrawerProps {
  toolResults: ToolResultPayload[];
  unifiedStream: boolean;
  open: boolean;
  onClose: () => void;
  actionsSummary?: Array<Record<string, any>>;
}

const ToolResultDrawer: React.FC<ToolResultDrawerProps> = ({
  toolResults,
  unifiedStream,
  open,
  onClose,
  actionsSummary,
}) => {
  if (!toolResults.length) return null;
  if (unifiedStream) return null;
  const summaryItems = Array.isArray(actionsSummary) ? actionsSummary : [];
  return (
    <Drawer
      title="工具调用详情"
      placement="right"
      width={640}
      open={open}
      onClose={onClose}
    >
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <ActionSummary items={summaryItems} />
        {toolResults.map((result, index) => (
          <ToolResultCard
            key={`${result.name ?? 'tool'}_${index}`}
            payload={result}
            defaultOpen={false}
          />
        ))}
      </Space>
    </Drawer>
  );
};

export { ToolResultDrawer, ActionSummary };
export default ToolResultDrawer;
