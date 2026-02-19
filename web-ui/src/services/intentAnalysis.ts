import { chatApi } from '@api/chat';
import { planTreeApi } from '@api/planTree';
import type { DecomposeTaskPayload } from '@api/planTree';
import type { DecompositionJobStatus } from '@/types';
import { SessionTaskSearch } from '@utils/taskSearch';
import { planTreeToTasks } from '@utils/planTree';
import { ENV } from '@/config/env';
import type { ChatSession, Task } from '@/types';

// Intent analysis result interface
export interface IntentAnalysisResult {
  needsToolCall: boolean;
  toolType?: string;
  confidence: number;
  reasoning: string;
  extractedParams?: Record<string, any>;
}

// Tool execution result interface
export interface ToolExecutionResult {
  handled: boolean;
  response: string;
  metadata?: Record<string, any>;
}

/**
 * Smart intent analysis: let the LLM decide whether a tool call is needed.
 */
export async function analyzeUserIntent(
  userInput: string, 
  context: {
    currentSession?: ChatSession | null;
    currentWorkflowId?: string | null;
    recentMessages?: Array<{role: string; content: string; timestamp: string}>;
  }
): Promise<IntentAnalysisResult> {
  
  const analysisPrompt = `You are an intelligent assistant. Analyze the user's intent and decide whether a tool call is needed.

User input: """${userInput}"""

Context:
- Current session ID: ${context.currentSession?.session_id || 'none'}
- Current workflow ID: ${context.currentWorkflowId || 'none'}
- Recent conversation: ${context.recentMessages?.map(m => `${m.role}: ${m.content}`).join('\n') || 'none'}

Available tool types:
1. task_search - Search tasks in the current workspace
2. task_create - Create a brand-new ROOT task
3. task_decompose - Intelligently decompose an existing task (ROOT→COMPOSITE→ATOMIC)
4. system_status - Check system status
5. general_chat - Normal conversation without tools

Analyze user intent and return JSON:
{
  "needsToolCall": boolean, // whether a tool call is required
  "toolType": string, // tool type to use (if needsToolCall is true)
  "confidence": number, // confidence score 0-1
  "reasoning": string, // reasoning
  "extractedParams": {} // extracted parameters
}

Intent rules (important; use context carefully):
- If the user wants to view/search/list current tasks -> task_search
- If the user wants to create a brand-new task (no existing task context) -> task_create
- If the user wants to split/decompose/refine an existing task -> task_decompose
  * Keywords: split, decompose, refine, expand, detailed plan, subtasks
  * Context rule: if a task was just created and user asks to decompose, it must be task_decompose
- If the user asks about system status/health -> system_status
- Otherwise -> general_chat

Special context rule:
- If a task was just created and the user says "split" or "decompose", select task_decompose rather than task_create

Return JSON only. Do not output any extra text:`;

  try {
    console.log('🧠 Sending intent analysis request...');
    
    const response = await chatApi.sendMessage(analysisPrompt, {
      mode: 'analyzer',
      workflow_id: context.currentWorkflowId,
      session_id: context.currentSession?.session_id,
      // 🔒 Mark as internal analysis request to avoid creating workflows.
      metadata: {
        internal_analysis: true,
        original_user_input: userInput
      }
    });
    
    console.log('🧠 Raw LLM analysis response:', response.response);
    
    // Parse JSON from the LLM response.
    const jsonMatch = response.response.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      console.warn('🧠 Failed to parse LLM response as JSON; falling back to default.');
      return {
        needsToolCall: false,
        confidence: 0.1,
        reasoning: 'Failed to parse LLM response',
        toolType: 'general_chat'
      };
    }

    const result = JSON.parse(jsonMatch[0]);
    console.log('🧠 Parsed intent analysis:', result);
    
    return {
      needsToolCall: result.needsToolCall || false,
      toolType: result.toolType || 'general_chat',
      confidence: result.confidence || 0.5,
      reasoning: result.reasoning || 'auto-analysis',
      extractedParams: result.extractedParams || {}
    };
    
  } catch (error) {
    console.error('🧠 Intent analysis failed:', error);
    // Fallback to general chat on failure.
    return {
      needsToolCall: false,
      confidence: 0.1,
      reasoning: `Analysis failed: ${error}`,
      toolType: 'general_chat'
    };
  }
}

/**
 * Execute the corresponding tool based on intent.
 */
export async function executeToolBasedOnIntent(
  intent: IntentAnalysisResult,
  context: {
    currentSession?: ChatSession | null;
    currentWorkflowId?: string | null;
    currentPlanId?: number | null;
    userInput: string;
  }
): Promise<ToolExecutionResult> {
  
  console.log(`🔧 Executing tool: ${intent.toolType}`, intent);
  
  try {
    switch (intent.toolType) {
      case 'task_create':
        return await executeTaskCreate(context.userInput, context);
      case 'task_search':
        return await executeTaskSearch(context.userInput, context);
      case 'task_decompose':
        return await executeTaskDecompose(context.userInput, context, intent);
      case 'system_status':
        return await executeSystemStatus();
      default:
        return {
          handled: false,
          response: '',
          metadata: { needsToolCall: false }
        };
    }
  } catch (error) {
    console.error(`🔧 Tool execution failed (${intent.toolType}):`, error);
    return {
      handled: false,
      response: `Tool execution error: ${error}`
    };
  }
}

/**
 * Execute task search tool.
 */
async function executeTaskSearch(
  userInput: string,
  context: {
    currentSession?: ChatSession | null;
    currentWorkflowId?: string | null;
    currentPlanId?: number | null;
  }
): Promise<ToolExecutionResult> {

  const searchResult = await SessionTaskSearch.searchCurrentSessionTasks(
    userInput,
    context.currentSession,
    context.currentWorkflowId,
    context.currentPlanId
  );
  
  const response = SessionTaskSearch.formatSearchResults(
    searchResult.tasks,
    searchResult.summary
  );
  
  return {
    handled: true,
    response,
    metadata: {
      tasks_found: searchResult.total,
      search_query: userInput
    }
  };
}

/**
 * Execute task creation tool.
 */
async function executeTaskCreate(
  userInput: string,
  context: {
    currentSession?: ChatSession | null;
    currentWorkflowId?: string | null;
    currentPlanId?: number | null;
  }
): Promise<ToolExecutionResult> {
  return {
    handled: true,
    response:
      'ℹ️ Please directly tell the assistant which task or plan to create; I will complete it through the chat flow.',
    metadata: {
      action: 'create_task',
      success: false,
    },
  };
}

/**
 * Execute system status query tool.
 */
async function executeSystemStatus(): Promise<ToolExecutionResult> {
  
  try {
    const response = await fetch(`${ENV.API_BASE_URL}/system/health`);
    if (!response.ok) {
      throw new Error(`system/health ${response.status}`);
    }
    const status = await response.json();

    const summary = `📊 **System Status Report**\n\n🏥 **System Health**: ${status.overall_status === 'healthy' ? '✅ Good' :
      status.overall_status === 'degraded' ? '⚠️ Warning' : '❌ Error'}\n\n` +
      `📦 Components: ${(status.components && Object.keys(status.components).length) || 0}\n` +
      `💡 Recommendations: ${(status.recommendations || []).join('; ') || 'None'}`;

    return {
      handled: true,
      response: summary,
      metadata: {
        system_health: status.overall_status,
        components: status.components,
      }
    };
  } catch (error) {
    return {
      handled: true,
      response: `❌ Failed to fetch system status: ${error}`,
      metadata: {
        error: String(error)
      }
    };
  }
}

/**
 * 🧠 Use LLM to intelligently select the target task.
 */
async function selectTargetTaskWithLLM(userInput: string, tasks: Task[]): Promise<Task | null> {
  try {
    if (!tasks || tasks.length === 0) {
      return null;
    }
    
    // Build task list description.
    const taskDescriptions = tasks.map((task, index) => {
      const typeLabel = task.task_type === 'root' ? 'ROOT' : 
                       task.task_type === 'composite' ? 'COMPOSITE' : 'ATOMIC';
      return `[${index + 1}] ID: ${task.id}, Name: "${task.name}", Type: ${typeLabel}, Depth: ${task.depth}`;
    }).join('\n');
    
    // 🧠 Use LLM to analyze user intent.
    const prompt = `Analyze which task the user wants to decompose.

User input: "${userInput}"

Current task list:
${taskDescriptions}

Task decomposition rules:
- ROOT task (depth 0) can be decomposed into multiple COMPOSITE tasks (depth 1)
- COMPOSITE task (depth 1) can be decomposed into multiple ATOMIC tasks (depth 2)
- ATOMIC task (depth 2) is the smallest unit and cannot be decomposed further

Analyze intent and return JSON (JSON only, no explanation):
{
  "target_task_id": <task ID>,
  "reasoning": "<why this task is selected>"
}

If user does not specify clearly, default selection is:
1. If there is a ROOT task without children -> select the ROOT task
2. If ROOT has been decomposed and there are undecomposed COMPOSITE tasks -> select the first COMPOSITE task
3. If user says "the Nth one", select the corresponding indexed task`;

    const response = await chatApi.sendMessage(prompt, { mode: 'assistant' });
    console.log('🧠 LLM task-selection response:', response);
    
    // Parse LLM response.
    try {
      const match = response.response.match(/\{[\s\S]*\}/);
      if (!match) {
        console.warn('⚠️ LLM did not return valid JSON; using default strategy.');
        return selectDefaultTask(tasks);
      }
      
      const result = JSON.parse(match[0]);
      const targetTaskId = result.target_task_id;
      
      // Find the matching task.
      const targetTask = tasks.find(t => t.id === targetTaskId);
      if (targetTask) {
        console.log(`✅ LLM selected task: ${targetTask.name} (ID: ${targetTask.id})`);
        return targetTask;
      }
    } catch (parseError) {
      console.warn('⚠️ Failed to parse LLM response; using default strategy:', parseError);
    }
    
    // Use default strategy if LLM selection fails.
    return selectDefaultTask(tasks);
    
  } catch (error) {
    console.error('❌ LLM task selection failed:', error);
    return selectDefaultTask(tasks);
  }
}

/**
 * Default task selection strategy (fallback when LLM fails).
 */
function selectDefaultTask(tasks: Task[]): Task | null {
  // Prefer ROOT task when it has no children.
  const rootTasks = tasks.filter(t => t.task_type === 'root' && !t.parent_id);
  if (rootTasks.length > 0) {
    const rootTask = rootTasks[rootTasks.length - 1];
    // Check whether it has child tasks.
    const hasChildren = tasks.some(t => t.parent_id === rootTask.id);
    if (!hasChildren) {
      return rootTask;
    }
  }
  
  // Select the first COMPOSITE task without children.
  const compositeTasks = tasks.filter(t => t.task_type === 'composite');
  for (const composite of compositeTasks) {
    const hasChildren = tasks.some(t => t.parent_id === composite.id);
    if (!hasChildren) {
      return composite;
    }
  }
  
  // If all have children, return the latest ROOT task.
  return rootTasks.length > 0 ? rootTasks[rootTasks.length - 1] : null;
}

/**
 * Execute task decomposition tool for existing tasks.
 */
async function executeTaskDecompose(
  userInput: string,
  context: {
    currentSession?: ChatSession | null;
    currentWorkflowId?: string | null;
    currentPlanId?: number | null;
  },
  analysis: any
): Promise<ToolExecutionResult> {
  const planId = context.currentPlanId;
  if (!planId) {
    return {
      handled: true,
      response:
        '❌ **Task decomposition failed**\n\n🚫 The current session is not bound to a specific plan, so the target node cannot be located.',
      metadata: {
        action: 'task_decompose',
        success: false,
        error: 'missing_plan_id',
      },
    };
  }

  try {
    const tree = await planTreeApi.getPlanTree(planId);
    const tasks = planTreeToTasks(tree);
    const targetTask = await selectTargetTaskWithLLM(userInput, tasks);

    if (!targetTask) {
      return {
        handled: true,
        response:
          '❌ **Task decomposition failed**\n\n🚫 No decomposable target task was found. Please make sure there is an existing ROOT or COMPOSITE task.',
        metadata: {
          action: 'task_decompose',
          success: false,
          error: 'no_target_task',
        },
      };
    }

    const payload: DecomposeTaskPayload = {
      plan_id: planId,
      async_mode: true,
    };

    if (typeof analysis?.extractedParams?.expand_depth === 'number') {
      payload.expand_depth = analysis.extractedParams.expand_depth;
    }
    if (typeof analysis?.extractedParams?.node_budget === 'number') {
      payload.node_budget = analysis.extractedParams.node_budget;
    }
    if (typeof analysis?.extractedParams?.allow_existing_children === 'boolean') {
      payload.allow_existing_children = analysis.extractedParams.allow_existing_children;
    }

    const decomposition = await planTreeApi.decomposeTask(targetTask.id, payload);

    const jobInfo: DecompositionJobStatus | null = decomposition.job || null;
    const jobId = jobInfo?.job_id ?? decomposition.result?.job_id ?? null;
    const responseText = `🧠 **Task decomposition started**\n\n📋 Target task: ${targetTask.name} (ID: ${targetTask.id})\n⏱️ Submitted to background execution, generating subtasks.\nPlease watch the real-time log panel below for updates.`;

    return {
      handled: true,
      response: responseText,
      metadata: {
        action: 'task_decompose',
        success: true,
        target_task_id: targetTask.id,
        target_task_name: targetTask.name,
        plan_id: planId,
        type: 'job_log',
        job_id: jobId,
        job_status: jobInfo?.status ?? 'queued',
        job: jobInfo,
        job_logs: jobInfo?.logs ?? [],
      },
    };
  } catch (error) {
    console.error('Task decomposition failed:', error);
    return {
      handled: true,
      response: `❌ **Task decomposition failed**\n\n🚫 System error: ${error}`,
      metadata: {
        action: 'task_decompose',
        success: false,
        error: String(error),
      },
    };
  }
}
