import { planTreeApi } from '@api/planTree';
import { planTreeToTasks } from '@utils/planTree';
import type { Task } from '@/types';

/**
 * Session-scoped task search helpers.
 */
export class SessionTaskSearch {
  /**
   * Search tasks in the currently bound plan for the active session/workflow.
   */
  static async searchCurrentSessionTasks(
    query: string,
    currentSession?: { session_id?: string | null },
    currentWorkflowId?: string | null,
    currentPlanId?: number | null,
  ): Promise<{
    tasks: Task[];
    total: number;
    summary: string;
  }> {
    try {
      const scope = {
        session_id: currentSession?.session_id || undefined,
        workflow_id: currentWorkflowId || undefined,
      };

      console.log('🔍 TaskSearch - search parameters:', {
        query,
        scope,
        session: currentSession,
        sessionId: currentSession?.session_id,
        workflowId: currentWorkflowId,
        hasSessionScope: !!(scope.session_id || scope.workflow_id),
      });

      if (!currentPlanId) {
        console.warn('🔒 TaskSearch - no plan bound to current session; returning empty results');
        return {
          tasks: [],
          total: 0,
          summary: '🔒 No plan is bound to this session, so task search is unavailable.',
        };
      }

      const tree = await planTreeApi.getPlanTree(currentPlanId);
      const allTasks = planTreeToTasks(tree);
      console.log('🔍 TaskSearch - loaded tasks:', allTasks.length);

      const normalizedQuery = query.toLowerCase();
      const tasks = allTasks.filter(
        (task) =>
          task.name.toLowerCase().includes(normalizedQuery) ||
          (task.task_type && task.task_type.toLowerCase().includes(normalizedQuery)),
      );

      console.log('🔍 TaskSearch - matched tasks:', tasks.length);

      const total = tasks.length;
      const summary = total > 0 ? `🎯 Found ${total} related task(s):` : '🔍 No related tasks found.';

      return {
        tasks,
        total,
        summary,
      };
    } catch (error) {
      console.error('Session task search failed:', error);
      return {
        tasks: [],
        total: 0,
        summary: '❌ Task search failed. Please try again.',
      };
    }
  }

  /**
   * Render task search results as plain text.
   */
  static formatSearchResults(tasks: Task[], summary: string): string {
    if (tasks.length === 0) {
      return summary;
    }

    const taskList = tasks
      .map((task, index) => `${index + 1}. ${task.name} (${task.status})`)
      .join('\n');

    return `${summary}\n${taskList}`;
  }

  /**
   * Get current root task from the active plan.
   */
  static async getCurrentRootTask(
    currentSession?: { session_id?: string | null },
    currentWorkflowId?: string | null,
    currentPlanId?: number | null,
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
