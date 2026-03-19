import { describe, expect, it } from 'vitest';
import { recoverPlanBindingFromMessages } from './planRecovery';

describe('planRecovery', () => {
  it('recovers the latest plan binding from thinking process action results', () => {
    const messages: any[] = [
      {
        id: 'm1',
        type: 'assistant',
        content: 'old plan',
        timestamp: new Date(),
        metadata: {
          thinking_process: {
            steps: [
              {
                action_result:
                  '[plan_operation] {"result":{"operation":"create","plan_id":54,"title":"旧计划"}}',
              },
            ],
          },
        },
      },
      {
        id: 'm2',
        type: 'assistant',
        content: '# Plan优化完成\nPlan ID: 55',
        timestamp: new Date(),
        metadata: {
          thinking_process: {
            steps: [
              {
                action_result:
                  '[plan_operation] {"result":{"operation":"review","plan_id":55,"plan_title":"增强版计划"}}',
              },
            ],
          },
        },
      },
    ];

    expect(recoverPlanBindingFromMessages(messages)).toEqual({
      planId: 55,
      planTitle: '增强版计划',
    });
  });

  it('falls back to plain text plan id in content', () => {
    const messages: any[] = [
      {
        id: 'm1',
        type: 'assistant',
        content: 'Plan ID: 77 已就绪',
        timestamp: new Date(),
        metadata: {},
      },
    ];

    expect(recoverPlanBindingFromMessages(messages)).toEqual({
      planId: 77,
      planTitle: null,
    });
  });
});
