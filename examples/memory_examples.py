"""
Memory System Usage Examples

Demonstrates how to use the integrated Memory-MCP system
"""

import asyncio
import requests
from app.models_memory import SaveMemoryRequest, QueryMemoryRequest, MemoryType, ImportanceLevel
from app.services.memory_service import get_memory_service


async def example_basic_memory_operations():
    """基本记忆操作示例"""
    print("🧠 Memory System Basic Operations Example")
    print("=" * 50)
    
    memory_service = get_memory_service()
    
    # 1. 保存不同类型的记忆
    print("\n1. 保存记忆...")
    
    memories_to_save = [
        {
            "content": "噬菌体治疗在对抗多重耐药菌感染方面显示出巨大潜力，特别是在传统抗生素失效的情况下。",
            "memory_type": MemoryType.KNOWLEDGE,
            "importance": ImportanceLevel.HIGH,
            "tags": ["噬菌体", "治疗", "耐药菌"],
            "related_task_id": None
        },
        {
            "content": "在实验中观察到，噬菌体PaP1对铜绿假单胞菌的杀菌效果在MOI=1时达到最佳。",
            "memory_type": MemoryType.EXPERIENCE,
            "importance": ImportanceLevel.CRITICAL,
            "tags": ["实验", "PaP1", "铜绿假单胞菌", "MOI"],
            "related_task_id": None
        },
        {
            "content": "用户询问关于噬菌体治疗的安全性问题，特别关心是否会产生免疫反应。",
            "memory_type": MemoryType.CONVERSATION,
            "importance": ImportanceLevel.MEDIUM,
            "tags": ["用户询问", "安全性", "免疫反应"],
            "related_task_id": None
        }
    ]
    
    saved_memory_ids = []
    for i, memory_data in enumerate(memories_to_save, 1):
        try:
            request = SaveMemoryRequest(**memory_data)
            response = await memory_service.save_memory(request)
            
            print(f"   ✅ 记忆 {i} 已保存: {response.memory_id[:8]}...")
            print(f"      类型: {response.memory_type.value}")
            print(f"      自动生成关键词: {response.keywords}")
            print(f"      自动生成上下文: {response.context}")
            print(f"      嵌入向量: {'已生成' if response.embedding_generated else '生成失败'}")
            
            saved_memory_ids.append(response.memory_id)
            
        except Exception as e:
            print(f"   ❌ 记忆 {i} 保存失败: {e}")
    
    # 2. 查询记忆
    print(f"\n2. 查询记忆...")
    
    search_queries = [
        "噬菌体治疗效果",
        "实验结果",
        "安全性问题"
    ]
    
    for query in search_queries:
        try:
            request = QueryMemoryRequest(
                search_text=query,
                limit=5,
                min_similarity=0.3
            )
            
            response = await memory_service.query_memory(request)
            
            print(f"\n   🔍 搜索: '{query}'")
            print(f"   找到 {response.total} 条相关记忆 (耗时: {response.search_time_ms:.1f}ms)")
            
            for j, memory in enumerate(response.memories[:3], 1):
                print(f"      {j}. [{memory.memory_type.value}] 相似度: {memory.similarity:.3f}")
                print(f"         内容: {memory.content[:60]}...")
                print(f"         标签: {', '.join(memory.tags[:3])}")
                
        except Exception as e:
            print(f"   ❌ 查询 '{query}' 失败: {e}")
    
    # 3. 获取统计信息
    print(f"\n3. 记忆系统统计...")
    try:
        stats = await memory_service.get_memory_stats()
        
        print(f"   📊 总记忆数量: {stats.total_memories}")
        print(f"   🔄 进化次数: {stats.evolution_count}")
        print(f"   🔗 平均连接数: {stats.average_connections:.2f}")
        print(f"   📈 嵌入覆盖率: {stats.embedding_coverage:.1%}")
        
        print(f"\n   记忆类型分布:")
        for mem_type, count in stats.memory_type_distribution.items():
            print(f"      {mem_type}: {count}")
        
        print(f"\n   重要性分布:")
        for importance, count in stats.importance_distribution.items():
            print(f"      {importance}: {count}")
            
    except Exception as e:
        print(f"   ❌ 获取统计信息失败: {e}")
    
    print(f"\n✅ 记忆系统示例完成！")
    return saved_memory_ids


async def example_api_usage():
    """API使用示例"""
    print("\n🌐 Memory API Usage Example")
    print("=" * 50)
    
    base_url = "http://localhost:8000"
    
    # 1. 测试MCP工具列表
    print("\n1. 获取MCP工具列表...")
    try:
        response = requests.get(f"{base_url}/mcp/tools")
        if response.status_code == 200:
            tools = response.json()["tools"]
            print(f"   ✅ 可用工具: {len(tools)} 个")
            for tool in tools:
                print(f"      - {tool['name']}: {tool['description']}")
        else:
            print(f"   ❌ 获取工具列表失败: {response.status_code}")
    except Exception as e:
        print(f"   ❌ API调用失败: {e}")
    
    # 2. 通过API保存记忆
    print(f"\n2. 通过API保存记忆...")
    try:
        save_payload = {
            "content": "通过API保存的测试记忆：CRISPR-Cas9基因编辑技术在噬菌体工程中的应用前景广阔。",
            "memory_type": "knowledge",
            "importance": "high",
            "tags": ["CRISPR", "基因编辑", "噬菌体工程"],
            "related_task_id": None
        }
        
        response = requests.post(f"{base_url}/mcp/save_memory", json=save_payload)
        if response.status_code == 200:
            result = response.json()
            print(f"   ✅ 记忆已保存: {result['context_id']}")
            print(f"      自动生成关键词: {result['meta']['agentic_keywords']}")
            print(f"      自动生成上下文: {result['meta']['agentic_context']}")
        else:
            print(f"   ❌ 保存失败: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   ❌ API调用失败: {e}")
    
    # 3. 通过API查询记忆
    print(f"\n3. 通过API查询记忆...")
    try:
        query_payload = {
            "search_text": "CRISPR基因编辑",
            "memory_types": ["knowledge", "experience"],
            "limit": 5,
            "min_similarity": 0.3
        }
        
        response = requests.post(f"{base_url}/mcp/query_memory", json=query_payload)
        if response.status_code == 200:
            result = response.json()
            memories = result["memories"]
            print(f"   ✅ 找到 {result['total']} 条相关记忆")
            
            for i, memory in enumerate(memories[:3], 1):
                print(f"      {i}. 相似度: {memory['similarity']:.3f}")
                print(f"         内容: {memory['content'][:50]}...")
                print(f"         标签: {', '.join(memory['meta']['tags'][:2])}")
        else:
            print(f"   ❌ 查询失败: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"   ❌ API调用失败: {e}")
    
    # 4. 获取记忆统计
    print(f"\n4. 获取记忆统计...")
    try:
        response = requests.get(f"{base_url}/mcp/memory/stats")
        if response.status_code == 200:
            stats = response.json()
            print(f"   ✅ 记忆统计:")
            print(f"      总数量: {stats['total_memories']}")
            print(f"      嵌入覆盖率: {stats['embedding_coverage']:.1%}")
            print(f"      进化次数: {stats['evolution_count']}")
        else:
            print(f"   ❌ 获取统计失败: {response.status_code}")
    except Exception as e:
        print(f"   ❌ API调用失败: {e}")
    
    print(f"\n✅ API使用示例完成！")


async def example_task_memory_integration():
    """任务与记忆集成示例"""
    print("\n🔗 Task-Memory Integration Example")
    print("=" * 50)
    
    # 模拟任务完成后自动保存记忆
    print("\n1. 模拟任务完成，自动保存记忆...")
    
    try:
        task_data = {
            "task_id": 999,
            "task_name": "噬菌体治疗机制研究",
            "content": """
噬菌体治疗机制研究结果：

1. 噬菌体通过特异性识别细菌表面受体进行感染
2. 裂解周期中，噬菌体复制并最终破坏宿主细菌
3. 治疗效果与噬菌体滴度、给药方式密切相关
4. 需要考虑患者免疫反应和噬菌体稳定性

实验数据显示，在体外条件下，噬菌体PaP1对铜绿假单胞菌的杀菌率达到99.5%。
"""
        }
        
        # 通过API自动保存任务记忆
        response = requests.post("http://localhost:8000/mcp/memory/auto_save_task", json=task_data)
        if response.status_code == 200:
            result = response.json()
            print(f"   ✅ 任务记忆已自动保存: {result['memory_id']}")
            print(f"      消息: {result['message']}")
        else:
            print(f"   ❌ 自动保存失败: {response.status_code}")
            
    except Exception as e:
        print(f"   ❌ 任务记忆集成失败: {e}")
    
    # 2. 查询任务相关记忆
    print(f"\n2. 查询任务相关记忆...")
    try:
        memory_service = get_memory_service()
        request = QueryMemoryRequest(
            search_text="噬菌体治疗机制",
            limit=10,
            min_similarity=0.2
        )
        
        response = await memory_service.query_memory(request)
        print(f"   ✅ 找到 {response.total} 条相关记忆")
        
        for memory in response.memories[:3]:
            print(f"      - [{memory.memory_type.value}] {memory.content[:40]}...")
            if memory.task_id:
                print(f"        关联任务: {memory.task_id}")
                
    except Exception as e:
        print(f"   ❌ 查询任务记忆失败: {e}")
    
    print(f"\n✅ 任务记忆集成示例完成！")


async def main():
    """运行所有示例"""
    print("🚀 Memory-MCP Integration Examples")
    print("=" * 60)
    
    try:
        # 基本操作示例
        await example_basic_memory_operations()
        
        # API使用示例
        await example_api_usage()
        
        # 任务集成示例
        await example_task_memory_integration()
        
        print(f"\n🎉 所有示例运行完成！")
        print(f"\n💡 使用提示:")
        print(f"   - CLI: python -m cli.main --memory-save --memory-content '记忆内容'")
        print(f"   - API: POST /mcp/save_memory")
        print(f"   - 查询: POST /mcp/query_memory")
        print(f"   - 统计: GET /mcp/memory/stats")
        
    except Exception as e:
        print(f"❌ 示例运行失败: {e}")


if __name__ == "__main__":
    asyncio.run(main())