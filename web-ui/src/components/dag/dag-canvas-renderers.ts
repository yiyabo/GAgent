import { CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS, getCardColors, getStatusColors } from './dag-constants';
import type { GraphNode } from './dag-constants';
import { withAlpha, type CanvasPalette } from '@/theme/canvasPalette';

export function drawNote(
  ctx: CanvasRenderingContext2D,
  node: GraphNode,
  isSelected: boolean,
  isHovered: boolean,
  isHighlighted: boolean,
  palette: CanvasPalette
) {
  const x = node.x || 0;
  const y = node.y || 0;
  const colors = getCardColors(palette, node.task.task_type);
  const statusInfo = getStatusColors(palette, node.task.status);
  
  ctx.save();
  ctx.translate(x, y);

  const opacity = isHighlighted ? 1 : 0.25;
  const isActive = isSelected || isHovered;

  if (isHighlighted) {
    ctx.shadowColor = isActive ? palette.shadow : withAlpha(palette.shadow, 0.5);
    ctx.shadowBlur = isActive ? 10 : 4;
    ctx.shadowOffsetX = 0;
    ctx.shadowOffsetY = isActive ? 4 : 2;
  }

  ctx.beginPath();
  ctx.roundRect(-CARD_WIDTH / 2, -CARD_HEIGHT / 2, CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS);
  ctx.fillStyle = colors.bg;
  ctx.globalAlpha = opacity;
  ctx.fill();

  ctx.shadowColor = 'transparent';
  ctx.shadowBlur = 0;

  ctx.beginPath();
  ctx.roundRect(-CARD_WIDTH / 2, -CARD_HEIGHT / 2, 4, CARD_HEIGHT, [CARD_RADIUS, 0, 0, CARD_RADIUS]);
  ctx.fillStyle = colors.accent;
  ctx.globalAlpha = opacity * 0.9;
  ctx.fill();

  ctx.beginPath();
  ctx.roundRect(-CARD_WIDTH / 2, -CARD_HEIGHT / 2, CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS);
  ctx.strokeStyle = isActive ? colors.accent : colors.line;
  ctx.lineWidth = isActive ? 1.5 : 1;
  ctx.globalAlpha = opacity;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(CARD_WIDTH / 2 - 8, -CARD_HEIGHT / 2 + 8, 4, 0, Math.PI * 2);
  ctx.fillStyle = statusInfo.color;
  ctx.globalAlpha = opacity;
  ctx.fill();

  ctx.globalAlpha = opacity;
  ctx.fillStyle = palette.textPrimary;
  // Canvas takes a literal font string, so use the resolved stack; 12px is the type floor.
  ctx.font = `${node.task.task_type?.toUpperCase() === 'ROOT' ? '600 ' : ''}12px ${palette.fontSans}`;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  
  const words = node.name.split('');
  let line = '';
  let lines: string[] = [];
  const maxWidth = CARD_WIDTH - 20;
  
  for (const char of words) {
    const testLine = line + char;
    const metrics = ctx.measureText(testLine);
    if (metrics.width > maxWidth && line) {
      lines.push(line);
      line = char;
    } else {
      line = testLine;
    }
  }
  lines.push(line);
  
  lines = lines.slice(0, 2);
  if (lines.length === 2 && node.name.length > lines.join('').length) {
    lines[1] = lines[1].slice(0, -2) + '..';
  }

  const lineHeight = 12;
  const textStartY = lines.length === 1 ? 0 : -lineHeight / 2;
  lines.forEach((l, i) => {
    ctx.fillText(l, 2, textStartY + i * lineHeight);
  });

  if (isSelected && isHighlighted) {
    ctx.strokeStyle = colors.accent;
    ctx.lineWidth = 2;
    ctx.globalAlpha = 1;
    ctx.beginPath();
    ctx.roundRect(-CARD_WIDTH / 2 - 2, -CARD_HEIGHT / 2 - 2, CARD_WIDTH + 4, CARD_HEIGHT + 4, CARD_RADIUS + 1);
    ctx.stroke();
  }

  ctx.restore();
}

export function drawLink(
  ctx: CanvasRenderingContext2D,
  link: any,
  isHighlighted: boolean,
  palette: CanvasPalette
) {
  const source = link.source;
  const target = link.target;
  const linkType: 'parent' | 'dependency' = link.type || 'parent';
  
  if (!source.x || !target.x) return;

  const isDependency = linkType === 'dependency';
  
  let sourceX = source.x || 0;
  let sourceY = (source.y || 0) + CARD_HEIGHT / 2;
  let targetX = target.x || 0;
  let targetY = (target.y || 0) - CARD_HEIGHT / 2;

  if (isDependency) {
    sourceX = (source.x || 0) + CARD_WIDTH / 2;
    sourceY = source.y || 0;
    targetX = (target.x || 0) - CARD_WIDTH / 2;
    targetY = target.y || 0;
  }

  ctx.save();
  
  if (isDependency) {
    ctx.globalAlpha = 0.8;
    ctx.strokeStyle = palette.warning;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([5, 3]);
  } else {
    ctx.globalAlpha = isHighlighted ? 0.4 : 0.12;
    ctx.strokeStyle = palette.textTertiary;
    ctx.lineWidth = isHighlighted ? 1 : 0.6;
  }

  ctx.beginPath();
  ctx.moveTo(sourceX, sourceY);
  
  if (isDependency) {
    const dx = targetX - sourceX;
    const dy = targetY - sourceY;
    const controlOffset = Math.min(Math.abs(dx) * 0.4, 60);
    ctx.bezierCurveTo(
      sourceX + controlOffset, sourceY,
      targetX - controlOffset, targetY,
      targetX, targetY
    );
  } else {
    const midY = sourceY + (targetY - sourceY) * 0.5;
    ctx.lineTo(sourceX, midY);
    ctx.lineTo(targetX, midY);
    ctx.lineTo(targetX, targetY);
  }
  
  ctx.stroke();

  if (isDependency) {
    const arrowSize = 4;
    ctx.globalAlpha = 0.9;
    ctx.fillStyle = palette.warning;
    ctx.setLineDash([]);
    
    ctx.beginPath();
    ctx.moveTo(targetX, targetY);
    ctx.lineTo(targetX - arrowSize * 1.8, targetY - arrowSize);
    ctx.lineTo(targetX - arrowSize * 1.8, targetY + arrowSize);
    ctx.closePath();
    ctx.fill();
  }

  ctx.restore();
}
