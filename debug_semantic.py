#!/usr/bin/env python3
import tempfile
import os
import sys
sys.path.append('/Users/apple/LLM/agent')

def debug_semantic_test():
    with tempfile.TemporaryDirectory() as tmp_dir:
        test_db = os.path.join(tmp_dir, "semantic_ctx.db")
        
        # 清理之前的模块缓存
        modules_to_clear = [m for m in sys.modules.keys() if m.startswith('app.')]
        for m in modules_to_clear:
            if m in sys.modules:
                del sys.modules[m]
        
        # 设置数据库路径
        import app.database
        app.database.DB_PATH = test_db
        
        from app.database import init_db
        from app.repository.tasks import SqliteTaskRepository
        from app.services.context import gather_context
        from app.services.embeddings import get_embeddings_service
        
        init_db()
        repo = SqliteTaskRepository()
        
        # Create two tasks in same plan; only B has output
        a = repo.create_task("[T] A", status="pending", priority=1)
        b = repo.create_task("[T] B", status="pending", priority=2)
        repo.upsert_task_input(a, "banana apple")
        repo.upsert_task_output(b, "banana banana something relevant")
        
        print(f"Task A ID: {a}, input: {repo.get_task_input_prompt(a)}")
        print(f"Task B ID: {b}, output: {repo.get_task_output_content(b)}")
        
        # Generate embedding for task B to enable semantic retrieval
        embeddings_service = get_embeddings_service()
        embedding = embeddings_service.get_single_embedding("banana banana something relevant")
        print(f"Generated embedding: {embedding is not None}")
        if embedding:
            embedding_json = embeddings_service.embedding_to_json(embedding)
            repo.store_task_embedding(b, embedding_json)
            print(f"Stored embedding for task {b}")
            
            # 验证embedding存储
            stored = repo.get_task_embedding(b)
            print(f"Stored embedding retrieved: {stored is not None}")
            if stored:
                print(f"Stored embedding keys: {stored.keys()}")
                print(f"Stored embedding content: {stored}")
            
            # 直接从数据库查询
            from app.database import get_db
            with get_db() as conn:
                row = conn.execute('''
                    SELECT task_id, embedding_vector, embedding_model, created_at, updated_at
                    FROM task_embeddings 
                    WHERE task_id = ?
                ''', (b,)).fetchone()
                print(f"Direct DB query result: {row}")
                if row:
                    print(f"Row type: {type(row)}")
                    print(f"Row keys (if dict): {row.keys() if hasattr(row, 'keys') else 'Not a dict'}")
        
        # 测试检索服务
        from app.services.retrieval import get_retrieval_service
        retrieval_service = get_retrieval_service()
        
        # 直接测试相似度计算
        query_embedding = embeddings_service.get_single_embedding("banana apple")
        
        results = retrieval_service.search("banana apple", k=1, min_similarity=0.0)
        print(f"Retrieval results (min_similarity=0.0): {results}")
        
        results = retrieval_service.search("banana apple", k=1, min_similarity=0.1)
        print(f"Retrieval results (min_similarity=0.1): {results}")
        
        bundle = gather_context(a, repo=repo, include_deps=False, include_plan=False, semantic_k=1)
        secs = bundle.get("sections", [])
        
        print("All sections:")
        for s in secs:
            print(f"  - {s.get('kind')}: task_id={s.get('task_id')}, name={s.get('short_name')}")
        
        retrieved_section = next((s for s in secs if s.get("kind") == "retrieved" and s.get("task_id") == b), None)
        print(f"Found retrieved section for task {b}: {retrieved_section is not None}")
        
        return any(s.get("kind") == "retrieved" and s.get("task_id") == b for s in secs)

if __name__ == "__main__":
    os.environ['RETRIEVAL_DEBUG'] = '1'
    result = debug_semantic_test()
    print(f"Test result: {result}")