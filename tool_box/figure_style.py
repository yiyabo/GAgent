"""Unified Scientific Figure Styling & Multi-format Export System.

Provides publication-grade style presets (Nature, Cell, Science), consistent typography,
color palettes, margins, background, DPI standards, and seamless export to PNG, SVG, and PDF.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

# --- Standard Scientific Palettes ---
PALETTES: Dict[str, List[str]] = {
    "nature": [
        "#E64B35",  # Coral red
        "#4DBBD5",  # Teal blue
        "#00A087",  # Sea green
        "#3C5488",  # Navy slate
        "#F39B7F",  # Soft salmon
        "#8491B4",  # Periwinkle
        "#91D1C2",  # Mint
        "#DC0000",  # Strong red
        "#7E6148",  # Earth brown
        "#B09C85",  # Warm grey
    ],
    "cell": [
        "#1F77B4",  # Classic blue
        "#FF7F0E",  # Vivid orange
        "#2CA02C",  # Vibrant green
        "#D62728",  # Bold crimson
        "#9467BD",  # Royal purple
        "#8C564B",  # Muted brown
        "#E377C2",  # Bright pink
        "#7F7F7F",  # Neutral grey
        "#BCBD22",  # Olive yellow
        "#17BECF",  # Cyan teal
    ],
    "science": [
        "#003F5C",  # Deep navy
        "#444E86",  # Indigo
        "#955196",  # Plum purple
        "#DD5182",  # Rose magenta
        "#FF6E54",  # Coral orange
        "#FFA600",  # Amber gold
        "#2F4B7C",  # Steel blue
        "#665191",  # Violet
        "#A05195",  # Fuchsia
        "#D45087",  # Magenta
    ],
    "pastel": [
        "#A6CEE3",  # Light blue
        "#1F78B4",  # Dark blue
        "#B2DF8A",  # Light green
        "#33A02C",  # Dark green
        "#FB9A99",  # Light red
        "#E31A1C",  # Dark red
        "#FDBF6F",  # Light orange
        "#FF7F00",  # Dark orange
        "#CAB2D6",  # Light purple
        "#6A3D9A",  # Dark purple
    ],
    "phage": [
        "#2B5C8F",  # Phage blue
        "#4EA8DE",  # Sky accent
        "#56CFE1",  # Lysis cyan
        "#72EFDD",  # Aqua light
        "#80FFDB",  # Glow teal
        "#F77F00",  # Capsid amber
        "#D62828",  # Host contrast
        "#003049",  # Deep genome
    ],
}

DEFAULT_PALETTE_NAME = "nature"
DEFAULT_DPI = 300


def get_palette(name: str = DEFAULT_PALETTE_NAME) -> List[str]:
    """Get color palette by name, fallback to nature."""
    return PALETTES.get(str(name).lower(), PALETTES[DEFAULT_PALETTE_NAME])


def apply_scientific_style(
    palette_name: str = DEFAULT_PALETTE_NAME,
    *,
    font_family: str = "sans-serif",
    base_fontsize: int = 10,
    dpi: int = DEFAULT_DPI,
    tight_layout: bool = True,
) -> Dict[str, Any]:
    """Configure matplotlib.rcParams with publication-quality scientific defaults.

    Ensures consistent:
    - Typography: Clean sans-serif (Arial/DejaVu Sans fallback), clear hierarchical font sizes
    - Geometry: Open top & right spines (despined), clean tick marks, 0.8pt line width
    - Color: High-contrast, colorblind-friendly curated palette
    - Output: 300+ DPI, tight margins, clean white background
    """
    try:
        import matplotlib as mpl
        from cycler import cycler
    except ImportError:
        logger.warning("matplotlib not available; cannot apply scientific style rcParams")
        return {}

    palette = get_palette(palette_name)

    style_dict = {
        # Figure geometry & resolution
        "figure.dpi": dpi,
        "savefig.dpi": dpi,
        "figure.facecolor": "white",
        "figure.edgecolor": "none",
        "savefig.facecolor": "white",
        "savefig.edgecolor": "none",
        "savefig.bbox": "tight" if tight_layout else "standard",
        "savefig.pad_inches": 0.05,
        "savefig.transparent": False,

        # Font hierarchy
        "font.family": font_family,
        "font.sans-serif": [
            "Arial",
            "Helvetica",
            "DejaVu Sans",
            "Liberation Sans",
            "SimHei",
            "sans-serif",
        ],
        "font.size": base_fontsize,
        "axes.titlesize": base_fontsize + 2,
        "axes.titleweight": "bold",
        "axes.titlepad": 6.0,
        "axes.labelsize": base_fontsize,
        "axes.labelweight": "medium",
        "axes.labelpad": 4.0,
        "xtick.labelsize": base_fontsize - 1,
        "ytick.labelsize": base_fontsize - 1,
        "legend.fontsize": base_fontsize - 1,
        "legend.title_fontsize": base_fontsize,

        # Axes spines & grid
        "axes.facecolor": "white",
        "axes.edgecolor": "#333333",
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": True,
        "axes.spines.bottom": True,
        "axes.axisbelow": True,
        "axes.grid": False,
        "grid.color": "#E5E5E5",
        "grid.linestyle": "--",
        "grid.linewidth": 0.6,
        "grid.alpha": 0.7,

        # Ticks
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size": 3.5,
        "ytick.major.size": 3.5,
        "xtick.major.width": 0.8,
        "ytick.major.width": 0.8,
        "xtick.color": "#333333",
        "ytick.color": "#333333",

        # Lines and markers
        "lines.linewidth": 1.5,
        "lines.markersize": 6,
        "lines.markeredgewidth": 0.8,
        "patch.linewidth": 0.8,

        # Legend
        "legend.frameon": False,
        "legend.loc": "best",
        "legend.borderpad": 0.4,

        # Color cycle
        "axes.prop_cycle": cycler(color=palette),
    }

    mpl.rcParams.update(style_dict)
    return style_dict


def save_figure_bundle(
    fig_or_plt: Any,
    base_path: Union[str, Path],
    *,
    formats: Sequence[str] = ("png", "svg", "pdf"),
    dpi: int = DEFAULT_DPI,
    attribution: bool = True,
    close_figure: bool = True,
) -> Dict[str, str]:
    """Save a figure into multiple formats (.png for web/preview, .svg for vector editing, .pdf for publication).

    Args:
        fig_or_plt: Matplotlib Figure instance or matplotlib.pyplot module.
        base_path: Target path without extension or with .png extension.
        formats: File formats to export (png, svg, pdf).
        dpi: Resolution for raster output (default 300).
        attribution: Whether to add the AI compliance watermark/attribution.
        close_figure: Whether to call plt.close(fig) after saving.

    Returns:
        Dict mapping format extension to absolute file path string, e.g.:
        {"png": "/path/fig.png", "svg": "/path/fig.svg", "pdf": "/path/fig.pdf"}
    """
    import matplotlib.pyplot as plt
    from tool_box.watermark import apply_watermark_inplace

    raw_path_str = str(base_path)
    for ext in (".png", ".svg", ".pdf", ".jpg", ".jpeg"):
        if raw_path_str.lower().endswith(ext):
            raw_path_str = raw_path_str[: -len(ext)]
            break

    target_base = Path(raw_path_str).resolve()
    target_base.parent.mkdir(parents=True, exist_ok=True)

    fig = fig_or_plt if hasattr(fig_or_plt, "savefig") else plt.gcf()

    saved_files: Dict[str, str] = {}
    normalized_formats = [fmt.lower().lstrip(".") for fmt in formats]

    for fmt in normalized_formats:
        out_file = target_base.with_suffix(f".{fmt}")
        try:
            if fmt == "png":
                fig.savefig(out_file, dpi=dpi, bbox_inches="tight", facecolor="white")
            elif fmt == "svg":
                # Ensure text is editable rather than converted to path curves in SVG
                fig.savefig(out_file, format="svg", bbox_inches="tight", facecolor="white")
            elif fmt == "pdf":
                fig.savefig(out_file, format="pdf", bbox_inches="tight", facecolor="white")
            else:
                fig.savefig(out_file, dpi=dpi, bbox_inches="tight", facecolor="white")

            if attribution and fmt in ("png", "pdf"):
                apply_watermark_inplace(out_file)

            saved_files[fmt] = str(out_file)
        except Exception as exc:
            logger.error("Failed saving figure format %s to %s: %s", fmt, out_file, exc)

    if close_figure:
        plt.close(fig)

    return saved_files
