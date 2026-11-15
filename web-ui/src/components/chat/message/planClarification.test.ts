import { describe, expect, it } from 'vitest';
import type { PlanClarificationRequest } from '@/types';
import { OTHER_OPTION, buildClarificationReply, isQuestionAnswered } from './planClarification';

const request: PlanClarificationRequest = {
  request_id: 'clarify_1',
  status: 'pending',
  round: 1,
  max_rounds: 2,
  questions: [
    {
      id: 'host_range',
      header: '宿主范围',
      question: '计划聚焦哪类宿主菌？',
      options: [
        { label: '仅金黄色葡萄球菌', recommended: true },
        { label: '葡萄球菌属' },
      ],
    },
    {
      id: 'deliverable',
      header: '交付形式',
      question: '最终需要什么？',
      multi_select: true,
      options: [{ label: '候选列表', recommended: true }, { label: '综述' }],
    },
  ],
};

const copy = {
  answerIntro: 'ANSWERS',
  defaultsIntro: 'DEFAULTS',
  moreContext: 'MORE',
  recommendedSuffix: ' (rec)',
  otherPrefix: 'Other: ',
};

describe('plan clarification replies', () => {
  it('requires text for the free-form choice', () => {
    const question = request.questions[0];
    expect(isQuestionAnswered(question, {}, {})).toBe(false);
    expect(isQuestionAnswered(question, { host_range: [OTHER_OPTION] }, {})).toBe(false);
    expect(isQuestionAnswered(question, { host_range: [OTHER_OPTION] }, { host_range: ' 大肠杆菌 ' })).toBe(true);
  });

  it('builds explicit answers with multi-select and Other text', () => {
    const { text, response } = buildClarificationReply(
      request,
      'answered',
      { host_range: [OTHER_OPTION], deliverable: ['候选列表', '综述'] },
      { host_range: ' 大肠杆菌 ' },
      copy,
    );
    expect(text).toBe('ANSWERS\n- 宿主范围：Other: 大肠杆菌\n- 交付形式：候选列表、综述');
    expect(response.mode).toBe('answered');
    expect(response.answers[0]).toEqual({
      id: 'host_range', header: '宿主范围', question: '计划聚焦哪类宿主菌？',
      selected: [], other: '大肠杆菌', recommended_used: false,
    });
    expect(response.answers.every((answer) => !answer.recommended_used)).toBe(true);
  });

  it('fills only unanswered questions with recommendations and flags them', () => {
    const { text, response } = buildClarificationReply(
      request, 'recommended', { deliverable: ['综述'] }, {}, copy,
    );
    expect(text).toBe('DEFAULTS\n- 宿主范围：仅金黄色葡萄球菌 (rec)\n- 交付形式：综述');
    expect(response.answers.map((answer) => answer.recommended_used)).toEqual([true, false]);
  });

  it('asks for more context without answers', () => {
    expect(buildClarificationReply(request, 'more_context', {}, {}, copy)).toEqual({
      text: 'MORE',
      response: { request_id: 'clarify_1', mode: 'more_context', answers: [] },
    });
  });
});
