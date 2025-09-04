"""
API Router Module

This package contains all FastAPI route definitions, organized by functional modules:
- task_routes: Task management endpoints
- plan_routes: Plan management endpoints  
- decomposition_routes: Recursive decomposition endpoints
- evaluation_routes: Evaluation system endpoints
- tool_routes: Tool integration endpoints
- context_routes: Context management endpoints
- execution_routes: Execution endpoints
- async_execution_routes: Asynchronous execution endpoints
- benchmark_routes: Benchmark endpoints
- smart_assembly_routes: Smart assembly endpoints
"""

# Lazy import to avoid circular dependencies
def get_all_routers():
    """Get all routers"""
    from .task_routes import router as task_router
    from .plan_routes import router as plan_router
    from .decomposition_routes import router as decomposition_router
    from .evaluation_routes import router as evaluation_router
    from .tool_routes import router as tool_router
    from .context_routes import router as context_router
    from .execution_routes import router as execution_router
    from .async_execution_routes import router as async_execution_router
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
        async_execution_router,
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
    "async_execution_router",
    "benchmark_router",
    "smart_assembly_router",
]
