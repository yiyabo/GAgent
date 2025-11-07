import { ChatActionSummary, PlanSyncEventDetail, PlanSyncEventType } from '@/types';

const PLAN_CREATION_ACTIONS = new Set(['create_plan', 'clone_plan', 'import_plan']);
const PLAN_DELETION_ACTIONS = new Set(['delete_plan', 'archive_plan']);
const PLAN_UPDATE_ACTIONS = new Set([
  'update_plan',
  'rename_plan',
  'update_plan_metadata',
  'set_plan_metadata',
  'assign_plan',
]);
const TASK_MUTATION_ACTIONS = new Set([
  'create_task',
  'update_task',
  'update_task_instruction',
  'move_task',
  'delete_task',
  'decompose_task',
  'rerun_task',
  'bulk_update_tasks',
  'bulk_delete_tasks',
]);
const TASK_AFFECTING_PLAN_ACTIONS = new Set([
  'execute_plan',
  'refresh_plan',
  'reorder_plan',
  'decompose_plan',
]);

const PLAN_EVENT_DEBOUNCE_MS = 500;
const RECENT_EVENT_TTL_MS = 10_000;
const recentEventMap = new Map<string, number>();

const hasOwn = (obj: Record<string, any> | undefined, key: string): boolean =>
  !!obj && Object.prototype.hasOwnProperty.call(obj, key);

export const coercePlanId = (value: unknown): number | null | undefined => {
  if (value === null) {
    return null;
  }
  if (value === undefined) {
    return undefined;
  }
  const numeric = Number(value);
  if (Number.isFinite(numeric)) {
    return numeric;
  }
  return undefined;
};

export const searchPlanId = (input: any, depth = 0): number | null | undefined => {
  if (!input || typeof input !== 'object' || depth > 5) {
    return undefined;
  }

  const directKeys = ['plan_id', 'planId', 'planID'];
  for (const key of directKeys) {
    if (hasOwn(input, key)) {
      const direct = coercePlanId((input as any)[key]);
      if (direct !== undefined) {
        return direct;
      }
      const nested = searchPlanId((input as any)[key], depth + 1);
      if (nested !== undefined) {
        return nested;
      }
    }
  }

  const nestedKeys = ['plan', 'plan_summary', 'parameters', 'result', 'payload', 'details', 'metadata'];
  for (const key of nestedKeys) {
    if (hasOwn(input, key)) {
      const nested = searchPlanId((input as any)[key], depth + 1);
      if (nested !== undefined) {
        return nested;
      }
    }
  }

  if (
    hasOwn(input, 'id') &&
    (hasOwn(input, 'title') || hasOwn(input, 'nodes') || hasOwn(input, 'tasks'))
  ) {
    const candidate = coercePlanId((input as any).id);
    if (candidate !== undefined) {
      return candidate;
    }
  }

  for (const value of Object.values(input)) {
    if (value && typeof value === 'object') {
      const nested = searchPlanId(value, depth + 1);
      if (nested !== undefined) {
        return nested;
      }
    }
  }

  return undefined;
};

export const coercePlanTitle = (value: unknown): string | null | undefined => {
  if (value == null) {
    return value as null | undefined;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    return trimmed.length > 0 ? trimmed : undefined;
  }
  return undefined;
};

const searchPlanTitle = (input: any, depth = 0): string | null | undefined => {
  if (!input || typeof input !== 'object' || depth > 5) {
    return undefined;
  }

  const directKeys = ['plan_title', 'planTitle', 'title'];
  for (const key of directKeys) {
    if (hasOwn(input, key)) {
      const direct = coercePlanTitle((input as any)[key]);
      if (direct !== undefined) {
        return direct;
      }
      const nested = searchPlanTitle((input as any)[key], depth + 1);
      if (nested !== undefined) {
        return nested;
      }
    }
  }

  const nestedKeys = ['plan', 'plan_summary', 'parameters', 'result', 'payload', 'details', 'metadata'];
  for (const key of nestedKeys) {
    if (hasOwn(input, key)) {
      const nested = searchPlanTitle((input as any)[key], depth + 1);
      if (nested !== undefined) {
        return nested;
      }
    }
  }

  for (const value of Object.values(input)) {
    if (value && typeof value === 'object') {
      const nested = searchPlanTitle(value, depth + 1);
      if (nested !== undefined) {
        return nested;
      }
    }
  }

  return undefined;
};

export const extractPlanIdFromActions = (
  actions: ChatActionSummary[] | undefined
): number | null | undefined => {
  if (!Array.isArray(actions)) {
    return undefined;
  }
  for (const action of actions) {
    const candidate =
      searchPlanId(action?.parameters) ??
      searchPlanId(action?.details) ??
      searchPlanId(action);
    if (candidate !== undefined) {
      return candidate;
    }
  }
  return undefined;
};

export const extractPlanTitleFromActions = (
  actions: ChatActionSummary[] | undefined
): string | null | undefined => {
  if (!Array.isArray(actions)) {
    return undefined;
  }
  for (const action of actions) {
    const candidate =
      searchPlanTitle(action?.parameters) ??
      searchPlanTitle(action?.details) ??
      searchPlanTitle(action);
    if (candidate !== undefined) {
      return candidate;
    }
  }
  return undefined;
};

const normalizeActionName = (value?: string | null): string =>
  (value ?? '').toString().toLowerCase();

interface PlanEventContext {
  fallbackPlanId?: number | null;
  fallbackPlanTitle?: string | null;
}

export const derivePlanSyncEventsFromActions = (
  actions: ChatActionSummary[] | undefined,
  context: PlanEventContext = {}
): PlanSyncEventDetail[] => {
  if (!Array.isArray(actions) || actions.length === 0) {
    return [];
  }

  const results = new Map<string, PlanSyncEventDetail>();

  for (const action of actions) {
    const kind = normalizeActionName(action?.kind);
    const name = normalizeActionName(action?.name);
    if (!kind || !name) {
      continue;
    }

    let eventType: PlanSyncEventType | null = null;
    if (kind === 'plan_operation') {
      if (PLAN_CREATION_ACTIONS.has(name)) {
        eventType = 'plan_created';
      } else if (PLAN_DELETION_ACTIONS.has(name)) {
        eventType = 'plan_deleted';
      } else if (PLAN_UPDATE_ACTIONS.has(name)) {
        eventType = 'plan_updated';
      } else if (TASK_AFFECTING_PLAN_ACTIONS.has(name)) {
        eventType = 'task_changed';
      }
    } else if (kind === 'task_operation') {
      if (TASK_MUTATION_ACTIONS.has(name)) {
        eventType = 'task_changed';
      }
    }

    if (!eventType) {
      continue;
    }

    const planId =
      coercePlanId(action?.parameters?.plan_id) ??
      searchPlanId(action?.parameters) ??
      searchPlanId(action?.details) ??
      context.fallbackPlanId ??
      null;
    const planTitle =
      coercePlanTitle(action?.parameters?.title) ??
      coercePlanTitle(action?.details?.title) ??
      searchPlanTitle(action?.parameters) ??
      searchPlanTitle(action?.details) ??
      context.fallbackPlanTitle ??
      null;

    const key = `${eventType}:${planId ?? 'null'}`;
    if (!results.has(key)) {
      results.set(key, {
        type: eventType,
        plan_id: planId ?? null,
        plan_title: planTitle ?? null,
      });
    }
  }

  return Array.from(results.values());
};

export interface DispatchPlanSyncOptions {
  trackingId?: string | null;
  source?: string | null;
  raw?: unknown;
  status?: string | null;
  jobId?: string | null;
  jobType?: string | null;
  sessionId?: string | null;
}

const cleanupRecentEvents = (now: number) => {
  for (const [key, timestamp] of recentEventMap.entries()) {
    if (now - timestamp > RECENT_EVENT_TTL_MS) {
      recentEventMap.delete(key);
    }
  }
};

export const dispatchPlanSyncEvent = (
  detail: PlanSyncEventDetail,
  options: DispatchPlanSyncOptions = {}
) => {
  if (typeof window === 'undefined') {
    return;
  }

  const now = Date.now();
  const trackingId = options.trackingId ?? detail.tracking_id ?? null;
  const planKey = detail.plan_id != null ? detail.plan_id : 'null';
  const dedupeKey = `${detail.type}:${planKey}:${trackingId ?? ''}`;
  const lastTimestamp = recentEventMap.get(dedupeKey);
  if (lastTimestamp && now - lastTimestamp < PLAN_EVENT_DEBOUNCE_MS) {
    return;
  }
  recentEventMap.set(dedupeKey, now);
  cleanupRecentEvents(now);

  const enriched: PlanSyncEventDetail = {
    ...detail,
    tracking_id: trackingId,
    source: options.source ?? detail.source ?? null,
    triggered_at: detail.triggered_at ?? new Date(now).toISOString(),
    status: options.status ?? detail.status,
    job_id: options.jobId ?? detail.job_id,
    job_type: options.jobType ?? detail.job_type,
    session_id: options.sessionId ?? detail.session_id,
    raw: options.raw ?? detail.raw,
  };

  try {
    console.info('[PlanSync] Dispatch event:', enriched);
    window.dispatchEvent(new CustomEvent('tasksUpdated', { detail: enriched }));
  } catch (error) {
    console.warn('Failed to dispatch plan sync event:', error, enriched);
  }
};

export const isPlanSyncEventDetail = (detail: unknown): detail is PlanSyncEventDetail => {
  if (!detail || typeof detail !== 'object') {
    return false;
  }
  const candidate = detail as Record<string, unknown>;
  return typeof candidate.type === 'string' && candidate.hasOwnProperty('plan_id');
};

export const shouldHandlePlanSyncEvent = (
  detail: unknown,
  planId: number | null | undefined,
  acceptedTypes?: PlanSyncEventType[]
): detail is PlanSyncEventDetail => {
  if (!isPlanSyncEventDetail(detail)) {
    return false;
  }
  if (acceptedTypes && acceptedTypes.length > 0 && !acceptedTypes.includes(detail.type)) {
    return false;
  }
  if (planId == null || detail.plan_id == null) {
    return true;
  }
  return detail.plan_id === planId;
};

