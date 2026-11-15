import type {
  PlanClarificationAnswer,
  PlanClarificationQuestion,
  PlanClarificationRequest,
  PlanClarificationResponse,
} from '@/types';

/** Sentinel option value for the client-added free-form choice. */
export const OTHER_OPTION = '__other__';

export type ClarificationSelections = Record<string, string[]>;
export type ClarificationOtherText = Record<string, string>;

export interface ClarificationCopy {
  answerIntro: string;
  defaultsIntro: string;
  moreContext: string;
  recommendedSuffix: string;
  otherPrefix: string;
}

const recommendedLabel = (question: PlanClarificationQuestion): string | null =>
  question.options.find((option) => option.recommended)?.label ?? question.options[0]?.label ?? null;

/** A question is answered when it has a choice and any "Other" choice has text. */
export function isQuestionAnswered(
  question: PlanClarificationQuestion,
  selections: ClarificationSelections,
  otherText: ClarificationOtherText,
): boolean {
  const chosen = selections[question.id] ?? [];
  if (chosen.length === 0) return false;
  return !chosen.includes(OTHER_OPTION) || (otherText[question.id] ?? '').trim().length > 0;
}

function answerFor(
  question: PlanClarificationQuestion,
  selections: ClarificationSelections,
  otherText: ClarificationOtherText,
  useDefault: boolean,
): PlanClarificationAnswer {
  if (!isQuestionAnswered(question, selections, otherText) && useDefault) {
    const fallback = recommendedLabel(question);
    return {
      id: question.id,
      header: question.header,
      question: question.question,
      selected: fallback ? [fallback] : [],
      other: null,
      recommended_used: true,
    };
  }
  const chosen = selections[question.id] ?? [];
  const other = chosen.includes(OTHER_OPTION) ? (otherText[question.id] ?? '').trim() : '';
  return {
    id: question.id,
    header: question.header,
    question: question.question,
    selected: chosen.filter((value) => value !== OTHER_OPTION),
    other: other || null,
    recommended_used: false,
  };
}

function formatAnswer(answer: PlanClarificationAnswer, copy: ClarificationCopy): string {
  const parts = [...answer.selected];
  if (answer.other) parts.push(`${copy.otherPrefix}${answer.other}`);
  const suffix = answer.recommended_used ? copy.recommendedSuffix : '';
  return `- ${answer.header}：${parts.join('、')}${suffix}`;
}

/**
 * Build the user's reply: readable text for the transcript plus a structured
 * response for the backend. "recommended" fills unanswered questions with the
 * agent's recommendation and flags them so the Plan records them as assumptions.
 */
export function buildClarificationReply(
  request: PlanClarificationRequest,
  mode: PlanClarificationResponse['mode'],
  selections: ClarificationSelections,
  otherText: ClarificationOtherText,
  copy: ClarificationCopy,
): { text: string; response: PlanClarificationResponse } {
  if (mode === 'more_context') {
    return { text: copy.moreContext, response: { request_id: request.request_id, mode, answers: [] } };
  }
  const answers = request.questions.map((question) =>
    answerFor(question, selections, otherText, mode === 'recommended'));
  const intro = mode === 'recommended' && answers.some((answer) => answer.recommended_used)
    ? copy.defaultsIntro
    : copy.answerIntro;
  return {
    text: [intro, ...answers.map((answer) => formatAnswer(answer, copy))].join('\n'),
    response: { request_id: request.request_id, mode, answers },
  };
}
