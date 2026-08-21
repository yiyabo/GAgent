import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Space, Button, Dropdown, Image, message as antMessage } from 'antd';
import type { MenuProps } from 'antd';
import {
  EditOutlined,
  DownloadOutlined,
  CopyOutlined,
  EyeOutlined,
} from '@ant-design/icons';
import { FigureFineTuneModal } from './FigureFineTuneModal';

const RETRY_DELAY_MS = 600;
const MAX_RETRY_INDEX = 5;

export function isSessionArtifactFileUrl(url: string): boolean {
  if (!url || typeof url !== 'string') return false;
  if (!/^https?:\/\//i.test(url.trim())) return false;
  try {
    const path = new URL(url.trim()).pathname;
    return path.includes('/artifacts/sessions/') && path.endsWith('/file');
  } catch {
    return false;
  }
}

export interface SessionArtifactImageProps {
  url: string;
  alt?: string;
  imageStyle?: React.CSSProperties;
  loading?: 'lazy' | 'eager';
  decoding?: 'async' | 'auto' | 'sync';
  showActions?: boolean;
}

export const SessionArtifactImage: React.FC<SessionArtifactImageProps> = ({
  url,
  alt = '',
  imageStyle,
  loading = 'lazy',
  decoding = 'async',
  showActions = true,
}) => {
  const [attempt, setAttempt] = useState(0);
  const [broken, setBroken] = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const [fineTuneOpen, setFineTuneOpen] = useState(false);
  const [previewVisible, setPreviewVisible] = useState(false);
  const timerRef = useRef<number | null>(null);

  useEffect(() => {
    setAttempt(0);
    setBroken(false);
  }, [url]);

  useEffect(
    () => () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
        timerRef.current = null;
      }
    },
    [],
  );

  const src = useMemo(() => {
    const u = url.trim();
    if (!isSessionArtifactFileUrl(u) || attempt === 0) {
      return u;
    }
    const sep = u.includes('?') ? '&' : '?';
    return `${u}${sep}_retry=${attempt}`;
  }, [url, attempt]);

  const filePath = useMemo(() => {
    try {
      if (!isSessionArtifactFileUrl(url)) return '';
      const parsed = new URL(url.trim());
      return parsed.searchParams.get('path') || '';
    } catch {
      return '';
    }
  }, [url]);

  const sessionId = useMemo(() => {
    try {
      if (!isSessionArtifactFileUrl(url)) return null;
      const parsed = new URL(url.trim());
      const parts = parsed.pathname.split('/');
      const idx = parts.indexOf('sessions');
      return idx >= 0 && parts[idx + 1] ? parts[idx + 1] : null;
    } catch {
      return null;
    }
  }, [url]);

  const handleError = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (!isSessionArtifactFileUrl(url)) {
      setBroken(true);
      return;
    }
    if (attempt >= MAX_RETRY_INDEX) {
      setBroken(true);
      return;
    }
    timerRef.current = window.setTimeout(() => {
      setAttempt((a) => a + 1);
      timerRef.current = null;
    }, RETRY_DELAY_MS);
  }, [url, attempt]);

  const handleCopyRef = () => {
    const targetPath = filePath || alt || 'figure.png';
    const ref = `![${alt || '图表'}](${targetPath})`;
    navigator.clipboard.writeText(ref);
    antMessage.success(`已复制 Markdown 引用: ${ref}`);
  };

  const handleDownloadFormat = (ext: 'png' | 'svg' | 'pdf') => {
    if (!sessionId || !filePath) {
      const a = document.createElement('a');
      a.href = url;
      a.download = alt || `figure.${ext}`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      return;
    }

    const base = filePath.replace(/\.[^/.]+$/, '');
    const downloadPath = `${base}.${ext}`;
    const downloadUrl = `/api/artifacts/sessions/${sessionId}/file?path=${encodeURIComponent(downloadPath)}`;
    const a = document.createElement('a');
    a.href = downloadUrl;
    a.download = downloadPath.split('/').pop() || `figure.${ext}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const downloadMenu: MenuProps = {
    items: [
      {
        key: 'svg',
        label: 'SVG 矢量图 (可编辑文字/图形)',
        onClick: () => handleDownloadFormat('svg'),
      },
      {
        key: 'pdf',
        label: 'PDF 矢量图 (高清出版级)',
        onClick: () => handleDownloadFormat('pdf'),
      },
      {
        key: 'png',
        label: 'PNG 标清预览图 (300 DPI)',
        onClick: () => handleDownloadFormat('png'),
      },
    ],
  };

  if (!url.trim()) {
    return null;
  }

  if (broken) {
    return (
      <span
        style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', margin: '8px 0' }}
        title={url}
      >
        [Image failed to load] {alt || url}
      </span>
    );
  }

  return (
    <div
      style={{
        position: 'relative',
        display: 'inline-block',
        maxWidth: '100%',
        margin: '8px 0',
      }}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <img
        key={`${url}_${attempt}`}
        src={src}
        alt={alt}
        loading={loading}
        decoding={decoding}
        onError={handleError}
        style={imageStyle}
      />

      {showActions && (
        <div
          style={{
            position: 'absolute',
            top: 8,
            right: 8,
            opacity: isHovered ? 1 : 0,
            transition: 'opacity 0.2s ease-in-out',
            background: 'rgba(255, 255, 255, 0.92)',
            backdropFilter: 'blur(4px)',
            borderRadius: 6,
            padding: '3px 6px',
            boxShadow: '0 2px 8px rgba(0, 0, 0, 0.15)',
            display: 'flex',
            gap: 4,
            zIndex: 10,
          }}
        >
          <Button
            size='small'
            type='text'
            icon={<EditOutlined />}
            onClick={() => setFineTuneOpen(true)}
            style={{ color: '#1677ff', fontWeight: 500, fontSize: 12 }}
          >
            微调此图
          </Button>

          <Dropdown menu={downloadMenu} placement='bottomRight'>
            <Button size='small' type='text' icon={<DownloadOutlined />} title='下载矢量图 (SVG/PDF/PNG)' />
          </Dropdown>

          <Button
            size='small'
            type='text'
            icon={<EyeOutlined />}
            onClick={() => setPreviewVisible(true)}
            title='全屏放大查看'
          />

          <Button
            size='small'
            type='text'
            icon={<CopyOutlined />}
            onClick={handleCopyRef}
            title='复制 Markdown 引用'
          />
        </div>
      )}

      {/* Hidden preview image for antd Image preview trigger */}
      <div style={{ display: 'none' }}>
        <Image
          src={src}
          preview={{
            visible: previewVisible,
            onVisibleChange: (v) => setPreviewVisible(v),
          }}
        />
      </div>

      {fineTuneOpen && (
        <FigureFineTuneModal
          visible={fineTuneOpen}
          onClose={() => setFineTuneOpen(false)}
          imageUrl={src}
          imageAlt={alt}
          imagePath={filePath}
          sessionId={sessionId}
          onSubmitPrompt={(prompt) => {
            window.dispatchEvent(
              new CustomEvent('phage:insert-chat-prompt', {
                detail: { prompt },
              })
            );
          }}
        />
      )}
    </div>
  );
};
