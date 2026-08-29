import React from 'react';
import { Button, Tooltip } from 'antd';
import { DownloadOutlined, FileTextOutlined } from '@ant-design/icons';
import { collectArtifactFiles, resolveArtifactFileItemSrc } from '@/utils/artifactGallery';

interface ArtifactFileListProps {
  items: unknown;
  sessionId?: string | null;
}

/**
 * Download entries for non-image files (manuscripts, tables, archives, …)
 * produced by the tool calls behind this message. Renders nothing when the
 * message produced no files.
 */
export const ArtifactFileList: React.FC<ArtifactFileListProps> = ({ items, sessionId }) => {
  const files = collectArtifactFiles(items);
  if (files.length === 0) {
    return null;
  }
  const resolved = files
    .map((item) => ({ item, url: resolveArtifactFileItemSrc(item, sessionId) }))
    .filter((entry) => entry.url);
  if (resolved.length === 0) {
    return null;
  }
  return (
    <div
      style={{
        marginTop: 10,
        display: 'flex',
        flexWrap: 'wrap',
        gap: 8,
        alignItems: 'center',
      }}
    >
      {resolved.map(({ item, url }) => (
        <Tooltip key={`${item.origin ?? 'artifact'}::${item.path}`} title={`下载 ${item.display_name || item.path}`}>
          <a
            href={url}
            download
            target="_blank"
            rel="noreferrer"
            style={{ textDecoration: 'none' }}
          >
            <Button size="small" icon={<FileTextOutlined />}>
              {item.display_name || item.path.split('/').pop()}
              <DownloadOutlined style={{ marginLeft: 6, color: '#1677ff' }} />
            </Button>
          </a>
        </Tooltip>
      ))}
    </div>
  );
};

export default ArtifactFileList;
