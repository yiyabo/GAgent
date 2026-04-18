#!/usr/bin/env python
"""
A-mem

A-mem
"""

import asyncio
import httpx
import sys
from pathlib import Path

# 
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


async def test_amem_health():
    """A-mem"""
    print("\n" + "="*60)
    print("🏥 A-mem")
    print("="*60)
    
    try:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, timeout=30.0, http2=False) as client:
            response = await client.get("http://127.0.0.1:8001/health")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ A-mem")
                print(f"   : {data.get('status')}")
                print(f"   : {data.get('memory_count')}")
                print(f"   : {data.get('timestamp')}")
                return True
            else:
                print(f"❌ A-mem: {response.status_code}")
                print(f"   : {response.text}")
                return False
                
    except httpx.ConnectError as e:
        print(f"❌ A-mem: {e}")
        print("   A-mem: bash scripts/start_amem.sh")
        return False
    except Exception as e:
        print(f"❌ : {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_add_memory():
    """"""
    print("\n" + "="*60)
    print("➕ ")
    print("="*60)
    
    test_content = """
# Claude Code

## 


## 
: ✅ 
: runtime/test_task_abc123

## 
Model trained successfully
Accuracy: 0.95
Saved to: model.pkl

## 
- sklearnLinearRegression
- 10
- 
"""
    
    try:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, http2=False) as client:
            response = await client.post(
                "http://127.0.0.1:8001/add_memory",
                json={
                    "content": test_content,
                    "tags": ["claude_code", "execution", "success", "test"],
                    "context": ""
                },
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                memory_id = data.get("memory_id")
                print(f"✅ ")
                print(f"   ID: {memory_id}")
                return memory_id
            else:
                print(f"❌ : {response.status_code}")
                print(f"   : {response.text}")
                return None
                
    except Exception as e:
        print(f"❌ : {e}")
        return None


async def test_query_memory(query: str = ""):
    """"""
    print("\n" + "="*60)
    print(f"🔍 : '{query}'")
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
                
                print(f"✅ ， {count} ")
                
                for i, result in enumerate(results, 1):
                    print(f"\n---  {i} ---")
                    print(f"ID: {result.get('id', 'N/A')[:16]}...")
                    print(f": {result.get('score', 0):.3f}")
                    print(f": {result.get('context', 'N/A')}")
                    print(f": {', '.join(result.get('tags', []))}")
                    content = result.get('content', '')
                    print(f": {content[:100]}...")
                
                return results
            else:
                print(f"❌ : {response.status_code}")
                return []
                
    except Exception as e:
        print(f"❌ : {e}")
        return []


async def test_amem_client():
    """A-mem"""
    print("\n" + "="*60)
    print("🔧 A-mem")
    print("="*60)
    
    try:
        from app.services.amem_client import AMemClient
        
        # 
        client = AMemClient(
            base_url="http://localhost:8001",
            enabled=True
        )
        
        # 
        is_healthy = await client.health_check()
        if is_healthy:
            print("✅ ")
        else:
            print("❌ ")
            return False
        
        # 
        experiences = await client.query_experiences(
            query="",
            top_k=2
        )
        print(f"✅  {len(experiences)} ")
        
        # 
        if experiences:
            formatted = client.format_experiences_for_llm(experiences)
            print(f"✅ ，: {len(formatted)} ")
        
        # 
        await client.close()
        print("✅ ")
        return True
        
    except Exception as e:
        print(f"❌ : {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """"""
    print("\n" + "🚀 " + "="*56)
    print("   A-mem")
    print("="*60 + "\n")
    
    # 1: 
    health_ok = await test_amem_health()
    if not health_ok:
        print("\n❌ A-mem，")
        print("   A-mem: bash scripts/start_amem.sh")
        return
    
    # 2: 
    memory_id = await test_add_memory()
    if not memory_id:
        print("\n⚠️  ，")
    
    # A-mem
    print("\n⏳ A-mem...")
    await asyncio.sleep(3)
    
    # 3: 
    results = await test_query_memory("")
    
    # 4: 
    client_ok = await test_amem_client()
    
    # 
    print("\n" + "="*60)
    print("📊 ")
    print("="*60)
    print(f"✅ A-mem: {health_ok}")
    print(f"✅ : {memory_id is not None}")
    print(f"✅ : {len(results) > 0}")
    print(f"✅ : {client_ok}")
    
    if health_ok and memory_id and client_ok:
        print("\n🎉 ！A-mem")
    else:
        print("\n⚠️  ，")


if __name__ == "__main__":
    asyncio.run(main())
