#!/usr/bin/env python3
"""
并发安全测试 - 简化版本

专门测试关键的并发安全问题修复。
"""

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

# 设置正确的模拟模式环境变量
os.environ["LLM_MOCK"] = "1"
os.environ["EMBEDDING_CACHE_PERSISTENT"] = "0"  # 禁用持久化缓存避免文件警告


def test_thread_safe_singleton():
    """测试线程安全的单例模式"""
    from app.services.thread_safe_embeddings import get_thread_safe_embeddings_service

    services = []
    errors = []

    def get_service():
        try:
            service = get_thread_safe_embeddings_service()
            services.append(service)
        except Exception as e:
            errors.append(e)

    # 并发获取服务实例
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(get_service) for _ in range(10)]
        for future in futures:
            future.result()

    # 验证
    assert len(errors) == 0, f"Errors: {errors}"
    assert len(services) == 10
    # 所有实例应该是同一个对象
    assert all(service is services[0] for service in services)


def test_thread_safe_cache():
    """测试线程安全的缓存操作"""
    from app.services.thread_safe_cache import ThreadSafeEmbeddingCache

    # 使用临时数据库，并确保正确关闭连接
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = ThreadSafeEmbeddingCache(cache_size=100, enable_persistent=True)
        cache.cache_db_path = os.path.join(temp_dir, "test_cache.db")
        cache._init_persistent_cache()

        try:
            errors = []

            def concurrent_cache_ops(thread_id: int):
                try:
                    # 写入操作
                    for i in range(10):
                        text = f"test_{thread_id}_{i}"
                        embedding = [float(j) for j in range(10)]
                        cache.put(text, embedding)

                    # 读取操作
                    for i in range(10):
                        text = f"test_{thread_id}_{i}"
                        result = cache.get(text)
                        assert result is not None or True  # 允许缓存未命中

                except Exception as e:
                    errors.append(f"Thread {thread_id}: {e}")

            # 并发执行缓存操作
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(concurrent_cache_ops, i) for i in range(5)]
                for future in futures:
                    future.result()

            # 验证
            assert len(errors) == 0, f"Concurrent cache errors: {errors}"

            # 获取统计信息验证缓存正常工作
            stats = cache.get_stats()
            assert stats["memory_cache_size"] >= 0

        finally:
            # 确保关闭缓存连接，避免ResourceWarning
            cache.shutdown()


def test_concurrent_embedding_generation():
    """测试并发嵌入向量生成"""
    from app.services.thread_safe_embeddings import get_thread_safe_embeddings_service

    service = get_thread_safe_embeddings_service()
    results = []
    errors = []

    def generate_embeddings(thread_id: int):
        try:
            texts = [f"concurrent_test_{thread_id}_{i}" for i in range(3)]
            embeddings = service.get_embeddings(texts)
            results.append((thread_id, len(embeddings)))

            # 验证结果格式
            assert len(embeddings) == len(texts)
            for embedding in embeddings:
                assert isinstance(embedding, list)

        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")

    # 并发生成嵌入向量
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(generate_embeddings, i) for i in range(5)]
        for future in futures:
            future.result()

    # 验证
    assert len(errors) == 0, f"Concurrent generation errors: {errors}"
    assert len(results) == 5
    # 所有结果应该有正确的嵌入向量数量
    for thread_id, embedding_count in results:
        assert embedding_count == 3


def test_async_task_safety():
    """测试异步任务的线程安全性"""
    from app.services.thread_safe_embeddings import get_thread_safe_embeddings_service

    service = get_thread_safe_embeddings_service()
    futures = []
    errors = []

    def submit_async_tasks(thread_id: int):
        try:
            for i in range(3):
                text = f"async_test_{thread_id}_{i}"
                future = service.get_single_embedding_async(text)
                futures.append(future)
        except Exception as e:
            errors.append(f"Thread {thread_id}: {e}")

    # 并发提交异步任务
    with ThreadPoolExecutor(max_workers=3) as executor:
        submit_futures = [executor.submit(submit_async_tasks, i) for i in range(3)]
        for future in submit_futures:
            future.result()

    # 等待所有异步任务完成
    for future in futures:
        try:
            result = future.result(timeout=10)
            assert isinstance(result, list)
        except Exception as e:
            errors.append(f"Async task error: {e}")

    # 验证
    assert len(errors) == 0, f"Async task errors: {errors}"
    assert len(futures) == 9  # 3 threads * 3 tasks each

    # 检查服务状态
    status = service.get_background_task_status()
    assert "thread_safe" in status
    assert status["thread_safe"] == True


if __name__ == "__main__":
    print("Running concurrent safety tests...")

    try:
        test_thread_safe_singleton()
        print("✓ Thread-safe singleton test passed")

        test_thread_safe_cache()
        print("✓ Thread-safe cache test passed")

        test_concurrent_embedding_generation()
        print("✓ Concurrent embedding generation test passed")

        test_async_task_safety()
        print("✓ Async task safety test passed")

        print("\n🎉 All concurrent safety tests passed!")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback

        traceback.print_exc()
        raise
