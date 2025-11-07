import { chatApi } from '@api/chat';
import { planTreeApi } from '@api/planTree';
import type { DecomposeTaskPayload } from '@api/planTree';
import type { DecompositionJobStatus } from '@/types';
import { SessionTaskSearch } from '@utils/taskSearch';
import { planTreeToTasks } from '@utils/planTree';
import { ENV } from '@/config/env';
import type { ChatSession, Task } from '@/types';

// 意图分析结果接口
export interface IntentAnalysisResult {
  needsToolCall: boolean;
  toolType?: string;
  confidence: number;
  reasoning: string;
  extractedParams?: Record<string, any>;
}

// 工具执行结果接口
export interface ToolExecutionResult {
  handled: boolean;
  response: string;
  metadata?: Record<string, any>;
}

/**
 * 智能意图分析 - 让LLM判断用户意图并决定是否需要工具调用
 */
export async function analyzeUserIntent(
  userInput: string, 
  context: {
    currentSession?: ChatSession | null;
    currentWorkflowId?: string | null;
    recentMessages?: Array<{role: string; content: string; timestamp: string}>;
  }
): Promise<IntentAnalysisResult> {
  
  const analysisPrompt = `你是一个智能助手，需要分析用户的输入意图，判断是否需要调用工具。

用户输入："""${userInput}"""

上下文信息：
- 当前会话ID：${context.currentSession?.session_id || '无'}
- 当前工作流ID：${context.currentWorkflowId || '无'}
- 最近对话：${context.recentMessages?.map(m => `${m.role}: ${m.content}`).join('\n') || '无'}

可用的工具类型：
1. task_search - 搜索当前工作空间的任务
2. task_create - 创建全新的ROOT任务
3. task_decompose - 对现有任务进行智能拆分（ROOT→COMPOSITE→ATOMIC）
4. system_status - 查看系统状态
5. general_chat - 普通对话，无需工具

请分析用户意图并返回JSON格式：
{
  "needsToolCall": boolean, // 是否需要调用工具
  "toolType": string, // 需要的工具类型（如果needsToolCall为true）
  "confidence": number, // 置信度 0-1
  "reasoning": string, // 判断理由
  "extractedParams": {} // 提取的参数
}

🧠 智能分析原则（重要！请仔细理解上下文）：
- 如果用户想查看、搜索、列出当前的任务 → task_search
- 如果用户想创建**全新的任务**（没有现有任务背景） → task_create  
- 如果用户想对**已存在的任务**进行拆分、分解、细化 → task_decompose
  * 关键词：拆分、分解、细化、展开、详细计划、子任务
  * 上下文：如果最近创建了任务，用户要求拆分，必须是task_decompose
- 如果用户询问系统状态、健康状况 → system_status
- 其他情况 → general_chat

⚠️ 特别注意上下文理解：
- 如果对话中刚创建了任务，用户说"拆分"、"分解"等，一定是task_decompose而不是task_create

只返回JSON，不要其他内容：`;

  try {
    console.log('🧠 发送意图分析请求...');
    
    const response = await chatApi.sendMessage(analysisPrompt, {
      mode: 'analyzer',
      workflow_id: context.currentWorkflowId,
      session_id: context.currentSession?.session_id,
      // 🔒 标记这是内部分析请求，避免创建工作流程
      metadata: {
        internal_analysis: true,
        original_user_input: userInput
      }
    });
    
    console.log('🧠 LLM原始分析响应:', response.response);
    
    // 解析LLM的JSON响应
    const jsonMatch = response.response.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      console.warn('🧠 无法解析LLM响应为JSON，使用默认值');
      return {
        needsToolCall: false,
        confidence: 0.1,
        reasoning: '无法解析LLM响应',
        toolType: 'general_chat'
      };
    }
    
    const result = JSON.parse(jsonMatch[0]);
    console.log('🧠 解析后的意图分析:', result);
    
    return {
      needsToolCall: result.needsToolCall || false,
      toolType: result.toolType || 'general_chat',
      confidence: result.confidence || 0.5,
      reasoning: result.reasoning || '自动分析',
      extractedParams: result.extractedParams || {}
    };
    
  } catch (error) {
    console.error('🧠 意图分析失败:', error);
    // 失败时默认为普通对话
    return {
      needsToolCall: false,
      confidence: 0.1,
      reasoning: `分析失败: ${error}`,
      toolType: 'general_chat'
    };
  }
}

/**
 * 基于意图执行相应的工具
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
  
  console.log(`🔧 执行工具: ${intent.toolType}`, intent);
  
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
    console.error(`🔧 工具执行失败 (${intent.toolType}):`, error);
    return {
      handled: false,
      response: `工具执行出错: ${error}`
    };
  }
}

/**
 * 执行任务搜索工具
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
 * 执行任务创建工具
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
      'ℹ️ 目前请直接告诉助手要创建的任务或计划，我会通过对话流完成操作。',
    metadata: {
      action: 'create_task',
      success: false,
    },
  };
}

/**
 * 执行系统状态查询工具
 */
async function executeSystemStatus(): Promise<ToolExecutionResult> {
  
  try {
    const response = await fetch(`${ENV.API_BASE_URL}/system/health`);
    if (!response.ok) {
      throw new Error(`system/health ${response.status}`);
    }
    const status = await response.json();

    const summary = `📊 **系统状态报告**\n\n🏥 **系统健康**: ${status.overall_status === 'healthy' ? '✅ 良好' :
      status.overall_status === 'degraded' ? '⚠️ 警告' : '❌ 异常'}\n\n` +
      `📦 组件数: ${(status.components && Object.keys(status.components).length) || 0}\n` +
      `💡 建议: ${(status.recommendations || []).join('；') || '暂无'}`;

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
      response: `❌ 获取系统状态失败: ${error}`,
      metadata: {
        error: String(error)
      }
    };
  }
}

/**
 * 🧠 使用LLM智能选择目标任务 - 科研项目要求：完全基于语义理解
 */
async function selectTargetTaskWithLLM(userInput: string, tasks: Task[]): Promise<Task | null> {
  try {
    if (!tasks || tasks.length === 0) {
      return null;
    }
    
    // 构建任务列表描述
    const taskDescriptions = tasks.map((task, index) => {
      const typeLabel = task.task_type === 'root' ? 'ROOT' : 
                       task.task_type === 'composite' ? 'COMPOSITE' : 'ATOMIC';
      return `[${index + 1}] ID: ${task.id}, 名称: "${task.name}", 类型: ${typeLabel}, 深度: ${task.depth}`;
    }).join('\n');
    
    // 🧠 使用LLM分析用户意图
    const prompt = `分析用户想要拆分哪个任务。

用户输入: "${userInput}"

当前任务列表:
${taskDescriptions}

任务拆分规则:
- ROOT任务（深度0）可以拆分为多个COMPOSITE任务（深度1）
- COMPOSITE任务（深度1）可以拆分为多个ATOMIC任务（深度2）
- ATOMIC任务（深度2）是最小单元，不能再拆分

分析用户意图，返回JSON格式（只返回JSON，不要任何解释）:
{
  "target_task_id": <任务ID>,
  "reasoning": "<为什么选择这个任务>"
}

如果用户没有明确指定，默认选择：
1. 如果有ROOT任务且没有子任务 → 选择ROOT任务
2. 如果ROOT已拆分，有未拆分的COMPOSITE任务 → 选择第一个COMPOSITE任务
3. 如果用户说"第N个"，选择对应序号的任务`;

    const response = await chatApi.sendMessage(prompt, { mode: 'assistant' });
    console.log('🧠 LLM任务选择响应:', response);
    
    // 解析LLM响应
    try {
      const match = response.response.match(/\{[\s\S]*\}/);
      if (!match) {
        console.warn('⚠️ LLM未返回有效JSON，使用默认策略');
        return selectDefaultTask(tasks);
      }
      
      const result = JSON.parse(match[0]);
      const targetTaskId = result.target_task_id;
      
      // 查找对应的任务
      const targetTask = tasks.find(t => t.id === targetTaskId);
      if (targetTask) {
        console.log(`✅ LLM选择任务: ${targetTask.name} (ID: ${targetTask.id})`);
        return targetTask;
      }
    } catch (parseError) {
      console.warn('⚠️ 解析LLM响应失败，使用默认策略:', parseError);
    }
    
    // 如果LLM选择失败，使用默认策略
    return selectDefaultTask(tasks);
    
  } catch (error) {
    console.error('❌ LLM任务选择失败:', error);
    return selectDefaultTask(tasks);
  }
}

/**
 * 默认任务选择策略（当LLM失败时的降级方案）
 */
function selectDefaultTask(tasks: Task[]): Task | null {
  // 优先选择ROOT任务（如果没有子任务）
  const rootTasks = tasks.filter(t => t.task_type === 'root' && !t.parent_id);
  if (rootTasks.length > 0) {
    const rootTask = rootTasks[rootTasks.length - 1];
    // 检查是否有子任务
    const hasChildren = tasks.some(t => t.parent_id === rootTask.id);
    if (!hasChildren) {
      return rootTask;
    }
  }
  
  // 选择第一个没有子任务的COMPOSITE任务
  const compositeTasks = tasks.filter(t => t.task_type === 'composite');
  for (const composite of compositeTasks) {
    const hasChildren = tasks.some(t => t.parent_id === composite.id);
    if (!hasChildren) {
      return composite;
    }
  }
  
  // 如果都有子任务，返回最新的ROOT任务
  return rootTasks.length > 0 ? rootTasks[rootTasks.length - 1] : null;
}

/**
 * 执行任务拆分工具 - 智能分解现有任务
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
        '❌ **任务拆分失败**\n\n🚫 当前会话尚未绑定具体的计划，无法定位要拆分的节点。',
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
          '❌ **任务拆分失败**\n\n🚫 未找到可拆分的目标任务。请先确认已有 ROOT 或 COMPOSITE 任务。',
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
    const responseText = `🧠 **任务拆分已启动**\n\n📋 目标任务: ${targetTask.name} (ID: ${targetTask.id})\n⏱️ 已提交后台执行，正在生成子任务。\n请留意下方实时日志面板以获取最新进度。`;

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
    console.error('任务拆分失败:', error);
    return {
      handled: true,
      response: `❌ **任务拆分失败**\n\n🚫 系统错误: ${error}`,
      metadata: {
        action: 'task_decompose',
        success: false,
        error: String(error),
      },
    };
  }
}
