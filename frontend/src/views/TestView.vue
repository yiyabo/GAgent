<template>
  <div class="test-view">
    <h1>GAgent 系统测试</h1>
    
    <el-card>
      <h2>1. 基本功能测试</h2>
      <el-button @click="testBasic">测试基础连接</el-button>
      <div v-if="testResults.basic">
        <pre>{{ testResults.basic }}</pre>
      </div>
    </el-card>
    
    <el-card style="margin-top: 20px">
      <h2>2. 对话功能测试</h2>
      <el-input 
        v-model="testCommand" 
        placeholder="输入测试命令，如：显示所有计划"
        @keyup.enter="testConversation"
      />
      <el-button @click="testConversation" style="margin-top: 10px">
        发送测试命令
      </el-button>
      <div v-if="testResults.conversation">
        <h3>响应：</h3>
        <pre>{{ testResults.conversation }}</pre>
      </div>
    </el-card>
    
    <el-card style="margin-top: 20px">
      <h2>3. 快速测试按钮</h2>
      <div class="quick-test-buttons">
        <el-button @click="quickTest('帮助')">帮助</el-button>
        <el-button @click="quickTest('显示所有计划')">显示计划</el-button>
        <el-button @click="quickTest('创建一个关于测试的计划')">创建计划</el-button>
      </div>
    </el-card>
  </div>
</template>

<script>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import api from '../services/api'

export default {
  name: 'TestView',
  setup() {
    const testCommand = ref('')
    const testResults = ref({
      basic: null,
      conversation: null
    })
    
    const testBasic = async () => {
      try {
        // 测试基本API连接
        const response = await api.get('/plans')
        testResults.value.basic = JSON.stringify(response.data, null, 2)
        ElMessage.success('API连接成功！')
      } catch (error) {
        testResults.value.basic = `错误: ${error.message}`
        ElMessage.error('API连接失败！')
      }
    }
    
    const testConversation = async () => {
      if (!testCommand.value.trim()) {
        ElMessage.warning('请输入测试命令')
        return
      }
      
      try {
        // 直接测试 ConversationalAgent
        const response = await api.post('/agent/command', {
          plan_id: 1,  // 使用默认的 plan_id
          command: testCommand.value
        })
        
        testResults.value.conversation = JSON.stringify(response.data, null, 2)
        
        // 显示响应消息
        if (response.data.response) {
          ElMessage.info(response.data.response.substring(0, 100) + '...')
        }
      } catch (error) {
        testResults.value.conversation = `错误: ${error.message}`
        ElMessage.error('命令执行失败！')
      }
    }
    
    const quickTest = (command) => {
      testCommand.value = command
      testConversation()
    }
    
    return {
      testCommand,
      testResults,
      testBasic,
      testConversation,
      quickTest
    }
  }
}
</script>

<style scoped>
.test-view {
  padding: 20px;
  max-width: 1200px;
  margin: 0 auto;
}

.quick-test-buttons {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

pre {
  background: #f5f7fa;
  padding: 10px;
  border-radius: 4px;
  overflow-x: auto;
  max-height: 400px;
  overflow-y: auto;
}
</style>