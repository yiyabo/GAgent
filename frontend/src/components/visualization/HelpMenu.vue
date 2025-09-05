<template>
  <div class="help-menu">
    <div class="view-header">
      <h2>Help Menu</h2>
    </div>
    
    <div class="help-content">
      <el-card class="command-section">
        <h3>Available Commands</h3>
        <div class="command-list">
          <div 
            v-for="cmd in commands" 
            :key="cmd.command"
            class="command-item"
            @click="executeCommand(cmd.command)"
          >
            <div class="command-name">
              <i class="el-icon-caret-right"></i>
              {{ cmd.command }}
            </div>
            <div class="command-desc">{{ cmd.description }}</div>
          </div>
        </div>
      </el-card>
      
      <el-card class="example-section">
        <h3>Example Usage</h3>
        <div class="example-list">
          <div class="example-item">
            <div class="example-command">"Create a research plan about deep learning"</div>
            <div class="example-desc">Create a new research plan and automatically break down tasks</div>
          </div>
          <div class="example-item">
            <div class="example-command">"Show all plans"</div>
            <div class="example-desc">View the list of all current plans</div>
          </div>
          <div class="example-item">
            <div class="example-command">"Execute plan 1"</div>
            <div class="example-desc">Start executing all tasks in the specified plan</div>
          </div>
          <div class="example-item">
            <div class="example-command">"Check status of plan 2"</div>
            <div class="example-desc">View the execution progress of a specific plan</div>
          </div>
        </div>
      </el-card>
      
      <el-card class="tips-section">
        <h3>Usage Tips</h3>
        <ul>
          <li>You can communicate with the system using natural language</li>
          <li>The system will automatically recognize your intent and execute corresponding actions</li>
          <li>The right panel will display relevant visualization information in real time</li>
          <li>Click elements in the visualization panel to quickly execute actions</li>
        </ul>
      </el-card>
    </div>
  </div>
</template>

<script>
export default {
  name: 'HelpMenu',
  props: {
    commands: {
      type: Array,
      default: () => []
    }
  },
  methods: {
    executeCommand(command) {
      this.$emit('execute-command', command)
    }
  }
}
</script>

<style scoped>
.help-menu {
  height: 100%;
  overflow-y: auto;
}

.view-header {
  margin-bottom: 20px;
}

.view-header h2 {
  margin: 0;
  color: #303133;
}

.help-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.command-section h3,
.example-section h3,
.tips-section h3 {
  margin: 0 0 15px 0;
  font-size: 16px;
  color: #303133;
}

.command-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.command-item {
  padding: 10px;
  border: 1px solid #e4e7ed;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.3s;
}

.command-item:hover {
  background: #f5f7fa;
  border-color: #409eff;
}

.command-name {
  font-weight: bold;
  color: #303133;
  margin-bottom: 5px;
}

.command-desc {
  color: #909399;
  font-size: 14px;
}

.example-list {
  display: flex;
  flex-direction: column;
  gap: 15px;
}

.example-item {
  padding: 10px;
  background: #f5f7fa;
  border-radius: 4px;
}

.example-command {
  font-family: 'Courier New', monospace;
  color: #409eff;
  margin-bottom: 5px;
}

.example-desc {
  color: #606266;
  font-size: 14px;
}

.tips-section ul {
  margin: 0;
  padding-left: 20px;
  color: #606266;
  line-height: 1.8;
}
</style>