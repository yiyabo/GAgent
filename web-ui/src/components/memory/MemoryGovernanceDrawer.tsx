import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Alert, Badge, Button, Drawer, Empty, Input, Popconfirm, Select, Spin, Tag, Tooltip, message } from 'antd';
import { DatabaseOutlined, DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { memoryApi } from '@api/memory';
import type { Memory, MemoryEntry, UpdateMemoryRequest } from '@/types';
import { useT, type TFn } from '@/i18n/useT';
import {
  MEMORY_IMPORTANCES,
  MEMORY_TYPES,
  memoryImportanceLabel,
  memoryImportanceOptions,
  memoryTypeLabel,
  memoryTypeOptions,
} from '@/i18n/memoryLabels';
import './MemoryGovernanceDrawer.css';

const { TextArea } = Input;

export const MEMORY_PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

const TYPE_COLORS: Record<Memory['memory_type'], string> = {
  conversation: 'blue',
  experience: 'green',
  knowledge: 'purple',
  context: 'orange',
};

const IMPORTANCE_COLORS: Record<Memory['importance'], string> = {
  critical: 'red',
  high: 'orange',
  medium: 'blue',
  low: 'default',
  temporary: 'default',
};

/** A value the backend sent that is not one of the known enums is shown as-is rather than as a raw i18n key. */
const typeLabel = (t: TFn, type: string): string =>
  MEMORY_TYPES.includes(type as Memory['memory_type']) ? memoryTypeLabel(t, type as Memory['memory_type']) : type;

const importanceLabel = (t: TFn, importance: string): string =>
  MEMORY_IMPORTANCES.includes(importance as Memory['importance'])
    ? memoryImportanceLabel(t, importance as Memory['importance'])
    : importance;

const errorText = (t: TFn, error: unknown): string =>
  error instanceof Error && error.message ? error.message : t('common.unknownError');

interface EditDraft {
  content: string;
  memory_type: Memory['memory_type'];
  importance: Memory['importance'];
  tags: string[];
}

/** Only the fields that actually changed, so an edit never rewrites what the user did not touch. */
function changedFields(entry: MemoryEntry, draft: EditDraft): UpdateMemoryRequest {
  const updates: UpdateMemoryRequest = {};
  const content = draft.content.trim();
  if (content !== entry.content) updates.content = content;
  if (draft.memory_type !== entry.memory_type) updates.memory_type = draft.memory_type;
  if (draft.importance !== entry.importance) updates.importance = draft.importance;
  const tags = draft.tags.map((tag) => tag.trim()).filter(Boolean);
  if (tags.length !== entry.tags.length || tags.some((tag, index) => tag !== entry.tags[index])) updates.tags = tags;
  return updates;
}

export interface MemoryGovernanceDrawerProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Lets the user see, search, add, correct and delete what the assistant remembers. The list comes
 * from `GET /mcp/memory/entries`; edits and deletions go straight to the backend and the list is
 * updated from its answer.
 */
const MemoryGovernanceDrawer: React.FC<MemoryGovernanceDrawerProps> = ({ open, onClose }) => {
  const t = useT();

  const [items, setItems] = useState<MemoryEntry[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState<Memory['memory_type'] | undefined>();
  const [importanceFilter, setImportanceFilter] = useState<Memory['importance'] | undefined>();
  const [reloadKey, setReloadKey] = useState(0);

  const [composing, setComposing] = useState(false);
  const [draftContent, setDraftContent] = useState('');
  const [draftType, setDraftType] = useState<Memory['memory_type']>('knowledge');
  const [draftImportance, setDraftImportance] = useState<Memory['importance']>('medium');
  const [adding, setAdding] = useState(false);

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<EditDraft | null>(null);
  const [savingEdit, setSavingEdit] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Responses that arrive after a newer request started are dropped, so a slow search never overwrites a fast one.
  const requestSeq = useRef(0);

  const load = useCallback(
    async (offset: number) => {
      const seq = ++requestSeq.current;
      if (offset === 0) setLoading(true);
      else setLoadingMore(true);
      setError(null);
      try {
        const page = await memoryApi.listMemories({
          search,
          memory_type: typeFilter,
          importance: importanceFilter,
          limit: MEMORY_PAGE_SIZE,
          offset,
        });
        if (seq !== requestSeq.current) return;
        setItems((previous) => (offset === 0 ? page.items : [...previous, ...page.items]));
        setTotal(page.total);
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setError(errorText(t, err));
      } finally {
        if (seq === requestSeq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [search, typeFilter, importanceFilter, t],
  );

  useEffect(() => {
    if (open) void load(0);
  }, [open, load, reloadKey]);

  useEffect(() => {
    if (searchInput.trim() === search) return undefined;
    const timer = window.setTimeout(() => setSearch(searchInput.trim()), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [searchInput, search]);

  const resetCompose = () => {
    setComposing(false);
    setDraftContent('');
    setDraftType('knowledge');
    setDraftImportance('medium');
  };

  const handleAdd = async () => {
    const content = draftContent.trim();
    if (!content) {
      message.warning(t('memory.form.contentRequired'));
      return;
    }
    setAdding(true);
    try {
      await memoryApi.saveMemory({ content, memory_type: draftType, importance: draftImportance, tags: [], keywords: [] });
      message.success(t('memory.governance.added'));
      resetCompose();
      setReloadKey((key) => key + 1);
    } catch (err) {
      message.error(t('memory.governance.addFailed', { message: errorText(t, err) }));
    } finally {
      setAdding(false);
    }
  };

  const startEdit = (entry: MemoryEntry) => {
    setEditingId(entry.id);
    setEditDraft({ content: entry.content, memory_type: entry.memory_type, importance: entry.importance, tags: [...entry.tags] });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditDraft(null);
  };

  const handleSaveEdit = async (entry: MemoryEntry) => {
    if (!editDraft) return;
    if (!editDraft.content.trim()) {
      message.warning(t('memory.form.contentRequired'));
      return;
    }
    const updates = changedFields(entry, editDraft);
    if (Object.keys(updates).length === 0) {
      cancelEdit();
      return;
    }
    setSavingEdit(true);
    try {
      const result = await memoryApi.updateMemory(entry.id, updates);
      const updated: MemoryEntry = result.item.id ? result.item : { ...entry, ...updates };
      setItems((previous) => previous.map((item) => (item.id === entry.id ? updated : item)));
      message.success(t('memory.governance.updated'));
      cancelEdit();
    } catch (err) {
      message.error(t('memory.governance.updateFailed', { message: errorText(t, err) }));
    } finally {
      setSavingEdit(false);
    }
  };

  const handleDelete = async (entry: MemoryEntry) => {
    setDeletingId(entry.id);
    try {
      await memoryApi.deleteMemory(entry.id);
      setItems((previous) => previous.filter((item) => item.id !== entry.id));
      setTotal((count) => Math.max(0, count - 1));
      if (editingId === entry.id) cancelEdit();
      message.success(t('memory.governance.deleted'));
    } catch (err) {
      message.error(t('memory.governance.deleteFailed', { message: errorText(t, err) }));
    } finally {
      setDeletingId(null);
    }
  };

  const filtered = Boolean(search || typeFilter || importanceFilter);

  const renderEdit = (entry: MemoryEntry, draft: EditDraft) => (
    <div className="memory-gov-edit">
      <TextArea
        aria-label={t('memory.governance.editContentLabel')}
        value={draft.content}
        onChange={(event) => setEditDraft({ ...draft, content: event.target.value })}
        autoSize={{ minRows: 3, maxRows: 10 }}
        maxLength={5000}
      />
      <div className="memory-gov-filters">
        <Select
          aria-label={t('memory.form.type')}
          value={draft.memory_type}
          options={memoryTypeOptions(t)}
          onChange={(value) => setEditDraft({ ...draft, memory_type: value })}
        />
        <Select
          aria-label={t('memory.form.importance')}
          value={draft.importance}
          options={memoryImportanceOptions(t)}
          onChange={(value) => setEditDraft({ ...draft, importance: value })}
        />
      </div>
      <Select
        mode="tags"
        aria-label={t('memory.form.tags')}
        placeholder={t('memory.form.tagsPlaceholder')}
        value={draft.tags}
        onChange={(value: string[]) => setEditDraft({ ...draft, tags: value })}
        tokenSeparators={[',']}
      />
      <div className="memory-gov-edit-actions">
        <Button size="small" onClick={cancelEdit} disabled={savingEdit}>
          {t('common.cancel')}
        </Button>
        <Button size="small" type="primary" loading={savingEdit} onClick={() => void handleSaveEdit(entry)}>
          {t('common.save')}
        </Button>
      </div>
    </div>
  );

  const renderCard = (entry: MemoryEntry) => {
    const editing = editingId === entry.id && editDraft !== null;
    return (
      <div key={entry.id} className="memory-gov-card" data-testid="memory-gov-card">
        {editing && editDraft ? (
          renderEdit(entry, editDraft)
        ) : (
          <>
            <div className="memory-gov-card-content">{entry.content}</div>
            <div className="memory-gov-card-meta">
              <Tag color={TYPE_COLORS[entry.memory_type] ?? 'default'}>{typeLabel(t, entry.memory_type)}</Tag>
              <Tag color={IMPORTANCE_COLORS[entry.importance] ?? 'default'}>{importanceLabel(t, entry.importance)}</Tag>
              {entry.tags.map((tag) => (
                <Tag key={tag}>{tag}</Tag>
              ))}
              {!entry.embedding_generated && <Tag color="warning">{t('memory.governance.notIndexed')}</Tag>}
            </div>
            <div className="memory-gov-card-footer">
              <span className="memory-gov-recalled">
                {entry.retrieval_count === 1
                  ? t('memory.governance.recalledOnce')
                  : t('memory.governance.recalled', { count: entry.retrieval_count })}
                {entry.created_at && dayjs(entry.created_at).isValid() && (
                  <span className="memory-gov-created">
                    {t('memory.governance.created', { time: dayjs(entry.created_at).format('YYYY-MM-DD HH:mm') })}
                  </span>
                )}
              </span>
              <div className="memory-gov-actions">
                <Tooltip title={t('memory.governance.edit')}>
                  <Button
                    type="text"
                    size="small"
                    icon={<EditOutlined />}
                    aria-label={t('memory.governance.edit')}
                    onClick={() => startEdit(entry)}
                  />
                </Tooltip>
                <Popconfirm
                  title={t('memory.governance.deleteConfirm')}
                  okText={t('memory.governance.delete')}
                  cancelText={t('common.cancel')}
                  okButtonProps={{ danger: true }}
                  onConfirm={() => handleDelete(entry)}
                >
                  <Tooltip title={t('memory.governance.delete')}>
                    <Button
                      type="text"
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      aria-label={t('memory.governance.delete')}
                      loading={deletingId === entry.id}
                    />
                  </Tooltip>
                </Popconfirm>
              </div>
            </div>
          </>
        )}
      </div>
    );
  };

  const renderBody = () => {
    if (loading && items.length === 0) {
      return (
        <div className="memory-gov-state" data-testid="memory-gov-loading">
          <Spin tip={t('common.loading')}>
            <div />
          </Spin>
        </div>
      );
    }
    if (error && items.length === 0) {
      return (
        <Alert
          type="error"
          showIcon
          message={t('memory.governance.loadFailed', { message: error })}
          action={
            <Button size="small" onClick={() => setReloadKey((key) => key + 1)}>
              {t('common.retry')}
            </Button>
          }
        />
      );
    }
    if (items.length === 0) {
      return (
        <div className="memory-gov-state">
          <Empty description={filtered ? t('memory.governance.emptyFiltered') : t('memory.governance.empty')} />
        </div>
      );
    }
    return (
      <Spin spinning={loading}>
        {error && (
          <Alert type="error" showIcon message={t('memory.governance.loadFailed', { message: error })} style={{ marginBottom: 8 }} />
        )}
        <div className="memory-gov-list">{items.map(renderCard)}</div>
        {items.length < total && (
          <div className="memory-gov-load-more">
            <Button loading={loadingMore} onClick={() => void load(items.length)}>
              {t('memory.governance.loadMore')}
            </Button>
          </div>
        )}
      </Spin>
    );
  };

  return (
    <Drawer
      title={
        <span className="memory-gov-title">
          <DatabaseOutlined style={{ color: 'var(--color-action)' }} />
          <span>{t('memory.governance.title')}</span>
          <Badge
            className="memory-gov-total"
            count={total}
            showZero
            overflowCount={99999}
            title={t('memory.governance.totalAria', { count: total })}
          />
        </span>
      }
      placement="right"
      width={520}
      open={open}
      onClose={onClose}
    >
      <div className="memory-gov-toolbar">
        <Input.Search
          allowClear
          aria-label={t('memory.governance.searchPlaceholder')}
          placeholder={t('memory.governance.searchPlaceholder')}
          value={searchInput}
          onChange={(event) => setSearchInput(event.target.value)}
          onSearch={(value) => {
            setSearchInput(value);
            setSearch(value.trim());
          }}
        />
        <div className="memory-gov-filters">
          <Select
            allowClear
            aria-label={t('memory.governance.typeFilter')}
            placeholder={t('memory.governance.typeFilter')}
            value={typeFilter}
            options={memoryTypeOptions(t)}
            onChange={(value) => setTypeFilter(value)}
          />
          <Select
            allowClear
            aria-label={t('memory.governance.importanceFilter')}
            placeholder={t('memory.governance.importanceFilter')}
            value={importanceFilter}
            options={memoryImportanceOptions(t)}
            onChange={(value) => setImportanceFilter(value)}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setComposing(true)} disabled={composing}>
            {t('memory.governance.add')}
          </Button>
        </div>
      </div>

      {composing && (
        <div className="memory-gov-compose">
          <TextArea
            aria-label={t('memory.form.content')}
            placeholder={t('memory.governance.addPlaceholder')}
            value={draftContent}
            onChange={(event) => setDraftContent(event.target.value)}
            autoSize={{ minRows: 3, maxRows: 8 }}
            maxLength={5000}
            autoFocus
          />
          <div className="memory-gov-filters">
            <Select
              aria-label={t('memory.form.type')}
              value={draftType}
              options={memoryTypeOptions(t)}
              onChange={(value) => setDraftType(value)}
            />
            <Select
              aria-label={t('memory.form.importance')}
              value={draftImportance}
              options={memoryImportanceOptions(t)}
              onChange={(value) => setDraftImportance(value)}
            />
          </div>
          <div className="memory-gov-edit-actions">
            <Button size="small" onClick={resetCompose} disabled={adding}>
              {t('common.cancel')}
            </Button>
            <Button size="small" type="primary" loading={adding} onClick={() => void handleAdd()}>
              {t('common.save')}
            </Button>
          </div>
        </div>
      )}

      {renderBody()}
    </Drawer>
  );
};

export default MemoryGovernanceDrawer;
