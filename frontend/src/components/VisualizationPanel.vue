<template>
  <div class="visualization-panel">
    <!-- 计划列表视图 -->
    <PlanListView 
      v-if="type === 'plan_list'" 
      :plans="data"
      @select-plan="handleSelectPlan"
      @execute-plan="handleExecutePlan"
      @action="handleAction"
    />
    
    <!-- 计划详情视图 -->
    <PlanDetails
      v-else-if="type === 'plan_details'"
      :data="data"
      :config="config"
      @action="handleAction"
    />
    
    <!-- 任务视图容器 (包含切换器) -->
    <div v-else-if="type === 'task_tree'" class="task-view-container">
      <div class="view-header">
        <div class="header-left">
          <el-button 
            type="text" 
            icon="el-icon-arrow-left" 
            @click="handleBackToList"
            class="back-button"
          >
            Back to Plan List
          </el-button>
          <h4 class="plan-title">{{ planTitle }}</h4>
        </div>
        <el-radio-group v-model="activeTaskView" size="small">
          <el-radio-button label="tree">Task Tree</el-radio-button>
          <el-radio-button label="graph">Task Graph</el-radio-button>
        </el-radio-group>
      </div>
      
      <!-- 动态任务视图 -->
      <keep-alive>
        <component 
          :is="activeTaskViewComponent"
          :tasks="data"
          :plan-data="data"
          :plan-title="planTitle"
          :loading="config.loading || false"
          :error="config.error || null"
          @task-selected="handleSelectTask"
          @refresh="handleRefresh"
        />
      </keep-alive>
    </div>
    
    <!-- 任务列表视图 (保持不变) -->
    <TaskTreeView 
      v-else-if="type === 'task_list'" 
      :tasks="data"
      :loading="config.loading || false"
      :error="config.error || null"
      @task-selected="handleSelectTask"
      @refresh="handleRefresh"
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
import { ref, computed, watch, nextTick } from 'vue'
import PlanListView from './visualization/PlanListView.vue'
import PlanDetails from './visualizations/PlanDetails.vue'
import PlanGraphView from './visualization/PlanGraphView.vue'
import TaskTreeView from './TaskTreeView.vue'
import HelpMenu from './visualization/HelpMenu.vue'
import WelcomeView from './visualization/WelcomeView.vue'

export default {
  name: 'VisualizationPanel',
  components: {
    PlanListView,
    PlanDetails,
    PlanGraphView,
    TaskTreeView,
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
  setup(props) {
    const activeTaskView = ref('tree'); // 'tree' or 'graph'

    const activeTaskViewComponent = computed(() => {
      return activeTaskView.value === 'tree' ? 'TaskTreeView' : 'PlanGraphView';
    });

    const planTitle = computed(() => {
      if (props.config && props.config.title) {
        return props.config.title;
      }
      return '任务详情';
    });

    // Watch for data changes to ensure reactivity
    watch(
      () => props.data,
      (newData, oldData) => {
        console.log('VisualizationPanel data changed:', {
          type: props.type,
          newDataLength: Array.isArray(newData) ? newData.length : Object.keys(newData || {}).length,
          oldDataLength: Array.isArray(oldData) ? oldData.length : Object.keys(oldData || {}).length
        });
        
        if (props.type === 'task_tree' || props.type === 'task_list') {
          nextTick(() => {
            // Trigger component update
          });
        }
      },
      { deep: true, immediate: false }
    );

    watch(
      () => props.type,
      (newType, oldType) => {
        console.log('VisualizationPanel type changed:', { newType, oldType });
        // Reset to tree view when a new plan is selected
        if (newType === 'task_tree') {
          activeTaskView.value = 'tree';
        }
        nextTick(() => {
          // Ensure proper rendering after type change
        });
      }
    );

    return {
      activeTaskView,
      activeTaskViewComponent,
      planTitle
    };
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
    },
    
    handleAction(action) {
      // A generic handler to pass actions up from child components
      this.$emit('action', action);
    },

    handleBackToList() {
      this.$emit('action', {
        type: 'show_plan_list'
      });
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
  display: flex;
  flex-direction: column;
}

.task-view-container {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 15px;
  flex-shrink: 0;
}

.view-header h4 {
  margin: 0;
  color: #303133;
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