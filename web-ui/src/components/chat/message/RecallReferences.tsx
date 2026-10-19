import React from 'react';
import { useT } from '@/i18n/useT';

/** Source evidence stays on its own message, even after switching sessions. */
export default function RecallReferences({ context }: { context?: Record<string, any> | null }) {
  const t = useT();
  const notes = Array.isArray(context?.memories) ? context.memories : [];
  const history = Array.isArray(context?.history) ? context.history : [];
  if (!notes.length && !history.length) return context?.unavailable
    ? <div style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>{t('recall.unavailable')}</div>
    : null;
  return (
    <details style={{ marginTop: 10, fontSize: 12, color: 'var(--text-secondary)' }}>
      <summary style={{ cursor: 'pointer' }}>{t('recall.summary', { count: notes.length + history.length })}</summary>
      {[...notes.map((item: any) => ({ ...item, label: t('recall.memory', { scope: item.scope === 'user' ? t('recall.scopeUser') : item.scope === 'project' ? t('recall.scopeProject') : t('recall.scopeSession'), id: item.id }) })),
        ...history.map((item: any) => ({ ...item, label: t('recall.history', { title: item.session_title || item.session_id, messageId: item.message_id, rest: `${item.role}${item.status ? ` · ${item.status}` : ''}` }) }))]
        .map((item, index) => (
          <div key={`${item.id ?? item.message_id}-${index}`} style={{ marginTop: 8 }}>
            <div>{item.label} · {item.created_at}</div>
            <div style={{ whiteSpace: 'pre-wrap', maxHeight: 180, overflow: 'auto', color: 'var(--text-secondary)' }}>{item.content}</div>
          </div>
        ))}
    </details>
  );
}
