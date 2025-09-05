<template>
  <div class="task-tree-view">
    <div class="view-header">
      <h2>任务结构</h2>
      <div class="header-actions">
        <el-button 
          size="small" 
          @click="expandAll = !expandAll"
        >
          {{ expandAll ? '收起全部' : '展开全部' }}
        </el-button>
      </div>
    </div>
    
    <div class="tree-container">
      <el-tree
        :data="treeData"
        :props="treeProps"
        :default-expand-all="expandAll"
        node-key="id"
        @node-click="handleNodeClick"
      >
        <template #default="{ node, data }">
          <div class="tree-node">
            <span class="node-label">{{ data.name }}</span>
            <div class="node-info">
              <el-tag 
                size="small" 
                :type="getStatusType(data.status)"
              >
                {{ data.status }}
              </el-tag>
              <el-tag 
                size="small" 
                type="info"
                v-if="data.task_type"
              >
                {{ data.task_type }}
              </el-tag>
            </div>
          </div>
        </template>
      </el-tree>
    </div>
  </div>
</template>

<script>
export default {
  name: 'TaskTreeView',
  props: {
    tasks: {
      type: [Array, Object],
      default: () => []
    },
    config: {
      type: Object,
      default: () => ({})
    }
  },
  data() {
    return {
      expandAll: false,
      treeProps: {
        children: 'children',
        label: 'name'
      }
    }
  },
  computed: {
    treeData() {
      // 如果tasks已经是树结构，直接使用
      if (Array.isArray(this.tasks) && this.tasks.length > 0 && this.tasks[0].children !== undefined) {
        return this.tasks
      }
      
      // 否则构建树结构
      return this.buildTree(this.tasks)
    }
  },
  mounted() {
    this.expandAll = this.config.expandAll || false
  },
  methods: {
    buildTree(tasks) {
      if (!Array.isArray(tasks)) return []
      
      const taskMap = {}
      const roots = []
      
      // 创建任务映射
      tasks.forEach(task => {
        taskMap[task.id] = { ...task, children: [] }
      })
      
      // 构建树结构
      tasks.forEach(task => {
        if (task.parent_id && taskMap[task.parent_id]) {
          taskMap[task.parent_id].children.push(taskMap[task.id])
        } else {
          roots.push(taskMap[task.id])
        }
      })
      
      return roots
    },
    
    getStatusType(status) {
      const types = {
        'pending': 'info',
        'processing': 'warning',
        'done': 'success',
        'failed': 'danger',
        'complete': 'success'
      }
      return types[status] || ''
    },
    
    handleNodeClick(data) {
      this.$emit('select-task', data.id)
    }
  }
}
</script>

<style scoped>
.task-tree-view {
  height: 100%;
  display: flex;
  flex-direction: column;
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

.tree-container {
  flex: 1;
  overflow: auto;
  background: white;
  border-radius: 4px;
  padding: 20px;
}

.tree-node {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 14px;
  padding-right: 8px;
}

.node-label {
  flex: 1;
  margin-right: 10px;
}

.node-info {
  display: flex;
  gap: 5px;
}

:deep(.el-tree-node__content) {
  height: auto;
  padding: 5px 0;
}

:deep(.el-tree-node__expand-icon) {
  padding: 6px;
}
</style>