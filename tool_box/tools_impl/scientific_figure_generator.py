"""Scientific composite figure generator with built-in QA artifacts."""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tool_box.context import ToolContext


COLORS = [
    "#ABD1BC",
    "#BED0F9",
    "#CCCC99",
    "#DBE4FB",
    "#E3BBED",
    "#EDC3A5",
    "#F1F1F1",
    "#FCB6A5",
    "#FDEBAA",
]

FORBIDDEN_CLAIM_PHRASES = (
    "therapeutic priority",
    "therapeutic rationale",
    "druggable targets",
    "inhibitor recommendation",
    "caf activation",
    "immune evasion",
)

DEFAULT_BASE_NAME = "scientific_composite_figure"


def _safe_resolve_within_root(raw_path: str, root: Path, *, label: str) -> Path:
    root_resolved = root.expanduser().resolve()
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = root_resolved / candidate
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"{label} must stay inside the tool work directory: {root_resolved}") from exc
    return resolved


def _work_dir_root(tool_context: Optional[ToolContext]) -> Path:
    if tool_context and tool_context.work_dir:
        return Path(tool_context.work_dir).expanduser().resolve()
    return Path.cwd().resolve()


@dataclass
class LoadedDataset:
    name: str
    rows: List[Dict[str, Any]]
    source: str


def _slugify(value: str, fallback: str = DEFAULT_BASE_NAME) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or fallback


def _resolve_output_dir(output_dir: Optional[str], tool_context: Optional[ToolContext]) -> Path:
    from app.services.tool_output_resolver import get_tool_output_resolver
    
    # Security constraint: output_dir must stay within work_dir if work_dir is set
    if output_dir and tool_context and tool_context.work_dir:
        work_dir_resolved = Path(tool_context.work_dir).expanduser().resolve()
        candidate = Path(output_dir).expanduser()
        if not candidate.is_absolute():
            candidate = work_dir_resolved / candidate
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(work_dir_resolved)
        except ValueError as exc:
            raise ValueError(f"output_dir must stay inside the tool work directory: {work_dir_resolved}") from exc
    
    resolver = get_tool_output_resolver()
    return resolver.resolve(
        explicit_dir=output_dir,
        tool_context=tool_context,
        tool_name="scientific_figures",
    )


def _coerce_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if math.isnan(float(value)) or math.isinf(float(value)):
            return None
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _read_tabular_file(path: Path, declared_format: Optional[str] = None) -> List[Dict[str, Any]]:
    fmt = str(declared_format or path.suffix.lstrip(".")).lower()
    if fmt in {"json", "jsonl"}:
        if fmt == "jsonl":
            rows = []
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
            return rows
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("rows", "data", "records"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [dict(row) for row in value if isinstance(row, dict)]
        return []

    delimiter = "\t" if fmt == "tsv" or path.suffix.lower() == ".tsv" else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        return [dict(row) for row in reader]


def _looks_like_dataset_spec(value: Dict[str, Any]) -> bool:
    if isinstance(value.get("rows"), list):
        return True
    if isinstance(value.get("records"), list):
        return True
    if isinstance(value.get("data"), list):
        return True
    if isinstance(value.get("path"), str) and value.get("path"):
        return True
    return False


def _normalize_dataset_specs(datasets: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    items = [dict(item) for item in (datasets or []) if isinstance(item, dict)]
    if items and not any(_looks_like_dataset_spec(item) for item in items):
        return [{"name": "dataset_1", "rows": items}]
    return items


def _load_datasets(
    datasets: Optional[List[Dict[str, Any]]],
    *,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, LoadedDataset]:
    loaded: Dict[str, LoadedDataset] = {}
    root = _work_dir_root(tool_context)
    rows_seen = 0
    for index, spec in enumerate(_normalize_dataset_specs(datasets), start=1):
        name = str(spec.get("name") or f"dataset_{index}").strip() or f"dataset_{index}"
        rows: List[Dict[str, Any]] = []
        source = "inline"
        raw_rows = spec.get("rows") or spec.get("records") or spec.get("data")
        if isinstance(raw_rows, list):
            rows = [dict(row) for row in raw_rows if isinstance(row, dict)]
        elif isinstance(spec.get("path"), str) and spec.get("path"):
            path = _safe_resolve_within_root(str(spec["path"]), root, label="dataset path")
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"Dataset path does not exist: {path}")
            rows = _read_tabular_file(path, spec.get("format") if isinstance(spec.get("format"), str) else None)
            source = str(path.resolve())
        if not rows:
            raise ValueError(f"Dataset '{name}' has no rows")
        rows_seen += len(rows)
        loaded[name] = LoadedDataset(name=name, rows=rows, source=source)
    if not loaded:
        raise ValueError("At least one dataset with rows or path is required")
    if rows_seen <= 0:
        raise ValueError("No data rows were loaded")
    return loaded


def _numeric_columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    keys: List[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    numeric = []
    for key in keys:
        values = [_coerce_number(row.get(key)) for row in rows[:100]]
        if any(value is not None for value in values):
            numeric.append(key)
    return numeric


def _all_columns(rows: Sequence[Dict[str, Any]]) -> List[str]:
    columns: List[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns


def _panel_specs(panels: Optional[List[Dict[str, Any]]], datasets: Dict[str, LoadedDataset]) -> List[Dict[str, Any]]:
    if panels:
        return [dict(panel) for panel in panels if isinstance(panel, dict)]
    specs: List[Dict[str, Any]] = []
    for name, dataset in datasets.items():
        numeric = _numeric_columns(dataset.rows)
        columns = _all_columns(dataset.rows)
        if len(numeric) >= 2:
            specs.append({"dataset": name, "type": "scatter", "x": numeric[0], "y": numeric[1], "title": name})
        elif numeric and len(columns) >= 2:
            x = next((col for col in columns if col != numeric[0]), columns[0])
            specs.append({"dataset": name, "type": "bar", "x": x, "y": numeric[0], "title": name})
        else:
            specs.append({"dataset": name, "type": "table", "title": name})
    return specs


def _select_dataset(panel: Dict[str, Any], datasets: Dict[str, LoadedDataset]) -> LoadedDataset:
    requested = str(panel.get("dataset") or "").strip()
    if requested and requested in datasets:
        return datasets[requested]
    if requested:
        raise ValueError(f"Panel references unknown dataset: {requested}")
    return next(iter(datasets.values()))


def _top_rows(rows: List[Dict[str, Any]], key: Optional[str], limit: int) -> List[Dict[str, Any]]:
    if not key:
        return rows[:limit]
    return sorted(rows, key=lambda row: _coerce_number(row.get(key)) or 0.0, reverse=True)[:limit]


def _draw_bar(ax: Any, rows: List[Dict[str, Any]], panel: Dict[str, Any]) -> None:
    x_key = str(panel.get("x") or "label")
    y_key = str(panel.get("y") or panel.get("value") or "value")
    rows = _top_rows(rows, y_key, int(panel.get("top_n") or 20))
    labels = [str(row.get(x_key, ""))[:36] for row in rows]
    values = [_coerce_number(row.get(y_key)) or 0.0 for row in rows]
    ax.barh(range(len(rows)), values, color=COLORS[0], edgecolor="#555555", linewidth=0.4)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel(str(panel.get("x_label") or y_key))
    ax.set_ylabel(str(panel.get("y_label") or x_key))


def _draw_line(ax: Any, rows: List[Dict[str, Any]], panel: Dict[str, Any]) -> None:
    x_key = str(panel.get("x") or "x")
    y_key = str(panel.get("y") or panel.get("value") or "value")
    x_values = [str(row.get(x_key, "")) for row in rows]
    y_values = [_coerce_number(row.get(y_key)) or 0.0 for row in rows]
    ax.plot(range(len(rows)), y_values, marker="o", color=COLORS[1], linewidth=2)
    ax.set_xticks(range(len(rows)))
    ax.set_xticklabels(x_values, rotation=45, ha="right", fontsize=8)
    ax.set_xlabel(str(panel.get("x_label") or x_key))
    ax.set_ylabel(str(panel.get("y_label") or y_key))


def _draw_scatter(ax: Any, rows: List[Dict[str, Any]], panel: Dict[str, Any]) -> None:
    x_key = str(panel.get("x") or "x")
    y_key = str(panel.get("y") or "y")
    points = [(_coerce_number(row.get(x_key)), _coerce_number(row.get(y_key))) for row in rows]
    points = [(x, y) for x, y in points if x is not None and y is not None]
    if not points:
        raise ValueError(f"Scatter panel has no numeric x/y values: {x_key}, {y_key}")
    ax.scatter([x for x, _ in points], [y for _, y in points], s=35, color=COLORS[1], edgecolor="#333333", alpha=0.85)
    ax.set_xlabel(str(panel.get("x_label") or x_key))
    ax.set_ylabel(str(panel.get("y_label") or y_key))


def _draw_heatmap(ax: Any, rows: List[Dict[str, Any]], panel: Dict[str, Any]) -> None:
    import numpy as np

    row_key = str(panel.get("row") or panel.get("y") or "row")
    col_key = str(panel.get("column") or panel.get("x") or "column")
    value_key = str(panel.get("value") or "value")
    row_labels = list(dict.fromkeys(str(row.get(row_key, "")) for row in rows if str(row.get(row_key, "")).strip()))[:30]
    col_labels = list(dict.fromkeys(str(row.get(col_key, "")) for row in rows if str(row.get(col_key, "")).strip()))[:30]
    matrix = np.zeros((len(row_labels), len(col_labels)))
    row_index = {label: i for i, label in enumerate(row_labels)}
    col_index = {label: i for i, label in enumerate(col_labels)}
    for row in rows:
        r = str(row.get(row_key, ""))
        c = str(row.get(col_key, ""))
        if r in row_index and c in col_index:
            matrix[row_index[r], col_index[c]] = _coerce_number(row.get(value_key)) or 0.0
    image = ax.imshow(matrix, cmap="YlGnBu", aspect="auto")
    ax.set_xticks(range(len(col_labels)))
    ax.set_xticklabels(col_labels, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=7)
    ax.figure.colorbar(image, ax=ax, fraction=0.046, pad=0.04, label=value_key)


def _draw_table(ax: Any, rows: List[Dict[str, Any]], panel: Dict[str, Any]) -> None:
    ax.axis("off")
    columns = _all_columns(rows)[:6]
    display_rows = rows[: int(panel.get("top_n") or 8)]
    cell_text = [[str(row.get(column, ""))[:32] for column in columns] for row in display_rows]
    table = ax.table(cellText=cell_text, colLabels=columns, loc="center", cellLoc="left")
    table.auto_set_font_size(False)
    table.set_fontsize(7)
    table.scale(1, 1.4)


def _draw_panel(ax: Any, dataset: LoadedDataset, panel: Dict[str, Any], panel_label: str) -> Dict[str, Any]:
    kind = str(panel.get("type") or "auto").strip().lower()
    if kind == "auto":
        numeric = _numeric_columns(dataset.rows)
        columns = _all_columns(dataset.rows)
        if len(numeric) >= 2:
            panel = {**panel, "type": "scatter", "x": panel.get("x") or numeric[0], "y": panel.get("y") or numeric[1]}
            kind = "scatter"
        elif numeric and len(columns) >= 2:
            x = next((col for col in columns if col != numeric[0]), columns[0])
            panel = {**panel, "type": "bar", "x": panel.get("x") or x, "y": panel.get("y") or numeric[0]}
            kind = "bar"
        else:
            kind = "table"
    if kind == "bar":
        _draw_bar(ax, dataset.rows, panel)
    elif kind == "line":
        _draw_line(ax, dataset.rows, panel)
    elif kind == "scatter":
        _draw_scatter(ax, dataset.rows, panel)
    elif kind == "heatmap":
        _draw_heatmap(ax, dataset.rows, panel)
    elif kind == "table":
        _draw_table(ax, dataset.rows, panel)
    else:
        raise ValueError(f"Unsupported panel type: {kind}")
    title = str(panel.get("title") or dataset.name).strip()
    ax.set_title(f"{panel_label}. {title}", loc="left", fontsize=11, fontweight="bold")
    ax.grid(True, axis="x", color="#E8E8E8", linewidth=0.7)
    return {
        "label": panel_label,
        "title": title,
        "type": kind,
        "dataset": dataset.name,
        "row_count": len(dataset.rows),
    }


def _render_figure(
    *,
    title: str,
    datasets: Dict[str, LoadedDataset],
    panels: List[Dict[str, Any]],
    png_path: Path,
    pdf_path: Optional[Path],
    dpi: int,
) -> List[Dict[str, Any]]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panel_count = len(panels)
    columns = 2 if panel_count > 1 else 1
    rows = int(math.ceil(panel_count / columns))
    fig_width = 14 if columns == 2 else 10
    fig_height = max(6, 4.8 * rows + 1.2)
    fig, axes = plt.subplots(rows, columns, figsize=(fig_width, fig_height), squeeze=False)
    fig.patch.set_facecolor("white")
    fig.suptitle(title, fontsize=16, fontweight="bold", x=0.02, y=0.995, ha="left")

    rendered: List[Dict[str, Any]] = []
    for index, panel in enumerate(panels):
        ax = axes[index // columns][index % columns]
        dataset = _select_dataset(panel, datasets)
        rendered.append(_draw_panel(ax, dataset, panel, chr(ord("A") + index)))
    for index in range(panel_count, rows * columns):
        axes[index // columns][index % columns].axis("off")
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.savefig(png_path, dpi=dpi, bbox_inches="tight", facecolor="white")
    if pdf_path is not None:
        fig.savefig(pdf_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return rendered


def _write_legend(path: Path, title: str, panels: List[Dict[str, Any]], datasets: Dict[str, LoadedDataset]) -> None:
    lines = [f"# {title}", "", "## Figure Legend", ""]
    for panel in panels:
        label = panel["label"]
        source = datasets[panel["dataset"]].source
        lines.append(
            f"**Panel {label}. {panel['title']}** {panel['type'].title()} view generated from "
            f"`{panel['dataset']}` ({panel['row_count']} rows; source: `{source}`)."
        )
        lines.append("")
    lines.extend([
        "## Provenance",
        "",
        "The figure was generated by Phage-Agent's scientific figure generator. All labels are rendered in English.",
    ])
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _write_provenance(path: Path, rendered_panels: List[Dict[str, Any]], datasets: Dict[str, LoadedDataset]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["panel", "title", "type", "dataset", "row_count", "source"])
        for panel in rendered_panels:
            dataset = datasets[panel["dataset"]]
            writer.writerow([panel["label"], panel["title"], panel["type"], panel["dataset"], panel["row_count"], dataset.source])


def _qa_image(png_path: Path, pdf_path: Optional[Path], legend_path: Path, provenance_path: Path) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, passed: bool, details: Dict[str, Any]) -> None:
        checks.append({"name": name, "passed": bool(passed), "details": details})

    add("png_exists", png_path.is_file() and png_path.stat().st_size > 0, {"path": str(png_path), "bytes": png_path.stat().st_size if png_path.exists() else 0})
    if pdf_path is not None:
        add("pdf_exists", pdf_path.is_file() and pdf_path.stat().st_size > 0, {"path": str(pdf_path), "bytes": pdf_path.stat().st_size if pdf_path.exists() else 0})
    add("legend_exists", legend_path.is_file() and legend_path.stat().st_size > 0, {"path": str(legend_path), "bytes": legend_path.stat().st_size if legend_path.exists() else 0})
    add("provenance_exists", provenance_path.is_file() and provenance_path.stat().st_size > 0, {"path": str(provenance_path), "bytes": provenance_path.stat().st_size if provenance_path.exists() else 0})

    try:
        from PIL import Image, ImageStat

        with Image.open(png_path) as image:
            width, height = image.size
            extrema = image.convert("L").getextrema()
            stat = ImageStat.Stat(image.convert("L"))
            add("png_dimensions", width >= 1200 and height >= 900, {"width": width, "height": height})
            add("png_not_blank", extrema[0] != extrema[1] and (stat.var[0] if stat.var else 0) > 0.5, {"extrema": extrema, "variance": stat.var[0] if stat.var else 0})
    except Exception as exc:
        add("png_readable", False, {"error": str(exc)})

    legend_text = legend_path.read_text(encoding="utf-8") if legend_path.exists() else ""
    lowered = legend_text.lower()
    forbidden_hits = [phrase for phrase in FORBIDDEN_CLAIM_PHRASES if phrase in lowered]
    add("forbidden_claims_absent", not forbidden_hits, {"forbidden_hits": forbidden_hits})
    passed = all(check["passed"] for check in checks)
    return {"passed": passed, "checks": checks}


async def scientific_figure_generator_handler(
    *,
    title: str = "Scientific Composite Figure",
    datasets: Optional[List[Dict[str, Any]]] = None,
    panels: Optional[List[Dict[str, Any]]] = None,
    output_dir: Optional[str] = None,
    output_basename: str = DEFAULT_BASE_NAME,
    formats: Optional[List[str]] = None,
    dpi: int = 300,
    publish: bool = True,
    tool_context: Optional[ToolContext] = None,
) -> Dict[str, Any]:
    """Generate a composite scientific figure plus QA/provenance artifacts."""
    try:
        loaded = _load_datasets(datasets, tool_context=tool_context)
        panel_list = _panel_specs(panels, loaded)
        if not panel_list:
            raise ValueError("At least one panel is required")
        if len(panel_list) > 26:
            raise ValueError("At most 26 panels are supported")

        normalized_formats = {str(item or "").strip().lower() for item in (formats or ["png", "pdf"])}
        normalized_formats.discard("")
        output_root = _resolve_output_dir(output_dir, tool_context)
        base = _slugify(output_basename)
        png_path = output_root / f"{base}.png"
        pdf_path = output_root / f"{base}.pdf" if "pdf" in normalized_formats else None
        legend_path = output_root / "summary.md"
        provenance_path = output_root / f"{base}_provenance.tsv"
        qa_path = output_root / f"{base}_qa.json"

        rendered_panels = _render_figure(
            title=title,
            datasets=loaded,
            panels=panel_list,
            png_path=png_path,
            pdf_path=pdf_path,
            dpi=max(150, int(dpi or 300)),
        )
        _write_legend(legend_path, title, rendered_panels, loaded)
        _write_provenance(provenance_path, rendered_panels, loaded)
        qa = _qa_image(png_path, pdf_path, legend_path, provenance_path)
        qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        artifacts = [
            {"path": str(png_path), "module": "image_tabular", "reason": "Final scientific composite figure PNG"},
            {"path": str(legend_path), "module": "docs", "reason": "Figure legend"},
            {"path": str(provenance_path), "module": "image_tabular", "reason": "Figure provenance table"},
            {"path": str(qa_path), "module": "image_tabular", "reason": "Figure QA report"},
        ]
        if pdf_path is not None:
            artifacts.insert(1, {"path": str(pdf_path), "module": "image_tabular", "reason": "Final scientific composite figure PDF"})

        result = {
            "success": bool(qa["passed"]),
            "tool": "scientific_figure_generator",
            "summary": f"Generated {len(rendered_panels)}-panel scientific figure at {png_path}",
            "result": {
                "title": title,
                "output_dir": str(output_root),
                "figure_png": str(png_path),
                "figure_pdf": str(pdf_path) if pdf_path is not None else None,
                "legend_md": str(legend_path),
                "provenance_tsv": str(provenance_path),
                "qa_json": str(qa_path),
                "qa_passed": bool(qa["passed"]),
                "panels": rendered_panels,
                "generated_files": [artifact["path"] for artifact in artifacts],
            },
            "deliverable_submit": {"publish": bool(publish), "artifacts": artifacts},
        }
        if not qa["passed"]:
            result["error"] = "Generated figure did not pass QA checks"
        return result
    except Exception as exc:
        return {
            "success": False,
            "tool": "scientific_figure_generator",
            "error": str(exc),
            "summary": f"scientific_figure_generator failed: {exc}",
        }


scientific_figure_generator_tool = {
    "name": "scientific_figure_generator",
    "description": (
        "Generate publication-quality scientific composite figures from tabular data. "
        "Outputs PNG/PDF figures, an English legend, provenance TSV, QA JSON, and deliverable artifacts."
    ),
    "category": "visualization",
    "parameters_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "English figure title."},
            "datasets": {
                "type": "array",
                "description": "Input datasets as file paths or inline rows.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "path": {"type": "string", "description": "CSV/TSV/JSON/JSONL file path."},
                        "format": {"type": "string", "description": "Optional format override: csv, tsv, json, jsonl."},
                        "rows": {"type": "array", "items": {"type": "object"}},
                    },
                },
            },
            "panels": {
                "type": "array",
                "description": "Panel specifications. Supported types: auto, bar, line, scatter, heatmap, table.",
                "items": {"type": "object"},
            },
            "output_dir": {"type": "string", "description": "Directory for generated files. Defaults to the task work dir."},
            "output_basename": {"type": "string", "description": "Base filename for generated artifacts."},
            "formats": {"type": "array", "items": {"type": "string"}, "description": "Output formats; png is always written, pdf optional."},
            "dpi": {"type": "integer", "description": "PNG DPI, minimum 150, default 300."},
            "publish": {"type": "boolean", "description": "Whether to publish generated artifacts to deliverables.", "default": True},
        },
        "required": ["datasets"],
    },
    "handler": scientific_figure_generator_handler,
    "tags": ["visualization", "figure", "plot", "scientific", "qa", "provenance"],
    "examples": [
        "Generate a 4-panel communication-network composite figure from TSV pathway summaries.",
        "Create a publication-ready heatmap plus legend and QA report from a CSV matrix.",
    ],
}
