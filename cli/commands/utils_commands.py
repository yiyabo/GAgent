"""Utility command implementations (placeholder)."""

from ..utils.io_utils import IOUtils
from .base import MultiCommand

try:
    from ...app.services.benchmark import run_benchmark
except ImportError:
    from app.services.benchmark import run_benchmark


class UtilsCommands(MultiCommand):
    """Handle utility operations."""

    @property
    def name(self) -> str:
        return "utils"

    @property
    def description(self) -> str:
        return "Utility operations"

    def get_action_map(self):
        return {"benchmark": self.handle_benchmark}

    def handle_default(self, args):
        return 1  # Placeholder

    def handle_benchmark(self, args) -> int:
        io = IOUtils()
        try:
            topic = getattr(args, "benchmark_topic", None)
            configs = getattr(args, "benchmark_configs", None)
            sections = getattr(args, "benchmark_sections", 5)
            output = getattr(args, "benchmark_output", None)
            outdir = getattr(args, "benchmark_outdir", None)
            csv_path = getattr(args, "benchmark_csv", None)

            if not topic or not configs:
                io.print_error("--benchmark-topic and --benchmark-configs are required")
                return 1

            out = run_benchmark(topic, configs, sections=sections, outdir=outdir, csv_path=csv_path)
            summary = out.get("summary_md", "")
            if output:
                with open(output, "w", encoding="utf-8") as f:
                    f.write(summary)
                io.print_success(f"Benchmark summary written to {output}")
            else:
                print(summary)
            return 0
        except Exception as e:
            io.print_error(f"Benchmark failed: {e}")
            return 1
