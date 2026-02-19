#!/usr/bin/env python3
"""
Bio Tools API 
======================
 POST  bio_tools

:  http://localhost:9000
"""

import requests
import json

BASE_URL = "http://localhost:9000"


def list_all_tools():
    """ 1: """
    print("=" * 60)
    print(" 1:  bio_tools")
    print("=" * 60)
    
    #  1:  Tool Box 
    url = f"{BASE_URL}/api/v1/tools/available"
    
    #  2:  bio_tools 
    url = f"{BASE_URL}/api/v1/tools/bio-tools/list"
    
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            print(f"✅ ! : {data.get('count', 0)}")
            
            # 
            by_category = data.get('tools_by_category', {})
            for category, tools in sorted(by_category.items()):
                print(f"\n{category}:")
                for tool in tools:
                    print(f"  - {tool['name']}: {', '.join(tool['operations'])}")
        else:
            print(f"❌ : {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ : {e}")
        print("\n: :")
        print("  cd /home/zczhao/GAgent && bash start_backend.sh")


def execute_seqkit_stats():
    """ 2:  SeqKit stats"""
    print("\n" + "=" * 60)
    print(" 2:  SeqKit stats")
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
                print("✅ !")
                print(f"\n: {result.get('command', '')[:80]}...")
                print(f"\n:")
                print(result.get('stdout', ''))
                print(f": {result.get('duration_seconds', 0):.2f} ")
            else:
                print(f"❌ : {data.get('result', {}).get('error', 'Unknown error')}")
        else:
            print(f"❌ : {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ : {e}")


def execute_tool_help(tool_name: str):
    """ 3: """
    print("\n" + "=" * 60)
    print(f" 3:  {tool_name} ")
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
                print(f"✅ !")
                print(f"\n: {result.get('tool', '')}")
                print(f": {result.get('description', '')}")
                print(f": {result.get('image', '')}")
                print(f"\n:")
                for op_name, op_info in result.get('operations', {}).items():
                    print(f"  - {op_name}: {op_info.get('description', '')}")
            else:
                print(f"❌ : {data.get('result', {}).get('error', 'Unknown error')}")
        else:
            print(f"❌ : {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ : {e}")


def execute_new_phage_tool():
    """ 4: """
    print("\n" + "=" * 60)
    print(" 4:  (checkv)")
    print("=" * 60)
    
    url = f"{BASE_URL}/api/v1/tools/bio-tools"
    
    # : 
    #  help 
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
                print(f"✅ !")
                print(f"\n: {result.get('tool', '')}")
                print(f": {result.get('description', '')}")
                print(f"\n:")
                for op_name, op_info in result.get('operations', {}).items():
                    print(f"  - {op_name}: {op_info.get('description', '')}")
                    print(f"    : {', '.join(op_info.get('extra_params', []))}")
            else:
                print(f"❌ : {data.get('result', {}).get('error', 'Unknown error')}")
        else:
            print(f"❌ : {response.status_code}")
            print(response.text[:500])
    except Exception as e:
        print(f"❌ : {e}")


def main():
    """ - """
    print("🧪 Bio Tools API ")
    print("=" * 60)
    print(f"API : {BASE_URL}")
    print("=" * 60)
    
    # 
    list_all_tools()
    execute_seqkit_stats()
    execute_tool_help("genomad")
    execute_new_phage_tool()
    
    print("\n" + "=" * 60)
    print("!")
    print("=" * 60)


if __name__ == '__main__':
    main()
