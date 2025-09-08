#!/usr/bin/env python3
"""
测试正则表达式匹配
"""
import re

def test_patterns():
    user_input = "创建一个有关与因果推断的report任务"
    print(f"测试输入: '{user_input}'")
    print()
    
    patterns = [
        (r"(添加|增加|新增|记录|创建|建立)(一个|一条)?(待办|任务|todo)([:： ]+)?(.+)$", "原格式"),
        (r"(添加|增加|新增|记录|创建|建立)(一个|一条)?(.+?)(待办|任务|task|todo)$", "新格式"),      
        (r"(记录|创建|建立|新建)(.+?)(的)?(任务|task|todo)", "简化格式"),
    ]
    
    for i, (pattern, name) in enumerate(patterns, 1):
        print(f"模式{i} ({name}):")
        print(f"  正则: {pattern}")
        
        m = re.search(pattern, user_input, re.I)
        if m:
            print(f"  ✅ 匹配成功!")
            print(f"  分组: {m.groups()}")
            
            # 根据模式提取内容
            if i == 1:  # 原格式
                content = m.group(5).strip() if len(m.groups()) >= 5 else ""
            elif i == 2:  # 新格式  
                content = m.group(3).strip() if len(m.groups()) >= 3 else ""
            elif i == 3:  # 简化格式
                content = m.group(2).strip() if len(m.groups()) >= 2 else ""
            else:
                content = ""
                
            print(f"  📝 提取内容: '{content}'")
        else:
            print(f"  ❌ 没有匹配")
        print()

if __name__ == "__main__":
    test_patterns()
