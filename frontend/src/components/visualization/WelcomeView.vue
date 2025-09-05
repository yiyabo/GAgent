<template>
  <div class="welcome-view">
    <el-empty description="Welcome to the GAgent Assistant">
      <template #image>
        <div class="welcome-icon">
          <i class="el-icon-chat-dot-round"></i>
        </div>
      </template>
      <div class="welcome-tips">
        <h3>Get Started</h3>
        <p>To get started, try the following commands:</p>
        <ul>
          <li>• "Create a research plan about machine learning"</li>
          <li>• "Show all plans"</li>
          <li>• "Execute plan 1"</li>
          <li>• "Help"</li>
        </ul>
      </div>
    </el-empty>
  </div>
</template>

<script>
export default {
  name: 'WelcomeView'
}
</script>

<style scoped>
.welcome-view {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.welcome-icon {
  font-size: 80px;
  color: #409eff;
  margin-bottom: 20px;
}

.welcome-tips {
  text-align: left;
  margin-top: 20px;
}

.welcome-tips h3 {
  color: #303133;
  margin-bottom: 10px;
}

.welcome-tips p {
  color: #606266;
  margin-bottom: 10px;
}

.welcome-tips ul {
  list-style: none;
  padding: 0;
  color: #909399;
}

.welcome-tips ul li {
  padding: 5px 0;
}
</style>