import React, { useMemo } from 'react';
import { Typography, Tag, Space } from 'antd';
import type { ArtifactGalleryItem } from '@/types';
import { resolveArtifactGalleryItemSrc } from '@/utils/artifactGallery';
import { SessionArtifactImage } from './SessionArtifactImage';

const { Text } = Typography;

interface ArtifactGalleryProps {
  items: ArtifactGalleryItem[];
  sessionId?: string | null;
}

interface ConsolidatedFigure {
  baseName: string;
  displayTitle: string;
  previewUrl: string;
  rawPath: string;
  hasPng: boolean;
  hasSvg: boolean;
  hasPdf: boolean;
}

export const ArtifactGallery: React.FC<ArtifactGalleryProps> = ({ items, sessionId }) => {
  if (!Array.isArray(items) || items.length === 0) {
    return null;
  }

  const visibleItems = useMemo(() => {
    return items
      .map((item) => ({
        item,
        url: resolveArtifactGalleryItemSrc(item, sessionId),
      }))
      .filter((entry) => entry.url);
  }, [items, sessionId]);

  // Consolidate figures by base name (grouping svg, png, pdf of the same figure into one card)
  const consolidatedFigures = useMemo<ConsolidatedFigure[]>(() => {
    const map = new Map<string, ConsolidatedFigure>();

    visibleItems.forEach(({ item, url }) => {
      const p = item.path || '';
      const fileName = p.split('/').pop() || p;
      const baseName = fileName.replace(/\.(png|svg|pdf|jpe?g|webp|gif)$/i, '');
      if (!baseName) return;

      let entry = map.get(baseName);
      if (!entry) {
        entry = {
          baseName,
          displayTitle: baseName.replace(/_/g, ' '),
          previewUrl: url,
          rawPath: p,
          hasPng: false,
          hasSvg: false,
          hasPdf: true, // PDF sibling is standard in our pipeline
        };
        map.set(baseName, entry);
      }

      const ext = fileName.split('.').pop()?.toLowerCase();
      if (ext === 'png' || ext === 'jpg' || ext === 'jpeg') {
        entry.hasPng = true;
        // Prefer PNG for preview thumbnail stability
        entry.previewUrl = url;
        entry.rawPath = p;
      } else if (ext === 'svg') {
        entry.hasSvg = true;
      } else if (ext === 'pdf') {
        entry.hasPdf = true;
      }
    });

    return Array.from(map.values());
  }, [visibleItems]);

  if (consolidatedFigures.length === 0) {
    return null;
  }

  return (
    <div style={{ marginTop: 12 }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
          gap: 12,
        }}
      >
        {consolidatedFigures.map((fig) => {
          return (
            <div
              key={fig.baseName}
              style={{
                border: '1px solid var(--border-color, #e8e8e8)',
                borderRadius: 8,
                background: '#ffffff',
                boxShadow: '0 2px 6px rgba(0, 0, 0, 0.04)',
                padding: 10,
                display: 'flex',
                flexDirection: 'column',
                gap: 8,
              }}
            >
              <SessionArtifactImage
                url={fig.previewUrl}
                alt={fig.displayTitle}
                imageStyle={{
                  width: '100%',
                  maxHeight: 400,
                  display: 'block',
                  borderRadius: 6,
                  objectFit: 'contain',
                  background: '#fafafa',
                }}
              />
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderTop: '1px solid #f0f0f0', paddingTop: 6 }}>
                <div>
                  <Text strong style={{ fontSize: 13, display: 'block' }}>
                    {fig.baseName}
                  </Text>
                  <Text type='secondary' style={{ fontSize: 11 }}>
                    {fig.displayTitle}
                  </Text>
                </div>
                <Space size={4}>
                  <Tag color='blue' style={{ fontSize: 10, margin: 0, padding: '0 4px' }}>PNG 300DPI</Tag>
                  <Tag color='green' style={{ fontSize: 10, margin: 0, padding: '0 4px' }}>SVG 矢量</Tag>
                  <Tag color='purple' style={{ fontSize: 10, margin: 0, padding: '0 4px' }}>PDF 出版级</Tag>
                </Space>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ArtifactGallery;
