#!/usr/bin/env python3
"""
Bio Tools API 调用示例
======================
展示如何通过 POST 请求调用 bio_tools

注意: 需要后端服务运行在 http://localhost:9000
"""

import requests
import json

BASE_URL = "http://localhost:9000"


def list_all_tools():
    """示例 1: 列出所有生物信息学工具"""
    print("=" * 60)
    print("示例 1: 列出所有 bio_tools")
    print("=" * 60)
    
    # 方法 1: 使用 Tool Box 的通用接口
    url = f"{BASE_URL}/api/v1/tools/available"
    
    # 方法 2: 使用 bio_tools 专用接口
    url = f"{BASE_URL}/api/v1/tools/bio-tools/list"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ 成功! 工具总数: {data.get('count', 0)}")
            
            # 打印按类别分组的工具
            by_category = data.get('tools_by_category', {})
            for category, tools in sorted(by_category.items()):
                print(f"\n{category}:")
                for tool in tools:
                    print(f"  - {tool['name']}: {', '.join(tool['operations'])}")
        else:
            print(f"❌ 请求失败: {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ 错误: {e}")
        print("\n提示: 确保后端服务已启动:")
        print("  cd /home/zczhao/GAgent && bash start_backend.sh")


def execute_seqkit_stats():
    """示例 2: 执行 SeqKit stats"""
    print("\n" + "=" * 60)
    print("示例 2: 执行 SeqKit stats")
    print("=" * 60)
    
    url = f"{BASE_URL}/api/v1/tools/bio-tools"
    
    payload = {
        "tool_name": "seqkit",
        "operation": "stats",
        "input_file": "/home/zczhao/GAgent/tool_box/bio_tools/test_data/contigs.fasta"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                result = data.get('result', {})
                print("✅ 执行成功!")
                print(f"\n命令: {result.get('command', '')[:80]}...")
                print(f"\n输出:")
                print(result.get('stdout', ''))
                print(f"耗时: {result.get('duration_seconds', 0):.2f} 秒")
            else:
                print(f"❌ 执行失败: {data.get('result', {}).get('error', 'Unknown error')}")
        else:
            print(f"❌ 请求失败: {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ 错误: {e}")


def execute_tool_help(tool_name: str):
    """示例 3: 获取工具帮助信息"""
    print("\n" + "=" * 60)
    print(f"示例 3: 获取 {tool_name} 帮助信息")
    print("=" * 60)
    
    url = f"{BASE_URL}/api/v1/tools/bio-tools"
    
    payload = {
        "tool_name": tool_name,
        "operation": "help"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                result = data.get('result', {})
                print(f"✅ 成功!")
                print(f"\n工具: {result.get('tool', '')}")
                print(f"描述: {result.get('description', '')}")
                print(f"镜像: {result.get('image', '')}")
                print(f"\n可用操作:")
                for op_name, op_info in result.get('operations', {}).items():
                    print(f"  - {op_name}: {op_info.get('description', '')}")
            else:
                print(f"❌ 失败: {data.get('result', {}).get('error', 'Unknown error')}")
        else:
            print(f"❌ 请求失败: {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ 错误: {e}")


def execute_new_phage_tool():
    """示例 4: 执行新增的噬菌体分析工具"""
    print("\n" + "=" * 60)
    print("示例 4: 测试新增的噬菌体分析工具 (checkv)")
    print("=" * 60)
    
    url = f"{BASE_URL}/api/v1/tools/bio-tools"
    
    # 注意: 需要实际的病毒序列文件才能执行
    # 这里仅展示 help 操作
    payload = {
        "tool_name": "checkv",
        "operation": "help"
    }
    
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            data = response.json()
            if data.get('success'):
                result = data.get('result', {})
                print(f"✅ 成功!")
                print(f"\n工具: {result.get('tool', '')}")
                print(f"描述: {result.get('description', '')}")
                print(f"\n可用操作:")
                for op_name, op_info in result.get('operations', {}).items():
                    print(f"  - {op_name}: {op_info.get('description', '')}")
                    print(f"    参数: {', '.join(op_info.get('extra_params', []))}")
            else:
                print(f"❌ 失败: {data.get('result', {}).get('error', 'Unknown error')}")
        else:
            print(f"❌ 请求失败: {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ 错误: {e}")


def main():
    """主函数 - 运行所有示例"""
    print("🧪 Bio Tools API 调用示例")
    print("=" * 60)
    print(f"API 基础地址: {BASE_URL}")
    print("=" * 60)
    
    # 运行示例
    list_all_tools()
    execute_seqkit_stats()
    execute_tool_help("genomad")
    execute_new_phage_tool()
    
    print("\n" + "=" * 60)
    print("示例完成!")
    print("=" * 60)


if __name__ == '__main__':
    main()
