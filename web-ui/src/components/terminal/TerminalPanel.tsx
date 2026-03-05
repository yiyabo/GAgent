import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Empty, Typography } from 'antd';
import { FitAddon } from 'xterm-addon-fit';
import { WebLinksAddon } from 'xterm-addon-web-links';
import { Terminal } from 'xterm';
import 'xterm/css/xterm.css';

import { ENV } from '@/config/env';
import { buildTerminalWsUrl, terminalApi } from '@/api/terminal';
import type { TerminalApprovalPayload, TerminalMode, TerminalWSMessage } from '@/types';
import TerminalToolbar from './TerminalToolbar';
import CommandApprovalModal from './CommandApprovalModal';

const { Text } = Typography;

interface Props {
  sessionId: string | null;
}

const encoder = new TextEncoder();
const decoder = new TextDecoder();

const bytesToBase64 = (bytes: Uint8Array): string => {
  let binary = '';
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
};

const base64ToBytes = (data: string): Uint8Array => {
  if (!data) return new Uint8Array();
  const binary = atob(data);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
};

const TerminalPanel: React.FC<Props> = ({ sessionId }) => {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const terminalRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<number | null>(null);
  const manualCloseRef = useRef(false);
  // Ref mirrors terminalId state so ws callbacks always read the latest value
  // without needing to close over stale state.
  const terminalIdRef = useRef<string | null>(null);

  const [mode, setMode] = useState<TerminalMode>('sandbox');
  const [terminalId, setTerminalId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [errorText, setErrorText] = useState<string | null>(null);
  const [pendingApproval, setPendingApproval] = useState<TerminalApprovalPayload | null>(null);

  // Keep ref in sync with state so ws closures always read the current value.
  const setTerminalIdSynced = useCallback((id: string | null) => {
    terminalIdRef.current = id;
    setTerminalId(id);
  }, []);

  const sendMessage = useCallback((message: TerminalWSMessage) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(message));
  }, []);

  const sendResize = useCallback(() => {
    const term = terminalRef.current;
    if (!term) return;
    sendMessage({
      type: 'resize',
      payload: { cols: term.cols, rows: term.rows },
    });
  }, [sendMessage]);

  const disposeSocket = useCallback(() => {
    const ws = wsRef.current;
    if (!ws) return;
    ws.onopen = null;
    ws.onmessage = null;
    ws.onerror = null;
    ws.onclose = null;
    if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
      ws.close();
    }
    wsRef.current = null;
  }, []);

  const connectSocket = useCallback(
    (targetTerminalId?: string) => {
      if (!sessionId || !ENV.TERMINAL_ENABLED) return;
      disposeSocket();
      const wsUrl = buildTerminalWsUrl(sessionId, { mode, terminalId: targetTerminalId });
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        setErrorText(null);
        manualCloseRef.current = false;
        sendResize();
      };

      ws.onmessage = (event) => {
        try {
          const message = JSON.parse(String(event.data)) as TerminalWSMessage;
          if (message.type === 'output') {
            const bytes = base64ToBytes(String(message.payload || ''));
            terminalRef.current?.write(decoder.decode(bytes));
            return;
          }
          if (message.type === 'approval') {
            setPendingApproval(message.payload as TerminalApprovalPayload);
            return;
          }
          if (message.type === 'error') {
            const msg = String(message.payload?.message || 'Terminal error');
            // If the session no longer exists on the server, clear the stale
            // terminal_id so the next reconnect creates a fresh session instead
            // of retrying the same dead id indefinitely.
            if (msg.toLowerCase().includes('unknown terminal_id')) {
              setTerminalIdSynced(null);
            }
            setErrorText(msg);
            terminalRef.current?.writeln(`\r\n[error] ${msg}`);
            return;
          }
          if (message.type === 'closed') {
            // Server explicitly closed the session – clear id so reconnect
            // will create a new one rather than looking up the dead session.
            setTerminalIdSynced(null);
            terminalRef.current?.writeln('\r\n[terminal] Session closed');
            setConnected(false);
            return;
          }
          if (message.type === 'pong') {
            const incomingId = String(message.payload?.terminal_id || '').trim();
            if (incomingId) {
              setTerminalIdSynced(incomingId);
            }
            return;
          }
        } catch (error) {
          setErrorText(`Invalid websocket message: ${String(error)}`);
        }
      };

      ws.onerror = () => {
        setErrorText('Terminal websocket error');
      };

      ws.onclose = () => {
        setConnected(false);
        if (!manualCloseRef.current && sessionId && ENV.TERMINAL_ENABLED) {
          reconnectRef.current = window.setTimeout(() => {
            // Always read the ref (latest value) – if it was cleared because the
            // session was closed/unknown, reconnect without a terminal_id so the
            // backend creates a fresh session via ensure_session_for_chat().
            connectSocket(terminalIdRef.current ?? undefined);
          }, 1500);
        }
      };
    },
    [disposeSocket, mode, sendResize, sessionId, terminalId]
  );

  useEffect(() => {
    if (!containerRef.current || terminalRef.current) return;
    const term = new Terminal({
      cursorBlink: true,
      convertEol: true,
      scrollback: 10000,
      fontFamily: 'Menlo, Monaco, "Courier New", monospace',
      fontSize: 12,
      theme: {
        background: '#0f172a',
        foreground: '#e2e8f0',
        cursor: '#93c5fd',
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fitAddon.fit();
    term.writeln('Agent Terminal ready');

    const disposable = term.onData((data) => {
      const bytes = encoder.encode(data);
      sendMessage({ type: 'input', payload: bytesToBase64(bytes) });
    });

    const onWindowResize = () => {
      fitAddon.fit();
      sendResize();
    };

    window.addEventListener('resize', onWindowResize);

    terminalRef.current = term;
    fitAddonRef.current = fitAddon;

    return () => {
      disposable.dispose();
      window.removeEventListener('resize', onWindowResize);
      term.dispose();
      terminalRef.current = null;
      fitAddonRef.current = null;
    };
  }, [sendMessage, sendResize]);

  useEffect(() => {
    if (!ENV.TERMINAL_ENABLED || !sessionId || !terminalRef.current) return;
    connectSocket(terminalId || undefined);

    return () => {
      if (reconnectRef.current != null) {
        window.clearTimeout(reconnectRef.current);
        reconnectRef.current = null;
      }
      manualCloseRef.current = true;
      disposeSocket();
    };
  }, [connectSocket, disposeSocket, sessionId]);

  const handleCreate = useCallback(async () => {
    if (!sessionId) return;
    try {
      const session = await terminalApi.createSession({ session_id: sessionId, mode });
      setTerminalIdSynced(session.terminal_id);
      terminalRef.current?.writeln(`\r\n[new] Created terminal ${session.terminal_id}`);
      connectSocket(session.terminal_id);
    } catch (error) {
      setErrorText(String(error));
    }
  }, [connectSocket, mode, sessionId]);

  const handleClose = useCallback(async () => {
    if (!terminalId) return;
    try {
      await terminalApi.closeSession(terminalId);
      manualCloseRef.current = true;
      disposeSocket();
      setConnected(false);
      setTerminalIdSynced(null);
      terminalRef.current?.writeln('\r\n[terminal] Closed by user');
    } catch (error) {
      setErrorText(String(error));
    }
  }, [disposeSocket, terminalId]);

  const handleReplay = useCallback(async () => {
    if (!terminalId) return;
    try {
      const replay = await terminalApi.getReplay(terminalId, 1000);
      terminalRef.current?.writeln('\r\n--- replay start ---');
      for (const event of replay) {
        const delayMs = Math.max(0, Number(event.delay || 0) * 1000);
        if (delayMs > 0) {
          // eslint-disable-next-line no-await-in-loop
          await new Promise((resolve) => window.setTimeout(resolve, delayMs));
        }
        const bytes = base64ToBytes(event.data || '');
        const text = decoder.decode(bytes);
        if (event.type === 'i') {
          terminalRef.current?.write(`\x1b[33m${text}\x1b[0m`);
        } else {
          terminalRef.current?.write(text);
        }
      }
      terminalRef.current?.writeln('\r\n--- replay end ---');
    } catch (error) {
      setErrorText(String(error));
    }
  }, [terminalId]);

  const handleRefresh = useCallback(async () => {
    if (!sessionId) return;
    try {
      const sessions = await terminalApi.listSessions(sessionId);
      const line = sessions
        .map((item) => `${item.terminal_id.slice(0, 8)}:${item.state}:${item.mode}`)
        .join(', ');
      terminalRef.current?.writeln(`\r\n[sessions] ${line || 'none'}`);
    } catch (error) {
      setErrorText(String(error));
    }
  }, [sessionId]);

  const handleClear = useCallback(() => {
    terminalRef.current?.clear();
  }, []);

  const handleApproval = useCallback(
    (approvalId: string, approve: boolean) => {
      sendMessage({
        type: approve ? 'cmd_approve' : 'cmd_reject',
        payload: { approval_id: approvalId },
      });
      setPendingApproval(null);
    },
    [sendMessage]
  );

  if (!ENV.TERMINAL_ENABLED) {
    return (
      <div style={{ padding: 16 }}>
        <Alert type="info" message="Terminal feature is disabled" showIcon />
      </div>
    );
  }

  if (!sessionId) {
    return (
      <div style={{ padding: 16 }}>
        <Empty description="No active session" />
      </div>
    );
  }

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <TerminalToolbar
        mode={mode}
        connected={connected}
        terminalId={terminalId}
        onModeChange={(nextMode) => {
          setMode(nextMode);
        }}
        onCreate={handleCreate}
        onClose={handleClose}
        onReplay={handleReplay}
        onClear={handleClear}
        onRefresh={handleRefresh}
      />

      {errorText ? (
        <div style={{ padding: '4px 12px' }}>
          <Text type="danger">{errorText}</Text>
        </div>
      ) : null}

      <div
        ref={containerRef}
        style={{
          flex: 1,
          minHeight: 180,
          background: '#0f172a',
          overflow: 'hidden',
        }}
      />

      <CommandApprovalModal
        open={Boolean(pendingApproval)}
        approval={pendingApproval}
        onApprove={(id) => handleApproval(id, true)}
        onReject={(id) => handleApproval(id, false)}
      />
    </div>
  );
};

export default TerminalPanel;
