#!/usr/bin/env python
"""
A-mem集成测试脚本

测试A-mem服务的基本功能和与主系统的集成
"""

import asyncio
import httpx
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def test_amem_health():
    """测试A-mem服务健康状态"""
    print("\n" + "="*60)
    print("🏥 测试A-mem服务健康状态")
    print("="*60)
    
    try:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, timeout=30.0, http2=False) as client:
            response = await client.get("http://127.0.0.1:8001/health")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ A-mem服务正常运行")
                print(f"   状态: {data.get('status')}")
                print(f"   记忆数量: {data.get('memory_count')}")
                print(f"   时间戳: {data.get('timestamp')}")
                return True
            else:
                print(f"❌ A-mem服务响应异常: {response.status_code}")
                print(f"   响应内容: {response.text}")
                return False
                
    except httpx.ConnectError as e:
        print(f"❌ 无法连接到A-mem服务: {e}")
        print("   请确保A-mem服务已启动: bash scripts/start_amem.sh")
        return False
    except Exception as e:
        print(f"❌ 请求失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_add_memory():
    """测试添加记忆"""
    print("\n" + "="*60)
    print("➕ 测试添加记忆")
    print("="*60)
    
    test_content = """
# Claude Code执行记录

## 任务描述
训练一个简单的线性回归模型

## 执行结果
状态: ✅ 成功
工作目录: runtime/test_task_abc123

## 标准输出
Model trained successfully
Accuracy: 0.95
Saved to: model.pkl

## 关键发现
- 使用sklearn的LinearRegression效果很好
- 训练时间约10秒
- 数据预处理很重要
"""
    
    try:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, http2=False) as client:
            response = await client.post(
                "http://127.0.0.1:8001/add_memory",
                json={
                    "content": test_content,
                    "tags": ["claude_code", "execution", "success", "test"],
                    "context": "机器学习模型训练"
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                memory_id = data.get("memory_id")
                print(f"✅ 记忆添加成功")
                print(f"   记忆ID: {memory_id}")
                return memory_id
            else:
                print(f"❌ 添加记忆失败: {response.status_code}")
                print(f"   响应: {response.text}")
                return None
                
    except Exception as e:
        print(f"❌ 添加记忆时出错: {e}")
        return None


async def test_query_memory(query: str = "训练模型"):
    """测试查询记忆"""
    print("\n" + "="*60)
    print(f"🔍 测试查询记忆: '{query}'")
    print("="*60)
    
    try:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, http2=False) as client:
            response = await client.post(
                "http://127.0.0.1:8001/query_memory",
                json={
                    "query": query,
                    "top_k": 3
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                count = data.get("count", 0)
                
                print(f"✅ 查询成功，找到 {count} 条相关记忆")
                
                for i, result in enumerate(results, 1):
                    print(f"\n--- 记忆 {i} ---")
                    print(f"ID: {result.get('id', 'N/A')[:16]}...")
                    print(f"相似度: {result.get('score', 0):.3f}")
                    print(f"上下文: {result.get('context', 'N/A')}")
                    print(f"标签: {', '.join(result.get('tags', []))}")
                    content = result.get('content', '')
                    print(f"内容预览: {content[:100]}...")
                
                return results
            else:
                print(f"❌ 查询失败: {response.status_code}")
                return []
                
    except Exception as e:
        print(f"❌ 查询时出错: {e}")
        return []


async def test_amem_client():
    """测试A-mem客户端"""
    print("\n" + "="*60)
    print("🔧 测试A-mem客户端")
    print("="*60)
    
    try:
        from app.services.amem_client import AMemClient
        
        # 创建客户端
        client = AMemClient(
            base_url="http://localhost:8001",
            enabled=True
        )
        
        # 测试健康检查
        is_healthy = await client.health_check()
        if is_healthy:
            print("✅ 客户端健康检查通过")
        else:
            print("❌ 客户端健康检查失败")
            return False
        
        # 测试查询经验
        experiences = await client.query_experiences(
            query="训练机器学习模型",
            top_k=2
        )
        print(f"✅ 查询到 {len(experiences)} 条经验")
        
        # 测试格式化经验
        if experiences:
            formatted = client.format_experiences_for_llm(experiences)
            print(f"✅ 格式化经验成功，长度: {len(formatted)} 字符")
        
        # 关闭客户端
        await client.close()
        print("✅ 客户端测试完成")
        return True
        
    except Exception as e:
        print(f"❌ 客户端测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主测试流程"""
    print("\n" + "🚀 " + "="*56)
    print("   A-mem集成测试")
    print("="*60 + "\n")
    
    # 测试1: 健康检查
    health_ok = await test_amem_health()
    if not health_ok:
        print("\n❌ A-mem服务未运行，测试终止")
        print("   请先启动A-mem服务: bash scripts/start_amem.sh")
        return
    
    # 测试2: 添加记忆
    memory_id = await test_add_memory()
    if not memory_id:
        print("\n⚠️  添加记忆失败，跳过后续测试")
    
    # 等待A-mem处理
    print("\n⏳ 等待A-mem处理记忆...")
    await asyncio.sleep(3)
    
    # 测试3: 查询记忆
    results = await test_query_memory("训练模型")
    
    # 测试4: 客户端测试
    client_ok = await test_amem_client()
    
    # 总结
    print("\n" + "="*60)
    print("📊 测试总结")
    print("="*60)
    print(f"✅ A-mem服务健康: {health_ok}")
    print(f"✅ 添加记忆: {memory_id is not None}")
    print(f"✅ 查询记忆: {len(results) > 0}")
    print(f"✅ 客户端功能: {client_ok}")
    
    if health_ok and memory_id and client_ok:
        print("\n🎉 所有测试通过！A-mem集成正常工作")
    else:
        print("\n⚠️  部分测试失败，请检查日志")


if __name__ == "__main__":
    asyncio.run(main())
