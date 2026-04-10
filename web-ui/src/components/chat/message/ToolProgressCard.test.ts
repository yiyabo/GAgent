import { describe, expect, it } from 'vitest';

import {
  deriveCompactActionPreview,
  deriveCompactRecentActivity,
} from './ToolProgressCard';

describe('ToolProgressCard helpers', () => {
  it('derives compact action preview labels without duplicating the current tool', () => {
    expect(
      deriveCompactActionPreview(
        [
          { kind: 'tool_operation', name: 'document_reader' },
          { kind: 'plan_operation', name: 'update' },
          { kind: 'tool_operation', name: 'file_operations' },
          { kind: 'tool_operation', name: 'document_reader' },
        ],
        'document_reader',
      ),
    ).toEqual(['plan_operation', 'file_operations']);
  });

  it('derives recent activity from history and notes while dropping current duplicates', () => {
    expect(
      deriveCompactRecentActivity({
        history: [
          { tool: 'document_reader', label: '读取元数据文件' },
          { tool: 'plan_operation', label: '更新计划信息' },
        ],
        expandedNotes: ['汇总生成 8 个任务'],
        currentLabel: '更新计划信息',
        currentDetails: null,
      }),
    ).toEqual([
      'document_reader · 读取元数据文件',
      '汇总生成 8 个任务',
    ]);
  });
});
