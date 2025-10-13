"""Utility operation parameter definitions and parsing."""

from argparse import ArgumentParser
from typing import Any, Dict, Optional


class UtilityParamsHandler:
    """Handler for utility operation parameters following SRP."""

    GROUP_NAME = "Utility Operations"

    @staticmethod
    def add_arguments(parser: ArgumentParser) -> None:
        """Add utility operation arguments to parser."""

        # Index management commands
        index_group = parser.add_argument_group("Global Index Management")
        index_group.add_argument(
            "--index-preview", action="store_true", help="Preview generated INDEX.md content without saving"
        )
        index_group.add_argument("--index-export", type=str, help="Export INDEX.md to specified path")
        index_group.add_argument(
            "--index-run-root", action="store_true", help="Generate and persist INDEX.md with history tracking"
        )

        # Embedding management commands
        embed_group = parser.add_argument_group("Embedding Management")
        embed_group.add_argument(
            "--generate-embeddings", action="store_true", help="Generate embeddings for all completed tasks"
        )

        # Benchmark operations
        bench_group = parser.add_argument_group("Benchmark Operations")
        bench_group.add_argument(
            "--benchmark",
            action="store_true",
            help="Run benchmark: generate reports under different configs and evaluate",
        )
        bench_group.add_argument("--benchmark-topic", type=str, help="Benchmark topic (report subject)")
        bench_group.add_argument(
            "--benchmark-configs",
            nargs="+",
            help='Benchmark config specs, e.g. "base,use_context=False" "ctx,use_context=True,max_chars=3000"',
        )
        bench_group.add_argument(
            "--benchmark-sections", type=int, default=5, help="Number of sections/tasks per run (default: 5)"
        )
        bench_group.add_argument("--benchmark-output", type=str, help="Output markdown path for benchmark summary")
        bench_group.add_argument(
            "--benchmark-outdir", type=str, help="Directory to write per-config generated markdown reports"
        )
        bench_group.add_argument("--benchmark-csv", type=str, help="Path to write per-config metrics CSV summary")
        embed_group.add_argument(
            "--embedding-stats", action="store_true", help="Show embedding service statistics and performance"
        )
        embed_group.add_argument(
            "--rebuild-embeddings", action="store_true", help="Rebuild all embeddings from scratch (DESTRUCTIVE)"
        )
        embed_group.add_argument(
            "--embedding-batch-size",
            dest="embedding_batch_size",
            type=int,
            default=10,
            help="Batch size for embedding generation (default: 10)",
        )

    @staticmethod
    def extract_values(args) -> Dict[str, Any]:
        """Extract utility parameter values from parsed args."""
        values = {}

        # Index operations
        index_commands = ["index_preview", "index_run_root"]
        for cmd in index_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True

        # Index export (has a path value)
        if hasattr(args, "index_export") and getattr(args, "index_export"):
            values["index_export"] = getattr(args, "index_export")

        # Embedding operations
        embed_commands = ["generate_embeddings", "embedding_stats", "rebuild_embeddings"]
        for cmd in embed_commands:
            if hasattr(args, cmd) and getattr(args, cmd):
                values[cmd] = True

        # Embedding configuration
        if hasattr(args, "embedding_batch_size"):
            value = getattr(args, "embedding_batch_size")
            if value is not None:
                values["embedding_batch_size"] = value

        # Benchmark
        if hasattr(args, "benchmark") and getattr(args, "benchmark"):
            values["benchmark"] = True
            if hasattr(args, "benchmark_topic") and getattr(args, "benchmark_topic"):
                values["benchmark_topic"] = getattr(args, "benchmark_topic")
            if hasattr(args, "benchmark_configs") and getattr(args, "benchmark_configs"):
                values["benchmark_configs"] = getattr(args, "benchmark_configs")
            if hasattr(args, "benchmark_sections"):
                values["benchmark_sections"] = getattr(args, "benchmark_sections")
            if hasattr(args, "benchmark_output") and getattr(args, "benchmark_output"):
                values["benchmark_output"] = getattr(args, "benchmark_output")
            if hasattr(args, "benchmark_outdir") and getattr(args, "benchmark_outdir"):
                values["benchmark_outdir"] = getattr(args, "benchmark_outdir")
            if hasattr(args, "benchmark_csv") and getattr(args, "benchmark_csv"):
                values["benchmark_csv"] = getattr(args, "benchmark_csv")

        return values

    @staticmethod
    def validate_values(values: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Validate utility parameter combinations."""
        # Index export path validation
        index_export = values.get("index_export")
        if index_export:
            if not index_export.strip():
                return False, "Index export path cannot be empty"
            if len(index_export) > 255:
                return False, "Index export path too long (max 255 characters)"
            if not index_export.endswith(".md"):
                return False, "Index export path must end with .md"

        # Embedding batch size validation
        batch_size = values.get("embedding_batch_size")
        if batch_size is not None and (batch_size <= 0 or batch_size > 100):
            return False, "Embedding batch size must be between 1 and 100"

        # Benchmark validation
        if values.get("benchmark"):
            if not values.get("benchmark_topic"):
                return False, "--benchmark requires --benchmark-topic"
            cfgs = values.get("benchmark_configs")
            if not cfgs or not isinstance(cfgs, list) or len(cfgs) == 0:
                return False, "--benchmark requires --benchmark-configs"
            secs = values.get("benchmark_sections", 5)
            if not isinstance(secs, int) or secs <= 0:
                return False, "--benchmark-sections must be positive integer"

        return True, None

    @staticmethod
    def has_utility_operation(args) -> bool:
        """Check if any utility operation is requested."""
        utility_ops = [
            "index_preview",
            "index_export",
            "index_run_root",
            "generate_embeddings",
            "embedding_stats",
            "rebuild_embeddings",
            "benchmark",
        ]
        return any(hasattr(args, attr) and getattr(args, attr) for attr in utility_ops)
