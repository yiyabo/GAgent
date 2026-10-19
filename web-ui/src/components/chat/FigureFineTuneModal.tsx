import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Modal, Input, Button, Space, Tag, Typography, message as antMessage } from 'antd';
import {
  EditOutlined,
  SendOutlined,
  ClearOutlined,
} from '@ant-design/icons';
import { useChatStore } from '@store/chat';
import { artifactImageFileName, parseSessionArtifactImageUrl } from '@utils/artifactImageUrl';
import { useT } from '@/i18n/useT';
import type { TranslationKey } from '@/i18n/keys';

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

const PRESET_CHIPS: Array<{ label: TranslationKey; prompt: TranslationKey }> = [
  { label: 'figureFineTune.chipColors', prompt: 'figureFineTune.chipColorsPrompt' },
  { label: 'figureFineTune.chipFonts', prompt: 'figureFineTune.chipFontsPrompt' },
  { label: 'figureFineTune.chipMargins', prompt: 'figureFineTune.chipMarginsPrompt' },
  { label: 'figureFineTune.chipHighlight', prompt: 'figureFineTune.chipHighlightPrompt' },
  { label: 'figureFineTune.chipExport', prompt: 'figureFineTune.chipExportPrompt' },
];

/** Renders `<b>…</b>` inside a catalogue sentence, so the emphasis can sit anywhere in either language. */
const renderBold = (text: string): React.ReactNode[] =>
  text.split(/<b>(.*?)<\/b>/g).map((part, index) => (index % 2 ? <strong key={index}>{part}</strong> : part));

export const FigureFineTuneModal: React.FC<FigureFineTuneModalProps> = ({
  visible,
  onClose,
  imageUrl,
  imageAlt: imageAltProp,
  imagePath = '',
  onSubmitPrompt,
}) => {
  const t = useT();
  const imageAlt = imageAltProp ?? t('figureFineTune.defaultAlt');
  const [instruction, setInstruction] = useState('');
  const [isDrawing, setIsDrawing] = useState(false);
  const [rects, setRects] = useState<Array<{ x: number; y: number; w: number; h: number }>>([]);
  const [startPos, setStartPos] = useState<{ x: number; y: number } | null>(null);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);

  const fileName = artifactImageFileName(imageUrl, imagePath, imageAlt);
  const sourcePath = imagePath || parseSessionArtifactImageUrl(imageUrl)?.path || fileName;

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

    // Canvas cannot read CSS variables, and the box marks a region of the user's figure, so it stays a fixed red.
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
      antMessage.warning(t('figureFineTune.emptyWarning'));
      return;
    }

    let finalPrompt = `${t('figureFineTune.reqHeader', { file: fileName, alt: imageAlt || t('figureFineTune.reqUntitled') })}\n`;
    finalPrompt += `${t('figureFineTune.reqSource', { path: sourcePath })}\n`;
    if (rects.length > 0) {
      finalPrompt += `${t('figureFineTune.reqBoxes', { count: rects.length })}\n`;
    }
    if (instruction.trim()) {
      finalPrompt += `${t('figureFineTune.reqInstruction', { text: instruction.trim() })}\n`;
    }
    finalPrompt += t('figureFineTune.reqSpec', { file: fileName });

    try {
      await useChatStore.getState().sendMessage(finalPrompt);
      antMessage.success(t('figureFineTune.sent'));
    } catch (err) {
      console.error('Failed to send fine-tune message directly:', err);
      const cur = useChatStore.getState().inputText;
      useChatStore.getState().setInputText(cur ? `${cur}\n\n${finalPrompt}` : finalPrompt);
      antMessage.info(t('figureFineTune.loadedInInput'));
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
          <EditOutlined style={{ color: 'var(--color-action)' }} />
          <span>{t('figureFineTune.title', { name: imageAlt || fileName })}</span>
        </Space>
      }
      width={720}
      footer={[
        <Button key='cancel' onClick={onClose}>
          {t('common.cancel')}
        </Button>,
        <Button key='submit' type='primary' icon={<SendOutlined />} onClick={handleSubmit}>
          {t('figureFineTune.send')}
        </Button>,
      ]}
      destroyOnClose
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ background: 'var(--surface-sunken)', padding: 10, borderRadius: 8, border: '1px solid var(--border-subtle)' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <Text type='secondary' style={{ fontSize: 12 }}>
              {renderBold(t('figureFineTune.drawHint'))}
            </Text>
            {rects.length > 0 && (
              <Button size='small' type='text' icon={<ClearOutlined />} onClick={handleClearBoxes} danger>
                {t('figureFineTune.clearBoxes', { count: rects.length })}
              </Button>
            )}
          </div>

          <div
            style={{
              position: 'relative',
              display: 'inline-block',
              maxWidth: '100%',
              margin: '0 auto',
              border: '1px solid var(--border-subtle)',
              borderRadius: 6,
              overflow: 'hidden',
              background: 'var(--surface-card)',
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
            {t('figureFineTune.presets')}
          </Text>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {PRESET_CHIPS.map((chip, idx) => (
              <Tag
                key={idx}
                color='blue'
                style={{ cursor: 'pointer', padding: '3px 8px', fontSize: 12 }}
                onClick={() => handleApplyChip(t(chip.prompt))}
              >
                {t(chip.label)}
              </Tag>
            ))}
          </div>
        </div>

        <div>
          <Text strong style={{ fontSize: 13, marginBottom: 6, display: 'block' }}>
            {t('figureFineTune.describe')}
          </Text>
          <TextArea
            rows={3}
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder={t('figureFineTune.placeholder')}
          />
        </div>
      </div>
    </Modal>
  );
};

export default FigureFineTuneModal;
