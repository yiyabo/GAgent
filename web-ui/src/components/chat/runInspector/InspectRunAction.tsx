import React, { useState } from 'react';
import { Button, Tooltip } from 'antd';
import { NodeIndexOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { useT } from '@/i18n/useT';
import RunInspectorDrawer from './RunInspectorDrawer';
import { resolveRunTarget } from './model';

interface InspectRunActionProps {
  metadata: Record<string, unknown> | undefined;
}

/** Message toolbar entry that opens the read-only Run Inspector for the run behind an assistant reply. */
const InspectRunAction: React.FC<InspectRunActionProps> = ({ metadata }) => {
  const t = useT();
  const sessionId = useChatStore((state) => state.currentSession?.session_id ?? state.currentSession?.id ?? null);
  const [open, setOpen] = useState(false);
  const target = resolveRunTarget(metadata);
  if (!target || !sessionId) return null;
  return (
    <>
      <Tooltip title={t('chat.inspectRun')}>
        <Button
          type="text"
          size="small"
          aria-label={t('chat.inspectRun')}
          icon={<NodeIndexOutlined />}
          onClick={() => setOpen(true)}
          style={{ fontSize: 12, padding: '0 4px' }}
        />
      </Tooltip>
      <RunInspectorDrawer open={open} sessionId={sessionId} target={target} onClose={() => setOpen(false)} />
    </>
  );
};

export default InspectRunAction;
