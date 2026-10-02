import React from 'react';

/** Source evidence stays on its own message, even after switching sessions. */
export default function RecallReferences({ context }: { context?: Record<string, any> | null }) {
  const notes = Array.isArray(context?.memories) ? context.memories : [];
  const history = Array.isArray(context?.history) ? context.history : [];
  if (!notes.length && !history.length) return context?.unavailable
    ? <div style={{ marginTop: 10, fontSize: 12, color: '#777' }}>记忆召回暂时不可用，本轮未引用历史资料。</div>
    : null;
  return (
    <details style={{ marginTop: 10, fontSize: 12, color: '#777' }}>
      <summary style={{ cursor: 'pointer' }}>参考记忆与历史（{notes.length + history.length} 条）</summary>
      {[...notes.map((item: any) => ({ ...item, label: `记忆 · ${item.scope === 'user' ? '用户' : item.scope === 'project' ? '项目' : '会话'} · ${item.id}` })),
        ...history.map((item: any) => ({ ...item, label: `历史 · ${item.session_title || item.session_id} · 消息 #${item.message_id} · ${item.role}${item.status ? ` · ${item.status}` : ''}` }))]
        .map((item, index) => (
          <div key={`${item.id ?? item.message_id}-${index}`} style={{ marginTop: 8 }}>
            <div>{item.label} · {item.created_at}</div>
            <div style={{ whiteSpace: 'pre-wrap', maxHeight: 180, overflow: 'auto', color: '#555' }}>{item.content}</div>
          </div>
        ))}
    </details>
  );
}
