import { chatApi } from '@api/chat';
import { SessionTaskSearch } from '@utils/taskSearch';
import type { ChatSession } from '@/types';
import { ENV } from '@/config/env';

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
  }
): Promise<ToolExecutionResult> {
  
  const searchResult = await SessionTaskSearch.searchCurrentSessionTasks(
    userInput,
    context.currentSession,
    context.currentWorkflowId
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
  }
): Promise<ToolExecutionResult> {
  
  try {
    // 🧠 前端不做任何文本处理，直接传递原始用户输入给后端
    // 后端LLM服务会智能提炼任务名称
    console.log('📤 传递原始用户输入给后端:', userInput);
    
    // 调用后端智能任务创建API - 后端会使用LLM提炼任务名称
    const response = await fetch(`${ENV.API_BASE_URL}/tasks/intelligent-create`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        user_input: userInput,  // 传递原始输入
        session_id: context.currentSession?.session_id || null,
        workflow_id: context.currentWorkflowId || null
      }),
    });

    const result = await response.json();
    
    // 调试信息
    console.log('任务创建API响应:', {
      status: response.status,
      ok: response.ok, 
      result: result
    });
    
    if (response.ok && result.id) {
      // 后端直接返回Task对象，包含id字段
      return {
        handled: true,
        response: `✅ **任务创建成功！**\n\n📋 **任务详情:**\n• **名称**: ${result.name}\n• **ID**: ${result.id}\n• **状态**: ${result.status}\n• **优先级**: ${result.priority === 1 ? '高' : result.priority === 2 ? '中' : '低'}\n• **会话ID**: ${result.session_id || '无'}\n\n🎯 任务已加入您的待办列表，可以随时查看或管理。`,
        metadata: {
          action: 'create_task',
          success: true,
          task_id: result.id,
          task_name: result.name
        }
      };
    } else {
      // 正确提取错误信息
      let errorMsg = '未知错误';
      if (result.error) {
        if (typeof result.error === 'string') {
          errorMsg = result.error;
        } else if (typeof result.error === 'object' && result.error.message) {
          errorMsg = result.error.message;
        } else if (typeof result.error === 'object' && result.error.detail) {
          errorMsg = result.error.detail;
        } else {
          errorMsg = JSON.stringify(result.error);
        }
      } else if (result.detail) {
        errorMsg = result.detail;
      } else if (result.message) {
        errorMsg = result.message;
      }
      
      return {
        handled: true,
        response: `❌ **任务创建失败**\n\n🚫 错误信息: ${errorMsg}\n\n💡 请检查输入格式或重试。`,
        metadata: {
          action: 'create_task',
          success: false,
          error: errorMsg
        }
      };
    }
    
  } catch (error) {
    console.error('任务创建失败:', error);
    return {
      handled: true,
      response: `❌ **任务创建失败**\n\n🚫 网络或服务器错误: ${error}\n\n💡 请稍后重试或检查网络连接。`,
      metadata: {
        action: 'create_task',
        success: false,
        error: String(error)
      }
    };
  }
}

/**
 * 执行系统状态查询工具
 */
async function executeSystemStatus(): Promise<ToolExecutionResult> {
  
  try {
    const status = await chatApi.getSystemStatus();
    
    const response = `📊 **系统状态报告**

🏥 **系统健康**: ${status.system_health === 'good' ? '✅ 良好' : 
                   status.system_health === 'warning' ? '⚠️ 警告' : '❌ 异常'}

📋 **活跃任务**: ${status.active_tasks} 个
📑 **待处理计划**: ${status.pending_plans} 个`;

    return {
      handled: true,
      response,
      metadata: {
        system_health: status.system_health,
        active_tasks: status.active_tasks
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
async function selectTargetTaskWithLLM(userInput: string, tasks: any[]): Promise<any | null> {
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
function selectDefaultTask(tasks: any[]): any | null {
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
  },
  analysis: any
): Promise<ToolExecutionResult> {
  
  try {
    console.log('🔧 任务拆分请求:', userInput, context);
    
    // 获取当前会话的最新任务
    const sessionId = context.currentSession?.session_id;
    if (!sessionId) {
      return {
        handled: true,
        response: `❌ **任务拆分失败**\n\n🚫 未找到当前会话信息，无法确定要拆分的任务。\n\n💡 请先创建一个ROOT任务，然后再进行拆分。`,
        metadata: {
          action: 'task_decompose',
          success: false,
          error: 'No session context'
        }
      };
    }
    
    // 查询当前会话的任务列表，找到最新的ROOT任务
    const tasksResponse = await fetch(`${ENV.API_BASE_URL}/tasks?session_id=${sessionId}`);
    
    if (!tasksResponse.ok) {
      throw new Error(`任务查询失败: ${tasksResponse.status}`);
    }
    
    const tasks = await tasksResponse.json();
    console.log('🔍 当前会话任务列表:', tasks);
    
    // 🧠 使用LLM智能选择目标任务（科研项目要求：零关键词匹配）
    const targetTask = await selectTargetTaskWithLLM(userInput, tasks);
    
    if (!targetTask) {
      return {
        handled: true,
        response: `❌ **任务拆分失败**\n\n🚫 当前会话中未找到可拆分的任务。\n\n💡 请先创建一个ROOT任务，或明确指定要拆分的任务。`,
        metadata: {
          action: 'task_decompose',
          success: false,
          error: 'No suitable task found'
        }
      };
    }
    
    console.log('🎯 LLM选择的目标任务:', targetTask);
    
    // 调用后端的真实任务拆分服务
    const decompositionResult = await performRealTaskDecomposition(targetTask, userInput, sessionId);
    
    return {
      handled: true,
      response: decompositionResult.response,
      metadata: {
        action: 'task_decompose',
        success: true,
        target_task_id: targetTask.id,
        target_task_name: targetTask.name,
        composite_tasks: decompositionResult.compositeTasks
      }
    };
    
  } catch (error) {
    console.error('任务拆分失败:', error);
    return {
      handled: true,
      response: `❌ **任务拆分失败**\n\n🚫 系统错误: ${error}\n\n💡 请稍后重试或检查网络连接。`,
      metadata: {
        action: 'task_decompose',
        success: false,
        error: String(error)
      }
    };
  }
}

/**
 * 真实的任务拆分 - 调用后端LLM服务并创建实际的COMPOSITE任务
 */
async function performRealTaskDecomposition(rootTask: any, userRequest: string, sessionId: string): Promise<{
  response: string;
  compositeTasks: any[];
}> {
  
  try {
    console.log('🧠 开始真实任务拆分...', rootTask);
    
    // 调用后端任务分解API
    const decompositionResponse = await fetch(`${ENV.API_BASE_URL}/tasks/${rootTask.id}/decompose`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        max_subtasks: 4,
        force: false,
        tool_aware: true
      }),
    });

    if (!decompositionResponse.ok) {
      throw new Error(`分解服务调用失败: ${decompositionResponse.status}`);
    }

    const decompositionData = await decompositionResponse.json();
    console.log('🔄 分解服务响应:', decompositionData);

    // 检查分解是否成功
    if (!decompositionData.success) {
      throw new Error(decompositionData.error || 'LLM分解服务返回失败状态');
    }

    // 后端已经创建了子任务，直接获取创建的任务信息
    const createdTasks = decompositionData.subtasks || [];
    
    if (createdTasks.length === 0) {
      throw new Error('LLM分解服务未创建任何子任务');
    }

    // 生成成功响应
    const responseText = `🧠 **LLM智能任务拆分完成** 

📋 **原ROOT任务**: ${rootTask.name} (ID: ${rootTask.id})

🔄 **LLM已创建${createdTasks.length}个子任务**:
${createdTasks.map((task, i) => `${i+1}. 📦 **${task.name}** (ID: ${task.id}) [${task.task_type?.toUpperCase()}]`).join('\n')}

⚡ **任务已写入全局上下文**，形成完整的任务DAG:
• 继续拆分COMPOSITE任务为ATOMIC任务
• 查看任务层次结构和依赖关系
• 开始执行具体的ATOMIC任务

💡 试试说"拆分第1个COMPOSITE任务"进行进一步细化。`;

    return {
      response: responseText,
      compositeTasks: createdTasks
    };

  } catch (error) {
    console.error('真实任务拆分失败:', error);
    
    // 科研项目要求：不允许任何回退机制，直接报告LLM服务失败
    throw new Error(`LLM分解服务不可用: ${error}. 科研项目要求使用真实LLM服务，不接受简化方案。`);
  }
}

