import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import type { Task } from '@/types';

/**
 * 会话级任务搜索工具 - 实现"专事专办"
 * 只搜索当前会话/工作流相关的任务
 */
export class SessionTaskSearch {
  /**
   * 搜索当前会话的任务
   */
  static async searchCurrentSessionTasks(
    query: string,
    currentSession?: { session_id?: string | null },
    currentWorkflowId?: string | null,
    currentPlanId?: number | null
  ): Promise<{
    tasks: Task[];
    total: number;
    summary: string;
  }> {
    try {
      // 构建搜索作用域
      const scope = {
        session_id: currentSession?.session_id || undefined,
        workflow_id: currentWorkflowId || undefined,
      };

      console.log('🔍 TaskSearch - 详细搜索参数:', {
        查询内容: query,
        作用域: scope,
        当前会话对象: currentSession,
        会话ID: currentSession?.session_id,
        工作流ID: currentWorkflowId,
        是否有会话信息: !!(scope.session_id || scope.workflow_id)
      });

      if (!currentPlanId) {
        console.warn('🔒 TaskSearch - 缺少 planId，返回空结果');
        return {
          tasks: [],
          total: 0,
          summary: '🔒 当前会话未绑定计划，无法搜索'
        };
      }

      const tree = await planTreeApi.getPlanTree(currentPlanId);
      const allTasks = planTreeToTasks(tree);
      console.log('🔍 TaskSearch - 获取到的所有任务:', allTasks.length, '条');

      const tasks = allTasks.filter(task => 
        task.name.toLowerCase().includes(query.toLowerCase()) ||
        (task.task_type && task.task_type.toLowerCase().includes(query.toLowerCase()))
      );
      
      console.log('🔍 TaskSearch - 过滤后任务:', tasks.length, '条');
      
      // 生成搜索摘要
      const total = tasks.length;
      const summary = total > 0 
        ? `🎯 当前工作空间找到 ${total} 条相关任务：`
        : '🔍 当前工作空间未找到相关任务';

      return {
        tasks,
        total,
        summary
      };
    } catch (error) {
      console.error('Session task search failed:', error);
      return {
        tasks: [],
        total: 0,
        summary: '❌ 任务搜索失败，请稍后重试'
      };
    }
  }

  /**
   * 格式化搜索结果为聊天显示
   */
  static formatSearchResults(
    tasks: Task[],
    summary: string
  ): string {
    if (tasks.length === 0) {
      return summary;
    }

    const taskList = tasks
      .map((task, index) => 
        `${index + 1}. ${task.name} (${task.status})`
      )
      .join('\n');

    return `${summary}\n${taskList}`;
  }

  /**
   * 获取当前ROOT任务信息
   */
  static async getCurrentRootTask(
    currentSession?: { session_id?: string | null },
    currentWorkflowId?: string | null,
    currentPlanId?: number | null
  ): Promise<Task | null> {
    try {
      if (!currentPlanId) {
        return null;
      }

      const tree = await planTreeApi.getPlanTree(currentPlanId);
      const allTasks = planTreeToTasks(tree);

      const typedRoot = allTasks.find((t) => t.task_type === 'root');
      if (typedRoot) return typedRoot;

      const topLevelRoot = allTasks.find((t) => t.parent_id == null);
      return topLevelRoot || null;

    } catch (error) {
      console.error('Get current root task failed:', error);
      return null;
    }
  }
}
