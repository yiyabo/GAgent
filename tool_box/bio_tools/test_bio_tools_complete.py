#!/usr/bin/env python3
"""
Bio Tools 完整测试脚本
======================
测试所有 bio_tools 的基本功能

使用方法:
    cd /home/zczhao/GAgent
    PYTHONPATH=/home/zczhao/GAgent:$PYTHONPATH python tool_box/bio_tools/test_bio_tools_complete.py
"""

import asyncio
import json
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tool_box import execute_tool

# 测试数据路径
TEST_DATA_DIR = Path(__file__).parent / "test_data"
CONTIGS_FA = TEST_DATA_DIR / "contigs.fasta"


class BioToolsTester:
    """Bio Tools 测试类"""
    
    def __init__(self):
        self.results = []
        self.passed = 0
        self.failed = 0
    
    async def run_all_tests(self):
        """运行所有测试"""
        print("🧪 Bio Tools 完整测试开始")
        print("=" * 70)
        
        # 1. 基础工具测试
        await self.test_tool_list()
        await self.test_seqkit_stats()
        await self.test_seqkit_head()
        
        # 2. 序列比对工具
        await self.test_blast_help()
        
        # 3. 组装相关工具
        await self.test_prodigal_help()
        
        # 4. 新增工具测试
        await self.test_genomad_help()
        await self.test_checkv_help()
        await self.test_virsorter2_help()
        
        # 5. 新注册的核心工具测试
        await self.test_bwa_help()
        await self.test_bowtie2_help()
        await self.test_mmseqs2_help()
        await self.test_trim_galore_help()
        await self.test_ngmlr_help()
        await self.test_sniffles2_help()
        await self.test_fastani_help()
        
        # 6. 新注册的噬菌体和工作流工具测试
        await self.test_vibrant_help()
        await self.test_iphop_help()
        await self.test_nextflow_help()
        await self.test_snakemake_help()
        
        # 汇总
        self.print_summary()
    
    async def test_tool_list(self):
        """测试 1: 列出所有工具"""
        print("\n📋 测试 1: 列出所有 bio_tools")
        print("-" * 70)
        
        result = await execute_tool('bio_tools', tool_name='list')
        
        if result.get('success'):
            tools = result.get('tools', [])
            print(f"✅ 成功! 共有 {len(tools)} 个工具")
            
            # 按类别分组
            by_category = {}
            for tool in tools:
                cat = tool['category']
                by_category.setdefault(cat, []).append(tool['name'])
            
            print("\n按类别分布:")
            for cat, tool_names in sorted(by_category.items()):
                print(f"  {cat}: {', '.join(tool_names)}")
            
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('tool_list', result.get('success', False)))
        return result
    
    async def test_seqkit_stats(self):
        """测试 2: SeqKit stats"""
        print("\n📊 测试 2: SeqKit stats")
        print("-" * 70)
        
        if not CONTIGS_FA.exists():
            print(f"⚠️  跳过: 测试数据不存在 {CONTIGS_FA}")
            return None
        
        result = await execute_tool('bio_tools',
            tool_name='seqkit',
            operation='stats',
            input_file=str(CONTIGS_FA)
        )
        
        if result.get('success'):
            print("✅ 成功!")
            print(f"  耗时: {result.get('duration_seconds', 0):.2f} 秒")
            print(f"  输出:\n{result.get('stdout', '')[:200]}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('seqkit_stats', result.get('success', False)))
        return result
    
    async def test_seqkit_head(self):
        """测试 3: SeqKit head"""
        print("\n📄 测试 3: SeqKit head")
        print("-" * 70)
        
        if not CONTIGS_FA.exists():
            print(f"⚠️  跳过: 测试数据不存在")
            return None
        
        result = await execute_tool('bio_tools',
            tool_name='seqkit',
            operation='head',
            input_file=str(CONTIGS_FA),
            params={'count': '1'}
        )
        
        if result.get('success'):
            print("✅ 成功!")
            stdout = result.get('stdout', '')
            print(f"  输出长度: {len(stdout)} 字符")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('seqkit_head', result.get('success', False)))
        return result
    
    async def test_blast_help(self):
        """测试 4: BLAST help"""
        print("\n🔍 测试 4: BLAST help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='blast',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('blast_help', result.get('success', False)))
        return result
    
    async def test_prodigal_help(self):
        """测试 5: Prodigal help"""
        print("\n🧬 测试 5: Prodigal help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='prodigal',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('prodigal_help', result.get('success', False)))
        return result
    
    async def test_genomad_help(self):
        """测试 6: geNomad help"""
        print("\n🦠 测试 6: geNomad help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='genomad',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            print(f"  镜像: {result.get('image', 'N/A')}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('genomad_help', result.get('success', False)))
        return result
    
    async def test_checkv_help(self):
        """测试 7: CheckV help"""
        print("\n✅ 测试 7: CheckV help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='checkv',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            print(f"  镜像: {result.get('image', 'N/A')}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('checkv_help', result.get('success', False)))
        return result
    
    async def test_virsorter2_help(self):
        """测试 8: VirSorter2 help"""
        print("\n🧪 测试 8: VirSorter2 help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='virsorter2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            print(f"  镜像: {result.get('image', 'N/A')}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('virsorter2_help', result.get('success', False)))
        return result
    
    # ===== 新注册的核心工具测试 =====
    
    async def test_bwa_help(self):
        """测试 9: BWA help"""
        print("\n🧬 测试 9: BWA help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='bwa',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('bwa_help', result.get('success', False)))
        return result
    
    async def test_bowtie2_help(self):
        """测试 10: Bowtie2 help"""
        print("\n🧬 测试 10: Bowtie2 help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='bowtie2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('bowtie2_help', result.get('success', False)))
        return result
    
    async def test_mmseqs2_help(self):
        """测试 11: MMseqs2 help"""
        print("\n🧬 测试 11: MMseqs2 help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='mmseqs2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('mmseqs2_help', result.get('success', False)))
        return result
    
    async def test_trim_galore_help(self):
        """测试 12: Trim Galore help"""
        print("\n✂️ 测试 12: Trim Galore help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='trim_galore',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('trim_galore_help', result.get('success', False)))
        return result
    
    async def test_ngmlr_help(self):
        """测试 13: NGMLR help"""
        print("\n🧬 测试 13: NGMLR help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='ngmlr',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('ngmlr_help', result.get('success', False)))
        return result
    
    async def test_sniffles2_help(self):
        """测试 14: Sniffles2 help"""
        print("\n🧬 测试 14: Sniffles2 help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='sniffles2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('sniffles2_help', result.get('success', False)))
        return result
    
    async def test_fastani_help(self):
        """测试 15: FastANI help"""
        print("\n🧬 测试 15: FastANI help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='fastani',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('fastani_help', result.get('success', False)))
        return result
    
    # ===== 新注册的噬菌体和工作流工具测试 =====
    
    async def test_vibrant_help(self):
        """测试 16: VIBRANT help"""
        print("\n🦠 测试 16: VIBRANT help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='vibrant',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('vibrant_help', result.get('success', False)))
        return result
    
    async def test_iphop_help(self):
        """测试 17: iPHoP help"""
        print("\n🦠 测试 17: iPHoP help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='iphop',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('iphop_help', result.get('success', False)))
        return result
    
    async def test_nextflow_help(self):
        """测试 18: Nextflow help"""
        print("\n⚙️ 测试 18: Nextflow help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='nextflow',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('nextflow_help', result.get('success', False)))
        return result
    
    async def test_snakemake_help(self):
        """测试 19: Snakemake help"""
        print("\n⚙️ 测试 19: Snakemake help (新增)")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='snakemake',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ 成功!")
            ops = list(result.get('operations', {}).keys())
            print(f"  可用操作: {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ 失败: {result.get('error')}")
            self.failed += 1
        
        self.results.append(('snakemake_help', result.get('success', False)))
        return result
    
    def print_summary(self):
        """打印测试汇总"""
        print("\n" + "=" * 70)
        print("📊 测试汇总")
        print("=" * 70)
        
        total = self.passed + self.failed
        pass_rate = (self.passed / total * 100) if total > 0 else 0
        
        print(f"\n总计: {total} 个测试")
        print(f"通过: {self.passed} ✅")
        print(f"失败: {self.failed} ❌")
        print(f"通过率: {pass_rate:.1f}%")
        
        print("\n详细结果:")
        for name, success in self.results:
            status = "✅" if success else "❌"
            print(f"  {status} {name}")
        
        if self.failed == 0:
            print("\n🎉 所有测试通过!")
        else:
            print(f"\n⚠️  {self.failed} 个测试失败，请检查配置")


async def main():
    """主函数"""
    tester = BioToolsTester()
    await tester.run_all_tests()


if __name__ == '__main__':
    asyncio.run(main())
