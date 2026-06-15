import { useEffect, useMemo, useState } from 'react';
import { Select, Tooltip, Tag, message as antdMessage } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import { chatApi } from '@api/chat';
import type { AvailableModelsResponse, ModelEntry } from '@api/chat';
import { useChatStore } from '@store/chat';

interface ModelSelectorProps {
  size?: 'small' | 'middle' | 'large';
  style?: React.CSSProperties;
}

const STORAGE_KEY = 'phage_agent.selected_model';

interface StoredSelection {
  provider: string;
  model: string;
}

function readStoredSelection(): StoredSelection | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed.provider === 'string' && typeof parsed.model === 'string') {
      return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

function writeStoredSelection(selection: StoredSelection) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(selection));
  } catch {
    // localStorage may be unavailable; silently ignore
  }
}

const ModelSelector: React.FC<ModelSelectorProps> = ({ size = 'small', style }) => {
  const [data, setData] = useState<AvailableModelsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [updating, setUpdating] = useState(false);
  const [selected, setSelected] = useState<string | undefined>(undefined);

  const currentSession = useChatStore((s) => s.currentSession);
  const sessionId = currentSession?.id;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    chatApi
      .getAvailableModels()
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        const stored = readStoredSelection();
        const initial = stored?.model || resp.current_model;
        setSelected(initial);
      })
      .catch((err) => {
        console.error('[ModelSelector] failed to load models:', err);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const options = useMemo(() => {
    if (!data) return [];
    return data.models.map((m) => ({
      value: m.id,
      label: (
        <span style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          {m.id === 'mimo-v2.5-pro-ultraspeed' && (
            <ThunderboltOutlined style={{ color: '#faad14' }} />
          )}
          <span>{m.name}</span>
          {!m.available && (
            <Tag color="default" style={{ marginLeft: 4, fontSize: 10 }}>
              未配置
            </Tag>
          )}
        </span>
      ),
      disabled: !m.available,
      raw: m,
    }));
  }, [data]);

  const handleChange = async (modelId: string) => {
    const entry = data?.models.find((m) => m.id === modelId);
    if (!entry) return;

    const previous = selected;
    setSelected(modelId);
    writeStoredSelection({ provider: entry.provider, model: entry.id });

    if (!sessionId) {
      antdMessage.info('已切换默认模型，下次新建会话时生效');
      return;
    }

    setUpdating(true);
    try {
      await chatApi.updateSession(sessionId, {
        settings: {
          default_base_model: entry.id,
          default_llm_provider: entry.provider,
        } as any,
      });
      antdMessage.success(`已切换到 ${entry.name}`);
    } catch (err: any) {
      console.error('[ModelSelector] update session failed:', err);
      antdMessage.error('模型切换失败：' + (err?.message || '未知错误'));
      setSelected(previous);
    } finally {
      setUpdating(false);
    }
  };

  const selectedEntry: ModelEntry | undefined = useMemo(
    () => data?.models.find((m) => m.id === selected),
    [data, selected],
  );

  return (
    <Tooltip
      title={selectedEntry?.description || '选择会话使用的语言模型'}
      placement="top"
    >
      <Select
        size={size}
        loading={loading || updating}
        value={selected}
        onChange={handleChange}
        options={options}
        style={{ minWidth: 220, ...style }}
        placeholder="选择模型"
        showSearch
        optionFilterProp="value"
      />
    </Tooltip>
  );
};

export default ModelSelector;
