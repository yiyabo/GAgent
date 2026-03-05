import React from 'react';
import { Button, Select, Space, Tag } from 'antd';
import {
  ClearOutlined,
  DisconnectOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { TerminalMode } from '@/types';

interface Props {
  mode: TerminalMode;
  connected: boolean;
  terminalId: string | null;
  onModeChange: (mode: TerminalMode) => void;
  onCreate: () => void;
  onClose: () => void;
  onReplay: () => void;
  onClear: () => void;
  onRefresh: () => void;
}

const TerminalToolbar: React.FC<Props> = ({
  mode,
  connected,
  terminalId,
  onModeChange,
  onCreate,
  onClose,
  onReplay,
  onClear,
  onRefresh,
}) => {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: 8,
        padding: '8px 12px',
        borderBottom: '1px solid var(--border-color)',
        background: 'var(--bg-tertiary)',
      }}
    >
      <Space size={8} wrap>
        <Select<TerminalMode>
          size="small"
          value={mode}
          onChange={onModeChange}
          options={[
            { value: 'sandbox', label: 'Sandbox PTY' },
            { value: 'ssh', label: 'SSH' },
          ]}
          style={{ width: 120 }}
        />
        <Tag color={connected ? 'success' : 'default'}>{connected ? 'Connected' : 'Disconnected'}</Tag>
        {terminalId ? <Tag>{terminalId.slice(0, 8)}</Tag> : null}
      </Space>

      <Space size={6} wrap>
        <Button size="small" icon={<ReloadOutlined />} onClick={onRefresh}>
          Refresh
        </Button>
        <Button size="small" icon={<PlusOutlined />} onClick={onCreate}>
          New
        </Button>
        <Button size="small" icon={<DisconnectOutlined />} onClick={onClose} disabled={!terminalId}>
          Close
        </Button>
        <Button size="small" icon={<PlayCircleOutlined />} onClick={onReplay} disabled={!terminalId}>
          Replay
        </Button>
        <Button size="small" icon={<ClearOutlined />} onClick={onClear}>
          Clear
        </Button>
      </Space>
    </div>
  );
};

export default TerminalToolbar;
