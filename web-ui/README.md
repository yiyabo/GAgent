# 🌐 AI 智能任务编排系统 - Web UI

> 现代化的AI驱动任务编排Web界面，提供DAG可视化、实时聊天、任务管理等功能

## ✨ 核心特性

- 🎯 **DAG可视化**: 实时渲染任务层次结构 (ROOT → COMPOSITE → ATOMIC)
- 💬 **智能对话**: 类似Windsurf IDE的聊天体验，支持Markdown和代码高亮
- 📊 **实时监控**: WebSocket实时状态更新和系统监控
- 🔧 **任务管理**: 可视化任务操作和执行监控
- 📱 **响应式设计**: 支持桌面和移动端访问

## 🚀 快速开始

### 环境要求

- Node.js >= 16.0.0
- npm >= 8.0.0
- 后端API服务默认运行在 http://localhost:9000（可通过 `VITE_API_BASE_URL` 配置）

### 安装依赖

```bash
cd web-ui
npm install
```

### 启动开发服务器

```bash
# 开发模式
npm run dev

# 访问 http://localhost:3000
```

### 构建生产版本

```bash
# 构建
npm run build

# 预览构建结果
npm run preview
```

## 🏗️ 技术栈

- **前端框架**: React 18 + TypeScript
- **构建工具**: Vite
- **UI组件库**: Ant Design
- **状态管理**: Zustand
- **HTTP客户端**: Axios + React Query
- **图形可视化**: vis-network
- **代码编辑器**: Monaco Editor
- **样式方案**: CSS Modules + Ant Design Theme

## 📁 项目结构

```
web-ui/
├── public/              # 静态资源
├── src/
│   ├── api/            # API客户端和服务
│   ├── components/     # React组件
│   │   ├── layout/     # 布局组件
│   │   ├── dag/        # DAG可视化组件
│   │   ├── chat/       # 聊天组件
│   │   └── tasks/      # 任务组件
│   ├── pages/          # 页面组件
│   ├── store/          # Zustand状态管理
│   ├── types/          # TypeScript类型定义
│   ├── utils/          # 工具函数
│   └── styles/         # 样式文件
├── package.json        # 项目配置
└── vite.config.ts      # Vite配置
```

## 🎨 主要组件

### DAG可视化 (`components/dag/DAGVisualization.tsx`)
- 支持层次、力导向、环形布局
- 实时任务状态更新
- 交互式节点操作
- 缩放、全屏、自动布局

### 聊天面板 (`components/chat/ChatPanel.tsx`)
- Markdown渲染和代码高亮
- 消息历史管理
- 快捷操作按钮
- 上下文感知对话

### 状态管理 (`store/`)
- **系统状态**: API连接、数据库状态、系统负载
- **任务状态**: 任务列表、DAG数据、过滤器
- **聊天状态**: 消息、会话、输入状态

## 🔌 API集成

### 后端连接

Web UI通过Vite代理连接到后端API，可在 `.env.*` 中调整目标地址：

```typescript
import { defineConfig, loadEnv } from 'vite';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  const apiTarget = env.VITE_API_BASE_URL || 'http://localhost:9000';
  const wsTarget = env.VITE_WS_BASE_URL || apiTarget.replace(/^http/, 'ws');

  return {
    server: {
      proxy: {
        '/api': apiTarget,
        '/ws': wsTarget,
      },
    },
  };
});
```

### API服务

- `tasksApi`: 任务CRUD和执行
- `plansApi`: 计划管理
- `systemApi`: 系统状态监控
- WebSocket: 实时状态更新

### 真实API模式

⚠️ **重要**: 系统运行在真实API模式下，不使用Mock数据:

```typescript
// 确保后端API服务正常运行
// GLM_API_KEY 已正确配置
// 所有API调用都连接真实服务
```

## 🎯 开发指南

### 添加新页面

1. 在 `src/pages/` 创建页面组件
2. 在 `App.tsx` 添加路由
3. 在 `AppSider.tsx` 添加菜单项

### 添加新API服务

1. 在 `src/api/` 创建API类
2. 继承 `BaseApi` 类
3. 在组件中使用 React Query 集成

### 自定义主题

在 `src/main.tsx` 修改Ant Design主题配置:

```typescript
const antdTheme = {
  token: {
    colorPrimary: '#1890ff',
    // 其他主题配置
  },
};
```

## 📊 性能优化

- **代码分割**: 按路由和组件懒加载
- **Bundle优化**: Vendor、UI库、可视化库分离
- **缓存策略**: React Query缓存API响应
- **虚拟化**: 大数据量场景的性能优化

## 🚨 注意事项

1. **后端依赖**: 需要后端API服务运行在 `VITE_API_BASE_URL` 指定的地址（默认 http://localhost:9000）
2. **真实API**: 不支持Mock模式，所有调用都是真实API
3. **WebSocket**: 实时功能需要WebSocket连接
4. **浏览器兼容**: 支持现代浏览器 (Chrome 88+, Firefox 85+, Safari 14+)

## 🐛 故障排除

### 常见问题

**1. API连接失败**
```bash
# 检查后端服务是否运行
curl http://localhost:9000/health

# 检查环境变量
echo $GLM_API_KEY
```

**2. 构建失败**
```bash
# 清理缓存重新安装
rm -rf node_modules package-lock.json
npm install
```

**3. 样式问题**
```bash
# 确保Ant Design版本兼容
npm ls antd
```

## 📄 许可证

MIT License

---

**🚀 AI智能任务编排系统 Web UI** - 让AI任务管理更直观、更高效！
