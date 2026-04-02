"""Code generation enhancer that injects data profiling information.

This module enhances the code generation process by:
1. Profiling data directories BEFORE generating code
2. Adding batch processing requirements to the task description
3. Ensuring the generated code handles all samples/files

This prevents the "processed 1 of 12 samples" issue.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.services.context.data_profiler import DataProfiler, DataProfile

logger = logging.getLogger(__name__)


class CodeGenerationEnhancer:
    """
    Enhances code generation with data profiling information.
    
    Usage:
        enhancer = CodeGenerationEnhancer()
        
        # Before generating code, profile the data
        enhanced_description = await enhancer.enhance_task_description(
            task_description="Filter cells based on QC metrics",
            data_dir="/home/zczhao/GAgent/data/ovarian_cancer_scRNA",
        )
        
        # Now generate code with enhanced_description
        # The code will know it needs to process ALL samples
    """
    
    def __init__(self):
        self._data_profiler = DataProfiler()
    
    async def enhance_task_description(
        self,
        task_description: str,
        data_dir: Optional[str] = None,
        force_profile: bool = False,
        require_batch_processing: bool = False,
    ) -> str:
        """
        Enhance task description with data profiling information.
        
        This adds:
        - Number of samples/files to process
        - File formats detected
        - Optional explicit batch-processing contract when the caller
          already knows the task must emit per-sample outputs
        
        Args:
            task_description: Original task description
            data_dir: Directory to profile (optional, will try to extract from task)
            force_profile: Force re-profiling even if cached
            require_batch_processing: Only set when the caller has an
                explicit structured contract that requires per-sample outputs
            
        Returns:
            Enhanced task description with data context
        """
        # Try to find data directory
        if not data_dir:
            data_dir = self._extract_data_dir(task_description)
        
        if not data_dir:
            logger.debug("No data directory found in task description, skipping enhancement")
            return task_description
        
        # Profile the data
        try:
            profile = await self._data_profiler.profile(data_dir, force_refresh=force_profile)
        except Exception as e:
            logger.warning(f"Failed to profile {data_dir}: {e}")
            return task_description
        
        # Build enhancement
        enhancement = self._build_enhancement(
            profile,
            require_batch_processing=require_batch_processing,
        )
        
        # Combine original with enhancement
        enhanced = f"{task_description}\n\n{enhancement}"
        
        logger.info(
            f"Enhanced task description with data profile: "
            f"{profile.total_files} files, {profile.sample_count} samples"
        )
        
        return enhanced
    
    def _build_enhancement(
        self,
        profile: DataProfile,
        *,
        require_batch_processing: bool = False,
    ) -> str:
        """Build enhancement text from data profile."""
        parts = []
        
        # Data context section
        parts.append("## 数据上下文")
        parts.append(f"- 数据目录: {profile.data_dir}")
        parts.append(f"- 文件总数: {profile.total_files}")
        parts.append(f"- 文件格式: {profile.format_summary}")
        
        # Samples section
        if profile.sample_names:
            parts.append(f"- 样本数量: {profile.sample_count}")
            parts.append(f"- 样本列表:")
            
            # Show all samples if <= 20, otherwise show first 20
            if profile.sample_count <= 20:
                samples_text = ", ".join(profile.sample_names)
                parts.append(f"  {samples_text}")
            else:
                samples_text = ", ".join(profile.sample_names[:20])
                parts.append(f"  {samples_text}")
                parts.append(f"  ... 以及其他 {profile.sample_count - 20} 个样本")
        
        # Requirements section
        parts.append("")
        parts.append("## 处理要求")
        
        if require_batch_processing and profile.sample_count > 1:
            parts.append(
                f"⚠️ **重要**: 必须处理所有 {profile.sample_count} 个样本,不能只处理部分样本!"
            )
            parts.append(
                "代码结构必须包含循环: for sample in samples:"
            )
        elif profile.sample_count > 1:
            parts.append(
                f"- 已检测到 {profile.sample_count} 个样本，请结合任务本身的输出契约决定是逐样本处理、汇总，还是整合。"
            )
        
        if not profile.is_consistent_format:
            parts.append(
                "⚠️ **注意**: 文件格式不一致,可能需要预处理或格式转换"
            )
        
        # Recommendations section
        if profile.recommendations:
            parts.append("")
            parts.append("## 建议")
            for i, rec in enumerate(profile.recommendations, 1):
                parts.append(f"{i}. {rec}")
        
        # Code pattern suggestion
        if require_batch_processing and profile.sample_count > 5:
            parts.append("")
            parts.append("## 推荐的代码结构")
            parts.append("```python")
            parts.append("# 1. 发现所有样本")
            parts.append("samples = [...]  # 所有样本列表")
            parts.append("")
            parts.append("# 2. 批量处理")
            parts.append("results = []")
            parts.append("for sample in samples:")
            parts.append("    result = process_single_sample(sample)")
            parts.append("    results.append(result)")
            parts.append("")
            parts.append("# 3. 汇总统计")
            parts.append("summarize_results(results)")
            parts.append("```")
        
        return "\n".join(parts)
    
    def _extract_data_dir(self, task_description: str) -> Optional[str]:
        """
        Extract data directory path from task description.
        """
        import re
        import os

        # Look for common path patterns (generic, not hardcoded)
        path_patterns = [
            r'(/[^\s"]+/data[^\s"]*)',
            r'(/[^\s"]+/datasets[^\s"]*)',
            r'data_dir\s*=\s*["\']([^"\']+)["\']',
            r'DATA_DIR\s*=\s*["\']([^"\']+)["\']',
        ]

        for pattern in path_patterns:
            matches = re.findall(pattern, task_description)
            if matches:
                path = matches[0].strip().strip('"').strip("'")
                if os.path.exists(path):
                    return path

        return None
