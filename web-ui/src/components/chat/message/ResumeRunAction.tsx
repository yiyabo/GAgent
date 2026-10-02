import React, { useRef, useState } from 'react';
import { Button, message } from 'antd';
import { PlayCircleOutlined } from '@ant-design/icons';
import { useChatStore } from '@store/chat';

export default function ResumeRunAction({ runId, sessionId, status }: {
  runId?: string | null; sessionId?: string | null; status?: string;
}) {
  const resume = useChatStore((state) => state.resumeChatRun);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  if (!runId || !sessionId || !['failed', 'cancelled'].includes(status ?? '')) return null;
  return (
    <Button size="small" icon={<PlayCircleOutlined />} loading={busy} style={{ marginTop: 12 }}
      onClick={async () => {
        if (pending.current) return;
        pending.current = true;
        setBusy(true);
        try { await resume(runId, sessionId); }
        catch (error) { message.info(error instanceof Error ? error.message : '暂时无法继续，请稍后再试。'); }
        finally { pending.current = false; setBusy(false); }
      }}>
      继续此任务
    </Button>
  );
}
