import type { ChatMessage } from '@/types';

export interface RecoveredPlanBinding {
  planId: number;
  planTitle: string | null;
}

const PLAN_ID_KEY_PATTERN = /"plan_id"\s*:\s*(\d+)/;
const PLAN_TITLE_KEY_PATTERN = /"(?:plan_title|title)"\s*:\s*"([^"]+)"/;
const PLAN_ID_CONTENT_PATTERN = /plan\s*id\s*[:：]\s*(\d+)/i;

function extractBindingFromText(text: unknown): RecoveredPlanBinding | null {
  if (typeof text !== 'string' || !text.trim()) {
    return null;
  }

  const idMatch = text.match(PLAN_ID_KEY_PATTERN) ?? text.match(PLAN_ID_CONTENT_PATTERN);
  if (!idMatch) {
    return null;
  }

  const planId = Number(idMatch[1]);
  if (!Number.isFinite(planId)) {
    return null;
  }

  const titleMatch = text.match(PLAN_TITLE_KEY_PATTERN);
  const planTitle = titleMatch?.[1]?.trim() ? titleMatch[1].trim() : null;
  return { planId, planTitle };
}

function extractBindingFromMessage(message: ChatMessage): RecoveredPlanBinding | null {
  const metadata = (message.metadata ?? {}) as Record<string, any>;
  const directPlanId = metadata.plan_id;
  if (typeof directPlanId === 'number' && Number.isFinite(directPlanId)) {
    return {
      planId: directPlanId,
      planTitle: typeof metadata.plan_title === 'string' && metadata.plan_title.trim()
        ? metadata.plan_title.trim()
        : null,
    };
  }

  const thinkingSteps = Array.isArray((metadata.thinking_process as any)?.steps)
    ? ((metadata.thinking_process as any).steps as Array<Record<string, any>>)
    : [];

  for (let index = thinkingSteps.length - 1; index >= 0; index -= 1) {
    const step = thinkingSteps[index] ?? {};
    const recovered =
      extractBindingFromText(step.action_result) ??
      extractBindingFromText(step.action);
    if (recovered) {
      return recovered;
    }
  }

  return extractBindingFromText(message.content);
}

export function recoverPlanBindingFromMessages(
  messages: ChatMessage[]
): RecoveredPlanBinding | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const recovered = extractBindingFromMessage(messages[index]);
    if (recovered) {
      return recovered;
    }
  }
  return null;
}
