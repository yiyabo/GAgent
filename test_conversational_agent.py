#!/usr/bin/env python3
"""
测试 ConversationalAgent 的功能
使用方法：
1. 先启动后端服务器: python -m uvicorn app.main:app --reload
2. 运行测试: python test_conversational_agent.py
"""

import json
import requests
from app.services.conversational_agent import ConversationalAgent

def test_agent():
    """测试对话代理的各种功能"""
    
    # 创建代理实例
    agent = ConversationalAgent()
    
    print("=" * 50)
    print("测试对话代理功能")
    print("=" * 50)
    
    # 测试用例
    test_cases = [
        {
            "command": "帮助",
            "description": "显示帮助信息"
        },
        {
            "command": "显示所有计划",
            "description": "列出所有计划"
        },
        {
            "command": "创建一个关于机器学习的研究计划",
            "description": "创建新计划"
        },
        {
            "command": "显示计划1的任务",
            "description": "显示任务树"
        },
        {
            "command": "查询计划1的状态",
            "description": "查询状态"
        },
        {
            "command": "执行计划1",
            "description": "执行计划"
        }
    ]
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n测试 {i}: {test['description']}")
        print(f"命令: {test['command']}")
        print("-" * 30)
        
        try:
            # 处理命令
            result = agent.process_command(test['command'])
            
            # 显示结果
            print(f"意图: {result.get('intent')}")
            print(f"成功: {result.get('success')}")
            print(f"响应: {result.get('response')}")
            
            # 显示可视化类型
            viz = result.get('visualization', {})
            print(f"可视化类型: {viz.get('type')}")
            
            # 如果有数据，显示数据概要
            if viz.get('data'):
                data = viz['data']
                if isinstance(data, list):
                    print(f"数据条数: {len(data)}")
                elif isinstance(data, dict):
                    print(f"数据键: {list(data.keys())}")
                    
        except Exception as e:
            print(f"错误: {e}")
            import traceback
            traceback.print_exc()
        
        print()

def test_api_integration():
    """测试与后端API的集成"""
    
    print("\n" + "=" * 50)
    print("测试API集成")
    print("=" * 50)
    
    base_url = "http://127.0.0.1:8000"
    
    # 1. 测试聊天接口
    print("\n1. 创建会话并发送消息")
    
    try:
        # 首先获取或创建一个计划
        plans_resp = requests.get(f"{base_url}/plans")
        plans = plans_resp.json().get("plans", [])
        
        if plans:
            plan_id = plans[0]["id"]
            print(f"使用现有计划 ID: {plan_id}")
        else:
            # 创建新计划
            plan_resp = requests.post(
                f"{base_url}/plans/propose",
                json={"goal": "测试计划"}
            )
            plan_data = plan_resp.json()
            plan_id = plan_data.get("plan_id")
            print(f"创建新计划 ID: {plan_id}")
        
        # 创建会话
        conv_resp = requests.post(
            f"{base_url}/chat/plans/{plan_id}/conversations",
            json={"title": "测试会话"}
        )
        conv_data = conv_resp.json()
        conv_id = conv_data.get("id")
        print(f"创建会话 ID: {conv_id}")
        
        # 发送测试消息
        test_messages = [
            "显示所有计划",
            "创建一个关于数据分析的计划",
            "执行计划1"
        ]
        
        for msg in test_messages:
            print(f"\n发送消息: {msg}")
            msg_resp = requests.post(
                f"{base_url}/chat/conversations/{conv_id}/messages",
                json={"text": msg, "sender": "user"}
            )
            
            if msg_resp.status_code == 200:
                result = msg_resp.json()
                print(f"响应: {result.get('message', {}).get('text', 'N/A')[:100]}...")
                print(f"可视化类型: {result.get('visualization', {}).get('type')}")
            else:
                print(f"错误: {msg_resp.status_code} - {msg_resp.text}")
                
    except Exception as e:
        print(f"API测试失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    import sys
    import os
    
    # 设置环境变量（如果需要）
    if not os.getenv("GLM_API_KEY"):
        os.environ["LLM_MOCK"] = "1"
        print("注意：使用Mock模式（未设置GLM_API_KEY）\n")
    
    # 运行测试
    print("开始测试 ConversationalAgent...")
    
    # 测试代理功能
    test_agent()
    
    # 测试API集成
    print("\n是否测试API集成？（需要先启动服务器）")
    choice = input("输入 y 继续，其他键跳过: ")
    if choice.lower() == 'y':
        test_api_integration()
    
    print("\n测试完成！")