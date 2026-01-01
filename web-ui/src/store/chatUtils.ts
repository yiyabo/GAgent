import {
    ActionStatusResponse,
    ChatActionStatus,
    ChatActionSummary,
    ChatMessage,
    ChatResponsePayload,
    ChatSession,
    ChatSessionSummary,
    ToolResultPayload,
} from '@/types';
import { ENV } from '@/config/env';
import { chatApi } from '@api/chat';
import {
    collectToolResultsFromMetadata,
    collectToolResultsFromActions,
    collectToolResultsFromSteps,
    mergeToolResults,
} from '@utils/toolResults';
import {
    coercePlanId,
    coercePlanTitle,
    extractPlanIdFromActions,
    extractPlanTitleFromActions,
} from '@utils/planSyncEvents';

export const isActionStatus = (value: any): value is ChatActionStatus => {
    return value === 'pending' || value === 'running' || value === 'completed' || value === 'failed';
};

export const parseDate = (value?: string | null): Date | null => {
    if (!value) {
        return null;
    }
    const timestamp = Date.parse(value);
    if (Number.isNaN(timestamp)) {
        return null;
    }
    return new Date(timestamp);
};

export const normalizeActionStatus = (status?: string | null): ChatActionStatus | null => {
    if (!status) {
        return null;
    }
    if (status === 'succeeded') {
        return 'completed';
    }
    return isActionStatus(status) ? status : null;
};

export const parseJobStreamPayload = (raw: MessageEvent<any>): Record<string, any> | null => {
    try {
        const payload = JSON.parse(raw.data);
        if (!payload || typeof payload !== 'object') {
            return null;
        }
        return payload as Record<string, any>;
    } catch (error) {
        console.warn('无法解析动作 SSE 消息:', error);
        return null;
    }
};

export type ChatStreamEvent =
    | { type: 'start' }
    | { type: 'delta'; content: string }
    | { type: 'final'; payload: ChatResponsePayload }
    | { type: 'job_update'; payload: Record<string, any> }
    | { type: 'error'; message?: string; error_type?: string };

export const parseChatStreamEvent = (raw: string): ChatStreamEvent | null => {
    const lines = raw.split('\n');
    const dataLines: string[] = [];
    for (const line of lines) {
        if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trim());
        }
    }
    if (dataLines.length === 0) {
        return null;
    }
    const payload = dataLines.join('\n');
    try {
        return JSON.parse(payload) as ChatStreamEvent;
    } catch (error) {
        console.warn('无法解析 SSE payload:', error);
        return { type: 'error', message: 'SSE payload parse failed' };
    }
};

export const streamChatEvents = async function* (
    request: Record<string, any>,
    maxRetries: number = 3
): AsyncGenerator<ChatStreamEvent> {
    let attempts = 0;

    while (attempts < maxRetries) {
        try {
            const response = await fetch(`${ENV.API_BASE_URL}/chat/stream`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request),
            });

            if (!response.ok) {
                if (response.status >= 500 && attempts < maxRetries - 1) {
                    throw new Error(`Server Error ${response.status}`);
                }
                throw new Error(`HTTP ${response.status}`);
            }
            if (!response.body) {
                throw new Error('Empty stream body');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let buffer = '';

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                let boundary = buffer.indexOf('\n\n');
                while (boundary !== -1) {
                    const rawEvent = buffer.slice(0, boundary);
                    buffer = buffer.slice(boundary + 2);
                    const event = parseChatStreamEvent(rawEvent);
                    if (event) {
                        yield event;
                    }
                    boundary = buffer.indexOf('\n\n');
                }
            }

            if (buffer.trim().length > 0) {
                const event = parseChatStreamEvent(buffer);
                if (event) {
                    yield event;
                }
            }

            // If we reached here, the stream finished successfully
            return;
        } catch (error) {
            attempts++;
            if (attempts >= maxRetries) {
                throw error;
            }
            const delay = Math.pow(2, attempts) * 1000;
            console.warn(`Stream failed, retrying in ${delay}ms (attempt ${attempts}):`, error);
            await new Promise(r => setTimeout(r, delay));
        }
    }
};

export const mapJobStatusToChatStatus = (value?: string | null): ChatActionStatus | null => {
    if (!value) return null;
    if (value === 'completed') return 'completed';
    if (value === 'queued') return 'pending';
    if (value === 'running') return 'running';
    if (value === 'succeeded') return 'completed';
    if (value === 'failed') return 'failed';
    return null;
};

export const buildActionsFromSteps = (steps: Array<Record<string, any>>): ChatActionSummary[] => {
    if (!Array.isArray(steps)) {
        return [];
    }
    return steps.map((step) => {
        const action = step?.action ?? {};
        const success = step?.success;
        const status =
            success === true ? 'completed' : success === false ? 'failed' : null;
        return {
            kind: action?.kind ?? null,
            name: action?.name ?? null,
            parameters: action?.parameters ?? null,
            order: action?.order ?? null,
            blocking: action?.blocking ?? null,
            status,
            success: success ?? null,
            message: step?.message ?? null,
            details: step?.details ?? null,
        };
    });
};

export const formatToolPlanPreface = (actions: ChatActionSummary[]): string => {
    const toolActions = (actions ?? []).filter((a) => a?.kind === 'tool_operation');
    if (!toolActions.length) {
        return '我将先调用工具获取信息，然后给出基于结果的回答。';
    }
    const names = toolActions
        .map((a) => (typeof a?.name === 'string' ? a.name : null))
        .filter(Boolean) as string[];
    const uniqueNames = Array.from(new Set(names));
    const label = uniqueNames.slice(0, 3).join(', ');
    const suffix = uniqueNames.length > 3 ? ` 等 ${uniqueNames.length} 个工具` : '';
    return `我将先调用工具（${label}${suffix}）获取最新信息，然后给出基于结果的回答。`;
};

export const summarizeSteps = (steps: Array<Record<string, any>>): string | null => {
    if (!Array.isArray(steps) || steps.length === 0) return null;
    const lines: string[] = [];
    steps.forEach((step, idx) => {
        const order = typeof step?.action?.order === 'number' ? step.action.order : idx + 1;
        const action = step?.action ?? {};
        const labelParts: string[] = [];
        if (typeof action?.kind === 'string') labelParts.push(action.kind);
        if (typeof action?.name === 'string') labelParts.push(action.name);
        const header = labelParts.length ? labelParts.join('/') : `步骤 ${order}`;

        const detail =
            (typeof step?.summary === 'string' && step.summary) ||
            (typeof (step as any)?.message === 'string' && (step as any).message) ||
            (step?.details && typeof step.details?.summary === 'string' ? step.details.summary : null);

        const subtasks =
            (step?.details && Array.isArray((step.details as any).subtasks)
                ? ((step.details as any).subtasks as any[])
                : []) ||
            (step?.details &&
                (step.details as any).result &&
                Array.isArray((step.details as any).result?.subtasks)
                ? ((step.details as any).result.subtasks as any[])
                : []);

        if (detail) {
            lines.push(`- ${header}: ${detail}`);
        } else {
            lines.push(`- ${header}`);
        }
        if (subtasks && subtasks.length > 0) {
            subtasks.forEach((st: any) => {
                const name =
                    (st && typeof st.name === 'string' && st.name) ||
                    (st && typeof st.title === 'string' && st.title) ||
                    null;
                if (name) {
                    lines.push(`  - 子任务: ${name}`);
                }
            });
        }
    });
    return lines.length ? lines.join('\n') : null;
};

export const convertRawActionToSummary = (act: any): ChatActionSummary => {
    return {
        kind: typeof act?.kind === 'string' ? act.kind : null,
        name: typeof act?.name === 'string' ? act.name : null,
        parameters: act?.parameters ?? null,
        order: typeof act?.order === 'number' ? act.order : null,
        blocking: typeof act?.blocking === 'boolean' ? act.blocking : null,
        status: null,
        success: null,
        message: typeof act?.message === 'string' ? act.message : null,
        details: act?.details ?? null,
    };
};

export const summarizeActions = (actions: ChatActionSummary[] | null | undefined): string | null => {
    if (!actions || actions.length === 0) return null;
    const lines: string[] = [];
    actions.forEach((act, idx) => {
        const order = typeof act.order === 'number' ? act.order : idx + 1;
        const labelParts: string[] = [];
        if (typeof act.kind === 'string') labelParts.push(act.kind);
        if (typeof act.name === 'string') labelParts.push(act.name);
        const header = labelParts.length ? labelParts.join('/') : `步骤 ${order}`;

        const params = (act.parameters ?? {}) as Record<string, any>;
        const hasMsg = typeof act.message === 'string' && act.message.trim().length > 0;
        const nameDetail = typeof params?.name === 'string' ? params.name : null;
        const instructionDetail =
            typeof params?.instruction === 'string' ? params.instruction : null;
        const detail = hasMsg
            ? act.message
            : instructionDetail
                ? nameDetail
                    ? `${nameDetail}: ${instructionDetail}`
                    : instructionDetail
                : nameDetail;

        if (detail) {
            lines.push(`- ${header}: ${detail}`);
        } else {
            lines.push(`- ${header}`);
        }
    });
    return lines.length ? lines.join('\n') : null;
};

export const summaryToChatSession = (summary: ChatSessionSummary): ChatSession => {
    const rawName = summary.name?.trim();
    const title =
        rawName ||
        (summary.plan_title && summary.plan_title.trim()) ||
        `会话 ${summary.id.slice(0, 8)}`;
    const titleSource =
        summary.name_source ??
        (rawName ? (summary.is_user_named ? 'user' : null) : null);
    const isUserNamed =
        summary.is_user_named === undefined || summary.is_user_named === null
            ? null
            : Boolean(summary.is_user_named);
    const createdAt = parseDate(summary.created_at) ?? new Date();
    const updatedAt = parseDate(summary.updated_at) ?? createdAt;
    const lastMessageAt = parseDate(summary.last_message_at);

    return {
        id: summary.id,
        title,
        messages: [],
        created_at: createdAt,
        updated_at: updatedAt,
        workflow_id: null,
        session_id: summary.id,
        plan_id: summary.plan_id ?? null,
        plan_title: summary.plan_title ?? null,
        current_task_id: summary.current_task_id ?? null,
        current_task_name: summary.current_task_name ?? null,
        last_message_at: lastMessageAt,
        is_active: summary.is_active,
        defaultSearchProvider: summary.settings?.default_search_provider ?? null,
        defaultBaseModel: summary.settings?.default_base_model ?? null,
        defaultLLMProvider: summary.settings?.default_llm_provider ?? null,
        titleSource,
        isUserNamed,
    };
};

export const derivePlanContextFromMessages = (
    messages: ChatMessage[]
): { planId: number | null | undefined; planTitle: string | null | undefined } => {
    let planId: number | null | undefined = undefined;
    let planTitle: string | null | undefined = undefined;

    for (let idx = messages.length - 1; idx >= 0; idx -= 1) {
        const metadata = messages[idx]?.metadata;
        if (!metadata) {
            continue;
        }

        if (planId === undefined && Object.prototype.hasOwnProperty.call(metadata, 'plan_id')) {
            const candidate = coercePlanId((metadata as any).plan_id);
            if (candidate !== undefined) {
                planId = candidate ?? null;
            }
        }

        if (planTitle === undefined && Object.prototype.hasOwnProperty.call(metadata, 'plan_title')) {
            const candidate = coercePlanTitle((metadata as any).plan_title);
            if (candidate !== undefined) {
                planTitle = candidate ?? null;
            }
        }

        if (planId !== undefined && planTitle !== undefined) {
            break;
        }
    }

    return { planId, planTitle };
};

export const buildToolResultsCache = (
    messages: ChatMessage[]
): Map<string, ToolResultPayload[]> => {
    const cache = new Map<string, ToolResultPayload[]>();
    for (const msg of messages) {
        if (msg.type !== 'assistant') {
            continue;
        }
        const metadata = msg.metadata as Record<string, any> | undefined;
        if (!metadata) {
            continue;
        }
        const trackingId =
            typeof metadata.tracking_id === 'string' ? metadata.tracking_id : null;
        if (!trackingId) {
            continue;
        }
        const toolResults = collectToolResultsFromMetadata(metadata.tool_results);
        if (toolResults.length > 0) {
            cache.set(trackingId, toolResults);
        }
    }
    return cache;
};

export const waitForActionCompletionViaStream = async (
    trackingId: string,
    timeoutMs: number = 120_000
): Promise<ActionStatusResponse | null> => {
    if (typeof EventSource === 'undefined') {
        return null;
    }

    return new Promise((resolve) => {
        let finished = false;
        // 统一使用通用 jobs SSE（GET），避免 chat/stream(POST) 中断后无法继续拿到终态
        const streamUrl = `${ENV.API_BASE_URL}/jobs/${trackingId}/stream`;
        const source = new EventSource(streamUrl);

        const finalize = async () => {
            if (finished) return;
            finished = true;
            source.close();
            try {
                const status = await chatApi.getActionStatus(trackingId);
                resolve(status);
            } catch (error) {
                console.warn('动作 SSE 获取最终状态失败:', error);
                resolve(null);
            }
        };

        const timeoutId = window.setTimeout(() => {
            if (finished) return;
            finished = true;
            source.close();
            resolve(null);
        }, timeoutMs);

        source.onmessage = (event) => {
            const payload = parseJobStreamPayload(event);
            if (!payload) return;
            const status = normalizeActionStatus(payload.status ?? payload.job?.status);
            if (status && (status === 'completed' || status === 'failed')) {
                window.clearTimeout(timeoutId);
                void finalize();
            }
        };

        source.onerror = () => {
            if (finished) return;
            window.clearTimeout(timeoutId);
            finished = true;
            source.close();
            resolve(null);
        };
    });
};

export const resolveHistoryCursor = (messages: ChatMessage[]): number | null => {
    let minId: number | null = null;
    for (const msg of messages) {
        const backendId = msg.metadata?.backend_id;
        if (typeof backendId === 'number' && Number.isFinite(backendId)) {
            minId = minId === null ? backendId : Math.min(minId, backendId);
            continue;
        }
        const match = msg.id.match(/_(\d+)$/);
        if (match) {
            const parsed = Number(match[1]);
            if (!Number.isNaN(parsed)) {
                minId = minId === null ? parsed : Math.min(minId, parsed);
            }
        }
    }
    return minId;
};

// Shared state for sessions and messages
export const pendingAutotitleSessions = new Set<string>();
export const autoTitleHistory = new Map<string, { planId: number | null }>();
export const activeActionFollowups = new Set<string>();
