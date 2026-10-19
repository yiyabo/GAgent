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
import { artifactImageFormatUrl, parseSessionArtifactImageUrl } from '@utils/artifactImageUrl';
import { useT } from '@/i18n/useT';

const RETRY_DELAY_MS = 600;
const MAX_RETRY_INDEX = 5;

export function isSessionArtifactFileUrl(url: string): boolean {
  return Boolean(parseSessionArtifactImageUrl(url));
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
  const t = useT();
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

  const artifact = useMemo(() => parseSessionArtifactImageUrl(url), [url]);
  const filePath = artifact?.path || '';
  const sessionId = artifact?.sessionId || null;

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
    const ref = `![${alt || t('artifactImage.defaultAlt')}](${targetPath})`;
    navigator.clipboard.writeText(ref);
    antMessage.success(t('artifactImage.copied', { ref }));
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
    const downloadUrl = artifactImageFormatUrl(url, ext);
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
        key: 'preview-pdf',
        label: t('artifactImage.previewPdf'),
        onClick: () => {
          if (!sessionId || !filePath) return;
          const base = filePath.replace(/\.[^/.]+$/, '');
          const pdfPath = `${base}.pdf`;
          const pdfUrl = artifactImageFormatUrl(url, 'pdf');
          window.dispatchEvent(
            new CustomEvent('phage:open-pdf-viewer', {
              detail: {
                url: pdfUrl,
                title: pdfPath.split('/').pop() || 'figure.pdf',
                downloadUrl: pdfUrl,
              },
            })
          );
        },
      },
      {
        key: 'svg',
        label: t('artifactImage.downloadSvg'),
        onClick: () => handleDownloadFormat('svg'),
      },
      {
        key: 'pdf',
        label: t('artifactImage.downloadPdf'),
        onClick: () => handleDownloadFormat('pdf'),
      },
      {
        key: 'png',
        label: t('artifactImage.downloadPng'),
        onClick: () => handleDownloadFormat('png'),
      },
    ],
  };

  if (!url.trim()) {
    return null;
  }

  if (broken) {
    const fileName = url.split('/').pop() ?? url;
    return (
      <span
        style={{ fontSize: 12, color: 'var(--text-secondary)', display: 'block', margin: '8px 0' }}
        title={url}
      >
        {t('artifactImage.loadFailed', { name: alt || fileName })}
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
            background: 'color-mix(in srgb, var(--surface-card) 92%, transparent)',
            backdropFilter: 'blur(4px)',
            borderRadius: 6,
            padding: '3px 6px',
            boxShadow: 'var(--shadow-2)',
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
            style={{ color: 'var(--color-action)', fontWeight: 500, fontSize: 12 }}
          >
            {t('artifactImage.fineTune')}
          </Button>

          <Dropdown menu={downloadMenu} placement='bottomRight'>
            <Button size='small' type='text' icon={<DownloadOutlined />} title={t('artifactImage.downloadTitle')} />
          </Dropdown>

          <Button
            size='small'
            type='text'
            icon={<EyeOutlined />}
            onClick={() => setPreviewVisible(true)}
            title={t('artifactImage.zoomTitle')}
          />

          <Button
            size='small'
            type='text'
            icon={<CopyOutlined />}
            onClick={handleCopyRef}
            title={t('artifactImage.copyRefTitle')}
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
        />
      )}
    </div>
  );
};
