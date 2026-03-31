import React from 'react';
import { Typography } from 'antd';
import type { ArtifactGalleryItem } from '@/types';
import { resolveArtifactGalleryItemSrc } from '@/utils/artifactGallery';
import { SessionArtifactImage } from './SessionArtifactImage';

const { Text } = Typography;

interface ArtifactGalleryProps {
  items: ArtifactGalleryItem[];
  sessionId?: string | null;
}

export const ArtifactGallery: React.FC<ArtifactGalleryProps> = ({ items, sessionId }) => {
  if (!Array.isArray(items) || items.length === 0) {
    return null;
  }

  const visibleItems = items
    .map((item) => ({
      item,
      url: resolveArtifactGalleryItemSrc(item, sessionId),
    }))
    .filter((entry) => entry.url);

  if (visibleItems.length === 0) {
    return null;
  }

  return (
    <div style={{ marginTop: 10 }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
          gap: 10,
        }}
      >
        {visibleItems.map(({ item, url }) => {
          const label = item.display_name || item.path.split('/').pop() || item.path;
          return (
            <div
              key={`${item.origin ?? 'artifact'}_${item.path}`}
              style={{
                border: '1px solid var(--border-color)',
                borderRadius: 'var(--radius-md)',
                background: 'var(--bg-tertiary)',
                padding: 8,
              }}
            >
              <SessionArtifactImage
                url={url}
                alt={label}
                imageStyle={{
                  width: '100%',
                  display: 'block',
                  borderRadius: 'calc(var(--radius-md) - 4px)',
                  objectFit: 'cover',
                  background: 'rgba(0, 0, 0, 0.04)',
                }}
              />
              <Text
                type="secondary"
                style={{
                  display: 'block',
                  marginTop: 6,
                  fontSize: 12,
                  wordBreak: 'break-word',
                }}
              >
                {label}
              </Text>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ArtifactGallery;
