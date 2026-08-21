import React, { useState, useEffect } from 'react';
import { Drawer, Button, Space, Typography, Spin, Tooltip } from 'antd';
import {
  FilePdfOutlined,
  DownloadOutlined,
  FullscreenOutlined,
  CloseOutlined,
} from '@ant-design/icons';

const { Text } = Typography;

interface PdfViewerState {
  visible: boolean;
  url: string;
  title: string;
  downloadUrl?: string;
}

export const PdfViewerDrawer: React.FC = () => {
  const [state, setState] = useState<PdfViewerState>({
    visible: false,
    url: '',
    title: 'PDF 预览',
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const handleOpen = (e: any) => {
      const { url, title, downloadUrl } = e.detail || {};
      if (url) {
        setState({
          visible: true,
          url,
          title: title || url.split('/').pop() || 'PDF 预览',
          downloadUrl: downloadUrl || url,
        });
        setLoading(true);
      }
    };

    window.addEventListener('phage:open-pdf-viewer', handleOpen);
    return () => {
      window.removeEventListener('phage:open-pdf-viewer', handleOpen);
    };
  }, []);

  const handleClose = () => {
    setState((prev) => ({ ...prev, visible: false }));
  };

  const handleDownload = () => {
    if (!state.downloadUrl) return;
    const a = document.createElement('a');
    a.href = state.downloadUrl;
    a.download = state.title || 'document.pdf';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  return (
    <Drawer
      open={state.visible}
      onClose={handleClose}
      title={
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
          <Space>
            <FilePdfOutlined style={{ color: '#ff4d4f', fontSize: 18 }} />
            <Text strong style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {state.title}
            </Text>
          </Space>
          <Space size="small">
            <Tooltip title="在新窗口全屏打开">
              <Button
                size="small"
                icon={<FullscreenOutlined />}
                onClick={() => window.open(state.url, '_blank')}
              />
            </Tooltip>
            <Tooltip title="下载此 PDF">
              <Button
                size="small"
                type="primary"
                icon={<DownloadOutlined />}
                onClick={handleDownload}
              >
                下载
              </Button>
            </Tooltip>
          </Space>
        </div>
      }
      width={720}
      bodyStyle={{ padding: 0, display: 'flex', flexDirection: 'column', background: '#525659' }}
    >
      {state.visible && (
        <div style={{ position: 'relative', width: '100%', height: '100%' }}>
          {loading && (
            <div
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                right: 0,
                bottom: 0,
                display: 'flex',
                justifyContent: 'center',
                alignItems: 'center',
                background: '#f0f2f5',
                zIndex: 2,
              }}
            >
              <Spin tip="正在加载 PDF 文档..." />
            </div>
          )}
          <iframe
            src={state.url}
            title={state.title}
            onLoad={() => setLoading(false)}
            style={{
              width: '100%',
              height: '100%',
              border: 'none',
              display: 'block',
            }}
          />
        </div>
      )}
    </Drawer>
  );
};

export default PdfViewerDrawer;
