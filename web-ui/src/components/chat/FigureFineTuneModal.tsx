import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Modal, Input, Button, Space, Tag, Typography, message as antMessage } from 'antd';
import {
  EditOutlined,
  SendOutlined,
  ClearOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';

const { TextArea } = Input;
const { Text } = Typography;

interface FigureFineTuneModalProps {
  visible: boolean;
  onClose: () => void;
  imageUrl: string;
  imageAlt?: string;
  imagePath?: string;
  sessionId?: string | null;
  onSubmitPrompt?: (prompt: string, annotatedImageBase64?: string) => void;
}

const PRESET_CHIPS = [
  { label: '🎨 统一Nature配色', prompt: '请将该图配色调整为统一的Nature科学色盘（珊瑚红、深蓝、青绿），保持全文风格一致。' },
  { label: '🔤 加大坐标与标签字号', prompt: '请调大坐标轴标签与刻度字号（Title 12pt bold, Labels 10pt, Ticks 9pt），避免文字遮挡或过小。' },
  { label: '📐 调整边距与去上右边框', prompt: '请调整图像边距，去除顶部和右侧无用边框（Despine top/right），保持背景纯白。' },
  { label: '📊 突出前3组关键数据', prompt: '请在图中对前3组关键差异数据进行重点高亮标记，其他组弱化对比。' },
  { label: '✨ 导出矢量SVG/PDF', prompt: '请重新导出该图，确保同时输出 PNG (300dpi)、可编辑矢量 SVG 和出版级 PDF。' },
];

export const FigureFineTuneModal: React.FC<FigureFineTuneModalProps> = ({
  visible,
  onClose,
  imageUrl,
  imageAlt = '图表',
  imagePath = '',
  onSubmitPrompt,
}) => {
  const [instruction, setInstruction] = useState('');
  const [isDrawing, setIsDrawing] = useState(false);
  const [rects, setRects] = useState<Array<{ x: number; y: number; w: number; h: number }>>([]);
  const [startPos, setStartPos] = useState<{ x: number; y: number } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const rawName = imagePath
    ? imagePath.split('/').pop() || imagePath
    : (imageUrl.split('?')[0].split('/').pop() || 'figure.png');
  const fileName = rawName.replace(/\.[^/.]+$/, '');

  useEffect(() => {
    if (visible) {
      setInstruction('');
      setRects([]);
    }
  }, [visible]);

  const redrawCanvas = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.strokeStyle = '#ff4d4f';
    ctx.lineWidth = 3;
    ctx.fillStyle = 'rgba(255, 77, 79, 0.2)';

    rects.forEach((r) => {
      ctx.fillRect(r.x, r.y, r.w, r.h);
      ctx.strokeRect(r.x, r.y, r.w, r.h);
    });
  }, [rects]);

  useEffect(() => {
    redrawCanvas();
  }, [rects, redrawCanvas]);

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    setStartPos({ x, y });
    setIsDrawing(true);
  };

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing || !startPos) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const curX = e.clientX - rect.left;
    const curY = e.clientY - rect.top;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    redrawCanvas();

    ctx.strokeStyle = '#ff4d4f';
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.strokeRect(startPos.x, startPos.y, curX - startPos.x, curY - startPos.y);
    ctx.setLineDash([]);
  };

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDrawing || !startPos) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const curX = e.clientX - rect.left;
    const curY = e.clientY - rect.top;

    const w = curX - startPos.x;
    const h = curY - startPos.y;

    if (Math.abs(w) > 8 && Math.abs(h) > 8) {
      setRects((prev) => [
        ...prev,
        {
          x: w > 0 ? startPos.x : curX,
          y: h > 0 ? startPos.y : curY,
          w: Math.abs(w),
          h: Math.abs(h),
        },
      ]);
    }

    setIsDrawing(false);
    setStartPos(null);
  };

  const handleClearBoxes = () => {
    setRects([]);
  };

  const handleApplyChip = (prompt: string) => {
    setInstruction((prev) => (prev ? `${prev}\n${prompt}` : prompt));
  };

  const handleSubmit = async () => {
    if (!instruction.trim() && rects.length === 0) {
      antMessage.warning('请输入微调要求或在图上框选标注区域');
      return;
    }

    let finalPrompt = `【图表局部微调】针对目标图表 \`${fileName}\` (${imageAlt || '未命名图表'}):\n`;
    if (rects.length > 0) {
      finalPrompt += `- 用户在图上框选了 ${rects.length} 处关键区域（已标红框）。\n`;
    }
    if (instruction.trim()) {
      finalPrompt += `- 修改具体要求: ${instruction.trim()}\n`;
    }
    finalPrompt += `- 规范要求: 请在保持全文风格一致（统一字体字号、Nature配色、纯白背景、无多余边框）的前提下进行定向修改，并覆盖更新 \`results/${fileName}\`（输出 PNG, 可编辑矢量 SVG, 出版级 PDF）。`;

    try {
      await useChatStore.getState().sendMessage(finalPrompt);
      antMessage.success('已发送微调指令，AI 正在为您修改图表...');
    } catch (err) {
      console.error('Failed to send fine-tune message directly:', err);
      const cur = useChatStore.getState().inputText;
      useChatStore.getState().setInputText(cur ? `${cur}\n\n${finalPrompt}` : finalPrompt);
      antMessage.info('微调指令已载入对话输入框');
    }

    if (onSubmitPrompt) {
      onSubmitPrompt(finalPrompt);
    }
    onClose();
  };

  return (
    <Modal
      open={visible}
      onCancel={onClose}
      title={
        <Space>
          <EditOutlined style={{ color: '#1677ff' }} />
          <span>图表定向微调 — {imageAlt || fileName}</span>
        </Space>
      }
      width={720}
      footer={[
        <Button key='cancel' onClick={onClose}>
          取消
        </Button>,
        <Button key='submit' type='primary' icon={<SendOutlined />} onClick={handleSubmit}>
          发送微调指令
        </Button>,
      ]}
      destroyOnClose
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ background: '#fafafa', padding: 10, borderRadius: 8, border: '1px solid #f0f0f0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Text type='secondary' style={{ fontSize: 12 }}>
              🖱️ 可直接在图上按住鼠标左键<strong>拖拽画框</strong>以标记需调整的局部区域:
            </Text>
            {rects.length > 0 && (
              <Button size='small' type='text' icon={<ClearOutlined />} onClick={handleClearBoxes} danger>
                清除红框 ({rects.length})
              </Button>
            )}
          </div>

          <div
            style={{
              position: 'relative',
              display: 'inline-block',
              maxWidth: '100%',
              margin: '0 auto',
              border: '1px solid #e8e8e8',
              borderRadius: 6,
              overflow: 'hidden',
              background: '#fff',
            }}
          >
            <img
              ref={imgRef}
              src={imageUrl}
              alt={imageAlt}
              style={{ display: 'block', maxWidth: '100%', maxHeight: 360, objectFit: 'contain' }}
              onLoad={(e) => {
                const img = e.currentTarget;
                if (canvasRef.current) {
                  canvasRef.current.width = img.clientWidth;
                  canvasRef.current.height = img.clientHeight;
                }
              }}
            />
            <canvas
              ref={canvasRef}
              onMouseDown={handleMouseDown}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                cursor: 'crosshair',
                width: '100%',
                height: '100%',
              }}
            />
          </div>
        </div>

        <div>
          <Text strong style={{ fontSize: 13, marginBottom: 6, display: 'block' }}>
            常用微调快捷模板:
          </Text>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {PRESET_CHIPS.map((chip, idx) => (
              <Tag
                key={idx}
                color='blue'
                style={{ cursor: 'pointer', padding: '3px 8px', fontSize: 12 }}
                onClick={() => handleApplyChip(chip.prompt)}
              >
                {chip.label}
              </Tag>
            ))}
          </div>
        </div>

        <div>
          <Text strong style={{ fontSize: 13, marginBottom: 6, display: 'block' }}>
            具体微调要求描述:
          </Text>
          <TextArea
            rows={3}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder='例如：将X轴字体调大，柱状图改为Nature浅蓝色，去掉背景网格线...'
          />
        </div>
      </div>
    </Modal>
  );
};

export default FigureFineTuneModal;
