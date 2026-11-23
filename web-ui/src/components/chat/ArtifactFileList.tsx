import React from 'react';
import { Button, Tooltip, Typography } from 'antd';
import { DownloadOutlined, EyeOutlined, FileTextOutlined } from '@ant-design/icons';
import { useLayoutStore } from '@store/layout';
import { collectArtifactFiles, resolveArtifactFileItemSrc } from '@/utils/artifactGallery';
import { useT } from '@/i18n/useT';

interface ArtifactFileListProps {
  items: unknown;
  sessionId?: string | null;
}

/**
 * File cards for files produced by this message. Published deliverables open
 * focused in the 产物 (Artifacts) panel; every card can also be downloaded.
 */
export const ArtifactFileList: React.FC<ArtifactFileListProps> = ({ items, sessionId }) => {
  const t = useT();
  const focusDeliverable = useLayoutStore((state) => state.focusDeliverable);
  const files = collectArtifactFiles(items);
  if (files.length === 0) {
    return null;
  }
  const sid = typeof sessionId === 'string' ? sessionId.trim() : '';
  const resolved = files
    .map((item) => ({ item, url: resolveArtifactFileItemSrc(item, sessionId) }))
    .filter((entry) => entry.url);
  if (resolved.length === 0) {
    return null;
  }
  return (
    <div style={{ marginTop: 10, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
      {resolved.map(({ item, url }) => {
        const name = item.display_name || item.path.split('/').pop() || item.path;
        const isDeliverable = Boolean(sid) && (item.origin ?? '').trim().toLowerCase() === 'deliverable';
        const open = () => {
          if (isDeliverable) {
            focusDeliverable(sid, item.path);
          } else {
            window.open(url, '_blank', 'noopener,noreferrer');
          }
        };
        return (
          <div
            key={`${item.origin ?? 'artifact'}::${item.path}`}
            data-testid="artifact-file-card"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              maxWidth: 360,
              padding: '6px 6px 6px 10px',
              border: '1px solid var(--border-color)',
              borderRadius: 8,
              background: 'var(--bg-primary)',
            }}
          >
            <FileTextOutlined style={{ color: 'var(--color-action)', fontSize: 18 }} />
            <Tooltip title={isDeliverable ? t('chat.openInArtifacts', { name }) : item.path}>
              <button
                type="button"
                onClick={open}
                style={{
                  minWidth: 0,
                  padding: 0,
                  border: 0,
                  background: 'none',
                  textAlign: 'left',
                  cursor: 'pointer',
                }}
              >
                <Typography.Text strong ellipsis style={{ display: 'block', maxWidth: 220 }}>
                  {name}
                </Typography.Text>
                {isDeliverable && (
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {t('chat.deliverableLocation')}
                  </Typography.Text>
                )}
              </button>
            </Tooltip>
            <Tooltip title={isDeliverable ? t('chat.openInArtifacts', { name }) : t('preview.openInNewTab')}>
              <Button size="small" type="text" icon={<EyeOutlined />} aria-label={t('chat.viewFile', { name })} onClick={open} />
            </Tooltip>
            <Tooltip title={t('chat.downloadFile', { name })}>
              <a href={url} download target="_blank" rel="noreferrer" aria-label={t('chat.downloadFile', { name })}>
                <Button size="small" type="text" icon={<DownloadOutlined style={{ color: 'var(--color-action)' }} />} />
              </a>
            </Tooltip>
          </div>
        );
      })}
    </div>
  );
};

export default ArtifactFileList;
