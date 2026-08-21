import React, { useState, useEffect, useMemo } from 'react';
import { Drawer, List, Card, Button, Space, Tag, Typography, Spin, Empty, message as antMessage } from 'antd';
import {
  FileImageOutlined,
  EditOutlined,
  DownloadOutlined,
  CopyOutlined,
  ReloadOutlined,
  FileZipOutlined,
} from '@ant-design/icons';
import axios from 'axios';
import { resolveArtifactImageSrc } from '@/utils/artifactImageUrl';
import { FigureFineTuneModal } from './FigureFineTuneModal';

const { Text, Title } = Typography;

export interface FigureItem {
  baseName: string;
  pngPath?: string;
  svgPath?: string;
  pdfPath?: string;
  displayTitle: string;
  updatedAt?: string;
}

interface FigureCatalogDrawerProps {
  visible: boolean;
  onClose: () => void;
  sessionId?: string | null;
  onInsertPrompt?: (prompt: string) => void;
}

export const FigureCatalogDrawer: React.FC<FigureCatalogDrawerProps> = ({
  visible,
  onClose,
  sessionId,
  onInsertPrompt,
}) => {
  const [loading, setLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<any[]>([]);
  const [activeFineTuneFigure, setActiveFineTuneFigure] = useState<FigureItem | null>(null);

  const fetchFigures = async () => {
    if (!sessionId) return;
    try {
      setLoading(true);
      const res = await axios.get(`/api/artifacts/sessions/${sessionId}?max_depth=4&limit=500`);
      if (res.data && Array.isArray(res.data.items)) {
        setArtifacts(res.data.items);
      }
    } catch (err) {
      console.error('Failed to load session figures:', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (visible && sessionId) {
      fetchFigures();
    }
  }, [visible, sessionId]);

  const figureList = useMemo<FigureItem[]>(() => {
    const map = new Map<string, FigureItem>();

    artifacts.forEach((item) => {
      const p = item.path || '';
      if (!p) return;
      const match = p.match(/(.*)\.(png|svg|pdf|jpg|jpeg)$/i);
      if (!match) return;

      const base = match[1];
      const ext = match[2].toLowerCase();

      let entry = map.get(base);
      if (!entry) {
        const simpleName = base.split('/').pop() || base;
        entry = {
          baseName: base,
          displayTitle: simpleName.replace(/_/g, ' '),
          updatedAt: item.modified_at || item.created_at,
        };
        map.set(base, entry);
      }

      if (ext === 'png' || ext === 'jpg' || ext === 'jpeg') {
        entry.pngPath = p;
      } else if (ext === 'svg') {
        entry.svgPath = p;
      } else if (ext === 'pdf') {
        entry.pdfPath = p;
      }
    });

    // Only return items that have at least one visual preview or vector file
    return Array.from(map.values()).filter((fig) => fig.pngPath || fig.svgPath || fig.pdfPath);
  }, [artifacts]);

  const handleCopyRef = (fig: FigureItem) => {
    const ref = `![${fig.displayTitle}](${fig.pngPath || fig.baseName + '.png'})`;
    navigator.clipboard.writeText(ref);
    antMessage.success(`已复制 Markdown 引用: ${ref}`);
  };

  const handleDownloadFile = (path?: string) => {
    if (!path || !sessionId) return;
    const url = `/api/artifacts/sessions/${sessionId}/file?path=${encodeURIComponent(path)}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = path.split('/').pop() || 'figure';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const handleBatchDownloadAll = () => {
    if (!sessionId || figureList.length === 0) return;
    // Download all files directly or trigger batch endpoint
    const allPaths: string[] = [];
    figureList.forEach((f) => {
      if (f.pngPath) allPaths.push(f.pngPath);
      if (f.svgPath) allPaths.push(f.svgPath);
      if (f.pdfPath) allPaths.push(f.pdfPath);
    });

    if (allPaths.length === 0) return;
    const url = `/api/artifacts/sessions/${sessionId}/batch-download?paths=${encodeURIComponent(allPaths.join(','))}`;
    window.open(url, '_blank');
    antMessage.info('已开始批量下载所有图表资产 (PNG/SVG/PDF)...');
  };

  return (
    <>
      <Drawer
        open={visible}
        onClose={onClose}
        title={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Space>
              <FileImageOutlined style={{ color: '#1677ff' }} />
              <span>文档图表全景索引 ({figureList.length})</span>
            </Space>
            <Button size='small' icon={<ReloadOutlined />} onClick={fetchFigures} loading={loading}>
              刷新
            </Button>
          </div>
        }
        width={480}
        footer={
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Text type='secondary' style={{ fontSize: 12 }}>
              支持 PNG 预览、矢量 SVG 及出版级 PDF
            </Text>
            <Button
              type='primary'
              icon={<FileZipOutlined />}
              onClick={handleBatchDownloadAll}
              disabled={figureList.length === 0}
            >
              一键打包全部图表
            </Button>
          </div>
        }
      >
        {loading && figureList.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40 }}>
            <Spin tip='正在检索图表资产...' />
          </div>
        ) : figureList.length === 0 ? (
          <Empty description='当前会话暂未生成图表资产' style={{ margin: '40px 0' }} />
        ) : (
          <List
            dataSource={figureList}
            renderItem={(fig) => {
              const previewSrc = resolveArtifactImageSrc(
                fig.pngPath || fig.svgPath || '',
                sessionId ?? null,
                'raw'
              );

              return (
                <Card
                  key={fig.baseName}
                  size='small'
                  style={{ marginBottom: 12, borderRadius: 8, overflow: 'hidden' }}
                  bodyStyle={{ padding: 10 }}
                >
                  <div style={{ display: 'flex', gap: 12 }}>
                    <div
                      style={{
                        width: 110,
                        height: 80,
                        background: '#fafafa',
                        borderRadius: 6,
                        overflow: 'hidden',
                        border: '1px solid #f0f0f0',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        flexShrink: 0,
                      }}
                    >
                      {previewSrc ? (
                        <img
                          src={previewSrc}
                          alt={fig.displayTitle}
                          style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                        />
                      ) : (
                        <FileImageOutlined style={{ fontSize: 28, color: '#bfbfbf' }} />
                      )}
                    </div>

                    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
                      <div>
                        <Text strong style={{ fontSize: 13, display: 'block', wordBreak: 'break-word' }}>
                          {fig.displayTitle}
                        </Text>
                        <Text type='secondary' style={{ fontSize: 11, display: 'block' }}>
                          {fig.baseName}
                        </Text>
                        <div style={{ marginTop: 4 }}>
                          {fig.pngPath && <Tag color='blue' style={{ fontSize: 10, padding: '0 4px' }}>PNG 300DPI</Tag>}
                          {fig.svgPath && <Tag color='green' style={{ fontSize: 10, padding: '0 4px' }}>SVG 矢量</Tag>}
                          {fig.pdfPath && <Tag color='purple' style={{ fontSize: 10, padding: '0 4px' }}>PDF 出版级</Tag>}
                        </div>
                      </div>

                      <Space size={4} style={{ marginTop: 8 }}>
                        <Button
                          size='small'
                          type='primary'
                          ghost
                          icon={<EditOutlined />}
                          onClick={() => setActiveFineTuneFigure(fig)}
                        >
                          局部微调
                        </Button>
                        {fig.svgPath ? (
                          <Button
                            size='small'
                            icon={<DownloadOutlined />}
                            onClick={() => handleDownloadFile(fig.svgPath)}
                            title='下载可编辑矢量 SVG'
                          >
                            SVG
                          </Button>
                        ) : null}
                        {fig.pdfPath ? (
                          <Button
                            size='small'
                            icon={<DownloadOutlined />}
                            onClick={() => handleDownloadFile(fig.pdfPath)}
                            title='下载出版级 PDF'
                          >
                            PDF
                          </Button>
                        ) : null}
                        {fig.pngPath ? (
                          <Button
                            size='small'
                            icon={<DownloadOutlined />}
                            onClick={() => handleDownloadFile(fig.pngPath)}
                            title='下载 PNG 预览图'
                          >
                            PNG
                          </Button>
                        ) : null}
                        <Button
                          size='small'
                          type='text'
                          icon={<CopyOutlined />}
                          onClick={() => handleCopyRef(fig)}
                          title='复制 Markdown 引用'
                        />
                      </Space>
                    </div>
                  </div>
                </Card>
              );
            }}
          />
        )}
      </Drawer>

      {activeFineTuneFigure && (
        <FigureFineTuneModal
          visible={!!activeFineTuneFigure}
          onClose={() => setActiveFineTuneFigure(null)}
          imageUrl={resolveArtifactImageSrc(
            activeFineTuneFigure.pngPath || activeFineTuneFigure.svgPath || '',
            sessionId ?? null,
            'raw'
          )}
          imageAlt={activeFineTuneFigure.displayTitle}
          imagePath={activeFineTuneFigure.baseName}
          sessionId={sessionId}
          onSubmitPrompt={(prompt) => {
            if (onInsertPrompt) {
              onInsertPrompt(prompt);
            }
          }}
        />
      )}
    </>
  );
};

export default FigureCatalogDrawer;
