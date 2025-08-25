#!/usr/bin/env python3
"""
线程安全测试模块

专门测试嵌入向量服务的并发安全性，包括：
1. 单例模式的竞态条件测试
2. 缓存操作的线程安全测试  
3. 异步任务管理的并发测试
4. 高并发负载测试
"""

import pytest
import threading
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any

# 导入线程安全的服务
from app.services.thread_safe_embeddings import get_thread_safe_embeddings_service
from app.services.thread_safe_cache import get_thread_safe_embedding_cache


class TestThreadSafetyEmbeddings:
    """线程安全嵌入向量服务测试"""
    
    def test_singleton_thread_safety(self):
        """测试单例模式的线程安全性"""
        services = []
        errors = []
        
        def get_service():
            try:
                service = get_thread_safe_embeddings_service()
                services.append(service)
            except Exception as e:
                errors.append(e)
        
        # 创建多个线程同时获取单例
        threads = []
        for _ in range(20):
            thread = threading.Thread(target=get_service)
            threads.append(thread)
        
        # 同时启动所有线程
        for thread in threads:
            thread.start()
        
        # 等待所有线程完成
        for thread in threads:
            thread.join()
        
        # 验证结果
        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(services) == 20, "Not all threads got service instance"
        
        # 验证所有服务实例都是同一个对象
        first_service = services[0]
        for service in services[1:]:
            assert service is first_service, "Singleton pattern violated"
    
    def test_cache_concurrent_access(self):
        """测试缓存的并发访问安全性"""
        cache = get_thread_safe_embedding_cache()
        
        # 测试数据
        test_texts = [f"test_text_{i}" for i in range(100)]
        test_embeddings = [[random.random() for _ in range(10)] for _ in range(100)]
        
        errors = []
        read_results = []
        
        def concurrent_write():
            """并发写入测试"""
            try:
                for i in range(0, len(test_texts), 2):
                    cache.put(test_texts[i], test_embeddings[i])
            except Exception as e:
                errors.append(f"Write error: {e}")
        
        def concurrent_read():
            """并发读取测试"""
            try:
                results = []
                for text in test_texts[1::2]:  # 读取不同的数据避免冲突
                    result = cache.get(text)
                    results.append(result)
                read_results.append(results)
            except Exception as e:
                errors.append(f"Read error: {e}")
        
        def concurrent_batch_operations():
            """并发批量操作测试"""
            try:
                batch_texts = test_texts[50:60]
                batch_embeddings = test_embeddings[50:60]
                
                # 批量写入
                cache.put_batch(batch_texts, batch_embeddings)
                
                # 批量读取
                results, misses = cache.get_batch(batch_texts)
                assert len(results) == len(batch_texts)
            except Exception as e:
                errors.append(f"Batch operation error: {e}")
        
        # 创建并发测试
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            
            # 提交写入任务
            for _ in range(3):
                futures.append(executor.submit(concurrent_write))
            
            # 提交读取任务
            for _ in range(3):
                futures.append(executor.submit(concurrent_read))
            
            # 提交批量操作任务
            for _ in range(2):
                futures.append(executor.submit(concurrent_batch_operations))
            
            # 等待所有任务完成
            for future in as_completed(futures):
                future.result()
        
        # 验证没有错误发生
        assert len(errors) == 0, f"Concurrent cache access errors: {errors}"
    
    def test_concurrent_embedding_generation(self):
        """测试并发嵌入向量生成"""
        service = get_thread_safe_embeddings_service()
        
        # 测试数据
        test_texts_sets = [
            [f"concurrent_test_{i}_{j}" for j in range(5)]
            for i in range(10)
        ]
        
        results = []
        errors = []
        
        def generate_embeddings(texts: List[str]):
            """生成嵌入向量的任务"""
            try:
                embeddings = service.get_embeddings(texts)
                results.append((texts, embeddings))
                
                # 验证结果完整性
                assert len(embeddings) == len(texts), "Result count mismatch"
                
                for embedding in embeddings:
                    assert isinstance(embedding, list), "Invalid embedding format"
                    
            except Exception as e:
                errors.append(f"Embedding generation error: {e}")
        
        # 并发执行多个嵌入向量生成任务
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = []
            for texts in test_texts_sets:
                futures.append(executor.submit(generate_embeddings, texts))
            
            # 等待所有任务完成
            for future in as_completed(futures):
                future.result()
        
        # 验证结果
        assert len(errors) == 0, f"Concurrent embedding generation errors: {errors}"
        assert len(results) == len(test_texts_sets), "Not all tasks completed"
    
    def test_async_task_management_thread_safety(self):
        """测试异步任务管理的线程安全性"""
        service = get_thread_safe_embeddings_service()
        
        completed_callbacks = []
        errors = []
        futures = []
        
        def completion_callback(embeddings):
            """完成回调"""
            completed_callbacks.append(len(embeddings))
        
        def error_callback(embeddings, error=None):
            """错误回调"""
            if error:
                errors.append(error)
        
        def submit_async_tasks():
            """提交异步任务"""
            try:
                for i in range(5):
                    texts = [f"async_test_{threading.get_ident()}_{i}_{j}" for j in range(3)]
                    future = service.get_embeddings_async(texts, completion_callback)
                    futures.append(future)
            except Exception as e:
                errors.append(f"Task submission error: {e}")
        
        # 并发提交异步任务
        with ThreadPoolExecutor(max_workers=5) as executor:
            submit_futures = []
            for _ in range(5):
                submit_futures.append(executor.submit(submit_async_tasks))
            
            # 等待所有提交完成
            for future in as_completed(submit_futures):
                future.result()
        
        # 等待所有异步任务完成
        for future in futures:
            try:
                future.result(timeout=30)
            except Exception as e:
                errors.append(f"Async task error: {e}")
        
        # 验证结果
        assert len(errors) == 0, f"Async task management errors: {errors}"
        
        # 检查任务状态
        status = service.get_background_task_status()
        assert status['thread_safe'] == True, "Service should be thread-safe"
    
    def test_high_concurrency_stress(self):
        """高并发压力测试"""
        service = get_thread_safe_embeddings_service()
        cache = get_thread_safe_embedding_cache()
        
        # 测试参数
        num_threads = 20
        operations_per_thread = 10
        
        start_time = time.time()
        errors = []
        operations_completed = []
        
        def stress_worker(worker_id: int):
            """压力测试工作线程"""
            try:
                completed_ops = 0
                
                for i in range(operations_per_thread):
                    # 随机选择操作类型
                    operation = random.choice(['get_embeddings', 'cache_operations', 'async_tasks'])
                    
                    if operation == 'get_embeddings':
                        texts = [f"stress_test_{worker_id}_{i}_{j}" for j in range(random.randint(1, 3))]
                        embeddings = service.get_embeddings(texts)
                        assert len(embeddings) == len(texts)
                    
                    elif operation == 'cache_operations':
                        text = f"cache_stress_{worker_id}_{i}"
                        embedding = [random.random() for _ in range(10)]
                        
                        # 写入缓存
                        cache.put(text, embedding)
                        
                        # 读取缓存
                        cached_embedding = cache.get(text)
                        if cached_embedding:
                            assert len(cached_embedding) == len(embedding)
                    
                    elif operation == 'async_tasks':
                        text = f"async_stress_{worker_id}_{i}"
                        future = service.get_single_embedding_async(text)
                        result = future.result(timeout=10)
                        assert isinstance(result, list)
                    
                    completed_ops += 1
                    
                    # 随机延迟以模拟真实使用
                    if random.random() < 0.1:
                        time.sleep(0.01)
                
                operations_completed.append(completed_ops)
                
            except Exception as e:
                errors.append(f"Worker {worker_id} error: {e}")
        
        # 启动压力测试
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            futures = []
            for worker_id in range(num_threads):
                futures.append(executor.submit(stress_worker, worker_id))
            
            # 等待所有工作线程完成
            for future in as_completed(futures):
                future.result()
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # 验证结果
        assert len(errors) == 0, f"Stress test errors: {errors}"
        assert len(operations_completed) == num_threads, "Not all workers completed"
        
        total_operations = sum(operations_completed)
        expected_operations = num_threads * operations_per_thread
        assert total_operations == expected_operations, f"Operations mismatch: {total_operations} vs {expected_operations}"
        
        # 性能统计
        ops_per_second = total_operations / total_time
        logger.info(f"Stress test completed: {total_operations} operations in {total_time:.2f}s ({ops_per_second:.2f} ops/sec)")
        
        # 验证服务状态
        service_info = service.get_service_info()
        assert service_info['thread_safe'] == True, "Service should remain thread-safe"
        
        cache_stats = cache.get_stats()
        assert cache_stats is not None, "Cache should provide stats"
    
    def test_resource_cleanup_thread_safety(self):
        """测试资源清理的线程安全性"""
        service = get_thread_safe_embeddings_service()
        
        # 创建大量异步任务
        futures = []
        for i in range(50):
            text = f"cleanup_test_{i}"
            future = service.get_single_embedding_async(text)
            futures.append(future)
        
        # 等待一半任务完成
        for i in range(0, 25):
            futures[i].result()
        
        # 取消剩余任务
        cancelled_count = service.cancel_background_tasks()
        assert cancelled_count >= 0, "Should return valid cancellation count"
        
        # 等待任务清理
        status = service.wait_for_background_tasks(timeout=10)
        assert status['status'] in ['completed', 'no_active_tasks'], "Tasks should be cleaned up"
        
        # 验证最终状态
        final_status = service.get_background_task_status()
        assert final_status['active_tasks'] == 0, "No active tasks should remain"


# 辅助函数和常量
import logging
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    # 直接运行测试
    test_instance = TestThreadSafetyEmbeddings()
    
    print("Running thread safety tests...")
    
    try:
        test_instance.test_singleton_thread_safety()
        print("✓ Singleton thread safety test passed")
        
        test_instance.test_cache_concurrent_access()
        print("✓ Cache concurrent access test passed")
        
        test_instance.test_concurrent_embedding_generation()
        print("✓ Concurrent embedding generation test passed")
        
        test_instance.test_async_task_management_thread_safety()
        print("✓ Async task management thread safety test passed")
        
        test_instance.test_high_concurrency_stress()
        print("✓ High concurrency stress test passed")
        
        test_instance.test_resource_cleanup_thread_safety()
        print("✓ Resource cleanup thread safety test passed")
        
        print("\n🎉 All thread safety tests passed!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        raise