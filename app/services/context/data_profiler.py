"""Data profiler for analyzing data directories before task execution.

Inspired by Claude Code's context gathering approach.
Analyzes data directories to understand:
- File counts and formats
- Sample names and patterns
- Consistency across files
- Recommendations for batch processing

This helps the agent make informed decisions before generating code.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class FileFormatStats:
    """Statistics for a specific file format."""
    format: str
    count: int
    total_size_bytes: int = 0
    sample_files: List[str] = field(default_factory=list)


@dataclass
class DataProfile:
    """Complete profile of a data directory."""
    data_dir: str
    total_files: int
    total_size_bytes: int
    file_formats: Dict[str, FileFormatStats]
    sample_names: List[str]
    is_consistent_format: bool
    directory_structure: Dict[str, int]  # dir_path -> file_count
    recommendations: List[str]
    potential_issues: List[str]
    
    @property
    def format_summary(self) -> str:
        """Human-readable format summary."""
        if not self.file_formats:
            return "No files found"
        
        parts = []
        for fmt, stats in sorted(self.file_formats.items(), key=lambda x: x[1].count, reverse=True):
            parts.append(f"{fmt}: {stats.count} files")
        return ", ".join(parts)
    
    @property
    def sample_count(self) -> int:
        return len(self.sample_names)
    
    def to_prompt_text(self) -> str:
        """Convert to text for injection into LLM prompt."""
        lines = [
            f"## Data Profile for: {self.data_dir}",
            f"",
            f"**Total Files**: {self.total_files}",
            f"**Total Size**: {self._format_size(self.total_size_bytes)}",
            f"**File Formats**: {self.format_summary}",
            f"**Samples Detected**: {self.sample_count}",
            f"**Consistent Format**: {'Yes' if self.is_consistent_format else 'No'}",
            f"",
        ]
        
        if self.sample_names:
            lines.append("### Sample Names:")
            # Show first 20 samples, then ellipsis if more
            display_samples = self.sample_names[:20]
            lines.append(f"  {', '.join(display_samples)}")
            if len(self.sample_names) > 20:
                lines.append(f"  ... and {len(self.sample_names) - 20} more")
            lines.append("")
        
        if self.recommendations:
            lines.append("### Recommendations:")
            for i, rec in enumerate(self.recommendations, 1):
                lines.append(f"  {i}. {rec}")
            lines.append("")
        
        if self.potential_issues:
            lines.append("### Potential Issues:")
            for issue in self.potential_issues:
                lines.append(f"  [WARNING] {issue}")
            lines.append("")
        
        return "\n".join(lines)
    
    @staticmethod
    def _format_size(size_bytes: float) -> str:
        """Format bytes to human-readable size."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024
        return f"{size_bytes:.1f} PB"


class DataProfiler:
    """
    Profiles data directories to understand data characteristics.
    
    This is called BEFORE code generation to ensure the agent understands:
    - How many samples/files need to be processed
    - What formats they're in
    - Whether batch processing is needed
    - Any potential issues or inconsistencies
    """
    
    # Common file extensions to track
    TRACKED_EXTENSIONS = {
        '.h5ad', '.h5', '.mtx', '.txt', '.tsv', '.csv',
        '.fastq', '.fq', '.fastq.gz', '.fq.gz',
        '.bam', '.sam', '.vcf', '.bed',
        '.fasta', '.fa', '.fna', '.faa',
        '.json', '.xml', '.yaml', '.yml',
        '.png', '.jpg', '.jpeg', '.pdf',
    }
    
    # Sample name extraction patterns
    SAMPLE_PATTERNS = [
        # filtered_cancer1.h5ad -> cancer1
        r'^(?:filtered_|processed_|raw_)?(.+?)\.(?:h5ad|h5|csv|tsv|txt)$',
        # cancer1_matrix.mtx -> cancer1
        r'^(.+?)[_.](?:matrix|counts|expression|barcodes|features)[_.]',
        # GSM12345_cancer1.fastq.gz -> cancer1
        r'^(?:GSM\d+_)?(.+?)[_.](?:fastq|fq|bam|sam)',
    ]
    
    def __init__(self):
        self._cache: Dict[str, DataProfile] = {}
    
    def clear_cache(self):
        """Clear the profile cache."""
        self._cache.clear()
    
    async def profile(self, data_dir: str, force_refresh: bool = False) -> DataProfile:
        """
        Profile a data directory asynchronously.
        
        Args:
            data_dir: Path to the data directory
            force_refresh: If True, ignore cache and re-profile
            
        Returns:
            DataProfile with complete analysis
        """
        # Check cache first
        if not force_refresh and data_dir in self._cache:
            logger.debug(f"Using cached profile for {data_dir}")
            return self._cache[data_dir]
        
        logger.info(f"Profiling data directory: {data_dir}")
        
        # Run profiling asynchronously
        profile = await asyncio.to_thread(self._profile_sync, data_dir)
        
        # Cache the result
        self._cache[data_dir] = profile
        
        return profile
    
    def _profile_sync(self, data_dir: str) -> DataProfile:
        """Synchronous profiling implementation."""
        # Resolve to real path first
        real_path = os.path.realpath(data_dir)
        data_path = Path(real_path)
        
        # Validate path is within allowed directories
        allowed_roots = [
            '/home/zczhao/GAgent', 
            '/tmp', 
            '/var/folders',  # macOS temp (symlink)
            '/private/var/folders',  # macOS temp (actual)
        ]
        if not any(real_path.startswith(root) for root in allowed_roots):
            return self._create_error_profile(
                data_dir, 
                f"Path not in allowed directories: {data_dir}"
            )
        
        if not data_path.exists():
            return self._create_error_profile(data_dir, "Directory does not exist")
        
        if not data_path.is_dir():
            return self._create_error_profile(data_dir, "Path is not a directory")
        
        # Collect file information
        all_files = []
        dir_structure = {}
        total_size = 0

        for root, dirs, files in os.walk(str(data_path)):
            rel_root = str(Path(root).relative_to(data_path))
            file_count = 0
            
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(data_path)
                
                # Skip hidden files and very large files
                if file.startswith('.'):
                    continue
                
                try:
                    stat = file_path.stat()
                    file_size = stat.st_size
                except OSError:
                    continue
                
                all_files.append({
                    'path': str(rel_path),
                    'name': file,
                    'size': file_size,
                    'extension': file_path.suffix.lower(),
                    'full_extension': self._get_full_extension(file),
                    'parent_dir': rel_root,  # Add parent directory
                })
                
                total_size += file_size
                file_count += 1
            
            dir_structure[rel_root] = file_count
        
        # Analyze file formats
        format_stats = self._analyze_formats(all_files)
        
        # Extract sample names (from both filenames and directory structure)
        sample_names = self._extract_sample_names(all_files, dir_structure)
        
        # Check format consistency
        is_consistent = self._check_format_consistency(all_files)
        
        # Generate recommendations
        recommendations = self._generate_recommendations(
            all_files, format_stats, sample_names, is_consistent
        )
        
        # Identify potential issues
        issues = self._identify_issues(all_files, format_stats, is_consistent)
        
        return DataProfile(
            data_dir=data_dir,
            total_files=len(all_files),
            total_size_bytes=total_size,
            file_formats=format_stats,
            sample_names=sorted(sample_names),
            is_consistent_format=is_consistent,
            directory_structure=dir_structure,
            recommendations=recommendations,
            potential_issues=issues,
        )
    
    def _get_full_extension(self, filename: str) -> str:
        """Get full extension including compound extensions like .fastq.gz."""
        name = filename.lower()
        if name.endswith('.fastq.gz'):
            return '.fastq.gz'
        if name.endswith('.fq.gz'):
            return '.fq.gz'
        return Path(filename).suffix.lower()
    
    def _analyze_formats(self, files: List[dict]) -> Dict[str, FileFormatStats]:
        """Analyze file format distribution."""
        formats: Dict[str, FileFormatStats] = {}
        
        for file_info in files:
            ext = file_info['full_extension'] or file_info['extension']
            if not ext:
                ext = 'no_extension'
            
            if ext not in formats:
                formats[ext] = FileFormatStats(
                    format=ext,
                    count=0,
                    total_size_bytes=0,
                    sample_files=[],
                )
            
            stats = formats[ext]
            stats.count += 1
            stats.total_size_bytes += file_info['size']
            
            # Keep track of first 3 sample files
            if len(stats.sample_files) < 3:
                stats.sample_files.append(file_info['name'])
        
        return formats
    
    def _extract_sample_names(self, files: List[dict], dir_structure: Dict[str, int]) -> Set[str]:
        """Extract sample names from filenames and directory structure.

        Strategy (all steps run, results merged):
        1. Identify leaf sample directories by MTX signature files
           (matrix.mtx/barcodes.tsv/features.tsv co-occurring)
        2. Extract from filtered_*.h5ad / processed_*.h5ad filenames
        3. Fallback: generic filename patterns

        Container directories (dataset, results, raw, GSE184880_RAW, etc.)
        are never treated as samples.
        """
        samples: Set[str] = set()

        # Directory names that are containers, not samples
        container_names = {
            'results', 'output', 'temp', 'cache', 'data', 'raw',
            'dataset', 'datasets',
            'GSE184880_RAW', 'GSE292661_RAW',
            'references_and_results', 'docs', 'config',
            'scripts', 'plans', 'plans_and_results',
        }

        # ---- Step 1: Leaf sample directories by MTX signature ----
        # Group files by their parent directory
        files_by_dir: Dict[str, List[str]] = {}
        for file_info in files:
            parent = file_info.get('parent_dir', '.')
            if parent not in files_by_dir:
                files_by_dir[parent] = []
            files_by_dir[parent].append(file_info['name'].lower())

        for dir_path, file_names in files_by_dir.items():
            # Get the leaf directory name (last component of path)
            dir_base = dir_path.split('/')[-1] if '/' in dir_path else dir_path
            if dir_base.lower() in container_names:
                continue

            # Check for MTX signature: matrix.mtx(.gz) + barcodes/features
            has_matrix = any('matrix.mtx' in f for f in file_names)
            has_barcodes = any('barcodes' in f for f in file_names)
            has_features = any('features' in f or 'genes' in f for f in file_names)

            # If it has matrix + at least one other signature, it's a sample dir
            if has_matrix and (has_barcodes or has_features):
                samples.add(dir_base)

        # ---- Step 2: Extract from filtered_*.h5ad filenames ----
        for file_info in files:
            filename = file_info['name']
            # Match filtered_<sample>.h5ad, processed_<sample>.h5ad, raw_<sample>.h5ad
            match = re.match(
                r'^(?:filtered|processed|raw)_(.+?)\.h5ad$',
                filename, re.IGNORECASE
            )
            if match:
                sample_name = match.group(1).strip()
                if sample_name and len(sample_name) > 1 and sample_name.lower() not in container_names:
                    samples.add(sample_name)

        # ---- Step 3: Fallback - generic patterns (only if no samples yet) ----
        if not samples:
            for file_info in files:
                filename = file_info['name']
                stem = Path(filename).stem

                # Skip known non-sample files
                if stem.lower() in {'features', 'barcodes', 'matrix', 'genes',
                                     'peaks', 'README', 'metadata', 'config'}:
                    continue

                # Try registered sample patterns
                for pattern in self.SAMPLE_PATTERNS:
                    match = re.match(pattern, filename, re.IGNORECASE)
                    if match:
                        sample_name = match.group(1).strip()
                        if (sample_name and len(sample_name) > 1
                                and sample_name.lower() not in container_names):
                            samples.add(sample_name)
                        break

                # Fallback: use filename stem without common prefixes
                if not samples:
                    for prefix in ['filtered_', 'processed_', 'raw_', 'output_']:
                        if stem.startswith(prefix):
                            stem = stem[len(prefix):]
                    if (stem and len(stem) > 1
                            and stem.lower() not in container_names):
                        samples.add(stem)

        return samples
    
    def _check_format_consistency(self, files: List[dict]) -> bool:
        """Check if all files have the same format."""
        if not files:
            return True
        
        extensions = set()
        for file_info in files:
            ext = file_info['full_extension'] or file_info['extension']
            if ext:
                extensions.add(ext)
        
        # Consider consistent if <= 2 different formats
        return len(extensions) <= 2
    
    def _generate_recommendations(
        self,
        files: List[dict],
        format_stats: Dict[str, FileFormatStats],
        sample_names: Set[str],
        is_consistent: bool,
    ) -> List[str]:
        """Generate processing recommendations based on data profile."""
        recommendations = []
        
        # Batch processing recommendation
        if len(files) > 5:
            recommendations.append(
                f"Large file count ({len(files)}), recommend using batch processing loop (for sample in samples)"
            )
        
        # Multiple samples recommendation
        if len(sample_names) > 1:
            recommendations.append(
                f"Detected {len(sample_names)} samples, need to process all samples: {', '.join(sorted(list(sample_names))[:10])}"
            )
        
        # Format inconsistency recommendation
        if not is_consistent:
            formats = list(format_stats.keys())
            recommendations.append(
                f"Multiple file formats detected: {', '.join(formats[:5])}, may need format conversion or unified processing"
            )
        
        # Large files recommendation
        large_files = [f for f in files if f['size'] > 100 * 1024 * 1024]  # > 100MB
        if large_files:
            recommendations.append(
                f"Found {len(large_files)} large files (>100MB), be mindful of memory usage, consider streaming"
            )
        
        # Specific format recommendations
        if '.mtx' in format_stats and '.h5ad' not in format_stats:
            recommendations.append(
                "MTX format data detected, may need to convert to h5ad format before analysis"
            )
        
        if '.fastq.gz' in format_stats or '.fq.gz' in format_stats:
            recommendations.append(
                "Compressed FASTQ files detected, need to decompress and quality control first"
            )
        
        return recommendations
    
    def _identify_issues(
        self,
        files: List[dict],
        format_stats: Dict[str, FileFormatStats],
        is_consistent: bool,
    ) -> List[str]:
        """Identify potential issues with the data."""
        issues = []
        
        # No files found
        if not files:
            issues.append("No files found in directory")
            return issues
        
        # Format inconsistency
        if not is_consistent:
            issues.append("Inconsistent file formats, may require preprocessing")

        # Very large number of files
        if len(files) > 100:
            issues.append(f"Very large file count ({len(files)}), may need batch processing")

        # Missing common formats
        has_data = any(ext in format_stats for ext in ['.h5ad', '.mtx', '.csv', '.fastq', '.bam'])
        if not has_data:
            issues.append("No common data file formats found")
        
        # Empty files
        empty_files = sum(1 for f in files if f['size'] == 0)
        if empty_files > 0:
            issues.append(f"Found {empty_files} empty files")
        
        return issues
    
    def _create_error_profile(self, data_dir: str, error: str) -> DataProfile:
        """Create an error profile when directory cannot be profiled."""
        return DataProfile(
            data_dir=data_dir,
            total_files=0,
            total_size_bytes=0,
            file_formats={},
            sample_names=[],
            is_consistent_format=True,
            directory_structure={},
            recommendations=[],
            potential_issues=[error],
        )
