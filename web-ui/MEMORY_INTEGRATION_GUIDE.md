# 🧠 Memory-MCP 集成与使用指南

## 📋 目录
1. [Memory-MCP 是什么](#memory-mcp-是什么)
2. [当前集成状态](#当前集成状态)
3. [如何起作用](#如何起作用)
4. [使用场景](#使用场景)
5. [如何将 Memory 集成到 AI 对话](#如何将-memory-集成到-ai-对话)

---

## 🎯 Memory-MCP 是什么

**Memory-MCP** (Memory with Model Context Protocol) 是一个智能记忆管理系统，用于：

1. **存储重要信息**: 保存对话、经验、知识、上下文
2. **语义搜索**: 基于向量嵌入的相似度搜索
3. **上下文增强**: 为 AI 提供相关历史记忆作为上下文
4. **知识积累**: 构建随时间增长的知识库

### 核心概念

#### 记忆类型 (Memory Type)
- **conversation** 对话: 重要的对话内容
- **experience** 经验: 操作经验和学习成果
- **knowledge** 知识: 领域知识和概念
- **context** 上下文: 环境和背景信息

#### 重要性级别 (Importance)
- **critical** 关键: 永久保存
- **high** 高: 长期保存
- **medium** 中: 定期清理
- **low** 低: 短期保存
- **temporary** 临时: 自动清理

---

## ✅ 当前集成状态

### 后端 (Backend) - ✅ 完全实现

**文件位置**:
```
app/
├── api/
│   └── memory_api.py              # Memory API 路由 (4个端点)
├── models_memory.py                # Memory 数据模型
└── services/
    └── memory/
        ├── memory_service.py       # Memory 核心服务
        └── unified_cache.py        # 统一缓存实现
```

**API 端点**:
1. `POST /mcp/save_memory` - 保存记忆
2. `POST /mcp/query_memory` - 查询记忆
3. `GET /mcp/memory/stats` - 获取统计信息
4. `POST /mcp/memory/auto_save_task` - 自动保存任务输出

**数据存储**:
- SQLite 数据库: `memory_contexts` 表
- 向量嵌入: 用于语义搜索
- 自动生成关键词和标签

### 前端 (Frontend) - ✅ 独立界面

**文件位置**:
```
web-ui/src/
├── api/
│   └── memory.ts                   # Memory API 客户端
├── components/
│   └── memory/
│       ├── MemoryGraph.tsx         # 图谱可视化
│       ├── MemoryDetailDrawer.tsx  # 详情展示
│       └── SaveMemoryModal.tsx     # 保存表单
├── pages/
│   └── Memory.tsx                  # 主页面
└── store/
    └── memory.ts                   # 状态管理
```

**访问方式**:
- URL: http://localhost:3001/memory
- 菜单: 侧边栏 → "记忆管理"

**功能**:
- 📊 统计看板
- 🔍 搜索和过滤
- 📋 列表视图
- 🗺️ 图谱可视化
- 💾 保存记忆
- 👁️ 查看详情

### AI 对话 (ChatLayout) - ❌ 未集成

**当前状态**: AI 对话界面 **尚未使用** Memory-MCP 功能

**原因**:
- ChatLayout 是独立的聊天组件
- 对话历史存储在本地状态中
- 没有调用 Memory API

---

## 🔄 如何起作用

### 1. 保存记忆流程

```
用户输入
    ↓
Web UI (SaveMemoryModal)
    ↓
POST /mcp/save_memory
    ↓
MemoryService.save_memory()
    ↓
├─→ 生成向量嵌入 (Embedding)
├─→ 提取关键词
├─→ 存储到 SQLite
└─→ 返回 Memory ID
```

### 2. 查询记忆流程

```
用户搜索 "如何部署项目"
    ↓
Web UI (Memory.tsx)
    ↓
POST /mcp/query_memory
    ↓
MemoryService.query_memory()
    ↓
├─→ 生成查询向量
├─→ 语义相似度计算
├─→ 过滤和排序
└─→ 返回相关记忆 (带相似度分数)
```

### 3. 自动保存任务输出

```
任务执行完成
    ↓
POST /mcp/memory/auto_save_task
    ↓
MemoryService.save_memory()
    ↓
自动标记为 "experience" 类型
自动添加 ["task_output", "auto_generated"] 标签
```

---

## 💡 使用场景

### 场景 1: 手动保存重要对话

**步骤**:
1. 访问 http://localhost:3001/memory
2. 点击"保存新记忆"按钮
3. 填写表单:
   - 内容: "用户提到他们的部署环境是 AWS Lambda"
   - 类型: 对话 (conversation)
   - 重要性: 高 (high)
   - 标签: ["用户信息", "部署环境"]
4. 点击保存

### 场景 2: 搜索相关记忆

**步骤**:
1. 在搜索框输入 "AWS"
2. 选择类型过滤: "对话"
3. 查看匹配结果（按相似度排序）
4. 点击"查看详情"查看完整信息

### 场景 3: 可视化知识图谱

**步骤**:
1. 切换到"图谱视图"标签
2. 查看记忆之间的语义连接
3. 拖拽节点探索关系
4. 调整相似度阈值过滤弱连接
5. 点击节点查看详情

### 场景 4: 任务输出自动保存 (后端)

**触发条件**: 任务执行完成时
**自动操作**:
```python
# 在任务完成处理函数中
async def on_task_complete(task_id, task_output):
    await auto_save_task_memory({
        "task_id": task_id,
        "task_name": "生成报告",
        "content": task_output
    })
```

---

## 🔗 如何将 Memory 集成到 AI 对话

### 方案 A: RAG 模式 (推荐)

在用户发送消息时，自动查询相关记忆并添加到上下文中。

#### 实现步骤

**1. 修改 ChatLayout.tsx**

```typescript
import { memoryApi } from '@api/memory';

// 在发送消息前查询相关记忆
const handleSendMessage = async (message: string) => {
  try {
    // 1. 查询相关记忆
    const memoryResult = await memoryApi.queryMemory({
      search_text: message,
      limit: 3,
      min_similarity: 0.6
    });

    // 2. 构建增强的上下文
    let enhancedPrompt = message;
    if (memoryResult.memories.length > 0) {
      const memoryContext = memoryResult.memories
        .map(m => `[记忆] ${m.content} (相似度: ${(m.similarity * 100).toFixed(1)}%)`)
        .join('\n');

      enhancedPrompt = `相关记忆:\n${memoryContext}\n\n用户问题: ${message}`;
    }

    // 3. 发送增强后的消息到 LLM
    const response = await chatApi.sendMessage(enhancedPrompt);

    // 4. (可选) 将重要对话保存为记忆
    if (shouldSaveAsMemory(response)) {
      await memoryApi.saveMemory({
        content: `Q: ${message}\nA: ${response}`,
        memory_type: 'conversation',
        importance: 'medium',
        tags: ['chat', 'auto_saved']
      });
    }

    return response;
  } catch (error) {
    console.error('Error with memory enhancement:', error);
    // 降级: 不使用记忆直接发送
    return await chatApi.sendMessage(message);
  }
};
```

**2. 添加记忆指示器 UI**

```tsx
// 显示使用的记忆
{relevantMemories.length > 0 && (
  <Alert
    message={`找到 ${relevantMemories.length} 条相关记忆`}
    description={
      <Space direction="vertical">
        {relevantMemories.map(m => (
          <Tag key={m.id} color="blue">
            {m.keywords.join(', ')} ({(m.similarity * 100).toFixed(0)}%)
          </Tag>
        ))}
      </Space>
    }
    type="info"
    closable
  />
)}
```

### 方案 B: 显式记忆管理

添加按钮让用户主动将对话保存为记忆。

#### 实现步骤

**1. 添加"保存为记忆"按钮**

```tsx
// 在每条消息旁边添加按钮
<Button
  icon={<DatabaseOutlined />}
  size="small"
  onClick={() => saveMessageAsMemory(message)}
>
  保存
</Button>
```

**2. 实现保存函数**

```typescript
const saveMessageAsMemory = async (message: Message) => {
  try {
    await memoryApi.saveMemory({
      content: message.content,
      memory_type: 'conversation',
      importance: 'medium',
      tags: ['chat', 'manual_saved'],
      context: `对话保存于 ${new Date().toLocaleString()}`
    });
    message.success('已保存为记忆');
  } catch (error) {
    message.error('保存失败');
  }
};
```

### 方案 C: 后台自动保存

定期将对话保存到记忆系统。

```typescript
// 每 5 条消息自动保存一次
useEffect(() => {
  if (messages.length % 5 === 0 && messages.length > 0) {
    const recentConversation = messages.slice(-5)
      .map(m => `${m.role}: ${m.content}`)
      .join('\n');

    memoryApi.saveMemory({
      content: recentConversation,
      memory_type: 'conversation',
      importance: 'low',
      tags: ['chat', 'auto_batch'],
    }).catch(err => console.error('Auto-save failed:', err));
  }
}, [messages]);
```

---

## 🎯 推荐集成方案

### 最佳实践组合

```typescript
// ChatLayout.tsx 完整示例

import React, { useState, useEffect } from 'react';
import { memoryApi } from '@api/memory';
import { Button, Tag, Alert, Space, Tooltip } from 'antd';
import { DatabaseOutlined, HistoryOutlined } from '@ant-design/icons';

const ChatLayout: React.FC = () => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [relevantMemories, setRelevantMemories] = useState<Memory[]>([]);
  const [memoryEnabled, setMemoryEnabled] = useState(true);

  // 🔄 方案 A: 发送消息时自动查询记忆
  const handleSendMessage = async (message: string) => {
    let enhancedPrompt = message;

    if (memoryEnabled) {
      try {
        const memoryResult = await memoryApi.queryMemory({
          search_text: message,
          limit: 3,
          min_similarity: 0.6
        });

        setRelevantMemories(memoryResult.memories);

        if (memoryResult.memories.length > 0) {
          const memoryContext = memoryResult.memories
            .map(m => `[记忆] ${m.content}`)
            .join('\n');
          enhancedPrompt = `${memoryContext}\n\n${message}`;
        }
      } catch (error) {
        console.error('Memory query failed:', error);
      }
    }

    // 发送消息到 LLM
    const response = await chatApi.sendMessage(enhancedPrompt);

    // 添加到消息列表
    setMessages([
      ...messages,
      { role: 'user', content: message },
      { role: 'assistant', content: response }
    ]);
  };

  // 💾 方案 B: 手动保存消息
  const saveMessageAsMemory = async (message: Message) => {
    try {
      await memoryApi.saveMemory({
        content: message.content,
        memory_type: 'conversation',
        importance: 'medium',
        tags: ['chat', 'manual_saved']
      });
      message.success('✅ 已保存为记忆');
    } catch (error) {
      message.error('❌ 保存失败');
    }
  };

  // 🔄 方案 C: 自动后台保存
  useEffect(() => {
    if (messages.length % 10 === 0 && messages.length > 0) {
      const recentConversation = messages.slice(-10)
        .map(m => `${m.role}: ${m.content}`)
        .join('\n');

      memoryApi.saveMemory({
        content: recentConversation,
        memory_type: 'conversation',
        importance: 'low',
        tags: ['chat', 'auto_batch'],
      }).catch(console.error);
    }
  }, [messages]);

  return (
    <div className="chat-layout">
      {/* 记忆功能开关 */}
      <div className="chat-header">
        <Space>
          <Tooltip title="启用记忆增强">
            <Button
              type={memoryEnabled ? 'primary' : 'default'}
              icon={<DatabaseOutlined />}
              onClick={() => setMemoryEnabled(!memoryEnabled)}
            >
              记忆增强 {memoryEnabled ? '已启用' : '已禁用'}
            </Button>
          </Tooltip>
        </Space>
      </div>

      {/* 相关记忆提示 */}
      {relevantMemories.length > 0 && (
        <Alert
          message={`🧠 找到 ${relevantMemories.length} 条相关记忆`}
          description={
            <Space wrap>
              {relevantMemories.map(m => (
                <Tag key={m.id} color="blue">
                  {m.keywords.slice(0, 2).join(', ')}
                  ({(m.similarity * 100).toFixed(0)}%)
                </Tag>
              ))}
            </Space>
          }
          type="info"
          closable
          style={{ marginBottom: 16 }}
        />
      )}

      {/* 消息列表 */}
      <div className="message-list">
        {messages.map((msg, idx) => (
          <div key={idx} className={`message message-${msg.role}`}>
            <div className="message-content">{msg.content}</div>
            <div className="message-actions">
              <Button
                size="small"
                icon={<DatabaseOutlined />}
                onClick={() => saveMessageAsMemory(msg)}
              >
                保存
              </Button>
            </div>
          </div>
        ))}
      </div>

      {/* 输入框 */}
      <div className="chat-input">
        <Input.TextArea
          value={inputValue}
          onChange={e => setInputValue(e.target.value)}
          onPressEnter={handleSendMessage}
          placeholder="输入消息... (记忆增强已启用)"
        />
        <Button type="primary" onClick={handleSendMessage}>
          发送
        </Button>
      </div>
    </div>
  );
};
```

---

## 📊 预期效果

### 集成前 (当前)
```
用户: "如何部署到 AWS?"
AI: "AWS 部署有多种方式..."
```

### 集成后 (方案 A)
```
用户: "如何部署到 AWS?"

[系统查询记忆]
找到 2 条相关记忆:
- "用户的部署环境是 AWS Lambda" (85%)
- "之前讨论过使用 Serverless Framework" (72%)

[增强上下文]
AI: "基于你之前提到的 AWS Lambda 环境，建议使用 Serverless Framework..."
```

---

## 🚀 下一步行动

### 立即可做
1. ✅ 使用独立的 Memory 页面手动管理记忆
2. ✅ 通过 API 测试保存和查询功能
3. ✅ 查看图谱可视化理解记忆关系

### 短期集成 (1周内)
1. 实现方案 A: RAG 模式集成到 ChatLayout
2. 添加记忆指示器 UI
3. 测试上下文增强效果

### 中期优化 (1月内)
1. 自动重要性评分
2. 记忆进化和合并
3. 多轮对话记忆管理
4. 记忆推荐系统

---

## 📝 总结

### 当前状态
- ✅ 后端: Memory-MCP 完全实现
- ✅ 前端: 独立 Memory 管理界面
- ❌ AI 对话: 尚未集成

### 为什么重要
Memory-MCP 集成后可以:
1. **增强 AI 回答质量**: 基于历史上下文提供更准确的回答
2. **保持对话连贯性**: 跨会话记住重要信息
3. **积累知识**: 随时间构建项目知识库
4. **个性化体验**: 记住用户偏好和环境

### 如何集成
推荐使用 **方案 A (RAG 模式)** + **方案 B (手动保存)**:
- 自动查询相关记忆增强上下文
- 允许用户手动保存重要对话
- 显示使用的记忆提高透明度

---

**最后更新**: 2025-10-13
**版本**: v1.0.0
