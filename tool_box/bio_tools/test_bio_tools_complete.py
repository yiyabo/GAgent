#!/usr/bin/env python3
"""
Bio Tools 
======================
 bio_tools 

:
    cd /home/zczhao/GAgent
    PYTHONPATH=/home/zczhao/GAgent:$PYTHONPATH python tool_box/bio_tools/test_bio_tools_complete.py
"""

import asyncio
import json
import sys
from pathlib import Path

# 
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tool_box import execute_tool

# 
TEST_DATA_DIR = Path(__file__).parent / "test_data"
CONTIGS_FA = TEST_DATA_DIR / "contigs.fasta"


class BioToolsTester:
    """Bio Tools """
    
    def __init__(self):
        self.results = []
        self.passed = 0
        self.failed = 0
    
    async def run_all_tests(self):
        """"""
        print("🧪 Bio Tools ")
        print("=" * 70)
        
        # 1. 
        await self.test_tool_list()
        await self.test_seqkit_stats()
        await self.test_seqkit_head()
        
        # 2. 
        await self.test_blast_help()
        
        # 3. 
        await self.test_prodigal_help()
        
        # 4. 
        await self.test_genomad_help()
        await self.test_checkv_help()
        await self.test_virsorter2_help()
        
        # 5. 
        await self.test_bwa_help()
        await self.test_bowtie2_help()
        await self.test_mmseqs2_help()
        await self.test_trim_galore_help()
        await self.test_ngmlr_help()
        await self.test_sniffles2_help()
        await self.test_fastani_help()
        
        # 6. 
        await self.test_vibrant_help()
        await self.test_iphop_help()
        await self.test_nextflow_help()
        await self.test_snakemake_help()
        
        # 
        self.print_summary()
    
    async def test_tool_list(self):
        """ 1: """
        print("\n📋  1:  bio_tools")
        print("-" * 70)
        
        result = await execute_tool('bio_tools', tool_name='list')
        
        if result.get('success'):
            tools = result.get('tools', [])
            print(f"✅ !  {len(tools)} ")
            
            # 
            by_category = {}
            for tool in tools:
                cat = tool['category']
                by_category.setdefault(cat, []).append(tool['name'])
            
            print("\n:")
            for cat, tool_names in sorted(by_category.items()):
                print(f"  {cat}: {', '.join(tool_names)}")
            
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('tool_list', result.get('success', False)))
        return result
    
    async def test_seqkit_stats(self):
        """ 2: SeqKit stats"""
        print("\n📊  2: SeqKit stats")
        print("-" * 70)
        
        if not CONTIGS_FA.exists():
            print(f"⚠️  :  {CONTIGS_FA}")
            return None
        
        result = await execute_tool('bio_tools',
            tool_name='seqkit',
            operation='stats',
            input_file=str(CONTIGS_FA)
        )
        
        if result.get('success'):
            print("✅ !")
            print(f"  : {result.get('duration_seconds', 0):.2f} ")
            print(f"  :\n{result.get('stdout', '')[:200]}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('seqkit_stats', result.get('success', False)))
        return result
    
    async def test_seqkit_head(self):
        """ 3: SeqKit head"""
        print("\n📄  3: SeqKit head")
        print("-" * 70)
        
        if not CONTIGS_FA.exists():
            print(f"⚠️  : ")
            return None
        
        result = await execute_tool('bio_tools',
            tool_name='seqkit',
            operation='head',
            input_file=str(CONTIGS_FA),
            params={'count': '1'}
        )
        
        if result.get('success'):
            print("✅ !")
            stdout = result.get('stdout', '')
            print(f"  : {len(stdout)} ")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('seqkit_head', result.get('success', False)))
        return result
    
    async def test_blast_help(self):
        """ 4: BLAST help"""
        print("\n🔍  4: BLAST help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='blast',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('blast_help', result.get('success', False)))
        return result
    
    async def test_prodigal_help(self):
        """ 5: Prodigal help"""
        print("\n🧬  5: Prodigal help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='prodigal',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('prodigal_help', result.get('success', False)))
        return result
    
    async def test_genomad_help(self):
        """ 6: geNomad help"""
        print("\n🦠  6: geNomad help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='genomad',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            print(f"  : {result.get('image', 'N/A')}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('genomad_help', result.get('success', False)))
        return result
    
    async def test_checkv_help(self):
        """ 7: CheckV help"""
        print("\n✅  7: CheckV help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='checkv',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            print(f"  : {result.get('image', 'N/A')}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('checkv_help', result.get('success', False)))
        return result
    
    async def test_virsorter2_help(self):
        """ 8: VirSorter2 help"""
        print("\n🧪  8: VirSorter2 help")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='virsorter2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            print(f"  : {result.get('image', 'N/A')}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('virsorter2_help', result.get('success', False)))
        return result
    
    # =====  =====
    
    async def test_bwa_help(self):
        """ 9: BWA help"""
        print("\n🧬  9: BWA help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='bwa',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('bwa_help', result.get('success', False)))
        return result
    
    async def test_bowtie2_help(self):
        """ 10: Bowtie2 help"""
        print("\n🧬  10: Bowtie2 help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='bowtie2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('bowtie2_help', result.get('success', False)))
        return result
    
    async def test_mmseqs2_help(self):
        """ 11: MMseqs2 help"""
        print("\n🧬  11: MMseqs2 help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='mmseqs2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('mmseqs2_help', result.get('success', False)))
        return result
    
    async def test_trim_galore_help(self):
        """ 12: Trim Galore help"""
        print("\n✂️  12: Trim Galore help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='trim_galore',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('trim_galore_help', result.get('success', False)))
        return result
    
    async def test_ngmlr_help(self):
        """ 13: NGMLR help"""
        print("\n🧬  13: NGMLR help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='ngmlr',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('ngmlr_help', result.get('success', False)))
        return result
    
    async def test_sniffles2_help(self):
        """ 14: Sniffles2 help"""
        print("\n🧬  14: Sniffles2 help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='sniffles2',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('sniffles2_help', result.get('success', False)))
        return result
    
    async def test_fastani_help(self):
        """ 15: FastANI help"""
        print("\n🧬  15: FastANI help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='fastani',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('fastani_help', result.get('success', False)))
        return result
    
    # =====  =====
    
    async def test_vibrant_help(self):
        """ 16: VIBRANT help"""
        print("\n🦠  16: VIBRANT help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='vibrant',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('vibrant_help', result.get('success', False)))
        return result
    
    async def test_iphop_help(self):
        """ 17: iPHoP help"""
        print("\n🦠  17: iPHoP help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='iphop',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('iphop_help', result.get('success', False)))
        return result
    
    async def test_nextflow_help(self):
        """ 18: Nextflow help"""
        print("\n⚙️  18: Nextflow help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='nextflow',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('nextflow_help', result.get('success', False)))
        return result
    
    async def test_snakemake_help(self):
        """ 19: Snakemake help"""
        print("\n⚙️  19: Snakemake help ()")
        print("-" * 70)
        
        result = await execute_tool('bio_tools',
            tool_name='snakemake',
            operation='help'
        )
        
        if result.get('success'):
            print("✅ !")
            ops = list(result.get('operations', {}).keys())
            print(f"  : {', '.join(ops)}")
            self.passed += 1
        else:
            print(f"❌ : {result.get('error')}")
            self.failed += 1
        
        self.results.append(('snakemake_help', result.get('success', False)))
        return result
    
    def print_summary(self):
        """"""
        print("\n" + "=" * 70)
        print("📊 ")
        print("=" * 70)
        
        total = self.passed + self.failed
        pass_rate = (self.passed / total * 100) if total > 0 else 0
        
        print(f"\n: {total} ")
        print(f": {self.passed} ✅")
        print(f": {self.failed} ❌")
        print(f": {pass_rate:.1f}%")
        
        print("\n:")
        for name, success in self.results:
            status = "✅" if success else "❌"
            print(f"  {status} {name}")
        
        if self.failed == 0:
            print("\n🎉 !")
        else:
            print(f"\n⚠️  {self.failed} ，")


async def main():
    """"""
    tester = BioToolsTester()
    await tester.run_all_tests()


if __name__ == '__main__':
    asyncio.run(main())
