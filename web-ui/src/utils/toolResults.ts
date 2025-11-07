import type { ChatActionSummary, ToolResultItem, ToolResultPayload } from '@/types';

type StepLike = {
  action?: {
    kind?: string | null;
    name?: string | null;
    parameters?: Record<string, any> | null;
  };
  details?: Record<string, any> | null;
  summary?: string | null;
};

const isNonEmptyString = (value: unknown): value is string =>
  typeof value === 'string' && value.trim().length > 0;

const sanitizeResultItems = (items: unknown): ToolResultItem[] | null => {
  if (!Array.isArray(items)) {
    return null;
  }
  const sanitized: ToolResultItem[] = [];
  for (const item of items) {
    if (!item || typeof item !== 'object') {
      continue;
    }
    const title = isNonEmptyString((item as any).title) ? (item as any).title : undefined;
    const url = isNonEmptyString((item as any).url) ? (item as any).url : undefined;
    const snippet =
      isNonEmptyString((item as any).snippet) || typeof (item as any).snippet === 'string'
        ? (item as any).snippet
        : undefined;
    const source = isNonEmptyString((item as any).source) ? (item as any).source : undefined;

    if (!title && !url && !snippet && !source) {
      continue;
    }
    sanitized.push({ title, url, snippet, source });
  }
  return sanitized.length > 0 ? sanitized : null;
};

const normalizeToolResultPayload = (raw: any): ToolResultPayload | null => {
  if (!raw || typeof raw !== 'object') {
    return null;
  }
  const name = isNonEmptyString((raw as any).name) ? (raw as any).name : undefined;
  const summary = isNonEmptyString((raw as any).summary) ? (raw as any).summary : undefined;
  const parameters =
    raw.parameters && typeof raw.parameters === 'object' ? { ...raw.parameters } : undefined;

  const result = raw.result && typeof raw.result === 'object' ? { ...(raw.result as any) } : {};
  if (isNonEmptyString(raw.message) && !result.message) {
    result.message = raw.message;
  }
  if (isNonEmptyString(raw.query) && !result.query) {
    result.query = raw.query;
  }
  const response =
    isNonEmptyString(result.response) || typeof result.response === 'string'
      ? (result.response as string)
      : undefined;
  const answer =
    isNonEmptyString(result.answer) || typeof result.answer === 'string'
      ? (result.answer as string)
      : undefined;
  const error =
    isNonEmptyString(result.error) || typeof result.error === 'string'
      ? (result.error as string)
      : undefined;

  const totalResults =
    typeof result.total_results === 'number' && Number.isFinite(result.total_results)
      ? result.total_results
      : undefined;
  const searchEngine = isNonEmptyString(result.search_engine) ? result.search_engine : undefined;
  const success =
    typeof result.success === 'boolean'
      ? result.success
      : typeof raw.success === 'boolean'
        ? raw.success
        : undefined;

  const normalized: ToolResultPayload = {
    name,
    summary,
    parameters,
    result: {
      ...(success === undefined ? {} : { success }),
      ...(searchEngine ? { search_engine: searchEngine } : {}),
      ...(isNonEmptyString(result.query) ? { query: result.query } : {}),
      ...(response ? { response } : {}),
      ...(answer ? { answer } : {}),
      ...(error ? { error } : {}),
      ...(totalResults !== undefined ? { total_results: totalResults } : {}),
      ...(result.results ? { results: sanitizeResultItems(result.results) } : {}),
    },
  };

  if (isNonEmptyString(result.prompt)) {
    normalized.result = { ...(normalized.result ?? {}), prompt: result.prompt };
  }
  if (Array.isArray(result.triples)) {
    normalized.result = { ...(normalized.result ?? {}), triples: result.triples };
  }
  if (result.metadata && typeof result.metadata === 'object') {
    normalized.result = {
      ...(normalized.result ?? {}),
      metadata: { ...(result.metadata as Record<string, any>) },
    };
  }
  if (result.subgraph && typeof result.subgraph === 'object') {
    normalized.result = {
      ...(normalized.result ?? {}),
      subgraph: { ...(result.subgraph as Record<string, any>) },
    };
  }

  if (normalized.result && Object.keys(normalized.result).length === 0) {
    normalized.result = null;
  }

  if (!normalized.summary && isNonEmptyString(raw.summary)) {
    normalized.summary = raw.summary;
  }

  if (!normalized.name && isNonEmptyString(raw.tool)) {
    normalized.name = raw.tool;
  }

  return normalized;
};

const createDedupKey = (payload: ToolResultPayload): string => {
  const name = payload.name ?? '';
  const summary = payload.summary ?? '';
  const query = payload.result?.query ?? '';
  return `${name}::${summary}::${query}`;
};

export const mergeToolResults = (
  existing: ToolResultPayload[] | null | undefined,
  additions: ToolResultPayload[] | null | undefined
): ToolResultPayload[] => {
  const merged: ToolResultPayload[] = [];
  const seen = new Set<string>();

  const pushIfValid = (payload: ToolResultPayload | null) => {
    if (!payload) {
      return;
    }
    const key = createDedupKey(payload);
    if (seen.has(key)) {
      return;
    }
    seen.add(key);
    merged.push(payload);
  };

  (existing ?? []).forEach((item) => pushIfValid(item));
  (additions ?? []).forEach((item) => pushIfValid(item));

  return merged;
};

export const collectToolResultsFromSteps = (
  steps: Array<StepLike | Record<string, any>> | null | undefined
): ToolResultPayload[] => {
  if (!Array.isArray(steps)) {
    return [];
  }
  const collected: ToolResultPayload[] = [];

  for (const step of steps) {
    if (!step || typeof step !== 'object') {
      continue;
    }
    const action = (step as StepLike).action ?? (step as any).action;
    if (!action || typeof action !== 'object') {
      continue;
    }
    const kind = (action as any).kind;
    if (kind !== 'tool_operation') {
      continue;
    }
    const name = (action as any).name;
    const parameters =
      action && typeof action === 'object' && 'parameters' in action
        ? ((action as any).parameters as Record<string, any>)
        : undefined;
    const details = (step as StepLike).details ?? (step as any).details ?? {};

    const payload = normalizeToolResultPayload({
      name,
      parameters,
      summary: (step as any).summary ?? (details as any)?.summary ?? (step as any).message,
      result: (details as any)?.result ?? details,
      success: (step as any).success,
    });
    if (payload) {
      collected.push(payload);
    }
  }

  return collected;
};

export const collectToolResultsFromMetadata = (value: any): ToolResultPayload[] => {
  if (!value) {
    return [];
  }
  const items = Array.isArray(value) ? value : [value];
  const collected: ToolResultPayload[] = [];
  for (const item of items) {
    const normalized = normalizeToolResultPayload(item);
    if (normalized) {
      collected.push(normalized);
    }
  }
  return collected;
};

export const collectToolResultsFromActions = (
  actions: ChatActionSummary[] | null | undefined
): ToolResultPayload[] => {
  if (!Array.isArray(actions)) {
    return [];
  }
  const collected: ToolResultPayload[] = [];
  for (const action of actions) {
    if (!action || action.kind !== 'tool_operation') {
      continue;
    }
    const normalized = normalizeToolResultPayload({
      name: action.name,
      parameters: action.parameters,
      summary: action.message,
      result: action.details,
    });
    if (normalized) {
      collected.push(normalized);
    }
  }
  return collected;
};
