<template>
  <div class="plan-list-view">
    <div class="view-header">
      <h2>计划列表</h2>
      <el-button type="primary" size="small" @click="refreshPlans">
        <i class="el-icon-refresh"></i> 刷新
      </el-button>
    </div>
    
    <div v-if="plans.length === 0" class="no-plans">
      <el-empty description="暂无计划">
        <el-button type="primary" @click="createPlan">创建新计划</el-button>
      </el-empty>
    </div>
    
    <div v-else class="plans-grid">
      <el-card 
        v-for="plan in plans" 
        :key="plan.id"
        class="plan-card"
        shadow="hover"
      >
        <div class="plan-header">
          <h3>{{ plan.title }}</h3>
          <el-tag :type="getStatusType(plan.status)">
            {{ plan.status || 'active' }}
          </el-tag>
        </div>
        
        <div class="plan-meta">
          <div class="meta-item">
            <i class="el-icon-document"></i>
            <span>{{ plan.task_count || 0 }} 个任务</span>
          </div>
          <div class="meta-item">
            <i class="el-icon-circle-check"></i>
            <span>{{ plan.completed_count || 0 }} 已完成</span>
          </div>
        </div>
        
        <el-progress 
          :percentage="Math.round((plan.progress || 0) * 100)"
          :status="plan.progress >= 1 ? 'success' : ''"
        />
        
        <div class="plan-actions">
          <el-button 
            size="small" 
            @click="viewTasks(plan.id)"
          >
            查看任务
          </el-button>
          <el-button 
            type="primary" 
            size="small"
            @click="executePlan(plan.id)"
            :disabled="plan.progress >= 1"
          >
            执行计划
          </el-button>
          <el-button 
            type="danger" 
            size="small"
            plain
            @click="deletePlan(plan.id)"
          >
            删除
          </el-button>
        </div>
      </el-card>
    </div>
  </div>
</template>

<script>
export default {
  name: 'PlanListView',
  props: {
    plans: {
      type: Array,
      default: () => []
    }
  },
  methods: {
    getStatusType(status) {
      const types = {
        'active': '',
        'completed': 'success',
        'archived': 'info',
        'failed': 'danger'
      }
      return types[status] || ''
    },
    
    refreshPlans() {
      this.$emit('action', {
        type: 'refresh_plans',
        command: '显示所有计划'
      })
    },
    
    createPlan() {
      this.$emit('action', {
        type: 'create_plan',
        command: '创建新计划'
      })
    },
    
    viewTasks(planId) {
      this.$emit('select-plan', planId)
    },
    
    executePlan(planId) {
      this.$emit('execute-plan', planId)
    },
    
    deletePlan(planId) {
      this.$confirm('确定要删除这个计划吗？', '提示', {
        confirmButtonText: '确定',
        cancelButtonText: '取消',
        type: 'warning'
      }).then(() => {
        this.$emit('action', {
          type: 'delete_plan',
          planId,
          command: `删除计划${planId}`
        })
      })
    }
  }
}
</script>

<style scoped>
.plan-list-view {
  height: 100%;
}

.view-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.view-header h2 {
  margin: 0;
  color: #303133;
}

.no-plans {
  display: flex;
  align-items: center;
  justify-content: center;
  height: calc(100% - 60px);
}

.plans-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 20px;
}

.plan-card {
  transition: transform 0.3s;
}

.plan-card:hover {
  transform: translateY(-4px);
}

.plan-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 15px;
}

.plan-header h3 {
  margin: 0;
  font-size: 16px;
  color: #303133;
  flex: 1;
  margin-right: 10px;
}

.plan-meta {
  display: flex;
  gap: 20px;
  margin-bottom: 15px;
  color: #909399;
  font-size: 14px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 5px;
}

.plan-actions {
  display: flex;
  gap: 10px;
  margin-top: 15px;
}
</style>