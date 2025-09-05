<template>
  <div class="visualization-panel">
    <!-- 计划列表视图 -->
    <PlanListView 
      v-if="type === 'plan_list'" 
      :plans="data"
      @select-plan="handleSelectPlan"
      @execute-plan="handleExecutePlan"
    />
    
    <!-- 计划详情视图 -->
    <PlanDetails
      v-else-if="type === 'plan_details'"
      :data="data"
      :config="config"
      @action="handleAction"
    />
    
    <!-- 计划图形视图 -->
    <PlanGraphView
      v-else-if="type === 'plan_graph'"
      :plan-data="data"
      :plan-title="config.title"
    />
    
    <!-- 任务树视图 -->
    <TaskTreeView 
      v-else-if="type === 'task_tree'" 
      :tasks="data"
      :loading="config.loading || false"
      :error="config.error || null"
      @task-selected="handleSelectTask"
      @refresh="handleRefresh"
    />
    
    <!-- 任务列表视图 -->
    <TaskTreeView 
      v-else-if="type === 'task_list'" 
      :tasks="data"
      :loading="config.loading || false"
      :error="config.error || null"
      @task-selected="handleSelectTask"
      @refresh="handleRefresh"
    />
    
    <!-- 执行进度视图 -->
    <ExecutionProgress 
      v-else-if="type === 'execution_progress'" 
      :tasks="data"
      :plan-id="config.plan_id"
      :auto-refresh="config.autoRefresh"
      :refresh-interval="config.refreshInterval"
    />
    
    <!-- 状态仪表板 -->
    <StatusDashboard 
      v-else-if="type === 'status_dashboard'" 
      :data="data"
      :config="config"
    />
    
    <!-- 帮助菜单 -->
    <HelpMenu 
      v-else-if="type === 'help_menu'"
      :commands="data"
      @execute-command="handleExecuteCommand"
    />
    
    <!-- 默认欢迎视图 -->
    <WelcomeView v-else />
  </div>
</template>

<script>
import PlanListView from './visualization/PlanListView.vue'
import PlanDetails from './visualizations/PlanDetails.vue'
import PlanGraphView from './visualization/PlanGraphView.vue'
import TaskTreeView from './TaskTreeView.vue'
import ExecutionProgress from './visualization/ExecutionProgress.vue'
import StatusDashboard from './visualization/StatusDashboard.vue'
import HelpMenu from './visualization/HelpMenu.vue'
import WelcomeView from './visualization/WelcomeView.vue'

export default {
  name: 'VisualizationPanel',
  components: {
    PlanListView,
    PlanDetails,
    PlanGraphView,
    TaskTreeView,
    ExecutionProgress,
    StatusDashboard,
    HelpMenu,
    WelcomeView
  },
  props: {
    type: {
      type: String,
      default: 'none'
    },
    data: {
      type: [Array, Object],
      default: () => ({})
    },
    config: {
      type: Object,
      default: () => ({})
    }
  },
  methods: {
    handleSelectPlan(planId) {
      this.$emit('action', {
        type: 'select_plan',
        planId,
        command: `显示计划${planId}的任务`
      })
    },
    
    handleExecutePlan(planId) {
      this.$emit('action', {
        type: 'execute_plan',
        planId,
        command: `执行计划${planId}`
      })
    },
    
    handleSelectTask(task) {
      this.$emit('action', {
        type: 'select_task',
        taskId: task.id,
        task: task,
        command: `查询任务${task.id}的状态`
      })
    },
    
    handleRefresh() {
      this.$emit('action', {
        type: 'refresh_tasks',
        command: '刷新任务状态'
      })
    },
    
    handleExecuteCommand(command) {
      this.$emit('action', {
        type: 'help_command',
        command: command
      })
    }
  }
}
</script>

<style scoped>
.visualization-panel {
  height: 100%;
  width: 100%;
  padding: 20px;
  background: #f5f7fa;
  overflow-y: auto;
}

/* 自定义滚动条 */
.visualization-panel::-webkit-scrollbar {
  width: 8px;
}

.visualization-panel::-webkit-scrollbar-track {
  background: #e4e7ed;
  border-radius: 4px;
}

.visualization-panel::-webkit-scrollbar-thumb {
  background: #909399;
  border-radius: 4px;
}

.visualization-panel::-webkit-scrollbar-thumb:hover {
  background: #606266;
}
</style>