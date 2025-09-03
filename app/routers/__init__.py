"""
API路由模块

这个包包含了所有的FastAPI路由定义，按功能模块组织：
- task_routes: 任务管理相关端点
- plan_routes: 计划管理相关端点  
- decomposition_routes: 递归分解相关端点
- evaluation_routes: 评估系统相关端点
- tool_routes: 工具集成相关端点
- context_routes: 上下文管理相关端点
- execution_routes: 执行相关端点
- benchmark_routes: 基准测试端点
"""

# 延迟导入，避免循环依赖
def get_all_routers():
    """获取所有路由器"""
    from .task_routes import router as task_router
    from .plan_routes import router as plan_router
    from .decomposition_routes import router as decomposition_router
    from .evaluation_routes import router as evaluation_router
    from .tool_routes import router as tool_router
    from .context_routes import router as context_router
    from .execution_routes import router as execution_router
    from .benchmark_routes import router as benchmark_router
    from .smart_assembly_routes import router as smart_assembly_router
    
    return [
        task_router,
        plan_router,
        decomposition_router,
        evaluation_router,
        tool_router,
        context_router,
        execution_router,
        benchmark_router,
        smart_assembly_router,
    ]

__all__ = [
    "task_router",
    "plan_router", 
    "decomposition_router",
    "evaluation_router",
    "tool_router",
    "context_router",
    "execution_router",
    "benchmark_router",
]
